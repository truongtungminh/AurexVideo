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
    resolve_vieneu_python,
)
from media_probe import AUDIO_PEAK_LIMITER, has_audio_stream, media_duration
try:
    from render_quality import RENDER_PROFILE_VERSION, RenderProfile, get_render_profile, quality_profile_names
except ModuleNotFoundError:  # Imported as ``tools.render_project`` by tests/tools.
    from tools.render_quality import RENDER_PROFILE_VERSION, RenderProfile, get_render_profile, quality_profile_names

configure_native_runtime()

ROOT = RESOURCE_ROOT
PYTHON = PYTHON_EXECUTABLE
VIENEU_PYTHON = resolve_vieneu_python()


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


def build_branding_watermark(
    logo: Path | None,
    name: str,
    width: int,
    height: int,
    destination: Path,
) -> Path | None:
    """Create a transparent, high-resolution watermark layer for FFmpeg."""
    if not logo and not name:
        return None
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
                (cursor, y + (group_height - text_height) // 2 - text_box[1]),
                name,
                font=font,
                fill=(0, 0, 0, 180),
            )

    draw_group(margin_x, margin_y)
    draw_group(width - margin_x - group_width, height - margin_y - group_height)
    canvas.save(destination, format="PNG", optimize=True)
    return destination


def finalize_video(
    video: Path,
    outro: Path | None,
    logo: Path | None,
    name: str,
    width: int,
    height: int,
    fps: int,
    profile: RenderProfile,
    token: str,
) -> None:
    """Apply optional branding/outro with one consistent final encode.

    The old path encoded branding and outro independently, which could apply
    two extra generations of H.264 loss and forced the outro to 24 FPS.  This
    path normalizes and concatenates both optional layers in one pass.
    """
    watermark_path: Path | None = None
    if logo or name:
        watermark_path = video.with_name(f"watermark-{token}.png")
        build_branding_watermark(logo, name, width, height, watermark_path)

    if not watermark_path and not outro:
        return

    print("Finalizing video: giữ màu, FPS và encode profile nhất quán...", flush=True)
    finalized = video.with_name(f"finalized-{token}.mp4")
    command = [str(ffmpeg_executable()), "-y", "-loglevel", "error", "-i", str(video)]
    outro_index: int | None = None
    watermark_index: int | None = None
    if outro:
        outro_index = 1
        command.extend(["-i", str(outro)])
    if watermark_path:
        watermark_index = 2 if outro else 1
        command.extend(["-loop", "1", "-i", str(watermark_path)])

    video_filter = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"setsar=1,fps={fps},"
        f"colorspace=all=bt709:iall=bt709:range=tv:irange=tv:"
        f"format={profile.pixel_format}:fast=1,setpts=PTS-STARTPTS"
    )
    filters = [f"[0:v]{video_filter}[main]"]
    main_label = "main"
    if watermark_index is not None:
        filters.append(f"[main][{watermark_index}:v]overlay=0:0:shortest=1[branded]")
        main_label = "branded"

    if outro_index is not None:
        outro_duration = media_duration(outro)
        filters.append(f"[{outro_index}:v]{video_filter}[outro]")
        filters.extend([
            "[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            "asetpts=PTS-STARTPTS[a0]",
            f"anullsrc=channel_layout=stereo:sample_rate=48000,"
            f"atrim=duration={outro_duration:.6f},asetpts=PTS-STARTPTS[a1]",
            f"[{main_label}][a0][outro][a1]concat=n=2:v=1:a=1[vout][aout]",
        ])
        map_video = "[vout]"
        map_audio = "[aout]"
        audio_options = [
            "-c:a", "aac", "-b:a", profile.audio_bitrate,
            "-ar", "48000", "-ac", "2", "-channel_layout", "stereo",
        ]
    else:
        map_video = f"[{main_label}]"
        map_audio = "0:a?"
        audio_options = ["-c:a", "copy"]

    command.extend([
        "-filter_complex", ";".join(filters),
        "-map", map_video, "-map", map_audio,
        *profile.encoder_video_options(), "-r", str(fps), "-fps_mode", "cfr",
        *audio_options, "-map_metadata", "0", "-movflags", "+faststart", str(finalized),
    ])
    try:
        run(command)
        finalized.replace(video)
    finally:
        finalized.unlink(missing_ok=True)
        if watermark_path:
            watermark_path.unlink(missing_ok=True)


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


def topic_tts_config(topic: dict) -> dict:
    """Return the optional TTS config without changing the native topic schema."""
    config: dict = {}
    for key in ("tts", "ttsConfig", "tts_config"):
        value = topic.get(key)
        if isinstance(value, dict):
            config.update(value)
    if isinstance(topic.get("speakers"), (dict, list)) and "speakers" not in config:
        config["speakers"] = topic["speakers"]
    for key in ("ttsMode", "tts_mode", "modelId", "model_id"):
        if topic.get(key) is not None and key not in config:
            config[key] = topic[key]
    return config


