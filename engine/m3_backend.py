#!/usr/bin/env python3
"""Local project manager and render UI for the AurexVideo template."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import signal
import shutil
import unicodedata
import subprocess
import sys
from threading import Lock, Thread
import time
import unicodedata
from urllib.parse import parse_qs, quote, unquote, urlparse
import uuid
import wave

from aurexvideo_paths import (
    CHARACTERS_ROOT,
    CONFIG_ROOT,
    DATA_ROOT,
    OUTPUT_ROOT,
    PROJECTS_ROOT,
    PYTHON_EXECUTABLE,
    RESOURCE_ROOT,
    ffmpeg_executable,
)
from media_probe import AUDIO_PEAK_LIMITER, media_duration

ROOT = RESOURCE_ROOT
WEBUI_ROOT = ROOT / "webui"
TTS_CONFIG_PATH = CONFIG_ROOT / "tts.json"
SOCIAL_CONFIG_PATH = CONFIG_ROOT / "social-upload.json"
PROJECT_DEFAULTS_PATH = CONFIG_ROOT / "project-defaults.json"
AUREX_ROOT = ROOT
AUREX_TTS_CONFIG_PATH = TTS_CONFIG_PATH
AUREX_SOCIAL_CONFIG_PATH = SOCIAL_CONFIG_PATH
AUREX_PYTHON = PYTHON_EXECUTABLE
TTS_PYTHON = AUREX_PYTHON if AUREX_PYTHON.is_file() else Path(sys.executable)
MAX_UPLOAD_BYTES = 80 * 1024 * 1024
DEFAULT_YOUTUBE_TITLE = "Sự khác nhau là gì?, Phần 1"
DEFAULT_YOUTUBE_DESCRIPTION = "🎬 Sự khác nhau là gì?\n#Hieuhamhoc #sosanh #kienthuc"
DEFAULT_FACEBOOK_CAPTION = "🎬 Sự khác nhau là gì?, Phần 1\n#Hieuhamhoc #sosanh #kienthuc"
JOBS: dict[str, dict] = {}
JOBS_LOCK = Lock()
PROJECT_DEFAULTS_LOCK = Lock()
JOB_PROCESSES: dict[str, subprocess.Popen] = {}
ACTIVE_STATUSES = {"queued", "running", "cancelling"}
POSES = ("neutral-left", "neutral-right", "question", "smile-left", "smile-right")
POSE_ALIASES = {
    "point-left": "smile-left",
    "point-right": "smile-right",
    "palms-open": "question",
}
DEFAULT_POSE_ASSETS = {
    pose: {
        "closed": f"../../assets/generated/human-presenter/{pose}.png",
        "speaking": f"../../assets/generated/human-presenter/{pose}.png",
    }
    for pose in POSES
}
DEFAULT_POSE_LABELS = {
    "neutral-left": "Chỉ trái · không cười",
    "neutral-right": "Chỉ phải · không cười",
    "question": "Thắc mắc · dấu hỏi",
    "smile-left": "Chỉ trái · cười",
    "smile-right": "Chỉ phải · cười",
}

DEFAULT_POSE_LABELS_EN = {
    "neutral-left": "Point left · no smile",
    "neutral-right": "Point right · no smile",
    "question": "Curious · question mark",
    "smile-left": "Point left · smile",
    "smile-right": "Point right · smile",
}

BIETCHICHOMET_DEFAULT_POSE_SEQUENCE = ("pose-1", "pose-2", "pose-3", "pose-1", "pose-2", "pose-4", "pose-1", "pose-2", "pose-5", "pose-1", "pose-2")


def normalize_ui_language(value: object) -> str:
    language = str(value or "vi").strip().lower()
    return "en" if language.startswith("en") else "vi"


def pose_label_for_language(pose_id: str, label: object = None, language: str = "vi") -> str:
    pose = str(pose_id or "").strip()
    raw = str(label or "").strip()
    if normalize_ui_language(language) != "en":
        return raw or DEFAULT_POSE_LABELS.get(pose, pose)
    if raw and raw in DEFAULT_POSE_LABELS_EN.values():
        return raw
    if raw in DEFAULT_POSE_LABELS.values():
        for key, vietnamese in DEFAULT_POSE_LABELS.items():
            if vietnamese == raw:
                return DEFAULT_POSE_LABELS_EN.get(key, raw)
    return DEFAULT_POSE_LABELS_EN.get(pose) or raw or pose


def normalize_pose_name(value: object) -> str:
    pose = str(value or "question")
    pose = POSE_ALIASES.get(pose, pose)
    return pose if pose in POSES else "question"


def normalize_custom_pose_id(value: object, fallback: str = "pose") -> str:
    text = unicodedata.normalize("NFD", str(value or "").strip().lower().replace("đ", "d"))
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    pose = re.sub(r"[^a-z0-9-]+", "-", text).strip("-")
    return pose[:48] or fallback


def load_default_sfx() -> dict[str, str]:
    library_path = ROOT / "assets" / "sfx" / "library.json"
    if not library_path.is_file():
        return {}
    try:
        items = json.loads(library_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, str] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        key = re.sub(r"[^a-z0-9_-]+", "-", str(item.get("key") or "").lower()).strip("-")
        path = str(item.get("path") or "").strip()
        if key and path:
            result[key] = path
    return result


DEFAULT_SFX = load_default_sfx()
SOCIAL_MODULES: dict[str, object] | None = None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_tts_config() -> dict:
    path = TTS_CONFIG_PATH
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def write_tts_config(value: dict) -> None:
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
    atomic_write_json(TTS_CONFIG_PATH, value)
    try:
        os.chmod(TTS_CONFIG_PATH, 0o600)
    except OSError:
        pass


def elevenlabs_config() -> dict:
    value = read_tts_config().get("elevenlabs", {})
    return value if isinstance(value, dict) else {}


def elevenlabs_public_config() -> dict:
    config = elevenlabs_config()
    return {
        "voiceId": str(config.get("voice_id") or "6adFm46eyy74snVn6YrT"),
        "modelId": str(config.get("model_id") or "eleven_v3"),
        "outputFormat": str(config.get("output_format") or "mp3_44100_128"),
        "apiKeyConfigured": bool(str(config.get("api_key") or os.environ.get("ELEVENLABS_API_KEY") or "").strip()),
        "usingSharedAurexVideoConfig": False,
    }


def update_elevenlabs_config(payload: dict) -> dict:
    config = read_tts_config()
    eleven = config.get("elevenlabs", {})
    if not isinstance(eleven, dict):
        eleven = {}
    api_key = str(payload.get("apiKey") or "").strip()
    voice_id = str(payload.get("voiceId") or eleven.get("voice_id") or "").strip()
    model_id = str(payload.get("modelId") or eleven.get("model_id") or "eleven_v3").strip()
    if api_key:
        if len(api_key) < 16 or not re.fullmatch(r"[A-Za-z0-9_.-]+", api_key):
            raise ValueError("ElevenLabs API key không hợp lệ.")
        eleven["api_key"] = api_key
    if not re.fullmatch(r"[A-Za-z0-9_-]+", voice_id):
        raise ValueError("ElevenLabs Voice ID không hợp lệ.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", model_id):
        raise ValueError("ElevenLabs model không hợp lệ.")
    eleven["voice_id"] = voice_id
    eleven["model_id"] = model_id
    eleven.setdefault("output_format", "mp3_44100_128")
    config["elevenlabs"] = eleven
    write_tts_config(config)
    return elevenlabs_public_config()


def migrate_shared_configs() -> None:
    """Kept for compatibility; configs are now owned by this project."""
    CONFIG_ROOT.mkdir(parents=True, exist_ok=True)


def latest_output_video(slug: str) -> Path:
    project_dir(slug)
    video = latest_file(OUTPUT_ROOT / slug, "*.mp4")
    if not video:
        raise FileNotFoundError(f"Dự án '{slug}' chưa có video render.")
    return video


def social_metadata(slug: str) -> dict:
    project_dir(slug)
    return {
        "project": slug,
        "title": DEFAULT_YOUTUBE_TITLE,
        "description": DEFAULT_YOUTUBE_DESCRIPTION,
        "youtubeDescription": DEFAULT_YOUTUBE_DESCRIPTION,
        "facebookCaption": DEFAULT_FACEBOOK_CAPTION,
        "facebookVideoState": "PUBLISHED",
        "facebookSourceComment": "",
        "source_url": "",
        "privacyStatus": "public",
        "tags": ["Hieuhamhoc", "sosanh", "kienthuc"],
        "video_url": file_url(latest_output_video(slug)),
    }


def social_modules() -> dict[str, object]:
    global SOCIAL_MODULES
    if SOCIAL_MODULES is not None:
        return SOCIAL_MODULES
    if str(AUREX_ROOT) not in sys.path:
        sys.path.insert(0, str(AUREX_ROOT))
    import social_upload.config as social_config_module
    import social_upload.facebook as facebook_module
    import social_upload.status as status_module
    import social_upload.youtube as youtube_module

    social_config_module.SOCIAL_UPLOAD_CONFIG = SOCIAL_CONFIG_PATH
    status_module.SOCIAL_UPLOAD_CONFIG = SOCIAL_CONFIG_PATH
    for module in (facebook_module, youtube_module):
        module.final_video_path_for_project = latest_output_video
        module.build_upload_metadata = social_metadata
        module.require_project = project_dir
    facebook_module.facebook_caption_for_project = lambda project, fallback: (str(fallback or social_metadata(project)["facebookCaption"])[:5000], "")
    SOCIAL_MODULES = {"status": status_module, "youtube": youtube_module, "facebook": facebook_module}
    return SOCIAL_MODULES


def social_status() -> dict:
    return social_modules()["status"].social_status()


def social_upload(platform: str, payload: dict) -> dict:
    modules = social_modules()
    if platform == "youtube":
        return modules["youtube"].youtube_upload_video(payload)
    if platform == "facebook":
        return modules["facebook"].facebook_upload_video(payload)
    raise ValueError("Nền tảng upload không hợp lệ.")


def set_social_active(platform: str, value: str) -> dict:
    modules = social_modules()
    if platform == "youtube":
        return modules["youtube"].set_youtube_active_channel(value)
    if platform == "facebook":
        return modules["facebook"].set_facebook_active_page(value)
    raise ValueError("Nền tảng upload không hợp lệ.")


def slugify_project_name(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).strip().lower()
    text = text.replace("đ", "d")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def validate_slug(value: str) -> str:
    slug = slugify_project_name(value)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}[a-z0-9]", slug):
        raise ValueError("Mã dự án chỉ gồm chữ thường, số và dấu gạch ngang (3-64 ký tự).")
    return slug


def project_dir(slug: str, *, must_exist: bool = True) -> Path:
    slug = validate_slug(slug)
    path = (PROJECTS_ROOT / slug).resolve()
    path.relative_to(PROJECTS_ROOT.resolve())
    if must_exist and (not path.is_dir() or not (path / "topic.json").is_file()):
        raise FileNotFoundError(f"Không tìm thấy dự án: {slug}")
    return path


def validate_character_id(value: object) -> str:
    slug = str(value or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}[a-z0-9]", slug):
        raise ValueError("Tên nhân vật chỉ gồm chữ thường không dấu, số và dấu gạch ngang (3-64 ký tự).")
    return slug


def character_manifest(character_id: str) -> dict:
    character_id = validate_character_id(character_id)
    path = (CHARACTERS_ROOT / character_id / "manifest.json").resolve()
    path.relative_to(CHARACTERS_ROOT.resolve())
    if not path.is_file():
        raise FileNotFoundError(f"Không tìm thấy nhân vật: {character_id}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("poses"), list):
        raise ValueError(f"Manifest nhân vật '{character_id}' không hợp lệ.")
    return value


def character_pose_config(character_id: str, language: str = "vi") -> tuple[dict, dict]:
    """Build topic-ready pose assets and labels from one saved character."""
    character_id = validate_character_id(character_id)
    manifest = character_manifest(character_id)
    poses = manifest.get("poses", [])
    pose_assets: dict[str, dict[str, str]] = {}
    pose_labels: dict[str, str] = {}
    directory = (CHARACTERS_ROOT / character_id).resolve()
    ui_language = normalize_ui_language(language)
    for index, item in enumerate(poses, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Pose {index} của nhân vật '{character_id}' không hợp lệ.")
        pose_id = normalize_custom_pose_id(item.get("id"), f"pose-{index}")
        filename = Path(str(item.get("file") or "")).name
        closed_filename = Path(str(item.get("closedFile") or filename)).name
        speaking_filename = Path(str(item.get("speakingFile") or filename)).name
        if not filename or not (directory / filename).is_file():
            raise FileNotFoundError(f"Không tìm thấy ảnh pose '{pose_id}' của nhân vật '{character_id}'.")
        if not (directory / closed_filename).is_file() or not (directory / speaking_filename).is_file():
            raise FileNotFoundError(f"Không tìm thấy media đóng/mở miệng của pose '{pose_id}'.")
        if pose_id in pose_assets:
            raise ValueError(f"Nhân vật '{character_id}' có mã pose bị trùng: {pose_id}.")
        sync_mode = str(item.get("syncMode") or "scene").strip().lower()
        if sync_mode not in {"scene", "timeline", "freeze"}:
            sync_mode = "scene"
        pose_assets[pose_id] = {
            "closed": f"../../assets/characters/{character_id}/{closed_filename}",
            "speaking": f"../../assets/characters/{character_id}/{speaking_filename}",
            "syncMode": sync_mode,
            "loop": item.get("loop") is not False,
            "loopStart": max(0.0, float(item.get("loopStart") or 0)),
            "loopEnd": max(0.0, float(item.get("loopEnd") or 0)),
        }
        source_label = item.get("labelEn") if ui_language == "en" else item.get("label")
        pose_labels[pose_id] = pose_label_for_language(pose_id, source_label, ui_language)[:80]
    if not pose_assets:
        raise ValueError(f"Nhân vật '{character_id}' chưa có pose.")
    return pose_assets, pose_labels


def default_pose_sequence(character_id: str, pose_assets: dict[str, dict[str, str]]) -> list[str]:
    ids = list(pose_assets)
    if character_id == "bietchichomet":
        sequence = [pose for pose in BIETCHICHOMET_DEFAULT_POSE_SEQUENCE if pose in pose_assets]
        if sequence:
            return sequence
    return ids


def enrich_character_poses(poses: list) -> list[dict]:
    enriched = []
    for index, item in enumerate(poses):
        if not isinstance(item, dict):
            continue
        pose_id = str(item.get("id") or f"pose-{index + 1}").strip() or f"pose-{index + 1}"
        label = str(item.get("label") or "").strip() or DEFAULT_POSE_LABELS.get(pose_id, pose_id)
        label_en = str(item.get("labelEn") or "").strip()
        if not label_en:
            label_en = pose_label_for_language(pose_id, label, "en")
        next_item = dict(item)
        next_item["id"] = pose_id
        next_item["label"] = label
        next_item["labelEn"] = label_en
        enriched.append(next_item)
    return enriched


def _seed_default_characters() -> None:
    """Copy characters bundled with the engine into the user data root
    on first launch so the app always has at least one character."""
    bundled = (RESOURCE_ROOT / "assets" / "characters")
    if not bundled.is_dir():
        return
    for src in sorted(bundled.glob("*/manifest.json")):
        char_id = src.parent.name
        if char_id.startswith("."):
            continue
        dst = CHARACTERS_ROOT / char_id
        if dst.exists():
            continue
        try:
            shutil.copytree(src.parent, dst)
        except (OSError, FileNotFoundError):
            continue


def list_characters() -> list[dict]:
    CHARACTERS_ROOT.mkdir(parents=True, exist_ok=True)
    # Seed default characters bundled with the engine on first launch
    # (studio data root starts empty; engine ships hieu-ham-hoc etc.).
    _seed_default_characters()
    result = []
    for path in sorted(CHARACTERS_ROOT.glob("*/manifest.json")):
        if path.parent.name.startswith("."):
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            poses = enrich_character_poses(item.get("poses", []))
            if not isinstance(item, dict) or not poses:
                continue
            result.append({
                "id": path.parent.name,
                "name": str(item.get("name") or path.parent.name),
                "poseCount": len(poses),
                "poses": poses,
                "coverUrl": f"/assets/characters/{path.parent.name}/{poses[0]['file']}",
            })
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
    return result


def decode_data_url(value: object) -> bytes:
    encoded = str(value or "")
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Dữ liệu ảnh upload không hợp lệ.") from exc
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("Ảnh rỗng hoặc vượt quá 80 MB.")
    return data


def remove_background_status() -> dict:
    """Report whether the offline remove-bg model is already downloaded."""
    from remove_background import remove_background_status as status

    return status()


def remove_background_image(payload: dict) -> dict:
    """Remove image background and return a PNG data URL."""
    from remove_background import remove_background_png

    data = decode_data_url(payload.get("data"))
    name = Path(str(payload.get("name") or "image.png")).name
    png = remove_background_png(data)
    encoded = base64.b64encode(png).decode("ascii")
    return {
        "ok": True,
        "name": Path(name).stem + "-nobg.png",
        "mime": "image/png",
        "data": f"data:image/png;base64,{encoded}",
    }


def split_character_sheet(payload: dict) -> dict:
    """Split a transparent, single-row pose sheet into baseline-aligned PNG sprites."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Thiếu Pillow trong venv; chưa thể tách pose PNG.") from exc
    import io

    data = decode_data_url(payload.get("data"))
    try:
        image = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as exc:
        raise ValueError("Không đọc được PNG pose-sheet.") from exc
    if image.width < 200 or image.height < 200:
        raise ValueError("Pose-sheet quá nhỏ; nên dùng ảnh rộng tối thiểu 1200 px.")
    alpha = image.getchannel("A")
    if alpha.getextrema()[0] == 255:
        raise ValueError("Ảnh chưa có nền trong suốt. Hãy dùng PNG đã tách nền.")

    occupied = []
    for x in range(image.width):
        occupied.append(alpha.crop((x, 0, x + 1, image.height)).getbbox() is not None)
    groups = []
    start = None
    for x, present in enumerate([*occupied, False]):
        if present and start is None:
            start = x
        elif not present and start is not None:
            if x - start >= max(8, image.width // 200):
                groups.append([start, x])
            start = None
    # Merge tiny detached symbols into their nearest figure (for example a question mark).
    while len(groups) > 12:
        gaps = [groups[i + 1][0] - groups[i][1] for i in range(len(groups) - 1)]
        index = gaps.index(min(gaps))
        groups[index:index + 2] = [[groups[index][0], groups[index + 1][1]]]
    if not 2 <= len(groups) <= 12:
        raise ValueError(f"Hệ thống nhận ra {len(groups)} dáng. Pose-sheet cần 2-12 nhân vật tách nhau trên một hàng.")

    bboxes = []
    for left, right in groups:
        bbox = alpha.crop((left, 0, right, image.height)).getbbox()
        if bbox:
            bboxes.append((left + bbox[0], bbox[1], left + bbox[2], bbox[3]))
    max_width = max(box[2] - box[0] for box in bboxes)
    max_height = max(box[3] - box[1] for box in bboxes)
    pad_x = max(20, round(max_width * 0.08))
    pad_top = max(20, round(max_height * 0.06))
    pad_bottom = max(8, round(max_height * 0.02))
    canvas_size = (max_width + pad_x * 2, max_height + pad_top + pad_bottom)

    token = uuid.uuid4().hex[:16]
    draft_dir = CHARACTERS_ROOT / ".drafts" / token
    draft_dir.mkdir(parents=True, exist_ok=False)
    poses = []
    for index, box in enumerate(bboxes, 1):
        crop = image.crop(box)
        canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        x = (canvas_size[0] - crop.width) // 2
        y = canvas_size[1] - pad_bottom - crop.height
        canvas.alpha_composite(crop, (x, y))
        filename = f"pose-{index:02d}.png"
        canvas.save(draft_dir / filename, optimize=True)
        poses.append({"index": index, "file": filename, "url": f"/assets/characters/.drafts/{token}/{filename}"})
    return {"token": token, "poses": poses, "width": canvas_size[0], "height": canvas_size[1]}


def save_character(payload: dict) -> dict:
    character_id = validate_character_id(payload.get("id"))
    name = character_id
    token = str(payload.get("token") or "").strip()
    if not re.fullmatch(r"[a-f0-9]{16}", token):
        raise ValueError("Phiên tách pose không hợp lệ.")
    draft_dir = (CHARACTERS_ROOT / ".drafts" / token).resolve()
    draft_dir.relative_to((CHARACTERS_ROOT / ".drafts").resolve())
    if not draft_dir.is_dir():
        raise FileNotFoundError("Bản tách pose đã hết hạn hoặc không tồn tại.")
    labels = payload.get("poseNames")
    files = sorted(draft_dir.glob("pose-*.png"))
    if not isinstance(labels, list) or len(labels) != len(files):
        raise ValueError("Cần đặt tên cho đủ tất cả dáng pose.")
    destination = CHARACTERS_ROOT / character_id
    if destination.exists():
        raise FileExistsError(f"Nhân vật '{character_id}' đã tồn tại.")
    destination.mkdir(parents=True)
    poses = []
    used = set()
    for index, (source, raw_label) in enumerate(zip(files, labels), 1):
        label = str(raw_label or "").strip()[:80]
        if not label:
            raise ValueError(f"Pose {index} chưa có tên.")
        pose_id = normalize_custom_pose_id(label, f"pose-{index}")
        base_id = pose_id
        suffix = 2
        while pose_id in used:
            pose_id = f"{base_id}-{suffix}"
            suffix += 1
        used.add(pose_id)
        filename = f"{pose_id}.png"
        shutil.copy2(source, destination / filename)
        poses.append({
            "id": pose_id,
            "label": label,
            "labelEn": pose_label_for_language(pose_id, label, "en"),
            "file": filename,
        })
    manifest = {"id": character_id, "name": name, "createdAt": now_iso(), "poses": poses}
    atomic_write_json(destination / "manifest.json", manifest)
    shutil.rmtree(draft_dir, ignore_errors=True)
    return {"character": {**manifest, "poseCount": len(poses), "coverUrl": f"/assets/characters/{character_id}/{poses[0]['file']}"}}


def update_character(current_id: str, payload: dict) -> dict:
    current_id = validate_character_id(current_id)
    next_id = validate_character_id(payload.get("id") or current_id)
    source_dir = (CHARACTERS_ROOT / current_id).resolve()
    source_dir.relative_to(CHARACTERS_ROOT.resolve())
    manifest_path = source_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy nhân vật: {current_id}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    poses = manifest.get("poses") if isinstance(manifest, dict) else None
    if not isinstance(poses, list) or not poses:
        raise ValueError(f"Manifest nhân vật '{current_id}' không hợp lệ.")
    labels = payload.get("poseNames")
    if not isinstance(labels, list) or len(labels) != len(poses):
        raise ValueError("Cần đặt tên cho đủ tất cả dáng pose.")

    updated_poses = []
    for index, (pose, raw_label) in enumerate(zip(poses, labels), 1):
        if not isinstance(pose, dict):
            raise ValueError(f"Pose {index} trong manifest không hợp lệ.")
        label = str(raw_label or "").strip()[:80]
        if not label:
            raise ValueError(f"Pose {index} chưa có tên.")
        pose_id = normalize_custom_pose_id(pose.get("id"), f"pose-{index}")
        filename = Path(str(pose.get("file") or "")).name
        if not filename or not (source_dir / filename).is_file():
            raise FileNotFoundError(f"Không tìm thấy ảnh của pose '{pose_id}'.")
        updated_poses.append({
            "id": pose_id,
            "label": label,
            "labelEn": pose_label_for_language(pose_id, label, "en"),
            "file": filename,
        })

    destination = (CHARACTERS_ROOT / next_id).resolve()
    destination.relative_to(CHARACTERS_ROOT.resolve())
    if next_id != current_id and destination.exists():
        raise FileExistsError(f"Nhân vật '{next_id}' đã tồn tại.")
    if next_id != current_id:
        source_dir.rename(destination)
    else:
        destination = source_dir

    updated_manifest = {
        **manifest,
        "id": next_id,
        "name": next_id,
        "updatedAt": now_iso(),
        "poses": updated_poses,
    }
    atomic_write_json(destination / "manifest.json", updated_manifest)

    pose_assets = {
        pose["id"]: {
            "closed": f"../../assets/characters/{next_id}/{pose.get('closedFile') or pose['file']}",
            "speaking": f"../../assets/characters/{next_id}/{pose.get('speakingFile') or pose['file']}",
            "syncMode": pose.get("syncMode") or "scene",
            "loop": pose.get("loop") is not False,
            "loopStart": max(0.0, float(pose.get("loopStart") or 0)),
            "loopEnd": max(0.0, float(pose.get("loopEnd") or 0)),
        }
        for pose in updated_poses
    }
    pose_labels = {pose["id"]: pose["label"] for pose in updated_poses}
    updated_projects: set[str] = set()
    if PROJECTS_ROOT.is_dir():
        for topic_path in PROJECTS_ROOT.glob("*/topic*.json"):
            try:
                topic = json.loads(topic_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(topic, dict) or str(topic.get("characterId") or "") != current_id:
                continue
            topic["characterId"] = next_id
            topic["poseAssets"] = pose_assets
            topic["poseLabels"] = pose_labels
            atomic_write_json(topic_path, topic)
            updated_projects.add(topic_path.parent.name)

    character = {
        **updated_manifest,
        "poseCount": len(updated_poses),
        "coverUrl": f"/assets/characters/{next_id}/{updated_poses[0]['file']}",
    }
    return {"character": character, "updatedProjects": sorted(updated_projects)}


def delete_character(character_id: str) -> dict:
    character_id = validate_character_id(character_id)
    directory = (CHARACTERS_ROOT / character_id).resolve()
    directory.relative_to(CHARACTERS_ROOT.resolve())
    if not (directory / "manifest.json").is_file():
        raise FileNotFoundError(f"Không tìm thấy nhân vật: {character_id}")

    used_by = []
    if PROJECTS_ROOT.is_dir():
        for topic_path in PROJECTS_ROOT.glob("*/topic.json"):
            try:
                topic = json.loads(topic_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(topic, dict) and str(topic.get("characterId") or "") == character_id:
                used_by.append(topic_path.parent.name)
    if used_by:
        projects = ", ".join(sorted(set(used_by)))
        raise RuntimeError(f"Nhân vật '{character_id}' đang được dùng trong project: {projects}. Hãy đổi nhân vật của project trước khi xoá.")

    shutil.rmtree(directory)
    return {"deleted": True, "characterId": character_id}


def read_topic(slug: str) -> dict:
    path = project_dir(slug) / "topic.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("topic.json phải là một JSON object.")
    current_sfx = value.get("sfx", {}) if isinstance(value.get("sfx"), dict) else {}
    value["sfx"] = {**DEFAULT_SFX, **current_sfx}

    character_id = str(value.get("characterId") or "").strip()
    synced_assets: dict[str, dict[str, str]] | None = None
    synced_labels: dict[str, str] | None = None
    if character_id and character_id != "human-presenter":
        try:
            synced_assets, synced_labels = character_pose_config(character_id)
        except (FileNotFoundError, ValueError):
            synced_assets = None
            synced_labels = None

    pose_assets = value.get("poseAssets") if isinstance(value.get("poseAssets"), dict) else {}
    if synced_assets and list(pose_assets) != list(synced_assets):
        pose_assets = synced_assets
        value["poseAssets"] = pose_assets
        value["poseLabels"] = synced_labels or {}
    elif not pose_assets:
        pose_assets = dict(DEFAULT_POSE_ASSETS)
        value["poseAssets"] = pose_assets
        labels = value.get("poseLabels") if isinstance(value.get("poseLabels"), dict) else {}
        value["poseLabels"] = {pose: str(labels.get(pose) or DEFAULT_POSE_LABELS.get(pose) or pose) for pose in pose_assets}
    else:
        labels = value.get("poseLabels") if isinstance(value.get("poseLabels"), dict) else {}
        value["poseLabels"] = {pose: str(labels.get(pose) or DEFAULT_POSE_LABELS.get(pose) or pose) for pose in pose_assets}

    valid_poses = list(pose_assets)
    fallback_pose = "question" if "question" in pose_assets else valid_poses[0]
    for event in value.get("poseTimeline", []):
        if isinstance(event, dict):
            pose = POSE_ALIASES.get(str(event.get("pose") or ""), str(event.get("pose") or ""))
            event["pose"] = pose if pose in pose_assets else fallback_pose
    return value


def atomic_write_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_project_defaults() -> dict:
    try:
        value = json.loads(PROJECT_DEFAULTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def project_defaults_assets_root() -> Path:
    return CONFIG_ROOT / "project-defaults-assets"


def default_character_id() -> str:
    return str(read_project_defaults().get("characterId") or "").strip()


def _safe_defaults_asset_name(value: object, fallback: str = "asset") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-")
    return text[:80] or fallback


def _same_defaults_asset(source: Path, target: Path) -> bool:
    """Skip rewriting unchanged default assets during frequent editor autosaves."""
    if not target.is_file():
        return False
    try:
        source_stat = source.stat()
        target_stat = target.stat()
    except OSError:
        return False
    return (
        source_stat.st_size == target_stat.st_size
        and int(getattr(source_stat, "st_mtime_ns", int(source_stat.st_mtime * 1_000_000_000)))
        == int(getattr(target_stat, "st_mtime_ns", int(target_stat.st_mtime * 1_000_000_000)))
    )


def _copy_into_project_defaults(source: Path, relative_dir: str, preferred_name: str) -> str:
    root = project_defaults_assets_root()
    target_dir = root / relative_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower() or ".bin"
    target = target_dir / f"{_safe_defaults_asset_name(preferred_name)}{suffix}"
    if not _same_defaults_asset(source, target):
        shutil.copy2(source, target)
    return target.relative_to(root).as_posix()


def _resolve_under_root(root: Path, relative: object) -> Path | None:
    raw = str(relative or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def remembered_pose_sfx(character_id: str, poses: object, custom_sfx: object = None) -> dict[str, str]:
    defaults = read_project_defaults()
    mappings = defaults.get("poseSfxByCharacter")
    if not isinstance(mappings, dict):
        return {}
    remembered = mappings.get(character_id)
    if not isinstance(remembered, dict):
        return {}
    custom = custom_sfx if isinstance(custom_sfx, dict) else defaults.get("customSfx")
    if not isinstance(custom, dict):
        custom = {}
    result: dict[str, str] = {}
    for pose in poses if isinstance(poses, (list, tuple, dict)) else ():
        if pose not in remembered:
            continue
        key = str(remembered.get(pose) or "").strip()
        if not key or key in DEFAULT_SFX or key in custom:
            result[str(pose)] = key
    return result


def remember_project_defaults(slug: str, topic: dict) -> None:
    character_id = str(topic.get("characterId") or "").strip()
    project = project_dir(slug)
    pose_sfx = topic.get("poseSfx") if isinstance(topic.get("poseSfx"), dict) else {}
    topic_sfx = topic.get("sfx") if isinstance(topic.get("sfx"), dict) else {}

    with PROJECT_DEFAULTS_LOCK:
        previous = read_project_defaults()
        defaults = json.loads(json.dumps(previous))
        if character_id:
            defaults["characterId"] = character_id

        defaults["karaokeColor"] = normalize_hex_color(topic.get("karaokeColor"), "#271f11")
        defaults["karaokeActiveColor"] = normalize_hex_color(topic.get("karaokeActiveColor"), "#de370d")
        try:
            defaults["karaokeSize"] = round(max(0.6, min(1.5, float(topic.get("karaokeSize", 1.2)))), 2)
        except (TypeError, ValueError):
            defaults["karaokeSize"] = 1.2

        background_type = str(topic.get("backgroundType") or "default").strip().lower()
        if background_type not in {"default", "color", "image"}:
            background_type = "default"
        defaults["backgroundType"] = background_type
        defaults["backgroundColor"] = normalize_hex_color(topic.get("backgroundColor"), "#f5eee3")
        try:
            defaults["backgroundImageZoom"] = round(max(1.0, min(3.0, float(topic.get("backgroundImageZoom", 1.0)))), 2)
            defaults["backgroundImageX"] = round(max(-50.0, min(50.0, float(topic.get("backgroundImageX", 0.0)))), 1)
            defaults["backgroundImageY"] = round(max(-50.0, min(50.0, float(topic.get("backgroundImageY", 0.0)))), 1)
        except (TypeError, ValueError):
            defaults["backgroundImageZoom"] = 1.0
            defaults["backgroundImageX"] = 0.0
            defaults["backgroundImageY"] = 0.0

        background_image = ""
        if background_type == "image":
            source = _resolve_under_root(project, topic.get("backgroundImage"))
            if source is not None:
                background_image = _copy_into_project_defaults(source, "backgrounds", "background")
        defaults["backgroundImage"] = background_image

        background_music = ""
        if str(topic.get("backgroundMusic") or "").strip():
            source = _resolve_under_root(project, topic.get("backgroundMusic"))
            if source is not None:
                background_music = _copy_into_project_defaults(source, "music", "background-music")
        else:
            # Clearing music in a project must also clear the shared default so
            # newly created projects stay without background music.
            stale = _resolve_under_root(project_defaults_assets_root(), defaults.get("backgroundMusic"))
            if stale is not None:
                stale.unlink(missing_ok=True)
        defaults["backgroundMusic"] = background_music
        defaults["backgroundMusicEnabled"] = bool(background_music)
        try:
            defaults["backgroundMusicVolume"] = round(
                max(0.05, min(0.5, float(topic.get("backgroundMusicVolume", 0.18)))),
                2,
            )
        except (TypeError, ValueError):
            defaults["backgroundMusicVolume"] = 0.18
        if not background_music:
            defaults["backgroundMusicVolume"] = 0.18

        custom_catalog = defaults.get("customSfx")
        if not isinstance(custom_catalog, dict):
            custom_catalog = {}
        portable: dict[str, str] = {}
        for pose, key in pose_sfx.items():
            sfx_key = str(key or "").strip()
            if not sfx_key:
                portable[str(pose)] = ""
                continue
            if sfx_key in DEFAULT_SFX:
                portable[str(pose)] = sfx_key
                continue
            source = _resolve_under_root(project, topic_sfx.get(sfx_key))
            if source is None:
                continue
            custom_catalog[sfx_key] = _copy_into_project_defaults(source, "sfx", sfx_key)
            portable[str(pose)] = sfx_key

        if character_id:
            mappings = defaults.get("poseSfxByCharacter")
            if not isinstance(mappings, dict):
                mappings = {}
            mappings[character_id] = portable
            defaults["poseSfxByCharacter"] = mappings
        defaults["customSfx"] = custom_catalog

        if defaults != previous:
            CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
            atomic_write_json(PROJECT_DEFAULTS_PATH, defaults)


def remember_pose_sfx(topic: dict) -> None:
    """Backward-compatible wrapper; prefer remember_project_defaults(slug, topic)."""
    slug = str(topic.get("id") or "").strip()
    if not slug:
        return
    try:
        remember_project_defaults(slug, topic)
    except FileNotFoundError:
        return


def safe_relative_asset(value: object, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"Thiếu {field}.")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Đường dẫn {field} không hợp lệ.")
    return path.as_posix()


def normalize_hex_color(value: object, fallback: str) -> str:
    text = str(value or fallback).strip()
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", text):
        return text.lower()
    if re.fullmatch(r"#[0-9A-Fa-f]{3}", text):
        return "#" + "".join(ch * 2 for ch in text[1:].lower())
    return fallback


def normalize_topic(slug: str, payload: dict) -> dict:
    current = read_topic(slug)
    topic = dict(current)
    topic["id"] = slug
    for field in ("brand", "leftLabel", "rightLabel"):
        text = str(payload.get(field, current.get(field, ""))).strip()
        if not text:
            raise ValueError(f"Trường {field} không được để trống.")
        topic[field] = text[:100]
    for field in ("leftImage", "rightImage", "voiceover"):
        topic[field] = safe_relative_asset(payload.get(field, current.get(field)), field)

    raw_sfx = payload.get("sfx", current.get("sfx", {}))
    if not isinstance(raw_sfx, dict):
        raise ValueError("Danh sách âm thanh không hợp lệ.")
    cleaned_sfx = dict(DEFAULT_SFX)
    for key, value in list(raw_sfx.items())[:120]:
        safe_key = re.sub(r"[^a-z0-9_-]+", "-", str(key).lower()).strip("-")
        if not safe_key:
            continue
        if safe_key in DEFAULT_SFX:
            cleaned_sfx[safe_key] = DEFAULT_SFX[safe_key]
        else:
            cleaned_sfx[safe_key] = safe_relative_asset(value, f"sfx.{safe_key}")
    topic["sfx"] = cleaned_sfx

    try:
        duration = float(payload.get("duration", current.get("duration", 0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("Duration phải là số.") from exc
    if not 0.1 <= duration <= 3600:
        raise ValueError("Duration phải nằm trong khoảng 0.1-3600 giây.")
    topic["duration"] = round(duration, 3)

    segments = payload.get("segments", current.get("segments", []))
    if not isinstance(segments, list) or not segments:
        raise ValueError("Cần ít nhất một đoạn subtitle.")
    cleaned_segments = []
    previous_start = -1.0
    for item in segments[:200]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = max(0.0, float(item.get("start", 0)))
        end = min(duration, float(item.get("end", duration)))
        if end <= start:
            end = min(duration, start + 0.25)
        if start < previous_start:
            raise ValueError("Các đoạn subtitle phải theo thứ tự thời gian.")
        cleaned_segments.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        previous_start = start
    if not cleaned_segments:
        raise ValueError("Subtitle không có nội dung hợp lệ.")
    topic["segments"] = cleaned_segments

    topic["labelColor"] = normalize_hex_color(
        payload.get("labelColor", current.get("labelColor", "#090909")),
        "#090909",
    )
    topic["leftLabelColor"] = normalize_hex_color(
        payload.get("leftLabelColor", current.get("leftLabelColor", topic["labelColor"])),
        topic["labelColor"],
    )
    topic["rightLabelColor"] = normalize_hex_color(
        payload.get("rightLabelColor", current.get("rightLabelColor", topic["labelColor"])),
        topic["labelColor"],
    )
    left_sub = str(payload.get("leftSubLabel", current.get("leftSubLabel", ""))).strip()[:40]
    right_sub = str(payload.get("rightSubLabel", current.get("rightSubLabel", ""))).strip()[:40]
    show_sub = bool(payload.get("showSubLabels", current.get("showSubLabels", False))) or bool(left_sub or right_sub)
    topic["showSubLabels"] = show_sub
    topic["leftSubLabel"] = left_sub if show_sub else ""
    topic["rightSubLabel"] = right_sub if show_sub else ""
    topic["leftSubLabelColor"] = normalize_hex_color(
        payload.get("leftSubLabelColor", current.get("leftSubLabelColor", "#808080")),
        "#808080",
    )
    topic["rightSubLabelColor"] = normalize_hex_color(
        payload.get("rightSubLabelColor", current.get("rightSubLabelColor", "#808080")),
        "#808080",
    )
    raw_comparisons = payload.get("comparisons", current.get("comparisons", []))
    if not isinstance(raw_comparisons, list):
        raise ValueError("Danh sách so sánh không hợp lệ.")
    cleaned_comparisons = []
    for index, item in enumerate(raw_comparisons[:20], 2):
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("id") or f"comparison-{index}")
        layout = "single" if str(item.get("layout") or "").lower() == "single" or raw_id.startswith("single-image-") else "pair"
        left_label = str(item.get("leftLabel") or "").strip()
        right_label = str(item.get("rightLabel") or "").strip()
        if not left_label or (layout == "pair" and not right_label):
            raise ValueError(f"Cặp so sánh {index} cần đủ hai nhãn.")
        try:
            start_sentence = int(item.get("startSentence", 2))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Câu bắt đầu của cặp so sánh {index} phải là số.") from exc
        cmp_left_sub = str(item.get("leftSubLabel") or "").strip()[:40]
        cmp_right_sub = str(item.get("rightSubLabel") or "").strip()[:40]
        cmp_show_sub = bool(item.get("showSubLabels", False)) or bool(cmp_left_sub or cmp_right_sub)
        comparison = {
            "id": re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_id).strip("-")[:80] or f"comparison-{index}",
            "layout": layout,
            "startSentence": max(1, min(len(cleaned_segments), start_sentence)),
            "leftLabel": left_label[:100],
            "rightLabel": right_label[:100] if layout == "pair" else "",
            "showSubLabels": cmp_show_sub,
            "leftSubLabel": cmp_left_sub if cmp_show_sub else "",
            "rightSubLabel": cmp_right_sub if cmp_show_sub and layout == "pair" else "",
            "leftImage": safe_relative_asset(item.get("leftImage"), f"comparisons[{index}].leftImage"),
            "rightImage": safe_relative_asset(item.get("rightImage"), f"comparisons[{index}].rightImage"),
            "leftLabelColor": normalize_hex_color(
                item.get("leftLabelColor", item.get("labelColor")),
                topic["leftLabelColor"],
            ),
            "rightLabelColor": normalize_hex_color(
                item.get("rightLabelColor", item.get("labelColor")),
                topic["rightLabelColor"],
            ),
            "leftSubLabelColor": normalize_hex_color(
                item.get("leftSubLabelColor"),
                topic["leftSubLabelColor"],
            ),
            "rightSubLabelColor": normalize_hex_color(
                item.get("rightSubLabelColor"),
                topic["rightSubLabelColor"],
            ),
        }
        for side in ("left", "right"):
            try:
                zoom = float(item.get(f"{side}ImageZoom", 1.0))
                offset_x = float(item.get(f"{side}ImageX", 0.0))
                offset_y = float(item.get(f"{side}ImageY", 0.0))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Thông số ảnh của cặp so sánh {index} phải là số.") from exc
            comparison[f"{side}ImageZoom"] = round(max(1.0, min(3.0, zoom)), 2)
            comparison[f"{side}ImageX"] = round(max(-50.0, min(50.0, offset_x)), 1)
            comparison[f"{side}ImageY"] = round(max(-50.0, min(50.0, offset_y)), 1)
        cleaned_comparisons.append(comparison)
    cleaned_comparisons.sort(key=lambda item: item["startSentence"])
    topic["comparisons"] = cleaned_comparisons
    topic["baseComparisonEnabled"] = bool(payload.get("baseComparisonEnabled", current.get("baseComparisonEnabled", True)))

    current_character_id = str(current.get("characterId") or "default-human")
    requested_character_id = str(payload.get("characterId", current_character_id) or "").strip()
    if requested_character_id != current_character_id:
        # A character switch must come from the shared character library. This
        # prevents a client from saving arbitrary pose paths into topic.json.
        pose_assets, pose_labels = character_pose_config(requested_character_id)
    else:
        try:
            pose_assets, pose_labels = character_pose_config(requested_character_id)
        except (FileNotFoundError, ValueError):
            # Keep legacy projects usable when their old built-in character has
            # no manifest in assets/characters.
            pose_assets = current.get("poseAssets") if isinstance(current.get("poseAssets"), dict) else dict(DEFAULT_POSE_ASSETS)
            pose_labels = current.get("poseLabels") if isinstance(current.get("poseLabels"), dict) else dict(DEFAULT_POSE_LABELS)
    valid_poses = list(pose_assets)
    fallback_pose = "question" if "question" in pose_assets else valid_poses[0]
    topic["poseAssets"] = pose_assets
    topic["poseLabels"] = pose_labels
    topic["characterId"] = requested_character_id

    timeline = payload.get("poseTimeline", current.get("poseTimeline", []))
    if not isinstance(timeline, list) or not timeline:
        timeline = [{"time": 0, "pose": fallback_pose}]
    cleaned_timeline = []
    for item in timeline[:100]:
        if not isinstance(item, dict):
            continue
        raw_pose = POSE_ALIASES.get(str(item.get("pose") or ""), str(item.get("pose") or ""))
        pose = raw_pose if raw_pose in pose_assets else fallback_pose
        event = {"time": round(max(0.0, min(duration, float(item.get("time", 0)))), 3), "pose": pose}
        sfx = str(item.get("sfx") or "").strip()
        if sfx in topic.get("sfx", {}):
            event["sfx"] = sfx
        cleaned_timeline.append(event)
    cleaned_timeline.sort(key=lambda item: item["time"])
    if not cleaned_timeline or cleaned_timeline[0]["time"] > 0:
        cleaned_timeline.insert(0, {"time": 0.0, "pose": fallback_pose})
    topic["poseTimeline"] = cleaned_timeline
    topic["sfxVolume"] = round(max(0.0, min(1.0, float(payload.get("sfxVolume", current.get("sfxVolume", 0.5))))), 2)

    default_pose_sfx = {
        "neutral-left": "pose-hard-pop-click",
        "neutral-right": "pose-hard-pop-click",
        "question": "pose-bubble-pop",
        "smile-left": "pose-explainer-pop-whoosh",
        "smile-right": "pose-explainer-pop-whoosh",
    }
    positional_pose_sfx = [
        "pose-hard-pop-click",
        "pose-hard-pop-click",
        "pose-bubble-pop",
        "pose-explainer-pop-whoosh",
        "pose-explainer-pop-whoosh",
    ]
    for index, pose in enumerate(valid_poses):
        default_pose_sfx.setdefault(pose, positional_pose_sfx[index] if index < len(positional_pose_sfx) else "")
    raw_pose_sfx = payload.get("poseSfx", current.get("poseSfx", default_pose_sfx))
    if not isinstance(raw_pose_sfx, dict):
        raw_pose_sfx = default_pose_sfx
    cleaned_pose_sfx = {}
    for pose in valid_poses:
        raw_key = raw_pose_sfx.get(pose, default_pose_sfx.get(pose, ""))
        key = re.sub(r"[^a-z0-9_-]+", "-", str(raw_key or "").lower()).strip("-")
        if raw_key == "" or raw_key is None:
            cleaned_pose_sfx[pose] = ""
        elif key and key in topic.get("sfx", {}):
            cleaned_pose_sfx[pose] = key
        elif default_pose_sfx.get(pose) in topic.get("sfx", {}):
            cleaned_pose_sfx[pose] = default_pose_sfx[pose]
        else:
            cleaned_pose_sfx[pose] = ""
    topic["poseSfx"] = cleaned_pose_sfx
    # Re-apply fixed pose→sfx mapping onto every pose change, including the
    # first pose so sentence 1 keeps its sound in preview and final render.
    previous_pose = None
    for event in topic["poseTimeline"]:
        pose = event.get("pose")
        if pose != previous_pose:
            mapped = cleaned_pose_sfx.get(pose) or ""
            if mapped:
                event["sfx"] = mapped
            else:
                event.pop("sfx", None)
        previous_pose = pose

    for side in ("left", "right"):
        zoom_key = f"{side}ImageZoom"
        x_key = f"{side}ImageX"
        y_key = f"{side}ImageY"
        try:
            zoom = float(payload.get(zoom_key, current.get(zoom_key, 1.0)))
            offset_x = float(payload.get(x_key, current.get(x_key, 0.0)))
            offset_y = float(payload.get(y_key, current.get(y_key, 0.0)))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{zoom_key}/{x_key}/{y_key} phải là số.") from exc
        zoom = max(1.0, min(3.0, zoom))
        topic[zoom_key] = round(zoom, 2)
        topic[x_key] = round(max(-50.0, min(50.0, offset_x)), 1)
        topic[y_key] = round(max(-50.0, min(50.0, offset_y)), 1)

    topic["karaokeColor"] = normalize_hex_color(payload.get("karaokeColor", current.get("karaokeColor", "#271f11")), "#271f11")
    topic["karaokeActiveColor"] = normalize_hex_color(
        payload.get("karaokeActiveColor", current.get("karaokeActiveColor", "#de370d")),
        "#de370d",
    )
    try:
        karaoke_size = float(payload.get("karaokeSize", current.get("karaokeSize", 1.2)))
    except (TypeError, ValueError) as exc:
        raise ValueError("karaokeSize phải là số.") from exc
    topic["karaokeSize"] = round(max(0.6, min(1.5, karaoke_size)), 2)

    background_type = str(payload.get("backgroundType", current.get("backgroundType", "default")) or "default").strip().lower()
    if background_type not in {"default", "color", "image"}:
        background_type = "default"
    topic["backgroundType"] = background_type
    topic["backgroundColor"] = normalize_hex_color(
        payload.get("backgroundColor", current.get("backgroundColor", "#f5eee3")),
        "#f5eee3",
    )
    background_image = str(payload.get("backgroundImage", current.get("backgroundImage", "")) or "").strip()
    topic["backgroundImage"] = safe_relative_asset(background_image, "backgroundImage") if background_image else ""
    try:
        bg_zoom = float(payload.get("backgroundImageZoom", current.get("backgroundImageZoom", 1.0)))
        bg_x = float(payload.get("backgroundImageX", current.get("backgroundImageX", 0.0)))
        bg_y = float(payload.get("backgroundImageY", current.get("backgroundImageY", 0.0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("backgroundImageZoom/backgroundImageX/backgroundImageY phải là số.") from exc
    topic["backgroundImageZoom"] = round(max(1.0, min(3.0, bg_zoom)), 2)
    topic["backgroundImageX"] = round(max(-50.0, min(50.0, bg_x)), 1)
    topic["backgroundImageY"] = round(max(-50.0, min(50.0, bg_y)), 1)

    background_music = str(payload.get("backgroundMusic", current.get("backgroundMusic", "")) or "").strip()
    topic["backgroundMusic"] = safe_relative_asset(background_music, "backgroundMusic") if background_music else ""
    topic["backgroundMusicEnabled"] = bool(topic["backgroundMusic"])
    try:
        background_music_volume = float(
            payload.get("backgroundMusicVolume", current.get("backgroundMusicVolume", 0.18))
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("backgroundMusicVolume phải là số.") from exc
    topic["backgroundMusicVolume"] = round(max(0.05, min(0.5, background_music_volume)), 2)
    topic["pasteImageMode"] = str(payload.get("pasteImageMode", current.get("pasteImageMode", "square")) or "square")
    if topic["pasteImageMode"] not in {"square", "original"}:
        topic["pasteImageMode"] = "square"
    return topic


def save_topic(slug: str, payload: dict) -> dict:
    topic = normalize_topic(slug, payload)
    directory = project_dir(slug)
    atomic_write_json(directory / "topic.json", topic)
    script = "\n".join(segment["text"] for segment in topic["segments"]) + "\n"
    (directory / "script.txt").write_text(script, encoding="utf-8")
    remember_project_defaults(slug, topic)
    return topic


def file_url(path: Path) -> str:
    resolved = path.resolve()
    roots = (
        (PROJECTS_ROOT.resolve(), "/project"),
        (CHARACTERS_ROOT.resolve(), "/assets/characters"),
        (OUTPUT_ROOT.resolve(), "/output"),
        (ROOT.resolve(), ""),
    )
    for root, prefix in roots:
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError:
            continue
        encoded = quote(relative, safe="/._-")
        return f"{prefix}/{encoded}" if prefix else f"/{encoded}"
    raise ValueError(f"Không thể tạo URL cho file ngoài vùng dữ liệu AurexVideo: {resolved}")


def latest_file(directory: Path, pattern: str) -> Path | None:
    files = [path for path in directory.glob(pattern) if path.is_file()] if directory.is_dir() else []
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def project_summary(path: Path) -> dict:
    topic_path = path / "topic.json"
    topic = json.loads(topic_path.read_text(encoding="utf-8"))
    output_dir = OUTPUT_ROOT / path.name
    preview = latest_file(output_dir, "*.png")
    video = latest_file(output_dir, "*.mp4")
    return {
        "id": path.name,
        "brand": topic.get("brand", "Aurex"),
        "leftLabel": topic.get("leftLabel", "Bên trái"),
        "rightLabel": topic.get("rightLabel", "Bên phải"),
        "duration": topic.get("duration", 0),
        "segmentCount": len(topic.get("segments", [])),
        "updatedAt": datetime.fromtimestamp(topic_path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
        "previewUrl": file_url(preview) if preview else None,
        "videoUrl": file_url(video) if video else None,
    }


def list_projects() -> list[dict]:
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    paths = [path for path in PROJECTS_ROOT.iterdir() if path.is_dir() and (path / "topic.json").is_file()]
    paths.sort(key=lambda path: (path / "topic.json").stat().st_mtime, reverse=True)
    return [project_summary(path) for path in paths]


def dependency_status() -> dict:
    try:
        import playwright  # noqa: F401
        playwright_ok = True
    except Exception:
        playwright_ok = False
    try:
        ffmpeg_ok = ffmpeg_executable().is_file()
    except Exception:
        ffmpeg_ok = False
    return {
        "ffmpeg": ffmpeg_ok,
        "ffprobe": True,
        "playwright": playwright_ok,
    }


def create_project(payload: dict) -> dict:
    slug = validate_slug(payload.get("id", ""))
    destination = project_dir(slug, must_exist=False)
    if destination.exists():
        raise FileExistsError(f"Dự án '{slug}' đã tồn tại.")
    language = normalize_ui_language(payload.get("language") or payload.get("locale"))
    is_en = language == "en"
    defaults = read_project_defaults()
    character_id = str(payload.get("characterId") or defaults.get("characterId") or "human-presenter").strip()
    try:
        character = character_manifest(character_id)
        character_poses = character["poses"]
        pose_assets = {
            item["id"]: {
                "closed": f"../../assets/characters/{character_id}/{item.get('closedFile') or item['file']}",
                "speaking": f"../../assets/characters/{character_id}/{item.get('speakingFile') or item['file']}",
                "syncMode": item.get("syncMode") or "scene",
                "loop": item.get("loop") is not False,
                "loopStart": max(0.0, float(item.get("loopStart") or 0)),
                "loopEnd": max(0.0, float(item.get("loopEnd") or 0)),
            }
            for item in character_poses
        }
        pose_labels = {
            item["id"]: pose_label_for_language(
                item["id"],
                item.get("labelEn") if is_en else item.get("label"),
                language,
            )
            for item in character_poses
        }
    except FileNotFoundError:
        character_id = "human-presenter"
        pose_assets = dict(DEFAULT_POSE_ASSETS)
        pose_labels = {
            pose_id: pose_label_for_language(pose_id, label, language)
            for pose_id, label in (DEFAULT_POSE_LABELS_EN if is_en else DEFAULT_POSE_LABELS).items()
        }
    pose_sequence = default_pose_sequence(character_id, pose_assets)
    first_pose = pose_sequence[0] if pose_sequence else ("question" if "question" in pose_assets else next(iter(pose_assets)))
    destination.mkdir(parents=True)
    assets_dir = destination / "assets"
    audio_dir = destination / "audio"
    assets_dir.mkdir()
    audio_dir.mkdir()

    def write_placeholder(path: Path, label: str, accent: str) -> None:
        path.write_text(
            f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="800" viewBox="0 0 800 800">
<rect width="800" height="800" fill="#fffaf0"/>
<rect x="28" y="28" width="744" height="744" rx="42" fill="none" stroke="{accent}" stroke-width="8" stroke-dasharray="18 16"/>
<circle cx="400" cy="330" r="92" fill="{accent}" opacity=".16"/>
<path d="M350 330h100M400 280v100" stroke="{accent}" stroke-width="20" stroke-linecap="round"/>
<text x="400" y="500" text-anchor="middle" fill="#4b3a29" font-family="Arial,sans-serif" font-size="42" font-weight="700">{label}</text>
</svg>''',
            encoding="utf-8",
        )

    write_placeholder(
        assets_dir / "placeholder-left.svg",
        "Left image" if is_en else "Ảnh bên trái",
        "#de370d",
    )
    write_placeholder(
        assets_dir / "placeholder-right.svg",
        "Right image" if is_en else "Ảnh bên phải",
        "#16756f",
    )
    silence_path = audio_dir / "silence.wav"
    with wave.open(str(silence_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(b"\x00\x00" * 48000)

    starter_text = "Enter your first line here." if is_en else "Nhập nội dung đầu tiên tại đây."
    default_left = "Content A" if is_en else "Nội dung A"
    default_right = "Content B" if is_en else "Nội dung B"
    custom_catalog = defaults.get("customSfx") if isinstance(defaults.get("customSfx"), dict) else {}
    default_pose_sfx = {
        pose: {
            "neutral-left": "pose-hard-pop-click",
            "neutral-right": "pose-hard-pop-click",
            "question": "pose-bubble-pop",
            "smile-left": "pose-explainer-pop-whoosh",
            "smile-right": "pose-explainer-pop-whoosh",
        }.get(pose, "")
        for pose in pose_assets
    }
    default_pose_sfx.update(remembered_pose_sfx(character_id, pose_assets, custom_catalog))

    sfx_map = dict(DEFAULT_SFX)
    defaults_assets = project_defaults_assets_root()
    used_custom = {
        key for key in default_pose_sfx.values()
        if key and key not in DEFAULT_SFX and key in custom_catalog
    }
    if used_custom:
        sfx_dir = destination / "sfx"
        sfx_dir.mkdir(parents=True, exist_ok=True)
        for key in used_custom:
            source = _resolve_under_root(defaults_assets, custom_catalog.get(key))
            if source is None:
                for pose, value in list(default_pose_sfx.items()):
                    if value == key:
                        default_pose_sfx[pose] = ""
                continue
            target = sfx_dir / source.name
            shutil.copy2(source, target)
            sfx_map[key] = target.relative_to(destination).as_posix()

    background_type = str(defaults.get("backgroundType") or "default").strip().lower()
    if background_type not in {"default", "color", "image"}:
        background_type = "default"
    background_color = normalize_hex_color(defaults.get("backgroundColor"), "#f5eee3")
    try:
        background_zoom = round(max(1.0, min(3.0, float(defaults.get("backgroundImageZoom", 1.0)))), 2)
        background_x = round(max(-50.0, min(50.0, float(defaults.get("backgroundImageX", 0.0)))), 1)
        background_y = round(max(-50.0, min(50.0, float(defaults.get("backgroundImageY", 0.0)))), 1)
    except (TypeError, ValueError):
        background_zoom, background_x, background_y = 1.0, 0.0, 0.0
    background_image = ""
    if background_type == "image":
        source = _resolve_under_root(defaults_assets, defaults.get("backgroundImage"))
        if source is not None:
            target = assets_dir / f"background-default{source.suffix.lower() or '.png'}"
            shutil.copy2(source, target)
            background_image = target.relative_to(destination).as_posix()
        else:
            background_type = "color" if background_color else "default"

    try:
        karaoke_size = round(max(0.6, min(1.5, float(defaults.get("karaokeSize", 1.2)))), 2)
    except (TypeError, ValueError):
        karaoke_size = 1.2

    background_music = ""
    background_music_enabled = False
    try:
        background_music_volume = round(max(0.05, min(0.5, float(defaults.get("backgroundMusicVolume", 0.18)))), 2)
    except (TypeError, ValueError):
        background_music_volume = 0.18
    default_music_path = str(defaults.get("backgroundMusic") or "").strip()
    if default_music_path and defaults.get("backgroundMusicEnabled") is not False:
        source = _resolve_under_root(defaults_assets, default_music_path)
        if source is not None:
            target = audio_dir / f"background-music{source.suffix.lower() or '.mp3'}"
            shutil.copy2(source, target)
            background_music = target.relative_to(destination).as_posix()
            background_music_enabled = True
        else:
            background_music_volume = 0.18

    topic = {
        "id": slug,
        "brand": "Aurex",
        "duration": 1.0,
        "leftLabel": str(payload.get("leftLabel") or default_left).strip(),
        "rightLabel": str(payload.get("rightLabel") or default_right).strip(),
        "leftImage": "assets/placeholder-left.svg",
        "rightImage": "assets/placeholder-right.svg",
        "voiceover": "audio/silence.wav",
        "segments": [{"start": 0.0, "end": 1.0, "text": starter_text}],
        "characterId": character_id,
        "poseTimeline": [{"time": 0.0, "pose": first_pose}],
        "poseAssets": pose_assets,
        "poseLabels": pose_labels,
        "sfx": sfx_map,
        "poseSfx": default_pose_sfx,
        "sfxVolume": 0.5,
        "sfxCooldownSeconds": 0.6,
        "leftImageZoom": 1.0,
        "leftImageX": 0.0,
        "leftImageY": 0.0,
        "rightImageZoom": 1.0,
        "rightImageX": 0.0,
        "rightImageY": 0.0,
        "backgroundType": background_type,
        "backgroundColor": background_color,
        "backgroundImage": background_image,
        "backgroundImageZoom": background_zoom,
        "backgroundImageX": background_x,
        "backgroundImageY": background_y,
        "backgroundMusic": background_music,
        "backgroundMusicEnabled": background_music_enabled,
        "backgroundMusicVolume": background_music_volume,
        "pasteImageMode": str(payload.get("pasteImageMode") or "square") if str(payload.get("pasteImageMode") or "square") in {"square", "original"} else "square",
        "labelColor": "#090909",
        "leftLabelColor": "#090909",
        "rightLabelColor": "#090909",
        "comparisons": [],
        "karaokeColor": normalize_hex_color(defaults.get("karaokeColor"), "#271f11"),
        "karaokeActiveColor": normalize_hex_color(defaults.get("karaokeActiveColor"), "#de370d"),
        "karaokeSize": karaoke_size,
    }
    atomic_write_json(destination / "topic.json", topic)
    (destination / "script.txt").write_text(starter_text + "\n", encoding="utf-8")
    return project_summary(destination)


def duplicate_project(slug: str, payload: dict) -> dict:
    source = project_dir(slug)
    new_slug = validate_slug(payload.get("id", ""))
    destination = project_dir(new_slug, must_exist=False)
    if destination.exists():
        raise FileExistsError(f"Dự án '{new_slug}' đã tồn tại.")
    shutil.copytree(source, destination)
    topic = json.loads((destination / "topic.json").read_text(encoding="utf-8"))
    topic["id"] = new_slug
    atomic_write_json(destination / "topic.json", topic)
    return project_summary(destination)


def delete_project(slug: str) -> dict:
    if any(job.get("project") == slug and job.get("status") in ACTIVE_STATUSES for job in JOBS.values()):
        raise RuntimeError("Dự án đang render, chưa thể xoá.")
    directory = project_dir(slug)
    shutil.rmtree(directory)
    output_dir = OUTPUT_ROOT / slug
    if output_dir.exists():
        shutil.rmtree(output_dir)
    return {"ok": True, "id": slug}


def decode_upload(slug: str, payload: dict) -> dict:
    kind = str(payload.get("kind") or "").strip()
    rules = {
        "leftImage": ("assets", {".png", ".jpg", ".jpeg", ".webp"}),
        "rightImage": ("assets", {".png", ".jpg", ".jpeg", ".webp"}),
        "comparisonImage": ("assets", {".png", ".jpg", ".jpeg", ".webp"}),
        "backgroundImage": ("assets", {".png", ".jpg", ".jpeg", ".webp"}),
        "backgroundMusic": ("audio", {".wav", ".mp3", ".m4a", ".aac", ".ogg"}),
        "voiceover": ("audio", {".wav", ".mp3", ".m4a", ".aac", ".ogg"}),
        "customSfx": ("sfx", {".wav", ".mp3", ".m4a", ".aac", ".ogg"}),
    }
    if kind not in rules:
        raise ValueError("Loại file upload không hợp lệ.")
    name = Path(str(payload.get("name") or "upload.bin")).name
    suffix = Path(name).suffix.lower()
    folder, allowed = rules[kind]
    if suffix not in allowed:
        raise ValueError(f"Định dạng {suffix or '(không có)'} chưa được hỗ trợ.")
    encoded = str(payload.get("data") or "")
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Dữ liệu upload không hợp lệ.") from exc
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("File rỗng hoặc vượt quá 80 MB.")
    directory = project_dir(slug)
    target_dir = directory / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip(".-") or kind
    target = target_dir / f"{kind}-{int(time.time())}-{safe_name}{suffix}"
    target.write_bytes(data)
    relative = target.relative_to(directory).as_posix()
    result = {"ok": True, "kind": kind, "path": relative, "url": file_url(target)}
    if kind == "voiceover":
        result["duration"] = media_duration(target)
    if kind == "customSfx":
        result["sfxKey"] = f"custom-{int(time.time())}-{safe_name.lower()}"
    return result


def normalize_render_options(slug: str, payload: dict) -> dict:
    source = str(payload.get("source") or "project").strip().lower()
    if source not in {"project", "upload", "elevenlabs", "edge"}:
        raise ValueError("Nguồn audio không hợp lệ.")
    try:
        speed = float(payload.get("speed", 1.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Tốc độ audio phải là số.") from exc
    if not 0.5 <= speed <= 2.0:
        raise ValueError("Tốc độ audio phải nằm trong khoảng 0.5-2.0.")
    try:
        volume = float(payload.get("volume", 1.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("Âm lượng audio phải là số.") from exc
    if not 1.0 <= volume <= 3.0:
        raise ValueError("Âm lượng audio phải nằm trong khoảng 1.0-3.0.")
    size = str(payload.get("size") or "1080x1920").strip()
    if size not in {"720x1280", "1080x1920"}:
        raise ValueError("Kích thước render không hợp lệ.")
    options = {
        "source": source,
        "speed": round(speed, 3),
        "volume": round(volume, 3),
        "size": size,
        "uploadYoutube": bool(payload.get("uploadYoutube")),
        "uploadFacebook": bool(payload.get("uploadFacebook")),
    }
    if source == "upload":
        audio_path = safe_relative_asset(payload.get("audioPath"), "audioPath")
        resolved = (project_dir(slug) / audio_path).resolve()
        resolved.relative_to(project_dir(slug).resolve())
        if not resolved.is_file():
            raise FileNotFoundError("Không tìm thấy file audio vừa tải lên.")
        options["audioPath"] = audio_path
    elif source == "elevenlabs":
        config = elevenlabs_config()
        if not str(config.get("api_key") or os.environ.get("ELEVENLABS_API_KEY") or "").strip():
            raise ValueError("Chưa cấu hình ElevenLabs API key.")
        options["voiceId"] = str(payload.get("voiceId") or config.get("voice_id") or "").strip()
        options["modelId"] = str(payload.get("modelId") or config.get("model_id") or "eleven_v3").strip()
        if not options["voiceId"]:
            raise ValueError("Chưa cấu hình ElevenLabs Voice ID.")
    elif source == "edge":
        voice = str(payload.get("voice") or "vi-VN-NamMinhNeural").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", voice):
            raise ValueError("Tên giọng Edge TTS không hợp lệ.")
        options["voice"] = voice
    return options


def new_job(project: str, kind: str, options: dict) -> dict:
    if any(job.get("project") == project and job.get("status") in ACTIVE_STATUSES for job in JOBS.values()):
        raise RuntimeError("Dự án này đang có tác vụ chạy.")
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "project": project,
        "kind": kind,
        "options": options,
        "status": "queued",
        "progress": 0,
        "logs": "",
        "createdAt": now_iso(),
        "startedAt": None,
        "finishedAt": None,
        "outputUrl": None,
        "error": None,
    }
    with JOBS_LOCK:
        JOBS[job_id] = job
    return dict(job)


def update_job(job_id: str, **values: object) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(values)


def append_job_log(job_id: str, line: str) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id]["logs"] = (JOBS[job_id].get("logs", "") + line)[-30000:]


def job_snapshot(job_id: str) -> dict | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def list_job_snapshots() -> list[dict]:
    with JOBS_LOCK:
        return [dict(job) for job in reversed(list(JOBS.values()))]


def job_cancelled(job_id: str) -> bool:
    with JOBS_LOCK:
        return bool((JOBS.get(job_id) or {}).get("cancelRequested"))


def run_logged_command(job_id: str, command: list[str], *, progress_start: int, progress_end: int, estimate_seconds: float) -> int:
    append_job_log(job_id, "$ " + " ".join(command) + "\n")
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    with JOBS_LOCK:
        JOB_PROCESSES[job_id] = process
    started = time.monotonic()
    assert process.stdout is not None

    def collect_output() -> None:
        for line in process.stdout:
            append_job_log(job_id, line)

    reader = Thread(target=collect_output, daemon=True)
    reader.start()
    while process.poll() is None:
        elapsed = time.monotonic() - started
        ratio = min(0.96, elapsed / max(1.0, estimate_seconds))
        update_job(job_id, progress=min(progress_end - 1, progress_start + int((progress_end - progress_start) * ratio)))
        if job_cancelled(job_id):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                process.terminate()
        time.sleep(0.5)
    returncode = process.wait()
    reader.join(timeout=2)
    with JOBS_LOCK:
        JOB_PROCESSES.pop(job_id, None)
    return returncode


def audio_source_for_job(job_id: str, slug: str, options: dict) -> Path:
    directory = project_dir(slug)
    source = options["source"]
    if source == "project":
        topic = read_topic(slug)
        path = (directory / safe_relative_asset(topic.get("voiceover"), "voiceover")).resolve()
        if not path.is_file():
            raise FileNotFoundError("Project chưa có voiceover hợp lệ.")
        return path
    if source == "upload":
        return (directory / options["audioPath"]).resolve()

    output = directory / "audio" / f"{source}-{job_id}.mp3"
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(TTS_PYTHON),
        str(ROOT / "tools" / "generate_voiceover.py"),
        str(directory / "topic.json"),
        "--engine",
        source,
        "--output",
        str(output),
        "--config",
        str(TTS_CONFIG_PATH),
    ]
    if source == "elevenlabs":
        command.extend(["--voice", options["voiceId"], "--model-id", options["modelId"]])
    else:
        command.extend(["--voice", options["voice"]])
    update_job(job_id, progress=4)
    if run_logged_command(job_id, command, progress_start=4, progress_end=24, estimate_seconds=24) != 0:
        raise RuntimeError(f"Không tạo được voiceover bằng {source}.")
    if not output.is_file():
        raise RuntimeError("TTS hoàn tất nhưng không tìm thấy file audio.")
    return output


def prepare_render_topic(
    job_id: str,
    slug: str,
    source_audio: Path,
    speed: float,
    volume: float = 1.0,
) -> tuple[Path, list[Path]]:
    directory = project_dir(slug)
    topic = read_topic(slug)
    temporary: list[Path] = []
    suffix = f"{speed:g}x"
    if abs(volume - 1.0) > 0.001:
        suffix = f"{suffix}-v{volume:g}"
    audio = directory / "audio" / f"render-{job_id}-{suffix}-lim.wav"
    filters = [f"atempo={speed:g}"]
    if abs(volume - 1.0) > 0.001:
        filters.append(f"volume={volume:g}")
    filters.append(AUDIO_PEAK_LIMITER)
    command = [
        str(ffmpeg_executable()), "-y", "-i", str(source_audio), "-vn", "-filter:a", ",".join(filters),
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(audio),
    ]
    if run_logged_command(job_id, command, progress_start=24, progress_end=30, estimate_seconds=5) != 0:
        raise RuntimeError("Không xử lý được tốc độ/âm lượng audio.")
    temporary.append(audio)

    duration = media_duration(audio)
    old_duration = max(0.1, float(topic.get("duration") or duration))
    scale = duration / old_duration
    topic["duration"] = duration
    topic["voiceover"] = audio.resolve().relative_to(directory.resolve()).as_posix()
    topic["segments"] = [
        {**segment, "start": round(float(segment["start"]) * scale, 3), "end": round(min(duration, float(segment["end"]) * scale), 3)}
        for segment in topic.get("segments", [])
    ]
    topic["poseTimeline"] = [
        {**event, "time": round(min(duration, float(event["time"]) * scale), 3)}
        for event in topic.get("poseTimeline", [])
    ]
    render_topic = directory / f"topic.render-{job_id}.json"
    atomic_write_json(render_topic, topic)
    temporary.append(render_topic)
    return render_topic, temporary


def run_job(job_id: str) -> None:
    job = job_snapshot(job_id)
    if not job:
        return
    slug, kind, options = job["project"], job["kind"], job["options"]
    directory = project_dir(slug)
    output_dir = OUTPUT_ROOT / slug
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = output_dir / f"{slug}-{stamp}.mp4"
    width, height = (int(value) for value in options["size"].split("x"))
    update_job(job_id, status="running", progress=5, startedAt=now_iso())
    temporary: list[Path] = []
    try:
        source_audio = audio_source_for_job(job_id, slug, options)
        if job_cancelled(job_id):
            raise InterruptedError("Đã dừng render.")
        render_topic, temporary = prepare_render_topic(
            job_id,
            slug,
            source_audio,
            float(options["speed"]),
            float(options.get("volume", 1.0)),
        )
        duration = max(1.0, media_duration(source_audio) / float(options["speed"]))
        align_command = [
            str(TTS_PYTHON), str(ROOT / "tools" / "align_voiceover.py"), str(render_topic),
            str((project_dir(slug) / json.loads(render_topic.read_text(encoding="utf-8"))["voiceover"]).resolve()),
            "--output", str(render_topic), "--model", "base",
        ]
        if run_logged_command(job_id, align_command, progress_start=30, progress_end=43, estimate_seconds=22) != 0:
            raise RuntimeError("Whisper không căn được subtitle với voiceover.")
        if job_cancelled(job_id):
            raise InterruptedError("Đã dừng render.")
        command = [
            sys.executable, str(ROOT / "tools" / "render_demo.py"), str(render_topic),
            "--output", str(output), "--width", str(width), "--height", str(height),
        ]
        returncode = run_logged_command(job_id, command, progress_start=43, progress_end=96, estimate_seconds=duration + 22)
        if job_cancelled(job_id):
            raise InterruptedError("Đã dừng render.")
        if returncode != 0 or not output.exists():
            raise RuntimeError(f"Tác vụ kết thúc với mã {returncode}.")
        update_job(job_id, progress=97, outputUrl=file_url(output))
        uploads = []
        for platform, enabled in (("youtube", options.get("uploadYoutube")), ("facebook", options.get("uploadFacebook"))):
            if not enabled:
                continue
            append_job_log(job_id, f"\nĐang upload {platform}...\n")
            result = social_upload(platform, {"project": slug})
            uploads.append(result)
            append_job_log(job_id, f"Upload {platform} hoàn tất: {result.get('url') or result.get('message') or 'OK'}\n")
        update_job(job_id, status="done", progress=100, finishedAt=now_iso(), outputUrl=file_url(output), uploads=uploads)
    except InterruptedError as exc:
        append_job_log(job_id, f"\n{exc}\n")
        update_job(job_id, status="cancelled", finishedAt=now_iso(), error=None)
    except Exception as exc:
        append_job_log(job_id, f"\nLỗi: {exc}\n")
        update_job(job_id, status="failed", finishedAt=now_iso(), error=str(exc))
    finally:
        for path in temporary:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def start_job(slug: str, kind: str, payload: dict) -> dict:
    project_dir(slug)
    if kind != "render":
        raise ValueError("Loại job không hợp lệ.")
    options = normalize_render_options(slug, payload)
    job = new_job(slug, kind, options)
    Thread(target=run_job, args=(job["id"],), daemon=True).start()
    return job


def cancel_job(job_id: str) -> dict:
    job = job_snapshot(job_id)
    if not job:
        raise FileNotFoundError("Không tìm thấy job.")
    if job["status"] not in ACTIVE_STATUSES:
        return job
    update_job(job_id, cancelRequested=True, status="cancelling")
    with JOBS_LOCK:
        process = JOB_PROCESSES.get(job_id)
    if process and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except Exception:
            process.terminate()
    return job_snapshot(job_id) or job


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_bytes(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, status: int, value: object) -> None:
        self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def send_webui(self, filename: str) -> None:
        path = WEBUI_ROOT / filename
        self.send_bytes(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD_BYTES * 2:
            raise ValueError("Request body rỗng hoặc quá lớn.")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("Request body phải là JSON object.")
        return value

    def handle_error(self, exc: Exception) -> None:
        status = 404 if isinstance(exc, FileNotFoundError) else 409 if isinstance(exc, (FileExistsError, RuntimeError)) else 400
        self.send_json(status, {"error": str(exc)})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            self.send_webui("dashboard.html")
            return
        if path in {"/editor", "/editor/"}:
            self.send_webui("editor.html")
            return
        if path in {"/upload", "/upload/"}:
            self.send_webui("upload.html")
            return
        if path.startswith("/webui/"):
            name = Path(path).name
            if name not in {"styles.css", "dashboard.js", "editor.js", "upload.js"}:
                self.send_json(404, {"error": "Không tìm thấy file Web UI."})
                return
            self.send_webui(name)
            return
        if path == "/api/health":
            self.send_json(200, {"ok": True, "root": str(ROOT), "projects": len(list_projects()), "dependencies": dependency_status()})
            return
        if path == "/api/projects":
            self.send_json(200, {"projects": list_projects()})
            return
        if path == "/api/characters":
            self.send_json(200, {
                "characters": list_characters(),
                "defaultCharacterId": default_character_id(),
            })
            return
        if path == "/api/jobs":
            self.send_json(200, {"jobs": list_job_snapshots()})
            return
        if path == "/api/images/remove-background/status":
            self.send_json(200, remove_background_status())
            return
        if path == "/api/tts/elevenlabs/config":
            self.send_json(200, elevenlabs_public_config())
            return
        if path == "/api/social/status":
            try:
                self.send_json(200, social_status())
            except Exception as exc:
                self.handle_error(exc)
            return
        if path == "/api/social/metadata":
            project = (parse_qs(parsed.query).get("project") or [""])[0]
            try:
                self.send_json(200, social_metadata(validate_slug(project)))
            except Exception as exc:
                self.handle_error(exc)
            return
        match = re.fullmatch(r"/api/projects/([^/]+)/topic", path)
        if match:
            try:
                slug = validate_slug(match.group(1))
                self.send_json(200, {"project": slug, "topic": read_topic(slug)})
            except Exception as exc:
                self.handle_error(exc)
            return
        match = re.fullmatch(r"/api/jobs/([a-f0-9]+)", path)
        if match:
            job = job_snapshot(match.group(1))
            self.send_json(200, job) if job else self.send_json(404, {"error": "Không tìm thấy job."})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/projects":
                self.send_json(201, {"project": create_project(payload)})
                return
            if path == "/api/characters/split":
                self.send_json(201, split_character_sheet(payload))
                return
            if path == "/api/images/remove-background":
                self.send_json(200, remove_background_image(payload))
                return
            if path == "/api/characters":
                self.send_json(201, save_character(payload))
                return
            if path == "/api/tts/elevenlabs/config":
                self.send_json(200, update_elevenlabs_config(payload))
                return
            match = re.fullmatch(r"/api/social/(youtube|facebook)/active", path)
            if match:
                key = "channelId" if match.group(1) == "youtube" else "pageId"
                self.send_json(200, set_social_active(match.group(1), str(payload.get(key) or "")))
                return
            match = re.fullmatch(r"/api/social/(youtube|facebook)/upload", path)
            if match:
                self.send_json(200, social_upload(match.group(1), payload))
                return
            match = re.fullmatch(r"/api/jobs/([a-f0-9]+)/cancel", path)
            if match:
                self.send_json(200, cancel_job(match.group(1)))
                return
            match = re.fullmatch(r"/api/projects/([^/]+)/(duplicate|upload|render)", path)
            if not match:
                self.send_json(404, {"error": "API không tồn tại."})
                return
            slug, action = validate_slug(match.group(1)), match.group(2)
            if action == "duplicate":
                self.send_json(201, {"project": duplicate_project(slug, payload)})
            elif action == "upload":
                self.send_json(201, decode_upload(slug, payload))
            else:
                self.send_json(202, {"job": start_job(slug, action, payload)})
        except Exception as exc:
            self.handle_error(exc)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        character_match = re.fullmatch(r"/api/characters/([^/]+)", path)
        if character_match:
            try:
                payload = self.read_json()
                self.send_json(200, update_character(unquote(character_match.group(1)), payload))
            except Exception as exc:
                self.handle_error(exc)
            return
        match = re.fullmatch(r"/api/projects/([^/]+)/topic", path)
        if not match:
            self.send_json(404, {"error": "API không tồn tại."})
            return
        try:
            payload = self.read_json()
            slug = validate_slug(match.group(1))
            self.send_json(200, {"project": slug, "topic": save_topic(slug, payload)})
        except Exception as exc:
            self.handle_error(exc)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        character_match = re.fullmatch(r"/api/characters/([^/]+)", path)
        if character_match:
            try:
                self.send_json(200, delete_character(unquote(character_match.group(1))))
            except Exception as exc:
                self.handle_error(exc)
            return
        match = re.fullmatch(r"/api/projects/([^/]+)", path)
        if not match:
            self.send_json(404, {"error": "API không tồn tại."})
            return
        try:
            self.send_json(200, delete_project(validate_slug(match.group(1))))
        except Exception as exc:
            self.handle_error(exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="AurexVideo local Web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args()
    migrate_shared_configs()
    handler = partial(Handler, directory=str(ROOT))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"AurexVideo Web UI: http://{args.host}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
