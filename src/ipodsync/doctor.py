"""Environment checks for `ipodsync doctor`.

Default mode (phase 2): host-only — confirms the Mac is ready before a
device is plugged in. ``--device`` (phase 15) adds on-device checks: needs
an iPod that's already mounted (we don't auto-mount during a diagnostic).
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.table import Table

from ipodsync.device import gpod as gpod_facade
from ipodsync.device import snapshot as snap
from ipodsync.device import sysinfo
from ipodsync.device.detect import DetectError, IpodDevice, find_ipod

Status = Literal["OK", "WARN", "FAIL"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str
    fix: str | None = None


def _check_macos() -> CheckResult:
    if platform.system() != "Darwin":
        return CheckResult(
            "macOS",
            "FAIL",
            f"not macOS (running {platform.system()})",
            fix="ipodsync targets macOS only",
        )
    ver = platform.mac_ver()[0] or "unknown"
    major = int(ver.split(".", 1)[0]) if ver[:1].isdigit() else 0
    # Sequoia = 15, Tahoe = 26. Anything older is untested territory.
    if major and major < 15:
        return CheckResult(
            "macOS",
            "WARN",
            f"{ver} — untested on pre-Sequoia",
            fix="ipodsync is developed against Sequoia 15.x / Tahoe 26.x",
        )
    return CheckResult("macOS", "OK", ver)


def _check_python() -> CheckResult:
    v = sys.version_info
    label = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) < (3, 12):
        return CheckResult(
            "Python",
            "FAIL",
            f"{label} — need 3.12+",
            fix="install Python 3.12+ (e.g. `brew install python@3.12`)",
        )
    return CheckResult("Python", "OK", label)


def _tool_version(name: str) -> str | None:
    try:
        out = subprocess.run(
            [name, "-version"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    first = (out.stdout or out.stderr).splitlines()
    return first[0] if first else None


def _check_ffmpeg_tool(name: str) -> CheckResult:
    path = shutil.which(name)
    if not path:
        return CheckResult(
            name,
            "FAIL",
            "not on PATH",
            fix="install ffmpeg: `brew install ffmpeg` (provides both ffmpeg and ffprobe)",
        )
    return CheckResult(name, "OK", _tool_version(name) or f"found at {path}")


def _check_libfdk_aac() -> CheckResult:
    from ipodsync.pipeline.transcode import _has_libfdk_aac

    if _has_libfdk_aac():
        return CheckResult("libfdk_aac", "OK", "available — used for non-native → AAC")
    return CheckResult(
        "libfdk_aac",
        "WARN",
        "not available — falls back to ffmpeg's built-in `aac` (lower quality)",
        fix=(
            "`brew uninstall ffmpeg && brew tap homebrew-ffmpeg/ffmpeg && "
            "brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-fdk-aac`"
        ),
    )


def _check_libgpod() -> CheckResult:
    try:
        import gpod  # type: ignore[import-not-found]
    except ImportError as e:
        return CheckResult(
            "libgpod",
            "FAIL",
            f"cannot import gpod: {e}",
            fix="run `./scripts/bootstrap.sh` from the repo root",
        )
    return CheckResult("libgpod", "OK", f"python-gpod {gpod.version}")


def _check_fda() -> CheckResult:
    home = Path.home()
    candidates = [home / "Library/Mail", home / "Library/Safari"]
    existing = [p for p in candidates if p.exists()]
    if not existing:
        return CheckResult(
            "Full Disk Access",
            "WARN",
            "inconclusive — no TCC-gated dirs found in ~/Library to probe",
            fix=(
                "grant FDA to your terminal in System Settings → "
                "Privacy & Security → Full Disk Access"
            ),
        )
    for p in existing:
        try:
            next(iter(p.iterdir()), None)
        except PermissionError:
            return CheckResult(
                "Full Disk Access",
                "FAIL",
                f"permission denied on {p}",
                fix=(
                "grant FDA to your terminal in System Settings → "
                "Privacy & Security → Full Disk Access"
            ),
            )
    return CheckResult("Full Disk Access", "OK", f"readable: ~/Library/{existing[0].name}")


def _check_pkg_manager() -> CheckResult:
    found: list[str] = []
    if shutil.which("brew"):
        found.append("Homebrew")
    if shutil.which("port"):
        found.append("MacPorts")
    if not found:
        return CheckResult(
            "package manager",
            "WARN",
            "neither Homebrew nor MacPorts found",
            fix=(
                "install one (https://brew.sh/ or https://macports.org/) "
                "to run scripts/bootstrap.sh"
            ),
        )
    return CheckResult("package manager", "OK", " + ".join(found))


CHECKS: tuple[Callable[[], CheckResult], ...] = (
    _check_macos,
    _check_python,
    lambda: _check_ffmpeg_tool("ffmpeg"),
    lambda: _check_ffmpeg_tool("ffprobe"),
    _check_libfdk_aac,
    _check_libgpod,
    _check_fda,
    _check_pkg_manager,
)


# --- device checks (phase 15) ----------------------------------------------


def _human_bytes(n: int) -> str:
    """1024-based, two decimals, IEC suffixes."""
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.2f} {u}" if u != "B" else f"{int(f)} B"
        f /= 1024
    return f"{n} B"  # unreachable


def _check_rockbox(mp: Path) -> CheckResult:
    if sysinfo.is_rockbox(mp):
        return CheckResult(
            "Rockbox",
            "FAIL",
            ".rockbox/ present — ipodsync targets factory firmware only",
            fix="boot the iPod into Apple firmware (or wipe Rockbox) before syncing",
        )
    return CheckResult("Rockbox", "OK", "not installed")


def _check_guid(mp: Path) -> CheckResult:
    guid = sysinfo.read_firewire_guid(mp)
    if not guid:
        return CheckResult(
            "FireWireGUID",
            "FAIL",
            "could not read SysInfo nor derive from ioreg",
            fix=(
                "ensure SysInfoExtended is present at "
                "iPod_Control/Device/SysInfoExtended (run `scripts/bootstrap.sh`)"
            ),
        )
    return CheckResult("FireWireGUID", "OK", guid)


def _check_free_space(mp: Path) -> CheckResult:
    try:
        usage = shutil.disk_usage(mp)
    except OSError as e:
        return CheckResult("free space", "FAIL", f"disk_usage failed: {e}")
    pct = (usage.free / usage.total * 100) if usage.total else 0
    return CheckResult(
        "free space",
        "OK",
        f"{_human_bytes(usage.free)} free / {_human_bytes(usage.total)} total ({pct:.1f}%)",
    )


def _check_dirs(mp: Path) -> CheckResult:
    itunes = mp / "iPod_Control" / "iTunes"
    music = mp / "iPod_Control" / "Music"
    artwork = mp / "iPod_Control" / "Artwork"

    if not itunes.is_dir():
        return CheckResult("iPod dirs", "FAIL", f"missing {itunes.relative_to(mp)}")
    if not music.is_dir():
        return CheckResult("iPod dirs", "FAIL", f"missing {music.relative_to(mp)}")

    f_dirs = [
        d for d in music.iterdir()
        if d.is_dir() and len(d.name) == 3 and d.name.startswith("F") and d.name[1:].isdigit()
    ]
    detail = f"iTunes ✓, Music ✓ ({len(f_dirs)} F## dirs)"
    if not artwork.is_dir():
        return CheckResult(
            "iPod dirs",
            "WARN",
            detail + ", Artwork missing",
            fix=(
                "artwork won't render until iPod_Control/Artwork exists; "
                "libgpod creates it on first artwork write"
            ),
        )
    return CheckResult("iPod dirs", "OK", detail + ", Artwork ✓")


def _check_db_roundtrip(mp: Path) -> CheckResult:
    try:
        with gpod_facade.open_readonly(mp) as db:
            n = len(db)
    except gpod_facade.GpodImportError as e:
        return CheckResult("iTunesDB", "FAIL", str(e))
    except gpod_facade.DbOpenError as e:
        return CheckResult(
            "iTunesDB",
            "FAIL",
            f"libgpod could not parse iTunesDB: {e}",
            fix="restore from a snapshot (`ipodsync restore --snapshot latest`)",
        )
    return CheckResult("iTunesDB", "OK", f"parsed; {n} track(s)")


def _check_track_counts(mp: Path) -> CheckResult:
    counts = {k: 0 for k in gpod_facade.Kind}
    try:
        with gpod_facade.open_readonly(mp) as db:
            for t in gpod_facade.iter_tracks(db):
                counts[t.kind] = counts.get(t.kind, 0) + 1
    except gpod_facade.DbOpenError as e:
        return CheckResult("track counts", "FAIL", str(e))
    parts = [
        f"music={counts[gpod_facade.Kind.MUSIC]}",
        f"podcast={counts[gpod_facade.Kind.PODCAST]}",
        f"book={counts[gpod_facade.Kind.AUDIOBOOK]}",
    ]
    if counts.get(gpod_facade.Kind.OTHER):
        parts.append(f"other={counts[gpod_facade.Kind.OTHER]}")
    return CheckResult("track counts", "OK", " ".join(parts))


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _check_snapshots(guid: str) -> CheckResult:
    snaps = snap.list_snapshots(guid)
    if not snaps:
        return CheckResult("snapshots", "OK", "none yet (created on first mutating sync)")
    total = sum(_dir_size(s.path) for s in snaps)
    return CheckResult(
        "snapshots",
        "OK",
        f"{len(snaps)} kept ({_human_bytes(total)} total); newest {snaps[-1].timestamp}",
    )


def _device_checks() -> list[CheckResult]:
    """Detect → mount → Rockbox → rest. Each gating failure short-circuits."""
    out: list[CheckResult] = []
    try:
        dev: IpodDevice = find_ipod()
    except DetectError as e:
        out.append(CheckResult(
            "device detect",
            "FAIL",
            str(e),
            fix="plug in an iPod Classic 6G via USB",
        ))
        return out
    out.append(CheckResult(
        "device detect",
        "OK",
        f"{dev.model_name} ({dev.whole_disk}, {dev.filesystem})",
    ))

    if not dev.is_mounted:
        out.append(CheckResult(
            "mount",
            "WARN",
            "iPod not mounted — device checks need a mount",
            fix="run `ipodsync mount` first",
        ))
        return out
    assert dev.mount_point is not None
    mp = dev.mount_point
    out.append(CheckResult("mount", "OK", str(mp)))

    rockbox = _check_rockbox(mp)
    out.append(rockbox)
    if rockbox.status == "FAIL":
        return out

    out.append(_check_guid(mp))
    out.append(_check_free_space(mp))
    out.append(_check_dirs(mp))
    out.append(_check_db_roundtrip(mp))
    out.append(_check_track_counts(mp))

    guid = sysinfo.read_firewire_guid(mp)
    if guid:
        out.append(_check_snapshots(guid))
    return out


def _render(console: Console, title: str, results: list[CheckResult]) -> None:
    table = Table(title=title, show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    style = {"OK": "green", "WARN": "yellow", "FAIL": "red"}
    for r in results:
        table.add_row(r.name, f"[{style[r.status]}]{r.status}[/]", r.detail)
    console.print(table)
    fixes = [r for r in results if r.fix and r.status != "OK"]
    if fixes:
        console.print()
        for r in fixes:
            console.print(f"[dim]  → {r.name}:[/] {r.fix}")


def run(*, device: bool = False, console: Console | None = None) -> int:
    """Run host (and optionally device) checks; return a non-zero exit on FAIL."""
    console = console or Console()
    host = [check() for check in CHECKS]
    _render(console, "ipodsync doctor", host)

    dev_results: list[CheckResult] = []
    if device:
        console.print()
        dev_results = _device_checks()
        _render(console, "ipodsync doctor — device", dev_results)

    return 1 if any(r.status == "FAIL" for r in (*host, *dev_results)) else 0
