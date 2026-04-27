"""Optional user config at ``~/.config/ipodsync/config.toml``.

Phase 16. Provides defaults for the global ``--strict`` flag, a fallback
``source_dir`` for ``sync``, and the stdlib logging level. Missing file →
all defaults. Missing keys → per-key default.
"""

from __future__ import annotations

import functools
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "ipodsync" / "config.toml"

EXAMPLE = """\
# ipodsync config — every key is optional. Delete this file to revert to defaults.

# Default source tree for `ipodsync sync`. When set, `ipodsync sync` (no path)
# uses it. Tilde-expanded.
# source_dir = "~/Music/ipod"

# Refuse transcoding by default. Equivalent to passing --strict on every call.
# strict = false

# Stdlib logging level applied at CLI startup. DEBUG | INFO | WARNING | ERROR.
# log_level = "INFO"

# [podcasts]
# # Auto-reap podcast episodes once the iPod marks them fully played
# # (playcount >= 1). The consumed-ledger remembers reaped episodes so a
# # later sync doesn't re-add them. Per-sync override: `sync --keep-played`.
# auto_reap_played = true
"""


@dataclass(frozen=True)
class Config:
    source_dir: Path | None = None
    strict: bool = False
    log_level: str = "INFO"
    auto_reap_played: bool = True


class ConfigError(RuntimeError):
    pass


def load(path: Path = CONFIG_PATH) -> Config:
    if not path.is_file():
        return Config()
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e

    src = raw.get("source_dir")
    podcasts = raw.get("podcasts") or {}
    return Config(
        source_dir=Path(str(src)).expanduser() if src else None,
        strict=bool(raw.get("strict", False)),
        log_level=str(raw.get("log_level", "INFO")).upper(),
        auto_reap_played=bool(podcasts.get("auto_reap_played", True)),
    )


@functools.lru_cache(maxsize=1)
def get() -> Config:
    """Cached config for the current process. Cheap to call repeatedly."""
    return load()


def init(path: Path = CONFIG_PATH, *, force: bool = False) -> bool:
    """Write the commented example. Returns True if written, False if it already existed."""
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE)
    return True
