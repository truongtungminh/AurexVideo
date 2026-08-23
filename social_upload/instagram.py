from __future__ import annotations

import os
import re
import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from .config import (
    canonical_brand,
    read_social_config,
    resolve_social_brand_connection,
    store_social_brand_connection,
    write_social_config,
)
from .http import http_form_request, http_get_request
from .metadata import (
    build_upload_metadata,
    final_video_path_for_project,
    record_scheduled_social_upload,
    record_social_upload,
    require_project,
    validate_upload_video,
    upload_brand_for_project,
)
from .r2 import (
    delete_file,
    r2_config,
    r2_config_hint,
    r2_is_configured,
    resolve_r2_config,
    upload_file,
)
from .schedule import parse_scheduled_publish_at, validate_schedule_window
from .scheduler import schedule_upload
from .remote_worker import schedule_on_vps


DEFAULT_INSTAGRAM_GRAPH_VERSION = "v25.0"
DEFAULT_INSTAGRAM_API_MODE = "instagram_login"
DEFAULT_POLL_ATTEMPTS = 60
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
MAX_INSTAGRAM_CAPTION_LENGTH = 2200
MAX_INSTAGRAM_REEL_BYTES = 300 * 1024 * 1024
INSTAGRAM_API_HOSTS = {
    "instagram_login": "https://graph.instagram.com",
    "facebook_login": "https://graph.facebook.com",
}


def instagram_config(config: dict | None = None) -> dict:
    config = read_social_config() if config is None else config
    value = config.get("instagram", {}) if isinstance(config, dict) else {}
    return value if isinstance(value, dict) else {}


def instagram_graph_version(instagram: dict) -> str:
    version = str(instagram.get("graph_version") or DEFAULT_INSTAGRAM_GRAPH_VERSION).strip()
    return version if version.startswith("v") else f"v{version}"


def instagram_api_mode(instagram: dict) -> str:
    mode = str(instagram.get("api_mode") or DEFAULT_INSTAGRAM_API_MODE).strip().lower()
    return mode if mode in INSTAGRAM_API_HOSTS else DEFAULT_INSTAGRAM_API_MODE


def instagram_user_id(instagram: dict) -> str:
    env_value = "" if instagram.get("_brand_connection") else os.environ.get("INSTAGRAM_IG_USER_ID")
    return str(env_value or instagram.get("ig_user_id") or "").strip()


def instagram_access_token(instagram: dict) -> str:
    env_value = "" if instagram.get("_brand_connection") else os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    return str(env_value or instagram.get("access_token") or "").strip()


def instagram_is_configured(instagram: dict | None = None) -> bool:
    value = instagram if isinstance(instagram, dict) else instagram_config()
    return bool(instagram_user_id(value) and instagram_access_token(value))


def instagram_is_ready(instagram: dict | None = None, r2: dict | None = None) -> bool:
    return instagram_is_configured(instagram) and r2_is_configured(r2)


def instagram_config_hint(instagram: dict | None = None, r2: dict | None = None) -> str:
    if not instagram_is_configured(instagram):
        return "Instagram chưa cấu hình. Nhập IG User ID và access token."
    if not r2_is_configured(r2):
        return r2_config_hint()
    return ""


def update_instagram_config(
    ig_user_id: str,
    access_token: str,
    api_mode: str = DEFAULT_INSTAGRAM_API_MODE,
    graph_version: str = DEFAULT_INSTAGRAM_GRAPH_VERSION,
    display_name: str = "",
    *,
    config: dict | None = None,
    persist: bool = True,
    brand: str = "",
    connection_id: str = "",
) -> dict:
    ig_user_id = str(ig_user_id or "").strip()
    access_token = str(access_token or "").strip()
    api_mode = str(api_mode or DEFAULT_INSTAGRAM_API_MODE).strip().lower()
    graph_version = str(graph_version or DEFAULT_INSTAGRAM_GRAPH_VERSION).strip()
    if not ig_user_id:
        raise ValueError("Instagram IG User ID không được để trống.")
    if not re.fullmatch(r"[0-9]+", ig_user_id):
        raise ValueError("Instagram IG User ID phải là chuỗi số.")
    if len(access_token) < 20 or re.search(r"\s", access_token):
        raise ValueError("Instagram access token có vẻ không hợp lệ.")
    if api_mode not in INSTAGRAM_API_HOSTS:
        raise ValueError("Instagram API mode không hợp lệ.")
    if not re.fullmatch(r"v?\d+\.\d+", graph_version):
        raise ValueError("Instagram Graph API version phải có dạng v25.0.")

    config = read_social_config() if config is None else config
    value = {
        "ig_user_id": ig_user_id,
        "access_token": access_token,
        "api_mode": api_mode,
        "graph_version": graph_version if graph_version.startswith("v") else f"v{graph_version}",
        "display_name": str(display_name or "").strip(),
    }
    brand = canonical_brand(brand)
    if brand:
        saved_id = store_social_brand_connection(
            config,
            brand,
            "instagram",
            value,
            connection_id=connection_id,
            name=display_name,
        )
        if persist:
            write_social_config(config)
        result = instagram_status({**value, "_brand_connection": True}, r2_config(config))
        result.update({"brand": brand, "connection_id": saved_id})
        return result
    instagram = instagram_config(config)
    instagram.update(value)
    config["instagram"] = instagram
    if persist:
        write_social_config(config)
    return instagram_status(instagram, r2_config(config))


