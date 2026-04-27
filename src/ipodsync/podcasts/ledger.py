"""Consumed-podcast ledger.

JSON file at ``~/Library/Application Support/ipodsync/<guid>/podcasts.json``
recording episodes the iPod has played through to the end so future syncs
neither re-add them from source nor reap the user's iTunes-seeded tracks.
Per-FireWireGUID; siblings share the dir layout with snapshots (when those
existed).

Schema::

    {
      "version": 1,
      "entries": {
        "<sha1>": {
          "show": "...",
          "title": "...",
          "source_path": "/abs/path/to/source/file",
          "removed_at": "2026-04-27T12:34:56Z"
        }
      }
    }

Pure logic + filesystem — no device or libgpod dependency. The reap pipeline
in ``sync.py`` (phase 19) is the only writer; ``cli.py``'s ``podcasts``
subcommand reads/forgets.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1

_APP_SUPPORT = Path.home() / "Library" / "Application Support" / "ipodsync"


@dataclass(frozen=True)
class Entry:
    sha1: str
    show: str
    title: str
    source_path: str
    removed_at: str  # ISO 8601 UTC, e.g. 2026-04-27T12:34:56Z


@dataclass
class Ledger:
    guid: str
    entries: dict[str, Entry] = field(default_factory=dict)

    def contains(self, sha1: str) -> bool:
        return sha1 in self.entries

    def record(self, entry: Entry) -> None:
        self.entries[entry.sha1] = entry

    def forget_all(self) -> list[Entry]:
        gone = list(self.entries.values())
        self.entries.clear()
        return gone

    def forget_pattern(self, pattern: str) -> list[Entry]:
        """Remove entries where ``pattern`` is a substring of ``show/title``.

        Case-insensitive. Pattern can be ``<show>``, ``<show>/<episode>``, or
        any substring of either. Returns the removed entries.
        """
        needle = pattern.casefold()
        if not needle:
            return []
        gone: list[Entry] = []
        for sha1, e in list(self.entries.items()):
            haystack = f"{e.show}/{e.title}".casefold()
            if needle in haystack:
                gone.append(e)
                del self.entries[sha1]
        return gone

    def prune_missing(self) -> list[Entry]:
        """Drop entries whose ``source_path`` no longer exists on disk.

        Lets a user re-add an episode by deleting and re-creating its source
        file: next sync prunes the ledger row, then add proceeds normally.
        Returns the dropped entries.
        """
        gone: list[Entry] = []
        for sha1, e in list(self.entries.items()):
            if not Path(e.source_path).exists():
                gone.append(e)
                del self.entries[sha1]
        return gone


def now_iso_utc() -> str:
    """ISO 8601 UTC timestamp with second precision and a ``Z`` suffix."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def path_for(guid: str) -> Path:
    return _APP_SUPPORT / guid / "podcasts.json"


def load(guid: str, *, path: Path | None = None) -> Ledger:
    """Read the ledger for ``guid``. Missing file → empty ledger.

    Schema's ``version`` key is optional on read (forward-compat: a future
    bump won't strand older callers); we always write the current version.
    Unknown fields on entries are ignored. Malformed JSON raises.
    """
    p = path or path_for(guid)
    if not p.is_file():
        return Ledger(guid=guid)
    raw = json.loads(p.read_text(encoding="utf-8"))
    entries_raw = raw.get("entries") or {}
    entries: dict[str, Entry] = {}
    for sha1, body in entries_raw.items():
        if not isinstance(body, dict):
            continue
        entries[sha1] = Entry(
            sha1=sha1,
            show=str(body.get("show", "")),
            title=str(body.get("title", "")),
            source_path=str(body.get("source_path", "")),
            removed_at=str(body.get("removed_at", "")),
        )
    return Ledger(guid=guid, entries=entries)


def save(ledger: Ledger, *, path: Path | None = None) -> Path:
    """Atomically write the ledger to disk. Returns the final path.

    Writes to ``<path>.tmp`` then ``os.replace`` to the final name so a crash
    mid-write can never leave a half-written podcasts.json.
    """
    p = path or path_for(ledger.guid)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SCHEMA_VERSION,
        "entries": {sha1: asdict(e) for sha1, e in ledger.entries.items()},
    }
    # Drop the redundant sha1 field from each entry body — it's the key.
    for body in payload["entries"].values():
        body.pop("sha1", None)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return p
