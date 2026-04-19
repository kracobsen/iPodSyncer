"""Transcode stage: non-native codecs → AAC ~256 kbps in .m4a.

Passthrough for codecs iPod Classic 6G plays natively: mp3, aac, alac, and
PCM (inside WAV/AIFF). Anything else (flac, opus, vorbis, wma, ...) is
re-encoded by ffmpeg. Tags carried via ``-map_metadata 0``; cover streams
dropped (artwork stage handles them separately, so the transcoded file
isn't bloated with JPEG data).

Output cached at
``~/Library/Caches/ipodsync/transcode/<sha1>-v{VERSION}.m4a`` so re-adds
and re-syncs skip the re-encode. Bumping :data:`VERSION` invalidates the
cache.
"""

from __future__ import annotations

import functools
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ipodsync.pipeline.probe import ProbeResult

VERSION = 3

# Encoder preference: libfdk_aac when ffmpeg has it (homebrew-ffmpeg/ffmpeg tap
# with --with-fdk-aac), else the built-in `aac` encoder. fdk_aac's VBR mode 5
# lands ~224 kbps avg while being audibly transparent on complex material; the
# built-in `aac` encoder smears transients on dense mixes and that's what shows
# up as the "scratchy once in a while" artifact on FLAC sources.
_FDK_VBR_QUALITY = "5"
_NATIVE_TARGET_BITRATE = "256k"

# Modern FLAC masters routinely reconstruct above 0 dBFS after AAC round-trip,
# which the iPod DAC hard-clips into audible scratches on transients. A −1 dBFS
# peak limiter pre-encode eats the inter-sample peaks. `level=disabled` stops
# alimiter from normalizing quiet passages upward.
_PEAK_LIMITER = "alimiter=limit=0.891:level=disabled"


@functools.cache
def _has_libfdk_aac() -> bool:
    """True if ffmpeg on PATH was built with libfdk_aac."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return "libfdk_aac" in r.stdout

_PASSTHROUGH_CODECS: frozenset[str] = frozenset({"mp3", "aac", "alac"})
_PCM_PREFIX = "pcm_"


class TranscodeError(RuntimeError):
    pass


class StrictRefusal(RuntimeError):
    """Raised when ``--strict`` is set and transcoding would be required."""


@dataclass(frozen=True)
class TranscodePlan:
    effective_path: Path      # file to hand to the writer
    transcoded: bool          # True iff ffmpeg produced a new file
    output_codec: str         # after-transcode: "aac"; else upstream codec_name


def needs_transcode(p: ProbeResult) -> bool:
    if p.codec_name in _PASSTHROUGH_CODECS:
        return False
    return not p.codec_name.startswith(_PCM_PREFIX)


def _cache_dir() -> Path:
    p = Path.home() / "Library" / "Caches" / "ipodsync" / "transcode"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_path(sha1: str) -> Path:
    return _cache_dir() / f"{sha1}-v{VERSION}.m4a"


def plan(source: Path, probe_result: ProbeResult, sha1: str, *, strict: bool) -> TranscodePlan:
    if not needs_transcode(probe_result):
        return TranscodePlan(
            effective_path=source,
            transcoded=False,
            output_codec=probe_result.codec_name,
        )

    if strict:
        raise StrictRefusal(
            f"--strict set; would transcode {source.name} ({probe_result.codec_name} → aac)"
        )

    out = cache_path(sha1)
    if not out.is_file():
        _run_ffmpeg(source, out)
    return TranscodePlan(effective_path=out, transcoded=True, output_codec="aac")


def _run_ffmpeg(source: Path, out: Path) -> None:
    tmp = out.with_suffix(out.suffix + ".tmp")
    if _has_libfdk_aac():
        codec_args = ["-c:a", "libfdk_aac", "-vbr", _FDK_VBR_QUALITY]
    else:
        codec_args = ["-c:a", "aac", "-b:a", _NATIVE_TARGET_BITRATE]
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i", str(source),
        "-vn",                          # drop cover streams — artwork stage owns them
        "-af", _PEAK_LIMITER,
        *codec_args,
        "-map_metadata", "0",
        "-movflags", "+faststart",
        "-f", "ipod",                   # force muxer; tmp path has no .m4a suffix
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError as e:
        raise TranscodeError(
            "ffmpeg not on PATH; install via Homebrew or MacPorts"
        ) from e
    except subprocess.CalledProcessError as e:
        tmp.unlink(missing_ok=True)
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        raise TranscodeError(
            f"ffmpeg failed on {source}: {stderr.strip()[-500:]}"
        ) from e
    tmp.replace(out)
