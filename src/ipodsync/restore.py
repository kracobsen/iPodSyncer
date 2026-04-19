"""`ipodsync snapshot` + `ipodsync restore` orchestration.

Phase 5 exposes these as standalone commands so snapshot/rollback can be
tested independently of any DB-mutating code. Later phases will call
`snapshot.create()` directly from `add` / `rm` / `sync` before writing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ipodsync.device import mount as mount_mod
from ipodsync.device import snapshot as snap
from ipodsync.device import sysinfo
from ipodsync.device.detect import DetectError, find_ipod


@dataclass
class _MountCtx:
    mount_point: Path
    guid: str
    we_mounted: bool


def _prepare(log: Console) -> _MountCtx | int:
    """Detect + mount (if needed) + GUID. Returns exit code on failure."""
    try:
        device = find_ipod()
    except DetectError as e:
        log.print(f"[red]✗[/] {e}")
        return 2

    if device.is_mounted:
        assert device.mount_point is not None
        mnt = device.mount_point
        we_mounted = False
    else:
        try:
            mnt = mount_mod.mount(device).mount_point
        except mount_mod.MountError as e:
            log.print(f"[red]✗[/] {e}")
            return 1
        we_mounted = True

    if sysinfo.is_rockbox(mnt):
        if we_mounted:
            _cleanup_mount(log, mnt)
        log.print("[red]✗[/] Rockbox detected — refusing to snapshot.")
        return 3

    guid = sysinfo.read_firewire_guid(mnt)
    if not guid:
        if we_mounted:
            _cleanup_mount(log, mnt)
        log.print(
            "[red]✗[/] FirewireGUID not found — cannot namespace snapshots "
            "without it"
        )
        return 1

    return _MountCtx(mount_point=mnt, guid=guid, we_mounted=we_mounted)


def _cleanup_mount(log: Console, mnt: Path) -> None:
    try:
        mount_mod.umount_quiet(mnt)
    except mount_mod.MountError as e:
        log.print(f"[yellow]![/] cleanup umount failed: {e}")


def run_snapshot(console: Console | None = None) -> int:
    log = console or Console(stderr=True)
    ctx = _prepare(log)
    if isinstance(ctx, int):
        return ctx
    try:
        try:
            s = snap.create(ctx.mount_point, ctx.guid)
        except snap.SnapshotError as e:
            log.print(f"[red]✗[/] {e}")
            return 1
        log.print(
            f"[green]✓[/] snapshot {s.timestamp} — "
            f"{len(s.files)} file(s) → {s.path}"
        )
        return 0
    finally:
        if ctx.we_mounted:
            _cleanup_mount(log, ctx.mount_point)


def _list_table(snaps: list[snap.Snapshot]) -> Table:
    table = Table(title="snapshots", show_lines=False)
    table.add_column("Timestamp", style="bold")
    table.add_column("Files", justify="right")
    table.add_column("Path", overflow="fold", style="dim")
    for s in snaps:
        table.add_row(s.timestamp, str(len(s.files)), str(s.path))
    return table


def run_restore(
    *,
    selector: str | None = None,
    assume_yes: bool = False,
    console: Console | None = None,
) -> int:
    log = console or Console(stderr=True)
    ctx = _prepare(log)
    if isinstance(ctx, int):
        return ctx

    try:
        snaps = snap.list_snapshots(ctx.guid)
        if selector is None:
            if not snaps:
                log.print(f"[dim]no snapshots for device {ctx.guid}[/]")
                return 0
            Console().print(_list_table(snaps))
            log.print(
                f"[dim]use `ipodsync restore --snapshot {snaps[-1].timestamp}` "
                f"or `--snapshot latest` to roll back[/]"
            )
            return 0

        try:
            target = snap.resolve(ctx.guid, selector)
        except snap.SnapshotError as e:
            log.print(f"[red]✗[/] {e}")
            return 1

        if not assume_yes:
            confirm = typer.confirm(
                f"Restore {target.timestamp} ({len(target.files)} file(s)) "
                f"to {ctx.mount_point}?",
                default=False,
            )
            if not confirm:
                log.print("[yellow]aborted[/]")
                return 1

        # Take a pre-restore snapshot so the rollback itself is reversible.
        pre = snap.create(ctx.mount_point, ctx.guid)
        log.print(
            f"[dim]saved pre-restore snapshot {pre.timestamp}[/]"
        )

        restored = snap.restore(ctx.mount_point, target)
        log.print(
            f"[green]✓[/] restored {target.timestamp} — "
            f"{len(restored)} file(s): {', '.join(restored)}"
        )
        return 0
    finally:
        if ctx.we_mounted:
            _cleanup_mount(log, ctx.mount_point)
