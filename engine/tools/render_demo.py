#!/usr/bin/env python3
"""Render a AurexVideo one-scene topic to MP4."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from threading import Thread
from urllib.parse import quote, unquote, urlsplit

LOCAL_ROOT = Path(__file__).resolve().parents[1]
if str(LOCAL_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_ROOT))

from aurexvideo_paths import CHARACTERS_ROOT, DATA_ROOT, RESOURCE_ROOT, ffmpeg_executable
from media_probe import AUDIO_PEAK_LIMITER, media_duration

ROOT = RESOURCE_ROOT
PROJECT_MOUNT_PREFIX = "/__aurexvideo_project__/"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


class RenderAssetHandler(QuietHandler):
    def __init__(self, *args: object, project_root: Path, characters_root: Path, **kwargs: object) -> None:
        self.project_root = project_root.resolve()
        self.characters_root = characters_root.resolve()
        super().__init__(*args, **kwargs)

    @staticmethod
    def mounted_path(root: Path, relative: str) -> Path | None:
        candidate = (root / Path(relative.lstrip("/"))).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate

    def translate_path(self, path: str) -> str:
        request_path = unquote(urlsplit(path).path)
        if request_path.startswith(PROJECT_MOUNT_PREFIX):
            mounted = self.mounted_path(self.project_root, request_path[len(PROJECT_MOUNT_PREFIX):])
            return str(mounted or (self.project_root / ".aurexvideo-invalid-path"))

        character_prefix = "/assets/characters/"
        if request_path.startswith(character_prefix):
            mounted = self.mounted_path(self.characters_root, request_path[len(character_prefix):])
            if mounted is not None and mounted.exists():
                return str(mounted)

        return super().translate_path(path)


def mounted_topic_url(topic_path: Path) -> str:
    return f"{PROJECT_MOUNT_PREFIX}{quote(topic_path.resolve().name)}"


def infer_data_root(topic_path: Path) -> Path:
    configured = str(os.environ.get("AUREX_DATA_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return topic_path.resolve().parent.parent.parent


@contextmanager
def local_server(project_root: Path | None = None, data_root: Path | None = None):
    resolved_project_root = (project_root or ROOT).resolve()
    resolved_data_root = (data_root or DATA_ROOT).resolve()
    handler = partial(
        RenderAssetHandler,
        directory=str(ROOT),
        project_root=resolved_project_root,
        characters_root=(resolved_data_root / "assets" / "characters"),
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def resolve_project_path(topic_path: Path, value: str, data_root: Path | None = None) -> Path:
    candidate = (topic_path.parent / value).resolve()
    if candidate.exists():
        return candidate

    parts = Path(value).parts
    if "assets" in parts:
        asset_path = Path(*parts[parts.index("assets") + 1:])
        resolved_data_root = (data_root or infer_data_root(topic_path)).resolve()
        for root in (resolved_data_root / "assets", ROOT / "assets"):
            mounted = RenderAssetHandler.mounted_path(root.resolve(), asset_path.as_posix())
            if mounted is not None and mounted.exists():
                return mounted
    return candidate


def build_mixed_audio(topic_path: Path, topic: dict, output: Path, data_root: Path | None = None) -> None:
    voiceover = resolve_project_path(topic_path, topic["voiceover"], data_root)
    voice_duration = max(0.1, float(topic.get("duration") or 0) or media_duration(voiceover))
    command = [str(ffmpeg_executable()), "-y", "-i", str(voiceover)]
    filters = []
    mix_inputs = ["[0:a]"]
    input_index = 1

    for event in topic.get("poseTimeline", []):
        # Older projects intentionally omitted sfx on the first timeline row.
        # Fall back to the global pose→sfx map so sentence 1 is still audible.
        sfx_name = event.get("sfx") or topic.get("poseSfx", {}).get(event.get("pose"))
        if not sfx_name:
            continue
        sfx_value = topic.get("sfx", {}).get(sfx_name)
        if not sfx_value:
            continue
        sfx_path = resolve_project_path(topic_path, sfx_value, data_root)
        command.extend(["-i", str(sfx_path)])
        delay_ms = max(0, round(float(event["time"]) * 1000))
        label = f"sfx{input_index}"
        filters.append(
            f"[{input_index}:a]adelay={delay_ms}:all=1,volume={float(topic.get('sfxVolume', 0.7)):.3f}[{label}]"
        )
        mix_inputs.append(f"[{label}]")
        input_index += 1

    music_path_value = str(topic.get("backgroundMusic") or "").strip()
    if music_path_value:
        music_path = resolve_project_path(topic_path, music_path_value, data_root)
        if music_path.exists():
            try:
                music_volume = max(0.05, min(0.5, float(topic.get("backgroundMusicVolume", 0.18))))
            except (TypeError, ValueError):
                music_volume = 0.18
            fade = min(1.0, max(0.15, voice_duration / 8))
            fade_out_start = max(0.0, voice_duration - fade)
            command.extend(["-stream_loop", "-1", "-i", str(music_path)])
            filters.append(
                f"[{input_index}:a]"
                f"atrim=0:{voice_duration:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={fade:.3f},"
                f"afade=t=out:st={fade_out_start:.3f}:d={fade:.3f},"
                f"volume={music_volume:.3f}[bgm]"
            )
            mix_inputs.append("[bgm]")
            input_index += 1

    filters.append(
        "".join(mix_inputs)
        # amix defaults to normalize=1. With delayed one-shot SFX inputs that
        # divided the voiceover by every active stream, then raised it again as
        # those streams ended. normalize=0 preserves the source voiceover at
        # unity gain. A final alimiter then caps voice+music+SFX peaks near -1 dB.
        + f"amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0:normalize=0[mix];"
        + f"[mix]{AUDIO_PEAK_LIMITER}[limited]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[limited]",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    subprocess.run(command, check=True, capture_output=True)


async def render_frames(
    topic_path: Path,
    audio: Path,
    output: Path,
    width: int,
    height: int,
    duration: float,
    fps: int = 24,
    data_root: Path | None = None,
    capture_format: str = "jpeg",
    capture_quality: int = 100,
) -> None:
    from playwright.async_api import async_playwright

    with local_server(topic_path.parent, data_root=data_root) as port:
        url = (
            f"http://127.0.0.1:{port}/index.html"
            f"?topic={mounted_topic_url(topic_path)}&render=1&offline=1"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        frame_total = max(1, math.ceil(duration * fps))
        pipe_codec = "png" if capture_format == "png" else "mjpeg"
        command = [
            str(ffmpeg_executable()), "-y", "-loglevel", "error",
            "-f", "image2pipe", "-framerate", str(fps), "-vcodec", pipe_codec, "-i", "pipe:0",
            "-i", str(audio),
            "-frames:v", str(frame_total),
            "-vf", "scale=in_range=pc:out_range=tv,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-color_range", "tv",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", str(output),
        ]
        encoder = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        evaluate_seconds = 0.0
        capture_seconds = 0.0
        pipe_seconds = 0.0
        capture_bytes = 0
        media_sync_stats: dict = {}
        render_started = time.perf_counter()
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=["--autoplay-policy=no-user-gesture-required", "--disable-dev-shm-usage"],
                )
                page = await browser.new_page(viewport={"width": width, "height": height})
                await page.goto(url, wait_until="networkidle")
                await page.wait_for_function("window.__AUREX_DEMO_READY__ === true", timeout=30_000)
                await page.evaluate("window.prepareOfflineRender()")
                if encoder.stdin is None:
                    raise RuntimeError("Không mở được luồng frame cho FFmpeg.")
                for frame_index in range(frame_total):
                    frame_time = frame_index / fps
                    stage_started = time.perf_counter()
                    await page.evaluate("(time) => window.renderOfflineFrame(time)", frame_time)
                    evaluate_seconds += time.perf_counter() - stage_started
                    stage_started = time.perf_counter()
                    screenshot_options = {"type": capture_format, "animations": "disabled"}
                    if capture_format == "jpeg":
                        screenshot_options["quality"] = capture_quality
                    frame = await page.screenshot(**screenshot_options)
                    capture_seconds += time.perf_counter() - stage_started
                    capture_bytes += len(frame)
                    stage_started = time.perf_counter()
                    encoder.stdin.write(frame)
                    pipe_seconds += time.perf_counter() - stage_started
                    if frame_index % fps == 0 or frame_index + 1 == frame_total:
                        current = min(duration, frame_index / fps)
                        print(f"Rendering frames: {current:.1f}/{duration:.1f}s", flush=True)
                media_sync_stats = await page.evaluate("window.__AUREX_MEDIA_SYNC_STATS__ || {}")
                await browser.close()
        except Exception:
            if encoder.stdin is not None:
                try:
                    encoder.stdin.close()
                except BrokenPipeError:
                    pass
            if encoder.poll() is None:
                encoder.terminate()
                try:
                    encoder.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    encoder.kill()
                    encoder.wait()
            raise

        if encoder.stdin is not None:
            try:
                encoder.stdin.close()
            except BrokenPipeError:
                pass
        stderr = encoder.stderr.read().decode("utf-8", errors="replace") if encoder.stderr else ""
        returncode = encoder.wait()
        total_seconds = time.perf_counter() - render_started
        print(
            "Render profile: "
            f"frames={frame_total} format={capture_format} total={total_seconds:.3f}s "
            f"evaluate={evaluate_seconds:.3f}s capture={capture_seconds:.3f}s "
            f"pipe_wait={pipe_seconds:.3f}s setup_and_encoder_drain="
            f"{max(0.0, total_seconds - evaluate_seconds - capture_seconds - pipe_seconds):.3f}s "
            f"captured={capture_bytes / 1024 / 1024:.1f}MiB",
            flush=True,
        )
        if media_sync_stats:
            print(
                "Character sync profile: "
                f"seeks={int(media_sync_stats.get('seeks', 0))} "
                f"skipped_seeks={int(media_sync_stats.get('skippedSeeks', 0))} "
                f"pose_changes={int(media_sync_stats.get('poseChanges', 0))} "
                f"seek_wait={float(media_sync_stats.get('seekWaitMs', 0)) / 1000:.3f}s "
                f"max_drift={float(media_sync_stats.get('maxDriftMs', 0)):.1f}ms",
                flush=True,
            )
        if returncode != 0:
            raise RuntimeError(f"FFmpeg không mã hóa được video frame-by-frame: {stderr.strip()}")


async def render(
    topic_path: Path,
    output: Path,
    width: int,
    height: int,
    fps: int = 15,
    benchmark_seconds: float | None = None,
    capture_format: str = "jpeg",
    capture_quality: int = 100,
) -> None:
    topic = json.loads(topic_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="aurexvideo-") as temp:
        work_dir = Path(temp)
        data_root = infer_data_root(topic_path)
        mixed_audio = work_dir / "mixed-audio.wav"
        audio_mix_started = time.perf_counter()
        build_mixed_audio(topic_path, topic, mixed_audio, data_root)
        print(f"Audio mix profile: {time.perf_counter() - audio_mix_started:.3f}s", flush=True)
        duration = media_duration(mixed_audio)
        if benchmark_seconds is not None:
            duration = min(duration, max(0.1, benchmark_seconds))
        await render_frames(
            topic_path, mixed_audio, output, width, height, duration,
            fps=fps, data_root=data_root, capture_format=capture_format,
            capture_quality=capture_quality,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "topic",
        nargs="?",
        default="project/inox-304-vs-316/topic.json",
        type=Path,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/inox-304-vs-316-demo.mp4"),
    )
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--benchmark-seconds", type=float)
    parser.add_argument("--capture-format", choices=["png", "jpeg"], default="jpeg")
    parser.add_argument("--capture-quality", type=int, choices=range(0, 101), default=100)
    args = parser.parse_args()
    asyncio.run(render(
        args.topic.resolve(), args.output.resolve(), args.width, args.height, args.fps,
        benchmark_seconds=args.benchmark_seconds, capture_format=args.capture_format,
        capture_quality=args.capture_quality,
    ))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
