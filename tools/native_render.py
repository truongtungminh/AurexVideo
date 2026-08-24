"""Capability-aware bridge from the Python pipeline to Aurex Render Core.

The native MVP deliberately has a narrow, explicit scene contract. A project
can provide a complete ``nativeRenderManifest``/``native-render.json`` or an
inline ``nativeRenderScene`` made only from solid and image layers. Standard
AurexVideo topics are never approximated: scenes that still need text,
karaoke, presenter poses, or comparison timelines stay on Browser.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import platform
import subprocess


CORE_SCHEMA_VERSION = 1
CORE_MVP_LAYER_TYPES = frozenset({"solid", "image"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm"})


class NativeRenderUnavailable(RuntimeError):
    """The project or machine cannot use the native backend for this render."""

    def __init__(self, message: str, *, reason: str = "native_unavailable") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class NativeScene:
    source_manifest: Path | None
    manifest_dir: Path
    document: dict[str, object]
    origin: str
    layer_types: tuple[str, ...]


@dataclass(frozen=True)
class NativeRenderPlan:
    binary: Path
    source_manifest: Path | None
    manifest: Path
    output: Path
    report: Path
    manifest_origin: str
    layer_types: tuple[str, ...]
    capabilities: dict[str, object]

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
            raise NativeRenderUnavailable(
                f"AUREX_RENDER_CORE_BIN không tồn tại hoặc không executable: {path}",
                reason="core_binary_unavailable",
            )
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


def _read_topic(topic_path: Path) -> dict[str, object]:
    try:
        topic = json.loads(topic_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NativeRenderUnavailable(
            f"Không đọc được topic để đánh giá Core: {exc}",
            reason="topic_invalid",
        ) from exc
    if not isinstance(topic, dict):
        raise NativeRenderUnavailable(
            "topic.json phải là JSON object để dùng Core.",
            reason="topic_invalid",
        )
    return topic


def _manifest_value(topic_path: Path, topic: dict[str, object] | None = None) -> object:
    topic = topic or _read_topic(topic_path)
    explicit = topic.get("nativeRenderManifest")
    if explicit is not None and str(explicit).strip():
        return explicit
    conventional = topic_path.parent / "native-render.json"
    if conventional.is_file():
        return conventional.name
    raise NativeRenderUnavailable(
        "Project chưa có nativeRenderManifest hoặc native-render.json.",
        reason="native_scene_contract_missing",
    )


def resolve_native_manifest(topic_path: Path) -> Path:
    project = topic_path.parent.resolve()
    value = _manifest_value(topic_path)
    relative = Path(str(value))
    if relative.is_absolute() or ".." in relative.parts:
        raise NativeRenderUnavailable(
            "nativeRenderManifest phải là đường dẫn tương đối an toàn trong project.",
            reason="native_manifest_path_unsafe",
        )
    manifest = (project / relative).resolve()
    if not _inside(manifest, project) or not manifest.is_file():
        raise NativeRenderUnavailable(
            f"Không tìm thấy native manifest trong project: {manifest}",
            reason="native_manifest_missing",
        )
    return manifest


def topic_scene_features(topic: dict[str, object]) -> tuple[str, ...]:
    """Return browser-only visual features present in a standard topic."""
    features: set[str] = set()
    segments = topic.get("segments")
    if isinstance(segments, list) and any(
        isinstance(item, dict) and str(item.get("text") or "").strip()
        for item in segments
    ):
        features.update({"text", "karaoke"})
    if any(str(topic.get(key) or "").strip() for key in (
        "leftLabel", "rightLabel", "leftSubLabel", "rightSubLabel",
    )):
        features.add("text")

    pose_assets = topic.get("poseAssets")
    if isinstance(pose_assets, dict):
        pose_sources = []
        for value in pose_assets.values():
            if not isinstance(value, dict):
                continue
            pose_sources.extend((value.get("closed"), value.get("speaking")))
        if any(Path(str(value)).suffix.lower() in VIDEO_SUFFIXES for value in pose_sources if value):
            features.add("pose-video")
        elif pose_assets:
            features.add("presenter-image")
    if isinstance(topic.get("poseTimeline"), list) and topic.get("poseTimeline"):
        features.add("pose-timeline")
    if isinstance(topic.get("comparisons"), list) and topic.get("comparisons"):
        features.add("comparison-timeline")
    if any(str(topic.get(key) or "").strip() for key in ("leftImage", "rightImage")):
        features.add("comparison-layout")
    if str(topic.get("backgroundType") or "").strip().lower() == "video":
        features.add("background-video")

    order = (
        "text", "karaoke", "pose-video", "presenter-image", "pose-timeline",
        "comparison-timeline", "comparison-layout", "background-video",
    )
    return tuple(item for item in order if item in features)


def _load_manifest_document(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NativeRenderUnavailable(
            f"Native manifest không hợp lệ: {exc}",
            reason="native_manifest_invalid",
        ) from exc
    if not isinstance(document, dict):
        raise NativeRenderUnavailable(
            "Native manifest phải là JSON object.",
            reason="native_manifest_invalid",
        )
    return document


def _inline_scene_document(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise NativeRenderUnavailable(
            "nativeRenderScene phải là JSON object.",
            reason="native_scene_invalid",
        )
    document = copy.deepcopy(value)
    document.setdefault("schemaVersion", CORE_SCHEMA_VERSION)
    canvas = document.setdefault("canvas", {})
    if not isinstance(canvas, dict):
        raise NativeRenderUnavailable(
            "nativeRenderScene.canvas phải là JSON object.",
            reason="native_scene_invalid",
        )
    canvas.setdefault("width", 320)
    canvas.setdefault("height", 576)
    canvas.setdefault("frameRate", {"numerator": 30, "denominator": 1})
    canvas.setdefault("frameCount", 30)
    canvas.setdefault("backgroundColor", str(document.get("backgroundColor") or "#000000"))
    document.setdefault("layers", [])
    return document


def _validate_scene_document(document: dict[str, object], manifest_dir: Path) -> tuple[str, ...]:
    if document.get("schemaVersion") != CORE_SCHEMA_VERSION:
        raise NativeRenderUnavailable(
            f"Native scene dùng schemaVersion={document.get('schemaVersion')}; Core MVP cần schemaVersion=1.",
            reason="native_schema_unsupported",
        )
    layers = document.get("layers")
    if not isinstance(layers, list):
        raise NativeRenderUnavailable(
            "Native scene.layers phải là array.",
            reason="native_scene_invalid",
        )
    layer_types: set[str] = set()
    for index, layer in enumerate(layers):
        if not isinstance(layer, dict):
            raise NativeRenderUnavailable(
                f"Native scene layer #{index + 1} phải là object.",
                reason="native_scene_invalid",
            )
        layer_type = str(layer.get("type") or "").strip().lower()
        if layer_type not in CORE_MVP_LAYER_TYPES:
            display = layer_type or "<missing>"
            raise NativeRenderUnavailable(
                f"Core MVP không hỗ trợ layer type '{display}'; chỉ hỗ trợ solid/image.",
                reason=f"unsupported_layer_type:{display}",
            )
        layer_types.add(layer_type)
        if layer_type != "image":
            continue
        source = str(layer.get("source") or "").strip()
        relative = Path(source)
        if not source or relative.is_absolute() or ".." in relative.parts:
            raise NativeRenderUnavailable(
                f"Image layer '{layer.get('id') or index}' cần source tương đối an toàn.",
                reason="native_image_path_unsafe",
            )
        asset = (manifest_dir / relative).resolve()
        if not _inside(asset, manifest_dir.resolve()) or not asset.is_file():
            raise NativeRenderUnavailable(
                f"Không tìm thấy image asset của Core: {source}",
                reason="native_image_missing",
            )
    return tuple(sorted(layer_types))


def resolve_native_scene(topic_path: Path) -> NativeScene:
    """Resolve the complete native scene contract without approximating Browser."""
    topic = _read_topic(topic_path)
    project = topic_path.parent.resolve()
    explicit = topic.get("nativeRenderManifest")
    conventional = project / "native-render.json"
    if explicit is not None and str(explicit).strip():
        source = resolve_native_manifest(topic_path)
        origin = "nativeRenderManifest"
        document = _load_manifest_document(source)
        manifest_dir = source.parent.resolve()
        layer_types = _validate_scene_document(document, manifest_dir)
        return NativeScene(source, manifest_dir, document, origin, layer_types)
    if topic.get("nativeRenderScene") is not None:
        document = _inline_scene_document(topic.get("nativeRenderScene"))
        layer_types = _validate_scene_document(document, project)
        return NativeScene(None, project, document, "nativeRenderScene", layer_types)
    if conventional.is_file():
        source = conventional.resolve()
        document = _load_manifest_document(source)
        layer_types = _validate_scene_document(document, source.parent.resolve())
        return NativeScene(source, source.parent.resolve(), document, "native-render.json", layer_types)

    features = topic_scene_features(topic)
    if features:
        raise NativeRenderUnavailable(
            "Core MVP chỉ hỗ trợ solid/image; scene hiện tại cần " + ", ".join(features) + ".",
            reason="unsupported_scene_features:" + ",".join(features),
        )
    raise NativeRenderUnavailable(
        "Scene chưa khai báo nativeRenderScene hoặc native manifest đầy đủ.",
        reason="native_scene_contract_missing",
    )


def read_native_capabilities(binary: Path) -> dict[str, object]:
    try:
        process = subprocess.run(
            [str(binary), "capabilities"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeRenderUnavailable(
            f"Không đọc được capabilities từ aurex-render: {exc}",
            reason="core_capabilities_unavailable",
        ) from exc
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip()
        raise NativeRenderUnavailable(
            f"aurex-render capabilities thất bại: {detail or f'exit {process.returncode}'}",
            reason="core_capabilities_unavailable",
        )
    try:
        capabilities = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise NativeRenderUnavailable(
            f"aurex-render trả capabilities không hợp lệ: {exc}",
            reason="core_capabilities_invalid",
        ) from exc
    if not isinstance(capabilities, dict):
        raise NativeRenderUnavailable(
            "aurex-render capabilities phải là JSON object.",
            reason="core_capabilities_invalid",
        )
    if capabilities.get("manifestSchemaVersion") != CORE_SCHEMA_VERSION:
        raise NativeRenderUnavailable(
            "Binary aurex-render không hỗ trợ manifest schemaVersion=1.",
            reason="core_schema_unsupported",
        )
    if not capabilities.get("metalDevice") or not capabilities.get("h264Encoders"):
        raise NativeRenderUnavailable(
            "Máy chưa có Metal device hoặc H.264 encoder cho Aurex Render Core.",
            reason="core_runtime_unavailable",
        )
    return capabilities


def validate_manifest_with_core(binary: Path, manifest: Path) -> None:
    try:
        process = subprocess.run(
            [str(binary), "validate", "--manifest", str(manifest)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeRenderUnavailable(
            f"Không validate được native manifest: {exc}",
            reason="native_manifest_validation_failed",
        ) from exc
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip()
        raise NativeRenderUnavailable(
            f"aurex-render từ chối manifest: {detail or f'exit {process.returncode}'}",
            reason="native_manifest_validation_failed",
        )


def _prepare_manifest(
    source: Path,
    destination: Path,
    width: int,
    height: int,
    fps: int,
    duration: float,
) -> None:
    document = _load_manifest_document(source)
    _prepare_manifest_document(document, destination, width, height, fps, duration)


def _prepare_manifest_document(
    source_document: dict[str, object],
    destination: Path,
    width: int,
    height: int,
    fps: int,
    duration: float,
) -> None:
    document = copy.deepcopy(source_document)
    canvas = document.get("canvas")
    if not isinstance(canvas, dict):
        raise NativeRenderUnavailable(
            "Native manifest thiếu canvas object.",
            reason="native_manifest_invalid",
        )

    source_frame_rate = canvas.get("frameRate")
    try:
        source_fps = float(source_frame_rate["numerator"]) / float(source_frame_rate["denominator"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise NativeRenderUnavailable(
            "Native manifest thiếu frameRate hợp lệ.",
            reason="native_manifest_invalid",
        ) from exc
    if not math.isfinite(source_fps) or source_fps <= 0:
        raise NativeRenderUnavailable(
            "Native manifest có frameRate không hợp lệ.",
            reason="native_manifest_invalid",
        )

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
    scene = resolve_native_scene(topic_path)
    binary = find_native_binary(resource_root)
    if binary is None:
        raise NativeRenderUnavailable(
            "Không tìm thấy aurex-render trên máy; cần build/stage Core binary.",
            reason="core_binary_unavailable",
        )
    capabilities = read_native_capabilities(binary)
    supported = {
        str(value).strip().lower()
        for value in capabilities.get("supportedLayerTypes", [])
        if str(value).strip()
    }
    missing = sorted(set(scene.layer_types) - supported)
    if missing:
        raise NativeRenderUnavailable(
            "Binary aurex-render không hỗ trợ layer: " + ", ".join(missing) + ".",
            reason="core_layer_capability_mismatch:" + ",".join(missing),
        )
    manifest = scene.manifest_dir / f".native-render-{token}.json"
    try:
        _prepare_manifest_document(scene.document, manifest, width, height, fps, duration)
        validate_manifest_with_core(binary, manifest)
        return NativeRenderPlan(
            binary=binary,
            source_manifest=scene.source_manifest,
            manifest=manifest,
            output=output,
            report=report,
            manifest_origin=scene.origin,
            layer_types=scene.layer_types,
            capabilities=capabilities,
        )
    except Exception:
        manifest.unlink(missing_ok=True)
        raise


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
        "-use_editlist",
        "0",
        "-avoid_negative_ts",
        # The native H.264 stream already starts at PTS 0. ``make_zero``
        # shifts AAC priming into the first copied video sample and changes
        # the measured FPS; keep the linear timestamps untouched instead.
        "disabled",
        "-movflags",
        "+faststart",
        str(output),
    ]
