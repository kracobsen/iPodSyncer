"""`ipodsync rm` — delete tracks from the iPod.

Selection:
- positional ``TRACK_IDS``: remove those ids (must all resolve)
- ``--filter KEY=VALUE``: case-insensitive equality on title/artist/album/genre
- ``--kind music|podcast|book``: additionally constrain by mediatype

All selectors intersect. At least one selector is required — blind ``rm``
with no criteria is refused, to keep a typo from wiping the device.

Every delete snapshots first, removes the track from all playlists, deletes
the F## file, unlinks the iTunesDB row, and (on success) persists via
``itdb_write`` + hash58.
"""

from __future__ import annotations

from dataclasses import dataclass

import typer
from rich.console import Console
from rich.table import Table

from ipodsync.device import gpod as gpod_facade
from ipodsync.device import mount as mount_mod
from ipodsync.device import snapshot as snap
from ipodsync.device import sysinfo
from ipodsync.device.detect import DetectError, find_ipod
from ipodsync.device.gpod import Kind, TrackInfo

_KIND_FILTERS: dict[str, Kind] = {
    "music": Kind.MUSIC,
    "podcast": Kind.PODCAST,
    "book": Kind.AUDIOBOOK,
}

_FILTER_FIELDS = {"title", "artist", "album", "genre"}


@dataclass(frozen=True)
class _Filter:
    field: str
    value: str  # already lower-cased

    @classmethod
    def parse(cls, expr: str) -> _Filter:
        if "=" not in expr:
            raise ValueError("filter must be KEY=VALUE")
        key, val = expr.split("=", 1)
        key = key.strip().lower()
        val = val.strip()
        if key not in _FILTER_FIELDS:
            raise ValueError(
                f"unknown filter field {key!r}; expected one of "
                + ", ".join(sorted(_FILTER_FIELDS))
            )
        if not val:
            raise ValueError("filter value is empty")
        return cls(field=key, value=val.lower())

    def matches(self, t: TrackInfo) -> bool:
        return str(getattr(t, self.field)).lower() == self.value


def _match(
    t: TrackInfo,
    id_set: set[int],
    filt: _Filter | None,
    kind: Kind | None,
) -> bool:
    if id_set and t.id not in id_set:
        return False
    if filt is not None and not filt.matches(t):
        return False
    return not (kind is not None and t.kind != kind)


def _preview(console: Console, tracks: list[TrackInfo]) -> None:
    table = Table(show_lines=False)
    table.add_column("ID", justify="right", style="dim")
    table.add_column("Title", overflow="fold")
    table.add_column("Artist", overflow="fold")
    table.add_column("Album", overflow="fold")
    table.add_column("Kind")
    for t in tracks:
        table.add_row(
            str(t.id),
            t.title or "[dim]—[/]",
            t.artist or "[dim]—[/]",
            t.album or "[dim]—[/]",
            t.kind.value,
        )
    console.print(table)


def run(
    track_ids: list[int],
    *,
    filter_expr: str | None = None,
    kind: str | None = None,
    dry_run: bool = False,
    assume_yes: bool = False,
    console: Console | None = None,
) -> int:
    log = console or Console(stderr=True)

    if not track_ids and not filter_expr and not kind:
        log.print(
            "[red]✗[/] refusing to delete without a selector; "
            "pass TRACK_IDS, --filter, or --kind"
        )
        return 2

    filt: _Filter | None = None
    if filter_expr is not None:
        try:
            filt = _Filter.parse(filter_expr)
        except ValueError as e:
            log.print(f"[red]✗[/] {e}")
            return 2

    kind_sel: Kind | None = None
    if kind is not None:
        if kind not in _KIND_FILTERS:
            log.print(
                f"[red]✗[/] invalid --kind {kind!r}; expected one of "
                + ", ".join(_KIND_FILTERS)
            )
            return 2
        kind_sel = _KIND_FILTERS[kind]

    id_set = set(track_ids)

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
                candidates = [
                    t for t in gpod_facade.iter_tracks(db)
                    if _match(t, id_set, filt, kind_sel)
                ]
        except gpod_facade.GpodImportError as e:
            log.print(f"[red]✗[/] {e}")
            return 1
        except gpod_facade.DbOpenError as e:
            log.print(f"[red]✗[/] could not read iTunesDB: {e}")
            return 1

        if id_set:
            matched_ids = {t.id for t in candidates}
            missing = id_set - matched_ids
            if missing:
                log.print(
                    "[red]✗[/] no track(s) with id: "
                    + ", ".join(str(i) for i in sorted(missing))
                )
                return 2

        if not candidates:
            log.print("[yellow]![/] no tracks match")
            return 0

        _preview(Console(stderr=True), candidates)
        log.print(f"plan: delete {len(candidates)} track(s)")

        if dry_run:
            log.print("[yellow]--dry-run: exiting without writes[/]")
            return 0

        if not assume_yes and not typer.confirm(
            f"Delete {len(candidates)} track(s) from {mnt}?",
            default=False,
        ):
            log.print("[yellow]aborted[/]")
            return 1

        try:
            pre = snap.create(mnt, guid)
        except snap.SnapshotError as e:
            log.print(f"[red]✗[/] snapshot failed: {e}")
            return 1
        log.print(f"[dim]snapshot {pre.timestamp}[/]")

        ids_to_remove = {t.id for t in candidates}
        removed = 0
        try:
            with gpod_facade.open_readwrite(mnt) as db:
                targets = [
                    w for _, w, _ in gpod_facade.iter_track_wrappers(db)
                    if int(w._track.id) in ids_to_remove
                ]
                for w in targets:
                    gpod_facade.remove_track(db, w)
                    removed += 1
        except gpod_facade.DbWriteError as e:
            log.print(f"[red]✗[/] write failed: {e}")
            log.print(
                f"[dim]  → roll back: ipodsync restore --snapshot {pre.timestamp}[/]"
            )
            return 1

        log.print(f"[green]✓[/] deleted {removed} track(s)")
        return 0
    finally:
        if we_mounted:
            try:
                mount_mod.umount_quiet(mnt)
            except mount_mod.MountError as e:
                log.print(f"[yellow]![/] cleanup umount failed: {e}")