def disconnect_instagram() -> dict:
    config = read_social_config()
    section = config.get("instagram")
    connections = section.get("connections") if isinstance(section, dict) else None
    if isinstance(connections, dict) and connections:
        config["instagram"] = {"connections": connections}
    else:
        config.pop("instagram", None)
    write_social_config(config)
    return {"ok": True, "configured": False, "connected": False}


def instagram_status(instagram: dict | None = None, r2: dict | None = None) -> dict:
    value = instagram if isinstance(instagram, dict) else instagram_config()
    r2_value = r2 if isinstance(r2, dict) else r2_config()
    configured = instagram_is_configured(value)
    r2_configured = r2_is_configured(r2_value)
    ready = configured and r2_configured
    return {
        "configured": configured,
        "connected": ready,
        "available": ready,
        "ready": ready,
        "ig_user_id": instagram_user_id(value),
        "display_name": str(value.get("display_name") or value.get("name") or "").strip(),
        "connection_id": str(value.get("_connection_id") or value.get("connection_id") or "").strip(),
        "api_mode": instagram_api_mode(value),
        "graph_version": instagram_graph_version(value),
        "r2": {
            "configured": r2_configured,
            "bucket": resolve_r2_config(r2_value).get("bucket", ""),
            "public_base_url": resolve_r2_config(r2_value).get("public_base_url", ""),
            "object_prefix": resolve_r2_config(r2_value).get("object_prefix", "instagram"),
            "retain_media": bool(resolve_r2_config(r2_value).get("retain_media")),
        },
        "message": "" if ready else instagram_config_hint(value, r2_value),
    }


def instagram_api_url(instagram: dict, path: str) -> str:
    base = INSTAGRAM_API_HOSTS[instagram_api_mode(instagram)].rstrip("/")
    version = instagram_graph_version(instagram)
    return f"{base}/{quote(version, safe='')}/{str(path).lstrip('/')}"


def instagram_caption_for_project(project: str, fallback_caption: str) -> str:
    require_project(project)
    caption = str(fallback_caption or "").strip()
    if not caption:
        metadata = build_upload_metadata(project)
        caption = str(metadata.get("instagramCaption") or metadata.get("facebookCaption") or "").strip()
    return caption


def instagram_object_key(project: str, r2: dict) -> str:
    safe_project = re.sub(r"[^A-Za-z0-9._-]+", "-", str(project or "")).strip("-") or "project"
    prefix = str(resolve_r2_config(r2).get("object_prefix") or "instagram").strip("/")
    return f"{prefix}/{safe_project}/{secrets.token_hex(8)}.mp4"


def instagram_container_status(instagram: dict, container_id: str, access_token: str) -> dict:
    return http_get_request(
        instagram_api_url(instagram, container_id),
        {
            "fields": "status_code,status",
            "access_token": access_token,
        },
    )


