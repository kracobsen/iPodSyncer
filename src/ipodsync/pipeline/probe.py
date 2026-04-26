"""ffprobe wrapper: codec + duration + container for a source file.

We only look at the first audio stream. Video/cover streams are ignored
here — artwork extraction has its own path.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

VERSION = 1


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    codec_name: str            # "mp3", "aac", "alac", "flac", "opus", "vorbis", "pcm_s16le", ...
    container: str             # ffprobe format_name, e.g. "mov,mp4,m4a,3gp,3g2,mj2"
    duration_ms: int
    sample_rate: int | None
    channels: int | None
    bitrate_kbps: int | None
    chapter_count: int         # 0 when the container has no chapter markers


def probe(path: Path) -> ProbeResult:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-select_streams", "a:0",
        "-show_streams",
        "-show_format",
        "-show_chapters",
        str(path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, check=True, text=True)
    except FileNotFoundError as e:
        raise ProbeError(
            "ffprobe not on PATH; install ffmpeg (scripts/bootstrap.sh or `brew install ffmpeg`)"
        ) from e
    except subprocess.CalledProcessError as e:
        raise ProbeError(f"ffprobe failed for {path}: {e.stderr.strip()}") from e

    data = json.loads(res.stdout)
    streams = data.get("streams") or []
    if not streams:
        raise ProbeError(f"no audio streams in {path}")
    s = streams[0]
    fmt = data.get("format") or {}

    duration_s = float(fmt.get("duration") or s.get("duration") or 0.0)
    br_raw = fmt.get("bit_rate") or s.get("bit_rate") or 0
    try:
        bitrate_kbps: int | None = int(br_raw) // 1000 if int(br_raw) else None
    except (TypeError, ValueError):
        bitrate_kbps = None

    chapters = data.get("chapters") or []

    return ProbeResult(
        codec_name=str(s.get("codec_name") or ""),
        container=str(fmt.get("format_name") or ""),
        duration_ms=int(round(duration_s * 1000)),
        sample_rate=int(s["sample_rate"]) if s.get("sample_rate") else None,
        channels=int(s["channels"]) if s.get("channels") else None,
        bitrate_kbps=bitrate_kbps,
        chapter_count=len(chapters),
    )
