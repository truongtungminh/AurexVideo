#!/usr/bin/env python3
"""Render a FastScene one-scene topic to MP4."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
from urllib.parse import quote, unquote, urlsplit

LOCAL_ROOT = Path(__file__).resolve().parents[1]
if str(LOCAL_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_ROOT))

from fastscene_paths import CHARACTERS_ROOT, DATA_ROOT, RESOURCE_ROOT, ffmpeg_executable
from media_probe import AUDIO_PEAK_LIMITER, media_duration

ROOT = RESOURCE_ROOT
PROJECT_MOUNT_PREFIX = "/__fastscene_project__/"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


class RenderAssetHandler(QuietHandler):
    def __init__(self, *args: object, project_root: Path, **kwargs: object) -> None:
        self.project_root = project_root.resolve()
        self.characters_root = CHARACTERS_ROOT.resolve()
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
            return str(mounted or (self.project_root / ".fastscene-invalid-path"))

        character_prefix = "/assets/characters/"
        if request_path.startswith(character_prefix):
            mounted = self.mounted_path(self.characters_root, request_path[len(character_prefix):])
            if mounted is not None and mounted.exists():
                return str(mounted)

        return super().translate_path(path)


def mounted_topic_url(topic_path: Path) -> str:
    return f"{PROJECT_MOUNT_PREFIX}{quote(topic_path.resolve().name)}"


@contextmanager
def local_server(project_root: Path | None = None):
    handler = partial(
        RenderAssetHandler,
        directory=str(ROOT),
        project_root=(project_root or ROOT).resolve(),
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


def resolve_project_path(topic_path: Path, value: str) -> Path:
    candidate = (topic_path.parent / value).resolve()
    if candidate.exists():
        return candidate

    parts = Path(value).parts
    if "assets" in parts:
        asset_path = Path(*parts[parts.index("assets") + 1:])
        for root in (DATA_ROOT / "assets", ROOT / "assets"):
            mounted = RenderAssetHandler.mounted_path(root.resolve(), asset_path.as_posix())
            if mounted is not None and mounted.exists():
                return mounted
    return candidate


def build_mixed_audio(topic_path: Path, topic: dict, output: Path) -> None:
    voiceover = resolve_project_path(topic_path, topic["voiceover"])
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
        sfx_path = resolve_project_path(topic_path, sfx_value)
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
        music_path = resolve_project_path(topic_path, music_path_value)
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
) -> None:
    from playwright.async_api import async_playwright

    with local_server(topic_path.parent) as port:
        url = (
            f"http://127.0.0.1:{port}/index.html"
            f"?topic={mounted_topic_url(topic_path)}&render=1&offline=1"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        frame_total = max(1, math.ceil(duration * fps))
        command = [
            str(ffmpeg_executable()), "-y", "-loglevel", "error",
            "-f", "image2pipe", "-framerate", str(fps), "-vcodec", "png", "-i", "pipe:0",
            "-i", str(audio),
            "-frames:v", str(frame_total),
            "-vf", f"scale={width}:{height}:flags=lanczos,format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", str(output),
        ]
        encoder = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
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
                    await page.evaluate("(time) => window.renderOfflineFrame(time)", frame_time)
                    frame = await page.screenshot(type="png", animations="disabled")
                    encoder.stdin.write(frame)
                    if frame_index % fps == 0 or frame_index + 1 == frame_total:
                        current = min(duration, frame_index / fps)
                        print(f"Rendering frames: {current:.1f}/{duration:.1f}s", flush=True)
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
        if returncode != 0:
            raise RuntimeError(f"FFmpeg không mã hóa được video frame-by-frame: {stderr.strip()}")


async def render(topic_path: Path, output: Path, width: int, height: int) -> None:
    topic = json.loads(topic_path.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="fastscene-") as temp:
        work_dir = Path(temp)
        mixed_audio = work_dir / "mixed-audio.wav"
        build_mixed_audio(topic_path, topic, mixed_audio)
        duration = media_duration(mixed_audio)
        await render_frames(topic_path, mixed_audio, output, width, height, duration)


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
    args = parser.parse_args()
    asyncio.run(render(args.topic.resolve(), args.output.resolve(), args.width, args.height))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
