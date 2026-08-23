"""Opt-in bridge from the Python render pipeline to Aurex Render Core.

The native core intentionally has a narrow scene contract in its first MVP.
This bridge keeps that contract explicit: a project must opt in with
``nativeRenderManifest`` in topic.json (or a local ``native-render.json``),
and callers can choose whether an unavailable native path is fatal or falls
back to the browser renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import platform


class NativeRenderUnavailable(RuntimeError):
    """The project or machine cannot use the native backend for this render."""


@dataclass(frozen=True)
class NativeRenderPlan:
    binary: Path
    source_manifest: Path
    manifest: Path
    output: Path
    report: Path

    @property
    def command(self) -> list[str]:
        return [
            str(self.binary),
            "render",
            "--manifest",
            str(self.manifest),
            "--output",
            str(self.output),
            "--report",
            str(self.report),
            "--overwrite",
            "--quiet",
        ]

    def cleanup(self) -> None:
        self.manifest.unlink(missing_ok=True)


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def find_native_binary(resource_root: Path) -> Path | None:
    """Find a packaged or development Aurex Render Core executable."""
    if platform.system() != "Darwin":
        return None

    configured = str(os.environ.get("AUREX_RENDER_CORE_BIN") or "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file() or not os.access(path, os.X_OK):
            raise NativeRenderUnavailable(f"AUREX_RENDER_CORE_BIN không tồn tại: {path}")
        return path

    roots: list[Path] = []
    for root in (resource_root, Path(__file__).resolve().parents[1]):
        resolved = root.expanduser().resolve()
        if resolved not in roots:
            roots.append(resolved)
    candidates: list[Path] = []
    for root in roots:
        package = root / "native" / "AurexRenderCore"
        candidates.extend([
            package / ".build" / "arm64-apple-macosx" / "release" / "aurex-render",
            package / ".build" / "arm64-apple-macosx" / "debug" / "aurex-render",
            package / ".build" / "x86_64-apple-macosx" / "release" / "aurex-render",
            package / ".build" / "x86_64-apple-macosx" / "debug" / "aurex-render",
            root / "native" / "bin" / "aurex-render",
            root / "bin" / "aurex-render",
        ])
    return next(
        (candidate for candidate in candidates if candidate.is_file() and os.access(candidate, os.X_OK)),
        None,
    )


def _manifest_value(topic_path: Path) -> object:
    try:
        topic = json.loads(topic_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NativeRenderUnavailable(f"Không đọc được topic để tìm native manifest: {exc}") from exc
    if not isinstance(topic, dict):
        raise NativeRenderUnavailable("topic.json phải là object để dùng native backend")
    explicit = topic.get("nativeRenderManifest")
    if explicit is not None and str(explicit).strip():
        return explicit
    conventional = topic_path.parent / "native-render.json"
    if conventional.is_file():
        return conventional.name
    raise NativeRenderUnavailable(
        "Project chưa có nativeRenderManifest hoặc native-render.json; giữ browser renderer."
    )


def resolve_native_manifest(topic_path: Path) -> Path:
    project = topic_path.parent.resolve()
    value = _manifest_value(topic_path)
    relative = Path(str(value))
    if relative.is_absolute() or ".." in relative.parts:
        raise NativeRenderUnavailable("nativeRenderManifest phải là đường dẫn tương đối trong project")
    manifest = (project / relative).resolve()
    if not _inside(manifest, project) or not manifest.is_file():
        raise NativeRenderUnavailable(f"Không tìm thấy native manifest trong project: {manifest}")
    return manifest


def _prepare_manifest(
    source: Path,
    destination: Path,
    width: int,
    height: int,
    fps: int,
    duration: float,
) -> None:
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NativeRenderUnavailable(f"Native manifest không hợp lệ: {exc}") from exc
    if not isinstance(document, dict):
        raise NativeRenderUnavailable("Native manifest phải là JSON object")
    canvas = document.get("canvas")
    if not isinstance(canvas, dict):
        raise NativeRenderUnavailable("Native manifest thiếu canvas object")

    source_frame_rate = canvas.get("frameRate")
    try:
        source_fps = float(source_frame_rate["numerator"]) / float(source_frame_rate["denominator"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise NativeRenderUnavailable("Native manifest thiếu frameRate hợp lệ") from exc
    if not math.isfinite(source_fps) or source_fps <= 0:
        raise NativeRenderUnavailable("Native manifest có frameRate không hợp lệ")

    old_frame_count = canvas.get("frameCount")
    frame_count = max(1, math.ceil(max(0.001, float(duration)) * max(1, int(fps))))
    canvas["width"] = int(width)
    canvas["height"] = int(height)
    canvas["frameRate"] = {"numerator": int(fps), "denominator": 1}
    canvas["frameCount"] = frame_count

    # A layer ending exactly at the template's old duration normally means
    # "full scene". Extend that sentinel when the narration duration changes;
    # preserve intentionally shorter layers.
    try:
        old_count = int(old_frame_count)
    except (TypeError, ValueError):
        old_count = 0
    frame_scale = float(fps) / source_fps
    layers = document.get("layers")
    if isinstance(layers, list):
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            try:
                start_frame = int(layer.get("startFrame", 0))
                layer["startFrame"] = int(round(start_frame * frame_scale))
            except (TypeError, ValueError):
                pass
            if layer.get("endFrame") is None:
                continue
            try:
                end_frame = int(layer["endFrame"])
            except (TypeError, ValueError):
                continue
            if old_count > 0 and end_frame == old_count:
                layer["endFrame"] = frame_count
            else:
                layer["endFrame"] = int(round(end_frame * frame_scale))

    destination.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_native_render_plan(
    *,
    topic_path: Path,
    resource_root: Path,
    output: Path,
    report: Path,
    width: int,
    height: int,
    fps: int,
    duration: float,
    token: str,
) -> NativeRenderPlan:
    binary = find_native_binary(resource_root)
    if binary is None:
        raise NativeRenderUnavailable(
            "Không tìm thấy aurex-render trên máy; hãy build native/AurexRenderCore hoặc đóng gói binary."
        )
    source_manifest = resolve_native_manifest(topic_path)
    manifest = source_manifest.with_name(f".native-render-{token}.json")
    _prepare_manifest(source_manifest, manifest, width, height, fps, duration)
    return NativeRenderPlan(
        binary=binary,
        source_manifest=source_manifest,
        manifest=manifest,
        output=output,
        report=report,
    )


def audio_mux_command(
    *,
    ffmpeg: Path,
    video: Path,
    audio: Path,
    output: Path,
    audio_bitrate: str,
) -> list[str]:
    """Mux the prepared narration without re-encoding native H.264 video."""
    return [
        str(ffmpeg),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        str(audio_bitrate),
        "-ar",
        "48000",
        "-ac",
        "2",
        "-shortest",
        "-map_metadata",
        "-1",
        "-movflags",
        "+faststart",
        str(output),
    ]
