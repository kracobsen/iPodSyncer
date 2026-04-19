"""`ipodsync add <file>` — copy one music file onto the iPod.

Phase 6 is the thinnest possible vertical slice through the writer: one file,
extension-gated passthrough only (MP3 / M4A), no transcode, no artwork, no
pipeline. Every write is preceded by a snapshot (phase 5) so a bad add can
be rolled back.
"""

from __future__ import annotations

from pathlib import Path

import mutagen
from rich.console import Console

from ipodsync.device import gpod as gpod_facade
from ipodsync.device import mount as mount_mod
from ipodsync.device import snapshot as snap
from ipodsync.device import sysinfo
from ipodsync.device.detect import DetectError, find_ipod
from ipodsync.pipeline import artwork

# Passthrough set for phase 6: no codec probe yet, we trust the extension.
# Phase 8 widens this via ffprobe-backed classification.
_SUPPORTED_EXT: dict[str, str] = {
    ".mp3": "MPEG audio file",
    ".m4a": "AAC audio file",
}


class AddError(RuntimeError):
    pass


def _pair(val: str | None) -> tuple[int | None, int | None]:
    """Split 'N/M' (id3) or 'N' into (nr, total)."""
    if not val:
        return None, None
    parts = str(val).split("/", 1)
    try:
        nr = int(parts[0]) if parts[0] else None
    except ValueError:
        nr = None
    tot: int | None = None
    if len(parts) == 2 and parts[1]:
        try:
            tot = int(parts[1])
        except ValueError:
            tot = None
    return nr, tot


def _first(d: mutagen.FileType, key: str) -> str:
    v = d.get(key)
    if isinstance(v, list) and v:
        return str(v[0])
    return ""


def probe_tags(path: Path) -> gpod_facade.MusicTags:
    ext = path.suffix.lower()
    if ext not in _SUPPORTED_EXT:
        raise AddError(
            f"unsupported file extension {ext!r}; phase 6 accepts "
            + " ".join(_SUPPORTED_EXT)
        )
    af = mutagen.File(str(path), easy=True)
    if af is None or af.info is None:
        raise AddError(f"mutagen could not parse {path}")

    track_nr, tracks = _pair(_first(af, "tracknumber"))
    cd_nr, cds = _pair(_first(af, "discnumber"))
    date = _first(af, "date")
    year = int(date[:4]) if date[:4].isdigit() else None

    title = _first(af, "title") or path.stem
    artist = _first(af, "artist")
    album = _first(af, "album")
    albumartist = _first(af, "albumartist") or artist
    genre = _first(af, "genre")

    info = af.info
    duration_ms = int(round(info.length * 1000)) if info.length else 0
    bitrate_kbps = int(info.bitrate / 1000) if getattr(info, "bitrate", 0) else None
    samplerate = int(info.sample_rate) if getattr(info, "sample_rate", 0) else None

    return gpod_facade.MusicTags(
        title=title,
        artist=artist,
        album=album,
        albumartist=albumartist,
        genre=genre,
        year=year,
        track_nr=track_nr,
        tracks=tracks,
        cd_nr=cd_nr,
        cds=cds,
        duration_ms=duration_ms,
        bitrate_kbps=bitrate_kbps,
        samplerate=samplerate,
        size_bytes=path.stat().st_size,
        filetype_label=_SUPPORTED_EXT[ext],
    )


def run(source: Path, *, console: Console | None = None) -> int:
    log = console or Console(stderr=True)

    if not source.exists():
        log.print(f"[red]✗[/] file not found: {source}")
        return 2

    try:
        tags = probe_tags(source)
    except AddError as e:
        log.print(f"[red]✗[/] {e}")
        return 2

    sha1 = gpod_facade.content_hash(source)

    try:
        device = find_ipod()
    except DetectError as e:
        log.print(f"[red]✗[/] {e}")
        return 2

    we_mounted = False
    if device.is_mounted:
        assert device.mount_point is not None
        mnt = device.mount_point
    else:
        try:
            mnt = mount_mod.mount(device).mount_point
        except mount_mod.MountError as e:
            log.print(f"[red]✗[/] {e}")
            return 1
        we_mounted = True

    try:
        if sysinfo.is_rockbox(mnt):
            log.print("[red]✗[/] Rockbox detected — refusing to write.")
            return 3

        guid = sysinfo.read_firewire_guid(mnt)
        if not guid:
            log.print(
                "[red]✗[/] FirewireGUID not found — needed for hash58 + snapshots"
            )
            return 1

        # Snapshot BEFORE any mutation (phase 5 contract).
        try:
            pre = snap.create(mnt, guid)
        except snap.SnapshotError as e:
            log.print(f"[red]✗[/] snapshot failed: {e}")
            return 1
        log.print(f"[dim]snapshot {pre.timestamp}[/]")

        added_track = None
        art_attached = False
        try:
            with gpod_facade.open_readwrite(mnt) as db:
                existing_id = gpod_facade.find_track_id_by_hash(db, sha1)
                if existing_id is not None:
                    log.print(
                        f"[yellow]=[/] already on device as track #{existing_id} "
                        f"(sha1={sha1[:10]}…)"
                    )
                    return 0
                added_track = gpod_facade.add_music_track(db, source, tags, sha1)

                art_bytes = artwork.extract_cached(source, sha1)
                if art_bytes:
                    art_attached = gpod_facade.attach_artwork(added_track, art_bytes)
        except gpod_facade.GpodImportError as e:
            log.print(f"[red]✗[/] {e}")
            return 1
        except gpod_facade.DbOpenError as e:
            log.print(f"[red]✗[/] could not read iTunesDB: {e}")
            return 1
        except gpod_facade.DbWriteError as e:
            log.print(f"[red]✗[/] write failed: {e}")
            log.print(f"[dim]  → roll back with: ipodsync restore --snapshot {pre.timestamp}[/]")
            return 1

        # id is only valid post-commit (assigned by itdb_write).
        new_id = int(added_track.id) if added_track is not None else 0
        art_note = " +art" if art_attached else ""
        log.print(
            f"[green]✓[/] added [bold]{tags.title}[/] — {tags.artist or '—'} "
            f"(track #{new_id}){art_note}"
        )
        return 0
    finally:
        if we_mounted:
            try:
                mount_mod.umount_quiet(mnt)
            except mount_mod.MountError as e:
                log.print(f"[yellow]![/] cleanup umount failed: {e}")
