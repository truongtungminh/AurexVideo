from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote

from aurexvideo_paths import PROJECTS_ROOT, RESOURCE_ROOT
from media_probe import validate_rendered_video
from .config import canonical_brand

REPO_ROOT = RESOURCE_ROOT
PROJECT_ROOT = PROJECTS_ROOT
YOUTUBE_TITLE_LIMIT = 90
DEFAULT_YOUTUBE_TITLE = "Sự khác nhau là gì?, Phần 1"
DEFAULT_YOUTUBE_DESCRIPTION = "🎬 Sự khác nhau là gì?\n#bietchichonhieu #sosanh #kienthuc"
DEFAULT_FACEBOOK_CAPTION = "🎬 Sự khác nhau là gì?, Phần 1\n#bietchichonhieu #sosanh #kienthuc"
DEFAULT_INSTAGRAM_CAPTION = DEFAULT_FACEBOOK_CAPTION
DEFAULT_YOUTUBE_TAGS = ["bietchichonhieu", "sosanh", "kienthuc"]
DEFAULT_BINANCE_CAPTION = "🎬 Sự khác nhau là gì?, Phần 1\n#Write2Earn $btc #btc"
DEFAULT_BINANCE_TAGS = ["Write2Earn", "btc"]
DEFAULT_YOUTUBE_TITLE_EN = "What's the difference?, Part 1"
DEFAULT_YOUTUBE_DESCRIPTION_EN = "🎬 What's the difference?\n#AurexVideo #comparison #learn"
DEFAULT_FACEBOOK_CAPTION_EN = "🎬 What's the difference?, Part 1\n#AurexVideo #comparison #learn"
DEFAULT_INSTAGRAM_CAPTION_EN = DEFAULT_FACEBOOK_CAPTION_EN
DEFAULT_YOUTUBE_TAGS_EN = ["AurexVideo", "comparison", "learn"]
DEFAULT_BINANCE_CAPTION_EN = "🎬 What's the difference?, Part 1\n#Write2Earn $btc #btc"


def default_upload_copy(language: str = "vi") -> dict:
    if str(language or "vi").lower().startswith("en"):
        return {
            "title": DEFAULT_YOUTUBE_TITLE_EN,
            "description": DEFAULT_YOUTUBE_DESCRIPTION_EN,
            "facebookCaption": DEFAULT_FACEBOOK_CAPTION_EN,
            "instagramCaption": DEFAULT_INSTAGRAM_CAPTION_EN,
            "binanceCaption": DEFAULT_BINANCE_CAPTION_EN,
            "tags": list(DEFAULT_YOUTUBE_TAGS_EN),
            "binanceTags": list(DEFAULT_BINANCE_TAGS),
            "sourcePrefix": "Source: ",
        }
    return {
        "title": DEFAULT_YOUTUBE_TITLE,
        "description": DEFAULT_YOUTUBE_DESCRIPTION,
        "facebookCaption": DEFAULT_FACEBOOK_CAPTION,
        "instagramCaption": DEFAULT_INSTAGRAM_CAPTION,
        "binanceCaption": DEFAULT_BINANCE_CAPTION,
        "tags": list(DEFAULT_YOUTUBE_TAGS),
        "binanceTags": list(DEFAULT_BINANCE_TAGS),
        "sourcePrefix": "Nguồn: ",
    }


def validate_project_name(project: str) -> str:
    project = unquote(str(project or "")).strip()
    if not project or project in {".", ".."} or "/" in project or "\\" in project or "\x00" in project:
        raise ValueError("Invalid project name.")
    return project


def require_project(project: str) -> Path:
    project = validate_project_name(project)
    # The web server may rebind PROJECT_ROOT during source-root setup;
    # normalize it here so upload paths always use pathlib semantics.
    project_root = Path(PROJECT_ROOT).expanduser().resolve()
    project_dir = (project_root / project).resolve()
    try:
        project_dir.relative_to(project_root)
    except ValueError as exc:
        raise ValueError("Invalid project path.") from exc
    if not project_dir.is_dir():
        raise FileNotFoundError(f"Project '{project}' not found.")
    return project_dir


