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


def add_music_track(db: Any, source: Path, tags: MusicTags, sha1: str) -> Any:
    """Create an Itdb_Track for `source`, copy the file to F## pool, add to MPL.

    Returns the raw `Itdb_Track` pointer. The caller should read `track.id`
    only AFTER the database has been committed (`open_readwrite` exit) —
    libgpod assigns the id during `itdb_write`, not on `itdb_track_add`.
    """
    gpod = _require_gpod()
    import socket

    track = gpod.itdb_track_new()

    def _set_str(attr: str, value: str) -> None:
        setattr(track, attr, value.encode("utf-8") if value else b"")

    _set_str("title", tags.title)
    _set_str("artist", tags.artist)
    _set_str("album", tags.album)
    _set_str("albumartist", tags.albumartist)
    _set_str("genre", tags.genre)
    _set_str("filetype", tags.filetype_label)

    track.mediatype = gpod.ITDB_MEDIATYPE_AUDIO   # 0x01
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

    mpl = gpod.itdb_playlist_mpl(db._itdb)
    gpod.itdb_playlist_add_track(mpl, track, -1)

    return track


def attach_artwork(track: Any, image_data: bytes) -> bool:
    """Queue cover art on `track`; libgpod renders + writes F1_1.ithmb at commit.

    Uses `itdb_track_set_thumbnails_from_data` so we avoid round-tripping the
    image through a temp file. gdk-pixbuf handles format decoding (JPEG/PNG);
    the per-model thumbnail size table lives inside libgpod.
    """
    gpod = _require_gpod()
    ok = gpod.itdb_track_set_thumbnails_from_data(track, image_data, len(image_data))
    return bool(ok)
