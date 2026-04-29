"""Per-device exclusion lock for write-path commands.

Two concurrent ``ipodsync`` processes against the same iPod will both call
``itdb_write`` + hash58 on close, last writer wins, and any add/remove from
the loser is silently dropped. The lock is keyed by FireWireGUID so two
different iPods can sync concurrently — only same-device is exclusive.

Read-only commands (``ls``, ``doctor``) don't take the lock; they tolerate a
concurrent writer landing a new DB beneath them.
"""

from __future__ import annotations

import contextlib
import fcntl
from collections.abc import Iterator
from pathlib import Path

_APP_SUPPORT = Path.home() / "Library" / "Application Support" / "ipodsync"


class LockError(RuntimeError):
    pass


def lock_path(guid: str) -> Path:
    return _APP_SUPPORT / guid / "lock"


@contextlib.contextmanager
def device_lock(guid: str) -> Iterator[None]:
    """Hold an exclusive ``flock`` on the per-device lock file.

    Raises ``LockError`` immediately if another ipodsync process holds it
    (``LOCK_NB``) — we don't want to silently queue behind a runaway sync.
    """
    p = lock_path(guid)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = p.open("w")
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise LockError(
                f"another ipodsync is running for device {guid} (lock: {p})"
            ) from e
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        fd.close()
