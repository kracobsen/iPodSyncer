"""Thin, read-oriented facade over `python-gpod`.

Phase 4 only needs to *read* the iTunesDB. We deliberately avoid `Database.close()`
because it calls `itdb_write` and would mutate the DB on exit. The context manager
here opens the DB, hands it back, and relies on Python GC + `itdb_free` for
cleanup — no writes happen.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class Kind(StrEnum):
    MUSIC = "music"
    PODCAST = "podcast"
    AUDIOBOOK = "audiobook"
    OTHER = "other"


@dataclass(frozen=True)
class TrackInfo:
    id: int
    title: str
    artist: str
    album: str
    kind: Kind
    size: int          # bytes
    duration_ms: int   # tracklen, ms
    ipod_path: str     # colon-form on-device path, "" if missing


class GpodImportError(RuntimeError):
    """`import gpod` failed — the bootstrap script hasn't been run."""


class DbOpenError(RuntimeError):
    """Raised when libgpod cannot parse the iTunesDB at the mount point."""


def _require_gpod() -> Any:
    try:
        import gpod  # type: ignore[import-not-found]
    except ImportError as e:
        raise GpodImportError(
            "python-gpod bindings not importable — run scripts/bootstrap.sh"
        ) from e
    return gpod


def kind_from_mediatype(mt: int | None) -> Kind:
    """Classify an `Itdb_Track.mediatype` bitfield.

    mediatype 0 (unset on legacy iTunes-added tracks) is treated as music.
    When multiple bits are set the priority is audiobook > podcast > music.
    """
    if mt is None:
        return Kind.MUSIC
    if mt & 0x08:
        return Kind.AUDIOBOOK
    if mt & 0x04:
        return Kind.PODCAST
    if mt == 0 or mt & 0x01:
        return Kind.MUSIC
    return Kind.OTHER


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return str(v)


def _i(v: Any) -> int:
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _track_info(track: Any) -> TrackInfo:
    return TrackInfo(
        id=_i(getattr(track, "id", 0)),
        title=_s(getattr(track, "title", None)),
        artist=_s(getattr(track, "artist", None)),
        album=_s(getattr(track, "album", None)),
        kind=kind_from_mediatype(getattr(track, "mediatype", 0)),
        size=_i(getattr(track, "size", 0)),
        duration_ms=_i(getattr(track, "tracklen", 0)),
        ipod_path=_s(getattr(track, "ipod_path", None)),
    )


@contextmanager
def open_readonly(mount_point: Path) -> Iterator[Any]:
    """Open the iTunesDB at `mount_point` without ever writing it back."""
    gpod = _require_gpod()
    try:
        db = gpod.Database(str(mount_point))
    except gpod.DatabaseException as e:
        raise DbOpenError(str(e)) from e
    try:
        yield db
    finally:
        # No close() — that would call itdb_write. Let GC run itdb_free.
        del db


def iter_tracks(db: Any) -> Iterator[TrackInfo]:
    for i in range(len(db)):
        yield _track_info(db[i]._track)
