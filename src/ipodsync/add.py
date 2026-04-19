"""`ipodsync add <file>` — copy one audio file onto the iPod.

Phase 6 was passthrough-only (MP3 / M4A, extension-gated). Phase 7 added
artwork. Phase 8 inserts ``probe`` + ``transcode`` stages: ffprobe decides
passthrough vs re-encode (mp3/aac/alac/pcm → passthrough; flac/opus/vorbis/
etc → AAC 256 kbps .m4a, cached on disk). ``--strict`` refuses to transcode.

Every write is preceded by a snapshot (phase 5) so a bad add can be rolled
back.
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
from ipodsync.pipeline import artwork, probe, transcode

# codec_name → iTunes-style filetype label (cosmetic; shown in the iPod's
# per-track info panel). Unknown codecs fall through to a generic string.
_CODEC_LABEL: dict[str, str] = {
    "mp3": "MPEG audio file",
    "aac": "AAC audio file",
    "alac": "Apple Lossless audio file",
}


class AddError(RuntimeError):
    pass


def _filetype_label(p: probe.ProbeResult) -> str:
    if p.codec_name in _CODEC_LABEL:
        return _CODEC_LABEL[p.codec_name]
    if p.codec_name.startswith("pcm_"):
        if "wav" in p.container:
            return "WAV audio file"
        if "aiff" in p.container:
            return "AIFF audio file"
    return f"{p.codec_name or 'unknown'} audio file"


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


def read_tags(
    tag_source: Path,
    effective_path: Path,
    probe_result: probe.ProbeResult,
) -> gpod_facade.MusicTags:
    """mutagen-backed tag read.

    String tags come from ``tag_source`` (always the original file) because
    ffmpeg's ``-map_metadata 0`` silently drops some Vorbis-comment fields
    when muxing into MP4 — Opus title/artist don't make it through. Audio
    metrics (duration/bitrate/sample-rate/size) come from the effective
    file's probe and stat so they reflect what lands on the iPod.
    """
    af = mutagen.File(str(tag_source), easy=True)
    if af is None or af.info is None:
        raise AddError(f"mutagen could not parse {tag_source}")

    track_nr, tracks = _pair(_first(af, "tracknumber"))
    cd_nr, cds = _pair(_first(af, "discnumber"))
    date = _first(af, "date")
    year = int(date[:4]) if date[:4].isdigit() else None

    title = _first(af, "title") or tag_source.stem
    artist = _first(af, "artist")
    album = _first(af, "album")
    albumartist = _first(af, "albumartist") or artist
    genre = _first(af, "genre")

    duration_ms = probe_result.duration_ms or (
        int(round(af.info.length * 1000)) if af.info.length else 0
    )
    bitrate_kbps = probe_result.bitrate_kbps or (
        int(af.info.bitrate / 1000) if getattr(af.info, "bitrate", 0) else None
    )
    samplerate = probe_result.sample_rate or (
        int(af.info.sample_rate) if getattr(af.info, "sample_rate", 0) else None
    )

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
        size_bytes=effective_path.stat().st_size,
        filetype_label=_filetype_label(probe_result),
    )


def run(source: Path, *, strict: bool = False, console: Console | None = None) -> int:
    log = console or Console(stderr=True)

    if not source.exists():
        log.print(f"[red]✗[/] file not found: {source}")
        return 2

    # 1. Codec probe on the source file.
    try:
        src_probe = probe.probe(source)
    except probe.ProbeError as e:
        log.print(f"[red]✗[/] {e}")
        return 2

    # 2. Dedupe key is the SOURCE content-hash — a second `add` of the same
    #    source stays idempotent regardless of transcode cache state.
    sha1 = gpod_facade.content_hash(source)

    # 3. Passthrough vs transcode decision.
    try:
        plan = transcode.plan(source, src_probe, sha1, strict=strict)
    except transcode.StrictRefusal as e:
        log.print(f"[red]✗[/] {e}")
        return 4
    except transcode.TranscodeError as e:
        log.print(f"[red]✗[/] transcode failed: {e}")
        return 1

    if plan.transcoded:
        log.print(
            f"[dim]transcode {src_probe.codec_name} → aac "
            f"({plan.effective_path.name})[/]"
        )
        # Re-probe the transcoded file so duration/bitrate reflect the output.
        try:
            eff_probe = probe.probe(plan.effective_path)
        except probe.ProbeError as e:
            log.print(f"[red]✗[/] re-probe failed: {e}")
            return 1
    else:
        eff_probe = src_probe

    # 4. Tags from the original source (string metadata); audio metrics from
    #    the effective file via eff_probe / stat.
    try:
        tags = read_tags(source, plan.effective_path, eff_probe)
    except AddError as e:
        log.print(f"[red]✗[/] {e}")
        return 2

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
                added_track = gpod_facade.add_music_track(
                    db, plan.effective_path, tags, sha1
                )

                # Artwork always pulled from the ORIGINAL source: embedded art
                # is dropped by `-vn` during transcode, and sibling cover files
                # live next to the source anyway.
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
