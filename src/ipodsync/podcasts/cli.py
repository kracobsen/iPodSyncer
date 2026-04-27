"""``ipodsync podcasts`` subcommand handlers (phase 18).

Read-only inspect / forget surface over the consumed-podcast ledger. No
device writes, no sync glue (that lands in phase 19). The ``--guid`` flag
is the offline path — without it we detect+mount the iPod just to learn
the GUID, which is also what hand-rolling the JSON file for a demo asks
for.
"""

from __future__ import annotations

import contextlib

from rich.console import Console
from rich.table import Table

from ipodsync.device import mount as mount_mod
from ipodsync.device import sysinfo
from ipodsync.device.detect import DetectError, find_ipod
from ipodsync.podcasts import ledger as ld


def _resolve_guid(explicit: str | None, log: Console) -> str | None:
    """Return the GUID the ledger lives under, or None on failure.

    With ``--guid``, bypass the device entirely (offline mode for hand-
    crafted ledger demos / inspecting a previously-used iPod). Without,
    detect+mount the iPod and read FirewireGUID from SysInfoExtended.
    """
    if explicit:
        return explicit
    try:
        device = find_ipod()
    except DetectError as e:
        log.print(f"[red]✗[/] {e}")
        log.print("[dim]hint: pass --guid to operate offline[/]")
        return None

    we_mounted = False
    if device.is_mounted:
        assert device.mount_point is not None
        mnt = device.mount_point
    else:
        try:
            result = mount_mod.mount(device)
        except mount_mod.MountError as e:
            log.print(f"[red]✗[/] {e}")
            return None
        mnt = result.mount_point
        we_mounted = True

    try:
        guid = sysinfo.read_firewire_guid(mnt)
    finally:
        if we_mounted:
            with contextlib.suppress(mount_mod.MountError):
                mount_mod.umount_quiet(mnt)
    if not guid:
        log.print("[red]✗[/] could not read FirewireGUID from device")
    return guid


def _emit_table(console: Console, ledger: ld.Ledger) -> None:
    if not ledger.entries:
        console.print("[dim]no consumed entries[/]")
        return
    table = Table(show_lines=False)
    table.add_column("Show", overflow="fold")
    table.add_column("Title", overflow="fold")
    table.add_column("Removed", style="dim")
    table.add_column("sha1", style="dim")
    # Stable order: show, then title.
    rows = sorted(ledger.entries.values(), key=lambda e: (e.show.casefold(), e.title.casefold()))
    for e in rows:
        table.add_row(e.show or "[dim]—[/]", e.title or "[dim]—[/]", e.removed_at, e.sha1[:12])
    console.print(table)
    console.print(f"[dim]{len(ledger.entries)} entry(ies) · {ld.path_for(ledger.guid)}[/]")


def run_list(*, guid: str | None) -> int:
    log = Console(stderr=True)
    g = _resolve_guid(guid, log)
    if not g:
        return 1
    ledger = ld.load(g)
    _emit_table(Console(), ledger)
    return 0


def run_forget(*, pattern: str | None, all_: bool, guid: str | None) -> int:
    log = Console(stderr=True)
    if all_ and pattern:
        log.print("[red]✗[/] pass either <pattern> or --all, not both")
        return 2
    if not all_ and not pattern:
        log.print("[red]✗[/] supply <pattern> or --all")
        return 2

    g = _resolve_guid(guid, log)
    if not g:
        return 1

    ledger = ld.load(g)
    if all_:
        gone = ledger.forget_all()
    else:
        assert pattern is not None
        gone = ledger.forget_pattern(pattern)

    if not gone:
        log.print("[yellow]no matching entries[/]")
        # Still save: a no-op write is harmless and ensures the file exists
        # at the documented schema for downstream tooling.
        ld.save(ledger)
        return 0

    ld.save(ledger)
    log.print(f"[green]✓[/] forgot {len(gone)} entry(ies)")
    for e in gone:
        log.print(f"  [dim]{e.show}/{e.title}[/]")
    return 0
