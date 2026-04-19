"""`ipodsync ls` — read-only listing of tracks on the mounted iPod.

JSON schema emitted by `--json`:

    {
      "device": {
        "mount_point": "/Users/.../Library/Caches/ipodsync/mount/disk4",
        "firewire_guid": "0x1122334455667788" | null
      },
      "count": 123,
      "tracks": [
        {
          "id":          int,   # iTunesDB track id
          "title":       str,
          "artist":      str,
          "album":       str,
          "kind":        "music" | "podcast" | "audiobook" | "other",
          "size":        int,   # bytes
          "duration_ms": int,   # milliseconds
          "ipod_path":   str    # colon-form on-device path, "" if unknown
        },
        ...
      ]
    }

Any of `title`/`artist`/`album`/`ipod_path` may be the empty string when a
track is missing that tag. The array preserves iTunesDB iteration order.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ipodsync.device import mount as mount_mod
from ipodsync.device import sysinfo
from ipodsync.device.detect import DetectError, find_ipod
from ipodsync.device.gpod import (
    DbOpenError,
    GpodImportError,
    Kind,
    TrackInfo,
    iter_tracks,
    open_readonly,
)

_KIND_FILTERS: dict[str, Kind] = {
    "music": Kind.MUSIC,
    "podcast": Kind.PODCAST,
    "book": Kind.AUDIOBOOK,
}


def _fmt_size(n: int) -> str:
    if n <= 0:
        return "—"
    units = ("B", "KB", "MB", "GB", "TB")
    f = float(n)
    i = 0
    while f >= 1024.0 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    return f"{f:.1f} {units[i]}" if i > 0 else f"{int(f)} {units[i]}"


def _fmt_duration(ms: int) -> str:
    if ms <= 0:
        return "—"
    s = ms // 1000
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _emit_table(console: Console, tracks: list[TrackInfo]) -> None:
    if not tracks:
        console.print("[dim]no tracks on device[/]")
        return
    table = Table(show_lines=False)
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Title", overflow="fold")
    table.add_column("Artist", overflow="fold")
    table.add_column("Album", overflow="fold")
    table.add_column("Kind")
    table.add_column("Size", justify="right")
    table.add_column("Duration", justify="right")
    for t in tracks:
        table.add_row(
            str(t.id),
            t.title or "[dim]—[/]",
            t.artist or "[dim]—[/]",
            t.album or "[dim]—[/]",
            t.kind.value,
            _fmt_size(t.size),
            _fmt_duration(t.duration_ms),
        )
    console.print(table)
    console.print(f"[dim]{len(tracks)} track(s)[/]")


def _emit_json(mount_point: Path, guid: str | None, tracks: list[TrackInfo]) -> None:
    payload = {
        "device": {"mount_point": str(mount_point), "firewire_guid": guid},
        "count": len(tracks),
        "tracks": [
            {**asdict(t), "kind": t.kind.value} for t in tracks
        ],
    }
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def run(
    *,
    kind: str | None = None,
    as_json: bool = False,
    console: Console | None = None,
) -> int:
    # Route human-readable status to stderr so --json stdout stays pipeable.
    log = console or Console(stderr=True)

    filter_kind: Kind | None = None
    if kind is not None:
        if kind not in _KIND_FILTERS:
            log.print(
                f"[red]✗[/] invalid --kind {kind!r}; expected one of "
                + ", ".join(_KIND_FILTERS)
            )
            return 2
        filter_kind = _KIND_FILTERS[kind]

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
            result = mount_mod.mount(device)
        except mount_mod.MountError as e:
            log.print(f"[red]✗[/] {e}")
            return 1
        mnt = result.mount_point
        we_mounted = True

    try:
        if sysinfo.is_rockbox(mnt):
            log.print(
                "[red]✗[/] Rockbox detected — ipodsync refuses to touch "
                "Rockbox-installed iPods."
            )
            return 3

        guid = sysinfo.read_firewire_guid(mnt)

        try:
            with open_readonly(mnt) as db:
                tracks = [
                    t for t in iter_tracks(db)
                    if filter_kind is None or t.kind == filter_kind
                ]
        except GpodImportError as e:
            log.print(f"[red]✗[/] {e}")
            return 1
        except DbOpenError as e:
            log.print(f"[red]✗[/] could not read iTunesDB: {e}")
            return 1

        if as_json:
            _emit_json(mnt, guid, tracks)
        else:
            _emit_table(Console(), tracks)
        return 0
    finally:
        if we_mounted:
            try:
                mount_mod.umount_quiet(mnt)
            except mount_mod.MountError as e:
                log.print(f"[yellow]![/] cleanup umount failed: {e}")
