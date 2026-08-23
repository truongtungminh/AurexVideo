"""Small FFmpeg-based media probes so desktop builds only ship one binary."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess

from aurexvideo_paths import ffmpeg_executable


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")

# ~-1 dBFS peak ceiling. level=false avoids makeup gain; latency=true keeps A/V sync.
AUDIO_PEAK_LIMITER = "alimiter=limit=0.891:attack=5:release=50:level=false:latency=true"
# A single final loudness stage keeps narration, music and SFX predictable
# without repeatedly normalizing individual assets.
AUDIO_LOUDNESS_NORMALIZER = "loudnorm=I=-14:TP=-1.5:LRA=7:linear=true:print_format=none"


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


def _contains_atom(path: Path, atom: bytes) -> bool:
    """Scan an MP4 without loading the entire delivery file into memory."""
    overlap = max(0, len(atom) - 1)
    previous = b""
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return False
                haystack = previous + chunk
                if atom in haystack:
                    return True
                previous = haystack[-overlap:] if overlap else b""
    except OSError:
        return False


def _top_level_atoms(path: Path) -> list[tuple[bytes, int]]:
    atoms: list[tuple[bytes, int]] = []
    try:
        with path.open("rb") as handle:
            while True:
                offset = handle.tell()
                header = handle.read(8)
                if len(header) < 8:
                    break
                size = int.from_bytes(header[:4], "big")
                kind = header[4:8]
                header_size = 8
                if size == 1:
                    extended = handle.read(8)
                    if len(extended) < 8:
                        break
                    size = int.from_bytes(extended, "big")
                    header_size = 16
                elif size == 0:
                    handle.seek(0, 2)
                    size = handle.tell() - offset
                if size < header_size:
                    break
                atoms.append((kind, offset))
                handle.seek(offset + size)
    except OSError:
        return []
    return atoms


def validate_rendered_video(
    path: Path,
    *,
    width: int | None = None,
    height: int | None = None,
    fps: int | None = None,
    max_size_bytes: int | None = None,
) -> dict[str, object]:
    """Run a strict, runtime-only postflight check for social delivery MP4s.

    The desktop bundle intentionally ships FFmpeg rather than a separate
    ffprobe binary. This validator therefore combines FFmpeg's stream probe
    and full decode check with a small MP4 atom scan for faststart/edit-list
    invariants.
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"Không tìm thấy file video hợp lệ: {path}")
    if max_size_bytes is not None and path.stat().st_size > max_size_bytes:
        raise RuntimeError(
            f"Video vượt giới hạn postflight: {path.stat().st_size} > {max_size_bytes} bytes."
        )

    text = probe_text(path)
    lines = text.splitlines()
    video_line = next((line for line in lines if "Video:" in line), "")
    audio_line = next((line for line in lines if "Audio:" in line), "")
    if not video_line:
        raise RuntimeError("Video không có video stream.")
    if "h264" not in video_line.casefold():
        raise RuntimeError(f"Video codec không phải H.264: {video_line.strip()}")
    if "yuv420p" not in video_line.casefold():
        raise RuntimeError(f"Video pixel format không phải yuv420p: {video_line.strip()}")
    if "bt709" not in video_line.casefold():
        raise RuntimeError(f"Video thiếu metadata BT.709: {video_line.strip()}")
    if "tv" not in video_line.casefold():
        raise RuntimeError(f"Video không ở TV range: {video_line.strip()}")
    dimensions = re.search(r"(?<!\d)(\d{2,5})x(\d{2,5})(?!\d)", video_line)
    if not dimensions:
        raise RuntimeError(f"Không đọc được kích thước video: {video_line.strip()}")
    actual_width, actual_height = (int(value) for value in dimensions.groups())
    if width is not None and height is not None and (actual_width, actual_height) != (width, height):
        raise RuntimeError(
            f"Kích thước video sai: nhận {actual_width}x{actual_height}, cần {width}x{height}."
        )
    fps_match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s+fps\b", video_line)
    actual_fps = float(fps_match.group(1)) if fps_match else None
    if fps is not None and actual_fps is not None and abs(actual_fps - fps) > 0.01:
        raise RuntimeError(f"FPS video sai: nhận {actual_fps:g}, cần {fps}.")

    if not audio_line:
        raise RuntimeError("Video không có audio stream.")
    audio_lower = audio_line.casefold()
    for required, label in (("aac", "AAC"), ("48000 hz", "48 kHz"), ("stereo", "stereo")):
        if required not in audio_lower:
            raise RuntimeError(f"Audio thiếu {label}: {audio_line.strip()}")

    decode = subprocess.run(
        [
            str(ffmpeg_executable()), "-v", "error", "-xerror", "-i", str(path),
            "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    if decode.returncode != 0:
        detail = (decode.stderr or decode.stdout).strip().splitlines()[-3:]
        raise RuntimeError("Postflight decode thất bại: " + " | ".join(detail))

    atoms = _top_level_atoms(path)
    atom_offsets = {kind: offset for kind, offset in atoms}
    if b"moov" in atom_offsets and b"mdat" in atom_offsets and atom_offsets[b"moov"] > atom_offsets[b"mdat"]:
        raise RuntimeError("MP4 chưa faststart: moov đứng sau mdat.")
    if _contains_atom(path, b"edts") or _contains_atom(path, b"elst"):
        raise RuntimeError("MP4 chứa edit list; delivery cần timestamp tuyến tính.")

    duration = media_duration(path)
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "width": actual_width,
        "height": actual_height,
        "fps": actual_fps,
        "duration_seconds": round(duration, 3),
        "video_stream": video_line.strip(),
        "audio_stream": audio_line.strip(),
        "faststart": bool(b"moov" in atom_offsets and b"mdat" in atom_offsets and atom_offsets[b"moov"] < atom_offsets[b"mdat"]),
        "edit_list": False,
    }
