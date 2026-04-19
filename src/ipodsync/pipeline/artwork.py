"""Artwork stage: pull a cover image for a source file.

Lookup order: embedded tags (mutagen) → sibling ``cover.{jpg,jpeg,png}`` /
``folder.{jpg,png}``. The raw image bytes are returned as-is; libgpod
resizes + encodes RGB565 + writes ``F1_1.ithmb`` / ``ArtworkDB`` inside
``itdb_write`` when the DB commits.

Cache layout (per architectural decisions, keyed by content-hash + stage
version): ``~/Library/Caches/ipodsync/artwork/<sha1>-v{VERSION}.bin``, with
a sibling ``.miss`` sentinel so re-runs on art-less files short-circuit too.
"""

from __future__ import annotations

from pathlib import Path

import mutagen
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

VERSION = 1

_COVER_NAMES: tuple[str, ...] = (
    "cover.jpg",
    "cover.jpeg",
    "cover.png",
    "folder.jpg",
    "folder.png",
)


def _cache_dir() -> Path:
    p = Path.home() / "Library" / "Caches" / "ipodsync" / "artwork"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _hit_path(sha1: str) -> Path:
    return _cache_dir() / f"{sha1}-v{VERSION}.bin"


def _miss_path(sha1: str) -> Path:
    return _cache_dir() / f"{sha1}-v{VERSION}.miss"


def _extract_embedded(path: Path) -> bytes | None:
    try:
        af = mutagen.File(str(path))
    except mutagen.MutagenError:
        return None
    if af is None or af.tags is None:
        return None

    if isinstance(af, MP3):
        for frame in af.tags.getall("APIC"):
            if frame.data:
                return bytes(frame.data)
        return None

    if isinstance(af, MP4):
        covrs = af.tags.get("covr")
        if covrs:
            return bytes(covrs[0])
        return None

    return None


def _extract_sibling(path: Path) -> bytes | None:
    parent = path.parent
    for name in _COVER_NAMES:
        candidate = parent / name
        if candidate.is_file():
            return candidate.read_bytes()
    return None


def extract(source: Path) -> bytes | None:
    """Return raw cover-art bytes for ``source``, or ``None`` if not found."""
    return _extract_embedded(source) or _extract_sibling(source)


def extract_cached(source: Path, sha1: str) -> Path | None:
    """Cached variant of :func:`extract`; returns the path to a cached image
    file (suitable for ``itdb_track_set_thumbnails``), or ``None`` if the
    source has no recoverable cover art.

    libgpod/gdk-pixbuf sniff format from magic bytes, so the cached file's
    ``.bin`` suffix is fine regardless of whether the payload is JPEG/PNG.
    """
    hit = _hit_path(sha1)
    if hit.is_file():
        return hit
    if _miss_path(sha1).is_file():
        return None

    data = extract(source)
    if data:
        tmp = hit.with_suffix(hit.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(hit)
        return hit
    _miss_path(sha1).touch()
    return None
