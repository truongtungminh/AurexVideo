#!/usr/bin/env python3
"""Render a AurexVideo one-scene topic to MP4."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
from threading import Thread
from urllib.parse import quote, unquote, urlsplit

LOCAL_ROOT = Path(__file__).resolve().parents[1]
if str(LOCAL_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_ROOT))

from aurexvideo_paths import CHARACTERS_ROOT, DATA_ROOT, RESOURCE_ROOT, ffmpeg_executable
from media_probe import AUDIO_LOUDNESS_NORMALIZER, AUDIO_PEAK_LIMITER, media_duration
try:
    from render_quality import RenderProfile, get_render_profile, quality_profile_names
except ModuleNotFoundError:  # Imported as ``tools.render_demo`` by tests/tools.
    from tools.render_quality import RenderProfile, get_render_profile, quality_profile_names
try:
    from native_render import audio_mux_command
except ModuleNotFoundError:  # Imported as ``tools.render_demo`` by tests/tools.
    from tools.native_render import audio_mux_command

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

    def send_head(self):
        range_header = self.headers.get("Range")
        if not range_header or not range_header.startswith("bytes="):
            return super().send_head()
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            source = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None
        size = os.fstat(source.fileno()).st_size
        try:
            start_text, end_text = range_header[6:].split(",", 1)[0].split("-", 1)
            start = int(start_text) if start_text else 0
            end = int(end_text) if end_text else size - 1
            if start < 0 or start >= size or end < start:
                raise ValueError
            end = min(end, size - 1)
        except ValueError:
            source.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Last-Modified", self.date_time_string(os.fstat(source.fileno()).st_mtime))
        self.end_headers()
        source.seek(start)
        self._range_remaining = length
        return source

    def copyfile(self, source, outputfile) -> None:
        remaining = getattr(self, "_range_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        try:
            while remaining > 0:
                chunk = source.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                try:
                    outputfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(chunk)
        finally:
            self._range_remaining = None


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
    filters = [
        "[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[voice]"
    ]
    mix_inputs = ["[voice]"]
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
            f"[{input_index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"adelay={delay_ms}:all=1,volume={float(topic.get('sfxVolume', 0.3)):.3f}[{label}]"
        )
        mix_inputs.append(f"[{label}]")
        input_index += 1

    countdown_value = str(topic.get("quizCountdownSound") or "").strip()
    if str(topic.get("projectType") or "").lower() == "quiz" and not countdown_value:
        # Keep existing Quiz projects compatible after the asset is installed.
        countdown_value = "audio/quiz-countdown.wav"
    if str(topic.get("projectType") or "").lower() == "quiz" and countdown_value:
        countdown_path = resolve_project_path(topic_path, countdown_value, data_root)
        if countdown_path.is_file():
            try:
                countdown_duration = max(0.0, float(topic.get("quizAnswerDelay", 5.0)))
            except (TypeError, ValueError):
                countdown_duration = 5.0
            for pair_index in range(0, len(topic.get("segments", [])) - 1, 2):
                question = topic["segments"][pair_index]
                if not isinstance(question, dict):
                    continue
                try:
                    start = max(0.0, float(question.get("start") or 0.0))
                    start = max(start, float(question.get("end") or start))
                except (TypeError, ValueError):
                    continue
                command.extend(["-i", str(countdown_path)])
                label = f"countdown{input_index}"
                delay_ms = round(start * 1000)
                countdown_filters = (
                    f"[{input_index}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                    f"atrim=0:{countdown_duration:.3f},asetpts=PTS-STARTPTS"
                )
                # The supplied countdown contains a short accent near its
                # tail. Fade the exact 5-second clip edge to avoid a click
                # when the answer narration starts, without modifying the
                # source asset stored in the project.
                if countdown_duration > 0.12:
                    countdown_filters += f",afade=t=out:st={countdown_duration - 0.12:.3f}:d=0.12"
                filters.append(
                    countdown_filters + ","
                    f"adelay={delay_ms}:all=1,volume={float(topic.get('quizCountdownVolume', 0.3)):.3f}[{label}]"
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
                f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
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
        + f"[mix]{AUDIO_LOUDNESS_NORMALIZER}[normalized];"
        + f"[normalized]{AUDIO_PEAK_LIMITER}[limited]"
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
            "2",
            "-channel_layout",
            "stereo",
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
    capture_format: str | None = None,
    capture_quality: int | None = None,
    profile: RenderProfile | None = None,
    mezzanine: bool = False,
    encoder_backend: str = "browser",
    core_binary: Path | None = None,
) -> None:
    from playwright.async_api import async_playwright

    quality = profile or get_render_profile()
    capture_format = str(capture_format or quality.capture_format).lower()
    if capture_format not in {"png", "jpeg"}:
        raise ValueError("capture_format phải là png hoặc jpeg.")
    if capture_format == "jpeg":
        capture_quality = int(capture_quality if capture_quality is not None else quality.capture_quality or 95)
        capture_quality = max(0, min(100, capture_quality))

    encoder_backend = str(encoder_backend or "browser").strip().lower()
    if encoder_backend not in {"browser", "aurex-render"}:
        raise ValueError("encoder_backend phải là browser hoặc aurex-render.")

    image_decoder = None
    if encoder_backend == "aurex-render":
        from PIL import Image

        def image_decoder(frame: bytes) -> bytes:
            with Image.open(BytesIO(frame)) as image:
                image = image.convert("RGBA")
                if image.size != (width, height):
                    image = image.resize((width, height), Image.Resampling.LANCZOS)
                return image.tobytes("raw", "BGRA")

    if encoder_backend == "aurex-render" and (core_binary is None or not core_binary.is_file()):
        raise FileNotFoundError(f"Không tìm thấy Aurex Render Core binary: {core_binary}")

    with local_server(topic_path.parent, data_root=data_root) as port:
        url = (
            f"http://127.0.0.1:{port}/index.html"
            f"?topic={mounted_topic_url(topic_path)}&render=1&offline=1"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        frame_total = max(1, math.ceil(duration * fps))
        pipe_codec = "png" if capture_format == "png" else "mjpeg"
        use_core_encoder = encoder_backend == "aurex-render"
        core_video = output.with_name(f".{output.stem}-core-video-{uuid.uuid4().hex[:8]}.mp4") if use_core_encoder else None
        core_report = core_video.with_name(f"{core_video.stem}.render-report.json") if core_video else None
        if mezzanine and not use_core_encoder:
            # Keep the browser raster in lossless RGB until the optional
            # branding/outro pass. This avoids H.264 -> H.264 generations.
            scale_filter = (
                f"scale={width}:{height}:flags=lanczos:in_range=pc:out_range=pc,"
                "setsar=1,format=gbrp"
            )
            video_options = [
                "-c:v", "ffv1", "-level", "3", "-coder", "1", "-context", "1", "-g", "1",
                "-pix_fmt", "gbrp",
            ]
            audio_options = [
                "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", "-channel_layout", "stereo",
            ]
            mux_options = ["-f", "matroska"]
        elif not use_core_encoder:
            # Playwright screenshots are full-range RGB/sRGB. Declare the
            # input range explicitly before converting to delivery BT.709 TV
            # range so we perform a real conversion instead of relabelling it.
            scale_filter = (
                f"scale={width}:{height}:flags=lanczos:in_range=pc:out_range=pc,"
                "setsar=1,"
                "colorspace=iall=bt709:all=bt709:range=tv:irange=pc:"
                f"format={quality.pixel_format}:fast=1"
            )
            video_options = quality.encoder_video_options(fps)
            audio_options = [
                "-c:a", "aac", "-b:a", quality.audio_bitrate,
                "-ar", "48000", "-ac", "2", "-channel_layout", "stereo",
            ]
            mux_options = ["-use_editlist", "0", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart"]
        if use_core_encoder:
            # The browser remains a scene adapter for features not yet
            # expressed by the native manifest. Core owns the raw-frame
            # timeline and H.264/VideoToolbox delivery for the whole project.
            core_command = [
                str(core_binary), "encode-raw",
                "--width", str(width), "--height", str(height),
                "--fps", str(fps), "--frames", str(frame_total),
                "--bit-rate", "8000000", "--hardware-acceleration", "prefer",
                "--output", str(core_video), "--report", str(core_report),
                "--overwrite", "--quiet",
            ]
            encoder = subprocess.Popen(
                core_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        else:
            command = [
                str(ffmpeg_executable()), "-y", "-loglevel", "error",
                "-f", "image2pipe", "-framerate", str(fps), "-vcodec", pipe_codec, "-i", "pipe:0",
                "-i", str(audio),
                "-map", "0:v:0", "-map", "1:a:0",
                "-frames:v", str(frame_total),
                "-vf", scale_filter,
                *video_options,
                "-r", str(fps), "-fps_mode", "cfr",
                *audio_options,
                "-map_metadata", "-1", "-map_chapters", "-1",
                "-shortest", *mux_options, str(output),
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
        core_decode_seconds = 0.0
        capture_bytes = 0
        media_sync_stats: dict = {}
        decoder = None
        render_started = time.perf_counter()
        try:
            async with async_playwright() as playwright:
                chromium_args = [
                    "--autoplay-policy=no-user-gesture-required",
                    "--disable-dev-shm-usage",
                ]
                # Headless Chromium otherwise falls back to SwiftShader on
                # macOS, which makes every canvas/video frame pay for software
                # rasterization even when the machine has a capable Metal GPU.
                if sys.platform == "darwin":
                    chromium_args.extend([
                        "--enable-gpu",
                        "--use-angle=metal",
                        "--ignore-gpu-blocklist",
                    ])
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=chromium_args,
                )
                page = await browser.new_page(
                    viewport={"width": width, "height": height},
                    device_scale_factor=quality.device_scale_factor,
                )
                await page.goto(url, wait_until="networkidle")
                await page.wait_for_function("window.__AUREX_DEMO_READY__ === true", timeout=30_000)
                await page.evaluate("window.prepareOfflineRender()")
                if encoder.stdin is None:
                    raise RuntimeError("Không mở được luồng frame cho bộ mã hóa.")
                last_good_frame = None
                frame_errors = 0
                reused_frames = 0
                for frame_index in range(frame_total):
                    frame_time = frame_index / fps
                    frame = None
                    last_error: Exception | None = None
                    for attempt in range(3):
                        try:
                            stage_started = time.perf_counter()
                            await asyncio.wait_for(
                                page.evaluate(
                                    "(time) => window.renderOfflineFrame(time)", frame_time
                                ),
                                timeout=20,
                            )
                            evaluate_seconds += time.perf_counter() - stage_started
                            stage_started = time.perf_counter()
                            screenshot_options = {"type": capture_format}
                            if capture_format == "jpeg":
                                screenshot_options["quality"] = capture_quality
                            frame = await asyncio.wait_for(
                                page.screenshot(**screenshot_options), timeout=30
                            )
                            capture_seconds += time.perf_counter() - stage_started
                            capture_bytes += len(frame)
                            break
                        except Exception as exc:  # noqa: BLE001
                            last_error = exc
                            if attempt < 2:
                                print(
                                    f"WARN frame {frame_time:.3f}s lỗi, retry {attempt + 1}/2: {exc!r}",
                                    flush=True,
                                )
                    if frame is None:
                        frame_errors += 1
                        if not quality.allow_frame_reuse or last_good_frame is None:
                            raise RuntimeError(
                                f"Frame {frame_time:.3f}s thất bại sau 3 lần thử; profile {quality.name} không cho phép dùng lại frame."
                            ) from last_error
                        reused_frames += 1
                        print(f"WARN frame {frame_time:.3f}s dùng lại frame trước", flush=True)
                    else:
                        last_good_frame = frame
                    stage_started = time.perf_counter()
                    if last_good_frame is None:
                        raise RuntimeError(f"Không có frame hợp lệ tại {frame_time:.3f}s.")
                    if use_core_encoder:
                        assert image_decoder is not None
                        decode_started = time.perf_counter()
                        raw_frame = image_decoder(last_good_frame)
                        core_decode_seconds += time.perf_counter() - decode_started
                        encoder.stdin.write(raw_frame)
                        encoder.stdin.flush()
                    else:
                        encoder.stdin.write(last_good_frame)
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
            f"profile={quality.name} frames={frame_total} format={capture_format} "
            f"scale={quality.device_scale_factor:g}x total={total_seconds:.3f}s "
            f"evaluate={evaluate_seconds:.3f}s capture={capture_seconds:.3f}s "
            f"pipe_wait={pipe_seconds:.3f}s core_decode={core_decode_seconds:.3f}s "
            f"setup_and_encoder_drain={max(0.0, total_seconds - evaluate_seconds - capture_seconds - pipe_seconds - core_decode_seconds):.3f}s "
            f"captured={capture_bytes / 1024 / 1024:.1f}MiB "
            f"frame_errors={frame_errors} reused_frames={reused_frames}",
            flush=True,
        )
        if media_sync_stats:
            print(
                "Character sync profile: "
                f"seeks={int(media_sync_stats.get('seeks', 0))} "
                f"skipped_seeks={int(media_sync_stats.get('skippedSeeks', 0))} "
                f"play_waits={int(media_sync_stats.get('playWaits', 0))} "
                f"drift_corrections={int(media_sync_stats.get('playDriftCorrections', 0))} "
                f"pose_changes={int(media_sync_stats.get('poseChanges', 0))} "
                f"pose_resets={int(media_sync_stats.get('poseResets', 0))} "
                f"seek_wait={float(media_sync_stats.get('seekWaitMs', 0)) / 1000:.3f}s "
                f"play_wait={float(media_sync_stats.get('playWaitMs', 0)) / 1000:.3f}s "
                f"max_drift={float(media_sync_stats.get('maxDriftMs', 0)):.1f}ms",
                flush=True,
            )
        if returncode != 0:
            engine_name = "Aurex Render Core" if use_core_encoder else "FFmpeg"
            raise RuntimeError(f"{engine_name} không mã hóa được video frame-by-frame: {stderr.strip()}")

        if use_core_encoder:
            if core_video is None or not core_video.is_file():
                raise RuntimeError("Aurex Render Core không tạo được video trung gian.")
            muxed_output = output.with_name(f".{output.stem}-core-muxed-{uuid.uuid4().hex[:8]}.mp4")
            mux_command = audio_mux_command(
                ffmpeg=ffmpeg_executable(),
                video=core_video,
                audio=audio,
                output=muxed_output,
                audio_bitrate=quality.audio_bitrate,
            )
            mux_process = subprocess.run(mux_command, check=False, capture_output=True)
            try:
                if mux_process.returncode != 0:
                    detail = (mux_process.stderr or mux_process.stdout).decode("utf-8", errors="replace").strip()
                    raise RuntimeError(f"Core video/audio mux thất bại: {detail}")
                muxed_output.replace(output)
            finally:
                muxed_output.unlink(missing_ok=True)
                core_video.unlink(missing_ok=True)

        report = {
            "schema_version": 1,
            "quality_profile": quality.to_dict(),
            "mezzanine": mezzanine,
            "encoder_backend": "aurex-render" if use_core_encoder else "browser-ffmpeg",
            "width": width,
            "height": height,
            "fps": fps,
            "duration_seconds": round(media_duration(output), 3),
            "requested_frames": frame_total,
            "frame_errors": frame_errors,
            "reused_frames": reused_frames,
            "capture_bytes": capture_bytes,
            "timing_seconds": {
                "total": round(total_seconds, 3),
                "evaluate": round(evaluate_seconds, 3),
                "capture": round(capture_seconds, 3),
                "pipe_wait": round(pipe_seconds, 3),
                "core_decode": round(core_decode_seconds, 3),
            },
            "character_sync": media_sync_stats,
            "output_size_bytes": output.stat().st_size if output.is_file() else 0,
        }
        if core_report and core_report.is_file():
            try:
                report["core_report"] = json.loads(core_report.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                report["core_report"] = {"path": str(core_report)}
            finally:
                core_report.unlink(missing_ok=True)
        report_path = output.with_name(f"{output.stem}.render-report.json")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def render(
    topic_path: Path,
    output: Path,
    width: int,
    height: int,
    fps: int = 30,
    benchmark_seconds: float | None = None,
    profile_name: str | None = None,
    capture_format: str | None = None,
    capture_quality: int | None = None,
    mezzanine: bool = False,
    encoder_backend: str = "browser",
    core_binary: Path | None = None,
) -> None:
    quality = get_render_profile(profile_name)
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
            capture_quality=capture_quality, profile=quality, mezzanine=mezzanine,
            encoder_backend=encoder_backend, core_binary=core_binary,
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
    parser.add_argument("--quality-profile", choices=quality_profile_names())
    parser.add_argument(
        "--mezzanine",
        action="store_true",
        help="Xuất FFV1/PCM lossless để branding/outro encode H.264 đúng một lần.",
    )
    parser.add_argument(
        "--encoder-backend",
        choices=["browser", "aurex-render"],
        default="browser",
        help="Bộ mã hóa delivery; aurex-render dùng Core/VideoToolbox cho mọi scene raster.",
    )
    parser.add_argument("--core-binary", type=Path)
    parser.add_argument("--capture-format", choices=["png", "jpeg"])
    parser.add_argument("--capture-quality", type=int, choices=range(0, 101))
    args = parser.parse_args()
    asyncio.run(render(
        args.topic.resolve(), args.output.resolve(), args.width, args.height, args.fps,
        benchmark_seconds=args.benchmark_seconds, capture_format=args.capture_format,
        capture_quality=args.capture_quality, profile_name=args.quality_profile,
        mezzanine=args.mezzanine, encoder_backend=args.encoder_backend,
        core_binary=args.core_binary.resolve() if args.core_binary else None,
    ))
    print(args.output.resolve())


if __name__ == "__main__":
    main()
