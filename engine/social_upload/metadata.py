from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote, unquote

from aurexvideo_paths import PROJECTS_ROOT, RESOURCE_ROOT

REPO_ROOT = RESOURCE_ROOT
PROJECT_ROOT = PROJECTS_ROOT
YOUTUBE_TITLE_LIMIT = 90
DEFAULT_YOUTUBE_TITLE = "Sự khác nhau là gì?, Phần 1"
DEFAULT_YOUTUBE_DESCRIPTION = "🎬 Sự khác nhau là gì?\n#Hieuhamhoc #sosanh #kienthuc"
DEFAULT_FACEBOOK_CAPTION = "🎬 Sự khác nhau là gì?, Phần 1\n#Hieuhamhoc #sosanh #kienthuc"
DEFAULT_YOUTUBE_TAGS = ["Hieuhamhoc", "sosanh", "kienthuc"]
DEFAULT_YOUTUBE_TITLE_EN = "What's the difference?, Part 1"
DEFAULT_YOUTUBE_DESCRIPTION_EN = "🎬 What's the difference?\n#AurexVideo #comparison #learn"
DEFAULT_FACEBOOK_CAPTION_EN = "🎬 What's the difference?, Part 1\n#AurexVideo #comparison #learn"
DEFAULT_YOUTUBE_TAGS_EN = ["AurexVideo", "comparison", "learn"]


def default_upload_copy(language: str = "vi") -> dict:
    if str(language or "vi").lower().startswith("en"):
        return {
            "title": DEFAULT_YOUTUBE_TITLE_EN,
            "description": DEFAULT_YOUTUBE_DESCRIPTION_EN,
            "facebookCaption": DEFAULT_FACEBOOK_CAPTION_EN,
            "tags": list(DEFAULT_YOUTUBE_TAGS_EN),
            "sourcePrefix": "Source: ",
        }
    return {
        "title": DEFAULT_YOUTUBE_TITLE,
        "description": DEFAULT_YOUTUBE_DESCRIPTION,
        "facebookCaption": DEFAULT_FACEBOOK_CAPTION,
        "tags": list(DEFAULT_YOUTUBE_TAGS),
        "sourcePrefix": "Nguồn: ",
    }


def validate_project_name(project: str) -> str:
    project = unquote(str(project or "")).strip()
    if not project or project in {".", ".."} or "/" in project or "\\" in project or "\x00" in project:
        raise ValueError("Invalid project name.")
    return project


def require_project(project: str) -> Path:
    project = validate_project_name(project)
    project_dir = (PROJECT_ROOT / project).resolve()
    try:
        project_dir.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("Invalid project path.") from exc
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Project '{project}' not found.")
    return project_dir


def final_video_url(project: str) -> str:
    return f"/project/{quote(project)}/output/final_video.mp4"


def final_video_path_for_project(project: str) -> Path:
    project_dir = require_project(project)
    video_path = (project_dir / "output" / "final_video.mp4").resolve()
    try:
        video_path.relative_to(project_dir.resolve())
    except ValueError as exc:
        raise ValueError("Invalid video path.") from exc
    if not video_path.is_file():
        raise FileNotFoundError(f"final_video.mp4 not found for project '{project_dir.name}'.")
    return video_path


def first_url_from_source(project_dir: Path) -> str:
    links = project_dir / "source" / "links.txt"
    if links.exists():
        for line in links.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if stripped.startswith("http"):
                return stripped
    source = project_dir / "source" / "source.md"
    if source.exists():
        match = re.search(r"https?://\S+", source.read_text(encoding="utf-8", errors="replace"))
        if match:
            return match.group(0).rstrip(").,")
    return ""


def upload_metadata_path(project_dir: Path) -> Path:
    return project_dir / "upload-metadata.json"


def script_path(project_dir: Path) -> Path:
    return project_dir / "script.txt"


def upload_metadata_needs_sync(project_dir: Path) -> bool:
    metadata_path = upload_metadata_path(project_dir)
    source_script = script_path(project_dir)
    if not source_script.exists():
        return False
    if not metadata_path.exists():
        return True
    return source_script.stat().st_mtime > metadata_path.stat().st_mtime


def read_script_lines(project_dir: Path) -> list[str]:
    source_script = script_path(project_dir)
    if not source_script.exists():
        return []
    return [line.strip() for line in source_script.read_text(encoding="utf-8").splitlines() if line.strip()]


def lead_icon_for_line(line: str, index: int) -> str:
    text = line.lower()
    keyword_icons = [
        (("ra mắt", "launch", "released", "vừa cho ra mắt", "vừa ra mắt"), "🚀"),
        (("miễn phí", "free", "trial", "openrouter", "dùng thử"), "🎁"),
        (("benchmark", "gpt", "opus", "model", "tham số", "moe"), "📊"),
        (("apache", "commercial", "open source", "thương mại"), "✅"),
        (("agent", "workflow", "pipeline", "tool", "api"), "⚙️"),
        (("giá", "rẻ", "affordable", "cost", "pricing"), "💸"),
        (("demo", "video", "source", "nguồn"), "🎬"),
    ]
    for keywords, icon in keyword_icons:
        if any(keyword in text for keyword in keywords):
            return icon
    return ["🎬", "🔎", "💡", "🧠", "⚡", "✅", "📌"][index % 7]


