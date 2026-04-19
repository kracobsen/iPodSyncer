"""Raw mount / unmount for an iPod Classic.

Bypasses `diskutil mount` (blocked on Sequoia 15.4.1+) by invoking
`mount_hfs` / `mount_msdos` directly. Mount points we create live under
`~/Library/Caches/ipodsync/mount/<disk-id>` so eject can tell our mounts
apart from a Finder auto-mount.
"""

from __future__ import annotations

import contextlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ipodsync.device.detect import IpodDevice

MOUNT_ROOT = Path.home() / "Library" / "Caches" / "ipodsync" / "mount"


class MountError(RuntimeError):
    pass


@dataclass(frozen=True)
class MountResult:
    mount_point: Path
    already_mounted: bool
    managed: bool  # True if mount_point is under MOUNT_ROOT (we own it)


def managed_mount_point(device: IpodDevice) -> Path:
    return MOUNT_ROOT / device.whole_disk


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def is_managed(path: Path) -> bool:
    return _is_under(path, MOUNT_ROOT)


def _mount_cmd(device: IpodDevice, mount_point: Path) -> list[str]:
    if device.filesystem == "hfs":
        return ["sudo", "/sbin/mount_hfs", device.dev_node, str(mount_point)]
    if device.filesystem == "msdos":
        return ["sudo", "/sbin/mount_msdos", device.dev_node, str(mount_point)]
    raise MountError(
        f"unsupported filesystem {device.filesystem!r} on {device.data_partition}"
    )


def plan(device: IpodDevice) -> tuple[Path, list[str] | None]:
    """Return (mount_point, cmd). cmd is None when the iPod is already mounted."""
    if device.is_mounted:
        assert device.mount_point is not None
        return device.mount_point, None
    mnt = managed_mount_point(device)
    return mnt, _mount_cmd(device, mnt)


def mount(device: IpodDevice) -> MountResult:
    if device.is_mounted:
        assert device.mount_point is not None
        return MountResult(
            mount_point=device.mount_point,
            already_mounted=True,
            managed=is_managed(device.mount_point),
        )

    mnt = managed_mount_point(device)
    mnt.mkdir(parents=True, exist_ok=True)
    cmd = _mount_cmd(device, mnt)
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise MountError(
            f"mount failed (exit {e.returncode}): {' '.join(cmd)}"
        ) from e
    return MountResult(mount_point=mnt, already_mounted=False, managed=True)


def umount_quiet(mount_point: Path) -> None:
    """Unmount a path we created, without spinning the disk down.

    Used by read-only commands (e.g. `ls`) that auto-mounted the iPod and want
    to clean up after themselves without the side-effect of `diskutil eject`.
    No-op if the path isn't one of our managed mounts.
    """
    if not is_managed(mount_point):
        return
    try:
        subprocess.run(["sudo", "/sbin/umount", str(mount_point)], check=True)
    except subprocess.CalledProcessError as e:
        raise MountError(f"umount failed (exit {e.returncode}) on {mount_point}") from e
    with contextlib.suppress(OSError):
        mount_point.rmdir()


def unmount(device: IpodDevice) -> Path | None:
    """Unmount + spin the iPod down. Returns the prior mount point (if any)."""
    mnt = device.mount_point
    if mnt is not None and is_managed(mnt):
        # Our own mount — unmount it explicitly so we can clean the mount dir.
        try:
            subprocess.run(["sudo", "/sbin/umount", str(mnt)], check=True)
        except subprocess.CalledProcessError as e:
            raise MountError(f"umount failed (exit {e.returncode}) on {mnt}") from e
        with contextlib.suppress(OSError):
            mnt.rmdir()

    # Spin down the whole disk so it's safe to physically unplug.
    try:
        subprocess.run(
            ["diskutil", "eject", device.whole_disk],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        raise MountError(
            f"diskutil eject {device.whole_disk} failed: {e.stderr.decode().strip()}"
        ) from e
    return mnt
