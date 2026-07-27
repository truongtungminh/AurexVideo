"""Small FFmpeg-based media probes so desktop builds only ship one binary."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

from aurexvideo_paths import ffmpeg_executable


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")

# ~-1 dBFS peak ceiling. level=false avoids makeup gain; latency=true keeps A/V sync.
AUDIO_PEAK_LIMITER = "alimiter=limit=0.891:attack=5:release=50:level=false:latency=true"


def probe_text(path: Path) -> str:
    result = subprocess.run(
        [str(ffmpeg_executable()), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
    )
    output = f"{result.stdout}\n{result.stderr}"
    if "No such file or directory" in output:
        raise FileNotFoundError(path)
    return output


def media_duration(path: Path) -> float:
    output = probe_text(path)
    match = _DURATION.search(output)
    if not match:
        raise RuntimeError(f"Không đọc được thời lượng media: {path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def has_audio_stream(path: Path) -> bool:
    return bool(re.search(r"Stream #.*Audio:", probe_text(path)))