def upload_paragraphs(lines: list[str]) -> str:
    paragraphs = []
    for index, line in enumerate(lines):
        text = re.sub(r"\s+", " ", line).strip()
        if text:
            paragraphs.append(f"{lead_icon_for_line(text, index)} {text}")
    return "\n\n".join(paragraphs)


def related_tags_for_script(lines: list[str], project_dir: Path | None = None) -> list[str]:
    text_parts = list(lines)
    if project_dir:
        text_parts.append(project_dir.name.replace("-", " "))
    text = " ".join(text_parts).lower()
    tags = ["ThoThongThai", "SoSanh", "KienThuc"]

    def add(tag: str) -> None:
        if tag not in tags:
            tags.append(tag)

    keyword_tags = [
        (("gpt", "opus", "gemini", "claude", "mô hình ai"), "AI"),
        (("agent", "agentic", "workflow", "usecase"), "AgenticAI"),
        (("api",), "API"),
        (("benchmark", "eval", "đánh giá"), "Benchmark"),
        (("openrouter",), "OpenRouter"),
        (("tencent",), "Tencent"),
        (("hunyuan",), "Hunyuan"),
        (("hy3",), "Hy3"),
        (("moe", "mixture of experts"), "MoE"),
        (("apache", "open source", "opensource", "mã nguồn mở"), "OpenSource"),
        (("github", "repo"), "GitHub"),
        (("claude code",), "ClaudeCode"),
        (("video", "clip", "youtube", "tiktok", "loom", "screen recording"), "VideoAI"),
        (("yt-dlp", "ffmpeg", "whisper"), "VideoTools"),
    ]
    for keywords, tag in keyword_tags:
        if any(keyword in text for keyword in keywords):
            add(tag)
    return tags[:9]


def hashtag_block_from_tags(tags: list[str]) -> str:
    cleaned = []
    for tag in tags:
        tag_text = re.sub(r"[^0-9A-Za-z_]", "", str(tag or ""))
        if tag_text and tag_text not in cleaned:
            cleaned.append(tag_text)
    return " ".join(f"#{tag}" for tag in cleaned) if cleaned else "#ThoThongThai"


def limit_youtube_title(value: str) -> str:
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(title) <= YOUTUBE_TITLE_LIMIT:
        return title
    cut = title[:YOUTUBE_TITLE_LIMIT].rstrip()
    last_space = cut.rfind(" ")
    if last_space >= 60:
        cut = cut[:last_space]
    return cut.rstrip(" .,;:-")


def read_project_upload_metadata(project_dir: Path) -> dict:
    metadata_path = upload_metadata_path(project_dir)
    if not metadata_path.exists():
        return {}
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"upload-metadata.json is invalid JSON: {metadata_path}") from exc
    return data if isinstance(data, dict) else {}


def has_todo_placeholder(value: object) -> bool:
    return bool(re.search(r"(?i)(?:#|\b)todo\b", str(value or "")))


