#!/usr/bin/env python3
"""Local web UI for previewing projects and starting render jobs."""

from __future__ import annotations

import argparse
import ast
import base64
from collections import deque
import html
import io
import json
import os
import re
import shutil
import shlex
import signal
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import uuid
import webbrowser
import zipfile
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from social_upload import (
    affiliate_brand_context,
    affiliate_overview,
    create_affiliate_link,
    discover_products,
    ingest_conversion_rows,
    list_saved_products,
    save_affiliate_settings,
    shopee_status,
    update_shopee_config,
    disconnect_shopee,
    binance_config,
    binance_upload_video,
    build_upload_metadata,
    disconnect_binance,
    disconnect_instagram,
    facebook_comment_source,
    facebook_upload_video,
    finish_youtube_oauth,
    instagram_upload_video,
    r2_config,
    publish_instagram_facebook_threads,
    threads_upload_video,
    tiktok_upload_video,
    update_zernio_config,
    disconnect_zernio,
    set_facebook_active_page,
    set_youtube_active_channel,
    social_status,
    start_youtube_oauth,
    update_binance_config,
    update_facebook_page_config,
    update_instagram_config,
    update_r2_config,
    update_threads_config,
    disconnect_threads,
    update_youtube_oauth_config,
    youtube_upload_video,
)
import social_upload.metadata as social_metadata
from social_upload.config import (
    SOCIAL_ROUTE_PLATFORMS,
    canonical_brand,
    read_social_config,
    save_social_brand_route,
    write_social_config,
)
from social_upload.r2 import merge_r2_config_values, resolve_r2_config
from social_upload.scheduler import start_scheduler
from social_upload.affiliate_poc import (
    CASES as AFFILIATE_POC_CASE_CODES,
    STATUSES as AFFILIATE_POC_STATUSES,
    case_definitions as affiliate_poc_case_definitions,
    poc_summary as affiliate_poc_summary,
    record_result as record_affiliate_poc_result,
    start_run as start_affiliate_poc_run,
)
import m3_backend as m3
from tts.elevenlabs import (
    elevenlabs_api_key,
    elevenlabs_config,
    elevenlabs_public_config,
    elevenlabs_voice_id,
    update_elevenlabs_api_key,
    update_elevenlabs_voice_id,
)
from tts.maziao import _submit_and_poll_single, _resolve_api_config, _resolve_voice, DEFAULT_API_KEY, DEFAULT_API_BASE, normalize_tts_mode
from media_probe import validate_rendered_video
from tools.render_quality import get_render_profile


if sys.platform.startswith("win"):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


REPO_ROOT = Path(__file__).resolve().parent
USER_DATA_ROOT = Path(os.environ.get("AUREX_DATA_ROOT") or REPO_ROOT).expanduser().resolve()
BOOTSTRAP_DATA_ROOT = Path(
    os.environ.get("AUREX_BOOTSTRAP_DATA_ROOT") or USER_DATA_ROOT
).expanduser().resolve()
BOOTSTRAP_SETTINGS_PATH = BOOTSTRAP_DATA_ROOT / "bootstrap-settings.json"
NATIVE_COMMAND_PATH = BOOTSTRAP_DATA_ROOT / "native-command.json"
NATIVE_REQUEST_LOCK = threading.Lock()
DEFAULT_UI_LANGUAGE = "vi" if os.environ.get("AUREXVIDEO_UI_LANGUAGE") == "vi" else "en"
APP_VERSION = "0.2.4"
UPDATE_MANIFEST_PATH = REPO_ROOT / "update-manifest.json"
# Central update manifest (GitHub raw) — anyone can fetch the latest release
# metadata from here. Falls back to the local update-manifest.json if offline.
UPDATE_MANIFEST_URL = os.environ.get(
    "AUREX_UPDATE_MANIFEST_URL",
    "https://raw.githubusercontent.com/truongtungminh/aurex-updates/main/update-manifest.json",
)
DEFAULT_PROJECT_ROOT = USER_DATA_ROOT / "project"
PROJECT_ROOT = DEFAULT_PROJECT_ROOT
SOURCE_ROOT_IS_PROJECT = False
# Keep the venv launcher path as-is. On macOS Homebrew, `.venv/bin/python` is a
# symlink into the framework binary; `.resolve()` would drop out of the venv and
# render with a bare interpreter (no Pillow / Whisper / Playwright).
VENV_PYTHON = Path(
    os.environ.get("AUREX_PYTHON")
    or REPO_ROOT / ".venv" / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
).expanduser()
RENDER_PYTHON = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
OCR_TOOL_PATH = REPO_ROOT / "tools" / "ocr_universal_deepseek2.py"
VENV_ROOT = (REPO_ROOT / ".venv").resolve()
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4173
PACKAGED_ENGINE_MARKER_NAME = ".aurexvideo-packaged-engine"
EMBEDDED_DESKTOP_ENV = "AUREXVIDEO_EMBEDDED_DESKTOP"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_LOG_CHARS = 240_000
AUDIO_UPLOAD_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".webm", ".flac"}
VOICEOVER_UPLOAD_EXTENSIONS = {".mp3", ".wav", ".mav"}
OUTRO_UPLOAD_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
BRAND_LOGO_UPLOAD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"}
MAX_LOGO_UPLOAD_BYTES = 20 * 1024 * 1024
DEFAULT_OUTRO_PATH = REPO_ROOT / "outro.mp4"
DEFAULT_BRAND_LOGO_PATH = REPO_ROOT / "web" / "aurexvideo-logo.png"
BRANDING_CONFIG_PATH = USER_DATA_ROOT / "config" / "branding.json"
BRANDING_ASSET_DIR = USER_DATA_ROOT / "config" / "branding"
UPLOAD_DEFAULTS_CONFIG_PATH = USER_DATA_ROOT / "config" / "upload-defaults.json"
VIENEU_RUNTIME_CONFIG_PATH = USER_DATA_ROOT / "config" / "vieneu-runtime.json"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
RENDER_QUEUE: deque[str] = deque()
RENDER_QUEUE_WORKER_ACTIVE = False
ACTIVE_JOB_STATUSES = {"authorizing", "queued", "running", "cancelling"}
PRIVATE_JOB_FIELDS = {
    "process",
    "trial_event_id",
    "queued_command",
    "allowance_finalized",
    "allowance_finalizing",
}
SLIDE_AUDIO_SETTING_KEYS = {
    "transitionSounds": "slideTransitions",
    "revealSounds": "slideReveals",
}


ANSI_ENABLED = os.environ.get("NO_COLOR") is None and (sys.stdout.isatty() or sys.stderr.isatty())
ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "green": "\033[32m",
    "cyan": "\033[36m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "magenta": "\033[35m",
    "blue": "\033[34m",
    "underline": "\033[4m",
} if ANSI_ENABLED else {key: "" for key in ["reset", "bold", "dim", "green", "cyan", "yellow", "red", "magenta", "blue", "underline"]}


def color_text(text: object, *styles: str) -> str:
    return "".join(ANSI.get(style, "") for style in styles) + str(text) + ANSI["reset"]


def json_dumps(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def current_ui_language() -> str:
    try:
        payload = json.loads(BOOTSTRAP_SETTINGS_PATH.read_text(encoding="utf-8"))
        language = str(payload.get("language") or "").strip().lower()
        if language in {"en", "vi"}:
            return language
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return DEFAULT_UI_LANGUAGE


def normalize_upload_tags(value: object) -> list[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[,\n]", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    tags: list[str] = []
    seen: set[str] = set()
    for raw_item in raw_items:
        tag = re.sub(r"\s+", " ", str(raw_item or "").strip().lstrip("#")).strip()
        if not tag:
            continue
        if len(tag) > 50:
            raise ValueError("Mỗi tag tối đa 50 ký tự.")
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
        if len(tags) > 30:
            raise ValueError("Tối đa 30 tag mặc định.")
    if not tags:
        raise ValueError("Cần ít nhất một tag mặc định.")
    return tags


def replace_trailing_hashtag_block(value: object, tags: list[str]) -> str:
    lines = str(value or "").rstrip().splitlines()
    while lines:
        stripped = lines[-1].strip()
        if not stripped or all(part.startswith("#") for part in stripped.split()):
            lines.pop()
            continue
        break
    hashtag_block = " ".join(f"#{tag}" for tag in tags)
    body = "\n".join(lines).rstrip()
    return f"{body}\n{hashtag_block}" if body else hashtag_block


def upload_tags_from_caption(value: object) -> list[str]:
    return normalize_upload_tags(re.findall(r"(?<!\w)#([\w-]+)", str(value or ""), flags=re.UNICODE))


def upload_title_from_caption(value: object) -> str:
    first_line = next((line.strip() for line in str(value or "").splitlines() if line.strip()), "")
    first_line = re.sub(r"^[^\wÀ-ỹ]+", "", first_line, flags=re.UNICODE).strip()
    if not first_line:
        raise ValueError("Dòng đầu caption phải có nội dung để làm tiêu đề YouTube.")
    return first_line[:100].rstrip()


def read_default_upload_copy(language: object = None) -> dict:
    normalized_language = "vi" if str(language or current_ui_language()).lower() == "vi" else "en"
    defaults = social_metadata.default_upload_copy(normalized_language)
    stored: dict = {}
    if UPLOAD_DEFAULTS_CONFIG_PATH.is_file():
        try:
            parsed = json.loads(UPLOAD_DEFAULTS_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                stored = parsed
        except (OSError, json.JSONDecodeError):
            stored = {}
    candidate = stored.get(normalized_language)
    candidate = candidate if isinstance(candidate, dict) else {}
    candidate_caption = str(
        candidate.get("caption")
        or candidate.get("facebookCaption")
        or candidate.get("instagramCaption")
        or candidate.get("youtubeDescription")
        or ""
    ).strip()
    caption = candidate_caption or str(defaults["facebookCaption"]).strip()
    try:
        stored_tags = normalize_upload_tags(candidate.get("tags"))
    except ValueError:
        stored_tags = normalize_upload_tags(defaults.get("tags") or [])
    if not candidate_caption and candidate.get("tags"):
        tags = stored_tags
        caption = replace_trailing_hashtag_block(caption, tags)
    else:
        try:
            tags = upload_tags_from_caption(caption)
        except ValueError:
            tags = stored_tags
            caption = replace_trailing_hashtag_block(caption, tags)
    return {
        "language": normalized_language,
        "tags": tags,
        "title": upload_title_from_caption(caption),
        "caption": caption,
        "youtubeDescription": caption,
        "facebookCaption": caption,
        "instagramCaption": caption,
    }


def read_default_upload_tags(language: object = None) -> list[str]:
    return read_default_upload_copy(language)["tags"]


def save_default_upload_tags(payload: dict) -> dict:
    language = "vi" if str(payload.get("language") or current_ui_language()).lower() == "vi" else "en"
    current = read_default_upload_copy(language)
    caption = str(
        payload.get("caption")
        or payload.get("facebookCaption")
        or payload.get("youtubeDescription")
        or current["caption"]
    ).strip()
    if not caption:
        raise ValueError("Caption mặc định không được để trống.")
    if len(caption) > 5000:
        raise ValueError("Caption mặc định tối đa 5.000 ký tự.")
    tags = upload_tags_from_caption(caption)
    title = upload_title_from_caption(caption)
    stored: dict = {}
    if UPLOAD_DEFAULTS_CONFIG_PATH.is_file():
        try:
            parsed = json.loads(UPLOAD_DEFAULTS_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                stored = parsed
        except (OSError, json.JSONDecodeError):
            stored = {}
    stored[language] = {
        "caption": caption,
        "tags": tags,
        "youtubeDescription": caption,
        "facebookCaption": caption,
        "instagramCaption": caption,
    }
    UPLOAD_DEFAULTS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = UPLOAD_DEFAULTS_CONFIG_PATH.with_name(
        f".{UPLOAD_DEFAULTS_CONFIG_PATH.name}.{uuid.uuid4().hex}.tmp"
    )
    temporary.write_bytes(json_dumps(stored) + b"\n")
    temporary.replace(UPLOAD_DEFAULTS_CONFIG_PATH)
    return {
        "ok": True,
        "language": language,
        "tags": tags,
        "title": title,
        "caption": caption,
        "youtubeDescription": caption,
        "facebookCaption": caption,
        "instagramCaption": caption,
    }


def current_account() -> dict:
    try:
        payload = json.loads(BOOTSTRAP_SETTINGS_PATH.read_text(encoding="utf-8"))
        account = payload.get("account")
        if isinstance(account, dict):
            return account
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return {}


def embedded_desktop_mode_enabled() -> bool:
    """Keep embedded desktop launches from opening a separate browser window."""
    return os.environ.get(EMBEDDED_DESKTOP_ENV) == "1" or os.environ.get("AUREXVIDEO_DESKTOP") == "1"



def entitlement_is_active_pro(entitlement: dict | None) -> bool:
    # Desktop app (Swift shell) is always full-featured: no trial, no Pro gating.
    # AUREXVIDEO_DESKTOP is set by the launcher, so local builds are fully unlocked.
    if os.environ.get("AUREXVIDEO_DESKTOP"):
        return True
    entitlement = entitlement if isinstance(entitlement, dict) else {}
    plan = str(entitlement.get("product_id") or entitlement.get("plan") or "").strip().lower()
    if "pro" not in plan or "trial" in plan:
        return False
    if str(entitlement.get("status") or "").strip().lower() != "active":
        return False
    valid_until = str(entitlement.get("valid_until") or "").strip()
    if not valid_until:
        return True
    try:
        expires_at = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc)
    except ValueError:
        return False


def trial_branding_required(account: dict | None = None) -> bool:
    # Desktop app is fully unlocked — never show trial branding.
    if os.environ.get("AUREXVIDEO_DESKTOP"):
        return False
    account = account if isinstance(account, dict) else current_account()
    entitlement = account.get("entitlement") if isinstance(account.get("entitlement"), dict) else {}
    return not entitlement_is_active_pro(entitlement)


def save_ui_language(language: object) -> str:
    normalized = str(language or "").strip().lower()
    if normalized not in {"en", "vi"}:
        raise ValueError("Unsupported language. Choose 'en' or 'vi'.")
    BOOTSTRAP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {}
    try:
        existing = json.loads(BOOTSTRAP_SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(existing, dict):
            payload = existing
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    payload["language"] = normalized
    temporary = BOOTSTRAP_SETTINGS_PATH.with_name(f".{BOOTSTRAP_SETTINGS_PATH.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(json_dumps(payload) + b"\n")
    temporary.replace(BOOTSTRAP_SETTINGS_PATH)
    return normalized


def read_vieneu_runtime_config() -> dict:
    """Return the persisted VieNeu runtime preference.

    VieNeu is enabled by default for existing installations.  The native
    shell reads the same small config file so the web toggle can control
    startup without coupling the web page to a platform-specific process API.
    """
    try:
        payload = json.loads(VIENEU_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return {"enabled": payload.get("enabled") is not False}
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return {"enabled": True}


def save_vieneu_runtime_config(enabled: object) -> dict:
    VIENEU_RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"enabled": bool(enabled)}
    temporary = VIENEU_RUNTIME_CONFIG_PATH.with_name(
        f".{VIENEU_RUNTIME_CONFIG_PATH.name}.{uuid.uuid4().hex}.tmp"
    )
    temporary.write_bytes(json_dumps(payload) + b"\n")
    temporary.replace(VIENEU_RUNTIME_CONFIG_PATH)
    return payload


def request_native_command(command: str, **payload: object) -> None:
    NATIVE_COMMAND_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = NATIVE_COMMAND_PATH.with_name(f".{NATIVE_COMMAND_PATH.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(json_dumps({"command": command, **payload}) + b"\n")
    temporary.replace(NATIVE_COMMAND_PATH)


def request_native_response(command: str, timeout: float = 20, **payload: object) -> dict:
    if os.environ.get("AUREX_NATIVE_BRIDGE") != "1":
        raise RuntimeError("Aurex desktop bridge is not available.")
    request_id = str(uuid.uuid4())
    response_path = BOOTSTRAP_DATA_ROOT / f"native-response-{request_id}.json"
    deadline = time.monotonic() + timeout
    with NATIVE_REQUEST_LOCK:
        response_path.unlink(missing_ok=True)
        request_native_command(command, requestId=request_id, **payload)
        while time.monotonic() < deadline:
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
                response_path.unlink(missing_ok=True)
                if not isinstance(response, dict):
                    raise RuntimeError("Aurex desktop bridge returned an invalid response.")
                if not response.get("ok"):
                    raise RuntimeError(str(response.get("error") or "Aurex could not verify your export allowance."))
                return response
            except FileNotFoundError:
                time.sleep(0.1)
            except json.JSONDecodeError:
                time.sleep(0.05)
        response_path.unlink(missing_ok=True)
        raise RuntimeError("Aurex could not verify your export allowance. Check your internet connection and try again.")


def entitlement_trial_usage(account: dict | None = None) -> tuple[bool, int, int]:
    account = account if isinstance(account, dict) else current_account()
    entitlement = account.get("entitlement") if isinstance(account.get("entitlement"), dict) else {}
    plan = str(entitlement.get("product_id") or entitlement.get("plan") or "").strip().lower()
    try:
        limit = max(0, int(entitlement.get("trial_export_limit", entitlement.get("trial_exports_limit", 3))))
    except (TypeError, ValueError):
        limit = 3
    try:
        used = max(0, int(entitlement.get("trial_exports_used", entitlement.get("exports_used", 0))))
    except (TypeError, ValueError):
        used = 0
    return "trial" in plan, used, limit


def local_development_render_enabled() -> bool:
    return (
        os.environ.get("AUREX_DEV_MODE") == "1"
        and (REPO_ROOT / ".git").exists()
        and not (REPO_ROOT / PACKAGED_ENGINE_MARKER_NAME).exists()
    )


def reserve_trial_export(project: str) -> tuple[str | None, dict | None]:
    # Desktop app is fully unlocked — no licensing/export limits.
    if os.environ.get("AUREXVIDEO_DESKTOP"):
        return None, None
    if os.environ.get("AUREX_NATIVE_BRIDGE") != "1":
        if local_development_render_enabled():
            return None, None
        if current_ui_language() == "vi":
            raise RuntimeError(
                "Không thể xác thực quyền xuất video. Hãy đóng bản local và mở lại AurexVideo chính thức."
            )
        raise RuntimeError(
            "Aurex could not authorize this export. Close the local copy and reopen the official Aurex app."
        )
    event_id = str(uuid.uuid4())
    response = request_native_response(
        "license-export",
        timeout=50,
        action="reserve",
        eventId=event_id,
        projectSlug=project,
    )
    entitlement = response.get("entitlement")
    if not isinstance(entitlement, dict):
        raise RuntimeError("AurexVideo licensing did not return an entitlement.")
    return str(response.get("event_id") or event_id), entitlement


def finish_trial_export(event_id: str | None, status: str) -> None:
    if not event_id or os.environ.get("AUREX_NATIVE_BRIDGE") != "1":
        return
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request_native_response(
                "license-export",
                timeout=50,
                action="finish",
                eventId=event_id,
                status=status,
            )
            return
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(str(last_error or "Could not finish export allowance."))


def inject_ui_language(data: bytes) -> bytes:
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    language = current_ui_language()
    source = re.sub(r'<html\s+lang="[^"]*"', f'<html lang="{language}"', source, count=1, flags=re.IGNORECASE)
    assignment = f"window.__AUREX_LANGUAGE__={json.dumps(language)}"
    if "window.__AUREX_LANGUAGE__=" not in source:
        bootstrap = (
            f"<script>{assignment};window.__AUREX_TRIAL__={json.dumps(trial_branding_required())};</script>"
            '<script src="/webui/i18n.js?v=20260726-bgm-no-toggle"></script>'
        )
        if "</head>" in source:
            source = source.replace("</head>", f"  {bootstrap}\n</head>", 1)
        else:
            source = bootstrap + source
    else:
        source = re.sub(
            r"window\.__AUREX_LANGUAGE__\s*=\s*['\"][^'\"]*['\"]",
            assignment,
            source,
            count=1,
        )
        if "/webui/i18n.js" not in source:
            source = source.replace(
                "</head>",
                '  <script src="/webui/i18n.js?v=20260726-bgm-no-toggle"></script>\n</head>',
                1,
            )
    return source.encode("utf-8")


def configure_source_root(path: str | Path | None) -> None:
    global PROJECT_ROOT, SOURCE_ROOT_IS_PROJECT
    raw_path = Path(path).expanduser() if path else DEFAULT_PROJECT_ROOT
    source_root = raw_path.resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"Source root not found: {source_root}")

    PROJECT_ROOT = source_root
    SOURCE_ROOT_IS_PROJECT = (PROJECT_ROOT / "topic.json").is_file()
    social_metadata.PROJECT_ROOT = project_lookup_root()
    m3.PROJECTS_ROOT = project_lookup_root()


def project_lookup_root() -> Path:
    if SOURCE_ROOT_IS_PROJECT:
        return PROJECT_ROOT.parent
    if PROJECT_ROOT.name == "engine" and (USER_DATA_ROOT / "project").is_dir():
        return USER_DATA_ROOT / "project"
    return PROJECT_ROOT


def source_root_mode() -> str:
    return "single-project" if SOURCE_ROOT_IS_PROJECT else "collection"


def source_root_mode_for(path: Path) -> str:
    return "single-project" if (path / "topic.json").is_file() else "collection"


def source_root_project_count(path: Path) -> int:
    if not path.is_dir():
        return 0
    if (path / "topic.json").is_file():
        return 1
    return sum(1 for child in path.iterdir() if child.is_dir() and (child / "topic.json").is_file())


def source_root_candidates() -> list[tuple[str, Path]]:
    raw_candidates = [
        ("Dự án AurexVideo", DEFAULT_PROJECT_ROOT),
        ("Current", PROJECT_ROOT),
    ]
    cwd = Path.cwd().resolve()
    if cwd != REPO_ROOT and (cwd / "topic.json").is_file():
        raw_candidates.append(("Current working folder", cwd))

    seen: set[Path] = set()
    candidates: list[tuple[str, Path]] = []
    for label, path in raw_candidates:
        resolved = path.expanduser().resolve()
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        candidates.append((label, resolved))
    return candidates


def source_root_option_payload() -> list[dict]:
    return [
        {
            "label": label,
            "path": str(path),
            "mode": source_root_mode_for(path),
            "projects": source_root_project_count(path),
            "active": path == PROJECT_ROOT,
        }
        for label, path in source_root_candidates()
    ]


def iter_project_dirs() -> list[Path]:
    root = project_lookup_root()
    if not root.exists():
        return []
    if SOURCE_ROOT_IS_PROJECT:
        return [PROJECT_ROOT]
    return [path for path in root.iterdir() if path.is_dir()]


def validate_project_name(project: str) -> str:
    project = unquote(str(project or "")).strip()
    if not project or project in {".", ".."} or "/" in project or "\\" in project or "\x00" in project:
        raise ValueError("Invalid project name.")
    return project


def project_url(project: str) -> str:
    return f"/project/{quote(project)}/"


def source_root_response() -> dict:
    return {
        "source_root": str(PROJECT_ROOT),
        "source_mode": source_root_mode(),
        "options": source_root_option_payload(),
        "projects": list_projects(),
    }


def has_active_jobs() -> bool:
    with JOBS_LOCK:
        return any(
            job.get("status") in ACTIVE_JOB_STATUSES
            for job in JOBS.values()
        )


def choose_source_root_dialog() -> Path:
    if sys.platform == "darwin":
        script = 'POSIX path of (choose folder with prompt "Chọn thư mục dự án AurexVideo")'
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout or "").strip()
            if "User canceled" in message or proc.returncode == 1:
                raise RuntimeError("Đã huỷ chọn folder.")
            raise RuntimeError(message or "Không mở được Finder để chọn folder.")
        selected = proc.stdout.strip()
        if not selected:
            raise RuntimeError("Chưa chọn folder.")
        return Path(selected)

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("Máy này không hỗ trợ hộp chọn folder native.") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        selected = filedialog.askdirectory(title="Chọn thư mục dự án AurexVideo")
    finally:
        root.destroy()
    if not selected:
        raise RuntimeError("Đã huỷ chọn folder.")
    return Path(selected)


def list_projects() -> list[dict]:
    if not project_lookup_root().exists():
        return []

    def project_sort_key(path: Path) -> tuple[float, str]:
        try:
            updated_at = path.stat().st_mtime
        except OSError:
            updated_at = 0
        return (-updated_at, path.name.lower())

    projects = []
    project_dirs = sorted(iter_project_dirs(), key=project_sort_key)
    for project_dir in project_dirs:
        if not project_dir.is_dir():
            continue
        if not (project_dir / "topic.json").exists():
            continue
        script_path = project_dir / "script.txt"
        output_dir = project_dir / "output"
        final_video = project_dir / "output" / "final_video.mp4"
        has_output = output_dir.is_dir() and any(output_dir.iterdir())
        output_url = final_video_url(project_dir.name)
        brand = social_metadata.project_brand_from_topic(project_dir)
        character_id = ""
        try:
            topic = json.loads((project_dir / "topic.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            topic = {}
        if isinstance(topic, dict):
            character_id = str(
                topic.get("characterId")
                or topic.get("character_id")
                or topic.get("character")
                or ""
            ).strip()
        social_status = social_metadata.project_social_status(project_dir)
        projects.append(
            {
                "name": project_dir.name,
                "url": project_url(project_dir.name),
                "source_path": str(project_dir),
                "has_script": script_path.exists(),
                "script_count": len(
                    [line for line in script_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                )
                if script_path.exists()
                else 0,
                "has_output": has_output,
                "output_url": output_url,
                "video_url": output_url if final_video.exists() else None,
                "brand": brand,
                "character": character_id,
                "social_status": social_status["label"],
                "social_status_class": "bad" if social_status.get("failed") else ("warn" if (social_status.get("scheduled") or social_status.get("drafted")) else ("ok" if social_status.get("posted") else "bad")),
                "social_status_title": social_status["title"],
                "social_status_detail": social_status,
                "social_status_scheduled_at": social_status.get("scheduled_at", ""),
                "social_status_scheduled_label": social_status.get("scheduled_label", ""),
                "social_status_published_at": social_status.get("published_at", ""),
                "social_status_published_label": social_status.get("published_label", ""),
            }
        )
    return projects


def upload_brand_context(project: str = "") -> dict:
    """Build the non-secret Brand + social destination context for Upload."""
    status = social_status()
    config = read_social_config()
    routes = status.get("brand_route_records") or {}
    brand_display: dict[str, str] = {}
    project_counts: dict[str, int] = {}
    project_brand = ""

    for project_dir in iter_project_dirs():
        topic_path = project_dir / "topic.json"
        if not topic_path.is_file():
            continue
        try:
            topic = json.loads(topic_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(topic, dict):
            continue
        raw_brand = str(topic.get("brand") or "").strip()
        brand = canonical_brand(raw_brand)
        if not brand:
            continue
        display_name = brand if raw_brand.casefold() != brand else raw_brand
        brand_display.setdefault(brand, display_name)
        project_counts[brand] = project_counts.get(brand, 0) + 1
        if project and project_dir.name == project:
            project_brand = brand

    for brand, platform_routes in routes.items():
        brand_display.setdefault(brand, brand)

    platforms = status.get("platforms") or {}
    safe_platforms: dict[str, dict] = {}
    for platform, value in platforms.items():
        if not isinstance(value, dict):
            continue
        # Keep only fields needed by the picker. In particular, never pass
        # credential-bearing config values through this endpoint.
        safe = {
            key: value.get(key)
            for key in (
                "configured", "connected", "available", "ready", "message",
                "display_name", "name", "ig_user_id", "threads_user_id",
                "account_id", "active_channel_id", "active_page_id", "channel", "page",
                "channels", "pages", "masked_api_key", "masked_secret", "app_id", "api_base_url",
                "accounts",
            )
            if key in value
        }
        safe_platforms[platform] = safe

    brands = [
        {
            "id": brand,
            "name": brand_display.get(brand) or brand,
            "project_count": project_counts.get(brand, 0),
            "routes": routes.get(brand, {}),
            "affiliate": affiliate_brand_context(config, brand),
        }
        for brand in brand_display
    ]
    brands.sort(key=lambda item: (item["name"].casefold(), item["id"]))
    if project_brand:
        brands.sort(key=lambda item: 0 if item["id"] == project_brand else 1)
    return {
        "project": project,
        "project_brand": project_brand,
        "brands": brands,
        "brand_routes": routes,
        "platforms": safe_platforms,
        "brand_routes_version": status.get("brand_routes_version", 1),
        "affiliate": affiliate_brand_context(config, project_brand) if project_brand else {},
    }


def _affiliate_poc_default_page_id(config: dict, brand: str) -> str:
    routes = config.get("brand_routes") if isinstance(config, dict) else {}
    brand_route = routes.get(brand) if isinstance(routes, dict) else {}
    facebook_route = brand_route.get("facebook") if isinstance(brand_route, dict) else {}
    if not isinstance(facebook_route, dict):
        return ""
    return str(
        facebook_route.get("page_id")
        or facebook_route.get("connection_id")
        or ""
    ).strip()


def _affiliate_poc_idempotency_key(content_id: str, page_id: str) -> str:
    # Keep raw project/Page identifiers out of the POC key while making one
    # content/Page pair idempotent. UUID5 is deterministic and matches the
    # scalar key contract enforced by affiliate_poc.
    seed = f"aurexvideo-affiliate-poc\x00{content_id}\x00{page_id}"
    return f"poc-{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex}"


def _affiliate_poc_cases() -> list[dict]:
    titles = {
        "A": "Manual Reel + manual comment",
        "B": "API Reel + manual comment",
        "C": "Manual Reel + API comment",
        "D": "API Reel + API comment",
    }
    descriptions = {
        "A": "Đăng Reel thủ công · comment thủ công",
        "B": "Đăng Reel qua API · comment thủ công",
        "C": "Đăng Reel thủ công · comment qua API",
        "D": "Đăng Reel và comment đều qua API",
    }
    return [
        {
            "key": definition["caseKey"],
            "case": definition["caseKey"],
            "title": titles[definition["caseKey"]],
            "description": descriptions[definition["caseKey"]],
            "publish_mode": definition["publishMode"],
            "comment_mode": definition["commentMode"],
            **definition,
        }
        for definition in affiliate_poc_case_definitions()
    ]


def _affiliate_poc_case_records(summary: dict, content_id: str, page_id: str) -> list[dict]:
    records = []
    for case in summary.get("cases") or []:
        if not isinstance(case, dict):
            continue
        banner_observed = case.get("bannerObserved")
        records.append({
            **case,
            "id": summary.get("runId"),
            "run_id": summary.get("runId"),
            "brand": summary.get("brand"),
            "content_id": summary.get("contentId") or content_id,
            "page_id": str(case.get("pageId") or page_id or ""),
            "post_id": str(case.get("postId") or ""),
            "comment_id": str(case.get("commentId") or ""),
            "banner_observed": "yes" if banner_observed is True else "no" if banner_observed is False else "",
            "evidence_url": str(case.get("evidenceUrl") or ""),
            "case_key": str(case.get("caseKey") or "").upper(),
            "updated_at": case.get("updatedAt") or summary.get("updatedAt") or "",
        })
    return records


def _affiliate_poc_summary(summary: dict | None = None) -> dict:
    summary = summary if isinstance(summary, dict) else {}
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    normalized = {status: int(counts.get(status) or 0) for status in AFFILIATE_POC_STATUSES}
    return {
        "total": sum(normalized.values()),
        **normalized,
        "status": str(summary.get("status") or "pending"),
    }


def _affiliate_poc_response(brand: str, content_id: str, page_id: str, summary: dict | None = None) -> dict:
    return {
        "ok": True,
        "brand": brand,
        "content_id": content_id,
        "page_id": page_id,
        "cases": _affiliate_poc_cases(),
        "runs": _affiliate_poc_case_records(summary or {}, content_id, page_id),
        "summary": _affiliate_poc_summary(summary),
        "context": {"page_id": page_id, "brand": brand},
    }


def _affiliate_poc_find_summary(brand: str, content_id: str, page_id: str) -> dict:
    if not brand or not content_id:
        return {}
    summary = affiliate_poc_summary(
        brand,
        content_id,
        idempotency_key=_affiliate_poc_idempotency_key(content_id, page_id),
    )
    return summary if summary.get("started") else {}


def _affiliate_poc_text(payload: dict, *keys: str, limit: int) -> str:
    value = ""
    for key in keys:
        if payload.get(key) is not None:
            value = str(payload.get(key) or "").strip()
            break
    if len(value) > limit:
        raise ValueError(f"POC field {keys[0]} tối đa {limit} ký tự.")
    return value


def require_project(project: str) -> Path:
    project = validate_project_name(project)

    if SOURCE_ROOT_IS_PROJECT:
        if project != PROJECT_ROOT.name:
            raise FileNotFoundError(f"Project not found: {project}")
        project_dir = PROJECT_ROOT.resolve()
    else:
        project_dir = (project_lookup_root() / project).resolve()
    try:
        project_dir.relative_to(project_lookup_root().resolve())
    except ValueError as exc:
        raise ValueError("Invalid project path.") from exc

    if not project_dir.is_dir() or not (project_dir / "topic.json").exists():
        raise FileNotFoundError(f"Không tìm thấy dự án: {project}")
    return project_dir


def require_payload_project(payload: dict) -> str:
    project = str(payload.get("project") or "").strip()
    require_project(project)
    return project


def coerce_speed(value: object) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Speed must be a number.") from exc
    if not 0.5 <= speed <= 2.0:
        raise ValueError("Speed must be between 0.5 and 2.0.")
    return speed


def coerce_volume(value: object) -> float:
    try:
        volume = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Volume must be a number.") from exc
    if not 1.0 <= volume <= 3.0:
        raise ValueError("Volume must be between 1.0 and 3.0.")
    return volume


def coerce_render_size(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"720", "720x1280"}:
        return "720x1280"
    if raw in {"", "1080", "1080x1920"}:
        return "1080x1920"
    raise ValueError("Render size must be 1080x1920 or 720x1280.")


def coerce_quality_profile(value: object) -> str:
    """Validate the render quality preset at the API boundary."""
    return get_render_profile(str(value or "standard")).name


def coerce_render_backend(value: object) -> str:
    """Validate and normalize the hybrid native/browser render policy."""
    requested = str(value or os.environ.get("AUREXVIDEO_RENDER_BACKEND") or "auto").strip().lower()
    aliases = {"native-core": "native", "aurex": "native", "compatibility": "browser"}
    requested = aliases.get(requested, requested)
    if requested not in {"browser", "auto", "native"}:
        raise ValueError("Render backend must be browser, auto or native.")
    return requested


def clean_filename(name: str, fallback: str = "voiceover.mp3") -> str:
    cleaned = Path(name or fallback).name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", cleaned).strip("._")
    return (cleaned or fallback)[:96]


def decode_audio_payload(audio: dict, project_dir: Path) -> Path:
    if not isinstance(audio, dict):
        raise ValueError("Missing ElevenLabs audio payload.")

    encoded = str(audio.get("data") or "")
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    if not encoded:
        raise ValueError("Missing ElevenLabs audio data.")

    try:
        audio_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("Audio payload is not valid base64.") from exc

    if not audio_bytes:
        raise ValueError("Uploaded audio is empty.")
    if len(audio_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("Uploaded audio is too large.")

    upload_dir = project_dir / "input_audio"
    upload_dir.mkdir(parents=True, exist_ok=True)
    stamped_name = f"{time.strftime('%Y%m%d-%H%M%S')}_{clean_filename(str(audio.get('name') or 'voiceover.mp3'))}"
    audio_path = upload_dir / stamped_name
    audio_path.write_bytes(audio_bytes)
    return audio_path


def decode_render_asset_payload(
    payload: dict | None,
    project_dir: Path,
    *,
    kind: str,
    allowed_extensions: set[str],
    max_bytes: int,
) -> Path | None:
    if not payload:
        return None
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid {kind} payload.")

    original_name = str(payload.get("name") or "")
    ext = Path(original_name).suffix.lower()
    if ext not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        raise ValueError(f"{kind} file must be one of: {allowed}.")

    encoded = str(payload.get("data") or "")
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    if not encoded:
        raise ValueError(f"Missing {kind} file data.")

    try:
        file_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError(f"{kind} payload is not valid base64.") from exc

    if not file_bytes:
        raise ValueError(f"{kind} file is empty.")
    if len(file_bytes) > max_bytes:
        raise ValueError(f"{kind} file is too large.")

    upload_dir = project_dir / "assets" / "render-options"
    upload_dir.mkdir(parents=True, exist_ok=True)
    output_path = upload_dir / f"{kind}{ext}"
    output_path.write_bytes(file_bytes)
    return output_path


def clean_brand_name(value: object) -> str:
    name = re.sub(r"\s+", " ", str(value or "").strip())
    if len(name) > 64:
        raise ValueError("Brand name must be 64 characters or shorter.")
    return name


def read_branding_config() -> dict:
    stored: dict = {}
    if BRANDING_CONFIG_PATH.is_file():
        try:
            parsed = json.loads(BRANDING_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                stored = parsed
        except (OSError, json.JSONDecodeError):
            stored = {}

    brand_name = clean_brand_name(stored.get("brandName")) or "aurexvideo.app"
    logo_path = None
    raw_logo_path = str(stored.get("logoPath") or "").strip()
    if raw_logo_path:
        candidate = (USER_DATA_ROOT / raw_logo_path).resolve()
        try:
            candidate.relative_to(BRANDING_ASSET_DIR.resolve())
        except ValueError:
            candidate = None
        if candidate and candidate.is_file() and candidate.suffix.lower() in BRAND_LOGO_UPLOAD_EXTENSIONS:
            logo_path = candidate
    if logo_path is None and BRANDING_ASSET_DIR.is_dir():
        logo_path = next(
            (
                path
                for path in sorted(BRANDING_ASSET_DIR.glob("brand-logo.*"))
                if path.is_file() and path.suffix.lower() in BRAND_LOGO_UPLOAD_EXTENSIONS
            ),
            None,
        )

    logo_name = str(stored.get("logoName") or "").strip()
    if logo_path and not logo_name:
        logo_name = logo_path.name
    return {
        "brandName": brand_name,
        "configured": BRANDING_CONFIG_PATH.is_file(),
        "hasLogo": bool(logo_path),
        "logoName": logo_name if logo_path else "",
        "logoPath": logo_path.resolve().relative_to(USER_DATA_ROOT.resolve()).as_posix() if logo_path else "",
    }


def persistent_brand_logo_path() -> Path | None:
    logo_path = str(read_branding_config().get("logoPath") or "").strip()
    return (USER_DATA_ROOT / logo_path).resolve() if logo_path else None


def save_branding_config(payload: dict) -> dict:
    if trial_branding_required():
        raise PermissionError("Tài khoản Trial luôn dùng Logo + brand của Aurex.")
    current = read_branding_config()
    brand_name_value = payload.get("brandName", payload.get("brand_name", current["brandName"]))
    brand_name = clean_brand_name(brand_name_value) or "aurexvideo.app"
    logo_payload = payload.get("logo") or payload.get("brandLogo") or payload.get("brand_logo")
    logo_path = persistent_brand_logo_path()
    logo_name = str(current.get("logoName") or "")

    if logo_payload:
        if not isinstance(logo_payload, dict):
            raise ValueError("Logo payload không hợp lệ.")
        original_name = str(logo_payload.get("name") or "")
        extension = Path(original_name).suffix.lower()
        if extension not in BRAND_LOGO_UPLOAD_EXTENSIONS:
            allowed = ", ".join(sorted(BRAND_LOGO_UPLOAD_EXTENSIONS))
            raise ValueError(f"File logo phải thuộc một trong các định dạng: {allowed}.")
        encoded = str(logo_payload.get("data") or "")
        if "," in encoded:
            encoded = encoded.split(",", 1)[1]
        if not encoded:
            raise ValueError("Thiếu dữ liệu file logo.")
        try:
            file_bytes = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ValueError("Dữ liệu file logo không hợp lệ.") from exc
        if not file_bytes:
            raise ValueError("File logo đang trống.")
        if len(file_bytes) > MAX_LOGO_UPLOAD_BYTES:
            raise ValueError("File logo lớn hơn 20 MB.")

        BRANDING_ASSET_DIR.mkdir(parents=True, exist_ok=True)
        logo_path = BRANDING_ASSET_DIR / f"brand-logo{extension}"
        temporary_logo = BRANDING_ASSET_DIR / f".brand-logo-{uuid.uuid4().hex}{extension}"
        temporary_logo.write_bytes(file_bytes)
        temporary_logo.replace(logo_path)
        for old_logo in BRANDING_ASSET_DIR.glob("brand-logo.*"):
            if old_logo != logo_path and old_logo.is_file():
                old_logo.unlink()
        logo_name = clean_filename(original_name, f"brand-logo{extension}")

    config = {
        "brandName": brand_name,
        "logoName": logo_name if logo_path else "",
        "logoPath": logo_path.resolve().relative_to(USER_DATA_ROOT.resolve()).as_posix() if logo_path else "",
    }
    BRANDING_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_config = BRANDING_CONFIG_PATH.with_name(f".{BRANDING_CONFIG_PATH.name}.{uuid.uuid4().hex}.tmp")
    temporary_config.write_bytes(json_dumps(config) + b"\n")
    temporary_config.replace(BRANDING_CONFIG_PATH)
    return read_branding_config()


def append_render_asset_options(
    cmd: list[str],
    payload: dict,
    project_dir: Path,
    authoritative_entitlement: dict | None = None,
) -> None:
    if bool(payload.get("outro", False)):
        cmd.append("--outro")
        outro_video = decode_render_asset_payload(
            payload.get("outroFile") or payload.get("outro_file"),
            project_dir,
            kind="outro",
            allowed_extensions=OUTRO_UPLOAD_EXTENSIONS,
            max_bytes=MAX_UPLOAD_BYTES,
        )
        if outro_video:
            cmd.extend(["--outro-video", str(outro_video)])
        elif DEFAULT_OUTRO_PATH.is_file():
            cmd.extend(["--outro-video", str(DEFAULT_OUTRO_PATH)])

    branding_account = {"entitlement": authoritative_entitlement} if authoritative_entitlement is not None else None
    if trial_branding_required(branding_account):
        if DEFAULT_BRAND_LOGO_PATH.is_file():
            cmd.extend(["--brand-logo", str(DEFAULT_BRAND_LOGO_PATH)])
        cmd.extend(["--brand-name", "aurexvideo.app"])
        return

    if not bool(payload.get("branding", True)):
        cmd.append("--no-branding")
        return

    brand_logo = decode_render_asset_payload(
        payload.get("brandLogo") or payload.get("brand_logo"),
        project_dir,
        kind="brand-logo",
        allowed_extensions=BRAND_LOGO_UPLOAD_EXTENSIONS,
        max_bytes=MAX_LOGO_UPLOAD_BYTES,
    )
    saved_branding = read_branding_config()
    saved_logo_path = str(saved_branding.get("logoPath") or "").strip()
    saved_logo = (USER_DATA_ROOT / saved_logo_path).resolve() if saved_logo_path else None
    if brand_logo:
        cmd.extend(["--brand-logo", str(brand_logo)])
    elif saved_logo and saved_logo.is_file():
        cmd.extend(["--brand-logo", str(saved_logo)])
    elif DEFAULT_BRAND_LOGO_PATH.is_file():
        cmd.extend(["--brand-logo", str(DEFAULT_BRAND_LOGO_PATH)])
    brand_name = clean_brand_name(payload.get("brandName") or payload.get("brand_name")) or saved_branding["brandName"]
    if brand_name:
        cmd.extend(["--brand-name", brand_name])


def quote_relative_url(path: str) -> str:
    return "/".join(quote(part) for part in path.split("/"))


def upload_preview_bgm(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    project_dir = require_project(project)
    audio = payload.get("audio")
    if not isinstance(audio, dict):
        raise ValueError("Missing BGM audio payload.")

    encoded = str(audio.get("data") or "")
    if "," in encoded:
        encoded = encoded.split(",", 1)[1]
    if not encoded:
        raise ValueError("Missing BGM audio data.")

    try:
        audio_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("BGM payload is not valid base64.") from exc

    if not audio_bytes:
        raise ValueError("Uploaded BGM is empty.")
    if len(audio_bytes) > MAX_UPLOAD_BYTES:
        raise ValueError("Uploaded BGM is too large.")

    filename = clean_filename(str(audio.get("name") or "background.mp3"), "background.mp3")
    suffix = Path(filename).suffix.lower()
    if suffix not in AUDIO_UPLOAD_EXTENSIONS:
        raise ValueError("BGM file must be an audio file.")

    upload_dir = project_dir / "preview-assets" / "bgm"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_name = f"{time.strftime('%Y%m%d-%H%M%S')}_{filename}"
    audio_path = upload_dir / saved_name
    audio_path.write_bytes(audio_bytes)
    rel_path = audio_path.relative_to(project_dir).as_posix()
    return {
        "project": project_dir.name,
        "audio": {
            "name": filename,
            "path": rel_path,
            "url": project_url(project_dir.name) + quote_relative_url(rel_path),
            "size": len(audio_bytes),
        },
    }


def preview_settings_path(project: str) -> Path:
    return require_project(project) / "preview-settings.json"


def read_preview_settings(project: str) -> dict:
    path = preview_settings_path(project)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"preview-settings.json is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("preview-settings.json must contain a JSON object.")
    return data


def write_preview_settings(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("Missing preview settings.")
    path = preview_settings_path(project)
    settings = sync_settings_script_lines(path.parent, settings)
    encoded = json.dumps(settings, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > 200_000:
        raise ValueError("Preview settings are too large.")
    path.write_bytes(encoded + b"\n")
    app_js_synced = sync_app_js_preview_settings(path.parent, settings)
    return {"project": project, "settings": settings, "app_js_synced": app_js_synced}


def clean_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = [str(item).strip() for item in value]
    return [item for item in cleaned if item]


def js_literal(value: object, base_indent: str = "") -> str:
    literal = json.dumps(value, ensure_ascii=False, indent=2)
    if "\n" not in literal or not base_indent:
        return literal
    lines = literal.splitlines()
    return lines[0] + "\n" + "\n".join(f"{base_indent}{line}" for line in lines[1:])


def js_literal_span(source: str, start: int) -> tuple[int, int]:
    idx = start
    while idx < len(source) and source[idx].isspace():
        idx += 1
    if idx >= len(source) or source[idx] not in "[{(":
        raise ValueError("Expected JS literal.")
    open_to_close = {"[": "]", "{": "}", "(": ")"}
    literal_start = idx
    stack: list[str] = []
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    while idx < len(source):
        char = source[idx]
        next_char = source[idx + 1] if idx + 1 < len(source) else ""
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            idx += 1
            continue
        if line_comment:
            if char == "\n":
                line_comment = False
            idx += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                idx += 2
                continue
            idx += 1
            continue
        if char == "/" and next_char == "/":
            line_comment = True
            idx += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            idx += 2
            continue
        if char in {"'", '"', "`"}:
            quote = char
            idx += 1
            continue
        if char in open_to_close:
            stack.append(open_to_close[char])
        elif char in {"]", "}", ")"}:
            if not stack or char != stack[-1]:
                raise ValueError("Malformed JS literal.")
            stack.pop()
            if not stack:
                return literal_start, idx + 1
        idx += 1
    raise ValueError("Unterminated JS literal.")


def js_const_literal_span(source: str, const_name: str) -> tuple[int, int]:
    match = re.search(rf"\bconst\s+{re.escape(const_name)}\s*=\s*", source)
    if not match:
        raise ValueError(f"Missing const {const_name}.")
    return js_literal_span(source, match.end())


def read_js_const_literal(project_dir: Path, const_name: str) -> object | None:
    path = project_dir / "app.js"
    if not path.exists():
        return None
    source = path.read_text(encoding="utf-8")
    try:
        start, end = js_const_literal_span(source, const_name)
        return ast.literal_eval(source[start:end])
    except Exception:
        return None


def replace_js_const_literal(source: str, const_name: str, value: object) -> str:
    start, end = js_const_literal_span(source, const_name)
    line_start = source.rfind("\n", 0, start) + 1
    base_indent = re.match(r"\s*", source[line_start:start]).group(0)
    return source[:start] + js_literal(value, base_indent) + source[end:]


def replace_default_preview_script_lines(source: str, lines: list[str]) -> str:
    object_start, object_end = js_const_literal_span(source, "defaultPreviewSettings")
    object_source = source[object_start:object_end]
    match = re.search(r'(?m)^(\s*)(["\']?scriptLines["\']?\s*:\s*)', object_source)
    if not match:
        raise ValueError("Missing defaultPreviewSettings.slides.scriptLines.")
    value_start = object_start + match.end()
    value_end = js_literal_span(source, value_start)[1]
    return source[:value_start] + js_literal(lines, match.group(1)) + source[value_end:]


def sync_app_js_script_lines(project_dir: Path, lines: list[str]) -> bool:
    path = project_dir / "app.js"
    if not path.exists():
        return False
    source = path.read_text(encoding="utf-8")
    updated = replace_js_const_literal(source, "slideScripts", lines)
    updated = replace_default_preview_script_lines(updated, lines)
    if updated != source:
        path.write_text(updated, encoding="utf-8")
    return updated != source


def sync_app_js_preview_settings(project_dir: Path, settings: dict) -> bool:
    path = project_dir / "app.js"
    if not path.exists():
        return False
    source = path.read_text(encoding="utf-8")
    updated = replace_js_const_literal(source, "defaultPreviewSettings", settings)
    script_lines = settings.get("slides", {}).get("scriptLines", [])
    if isinstance(script_lines, list) and script_lines and all(isinstance(line, str) for line in script_lines):
        updated = replace_js_const_literal(updated, "slideScripts", script_lines)
    slides = settings.get("slides", {})
    if isinstance(slides, dict):
        for setting_key, const_name in SLIDE_AUDIO_SETTING_KEYS.items():
            values = clean_string_list(slides.get(setting_key))
            if values:
                updated = replace_js_const_literal(updated, const_name, values)
    if updated != source:
        path.write_text(updated, encoding="utf-8")
    return updated != source


def read_script_lines_from_path(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sync_settings_script_lines(project_dir: Path, settings: dict) -> dict:
    script_lines = read_script_lines_from_path(project_dir / "script.txt")
    synced = json.loads(json.dumps(settings, ensure_ascii=False))
    slides = synced.get("slides")
    if not isinstance(slides, dict):
        slides = {}
    if script_lines:
        slides["scriptLines"] = script_lines
    existing_slides: dict = {}
    settings_path = project_dir / "preview-settings.json"
    if settings_path.exists():
        try:
            existing_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(existing_settings, dict) and isinstance(existing_settings.get("slides"), dict):
                existing_slides = existing_settings["slides"]
        except json.JSONDecodeError:
            existing_slides = {}
    for setting_key, const_name in SLIDE_AUDIO_SETTING_KEYS.items():
        values = clean_string_list(slides.get(setting_key))
        if not values:
            values = clean_string_list(existing_slides.get(setting_key))
        if not values:
            values = clean_string_list(read_js_const_literal(project_dir, const_name))
        if values:
            slides[setting_key] = values
    synced["slides"] = slides
    return synced


def sync_preview_settings_script_lines(project_dir: Path, lines: list[str]) -> dict:
    path = project_dir / "preview-settings.json"
    settings: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"preview-settings.json is invalid: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("preview-settings.json must contain a JSON object.")
        settings = data
    slides = settings.get("slides")
    if not isinstance(slides, dict):
        slides = {}
    slides["scriptLines"] = lines
    for setting_key, const_name in SLIDE_AUDIO_SETTING_KEYS.items():
        values = clean_string_list(slides.get(setting_key))
        if not values:
            values = clean_string_list(read_js_const_literal(project_dir, const_name))
        if values:
            slides[setting_key] = values
    settings["slides"] = slides
    encoded = json.dumps(settings, ensure_ascii=False, indent=2).encode("utf-8")
    if len(encoded) > 200_000:
        raise ValueError("Preview settings are too large.")
    path.write_bytes(encoded + b"\n")
    return settings


def project_script_path(project: str) -> Path:
    return require_project(project) / "script.txt"


def read_project_script(project: str) -> dict:
    path = project_script_path(project)
    return {"project": project, "lines": read_script_lines_from_path(path)}


def write_project_script(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    path = project_script_path(project)
    lines = payload.get("lines")
    allow_count_change = bool(payload.get("allowCountChange") or payload.get("allow_count_change"))
    if not isinstance(lines, list):
        raise ValueError("Missing script lines.")
    cleaned = [str(line).strip() for line in lines]
    if not cleaned or any(not line for line in cleaned):
        raise ValueError("Script lines cannot be empty.")
    existing_count = len(read_project_script(project)["lines"])
    if existing_count and len(cleaned) != existing_count and not allow_count_change:
        raise ValueError(f"Expected {existing_count} script lines.")
    encoded = ("\n".join(cleaned) + "\n").encode("utf-8")
    if len(encoded) > 100_000:
        raise ValueError("Script is too large.")
    project_dir = path.parent
    existing_metadata = social_metadata.read_project_upload_metadata(project_dir)
    upload_metadata = social_metadata.generated_upload_metadata(project_dir, cleaned, existing_metadata)
    path.write_bytes(encoded)
    social_metadata.write_project_upload_metadata(project_dir, upload_metadata)
    sync_preview_settings_script_lines(project_dir, cleaned)
    app_js_synced = sync_app_js_script_lines(project_dir, cleaned)
    return {"project": project, "lines": cleaned, "upload_metadata": upload_metadata, "app_js_synced": app_js_synced}


def append_log(job_id: str, text: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["logs"] = (job.get("logs", "") + text)[-MAX_LOG_CHARS:]
        job["updated_at"] = time.time()


def set_job_state(job_id: str, **updates: object) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = time.time()


def refresh_queue_positions_locked() -> None:
    """Update public FIFO positions. JOBS_LOCK must already be held."""
    position = 1
    for queued_job_id in RENDER_QUEUE:
        job = JOBS.get(queued_job_id)
        if not job or job.get("status") != "queued" or job.get("cancel_requested"):
            continue
        job["queue_position"] = position
        job["updated_at"] = time.time()
        position += 1


def finalize_job_trial_export(job_id: str, status: str) -> None:
    """Finish a reserved allowance once, even when cancellation races with rendering."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("allowance_finalized") or job.get("allowance_finalizing"):
            return
        event_id = str(job.get("trial_event_id") or "") or None
        if not event_id:
            job["allowance_finalized"] = True
            return
        job["allowance_finalizing"] = True

    try:
        finish_trial_export(event_id, status)
        is_trial, used, limit = entitlement_trial_usage()
        if is_trial:
            set_job_state(job_id, trial_exports_used=used, trial_export_limit=limit)
    except Exception as exc:
        append_log(job_id, f"\nWarning: could not sync Trial export allowance: {exc}\n")
    finally:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["allowance_finalizing"] = False
                job["allowance_finalized"] = True
                job["updated_at"] = time.time()


def public_job(job: dict) -> dict:
    return {key: value for key, value in job.items() if key not in PRIVATE_JOB_FIELDS}


def list_jobs() -> list[dict]:
    with JOBS_LOCK:
        jobs = [public_job(job) for job in JOBS.values()]
    return sorted(
        jobs,
        key=lambda job: (-float(job.get("updated_at") or 0.0), str(job.get("id") or "")),
    )


def job_cancel_requested(job_id: str) -> bool:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return bool(job and job.get("cancel_requested"))


def failure_summary(logs: str, returncode: int) -> str:
    lines = [line.strip() for line in str(logs or "").splitlines() if line.strip()]
    skip_patterns = (
        r"^\$ ",
        r"^Traceback ",
        r"^File ",
        r"^\^+$",
        r"^raise CalledProcessError",
        r"^Render failed with exit code ",
        r"returned non-zero exit status",
        r"subprocess\.run\(",
        r"^During handling of the above",
        r"^The above exception was the direct cause",
    )
    preferred_patterns = (
        r"^Render failed:",
        r"^Lệnh render thất bại",
        r"^Không tìm thấy (lệnh render|FFmpeg)",
        r"Missing ElevenLabs|ElevenLabs API failed|ElevenLabs TTS failed|Missing ElevenLabs SDK|Invalid ElevenLabs",
        r"ValueError:|RuntimeError:|TimeoutError:|ModuleNotFoundError:|FileNotFoundError:",
        r"Media type mismatch|Error linking filters|Invalid argument|Error opening input|Invalid data found",
        r"No such file or directory|WinError|cannot find the file specified",
        r"❌",
    )
    candidates = []
    for line in lines:
        if any(re.search(pattern, line) for pattern in skip_patterns):
            continue
        if any(re.search(pattern, line) for pattern in preferred_patterns):
            candidates.append(line)
    if candidates:
        summary = candidates[-1]
        summary = re.sub(r"^❌\s*", "", summary)
        summary = re.sub(r"^Render failed:\s*", "", summary)
        return re.sub(r"^(ValueError|RuntimeError|ModuleNotFoundError|FileNotFoundError):\s*", "", summary)
    for line in reversed(lines):
        if any(re.search(pattern, line) for pattern in skip_patterns):
            continue
        if re.fullmatch(r"subprocess\.CalledProcessError.*", line):
            continue
        return line
    return f"Render failed with exit code {returncode}."


def final_video_url(project: str) -> str:
    return f"/project/{quote(project)}/output/final_video.mp4"


def player_url(project: str) -> str:
    return f"/watch/{quote(project)}"


def render_simple_player_html(project: str) -> bytes:
    project_name = html.escape(project)
    dashboard_url = f"/?project={quote(project)}"
    editor_url = project_url(project)
    video_url = final_video_url(project)
    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Xem {project_name} · Aurex</title>
  <style>
    :root {{
      color-scheme: dark;
      --player-background: #181818;
      --player-control-line: rgba(255, 255, 255, .14);
      --player-accent-soft: rgba(242, 178, 101, .14);
      --player-accent-border: rgba(232, 160, 96, .55);
      --player-text: rgba(246, 255, 249, .86);
    }}
    html[data-theme="light"] {{
      color-scheme: light;
      --player-background:
        radial-gradient(circle at 12% -8%, rgba(255, 165, 92, .22), transparent 31rem),
        radial-gradient(circle at 92% 108%, rgba(138, 108, 69, .10), transparent 28rem),
        linear-gradient(135deg, #f8f5ef, #e9e4da);
      --player-control-line: rgba(79, 57, 31, .16);
      --player-accent-soft: rgba(242, 178, 101, .12);
      --player-accent-border: rgba(216, 132, 53, .55);
      --player-text: rgba(32, 23, 15, .78);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ width: 100%; height: 100%; margin: 0; overflow: hidden; background: var(--player-background); }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    .player-stage {{
      display: grid;
      grid-template-columns: minmax(110px, 1fr) auto minmax(110px, 1fr);
      align-items: center;
      gap: clamp(18px, 3vw, 52px);
      width: 100%;
      height: 100%;
      min-height: 0;
      overflow: hidden;
      padding: 0 28px;
      background: transparent;
    }}
    .player-action {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
      padding: 0 18px;
      border: 1px solid var(--player-accent-border);
      border-radius: 12px;
      color: var(--player-text);
      background: var(--player-accent-soft);
      text-decoration: none;
      font-size: 15px;
      font-weight: 750;
      line-height: 1;
      transition: background 140ms ease, border-color 140ms ease;
    }}
    .player-action:hover {{ background: rgba(242, 178, 101, .18); border-color: rgba(216, 132, 53, .70); }}
    .player-back {{ justify-self: end; }}
    .player-edit {{ justify-self: start; }}
    video {{
      display: block;
      width: auto;
      height: 100vh;
      max-width: calc(100vw - 360px);
      max-height: 100vh;
      min-height: 0;
      object-fit: contain;
      background: transparent;
      border-radius: 18px;
      box-shadow: 0 0 0 1px rgba(216, 132, 53, .14), 0 18px 40px rgba(58, 44, 24, .12);
    }}
    html[data-theme="dark"] video {{
      box-shadow: 0 0 0 1px rgba(242, 178, 101, .14), 0 18px 40px rgba(0, 0, 0, .45);
    }}
    @media (max-width: 760px) {{
      .player-stage {{
        grid-template-columns: 1fr 1fr;
        grid-template-rows: auto minmax(0, 1fr);
        gap: 10px;
        padding: 10px;
      }}
      .player-action {{ min-height: 40px; padding: 0 14px; }}
      .player-back {{ grid-area: 1 / 1; justify-self: start; }}
      .player-edit {{ grid-area: 1 / 2; justify-self: end; }}
      video {{
        grid-area: 2 / 1 / 3 / 3;
        height: calc(100vh - 82px);
        max-width: 100%;
        max-height: calc(100vh - 82px);
        justify-self: center;
      }}
    }}
  </style>
</head>
<body>
  <script>document.documentElement.dataset.theme = localStorage.getItem('aurexvideo-theme') === 'light' ? 'light' : 'dark';</script>
  <script>if (window.__TAURI_INTERNALS__ && /Mac/.test(navigator.platform)) document.documentElement.classList.add('tauri-macos');</script>
  <main class="player-stage" aria-label="Trình phát video">
    <a class="player-action player-back" href="{dashboard_url}">← Quay lại</a>
    <video controls autoplay playsinline preload="metadata" src="{video_url}">Trình duyệt không hỗ trợ phát video.</video>
    <a class="player-action player-edit" href="{editor_url}">✎ Sửa</a>
  </main>
  <script>
    const video = document.querySelector('video');
    const autoplay = () => video.play().catch(() => {{
      video.muted = true;
      return video.play().catch(() => undefined);
    }});
    if (video.readyState >= 2) autoplay();
    else video.addEventListener('loadeddata', autoplay, {{ once: true }});
  </script>
</body>
</html>""".encode("utf-8")


def render_queue_worker() -> None:
    """Run queued renders one at a time, preserving their insertion order."""
    global RENDER_QUEUE_WORKER_ACTIVE
    try:
        while True:
            with JOBS_LOCK:
                next_job: tuple[str, list[str], str] | None = None
                invalid_job_ids: list[str] = []
                while RENDER_QUEUE:
                    job_id = RENDER_QUEUE.popleft()
                    job = JOBS.get(job_id)
                    if not job or job.get("status") != "queued" or job.get("cancel_requested"):
                        continue
                    command = job.get("queued_command")
                    project = str(job.get("project") or "")
                    if not isinstance(command, list) or not project:
                        job["status"] = "failed"
                        job["finished_at"] = time.time()
                        job["error"] = "Queued render job is missing its command."
                        job["updated_at"] = time.time()
                        invalid_job_ids.append(job_id)
                        continue
                    job["status"] = "running"
                    job["queue_position"] = None
                    job["updated_at"] = time.time()
                    next_job = (job_id, command, project)
                    break
                refresh_queue_positions_locked()
            for invalid_job_id in invalid_job_ids:
                finalize_job_trial_export(invalid_job_id, "failed")
            if not next_job:
                if invalid_job_ids:
                    continue
                return
            run_job(*next_job)
    finally:
        with JOBS_LOCK:
            RENDER_QUEUE_WORKER_ACTIVE = False
            restart_needed = any(
                (job := JOBS.get(job_id))
                and job.get("status") == "queued"
                and not job.get("cancel_requested")
                for job_id in RENDER_QUEUE
            )
        if restart_needed:
            start_render_queue_worker()


def start_render_queue_worker() -> None:
    global RENDER_QUEUE_WORKER_ACTIVE
    with JOBS_LOCK:
        if RENDER_QUEUE_WORKER_ACTIVE:
            return
        if not any(
            (job := JOBS.get(job_id))
            and job.get("status") == "queued"
            and not job.get("cancel_requested")
            for job_id in RENDER_QUEUE
        ):
            return
        RENDER_QUEUE_WORKER_ACTIVE = True
    try:
        threading.Thread(target=render_queue_worker, daemon=True).start()
    except Exception:
        with JOBS_LOCK:
            RENDER_QUEUE_WORKER_ACTIVE = False
        raise


def run_job(job_id: str, cmd: list[str], project: str) -> None:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    pretty_cmd = " ".join(shlex.quote(part) for part in cmd)
    def finish_export_allowance(status: str) -> None:
        finalize_job_trial_export(job_id, status)

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        cancelled_before_start = bool(job and job.get("cancel_requested"))
        if job and not cancelled_before_start:
            job.update(status="running", command=pretty_cmd, started_at=time.time())
            job["updated_at"] = time.time()
    append_log(job_id, f"$ {pretty_cmd}\n\n")
    if cancelled_before_start or job_cancel_requested(job_id):
        append_log(job_id, "Render stopped before process start.\n")
        finish_export_allowance("cancelled")
        set_job_state(job_id, status="cancelled", returncode=None, finished_at=time.time())
        return

    try:
        popen_kwargs = {}
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            if creationflags:
                popen_kwargs["creationflags"] = creationflags
        else:
            popen_kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **popen_kwargs,
        )
        set_job_state(job_id, process=proc, pid=proc.pid)
        # Cancellation can arrive after the pre-start check but before Popen
        # publishes its process handle. Stop that process immediately.
        if job_cancel_requested(job_id):
            terminate_process(proc)
    except Exception as exc:
        append_log(job_id, f"Failed to start render: {exc}\n")
        finish_export_allowance("failed")
        set_job_state(job_id, status="failed", returncode=-1, finished_at=time.time())
        return

    assert proc.stdout is not None
    for line in proc.stdout:
        append_log(job_id, line)

    returncode = proc.wait()
    set_job_state(job_id, process=None, pid=None)
    if job_cancel_requested(job_id):
        append_log(job_id, "\nRender stopped by user.\n")
        finish_export_allowance("cancelled")
        set_job_state(job_id, status="cancelled", returncode=returncode, finished_at=time.time())
        return
    try:
        project_dir = require_project(project)
    except Exception:
        project_dir = project_lookup_root() / project
    video_path = project_dir / "output" / "final_video.mp4"

    if returncode == 0 and video_path.exists():
        try:
            postflight = validate_rendered_video(video_path)
        except Exception as exc:
            summary = f"Postflight media thất bại: {exc}"
            append_log(job_id, f"\nRender đã tạo file nhưng bị chặn: {summary}\n")
            finish_export_allowance("failed")
            set_job_state(
                job_id,
                status="failed",
                returncode=returncode,
                finished_at=time.time(),
                error=summary,
            )
        else:
            report_path = video_path.with_name(f"{video_path.stem}.render-report.json")
            try:
                render_report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                render_report = {}
            backend_requested = str(render_report.get("backend_requested") or "auto")
            backend_used = str(render_report.get("backend_used") or render_report.get("render_backend") or "unknown")
            fallback_reason = render_report.get("fallback_reason")
            append_log(job_id, f"\nPostflight OK: {postflight.get('width')}x{postflight.get('height')} @ {postflight.get('fps')}fps, BT.709, AAC stereo 48kHz.\n")
            append_log(
                job_id,
                "Backend report: "
                f"backend_requested={backend_requested} backend_used={backend_used} "
                f"fallback_reason={fallback_reason or 'null'}\n",
            )
            append_log(job_id, f"Done: {video_path}\n")
            finish_export_allowance("completed")
            set_job_state(
                job_id,
                status="done",
                returncode=returncode,
                finished_at=time.time(),
                video_url=final_video_url(project),
                postflight=postflight,
                backend_requested=backend_requested,
                backend_used=backend_used,
                fallback_reason=fallback_reason,
            )
    elif returncode == 0:
        append_log(job_id, "\nRender finished, but final_video.mp4 was not found.\n")
        finish_export_allowance("failed")
        set_job_state(job_id, status="failed", returncode=returncode, finished_at=time.time())
    else:
        with JOBS_LOCK:
            logs = str((JOBS.get(job_id) or {}).get("logs") or "")
        summary = failure_summary(logs, returncode)
        append_log(job_id, f"\nRender failed: {summary}\n")
        finish_export_allowance("failed")
        set_job_state(job_id, status="failed", returncode=returncode, finished_at=time.time(), error=summary)


def build_render_command(payload: dict, authoritative_entitlement: dict | None = None) -> tuple[list[str], str]:
    project = str(payload.get("project") or "").strip()
    project_dir = require_project(project)
    speed = coerce_speed(payload.get("speed", 1.0))
    volume = coerce_volume(payload.get("volume", 1.0))
    render_size = coerce_render_size(payload.get("size", "1080x1920"))
    quality_profile = coerce_quality_profile(payload.get("qualityProfile") or payload.get("quality_profile"))
    render_backend = coerce_render_backend(payload.get("renderBackend") or payload.get("render_backend"))
    engine = str(payload.get("engine") or "").strip().lower()
    cmd = [
        str(RENDER_PYTHON), "-u", str(REPO_ROOT / "tools" / "render_project.py"),
        str(project_dir), "--speed", f"{speed:g}", "--volume", f"{volume:g}", "--size", render_size,
        "--quality-profile", quality_profile,
        "--render-backend", render_backend,
    ]
    rebuild_audio_cache = bool(
        payload.get("force", False)
        or payload.get("rebuildAudioCache", False)
        or payload.get("rebuild_audio_cache", False)
    )

    if engine in {"upload"}:
        audio_payload = payload.get("audio")
        if not isinstance(audio_payload, dict):
            raise ValueError("Hãy chọn file audio trước khi render.")
        audio_name = str(audio_payload.get("name") or "")
        if Path(audio_name).suffix.casefold() not in VOICEOVER_UPLOAD_EXTENSIONS:
            raise ValueError("Chỉ hỗ trợ file MP3, WAV hoặc MAV.")
        uploaded = m3.decode_upload(
            project,
            {
                "kind": "voiceover",
                "name": audio_name,
                "data": audio_payload.get("data"),
            },
        )
        audio_path = (project_dir / str(uploaded.get("path") or "")).resolve()
        try:
            audio_path.relative_to(project_dir.resolve())
        except ValueError as exc:
            raise ValueError("Đường dẫn audio upload không hợp lệ.") from exc
        if not audio_path.is_file():
            raise FileNotFoundError("Không tìm thấy file audio vừa tải lên.")
        cmd.extend(["--engine", "upload", "--audio", str(audio_path)])
        append_render_asset_options(cmd, payload, project_dir, authoritative_entitlement)
        return cmd, "upload"

    if engine in {"elevenlabs"}:
        mode = str(payload.get("mode") or "tts").strip().lower()
        if mode == "upload":
            audio_path = decode_audio_payload(payload.get("audio", {}), project_dir)
            cmd.extend(["--engine", "upload", "--audio", str(audio_path)])
            append_render_asset_options(cmd, payload, project_dir, authoritative_entitlement)
            return cmd, "elevenlabs-upload"
        config = elevenlabs_config()
        elevenlabs_api_key(None, config)
        voice = elevenlabs_voice_id(str(payload.get("voice") or "").strip() or None, config)
        model_id = str(payload.get("modelId") or config.get("model_id") or "eleven_v3").strip()
        for value, label in ((voice, "voice id"), (model_id, "model id")):
            if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
                raise ValueError(f"Invalid ElevenLabs {label}.")
        cmd.extend(["--engine", "elevenlabs", "--voice", voice, "--model-id", model_id])
        if rebuild_audio_cache:
            cmd.append("--force-tts")
        append_render_asset_options(cmd, payload, project_dir, authoritative_entitlement)
        return cmd, "elevenlabs"

    if engine in {"maziao"}:
        voice = str(payload.get("voice") or "OncoinX").strip()
        model_id = str(payload.get("modelId") or "").strip()
        requested_mode = str(
            payload.get("ttsMode")
            or payload.get("tts_mode")
            or payload.get("mode")
            or "auto"
        ).strip()
        if requested_mode.casefold() == "auto" or not requested_mode:
            tts_mode = "auto"
        else:
            tts_mode = normalize_tts_mode(requested_mode)
        if model_id and not re.fullmatch(r"[A-Za-z0-9._:-]+", model_id):
            raise ValueError("Invalid Maziao model id.")
        cmd.extend(["--engine", "maziao", "--voice", voice, "--tts-mode", tts_mode])
        if model_id:
            cmd.extend(["--model-id", model_id])
        tts_config = payload.get("ttsConfig") or payload.get("tts_config")
        if tts_config is not None:
            if not isinstance(tts_config, dict):
                raise ValueError("Maziao ttsConfig phải là một JSON object.")
            encoded_tts_config = json.dumps(tts_config, ensure_ascii=False, separators=(",", ":"))
            if len(encoded_tts_config.encode("utf-8")) > 32 * 1024:
                raise ValueError("Maziao ttsConfig tối đa 32 KB.")
            cmd.extend(["--tts-config-json", encoded_tts_config])
        if rebuild_audio_cache:
            cmd.append("--force-tts")
        append_render_asset_options(cmd, payload, project_dir, authoritative_entitlement)
        return cmd, "maziao"

    if engine in {"edgetts", "edtts", "edge_tts", "edge-tts"}:
        voice = str(payload.get("voice") or "vi-VN-NamMinhNeural").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", voice):
            raise ValueError("Invalid Edge TTS voice name.")
        cmd.extend(["--engine", "edge", "--voice", voice])
        if rebuild_audio_cache:
            cmd.append("--force-tts")
        append_render_asset_options(cmd, payload, project_dir, authoritative_entitlement)
        return cmd, "edgetts"

    if engine in {"vieneu", "aurextts"}:
        config = m3.vieneu_public_config()
        voice = str(payload.get("voice") or config.get("voice") or "chautinhtri").strip()
        mode = str(payload.get("mode") or config.get("mode") or "v3turbo").strip()
        device = str(payload.get("device") or config.get("device") or "cpu").strip()
        ref_audio = str(payload.get("refAudio") or payload.get("ref_audio") or config.get("refAudio") or "").strip()
        cmd.extend(["--engine", "vieneu", "--voice", voice])
        cmd.extend([
            "--tts-config-json",
            json.dumps(
                {"mode": mode, "device": device, "refAudio": ref_audio},
                ensure_ascii=False,
            ),
        ])
        if rebuild_audio_cache:
            cmd.append("--force-tts")
        append_render_asset_options(cmd, payload, project_dir, authoritative_entitlement)
        return cmd, "vieneu"

    if engine in {"project", "local"}:
        cmd.extend(["--engine", "project"])
        append_render_asset_options(cmd, payload, project_dir, authoritative_entitlement)
        return cmd, "project"

    raise ValueError("Engine must be upload, vieneu, maziao, edgetts, elevenlabs or project.")


def has_running_job(project: str) -> bool:
    with JOBS_LOCK:
        return any(
            job.get("project") == project and job.get("status") in ACTIVE_JOB_STATUSES
            for job in JOBS.values()
        )


def create_job(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    require_project(project)
    backend_requested = coerce_render_backend(payload.get("renderBackend") or payload.get("render_backend"))
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    with JOBS_LOCK:
        if any(
            job.get("project") == project and job.get("status") in ACTIVE_JOB_STATUSES
            for job in JOBS.values()
        ):
            raise RuntimeError(f"Project '{project}' already has a running render job.")
        JOBS[job_id] = {
            "id": job_id,
            "project": project,
            "status": "authorizing",
            "logs": "",
            "created_at": now,
            "updated_at": now,
            "video_url": None,
            "backend_requested": backend_requested,
            "backend_used": None,
            "fallback_reason": None,
        }

    license_event_id = None
    try:
        license_event_id, authoritative_entitlement = reserve_trial_export(project)
        cmd, engine = build_render_command(payload, authoritative_entitlement)
        try:
            m3.write_render_preferences(payload)
        except Exception:
            # Preferences are convenience state; never block a valid render job.
            pass
    except Exception:
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        try:
            finish_trial_export(license_event_id, "failed")
        except Exception:
            pass
        raise
    authoritative_account = {"entitlement": authoritative_entitlement} if authoritative_entitlement is not None else None
    is_trial, trial_used, trial_limit = entitlement_trial_usage(authoritative_account)
    job = {
        "id": job_id,
        "project": project,
        "engine": engine,
        "status": "queued",
        "logs": "",
        "created_at": now,
        "updated_at": now,
        "video_url": None,
        "backend_requested": backend_requested,
        "backend_used": None,
        "fallback_reason": None,
        "trial_event_id": license_event_id,
        "allowance_finalized": False,
        "allowance_finalizing": False,
        "trial_exports_used": trial_used if is_trial else None,
        "trial_export_limit": trial_limit if is_trial else None,
        "queue_position": None,
        "queued_command": cmd,
    }

    with JOBS_LOCK:
        JOBS[job_id] = job
        RENDER_QUEUE.append(job_id)
        refresh_queue_positions_locked()
    try:
        start_render_queue_worker()
    except Exception:
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
            try:
                RENDER_QUEUE.remove(job_id)
            except ValueError:
                pass
            refresh_queue_positions_locked()
        try:
            finish_trial_export(license_event_id, "failed")
        except Exception:
            pass
        raise
    return public_job(job)


def get_job(job_id: str) -> dict | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return public_job(job) if job else None


def terminate_process(proc: subprocess.Popen) -> bool:
    if proc.poll() is not None:
        return True
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=5)
        return True
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "nt":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        proc.kill()
    try:
        proc.wait(timeout=5)
        return True
    except subprocess.TimeoutExpired:
        return False


def cancel_job(job_id: str) -> dict | None:
    finish_queued_allowance = False
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        status = str(job.get("status") or "")
        if status not in ACTIVE_JOB_STATUSES:
            return public_job(job)
        job["cancel_requested"] = True
        if status == "queued":
            job["status"] = "cancelled"
            job["returncode"] = None
            job["finished_at"] = time.time()
            job["queue_position"] = None
            try:
                RENDER_QUEUE.remove(job_id)
            except ValueError:
                pass
            refresh_queue_positions_locked()
            finish_queued_allowance = True
        else:
            job["status"] = "cancelling"
        job["updated_at"] = time.time()
        proc = job.get("process")

    append_log(job_id, "\nStop requested by user.\n")
    if finish_queued_allowance:
        # A reservation happens before enqueueing, so a waiting cancellation must
        # release it here. Running jobs finalize in run_job to avoid a double finish.
        finalize_job_trial_export(job_id, "cancelled")
        return get_job(job_id)
    if isinstance(proc, subprocess.Popen):
        if terminate_process(proc):
            set_job_state(job_id, status="cancelled", returncode=proc.returncode, process=None, pid=None, finished_at=time.time())
    return get_job(job_id)


def delete_project_output(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    confirm = bool(payload.get("confirm"))
    if not confirm:
        raise ValueError("Missing delete confirmation.")
    if has_running_job(project):
        raise RuntimeError(f"Project '{project}' has a running render job.")

    project_dir = require_project(project)
    output_dir = (project_dir / "output").resolve()
    try:
        output_dir.relative_to(project_dir.resolve())
    except ValueError as exc:
        raise ValueError("Invalid output path.") from exc
    if output_dir.name != "output":
        raise ValueError("Refusing to delete a non-output directory.")

    existed = output_dir.is_dir() and any(output_dir.iterdir())
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError("Output path exists but is not a directory.")
    if existed:
        shutil.rmtree(output_dir)

    return {
        "ok": True,
        "project": project_dir.name,
        "deleted": existed,
        "output_url": final_video_url(project_dir.name),
    }


def delete_project(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    if not bool(payload.get("confirm")):
        raise ValueError("Missing delete confirmation.")
    if has_running_job(project):
        raise RuntimeError(f"Project '{project}' has a running render job.")
    if SOURCE_ROOT_IS_PROJECT:
        raise RuntimeError("Không thể xoá project khi source root đang trỏ thẳng vào chính project đó.")

    project_dir = require_project(project).resolve()
    root = project_lookup_root().resolve()
    try:
        project_dir.relative_to(root)
    except ValueError as exc:
        raise ValueError("Invalid project path.") from exc
    if project_dir.parent != root or project_dir == root:
        raise ValueError("Refusing to delete a path outside the project collection.")
    shutil.rmtree(project_dir)

    legacy_output = (REPO_ROOT / "output" / project).resolve()
    try:
        legacy_output.relative_to((REPO_ROOT / "output").resolve())
    except ValueError:
        legacy_output = None
    if legacy_output and legacy_output.is_dir():
        shutil.rmtree(legacy_output)
    return {"ok": True, "project": project, "deleted": True}


def rename_project(payload: dict) -> dict:
    project = validate_project_name(payload.get("project"))
    next_project = validate_project_name(payload.get("name") or payload.get("newProject") or payload.get("new_project"))
    if len(next_project) > 120:
        raise ValueError("Tên project tối đa 120 ký tự.")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", next_project):
        raise ValueError(
            "Tên project chỉ được dùng chữ thường không dấu, số và dấu gạch ngang; "
            "ví dụ: tieu-thuyet-phan-1."
        )
    if project == next_project:
        return {"ok": True, "project": next_project, "previous_project": project, "renamed": False}
    if has_running_job(project):
        raise RuntimeError(f"Project '{project}' đang render, chưa thể đổi tên.")
    if SOURCE_ROOT_IS_PROJECT:
        raise RuntimeError("Không thể đổi tên khi source root đang trỏ thẳng vào chính project đó.")

    source = require_project(project).resolve()
    root = project_lookup_root().resolve()
    destination = (root / next_project).resolve()
    try:
        source.relative_to(root)
        destination.relative_to(root)
    except ValueError as exc:
        raise ValueError("Invalid project path.") from exc
    if source.parent != root or destination.parent != root:
        raise ValueError("Refusing to rename a path outside the project collection.")
    if destination.exists():
        raise FileExistsError(f"Project '{next_project}' đã tồn tại.")

    legacy_root = (REPO_ROOT / "output").resolve()
    legacy_source = (legacy_root / project).resolve()
    legacy_destination = (legacy_root / next_project).resolve()
    if legacy_source.is_dir() and legacy_destination.exists():
        raise FileExistsError(f"Output cũ của project '{next_project}' đã tồn tại.")

    source.rename(destination)
    topic_path = destination / "topic.json"
    topic = json.loads(topic_path.read_text(encoding="utf-8"))
    topic["id"] = next_project
    topic_path.write_bytes(json_dumps(topic) + b"\n")
    rendered_topic_path = destination / "topic.rendered.json"
    if rendered_topic_path.is_file():
        rendered_topic = json.loads(rendered_topic_path.read_text(encoding="utf-8"))
        if isinstance(rendered_topic, dict):
            rendered_topic["id"] = next_project
            rendered_topic_path.write_bytes(json_dumps(rendered_topic) + b"\n")
    if legacy_source.is_dir():
        legacy_source.rename(legacy_destination)

    return {
        "ok": True,
        "project": next_project,
        "previous_project": project,
        "renamed": True,
        "url": project_url(next_project),
    }


def next_duplicate_project_name(project: str) -> str:
    root = project_lookup_root()
    index = 1
    while True:
        candidate = f"{project}-copy-{index}"
        if len(candidate) > 120:
            raise ValueError(
                "Tên project sau khi nhân bản vượt quá 120 ký tự. Hãy rút ngắn tên gốc rồi thử lại."
            )
        if not (root / candidate).exists():
            return candidate
        index += 1


def duplicate_project(payload: dict) -> dict:
    project = validate_project_name(payload.get("project"))
    if has_running_job(project):
        raise RuntimeError(f"Project '{project}' đang render, chưa thể nhân bản.")
    if SOURCE_ROOT_IS_PROJECT:
        raise RuntimeError("Không thể nhân bản khi source root đang trỏ thẳng vào chính project đó.")

    source = require_project(project).resolve()
    root = project_lookup_root().resolve()
    next_project = validate_project_name(next_duplicate_project_name(project))
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", next_project):
        raise ValueError("Tên project nhân bản không hợp lệ.")
    destination = (root / next_project).resolve()
    try:
        source.relative_to(root)
        destination.relative_to(root)
    except ValueError as exc:
        raise ValueError("Invalid project path.") from exc
    if source.parent != root or destination.parent != root:
        raise ValueError("Refusing to duplicate a path outside the project collection.")
    if destination.exists():
        raise FileExistsError(f"Project '{next_project}' đã tồn tại.")

    shutil.copytree(source, destination)
    topic_path = destination / "topic.json"
    topic = json.loads(topic_path.read_text(encoding="utf-8"))
    topic["id"] = next_project
    topic_path.write_bytes(json_dumps(topic) + b"\n")
    rendered_topic_path = destination / "topic.rendered.json"
    if rendered_topic_path.is_file():
        rendered_topic = json.loads(rendered_topic_path.read_text(encoding="utf-8"))
        if isinstance(rendered_topic, dict):
            rendered_topic["id"] = next_project
            rendered_topic_path.write_bytes(json_dumps(rendered_topic) + b"\n")

    return {
        "ok": True,
        "project": next_project,
        "source_project": project,
        "url": project_url(next_project),
    }


def reveal_project_output(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    project_dir = require_project(project)
    output_dir = (project_dir / "output").resolve()
    video_path = (output_dir / "final_video.mp4").resolve()
    try:
        output_dir.relative_to(project_dir.resolve())
        video_path.relative_to(project_dir.resolve())
    except ValueError as exc:
        raise ValueError("Invalid output path.") from exc
    if not output_dir.is_dir():
        raise FileNotFoundError(f"output folder not found for project '{project_dir.name}'.")

    select_path = video_path if video_path.is_file() else output_dir
    if sys.platform == "darwin":
        cmd = ["open", "-R", str(select_path)] if select_path.is_file() else ["open", str(output_dir)]
    elif sys.platform.startswith("win"):
        # Keep "/select," as its own argv so paths on other drives (D:) open correctly.
        cmd = ["explorer", "/select,", str(select_path)]
    else:
        opener = shutil.which("xdg-open")
        if not opener:
            raise RuntimeError("No supported file manager opener found.")
        cmd = [opener, str(output_dir)]

    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {
        "ok": True,
        "project": project_dir.name,
        "path": str(select_path),
        "output_dir": str(output_dir),
    }


def open_external_url(payload: dict) -> dict:
    url = str(payload.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Chỉ được mở liên kết http hoặc https hợp lệ.")

    if sys.platform == "darwin":
        subprocess.Popen(["/usr/bin/open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif os.name == "nt":
        os.startfile(url)  # type: ignore[attr-defined]
    else:
        opener = shutil.which("xdg-open")
        if not opener:
            raise RuntimeError("Không tìm thấy trình duyệt mặc định để mở liên kết.")
        subprocess.Popen([opener, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "url": url}




def social_callback_html(title: str, message: str, ok: bool = True) -> bytes:
    color = "#f2b261" if ok else "#ff8585"
    return f"""<!doctype html>
<html lang="vi">
<head><meta charset="utf-8"><title>{html.escape(title)}</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#181818;color:#eefcf4;padding:32px;">
  <main style="max-width:680px;margin:0 auto;border:1px solid rgba(255,255,255,.14);border-radius:20px;padding:24px;background:rgba(255,255,255,.06);">
    <h1 style="margin-top:0;color:{color};">{html.escape(title)}</h1>
    <p style="line-height:1.6;">{html.escape(message)}</p>
    <p style="opacity:.72;">Bạn có thể đóng tab này và quay lại AurexVideo Upload Center.</p>
  </main>
</body>
</html>""".encode("utf-8")


def render_elevenlabs_guide_html() -> bytes:
    return render_page_shell(
        title="Hướng dẫn ElevenLabs",
        body="""
  <main class="guide-page">
    <header class="guide-hero">
      <a class="app-back" href="/"><img src="/web/aurexvideo-logo.png" alt="" /><span><strong>Aurex</strong><small>← Quay lại Dashboard</small></span></a>
      <h1>Hướng dẫn ElevenLabs</h1>
      <p>Dùng cách thủ công khi bạn muốn tự nghe và tải file audio. Dùng API khi muốn Web UI tự gọi ElevenLabs rồi render luôn.</p>
      <a class="affiliate-cta" href="https://try.elevenlabs.io/z38mu0hfbskn" target="_blank" rel="noreferrer">
        <span class="affiliate-cta-icon">↗</span>
        <span class="affiliate-cta-copy"><strong>Đăng ký ElevenLabs</strong><small>Tạo tài khoản mới để bắt đầu làm voiceover.</small></span>
      </a>
      <div class="guide-links" aria-label="Liên kết công cụ ElevenLabs">
        <a href="https://elevenlabs.io/app/speech-synthesis/text-to-speech" target="_blank" rel="noreferrer"><span>🎙</span>Text to Speech</a>
        <a href="https://elevenlabs.io/app/voice-library?search=nh%E1%BA%ADt+" target="_blank" rel="noreferrer"><span>🎧</span>Voice Library</a>
        <a href="https://elevenlabs.io/app/subscription/api" target="_blank" rel="noreferrer"><span>💳</span>Nạp API credit</a>
        <a href="https://elevenlabs.io/app/developers/api-keys" target="_blank" rel="noreferrer"><span>🔑</span>Tạo API key</a>
      </div>
    </header>

    <section class="guide-card" id="manual">
      <div class="guide-copy">
        <p class="kicker">Tải file audio</p>
        <h2>Cách làm thủ công</h2>
        <ol>
          <li class="guide-action-step"><span>Chưa có tài khoản?</span><a class="affiliate-inline-link" href="https://try.elevenlabs.io/z38mu0hfbskn" target="_blank" rel="noreferrer">Đăng ký ElevenLabs ↗</a></li>
          <li>Bấm mở ElevenLabs Dashboard.</li>
          <li>Dán toàn bộ `script.txt` vào vùng nhập chữ.</li>
          <li>Chọn voice “Nhật - Narrative & Compelling”.</li>
          <li>Chọn model Eleven v3, generate rồi tải audio về.</li>
          <li>Quay lại Web UI, chọn file audio và render.</li>
        </ol>
      </div>
      <div class="guide-shot tts-shot" aria-label="Hướng dẫn ElevenLabs Text to Speech">
        <div class="shot-sidebar"><strong>ElevenLabs</strong><span>Text to Speech</span><span>Voices</span><span>Studio</span></div>
        <div class="shot-main">
          <div class="shot-top">Text to Speech</div>
          <div class="script-zone callout script-callout">
            <span class="voice-pill callout voice-callout">Nhật - Narrative & Compelling</span>
            <p>Dán script.txt vào đây</p>
          </div>
        </div>
        <div class="shot-settings">
          <strong>Settings</strong>
          <div class="setting-row callout voice-callout">Voice: Nhật...</div>
          <div class="setting-row callout model-callout">Model: Eleven v3</div>
          <div class="setting-row">Output: MP3 44.1kHz</div>
        </div>
        <div class="note-pin note-script">Dán script</div>
        <div class="note-pin note-voice">Voice Nhật</div>
        <div class="note-pin note-model">Model v3</div>
      </div>
      <a class="guide-section-link" href="https://elevenlabs.io/app/speech-synthesis/text-to-speech" target="_blank" rel="noreferrer">Mở Text to Speech ↗</a>
    </section>

    <section class="guide-card" id="api-credit">
      <div class="guide-copy">
        <p class="kicker">ElevenLabs API</p>
        <h2>Nạp credit trước khi test</h2>
        <ol>
          <li class="guide-action-step"><span>Chưa có tài khoản?</span><a class="affiliate-inline-link" href="https://try.elevenlabs.io/z38mu0hfbskn" target="_blank" rel="noreferrer">Đăng ký ElevenLabs ↗</a></li>
          <li>Vào trang ElevenAPI subscription.</li>
          <li>Bấm Add credits.</li>
          <li>Nạp khoảng 5 đô để test trước, đừng nạp nhiều khi chưa rõ workflow.</li>
        </ol>
      </div>
      <div class="guide-shot billing-shot" aria-label="Hướng dẫn nạp credit ElevenLabs API">
        <div class="shot-sidebar"><strong>ElevenLabs</strong><span>Home</span><span>Text to Speech</span><span>Developers</span></div>
        <div class="billing-main">
          <h3>Subscription</h3>
          <div class="billing-tabs"><span>ElevenCreative</span><span class="active">ElevenAPI</span></div>
          <div class="balance-box">
            <span>Top up balance</span>
            <strong>$2.43</strong>
            <button class="callout credit-callout">+ Add credits</button>
          </div>
          <div class="pricing-row"><span>Multilingual v2 / v3</span><b>$0.10</b><small>per 1K characters</small></div>
        </div>
        <div class="note-pin note-credit">Nạp $5 để test</div>
      </div>
      <a class="guide-section-link" href="https://elevenlabs.io/app/subscription/api" target="_blank" rel="noreferrer">Mở trang nạp API credit ↗</a>
    </section>

    <section class="guide-card" id="api-key">
      <div class="guide-copy">
        <p class="kicker">API key</p>
        <h2>Tạo key rồi lưu vào Web UI</h2>
        <ol>
          <li>Vào Developers → API Keys.</li>
          <li>Bấm Create Key.</li>
          <li>Copy key, quay lại Web UI và dán vào ô ElevenLabs API key.</li>
        </ol>
      </div>
      <div class="guide-shot api-shot" aria-label="Hướng dẫn tạo ElevenLabs API key">
        <div class="shot-sidebar"><strong>ElevenLabs</strong><span>Home</span><span>Text to Speech</span><span>Developers</span></div>
        <div class="api-main">
          <h3>Developers</h3>
          <div class="api-tabs"><span>Overview</span><span class="active">API Keys</span><span>Webhooks</span><span>Analytics</span></div>
          <p>An API key lets you connect to the API.</p>
          <button class="callout key-callout">+ Create Key</button>
          <div class="key-row"><span>main</span><span>••••••••••••a283</span><span>Enabled</span></div>
        </div>
        <div class="note-pin note-key">Tạo API key</div>
      </div>
      <a class="guide-section-link" href="https://elevenlabs.io/app/developers/api-keys" target="_blank" rel="noreferrer">Mở trang tạo API key ↗</a>
    </section>

    <section class="guide-card" id="voice-id">
      <div class="guide-copy">
        <p class="kicker">Voice Library</p>
        <h2>Lấy Voice ID của giọng Nhật</h2>
        <ol>
          <li>Mở Voice Library với từ khóa “nhật” đã điền sẵn.</li>
          <li>Tìm dòng “Nhật - Narrative & Compelling”.</li>
          <li>Bấm menu ba chấm rồi chọn Copy voice ID.</li>
          <li>Quay lại Web UI, dán vào ô Voice ID trong phần nâng cao.</li>
        </ol>
      </div>
      <div class="guide-shot voice-shot" aria-label="Hướng dẫn lấy ElevenLabs Voice ID">
        <div class="shot-sidebar"><strong>ElevenLabs</strong><span>Home</span><span class="active-side">Voices</span><span>Studio</span><span>Flows</span></div>
        <div class="voice-main">
          <div class="voice-breadcrumb">Voices › Explore</div>
          <h3>Voices</h3>
          <div class="voice-tabs"><span class="active">Explore</span><span>My Voices</span></div>
          <div class="voice-search">⌕ <span>nhật</span></div>
          <div class="voice-filters"><span>Language</span><span>Narration</span><span>Characters</span><span>Social Media</span><span>Educational</span></div>
          <p class="voice-count">2,721 voices</p>
          <div class="voice-list">
            <div class="voice-row featured">
              <div class="voice-avatar"></div>
              <div class="voice-title"><strong>Nhật - Narrative & Compelling</strong><span>Articulate Vietnamese voice suited...</span></div>
              <span>Vietnamese</span><span>Northern</span><span>45.8K</span><span>Narration</span>
              <button class="voice-dots callout voice-dots-callout" type="button">⋮</button>
            </div>
            <div class="voice-row muted-row"><div class="voice-avatar dark"></div><div class="voice-title"><strong>Finn - The British voice that makes ...</strong><span>I'm Finn, a British voice artist with...</span></div><span>English</span><span>British</span><span>15.4K</span><span>Conversational</span><button class="voice-dots" type="button">⋮</button></div>
            <div class="voice-row muted-row"><div class="voice-avatar blue"></div><div class="voice-title"><strong>Suhaan - Calm, Clear and Neat</strong><span>Suhaan - Delhi Guy - Suhan is a...</span></div><span>Hindi</span><span>Standard</span><span>44.2K</span><span>Conversational</span><button class="voice-dots" type="button">⋮</button></div>
          </div>
          <div class="voice-menu callout voice-id-callout">
            <div class="voice-copy-row">⧉ <strong>Copy voice ID</strong></div>
            <div>▱ Add to collection</div>
            <div>≋ View similar</div>
          </div>
        </div>
        <div class="note-pin note-voice-id">Khoanh chỗ lấy Voice ID</div>
      </div>
      <a class="guide-section-link" href="https://elevenlabs.io/app/voice-library?search=nh%E1%BA%ADt+" target="_blank" rel="noreferrer">Mở Voice Library đã tìm “nhật” ↗</a>
    </section>
  </main>
""",
        extra_style="""
    body { overflow: auto; }
    .guide-page { max-width: 1180px; margin: 0 auto; }
    .guide-hero { max-width: none; margin-bottom: 22px; }
    .guide-hero h1 {
      max-width: none;
      font-size: clamp(34px, 4vw, 54px);
      white-space: normal;
    }
    .back-link { margin-bottom: 18px; }
    .affiliate-cta {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 14px;
      align-items: center;
      width: min(100%, 720px);
      min-height: 86px;
      margin-top: 22px;
      border: 1px solid rgba(255, 170, 45, 0.72);
      border-radius: 22px;
      padding: 14px 18px;
      color: #2d1903;
      background:
        radial-gradient(circle at 18% 0%, rgba(255,255,255,.7), transparent 35%),
        linear-gradient(135deg, #ffd35a, #ff9f1c 62%, #ed7625);
      box-shadow: 0 18px 42px rgba(230, 124, 25, 0.3);
      text-decoration: none;
      transition: transform 160ms ease, box-shadow 160ms ease, filter 160ms ease;
    }
    .affiliate-cta:hover {
      transform: translateY(-2px);
      filter: brightness(1.03);
      box-shadow: 0 22px 52px rgba(230,124,25,.38);
    }
    .affiliate-cta-icon {
      display: grid;
      place-items: center;
      width: 48px;
      height: 48px;
      border-radius: 16px;
      color: #fff8eb;
      background: #2f1b08;
      font-size: 25px;
      font-weight: 950;
    }
    .affiliate-cta-copy { display: grid; gap: 3px; min-width: 0; }
    .affiliate-cta-copy strong { font-size: 22px; line-height: 1.1; }
    .affiliate-cta-copy small { color: rgba(45,25,3,.74); font-size: 13px; font-weight: 750; line-height: 1.4; }
    .guide-links {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 16px;
    }
    .guide-links a {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-height: 48px;
      border: 1px solid var(--control-line);
      border-radius: 15px;
      padding: 10px 12px;
      color: var(--text);
      background: var(--surface);
      text-align: center;
      font-size: 13px;
      font-weight: 900;
      text-decoration: none;
    }
    .guide-links a:hover { border-color: var(--accent); background: var(--surface-strong); }
    .guide-card {
      display: grid;
      grid-template-columns: minmax(240px, 0.72fr) minmax(420px, 1.28fr);
      gap: 20px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 28px;
      padding: 22px;
      background: var(--panel);
      box-shadow: var(--shadow);
      margin: 18px 0;
    }
    .guide-copy h2 { margin: 0 0 12px; font-size: clamp(26px, 3vw, 42px); letter-spacing: -0.05em; }
    .guide-copy ol { margin: 0; padding-left: 20px; color: var(--text-soft); line-height: 1.65; font-weight: 700; }
    .guide-action-step { margin-bottom: 8px; }
    .guide-action-step a {
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      margin-left: 8px;
      padding: 4px 10px;
      border: 1px solid var(--accent);
      border-radius: 999px;
      color: var(--accent-contrast);
      background: var(--accent);
      font-size: 12px;
      font-weight: 950;
      line-height: 1.2;
      white-space: nowrap;
    }
    .affiliate-inline-link { color: var(--accent-contrast); }
    .guide-section-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      justify-self: start;
      min-height: 44px;
      border: 1px solid var(--control-line);
      border-radius: 14px;
      padding: 10px 14px;
      color: var(--text);
      background: var(--surface);
      font-size: 13px;
      font-weight: 900;
      text-decoration: none;
    }
    .guide-section-link:hover { border-color: var(--accent); background: var(--surface-strong); }
    .kicker { margin: 0 0 8px; color: var(--accent); font-size: 12px; font-weight: 950; letter-spacing: 0.14em; text-transform: uppercase; }
    .guide-shot {
      position: relative;
      min-height: 360px;
      border: 1px solid rgba(0,0,0,.12);
      border-radius: 22px;
      overflow: hidden;
      background: #f7f7f7;
      color: #171717;
      box-shadow: 0 22px 60px rgba(0,0,0,.22);
      font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .shot-sidebar {
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      width: 170px;
      padding: 18px 16px;
      border-right: 1px solid #ddd;
      background: #fbfbfb;
      display: grid;
      align-content: start;
      gap: 18px;
      color: #6b6b78;
      font-weight: 700;
    }
    .shot-sidebar strong { color: #080808; font-size: 20px; }
    .shot-sidebar .active-side {
      margin-left: -8px;
      margin-right: -8px;
      padding: 8px;
      border-radius: 12px;
      background: #ededed;
      color: #151515;
    }
    .shot-main { margin-left: 170px; margin-right: 280px; min-height: 360px; padding: 18px 22px; background: #fff; }
    .shot-top { font-weight: 800; font-size: 18px; margin-bottom: 56px; }
    .script-zone {
      position: relative;
      height: 174px;
      border-radius: 18px;
      background: linear-gradient(#fff,#fff) padding-box, linear-gradient(135deg,#38bdf8,#f472b6,#fb923c) border-box;
      border: 3px solid transparent;
      padding: 24px;
    }
    .script-zone p { color: #8a8a96; font-size: 18px; margin: 24px 0 0; }
    .voice-pill { display: inline-flex; padding: 9px 13px; border: 2px solid #0f172a; border-radius: 999px; background: #fff; font-weight: 800; }
    .shot-settings {
      position: absolute;
      right: 0;
      top: 0;
      bottom: 0;
      width: 280px;
      padding: 70px 16px 18px;
      border-left: 1px solid #ddd;
      background: #fff;
      display: grid;
      align-content: start;
      gap: 14px;
    }
    .shot-settings strong { font-size: 18px; margin-bottom: 10px; }
    .setting-row { padding: 14px 12px; border: 1px solid #dedee5; border-radius: 14px; background: #fff; font-weight: 800; }
    .billing-main, .api-main { margin-left: 170px; min-height: 360px; padding: 30px; background: #fff; }
    .billing-main h3, .api-main h3 { margin: 0 0 18px; font-size: 34px; font-weight: 500; }
    .billing-tabs, .api-tabs { display: flex; gap: 18px; padding-bottom: 12px; border-bottom: 1px solid #e5e5e5; color: #777985; font-weight: 700; }
    .billing-tabs .active, .api-tabs .active { color: #111; border: 2px solid #111; border-radius: 10px; padding: 7px 12px; margin-top: -9px; }
    .balance-box { width: min(520px, 92%); margin-top: 36px; border: 1px solid #ddd; border-radius: 20px; padding: 22px; box-shadow: 0 8px 20px rgba(0,0,0,.05); }
    .balance-box span { color: #7a7d89; font-weight: 700; }
    .balance-box strong { display: block; font-size: 32px; margin: 8px 0; }
    .balance-box button, .api-main button { border: 0; border-radius: 12px; padding: 13px 18px; background: #050505; color: #fff; font-weight: 800; font-size: 17px; }
    .pricing-row { margin-top: 40px; width: 290px; border: 1px solid #e1e1e1; border-radius: 18px; padding: 22px; display: grid; gap: 14px; }
    .pricing-row b { font-size: 28px; }
    .pricing-row small { color: #7a7d89; font-size: 16px; }
    .api-main p { color: #777985; font-weight: 700; max-width: 520px; line-height: 1.5; }
    .api-main button { position: absolute; right: 26px; top: 142px; }
    .key-row { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-top: 86px; border-top: 1px solid #e5e5e5; padding-top: 18px; font-weight: 700; }
    .callout { box-shadow: 0 0 0 4px rgba(244,114,182,.35), 0 0 0 8px rgba(56,189,248,.22) !important; }
    .note-pin {
      position: absolute;
      z-index: 5;
      padding: 9px 12px;
      border-radius: 999px;
      color: #fff;
      background: #e11d48;
      font-weight: 950;
      font-size: 13px;
      box-shadow: 0 10px 24px rgba(225,29,72,.24);
    }
    .note-script { left: 250px; top: 94px; }
    .note-voice { right: 126px; top: 118px; }
    .note-model { right: 56px; top: 220px; }
    .voice-shot { min-height: 420px; background: #fff; }
    .voice-main {
      position: relative;
      margin-left: 170px;
      min-height: 420px;
      padding: 18px 22px 20px;
      background: #fff;
    }
    .voice-breadcrumb { color: #5f6068; font-size: 16px; font-weight: 700; margin-bottom: 20px; }
    .voice-main h3 { margin: 0 0 14px; font-size: 32px; font-weight: 500; }
    .voice-tabs {
      display: flex;
      gap: 18px;
      align-items: center;
      margin-bottom: 14px;
      font-weight: 750;
      color: #777985;
    }
    .voice-tabs span { padding: 9px 11px; border-radius: 11px; }
    .voice-tabs .active { color: #121212; border: 1px solid #d7d7d7; border-bottom: 3px solid #121212; }
    .voice-search {
      height: 42px;
      border: 1px solid #e3e3e8;
      border-radius: 14px;
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 0 14px;
      color: #9798a3;
      font-size: 20px;
      font-weight: 700;
    }
    .voice-search span { color: #222; font-size: 18px; font-weight: 500; }
    .voice-filters { display: flex; gap: 8px; overflow: hidden; margin: 12px 0 18px; white-space: nowrap; }
    .voice-filters span {
      border: 1px solid #e2e2e7;
      border-radius: 12px;
      padding: 9px 12px;
      color: #5f6068;
      font-weight: 750;
      background: #fff;
    }
    .voice-count { margin: 0 0 10px; color: #6a6b73; font-weight: 800; }
    .voice-list { display: grid; gap: 0; border-radius: 16px; overflow: hidden; }
    .voice-row {
      position: relative;
      display: grid;
      grid-template-columns: 40px minmax(180px, 1fr) 92px 82px 72px 118px 34px;
      align-items: center;
      gap: 10px;
      min-height: 58px;
      padding: 6px 10px;
      font-size: 14px;
      color: #242424;
    }
    .voice-row.featured { background: #f0f0f2; }
    .muted-row { color: #4b4c54; }
    .voice-avatar {
      width: 34px;
      height: 34px;
      border-radius: 50%;
      background: radial-gradient(circle at 30% 30%, #90e0ef, #a78bfa 45%, #475569);
    }
    .voice-avatar.dark { background: radial-gradient(circle at 35% 35%, #fde68a, #7c2d12 50%, #111827); }
    .voice-avatar.blue { background: radial-gradient(circle at 35% 35%, #bae6fd, #64748b 55%, #111827); }
    .voice-title { display: grid; gap: 2px; min-width: 0; }
    .voice-title strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
    .voice-title span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #7a7b84; font-weight: 650; }
    .voice-dots {
      width: 30px;
      height: 30px;
      border: 0;
      border-radius: 10px;
      background: #d7d7dd;
      color: #252525;
      font-size: 20px;
      font-weight: 900;
    }
    .voice-menu {
      position: absolute;
      right: 18px;
      top: 232px;
      width: 176px;
      border: 1px solid #dedee4;
      border-radius: 14px;
      background: #fff;
      box-shadow: 0 16px 34px rgba(0,0,0,.18);
      padding: 8px;
      display: grid;
      gap: 2px;
      z-index: 4;
    }
    .voice-menu div {
      padding: 9px 10px;
      border-radius: 10px;
      font-size: 14px;
      font-weight: 750;
      color: #1f1f1f;
    }
    .voice-copy-row {
      background: rgba(244,114,182,.08);
      outline: 3px solid rgba(244,114,182,.45);
    }
    .note-voice-id { right: 38px; top: 184px; }
    .note-credit { left: 300px; top: 195px; }
    .note-key { right: 44px; top: 94px; }
    @media (max-width: 920px) {
      .guide-card { grid-template-columns: 1fr; }
      .guide-hero h1 { white-space: normal; }
      .guide-links { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .guide-shot { overflow-x: auto; }
    }
    @media (max-width: 560px) {
      .affiliate-cta { grid-template-columns: auto minmax(0, 1fr); }
      .guide-links { grid-template-columns: 1fr; }
    }
""",
    )


UPLOAD_GUIDE_DOCS = {
    "youtube": {
        "path": REPO_ROOT / "docs" / "upload" / "youtube-api-upload.md",
        "path_en": REPO_ROOT / "docs" / "upload" / "youtube-api-upload.en.md",
        "kicker": "YouTube API",
        "actions": [
            ("Mở Google Cloud Console", "Open Google Cloud Console", "https://console.cloud.google.com/"),
            ("Docs upload video", "Video upload docs", "https://developers.google.com/youtube/v3/guides/uploading_a_video"),
            ("Docs OAuth", "OAuth docs", "https://developers.google.com/youtube/v3/guides/authentication"),
        ],
    },
    "facebook": {
        "path": REPO_ROOT / "docs" / "upload" / "facebook-api-upload.md",
        "path_en": REPO_ROOT / "docs" / "upload" / "facebook-api-upload.en.md",
        "kicker": "Facebook Reels API",
        "actions": [
            ("Mở Meta for Developers", "Open Meta for Developers", "https://developers.facebook.com/"),
            ("Graph API Explorer", "Graph API Explorer", "https://developers.facebook.com/tools/explorer/"),
            ("Token Debugger", "Token Debugger", "https://developers.facebook.com/tools/debug/accesstoken/"),
        ],
    },
    "instagram": {
        "path": REPO_ROOT / "docs" / "upload" / "instagram-api-upload.md",
        "path_en": REPO_ROOT / "docs" / "upload" / "instagram-api-upload.en.md",
        "kicker": "Instagram Reels API + R2",
        "actions": [
            ("Mở Meta for Developers", "Open Meta for Developers", "https://developers.facebook.com/"),
            ("Instagram API docs", "Instagram API docs", "https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api"),
            ("Cloudflare R2 docs", "Cloudflare R2 docs", "https://developers.cloudflare.com/r2/buckets/public-buckets/"),
        ],
    },
}


def markdown_doc_url(target: str, markdown_path: Path) -> str:
    target = str(target or "").strip()
    if re.match(r"^https?://", target):
        return target
    asset_path = (markdown_path.parent / target).resolve()
    try:
        relative = asset_path.relative_to(REPO_ROOT)
    except ValueError:
        return "#"
    return "/" + quote(relative.as_posix(), safe="/._-")


def render_inline_markdown(text: str) -> str:
    rendered = html.escape(text)
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    rendered = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", rendered)

    def link_repl(match: re.Match) -> str:
        url = match.group(0)
        trailing = ""
        while url and url[-1] in ".,)":
            trailing = url[-1] + trailing
            url = url[:-1]
        escaped_url = html.escape(url, quote=True)
        return f'<a class="text-link" href="{escaped_url}" target="_blank" rel="noreferrer">{html.escape(url)}</a>{trailing}'

    return re.sub(r"https?://[^\s<]+", link_repl, rendered)


def render_markdown_blocks(lines: list[str], markdown_path: Path) -> str:
    blocks: list[str] = []
    paragraph: list[str] = []
    image_pattern = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")

    def flush_paragraph() -> None:
        if paragraph:
            text = " ".join(part.strip() for part in paragraph if part.strip())
            if text:
                blocks.append(f"<p>{render_inline_markdown(text)}</p>")
            paragraph.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            index += 1
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            code = html.escape("\n".join(code_lines))
            blocks.append(f"<pre><code>{code}</code></pre>")
            continue

        heading_match = re.match(r"^(#{3,6})\s+(.+)$", stripped)
        if heading_match:
            flush_paragraph()
            level = min(len(heading_match.group(1)), 4)
            blocks.append(f"<h{level}>{render_inline_markdown(heading_match.group(2))}</h{level}>")
            index += 1
            continue

        image_match = image_pattern.match(stripped)
        if image_match:
            flush_paragraph()
            images: list[str] = []
            while index < len(lines):
                next_match = image_pattern.match(lines[index].strip())
                if not next_match:
                    break
                alt = next_match.group(1).strip()
                target = next_match.group(2).strip()
                asset_path = (markdown_path.parent / target).resolve()
                if re.match(r"^https?://", target) or asset_path.is_file():
                    src = markdown_doc_url(target, markdown_path)
                    images.append(
                        f'<figure><img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}" /></figure>'
                    )
                index += 1
            if images:
                grid_class = "single" if len(images) == 1 else "multi"
                blocks.append(f'<div class="guide-image-grid {grid_class}">' + "".join(images) + "</div>")
            continue

        if re.match(r"^\d+\.\s+", stripped):
            flush_paragraph()
            items: list[str] = []
            while index < len(lines):
                item_match = re.match(r"^\d+\.\s+(.+)$", lines[index].strip())
                if not item_match:
                    break
                items.append(f"<li>{render_inline_markdown(item_match.group(1))}</li>")
                index += 1
            blocks.append("<ol>" + "".join(items) + "</ol>")
            continue

        if re.match(r"^[-*]\s+", stripped):
            flush_paragraph()
            items = []
            while index < len(lines):
                item_match = re.match(r"^[-*]\s+(.+)$", lines[index].strip())
                if not item_match:
                    break
                items.append(f"<li>{render_inline_markdown(item_match.group(1))}</li>")
                index += 1
            blocks.append("<ul>" + "".join(items) + "</ul>")
            continue

        paragraph.append(line)
        index += 1

    flush_paragraph()
    return "\n".join(blocks)


def split_markdown_guide(markdown_text: str) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    lines = markdown_text.splitlines()
    title = "Hướng dẫn upload"
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        lines = lines[1:]

    lead: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    section_title = ""
    section_lines: list[str] = []

    for line in lines:
        if line.startswith("## "):
            if section_title or section_lines:
                sections.append((section_title, section_lines))
            section_title = line[3:].strip()
            section_lines = []
            continue
        if section_title:
            section_lines.append(line)
        else:
            lead.append(line)

    if section_title or section_lines:
        sections.append((section_title, section_lines))
    return title, lead, sections


def render_social_upload_guide_html(platform: str) -> bytes:
    platform = platform.strip().lower()
    guide = UPLOAD_GUIDE_DOCS.get(platform)
    language = current_ui_language()
    is_en = language == "en"
    if not guide:
        missing_title = "Upload guide not found" if is_en else "Không tìm thấy hướng dẫn"
        missing_back = "← Back to Upload Center" if is_en else "← Quay lại Upload Center"
        missing_body = "Upload guide not found." if is_en else "Không tìm thấy hướng dẫn upload."
        return render_page_shell(
            title=missing_title,
            body=(
                f'<main class="social-guide-page"><a class="app-back" href="/upload"><img src="/web/aurexvideo-logo.png" alt="" />'
                f'<span><strong>Aurex</strong><small>{html.escape(missing_back)}</small></span></a>'
                f'<h1>{html.escape(missing_body)}</h1></main>'
            ),
        )

    markdown_path = guide.get("path_en") if is_en else guide["path"]
    if not Path(markdown_path).is_file():
        markdown_path = guide["path"]
    markdown_text = Path(markdown_path).read_text(encoding="utf-8")
    title, lead_lines, sections = split_markdown_guide(markdown_text)
    lead_html = render_markdown_blocks(lead_lines, Path(markdown_path))
    actions_html = "".join(
        f'<a class="guide-action-link {"small-link" if index else ""}" href="{html.escape(url, quote=True)}" target="_blank" rel="noreferrer">{html.escape(label_en if is_en else label_vi)}</a>'
        for index, (label_vi, label_en, url) in enumerate(guide["actions"])
    )
    section_kicker = "Guide" if is_en else "Hướng dẫn"
    section_html = "\n".join(
        f"""
    <section class="md-guide-section">
      <div class="md-section-head">
        <p class="kicker">{html.escape(section_kicker)}</p>
        <h2>{render_inline_markdown(section_title)}</h2>
      </div>
      <div class="md-section-body">
        {render_markdown_blocks(section_lines, Path(markdown_path))}
      </div>
    </section>
"""
        for section_title, section_lines in sections
    )

    back_label = "← Back to Upload Center" if is_en else "← Quay lại Upload Center"
    body = f"""
  <main class="social-guide-page md-guide-page">
    <header class="guide-hero">
      <a class="app-back" href="/upload"><img src="/web/aurexvideo-logo.png" alt="" /><span><strong>Aurex</strong><small>{html.escape(back_label)}</small></span></a>
      <p class="kicker">{html.escape(str(guide["kicker"]))}</p>
      <h1>{html.escape(title)}</h1>
      <div class="guide-lead">{lead_html}</div>
      <div class="guide-links">{actions_html}</div>
    </header>
    <article class="md-guide">
{section_html}
    </article>
  </main>
"""

    return render_page_shell(
        title=title,
        body=body,
        extra_style="""
    body { overflow: auto; }
    .social-guide-page {
      width: min(100%, 1280px);
      margin: 0 auto;
    }
    .guide-hero {
      max-width: none;
      margin-bottom: 24px;
      border: 1px solid var(--line);
      border-radius: 30px;
      background: var(--panel);
      box-shadow: var(--shadow);
      padding: clamp(22px, 3vw, 34px);
    }
    .guide-hero h1 {
      max-width: none;
      font-size: clamp(28px, 3vw, 46px);
      line-height: 1;
      white-space: normal;
      letter-spacing: -0.055em;
    }
    .guide-lead {
      max-width: 980px;
      color: var(--text-soft);
      font-size: 16px;
      line-height: 1.7;
      font-weight: 720;
    }
    .guide-lead p { margin: 10px 0 0; }
    .back-link { margin-bottom: 18px; }
    .guide-links {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 20px;
    }
    .guide-action-link { min-height: 42px; }
    .md-guide {
      display: grid;
      gap: 18px;
    }
    .md-guide-section {
      border: 1px solid var(--line);
      border-radius: 28px;
      padding: clamp(18px, 2.4vw, 28px);
      background: var(--panel);
      box-shadow: var(--shadow);
    }
    .md-section-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 16px;
      border-bottom: 1px solid var(--line);
      padding-bottom: 14px;
    }
    .md-section-head .kicker {
      flex: 0 0 auto;
      margin: 0;
    }
    .md-section-head h2 {
      flex: 1 1 auto;
      margin: 0;
      color: var(--text);
      font-size: clamp(24px, 2.35vw, 38px);
      line-height: 1.05;
      letter-spacing: -0.045em;
      text-align: right;
    }
    .md-section-body {
      display: grid;
      gap: 14px;
      color: var(--text-soft);
      font-size: 16px;
      line-height: 1.7;
      font-weight: 720;
    }
    .md-section-body p,
    .md-section-body ol,
    .md-section-body ul { margin: 0; }
    .md-section-body ol,
    .md-section-body ul {
      padding-left: 24px;
    }
    .md-section-body li + li { margin-top: 8px; }
    .md-section-body h3,
    .md-section-body h4 {
      margin: 14px 0 0;
      color: var(--text);
      font-size: clamp(19px, 1.7vw, 26px);
      letter-spacing: -0.035em;
    }
    .md-section-body strong { color: var(--text); font-weight: 950; }
    .md-guide code {
      border-radius: 8px;
      background: rgba(164, 98, 42, 0.12);
      color: #7a3f0c;
      padding: 0.05em 0.35em;
      font-weight: 850;
    }
    .md-guide pre {
      margin: 0;
      overflow-x: auto;
      border: 1px solid var(--control-line-soft);
      border-radius: 18px;
      padding: 16px;
      background: rgba(255, 251, 244, 0.92);
      color: var(--text);
      box-shadow: inset 0 1px 0 rgba(255,255,255,.55);
    }
    .md-guide pre code {
      display: block;
      padding: 0;
      background: transparent;
      color: inherit;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 13px;
      line-height: 1.55;
      white-space: pre;
    }
    .md-guide a.text-link {
      display: inline;
      padding: 0;
      border-radius: 0;
      color: #9b521b;
      background: transparent;
      text-decoration: underline;
      text-decoration-thickness: 2px;
      text-underline-offset: 3px;
      font-weight: 850;
    }
    .guide-image-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      align-items: start;
    }
    .guide-image-grid.single { grid-template-columns: minmax(0, 1fr); }
    .guide-image-grid figure {
      margin: 0;
      border: 1px solid rgba(79, 57, 31, 0.15);
      border-radius: 18px;
      padding: 10px;
      background: #fff;
      box-shadow: 0 18px 42px rgba(78, 54, 28, 0.13);
    }
    .guide-image-grid img {
      display: block;
      width: 100%;
      max-height: 640px;
      object-fit: contain;
      border-radius: 12px;
      background: #fff;
    }
    body:not(.theme-light) .md-guide code { color: #ffd59a; background: rgba(0,0,0,.38); }
    body:not(.theme-light) .md-guide pre { background: rgba(0,0,0,.38); }
    body:not(.theme-light) .guide-image-grid figure {
      border-color: rgba(255,255,255,.14);
      box-shadow: 0 18px 42px rgba(0,0,0,.28);
    }
    @media (max-width: 1040px) {
      .guide-hero h1 { white-space: normal; }
      .md-section-head {
        align-items: flex-start;
        flex-direction: column;
      }
      .md-section-head h2 { text-align: left; }
      .guide-image-grid { grid-template-columns: 1fr; }
    }
""",
    )



def ui_icon(name: str, class_name: str = "btn-icon") -> str:
    """Lucide-style outline icon for light/dark themes."""
    glyphs = {
        "sparkles": (
            '<path d="M11.017 2.814a1 1 0 0 1 1.966 0l1.22 4.684a1 1 0 0 0 .949.694h4.969a1 1 0 0 1 .599 1.807l-4.012 2.897a1 1 0 0 0-.362 1.118l1.53 4.694a1 1 0 0 1-1.538 1.118L12.6 16.545a1 1 0 0 0-1.176 0l-4.012 2.897a1 1 0 0 1-1.538-1.118l1.53-4.694a1 1 0 0 0-.362-1.118L3.03 10a1 1 0 0 1 .599-1.807h4.969a1 1 0 0 0 .949-.694z"/>'
        ),
        "mic": (
            '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>'
            '<path d="M19 10v2a7 7 0 0 1-14 0v-2"/>'
            '<line x1="12" x2="12" y1="19" y2="22"/>'
        ),
        "audio-lines": (
            '<path d="M2 10v3"/><path d="M6 6v11"/><path d="M10 3v18"/>'
            '<path d="M14 8v7"/><path d="M18 5v13"/><path d="M22 10v3"/>'
        ),
        "upload": (
            '<path d="M12 3v12"/><path d="m17 8-5-5-5 5"/>'
            '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        ),
        "key": (
            '<path d="m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4"/>'
            '<path d="m21 2-9.6 9.6"/>'
            '<circle cx="7.5" cy="15.5" r="5.5"/>'
        ),
        "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
        "square": '<rect width="14" height="14" x="5" y="5" rx="2"/>',
        "search": (
            '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>'
        ),
        "help": (
            '<circle cx="12" cy="12" r="10"/>'
            '<path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>'
            '<path d="M12 17h.01"/>'
        ),
        "settings": (
            '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
            '<circle cx="12" cy="12" r="3"/>'
        ),
        "gauge": (
            '<path d="m12 14 4-4"/>'
            '<path d="M3.34 19a10 10 0 1 1 17.32 0"/>'
        ),
        "volume": (
            '<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/>'
            '<path d="M16 9a5 5 0 0 1 0 6"/>'
            '<path d="M19.364 18.364a9 9 0 0 0 0-12.728"/>'
        ),
        "smartphone": (
            '<rect width="14" height="20" x="5" y="2" rx="2" ry="2"/>'
            '<path d="M12 18h.01"/>'
        ),
        "cpu": (
            '<rect width="16" height="16" x="4" y="4" rx="2"/>'
            '<rect width="6" height="6" x="9" y="9" rx="1"/>'
            '<path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/>'
        ),
        "image": (
            '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>'
            '<circle cx="9" cy="9" r="2"/>'
            '<path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>'
        ),
        "at-sign": (
            '<circle cx="12" cy="12" r="4"/>'
            '<path d="M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-4 8"/>'
        ),
        "pencil": (
            '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/>'
            '<path d="m15 5 4 4"/>'
        ),
        "moon": (
            '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>'
        ),
        "sun": (
            '<circle cx="12" cy="12" r="4"/>'
            '<path d="M12 2v2"/><path d="M12 20v2"/>'
            '<path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/>'
            '<path d="M2 12h2"/><path d="M20 12h2"/>'
            '<path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>'
        ),
        "refresh": (
            '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
            '<path d="M21 3v5h-5"/>'
            '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>'
            '<path d="M8 16H3v5"/>'
        ),
        "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
        "check": '<path d="M20 6 9 17l-5-5"/>',
        "external-link": (
            '<path d="M15 3h6v6"/><path d="M10 14 21 3"/>'
            '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>'
        ),
        "copy": (
            '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>'
            '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'
        ),
        "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
        "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
        "arrow-left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
        "arrow-down-right": '<path d="m7 7 10 10"/><path d="M17 7v10H7"/>',
        "list": '<path d="M3 12h.01"/><path d="M3 18h.01"/><path d="M3 6h.01"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M8 6h13"/>',
        "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
        "users": (
            '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>'
            '<circle cx="9" cy="7" r="4"/>'
            '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/>'
            '<path d="M16 3.13a4 4 0 0 1 0 7.75"/>'
        ),
        "braces": (
            '<path d="M8 3H7a2 2 0 0 0-2 2v4a2 2 0 0 1-2 2 2 2 0 0 1 2 2v4a2 2 0 0 0 2 2h1"/>'
            '<path d="M16 3h1a2 2 0 0 1 2 2v4a2 2 0 0 0 2 2 2 2 0 0 0-2 2v4a2 2 0 0 1-2 2h-1"/>'
        ),
        "message": (
            '<path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z"/>'
        ),
    }
    inner = glyphs.get(name)
    if not inner:
        raise KeyError(f"Unknown UI icon: {name}")
    cls = html.escape(class_name, quote=True)
    return (
        f'<span class="{cls}" aria-hidden="true">'
        f'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" '
        f'stroke-linecap="round" stroke-linejoin="round">{inner}</svg>'
        f"</span>"
    )


def brand_icon(name: str, class_name: str = "platform-brand-icon") -> str:
    """Filled brand marks for platform headers."""
    marks = {
        "youtube": (
            '<path fill="#FF0000" d="M23.5 6.19a3.02 3.02 0 0 0-2.12-2.14C19.54 3.5 12 3.5 12 3.5s-7.54 0-9.38.55A3.02 3.02 0 0 0 .5 6.19 31.8 31.8 0 0 0 0 12a31.8 31.8 0 0 0 .5 5.81 3.02 3.02 0 0 0 2.12 2.14c1.84.55 9.38.55 9.38.55s7.54 0 9.38-.55a3.02 3.02 0 0 0 2.12-2.14A31.8 31.8 0 0 0 24 12a31.8 31.8 0 0 0-.5-5.81z"/>'
            '<path fill="#fff" d="M9.75 15.02V8.98L15.5 12z"/>'
        ),
        "facebook": (
            '<path fill="#1877F2" d="M24 12.07C24 5.41 18.63 0 12 0S0 5.41 0 12.07C0 18.1 4.39 23.1 10.13 24v-8.44H7.08v-3.49h3.05V9.41c0-3.02 1.8-4.7 4.54-4.7 1.32 0 2.7.24 2.7.24v2.97h-1.52c-1.5 0-1.97.93-1.97 1.89v2.26h3.34l-.53 3.49h-2.81V24C19.61 23.1 24 18.1 24 12.07z"/>'
        ),
        "binance": (
            '<path fill="#F3BA2F" d="M12 2.5 9.6 4.9 7.2 2.5 4.8 4.9 2.4 2.5v4.8L0 9.7l2.4 2.4L0 14.5v4.8L2.4 17l2.4 2.4-2.4 2.4 2.4 2.4 2.4-2.4 2.4 2.4 2.4-2.4 2.4 2.4 2.4-2.4 2.4 2.4 2.4-2.4 2.4 2.4 2.4-2.4V14.5l-2.4-2.4 2.4-2.4V4.9L19.2 7.3 16.8 4.9 14.4 7.3 12 4.9 9.6 2.5zm0 6.6L14.4 11.5 12 13.9 9.6 11.5 12 9.1z"/>'
        ),
        "instagram": (
            '<rect x="2" y="2" width="20" height="20" rx="6" fill="#E1306C"/>'
            '<circle cx="12" cy="12" r="4.3" fill="none" stroke="#fff" stroke-width="1.8"/>'
            '<circle cx="17.5" cy="6.7" r="1.1" fill="#fff"/>'
        ),
        "threads": (
            '<circle cx="12" cy="12" r="10" fill="#111827"/>'
            '<path fill="#fff" d="M7 7h10v2h-4v8h-2V9H7z"/>'
        ),
    }
    inner = marks.get(name)
    if not inner:
        raise KeyError(f"Unknown brand icon: {name}")
    cls = html.escape(class_name, quote=True)
    return (
        f'<span class="{cls}" aria-hidden="true">'
        f'<svg viewBox="0 0 24 24" role="img">{inner}</svg>'
        f"</span>"
    )


def render_home_html(selected_project: str | None = None, preview_update: bool = False) -> bytes:
    projects = list_projects()
    account = current_account()
    account_name = str(account.get("name") or account.get("email") or "Aurex user").strip()
    account_email = str(account.get("email") or "").strip()
    avatar_url = str(account.get("avatarUrl") or "").strip()
    avatar_initial = (account_name[:1] or "F").upper()
    entitlement = account.get("entitlement") if isinstance(account.get("entitlement"), dict) else {}
    trial_limit = entitlement.get("trial_export_limit", entitlement.get("trial_exports_limit", entitlement.get("export_limit", 3)))
    trial_used = entitlement.get("trial_exports_used", entitlement.get("exports_used", 0))
    product_id = str(entitlement.get("product_id") or "").strip()
    entitlement_plan = str(entitlement.get("plan") or "").strip()
    plan_name = product_id or entitlement_plan or "AurexVideo Trial"
    normalized_plan = f"{product_id} {entitlement_plan}".strip().lower()
    has_pro_product = "pro" in normalized_plan and "trial" not in normalized_plan
    is_pro_plan = entitlement_is_active_pro(entitlement)
    is_expired_pro = has_pro_product and not is_pro_plan
    display_plan_name = "Pro" if is_pro_plan else ("Pro · hết hạn" if is_expired_pro else plan_name)
    access_status = "Đã hết hạn" if is_expired_pro else str(entitlement.get("status") or "Active")
    branding_locked = trial_branding_required(account)
    branding_lock_attributes = (
        'disabled aria-disabled="true" data-trial-locked="true" title="Trial luôn dùng Logo + brand Aurex"'
        if branding_locked
        else ""
    )
    avatar_markup = (
        f'<img src="{html.escape(avatar_url, quote=True)}" alt="{html.escape(account_name, quote=True)}" />'
        if avatar_url
        else f'<span>{html.escape(avatar_initial)}</span>'
    )
    trial_usage_markup = (
        ""
        if has_pro_product
        else (
            '<div class="account-popover-metric">'
            f'<strong id="trialExportUsage">{html.escape(str(trial_used))}/{html.escape(str(trial_limit))}</strong>'
            '<span>Lượt xuất trial</span>'
            '</div>'
        )
    )
    account_upgrade_markup = (
        ""
        if is_pro_plan
        else (
            '<button class="account-upgrade-button" id="accountUpgradeButton" type="button">'
            f'{ui_icon("sparkles", "account-upgrade-icon")}<strong>{"Gia hạn Pro" if is_expired_pro else "Nâng cấp Pro"}</strong>'
            f'<small>{"Chọn lại gói tháng hoặc gói năm" if is_expired_pro else "Mở khoá nhân vật riêng và toàn bộ tính năng"}</small>'
            '</button>'
        )
    )
    metrics_class = "account-popover-metrics is-pro" if is_pro_plan else "account-popover-metrics"
    popover_avatar_class = "account-popover-avatar is-pro" if is_pro_plan else "account-popover-avatar"
    plan_badge_class = "account-plan-badge is-pro" if is_pro_plan else "account-plan-badge"
    project_names = {project["name"] for project in projects}
    selected_project = selected_project if selected_project in project_names else (projects[0]["name"] if projects else "")

    rows = []
    for project in projects:
        name = html.escape(project["name"])
        href = html.escape(project["url"])
        brand = html.escape(project.get("brand") or "", quote=True)
        character = html.escape(project.get("character") or "", quote=True)
        has_video = bool(project["video_url"])
        video_link = (
            f'<a class="small-link icon-btn" href="{html.escape(player_url(project["name"]))}">{ui_icon("play")}<span>Mở</span></a>'
            if has_video
            else f'<span class="icon-btn disabled">{ui_icon("play")}<span>Mở</span></span>'
        )
        status = "Đã render" if has_video else ("Chưa render" if project["has_script"] else "Thiếu script")
        status_class = "ok" if has_video else ("bad" if project["has_script"] else "bad")
        selected_class = " selected" if project["name"] == selected_project else ""
        social_detail = project.get("social_status_detail") if isinstance(project.get("social_status_detail"), dict) else {}
        social_label = str(project.get("social_status") or social_detail.get("label") or "").strip()
        social_state = str(social_detail.get("state") or "").strip().lower()
        social_is_scheduled = social_label == "Đã lên lịch" or (
            social_label not in {"Published", "Pending"}
            and (social_detail.get("scheduled") is True or social_state == "scheduled")
        )
        social_schedule = (
            f'<small class="social-schedule-time" datetime="{html.escape(project["social_status_scheduled_at"], quote=True)}">'
            f'{html.escape(project["social_status_scheduled_label"])}</small>'
            if social_is_scheduled and project.get("social_status_scheduled_label")
            else ""
        )
        social_published = (
            f'<small class="social-schedule-time" datetime="{html.escape(project["social_status_published_at"], quote=True)}">'
            f'{html.escape(project["social_status_published_label"])}</small>'
            if social_label == "Published" and project.get("social_status_published_label")
            else ""
        )
        rows.append(
            f"""
            <li class="project-row{selected_class}" data-project="{name}" data-brand="{brand}" data-character="{character}">
              <div class="project-main">
                <span class="project-name">{name}</span>
                <div class="project-meta-row">
                  <span class="project-slide-count">{project["script_count"] or "?"} câu</span>
                  <button class="project-rename-button" data-project="{name}" type="button" aria-label="Đổi tên {name}" title="Đổi tên project">{ui_icon("pencil", "project-rename-icon")}</button>
                  <button class="project-duplicate-button" data-project="{name}" type="button" aria-label="Nhân bản {name}" title="Nhân bản dự án">{ui_icon("copy", "project-duplicate-icon")}</button>
                </div>
              </div>
              <span class="status-pill {status_class}">{status}</span>
              <div class="social-status-cell" title="{html.escape(project['social_status_title'], quote=True)}">
                <span class="status-pill {project['social_status_class']} social-status">{html.escape(project['social_status'])}</span>
                {social_schedule}{social_published}
              </div>
              <div class="actions">
                <button class="select-btn icon-btn" type="button" data-project="{name}">{ui_icon("check")}<span>Chọn</span></button>
                <a class="small-link icon-btn" href="{href}">{ui_icon("external-link")}<span>Xem</span></a>
                <button class="copy-script-btn icon-btn" type="button" data-project="{name}" title="Copy script.txt">{ui_icon("copy")}<span>Kịch bản</span></button>
                <span class="row-video-slot">{video_link}</span>
                <button class="delete-output-btn icon-btn" type="button" data-project="{name}">{ui_icon("x")}<span>Xoá</span></button>
              </div>
            </li>
            """
        )

    body = "\n".join(rows) or '<li class="empty">Chưa có dự án nào.</li>'
    return render_page_shell(
        title="AurexVideo",
        body=f"""
  <header class="dashboard-header">
    <div class="brand-lockup">
      <img src="/web/aurexvideo-logo.png" alt="Aurex" class="brand-mark" />
      <div>
        <h1>AurexVideo</h1>
      </div>
    </div>
    <div class="header-tools">
      <a class="refresh-btn icon-btn header-nav-action" href="/new-project">{ui_icon("plus")}<span>Dự án mới</span></a>
      <a class="refresh-btn icon-btn header-nav-action" href="/upload">{ui_icon("upload")}<span>Đăng tải</span></a>
      <button class="refresh-btn icon-btn header-nav-action" id="refreshProjects" type="button">{ui_icon("refresh")}<span>Làm mới</span></button>
      <button class="refresh-btn icon-btn header-nav-action theme-toggle" type="button" data-theme-toggle>{ui_icon("moon")}<span>Giao diện</span></button>
      <button class="refresh-btn icon-btn header-nav-action update-available-button" id="updateAvailableButton" type="button"{' title="Xem thử vị trí thông báo cập nhật"' if preview_update else ' hidden'}>{ui_icon("arrow-up")}<span>Cập nhật bản mới</span></button>
      <button class="refresh-btn icon-btn header-nav-action settings-button" id="openSettingsButton" type="button">{ui_icon("settings")}<span>Cài đặt</span></button>
      <span class="update-slot-placeholder" id="updateSlotPlaceholder" aria-hidden="true"{' hidden' if preview_update else ''}></span>
    </div>
    <div class="header-account">
      <div class="header-stat"><strong>{len(projects)}</strong><span>dự án</span></div>
    </div>
  </header>

  <dialog class="plan-picker-dialog" id="upgradePlanDialog">
    <div class="plan-picker-content">
      <button class="plan-picker-close" id="upgradePlanClose" type="button" aria-label="Đóng">×</button>
      <div class="plan-picker-heading">
        <h2>{"Gia hạn Aurex Pro" if is_expired_pro else "Nâng cấp Aurex Pro"}</h2>
        <p>{"Chọn gói mới để tiếp tục dùng toàn bộ tính năng Pro." if is_expired_pro else "Chọn thời hạn phù hợp. Cả hai gói đều mở khoá toàn bộ tính năng Pro trên 1 thiết bị."}</p>
      </div>
      <div class="plan-picker-options">
        <article class="plan-option monthly">
          <h3>Pro theo tháng</h3>
          <div class="plan-option-price"><strong>99.000đ</strong><span>/ 30 ngày</span></div>
          <p class="plan-option-copy">Thanh toán một lần. Khi hết hạn, bạn chủ động gia hạn ngay trong AurexVideo.</p>
          <button type="button" data-checkout-plan="monthly">Chọn gói tháng</button>
        </article>
        <article class="plan-option yearly">
          <span class="plan-option-badge">Tiết kiệm 58%</span>
          <h3>Pro theo năm</h3>
          <div class="plan-option-price"><strong>499.000đ</strong><span>/ 12 tháng</span></div>
          <p class="plan-option-copy">Phù hợp khi dùng lâu dài, tương đương khoảng 41.600đ mỗi tháng.</p>
          <button type="button" data-checkout-plan="yearly">Chọn gói năm</button>
        </article>
      </div>
      <p class="plan-picker-status" id="upgradePlanStatus" aria-live="polite"></p>
    </div>
  </dialog>

  <main class="dashboard-shell">
    <section class="render-machine panel">
      <div class="render-heading">
        <h2>{ui_icon("sparkles", "render-lead-icon")}<span>Bộ máy render</span></h2>
      </div>

      <div class="selected-box">
        <div>
          <strong id="selectedName">Chưa chọn dự án</strong>
        </div>
      </div>

      <div class="status warn" id="renderStatus" hidden></div>

      <div class="tabs">
        <button class="tab active" data-engine="vieneu" type="button">{ui_icon("mic", "tab-icon")}<span>VieNeu TTS</span></button>
        <button class="tab" data-engine="maziao" type="button">{ui_icon("mic", "tab-icon")}<span>Maziao</span></button>
        <button class="tab" data-engine="edgetts" type="button">{ui_icon("audio-lines", "tab-icon")}<span>Edge TTS</span></button>
        <button class="tab" data-engine="upload" type="button">{ui_icon("upload", "tab-icon")}<span>Upload File</span></button>
      </div>

      <div data-pane="vieneu">
        <div class="vieneu-runtime-control">
          <div class="vieneu-runtime-info">
            <label class="check">
              <input id="vieneuRuntimeToggle" type="checkbox" checked />
              <span>Bật VieNeu-TTS</span>
            </label>
            <a class="vieneu-web-ui-link" href="http://127.0.0.1:7860/" target="_blank" rel="noopener">Mở VieNeu Web UI ↗</a>
          </div>
          <span class="vieneu-runtime-state" id="vieneuRuntimeState" aria-live="polite">Đang kiểm tra…</span>
        </div>
        <div class="field">
          <span class="field-label">{ui_icon("message", "field-icon")}<span>Giọng VieNeu</span></span>
          <select id="vieneuVoice"><option value="">Đang tải giọng...</option></select>
        </div>
        <div class="advanced-check-grid">
          <label class="check">
            <input id="vieneuForce" type="checkbox" />
            Tạo lại audio cache
          </label>
        </div>
      </div>

      <div data-pane="maziao" hidden>
        <label class="field maziao-mode-field">
          <span class="field-label">{ui_icon("users", "field-icon")}<span>Chế độ TTS Maziao</span></span>
          <select id="maziaoTtsMode" aria-label="Chế độ TTS Maziao">
            <option value="auto" selected>Theo cấu hình project</option>
            <option value="paragraph">Một voice cho toàn bộ script</option>
            <option value="multiSpeakers">Multi-speakers</option>
          </select>
        </label>
        <section class="maziao-customization" id="maziaoCustomization" aria-labelledby="maziaoCustomizationTitle">
          <div class="maziao-customization-heading">
            <div>
              <strong id="maziaoCustomizationTitle">Voice customization</strong>
              <small id="maziaoCustomizationHint">Mỗi speaker có thể dùng một voice riêng.</small>
            </div>
            <span class="maziao-customization-mark" aria-hidden="true">Aa</span>
          </div>
          <div class="maziao-speaker-cards" id="maziaoSpeakerCards"></div>
          <p class="maziao-speaker-empty" id="maziaoSpeakerEmpty" hidden>Chưa có speaker trong project.</p>
        </section>
        <div class="advanced-check-grid maziao-cache-row">
          <label class="check">
            <input id="maziaoForce" type="checkbox" />
            Tạo lại audio cache
          </label>
        </div>
      </div>

      <div data-pane="edgetts" hidden>
      </div>

      <div data-pane="upload" hidden>
        <label class="field upload-audio-field">
          <span class="field-label">{ui_icon("upload", "field-icon")}<span>File audio</span></span>
          <span class="file-picker">
            <input id="uploadAudioFile" type="file" accept=".mp3,.wav,.mav,audio/mpeg,audio/wav,audio/x-wav" />
            <span class="file-picker-button">Chọn file</span>
            <span class="file-picker-name" id="uploadAudioFileName">Chưa chọn file audio</span>
          </span>
        </label>
        <p class="engine-note">Dùng file MP3, WAV hoặc MAV có sẵn của AurexVideo; không cần tải model TTS.</p>
      </div>

      <div class="form-actions render-primary-actions">
        <button class="start" id="startRender" type="button" {"disabled" if not projects else ""}>{ui_icon("play")}<span>Bắt đầu render</span></button>
        <button class="icon-btn stop-render-btn" id="stopRender" type="button" hidden>{ui_icon("square")}<span>Dừng render</span></button>
        <a class="small-link" id="videoLink" href="#" hidden>Mở video cuối</a>
        <button class="icon-btn reveal-output-btn" id="revealOutput" type="button" hidden>{ui_icon("search")}<span>{"Mở File Explorer" if sys.platform.startswith("win") else "Mở trong Finder"}</span></button>
      </div>

      <div class="render-state" id="renderState" hidden>
        <div class="state-head">
          <span class="state-dot"></span>
          <strong id="stateTitle">Trạng thái render</strong>
          <span class="state-percent" id="statePercent">0%</span>
        </div>
        <div class="state-progress"><span id="stateProgressBar"></span></div>
        <pre id="stateList" hidden></pre>
      </div>

      <section class="advanced-settings" id="advancedSettings">
        <div class="advanced-heading">
          {ui_icon("settings", "advanced-heading-icon")}
          <span class="advanced-heading-title">Cài đặt nâng cao</span>
        </div>
        <div class="advanced-body">

          <div data-advanced-engine="edgetts" hidden>
            <label class="field">
              <span class="field-label">
                {ui_icon("message", "field-icon advanced-field-icon")}
                <span>Giọng Edge TTS</span>
              </span>
              <select id="edgeVoice">
                <optgroup label="Tiếng Việt">
                  <option value="vi-VN-NamMinhNeural" selected>Nam Minh · Nam</option>
                  <option value="vi-VN-HoaiMyNeural">Hoài My · Nữ</option>
                </optgroup>
                <optgroup label="Tiếng Anh (Mỹ)">
                  <option value="en-US-AvaNeural">Ava · Nữ</option>
                  <option value="en-US-AndrewNeural">Andrew · Nam</option>
                  <option value="en-US-JennyNeural">Jenny · Nữ</option>
                  <option value="en-US-GuyNeural">Guy · Nam</option>
                </optgroup>
                <optgroup label="Tiếng Anh (Anh)">
                  <option value="en-GB-SoniaNeural">Sonia · Nữ</option>
                  <option value="en-GB-RyanNeural">Ryan · Nam</option>
                </optgroup>
                <option value="custom">Tùy chỉnh mã giọng…</option>
              </select>
            </label>
            <label class="field" id="edgeVoiceCustomField" hidden>
              <span class="field-label">
                {ui_icon("pencil", "field-icon advanced-field-icon")}
                <span>Mã giọng tùy chỉnh</span>
              </span>
              <input id="edgeVoiceCustom" type="text" placeholder="Ví dụ: en-US-AriaNeural" autocomplete="off" spellcheck="false" />
            </label>
            <div class="advanced-check-grid">
              <label class="check">
                <input id="edgeForce" type="checkbox" />
                Tạo lại audio cache
              </label>
            </div>
          </div>

          <div data-advanced-engine="vieneu">
            <label class="field">
              <span class="field-label">
                {ui_icon("message", "field-icon advanced-field-icon")}
                <span>Model Mode</span>
              </span>
              <select id="vieneuMode">
                <option value="v3turbo" selected>VieNeu-TTS-v3-Turbo (Khuyên dùng, 48kHz)</option>
              </select>
            </label>
            <label class="field">
              <span class="field-label">
                {ui_icon("gauge", "field-icon advanced-field-icon")}
                <span>Device</span>
              </span>
              <select id="vieneuDevice">
                <option value="cpu" selected>CPU (Ổn định)</option>
                <option value="mps">Apple Silicon GPU (MPS)</option>
                <option value="cuda">NVIDIA GPU (CUDA)</option>
              </select>
            </label>
            <div class="advanced-check-grid">
              <button class="start" id="checkVieneu" type="button">Kiểm tra VieNeu-TTS</button>
            </div>
            <p class="engine-note" id="vieneuConfigState">VieNeu-TTS chạy trực tiếp trong máy.</p>
          </div>

          <div class="render-speed-block">
            <label class="field render-speed-field">
              <span class="field-label">
                {ui_icon("gauge", "field-icon advanced-field-icon")}
                <span>Tốc độ audio</span>
              </span>
              <input id="renderSpeed" type="number" min="0.5" max="2" step="0.05" value="1.0" />
            </label>
            <div class="render-options-stack">
              <div class="render-option-row">
                <label class="check render-option-check">
                  <input id="renderBranding" type="checkbox" {branding_lock_attributes} />
                  Logo + brand
                </label>
                <button class="render-option-choose" id="openBrandConfig" type="button" {branding_lock_attributes}>Chọn</button>
              </div>
            </div>
            <div class="speed-presets" aria-label="Chọn nhanh tốc độ audio">
              <button class="speed-preset active" type="button" data-speed="1">1</button>
              <button class="speed-preset" type="button" data-speed="1.15">1.15</button>
              <button class="speed-preset" type="button" data-speed="1.2">1.2</button>
              <button class="speed-preset" type="button" data-speed="1.25">1.25</button>
            </div>
          </div>
          <div class="render-volume-block">
            <label class="field render-volume-field">
              <span class="field-label">
                {ui_icon("volume", "field-icon advanced-field-icon")}
                <span>Âm lượng audio</span>
              </span>
              <input id="renderVolume" type="number" min="1" max="3" step="0.1" value="1" />
            </label>
            <div class="speed-presets volume-presets" aria-label="Chọn nhanh âm lượng audio">
              <button class="speed-preset" type="button" data-volume="1.2">1.2</button>
              <button class="speed-preset" type="button" data-volume="1.5">1.5</button>
              <button class="speed-preset" type="button" data-volume="2">2.0</button>
              <button class="speed-preset" type="button" data-volume="3">3.0</button>
            </div>
          </div>
          <label class="field render-size-field">
            <span class="field-label">
              {ui_icon("smartphone", "field-icon advanced-field-icon")}
              <span>Độ phân giải render</span>
            </span>
            <select id="renderSize">
              <option value="720x1280">720 x 1280 (nhẹ hơn)</option>
              <option value="1080x1920" selected>1080 x 1920 (mặc định, nét hơn)</option>
            </select>
          </label>
          <label class="field render-quality-field">
            <span class="field-label">
              {ui_icon("sparkles", "field-icon advanced-field-icon")}
              <span>Chất lượng video</span>
            </span>
            <select id="renderQuality">
              <option value="standard" selected>Standard · mặc định, nét và cân bằng</option>
              <option value="master">Master · chất lượng cao nhất (chậm)</option>
              <option value="draft">Draft · xem trước nhanh</option>
            </select>
          </label>
          <label class="field render-backend-field">
            <span class="field-label">
              {ui_icon("cpu", "field-icon advanced-field-icon")}
              <span>Bộ máy render</span>
            </span>
            <select id="renderBackend">
              <option value="auto" selected>Auto · Aurex Render Core ưu tiên, Browser fallback</option>
              <option value="native">Aurex Render Core · bắt buộc Native</option>
              <option value="browser">Browser · giữ đúng CSS preview</option>
            </select>
          </label>
          <p class="engine-note render-backend-note">Auto ưu tiên Aurex Render Core; scene chưa đạt contract hoặc Core lỗi sẽ fallback Browser để giữ đúng preview. Chọn Native sẽ dừng nếu project chưa đạt parity.</p>
        </div>
      </section>

      <div class="brand-modal-backdrop" id="brandConfigModal" hidden>
        <div class="brand-modal-card" role="dialog" aria-modal="true" aria-labelledby="brandConfigTitle">
          <button class="brand-modal-close" id="closeBrandConfig" type="button" aria-label="Đóng">×</button>
          <p class="kicker">Logo + brand</p>
          <h3 id="brandConfigTitle">Tuỳ chỉnh logo và tên brand</h3>
          <p class="brand-modal-copy">Mặc định dùng logo AurexVideo và aurexvideo.app. Logo và tên riêng sau khi thay sẽ được giữ cho các lần render tiếp theo.</p>
          <label class="field brand-modal-field">
            <span class="field-label">{ui_icon("image", "field-icon")}<span>File logo</span></span>
            <span class="file-picker">
              <input id="brandLogoFile" type="file" accept=".png,.jpg,.jpeg,.webp,.gif,.ico" />
              <span class="file-picker-button">Chọn file</span>
              <span class="file-picker-name" id="brandLogoFileName">Logo AurexVideo mặc định</span>
            </span>
          </label>
          <label class="field brand-modal-field">
            <span class="field-label">{ui_icon("at-sign", "field-icon")}<span>Tên brand</span></span>
            <input id="brandNameInput" type="text" maxlength="64" value="aurexvideo.app" placeholder="aurexvideo.app" />
          </label>
          <div class="brand-modal-actions">
            <button class="brand-modal-button secondary" id="cancelBrandConfig" type="button">Huỷ</button>
            <button class="brand-modal-button" id="saveBrandConfig" type="button">Áp dụng</button>
          </div>
        </div>
      </div>

    </section>

    <aside class="slide-list panel">
      <div class="panel-head">
        <div>
          <p class="kicker">Thư viện</p>
          <h2>Dự án của bạn</h2>
        </div>
        <label class="project-sort-control" for="projectSort">
          <span>Lọc / sắp xếp</span>
          <select id="projectSort" aria-label="Lọc hoặc sắp xếp dự án">
            <option value="all">Tất cả dự án</option>
            <option value="recent">Mới cập nhật</option>
            <optgroup id="projectBrandCharacterOptions" label="Brand/Character"></optgroup>
          </select>
        </label>
      </div>
      <div class="list-head">
        <span>Dự án</span>
        <span>Trạng thái</span>
        <span>Đăng social</span>
        <span>Thao tác</span>
      </div>
      <ol class="project-list" id="projectList">
        {body}
      </ol>
    </aside>
  </main>

  <div class="brand-modal-backdrop" id="renameProjectModal" hidden>
    <form class="brand-modal-card rename-project-modal-card" id="renameProjectForm" role="dialog" aria-modal="true" aria-labelledby="renameProjectTitle" novalidate>
      <button class="brand-modal-close" id="closeRenameProject" type="button" aria-label="Đóng">×</button>
      <p class="kicker">Tên project</p>
      <h3 id="renameProjectTitle">Đổi tên project</h3>
      <p class="brand-modal-copy">Tên hiện tại: <strong id="renameProjectCurrent"></strong></p>
      <label class="field brand-modal-field rename-project-field" for="renameProjectInput">
        <span class="field-label">{ui_icon("pencil", "field-icon")}<span>Tên mới</span></span>
        <input id="renameProjectInput" type="text" maxlength="120" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="tieu-thuyet-phan-1" />
      </label>
      <div class="rename-project-guide">
        <strong>Viết tên đúng như sau:</strong>
        <span>Chỉ dùng chữ thường không dấu, số và dấu gạch ngang (-).</span>
        <span>Không dùng khoảng trắng; không đặt dấu gạch ngang ở đầu hoặc cuối.</span>
        <span>Ví dụ: <code>tieu-thuyet-phan-1</code></span>
      </div>
      <p class="rename-project-error" id="renameProjectError" role="alert" aria-live="polite" hidden></p>
      <div class="brand-modal-actions">
        <button class="brand-modal-button secondary" id="cancelRenameProject" type="button">Huỷ</button>
        <button class="brand-modal-button" id="renameProjectSubmit" type="submit">Đổi tên</button>
      </div>
    </form>
  </div>
""",
        extra_style="""
    body.theme-light {
      --accent: #e8a060;
      --accent-contrast: #5c3310;
      --accent-glow: rgba(232, 160, 96, 0.18);
      --bg: #e9e4da;
      --panel: transparent;
      --body-bg:
        radial-gradient(circle at 18% 12%, rgba(232, 160, 96, 0.12), transparent 28rem),
        radial-gradient(circle at 88% 18%, rgba(204, 136, 52, 0.10), transparent 24rem),
        linear-gradient(135deg, #f7f2e8, var(--bg));
      --shadow: none;
      --surface: transparent;
      --surface-strong: rgba(79, 57, 31, 0.05);
      --field-bg: rgba(255, 251, 244, 0.55);
    }
    body.theme-light .panel {
      border: 1px solid rgba(79, 57, 31, 0.16);
      border-radius: 22px;
      background: transparent;
      box-shadow: none;
      backdrop-filter: none;
    }
    body.theme-light .brand-mark {
      box-shadow: 0 14px 34px rgba(58, 44, 24, 0.14);
    }
    body.theme-light .header-stat {
      border-color: rgba(79, 57, 31, 0.14);
      background: transparent;
    }
    body.theme-light .advanced-settings {
      border-color: rgba(79, 57, 31, 0.14);
      background: transparent;
    }
    body.theme-light .selected-box {
      border-color: rgba(216, 132, 53, 0.70);
      background: rgba(242, 178, 101, 0.12);
      box-shadow: 0 0 0 1px rgba(216, 132, 53, 0.16);
    }
    body.theme-light .slide-list {
      color: #20170f;
    }
    .render-machine {
      color: var(--text-soft);
    }
    .render-machine .field span,
    .render-machine .field-label,
    .render-machine .kicker,
    .render-machine h2,
    .render-machine .file-picker-name,
    .render-machine .selected-box strong,
    .render-machine .advanced-settings .advanced-heading,
    .render-machine .advanced-heading-title,
    .render-machine .advanced-heading-icon,
    .render-machine .render-lead-icon,
    .render-machine .tab,
    .render-machine .tab.active,
    .render-machine .mode-toggle label,
    .render-machine .speed-preset,
    .render-machine .start,
    .render-machine .reveal-output-btn,
    .render-machine .guide-header-btn,
    .render-machine .file-picker-button,
    .render-machine .render-option-choose,
    .render-machine .btn-icon,
    .render-machine .tab-icon,
    .render-machine .field-icon,
    .render-machine .advanced-field-icon {
      color: var(--text-soft);
    }
    .render-machine .btn-icon,
    .render-machine .tab-icon,
    .render-machine .field-icon,
    .render-machine .render-lead-icon,
    .render-machine .advanced-heading-icon,
    .render-machine .advanced-field-icon {
      color: var(--text-soft) !important;
    }
    body.theme-light .render-machine {
      color: var(--text-soft);
    }
    body.theme-light .render-machine .field span,
    body.theme-light .render-machine .field-label,
    body.theme-light .render-machine .kicker,
    body.theme-light .render-machine h2,
    body.theme-light .render-machine .file-picker-name,
    body.theme-light .render-machine .selected-box strong,
    body.theme-light .render-machine .advanced-settings .advanced-heading,
    body.theme-light .render-machine .advanced-heading-title,
    body.theme-light .render-machine .advanced-heading-icon,
    body.theme-light .render-machine .render-lead-icon {
      color: var(--text-soft);
    }
    body.theme-light .slide-list .kicker,
    body.theme-light .slide-list h2,
    body.theme-light .slide-list .list-head,
    body.theme-light .project-name {
      color: #20170f;
    }
    body.theme-light .refresh-btn,
    body.theme-light .start,
    body.theme-light .brand-modal-button,
    body.theme-light .file-picker-button,
    body.theme-light .render-option-choose,
    body.theme-light .reveal-output-btn,
    body.theme-light .refresh-btn.guide-header-btn,
    body.theme-light .tab:not(.active),
    body.theme-light .mode-toggle label:not(:has(input:checked)),
    body.theme-light .speed-preset:not(.active) {
      color: var(--text-soft);
      border: 1px solid rgba(79, 57, 31, 0.16);
      background: transparent;
      box-shadow: none;
    }
    body.theme-light .refresh-btn:hover,
    body.theme-light .start:hover,
    body.theme-light .file-picker-button:hover,
    body.theme-light .render-option-choose:hover,
    body.theme-light .reveal-output-btn:hover,
    body.theme-light .refresh-btn.guide-header-btn:hover {
      background: rgba(79, 57, 31, 0.04);
    }
    body.theme-light .tab.active,
    body.theme-light .mode-toggle label:has(input:checked),
    body.theme-light .speed-preset.active {
      color: var(--text-soft);
      border: 1px solid rgba(216, 132, 53, 0.55);
      background: rgba(242, 178, 101, 0.14);
      box-shadow: none;
    }
    body.theme-light .refresh-btn.guide-header-btn .btn-icon,
    body.theme-light .start .btn-icon,
    body.theme-light .reveal-output-btn .btn-icon,
    body.theme-light .render-machine .btn-icon,
    body.theme-light .render-machine .tab-icon,
    body.theme-light .render-machine .field-icon {
      color: var(--text-soft) !important;
      background: transparent !important;
      box-shadow: none !important;
    }
    body.theme-light .render-machine .render-lead-icon {
      color: var(--text-soft) !important;
    }
    body.theme-light .dashboard-header .header-tools .header-nav-action {
      color: var(--text-soft);
      border: 1px solid rgba(79, 57, 31, 0.12);
      background: transparent;
      box-shadow: none;
    }
    body.theme-light .dashboard-header .header-tools .header-nav-action:hover {
      color: #20170f;
      border-color: rgba(79, 57, 31, 0.22);
      background: rgba(79, 57, 31, 0.04);
    }
    body.theme-light .dashboard-header .header-tools .header-nav-action .btn-icon {
      color: #20170f;
      background: transparent;
      box-shadow: none;
    }
    body.theme-light .dashboard-header .header-tools .theme-toggle.header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools #refreshProjects.header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools a[href="/upload"].header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools a[href="/new-project"].header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools .settings-button.header-nav-action .btn-icon {
      color: #20170f;
      background: transparent;
    }
    body.theme-light .project-row {
      border-color: rgba(79, 57, 31, 0.14);
      background: transparent;
      box-shadow: none;
    }
    body.theme-light .project-row.selected {
      border-color: rgba(216, 132, 53, 0.70);
      background: rgba(242, 178, 101, 0.12);
      box-shadow: 0 0 0 1px rgba(216, 132, 53, 0.16);
      transform: none;
    }
    body.theme-light .project-row .actions .icon-btn,
    body.theme-light .project-row .actions .select-btn,
    body.theme-light .project-row .actions .small-link,
    body.theme-light .project-row .actions .delete-output-btn,
    body.theme-light .project-row .actions .copy-script-btn,
    body.theme-light .project-row .actions .select-btn.active {
      color: #20170f;
      border: 1px solid rgba(79, 57, 31, 0.14);
      background: transparent;
      box-shadow: none;
    }
    body.theme-light .project-row .actions .delete-output-btn {
      color: #9f2e2e;
      border-color: rgba(159, 46, 46, 0.22);
    }
    body.theme-light .project-row .actions .delete-output-btn .btn-icon {
      color: #9f2e2e;
      background: transparent !important;
    }
    body.theme-light .reveal-output-btn {
      color: var(--text-soft);
      border: 1px solid rgba(79, 57, 31, 0.16);
      background: transparent;
      box-shadow: none;
    }
    body.theme-light .reveal-output-btn .btn-icon {
      color: var(--text-soft);
      background: transparent !important;
      box-shadow: none !important;
    }
    body.theme-light .tab:not(.active) .tab-icon,
    body.theme-light .tab[data-engine="elevenlabs"] .tab-icon,
    body.theme-light .tab[data-engine="edgetts"] .tab-icon,
    body.theme-light .tab.active .tab-icon {
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
    }
    body.theme-light .project-rename-button,
    body.theme-light .project-duplicate-button {
      color: #20170f;
      border-color: rgba(79, 57, 31, 0.16);
      background: transparent;
      box-shadow: none;
    }
    body.theme-light .speed-preset:not(.active),
    body.theme-light .mode-toggle label:not(:has(input:checked)),
    body.theme-light .tab:not(.active) {
      color: var(--text-soft) !important;
      border-color: rgba(79, 57, 31, 0.16);
      background: transparent;
      box-shadow: none;
    }
    body.theme-light .btn-icon,
    body.theme-light .tab-icon,
    body.theme-light .field-icon,
    body.theme-light .render-lead-icon,
    body.theme-light .start .btn-icon,
    body.theme-light .reveal-output-btn .btn-icon,
    body.theme-light .refresh-btn .btn-icon,
    body.theme-light .guide-header-btn .btn-icon {
      width: 18px;
      height: 18px;
      min-width: 18px;
      border-radius: 0;
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
      text-shadow: none;
      font-size: inherit;
    }
    body.theme-light .project-row .actions .icon-btn .btn-icon,
    body.theme-light .project-row .actions .select-btn .btn-icon,
    body.theme-light .project-row .actions .small-link .btn-icon,
    body.theme-light .project-row .actions .delete-output-btn .btn-icon,
    body.theme-light .project-row .actions .copy-script-btn .btn-icon {
      width: 16px;
      height: 16px;
      min-width: 16px;
      flex: 0 0 16px;
      border-radius: 0;
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
      text-shadow: none;
      font-size: inherit;
    }
    body.theme-light .project-row .actions .delete-output-btn .btn-icon {
      color: #9f2e2e;
    }
    body.theme-light .reveal-output-btn .btn-icon {
      color: var(--text-soft);
    }
    body.theme-light .render-lead-icon {
      color: var(--text-soft);
    }
    body.theme-light .field-icon {
      color: var(--text-soft);
      font-size: 13px;
    }
    body {
      height: 100vh;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      padding: 28px min(4vw, 48px);
    }
    html.tauri-macos body { padding-top: 54px; }
    .dashboard-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      width: 100%;
      max-width: 1760px;
      flex: 0 0 auto;
      margin: 0 auto 8px;
      padding: 0;
      border: 0;
      background: transparent;
      box-shadow: none;
    }
    .brand-lockup {
      display: flex;
      align-items: center;
      gap: 14px;
      flex: 0 0 auto;
      min-width: 0;
    }
    .brand-mark {
      width: 58px;
      height: 58px;
      flex: 0 0 58px;
      border-radius: 16px;
      object-fit: cover;
      box-shadow: 0 14px 34px rgba(0, 0, 0, 0.24);
    }
    .dashboard-header h1 {
      margin: 0;
      font-size: clamp(28px, 3.4vw, 40px);
      line-height: 1;
      letter-spacing: -0.05em;
      white-space: nowrap;
    }
    .brand-lockup p {
      margin: 10px 0 0;
      color: var(--muted);
      font-size: clamp(18px, 2.4vw, 26px);
      line-height: 1;
      letter-spacing: -0.03em;
    }
    .kicker { margin: 0 0 6px; color: var(--muted); font-size: 11px; font-weight: 900; letter-spacing: 0.16em; text-transform: uppercase; }
    .header-tools {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      flex: 1 1 auto;
      min-width: 0;
      justify-content: flex-end;
    }
    .header-account { position: relative; display: flex; align-items: center; gap: 10px; margin-left: auto; flex: 0 0 auto; }
    .guide-header-btn {
      white-space: nowrap;
      padding-inline: 14px;
    }
    .render-guide-links {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin: 2px 0 12px;
    }
    .render-guide-links .guide-header-btn { width: 100%; min-height: 44px; }
    .settings-button { white-space: nowrap; }
    .update-slot-placeholder { width: 158px; height: 48px; flex: 0 0 158px; }
    .update-available-button {
      white-space: nowrap;
      border-color: #7dcf25;
      color: #18200f;
      background: linear-gradient(135deg, #b5ff63, #8fe83f);
      box-shadow: 0 8px 20px rgba(117, 204, 42, 0.24);
    }
    .update-available-button .btn-icon { color: #1f3b0f; }
    @media (min-width: 1280px) {
      html[lang="en"] .dashboard-header { gap: 14px; }
      html[lang="en"] .header-tools { gap: 7px; flex-wrap: nowrap; }
      html[lang="en"] .header-tools .icon-btn {
        gap: 5px;
        padding-inline: 9px;
        font-size: 11px;
      }
      html[lang="en"] .guide-header-btn { padding-inline: 10px; }
    }
    .header-stat {
      min-width: 116px;
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 14px 16px;
      background: transparent;
      text-align: center;
    }
    .header-stat strong { display: block; color: var(--accent); font-size: 32px; line-height: 1; }
    .account-upgrade-button > span { grid-row: 1 / 3; color: #315a16; font-size: 24px; text-align: center; }
    .account-upgrade-button strong { font-size: 15px; line-height: 1.15; }
    .account-upgrade-button small { margin-top: 3px; color: #315021; font-size: 10px; font-weight: 750; line-height: 1.3; }
    .account-upgrade-button:hover { filter: brightness(1.04); transform: translateY(-1px); }
    .account-upgrade-button:disabled { opacity: .72; cursor: wait; transform: none; }
    .plan-picker-dialog { width: min(720px, calc(100vw - 32px)); border: 1px solid var(--control-line); border-radius: 28px; padding: 0; color: var(--text); background: var(--panel); box-shadow: 0 34px 110px rgba(0,0,0,.5); }
    .plan-picker-dialog::backdrop { background: rgba(15,12,9,.72); backdrop-filter: blur(6px); }
    .plan-picker-content { position: relative; display: grid; gap: 22px; padding: 34px; }
    .plan-picker-close { width: 40px; height: 40px; position: absolute; top: 18px; right: 18px; border: 1px solid var(--control-line); border-radius: 50%; color: var(--text); background: var(--surface-strong); font-size: 25px; cursor: pointer; }
    .plan-picker-heading { padding-right: 48px; }
    .plan-picker-heading h2 { margin: 0; font-size: 32px; letter-spacing: -.04em; }
    .plan-picker-heading p { margin: 8px 0 0; color: var(--muted); font-size: 14px; line-height: 1.55; }
    .plan-picker-options { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 14px; }
    .plan-option { position: relative; display: grid; gap: 14px; min-height: 250px; border: 1px solid var(--control-line); border-radius: 20px; padding: 24px; background: var(--surface); }
    .plan-option.yearly { border-color: rgba(155,255,63,.55); background: linear-gradient(145deg, rgba(155,255,63,.12), var(--surface)); box-shadow: inset 0 0 0 1px rgba(155,255,63,.12); }
    .plan-option-badge { width: fit-content; border-radius: 999px; padding: 6px 10px; color: #16200e; background: #9bff3f; font-size: 10px; font-weight: 950; text-transform: uppercase; }
    .plan-option h3 { margin: 0; font-size: 21px; }
    .plan-option-price { display: flex; align-items: baseline; gap: 7px; }
    .plan-option-price strong { font-size: 38px; letter-spacing: -.05em; }
    .plan-option-price span, .plan-option-copy { color: var(--muted); font-size: 12px; font-weight: 750; line-height: 1.5; }
    .plan-option-copy { margin: 0; }
    .plan-option button { width: 100%; min-height: 48px; margin-top: auto; border: 0; border-radius: 14px; color: #1c1209; background: #f2b261; font: 900 14px var(--font-ui); cursor: pointer; }
    .plan-option.yearly button { background: #9bff3f; }
    .plan-option button:disabled { opacity: .65; cursor: wait; }
    .plan-picker-status { min-height: 20px; margin: 0; color: var(--warn-text); font-size: 12px; font-weight: 800; text-align: center; }
    body.theme-light .plan-picker-dialog {
      border-color: rgba(232, 160, 96, 0.52);
      color: #20170f;
      background: var(--dashboard-surface-warm, rgba(250, 244, 236, 0.88));
      box-shadow: 0 12px 30px rgba(91, 61, 30, 0.08);
    }
    body.theme-light .plan-picker-close {
      border-color: rgba(79, 57, 31, 0.14);
      color: #20170f;
      background: rgba(250, 244, 236, 0.96);
    }
    body.theme-light .plan-option {
      border-color: rgba(79, 57, 31, 0.14);
      background: rgba(250, 244, 236, 0.96);
    }
    body.theme-light .plan-option.yearly {
      border-color: rgba(126, 180, 48, 0.45);
      background: linear-gradient(145deg, rgba(126, 245, 82, 0.16), rgba(250, 244, 236, 0.96));
      box-shadow: inset 0 0 0 1px rgba(126, 245, 82, 0.12);
    }
    body:not(.theme-light) .plan-picker-dialog {
      border-color: rgba(232, 160, 96, 0.46);
      background: var(--dashboard-surface-dark, rgba(31, 29, 26, 0.96));
      box-shadow: 0 16px 36px rgba(0, 0, 0, 0.24);
    }
    body:not(.theme-light) .plan-picker-close {
      border-color: rgba(255, 238, 218, 0.18);
      background: var(--dashboard-button-dark, rgba(48, 44, 39, 0.92));
    }
    body:not(.theme-light) .plan-option {
      border-color: rgba(255, 238, 218, 0.16);
      background: var(--dashboard-row-dark, rgba(39, 36, 32, 0.94));
    }
    body:not(.theme-light) .plan-option.yearly {
      border-color: rgba(80, 226, 92, 0.55);
      background:
        linear-gradient(145deg, rgba(80, 226, 92, 0.14), rgba(39, 36, 32, 0.42)),
        var(--dashboard-row-dark, rgba(39, 36, 32, 0.94));
      box-shadow: inset 0 0 0 1px rgba(80, 226, 92, 0.12);
    }
    @media (max-width: 620px) { .plan-picker-content { padding: 28px 20px 22px; } .plan-picker-options { grid-template-columns: 1fr; } .plan-option { min-height: 210px; } }
    .dashboard-shell {
      display: grid;
      grid-template-columns: minmax(360px, 480px) minmax(0, 1fr);
      gap: 18px;
      width: 100%;
      max-width: 1480px;
      flex: 1 1 auto;
      min-height: 0;
      overflow: hidden;
      margin: 0 auto;
      align-items: stretch;
    }
    .panel {
      border: 1px solid var(--line);
      border-radius: 24px;
      background: transparent;
      box-shadow: none;
      backdrop-filter: none;
    }
    .render-machine {
      width: 100%;
      justify-self: stretch;
      align-self: start;
      height: auto;
      max-height: 100%;
      min-height: 0;
      padding: 16px 16px 20px;
      position: static;
      overflow-y: auto;
      overscroll-behavior: contain;
      scroll-padding-bottom: 40px;
      -webkit-overflow-scrolling: touch;
    }
    .slide-list {
      height: 100%;
      min-height: 0;
      padding: 16px;
      overflow-y: auto;
      overscroll-behavior: contain;
      -webkit-overflow-scrolling: touch;
      container-type: inline-size;
    }
    .panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 12px; }
    h2 { margin: 0; font-size: 22px; letter-spacing: -0.04em; }
    .render-heading { text-align: center; margin: 0 0 14px; }
    .render-heading h2 {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 9px;
    }
    .render-heading .kicker { margin-bottom: 3px; }
    .render-lead-icon,
    .tab-icon,
    .field-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      min-width: 18px;
      flex: 0 0 18px;
      border-radius: 0;
      color: currentColor;
      background: transparent;
      box-shadow: none;
      font-size: inherit;
      line-height: 1;
      text-shadow: none;
    }
    .render-lead-icon {
      width: 20px;
      height: 20px;
      min-width: 20px;
      flex: 0 0 20px;
      color: currentColor;
      background: transparent;
      box-shadow: none;
    }
    .render-machine .field,
    .render-machine .tabs,
    .render-machine .check,
    .render-machine .mode-toggle,
    .render-machine .eleven-actions,
    .render-machine .speed-presets,
    .render-machine .form-actions,
    .render-machine .render-guide-links,
    .render-machine .selected-box,
    .render-machine .advanced-settings {
      max-width: 440px;
      width: 100%;
      margin-left: auto;
      margin-right: auto;
    }
    .render-machine .speed-presets {
      justify-content: center;
    }
    .render-machine .form-actions {
      justify-content: center;
    }
    body.theme-light .render-machine .btn-icon,
    body.theme-light .render-machine .tab-icon,
    body.theme-light .render-machine .field-icon,
    body.theme-light .render-machine .render-lead-icon {
      width: 18px;
      height: 18px;
      min-width: 18px;
      border-radius: 0;
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
      font-size: inherit;
      font-weight: 400;
      line-height: 1;
    }
    body.theme-light .render-machine .render-lead-icon {
      width: 20px;
      height: 20px;
      min-width: 20px;
      color: var(--text-soft);
    }
    body.theme-light .render-machine .field-icon {
      color: var(--text-soft);
      font-size: 17px;
    }
    body.theme-light .render-machine .advanced-field-icon,
    body.theme-light .render-machine .advanced-body .field-icon.advanced-field-icon {
      width: 18px;
      height: 18px;
      min-width: 18px;
      flex: 0 0 18px;
      font-size: inherit;
      font-weight: 400;
    }
    body.theme-light .render-machine .start .btn-icon,
    body.theme-light .render-machine .reveal-output-btn .btn-icon,
    body.theme-light .render-machine .guide-header-btn .btn-icon {
      width: 18px;
      height: 18px;
      min-width: 18px;
      font-size: inherit;
      color: var(--text-soft) !important;
    }
    .field { display: grid; gap: 7px; margin: 10px 0; }
    .field span { color: var(--muted); font-size: 12px; font-weight: 900; }
    .field-label { display: inline-flex; align-items: center; gap: 7px; }
    .maziao-voice-control {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 56px;
      align-items: center;
      gap: 12px;
    }
    .maziao-voice-field { margin-bottom: 4px; }
    .maziao-voice-control #maziaoVoice {
      min-height: 56px;
      padding: 13px 44px 13px 16px;
      border-radius: 14px;
      font-size: 16px;
      font-weight: 700;
      line-height: 1.25;
    }
    .maziao-voice-control #maziaoVoice option {
      min-height: 40px;
      padding: 10px 14px;
      font-size: 16px;
    }
    .maziao-mode-field { margin-bottom: 8px; }
    .maziao-customization {
      margin: 12px 0 8px;
      padding: 16px 14px 8px;
      border: 1px solid var(--control-line);
      border-radius: 18px;
      background: color-mix(in srgb, var(--control-bg) 76%, transparent);
    }
    .maziao-customization-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0 2px 12px; }
    .maziao-customization-heading strong { display: block; color: var(--text); font-size: 16px; letter-spacing: -.02em; }
    .maziao-customization-heading small { display: block; margin-top: 3px; color: var(--muted); font-size: 11px; font-weight: 600; }
    .maziao-customization-mark { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 11px; color: #6d3ee8; background: #f0e8ff; font-size: 13px; font-weight: 900; }
    .maziao-speaker-cards { display: grid; gap: 12px; }
    .maziao-speaker-card { --speaker-accent: #caa0ff; --speaker-soft: #f4ebff; padding: 13px 12px 12px; border: 2px dashed var(--speaker-accent); border-radius: 17px; background: color-mix(in srgb, var(--speaker-soft) 18%, transparent); }
    .maziao-speaker-card[data-accent="blue"] { --speaker-accent: #a8c6ff; --speaker-soft: #e9f0ff; }
    .maziao-speaker-card[data-accent="mint"] { --speaker-accent: #9edfcf; --speaker-soft: #e7faf4; }
    .maziao-speaker-card[data-accent="orange"] { --speaker-accent: #ffd09a; --speaker-soft: #fff4e3; }
    .maziao-speaker-head { display: flex; align-items: center; gap: 9px; margin-bottom: 10px; }
    .maziao-speaker-badge { display: inline-flex; align-items: center; min-height: 33px; padding: 5px 13px; border: 1px solid color-mix(in srgb, var(--speaker-accent) 70%, #fff); border-radius: 999px; color: #7040df; background: var(--speaker-soft); font-size: 13px; font-weight: 900; }
    .maziao-speaker-count { flex: 1; color: var(--text-soft); font-size: 12px; font-weight: 700; }
    .maziao-speaker-preview { display: grid; place-items: center; width: 34px; height: 34px; padding: 0; border: 0; border-radius: 10px; color: var(--text); background: transparent; cursor: pointer; }
    .maziao-speaker-preview:hover { background: var(--speaker-soft); }
    .maziao-speaker-preview:disabled { cursor: not-allowed; opacity: .38; }
    .maziao-speaker-preview svg { width: 21px; height: 21px; }
    .maziao-voice-picker { position: relative; display: flex; align-items: center; gap: 12px; min-height: 78px; padding: 11px 14px; overflow: hidden; border: 1px solid var(--control-line); border-radius: 17px; background: var(--control-bg); box-shadow: 0 4px 12px rgba(30, 40, 70, .06); }
    .maziao-voice-picker:focus-within { outline: 3px solid color-mix(in srgb, var(--speaker-accent) 35%, transparent); }
    .maziao-voice-avatar { display: grid; place-items: center; flex: 0 0 48px; width: 48px; height: 48px; border-radius: 50%; color: #fff; background: #ec3f91; font-size: 18px; font-weight: 900; }
    .maziao-voice-picker[data-avatar-tone="blue"] .maziao-voice-avatar { background: #4179ea; }
    .maziao-voice-picker[data-avatar-tone="green"] .maziao-voice-avatar { background: #2ea98e; }
    .maziao-voice-picker[data-avatar-tone="orange"] .maziao-voice-avatar { background: #ec8c32; }
    .maziao-voice-info { min-width: 0; flex: 1; }
    .maziao-voice-name { display: block; overflow: hidden; color: var(--text); font-size: 15px; font-weight: 900; text-overflow: ellipsis; white-space: nowrap; }
    .maziao-voice-tags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
    .maziao-voice-tag { display: inline-flex; padding: 3px 8px; border-radius: 999px; color: var(--text-soft); background: color-mix(in srgb, var(--control-line) 65%, transparent); font-size: 10px; font-weight: 800; }
    .maziao-voice-tag.gender { color: #c41f79; background: #ffeaf5; }
    .maziao-speaker-voice { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; opacity: 0; cursor: pointer; }
    .maziao-voice-chevron { color: var(--muted); font-size: 18px; pointer-events: none; }
    .maziao-speaker-controls { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 11px; }
    .maziao-speaker-control { display: grid; gap: 5px; }
    .maziao-speaker-control span { color: var(--text-soft); font-size: 11px; font-weight: 800; }
    .maziao-speaker-control input { width: 100%; min-height: 42px; padding: 8px 11px; border: 1px solid var(--control-line); border-radius: 12px; color: var(--text); background: var(--control-bg); font-size: 14px; font-weight: 700; }
    .maziao-speaker-empty { margin: 8px 2px; color: var(--muted); font-size: 12px; }
    @media (max-width: 560px) { .maziao-speaker-controls { gap: 8px; } .maziao-voice-picker { padding-inline: 10px; } }
    .maziao-multi-config-field { margin-top: 8px; }
    .maziao-multi-config-field textarea {
      width: 100%;
      min-height: 104px;
      resize: vertical;
      padding: 10px 12px;
      border: 1px solid var(--control-line);
      border-radius: 12px;
      color: var(--text);
      background: var(--control-bg);
      font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .maziao-multi-config-help { color: var(--muted); line-height: 1.45; }
    .maziao-multi-config-help code {
      color: var(--text-soft);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .maziao-preview-button {
      display: grid;
      place-items: center;
      width: 56px;
      min-width: 56px;
      height: 56px;
      padding: 0;
      border: 0;
      border-radius: 50%;
      color: #4c4fe3;
      background: #e9edff;
      cursor: pointer;
      transition: background-color 150ms ease, transform 150ms ease;
    }
    .maziao-preview-button:hover { background: #e5e8ff; }
    .maziao-preview-button:active { transform: scale(0.96); }
    .maziao-preview-button:focus-visible { outline: 3px solid rgba(85, 87, 232, 0.32); outline-offset: 2px; }
    .maziao-preview-button:disabled { cursor: not-allowed; opacity: 0.72; }
    .maziao-preview-play {
      width: 0;
      height: 0;
      margin-left: 4px;
      border-top: 11px solid transparent;
      border-bottom: 11px solid transparent;
      border-left: 18px solid currentColor;
    }
    .maziao-preview-pause {
      display: none;
      width: 15px;
      height: 19px;
      border-left: 4px solid currentColor;
      border-right: 4px solid currentColor;
    }
    .maziao-preview-button.is-playing .maziao-preview-play { display: none; }
    .maziao-preview-button.is-playing .maziao-preview-pause { display: block; }
    .maziao-cache-row { margin-top: 0; }
    .maziao-cache-row .check { margin: 4px 0 6px; }
    .vieneu-runtime-control {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin: 2px 0 10px;
      padding: 9px 11px;
      border: 1px solid var(--control-line);
      border-radius: 12px;
      background: rgba(242, 178, 101, 0.08);
    }
    .vieneu-runtime-info { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; min-width: 0; }
    .vieneu-runtime-control .check { margin: 0; }
    .vieneu-web-ui-link {
      display: inline;
      align-self: flex-start;
      min-height: 0;
      padding: 0;
      border: 0;
      border-radius: 0;
      color: var(--accent);
      background: transparent;
      font-size: 11px;
      font-weight: 850;
      line-height: 1.2;
      text-decoration: underline;
      text-underline-offset: 2px;
      transition: color 150ms ease;
    }
    .vieneu-web-ui-link:hover {
      color: var(--text);
      background: transparent;
      box-shadow: none;
      transform: none;
    }
    .vieneu-runtime-state {
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      text-align: right;
    }
    .mode-toggle {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 8px;
      margin: 10px auto 12px;
    }
    .mode-toggle label {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 7px;
      min-height: 38px;
      border: 1px solid var(--control-line);
      border-radius: 13px;
      color: var(--text-soft);
      background: transparent;
      font-size: 12px;
      font-weight: 900;
    }
    .mode-toggle label:has(input:checked) {
      color: var(--text-soft);
      background: rgba(242, 178, 101, 0.16);
      border-color: rgba(232, 160, 96, 0.55);
    }
    .eleven-actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin: 8px auto 10px;
    }
    .config-state {
      min-width: 0;
      color: var(--muted);
      font-size: 12px;
      font-weight: 900;
      line-height: 1.3;
    }
    .api-key-field { margin-top: 8px; }
    .api-key-panel[hidden] { display: none !important; }
    .api-key-actions {
      align-items: center;
      margin-top: 6px;
      margin-bottom: 14px;
    }
    .save-voice-btn {
      min-height: 36px;
      padding: 8px 12px;
    }
    .engine-note {
      max-width: 440px;
      margin: 10px auto 12px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      line-height: 1.45;
    }
    .engine-note code {
      color: var(--text);
      font-weight: 900;
    }
    .engine-note.compact {
      margin-top: 8px;
      margin-bottom: 12px;
      padding: 10px 12px;
      border: 1px solid var(--control-line-soft);
      border-radius: 14px;
      background: var(--surface);
    }
    .primary-field { margin-bottom: 12px; }
    .render-primary-actions { margin-top: 12px; }
    .advanced-settings {
      max-width: 440px;
      margin: 14px auto 0;
      border: 1px solid var(--control-line-soft);
      border-radius: 18px;
      background: var(--surface-panel);
      overflow: hidden;
    }
    .advanced-heading {
      display: flex;
      align-items: center;
      gap: 10px;
      min-height: 48px;
      padding: 13px 16px;
      color: var(--text-soft);
      font-weight: 950;
      font-size: 15px;
      letter-spacing: -0.02em;
      line-height: 1;
    }
    .advanced-heading-icon {
      width: 20px;
      height: 20px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 20px;
      color: currentColor;
    }
    .advanced-heading-icon svg {
      width: 20px;
      height: 20px;
      display: block;
    }
    .advanced-heading-title {
      display: inline-flex;
      align-items: center;
      line-height: 1.15;
      transform: translateY(0.5px);
    }
    .advanced-body {
      display: grid;
      gap: 12px;
      padding: 14px 16px 16px;
      border-top: 1px solid var(--control-line-soft);
    }
    .advanced-body .field:first-child { margin-top: 0; }
    .field input[type="file"] {
      min-height: 54px;
      padding: 8px;
      color: var(--text-soft);
      background: rgba(255, 255, 255, 0.035);
    }
    body.theme-light .field input[type="file"] {
      background: rgba(255, 251, 244, 0.62);
    }
    .field input[type="file"]::file-selector-button {
      min-height: 36px;
      margin-right: 12px;
      border: 1px solid var(--control-line);
      border-radius: 11px;
      padding: 8px 13px;
      color: var(--text);
      background: transparent;
      box-shadow: none;
      font-family: inherit;
      font-weight: 950;
      cursor: pointer;
    }
    .advanced-settings {
      background: transparent;
      box-shadow: none;
    }
    body.theme-light .advanced-settings {
      background: transparent;
    }
    body.theme-light .advanced-heading-icon,
    body.theme-light .advanced-field-icon {
      color: var(--text-soft) !important;
      background: transparent !important;
      box-shadow: none !important;
    }
    .advanced-body .advanced-field-icon {
      width: 18px;
      height: 18px;
      min-width: 18px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 18px;
      border-radius: 0;
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
      font-size: inherit;
      line-height: 1;
    }
    .advanced-body .advanced-field-icon svg {
      width: 18px;
      height: 18px;
      display: block;
    }
    .advanced-body .field-label {
      display: inline-flex;
      align-items: center;
      gap: 9px;
      min-height: 40px;
      line-height: 1.2;
    }
    .advanced-body .field,
    .advanced-body .check,
    .advanced-body .eleven-actions,
    .advanced-body .speed-presets {
      max-width: none;
      margin-left: 0;
      margin-right: 0;
    }
    .advanced-body .field {
      display: grid;
      grid-template-columns: minmax(132px, 0.72fr) minmax(0, 1fr);
      align-items: center;
      gap: 10px 14px;
      margin-top: 0;
      margin-bottom: 0;
    }
    .advanced-body .field:first-child { margin-top: 0; }
    .advanced-body .field-label {
      align-self: center;
      gap: 8px;
      min-width: 0;
    }
    .advanced-body .field-label span:last-child {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .advanced-body .field input {
      min-height: 38px;
      border-radius: 14px;
    }
    .advanced-body .field select {
      min-width: 0;
      max-width: 100%;
      min-height: 42px;
      box-sizing: border-box;
      border-radius: 9px;
      padding: 8px 36px 8px 13px;
    }
    .advanced-body .render-size-field {
      grid-template-columns: minmax(155px, 0.78fr) minmax(0, 1.22fr);
      min-width: 0;
    }
    .advanced-body .render-size-field select {
      width: 100%;
      justify-self: start;
    }
    .advanced-body .speed-presets {
      justify-content: center;
      width: 100%;
      margin-top: 5px;
      margin-left: 0;
      margin-bottom: 0;
      gap: 6px;
    }
    .advanced-body .speed-preset {
      width: 100%;
      min-width: 0;
      min-height: 30px;
      padding: 6px 4px;
      border-radius: 12px;
    }
    .advanced-body .check {
      min-height: 34px;
      margin-top: 3px;
      margin-bottom: 3px;
      padding: 7px 10px;
      border: 1px solid var(--control-line-soft);
      border-radius: 11px;
      background: transparent;
      font-size: 12px;
    }
    .advanced-check-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }
    .advanced-body [data-advanced-engine="edgetts"] > .check { margin-top: 5px; }
    .advanced-body .render-speed-block,
    .advanced-body .render-volume-block {
      display: grid;
      grid-template-columns: minmax(155px, 0.78fr) minmax(0, 1.22fr);
      gap: 8px 10px;
      align-items: center;
      min-width: 0;
    }
    .advanced-body .render-speed-field,
    .advanced-body .render-volume-field {
      grid-column: 1 / -1;
    }
    .advanced-body .render-volume-block > .volume-presets {
      grid-column: 1 / -1;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      width: 100%;
      margin-top: 0;
    }
    .advanced-body .render-options-stack {
      grid-column: 1;
      grid-row: 2;
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 3px;
      padding-top: 0;
    }
    .advanced-body .render-speed-block > .speed-presets {
      grid-column: 2;
      grid-row: 2;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      align-self: center;
      width: 100%;
      margin-top: 0;
    }
    .advanced-body .render-option-row {
      display: flex;
      align-items: center;
      justify-content: flex-start;
      gap: 8px;
      margin-top: 0;
      margin-bottom: 0;
    }
    .advanced-body .render-option-check {
      width: auto;
      min-width: 0;
      min-height: 36px;
      display: inline-flex;
      align-items: center;
      justify-content: flex-start;
      gap: 8px;
      margin: 0;
      padding: 0 12px;
      border-radius: 12px;
      font-size: 12px;
      line-height: 1;
      font-weight: 850;
    }
    .advanced-body .render-option-choose {
      min-height: 36px;
      padding: 0 12px;
      border-radius: 12px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      line-height: 1;
    }
    .advanced-body .speed-preset {
      min-height: 36px;
      padding: 0 8px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 12px;
      font-weight: 850;
      line-height: 1;
    }
    .advanced-body .render-option-check input {
      width: 13px;
      height: 13px;
    }
    .render-brand-fixed {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 42px;
      padding: 6px 10px;
      border: 1px solid var(--control-line);
      border-radius: 12px;
      background: var(--control-bg);
    }
    .render-brand-fixed img { width: 28px; height: 28px; border-radius: 8px; }
    .render-brand-fixed span { display: grid; gap: 1px; text-align: left; }
    .render-brand-fixed strong { font-size: 11px; line-height: 1; }
    .render-brand-fixed small { color: var(--muted); font-size: 9px; line-height: 1; }
    .render-option-choose,
    .brand-modal-button {
      min-height: 34px;
      border: 1px solid var(--control-line);
      border-radius: 12px;
      padding: 7px 13px;
      color: var(--text-soft);
      background: transparent;
      box-shadow: none;
      font: inherit;
      font-size: 12px;
      font-weight: 950;
      cursor: pointer;
      white-space: nowrap;
    }
    .render-option-choose:hover,
    .brand-modal-button:hover { transform: translateY(-1px); }
    .render-option-choose:disabled {
      opacity: .48;
      cursor: not-allowed;
      filter: saturate(.45);
      transform: none;
      box-shadow: none;
    }
    .render-option-check:has(input:disabled) { cursor: not-allowed; }
    .render-option-choose {
      min-height: 27px;
      padding: 4px 9px;
      border-radius: 999px;
      font-size: 10px;
    }
    .brand-modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 220;
      display: grid;
      place-items: center;
      padding: 24px;
      background: rgba(11, 7, 3, 0.44);
      backdrop-filter: blur(18px);
    }
    .brand-modal-backdrop[hidden] { display: none !important; }
    .brand-modal-card {
      position: relative;
      width: min(100%, 640px);
      border: 1px solid rgba(216, 132, 53, 0.42);
      border-radius: 24px;
      padding: 22px;
      color: var(--text);
      background:
        radial-gradient(circle at 10% 16%, rgba(242, 178, 101, 0.22), transparent 36%),
        radial-gradient(circle at 92% 0%, rgba(232, 160, 96, 0.16), transparent 34%),
        linear-gradient(145deg, #fffefb 0%, #fff9f0 52%, #fff4e6 100%);
      box-shadow: 0 34px 95px rgba(44, 31, 18, 0.28);
    }
    body:not(.theme-light) .brand-modal-card {
      border-color: rgba(232, 160, 96, 0.42);
      color: #fff8ef;
      background:
        radial-gradient(circle at 10% 12%, rgba(242, 178, 101, 0.18), transparent 36%),
        radial-gradient(circle at 92% 0%, rgba(232, 160, 96, 0.12), transparent 36%),
        linear-gradient(145deg, #2a2118 0%, #241c16 58%, #1a1510 100%);
      box-shadow: 0 34px 110px rgba(0, 0, 0, 0.62);
    }
    .brand-modal-card h3 {
      margin: 0 0 8px;
      font-size: 26px;
      letter-spacing: -0.045em;
    }
    .brand-modal-copy {
      margin: 0 0 16px;
      color: var(--text-faint);
      font-size: 13px;
      line-height: 1.45;
      font-weight: 800;
    }
    .brand-modal-field {
      margin: 10px 0 0 !important;
      padding: 10px 12px;
      border: 1px solid rgba(216, 132, 53, 0.55);
      border-radius: 18px;
      background: rgba(255, 252, 246, 0.92);
      box-shadow: 0 0 0 1px rgba(242, 178, 101, 0.12);
    }
    body:not(.theme-light) .brand-modal-field {
      border-color: rgba(232, 160, 96, 0.48);
      background: rgba(242, 178, 101, 0.10);
      box-shadow: 0 0 0 1px rgba(242, 178, 101, 0.10);
    }
    .brand-modal-field input {
      border-color: rgba(216, 132, 53, 0.28);
      background: rgba(255, 255, 255, 0.72);
    }
    body:not(.theme-light) .brand-modal-field input {
      border-color: rgba(255, 255, 255, 0.14);
      background: rgba(0, 0, 0, 0.22);
    }
    .brand-modal-close {
      position: absolute;
      top: 14px;
      right: 14px;
      width: 34px;
      height: 34px;
      border: 1px solid var(--control-line);
      border-radius: 999px;
      color: var(--text);
      background: var(--surface);
      font: inherit;
      font-size: 20px;
      font-weight: 900;
      cursor: pointer;
    }
    .brand-modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 9px;
      margin-top: 16px;
    }
    .brand-modal-button.secondary {
      color: var(--text-button);
      background: var(--surface);
    }
    .rename-project-modal-card { width: min(100%, 540px); }
    .rename-project-field input[aria-invalid="true"] {
      border-color: rgba(220, 38, 38, 0.78);
      box-shadow: 0 0 0 3px rgba(220, 38, 38, 0.12);
    }
    .rename-project-guide {
      display: grid;
      gap: 5px;
      margin-top: 12px;
      padding: 12px 14px;
      border: 1px solid rgba(217, 119, 6, 0.28);
      border-radius: 15px;
      color: var(--text-faint);
      background: rgba(245, 158, 11, 0.09);
      font-size: 12px;
      line-height: 1.45;
      font-weight: 750;
    }
    .rename-project-guide strong { color: var(--text); }
    .rename-project-guide code {
      border-radius: 6px;
      padding: 2px 6px;
      color: var(--accent-contrast);
      background: rgba(217, 119, 6, 0.14);
      font: 850 12px/1.4 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    }
    .rename-project-error {
      margin: 10px 0 0;
      color: #dc2626;
      font-size: 12px;
      line-height: 1.45;
      font-weight: 900;
    }
    body:not(.theme-light) .rename-project-error { color: #fca5a5; }
    .brand-modal-button:disabled {
      cursor: not-allowed;
      opacity: 0.5;
      transform: none;
    }
    .advanced-body .eleven-actions {
      justify-content: flex-start;
      flex-wrap: wrap;
      margin-top: 0;
      margin-bottom: 8px;
    }
    .file-picker {
      width: 100%;
      min-height: 54px;
      display: grid;
      grid-template-columns: auto minmax(0, 1fr);
      align-items: center;
      gap: 12px;
      border: 1px solid var(--control-line);
      border-radius: 14px;
      padding: 8px;
      color: var(--text-soft);
      background: transparent;
      cursor: pointer;
    }
    body.theme-light .file-picker {
      background: transparent;
    }
    .file-picker input[type="file"] {
      position: absolute;
      width: 1px;
      height: 1px;
      opacity: 0;
      pointer-events: none;
    }
    .file-picker-button {
      min-height: 36px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border: 1px solid var(--control-line);
      border-radius: 11px;
      padding: 8px 14px;
      color: var(--text-soft);
      background: transparent;
      box-shadow: none;
      font-size: 12px;
      font-weight: 950;
      white-space: nowrap;
    }
    .file-picker-name {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--text-soft);
      font-size: 13px;
      font-weight: 800;
    }
    .advanced-body {
      display: grid;
      gap: 10px;
      padding: 12px 14px 14px;
      border-top: 1px solid var(--control-line-soft);
      max-height: none;
      overflow: visible;
    }
    .advanced-body .field:first-child { margin-top: 0; }
    /* Native WebViews compress form controls more than browser tabs. These
       dimensions apply only when AurexVideo is running inside Tauri. */
    html.aurexvideo-desktop-app .advanced-body {
      gap: 7px;
      padding: 14px;
    }
    html.aurexvideo-desktop-app .advanced-body .field select,
    html.aurexvideo-desktop-app .advanced-body .field input {
      min-height: 50px;
      border-radius: 10px;
      padding: 10px 40px 10px 15px;
      font-size: 15px;
    }
    html.aurexvideo-desktop-app .advanced-body .speed-preset {
      min-height: 38px;
      padding: 8px 6px;
      border-radius: 10px;
      font-size: 14px;
    }
    html.aurexvideo-desktop-app .advanced-body .render-option-check {
      min-height: 34px;
      padding: 7px 10px;
      font-size: 12px;
    }
    @media (max-width: 720px) {
      .advanced-check-grid { grid-template-columns: 1fr; }
      .advanced-body .field { grid-template-columns: 1fr; }
      .advanced-body .render-size-field { grid-template-columns: 1fr; }
      .advanced-body .render-size-field select { width: 100%; }
      .advanced-body .speed-presets { margin-left: 0; }
      .advanced-body .render-speed-block,
      .advanced-body .render-volume-block {
        grid-template-columns: 1fr;
        margin: 0;
      }
      .advanced-body .render-speed-field,
      .advanced-body .render-volume-field,
      .advanced-body .render-options-stack,
      .advanced-body .render-speed-block > .speed-presets,
      .advanced-body .render-volume-block > .volume-presets {
        grid-column: 1;
        grid-row: auto;
      }
      .advanced-body .render-speed-block > .speed-presets,
      .advanced-body .render-volume-block > .volume-presets { justify-content: flex-start; }
      .advanced-body .render-option-row { display: flex; }
      .brand-modal-actions { flex-direction: column-reverse; }
      .brand-modal-button { width: 100%; }
    }
    .field-label .field-icon,
    [data-pane="elevenlabs"] .field-icon,
    [data-pane="edgetts"] .field-icon {
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
      font-size: inherit;
    }
    .field input,
    .field select {
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--control-line);
      border-radius: 13px;
      padding: 9px 11px;
      color: var(--text);
      background: var(--field-bg);
    }
    .speed-presets {
      display: flex;
      align-items: center;
      justify-content: center;
      width: fit-content;
      max-width: 100%;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: -2px;
      margin-bottom: 12px;
    }
    .speed-preset {
      min-height: 34px;
      padding: 8px 14px;
      border: 1px solid var(--control-line-faint);
      border-radius: 12px;
      color: var(--text-soft);
      background: transparent;
      font-size: 12px;
      font-weight: 900;
      cursor: pointer;
    }
    .speed-preset.active {
      color: var(--text-soft);
      background: rgba(242, 178, 101, 0.16);
      border-color: rgba(232, 160, 96, 0.55);
    }
    .selected-box {
      max-width: 440px;
      margin: 0 auto;
      border: 1px solid rgba(232, 160, 96, 0.65);
      border-radius: 16px;
      padding: 10px 11px;
      background: rgba(242, 178, 101, 0.14);
      box-shadow: 0 0 0 1px rgba(242, 178, 101, 0.18);
      text-align: center;
    }
    .selected-box strong { display: block; margin-bottom: 3px; }
    .selected-box span { display: block; color: var(--muted); font-size: 12px; }
    .form-actions,
    .actions { display: flex; align-items: center; justify-content: flex-end; gap: 6px; flex-wrap: nowrap; white-space: nowrap; }
    .status {
      border: 1px solid var(--control-line-soft);
      border-radius: 16px;
      padding: 9px 11px;
      background: var(--surface);
      color: var(--status-text);
      font-size: 13px;
      line-height: 1.45;
      margin-top: 10px;
    }
    .render-machine .status {
      max-width: 440px;
      margin: 10px auto 0;
      text-align: center;
    }
    .status.good { border-color: rgba(242, 178, 101, 0.35); color: var(--good-text); }
    .status.warn { border-color: rgba(255, 171, 64, 0.38); color: var(--warn-text); }
    .status.bad { border-color: rgba(255, 82, 82, 0.42); color: var(--danger-text); }
    .cache-warning {
      margin-top: 10px;
      border: 1px solid rgba(255, 171, 64, 0.38);
      border-radius: 16px;
      padding: 10px 12px;
      color: var(--warning-text);
      background: rgba(255, 171, 64, 0.08);
      font-size: 12px;
      line-height: 1.45;
    }
    .cache-warning strong { display: block; margin-bottom: 6px; color: var(--warn-text); }
    .cache-warning ul { margin: 0; padding-left: 18px; }
    .cache-warning li + li { margin-top: 6px; }
    .header-warning { max-width: 820px; }
    .tabs {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-top: 12px;
      margin-bottom: 12px;
    }
    .tab,
    .select-btn,
    .refresh-btn,
    .delete-output-btn,
    .icon-btn {
      border: 1px solid var(--control-line-faint);
      border-radius: 13px;
      padding: 8px 10px;
      cursor: pointer;
      color: var(--text-soft);
      background: transparent;
      font-weight: 900;
      gap: 6px;
      min-height: 36px;
      font-size: 12px;
      line-height: 1;
      flex: 0 0 auto;
    }
    .tab {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      color: var(--text-soft);
    }
    .tab .tab-icon,
    .tab.active .tab-icon,
    .tab[data-engine="elevenlabs"] .tab-icon,
    .tab[data-engine="edgetts"] .tab-icon {
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
      text-shadow: none;
    }
    .icon-btn { display: inline-flex; align-items: center; justify-content: center; text-decoration: none; }
    .icon-btn[hidden],
    .small-link[hidden] { display: none !important; }
    .btn-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      min-width: 18px;
      flex: 0 0 18px;
      border-radius: 0;
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
      font-size: inherit;
      font-weight: 400;
      line-height: 1;
      text-shadow: none;
    }
    .btn-icon svg,
    .tab-icon svg,
    .field-icon svg,
    .render-lead-icon svg,
    .advanced-heading-icon svg,
    .project-rename-icon svg,
    .project-duplicate-icon svg,
    .account-upgrade-icon svg {
      width: 18px;
      height: 18px;
      max-width: 100%;
      max-height: 100%;
      display: block;
      stroke: currentColor;
      fill: none;
    }
    .tab-icon,
    .field-icon,
    .render-lead-icon,
    .advanced-heading-icon,
    .project-rename-icon,
    .project-duplicate-icon,
    .account-upgrade-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      min-width: 18px;
      flex: 0 0 18px;
      border-radius: 0;
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
      font-size: inherit;
      font-weight: 400;
      line-height: 1;
      text-shadow: none;
    }
    .render-lead-icon {
      width: 20px;
      height: 20px;
      min-width: 20px;
      flex: 0 0 20px;
    }
    .advanced-heading-icon {
      width: 20px;
      height: 20px;
      min-width: 20px;
      flex: 0 0 20px;
    }
    .tab .tab-icon {
      width: 18px;
      height: 18px;
      min-width: 18px;
    }
    .delete-output-btn .btn-icon,
    .stop-render-btn .btn-icon {
      color: #ff8d8d;
    }
    body.theme-light .delete-output-btn .btn-icon,
    body.theme-light .stop-render-btn .btn-icon {
      color: #9f2e2e;
    }
    .project-rename-button,
    .project-duplicate-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: inherit;
    }
    .project-rename-icon,
    .project-duplicate-icon {
      width: 14px;
      height: 14px;
      min-width: 14px;
      flex: 0 0 14px;
      opacity: 0.75;
    }
    .account-upgrade-icon {
      width: 18px;
      height: 18px;
      color: currentColor;
    }
    .small-link .btn-icon,
    .select-btn .btn-icon {
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
    }
    .theme-toggle .btn-icon { color: currentColor; background: transparent !important; box-shadow: none !important; }
    #refreshProjects .btn-icon { color: currentColor; background: transparent !important; box-shadow: none !important; }
    a[href="/upload"].icon-btn .btn-icon,
    #startRender .btn-icon { color: currentColor; background: transparent !important; box-shadow: none !important; }
    .select-btn .btn-icon { color: currentColor; background: transparent !important; box-shadow: none !important; }
    .small-link.icon-btn .btn-icon { color: currentColor; background: transparent !important; box-shadow: none !important; }
    .row-video-slot .small-link.icon-btn .btn-icon,
    a.small-link.icon-btn[href*="/output/final_video.mp4"] .btn-icon { color: currentColor; background: transparent !important; box-shadow: none !important; }
    .reveal-output-btn .btn-icon { color: currentColor; background: transparent !important; box-shadow: none !important; }
    .stop-render-btn {
      color: var(--delete-text);
      background: rgba(255, 82, 82, 0.12);
      border-color: rgba(255, 82, 82, 0.28);
    }
    .stop-render-btn .btn-icon { color: #ff8d8d; background: transparent !important; box-shadow: none !important; text-shadow: none; }
    .delete-output-btn .btn-icon { color: #ff8d8d; background: transparent !important; box-shadow: none !important; text-shadow: none; }
    .icon-btn.disabled,
    .icon-btn:disabled {
      opacity: 0.42;
      cursor: not-allowed;
    }
    .icon-btn.disabled {
      pointer-events: none;
    }
    .tab.active,
    .select-btn.active {
      color: var(--text-soft);
      border: 1px solid rgba(232, 160, 96, 0.55);
      background: rgba(242, 178, 101, 0.16);
      box-shadow: none;
    }
    .select-btn.active .btn-icon,
    .refresh-btn .btn-icon { color: currentColor; background: transparent !important; box-shadow: none !important; text-shadow: none; }
    .refresh-btn {
      min-height: 42px;
      color: var(--text-soft);
      border: 1px solid var(--control-line);
      background: transparent;
      box-shadow: none;
    }
    .refresh-btn.guide-header-btn {
      color: var(--text-soft);
      border: 1px solid var(--control-line);
      background: transparent;
      box-shadow: none;
    }
    .refresh-btn.guide-header-btn .btn-icon {
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
    }
    body.theme-light .refresh-btn.guide-header-btn {
      color: var(--text-soft);
      border: 1px solid rgba(79, 57, 31, 0.16);
      background: transparent;
      box-shadow: none;
    }
    body.theme-light .refresh-btn.guide-header-btn .btn-icon {
      color: var(--text-soft);
      background: transparent !important;
      box-shadow: none;
    }
    .dashboard-header .header-tools .header-nav-action,
    body.theme-light .dashboard-header .header-tools .header-nav-action {
      min-height: 48px;
      gap: 9px;
      border: 1px solid var(--control-line-soft);
      border-radius: 13px;
      padding: 9px 11px;
      color: var(--text-soft);
      background: transparent;
      box-shadow: none;
      font-size: 13px;
      transition: color .16s ease, background .16s ease, box-shadow .16s ease;
    }
    body.theme-light .dashboard-header .header-tools .header-nav-action {
      border-color: rgba(79, 57, 31, 0.12);
    }
    .dashboard-header .header-tools .header-nav-action:hover {
      color: var(--text);
      background: rgba(255, 255, 255, 0.05);
      box-shadow: none;
      transform: none;
    }
    body.theme-light .dashboard-header .header-tools .header-nav-action:hover {
      color: var(--text);
      background: rgba(79, 57, 31, 0.04);
      box-shadow: none;
      transform: none;
    }
    .dashboard-header .header-tools .header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools .header-nav-action .btn-icon {
      width: 26px;
      height: 26px;
      flex: 0 0 26px;
      border-radius: 0;
      color: currentColor;
      background: transparent;
      box-shadow: none;
      font-size: 21px;
      font-weight: 850;
    }
    .dashboard-header .header-tools .header-nav-action:hover .btn-icon,
    body.theme-light .dashboard-header .header-tools .header-nav-action:hover .btn-icon {
      color: var(--accent);
    }
    .dashboard-header .header-tools .theme-toggle.header-nav-action .btn-icon,
    .dashboard-header .header-tools #refreshProjects.header-nav-action .btn-icon,
    .dashboard-header .header-tools a[href="/upload"].header-nav-action .btn-icon,
    .dashboard-header .header-tools a[href="/new-project"].header-nav-action .btn-icon,
    .dashboard-header .header-tools .settings-button.header-nav-action .btn-icon,
    .dashboard-header .header-tools #refreshProjects.header-nav-action:hover .btn-icon {
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
    }
    body.theme-light .dashboard-header .header-tools .theme-toggle.header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools #refreshProjects.header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools a[href="/upload"].header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools a[href="/new-project"].header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools .settings-button.header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools #refreshProjects.header-nav-action:hover .btn-icon {
      color: #20170f;
      background: transparent;
    }
    .dashboard-header .header-tools .update-available-button,
    body.theme-light .dashboard-header .header-tools .update-available-button {
      border-color: transparent;
      color: #4c861e;
      background: transparent;
      box-shadow: none;
    }
    .dashboard-header .header-tools .update-available-button .btn-icon,
    body.theme-light .dashboard-header .header-tools .update-available-button .btn-icon {
      color: #5ba126;
      background: transparent;
      box-shadow: none;
    }
    .dashboard-header .header-tools .update-available-button:hover,
    body.theme-light .dashboard-header .header-tools .update-available-button:hover {
      color: #315f12;
      background: rgba(126, 213, 52, .13);
      box-shadow: inset 0 0 0 1px rgba(92, 158, 36, .15);
    }
    .delete-output-btn {
      color: var(--delete-text);
      background: rgba(255, 82, 82, 0.12);
      border-color: rgba(255, 82, 82, 0.28);
    }
    .delete-output-btn:hover { background: rgba(255, 82, 82, 0.2); }
    .delete-choice-backdrop {
      position: fixed;
      inset: 0;
      z-index: 500;
      display: grid;
      place-items: center;
      padding: 22px;
      background: rgba(15, 12, 9, 0.52);
      backdrop-filter: blur(6px);
    }
    .delete-choice-card {
      width: min(100%, 470px);
      border: 1px solid rgba(232, 160, 96, 0.52);
      border-radius: 24px;
      padding: 24px;
      color: #20170f;
      background: var(--dashboard-surface-warm, rgba(250, 244, 236, 0.96));
      box-shadow: 0 12px 30px rgba(91, 61, 30, 0.08);
    }
    body:not(.theme-light) .delete-choice-backdrop {
      background: rgba(8, 6, 4, 0.64);
    }
    body:not(.theme-light) .delete-choice-card {
      border-color: rgba(232, 160, 96, 0.46);
      color: #fff8ef;
      background: var(--dashboard-surface-dark, rgba(31, 29, 26, 0.96));
      box-shadow: 0 16px 36px rgba(0, 0, 0, 0.24);
    }
    .delete-choice-card h3 { margin: 0 0 8px; font-size: 25px; letter-spacing: -0.04em; color: inherit; }
    .delete-choice-card p { margin: 0; color: rgba(79, 57, 31, 0.72); font-size: 13px; line-height: 1.55; }
    body:not(.theme-light) .delete-choice-card p { color: rgba(255, 247, 237, 0.72); }
    .delete-choice-project { color: #c43b3b; font-weight: 950; }
    body:not(.theme-light) .delete-choice-project { color: #ff8f8f; }
    .delete-choice-actions { display: grid; gap: 10px; margin-top: 20px; }
    .delete-choice-actions button {
      width: 100%;
      min-height: 56px;
      border-radius: 14px;
      padding: 12px 18px;
      font: inherit;
      font-size: 17px;
      line-height: 1.15;
      font-weight: 950;
      cursor: pointer;
      transition: transform 160ms ease, filter 160ms ease, box-shadow 160ms ease;
    }
    .delete-choice-actions button:hover:not(:disabled) {
      transform: translateY(-1px);
      filter: brightness(1.04);
    }
    .delete-choice-output-button {
      color: #3f2506;
      border: 1px solid rgba(205, 126, 30, 0.48);
      background: linear-gradient(135deg, #f6c46e, #df8d2e);
      box-shadow: 0 10px 26px rgba(205, 126, 30, 0.18);
    }
    .delete-choice-output-button:disabled {
      opacity: 0.48;
      cursor: not-allowed;
      box-shadow: none;
    }
    .delete-choice-project-button {
      color: #fff7f7;
      border: 1px solid rgba(255, 82, 82, 0.46);
      background: linear-gradient(135deg, #d85a5a, #9f2e2e);
      box-shadow: 0 10px 26px rgba(159, 46, 46, 0.2);
    }
    .delete-choice-cancel-button {
      color: #123f3a;
      border: 1px solid rgba(41, 124, 115, 0.38);
      background: linear-gradient(135deg, #d7eee8, #afd5cc);
      box-shadow: 0 10px 26px rgba(41, 124, 115, 0.14);
    }
    body:not(.theme-light) .delete-choice-cancel-button {
      color: #d7f3ee;
      border-color: rgba(120, 196, 184, 0.34);
      background: linear-gradient(135deg, rgba(55, 92, 86, 0.95), rgba(36, 68, 63, 0.98));
      box-shadow: 0 10px 26px rgba(0, 0, 0, 0.22);
    }
    .check { display: flex; align-items: center; gap: 9px; margin: 8px 0 12px; color: var(--status-text); font-size: 13px; }
    .start {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 9px;
      min-height: 42px;
      border: 1px solid var(--control-line);
      border-radius: 14px;
      padding: 0 18px;
      color: var(--text-soft);
      background: transparent;
      box-shadow: none;
      font-weight: 900;
      cursor: pointer;
    }
    .render-machine .form-actions { justify-content: center; }
    .start:disabled { cursor: wait; opacity: 0.6; }
    .upload-panel {
      max-width: 440px;
      margin: 14px auto 0;
      border: 1px solid rgba(242, 178, 101, 0.16);
      border-radius: 20px;
      padding: 14px;
      background:
        radial-gradient(circle at 20% 0%, rgba(242, 178, 101, 0.10), transparent 40%),
        var(--surface-panel);
    }
    .upload-panel[hidden] { display: none !important; }
    .upload-head { text-align: center; margin-bottom: 10px; }
    .upload-head h3 { margin: 0 0 5px; font-size: 18px; letter-spacing: -0.035em; }
    .upload-head span {
      display: block;
      color: var(--text-faint);
      font-size: 12px;
      line-height: 1.45;
    }
    .upload-field { margin-top: 10px; margin-bottom: 0; }
    .upload-field textarea {
      width: 100%;
      resize: vertical;
      min-height: 104px;
      border: 1px solid var(--control-line);
      border-radius: 13px;
      padding: 9px 11px;
      color: var(--text);
      background: var(--field-bg);
      font-family: inherit;
      line-height: 1.45;
    }
    .upload-field.compact { max-width: 240px; }
    .upload-actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-top: 12px;
    }
    .upload-btn {
      min-height: 38px;
      border: 1px solid var(--control-line);
      border-radius: 13px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 7px;
      color: var(--text-button);
      background: var(--surface);
      font-size: 12px;
      font-weight: 950;
      cursor: pointer;
    }
    .upload-btn.youtube { border-color: rgba(255, 82, 82, 0.34); background: rgba(255, 82, 82, 0.12); }
    .upload-btn.facebook { border-color: rgba(74, 144, 226, 0.34); }
    .upload-btn:disabled { cursor: not-allowed; opacity: 0.46; }
    .upload-status,
    .upload-result {
      margin-top: 10px;
      border: 1px solid var(--control-line-soft);
      border-radius: 14px;
      padding: 9px 10px;
      color: var(--status-text);
      background: var(--surface);
      font-size: 12px;
      line-height: 1.45;
    }
    .upload-status.good,
    .upload-result.good { border-color: rgba(242, 178, 101, 0.34); color: var(--good-text); }
    .upload-status.bad,
    .upload-result.bad { border-color: rgba(255, 82, 82, 0.40); color: var(--danger-text); }
    .upload-status.warn,
    .upload-result.warn { border-color: rgba(255, 171, 64, 0.34); color: var(--warn-text); }
    .upload-result a { color: var(--accent); font-weight: 900; }
    .render-state {
      width: 100%;
      margin: 14px 0 0;
      border: 1px solid rgba(242, 178, 101, 0.16);
      border-radius: 16px;
      padding: 12px;
      color: var(--good-text);
      background: var(--surface-panel);
    }
    .state-head {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 9px;
      color: var(--text);
      font-size: 13px;
    }
    .state-percent { margin-left: auto; color: var(--accent); font-weight: 900; }
    .state-progress {
      height: 7px;
      margin-bottom: 11px;
      overflow: hidden;
      border-radius: 999px;
      background: var(--surface);
    }
    .state-progress span {
      display: block;
      width: 0;
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), #ffd08a);
      transition: width 280ms ease;
    }
    .state-dot {
      width: 9px;
      height: 9px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 16px rgba(242, 178, 101, 0.65);
    }
    .render-state.running .state-dot {
      animation: statePulse 1s ease-in-out infinite;
    }
    .render-state.failed .state-dot {
      background: #ff5252;
      box-shadow: 0 0 16px rgba(255, 82, 82, 0.65);
      animation: none;
    }
    .render-state.cancelled .state-dot {
      background: #ffab40;
      box-shadow: 0 0 16px rgba(255, 171, 64, 0.55);
      animation: none;
    }
    .state-list {
      display: grid;
      gap: 7px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .state-list li {
      display: block;
      overflow: hidden;
      border-radius: 10px;
      padding: 8px 9px;
      color: var(--text-soft);
      background: var(--surface);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      line-height: 1.45;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .state-list li::before {
      content: none;
    }
    @keyframes statePulse {
      0%, 100% { transform: scale(0.92); opacity: 0.65; }
      50% { transform: scale(1.28); opacity: 1; }
    }
    .list-head,
    .project-row {
      display: grid;
      grid-template-columns: minmax(180px, 1fr) 100px 120px minmax(320px, 420px);
      gap: 12px;
      align-items: center;
    }
    .list-head {
      padding: 0 18px 10px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 900;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    .list-head span:last-child,
    .list-head span:nth-child(3) {
      justify-self: center;
      text-align: center;
    }
    .project-list {
      display: grid;
      gap: 10px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .project-row {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 11px 12px;
      background: transparent;
      transition: border-color 0.18s ease, background 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
    }
    .project-row .actions {
      display: grid;
      grid-template-columns: 74px 78px 96px 64px 70px;
      justify-content: end;
      gap: 6px;
      min-width: 0;
      white-space: nowrap;
    }
    .project-row .actions .icon-btn,
    .project-row .actions .select-btn,
    .project-row .actions .small-link,
    .project-row .actions .delete-output-btn {
      width: 100%;
      min-width: 0;
      min-height: 38px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 7px 8px;
      border-radius: 12px;
      font-size: 12px;
      gap: 4px;
      box-sizing: border-box;
    }
    .project-row .actions .btn-icon {
      width: 16px;
      height: 16px;
      min-width: 16px;
      flex: 0 0 16px;
      margin: 0;
    }
    .project-row .actions .btn-icon svg {
      width: 16px;
      height: 16px;
      display: block;
    }
    .project-row .actions .icon-btn,
    .project-row .actions .select-btn,
    .project-row .actions .small-link,
    .project-row .actions .delete-output-btn,
    .project-row .actions .copy-script-btn,
    .project-row .actions .select-btn.active {
      color: var(--text);
      border: 1px solid var(--control-line-soft);
      background: transparent;
      box-shadow: none;
    }
    .project-row .actions .delete-output-btn {
      color: #ffb8b8;
      border-color: rgba(255, 140, 140, 0.28);
    }
    .project-row .actions .icon-btn > span:last-child,
    .project-row .actions .select-btn > span:last-child,
    .project-row .actions .small-link > span:last-child,
    .project-row .actions .delete-output-btn > span:last-child {
      overflow: visible;
      text-overflow: clip;
      white-space: nowrap;
    }
    .project-row .row-video-slot {
      min-width: 0;
      width: 100%;
    }
    .project-row.selected {
      border-color: rgba(232, 160, 96, 0.65);
      background: rgba(242, 178, 101, 0.14);
      box-shadow: 0 0 0 1px rgba(242, 178, 101, 0.18);
      transform: none;
    }
    .project-row.selected .project-name { color: var(--text); }
    body.theme-light .project-row.selected .project-name { color: #20170f; }
    body.theme-light .project-row.selected {
      border-color: rgba(216, 132, 53, 0.70);
      background: rgba(242, 178, 101, 0.12);
      box-shadow: 0 0 0 1px rgba(216, 132, 53, 0.16);
      transform: none;
    }
    body.theme-light .project-row .actions .icon-btn,
    body.theme-light .project-row .actions .select-btn:not(.active),
    body.theme-light .project-row .actions .select-btn.active,
    body.theme-light .project-row .actions .small-link,
    body.theme-light .project-row .actions .delete-output-btn,
    body.theme-light .project-row .actions .copy-script-btn {
      color: #20170f;
      border: 1px solid rgba(79, 57, 31, 0.14);
      background: transparent;
      box-shadow: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 7px 10px;
    }
    body.theme-light .project-row .actions .icon-btn .btn-icon,
    body.theme-light .project-row .actions .select-btn:not(.active) .btn-icon,
    body.theme-light .project-row .actions .select-btn.active .btn-icon,
    body.theme-light .project-row .actions .small-link .btn-icon,
    body.theme-light .project-row .actions .copy-script-btn .btn-icon,
    body.theme-light .project-row .actions .delete-output-btn .btn-icon {
      width: 16px;
      height: 16px;
      min-width: 16px;
      flex: 0 0 16px;
      border-radius: 0;
      color: currentColor;
      background: transparent !important;
      box-shadow: none !important;
      font-size: inherit;
    }
    body.theme-light .project-row .actions .delete-output-btn {
      color: #9f2e2e;
      border-color: rgba(159, 46, 46, 0.22);
    }
    body.theme-light .project-row .actions .delete-output-btn .btn-icon {
      color: #9f2e2e;
    }
    .project-main { display: grid; gap: 4px; min-width: 0; }
    .project-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 18px; font-weight: 900; letter-spacing: -0.02em; }
    .project-meta-row { display: flex; align-items: center; gap: 6px; width: fit-content; }
    .project-slide-count {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: fit-content;
      min-height: 26px;
      padding: 4px 10px;
      border: 1px solid rgba(34, 197, 94, 0.28);
      border-radius: 999px;
      color: var(--text);
      background: rgba(34, 197, 94, 0.12);
      box-shadow: none;
      font-size: 12px;
      font-weight: 950;
    }
    body.theme-light .project-slide-count {
      color: #0b1220;
      border-color: rgba(22, 163, 74, 0.28);
      background: rgba(34, 197, 94, 0.08);
      box-shadow: none;
    }
    body.theme-light .project-rename-button,
    body.theme-light .project-duplicate-button {
      background: transparent;
    }
    body.theme-light .status-pill.ok {
      color: #0b1220;
      border: 1px solid rgba(22, 163, 74, 0.28);
      background: rgba(34, 197, 94, 0.08);
      box-shadow: none;
    }
    .status-pill.warn { color: #4d3100; background: rgba(251, 191, 36, 0.20); border: 1px solid rgba(217, 119, 6, 0.38); }
    .social-status-cell { display: grid; gap: 4px; justify-self: start; min-width: 0; }
    .social-schedule-time { color: var(--muted); font-size: 11px; font-weight: 800; line-height: 1.2; white-space: nowrap; }
    .project-sort-control { display: grid; gap: 4px; color: var(--muted); font-size: 10px; font-weight: 900; letter-spacing: .08em; text-transform: uppercase; }
    .project-sort-control select { min-height: 34px; min-width: 148px; border: 1px solid var(--control-line); border-radius: 9px; padding: 6px 9px; color: var(--text); background: var(--field-bg); font: inherit; font-size: 12px; font-weight: 800; letter-spacing: normal; text-transform: none; }
    body.theme-light .speed-preset:not(.active),
    body.theme-light .mode-toggle label:not(:has(input:checked)),
    body.theme-light .tab:not(.active) {
      color: var(--text-soft) !important;
      background: transparent;
    }
    .tab:not(.active),
    .mode-toggle label:not(:has(input:checked)),
    .speed-preset:not(.active) {
      color: var(--text-soft);
    }
    .project-rename-button,
    .project-duplicate-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 26px;
      height: 26px;
      flex: 0 0 26px;
      border: 1px solid var(--control-line-soft);
      border-radius: 999px;
      padding: 0;
      color: var(--text);
      background: transparent;
      font: inherit;
      cursor: pointer;
      transition: transform 150ms ease, border-color 150ms ease, background 150ms ease;
    }
    .project-rename-button:hover,
    .project-duplicate-button:hover { border-color: var(--control-line); background: rgba(255, 255, 255, 0.05); transform: none; }
    .project-rename-button:disabled,
    .project-duplicate-button:disabled { cursor: wait; opacity: 0.55; transform: none; }
    body.theme-light .project-rename-button,
    body.theme-light .project-duplicate-button { color: #20170f; background: transparent; }
    .status-pill {
      justify-self: start;
      border-radius: 999px;
      padding: 7px 11px;
      font-size: 12px;
      font-weight: 900;
      white-space: nowrap;
    }
    .status-pill.ok {
      color: var(--text);
      border: 1px solid rgba(34, 197, 94, 0.28);
      background: rgba(34, 197, 94, 0.12);
      box-shadow: none;
    }
    .status-pill.bad { color: #fff0f0; background: rgba(255, 82, 82, 0.22); border: 1px solid rgba(255, 82, 82, 0.35); }
    .muted, .empty { color: var(--muted); }
    .row-video-slot { display: inline-flex; align-items: center; flex: 0 0 auto; }

    /* Functional icon palette: keep controls quiet while making actions scannable. */
    .dashboard-header .header-tools .theme-toggle.header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools .theme-toggle.header-nav-action .btn-icon {
      color: #e8a000 !important;
    }
    .dashboard-header .header-tools #refreshProjects.header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools #refreshProjects.header-nav-action .btn-icon {
      color: #7c3aed !important;
    }
    .dashboard-header .header-tools a[href="/upload"].header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools a[href="/upload"].header-nav-action .btn-icon {
      color: #2563eb !important;
    }
    .dashboard-header .header-tools a[href="/new-project"].header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools a[href="/new-project"].header-nav-action .btn-icon {
      color: #16a34a !important;
    }
    .dashboard-header .header-tools .settings-button.header-nav-action .btn-icon,
    body.theme-light .dashboard-header .header-tools .settings-button.header-nav-action .btn-icon {
      color: #f97316 !important;
    }
    .dashboard-header .header-tools .update-available-button .btn-icon,
    body.theme-light .dashboard-header .header-tools .update-available-button .btn-icon {
      color: #65a30d !important;
    }

    .render-machine .render-lead-icon,
    body.theme-light .render-machine .render-lead-icon {
      color: #8b5cf6 !important;
    }
    .render-guide-links .guide-header-btn:first-child .btn-icon,
    body.theme-light .render-guide-links .guide-header-btn:first-child .btn-icon {
      color: #db2777 !important;
    }
    .render-guide-links .guide-header-btn:last-child .btn-icon,
    body.theme-light .render-guide-links .guide-header-btn:last-child .btn-icon {
      color: #0284c7 !important;
    }
    .render-machine .tab[data-engine="elevenlabs"] .tab-icon,
    body.theme-light .render-machine .tab[data-engine="elevenlabs"] .tab-icon {
      color: #db2777 !important;
    }
    .render-machine .tab[data-engine="edgetts"] .tab-icon,
    body.theme-light .render-machine .tab[data-engine="edgetts"] .tab-icon {
      color: #f59e0b !important;
    }
    .render-machine .primary-field .field-icon,
    body.theme-light .render-machine .primary-field .field-icon {
      color: #ec4899 !important;
    }
    .render-machine .api-key-field .field-icon,
    body.theme-light .render-machine .api-key-field .field-icon {
      color: #e8a000 !important;
    }
    .render-machine #startRender .btn-icon,
    body.theme-light .render-machine #startRender .btn-icon {
      color: #f59e0b !important;
    }
    .render-machine .reveal-output-btn .btn-icon,
    body.theme-light .render-machine .reveal-output-btn .btn-icon {
      color: #7c3aed !important;
    }
    .render-machine .advanced-heading-icon,
    body.theme-light .render-machine .advanced-heading-icon {
      color: #8b5cf6 !important;
    }
    .render-machine [data-advanced-engine="elevenlabs"] .advanced-field-icon,
    body.theme-light .render-machine [data-advanced-engine="elevenlabs"] .advanced-field-icon {
      color: #db2777 !important;
    }
    .render-machine [data-advanced-engine="edgetts"] .advanced-field-icon,
    body.theme-light .render-machine [data-advanced-engine="edgetts"] .advanced-field-icon {
      color: #0284c7 !important;
    }
    .render-machine .render-speed-field .advanced-field-icon,
    body.theme-light .render-machine .render-speed-field .advanced-field-icon {
      color: #16a34a !important;
    }
    .render-machine .render-size-field .advanced-field-icon,
    body.theme-light .render-machine .render-size-field .advanced-field-icon {
      color: #0891b2 !important;
    }
    .render-machine input[type="checkbox"] { accent-color: #22c55e; }
    .render-machine input[type="radio"] { accent-color: #f59e0b; }

    .project-rename-button .project-rename-icon,
    body.theme-light .project-rename-button .project-rename-icon {
      color: #d97706 !important;
    }
    .project-duplicate-button .project-duplicate-icon,
    body.theme-light .project-duplicate-button .project-duplicate-icon {
      color: #2563eb !important;
    }
    .project-row .actions .select-btn .btn-icon,
    body.theme-light .project-row .actions .select-btn .btn-icon {
      color: #16a34a !important;
    }
    .project-row .actions > .small-link .btn-icon,
    body.theme-light .project-row .actions > .small-link .btn-icon {
      color: #2563eb !important;
    }
    .project-row .actions .copy-script-btn .btn-icon,
    body.theme-light .project-row .actions .copy-script-btn .btn-icon {
      color: #f59e0b !important;
    }
    .project-row .actions .row-video-slot .btn-icon,
    body.theme-light .project-row .actions .row-video-slot .btn-icon {
      color: #16a34a !important;
    }
    .project-row .actions .delete-output-btn .btn-icon,
    body.theme-light .project-row .actions .delete-output-btn .btn-icon {
      color: #dc2626 !important;
    }

    /* Layered cream surfaces for the light dashboard. */
    body.theme-light {
      --dashboard-surface-warm: rgba(250, 244, 236, 0.88);
      --dashboard-row-cream: var(--dashboard-surface-warm);
      --dashboard-control-cream: var(--dashboard-surface-warm);
      --dashboard-button-white: var(--dashboard-surface-warm);
      --dashboard-selected-green-start: rgba(126, 245, 82, 0.34);
      --dashboard-selected-green-mid: rgba(190, 245, 172, 0.22);
      --dashboard-selected-border: rgba(38, 174, 66, 0.72);
      --bg: #dfd4c2;
      --body-bg:
        radial-gradient(circle at 18% 12%, rgba(222, 142, 69, 0.14), transparent 28rem),
        radial-gradient(circle at 88% 18%, rgba(187, 123, 45, 0.11), transparent 24rem),
        linear-gradient(135deg, #efe6d7, var(--bg));
    }
    body.theme-light .render-machine.panel,
    body.theme-light .slide-list.panel {
      border-color: rgba(232, 160, 96, 0.52);
      background: var(--dashboard-surface-warm);
      box-shadow: 0 12px 30px rgba(91, 61, 30, 0.05);
    }
    body.theme-light .project-row {
      background: var(--dashboard-row-cream);
    }
    body.theme-light .project-row.selected {
      background:
        linear-gradient(105deg, var(--dashboard-selected-green-start) 0%, var(--dashboard-selected-green-mid) 46%, rgba(250, 244, 236, 0.42) 100%),
        var(--dashboard-row-cream);
      border-color: var(--dashboard-selected-border);
      box-shadow: 0 5px 0 rgba(38, 174, 66, 0.10), 0 0 0 1px rgba(38, 174, 66, 0.10);
    }
    body.theme-light .project-row .actions .icon-btn,
    body.theme-light .project-row .actions .select-btn:not(.active),
    body.theme-light .project-row .actions .select-btn.active,
    body.theme-light .project-row .actions .small-link,
    body.theme-light .project-row .actions .copy-script-btn,
    body.theme-light .project-row .actions .delete-output-btn,
    body.theme-light .project-row .project-rename-button,
    body.theme-light .project-row .project-duplicate-button {
      background: var(--dashboard-button-white);
    }

    body.theme-light .dashboard-header .header-tools .header-nav-action {
      background: var(--dashboard-button-white);
    }

    body.theme-light .render-machine .guide-header-btn,
    body.theme-light .render-machine .tab:not(.active),
    body.theme-light .render-machine .mode-toggle label:not(:has(input:checked)),
    body.theme-light .render-machine .file-picker,
    body.theme-light .render-machine .file-picker-button,
    body.theme-light .render-machine .start,
    body.theme-light .render-machine .stop-render-btn,
    body.theme-light .render-machine .render-primary-actions .small-link,
    body.theme-light .render-machine .reveal-output-btn,
    body.theme-light .render-machine .render-option-check,
    body.theme-light .render-machine .render-option-choose,
    body.theme-light .render-machine .speed-preset:not(.active) {
      background: var(--dashboard-button-white);
    }
    body.theme-light .render-machine .selected-box,
    body.theme-light .render-machine .tab.active,
    body.theme-light .render-machine .mode-toggle label:has(input:checked),
    body.theme-light .render-machine .speed-preset.active {
      background:
        linear-gradient(105deg, var(--dashboard-selected-green-start) 0%, var(--dashboard-selected-green-mid) 52%, rgba(250, 244, 236, 0.42) 100%),
        var(--dashboard-control-cream);
      border-color: var(--dashboard-selected-border);
      box-shadow: 0 4px 0 rgba(38, 174, 66, 0.08), 0 0 0 1px rgba(38, 174, 66, 0.10);
    }
    body.theme-light .render-machine #renderStatus,
    body.theme-light .render-machine .render-state,
    body.theme-light .render-machine .advanced-settings {
      background: var(--dashboard-control-cream);
    }
    body.theme-light .render-machine .guide-header-btn:hover,
    body.theme-light .render-machine .file-picker-button:hover,
    body.theme-light .render-machine .start:hover,
    body.theme-light .render-machine .reveal-output-btn:hover,
    body.theme-light .render-machine .render-option-choose:hover {
      background: rgba(217, 201, 178, 0.82);
    }

    /* Brighter green states and stronger, more legible dashboard icons. */
    body.theme-light .status-pill.ok {
      border-color: rgba(34, 197, 94, 0.46);
      background: rgba(134, 239, 172, 0.24);
    }
    body.theme-light .status-pill.warn { color: #713f12; border-color: rgba(217, 119, 6, 0.36); background: rgba(253, 230, 138, 0.42); }
    .dashboard-header .header-tools a[href="/new-project"] .btn-icon,
    .project-row .actions .select-btn .btn-icon,
    .project-row .actions .row-video-slot .btn-icon {
      color: #22c55e !important;
    }
    .dashboard-header .header-tools .header-nav-action .btn-icon {
      width: 26px !important;
      height: 26px !important;
      flex-basis: 26px !important;
    }
    .dashboard-header .header-tools .header-nav-action .btn-icon svg {
      width: 18px;
      height: 18px;
      stroke-width: 2.4;
    }
    .render-machine .btn-icon,
    .render-machine .tab-icon,
    .render-machine .field-icon,
    .render-machine .advanced-heading-icon {
      width: 18px !important;
      height: 18px !important;
      min-width: 18px !important;
      flex-basis: 18px !important;
    }
    .render-machine .render-lead-icon {
      width: 20px !important;
      height: 20px !important;
      min-width: 20px !important;
      flex-basis: 20px !important;
    }
    .render-machine .btn-icon svg,
    .render-machine .tab-icon svg,
    .render-machine .field-icon svg,
    .render-machine .advanced-heading-icon svg,
    .render-machine .render-lead-icon svg {
      width: 100%;
      height: 100%;
      stroke-width: 2.4;
    }
    .project-row .actions .btn-icon,
    .project-row .project-rename-icon,
    .project-row .project-duplicate-icon {
      width: 16px;
      height: 16px;
      min-width: 16px;
      flex-basis: 16px;
    }
    .project-row .actions .btn-icon svg,
    .project-row .project-rename-icon svg,
    .project-row .project-duplicate-icon svg {
      width: 16px;
      height: 16px;
      stroke-width: 2.4;
    }
    .render-machine .render-lead-icon svg path,
    .render-machine #startRender .btn-icon svg polygon,
    .project-row .actions .row-video-slot .btn-icon svg polygon {
      fill: currentColor;
    }
    body.theme-light .render-machine.panel,
    body.theme-light .slide-list.panel {
      border-color: rgba(232, 160, 96, 0.66);
    }
    body.theme-light .project-row,
    body.theme-light .render-machine .guide-header-btn,
    body.theme-light .render-machine .tab,
    body.theme-light .render-machine .mode-toggle label,
    body.theme-light .render-machine .file-picker,
    body.theme-light .render-machine .start,
    body.theme-light .render-machine .stop-render-btn,
    body.theme-light .render-machine .render-primary-actions .small-link,
    body.theme-light .render-machine .reveal-output-btn,
    body.theme-light .render-machine .render-option-check,
    body.theme-light .render-machine .render-option-choose,
    body.theme-light .render-machine .speed-preset,
    body.theme-light .render-machine .advanced-settings,
    body.theme-light .project-row .actions .icon-btn,
    body.theme-light .project-row .actions .small-link,
    body.theme-light .project-row .project-rename-button,
    body.theme-light .project-row .project-duplicate-button,
    body.theme-light .dashboard-header .header-tools .header-nav-action {
      border-color: rgba(103, 75, 45, 0.24);
    }

    /* The same warm hierarchy in dark mode: charcoal canvas, raised controls,
       and one vivid green selection language shared with the light dashboard. */
    body:not(.theme-light) {
      --dashboard-surface-dark: rgba(31, 29, 26, 0.96);
      --dashboard-row-dark: rgba(39, 36, 32, 0.94);
      --dashboard-control-dark: rgba(45, 41, 36, 0.94);
      --dashboard-button-dark: rgba(48, 44, 39, 0.92);
      --dashboard-selected-green-start-dark: rgba(73, 240, 73, 0.30);
      --dashboard-selected-green-mid-dark: rgba(94, 210, 86, 0.14);
      --dashboard-selected-border-dark: rgba(80, 226, 92, 0.72);
      --bg: #12110f;
      --body-bg:
        radial-gradient(circle at 18% 12%, rgba(232, 128, 55, 0.12), transparent 28rem),
        radial-gradient(circle at 88% 18%, rgba(116, 88, 50, 0.13), transparent 24rem),
        linear-gradient(135deg, #1d1a17, var(--bg));
    }
    body:not(.theme-light) .render-machine.panel,
    body:not(.theme-light) .slide-list.panel {
      border-color: rgba(232, 160, 96, 0.46);
      background: var(--dashboard-surface-dark);
      box-shadow: 0 16px 36px rgba(0, 0, 0, 0.24);
    }
    body:not(.theme-light) .project-row {
      border-color: rgba(255, 238, 218, 0.16);
      background: var(--dashboard-row-dark);
    }
    body:not(.theme-light) .project-row.selected {
      border-color: var(--dashboard-selected-border-dark);
      background:
        linear-gradient(105deg, var(--dashboard-selected-green-start-dark) 0%, var(--dashboard-selected-green-mid-dark) 48%, rgba(39, 36, 32, 0.38) 100%),
        var(--dashboard-row-dark);
      box-shadow: 0 5px 0 rgba(49, 201, 62, 0.12), 0 0 0 1px rgba(80, 226, 92, 0.12);
    }
    body:not(.theme-light) .project-row .actions .icon-btn,
    body:not(.theme-light) .project-row .actions .select-btn,
    body:not(.theme-light) .project-row .actions .small-link,
    body:not(.theme-light) .project-row .actions .copy-script-btn,
    body:not(.theme-light) .project-row .actions .delete-output-btn,
    body:not(.theme-light) .project-row .project-rename-button,
    body:not(.theme-light) .project-row .project-duplicate-button,
    body:not(.theme-light) .dashboard-header .header-tools .header-nav-action,
    body:not(.theme-light) .render-machine .guide-header-btn,
    body:not(.theme-light) .render-machine .tab:not(.active),
    body:not(.theme-light) .render-machine .mode-toggle label:not(:has(input:checked)),
    body:not(.theme-light) .render-machine .file-picker,
    body:not(.theme-light) .render-machine .file-picker-button,
    body:not(.theme-light) .render-machine .start,
    body:not(.theme-light) .render-machine .stop-render-btn,
    body:not(.theme-light) .render-machine .render-primary-actions .small-link,
    body:not(.theme-light) .render-machine .reveal-output-btn,
    body:not(.theme-light) .render-machine .render-option-check,
    body:not(.theme-light) .render-machine .render-option-choose,
    body:not(.theme-light) .render-machine .speed-preset:not(.active) {
      border-color: rgba(255, 238, 218, 0.18);
      background: var(--dashboard-button-dark);
    }
    body:not(.theme-light) .render-machine .selected-box,
    body:not(.theme-light) .render-machine .tab.active,
    body:not(.theme-light) .render-machine .mode-toggle label:has(input:checked),
    body:not(.theme-light) .render-machine .speed-preset.active {
      border-color: var(--dashboard-selected-border-dark);
      background:
        linear-gradient(105deg, var(--dashboard-selected-green-start-dark) 0%, var(--dashboard-selected-green-mid-dark) 54%, rgba(45, 41, 36, 0.38) 100%),
        var(--dashboard-control-dark);
      box-shadow: 0 4px 0 rgba(49, 201, 62, 0.10), 0 0 0 1px rgba(80, 226, 92, 0.10);
    }
    body:not(.theme-light) .render-machine #renderStatus,
    body:not(.theme-light) .render-machine .render-state,
    body:not(.theme-light) .render-machine .advanced-settings {
      border-color: rgba(255, 238, 218, 0.16);
      background: var(--dashboard-control-dark);
    }
    body:not(.theme-light) .status-pill.ok {
      color: #eaffec;
      border-color: rgba(80, 226, 92, 0.48);
      background: rgba(73, 240, 73, 0.18);
    }
    body:not(.theme-light) .render-machine .guide-header-btn:hover,
    body:not(.theme-light) .render-machine .file-picker-button:hover,
    body:not(.theme-light) .render-machine .start:hover,
    body:not(.theme-light) .render-machine .reveal-output-btn:hover,
    body:not(.theme-light) .render-machine .render-option-choose:hover,
    body:not(.theme-light) .dashboard-header .header-tools .header-nav-action:hover {
      border-color: rgba(242, 178, 101, 0.42);
      background: rgba(61, 55, 48, 0.96);
    }
    [hidden] { display: none !important; }
    @container (max-width: 720px) {
      .list-head { display: none; }
      .project-row {
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: start;
      }
      .project-row .status-pill { justify-self: end; }
      .project-row .social-status-cell { justify-self: end; }
      .project-row .actions {
        grid-column: 1 / -1;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        justify-content: stretch;
      }
    }
    @container (max-width: 420px) {
      .project-row .actions {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
    }
    @media (max-width: 1060px) {
      body { height: auto; min-height: 100vh; overflow: auto; display: block; }
      .dashboard-header { align-items: flex-start; }
      .dashboard-shell { grid-template-columns: 1fr; min-height: auto; overflow: visible; }
      .render-machine { align-self: auto; height: auto; min-height: 0; position: static; max-height: none; overflow: visible; }
      .slide-list { height: auto; overflow: visible; }
    }
    @media (max-width: 720px) {
      body { padding: 20px 14px; }
      .dashboard-header { display: grid; }
      .header-tools { justify-content: flex-start; }
      .panel-head { align-items: flex-start; gap: 12px; }
      .project-sort-control { min-width: 0; }
      .project-sort-control select { min-width: 136px; }
      .update-slot-placeholder { display: none; }
      .render-guide-links { grid-template-columns: 1fr; }
      .list-head { display: none; }
      .project-row { grid-template-columns: 1fr; align-items: stretch; }
      .project-row .actions {
        grid-template-columns: repeat(5, minmax(0, 1fr));
        justify-content: stretch;
        overflow: visible;
        padding-bottom: 2px;
      }
      .project-row .actions .icon-btn,
      .project-row .actions .select-btn,
      .project-row .actions .small-link,
      .project-row .actions .delete-output-btn {
        padding: 8px 6px;
        font-size: 11px;
      }
    }
""",
        extra_script=f"""
  <script>
    window.__PROJECTS__ = {json.dumps(projects, ensure_ascii=False)};
    window.__INITIAL_PROJECT__ = {json.dumps(selected_project, ensure_ascii=False)};
    window.__PROJECT_SOURCE_ROOT__ = {json.dumps(str(PROJECT_ROOT), ensure_ascii=False)};
    const upgradePlanDialog = document.getElementById('upgradePlanDialog');
    const upgradePlanStatus = document.getElementById('upgradePlanStatus');
    function openUpgradePlanDialog() {{
      if (upgradePlanStatus) upgradePlanStatus.textContent = '';
      upgradePlanDialog?.showModal();
    }}
    document.getElementById('upgradePlanClose')?.addEventListener('click', () => upgradePlanDialog?.close());
    upgradePlanDialog?.addEventListener('click', (event) => {{ if (event.target === upgradePlanDialog) upgradePlanDialog.close(); }});
    document.getElementById('accountUpgradeButton')?.addEventListener('click', openUpgradePlanDialog);
    upgradePlanDialog?.querySelectorAll('[data-checkout-plan]').forEach((button) => button.addEventListener('click', async () => {{
      const plan = button.dataset.checkoutPlan === 'monthly' ? 'monthly' : 'yearly';
      const isVietnamese = document.documentElement.lang === 'vi';
      upgradePlanDialog.querySelectorAll('[data-checkout-plan]').forEach((item) => {{ item.disabled = true; }});
      if (upgradePlanStatus) upgradePlanStatus.textContent = isVietnamese ? 'Đang mở trang thanh toán…' : 'Opening checkout…';
      try {{
        const response = await fetch('/api/license/checkout', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ locale: isVietnamese ? 'vi' : 'en', plan }}),
        }});
        const payload = await response.json().catch(() => ({{}}));
        if (!response.ok) throw new Error(payload.error || `HTTP ${{response.status}}`);
        if (upgradePlanStatus) upgradePlanStatus.textContent = isVietnamese
          ? 'Đã mở trang thanh toán. AurexVideo sẽ tự cập nhật sau khi bạn thanh toán.'
          : 'Checkout opened. Aurex will update automatically after you pay.';
      }} catch (error) {{
        upgradePlanDialog.querySelectorAll('[data-checkout-plan]').forEach((item) => {{ item.disabled = false; }});
        if (upgradePlanStatus) upgradePlanStatus.textContent = error?.message || String(error);
      }}
    }}));
    if (new URLSearchParams(window.location.search).get('upgrade') === '1') openUpgradePlanDialog();
    async function openAurexVideoSettings(section = 'all') {{
      const theme = (
        document.body.classList.contains('theme-light')
        || localStorage.getItem('aurexvideo-theme') !== 'dark'
      ) ? 'light' : 'dark';
      const response = await fetch('/api/settings/open', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ section, theme }}),
      }});
      const payload = await response.json().catch(() => ({{}}));
      if (!response.ok) throw new Error(payload.error || `HTTP ${{response.status}}`);
    }}
    document.getElementById('openSettingsButton')?.addEventListener('click', () => {{
      openAurexVideoSettings('all').catch((error) => window.alert(error?.message || String(error)));
    }});
    let updateInstallRunning = false;
    document.getElementById('updateAvailableButton')?.addEventListener('click', async () => {{
      if (updateInstallRunning) return;
      const button = document.getElementById('updateAvailableButton');
      const label = button?.querySelector('span:last-child');
      if (!button) return;
      updateInstallRunning = true;
      button.disabled = true;
      if (label) label.textContent = 'Đang chuẩn bị…';
      try {{
        // One native bridge path: ack before stop_engine, then install on the Tauri
        // task. Do not invoke install from this engine-served page — on Windows that
        // IPC can die when the engine stops and leave only "Thử cập nhật lại".
        const response = await fetch('/api/app-update/install', {{ method: 'POST' }});
        const payload = await response.json().catch(() => ({{}}));
        if (!response.ok) throw new Error(payload.error || `HTTP ${{response.status}}`);
        if (!payload.available) {{
          button.hidden = true;
          document.getElementById('updateSlotPlaceholder')?.removeAttribute('hidden');
          button.disabled = false;
          updateInstallRunning = false;
          if (label) label.textContent = 'Cập nhật bản mới';
          return;
        }}
        if (label) label.textContent = payload.version ? `Đang cài ${{payload.version}}…` : 'Đang cài…';
        button.title = payload.version ? `AurexVideo ${{payload.version}}` : '';
      }} catch (error) {{
        button.disabled = false;
        updateInstallRunning = false;
        const message = error?.message || String(error);
        if (label) label.textContent = 'Thử cập nhật lại';
        button.title = message;
        button.dataset.updateError = message;
        window.alert(message);
      }}
    }});
    if (new URLSearchParams(window.location.search).get('preview-update') === '1') {{
      const previewUpdateButton = document.getElementById('updateAvailableButton');
      if (previewUpdateButton) {{
        previewUpdateButton.hidden = false;
        document.getElementById('updateSlotPlaceholder')?.setAttribute('hidden', '');
        previewUpdateButton.title = 'Xem thử vị trí thông báo cập nhật';
      }}
    }}
    let updateCheckRunning = false;
    async function checkForAurexVideoUpdate() {{
      if (updateCheckRunning || !navigator.onLine) return;
      updateCheckRunning = true;
      try {{
        const response = await fetch('/api/app-update/check', {{ method: 'POST' }});
        const payload = await response.json().catch(() => ({{}}));
        if (!response.ok || !payload.available) return;
        const button = document.getElementById('updateAvailableButton');
        if (!button) return;
        button.hidden = false;
        document.getElementById('updateSlotPlaceholder')?.setAttribute('hidden', '');
        button.title = payload.version ? `AurexVideo ${{payload.version}}` : '';
      }} catch (_) {{
        // Background checks stay silent; the user can retry from Settings.
      }} finally {{
        updateCheckRunning = false;
      }}
    }}
    window.setTimeout(checkForAurexVideoUpdate, 10000);
    window.setInterval(checkForAurexVideoUpdate, 6 * 60 * 60 * 1000);
    window.addEventListener('online', checkForAurexVideoUpdate);
  </script>
      <script src="/web/render_page.js?v=20260830-affiliate-v1"></script>
""",
    )


def render_upload_html(selected_project: str | None = None) -> bytes:
    projects = list_projects()
    project_names = {project["name"] for project in projects}
    output_projects = [project for project in projects if project["video_url"]]
    if selected_project not in project_names:
        selected_project = output_projects[0]["name"] if output_projects else (projects[0]["name"] if projects else "")

    options = "\n".join(
        f'<option value="{html.escape(project["name"])}" {"selected" if project["name"] == selected_project else ""}>'
        f'{html.escape(project["name"])}'
        "</option>"
        for project in projects
    )
    return render_page_shell(
        title="AurexVideo Upload Center",
        body=f"""
  <main class="upload-shell">
    <section class="upload-machine">
      <header class="top-upload-bar">
        <div class="brand-lockup upload-brand-lockup">
          <img src="/web/aurexvideo-logo.png" alt="Aurex" class="brand-mark upload-brand-mark" />
          <div>
            <h1>AurexVideo</h1>
          </div>
        </div>
        <div class="top-upload-actions">
          <button class="refresh-btn icon-btn header-nav-action" id="openDefaultTags" type="button">{ui_icon("pencil")}<span>Sửa caption</span></button>
          <a class="refresh-btn icon-btn header-nav-action" href="/">{ui_icon("arrow-left")}<span>Bảng điều khiển</span></a>
        </div>
      </header>

      <section class="upload-page-head">
        <div>
          <p class="kicker">Upload Center</p>
          <h2>Upload &amp; Publish</h2>
          <p>Chọn project và brand, viết caption một lần rồi đăng lên các social đã kết nối.</p>
        </div>
        <span class="ready-pill"><strong>{len(output_projects)}</strong><span>sẵn sàng</span></span>
      </section>

      <section class="upload-context-card" id="uploadContextCard">
        <div class="upload-context-card-head">
          <div>
            <strong>Ngữ cảnh đăng</strong>
            <span>Project quyết định Brand có thể chọn; Brand quyết định các social được dùng.</span>
          </div>
        </div>
        <div class="upload-context-grid" id="uploadContextCluster">
          <label class="field project-select-field field-project context-project-field">
            <span class="upload-context-label">Project đăng</span>
            <select id="projectSelect" aria-label="Project đăng" {"disabled" if not projects else ""}>
              {options}
            </select>
          </label>
          <div class="upload-brand-context-slot" id="uploadBrandContextSlot"></div>
        </div>
        <div class="upload-social-summary">
          <strong>Social đã kết nối</strong>
          <div class="upload-social-chips" id="uploadSocialChips"></div>
        </div>
      </section>

      <div class="status warn" id="renderStatus" hidden></div>

      <div class="upload-empty" id="uploadEmpty" hidden>
        <strong id="uploadEmptyProject">Project này chưa có final_video.mp4</strong>
        <span>Render project ở Dashboard trước, rồi quay lại Upload Center để đăng.</span>
        <a class="small-link" href="/">Về Dashboard</a>
      </div>

      <div class="upload-panel" id="uploadPanel" hidden>
        <div class="platform-grid">
          <section class="platform-card platform-youtube">
            <div class="platform-card-head">
              <div class="platform-title">{brand_icon("youtube")}<span>YouTube</span></div>
              <a class="small-link icon-btn platform-guide-link" href="/upload-guide/youtube">{ui_icon("help")}<span>Hướng dẫn YouTube</span></a>
            </div>
            <div class="platform-account-list" id="youtubeAccountList" hidden></div>
            <div class="field upload-field field-title">
              <span class="field-label field-label-between">
                <span class="field-label-main">{ui_icon("pencil", "field-icon")}<span>Tiêu đề YouTube</span></span>
                <button class="copy-field-btn" data-copy-target="uploadTitle" data-copy-label="Tiêu đề YouTube" type="button" aria-label="Copy Tiêu đề YouTube" title="Copy Tiêu đề YouTube">{ui_icon("copy")}</button>
              </span>
              <input id="uploadTitle" type="text" maxlength="100" placeholder="Tiêu đề YouTube" />
            </div>
            <div class="field upload-field field-description">
              <span class="field-label field-label-between">
                <span class="field-label-main">{ui_icon("list", "field-icon")}<span>Mô tả YouTube</span></span>
                <button class="copy-field-btn" data-copy-target="youtubeDescription" data-copy-label="Mô tả YouTube" type="button" aria-label="Copy Mô tả YouTube" title="Copy Mô tả YouTube">{ui_icon("copy")}</button>
              </span>
              <textarea id="youtubeDescription" rows="7" maxlength="5000" placeholder="Mô tả YouTube, có nguồn và hashtag"></textarea>
            </div>
            <label class="field upload-field compact field-youtube">
              <span class="field-label"><span>Quyền riêng tư YouTube</span></span>
              <select id="youtubePrivacy">
                <option value="private">Private - duyệt trước</option>
                <option value="unlisted">Unlisted - có link mới xem</option>
                <option value="public" selected>Public - đăng công khai</option>
              </select>
            </label>
            <label class="field upload-field compact schedule-field field-youtube">
              <span class="field-label schedule-toggle-label">
                <input type="checkbox" id="youtubeScheduleToggle" />
                <span>Hẹn giờ đăng</span>
              </span>
            </label>
            <div class="field upload-field compact schedule-row" id="youtubeScheduleRow" hidden>
              <input type="datetime-local" id="youtubeScheduleTime" />
              <p class="form-note">YouTube giữ video riêng tư đến đúng giờ hẹn rồi tự động công khai.</p>
            </div>
            <div class="platform-actions">
              <button class="upload-btn youtube secondary" id="openYoutubeConfig" type="button">{ui_icon("key")}<span>Khoá OAuth</span></button>
              <button class="upload-btn youtube" id="connectYoutube" type="button">{ui_icon("play")}<span>Thêm channel</span></button>
              <button class="upload-btn youtube" id="uploadYoutube" type="button">{ui_icon("upload")}<span>Upload YouTube</span></button>
            </div>
          </section>
          <section class="platform-card platform-facebook">
            <div class="platform-card-head">
              <div class="platform-title">{brand_icon("facebook")}<span>Facebook Reels</span></div>
              <a class="small-link icon-btn platform-guide-link" href="/upload-guide/facebook">{ui_icon("help")}<span>Hướng dẫn Facebook</span></a>
            </div>
            <div class="platform-account-list" id="facebookAccountList" hidden></div>
            <div class="field upload-field field-description">
              <span class="field-label field-label-between">
                <span class="field-label-main">{ui_icon("list", "field-icon")}<span>Caption Facebook</span></span>
                <button class="copy-field-btn" data-copy-target="facebookCaption" data-copy-label="Caption Facebook" type="button" aria-label="Copy Caption Facebook" title="Copy Caption Facebook">{ui_icon("copy")}</button>
              </span>
              <textarea id="facebookCaption" rows="7" maxlength="5000" placeholder="Caption Reels, không chứa link nguồn"></textarea>
            </div>
            <div class="field upload-field field-source-comment">
              <span class="field-label field-label-between">
                <span class="field-label-main">{ui_icon("arrow-down-right", "field-icon")}<span>Comment nguồn</span></span>
                <button class="copy-field-btn" data-copy-target="facebookSourceComment" data-copy-label="Comment nguồn" type="button" aria-label="Copy Comment nguồn" title="Copy Comment nguồn">{ui_icon("copy")}</button>
              </span>
              <input id="facebookSourceComment" type="text" maxlength="1000" placeholder="Nguồn: https://..." />
            </div>
            <label class="field upload-field compact field-facebook">
              <span class="field-label"><span>Trạng thái Reels</span></span>
              <select id="facebookVideoState">
                <option value="DRAFT">Draft - duyệt trước</option>
                <option value="PUBLISHED" selected>Publish now</option>
              </select>
            </label>
            <label class="field upload-field compact schedule-field field-facebook">
              <span class="field-label schedule-toggle-label">
                <input type="checkbox" id="facebookScheduleToggle" />
                <span>Hẹn giờ đăng</span>
              </span>
            </label>
            <div class="field upload-field compact schedule-row" id="facebookScheduleRow" hidden>
              <input type="datetime-local" id="facebookScheduleTime" />
              <p class="form-note">Facebook cho hẹn từ 10 phút đến 75 ngày trước. Reels tự đăng đúng giờ.</p>
            </div>
            <div class="platform-actions">
              <button class="upload-btn facebook secondary" id="openFacebookConfig" type="button">{ui_icon("plus")}<span>Thêm Page</span></button>
              <button class="upload-btn facebook" id="uploadFacebook" type="button">{ui_icon("upload")}<span>Upload Facebook Reels</span></button>
            </div>
          </section>
          <section class="platform-card platform-tiktok">
            <div class="platform-card-head"><div class="platform-title"><span>♪</span><span>TikTok · Zernio</span></div><button class="small-link icon-btn" id="openTiktokConfig" type="button">{ui_icon("key")}<span>Cấu hình Zernio</span></button></div>
            <div class="field upload-field field-description"><span class="field-label field-label-between"><span class="field-label-main">{ui_icon("list", "field-icon")}<span>Caption TikTok</span></span><button class="copy-field-btn" data-copy-target="tiktokCaption" type="button" aria-label="Copy Caption TikTok" title="Copy Caption TikTok">{ui_icon("copy")}</button></span><textarea id="tiktokCaption" rows="7" maxlength="2200" placeholder="Caption TikTok, tối đa 2.200 ký tự"></textarea></div>
            <label class="field upload-field compact schedule-field"><span class="field-label schedule-toggle-label"><input type="checkbox" id="tiktokScheduleToggle" /><span>Hẹn giờ đăng</span></span></label>
            <div class="field upload-field compact schedule-row" id="tiktokScheduleRow" hidden><input type="datetime-local" id="tiktokScheduleTime" /><p class="form-note">Zernio giữ lịch đăng; VPS theo dõi trạng thái và retry lỗi tạm thời sau 5 phút. Không cần giữ app mở sau khi tạo lịch.</p></div>
            <p class="form-note platform-config-note" id="tiktokConfigState">Cần cấu hình Zernio API key và TikTok account ID.</p>
            <div class="platform-actions"><button class="upload-btn tiktok" id="uploadTiktok" type="button">{ui_icon("upload")}<span>Đăng TikTok</span></button></div>
          </section>
          <section class="platform-card platform-instagram">
            <div class="platform-card-head">
              <div class="platform-title">{brand_icon("instagram")}<span>Instagram Reels</span></div>
              <a class="small-link icon-btn platform-guide-link" href="/upload-guide/instagram">{ui_icon("help")}<span>Hướng dẫn Instagram</span></a>
            </div>
            <div class="field upload-field field-description">
              <span class="field-label field-label-between">
                <span class="field-label-main">{ui_icon("list", "field-icon")}<span>Caption Instagram</span></span>
                <button class="copy-field-btn" data-copy-target="instagramCaption" data-copy-label="Caption Instagram" type="button" aria-label="Copy Caption Instagram" title="Copy Caption Instagram">{ui_icon("copy")}</button>
              </span>
              <textarea id="instagramCaption" rows="7" maxlength="2200" placeholder="Caption Instagram, tối đa 2.200 ký tự"></textarea>
            </div>
            <label class="field upload-field compact schedule-field"><span class="field-label schedule-toggle-label"><input type="checkbox" id="instagramScheduleToggle" /><span>Hẹn giờ đăng qua VPS</span></span></label>
            <div class="field upload-field compact schedule-row" id="instagramScheduleRow" hidden><input type="datetime-local" id="instagramScheduleTime" /><p class="form-note">Video sẽ được copy lên VPS và đăng đúng giờ, không cần giữ app mở.</p></div>
            <p class="form-note platform-config-note" id="instagramConfigState">Cần cấu hình Instagram API và Cloudflare R2.</p>
            <div class="platform-actions">
              <button class="upload-btn instagram secondary" id="openInstagramConfig" type="button">{ui_icon("key")}<span>Cấu hình Instagram + R2</span></button>
              <button class="upload-btn instagram" id="uploadInstagram" type="button">{ui_icon("upload")}<span>Upload Instagram Reels</span></button>
            </div>
          </section>
          <section class="platform-card platform-threads">
            <div class="platform-card-head">
              <div class="platform-title">{brand_icon("threads")}<span>Threads</span></div>
              <a class="small-link icon-btn platform-guide-link" href="https://developers.facebook.com/docs/threads/" target="_blank" rel="noreferrer">{ui_icon("help")}<span>Hướng dẫn Threads</span></a>
            </div>
            <div class="field upload-field field-description">
              <span class="field-label field-label-between">
                <span class="field-label-main">{ui_icon("list", "field-icon")}<span>Nội dung Threads</span></span>
                <button class="copy-field-btn" data-copy-target="threadsText" data-copy-label="Nội dung Threads" type="button" aria-label="Copy Nội dung Threads" title="Copy Nội dung Threads">{ui_icon("copy")}</button>
              </span>
              <textarea id="threadsText" rows="7" maxlength="500" placeholder="Nội dung Threads, tối đa 500 ký tự"></textarea>
            </div>
            <label class="field upload-field compact schedule-field"><span class="field-label schedule-toggle-label"><input type="checkbox" id="threadsScheduleToggle" /><span>Hẹn giờ đăng qua VPS</span></span></label>
            <div class="field upload-field compact schedule-row" id="threadsScheduleRow" hidden><input type="datetime-local" id="threadsScheduleTime" /><p class="form-note">Video sẽ được copy lên VPS và đăng đúng giờ, không cần giữ app mở.</p></div>
            <p class="form-note platform-config-note" id="threadsConfigState">Cần cấu hình Threads API.</p>
            <div class="platform-actions">
              <button class="upload-btn threads secondary" id="openThreadsConfig" type="button">{ui_icon("key")}<span>Cấu hình Threads</span></button>
              <button class="upload-btn threads" id="uploadThreads" type="button">{ui_icon("upload")}<span>Upload Threads</span></button>
            </div>
          </section>
          <section class="platform-card platform-binance">
            <div class="platform-card-head">
              <div class="platform-title">{brand_icon("binance")}<span>Binance Square</span></div>
            </div>
            <div class="field upload-field field-description">
              <span class="field-label field-label-between">
                <span class="field-label-main">{ui_icon("list", "field-icon")}<span>Caption Binance</span></span>
                <button class="copy-field-btn" data-copy-target="binanceCaption" data-copy-label="Caption Binance" type="button" aria-label="Copy Caption Binance" title="Copy Caption Binance">{ui_icon("copy")}</button>
              </span>
              <textarea id="binanceCaption" rows="6" maxlength="5000" placeholder="Nội dung tự nhập, giống Facebook Caption"></textarea>
            </div>
            <label class="field upload-field compact field-binance" hidden>
              <span class="field-label"><span>Thời lượng video (giây)</span></span>
              <input id="binanceDuration" type="number" min="0.1" step="0.1" value="10" />
            </label>
            <div class="platform-actions">
              <button class="upload-btn binance secondary" id="openBinanceConfig" type="button">{ui_icon("key")}<span>Cấu hình OpenAPI key</span></button>
              <button class="upload-btn binance" id="uploadBinance" type="button">{ui_icon("upload")}<span>Đăng Binance</span></button>
            </div>
          </section>
        </div>
        <div class="final-upload-actions">
          <button class="upload-btn both" id="uploadBothPublic" type="button" disabled>{ui_icon("check")}<span>Upload Facebook + YouTube + comment nguồn</span></button>
          <button class="upload-btn meta-all" id="uploadMetaAll" type="button" disabled>{ui_icon("check")}<span>Đăng Instagram + Facebook + Threads</span></button>
        </div>
        <div class="upload-status" id="uploadStatus" hidden></div>
        <div class="upload-result" id="uploadResult" hidden></div>
      </div>
      <div class="modal-backdrop" id="youtubeConfigModal" hidden>
        <div class="modal-card facebook-config-modal youtube-config-modal" role="dialog" aria-modal="true" aria-labelledby="youtubeConfigTitle">
          <button class="modal-close" id="closeYoutubeConfig" type="button" aria-label="Đóng">×</button>
          <p class="kicker">Google OAuth</p>
          <h3 id="youtubeConfigTitle">Nhập khóa YouTube</h3>
          <p class="modal-copy">Dán OAuth Client ID và Client Secret lấy từ Google Cloud. Thông tin được lưu trong ứng dụng và không hiển thị lại.</p>
          <div class="field upload-field compact facebook-config-field youtube-config-field">
            <span class="field-label"><span class="field-icon">ID</span><span>OAuth Client ID</span></span>
            <input id="youtubeClientId" type="text" autocomplete="off" placeholder="Dán OAuth Client ID" />
          </div>
          <div class="field upload-field compact facebook-config-field youtube-config-field">
            <span class="field-label">{ui_icon("key", "field-icon")}<span>Client Secret</span></span>
            <input id="youtubeClientSecret" type="password" autocomplete="off" placeholder="Dán Client Secret" />
          </div>
          <div class="field upload-field compact facebook-config-field youtube-config-field">
            <span class="field-label"><span class="field-icon">↪</span><span>Redirect URI</span></span>
            <input id="youtubeRedirectUri" type="text" autocomplete="off" placeholder="Aurex tự điền đúng port đang chạy" />
          </div>
          <div class="modal-actions">
            <button class="upload-btn secondary" id="cancelYoutubeConfig" type="button">{ui_icon("x")}<span>Huỷ</span></button>
            <button class="upload-btn youtube" id="saveYoutubeConfig" type="button">{ui_icon("check")}<span>Lưu và kết nối</span></button>
          </div>
        </div>
      </div>
      <div class="modal-backdrop" id="defaultTagsModal" hidden>
        <div class="modal-card default-tags-modal" role="dialog" aria-modal="true" aria-labelledby="defaultTagsTitle">
          <button class="modal-close" id="closeDefaultTags" type="button" aria-label="Đóng">×</button>
          <p class="kicker">Upload Center</p>
          <h3 id="defaultTagsTitle">Caption mặc định</h3>
          <p class="modal-copy">Dòng đầu dùng làm tiêu đề YouTube. Toàn bộ caption dùng cho Facebook và mô tả YouTube; các #tag cũng được gửi vào tag YouTube.</p>
          <label class="field upload-field field-description">
            <span class="field-label">{ui_icon("pencil", "field-icon")}<span>Caption mặc định</span></span>
            <textarea id="defaultCaptionInput" rows="9" maxlength="5000" spellcheck="false" placeholder="🎬 Tiêu đề video&#10;#bietchichonhieu #sosanh #kienthuc"></textarea>
          </label>
          <div class="modal-actions">
            <button class="upload-btn secondary" id="cancelDefaultTags" type="button">{ui_icon("x")}<span>Huỷ</span></button>
            <button class="upload-btn both" id="saveDefaultTags" type="button">{ui_icon("check")}<span>Lưu caption mặc định</span></button>
          </div>
        </div>
      </div>
      <div class="modal-backdrop" id="facebookConfigModal" hidden>
        <div class="modal-card facebook-config-modal" role="dialog" aria-modal="true" aria-labelledby="facebookConfigTitle">
          <button class="modal-close" id="closeFacebookConfig" type="button" aria-label="Đóng">×</button>
          <p class="kicker">Facebook Page</p>
          <h3 id="facebookConfigTitle">Thêm Page</h3>
          <p class="modal-copy">Dán Page ID và Page access token đã extend. Thông tin được lưu trong ứng dụng và không hiển thị lại.</p>
          <div class="field upload-field compact facebook-config-field">
            <span class="field-label"><span class="field-icon">ID</span><span>Facebook Page ID</span></span>
            <input id="facebookPageId" type="text" inputmode="numeric" autocomplete="off" placeholder="Dán Page ID" />
          </div>
          <div class="field upload-field compact facebook-config-field">
            <span class="field-label">{ui_icon("key", "field-icon")}<span>Page access token</span></span>
            <input id="facebookPageAccessToken" type="password" autocomplete="off" placeholder="Dán Page access token" />
          </div>
          <div class="modal-actions">
            <button class="upload-btn secondary" id="cancelFacebookConfig" type="button">{ui_icon("x")}<span>Huỷ</span></button>
            <button class="upload-btn facebook" id="saveFacebookConfig" type="button">{ui_icon("check")}<span>Lưu Page</span></button>
          </div>
        </div>
      </div>
      <div class="modal-backdrop" id="instagramConfigModal" hidden>
        <div class="modal-card facebook-config-modal instagram-config-modal" role="dialog" aria-modal="true" aria-labelledby="instagramConfigTitle">
          <button class="modal-close" id="closeInstagramConfig" type="button" aria-label="Đóng">×</button>
          <p class="kicker">Instagram API + R2 chung</p>
          <h3 id="instagramConfigTitle">Cấu hình Instagram Reels</h3>
          <p class="modal-copy" id="instagramConfigScope" hidden></p>
          <p class="modal-copy" id="instagramConfigDescription">Instagram sẽ kéo video từ public HTTPS URL trên R2 chung. Access token và Secret Key được lưu trong file config local với quyền hạn chế.</p>
          <div class="instagram-config-grid">
            <div class="field upload-field compact instagram-config-field">
              <span class="field-label"><span class="field-icon">ID</span><span>Instagram IG User ID</span></span>
              <input id="instagramIgUserId" type="text" inputmode="numeric" autocomplete="off" placeholder="Ví dụ: 1784..." />
            </div>
            <div class="field upload-field compact instagram-config-field">
              <span class="field-label"><span>Tên account</span></span>
              <input id="instagramDisplayName" type="text" autocomplete="off" maxlength="160" placeholder="Ví dụ: Popsy Instagram" />
            </div>
            <div class="field upload-field compact instagram-config-field">
              <span class="field-label">{ui_icon("key", "field-icon")}<span>Instagram access token</span></span>
              <input id="instagramAccessToken" type="password" autocomplete="off" placeholder="Dán token dài hạn" />
            </div>
            <label class="field upload-field compact instagram-config-field">
              <span class="field-label"><span>API login mode</span></span>
              <select id="instagramApiMode"><option value="instagram_login">Instagram Login</option><option value="facebook_login">Facebook Login / Page token</option></select>
            </label>
            <div class="field upload-field compact instagram-config-field">
              <span class="field-label"><span>Graph API version</span></span>
              <input id="instagramGraphVersion" type="text" autocomplete="off" value="v25.0" placeholder="v25.0" />
            </div>
            <div class="instagram-r2-shared-note instagram-config-wide" id="instagramR2SharedNote" hidden>
              <span>R2 dùng chung cho toàn bộ Brand.</span>
              <small id="instagramR2SharedState">Mỗi account Instagram chỉ cần nhập thông tin Instagram; video sẽ dùng kho R2 chung.</small>
              <button class="small-link" id="openSharedR2Config" type="button">Cấu hình R2 chung</button>
            </div>
            <div class="instagram-r2-config-fields instagram-config-wide" id="instagramR2ConfigFields">
              <div class="field upload-field compact instagram-config-field">
                <span class="field-label"><span class="field-icon">R2</span><span>R2 Account ID</span></span>
                <input id="r2AccountId" type="text" autocomplete="off" placeholder="Cloudflare Account ID" />
              </div>
              <div class="field upload-field compact instagram-config-field">
                <span class="field-label"><span>R2 bucket</span></span>
                <input id="r2Bucket" type="text" autocomplete="off" placeholder="instagram-media" />
              </div>
              <div class="field upload-field compact instagram-config-field">
                <span class="field-label"><span>R2 Access Key ID</span></span>
                <input id="r2AccessKeyId" type="text" autocomplete="off" placeholder="Access Key ID" />
              </div>
              <div class="field upload-field compact instagram-config-field">
                <span class="field-label">{ui_icon("key", "field-icon")}<span>R2 Secret Access Key</span></span>
                <input id="r2SecretAccessKey" type="password" autocomplete="off" placeholder="Secret Access Key" />
              </div>
              <div class="field upload-field compact instagram-config-field instagram-config-wide">
                <span class="field-label"><span>R2 public base URL</span></span>
                <input id="r2PublicBaseUrl" type="url" autocomplete="off" placeholder="https://media.example.com" />
              </div>
              <div class="field upload-field compact instagram-config-field">
                <span class="field-label"><span>Object prefix</span></span>
                <input id="r2ObjectPrefix" type="text" autocomplete="off" value="instagram" placeholder="instagram" />
              </div>
              <label class="field upload-field compact instagram-config-field instagram-retain-field">
                <span class="field-label"><span>Giữ file trên R2 sau khi đăng</span></span>
                <input id="r2RetainMedia" type="checkbox" />
              </label>
            </div>
          </div>
          <div class="modal-actions">
            <button class="upload-btn secondary" id="cancelInstagramConfig" type="button">{ui_icon("x")}<span>Huỷ</span></button>
            <button class="upload-btn instagram" id="saveInstagramConfig" type="button">{ui_icon("check")}<span>Lưu Instagram + R2</span></button>
          </div>
        </div>
      </div>
      <div class="modal-backdrop" id="threadsConfigModal" hidden>
        <div class="modal-card facebook-config-modal threads-config-modal" role="dialog" aria-modal="true" aria-labelledby="threadsConfigTitle">
          <button class="modal-close" id="closeThreadsConfig" type="button" aria-label="Đóng">×</button>
          <p class="kicker">Threads API</p>
          <h3 id="threadsConfigTitle">Cấu hình Threads</h3>
          <p class="modal-copy" id="threadsConfigScope" hidden></p>
          <p class="modal-copy">Threads cần token riêng có quyền <code>threads_basic</code> và <code>threads_content_publish</code>. Token được lưu local và không hiển thị lại.</p>
          <div class="field upload-field compact threads-config-field">
            <span class="field-label"><span class="field-icon">ID</span><span>Threads User ID</span></span>
            <input id="threadsUserId" type="text" inputmode="numeric" autocomplete="off" placeholder="Dán Threads User ID" />
          </div>
          <div class="field upload-field compact threads-config-field">
            <span class="field-label"><span>Tên account</span></span>
            <input id="threadsDisplayName" type="text" autocomplete="off" maxlength="160" placeholder="Ví dụ: Popsy Threads" />
          </div>
          <div class="field upload-field compact threads-config-field">
            <span class="field-label">{ui_icon("key", "field-icon")}<span>Threads access token</span></span>
            <input id="threadsAccessToken" type="password" autocomplete="off" placeholder="Dán Threads User Access Token" />
          </div>
          <div class="field upload-field compact threads-config-field">
            <span class="field-label"><span>API version</span></span>
            <input id="threadsGraphVersion" type="text" autocomplete="off" value="v1.0" placeholder="v1.0" />
          </div>
          <div class="modal-actions">
            <button class="upload-btn secondary" id="cancelThreadsConfig" type="button">{ui_icon("x")}<span>Huỷ</span></button>
            <button class="upload-btn threads" id="saveThreadsConfig" type="button">{ui_icon("check")}<span>Lưu Threads</span></button>
          </div>
        </div>
      </div>
      <div class="modal-backdrop" id="tiktokConfigModal" hidden>
        <div class="modal-card facebook-config-modal tiktok-config-modal" role="dialog" aria-modal="true" aria-labelledby="tiktokConfigTitle">
          <button class="modal-close" id="closeTiktokConfig" type="button" aria-label="Đóng">×</button>
          <p class="kicker">TikTok · Zernio</p>
          <h3 id="tiktokConfigTitle">Cấu hình TikTok</h3>
          <p class="modal-copy" id="tiktokConfigScope" hidden></p>
          <p class="modal-copy">Nhập Zernio API key và TikTok account ID. Khi thêm từ Brand Social Center, account này chỉ được lưu và sử dụng cho Brand đang chọn.</p>
          <div class="field upload-field compact tiktok-config-field">
            <span class="field-label"><span>Tên account</span></span>
            <input id="tiktokDisplayName" type="text" autocomplete="off" maxlength="160" placeholder="Ví dụ: Popsy TikTok" />
          </div>
          <div class="field upload-field compact tiktok-config-field">
            <span class="field-label">{ui_icon("key", "field-icon")}<span>Zernio API key</span></span>
            <input id="tiktokApiKey" type="password" autocomplete="off" placeholder="Dán Zernio API key" />
          </div>
          <div class="field upload-field compact tiktok-config-field">
            <span class="field-label"><span class="field-icon">ID</span><span>TikTok account ID trong Zernio</span></span>
            <input id="tiktokAccountId" type="text" autocomplete="off" placeholder="Dán TikTok account ID" />
          </div>
          <div class="modal-actions">
            <button class="upload-btn secondary" id="cancelTiktokConfig" type="button">{ui_icon("x")}<span>Huỷ</span></button>
            <button class="upload-btn tiktok" id="saveTiktokConfig" type="button">{ui_icon("check")}<span>Lưu TikTok</span></button>
          </div>
        </div>
      </div>
      <div class="modal-backdrop" id="binanceConfigModal" hidden>
            <div class="modal-card binance-config-modal" role="dialog" aria-modal="true" aria-labelledby="binanceConfigTitle">
              <button class="modal-close" id="closeBinanceConfig" type="button" aria-label="Đóng">×</button>
              <p class="kicker">Binance Square</p>
              <h3 id="binanceConfigTitle">Cấu hình OpenAPI key</h3>
              <p class="modal-copy">Dán OpenAPI key lấy từ Binance Square OpenAPI. Thông tin được lưu trong ứng dụng và không hiển thị lại.</p>
              <div class="field upload-field compact binance-config-field">
                <span class="field-label">{ui_icon("key", "field-icon")}<span>OpenAPI key</span></span>
                <input id="binanceApiKey" type="password" autocomplete="off" placeholder="Dán OpenAPI key Binance Square" />
              </div>
              <div class="modal-actions">
                <button class="upload-btn secondary" id="disconnectBinance" type="button">{ui_icon("x")}<span>Gỡ cấu hình</span></button>
                <button class="upload-btn binance" id="saveBinanceConfig" type="button">{ui_icon("check")}<span>Lưu cấu hình</span></button>
              </div>
            </div>
          </div>
        </section>
      </main>
""",
        extra_style="""
    body {
      --upload-page-max: 1440px;
      padding: 24px clamp(24px, 3vw, 56px);
    }
    html.tauri-macos body {
      padding-top: 54px;
    }
    body:not(.theme-light) {
      --body-bg: #181818;
      --surface: transparent;
      --surface-strong: rgba(255, 255, 255, 0.06);
      --surface-panel: transparent;
      --field-bg: rgba(255, 255, 255, 0.04);
      --control-line: rgba(255, 255, 255, 0.14);
      --control-line-soft: rgba(255, 255, 255, 0.12);
      --text-faint: rgba(246, 255, 249, 0.72);
      --status-text: rgba(246, 255, 249, 0.84);
      --shadow: none;
    }
    .upload-header {
      display: grid;
      grid-template-columns: minmax(320px, 0.86fr) minmax(420px, 1fr);
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      width: min(100%, var(--upload-page-max));
      max-width: var(--upload-page-max);
      margin: 0 auto 22px;
    }
    .upload-title-block {
      display: flex;
      align-items: center;
      gap: 14px;
      min-height: 74px;
    }
    .upload-brand-mark {
      width: 58px;
      height: 58px;
      flex: 0 0 58px;
      border-radius: 16px;
      object-fit: cover;
      box-shadow: 0 14px 34px rgba(0, 0, 0, 0.24);
    }
    .upload-header h1 {
      margin: 0;
      font-size: clamp(36px, 4.4vw, 54px);
      line-height: 0.95;
      letter-spacing: -0.065em;
      white-space: nowrap;
    }
    .kicker { margin: 0 0 6px; color: var(--muted); font-size: 11px; font-weight: 900; letter-spacing: 0.16em; text-transform: uppercase; }
    .header-tools {
      display: grid;
      grid-template-columns: minmax(140px, auto);
      gap: 10px;
      justify-content: end;
      align-items: stretch;
      width: auto;
      justify-self: end;
    }
    .upload-header .refresh-btn {
      min-height: 42px;
      padding: 8px 12px;
      width: 100%;
      white-space: nowrap;
    }
    .upload-shell {
      width: min(100%, var(--upload-page-max));
      max-width: var(--upload-page-max);
      margin: 0 auto;
    }
    .upload-machine,
    .upload-panel,
    .platform-card {
      background: transparent !important;
      box-shadow: none !important;
    }
    .upload-panel,
    .platform-card {
      border: 1px solid var(--control-line-soft);
    }
    .upload-machine .field-icon {
      width: 18px !important;
      height: 18px !important;
      min-width: 18px;
      border-radius: 0 !important;
      color: var(--text-soft) !important;
      background: transparent !important;
      box-shadow: none !important;
      font-size: inherit !important;
    }
    .upload-machine .field-icon svg {
      width: 18px;
      height: 18px;
      display: block;
    }
    .upload-machine,
    .upload-machine .refresh-btn,
    .upload-machine .upload-btn,
    .upload-machine .small-link,
    .upload-machine .field-label,
    .upload-machine .platform-title {
      color: var(--text-soft);
    }
    .ready-pill {
      color: var(--text) !important;
      border: 1px solid rgba(34, 197, 94, 0.45) !important;
      background: rgba(34, 197, 94, 0.12) !important;
      box-shadow: none !important;
    }
    body.theme-light .ready-pill {
      color: #0b1220 !important;
      border-color: rgba(22, 163, 74, 0.45) !important;
      background: rgba(34, 197, 94, 0.08) !important;
    }
    .upload-btn {
      color: var(--text-soft) !important;
      background: rgba(242, 178, 101, 0.12) !important;
      border: 1px solid rgba(216, 132, 53, 0.42) !important;
      box-shadow: none !important;
    }
    .upload-btn.secondary {
      color: var(--text-soft) !important;
      background: rgba(79, 57, 31, 0.06) !important;
      border: 1px solid rgba(79, 57, 31, 0.18) !important;
    }
    .upload-btn.youtube {
      border-color: rgba(239, 68, 68, 0.42) !important;
      background: rgba(239, 68, 68, 0.10) !important;
    }
    .upload-btn.youtube.secondary {
      border-color: rgba(239, 68, 68, 0.36) !important;
      background: rgba(239, 68, 68, 0.08) !important;
    }
    .upload-btn.facebook {
      border-color: rgba(124, 58, 237, 0.40) !important;
      background: rgba(124, 58, 237, 0.10) !important;
    }
    .upload-btn.facebook.secondary {
      border-color: rgba(124, 58, 237, 0.34) !important;
      background: rgba(124, 58, 237, 0.08) !important;
    }
    .upload-btn.instagram {
      border-color: rgba(225, 48, 108, 0.46) !important;
      background: rgba(225, 48, 108, 0.12) !important;
    }
    .upload-btn.instagram.secondary {
      border-color: rgba(225, 48, 108, 0.36) !important;
      background: rgba(225, 48, 108, 0.08) !important;
    }
    .upload-btn.threads {
      border-color: rgba(17, 24, 39, 0.42) !important;
      background: rgba(17, 24, 39, 0.10) !important;
    }
    .upload-btn.threads.secondary {
      border-color: rgba(17, 24, 39, 0.30) !important;
      background: rgba(17, 24, 39, 0.06) !important;
    }
    .upload-btn.meta-all {
      border-color: rgba(17, 24, 39, 0.46) !important;
      background: linear-gradient(135deg, rgba(225, 48, 108, 0.14), rgba(17, 24, 39, 0.12), rgba(124, 58, 237, 0.14)) !important;
    }
    .upload-btn.both {
      border-color: rgba(242, 178, 101, 0.50) !important;
      background: rgba(242, 178, 101, 0.16) !important;
    }
    .modal-actions .upload-btn.secondary {
      color: var(--text-soft) !important;
      background: rgba(79, 57, 31, 0.08) !important;
      border: 1px solid rgba(79, 57, 31, 0.22) !important;
    }
    .modal-actions .upload-btn.youtube {
      border-color: rgba(239, 68, 68, 0.45) !important;
      background: rgba(239, 68, 68, 0.14) !important;
    }
    .modal-actions .upload-btn.facebook {
      border-color: rgba(124, 58, 237, 0.45) !important;
      background: rgba(124, 58, 237, 0.14) !important;
    }
    .modal-actions .upload-btn.instagram {
      border-color: rgba(225, 48, 108, 0.45) !important;
      background: rgba(225, 48, 108, 0.14) !important;
    }
    body.theme-light .modal-actions .upload-btn.secondary {
      color: #20170f !important;
      background: rgba(255, 255, 255, 0.72) !important;
      border: 1px solid rgba(79, 57, 31, 0.20) !important;
    }
    body.theme-light .modal-actions .upload-btn.youtube {
      color: #20170f !important;
      background: rgba(254, 226, 226, 0.92) !important;
    }
    body.theme-light .modal-actions .upload-btn.facebook {
      color: #20170f !important;
      background: rgba(237, 233, 254, 0.92) !important;
    }
    body:not(.theme-light) .small-link {
      border: 1px solid var(--control-line-soft) !important;
      background: transparent !important;
      color: var(--text-soft) !important;
    }

    .upload-machine { display: grid; gap: 18px; }
    .top-upload-bar {
      display: grid;
      grid-template-columns: minmax(260px, 1fr) auto;
      align-items: center;
      gap: 22px;
      width: 100%;
      margin: 0 0 8px;
      padding: 0;
      border: 0;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }
    .upload-page-head {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 20px;
      margin: 4px 0 0;
      padding: 0 2px;
    }
    .upload-page-head h2 {
      margin: 0 0 7px;
      color: var(--text);
      font-size: clamp(30px, 3.6vw, 46px);
      line-height: 0.98;
      letter-spacing: -0.06em;
    }
    .upload-page-head p:not(.kicker) {
      max-width: 760px;
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.5;
      font-weight: 700;
    }
    .upload-page-head .kicker {
      margin: 0 0 7px;
    }
    .upload-page-head .ready-pill {
      flex: 0 0 auto;
      min-height: 42px;
    }
    .upload-context-card {
      display: grid;
      gap: 16px;
      border: 1px solid var(--control-line);
      border-radius: 20px;
      padding: 18px 20px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }
    body.theme-light .upload-context-card {
      background: rgba(255, 251, 244, 0.64);
      box-shadow: 0 14px 34px rgba(95, 61, 31, 0.06);
    }
    .upload-context-card-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }
    .upload-context-card-head strong {
      display: block;
      color: var(--text);
      font-size: 15px;
      font-weight: 950;
      letter-spacing: -0.02em;
    }
    .upload-context-card-head span {
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
      font-weight: 700;
    }
    .upload-context-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 14px;
      align-items: stretch;
    }
    .upload-context-grid > .field,
    .upload-brand-context-slot {
      min-width: 0;
    }
    .context-project-field,
    .upload-context-card .upload-brand-context {
      display: grid;
      align-content: start;
      gap: 7px;
      width: 100%;
      min-width: 0;
      margin: 0;
      padding: 0;
      border: 0;
      background: transparent;
    }
    .context-project-field select,
    .upload-context-card .upload-brand-context select {
      min-height: 46px;
      width: 100%;
      border: 1px solid var(--control-line);
      border-radius: 11px;
      padding: 11px 13px;
      color: var(--text);
      background: var(--field-bg);
      font: inherit;
      font-size: 14px;
      font-weight: 850;
    }
    .upload-context-card .upload-brand-context > span,
    .upload-context-card .upload-brand-context small,
    .upload-context-card .upload-brand-context button {
      grid-column: 1;
    }
    .upload-context-card .upload-brand-context > span {
      margin: 0;
      color: var(--muted);
      font-size: 10px;
      font-weight: 950;
      letter-spacing: 0.14em;
      line-height: 1.15;
      text-transform: uppercase;
    }
    .upload-context-card .upload-brand-context small {
      color: var(--muted);
      font-size: 10px;
      font-weight: 750;
      line-height: 1.25;
    }
    .upload-context-card .upload-brand-context button {
      justify-self: start;
      min-height: 24px;
      padding: 0;
      border: 0;
      color: var(--accent);
      background: transparent;
      font: inherit;
      font-size: 11px;
      font-weight: 950;
    }
    .upload-social-summary {
      display: flex;
      align-items: flex-start;
      gap: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--control-line-soft);
    }
    .upload-social-summary > strong {
      min-width: 132px;
      padding-top: 7px;
      color: var(--text-soft);
      font-size: 12px;
      font-weight: 900;
    }
    .upload-social-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 9px;
      min-width: 0;
    }
    .upload-social-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 38px;
      border: 1px solid var(--control-line-soft);
      border-radius: 10px;
      padding: 7px 10px;
      color: var(--text-soft);
      background: var(--surface);
      font-size: 11px;
      font-weight: 850;
    }
    .upload-social-chip.is-off { opacity: 0.52; }
    .upload-social-chip.is-pending { border-color: rgba(243, 107, 33, 0.30); }
    .upload-social-chip-icon {
      display: grid;
      place-items: center;
      width: 22px;
      height: 22px;
      border-radius: 7px;
      color: var(--text);
      background: var(--surface-strong);
      font-size: 10px;
      font-weight: 950;
    }
    .upload-social-chip-copy {
      display: grid;
      min-width: 0;
      gap: 2px;
    }
    .upload-social-chip-copy strong {
      overflow: hidden;
      color: var(--text-soft);
      font-size: 11px;
      font-weight: 900;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .upload-social-chip-copy small {
      overflow: hidden;
      color: var(--muted);
      font-size: 10px;
      font-weight: 700;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .upload-social-chip-dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #22a35a;
    }
    .upload-social-chip.is-pending .upload-social-chip-dot { background: var(--accent); }
    .upload-social-chip.is-off .upload-social-chip-dot { background: var(--muted); }
    .upload-social-chip-status {
      color: var(--muted);
      font-size: 10px;
      font-weight: 750;
    }
    body.theme-light .top-upload-bar,
    body:not(.theme-light) .top-upload-bar {
      border: 0;
      background: transparent;
      box-shadow: none;
    }
    .upload-brand-lockup {
      display: flex;
      align-items: center;
      gap: 14px;
      flex: 0 0 auto;
      min-width: 0;
    }
    .top-upload-bar .brand-mark,
    .top-upload-bar .upload-brand-mark {
      width: 58px;
      height: 58px;
      flex: 0 0 58px;
      border-radius: 16px;
      object-fit: cover;
      box-shadow: 0 14px 34px rgba(0, 0, 0, 0.24);
    }
    .top-upload-bar h1 {
      margin: 0;
      font-size: clamp(28px, 3.4vw, 40px);
      line-height: 1;
      letter-spacing: -0.05em;
      white-space: nowrap;
    }
    .upload-context-cluster {
      display: grid;
      grid-template-columns: minmax(220px, 0.95fr) minmax(260px, 1.05fr);
      align-items: stretch;
      gap: 12px;
      min-width: 0;
    }
    .upload-context-label {
      display: block;
      margin: 0 0 6px;
      color: var(--muted);
      font-size: 10px;
      font-weight: 950;
      letter-spacing: 0.14em;
      line-height: 1.15;
      text-transform: uppercase;
    }
    .top-project-field {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      align-content: center;
      width: 100%;
      min-width: 0;
      max-width: none;
      margin: 0;
      border: 1px solid var(--control-line);
      border-radius: 14px;
      padding: 9px 12px;
      background: var(--surface);
    }
    body:not(.theme-light) .top-project-field {
      border: 1px solid var(--control-line);
      padding: 9px 12px;
      background: var(--surface);
    }
    .top-project-field select {
      width: 100%;
      min-width: 0;
      max-width: 100%;
      box-sizing: border-box;
      min-height: 46px;
      padding-block: 10px;
      font-size: 15px;
      font-weight: 850;
    }
    .top-upload-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 10px;
      min-width: 0;
      flex-wrap: wrap;
      margin-left: 0;
    }
    .top-upload-actions .ready-pill {
      min-height: 40px;
      padding: 7px 11px;
    }
    .top-upload-actions .refresh-btn {
      min-height: 46px;
      padding: 9px 12px;
      white-space: nowrap;
    }
    .project-picker {
      display: grid;
      gap: 10px;
      border: 1px solid rgba(242, 178, 101, 0.16);
      border-radius: 22px;
      padding: 14px;
      background: var(--surface-panel);
      box-shadow: var(--shadow);
    }
    body:not(.theme-light) .project-picker {
      border-color: rgba(232, 160, 96, 0.42);
      background:
        radial-gradient(circle at 4% 0%, rgba(242, 178, 101, 0.18), transparent 34%),
        linear-gradient(150deg, rgba(28, 18, 10, 0.97), rgba(8, 5, 2, 0.98));
      box-shadow:
        0 0 0 1px rgba(242, 178, 101, 0.10),
        0 24px 80px rgba(0, 0, 0, 0.76);
    }
    .project-picker-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
    }
    h2 { margin: 0; font-size: 22px; letter-spacing: -0.05em; }
    .ready-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid rgba(34, 197, 94, 0.28);
      border-radius: 999px;
      padding: 7px 11px;
      color: var(--text);
      background: rgba(34, 197, 94, 0.12);
      font-size: 12px;
      font-weight: 950;
      white-space: nowrap;
      box-shadow: none;
    }
    .ready-pill strong { font-size: 16px; line-height: 1; }
    .ready-pill span { text-transform: uppercase; letter-spacing: 0.08em; }
    .field-project,
    .project-summary {
      --field-accent: #e8a060;
      --field-accent-2: #f2b261;
      --field-tint: rgba(242, 178, 101, 0.13);
      --field-border: rgba(232, 160, 96, 0.36);
      --field-icon-text: #2b1602;
    }
    .field-title {
      --field-accent: #f59e0b;
      --field-accent-2: #fde68a;
      --field-tint: rgba(245, 158, 11, 0.14);
      --field-border: rgba(245, 158, 11, 0.38);
      --field-icon-text: #2b1602;
    }
    .field-description {
      --field-accent: #06b6d4;
      --field-accent-2: #67e8f9;
      --field-tint: rgba(6, 182, 212, 0.13);
      --field-border: rgba(6, 182, 212, 0.36);
      --field-icon-text: #031f26;
    }
    .field-youtube {
      --field-accent: #ef4444;
      --field-accent-2: #fca5a5;
      --field-tint: rgba(239, 68, 68, 0.13);
      --field-border: rgba(239, 68, 68, 0.40);
      --field-icon-text: #300808;
    }
    .field-facebook {
      --field-accent: #7c3aed;
      --field-accent-2: #c4b5fd;
      --field-tint: rgba(124, 58, 237, 0.13);
      --field-border: rgba(124, 58, 237, 0.40);
      --field-icon-text: #f5f3ff;
    }
    .field-source-comment {
      --field-accent: #7c3aed;
      --field-accent-2: #c4b5fd;
      --field-tint: rgba(124, 58, 237, 0.10);
      --field-border: rgba(124, 58, 237, 0.32);
      --field-icon-text: #f5f3ff;
      max-width: 520px;
    }
    .field { display: grid; gap: 7px; margin: 0; }
    .field > span { color: var(--muted); font-size: 12px; font-weight: 900; }
    .field-label,
    .summary-label {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      justify-self: start;
      color: var(--field-accent-2, var(--muted));
    }
    .field-label-between {
      width: 100%;
      justify-content: space-between;
      gap: 12px;
    }
    .field-label-main {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }
    .project-field-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .project-video-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      padding: 7px 11px;
      font-size: 11px;
      font-weight: 950;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .project-video-badge.ready {
      color: #052e16;
      border: 1px solid rgba(34, 197, 94, 0.36);
      background: linear-gradient(135deg, #86efac, #22c55e);
      box-shadow: 0 12px 28px rgba(34, 197, 94, 0.24);
    }
    .project-video-badge.missing {
      color: #32160a;
      border: 1px solid rgba(251, 146, 60, 0.36);
      background: linear-gradient(135deg, #fed7aa, #fb923c);
      box-shadow: 0 12px 28px rgba(251, 146, 60, 0.20);
    }
    .field-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 24px;
      height: 24px;
      border-radius: 9px;
      color: var(--field-icon-text, #170d05);
      background: linear-gradient(135deg, var(--field-accent-2, #fde68a), var(--field-accent, #f2b261));
      font-size: 10px;
      font-weight: 950;
      letter-spacing: -0.03em;
      line-height: 1;
      box-shadow: 0 10px 24px color-mix(in srgb, var(--field-accent, #f2b261) 26%, transparent);
    }
    .field input,
    .field select,
    .field textarea {
      width: 100%;
      min-height: 46px;
      border: 1px solid var(--control-line);
      border-radius: 15px;
      padding: 11px 13px;
      color: var(--text);
      background: var(--field-bg);
      font-family: inherit;
      line-height: 1.45;
    }
    body:not(.theme-light) .field input,
    body:not(.theme-light) .field select,
    body:not(.theme-light) .field textarea {
      border-color: rgba(255, 255, 255, 0.22);
      background: rgba(0, 0, 0, 0.66);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
    }
    body:not(.theme-light) .field-project select,
    body:not(.theme-light) .field-title input,
    body:not(.theme-light) .field-description textarea,
    body:not(.theme-light) .field-source-comment input,
    body:not(.theme-light) .field-youtube select,
    body:not(.theme-light) .field-facebook select {
      border-color: var(--field-border);
      background:
        linear-gradient(135deg, var(--field-tint), rgba(255, 255, 255, 0.025)),
        rgba(0, 0, 0, 0.68);
    }
    body:not(.theme-light) .field input:focus,
    body:not(.theme-light) .field select:focus,
    body:not(.theme-light) .field textarea:focus {
      outline: none;
      border-color: rgba(242, 178, 101, 0.58);
      box-shadow:
        0 0 0 3px rgba(242, 178, 101, 0.14),
        inset 0 1px 0 rgba(255, 255, 255, 0.05);
    }
    .platform-card .upload-field.compact.field-youtube,
    .platform-card .upload-field.compact.field-facebook {
      width: min(100%, 260px);
      max-width: 260px;
    }
    .project-select-field select {
      min-height: 48px;
      border-color: rgba(242, 178, 101, 0.22);
      border-radius: 16px;
      background:
        linear-gradient(135deg, rgba(242, 178, 101, 0.08), var(--surface)),
        var(--field-bg);
      font-size: 15px;
      font-weight: 850;
    }
    body:not(.theme-light) .project-select-field select {
      border-color: rgba(232, 160, 96, 0.38);
      background:
        linear-gradient(135deg, rgba(242, 178, 101, 0.14), rgba(255, 255, 255, 0.035)),
        rgba(0, 0, 0, 0.70);
    }
    .top-project-field select,
    body:not(.theme-light) .top-project-field select {
      min-height: 34px;
      border: 0;
      border-radius: 0;
      padding: 0 24px 0 0;
      background: transparent;
      box-shadow: none;
    }
    .top-project-field select:focus,
    body:not(.theme-light) .top-project-field select:focus {
      outline: none;
      border: 0;
      box-shadow: none;
    }
    body:not(.theme-light) .project-select-field:not(.top-project-field) {
      border: 1px solid var(--field-border);
      border-radius: 18px;
      padding: 10px;
      background:
        linear-gradient(135deg, var(--field-tint), rgba(255, 255, 255, 0.025)),
        rgba(0, 0, 0, 0.24);
    }
    .field textarea { resize: vertical; min-height: 150px; }
    .field-source-comment input {
      min-height: 44px;
      padding-block: 9px;
      font-size: 14px;
    }
    .copy-field-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 auto;
      width: 32px;
      height: 32px;
      border: 1px solid var(--field-border);
      border-radius: 11px;
      padding: 0;
      color: var(--field-accent-2);
      background:
        linear-gradient(135deg, rgba(255, 255, 255, 0.88), rgba(255, 255, 255, 0.62)),
        var(--surface);
      font: inherit;
      font-size: 11px;
      font-weight: 950;
      letter-spacing: 0.02em;
      cursor: pointer;
      transition: transform 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
      white-space: nowrap;
    }
    .copy-field-btn:hover {
      transform: translateY(-1px);
      border-color: color-mix(in srgb, var(--field-accent) 72%, white 28%);
      box-shadow: 0 10px 24px color-mix(in srgb, var(--field-accent) 20%, transparent);
    }
    .copy-field-btn:focus-visible {
      outline: none;
      box-shadow:
        0 0 0 3px color-mix(in srgb, var(--field-accent) 22%, transparent),
        0 10px 24px color-mix(in srgb, var(--field-accent) 18%, transparent);
    }
    .copy-field-btn .btn-icon {
      font-size: 14px;
      line-height: 1;
    }
    body:not(.theme-light) .copy-field-btn {
      border-color: var(--field-border);
      background:
        linear-gradient(135deg, color-mix(in srgb, var(--field-tint) 76%, rgba(255, 255, 255, 0.03)), rgba(0, 0, 0, 0.28)),
        rgba(0, 0, 0, 0.42);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
    }
    body:not(.theme-light) .copy-field-btn:hover {
      border-color: color-mix(in srgb, var(--field-accent-2) 66%, transparent);
    }
    .project-summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      padding: 0;
    }
    body:not(.theme-light) .project-summary {
      border: 1px solid rgba(232, 160, 96, 0.24);
      border-radius: 16px;
      padding: 12px 14px;
      background:
        linear-gradient(135deg, rgba(242, 178, 101, 0.10), rgba(255, 255, 255, 0.035)),
        rgba(0, 0, 0, 0.34);
    }
    .project-summary > div { min-width: 0; }
    .summary-label {
      display: inline-flex;
      margin-bottom: 7px;
      color: var(--field-accent-2);
      font-size: 11px;
      font-weight: 900;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    .project-summary strong {
      display: block;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      margin-bottom: 3px;
      font-size: 20px;
      letter-spacing: -0.04em;
    }
    .quick-links { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 2px; }
    .status,
    .upload-empty,
    .upload-status,
    .upload-result {
      border: 1px solid var(--control-line-soft);
      border-radius: 14px;
      padding: 10px 11px;
      color: var(--status-text);
      background: var(--surface);
      font-size: 13px;
      line-height: 1.45;
    }
    .status.good,
    .upload-status.good,
    .upload-result.good { border-color: rgba(242, 178, 101, 0.34); color: var(--good-text); }
    .status.bad,
    .upload-status.bad,
    .upload-result.bad { border-color: rgba(255, 82, 82, 0.40); color: var(--danger-text); }
    .status.warn,
    .upload-status.warn,
    .upload-result.warn { border-color: rgba(255, 171, 64, 0.34); color: var(--warn-text); }
    .upload-empty { display: grid; place-content: center; justify-items: center; gap: 10px; min-height: 280px; margin-top: 10px; border-radius: 22px; background: radial-gradient(circle at 50% 0%, rgba(243,107,33,.08), transparent 50%), var(--surface-panel); box-shadow: var(--shadow); text-align: center; }
    .upload-empty strong { color: var(--warn-text); font-size: 20px; letter-spacing: -.03em; }
    .upload-empty .small-link { min-width: 220px; margin-top: 6px; }
    .upload-panel {
      border: 1px solid rgba(242, 178, 101, 0.16);
      border-radius: 22px;
      padding: 14px;
      background:
        radial-gradient(circle at 20% 0%, rgba(242, 178, 101, 0.10), transparent 40%),
        var(--surface-panel);
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }
    body:not(.theme-light) .upload-panel {
      border-color: rgba(168, 85, 247, 0.42);
      background:
        radial-gradient(circle at 50% 0%, rgba(168, 85, 247, 0.18), transparent 34%),
        linear-gradient(150deg, rgba(26, 17, 39, 0.97), rgba(6, 5, 12, 0.98));
      box-shadow:
        0 0 0 1px rgba(168, 85, 247, 0.10),
        0 24px 80px rgba(0, 0, 0, 0.78);
    }
    .upload-head { text-align: center; margin-bottom: 8px; }
    .upload-head h3 { margin: 0 0 5px; font-size: 20px; letter-spacing: -0.035em; }
    .upload-head span { display: block; color: var(--text-faint); font-size: 12px; line-height: 1.45; }
    .upload-field { margin-top: 8px; }
    body:not(.theme-light) .upload-panel .upload-field {
      border: 1px solid var(--field-border);
      border-radius: 17px;
      padding: 10px;
      background:
        radial-gradient(circle at 0% 0%, var(--field-tint), transparent 34%),
        rgba(0, 0, 0, 0.24);
    }
    .upload-field.compact { max-width: none; }
    .platform-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: clamp(12px, 1.4vw, 22px);
      margin-top: 0;
      align-items: stretch;
    }
    .platform-card {
      border: 1px solid var(--field-border);
      border-radius: 18px;
      padding: 12px;
      background:
        radial-gradient(circle at 0% 0%, var(--field-tint), transparent 36%),
        var(--surface);
    }
    .platform-youtube { order: 1; --field-accent: #ef4444; --field-accent-2: #fca5a5; --field-tint: rgba(239, 68, 68, 0.12); --field-border: rgba(239, 68, 68, 0.32); --field-icon-text: #300808; }
    .platform-facebook { order: 2; --field-accent: #7c3aed; --field-accent-2: #c4b5fd; --field-tint: rgba(124, 58, 237, 0.12); --field-border: rgba(124, 58, 237, 0.32); --field-icon-text: #f5f3ff; }
    .platform-binance { order: 3; --field-accent: #F3BA2F; --field-accent-2: #f7d774; --field-tint: rgba(243, 186, 47, 0.12); --field-border: rgba(243, 186, 47, 0.32); --field-icon-text: #2a1f02; }
    .platform-instagram { order: 4; --field-accent: #e1306c; --field-accent-2: #f9a8d4; --field-tint: rgba(225, 48, 108, 0.12); --field-border: rgba(225, 48, 108, 0.32); --field-icon-text: #fff1f7; }
    .platform-threads { order: 5; --field-accent: #111827; --field-accent-2: #374151; --field-tint: rgba(17, 24, 39, 0.10); --field-border: rgba(17, 24, 39, 0.24); --field-icon-text: #fff; }
    .platform-card-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }
    .platform-title {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--field-accent-2);
      font-size: 13px;
      font-weight: 950;
      letter-spacing: -0.02em;
    }
    .platform-guide-link {
      min-height: 34px;
      padding: 7px 10px;
      flex: 0 0 auto;
      border-color: var(--field-border);
      font-size: 11px;
    }
    .platform-brand-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 22px;
      height: 22px;
      min-width: 22px;
      flex: 0 0 22px;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }
    .platform-brand-icon svg {
      width: 22px;
      height: 22px;
      display: block;
    }
    .platform-guide-link .btn-icon {
      color: #0b1220 !important;
      background: transparent !important;
      box-shadow: none !important;
    }
    body:not(.theme-light) .platform-guide-link .btn-icon {
      color: #f4fff8 !important;
    }
    body.theme-light .platform-guide-link .btn-icon {
      color: #0b1220 !important;
    }
    .platform-account-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
      position: relative;
      z-index: 3;
    }
    .platform-account-list.open {
      z-index: 80;
    }
    .platform-account {
      display: grid;
      grid-template-columns: 42px minmax(0, 1fr);
      align-items: center;
      gap: 10px;
      width: 100%;
      border: 1px solid var(--field-border);
      border-radius: 16px;
      padding: 9px;
      background: var(--surface);
      color: inherit;
      font: inherit;
      text-align: left;
    }
    button.platform-account {
      cursor: pointer;
      transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease, background 0.16s ease;
    }
    button.platform-account:hover:not(:disabled) {
      transform: translateY(-1px);
      border-color: var(--field-accent-2);
      box-shadow: 0 14px 30px color-mix(in srgb, var(--field-accent, #f2b261) 16%, transparent);
    }
    button.platform-account:disabled {
      cursor: default;
      opacity: 0.78;
    }
    .platform-account.active {
      border-color: var(--field-accent-2);
      background:
        linear-gradient(135deg, var(--field-tint), rgba(255, 255, 255, 0.04)),
        var(--surface);
      box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--field-accent, #f2b261) 18%, transparent);
    }
    .platform-account-trigger {
      grid-template-columns: 42px minmax(0, 1fr) auto;
    }
    .account-chevron {
      display: inline-flex !important;
      align-items: center;
      justify-content: center;
      min-width: auto !important;
      overflow: visible !important;
      color: var(--text-faint);
      font-size: 18px;
      font-weight: 950;
      line-height: 1;
      transition: transform 0.16s ease;
    }
    .platform-account-list.open .account-chevron {
      transform: rotate(180deg);
    }
    .platform-account-options {
      position: absolute;
      top: calc(100% + 8px);
      left: 0;
      right: 0;
      display: none;
      gap: 7px;
      z-index: 20;
      border: 1px solid var(--field-border);
      border-radius: 18px;
      padding: 8px;
      background: #fff7ea;
      box-shadow: 0 22px 48px rgba(0, 0, 0, 0.24);
    }
    .platform-account-list.open .platform-account-options {
      display: grid;
    }
    .platform-account-option {
      border-color: rgba(79, 57, 31, 0.12);
      background: #fffbf4;
    }
    body:not(.theme-light) .platform-account-options {
      background: #111318;
      box-shadow: 0 24px 54px rgba(0, 0, 0, 0.46);
    }
    body:not(.theme-light) .platform-account-option {
      border-color: rgba(255, 255, 255, 0.10);
      background: #171a21;
    }
    .platform-account img {
      width: 42px;
      height: 42px;
      border-radius: 999px;
      object-fit: cover;
      background: var(--surface-strong);
    }
    .platform-account strong,
    .platform-account span {
      display: block;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .platform-account strong {
      color: var(--text);
      font-size: 13px;
      font-weight: 950;
    }
    .platform-account span {
      margin-top: 3px;
      color: var(--text-faint);
      font-size: 11px;
      font-weight: 800;
    }
    .platform-card .upload-field {
      margin-top: 10px;
      padding: 0;
      border: 0;
      background: transparent;
    }
    .platform-card .field-source-comment {
      max-width: 520px;
    }
    .platform-card .field-source-comment input {
      min-height: 44px;
      padding-block: 9px;
      font-size: 14px;
    }
    .facebook-config-field {
      display: grid;
      grid-template-columns: minmax(120px, 0.64fr) minmax(0, 1fr);
      align-items: center;
      gap: 9px;
      margin: 0 !important;
    }
    .facebook-config-field input {
      min-height: 42px;
      font-size: 13px;
    }
    .config-actions {
      align-items: center;
      margin-top: 0;
    }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 200;
      display: grid;
      place-items: center;
      padding: 24px;
      background: rgba(11, 7, 3, 0.44);
      backdrop-filter: blur(18px);
    }
    .modal-card {
      position: relative;
      width: min(100%, 560px);
      border: 1px solid rgba(216, 132, 53, 0.42);
      border-radius: 24px;
      padding: 22px;
      color: #21170f;
      background:
        radial-gradient(circle at 10% 16%, rgba(242, 178, 101, 0.22), transparent 36%),
        radial-gradient(circle at 92% 0%, rgba(232, 160, 96, 0.16), transparent 34%),
        linear-gradient(145deg, #fffefb 0%, #fff9f0 52%, #fff4e6 100%);
      box-shadow: 0 34px 95px rgba(44, 31, 18, 0.28);
    }
    .facebook-config-modal,
    .youtube-config-modal {
      width: min(100%, 720px);
      border-color: rgba(216, 132, 53, 0.42);
      color: #21170f;
      background:
        radial-gradient(circle at 10% 16%, rgba(242, 178, 101, 0.22), transparent 36%),
        radial-gradient(circle at 92% 0%, rgba(232, 160, 96, 0.16), transparent 34%),
        linear-gradient(145deg, #fffefb 0%, #fff9f0 52%, #fff4e6 100%);
      box-shadow: 0 34px 95px rgba(44, 31, 18, 0.28);
    }
    .instagram-config-modal {
      width: min(100%, 980px);
    }
    .instagram-config-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px 14px;
      margin-top: 16px;
    }
    .instagram-config-field {
      min-width: 0;
      margin: 0 !important;
    }
    .instagram-config-field input,
    .instagram-config-field select {
      min-height: 42px;
      font-size: 13px;
    }
    .instagram-config-wide {
      grid-column: 1 / -1;
    }
    .instagram-r2-config-fields {
      display: contents;
    }
    .instagram-r2-shared-note {
      display: grid;
      gap: 4px;
      border: 1px solid rgba(232, 160, 96, 0.42);
      border-radius: 14px;
      padding: 11px 13px;
      color: #8d4e1e;
      background: rgba(242, 178, 101, 0.14);
    }
    .instagram-r2-shared-note > span {
      font-size: 13px;
      font-weight: 950;
    }
    .instagram-r2-shared-note small {
      color: rgba(75, 48, 27, 0.72);
      font-size: 11px;
      font-weight: 750;
      line-height: 1.45;
    }
    .instagram-r2-shared-note .small-link {
      justify-self: start;
      margin-top: 3px;
      border: 0;
      padding: 0;
      color: #c96c27;
      background: transparent;
      font: inherit;
      font-size: 11px;
      font-weight: 950;
      cursor: pointer;
    }
    body:not(.theme-light) .instagram-r2-shared-note {
      color: #ffc08f;
      background: rgba(242, 178, 101, 0.12);
    }
    body:not(.theme-light) .instagram-r2-shared-note small {
      color: rgba(255, 247, 237, 0.76);
    }
    body:not(.theme-light) .instagram-r2-shared-note .small-link {
      color: #ffb47e;
    }
    .instagram-retain-field {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .instagram-retain-field input {
      width: 18px;
      min-height: 18px;
      accent-color: #e1306c;
    }
    body:not(.theme-light) .modal-card,
    body:not(.theme-light) .facebook-config-modal,
    body:not(.theme-light) .youtube-config-modal {
      border-color: rgba(232, 160, 96, 0.42);
      color: #fff8ef;
      background:
        radial-gradient(circle at 10% 12%, rgba(242, 178, 101, 0.18), transparent 36%),
        radial-gradient(circle at 92% 0%, rgba(232, 160, 96, 0.12), transparent 36%),
        linear-gradient(145deg, #2a2118 0%, #241c16 58%, #1a1510 100%);
      box-shadow: 0 34px 110px rgba(0, 0, 0, 0.62);
    }
    .modal-card h3 {
      margin: 0 0 8px;
      font-size: 30px;
      letter-spacing: -0.045em;
    }
    .facebook-config-modal .kicker,
    .facebook-config-modal h3,
    .facebook-config-modal .field-label,
    .facebook-config-modal .field-label span {
      color: currentColor;
    }
    .modal-copy {
      margin: 0 0 16px;
      color: var(--text-faint);
      font-size: 13px;
      line-height: 1.45;
      font-weight: 750;
    }
    .facebook-config-modal .modal-copy,
    .youtube-config-modal .modal-copy {
      max-width: 610px;
      color: rgba(33, 23, 15, 0.74);
      font-size: 14px;
      font-weight: 850;
    }
    body:not(.theme-light) .facebook-config-modal .modal-copy,
    body:not(.theme-light) .youtube-config-modal .modal-copy {
      color: rgba(255, 247, 237, 0.78);
    }
    .modal-copy code {
      color: var(--text);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
    }
    .facebook-config-modal .modal-copy code,
    .youtube-config-modal .modal-copy code {
      color: #9a5a22;
      background: rgba(242, 178, 101, 0.18);
      border-radius: 8px;
      padding: 2px 5px;
      font-weight: 950;
    }
    body:not(.theme-light) .facebook-config-modal .modal-copy code,
    body:not(.theme-light) .youtube-config-modal .modal-copy code {
      color: #ffb47e;
      background: rgba(0, 0, 0, 0.28);
    }
    .facebook-config-modal .facebook-config-field,
    .youtube-config-modal .facebook-config-field {
      border: 1px solid rgba(216, 132, 53, 0.55);
      border-radius: 18px;
      padding: 10px 12px;
      background: rgba(255, 252, 246, 0.92);
      box-shadow: 0 0 0 1px rgba(242, 178, 101, 0.12);
    }
    .facebook-config-modal .facebook-config-field + .facebook-config-field,
    .youtube-config-modal .facebook-config-field + .facebook-config-field {
      margin-top: 10px !important;
    }
    .facebook-config-modal .facebook-config-field input,
    .youtube-config-modal .facebook-config-field input {
      color: #21170f;
      border-color: rgba(216, 132, 53, 0.28);
      background: rgba(255, 255, 255, 0.72);
      box-shadow: none;
    }
    .facebook-config-modal .facebook-config-field input::placeholder,
    .youtube-config-modal .facebook-config-field input::placeholder {
      color: rgba(33, 23, 15, 0.52);
    }
    .facebook-config-modal .facebook-config-field input:focus,
    .youtube-config-modal .facebook-config-field input:focus {
      outline: none;
      border-color: rgba(216, 132, 53, 0.70);
      box-shadow: 0 0 0 3px rgba(242, 178, 101, 0.18);
    }
    .facebook-config-modal .facebook-config-field input:-webkit-autofill,
    .facebook-config-modal .facebook-config-field input:-webkit-autofill:hover,
    .facebook-config-modal .facebook-config-field input:-webkit-autofill:focus,
    .youtube-config-modal .facebook-config-field input:-webkit-autofill,
    .youtube-config-modal .facebook-config-field input:-webkit-autofill:hover,
    .youtube-config-modal .facebook-config-field input:-webkit-autofill:focus {
      -webkit-text-fill-color: #21170f;
      caret-color: #21170f;
      border-color: rgba(216, 132, 53, 0.55);
      box-shadow: 0 0 0 1000px rgba(255, 252, 246, 0.96) inset;
      transition: background-color 99999s ease-out;
    }
    body:not(.theme-light) .facebook-config-modal .facebook-config-field,
    body:not(.theme-light) .youtube-config-modal .facebook-config-field {
      border-color: rgba(232, 160, 96, 0.48);
      background: rgba(242, 178, 101, 0.10);
      box-shadow: 0 0 0 1px rgba(242, 178, 101, 0.10);
    }
    body:not(.theme-light) .facebook-config-modal .facebook-config-field input,
    body:not(.theme-light) .youtube-config-modal .facebook-config-field input {
      color: #fff7ed;
      border-color: rgba(255, 255, 255, 0.14);
      background: rgba(0, 0, 0, 0.22);
    }
    body:not(.theme-light) .facebook-config-modal .facebook-config-field input:-webkit-autofill,
    body:not(.theme-light) .facebook-config-modal .facebook-config-field input:-webkit-autofill:hover,
    body:not(.theme-light) .facebook-config-modal .facebook-config-field input:-webkit-autofill:focus,
    body:not(.theme-light) .youtube-config-modal .facebook-config-field input:-webkit-autofill,
    body:not(.theme-light) .youtube-config-modal .facebook-config-field input:-webkit-autofill:hover,
    body:not(.theme-light) .youtube-config-modal .facebook-config-field input:-webkit-autofill:focus {
      -webkit-text-fill-color: #fff7ed;
      caret-color: #fff7ed;
      border-color: rgba(232, 160, 96, 0.55);
      box-shadow: 0 0 0 1000px rgba(42, 33, 24, 0.96) inset;
    }
    .modal-close {
      position: absolute;
      top: 14px;
      right: 14px;
      width: 34px;
      height: 34px;
      border: 1px solid var(--control-line);
      border-radius: 999px;
      color: var(--text);
      background: var(--surface);
      font: inherit;
      font-size: 20px;
      font-weight: 900;
      cursor: pointer;
    }
    .modal-actions {
      display: flex;
      justify-content: flex-end;
      gap: 9px;
      margin-top: 16px;
    }
    body:not(.theme-light) .upload-panel .platform-card .upload-field {
      padding: 0;
      border: 0;
      background: transparent;
    }
    .platform-actions {
      display: flex;
      align-items: center;
      justify-content: flex-start;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }
    .schedule-field { max-width: none !important; }
    .schedule-toggle-label {
      display: flex;
      align-items: center;
      gap: 9px;
      cursor: pointer;
      color: var(--text-soft);
      font-weight: 700;
    }
    .schedule-toggle-label input[type="checkbox"] {
      width: 17px;
      min-height: 17px;
      accent-color: var(--accent);
    }
    .schedule-row { max-width: none !important; }
    .schedule-row[hidden] { display: none; }
    .schedule-row input { min-height: 42px; font-size: 13px; }
    .schedule-row .form-note { margin: 4px 0 0; color: var(--text-faint); font-size: 11px; line-height: 1.45; }
    .final-upload-actions {
      display: flex;
      justify-content: center;
      align-items: center;
      flex-wrap: wrap;
      gap: 9px;
      margin-top: 18px;
      padding-top: 14px;
      border-top: 1px solid var(--control-line-soft);
    }
    .upload-btn,
    .refresh-btn,
    .icon-btn {
      min-height: 40px;
      border: 1px solid var(--control-line);
      border-radius: 13px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 7px;
      color: var(--text-button);
      background: var(--surface);
      font-size: 12px;
      font-weight: 950;
      cursor: pointer;
      text-decoration: none;
    }
    .platform-actions .upload-btn {
      width: auto;
      min-height: 38px;
      padding: 8px 12px;
      flex: 0 0 auto;
      justify-content: center;
    }
    .refresh-btn {
      color: var(--text-soft);
      background: transparent;
      border: 1px solid var(--control-line);
      box-shadow: none;
    }
    .refresh-btn .btn-icon,
    .theme-toggle .btn-icon,
    #refreshProjects .btn-icon {
      color: var(--text-soft) !important;
      background: transparent !important;
      box-shadow: none !important;
    }
    .upload-btn:disabled { cursor: not-allowed; opacity: 0.46; }
    .btn-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 18px;
      height: 18px;
      min-width: 18px;
      flex: 0 0 18px;
      border-radius: 0;
      color: var(--text-soft);
      background: transparent !important;
      box-shadow: none !important;
      font-size: inherit;
      font-weight: 400;
      line-height: 1;
      text-shadow: none;
    }
    .btn-icon svg {
      width: 18px;
      height: 18px;
      display: block;
      stroke: currentColor;
      fill: none;
    }
    .upload-btn.youtube .btn-icon,
    .upload-btn.facebook .btn-icon,
    .upload-btn.instagram .btn-icon,
    .upload-btn.both .btn-icon {
      color: var(--text-soft) !important;
      background: transparent !important;
      box-shadow: none !important;
    }
    .upload-status {
      width: fit-content;
      max-width: min(100%, 860px);
      margin: 14px auto 0;
    }
    .upload-result { margin-top: 10px; }
    .small-link { color: var(--text); background: var(--surface-strong); }
    .upload-result a { color: var(--accent); font-weight: 900; background: transparent; padding: 0; }

    /* Match Dashboard surfaces and icon weight across Upload Center. */
    body.theme-light {
      --upload-surface: rgba(250, 244, 236, 0.90);
      --upload-card: rgba(250, 244, 236, 0.82);
      --upload-control: rgba(250, 244, 236, 0.94);
      --body-bg:
        radial-gradient(circle at 18% 12%, rgba(222, 142, 69, 0.14), transparent 28rem),
        radial-gradient(circle at 88% 18%, rgba(187, 123, 45, 0.11), transparent 24rem),
        linear-gradient(135deg, #efe6d7, #dfd4c2);
    }
    body:not(.theme-light) {
      --upload-surface: rgba(31, 29, 26, 0.96);
      --upload-card: rgba(39, 36, 32, 0.94);
      --upload-control: rgba(48, 44, 39, 0.92);
      --body-bg:
        radial-gradient(circle at 18% 12%, rgba(232, 128, 55, 0.12), transparent 28rem),
        radial-gradient(circle at 88% 18%, rgba(116, 88, 50, 0.13), transparent 24rem),
        linear-gradient(135deg, #1d1a17, #12110f);
    }
    body.theme-light .upload-panel,
    body:not(.theme-light) .upload-panel {
      border-color: rgba(232, 160, 96, 0.48) !important;
      background: var(--upload-surface) !important;
      box-shadow: 0 14px 34px rgba(0, 0, 0, 0.08) !important;
    }
    body.theme-light .platform-card,
    body:not(.theme-light) .platform-card {
      border-color: color-mix(in srgb, var(--accent) 34%, transparent) !important;
      background: var(--upload-card) !important;
    }
    body.theme-light .top-project-field select,
    body.theme-light .platform-card input,
    body.theme-light .platform-card textarea,
    body.theme-light .platform-card select,
    body.theme-light .platform-account,
    body.theme-light .refresh-btn,
    body.theme-light .platform-guide-link,
    body:not(.theme-light) .top-project-field select,
    body:not(.theme-light) .platform-card input,
    body:not(.theme-light) .platform-card textarea,
    body:not(.theme-light) .platform-card select,
    body:not(.theme-light) .platform-account,
    body:not(.theme-light) .refresh-btn,
    body:not(.theme-light) .platform-guide-link {
      border-color: color-mix(in srgb, var(--text) 18%, transparent) !important;
      background: var(--upload-control) !important;
    }
    body:not(.theme-light) .upload-btn.secondary {
      border-color: rgba(255, 238, 218, 0.18) !important;
      background: var(--upload-control) !important;
    }
    .upload-machine .btn-icon svg,
    .upload-machine .field-icon svg,
    .platform-brand-icon svg {
      stroke-width: 2.35;
    }
    .upload-machine .upload-btn.youtube .btn-icon { color: #ef4444 !important; }
    .upload-machine .upload-btn.facebook .btn-icon { color: #7c3aed !important; }
    .upload-machine .upload-btn.instagram .btn-icon { color: #e1306c !important; }
    .upload-machine .upload-btn.both .btn-icon { color: #22c55e !important; }
    .upload-machine .refresh-btn .btn-icon { color: #2563eb !important; }
    [hidden] { display: none !important; }
    @media (min-width: 1500px) {
      body {
        --upload-page-max: 1440px;
        padding: 32px clamp(40px, 4vw, 82px);
      }
      html.tauri-macos body { padding-top: 54px; }
      .upload-machine { gap: 16px; }
      .top-upload-bar .brand-mark,
      .top-upload-bar .upload-brand-mark { width: 68px; height: 68px; flex-basis: 68px; }
      .top-upload-bar h1 { font-size: clamp(34px, 3.6vw, 44px); }
      .upload-context-cluster { grid-template-columns: minmax(240px, 0.95fr) minmax(280px, 1.05fr); gap: 14px; }
      .top-project-field { width: 100%; max-width: none; grid-template-columns: minmax(0, 1fr); }
      .top-project-field select { min-height: 50px; font-size: 16px; }
      .top-upload-actions .refresh-btn { min-height: 48px; padding: 10px 16px; font-size: 14px; }
      .ready-pill { padding: 10px 14px; font-size: 14px; }
      .ready-pill strong { font-size: 20px; }
      .upload-panel { padding: 22px; border-radius: 28px; }
      .platform-grid { gap: 28px; }
      .platform-card { padding: 20px; border-radius: 22px; }
      .platform-card-head { margin-bottom: 16px; }
      .platform-title { gap: 10px; font-size: 17px; }
      .platform-guide-link { min-height: 42px; padding: 9px 14px; font-size: 14px; }
      .field > span { font-size: 14px; }
      .field-icon { width: 30px; height: 30px; font-size: 12px; }
      .field input,
      .field select,
      .field textarea { min-height: 54px; padding: 14px 16px; font-size: 16px; }
      .field textarea { min-height: 210px; }
      .platform-card .field-source-comment input { min-height: 50px; font-size: 15px; }
      .platform-card .upload-field.compact.field-youtube,
      .platform-card .upload-field.compact.field-facebook { width: min(100%, 350px); max-width: 350px; }
      .platform-actions { gap: 11px; margin-top: 16px; }
      .platform-actions .upload-btn { min-height: 46px; padding: 10px 16px; font-size: 14px; }
      .upload-btn.both { min-height: 52px; min-width: min(100%, 520px); font-size: 14px; }
      .upload-status, .upload-result { font-size: 15px; }
    }
    @media (max-width: 1420px) {
      .upload-header { display: grid; }
      .header-tools { justify-content: stretch; justify-self: stretch; width: 100%; }
      .top-upload-bar {
        grid-template-columns: minmax(220px, 1fr) auto;
        gap: 14px 18px;
      }
      .top-upload-actions {
        justify-content: flex-end;
      }
    }
    @media (max-width: 720px) {
      body { padding: 20px 14px; }
      html.tauri-macos body { padding-top: 54px; }
      .project-picker-head,
      .project-summary { align-items: flex-start; flex-direction: column; }
      .upload-header { grid-template-columns: 1fr; gap: 16px; }
      .upload-title-block { min-height: 0; }
      .header-tools { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .top-upload-bar {
        grid-template-columns: 1fr;
        align-items: flex-start;
      }
      .upload-page-head { align-items: flex-start; flex-direction: column; }
      .upload-context-grid { grid-template-columns: 1fr; }
      .upload-social-summary { flex-direction: column; gap: 8px; }
      .upload-social-summary > strong { min-width: 0; padding-top: 0; }
      .top-upload-actions { width: 100%; justify-content: flex-start; flex-wrap: wrap; }
      .platform-card-head { align-items: flex-start; flex-direction: column; }
      .platform-grid { grid-template-columns: 1fr; }
      .facebook-config-field,
      .youtube-config-field { grid-template-columns: 1fr; }
      .modal-actions { flex-direction: column-reverse; }
      .modal-actions .upload-btn { width: 100%; }
    }
""",
        extra_script=f"""
  <script>
    window.__PROJECTS__ = {json.dumps(projects, ensure_ascii=False)};
    window.__INITIAL_PROJECT__ = {json.dumps(selected_project, ensure_ascii=False)};
    window.__PROJECT_SOURCE_ROOT__ = {json.dumps(str(PROJECT_ROOT), ensure_ascii=False)};
  </script>
      <script src="/web/render_page.js?v=20260830-affiliate-v1"></script>
""",
    )


def render_page_shell(title: str, body: str, extra_style: str = "", extra_script: str = "") -> bytes:
    html_text = f"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html.escape(title)}</title>
  <link rel="icon" href="/web/favicon.ico" sizes="any" />
  <link rel="shortcut icon" href="/web/favicon.ico" type="image/x-icon" />
  <style>
    /* Prefer OS Vietnamese-capable faces on Windows WebView2. Keep Inter as a
       self-hosted fallback with multi-format src so variable fonts still load. */
    @font-face {{
      font-family: "Inter";
      font-style: normal;
      font-weight: 100 900;
      font-display: swap;
      src: url("/assets/fonts/InterVariable.woff2") format("woff2") tech("variations"),
           url("/assets/fonts/InterVariable.woff2") format("woff2-variations"),
           url("/assets/fonts/InterVariable.woff2") format("woff2");
    }}
    /* Static Inter faces for Windows WebView2 (variable + weight 900 breaks Vietnamese). */
    @font-face {{
      font-family: "Inter UI";
      font-style: normal;
      font-weight: 400;
      font-display: swap;
      src: url("/assets/fonts/Inter-Regular.woff2") format("woff2");
    }}
    @font-face {{
      font-family: "Inter UI";
      font-style: normal;
      font-weight: 700;
      font-display: swap;
      src: url("/assets/fonts/Inter-Bold.woff2") format("woff2");
    }}
    :root {{
      color-scheme: dark;
      --bg: #181818;
      --panel: transparent;
      --line: rgba(242, 178, 101, 0.18);
      --text: #f4fff8;
      --muted: rgba(244, 255, 248, 0.62);
      --accent: #f2b261;
      --accent-contrast: #f4fff8;
      --body-bg: #181818;
      --surface: transparent;
      --surface-strong: rgba(255, 255, 255, 0.06);
      --surface-panel: transparent;
      --field-bg: rgba(255, 255, 255, 0.04);
      --control-line: rgba(255, 255, 255, 0.14);
      --control-line-soft: rgba(255, 255, 255, 0.12);
      --control-line-faint: rgba(255, 255, 255, 0.10);
      --text-soft: rgba(246, 255, 249, 0.78);
      --text-faint: rgba(246, 255, 249, 0.62);
      --text-button: rgba(246, 255, 249, 0.86);
      --status-text: rgba(246, 255, 249, 0.74);
      --good-text: #ffd59a;
      --warn-text: #ffd699;
      --danger-text: #ffb8b8;
      --delete-text: #ffd6d6;
      --warning-text: #ffe4ba;
      --shadow: none;
      --accent-glow: rgba(242, 178, 101, 0.20);
      --font-ui: Inter, -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
    }}
    html.tauri-windows {{
      /* Static Inter (not variable): keeps Vietnamese diacritics on one face.
         Cap UI bold at 700 so WebView2 does not synthesize Black weights. */
      --font-ui: "Inter UI", "Segoe UI", system-ui, sans-serif;
    }}
    html.tauri-windows body,
    html.tauri-windows button,
    html.tauri-windows input,
    html.tauri-windows select,
    html.tauri-windows textarea,
    html.tauri-windows optgroup,
    html.tauri-windows option {{
      font-family: var(--font-ui) !important;
      font-feature-settings: normal;
      font-synthesis: none;
    }}
    html.tauri-windows .tab,
    html.tauri-windows .select-btn,
    html.tauri-windows .refresh-btn,
    html.tauri-windows .delete-output-btn,
    html.tauri-windows .icon-btn,
    html.tauri-windows .small-link,
    html.tauri-windows .list-head,
    html.tauri-windows .project-row,
    html.tauri-windows .project-name,
    html.tauri-windows .kicker,
    html.tauri-windows .header-stat span,
    html.tauri-windows .field span,
    html.tauri-windows .field > span,
    html.tauri-windows .section-title,
    html.tauri-windows .panel-title,
    html.tauri-windows h1,
    html.tauri-windows h2,
    html.tauri-windows h3,
    html.tauri-windows label,
    html.tauri-windows .muted,
    html.tauri-windows .status,
    html.tauri-windows .status-pill,
    html.tauri-windows .state-percent,
    html.tauri-windows .account-plan-badge {{
      font-family: var(--font-ui) !important;
      font-feature-settings: normal;
      font-weight: 700 !important;
      font-synthesis: none;
    }}
    body.theme-light {{
      color-scheme: light;
      --bg: #e9e4da;
      --panel: transparent;
      --line: rgba(79, 57, 31, 0.12);
      --text: #20170f;
      --muted: rgba(32, 23, 15, 0.62);
      --accent: #f36b21;
      --accent-contrast: #fffaf4;
      --body-bg:
        radial-gradient(circle at 12% -8%, rgba(255, 165, 92, 0.24), transparent 31rem),
        radial-gradient(circle at 92% 108%, rgba(138, 108, 69, 0.12), transparent 28rem),
        linear-gradient(135deg, #f8f5ef, var(--bg));
      --surface: transparent;
      --surface-strong: rgba(79, 57, 31, 0.05);
      --surface-panel: transparent;
      --field-bg: rgba(255, 251, 244, 0.72);
      --control-line: rgba(79, 57, 31, 0.13);
      --control-line-soft: rgba(79, 57, 31, 0.12);
      --control-line-faint: rgba(79, 57, 31, 0.11);
      --text-soft: rgba(32, 23, 15, 0.78);
      --text-faint: rgba(32, 23, 15, 0.62);
      --text-button: rgba(32, 23, 15, 0.84);
      --status-text: rgba(32, 23, 15, 0.72);
      --good-text: #8f4f19;
      --warn-text: #885100;
      --danger-text: #9f2e2e;
      --delete-text: #842f2f;
      --warning-text: #754900;
      --shadow: none;
      --accent-glow: rgba(243, 107, 33, 0.18);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: var(--font-ui);
      font-feature-settings: "ss01" 1, "cv01" 1;
      color: var(--text);
      background: var(--body-bg);
      padding: 44px min(5vw, 72px);
    }}
    header {{ max-width: 920px; margin-bottom: 34px; }}
    h1 {{ margin: 0 0 10px; font-size: clamp(36px, 6vw, 76px); line-height: 0.92; letter-spacing: -0.06em; }}
    header p {{ max-width: 720px; color: var(--muted); font-size: 17px; line-height: 1.6; }}
    code {{ color: var(--accent); }}
    a {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 14px;
      padding: 10px 14px;
      color: var(--accent-contrast);
      background: var(--accent);
      text-decoration: none;
      font-weight: 800;
      transition: transform 150ms ease, box-shadow 150ms ease, background 150ms ease;
    }}
    a:hover {{ transform: translateY(-1px); box-shadow: 0 9px 24px var(--accent-glow); }}
    .small-link {{ border: 1px solid var(--control-line); color: var(--text); background: var(--surface-strong); }}
    .app-back {{
      display: inline-flex;
      align-items: center;
      justify-content: flex-start;
      gap: 10px;
      margin-bottom: 22px;
      border: 1px solid var(--control-line);
      padding: 7px 12px 7px 7px;
      color: var(--text);
      background: var(--panel);
      box-shadow: 0 10px 28px rgba(48,36,18,.08);
    }}
    .app-back img {{ width: 38px; height: 38px; border-radius: 11px; box-shadow: 0 6px 15px rgba(44,25,10,.18); }}
    .app-back span {{ display: grid; gap: 1px; text-align: left; }}
    .app-back strong {{ font-size: 12px; line-height: 1.15; }}
    .app-back small {{ color: var(--muted); font-size: 9px; font-weight: 700; line-height: 1.2; }}
    .native-titlebar-drag-region {{ display: none; }}
    html.tauri-macos .native-titlebar-drag-region {{
      position: fixed;
      top: 0;
      left: 100px;
      right: 100px;
      z-index: 10000;
      display: block;
      height: 30px;
    }}
{extra_style}
  </style>
</head>
<body class="theme-light">
<script>
  if (window.__TAURI_INTERNALS__ || new URLSearchParams(location.search).get('desktopApp') === '1') {{
    document.documentElement.classList.add('aurexvideo-desktop-app');
  }}
  if (window.__TAURI_INTERNALS__) {{
    const platform = String(navigator.platform || '');
    const ua = String(navigator.userAgent || '');
    if (/Mac/i.test(platform) || /Macintosh|Mac OS X/i.test(ua)) {{
      document.documentElement.classList.add('tauri-macos');
    }}
    if (/Win/i.test(platform) || /Windows/i.test(ua)) {{
      document.documentElement.classList.add('tauri-windows');
    }}
  }}
</script>
<div class="native-titlebar-drag-region" data-tauri-drag-region aria-hidden="true"></div>
{body}
<script>
  document.addEventListener('click', (event) => {{
    const link = event.target.closest('a[href]');
    if (!link) return;
    const url = new URL(link.href, window.location.href);
    if (!/^https?:$/.test(url.protocol) || url.origin === window.location.origin) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    fetch('/api/open-external', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ url: url.href }}),
    }}).catch(() => {{ window.location.href = url.href; }});
  }});
</script>
{extra_script}
<div class="settings-modal-backdrop" id="settingsModalBackdrop" hidden>
  <div class="settings-modal" role="dialog" aria-modal="true" aria-label="Cài đặt">
    <div class="settings-modal-head">
      <strong>Cài đặt</strong>
      <button class="settings-modal-close" type="button" id="settingsModalClose" aria-label="Đóng">×</button>
    </div>
    <iframe id="settingsModalFrame" src="/settings" title="Aurex Settings" style="width:100%;height:78vh;border:0;background:#fff;"></iframe>
  </div>
</div>
<style>
.settings-modal-backdrop{{position:fixed;inset:0;background:rgba(0,0,0,.55);display:flex;align-items:center;justify-content:center;z-index:9999}}
.settings-modal-backdrop[hidden]{{display:none !important;}}
.settings-modal{{background:#fff;border-radius:14px;width:min(720px,92vw);max-height:90vh;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.4)}}
.settings-modal-head{{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid #eee}}
.settings-modal-close{{border:0;background:transparent;font-size:22px;line-height:1;cursor:pointer}}
</style>
<script>
(function(){{
  var btn = document.getElementById('openSettingsButton');
  var backdrop = document.getElementById('settingsModalBackdrop');
  var frame = document.getElementById('settingsModalFrame');
  var close = document.getElementById('settingsModalClose');
  if(!backdrop || !frame || !close) return;
  function hide(){{ backdrop.hidden = true; }}
  if (btn) btn.addEventListener('click', function(e){{
    e.preventDefault();
    frame.src = '/settings';
    backdrop.hidden = false;
  }});
  close.addEventListener('click', hide);
  backdrop.addEventListener('click', function(e){{ if(e.target === backdrop) hide(); }});
  document.addEventListener('keydown', function(e){{ if(e.key === 'Escape' && !backdrop.hidden) hide(); }});
}})();
</script>
</body>
</html>

"""
    return html_text.encode("utf-8")


def project_url_to_path(request_path: str) -> Path:
    path = urlparse(request_path).path
    if path == "/project":
        return PROJECT_ROOT
    if not path.startswith("/project/"):
        raise ValueError("Not a project URL.")

    tail = path[len("/project/"):]
    project_part, _, relative_part = tail.partition("/")
    project = validate_project_name(project_part)
    project_dir = require_project(project)
    if not relative_part:
        return project_dir

    relative_text = unquote(relative_part)
    if "\x00" in relative_text:
        raise ValueError("Invalid project asset path.")
    target = (project_dir / relative_text).resolve()
    try:
        target.relative_to(project_dir.resolve())
    except ValueError as exc:
        raise ValueError("Invalid project asset path.") from exc
    return target

class WebHandler(SimpleHTTPRequestHandler):
    server_version = "VideoTemplateWeb/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(REPO_ROOT), **kwargs)

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def translate_path(self, path: str) -> str:
        parsed_path = urlparse(path).path
        if parsed_path == "/project" or parsed_path.startswith("/project/"):
            try:
                return str(project_url_to_path(path))
            except Exception:
                return str(REPO_ROOT / "__missing_project_asset__")
        if parsed_path == "/assets/characters" or parsed_path.startswith("/assets/characters/"):
            relative = unquote(parsed_path[len("/assets/characters/"):]) if parsed_path.startswith("/assets/characters/") else ""
            target = (m3.CHARACTERS_ROOT / relative).resolve()
            try:
                target.relative_to(m3.CHARACTERS_ROOT.resolve())
            except ValueError:
                return str(REPO_ROOT / "__missing_character_asset__")
            return str(target)
        if parsed_path == "/output" or parsed_path.startswith("/output/"):
            relative = unquote(parsed_path[len("/output/"):]) if parsed_path.startswith("/output/") else ""
            target = (m3.OUTPUT_ROOT / relative).resolve()
            try:
                target.relative_to(m3.OUTPUT_ROOT.resolve())
            except ValueError:
                return str(REPO_ROOT / "__missing_output_asset__")
            return str(target)
        return super().translate_path(path)

    def log_message(self, format: str, *args: object) -> None:
        timestamp = time.strftime("%H:%M:%S")
        if format == '"%s" %s %s' and len(args) >= 3:
            request_line = str(args[0])
            parts = request_line.split()
            method = parts[0] if parts else self.command
            target = parts[1] if len(parts) > 1 else self.path
            status = str(args[1])
            size = str(args[2])
            try:
                status_code = int(status)
            except ValueError:
                status_code = 0
            if 200 <= status_code < 300:
                icon, status_style = "✅", "green"
            elif 300 <= status_code < 400:
                icon, status_style = "↪", "cyan"
            elif 400 <= status_code < 500:
                icon, status_style = "⚠️", "yellow"
            else:
                icon, status_style = "❌", "red"
            method_style = "blue" if method == "GET" else "magenta"
            size_text = "" if size == "-" else f" · {size}B"
            print(
                f"{icon} {color_text(timestamp, 'dim')}  "
                f"{color_text(method, 'bold', method_style)} {color_text(target, 'cyan')}  "
                f"{color_text(status, 'bold', status_style)}{color_text(size_text, 'dim')}",
                file=sys.stderr,
            )
            return

        print(f"ℹ️  {color_text(timestamp, 'dim')}  {format % args}", file=sys.stderr)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        parsed_path = urlparse(self.path).path
        if parsed_path in {"/style.css", "/app.js", "/index.html"} or parsed_path.startswith(("/web/", "/webui/")) or (parsed_path.startswith("/project/") and parsed_path.endswith((".js", ".css"))):
            self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def send_json(self, status: int, payload: object) -> None:
        data = json_dumps(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, status: int, data: bytes) -> None:
        data = inject_ui_language(data)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            raise ValueError("Missing request body.")
        if length > MAX_UPLOAD_BYTES * 4:
            raise ValueError("Request body is too large.")
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            query = parse_qs(parsed.query)
            selected = (query.get("project") or [None])[0]
            preview_update = (query.get("preview-update") or [""])[0] == "1"
            self.send_html(200, render_home_html(selected, preview_update=preview_update))
            return

        if path in {"/editor", "/editor/"}:
            self.send_html(200, (REPO_ROOT / "webui" / "editor.html").read_bytes())
            return

        if path in {"/custom-editor", "/custom-editor/"}:
            self.send_html(200, (REPO_ROOT / "webui" / "custom-editor.html").read_bytes())
            return

        watch_match = re.fullmatch(r"/watch/([^/]+)/?", path)
        if watch_match:
            try:
                project = unquote(watch_match.group(1))
                project_dir = require_project(project)
                if not (project_dir / "output" / "final_video.mp4").is_file():
                    raise FileNotFoundError(f"Project '{project_dir.name}' chưa có video đã render.")
                self.send_html(200, render_simple_player_html(project_dir.name))
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        editor_match = re.fullmatch(r"/project/([^/]+)/", path)
        if editor_match:
            try:
                require_project(editor_match.group(1))
                self.send_html(200, (REPO_ROOT / "webui" / "editor.html").read_bytes())
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            return

        if path in {"/new-project", "/new-project/"}:
            self.send_html(200, (REPO_ROOT / "webui" / "new-project.html").read_bytes())
            return

        if path == "/api/ui-language":
            self.send_json(200, {"language": current_ui_language()})
            return

        if path in {"/settings", "/settings/"}:
            try:
                html = (REPO_ROOT / "webui" / "settings.html").read_text(encoding="utf-8")
                lang = current_ui_language()
                # inject language flag so the page can sync with the i18n framework
                injection = (
                    f'<script>window.__AUREX_LANGUAGE__={json.dumps(lang)};'
                    f'window.__AUREX_TRIAL__={json.dumps(trial_branding_required())};</script>'
                )
                if "<head>" in html:
                    html = html.replace("<head>", "<head>\n" + injection, 1)
                else:
                    html = injection + html
                self.send_html(200, html.encode("utf-8"))
            except Exception as exc:
                self.send_json(404, {"error": str(exc)})
            return

        if path == "/api/settings":
            self.send_json(200, {
                "language": current_ui_language(),
                "version": APP_VERSION,
                "updateAvailable": False,
            })
            return

        if path == "/api/images/remove-background/status":
            try:
                self.send_json(200, m3.remove_background_status())
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        match = re.fullmatch(r"/api/projects/([^/]+)/topic", path)
        if match:
            try:
                slug = m3.validate_slug(match.group(1))
                preview_topic_url = f"/project/{quote(slug)}/topic.json"
                self.send_json(200, {"project": slug, "topic": m3.read_topic(slug), "previewTopicUrl": preview_topic_url})
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path in {"/upload", "/upload/"}:
            selected = (parse_qs(parsed.query).get("project") or [None])[0]
            self.send_html(200, render_upload_html(selected))
            return

        if path in {"/affiliate", "/affiliate/"}:
            self.send_html(200, (REPO_ROOT / "webui" / "affiliate.html").read_bytes())
            return

        if path in {"/upload-guide/youtube", "/upload-guide/youtube/"}:
            self.send_html(200, render_social_upload_guide_html("youtube"))
            return

        if path in {"/upload-guide/facebook", "/upload-guide/facebook/"}:
            self.send_html(200, render_social_upload_guide_html("facebook"))
            return

        if path in {"/upload-guide/instagram", "/upload-guide/instagram/"}:
            self.send_html(200, render_social_upload_guide_html("instagram"))
            return

        if path in {"/elevenlabs-guide", "/elevenlabs-guide/"}:
            self.send_html(200, render_elevenlabs_guide_html())
            return
        if path == "/api/health":
            self.send_json(
                200,
                {
                    "ok": True,
                    "root": str(REPO_ROOT),
                    "source_root": str(PROJECT_ROOT),
                    "source_mode": source_root_mode(),
                    "projects": len(list_projects()),
                },
            )
            return

        if path == "/api/source-root":
            self.send_json(200, source_root_response())
            return

        if path == "/api/projects":
            self.send_json(200, {"projects": list_projects()})
            return

        if path == "/api/render-preferences":
            try:
                self.send_json(200, {"preferences": m3.read_render_preferences()})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/characters":
            try:
                characters = m3.list_characters()
                if trial_branding_required():
                    characters = [item for item in characters if item.get("id") == "hieu-ham-hoc"]
                default_character_id = ""
                try:
                    default_character_id = m3.default_character_id()
                except Exception:
                    default_character_id = ""
                if trial_branding_required() and default_character_id != "hieu-ham-hoc":
                    default_character_id = "hieu-ham-hoc" if characters else ""
                elif default_character_id and not any(item.get("id") == default_character_id for item in characters):
                    default_character_id = ""
                self.send_json(200, {
                    "characters": characters,
                    "locked": trial_branding_required(),
                    "defaultCharacterId": default_character_id,
                })
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/social/brands":
            project = (parse_qs(parsed.query).get("project") or [""])[0]
            try:
                if project:
                    require_project(project)
                self.send_json(200, upload_brand_context(project))
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/affiliate/context":
            project = (parse_qs(parsed.query).get("project") or [""])[0]
            brand = (parse_qs(parsed.query).get("brand") or [""])[0]
            try:
                if project:
                    require_project(project)
                context = upload_brand_context(project)
                selected_brand = canonical_brand(brand or context.get("project_brand") or "")
                context["selected_brand"] = selected_brand
                context["affiliate"] = affiliate_brand_context(read_social_config(), selected_brand) if selected_brand else {}
                self.send_json(200, context)
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/affiliate/products":
            query_values = parse_qs(parsed.query)
            project = (query_values.get("project") or [""])[0]
            brand = canonical_brand((query_values.get("brand") or [""])[0])
            query = (query_values.get("query") or [""])[0].strip()
            saved_query = (query_values.get("q") or [""])[0].strip()
            try:
                if project:
                    require_project(project)
                    if "query" in query_values and not query:
                        query = " ".join(social_metadata.read_script_lines(social_metadata.require_project(project))[:4])[:500]
                if not brand:
                    raise ValueError("Cần chọn Brand để tìm sản phẩm Shopee.")
                limit = int((query_values.get("limit") or [10])[0])
                if query:
                    self.send_json(200, discover_products(brand, query, limit=limit))
                else:
                    cached = list_saved_products(saved_query, limit=limit)
                    cached.update({
                        "brand": brand,
                        "settings": affiliate_brand_context(read_social_config(), brand).get("settings", {}),
                    })
                    self.send_json(200, cached)
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/affiliate/overview":
            query_values = parse_qs(parsed.query)
            try:
                start_date = (query_values.get("startDate") or query_values.get("start_date") or [""])[0]
                end_date = (query_values.get("endDate") or query_values.get("end_date") or [""])[0]
                period = str((query_values.get("period") or [""])[0] or "").strip().lower()
                if period and not (start_date or end_date):
                    today = datetime.now(timezone.utc).date()
                    end_date = today.isoformat()
                    if period == "today":
                        start_date = end_date
                    elif period == "7d":
                        start_date = (today - timedelta(days=6)).isoformat()
                    elif period == "30d":
                        start_date = (today - timedelta(days=29)).isoformat()
                    elif period == "month":
                        start_date = today.replace(day=1).isoformat()
                self.send_json(200, affiliate_overview(
                    canonical_brand((query_values.get("brand") or [""])[0]),
                    (query_values.get("contentId") or query_values.get("content_id") or [""])[0],
                    start_date=start_date,
                    end_date=end_date,
                ))
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/affiliate/poc":
            query_values = parse_qs(parsed.query)
            try:
                brand = canonical_brand((query_values.get("brand") or [""])[0])
                content_id = str((query_values.get("contentId") or query_values.get("content_id") or [""])[0] or "").strip()
                if len(content_id) > 128:
                    raise ValueError("POC Content / project ID tối đa 128 ký tự.")
                config = read_social_config()
                page_id = str((query_values.get("pageId") or query_values.get("page_id") or [""])[0] or "").strip()
                page_id = page_id or _affiliate_poc_default_page_id(config, brand)
                if len(page_id) > 128:
                    raise ValueError("POC Page ID tối đa 128 ký tự.")
                summary = _affiliate_poc_find_summary(brand, content_id, page_id)
                self.send_json(200, _affiliate_poc_response(brand, content_id, page_id, summary))
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/social/status":
            try:
                self.send_json(200, social_status())
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/tts/elevenlabs/config":
            try:
                self.send_json(200, elevenlabs_public_config())
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path in {"/api/tts/vieneu/config", "/api/tts/aurextts/config"}:
            try:
                self.send_json(200, m3.vieneu_public_config())
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/tts/vieneu/runtime":
            self.send_json(200, read_vieneu_runtime_config())
            return

        if path in {"/api/tts/vieneu/health", "/api/tts/aurextts/health"}:
            try:
                self.send_json(200, m3.vieneu_health())
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/tts/maziao/config":
            try:
                api_key = DEFAULT_API_KEY
                api_base = DEFAULT_API_BASE
                try:
                    api_key = os.environ.get("MAZIAO_API_KEY") or api_key
                    api_base = (os.environ.get("MAZIAO_API_BASE") or api_base).rstrip("/")
                except Exception:
                    pass
                self.send_json(200, {
                    "api_key_configured": bool(api_key),
                    "api_base": api_base,
                    "default_voice": "oncoinx",
                })
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/voices/favourites":
            try:
                _, api_base, headers = _resolve_api_config(None, DEFAULT_API_BASE)
                request = urllib.request.Request(
                    f"{api_base}/api/voices/favourites",
                    headers=headers,
                    method="GET",
                )
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.send_json(200, payload)
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/render-branding":
            try:
                config = read_branding_config()
                config["locked"] = trial_branding_required()
                if config["locked"]:
                    config.update({
                        "brandName": "aurexvideo.app",
                        "configured": False,
                        "hasLogo": False,
                        "logoName": "",
                        "logoPath": "",
                    })
                self.send_json(200, config)
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/social/upload-metadata":
            project = (parse_qs(parsed.query).get("project") or [""])[0]
            try:
                require_project(project)
                language = current_ui_language()
                payload = build_upload_metadata(project, language=language)
                default_copy = read_default_upload_copy(language)
                payload["tags"] = default_copy["tags"]
                payload["title"] = default_copy["title"]
                payload["youtubeDescription"] = default_copy["youtubeDescription"]
                payload["description"] = default_copy["youtubeDescription"]
                payload["facebookCaption"] = default_copy["facebookCaption"]
                payload["instagramCaption"] = default_copy["instagramCaption"]
                publish = payload.get("publish") if isinstance(payload.get("publish"), dict) else {}
                common_caption = str(publish.get("caption") or "").strip()
                if common_caption:
                    payload["commonCaption"] = common_caption
                    try:
                        first_line = next((line.strip() for line in common_caption.splitlines() if line.strip()), "")
                        first_line = re.sub(r"^[^\wÀ-ỹ]+", "", first_line, flags=re.UNICODE).strip()
                        if not first_line:
                            raise ValueError("Caption không có dòng đầu hợp lệ.")
                        payload["title"] = social_metadata.limit_youtube_title(first_line)
                    except ValueError:
                        payload["title"] = default_copy["title"]
                    payload["youtubeDescription"] = common_caption
                    payload["description"] = common_caption
                    payload["facebookCaption"] = common_caption
                    payload["instagramCaption"] = common_caption[:2200]
                    payload["tiktokCaption"] = common_caption[:2200]
                    payload["binanceCaption"] = common_caption
                else:
                    payload["commonCaption"] = default_copy["facebookCaption"]
                payload["defaultUploadCopy"] = default_copy
                self.send_json(200, payload)
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/social/default-tags":
            language = (parse_qs(parsed.query).get("language") or [current_ui_language()])[0]
            try:
                normalized_language = "vi" if str(language).lower() == "vi" else "en"
                self.send_json(200, read_default_upload_copy(normalized_language))
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/social/youtube/connect":
            project = (parse_qs(parsed.query).get("project") or [""])[0]
            try:
                require_project(project)
                self.send_redirect(start_youtube_oauth(project))
            except Exception as exc:
                self.send_html(400, social_callback_html("Không thể kết nối YouTube", str(exc), ok=False))
            return

        if path == "/api/social/youtube/connect-url":
            project = (parse_qs(parsed.query).get("project") or [""])[0]
            try:
                require_project(project)
                self.send_json(200, {"url": start_youtube_oauth(project)})
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if path == "/api/social/youtube/callback":
            try:
                project = finish_youtube_oauth(parse_qs(parsed.query))
                message = f"YouTube đã kết nối cho project {project}." if project else "YouTube đã kết nối."
                self.send_html(200, social_callback_html("Đã kết nối YouTube", message))
            except Exception as exc:
                self.send_html(400, social_callback_html("Kết nối YouTube thất bại", str(exc), ok=False))
            return

        if path == "/api/preview-settings":
            project = (parse_qs(parsed.query).get("project") or [""])[0]
            try:
                settings = read_preview_settings(project)
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(200, {"project": project, "settings": settings})
            return

        if path == "/api/project-script":
            project = (parse_qs(parsed.query).get("project") or [""])[0]
            try:
                result = read_project_script(project)
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(200, result)
            return

        if path == "/api/jobs":
            self.send_json(200, {"jobs": list_jobs()})
            return

        if path == "/render":
            self.send_redirect("/")
            return

        if path.startswith("/render/"):
            project = path.split("/render/", 1)[1].strip("/")
            self.send_redirect(f"/?project={quote(unquote(project))}")
            return

        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            job = get_job(job_id)
            if not job:
                self.send_json(404, {"error": "Job not found."})
                return
            self.send_json(200, job)
            return

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/api/ui-language":
            try:
                language = save_ui_language(self.read_json_body().get("language"))
                self.send_json(200, {"ok": True, "language": language})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/social/default-tags":
            try:
                self.send_json(200, save_default_upload_tags(self.read_json_body()))
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/social/upload-metadata":
            try:
                payload = self.read_json_body()
                project = require_payload_project(payload)
                project_dir = social_metadata.require_project(project)
                existing = social_metadata.read_project_upload_metadata(project_dir)
                if not existing:
                    existing = social_metadata.generated_upload_metadata(
                        project_dir,
                        social_metadata.read_script_lines(project_dir),
                        {},
                        language=current_ui_language(),
                    )
                publish = existing.get("publish") if isinstance(existing.get("publish"), dict) else {}
                publish = dict(publish)
                brand = canonical_brand(
                    payload.get("brand")
                    or payload.get("brandId")
                    or publish.get("brand")
                    or social_metadata.project_brand_from_topic(project_dir)
                )
                if brand and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", brand):
                    raise ValueError("Brand chỉ được dùng chữ thường, số, dấu chấm, gạch ngang hoặc gạch dưới.")
                caption = str(payload.get("caption") if "caption" in payload else payload.get("commonCaption") if "commonCaption" in payload else publish.get("caption") or "").strip()
                if len(caption) > 5000:
                    raise ValueError("Caption chung tối đa 5.000 ký tự.")
                publish.update({"schemaVersion": 1, "brand": brand, "caption": caption})
                existing["publish"] = publish
                social_metadata.write_project_upload_metadata(project_dir, existing)
                self.send_json(200, {"ok": True, "project": project, "publish": publish})
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/social/shopee/config":
            try:
                payload = self.read_json_body()
                brand = canonical_brand(payload.get("brand") or payload.get("brandId") or "")
                if not brand:
                    raise ValueError("Shopee Affiliate config cần Brand.")
                result = update_shopee_config(
                    str(payload.get("appId") or payload.get("app_id") or ""),
                    str(payload.get("secret") or payload.get("appSecret") or payload.get("app_secret") or ""),
                    api_base_url=str(payload.get("apiBaseUrl") or payload.get("api_base_url") or ""),
                    brand=brand,
                    connection_id=str(payload.get("connectionId") or payload.get("connection_id") or ""),
                    display_name=str(payload.get("displayName") or payload.get("display_name") or ""),
                )
                settings_payload = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
                settings = save_affiliate_settings(brand, settings_payload)
                self.send_json(200, {"ok": True, "brand": brand, "shopee": result, "settings": settings})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/social/shopee/disconnect":
            try:
                payload = self.read_json_body()
                brand = canonical_brand(payload.get("brand") or payload.get("brandId") or "")
                if not brand:
                    raise ValueError("Cần chỉ định Brand cần gỡ Shopee Affiliate.")
                self.send_json(200, disconnect_shopee(brand))
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/affiliate/settings":
            try:
                payload = self.read_json_body()
                brand = canonical_brand(payload.get("brand") or payload.get("brandId") or "")
                if not brand:
                    raise ValueError("Affiliate settings cần Brand.")
                self.send_json(200, {"ok": True, "brand": brand, "settings": save_affiliate_settings(brand, payload)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/affiliate/link":
            try:
                payload = self.read_json_body()
                project = str(payload.get("project") or payload.get("contentId") or payload.get("content_id") or "").strip()
                if payload.get("project"):
                    require_project(project)
                brand = canonical_brand(payload.get("brand") or payload.get("brandId") or "")
                if not brand:
                    raise ValueError("Affiliate link cần Brand.")
                result = create_affiliate_link(
                    brand=brand,
                    content_id=project or "affiliate-dashboard",
                    product_id=str(payload.get("productId") or payload.get("product_id") or ""),
                    origin_url=str(payload.get("originUrl") or payload.get("origin_url") or ""),
                    affiliate_url=str(payload.get("affiliateUrl") or payload.get("affiliate_url") or ""),
                    placement=str(payload.get("placement") or "first_comment"),
                    page_id=str(payload.get("pageId") or payload.get("page_id") or ""),
                    product_payload=payload.get("product") if isinstance(payload.get("product"), dict) else None,
                )
                self.send_json(200, result)
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/affiliate/poc":
            try:
                payload = self.read_json_body()
                brand = canonical_brand(payload.get("brand") or payload.get("brandId") or "")
                if not brand:
                    raise ValueError("POC cần Brand.")
                content_id = _affiliate_poc_text(payload, "contentId", "content_id", limit=128)
                if not content_id:
                    raise ValueError("POC cần Content / project ID.")
                config = read_social_config()
                page_id = _affiliate_poc_text(payload, "pageId", "page_id", limit=128)
                page_id = page_id or _affiliate_poc_default_page_id(config, brand)
                case_code = str(payload.get("caseKey") or payload.get("case_key") or payload.get("case") or "").strip().upper()
                if case_code not in AFFILIATE_POC_CASE_CODES:
                    raise ValueError("POC case phải là A, B, C hoặc D.")
                status = str(payload.get("status") or "").strip().lower()
                if status not in AFFILIATE_POC_STATUSES or status == "pending":
                    raise ValueError("POC status phải là running, passed, failed hoặc blocked.")
                post_id = _affiliate_poc_text(payload, "postId", "post_id", limit=96)
                comment_id = _affiliate_poc_text(payload, "commentId", "comment_id", limit=96)
                evidence_url = _affiliate_poc_text(payload, "evidenceUrl", "evidence_url", "evidence", limit=500)
                notes = _affiliate_poc_text(payload, "notes", "note", limit=1000)
                banner_observed = str(payload.get("bannerObserved") or payload.get("banner_observed") or "").strip().lower()
                if banner_observed not in {"", "yes", "no"}:
                    raise ValueError("Banner Affiliate chỉ nhận yes, no hoặc để trống.")
                run = start_affiliate_poc_run(
                    brand,
                    content_id,
                    idempotency_key=_affiliate_poc_idempotency_key(content_id, page_id),
                )
                result = record_affiliate_poc_result(
                    brand,
                    case_code,
                    status,
                    run_id=run["runId"],
                    content_id=content_id,
                    page_id=page_id,
                    post_id=post_id,
                    comment_id=comment_id,
                    banner_observed=None if not banner_observed else banner_observed == "yes",
                    evidence_url=evidence_url,
                    notes=notes,
                )
                self.send_json(200, _affiliate_poc_response(brand, content_id, page_id, result))
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/affiliate/conversions/import":
            try:
                payload = self.read_json_body()
                brand = canonical_brand(payload.get("brand") or payload.get("brandId") or "")
                rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
                self.send_json(200, ingest_conversion_rows(rows, brand=brand))
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/social/brand-route":
            try:
                payload = self.read_json_body()
                brand = canonical_brand(payload.get("brand") or payload.get("brandId") or "")
                platform = str(payload.get("platform") or "").strip().casefold()
                connection_id = str(
                    payload.get("connectionId")
                    or payload.get("channelId")
                    or payload.get("pageId")
                    or payload.get("accountId")
                    or ""
                ).strip()
                name = str(payload.get("name") or payload.get("displayName") or "").strip()
                if platform not in SOCIAL_ROUTE_PLATFORMS:
                    raise ValueError(f"Unsupported social platform: {platform or '<empty>'}.")

                status = social_status()
                platform_status = status.get("platforms", {}).get(platform, {})
                if platform in {"youtube", "facebook"}:
                    account_key = "channels" if platform == "youtube" else "pages"
                    accounts = platform_status.get(account_key) or []
                    account = next((item for item in accounts if str(item.get("id") or "") == connection_id), None)
                    if not account:
                        raise ValueError(f"Không tìm thấy account {platform} để gán vào Brand.")
                    name = name or str(account.get("title") or account.get("name") or connection_id)
                elif platform in {"instagram", "tiktok", "threads"}:
                    accounts = platform_status.get("accounts") or []
                    account = next(
                        (
                            item for item in accounts
                            if str(item.get("connection_id") or item.get("id") or "") == connection_id
                        ),
                        None,
                    )
                    if not account:
                        raise ValueError(f"Không tìm thấy account {platform} để gán vào Brand.")
                    account_brand = canonical_brand(account.get("brand"))
                    if account_brand != brand:
                        raise ValueError(f"Account {platform} đang thuộc brand {account_brand or 'khác'}.")
                    if not bool(account.get("connected") or account.get("available")):
                        raise ValueError(account.get("message") or f"{platform} chưa sẵn sàng.")
                    name = name or str(account.get("display_name") or account.get("name") or connection_id)
                else:
                    if not bool(platform_status.get("connected") or platform_status.get("available")):
                        raise ValueError(platform_status.get("message") or f"{platform} chưa kết nối.")
                    current_id = str(
                        platform_status.get("ig_user_id")
                        or platform_status.get("threads_user_id")
                        or platform_status.get("account_id")
                        or "global"
                    ).strip()
                    if connection_id in {"", "global"}:
                        connection_id = current_id or "global"
                    elif current_id and connection_id != current_id:
                        raise ValueError(f"Account {platform} không khớp cấu hình hiện tại.")
                    name = name or str(
                        platform_status.get("display_name")
                        or platform_status.get("name")
                        or connection_id
                    )

                routes = save_social_brand_route(brand, platform, connection_id, name=name)
                self.send_json(200, {"ok": True, "brand": brand, "platform": platform, "route": routes.get(platform, {})})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/social/brand-connection":
            try:
                payload = self.read_json_body()
                brand = canonical_brand(payload.get("brand") or payload.get("brandId") or "")
                platform = str(payload.get("platform") or "").strip().casefold()
                connection_id = str(payload.get("connectionId") or payload.get("connection_id") or "").strip()
                display_name = str(payload.get("displayName") or payload.get("name") or "").strip()
                if platform not in {"instagram", "tiktok", "threads"}:
                    raise ValueError("Brand connection chỉ hỗ trợ Instagram, TikTok và Threads.")
                if not brand:
                    raise ValueError("Brand không được để trống.")
                config = read_social_config()
                if platform == "instagram":
                    shared_r2 = resolve_r2_config(r2_config(config))
                    if not all(shared_r2.get(key) for key in ("account_id", "bucket", "access_key_id", "secret_access_key", "public_base_url")):
                        raise ValueError("Cloudflare R2 dùng chung chưa được cấu hình. Hãy lưu R2 ở cấu hình Instagram chung trước.")
                    account = update_instagram_config(
                        str(payload.get("igUserId") or payload.get("ig_user_id") or ""),
                        str(payload.get("accessToken") or payload.get("access_token") or ""),
                        str(payload.get("apiMode") or payload.get("api_mode") or "instagram_login"),
                        str(payload.get("graphVersion") or payload.get("graph_version") or "v25.0"),
                        display_name,
                        config=config,
                        persist=False,
                        brand=brand,
                        connection_id=connection_id,
                    )
                elif platform == "tiktok":
                    account = update_zernio_config(
                        str(payload.get("apiKey") or payload.get("api_key") or ""),
                        str(payload.get("accountId") or payload.get("account_id") or ""),
                        base_url=str(payload.get("baseUrl") or payload.get("base_url") or "https://zernio.com/api/v1"),
                        brand=brand,
                        connection_id=connection_id,
                        display_name=display_name,
                        config=config,
                        persist=False,
                    )
                else:
                    account = update_threads_config(
                        str(payload.get("threadsUserId") or payload.get("userId") or ""),
                        str(payload.get("accessToken") or payload.get("access_token") or ""),
                        str(payload.get("graphVersion") or payload.get("graph_version") or "v1.0"),
                        display_name,
                        brand=brand,
                        connection_id=connection_id,
                        config=config,
                        persist=False,
                    )
                write_social_config(config)
                self.send_json(200, {
                    "ok": True,
                    "brand": brand,
                    "platform": platform,
                    "connection_id": account.get("connection_id") if isinstance(account, dict) else "",
                    "account": account,
                })
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/tts/maziao/preview":
            if self.command != "POST":
                self.send_json(405, {"error": "Method not allowed."})
                return
            try:
                payload = self.read_json_body()
                preview_url = str(payload.get("preview_url") or "").strip()
                voice = str(payload.get("voice") or "oncoinx").strip()
                text = str(payload.get("text") or "").strip()
                audio_bytes = None
                if preview_url:
                    try:
                        audio_bytes = _download_audio(preview_url, headers={})
                    except Exception as exc:
                        return self.send_json(400, {"error": f"Preview download failed: {exc}"})
                else:
                    text = text[:220]
                    if len(text) < 100:
                        text = (text + " " + text)[:220]
                    voice_id, model_id = _resolve_voice(voice)
                    _, api_base, headers = _resolve_api_config(None, DEFAULT_API_BASE)
                    audio_bytes = _submit_and_poll_single(text, voice_id, model_id, api_base, headers)
                self.send_json(200, {
                    "voice": voice,
                    "voice_id": payload.get("voice_id"),
                    "text": text,
                    "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
                    "source": "preview_url" if preview_url else "tts",
                })
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/account/logout":
            try:
                request_native_command("logout")
                self.send_json(200, {"ok": True})
            except Exception as exc:
                self.send_json(500, {"error": str(exc)})
            return

        if parsed.path == "/api/settings/open":
            try:
                if os.environ.get("AUREX_NATIVE_BRIDGE") != "1":
                    raise RuntimeError("Aurex desktop bridge is not available.")
                body = self.read_json_body()
                section = str(body.get("section") or "all").strip().lower()
                if section not in {"all", "update"}:
                    section = "all"
                theme = str(body.get("theme") or "light").strip().lower()
                if theme not in {"light", "dark"}:
                    theme = "light"
                request_native_command("open-settings", section=section, theme=theme)
                self.send_json(200, {"ok": True})
            except Exception as exc:
                self.send_json(503, {"error": str(exc)})
            return

        if parsed.path == "/api/app-update/check":
            try:
                # The Aurex backup self-updates its engine via a local manifest.
                # Only fall back to the native bridge when no manifest exists
                # (i.e. running under the real AurexVideo app, not Aurex).
                manifest = load_update_manifest()
                if manifest.get("version"):
                    self.send_json(200, check_app_update())
                elif os.environ.get("AUREX_NATIVE_BRIDGE") == "1":
                    response = request_native_response("check-app-update", timeout=30)
                    self.send_json(200, response)
                else:
                    self.send_json(200, check_app_update())
            except Exception as exc:
                self.send_json(503, {"error": str(exc)})
            return

        if parsed.path == "/api/app-update/install":
            try:
                manifest = load_update_manifest()
                if manifest.get("version"):
                    result = install_app_update()
                    self.send_json(200, result)
                else:
                    response = request_native_response("install-app-update", timeout=60)
                    self.send_json(200, response)
            except Exception as exc:
                self.send_json(503, {"error": str(exc)})
            return

        if parsed.path == "/api/settings/language":
            try:
                body = self.read_json_body()
                lang = str(body.get("language") or "").strip().lower()
                if lang not in {"en", "vi"}:
                    raise ValueError("language must be 'en' or 'vi'")
                try:
                    settings = json.loads(BOOTSTRAP_SETTINGS_PATH.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    settings = {}
                settings["language"] = lang
                BOOTSTRAP_SETTINGS_PATH.write_text(
                    json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                self.send_json(200, {"ok": True, "language": lang})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/projects":
            try:
                payload = self.read_json_body()
                if not payload.get("language") and not payload.get("locale"):
                    payload["language"] = current_ui_language()
                project = m3.create_project(payload)
                self.send_json(201, {"project": project})
            except FileExistsError as exc:
                self.send_json(409, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/characters/split":
            if trial_branding_required():
                self.send_json(403, {"error": "Tài khoản Trial chỉ dùng nhân vật hieu-ham-hoc có sẵn."})
                return
            try:
                self.send_json(201, m3.split_character_sheet(self.read_json_body()))
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/images/remove-background":
            try:
                self.send_json(200, m3.remove_background_image(self.read_json_body()))
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path in {"/api/tts/vieneu/config", "/api/tts/aurextts/config"}:
            try:
                self.send_json(200, m3.update_vieneu_config(self.read_json_body()))
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/tts/vieneu/runtime":
            try:
                payload = self.read_json_body()
                if "enabled" not in payload:
                    raise ValueError("Thiếu trạng thái bật/tắt VieNeu-TTS.")
                self.send_json(200, save_vieneu_runtime_config(payload["enabled"]))
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path == "/api/characters":
            if trial_branding_required():
                self.send_json(403, {"error": "Tài khoản Trial không thể thêm nhân vật mới."})
                return
            try:
                self.send_json(201, m3.save_character(self.read_json_body()))
            except FileExistsError as exc:
                self.send_json(409, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        upload_match = re.fullmatch(r"/api/projects/([^/]+)/upload", parsed.path)
        if upload_match:
            try:
                payload = self.read_json_body()
                result = m3.decode_upload(m3.validate_slug(upload_match.group(1)), payload)
                self.send_json(201, result)
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return

        if parsed.path != "/api/render":
            if parsed.path == "/api/license/checkout":
                try:
                    payload = self.read_json_body()
                    locale = "vi" if str(payload.get("locale") or "").lower() == "vi" else "en"
                    plan = "monthly" if str(payload.get("plan") or "").lower() == "monthly" else "yearly"
                    result = request_native_response("license-checkout", timeout=50, locale=locale, plan=plan)
                except RuntimeError as exc:
                    self.send_json(500, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/open-external":
                try:
                    result = open_external_url(self.read_json_body())
                except RuntimeError as exc:
                    self.send_json(500, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/preview-settings":
                try:
                    payload = self.read_json_body()
                    result = write_preview_settings(payload)
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/render-branding":
                try:
                    payload = self.read_json_body()
                    result = save_branding_config(payload)
                except PermissionError as exc:
                    self.send_json(403, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/ocr":
                try:
                    payload = self.read_json_body()
                    image_path = Path(str(payload.get("imagePath") or payload.get("image_path") or payload.get("path") or "")).expanduser()
                    if not image_path.is_absolute():
                        image_path = (REPO_ROOT / image_path).resolve()
                    prompt = str(payload.get("prompt") or "").strip() or None
                    device = str(payload.get("device") or "auto").strip().lower()
                    model = str(payload.get("model") or "").strip() or None
                    output_dir = str(payload.get("outputDir") or payload.get("output_dir") or "").strip() or None
                    cmd = [str(RENDER_PYTHON), str(OCR_TOOL_PATH), str(image_path), "--device", device]
                    if prompt:
                        cmd.extend(["--prompt", prompt])
                    if model:
                        cmd.extend(["--model", model])
                    if output_dir:
                        cmd.extend(["--output-dir", output_dir])
                    proc = subprocess.run(
                        cmd,
                        cwd=str(REPO_ROOT),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=900,
                    )
                    if proc.returncode != 0:
                        raise RuntimeError((proc.stderr or proc.stdout or "OCR failed").strip())
                    data = json.loads(proc.stdout or "{}")
                    self.send_json(200, data)
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                return

            if parsed.path == "/api/source-root":
                try:
                    if has_active_jobs():
                        raise RuntimeError("Đang có job render chạy, đợi xong rồi đổi source folder.")
                    payload = self.read_json_body()
                    source_root = str(payload.get("sourceRoot") or payload.get("source_root") or "").strip()
                    if not source_root:
                        raise ValueError("Missing source root.")
                    configure_source_root(source_root)
                    result = source_root_response()
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/source-root/select":
                try:
                    if has_active_jobs():
                        raise RuntimeError("Đang có job render chạy, đợi xong rồi đổi source folder.")
                    selected_root = choose_source_root_dialog()
                    configure_source_root(selected_root)
                    result = source_root_response()
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/preview-bgm":
                try:
                    payload = self.read_json_body()
                    result = upload_preview_bgm(payload)
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/tts/elevenlabs/config":
                try:
                    payload = self.read_json_body()
                    api_key = str(payload.get("api_key") or "").strip()
                    voice_id = str(payload.get("voice_id") or "").strip()
                    if api_key:
                        result = update_elevenlabs_api_key(api_key)
                    elif voice_id:
                        result = update_elevenlabs_voice_id(voice_id)
                    else:
                        raise ValueError("Missing ElevenLabs voice_id or api_key.")
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/project-script":
                try:
                    payload = self.read_json_body()
                    result = write_project_script(payload)
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
                job_id = parsed.path.split("/api/jobs/", 1)[1].rsplit("/cancel", 1)[0].strip("/")
                try:
                    result = cancel_job(job_id)
                    if not result:
                        self.send_json(404, {"error": "Job not found."})
                        return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/output/delete":
                try:
                    payload = self.read_json_body()
                    result = delete_project_output(payload)
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/project/delete":
                try:
                    payload = self.read_json_body()
                    result = delete_project(payload)
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/project/rename":
                try:
                    payload = self.read_json_body()
                    result = rename_project(payload)
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except FileExistsError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/project/duplicate":
                try:
                    payload = self.read_json_body()
                    result = duplicate_project(payload)
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except FileExistsError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/output/reveal":
                try:
                    payload = self.read_json_body()
                    result = reveal_project_output(payload)
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/youtube/upload":
                try:
                    payload = self.read_json_body()
                    require_payload_project(payload)
                    result = youtube_upload_video(payload)
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/youtube/active-channel":
                try:
                    payload = self.read_json_body()
                    result = set_youtube_active_channel(str(payload.get("channelId") or payload.get("channel_id") or ""))
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/youtube/config":
                try:
                    payload = self.read_json_body()
                    result = update_youtube_oauth_config(
                        str(payload.get("clientId") or payload.get("client_id") or ""),
                        str(payload.get("clientSecret") or payload.get("client_secret") or ""),
                        str(payload.get("redirectUri") or payload.get("redirect_uri") or ""),
                    )
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/facebook/active-page":
                try:
                    payload = self.read_json_body()
                    result = set_facebook_active_page(str(payload.get("pageId") or payload.get("page_id") or ""))
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/facebook/config":
                try:
                    payload = self.read_json_body()
                    result = update_facebook_page_config(
                        str(payload.get("pageId") or payload.get("page_id") or ""),
                        str(payload.get("pageAccessToken") or payload.get("page_access_token") or ""),
                    )
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/facebook/upload":
                try:
                    payload = self.read_json_body()
                    require_payload_project(payload)
                    result = facebook_upload_video(payload)
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/tiktok/config":
                try:
                    payload = self.read_json_body()
                    result = update_zernio_config(
                        str(payload.get("apiKey") or ""),
                        str(payload.get("accountId") or ""),
                        base_url=str(payload.get("baseUrl") or "https://zernio.com/api/v1"),
                        display_name=str(payload.get("displayName") or payload.get("display_name") or ""),
                    )
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/tiktok/disconnect":
                try:
                    result = disconnect_zernio()
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/tiktok/upload":
                try:
                    payload = self.read_json_body()
                    require_payload_project(payload)
                    result = tiktok_upload_video(payload)
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/instagram/config":
                try:
                    payload = self.read_json_body()
                    config = read_social_config()
                    r2_values = merge_r2_config_values(payload, resolve_r2_config(r2_config(config)))
                    r2_result = update_r2_config(
                        r2_values["account_id"],
                        r2_values["bucket"],
                        r2_values["access_key_id"],
                        r2_values["secret_access_key"],
                        r2_values["public_base_url"],
                        r2_values["region"],
                        r2_values["object_prefix"],
                        r2_values["retain_media"],
                        config=config,
                        persist=False,
                    )
                    instagram_result = update_instagram_config(
                        str(payload.get("igUserId") or payload.get("ig_user_id") or ""),
                        str(payload.get("accessToken") or payload.get("access_token") or ""),
                        str(payload.get("apiMode") or payload.get("api_mode") or "instagram_login"),
                        str(payload.get("graphVersion") or payload.get("graph_version") or "v25.0"),
                        str(payload.get("displayName") or payload.get("display_name") or ""),
                        config=config,
                        persist=False,
                    )
                    write_social_config(config)
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, {"ok": True, "instagram": instagram_result, "r2": r2_result})
                return

            if parsed.path == "/api/social/instagram/disconnect":
                try:
                    result = disconnect_instagram()
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/threads/config":
                try:
                    payload = self.read_json_body()
                    result = update_threads_config(
                        str(payload.get("threadsUserId") or payload.get("userId") or ""),
                        str(payload.get("accessToken") or payload.get("access_token") or ""),
                        str(payload.get("graphVersion") or payload.get("graph_version") or "v1.0"),
                        str(payload.get("displayName") or payload.get("display_name") or ""),
                    )
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, {"ok": True, "threads": result})
                return

            if parsed.path == "/api/social/threads/disconnect":
                try:
                    result = disconnect_threads()
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/instagram/upload":
                try:
                    payload = self.read_json_body()
                    require_payload_project(payload)
                    result = instagram_upload_video(payload)
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/threads/upload":
                try:
                    payload = self.read_json_body()
                    require_payload_project(payload)
                    result = threads_upload_video(payload)
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/publish-all":
                try:
                    payload = self.read_json_body()
                    require_payload_project(payload)
                    result = publish_instagram_facebook_threads(payload)
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except ValueError as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                response_status = 200 if result.get("ok") or result.get("partial") else 409
                self.send_json(response_status, result)
                return

            if parsed.path == "/api/social/facebook/comment-source":
                try:
                    payload = self.read_json_body()
                    require_payload_project(payload)
                    result = facebook_comment_source(payload)
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/binance/config":
                try:
                    payload = self.read_json_body()
                    api_key = str(payload.get("apiKey") or "").strip()
                    if not api_key:
                        result = disconnect_binance()
                    else:
                        result = update_binance_config(api_key)
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/binance/disconnect":
                try:
                    result = disconnect_binance()
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            if parsed.path == "/api/social/binance/upload":
                try:
                    payload = self.read_json_body()
                    require_payload_project(payload)
                    duration = float(payload.get("duration") or 0)
                    if not duration:
                        raise ValueError("Binance video upload requires duration.")
                    result = binance_upload_video(payload)
                except FileNotFoundError as exc:
                    self.send_json(404, {"error": str(exc)})
                    return
                except RuntimeError as exc:
                    self.send_json(409, {"error": str(exc)})
                    return
                except Exception as exc:
                    self.send_json(400, {"error": str(exc)})
                    return
                self.send_json(200, result)
                return

            self.send_json(404, {"error": "Unknown endpoint."})
            return

        try:
            payload = self.read_json_body()
            job = create_job(payload)
        except RuntimeError as exc:
            self.send_json(409, {"error": str(exc)})
            return
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})
            return

        self.send_json(202, job)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        character_match = re.fullmatch(r"/api/characters/([^/]+)", path)
        if character_match:
            if trial_branding_required():
                self.send_json(403, {"error": "Thư viện nhân vật Trial là chỉ đọc."})
                return
            try:
                payload = self.read_json_body()
                result = m3.update_character(unquote(character_match.group(1)), payload)
                self.send_json(200, result)
            except FileNotFoundError as exc:
                self.send_json(404, {"error": str(exc)})
            except FileExistsError as exc:
                self.send_json(409, {"error": str(exc)})
            except Exception as exc:
                self.send_json(400, {"error": str(exc)})
            return
        match = re.fullmatch(r"/api/projects/([^/]+)/topic", path)
        if not match:
            self.send_json(404, {"error": "Unknown endpoint."})
            return
        try:
            payload = self.read_json_body()
            slug = m3.validate_slug(match.group(1))
            topic = m3.save_topic(slug, payload)
            self.send_json(200, {"project": slug, "topic": topic})
        except FileNotFoundError as exc:
            self.send_json(404, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        character_match = re.fullmatch(r"/api/characters/([^/]+)", path)
        if not character_match:
            self.send_json(404, {"error": "Unknown endpoint."})
            return
        if trial_branding_required():
            self.send_json(403, {"error": "Không thể xoá nhân vật mặc định của Trial."})
            return
        try:
            result = m3.delete_character(unquote(character_match.group(1)))
            self.send_json(200, result)
        except FileNotFoundError as exc:
            self.send_json(404, {"error": str(exc)})
        except RuntimeError as exc:
            self.send_json(409, {"error": str(exc)})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})


def parse_version(v: str) -> tuple:
    """Parse a semver-ish string into a comparable tuple."""
    parts = []
    for chunk in str(v or "").split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def load_update_manifest() -> dict:
    """Read the central update manifest (GitHub raw URL) if reachable,
    otherwise fall back to the local update-manifest.json.
    """
    import urllib.request

    # 1) Try central URL first (so any user gets the latest release).
    try:
        req = urllib.request.Request(
            UPDATE_MANIFEST_URL, headers={"User-Agent": "AurexUpdater/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read().decode("utf-8")
        central = json.loads(data)
        if isinstance(central, dict) and central.get("version"):
            return central
    except Exception:
        pass

    # 2) Fallback to local file (offline / private builds).
    try:
        return json.loads(UPDATE_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def check_app_update() -> dict:
    """Compare manifest version against current APP_VERSION.

    Returns a dict shaped like the native bridge stub expects:
    {ok, available, currentVersion, latestVersion, notes, source}
    """
    manifest = load_update_manifest()
    latest = str(manifest.get("version") or "")
    notes = str(manifest.get("notes") or "")
    source = str(manifest.get("source") or "")
    available = bool(latest) and parse_version(latest) > parse_version(APP_VERSION)
    return {
        "ok": True,
        "available": available,
        "currentVersion": APP_VERSION,
        "latestVersion": latest or APP_VERSION,
        "notes": notes,
        "source": source,
    }


def install_app_update() -> dict:
    """Apply an AurexVideo engine update:
    - Preferred: delta payload (changed/added tar.gz + explicit deleted list).
    - Fallback: full engine ZIP for backward-compatible `source` manifests.

    Only the engine (web_server.py, webui, assets, ...) is replaced — the
    Tauri binary is never touched.
    """
    manifest = load_update_manifest()
    source = str(manifest.get("source") or "").strip()
    protocol = str(manifest.get("protocol") or "").strip()

    def _acquire_bytes(loc):
        if isinstance(loc, str) and loc.startswith(("http://", "https://")):
            req = urllib.request.Request(loc, headers={"User-Agent": "AurexUpdater/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        if isinstance(loc, str) and loc.startswith("file://"):
            local = Path(loc[len("file://"):]).expanduser()
            return local.read_bytes()
        if isinstance(loc, str) and loc:
            return Path(loc).expanduser().read_bytes()
        return b""

    if protocol == "aurexvideo-delta-v1":
        tar_src = str(manifest.get("deltaSource") or "").strip() or source.replace(".zip", ".tar.gz")
        data = _acquire_bytes(tar_src)

        # Validate tarball
        try:
            tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
            members = [m.name for m in tf.getmembers() if m.isfile()]
            tf.close()
        except Exception as exc:
            raise ValueError(f"delta payload is not a valid tar.gz: {exc}")

        # Backup current engine, then apply over it
        backup_dir = REPO_ROOT.with_name(REPO_ROOT.name + ".bak")
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.copytree(REPO_ROOT, backup_dir)

        apply_root = REPO_ROOT
        try:
            tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
            tf.extractall(apply_root)
            tf.close()

            # Explicit deletions
            deleted = list(manifest.get("deleted") or [])
            allowed_base = REPO_ROOT.resolve()
            for rel in deleted:
                target = (allowed_base / str(rel)).resolve()
                if str(target).startswith(str(allowed_base)) and target.is_file():
                    target.unlink()
                # ignore dirs for safety; users can delete dirs manually
        except Exception as exc:
            shutil.rmtree(apply_root, ignore_errors=True)
            shutil.copytree(backup_dir, apply_root)
            raise RuntimeError(f"delta apply failed, rolled back: {exc}")
    else:
        # Backward-compatible full ZIP fallback
        if not source:
            raise ValueError("manifest missing 'source' (engine upgrade zip)")

        import io
        import zipfile

        if source.startswith("http://") or source.startswith("https://"):
            req = urllib.request.Request(source, headers={"User-Agent": "AurexUpdater/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
        elif source.startswith("file://"):
            local = Path(source[len("file://"):]).expanduser()
            data = local.read_bytes()
        else:
            local = Path(source).expanduser()
            data = local.read_bytes()

        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
            bad = zf.testzip()
            if bad is not None:
                raise ValueError(f"corrupt zip member: {bad}")
        except zipfile.BadZipFile as exc:
            raise ValueError(f"source is not a valid zip: {exc}")

        backup_dir = REPO_ROOT.with_name(REPO_ROOT.name + ".bak")
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.copytree(REPO_ROOT, backup_dir)

        extract_root = REPO_ROOT
        try:
            zf.extractall(extract_root)
        except Exception as exc:
            shutil.rmtree(extract_root, ignore_errors=True)
            shutil.copytree(backup_dir, extract_root)
            raise RuntimeError(f"extract failed, rolled back: {exc}")

    # Mark as installed: drop the 'version' key so re-check shows no update,
    # and record what was installed for reference.
    try:
        installed = manifest.get("version")
        manifest["installedVersion"] = installed
        manifest.pop("version", None)
        manifest.pop("source", None)
        UPDATE_MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "installedVersion": installed,
            "restartRequired": True,
        }
    except OSError:
        pass
    return {
        "ok": True,
        "installedVersion": None,
        "restartRequired": True,
    }


def main() -> None:
    # The UI depends on the project's local packages (Pillow, Whisper,
    # Playwright). Re-launch under this project's venv even when the user
    # starts it with a system `python web_server.py` command.
    if VENV_PYTHON.exists() and os.environ.get("AUREX_VENV_REEXEC") != "1":
        try:
            # Compare against the venv root, not the resolved framework binary
            # behind `.venv/bin/python` (that comparison falsely looks "in venv").
            in_project_venv = Path(sys.prefix).resolve() == VENV_ROOT
        except OSError:
            in_project_venv = False
        if not in_project_venv:
            environment = {**os.environ, "AUREX_VENV_REEXEC": "1"}
            os.execve(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]], environment)
    parser = argparse.ArgumentParser(description="Start the local project render web UI.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--source-root",
        "--project-root",
        default=None,
        help="Folder chứa các project, hoặc chính một project folder có topic.json.",
    )
    args = parser.parse_args()

    # Seed default characters bundled with the engine so the app always
    # has at least one character available on first launch.
    try:
        from aurexvideo_paths import ensure_user_layout
        ensure_user_layout()
        m3._seed_default_characters()
        print(color_text("   ✓ Character mặc định đã sẵn sàng", "green"))
    except Exception as exc:
        print(color_text(f"   ⚠ Không seed được character mặc định: {exc}", "yellow"))

    try:
        source_root = args.source_root or os.environ.get("AUREX_SOURCE_ROOT") or os.environ.get("AUREX_PROJECT_ROOT")
        configure_source_root(source_root or DEFAULT_PROJECT_ROOT)
    except Exception as exc:
        parser.error(str(exc))

    start_scheduler()
    server = ThreadingHTTPServer((args.host, args.port), WebHandler)
    actual_port = int(server.server_address[1])
    url = f"http://localhost:{actual_port}"
    os.environ["AUREX_SERVER_ORIGIN"] = url
    print()
    print(color_text("🚀  Bộ máy render project local", "bold", "green"))
    print(f"   {color_text('🌐 Dashboard', 'cyan')}: {color_text(url, 'underline', 'bold')}")
    print(f"   {color_text('📂 Workspace', 'cyan')}: {REPO_ROOT}")
    print(f"   {color_text('🗂 Source root', 'cyan')}: {PROJECT_ROOT} ({source_root_mode()})")
    print(f"   {color_text('■ Dừng server', 'yellow')}: Ctrl+C")
    restart_cmd = r".\install.cmd" if sys.platform.startswith("win") else "./run_webui.sh"
    print(f"   {color_text('↻ Chạy lại server', 'yellow')}: {restart_cmd}")
    print()
    sys.stdout.flush()
    # Không tự mở trình duyệt khi chạy bên trong desktop app (Swift đã nhúng WKWebView).
    # Chỉ mở browser mặc định khi chạy standalone (dev / dòng lệnh).
    if not embedded_desktop_mode_enabled():
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{color_text('👋 Đã dừng server.', 'yellow')}")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
