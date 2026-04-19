"""Typer entrypoint for the `ipodsync` CLI.

Phase 1: every top-level command is a stub that prints "not implemented" and
exits 0. Subsequent phases fill these in.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ipodsync import __version__
from ipodsync import add as add_mod
from ipodsync import doctor as doctor_mod
from ipodsync import ls as ls_mod
from ipodsync import restore as restore_mod
from ipodsync import rm as rm_mod
from ipodsync import sync as sync_mod
from ipodsync.device import ops as device_ops

app = typer.Typer(
    name="ipodsync",
    help="Sync music, podcasts, and audiobooks to an iPod Classic 6G.",
    no_args_is_help=True,
    add_completion=False,
)

playlist_app = typer.Typer(
    name="playlist",
    help="Manage playlists on the iPod.",
    no_args_is_help=True,
)
app.add_typer(playlist_app)


@app.callback()
def _root(
    ctx: typer.Context,
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Refuse to transcode: fail instead of re-encoding non-native codecs.",
    ),
) -> None:
    ctx.obj = {"strict": strict}


def _stub(cmd: str) -> None:
    typer.echo(f"ipodsync {cmd}: not implemented yet (see plans/ipodsyncer-v0.1.md)")


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def mount(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show the mount plan without touching the device."
    ),
) -> None:
    """Mount a connected iPod Classic (bypasses Finder / diskutil)."""
    raise typer.Exit(code=device_ops.run_mount(dry_run=dry_run))


@app.command(name="ls")
def ls_(
    kind: str | None = typer.Option(
        None,
        "--kind",
        help="Filter: music | podcast | book",
        case_sensitive=False,
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Emit stable JSON on stdout instead of a table."
    ),
) -> None:
    """List tracks on the mounted iPod (read-only)."""
    raise typer.Exit(code=ls_mod.run(kind=kind, as_json=as_json))


@app.command()
def add(
    ctx: typer.Context,
    file: Path = typer.Argument(  # noqa: B008  (typer idiom)
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Audio file (mp3/m4a/flac/opus/ogg/wav/aiff …)",
    ),
) -> None:
    """Add a single audio file to the iPod; transcodes if the codec isn't native."""
    strict = bool((ctx.obj or {}).get("strict", False))
    raise typer.Exit(code=add_mod.run(file, strict=strict))


@app.command()
def rm(
    track_ids: list[int] = typer.Argument(  # noqa: B008
        None,
        metavar="[TRACK_IDS]...",
        help="iTunesDB track id(s) to delete (see `ipodsync ls`).",
    ),
    filter_expr: str | None = typer.Option(
        None,
        "--filter",
        metavar="KEY=VALUE",
        help="Match title/artist/album/genre (case-insensitive equality).",
    ),
    kind: str | None = typer.Option(
        None,
        "--kind",
        help="Constrain to music | podcast | book.",
        case_sensitive=False,
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be deleted without writing."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Remove tracks from the iPod. Deletes the F## file and the DB row."""
    raise typer.Exit(
        code=rm_mod.run(
            list(track_ids or []),
            filter_expr=filter_expr,
            kind=kind,
            dry_run=dry_run,
            assume_yes=yes,
        )
    )


@app.command()
def sync(
    ctx: typer.Context,
    source: Path = typer.Argument(  # noqa: B008
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Source directory; music scanned under <src>/music/**",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the plan without touching the device's DB."
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Also remove on-device tracks that are no longer in the source tree.",
    ),
) -> None:
    """Mirror ``<src>/music/**`` to the iPod (music-only for now; idempotent)."""
    strict = bool((ctx.obj or {}).get("strict", False))
    raise typer.Exit(
        code=sync_mod.run(source, strict=strict, dry_run=dry_run, prune=prune)
    )


@app.command()
def doctor() -> None:
    """Check host (and device, when mounted) for common setup problems."""
    raise typer.Exit(code=doctor_mod.run())


@app.command()
def eject() -> None:
    """Unmount the iPod cleanly and spin the disk down."""
    raise typer.Exit(code=device_ops.run_eject())


@app.command()
def snapshot() -> None:
    """Copy iTunesDB/iTunesCDB/ArtworkDB to the local snapshots dir."""
    raise typer.Exit(code=restore_mod.run_snapshot())


@app.command()
def restore(
    snapshot: str | None = typer.Option(
        None,
        "--snapshot",
        help="Timestamp (YYYYMMDDTHHMMSSZ) or 'latest'. Omit to list.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """List snapshots, or roll back to one with --snapshot."""
    raise typer.Exit(
        code=restore_mod.run_restore(selector=snapshot, assume_yes=yes)
    )


@playlist_app.command("create")
def playlist_create() -> None:
    """Create a new playlist."""
    _stub("playlist create")


@playlist_app.command("add")
def playlist_add() -> None:
    """Add tracks to an existing playlist."""
    _stub("playlist add")


@playlist_app.command("rm")
def playlist_rm() -> None:
    """Remove a playlist or tracks from a playlist."""
    _stub("playlist rm")


if __name__ == "__main__":
    app()
