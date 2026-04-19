"""`ipodsync sync <src>` — mirror ``<src>/music/**`` onto the iPod.

Two device-touching phases bracket a lazy middle:

1. **Scan** every source file with ffprobe → codec + sha1 (cheap, ~50 ms/file).
2. **Read existing sha1s** off the iPod once (read-only open).
3. **Plan** = scan ∖ existing. ``--dry-run`` exits here.
4. **Prepare** only the to-add items: transcode where needed, tag read,
   artwork extract. Expensive but skipped entirely when there's nothing new.
5. **Commit** — snapshot, then one ``open_readwrite`` block that adds every
   track + artwork, so libgpod writes ``iTunesDB`` / ``iTunesCDB`` /
   ``ArtworkDB`` once.

Idempotent by construction: the dedupe key is the source-content sha1
stashed in userdata (gtkpod ``.ext`` file), so a second run on an
unchanged tree walks to step 3 and returns with "nothing to do".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)

from ipodsync.add import AddError, read_tags
from ipodsync.device import gpod as gpod_facade
from ipodsync.device import mount as mount_mod
from ipodsync.device import snapshot as snap
from ipodsync.device import sysinfo
from ipodsync.device.detect import DetectError, find_ipod
from ipodsync.pipeline import artwork, probe, transcode

_MUSIC_EXT = frozenset(
    {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wav", ".wave", ".aif", ".aiff"}
)


@dataclass(frozen=True)
class _Plan:
    source: Path
    sha1: str
    probe_result: probe.ProbeResult
    source_size: int

    @property
    def codec(self) -> str:
        return self.probe_result.codec_name

    @property
    def needs_transcode(self) -> bool:
        return transcode.needs_transcode(self.probe_result)


@dataclass(frozen=True)
class _Prepared:
    plan: _Plan
    effective: Path
    tags: gpod_facade.MusicTags
    art_path: Path | None
    transcoded: bool


def _walk_music(src: Path) -> list[Path]:
    music = src / "music"
    if not music.is_dir():
        return []
    return sorted(
        p for p in music.rglob("*") if p.is_file() and p.suffix.lower() in _MUSIC_EXT
    )


def _progress(title: str) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn(f"[bold]{title}[/]"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        TextColumn("{task.fields[current]}", style="dim"),
    )


def _scan(files: list[Path], log: Console) -> tuple[list[_Plan], list[tuple[Path, str]]]:
    plans: list[_Plan] = []
    failures: list[tuple[Path, str]] = []
    with _progress("scan") as prog:
        task = prog.add_task("", total=len(files), current="")
        for f in files:
            prog.update(task, current=f.name)
            try:
                pr = probe.probe(f)
                sha = gpod_facade.content_hash(f)
            except probe.ProbeError as e:
                failures.append((f, f"probe: {e}"))
            else:
                plans.append(
                    _Plan(
                        source=f,
                        sha1=sha,
                        probe_result=pr,
                        source_size=f.stat().st_size,
                    )
                )
            prog.advance(task)
    for f, msg in failures:
        log.print(f"[yellow]  · skip {f}: {msg}[/]")
    return plans, failures


def _prepare(
    plans: list[_Plan], strict: bool, log: Console
) -> tuple[list[_Prepared], list[tuple[Path, str]]]:
    out: list[_Prepared] = []
    failures: list[tuple[Path, str]] = []
    with _progress("prepare") as prog:
        task = prog.add_task("", total=len(plans), current="")
        for p in plans:
            prog.update(task, current=p.source.name)
            try:
                tp = transcode.plan(p.source, p.probe_result, p.sha1, strict=strict)
                eff_probe = (
                    probe.probe(tp.effective_path) if tp.transcoded else p.probe_result
                )
                tags = read_tags(p.source, tp.effective_path, eff_probe)
                art = artwork.extract_cached(p.source, p.sha1)
            except (probe.ProbeError, transcode.TranscodeError, AddError) as e:
                failures.append((p.source, str(e)))
            else:
                out.append(
                    _Prepared(
                        plan=p,
                        effective=tp.effective_path,
                        tags=tags,
                        art_path=art,
                        transcoded=tp.transcoded,
                    )
                )
            prog.advance(task)
    for f, msg in failures:
        log.print(f"[yellow]  · skip {f}: {msg}[/]")
    return out, failures


def run(
    source_dir: Path,
    *,
    strict: bool = False,
    dry_run: bool = False,
    console: Console | None = None,
) -> int:
    log = console or Console(stderr=True)

    if not source_dir.is_dir():
        log.print(f"[red]✗[/] not a directory: {source_dir}")
        return 2

    files = _walk_music(source_dir)
    if not files:
        log.print(f"[yellow]![/] no audio files under {source_dir / 'music'}")
        return 0
    log.print(f"found {len(files)} file(s) under {source_dir / 'music'}")

    plans, scan_failures = _scan(files, log)
    if not plans:
        log.print("[red]✗[/] every file failed to scan")
        return 1

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
            log.print("[red]✗[/] FirewireGUID not found")
            return 1

        try:
            with gpod_facade.open_readonly(mnt) as db:
                existing = gpod_facade.collect_sha1_hashes(db)
        except gpod_facade.GpodImportError as e:
            log.print(f"[red]✗[/] {e}")
            return 1
        except gpod_facade.DbOpenError as e:
            log.print(f"[red]✗[/] could not read iTunesDB: {e}")
            return 1

        to_add = [p for p in plans if p.sha1 not in existing]
        dedup_skip = len(plans) - len(to_add)
        transcode_n = sum(1 for p in to_add if p.needs_transcode)

        log.print(
            f"plan: add={len(to_add)} skip(dedup)={dedup_skip} "
            f"transcode={transcode_n} scan-failed={len(scan_failures)}"
        )

        if strict and transcode_n:
            log.print(
                f"[red]✗[/] --strict: {transcode_n} file(s) would need transcoding:"
            )
            for p in to_add:
                if p.needs_transcode:
                    log.print(f"[red]  · {p.source} ({p.codec})[/]")
            return 4

        if dry_run:
            log.print("[yellow]--dry-run: exiting without writes[/]")
            return 0

        if not to_add:
            log.print("[green]✓[/] already in sync")
            return 5 if scan_failures else 0

        prepared, prep_failures = _prepare(to_add, strict, log)
        if not prepared:
            log.print("[red]✗[/] all new items failed during prepare")
            return 1

        try:
            pre = snap.create(mnt, guid)
        except snap.SnapshotError as e:
            log.print(f"[red]✗[/] snapshot failed: {e}")
            return 1
        log.print(f"[dim]snapshot {pre.timestamp}[/]")

        added = 0
        try:
            with gpod_facade.open_readwrite(mnt) as db, _progress("commit") as prog:
                task = prog.add_task("", total=len(prepared), current="")
                for it in prepared:
                    prog.update(task, current=it.plan.source.name)
                    track = gpod_facade.add_music_track(
                        db, it.effective, it.tags, it.plan.sha1
                    )
                    if it.art_path is not None:
                        gpod_facade.attach_artwork(track, it.art_path)
                    added += 1
                    prog.advance(task)
        except gpod_facade.DbWriteError as e:
            log.print(f"[red]✗[/] write failed: {e}")
            log.print(
                f"[dim]  → roll back: ipodsync restore --snapshot {pre.timestamp}[/]"
            )
            return 1

        total_failed = len(scan_failures) + len(prep_failures)
        log.print(
            f"[green]✓[/] added {added} track(s) in one commit; "
            f"{total_failed} skipped"
        )
        return 5 if total_failed else 0
    finally:
        if we_mounted:
            try:
                mount_mod.umount_quiet(mnt)
            except mount_mod.MountError as e:
                log.print(f"[yellow]![/] cleanup umount failed: {e}")
