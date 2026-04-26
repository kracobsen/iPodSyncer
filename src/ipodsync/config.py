"""Optional user config at ``~/.config/ipodsync/config.toml``.

Phase 16. Provides defaults for the global ``--strict`` flag, a fallback
``source_dir`` for ``sync``, the stdlib logging level, and the snapshot
retention count. Missing file → all defaults. Missing keys → per-key default.
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

# Number of pre-write DB snapshots to keep per device. Older ones are pruned
# after each successful snapshot.
# snapshot_retention = 10
"""


@dataclass(frozen=True)
class Config:
    source_dir: Path | None = None
    strict: bool = False
    log_level: str = "INFO"
    snapshot_retention: int = 10


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
    return Config(
        source_dir=Path(str(src)).expanduser() if src else None,
        strict=bool(raw.get("strict", False)),
        log_level=str(raw.get("log_level", "INFO")).upper(),
        snapshot_retention=int(raw.get("snapshot_retention", 10)),
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
