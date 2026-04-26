"""Pre-sync DB snapshots + rollback.

Copies `iTunesDB`, `iTunesCDB`, and `ArtworkDB` (when present) off the device
into `~/Library/Application Support/ipodsync/snapshots/<guid>/<timestamp>/`
before any mutating command runs. Each file is staged under a `.tmp` name and
renamed into place, so a crash mid-copy can't leave a partial file at the
final path.

Phase 5 wires this into the standalone `snapshot` + `restore` commands.
Later phases (`add`, `rm`, `sync`) will call `create()` before any DB write.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SNAP_ROOT = Path.home() / "Library" / "Application Support" / "ipodsync" / "snapshots"
DEFAULT_KEEP = 10

# Relative to mount point. Order matters only for display.
DB_FILES: tuple[Path, ...] = (
    Path("iPod_Control/iTunes/iTunesDB"),
    Path("iPod_Control/iTunes/iTunesCDB"),
    Path("iPod_Control/Artwork/ArtworkDB"),
)

# ISO-8601 basic form; no colons → safe on every filesystem we care about.
_TS_FMT = "%Y%m%dT%H%M%SZ"


class SnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snapshot:
    guid: str
    timestamp: str          # "YYYYMMDDTHHMMSSZ"
    path: Path              # directory holding the snapshotted files
    files: tuple[str, ...]  # snapshotted DB paths, relative to mount point


def _guid_dir(guid: str) -> Path:
    return SNAP_ROOT / guid


def _now_ts() -> str:
    return datetime.now(UTC).strftime(_TS_FMT)


def _atomic_copy(src: Path, dst: Path) -> None:
    """Copy src → dst via `<dst>.tmp-ipodsync` + rename. Same-FS rename is atomic."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp-ipodsync")
    shutil.copy2(src, tmp)
    tmp.replace(dst)


def create(mount_point: Path, guid: str, *, keep: int | None = None) -> Snapshot:
    """Copy present DB files into a fresh timestamped snapshot dir.

    Raises SnapshotError if no DB files exist under the mount (e.g. the iPod
    was never initialized by iTunes). When ``keep`` is None, retention is
    pulled from the user config (default ``DEFAULT_KEEP``).
    """
    if keep is None:
        # Imported lazily to keep the device subpackage independent of CLI config.
        from ipodsync.config import get as _get_config

        keep = _get_config().snapshot_retention
    ts = _now_ts()
    guid_dir = _guid_dir(guid)
    guid_dir.mkdir(parents=True, exist_ok=True)

    # Stage the whole snapshot in a hidden dir, rename into place at the end.
    staging = guid_dir / f".{ts}.partial"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()

    copied: list[str] = []
    try:
        for rel in DB_FILES:
            src = mount_point / rel
            if not src.is_file():
                continue
            _atomic_copy(src, staging / rel)
            copied.append(str(rel))

        if not copied:
            raise SnapshotError(
                f"no iPod DB files found under {mount_point}/iPod_Control"
            )

        final = guid_dir / ts
        staging.rename(final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    _prune(guid, keep=keep)
    return Snapshot(guid=guid, timestamp=ts, path=final, files=tuple(copied))


def list_snapshots(guid: str) -> list[Snapshot]:
    """Return snapshots for a device, oldest first."""
    d = _guid_dir(guid)
    if not d.is_dir():
        return []
    out: list[Snapshot] = []
    for p in sorted(d.iterdir()):
        if not p.is_dir() or p.name.startswith("."):
            continue
        files = tuple(str(rel) for rel in DB_FILES if (p / rel).is_file())
        if not files:
            continue  # corrupt/empty snapshot dir — skip
        out.append(Snapshot(guid=guid, timestamp=p.name, path=p, files=files))
    return out


def resolve(guid: str, selector: str) -> Snapshot:
    """Find a snapshot by exact timestamp or the special value 'latest'."""
    snaps = list_snapshots(guid)
    if not snaps:
        raise SnapshotError(f"no snapshots for device {guid}")
    if selector == "latest":
        return snaps[-1]
    for s in snaps:
        if s.timestamp == selector:
            return s
    raise SnapshotError(f"snapshot {selector!r} not found for device {guid}")


def restore(mount_point: Path, snapshot: Snapshot) -> list[str]:
    """Roll the device back to `snapshot`. Per-file atomic via stage + rename."""
    restored: list[str] = []
    for rel in snapshot.files:
        src = snapshot.path / rel
        if not src.is_file():
            continue
        _atomic_copy(src, mount_point / rel)
        restored.append(rel)
    return restored


def _prune(guid: str, *, keep: int) -> None:
    snaps = list_snapshots(guid)
    if len(snaps) <= keep:
        return
    for old in snaps[: len(snaps) - keep]:
        shutil.rmtree(old.path, ignore_errors=True)