def project_brand_from_topic(project_dir: Path) -> str:
    """Read the project-owned brand used to select social destinations."""
    topic_path = project_dir / "topic.json"
    if not topic_path.is_file():
        return ""
    try:
        topic = json.loads(topic_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(topic, dict):
        return ""
    return canonical_brand(topic.get("brand"))


def upload_brand_for_project(payload: dict, project_dir: Path, platform: str) -> str:
    """Return the project-owned Brand and reject a conflicting API payload."""
    declared = canonical_brand(payload.get("brand") or payload.get("brandId"))
    project_brand = project_brand_from_topic(project_dir)
    if declared and project_brand and declared != project_brand:
        raise ValueError(
            f"{platform} Brand '{declared}' không khớp Brand '{project_brand}' của project."
        )
    brand = declared or project_brand
    if not brand:
        raise ValueError(f"{platform} upload thiếu Brand.")
    return brand


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


def read_expected_video_bytes(video_path: Path, payload: dict) -> bytes:
    """Read one immutable upload payload and verify an optional Bridge digest."""
    expected = str(
        payload.get("expectedMediaSha256")
        or payload.get("expected_media_sha256")
        or ""
    ).strip().lower()
    if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("expectedMediaSha256 must be a SHA-256 hex digest.")
    validate_upload_video(video_path)
    data = video_path.read_bytes()
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise RuntimeError("The project MP4 changed after the social draft was created.")
    return data


def validate_upload_video(video_path: Path) -> dict[str, object]:
    """Run the same strict media postflight immediately before upload."""
    return validate_rendered_video(Path(video_path))


def _probe_duration_ffmpeg(video_path: Path) -> float:
    candidates = [
        shutil.which("ffmpeg"),
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        str(Path.home() / "Library/Application Support/app.aurexvideo/runtime/bin/ffmpeg"),
    ]
    ffmpeg = next((c for c in candidates if c and Path(c).exists()), None)
    if not ffmpeg:
        return 0.0
    try:
        result = subprocess.run(
            [ffmpeg, "-i", str(video_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, ValueError):
        return 0.0
    text = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return float(hours) * 3600 + float(minutes) * 60 + float(seconds)


def video_duration_seconds(project: str) -> float:
    """Return final_video.mp4 duration in seconds (0.0 if unknown)."""
    try:
        video_path = final_video_path_for_project(project)
    except (FileNotFoundError, ValueError):
        return 0.0
    ffprobe_candidates = [
        shutil.which("ffprobe"),
        "/opt/homebrew/bin/ffprobe",
        "/usr/local/bin/ffprobe",
    ]
    ffprobe = next((c for c in ffprobe_candidates if c and Path(c).exists()), None)
    if ffprobe:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            value = result.stdout.strip()
            if value and re.fullmatch(r"\d+(\.\d+)?", value):
                return float(value)
        except (OSError, ValueError):
            pass
    return _probe_duration_ffmpeg(video_path)


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


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


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


def read_project_upload_metadata(project_dir: Path | str) -> dict:
    # Upload callers pass either a project slug or an already-resolved Path.
    if isinstance(project_dir, (str, bytes)):
        project_dir = require_project(str(project_dir))
    else:
        project_dir = Path(project_dir)
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
    for section in ("youtube", "facebook", "instagram", "tiktok"): 
        if isinstance(custom.get(section), dict):
            result.setdefault(section, {}).update(custom[section])
    for key, value in custom.items():
        if key not in {"youtube", "facebook", "instagram"}:
            result[key] = value
    return result


def generated_upload_metadata(project_dir: Path, script_lines: list[str], existing: dict | None = None, language: str = "vi") -> dict:
    existing = existing if isinstance(existing, dict) else {}
    source_url = first_url_from_source(project_dir)
    existing_youtube = existing.get("youtube", {}) if isinstance(existing.get("youtube"), dict) else {}
    existing_facebook = existing.get("facebook", {}) if isinstance(existing.get("facebook"), dict) else {}
    existing_instagram = existing.get("instagram", {}) if isinstance(existing.get("instagram"), dict) else {}
    existing_tiktok = existing.get("tiktok", {}) if isinstance(existing.get("tiktok"), dict) else {}
    existing_social = existing.get("social", {}) if isinstance(existing.get("social"), dict) else {}
    existing_publish = existing.get("publish", {}) if isinstance(existing.get("publish"), dict) else {}
    copy = default_upload_copy(language)
    binance_tags = list(DEFAULT_BINANCE_TAGS)
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
        "instagram": {
            "caption": str(existing_instagram.get("caption") or copy["instagramCaption"]),
        },
        "tiktok": {
            "caption": str(existing_tiktok.get("caption") or copy["instagramCaption"]),
        },
        "binance": {
            "caption": copy["binanceCaption"],
            "tags": binance_tags,
        },
        "publish": {
            "schemaVersion": 1,
            "brand": canonical_brand(existing_publish.get("brand") or project_brand_from_topic(project_dir)),
            "caption": str(existing_publish.get("caption") or "").strip(),
        },
        "social": existing_social,
    }


def write_project_upload_metadata(project_dir: Path, metadata: dict) -> None:
    encoded = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > 200_000:
        raise ValueError("upload-metadata.json is too large.")
    upload_metadata_path(project_dir).write_bytes(encoded + b"\n")


def _record_social_upload(project_dir: Path, platform: str, details: dict) -> dict:
    existing = read_project_upload_metadata(project_dir)
    if not existing:
        existing = generated_upload_metadata(project_dir, read_script_lines(project_dir), {}, language="vi")
    social = existing.get("social", {}) if isinstance(existing.get("social"), dict) else {}
    state = str(details.get("state") or details.get("video_state") or "published").strip().upper()
    entry = {
        "platform": platform,
        "url": str(details.get("url") or "").strip(),
        "videoId": str(details.get("videoId") or details.get("video_id") or "").strip(),
        "postId": str(details.get("postId") or details.get("post_id") or "").strip(),
        "state": state,
        "scheduledAt": str(details.get("scheduledAt") or details.get("scheduled_at") or "").strip(),
    }
    if state in {"SCHEDULED", "DRAFT", "INBOX"}:
        entry["queuedAt"] = now_iso()
    elif state == "FAILED":
        entry["failedAt"] = now_iso()
    else:
        entry["postedAt"] = now_iso()
    brand = canonical_brand(details.get("brand") or details.get("brandId"))
    if brand:
        entry["brand"] = brand
    connection_id = str(details.get("connection_id") or details.get("connectionId") or "").strip()
    if connection_id:
        entry["connectionId"] = connection_id
    error = str(details.get("error") or "").strip()
    if error:
        entry["error"] = error[:1000]
    social[platform] = entry
    existing["social"] = social
    write_project_upload_metadata(project_dir, existing)
    return entry


def record_social_upload(project: str, platform: str, details: dict) -> dict:
    return _record_social_upload(require_project(project), platform, details)


def record_scheduled_social_upload(
    project_dir: Path,
    platform: str,
    scheduled_at: str,
    *,
    brand: str = "",
    connection_id: str = "",
) -> dict:
    """Persist the dashboard-safe part of a queued social upload.

    Queued workers must not write their job payloads to project metadata: those can
    contain credentials or temporary media URLs.  This record intentionally keeps
    only state needed by the dashboard.
    """
    project_dir = Path(project_dir)
    if not project_dir.is_dir():
        # A project removed after queuing cannot be shown on the dashboard.
        return {}
    details = {"state": "SCHEDULED", "scheduled_at": scheduled_at, "brand": brand}
    if connection_id:
        details["connection_id"] = connection_id
    return _record_social_upload(project_dir, platform, details)


def record_scheduled_social_failure(
    project: str,
    platform: str,
    error: str,
    *,
    scheduled_at: str = "",
    brand: str = "",
    connection_id: str = "",
) -> dict:
    """Persist a safe, dashboard-visible failure from a local social worker."""
    details = {
        "state": "FAILED",
        "error": str(error or "").strip(),
        "scheduled_at": scheduled_at,
        "brand": brand,
    }
    if connection_id:
        details["connection_id"] = connection_id
    return record_social_upload(project, platform, details)


def normalize_scheduled_social_time(value: object) -> str:
    """Return an ISO UTC timestamp for a stored scheduled social upload."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        if raw.replace(".", "", 1).isdigit():
            parsed = datetime.fromtimestamp(float(raw), tz=timezone.utc)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except (OverflowError, TypeError, ValueError):
        return ""


def localized_scheduled_social_label(value: object) -> str:
    """Format a stored scheduled timestamp for the Vietnamese dashboard."""
    normalized = normalize_scheduled_social_time(value)
    if not normalized:
        return ""
    local_time = datetime.fromisoformat(normalized.replace("Z", "+00:00")).astimezone()
    return f"Lúc {local_time:%H:%M} · {local_time:%d/%m/%Y}"


def scheduled_social_details(entry: object) -> dict:
    """Extract a normalized, dashboard-safe schedule payload from a social entry."""
    if not isinstance(entry, dict):
        return {}
    for key in ("scheduledAt", "scheduled_at", "scheduledPublishAt", "scheduled_publish_at"):
        scheduled_at = normalize_scheduled_social_time(entry.get(key))
        if scheduled_at:
            return {
                "scheduled_at": scheduled_at,
                "scheduled_label": localized_scheduled_social_label(scheduled_at),
            }
    return {}


def published_social_details(entry: object) -> dict:
    """Extract a normalized, dashboard-safe publish payload from a social entry."""
    if not isinstance(entry, dict):
        return {}
    for key in ("postedAt", "posted_at", "publishedAt", "published_at"):
        published_at = normalize_scheduled_social_time(entry.get(key))
        if published_at:
            return {
                "published_at": published_at,
                "published_label": localized_scheduled_social_label(published_at),
            }
    return {}


def project_social_status(project_dir: Path, *, now: datetime | None = None) -> dict:
    """Return the dashboard social status using a UTC-aware current time.

    A scheduled entry becomes published for dashboard purposes when its due time
    is reached, even if its upload worker has not yet recorded a URL or post ID,
    unless the worker has explicitly recorded a failure.
    """
    metadata = read_project_upload_metadata(project_dir)
    social = metadata.get("social", {}) if isinstance(metadata.get("social"), dict) else {}
    current_time = now if now is not None else datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)
    published_platforms: list[tuple[str, dict]] = []
    draft_platforms: list[tuple[str, dict]] = []
    failed_platforms: list[tuple[str, dict]] = []
    scheduled_platforms: list[tuple[str, dict]] = []
    for platform, label in (("youtube", "YouTube"), ("facebook", "Facebook"), ("instagram", "Instagram"), ("tiktok", "TikTok"), ("threads", "Threads"), ("binance", "Binance Square")):
        entry = social.get(platform)
        entry_state = str(entry.get("state") or "").strip().upper() if isinstance(entry, dict) else ""
        if entry_state == "FAILED":
            failed_platforms.append((label, entry))
            continue
        schedule = scheduled_social_details(entry)
        if schedule:
            scheduled_time = datetime.fromisoformat(schedule["scheduled_at"].replace("Z", "+00:00"))
            if scheduled_time > current_time:
                scheduled_platforms.append((label, schedule))
            else:
                published_platforms.append((label, {
                    "published_at": schedule["scheduled_at"],
                    "published_label": schedule["scheduled_label"],
                }))
            continue
        if isinstance(entry, dict) and str(entry.get("state") or "").strip().upper() in {"DRAFT", "INBOX"}:
            draft_platforms.append((label, entry))
            continue
        if isinstance(entry, dict) and any(str(entry.get(key) or "").strip() for key in ("url", "videoId", "postId")):
            published_platforms.append((label, published_social_details(entry)))
    if scheduled_platforms:
        scheduled_platforms.sort(key=lambda item: item[1]["scheduled_at"])
        next_label, next_schedule = scheduled_platforms[0]
        platforms = [label for label, _ in scheduled_platforms] + [label for label, _ in published_platforms]
        failure_labels = [label for label, _ in failed_platforms]
        failure_details = next((entry for _, entry in failed_platforms if entry.get("error")), {})
        failure_error = str(failure_details.get("error") or "").strip()
        title = f"{next_label} · {next_schedule['scheduled_label']}"
        if failure_labels:
            title = f"{title} · Lỗi: {' · '.join(failure_labels)}"
            if failure_error:
                title = f"{title}: {failure_error[:240]}"
        return {
            "posted": bool(published_platforms),
            "scheduled": True,
            "state": "scheduled",
            "label": "Đã lên lịch · Có lỗi" if failure_labels else "Đã lên lịch",
            "title": title,
            "platforms": platforms,
            "scheduled_platforms": [label for label, _ in scheduled_platforms],
            "drafted": bool(draft_platforms),
            "draft_platforms": [label for label, _ in draft_platforms],
            "failed": bool(failed_platforms),
            "failed_platforms": [label for label, _ in failed_platforms],
            **next_schedule,
        }
    if not published_platforms:
        if failed_platforms:
            failed_labels = [label for label, _ in failed_platforms]
            failure_details = next((entry for _, entry in failed_platforms if entry.get("error")), {})
            error = str(failure_details.get("error") or "").strip()
            title = f"{' · '.join(failed_labels)} · Lỗi đăng theo lịch"
            if error:
                title = f"{title}: {error[:240]}"
            return {
                "posted": False,
                "drafted": bool(draft_platforms),
                "failed": True,
                "scheduled": False,
                "state": "failed",
                "label": "Lỗi đăng",
                "title": title,
                "platforms": [],
                "draft_platforms": [label for label, _ in draft_platforms],
                "failed_platforms": failed_labels,
                "scheduled_at": "",
                "scheduled_label": "",
                "error": error,
            }
        if draft_platforms:
            draft_labels = [label for label, _ in draft_platforms]
            return {
                "posted": False,
                "drafted": True,
                "failed": False,
                "scheduled": False,
                "state": "draft",
                "label": "Draft",
                "title": f"{' · '.join(draft_labels)} · Creator Inbox",
                "platforms": [],
                "draft_platforms": draft_labels,
                "scheduled_at": "",
                "scheduled_label": "",
            }
        return {
            "posted": False,
            "drafted": False,
            "failed": False,
            "scheduled": False,
            "state": "pending",
            "label": "Pending",
            "title": "Chưa có video nào được đăng social",
            "platforms": [],
            "scheduled_at": "",
            "scheduled_label": "",
        }
    return {
        "posted": True,
        "drafted": bool(draft_platforms),
        "failed": bool(failed_platforms),
        "scheduled": False,
        "state": "mixed" if (draft_platforms or failed_platforms) else "complete",
        "label": "Published + Draft + Failed" if (draft_platforms and failed_platforms) else ("Published + Failed" if failed_platforms else ("Published + Draft" if draft_platforms else "Published")),
        "title": " · ".join(label for label, _ in published_platforms)
        + (f" · Draft: {' · '.join(label for label, _ in draft_platforms)}" if draft_platforms else "")
        + (f" · Lỗi: {' · '.join(label for label, _ in failed_platforms)}" if failed_platforms else ""),
        "platforms": [label for label, _ in published_platforms],
        "draft_platforms": [label for label, _ in draft_platforms],
        "failed_platforms": [label for label, _ in failed_platforms],
        **next((details for _, details in published_platforms if details), {}),
        "scheduled_at": "",
        "scheduled_label": "",
    }


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
    instagram = metadata.get("instagram", {}) if isinstance(metadata.get("instagram"), dict) else {}
    tiktok = metadata.get("tiktok", {}) if isinstance(metadata.get("tiktok"), dict) else {}
    binance = metadata.get("binance", {}) if isinstance(metadata.get("binance"), dict) else {}
    title = str(youtube.get("title") or "").strip()
    description = str(youtube.get("description") or "").strip()
    caption = str(facebook.get("caption") or "").strip()
    instagram_caption = str(instagram.get("caption") or "").strip()
    tiktok_caption = str(tiktok.get("caption") or "").strip()
    binance_caption = str(binance.get("caption") or "").strip()
    publish = metadata.get("publish", {}) if isinstance(metadata.get("publish"), dict) else {}
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
        if not instagram_caption or instagram_caption in {vi["instagramCaption"], en["instagramCaption"]}:
            instagram_caption = en["instagramCaption"]
        if not tiktok_caption or tiktok_caption in {vi["instagramCaption"], en["instagramCaption"]}:
            tiktok_caption = en["instagramCaption"]
        if not binance_caption or binance_caption in {vi["binanceCaption"], en["binanceCaption"]}:
            binance_caption = en["binanceCaption"]
        tags = en["tags"]
        source_prefix = en["sourcePrefix"]
    else:
        if not title or title in {vi["title"], en["title"]}:
            title = vi["title"]
        if not description or description in {vi["description"], en["description"]}:
            description = vi["description"]
        if not caption or caption in {vi["facebookCaption"], en["facebookCaption"]}:
            caption = vi["facebookCaption"]
        if not instagram_caption or instagram_caption in {vi["instagramCaption"], en["instagramCaption"]}:
            instagram_caption = vi["instagramCaption"]
        if not tiktok_caption or tiktok_caption in {vi["instagramCaption"], en["instagramCaption"]}:
            tiktok_caption = vi["instagramCaption"]
        if not binance_caption or binance_caption in {vi["binanceCaption"], en["binanceCaption"]}:
            binance_caption = vi["binanceCaption"]
        tags = vi["tags"]
        source_prefix = vi["sourcePrefix"]
    binance_tags = list(DEFAULT_BINANCE_TAGS)
    publish_brand = canonical_brand(publish.get("brand") or project_brand_from_topic(project_dir))
    publish_caption = str(publish.get("caption") or caption).strip()
    return {
        "project": project_dir.name,
        "title": title,
        "description": description,
        "youtubeDescription": description,
        "facebookCaption": caption,
        "instagramCaption": instagram_caption,
        "tiktokCaption": tiktok_caption,
        "binanceCaption": binance_caption,
        "binanceTags": binance_tags,
        "facebookVideoState": str(facebook.get("videoState") or "PUBLISHED").upper(),
        "facebookSourceComment": str(facebook.get("sourceComment") or (f"{source_prefix}{source_url}" if source_url else "")),
        "source_url": source_url,
        "privacyStatus": str(youtube.get("privacyStatus") or "public"),
        "tags": tags,
        "publish": {
            "schemaVersion": 1,
            "brand": publish_brand,
            "caption": publish_caption,
        },
        "upload_metadata_url": f"/project/{quote(project_dir.name)}/upload-metadata.json",
        "upload_metadata_exists": upload_metadata_path(project_dir).exists(),
        "video_url": final_video_url(project_dir.name),
        "durationSeconds": video_duration_seconds(project_dir.name),
    }