def wait_for_instagram_container(
    instagram: dict,
    container_id: str,
    access_token: str,
    attempts: int = DEFAULT_POLL_ATTEMPTS,
    delay_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict:
    attempts = max(1, min(int(attempts), 240))
    delay_seconds = max(1.0, min(float(delay_seconds), 60.0))
    last = {}
    for attempt in range(attempts):
        last = instagram_container_status(instagram, container_id, access_token)
        status_code = str(last.get("status_code") or "").upper()
        if status_code == "FINISHED":
            return last
        if status_code in {"ERROR", "EXPIRED"}:
            detail = str(last.get("status") or status_code)
            raise RuntimeError(f"Instagram media container failed: {detail}")
        if attempt < attempts - 1:
            time.sleep(delay_seconds)
    status = str(last.get("status") or "unknown")
    raise RuntimeError(f"Instagram media container timed out after {attempts} checks: {status}")


def instagram_media_metadata(instagram: dict, media_id: str, access_token: str) -> dict:
    return http_get_request(
        instagram_api_url(instagram, media_id),
        {
            "fields": "id,permalink,media_type",
            "access_token": access_token,
        },
    )


def instagram_upload_video(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    project_dir = require_project(project)
    brand = upload_brand_for_project(payload, project_dir, "Instagram")
    payload = {**payload, "brand": brand}
    config = read_social_config()
    connection_id, instagram = resolve_social_brand_connection(config, brand, "instagram")
    r2 = r2_config(config)
    if not instagram_is_configured(instagram) or not r2_is_configured(r2):
        raise ValueError(instagram_config_hint(instagram, r2))
    scheduled = parse_scheduled_publish_at(payload)
    video_path = final_video_path_for_project(project)
    validate_upload_video(video_path)
    if video_path.stat().st_size > MAX_INSTAGRAM_REEL_BYTES:
        raise ValueError("Instagram Reels chỉ nhận video tối đa 300 MB.")
    caption = instagram_caption_for_project(project, str(payload.get("instagramCaption") or ""))
    if len(caption) > MAX_INSTAGRAM_CAPTION_LENGTH:
        raise ValueError("Instagram caption tối đa 2.200 ký tự.")
    if scheduled:
        validate_schedule_window(scheduled, timedelta(minutes=10), platform="Instagram")
        queued = schedule_on_vps("instagram", video_path, caption, scheduled)
        record_scheduled_social_upload(
            project_dir,
            "instagram",
            queued["scheduledPublishAt"],
            brand=brand,
            connection_id=connection_id,
        )
        return {"ok": True, "platform": "instagram", "project": project, "brand": brand, "connection_id": connection_id, "state": "SCHEDULED", "scheduledPublishAt": queued["scheduledPublishAt"], "schedule_id": queued["id"], "worker_id": queued.get("id"), "message": "Đã chuyển lịch Instagram lên VPS; worker sẽ publish đúng giờ."}

    access_token = instagram_access_token(instagram)
    object_key = instagram_object_key(project, r2)
    public_url = upload_file(video_path, object_key, "video/mp4", r2)
    container_id = ""
    published_id = ""
    cleanup_error = ""
    try:
        create_data = http_form_request(
            instagram_api_url(instagram, instagram_user_id(instagram) + "/media"),
            {
                "media_type": "REELS",
                "video_url": public_url,
                "caption": caption,
                "share_to_feed": "true",
                "access_token": access_token,
            },
        )
        container_id = str(create_data.get("id") or "").strip()
        if not container_id:
            raise RuntimeError(f"Instagram container creation did not return an id: {create_data}")

        wait_for_instagram_container(
            instagram,
            container_id,
            access_token,
            attempts=int(instagram.get("poll_attempts") or DEFAULT_POLL_ATTEMPTS),
            delay_seconds=float(instagram.get("poll_interval_seconds") or DEFAULT_POLL_INTERVAL_SECONDS),
        )
        publish_data = http_form_request(
            instagram_api_url(instagram, instagram_user_id(instagram) + "/media_publish"),
            {
                "creation_id": container_id,
                "access_token": access_token,
            },
        )
        published_id = str(publish_data.get("id") or "").strip()
        if not published_id:
            raise RuntimeError(f"Instagram publish did not return a media id: {publish_data}")
    except Exception:
        raise
    finally:
        if published_id and not resolve_r2_config(r2).get("retain_media"):
            try:
                delete_file(object_key, r2)
            except Exception as exc:  # The post is already published; expose cleanup without masking success.
                cleanup_error = str(exc)

    permalink = ""
    try:
        media_data = instagram_media_metadata(instagram, published_id, access_token)
        permalink = str(media_data.get("permalink") or "").strip()
    except RuntimeError:
        permalink = ""
    details = {
        "url": permalink,
        "video_id": container_id,
        "post_id": published_id,
        "state": "PUBLISHED",
        "r2_key": object_key,
        "r2_url": public_url,
    }
    if brand:
        details["brand"] = brand
    details["connection_id"] = connection_id
    record_social_upload(project, "instagram", details)
    message = "Đã đăng Instagram Reels."
    if cleanup_error:
        message += f" Không xóa được file tạm trên R2: {cleanup_error}"
    return {
        "ok": True,
        "platform": "instagram",
        "project": project,
        "brand": brand,
        "connection_id": connection_id,
        "ig_user_id": instagram_user_id(instagram),
        "container_id": container_id,
        "media_id": published_id,
        "url": permalink,
        "r2_url": public_url,
        "message": message,
        "cleanup_error": cleanup_error,
    }
