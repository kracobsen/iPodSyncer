"""Thin, read-oriented facade over `python-gpod`.

Phase 4 only needs to *read* the iTunesDB. We deliberately avoid `Database.close()`
because it calls `itdb_write` and would mutate the DB on exit. The context manager
here opens the DB, hands it back, and relies on Python GC + `itdb_free` for
cleanup — no writes happen.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class Kind(StrEnum):
    MUSIC = "music"
    PODCAST = "podcast"
    AUDIOBOOK = "audiobook"
    OTHER = "other"


@dataclass(frozen=True)
class TrackInfo:
    id: int
    title: str
    artist: str
    album: str
    kind: Kind
    size: int          # bytes
    duration_ms: int   # tracklen, ms
    ipod_path: str     # colon-form on-device path, "" if missing


class GpodImportError(RuntimeError):
    """`import gpod` failed — the bootstrap script hasn't been run."""


class DbOpenError(RuntimeError):
    """Raised when libgpod cannot parse the iTunesDB at the mount point."""


class DbWriteError(RuntimeError):
    """Raised when libgpod fails to copy a file or save the DB."""


def _require_gpod() -> Any:
    try:
        import gpod  # type: ignore[import-not-found]
    except ImportError as e:
        raise GpodImportError(
            "python-gpod bindings not importable — run scripts/bootstrap.sh"
        ) from e
    return gpod


def kind_from_mediatype(mt: int | None) -> Kind:
    """Classify an `Itdb_Track.mediatype` bitfield.

    mediatype 0 (unset on legacy iTunes-added tracks) is treated as music.
    When multiple bits are set the priority is audiobook > podcast > music.
    """
    if mt is None:
        return Kind.MUSIC
    if mt & 0x08:
        return Kind.AUDIOBOOK
    if mt & 0x04:
        return Kind.PODCAST
    if mt == 0 or mt & 0x01:
        return Kind.MUSIC
    return Kind.OTHER


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return str(v)