def metadata_has_todo_placeholder(value: object) -> bool:
    if isinstance(value, dict):
        return any(metadata_has_todo_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(metadata_has_todo_placeholder(item) for item in value)
    return has_todo_placeholder(value)


def trailing_hashtag_block(value: str, fallback: str = "#ThoThongThai") -> str:
    lines = [line.strip() for line in str(value or "").splitlines()]
    trailing = []
    for line in reversed(lines):
        if not line:
            if trailing:
                continue
            continue
        if line.startswith("#"):
            trailing.append(line)
            continue
        break
    trailing.reverse()
    return "\n".join(trailing) if trailing else fallback


def resolved_hashtag_block(existing_value: str, generated: str) -> str:
    block = trailing_hashtag_block(existing_value, generated).strip()
    return generated if block.lower() == "#aurexvideo" or has_todo_placeholder(block) else block


def resolved_youtube_tags(existing_tags: object, generated_tags: list[str]) -> list[str]:
    if not isinstance(existing_tags, list):
        return generated_tags
    cleaned = [str(tag).strip() for tag in existing_tags if str(tag or "").strip()]
    meaningful = [tag for tag in cleaned if tag.lower() != "aurexvideo"]
    if len(meaningful) == 0 or any(has_todo_placeholder(tag) for tag in cleaned):
        return generated_tags
    return cleaned


def resolved_source_comment(existing_value: object, source_url: str) -> str:
    existing = str(existing_value or "").strip()
    if existing and not has_todo_placeholder(existing):
        return existing
    return f"Nguồn: {source_url}" if source_url else ""


def merge_upload_metadata(defaults: dict, custom: dict) -> dict:
    if not custom:
        return defaults
    result = json.loads(json.dumps(defaults, ensure_ascii=False))
    for section in ("youtube", "facebook"):
        if isinstance(custom.get(section), dict):
            result.setdefault(section, {}).update(custom[section])
    for key, value in custom.items():
        if key not in {"youtube", "facebook"}:
            result[key] = value
    return result


def generated_upload_metadata(project_dir: Path, script_lines: list[str], existing: dict | None = None, language: str = "vi") -> dict:
    existing = existing if isinstance(existing, dict) else {}
    source_url = first_url_from_source(project_dir)
    existing_youtube = existing.get("youtube", {}) if isinstance(existing.get("youtube"), dict) else {}
    existing_facebook = existing.get("facebook", {}) if isinstance(existing.get("facebook"), dict) else {}
    copy = default_upload_copy(language)
    return {
        "version": int(existing.get("version") or 1) if isinstance(existing.get("version", 1), int) else 1,
        "youtube": {
            "title": copy["title"],
            "description": copy["description"],
            "privacyStatus": str(existing_youtube.get("privacyStatus") or "public"),
            "tags": copy["tags"],
        },
        "facebook": {
            "caption": copy["facebookCaption"],
            "videoState": str(existing_facebook.get("videoState") or "PUBLISHED"),
            "sourceComment": resolved_source_comment(existing_facebook.get("sourceComment"), source_url),
        },
    }


def write_project_upload_metadata(project_dir: Path, metadata: dict) -> None:
    encoded = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > 200_000:
        raise ValueError("upload-metadata.json is too large.")
    upload_metadata_path(project_dir).write_bytes(encoded + b"\n")


def sync_upload_metadata_from_script(project: str, script_lines: list[str] | None = None, language: str = "vi") -> dict:
    project_dir = require_project(project)
    lines = script_lines if script_lines is not None else read_script_lines(project_dir)
    existing = read_project_upload_metadata(project_dir)
    metadata = generated_upload_metadata(project_dir, lines, existing, language=language)
    write_project_upload_metadata(project_dir, metadata)
    return metadata


def build_upload_metadata(project: str, language: str = "vi") -> dict:
    project_dir = require_project(project)
    script_lines = read_script_lines(project_dir)
    existing = read_project_upload_metadata(project_dir)
    copy = default_upload_copy(language)
    defaults = generated_upload_metadata(project_dir, script_lines, {}, language=language)
    if upload_metadata_needs_sync(project_dir) or metadata_has_todo_placeholder(existing):
        metadata = generated_upload_metadata(project_dir, script_lines, existing, language=language)
        write_project_upload_metadata(project_dir, metadata)
    else:
        metadata = merge_upload_metadata(defaults, existing)
    source_url = first_url_from_source(project_dir)
    youtube = metadata.get("youtube", {}) if isinstance(metadata.get("youtube"), dict) else {}
    facebook = metadata.get("facebook", {}) if isinstance(metadata.get("facebook"), dict) else {}
    title = str(youtube.get("title") or "").strip()
    description = str(youtube.get("description") or "").strip()
    caption = str(facebook.get("caption") or "").strip()
    # Prefer language-aware starter copy when the stored values are still the other-language defaults.
    vi = default_upload_copy("vi")
    en = default_upload_copy("en")
    if language.startswith("en"):
        if not title or title in {vi["title"], en["title"]}:
            title = en["title"]
        if not description or description in {vi["description"], en["description"]}:
            description = en["description"]
        if not caption or caption in {vi["facebookCaption"], en["facebookCaption"]}:
            caption = en["facebookCaption"]
        tags = en["tags"]
        source_prefix = en["sourcePrefix"]
    else:
        if not title or title in {vi["title"], en["title"]}:
            title = vi["title"]
        if not description or description in {vi["description"], en["description"]}:
            description = vi["description"]
        if not caption or caption in {vi["facebookCaption"], en["facebookCaption"]}:
            caption = vi["facebookCaption"]
        tags = vi["tags"]
        source_prefix = vi["sourcePrefix"]
    return {
        "project": project_dir.name,
        "title": title,
        "description": description,
        "youtubeDescription": description,
        "facebookCaption": caption,
        "facebookVideoState": str(facebook.get("videoState") or "PUBLISHED").upper(),
        "facebookSourceComment": str(facebook.get("sourceComment") or (f"{source_prefix}{source_url}" if source_url else "")),
        "source_url": source_url,
        "privacyStatus": str(youtube.get("privacyStatus") or "public"),
        "tags": tags,
        "upload_metadata_url": f"/project/{quote(project_dir.name)}/upload-metadata.json",
        "upload_metadata_exists": upload_metadata_path(project_dir).exists(),
        "video_url": final_video_url(project_dir.name),
    }
