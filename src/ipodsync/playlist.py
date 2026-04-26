"""M3U playlist support for ``ipodsync sync``.

Phase 14. Each ``*.m3u`` / ``*.m3u8`` under ``<src>/playlists/`` becomes a
playlist on device named after the file's basename. Entries are resolved
against the source tree (per the spec) with a fallback to the M3U's parent
directory; absolute paths are honoured as-is. Missing entries are skipped
with a warning, never abort.

Ownership is tracked per-device in
``~/Library/Application Support/ipodsync/playlists/<guid>.json``. The ledger
records playlist names that ipodsync created so ``--prune`` only removes
*its own* playlists when the M3U disappears — manually-created device
playlists with arbitrary names are never touched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

LEDGER_ROOT = (
    Path.home() / "Library" / "Application Support" / "ipodsync" / "playlists"
)
M3U_EXT = frozenset({".m3u", ".m3u8"})


class PlaylistError(RuntimeError):
    pass


@dataclass(frozen=True)
class M3UPlaylist:
    name: str                         # basename without extension
    m3u_path: Path
    entries: tuple[Path, ...]         # resolved absolute paths in the filesystem
    warnings: tuple[str, ...]         # parse-time complaints, prefixed-readable


def walk_m3us(src: Path) -> list[Path]:
    root = src / "playlists"
    if not root.is_dir():
        return []
    return sorted(
        p
        for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in M3U_EXT
    )


def parse_m3u(m3u: Path, src: Path) -> M3UPlaylist:
    """Parse an M3U/M3U8 file. Comments (``#``) and blank lines are ignored."""
    entries: list[Path] = []
    warnings: list[str] = []
    try:
        text = m3u.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = m3u.read_text(encoding="latin-1")

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        candidate = Path(line)
        if candidate.is_absolute():
            resolved = candidate
        else:
            cand_src = src / line
            cand_m3u = m3u.parent / line
            if cand_src.exists():
                resolved = cand_src
            elif cand_m3u.exists():
                resolved = cand_m3u
            else:
                warnings.append(f"unresolved entry {line!r}")
                continue
        try:
            resolved = resolved.resolve()
        except OSError:
            warnings.append(f"cannot resolve {line!r}")
            continue
        if not resolved.is_file():
            warnings.append(f"missing file {line!r}")
            continue
        entries.append(resolved)

    return M3UPlaylist(
        name=m3u.stem,
        m3u_path=m3u,
        entries=tuple(entries),
        warnings=tuple(warnings),
    )


def ledger_path(guid: str) -> Path:
    return LEDGER_ROOT / f"{guid}.json"


def load_ledger(guid: str) -> set[str]:
    """Return the set of playlist names ipodsync owns on this device."""
    p = ledger_path(guid)
    if not p.is_file():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if isinstance(data, dict):
        owned = data.get("owned")
        if isinstance(owned, list):
            return {str(x) for x in owned}
    return set()


def save_ledger(guid: str, names: set[str]) -> None:
    p = ledger_path(guid)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"owned": sorted(names)}
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