def _i(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _track_info(track: Any) -> TrackInfo:
    return TrackInfo(
        id=_i(getattr(track, "id", 0)),
        title=_s(getattr(track, "title", None)),
        artist=_s(getattr(track, "artist", None)),
        album=_s(getattr(track, "album", None)),
        kind=kind_from_mediatype(getattr(track, "mediatype", 0)),
        size=_i(getattr(track, "size", 0)),
        duration_ms=_i(getattr(track, "tracklen", 0)),
        ipod_path=_s(getattr(track, "ipod_path", None)),
    )


@contextmanager
def open_readonly(mount_point: Path) -> Iterator[Any]:
    """Open the iTunesDB at `mount_point` without ever writing it back."""
    gpod = _require_gpod()
    try:
        db = gpod.Database(str(mount_point))
    except gpod.DatabaseException as e:
        raise DbOpenError(str(e)) from e
    try:
        yield db
    finally:
        # No close() — that would call itdb_write. Let GC run itdb_free.
        del db


@contextmanager
def open_readwrite(mount_point: Path) -> Iterator[Any]:
    """Open the iTunesDB for mutation; `db.close()` writes iTunesDB + iTunesCDB + hash58."""
    gpod = _require_gpod()
    try:
        db = gpod.Database(str(mount_point))
    except gpod.DatabaseException as e:
        raise DbOpenError(str(e)) from e
    committed = False
    try:
        yield db
        try:
            db.close()
        except gpod.DatabaseException as e:
            raise DbWriteError(f"itdb_write failed: {e}") from e
        committed = True
    finally:
        if not committed:
            # Don't call close() on error — that would persist partial state.
            del db


def iter_tracks(db: Any) -> Iterator[TrackInfo]:
    for i in range(len(db)):
        yield _track_info(db[i]._track)


# --- mutation helpers (phase 6+) -------------------------------------------


def content_hash(path: Path) -> str:
    """SHA-1 of (file-size || first 16 KiB). Matches gtkpod's `sha1_hash`."""
    _require_gpod()
    from gpod import gtkpod
    return gtkpod.sha1_hash(str(path))


def find_track_id_by_hash(db: Any, sha1: str) -> int | None:
    """Return the iTunesDB id of a previously-added track with this source hash."""
    for i in range(len(db)):
        track = db[i]
        try:
            ud = track["userdata"]
        except KeyError:
            continue
        if isinstance(ud, dict) and ud.get("sha1_hash") == sha1:
            return int(track._track.id)
    return None


def collect_sha1_hashes(db: Any) -> set[str]:
    """All source-content sha1 hashes stashed in track userdata (phase 6+)."""
    out: set[str] = set()
    for i in range(len(db)):
        track = db[i]
        try:
            ud = track["userdata"]
        except KeyError:
            continue
        if isinstance(ud, dict):
            h = ud.get("sha1_hash")
            if h:
                out.add(str(h))
    return out


def _track_sha1(track: Any) -> str | None:
    try:
        ud = track["userdata"]
    except KeyError:
        return None
    if isinstance(ud, dict):
        h = ud.get("sha1_hash")
        return str(h) if h else None
    return None


def find_track_by_id(db: Any, track_id: int) -> Any | None:
    """Return the python-gpod Track wrapper whose `id` matches, or None."""
    for i in range(len(db)):
        track = db[i]
        if int(track._track.id) == int(track_id):
            return track
    return None


def iter_track_wrappers(db: Any) -> Iterator[tuple[TrackInfo, Any, str | None]]:
    """Yield (TrackInfo, wrapper, sha1) per track. Wrapper lives only while `db` is open."""
    for i in range(len(db)):
        wrapper = db[i]
        yield _track_info(wrapper._track), wrapper, _track_sha1(wrapper)


def remove_track(db: Any, track_wrapper: Any) -> None:
    """Unlink from all playlists, delete F## file, unlink from DB. DB must be open r/w."""
    db.remove(track_wrapper, ipod=True, harddisk=False, quiet=True)


def music_pool_files(mount_point: Path) -> Iterator[Path]:
    """Yield every real file under `iPod_Control/Music/F##/`."""
    music = mount_point / "iPod_Control" / "Music"
    if not music.is_dir():
        return
    for f_dir in sorted(music.iterdir()):
        if not (f_dir.is_dir() and f_dir.name.startswith("F")):
            continue
        for entry in f_dir.iterdir():
            if entry.is_file():
                yield entry


def referenced_ipod_paths(db: Any) -> set[Path]:
    """Absolute paths of every F## file referenced by a track in `db`."""
    gpod = _require_gpod()
    out: set[Path] = set()
    for i in range(len(db)):
        track = db[i]._track
        try:
            p = gpod.itdb_filename_on_ipod(track)
        except Exception:
            continue
        if p:
            out.add(Path(p.decode("utf-8") if isinstance(p, bytes) else p))
    return out


@dataclass(frozen=True)
class MusicTags:
    title: str
    artist: str
    album: str
    albumartist: str
    genre: str
    year: int | None
    track_nr: int | None
    tracks: int | None
    cd_nr: int | None
    cds: int | None
    duration_ms: int
    bitrate_kbps: int | None
    samplerate: int | None
    size_bytes: int
    filetype_label: str   # human-readable filetype tag ("MPEG audio file", ...)


def ensure_m4b_suffix(source: Path, sha1: str) -> Path:
    """For audiobook sources with the wrong extension, return a path whose
    suffix is ``.m4b`` so ``itdb_cp_track_to_ipod`` names the destination
    accordingly. Implemented as a cache-dir symlink — zero extra disk cost
    regardless of how large the source file is. The symlink target is the
    resolved source path; the symlink's mtime follows the source because
    ``itdb_cp`` reads through the link.
    """
    if source.suffix.lower() == ".m4b":
        return source
    cache = Path.home() / "Library" / "Caches" / "ipodsync" / "audiobooks"
    cache.mkdir(parents=True, exist_ok=True)
    link = cache / f"{sha1}.m4b"
    target = source.resolve()
    if link.is_symlink() or link.exists():
        if link.is_symlink() and link.readlink() == target:
            return link
        link.unlink()
    link.symlink_to(target)
    return link


def add_music_track(
    db: Any,
    source: Path,
    tags: MusicTags,
    sha1: str,
    *,
    kind: Kind = Kind.MUSIC,
    podcast_playlist: Any | None = None,
) -> Any:
    """Create an Itdb_Track for `source`, copy the file to F## pool, add to a playlist.

    ``kind=MUSIC`` → mediatype=AUDIO, added to the master playlist (MPL).
    ``kind=PODCAST`` → mediatype=PODCAST, added to ``podcast_playlist`` only
    (never MPL — that's what keeps episodes out of Songs/Albums/Artists).
    The writer groups podcast-playlist members by ``track.album`` into mhip
    groups automatically, so callers should set ``tags.album`` to the show.
    ``kind=AUDIOBOOK`` → mediatype=AUDIOBOOK, added to the MPL. The iPod
    firmware routes mediatype=0x08 tracks into the Books menu and filters
    them out of Songs / Albums / Artists automatically. Caller is expected
    to have pre-normalised the source extension to ``.m4b`` (see
    :func:`ensure_m4b_suffix`) — the ``.m4b`` vs ``.m4a`` distinction is
    firmware-load-bearing per FEASIBILITY.

    Returns the raw ``Itdb_Track`` pointer. The caller should read ``track.id``
    only AFTER the database has been committed (``open_readwrite`` exit) —
    libgpod assigns the id during ``itdb_write``, not on ``itdb_track_add``.
    """
    gpod = _require_gpod()
    import socket

    if kind == Kind.PODCAST and podcast_playlist is None:
        raise DbWriteError("podcast track needs a podcast_playlist to be added to")

    track = gpod.itdb_track_new()

    def _set_str(attr: str, value: str) -> None:
        setattr(track, attr, value.encode("utf-8") if value else b"")

    _set_str("title", tags.title)
    _set_str("artist", tags.artist)
    _set_str("album", tags.album)
    _set_str("albumartist", tags.albumartist)
    _set_str("genre", tags.genre)
    _set_str("filetype", tags.filetype_label)

    track.mediatype = {
        Kind.PODCAST: gpod.ITDB_MEDIATYPE_PODCAST,
        Kind.AUDIOBOOK: gpod.ITDB_MEDIATYPE_AUDIOBOOK,
    }.get(kind, gpod.ITDB_MEDIATYPE_AUDIO)
    if kind == Kind.PODCAST:
        # 6G firmware won't expose podcast episodes in the Podcasts menu unless
        # mark_unplayed=0x02 is set (tracks with neither unplayed flag nor a
        # positive playcount are hidden). The other three flags go along with
        # the same "treat as podcast" contract per libgpod's own convention;
        # technically phase 12 territory but navigation depends on them.
        track.skip_when_shuffling = 0x01
        track.remember_playback_position = 0x01
        track.flag4 = 0x01
        track.mark_unplayed = 0x02
    elif kind == Kind.AUDIOBOOK:
        # Keeps audiobooks out of Shuffle Songs and lets the firmware bookmark
        # the playback position across pause/eject, which is what makes the
        # Books menu usable at all.
        track.skip_when_shuffling = 0x01
        track.remember_playback_position = 0x01
    track.tracklen = tags.duration_ms
    track.size = tags.size_bytes
    if tags.bitrate_kbps is not None:
        track.bitrate = tags.bitrate_kbps
    if tags.samplerate is not None:
        track.samplerate = tags.samplerate
    if tags.year is not None:
        track.year = tags.year
    if tags.track_nr is not None:
        track.track_nr = tags.track_nr
    if tags.tracks is not None:
        track.tracks = tags.tracks
    if tags.cd_nr is not None:
        track.cd_nr = tags.cd_nr
    if tags.cds is not None:
        track.cds = tags.cds

    # Attach BEFORE copy — itdb_cp_track_to_ipod reads the mount point off track.itdb.
    gpod.itdb_track_add(db._itdb, track, -1)

    if gpod.itdb_cp_track_to_ipod(track, str(source).encode("utf-8"), None) != 1:
        gpod.itdb_track_unlink(track)
        raise DbWriteError(f"itdb_cp_track_to_ipod failed for {source}")

    # Stash provenance so re-runs can dedupe (persists via gtkpod .ext file).
    mp = gpod.itdb_get_mountpoint(track.itdb).decode("utf-8")
    ipod_rel = gpod.itdb_filename_on_ipod(track).decode("utf-8").replace(
        mp, ""
    ).replace("/", ":")
    gpod.sw_set_track_userdata(track, {
        "transferred": 1,
        "sha1_hash": sha1,
        "filename": str(source),
        "filename_ipod": ipod_rel,
        "hostname": socket.gethostname(),
        "charset": "UTF-8",
    })

    if kind == Kind.PODCAST:
        gpod.itdb_playlist_add_track(podcast_playlist, track, -1)
    else:
        mpl = gpod.itdb_playlist_mpl(db._itdb)
        gpod.itdb_playlist_add_track(mpl, track, -1)

    return track


def count_podcast_playlists(db: Any) -> int:
    """Number of playlists with ``podcastflag == ITDB_PL_FLAG_PODCASTS``."""
    gpod = _require_gpod()
    pls = db.Playlists
    return sum(
        1 for i in range(len(pls))
        if gpod.itdb_playlist_is_podcasts(pls[i]._pl) == 1
    )


def ensure_podcast_playlist(db: Any) -> Any:
    """Return the (single) podcasts playlist, creating one if absent.

    The caller must already hold the DB open read-write. Refuses to proceed
    if the device somehow has more than one podcast-flagged playlist — that
    would make routing ambiguous and violate the "exactly one" invariant.
    """
    gpod = _require_gpod()
    existing = gpod.itdb_playlist_podcasts(db._itdb)
    if existing is not None:
        if count_podcast_playlists(db) > 1:
            raise DbWriteError(
                "device has more than one podcast-flagged playlist; "
                "delete duplicates before syncing podcasts"
            )
        return existing
    pl = gpod.itdb_playlist_new(b"Podcasts", False)
    gpod.itdb_playlist_set_podcasts(pl)
    gpod.itdb_playlist_add(db._itdb, pl, -1)
    return pl


def attach_artwork(track: Any, image_path: Path) -> bool:
    """Queue cover art on `track`; libgpod renders + writes F1_1.ithmb at commit.

    SWIG's `gchar const *` typemap in this build wants `bytes`, not `str`
    (same as `itdb_cp_track_to_ipod`). The file-path variant is used because
    the `_from_data` binding's `guchar const *` typemap is unwired — bytes
    get rejected there too. gdk-pixbuf sniffs format from magic bytes, so
    the cache file's `.bin` suffix doesn't matter.
    """
    gpod = _require_gpod()
    ok = gpod.itdb_track_set_thumbnails(track, str(image_path).encode("utf-8"))
    return bool(ok)
