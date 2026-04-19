"""CLI-facing orchestration for `ipodsync mount` and `ipodsync eject`."""

from __future__ import annotations

from rich.console import Console

from ipodsync.device import mount as mount_mod
from ipodsync.device import sysinfo
from ipodsync.device.detect import DetectError, find_ipod


def run_mount(*, dry_run: bool = False, console: Console | None = None) -> int:
    console = console or Console()
    try:
        device = find_ipod()
    except DetectError as e:
        console.print(f"[red]✗[/] {e}")
        return 2

    mnt, cmd = mount_mod.plan(device)

    console.print(f"[bold]device:[/]     {device.model_name} ({device.whole_disk})")
    console.print(f"[bold]partition:[/]  {device.data_partition}  [dim]({device.filesystem})[/]")
    console.print(f"[bold]volume:[/]     {device.volume_name}")

    if dry_run:
        if cmd is None:
            console.print(f"[bold]mount:[/]      already mounted at {mnt}")
        else:
            console.print(f"[bold]mount:[/]      would run [cyan]{' '.join(cmd)}[/]")
        return 0

    try:
        result = mount_mod.mount(device)
    except mount_mod.MountError as e:
        console.print(f"[red]✗[/] {e}")
        return 1

    if result.already_mounted:
        console.print(f"[bold]mount:[/]      already mounted at {result.mount_point}")
    else:
        console.print(f"[bold]mount:[/]      [green]mounted[/] at {result.mount_point}")

    if sysinfo.is_rockbox(result.mount_point):
        console.print(
            "[red]✗[/] Rockbox detected ([dim].rockbox/[/] present). "
            "ipodsync refuses to modify Rockbox-installed iPods."
        )
        return 3

    guid = sysinfo.read_firewire_guid(result.mount_point)
    if guid:
        console.print(f"[bold]FirewireGUID:[/] {guid}")
    else:
        console.print(
            "[yellow]![/] FirewireGUID not found in "
            "iPod_Control/Device/SysInfo — hash58 writes will fail later"
        )
    return 0


def run_eject(console: Console | None = None) -> int:
    console = console or Console()
    try:
        device = find_ipod()
    except DetectError as e:
        console.print(f"[red]✗[/] {e}")
        return 2

    try:
        mnt = mount_mod.unmount(device)
    except mount_mod.MountError as e:
        console.print(f"[red]✗[/] {e}")
        return 1

    if mnt is None:
        console.print(f"[green]ejected[/] {device.whole_disk} (was not mounted)")
    else:
        console.print(f"[green]ejected[/] {device.whole_disk} (was at {mnt})")
    return 0
