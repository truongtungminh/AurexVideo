#!/usr/bin/env python3
"""Render one AurexVideo project through the local Web UI contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

LOCAL_ROOT = Path(__file__).resolve().parents[1]
if str(LOCAL_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_ROOT))

from aurexvideo_paths import (
    CONFIG_ROOT,
    PYTHON_EXECUTABLE,
    RESOURCE_ROOT,
    configure_native_runtime,
    ffmpeg_executable,
)
from media_probe import AUDIO_PEAK_LIMITER, has_audio_stream, media_duration

configure_native_runtime()

ROOT = RESOURCE_ROOT
PYTHON = PYTHON_EXECUTABLE


def run(command: list[str]) -> None:
    pretty = " ".join(str(part) for part in command)
    print("$ " + pretty, flush=True)
    try:
        proc = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError as exc:
        missing = command[0] if command else "command"
        raise FileNotFoundError(f"Không tìm thấy lệnh render: {missing}") from exc
    assert proc.stdout is not None
    chunks: list[str] = []
    for line in proc.stdout:
        chunks.append(line)
        # Stream child progress (e.g. Rendering frames: 3.0/12.0s) to the job log in real time.
        print(line, end="", flush=True)
    returncode = proc.wait()
    if returncode == 0:
        return
    detail = "".join(chunks).strip()
    if detail:
        tail = "\n".join(detail.splitlines()[-12:])
        raise RuntimeError(f"Lệnh render thất bại (exit {returncode}): {pretty}\n{tail}")
    raise RuntimeError(f"Lệnh render thất bại (exit {returncode}): {pretty}")


def branding_font(size: int):
    """Resolve a bold UI font for watermark text on macOS and Windows."""
    from PIL import ImageFont

    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    candidates = [
        ROOT / "assets" / "fonts" / "Inter-Bold.ttf",
        LOCAL_ROOT / "assets" / "fonts" / "Inter-Bold.ttf",
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        windir / "Fonts" / "arialbd.ttf",
        windir / "Fonts" / "segoeuib.ttf",
        windir / "Fonts" / "arial.ttf",
        windir / "Fonts" / "segoeui.ttf",
    ]
    for font_path in candidates:
        if font_path.is_file():
            try:
                return ImageFont.truetype(str(font_path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def apply_branding(video: Path, logo: Path | None, name: str, width: int, height: int, token: str) -> None:
    if not logo and not name:
        return
    print("Applying branding: đóng logo và tên brand...", flush=True)
    branded = video.with_name(f"branded-{token}.mp4")
    watermark_path = video.with_name(f"watermark-{token}.png")
    from PIL import Image, ImageChops, ImageDraw

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    margin_x = max(24, round(width * 0.033))
    margin_y = max(28, round(height * 0.018))
    gap = max(6, round(width * 0.007))
    logo_size = max(24, round(width * 0.035))
    font = branding_font(max(20, round(width * 0.022)))
    logo_image = None
    if logo:
        logo_image = Image.open(logo).convert("RGBA")
        logo_image.thumbnail((logo_size, logo_size), Image.Resampling.LANCZOS)
        mask = Image.new("L", logo_image.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, logo_image.width - 1, logo_image.height - 1), fill=184)
        logo_image.putalpha(ImageChops.multiply(logo_image.getchannel("A"), mask))

    text_box = draw.textbbox((0, 0), name, font=font) if name else (0, 0, 0, 0)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    group_width = (logo_image.width + gap if logo_image else 0) + text_width
    group_height = max(logo_image.height if logo_image else 0, text_height)

    def draw_group(x: int, y: int) -> None:
        cursor = x
        if logo_image:
            canvas.alpha_composite(logo_image, (cursor, y + (group_height - logo_image.height) // 2))
            cursor += logo_image.width + gap
        if name:
            draw.text(
                (cursor, y + (group_height - text_height) // 2 - text_box[1]), name,
                font=font, fill=(0, 0, 0, 180),
            )

    draw_group(margin_x, margin_y)
    draw_group(width - margin_x - group_width, height - margin_y - group_height)
    canvas.save(watermark_path)
    command = [
        str(ffmpeg_executable()), "-y", "-i", str(video), "-loop", "1", "-i", str(watermark_path),
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[brandout]",
        "-map", "[brandout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "copy", "-movflags", "+faststart", str(branded),
    ]
    try:
        run(command)
        branded.replace(video)
    finally:
        branded.unlink(missing_ok=True)
        watermark_path.unlink(missing_ok=True)


def append_outro(video: Path, outro: Path, width: int, height: int, token: str) -> None:
    print("Appending outro: chuẩn hóa và nối video cuối...", flush=True)
    combined = video.with_name(f"with-outro-{token}.mp4")
    outro_duration = media_duration(outro)
    command = [str(ffmpeg_executable()), "-y", "-i", str(video), "-i", str(outro)]
    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=24,format=yuv420p"
    )
    filters = (
        f"[0:v]{video_filter},setpts=PTS-STARTPTS[v0];"
        "[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"
        f"[1:v]{video_filter},setpts=PTS-STARTPTS[v1];"
        f"anullsrc=channel_layout=stereo:sample_rate=44100,atrim=duration={outro_duration:.6f},asetpts=PTS-STARTPTS[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
    )
    command.extend([
        "-filter_complex", filters, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(combined),
    ])
    try:
        run(command)
        combined.replace(video)
    finally:
        combined.unlink(missing_ok=True)


def resolve_project_asset(project: Path, value: object) -> Path:
    relative = Path(str(value or ""))
    if not str(relative) or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Đường dẫn voiceover trong topic.json không hợp lệ.")
    path = (project / relative).resolve()
    path.relative_to(project.resolve())
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy voiceover: {path}")
    return path


def write_script(project: Path, topic: dict) -> None:
    lines = [str(item.get("text") or "").strip() for item in topic.get("segments", [])]
    content = "\n".join(line for line in lines if line) + "\n"
    (project / "script.txt").write_text(content, encoding="utf-8")


def create_voiceover(args: argparse.Namespace, project: Path, topic_path: Path, token: str) -> Path:
    if args.engine == "project":
        topic = json.loads(topic_path.read_text(encoding="utf-8"))
        return resolve_project_asset(project, topic.get("voiceover"))
    if args.engine == "upload":
        if not args.audio:
            raise ValueError("Thiếu file audio upload.")
        audio = args.audio.resolve()
        if not audio.is_file():
            raise FileNotFoundError(f"Không tìm thấy audio upload: {audio}")
        return audio

    topic = json.loads(topic_path.read_text(encoding="utf-8"))
    text = "\n\n".join(str(item.get("text") or "").strip() for item in topic.get("segments", [])).strip()
    config_path = CONFIG_ROOT / "tts.json"
    config_fingerprint = config_path.read_bytes() if config_path.is_file() else b""
    cache_key = hashlib.sha256(
        b"\0".join([
            args.engine.encode(), args.voice.encode(), args.model_id.encode(), text.encode("utf-8"), config_fingerprint,
        ])
    ).hexdigest()[:20]
    output = project / "audio" / "cache" / f"{args.engine}-{cache_key}.mp3"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_file() and output.stat().st_size > 0 and not args.force_tts:
        print(f"TTS cache hit: dùng lại {output}", flush=True)
        return output
    if args.force_tts:
        print("TTS cache bypass: tạo lại audio theo yêu cầu.", flush=True)
        output.unlink(missing_ok=True)
    command = [
        str(PYTHON), "-u", str(ROOT / "tools" / "generate_voiceover.py"),
        str(topic_path), "--engine", args.engine, "--output", str(output),
        "--config", str(config_path), "--voice", args.voice,
    ]
    if args.engine == "elevenlabs":
        command.extend(["--model-id", args.model_id])
    run(command)
    return output


def prepare_render_audio(source: Path, project: Path, speed: float, volume: float = 1.0) -> Path:
    output = project / "output" / "render-voiceover.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    filters = [f"atempo={speed:g}"]
    if abs(volume - 1.0) > 0.001:
        filters.append(f"volume={volume:g}")
    filters.append(AUDIO_PEAK_LIMITER)
    run([
        str(ffmpeg_executable()), "-y", "-i", str(source), "-vn", "-filter:a", ",".join(filters),
        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(output),
    ])
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("--engine", choices=["project", "upload", "elevenlabs", "edge"], required=True)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--volume", type=float, default=1.0)
    parser.add_argument("--size", choices=["720x1280", "1080x1920"], default="1080x1920")
    parser.add_argument("--voice", default="vi-VN-NamMinhNeural")
    parser.add_argument("--model-id", default="eleven_v3")
    parser.add_argument("--whisper-model", default="base")
    parser.add_argument("--force-tts", action="store_true")
    parser.add_argument("--outro", action="store_true")
    parser.add_argument("--outro-video", type=Path)
    parser.add_argument("--no-branding", action="store_true")
    parser.add_argument("--brand-logo", type=Path)
    parser.add_argument("--brand-name", default="")
    args = parser.parse_args()

    project = args.project.resolve()
    topic_path = project / "topic.json"
    if not topic_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy topic.json: {topic_path}")
    if not 0.5 <= args.speed <= 2.0:
        raise ValueError("Tốc độ phải nằm trong khoảng 0.5-2.0.")
    if not 1.0 <= args.volume <= 3.0:
        raise ValueError("Âm lượng phải nằm trong khoảng 1.0-3.0.")
    if not PYTHON.is_file():
        raise RuntimeError("Không tìm thấy Python runtime của AurexVideo.")

    token = uuid.uuid4().hex[:8]
    original = json.loads(topic_path.read_text(encoding="utf-8"))
    write_script(project, original)
    source_audio = create_voiceover(args, project, topic_path, token)
    render_audio = prepare_render_audio(source_audio, project, args.speed, args.volume)
    duration = media_duration(render_audio)

    prepared = dict(original)
    prepared["voiceover"] = Path(os.path.relpath(render_audio, project)).as_posix()
    prepared["duration"] = round(duration, 3)
    prepared_topic = project / f"topic.render-{token}.json"
    prepared_topic.write_text(json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    aligned_topic = project / "topic.rendered.json"
    output = project / "output" / "final_video.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    width, height = (int(value) for value in args.size.split("x"))

    try:
        print("Whisper transcription: căn subtitle và pose theo audio...", flush=True)
        run([
            str(PYTHON), "-u", str(ROOT / "tools" / "align_voiceover.py"),
            str(prepared_topic), str(render_audio), "--output", str(aligned_topic),
            "--model", args.whisper_model,
        ])
        print("Rendering one-scene video frame-by-frame...", flush=True)
        run([
            str(PYTHON), "-u", str(ROOT / "tools" / "render_demo.py"),
            str(aligned_topic), "--output", str(output),
            "--width", str(width), "--height", str(height),
        ])
        if not args.no_branding:
            apply_branding(output, args.brand_logo, args.brand_name, width, height, token)
        if args.outro and args.outro_video:
            append_outro(output, args.outro_video, width, height, token)
    finally:
        prepared_topic.unlink(missing_ok=True)

    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Render xong nhưng không có final_video.mp4.")
    print(f"Done: {output}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"Render failed: {exc}", file=sys.stderr, flush=True)
        raise
