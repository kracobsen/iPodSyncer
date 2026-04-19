"""Locate a connected iPod Classic via `diskutil`.

Phase 3: parses `diskutil list -plist` + `diskutil info -plist` to find the
whole disk + data partition belonging to an iPod Classic, along with its
filesystem and any existing mount point.
"""

from __future__ import annotations

import plistlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DetectError(RuntimeError):
    """Raised when zero or multiple iPods are found, or diskutil fails."""


@dataclass(frozen=True)
class IpodDevice:
    whole_disk: str            # e.g. "disk4"
    data_partition: str        # e.g. "disk4s3"
    filesystem: str            # "hfs" | "msdos"
    volume_name: str           # e.g. "iPod"
    model_name: str            # e.g. "iPod"
    mount_point: Path | None   # populated if already mounted

    @property
    def is_mounted(self) -> bool:
        return self.mount_point is not None

    @property
    def dev_node(self) -> str:
        return f"/dev/{self.data_partition}"


def _run_plist(cmd: list[str]) -> dict[str, Any]:
    try:
        out = subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as e:
        raise DetectError(f"command not found: {cmd[0]}") from e
    except subprocess.CalledProcessError as e:
        raise DetectError(
            f"{' '.join(cmd)} failed (exit {e.returncode}): {e.stderr.decode().strip()}"
        ) from e
    return plistlib.loads(out.stdout)


def _info(disk: str) -> dict[str, Any]:
    return _run_plist(["diskutil", "info", "-plist", disk])


def _is_ipod(info: dict[str, Any]) -> bool:
    fields = (
        info.get("MediaName"),
        info.get("DeviceModel"),
        info.get("IORegistryEntryName"),
    )
    return any("ipod" in (f or "").lower() for f in fields)


def _pick_data_partition(whole: dict[str, Any]) -> str | None:
    """Return the DeviceIdentifier of the iPod's user-visible data partition."""
    # Candidates in priority order: explicit HFS/FAT content types.
    for p in whole.get("Partitions", []):
        content = (p.get("Content") or "").lower()
        if content in {"apple_hfs", "apple_hfsx"}:
            return p["DeviceIdentifier"]
        if "fat_32" in content or "fat32" in content or "dos_fat_32" in content:
            return p["DeviceIdentifier"]
    # Fallback: largest partition that has a volume name.
    named = [p for p in whole.get("Partitions", []) if p.get("VolumeName")]
    if named:
        named.sort(key=lambda p: p.get("Size", 0), reverse=True)
        return named[0]["DeviceIdentifier"]
    return None


def _fs_kind(part_info: dict[str, Any]) -> str:
    fstype = (part_info.get("FilesystemType") or "").lower()
    if "hfs" in fstype:
        return "hfs"
    if "msdos" in fstype or "fat" in fstype:
        return "msdos"
    content = (part_info.get("Content") or "").lower()
    if "hfs" in content:
        return "hfs"
    if "fat" in content:
        return "msdos"
    return "unknown"


def find_ipod() -> IpodDevice:
    """Find exactly one connected iPod. Raises DetectError otherwise."""
    listing = _run_plist(["diskutil", "list", "-plist"])
    hits: list[IpodDevice] = []
    for whole in listing.get("AllDisksAndPartitions", []):
        disk_id = whole.get("DeviceIdentifier")
        if not disk_id:
            continue
        info = _info(disk_id)
        if not _is_ipod(info):
            continue
        part_id = _pick_data_partition(whole)
        if part_id is None:
            continue
        part_info = _info(part_id)
        mp = part_info.get("MountPoint") or None
        hits.append(
            IpodDevice(
                whole_disk=disk_id,
                data_partition=part_id,
                filesystem=_fs_kind(part_info),
                volume_name=part_info.get("VolumeName") or "iPod",
                model_name=info.get("MediaName") or info.get("DeviceModel") or "iPod",
                mount_point=Path(mp) if mp else None,
            )
        )

    if not hits:
        raise DetectError(
            "no iPod detected — plug in an iPod Classic via USB and try again"
        )
    if len(hits) > 1:
        names = ", ".join(h.whole_disk for h in hits)
        raise DetectError(f"multiple iPods detected ({names}); v0.1 supports one at a time")
    return hits[0]
