"""Compile a standard AurexVideo topic into the Aurex Scene IR v2.

The compiler is intentionally kept outside the Swift core.  It owns the
product/editor schema (topic.json) and emits a small, typed scene contract
that the native compositor can render without starting a browser.
"""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import shutil
from typing import Any


class SceneCompileError(RuntimeError):
    """A topic cannot be represented by Scene IR v2."""

    def __init__(self, message: str, *, reason: str = "native_scene_compile_failed") -> None:
        super().__init__(message)
        self.reason = reason


VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm"})
SCENE_FPS = 30
WORD_EDGE_EPSILON = 0.035
WORD_TAIL_EPSILON = 0.08


_STYLE_PROFILES: dict[str, dict[str, Any]] = {
    "bietchichomet": {
        "presenter_full_stage": True,
        "media_rects": {
            "left": {"x": 0.030, "y": 0.240, "width": 0.420, "height": 0.230},
            "right": {"x": 0.460, "y": 0.240, "width": 0.420, "height": 0.230},
        },
        "label_rects": {
            "left": {"x": 0.030, "y": 0.165, "width": 0.420, "height": 0.070},
            "right": {"x": 0.460, "y": 0.165, "width": 0.420, "height": 0.070},
        },
        "karaoke_rect": {"x": 0.050, "y": 0.462, "width": 0.900, "height": 0.090},
        "label_font_size": 54.0,
        "karaoke_font_size": 59.9,
        "border_color": "#8b5a2b",
        "border_inset": 0.006,
        "border": True,
    },
    "default": {
        "presenter_full_stage": False,
        "presenter_rect": {"x": 0.180, "y": 0.475, "width": 0.640, "height": 0.510},
        "media_rects": {
            "left": {"x": 0.050, "y": 0.171, "width": 0.444, "height": 0.256},
            "right": {"x": 0.506, "y": 0.171, "width": 0.444, "height": 0.256},
        },
        "label_rects": {
            "left": {"x": 0.050, "y": 0.070, "width": 0.444, "height": 0.100},
            "right": {"x": 0.506, "y": 0.070, "width": 0.444, "height": 0.100},
        },
        "karaoke_rect": {"x": 0.050, "y": 0.462, "width": 0.900, "height": 0.090},
        "label_font_size": 58.0,
        "karaoke_font_size": 59.9,
        "border": False,
    },
}


def _number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _frame(time_seconds: object, frame_count: int) -> int:
    return max(0, min(frame_count, int(round(_number(time_seconds) * SCENE_FPS))))


def _rect(value: object, fallback: dict[str, float]) -> dict[str, float]:
    if not isinstance(value, dict):
        return dict(fallback)
    return {
        "x": _number(value.get("x"), fallback["x"]),
        "y": _number(value.get("y"), fallback["y"]),
        "width": max(0.001, _number(value.get("width"), fallback["width"])),
        "height": max(0.001, _number(value.get("height"), fallback["height"])),
    }


def _safe_filename(source: Path) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", source.name).strip("-") or "asset"
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:12]
    return f"{digest}-{name}"


def _stage_asset(
    project: Path,
    value: object,
    stage_dir: Path,
    cache: dict[Path, str],
    *,
    label: str,
) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SceneCompileError(f"Thiếu asset {label}.", reason="native_scene_asset_missing")
    candidate = Path(raw)
    if candidate.is_absolute():
        source = candidate.resolve()
    else:
        source = (project / candidate).resolve()
    if not source.is_file():
        raise SceneCompileError(
            f"Không tìm thấy asset {label}: {raw}",
            reason="native_scene_asset_missing",
        )
    if source in cache:
        return cache[source]
    relative = Path("assets") / _safe_filename(source)
    destination = stage_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            raise SceneCompileError(
                f"Không staging được asset {label}: {source}",
                reason="native_scene_asset_stage_failed",
            ) from exc
    value = relative.as_posix()
    cache[source] = value
    return value


