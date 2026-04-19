"""Host-only environment checks for `ipodsync doctor`.

Phase 2: verifies the Mac is set up to run ipodsync before a device is
plugged in. On-device checks land in phase 15 (`doctor --device`).
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


def run(console: Console | None = None) -> int:
    """Run every check, print a status table, return an exit code."""
    console = console or Console()
    results = [check() for check in CHECKS]

    table = Table(title="ipodsync doctor", show_lines=False)
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

    return 1 if any(r.status == "FAIL" for r in results) else 0
