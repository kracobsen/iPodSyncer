"""Typer entrypoint for the `ipodsync` CLI."""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from ipodsync import __version__
from ipodsync import add as add_mod
from ipodsync import config as config_mod
from ipodsync import doctor as doctor_mod
from ipodsync import ls as ls_mod
from ipodsync import rm as rm_mod
from ipodsync import sync as sync_mod
from ipodsync.device import ops as device_ops
from ipodsync.podcasts import cli as podcasts_cli

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

config_app = typer.Typer(
    name="config",
    help="Manage ~/.config/ipodsync/config.toml.",
    no_args_is_help=True,
)
app.add_typer(config_app)

podcasts_app = typer.Typer(
    name="podcasts",
    help="Inspect and forget the consumed-podcast ledger.",
    no_args_is_help=True,
)
app.add_typer(podcasts_app)


@app.callback()
def _root(
    ctx: typer.Context,
    strict: bool = typer.Option(
        False,
        "--strict",
        help="Refuse to transcode: fail instead of re-encoding non-native codecs.",
    ),
) -> None:
    cfg = config_mod.get()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )
    # Flag wins over config; flag absent → fall back to config.strict.
    ctx.obj = {"strict": strict or cfg.strict, "config": cfg}


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
        help="Filter: music | podcast | book (alias: audiobook)",
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
        help="Constrain to music | podcast | book (alias: audiobook).",
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
    source: Path | None = typer.Argument(  # noqa: B008
        None,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help="Source directory; falls back to config.source_dir.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the plan without touching the device's DB."
    ),
    prune: bool = typer.Option(
        False,
        "--prune",
        help="Also remove on-device tracks that are no longer in the source tree.",
    ),
    keep_played: bool = typer.Option(
        False,
        "--keep-played",
        help=(
            "Skip auto-reap of fully-played podcasts for this run. "
            "The consumed-podcast ledger still suppresses re-adds."
        ),
    ),
) -> None:
    """Mirror ``<src>/{music,podcasts,audiobooks}/**`` to the iPod (idempotent)."""
    obj = ctx.obj or {}
    strict = bool(obj.get("strict", False))
    cfg: config_mod.Config = obj.get("config") or config_mod.get()

    chosen = source or cfg.source_dir
    if chosen is None:
        typer.echo(
            "ipodsync sync: no source given and no source_dir set in "
            f"{config_mod.CONFIG_PATH}. Pass a path or run `ipodsync config init`.",
            err=True,
        )
        raise typer.Exit(code=2)
    if not chosen.is_dir():
        typer.echo(f"ipodsync sync: {chosen} is not a directory", err=True)
        raise typer.Exit(code=2)

    raise typer.Exit(
        code=sync_mod.run(
            chosen,
            strict=strict,
            dry_run=dry_run,
            prune=prune,
            keep_played=keep_played,
            auto_reap_played=cfg.auto_reap_played,
        )
    )


@app.command()
def doctor(
    device: bool = typer.Option(
        False,
        "--device",
        help="Also run on-device checks (requires the iPod to be mounted).",
    ),
) -> None:
    """Check host (and, with --device, the mounted iPod) for setup problems."""
    raise typer.Exit(code=doctor_mod.run(device=device))


@app.command()
def eject() -> None:
    """Unmount the iPod cleanly and spin the disk down."""
    raise typer.Exit(code=device_ops.run_eject())


@config_app.command("init")
def config_init(
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite an existing config file."
    ),
) -> None:
    """Write a commented example config to ~/.config/ipodsync/config.toml."""
    written = config_mod.init(force=force)
    if written:
        typer.echo(f"wrote {config_mod.CONFIG_PATH}")
        raise typer.Exit(code=0)
    typer.echo(
        f"{config_mod.CONFIG_PATH} already exists — pass --force to overwrite.",
        err=True,
    )
    raise typer.Exit(code=1)


@config_app.command("show")
def config_show() -> None:
    """Print the resolved config (file values + defaults)."""
    cfg = config_mod.load()
    typer.echo(f"path:               {config_mod.CONFIG_PATH}")
    typer.echo(f"exists:             {config_mod.CONFIG_PATH.is_file()}")
    typer.echo(f"source_dir:         {cfg.source_dir or '(unset)'}")
    typer.echo(f"strict:             {cfg.strict}")
    typer.echo(f"log_level:          {cfg.log_level}")
    typer.echo(f"auto_reap_played:   {cfg.auto_reap_played}")


@podcasts_app.command("list")
def podcasts_list(
    guid: str | None = typer.Option(
        None,
        "--guid",
        help="FirewireGUID of the iPod whose ledger to read. Omit to detect+mount.",
    ),
) -> None:
    """Print consumed-podcast ledger entries as a table."""
    raise typer.Exit(code=podcasts_cli.run_list(guid=guid))


@podcasts_app.command("forget")
def podcasts_forget(
    pattern: str | None = typer.Argument(
        None,
        metavar="[PATTERN]",
        help="Substring matched against `<show>/<episode>` (case-insensitive).",
    ),
    all_: bool = typer.Option(
        False, "--all", help="Forget every entry in the ledger."
    ),
    guid: str | None = typer.Option(
        None,
        "--guid",
        help="FirewireGUID of the iPod whose ledger to mutate. Omit to detect+mount.",
    ),
) -> None:
    """Remove ledger entries so future syncs may re-add them."""
    raise typer.Exit(
        code=podcasts_cli.run_forget(pattern=pattern, all_=all_, guid=guid)
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
