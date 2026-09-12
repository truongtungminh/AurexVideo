from __future__ import annotations

import subprocess
from pathlib import Path

from aurexvideo_paths import ffmpeg_executable


THUMBNAIL_TIMESTAMP = "00:00:01.000"


def extract_thumbnail_frame(video_path: str | Path) -> Path | None:
    """Extract and cache the frame at 1 second as a PNG thumbnail."""
    video_path = Path(video_path)
    thumbnail_path = video_path.with_name("thumbnail_1s.png")
    try:
        cached = (
            thumbnail_path.exists()
            and thumbnail_path.stat().st_size > 1024
            and thumbnail_path.stat().st_mtime >= video_path.stat().st_mtime
        )
        if cached:
            return thumbnail_path
        result = subprocess.run(
            [
                str(ffmpeg_executable()),
                "-y",
                "-loglevel",
                "error",
                "-ss",
                THUMBNAIL_TIMESTAMP,
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-compression_level",
                "9",
                str(thumbnail_path),
            ],
            capture_output=True,
            timeout=60,
        )
        if result.returncode == 0 and thumbnail_path.exists() and thumbnail_path.stat().st_size > 1024:
            return thumbnail_path
    except Exception:
        pass
    return None
