"""Typer entrypoint for the `ipodsync` CLI.

Phase 1: every top-level command is a stub that prints "not implemented" and
exits 0. Subsequent phases fill these in.
"""

from __future__ import annotations

import typer

from ipodsync import __version__

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


def _stub(cmd: str) -> None:
    typer.echo(f"ipodsync {cmd}: not implemented yet (see plans/ipodsyncer-v0.1.md)")


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def mount() -> None:
    """Mount a connected iPod Classic (bypasses Finder / diskutil)."""
    _stub("mount")


@app.command(name="ls")
def ls_() -> None:
    """List tracks on the mounted iPod."""
    _stub("ls")


@app.command()
def add() -> None:
    """Add one or more audio files to the iPod."""
    _stub("add")


@app.command()
def rm() -> None:
    """Remove tracks from the iPod."""
    _stub("rm")


@app.command()
def sync() -> None:
    """Mirror a source directory tree to the iPod."""
    _stub("sync")


@app.command()
def doctor() -> None:
    """Check host (and device, when mounted) for common setup problems."""
    _stub("doctor")


@app.command()
def eject() -> None:
    """Unmount the iPod cleanly."""
    _stub("eject")


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