def latest_cached_voiceover(project: Path, engine: str) -> Path | None:
    """Return only a cache generated by the currently selected TTS engine."""
    prefix = f"{engine}-"
    metadata = project / "audio" / "cache" / "latest.json"
    try:
        value = json.loads(metadata.read_text(encoding="utf-8")).get("path")
        path = (project / str(value or "")).resolve()
        path.relative_to(project.resolve())
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        path = None
    if (
        path
        and path.is_file()
        and path.stat().st_size > 0
        and path.parent == (project / "audio" / "cache").resolve()
        and path.name.startswith(prefix)
    ):
        return path
    try:
        candidates = [
            item
            for item in (project / "audio" / "cache").glob(f"{prefix}*.mp3")
            if item.is_file() and item.stat().st_size > 0
        ]
    except OSError:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime_ns, default=None)


def remember_cached_voiceover(project: Path, path: Path) -> None:
    cache_dir = project / "audio" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    metadata = cache_dir / "latest.json"
    temporary = cache_dir / f".latest-{uuid.uuid4().hex}.tmp"
    relative_path = path.resolve().relative_to(project.resolve()).as_posix()
    temporary.write_text(json.dumps({"path": relative_path}, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(metadata)


def create_voiceover(args: argparse.Namespace, project: Path, topic_path: Path, token: str) -> Path:
    if args.engine == "project":
        topic = json.loads(topic_path.read_text(encoding="utf-8"))
        voiceover_value = topic.get("voiceover")
        source = resolve_project_asset(project, voiceover_value)
        # New projects use audio/silence.wav only as a schema-compatible placeholder.
        # Rendering it as the real narration compresses every sentence into one second
        # and can leave the frame renderer looking stalled after the audio-mix log.
        if source.name.casefold() == "silence.wav":
            raise ValueError(
                "Project chưa có audio lời thoại thật. audio/silence.wav chỉ là file giữ chỗ; "
                "hãy chọn công cụ tạo giọng đọc hoặc tải audio lên trước khi render."
            )
        return source
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
    tts_fingerprint = json.dumps(
        {
            "topic": topic_tts_config(topic),
            "mode": str(getattr(args, "tts_mode", "auto") or "auto"),
            "override": str(getattr(args, "tts_config_json", "") or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    cache_key = hashlib.sha256(
        b"\0".join([
            args.engine.encode(), args.voice.encode(), args.model_id.encode(), text.encode("utf-8"),
            config_fingerprint, tts_fingerprint,
        ])
    ).hexdigest()[:20]
    output = project / "audio" / "cache" / f"{args.engine}-{cache_key}.mp3"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not args.force_tts:
        latest = latest_cached_voiceover(project, args.engine)
        if latest:
            print(f"TTS latest cache hit: dùng lại audio gần nhất {latest}", flush=True)
            return latest
    if output.is_file() and output.stat().st_size > 0 and not args.force_tts:
        print(f"TTS cache hit: dùng lại {output}", flush=True)
        remember_cached_voiceover(project, output)
        return output
    if not args.force_tts:
        # Older projects stored the generated voice directly in topic.json.
        # Keep that audio as the default source unless the user explicitly
        # requests a TTS cache rebuild.
        existing_engine = topic.get("ttsEngine") or topic.get("tts_engine")
        if not existing_engine and isinstance(topic.get("tts"), dict):
            existing_engine = topic["tts"].get("engine")
        existing_engine = str(existing_engine or "").strip().casefold()
        requested_engine = "vieneu" if args.engine == "aurextts" else args.engine
        if existing_engine == "aurextts":
            existing_engine = "vieneu"
        existing_voiceover = topic.get("voiceover") if existing_engine == requested_engine else None
        try:
            existing_path = resolve_project_asset(project, existing_voiceover)
        except (FileNotFoundError, ValueError):
            existing_path = None
        if existing_path and existing_path.name.casefold() != "silence.wav":
            print(f"TTS cache hit: dùng lại voiceover hiện tại {existing_path}", flush=True)
            return existing_path
    if args.force_tts:
        print("TTS cache bypass: tạo lại audio theo yêu cầu.", flush=True)
        output.unlink(missing_ok=True)
    tts_python = VIENEU_PYTHON if args.engine in {"vieneu", "aurextts"} else PYTHON
    command = [
        str(tts_python), "-u", str(ROOT / "tools" / "generate_voiceover.py"),
        str(topic_path), "--engine", args.engine, "--output", str(output),
        "--config", str(config_path), "--voice", args.voice,
    ]
    if args.engine == "elevenlabs":
        command.extend(["--model-id", args.model_id])
    elif args.engine == "maziao":
        if args.model_id:
            command.extend(["--model-id", args.model_id])
        command.extend(["--tts-mode", args.tts_mode])
        if args.tts_config_json:
            command.extend(["--tts-config-json", args.tts_config_json])
    elif args.engine in {"vieneu", "aurextts"}:
        if args.tts_config_json:
            command.extend(["--tts-config-json", args.tts_config_json])
    run(command)
    remember_cached_voiceover(project, output)
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


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def alignment_signature(prepared_topic: Path, render_audio: Path, whisper_model: str) -> str:
    align_script = ROOT / "tools" / "align_voiceover.py"
    payload = b"\0".join([
        b"align-cache-v2",
        whisper_model.encode("utf-8"),
        file_digest(prepared_topic).encode("ascii"),
        str(render_audio.resolve()).encode("utf-8"),
        str(render_audio.stat().st_size).encode("utf-8"),
        str(render_audio.stat().st_mtime_ns).encode("utf-8"),
        file_digest(align_script).encode("ascii"),
    ])
    return hashlib.sha256(payload).hexdigest()
def render_signature(topic_path: Path, args: argparse.Namespace) -> str:
    topic = topic_path.read_bytes()
    renderer_files = [
        ROOT / "app.js",
        ROOT / "style.css",
        ROOT / "index.html",
        ROOT / "tools" / "render_demo.py",
        ROOT / "tools" / "render_project.py",
        ROOT / "tools" / "render_quality.py",
    ]
    quality = get_render_profile(getattr(args, "quality_profile", None))
    payload = b"\0".join(
        [
            b"render-cache-v3",
            RENDER_PROFILE_VERSION.encode("utf-8"),
            topic,
            *(file_digest(path).encode("ascii") for path in renderer_files),
            args.engine.encode("utf-8"),
            str(args.audio.resolve() if args.audio else "").encode("utf-8"),
            f"{float(args.speed):.6f}".encode("utf-8"),
            f"{float(args.volume):.6f}".encode("utf-8"),
            str(int(args.fps)).encode("utf-8"),
            args.size.encode("utf-8"),
            quality.name.encode("utf-8"),
            json.dumps(quality.to_dict(), sort_keys=True).encode("utf-8"),
            args.voice.encode("utf-8"),
            args.model_id.encode("utf-8"),
            str(getattr(args, "tts_mode", "auto") or "auto").encode("utf-8"),
            str(getattr(args, "tts_config_json", "") or "").encode("utf-8"),
            args.whisper_model.encode("utf-8"),
            str(bool(args.force_tts)).encode("utf-8"),
            str(bool(args.outro)).encode("utf-8"),
            str(args.outro_video.resolve() if args.outro_video else "").encode("utf-8"),
            str(bool(args.no_branding)).encode("utf-8"),
            str(args.brand_logo.resolve() if args.brand_logo else "").encode("utf-8"),
            args.brand_name.encode("utf-8"),
        ]
    )
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument(
        "--engine",
        choices=["project", "upload", "maziao", "elevenlabs", "edge", "vieneu", "aurextts"],
        required=True,
    )
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--volume", type=float, default=1.0)
    parser.add_argument("--size", choices=["720x1280", "1080x1920"], default="1080x1920")
    parser.add_argument("--fps", type=int)
    parser.add_argument("--quality-profile", choices=quality_profile_names())
    parser.add_argument("--voice", default="vi-VN-NamMinhNeural")
    parser.add_argument("--model-id", default="")
    parser.add_argument("--tts-mode", choices=["auto", "paragraph", "multiSpeakers"], default="auto")
    parser.add_argument("--tts-config-json", default="")
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
    if args.fps is not None and args.fps < 1:
        raise ValueError("FPS phải lớn hơn 0.")
    args.fps = int(args.fps or os.environ.get("AUREXVIDEO_RENDER_FPS", "30") or "30")
    quality = get_render_profile(args.quality_profile)

    signature = render_signature(topic_path, args)
    output = project / "output" / "final_video.mp4"
    signature_file = output.with_suffix(".signature.json")
    if output.is_file() and output.stat().st_size > 0 and signature_file.is_file():
        try:
            if json.loads(signature_file.read_text(encoding="utf-8")).get("signature") == signature:
                print(f"Render cache hit: dùng lại {output}", flush=True)
                print(f"Done: {output}", flush=True)
                return
        except json.JSONDecodeError:
            pass

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
        render_fps = max(1, int(args.fps))
        run([
            str(PYTHON), "-u", str(ROOT / "tools" / "render_demo.py"),
            str(aligned_topic), "--output", str(output),
            "--width", str(width), "--height", str(height), "--fps", str(render_fps),
            "--quality-profile", quality.name,
        ])
        finalize_video(
            output,
            args.outro_video if args.outro and args.outro_video else None,
            None if args.no_branding else args.brand_logo,
            "" if args.no_branding else args.brand_name,
            width,
            height,
            render_fps,
            quality,
            token,
        )
    finally:
        prepared_topic.unlink(missing_ok=True)

    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Render xong nhưng không có final_video.mp4.")
    report_path = output.with_name(f"{output.stem}.render-report.json")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        report = {}
    report.update({
        "quality_profile": quality.to_dict(),
        "finalization": {
            "branding": bool(not args.no_branding and (args.brand_logo or args.brand_name)),
            "outro": bool(args.outro and args.outro_video),
        },
        "duration_seconds": round(media_duration(output), 3),
        "output_size_bytes": output.stat().st_size,
        "has_audio_stream": has_audio_stream(output),
    })
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    signature_file.write_text(json.dumps({"signature": signature}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Done: {output}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(f"Render failed: {exc}", file=sys.stderr, flush=True)
        raise
