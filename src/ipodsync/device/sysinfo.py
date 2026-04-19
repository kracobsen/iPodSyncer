"""Read-only probes of an iPod's on-disk metadata.

Phase 3: just what `mount` needs — FireWireGUID (for hash58 downstream) and
Rockbox detection (which we refuse to sync to).

On iPod Classic 6G the `iPod_Control/Device/SysInfo` text file is often empty
until iTunes populates it. Apple encodes the FireWire GUID as the USB serial
number on that model, so when SysInfo is blank we fall back to reading
`kUSBSerialNumberString` from ioreg — same trick libgpod uses internally.
"""

from __future__ import annotations

import plistlib
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

SYSINFO_REL = Path("iPod_Control/Device/SysInfo")
ROCKBOX_REL = Path(".rockbox")

_GUID_RE = re.compile(r"^\s*FirewireGuid\s*:\s*(0x[0-9A-Fa-f]+)\s*$", re.MULTILINE)


def sysinfo_path(mount_point: Path) -> Path:
    return mount_point / SYSINFO_REL


def is_rockbox(mount_point: Path) -> bool:
    return (mount_point / ROCKBOX_REL).is_dir()


def _read_guid_from_sysinfo(mount_point: Path) -> str | None:
    path = sysinfo_path(mount_point)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _GUID_RE.search(text)
    return m.group(1) if m else None


def _walk(nodes: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for n in nodes:
        yield n
        yield from _walk(n.get("IORegistryEntryChildren") or [])


def _read_guid_from_ioreg() -> str | None:
    """Derive FirewireGUID from an iPod USB device's serial number."""
    try:
        out = subprocess.run(
            ["ioreg", "-a", "-l", "-r", "-c", "IOUSBHostDevice"],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    try:
        roots = plistlib.loads(out.stdout)
    except Exception:
        return None

    hits: list[str] = []
    for node in _walk(roots):
        if node.get("IORegistryEntryName") != "iPod":
            continue
        serial = node.get("kUSBSerialNumberString") or node.get("USB Serial Number")
        if isinstance(serial, str) and re.fullmatch(r"[0-9A-Fa-f]{16}", serial):
            hits.append(serial)
    if len(hits) != 1:
        return None
    return f"0x{hits[0].upper()}"


def read_firewire_guid(mount_point: Path) -> str | None:
    """Return `0x…` FireWire GUID from SysInfo, falling back to ioreg."""
    return _read_guid_from_sysinfo(mount_point) or _read_guid_from_ioreg()