def _stage_font(resource_root: Path, stage_dir: Path, cache: dict[Path, str]) -> str:
    return _stage_asset(
        resource_root,
        Path("assets/fonts/Inter-Bold.ttf"),
        stage_dir,
        cache,
        label="font Inter-Bold",
    )


def _tokenize(text: object) -> list[str]:
    return [item for item in re.split(r"\s+", str(text or "").strip()) if item]


def _format_label(text: object) -> str:
    words = _tokenize(text)
    if len(words) < 4:
        return " ".join(words)
    middle = math.ceil(len(words) / 2)
    return f"{' '.join(words[:middle])}\n{' '.join(words[middle:])}"


def _timed_words(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment in segments:
        segment_start = _number(segment.get("start"))
        segment_end = _number(segment.get("end"), segment_start)
        supplied = segment.get("words")
        if isinstance(supplied, list) and supplied:
            for item in supplied:
                if not isinstance(item, dict):
                    continue
                words.append({
                    "word": str(item.get("word") or item.get("text") or ""),
                    "start": _number(item.get("start"), segment_start),
                    "end": _number(item.get("end"), segment_end),
                    "segmentEnd": segment_end,
                })
            continue
        tokens = _tokenize(segment.get("text"))
        if not tokens:
            continue
        duration = max(0.05, segment_end - segment_start)
        weights = [max(1, sum(1 for char in token if char.isalnum())) for token in tokens]
        total = sum(weights) or len(tokens)
        cursor = segment_start
        for index, token in enumerate(tokens):
            end = segment_end if index == len(tokens) - 1 else cursor + duration * weights[index] / total
            words.append({"word": token, "start": cursor, "end": end, "segmentEnd": segment_end})
            cursor = end
    return words


def _word_groups(words: list[dict[str, Any]], *, karaoke_size: float) -> list[list[int]]:
    groups: list[list[int]] = []
    max_words = 3
    max_chars = max(18, min(42, math.floor(31.5 / max(0.6, min(1.5, karaoke_size)))))
    index = 0
    while index < len(words):
        segment_end = words[index]["segmentEnd"]
        candidates: list[int] = []
        while index < len(words) and words[index]["segmentEnd"] == segment_end:
            candidates.append(index)
            index += 1
        current: list[int] = []
        for word_index in candidates:
            next_group = [*current, word_index]
            next_text = " ".join(str(words[item]["word"]) for item in next_group)
            if current and (len(current) >= max_words or len(next_text) > max_chars):
                groups.append(current)
                current = [word_index]
            else:
                current = next_group
        if current:
            groups.append(current)
    return groups


def _scene_window(
    scene: dict[str, Any],
    segments: list[dict[str, Any]],
    duration: float,
    default_start_sentence: int,
    next_start_sentence: int | None,
) -> tuple[float, float]:
    start_sentence = int(_number(scene.get("startSentence"), default_start_sentence))
    start_index = max(0, start_sentence - 1)
    start = _number(segments[start_index].get("start")) if start_index < len(segments) else 0.0
    if next_start_sentence is None:
        return start, duration
    next_index = max(0, next_start_sentence - 1)
    end = _number(segments[next_index].get("start"), duration) if next_index < len(segments) else duration
    return start, max(start, min(duration, end))


def _text_layer(
    layer_id: str,
    *,
    start_frame: int,
    end_frame: int,
    rect: dict[str, float],
    font_source: str,
    text: str | None = None,
    spans: list[dict[str, str]] | None = None,
    color: str = "#111111",
    font_size: float = 48.0,
    line_height: float = 1.15,
    z_index: int = 5,
) -> dict[str, Any]:
    layer: dict[str, Any] = {
        "id": layer_id,
        "type": "text",
        "zIndex": z_index,
        "startFrame": start_frame,
        "endFrame": max(start_frame + 1, end_frame),
        "rect": rect,
        "fontFamily": "Inter",
        "fontSource": font_source,
        "fontSize": font_size,
        "fontWeight": 800,
        "lineHeight": line_height,
        "textAlignment": "center",
        "textColor": color,
    }
    if text is not None:
        layer["text"] = text
    if spans:
        layer["spans"] = spans
    return layer


def compile_standard_topic(
    topic_path: Path,
    *,
    staging_dir: Path,
    resource_root: Path,
) -> dict[str, Any]:
    """Compile a normal topic into a self-contained Scene IR v2 document."""
    try:
        topic = __import__("json").loads(topic_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SceneCompileError(f"Không đọc được topic: {exc}", reason="topic_invalid") from exc
    if not isinstance(topic, dict):
        raise SceneCompileError("topic.json phải là JSON object.", reason="topic_invalid")

    project = topic_path.parent.resolve()
    staging_dir = staging_dir.resolve()
    staging_dir.mkdir(parents=True, exist_ok=True)
    asset_cache: dict[Path, str] = {}
    font_source = _stage_font(resource_root.resolve(), staging_dir, asset_cache)
    duration = max(0.001, _number(topic.get("duration"), 0.001))
    frame_count = max(1, math.ceil(duration * SCENE_FPS))
    width, height = 1080, 1920
    character_id = str(topic.get("characterId") or topic.get("brand") or "").strip().lower()
    profile = _STYLE_PROFILES.get(character_id, _STYLE_PROFILES["default"])
    background_type = str(topic.get("backgroundType") or "default").strip().lower()
    if background_type == "video":
        raise SceneCompileError(
            "Scene IR v2 chưa hỗ trợ background video.",
            reason="unsupported_scene_features:background-video",
        )
    background_color = str(topic.get("backgroundColor") or "#f5eee3")

    layers: list[dict[str, Any]] = []
    if background_type == "image" and str(topic.get("backgroundImage") or "").strip():
        layers.append({
            "id": "background-image",
            "type": "image",
            "zIndex": 0,
            "rect": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
            "source": _stage_asset(project, topic.get("backgroundImage"), staging_dir, asset_cache, label="background"),
            "contentMode": "fill",
            "zoom": _number(topic.get("backgroundImageZoom"), 1.0),
            "panX": _number(topic.get("backgroundImageX")),
            "panY": _number(topic.get("backgroundImageY")),
        })

    pose_assets = topic.get("poseAssets") if isinstance(topic.get("poseAssets"), dict) else {}
    timeline = topic.get("poseTimeline") if isinstance(topic.get("poseTimeline"), list) else []
    pose_events = [item for item in timeline if isinstance(item, dict) and str(item.get("pose") or "").strip()]
    if not pose_events and pose_assets:
        pose_events = [{"time": 0.0, "pose": next(iter(pose_assets))}]
    pose_events.sort(key=lambda item: _number(item.get("time")))
    for index, event in enumerate(pose_events):
        pose_name = str(event.get("pose") or "")
        pose = pose_assets.get(pose_name) if isinstance(pose_assets, dict) else None
        if not isinstance(pose, dict):
            continue
        source_value = pose.get("speaking") or pose.get("closed")
        source_path = _stage_asset(project, source_value, staging_dir, asset_cache, label=f"pose {pose_name}")
        start = max(0.0, _number(event.get("time")))
        end = duration if index + 1 >= len(pose_events) else min(duration, _number(pose_events[index + 1].get("time"), duration))
        if end <= start:
            continue
        is_video = Path(source_path).suffix.lower() in VIDEO_SUFFIXES
        rect = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0} if profile.get("presenter_full_stage") and is_video else dict(profile.get("presenter_rect", {"x": 0.18, "y": 0.475, "width": 0.64, "height": 0.51}))
        layer: dict[str, Any] = {
            "id": f"presenter-{index:03d}-{pose_name}",
            "type": "video" if is_video else "image",
            "zIndex": 1,
            "startFrame": _frame(start, frame_count),
            "endFrame": _frame(end, frame_count),
            "rect": rect,
            "source": source_path,
            "contentMode": "fill" if is_video else "fit",
            "opacity": 1.0,
        }
        if is_video:
            layer.update({
                "videoSyncMode": str(pose.get("syncMode") or "scene"),
                "videoLoop": pose.get("loop") is not False,
                "videoLoopStart": max(0.0, _number(pose.get("loopStart"))),
                "videoLoopEnd": max(0.0, _number(pose.get("loopEnd"))),
            })
        layers.append(layer)

    segments = [item for item in topic.get("segments", []) if isinstance(item, dict)]
    comparisons: list[dict[str, Any]] = []
    if topic.get("baseComparisonEnabled") is not False:
        comparisons.append({
            "id": "base",
            "startSentence": 1,
            "leftLabel": topic.get("leftLabel"),
            "rightLabel": topic.get("rightLabel"),
            "leftSubLabel": topic.get("leftSubLabel"),
            "rightSubLabel": topic.get("rightSubLabel"),
            "leftImage": topic.get("leftImage"),
            "rightImage": topic.get("rightImage"),
            "leftImageZoom": topic.get("leftImageZoom"),
            "leftImageX": topic.get("leftImageX"),
            "leftImageY": topic.get("leftImageY"),
            "rightImageZoom": topic.get("rightImageZoom"),
            "rightImageX": topic.get("rightImageX"),
            "rightImageY": topic.get("rightImageY"),
            "leftLabelColor": topic.get("leftLabelColor") or topic.get("labelColor") or "#090909",
            "rightLabelColor": topic.get("rightLabelColor") or topic.get("labelColor") or "#090909",
            "leftSubLabelColor": topic.get("leftSubLabelColor") or "#808080",
            "rightSubLabelColor": topic.get("rightSubLabelColor") or "#808080",
            "labelFontFamily": topic.get("labelFontFamily") or "Inter",
            "showSubLabels": topic.get("showSubLabels") is True,
        })
    extra_comparisons = topic.get("comparisons") if isinstance(topic.get("comparisons"), list) else []
    comparisons.extend(item for item in extra_comparisons if isinstance(item, dict))
    comparisons.sort(key=lambda item: int(_number(item.get("startSentence"), 1)))
    for scene_index, scene in enumerate(comparisons):
        next_start_sentence = None
        if scene_index + 1 < len(comparisons):
            next_start_sentence = int(_number(comparisons[scene_index + 1].get("startSentence"), 1))
        start, end = _scene_window(scene, segments, duration, 1, next_start_sentence)
        start_frame, end_frame = _frame(start, frame_count), _frame(end, frame_count)
        if end_frame <= start_frame:
            continue
        for side in ("left", "right"):
            image_value = scene.get(f"{side}Image")
            if not str(image_value or "").strip():
                continue
            outer = _rect(profile["media_rects"].get(side), profile["media_rects"]["left"])
            image_rect = dict(outer)
            if profile.get("border"):
                inset = float(profile.get("border_inset", 0.0))
                layers.append({
                    "id": f"comparison-{scene_index:03d}-{side}-border",
                    "type": "solid",
                    "zIndex": 2,
                    "startFrame": start_frame,
                    "endFrame": end_frame,
                    "rect": outer,
                    "color": str(profile.get("border_color") or "#8b5a2b"),
                })
                image_rect = {
                    "x": outer["x"] + inset,
                    "y": outer["y"] + inset,
                    "width": max(0.001, outer["width"] - 2 * inset),
                    "height": max(0.001, outer["height"] - 2 * inset),
                }
            layers.append({
                "id": f"comparison-{scene_index:03d}-{side}-image",
                "type": "image",
                "zIndex": 3,
                "startFrame": start_frame,
                "endFrame": end_frame,
                "rect": image_rect,
                "source": _stage_asset(project, image_value, staging_dir, asset_cache, label=f"{side} comparison"),
                "contentMode": "fill",
                "zoom": max(1.0, min(3.0, _number(scene.get(f"{side}ImageZoom"), 1.0))),
                "panX": max(-50.0, min(50.0, _number(scene.get(f"{side}ImageX")))),
                "panY": max(-50.0, min(50.0, _number(scene.get(f"{side}ImageY")))),
            })
            label_value = _format_label(scene.get(f"{side}Label"))
            if label_value:
                layers.append(_text_layer(
                    f"comparison-{scene_index:03d}-{side}-label",
                    start_frame=start_frame,
                    end_frame=end_frame,
                    rect=_rect(profile["label_rects"].get(side), profile["label_rects"]["left"]),
                    font_source=font_source,
                    text=label_value,
                    color=str(scene.get(f"{side}LabelColor") or "#090909"),
                    font_size=float(profile.get("label_font_size", 58.0)),
                    line_height=0.98,
                    z_index=6,
                ))
            sub_value = str(scene.get(f"{side}SubLabel") or "").strip()
            if scene.get("showSubLabels") is True and sub_value:
                sub_rect = _rect(profile["label_rects"].get(side), profile["label_rects"]["left"])
                sub_rect["y"] += 0.060
                sub_rect["height"] = 0.035
                layers.append(_text_layer(
                    f"comparison-{scene_index:03d}-{side}-sub-label",
                    start_frame=start_frame,
                    end_frame=end_frame,
                    rect=sub_rect,
                    font_source=font_source,
                    text=sub_value,
                    color=str(scene.get(f"{side}SubLabelColor") or "#808080"),
                    font_size=28.0,
                    line_height=1.0,
                    z_index=6,
                ))

    karaoke_color = str(topic.get("karaokeColor") or "#111111")
    active_color = str(topic.get("karaokeActiveColor") or "#de370d")
    karaoke_size = max(0.6, min(1.5, _number(topic.get("karaokeSize"), 1.2)))
    words = _timed_words(segments)
    groups = _word_groups(words, karaoke_size=karaoke_size)
    karaoke_rect = _rect(profile.get("karaoke_rect"), _STYLE_PROFILES["default"]["karaoke_rect"])
    for group_index, group in enumerate(groups):
        for active_offset, word_index in enumerate(group):
            start = _number(words[word_index].get("start")) - WORD_EDGE_EPSILON
            if word_index + 1 < len(words):
                # Browser karaoke swaps groups at the next word boundary. Do
                # not leave the previous group's tail alive at the same frame,
                # otherwise the native compositor would draw two subtitles.
                end = _number(words[word_index + 1].get("start")) - WORD_EDGE_EPSILON
            else:
                end = _number(words[word_index].get("end")) + WORD_TAIL_EPSILON
            start_frame, end_frame = _frame(max(0.0, start), frame_count), _frame(min(duration, end), frame_count)
            if end_frame <= start_frame:
                end_frame = min(frame_count, start_frame + 1)
            spans = [
                {"text": str(words[item].get("word") or ""), "color": active_color if item == word_index else karaoke_color}
                for item in group
            ]
            layers.append(_text_layer(
                f"karaoke-{group_index:03d}-{active_offset:02d}",
                start_frame=start_frame,
                end_frame=end_frame,
                rect=karaoke_rect,
                font_source=font_source,
                spans=spans,
                color=karaoke_color,
                font_size=float(profile.get("karaoke_font_size", 59.9)) * karaoke_size / 1.2,
                line_height=1.25,
                z_index=5,
            ))

    features = ["text", "karaoke"]
    if pose_events:
        features.append("pose-timeline")
    if any(Path(str(item.get("source") or "")).suffix.lower() in VIDEO_SUFFIXES for item in layers if isinstance(item, dict)):
        features.append("pose-video")
    if comparisons:
        features.append("comparison-layout")
    if len(comparisons) > 1:
        features.append("comparison-timeline")
    return {
        "schemaVersion": 2,
        "canvas": {
            "width": width,
            "height": height,
            "frameRate": {"numerator": SCENE_FPS, "denominator": 1},
            "frameCount": frame_count,
            "backgroundColor": background_color,
        },
        "output": {"bitRate": 12_000_000, "hardwareAcceleration": "prefer", "keyFrameIntervalSeconds": 2},
        "metadata": {
            "compiler": "aurex-scene-ir-v2",
            "characterId": character_id,
            "features": features,
        },
        "layers": layers,
    }
