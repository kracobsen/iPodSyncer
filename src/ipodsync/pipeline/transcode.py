"""Transcode stage: non-native codecs → AAC ~256 kbps in .m4a.

Passthrough for codecs iPod Classic 6G plays natively: mp3, aac, alac, and
PCM (inside WAV/AIFF). Anything else (flac, opus, vorbis, wma, ...) is
re-encoded by ffmpeg. Tags carried via ``-map_metadata 0``; chapters via
``-map_chapters 0`` so audiobook nav atoms survive a re-encode; cover
streams dropped (artwork stage handles them separately, so the transcoded
file isn't bloated with JPEG data).

Passthrough sources are also peak-checked via ``ffmpeg volumedetect``: any
source whose ``max_volume`` is within :data:`_PEAK_THRESHOLD_DB` of 0 dBFS
gets re-encoded through alimiter, because inter-sample peaks reconstruct
above 0 dBFS at the iPod 6G DAC and clip into audible crackle on
transients (same mechanism that haunted FLAC→AAC; identical fix). The
peak measurement is cached on disk because it costs a full-file decode.

Output cached at
``~/Library/Caches/ipodsync/transcode/<sha1>-v{VERSION}.m4a`` so re-adds
and re-syncs skip the re-encode. Bumping :data:`VERSION` invalidates the
cache.
"""

from __future__ import annotations

import functools
import os
import re
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

# Passthrough sources whose measured peak is at or above this threshold get
# routed through the limiter anyway. 0 dBFS source peaks (common in modern
# masters and Audible-style audiobooks) reconstruct above 0 dBFS at the DAC
# and clip — same mechanism that bit FLAC→AAC even before re-encoding.
_PEAK_THRESHOLD_DB = -1.0


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


def _peak_cache_path(sha1: str) -> Path:
    return _cache_dir() / f"{sha1}-peak.txt"


def measure_peak_db(source: Path, sha1: str) -> float:
    """Return the source's max_volume (dBFS) per ffmpeg volumedetect.

    Cached on disk: a 12 h audiobook decodes for ~30 s, too costly to
    repeat each sync. Cache survives :data:`VERSION` bumps because the
    measurement only depends on the source bytes.
    """
    cache = _peak_cache_path(sha1)
    if cache.is_file():
        try:
            return float(cache.read_text().strip())
        except ValueError:
            pass  # corrupt — re-measure
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-i", str(source),
        "-vn", "-sn", "-dn",
        "-af", "volumedetect",
        "-f", "null",
        "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, check=True)
    except FileNotFoundError as e:
        raise TranscodeError(
            "ffmpeg not on PATH; install via Homebrew or MacPorts"
        ) from e
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        raise TranscodeError(
            f"ffmpeg volumedetect failed on {source}: {stderr.strip()[-500:]}"
        ) from e
    stderr = r.stderr.decode("utf-8", errors="replace")
    m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", stderr)
    if not m:
        raise TranscodeError(f"could not parse volumedetect output for {source}")
    peak = float(m.group(1))
    cache.write_text(f"{peak}\n")
    return peak


def plan(source: Path, probe_result: ProbeResult, sha1: str, *, strict: bool) -> TranscodePlan:
    out = cache_path(sha1)

    if needs_transcode(probe_result):
        if strict:
            raise StrictRefusal(
                f"--strict set; would transcode {source.name} ({probe_result.codec_name} → aac)"
            )
        if not out.is_file():
            _run_ffmpeg(source, out)
        return TranscodePlan(effective_path=out, transcoded=True, output_codec="aac")

    # Codec is passthrough-safe. Measure peaks: anything within
    # _PEAK_THRESHOLD_DB of 0 dBFS gets re-encoded through alimiter so the
    # iPod DAC doesn't clip ISPs on playback. --strict opts out of the
    # (slow) measurement and accepts the source verbatim.
    if not strict and measure_peak_db(source, sha1) >= _PEAK_THRESHOLD_DB:
        if not out.is_file():
            _run_ffmpeg(source, out)
        return TranscodePlan(effective_path=out, transcoded=True, output_codec="aac")

    return TranscodePlan(
        effective_path=source,
        transcoded=False,
        output_codec=probe_result.codec_name,
    )


def _run_ffmpeg(source: Path, out: Path) -> None:
    # PID-unique tmp so concurrent runs on the same sha1 don't trample each
    # other mid-encode; os.replace is atomic, so the published file is always
    # a complete encode (last writer wins, but never a torn one).
    tmp = out.with_suffix(f"{out.suffix}.{os.getpid()}.tmp")
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
        "-map_chapters", "0",           # preserve audiobook chapter atoms
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
