from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import read_social_config, write_social_config
from .metadata import (
    build_upload_metadata,
    final_video_path_for_project,
    read_expected_video_bytes,
    record_social_upload,
)
from .schedule import parse_scheduled_publish_at, validate_schedule_window

ZERNIO_BASE_URL = "https://zernio.com/api/v1"
MAX_TIKTOK_VIDEO_BYTES = 500 * 1024 * 1024
MAX_TIKTOK_CAPTION_LENGTH = 2200


def zernio_config(config: dict | None = None) -> dict:
    config = read_social_config() if config is None else config
    value = config.get("zernio", {}) if isinstance(config, dict) else {}
    return value if isinstance(value, dict) else {}


def resolve_zernio_config(value: dict | None = None) -> dict:
    saved = value if isinstance(value, dict) else zernio_config()
    return {
        "api_key": str(os.environ.get("ZERNIO_API_KEY") or saved.get("api_key") or "").strip(),
        "account_id": str(os.environ.get("ZERNIO_TIKTOK_ACCOUNT_ID") or saved.get("account_id") or "").strip(),
        "base_url": str(os.environ.get("ZERNIO_BASE_URL") or saved.get("base_url") or ZERNIO_BASE_URL).strip().rstrip("/"),
    }


def zernio_is_configured(value: dict | None = None) -> bool:
    config = resolve_zernio_config(value)
    return bool(config["api_key"] and config["account_id"])


def zernio_config_hint() -> str:
    return "Chưa cấu hình Zernio. Nhập API key và TikTok account ID."


def zernio_status(value: dict | None = None) -> dict:
    config = resolve_zernio_config(value)
    key = config["api_key"]
    masked = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else ("đã cấu hình" if key else "")
    return {
        "configured": bool(config["api_key"]),
        "connected": zernio_is_configured(value),
        "account_id": config["account_id"],
        "masked_api_key": masked,
        "base_url": config["base_url"],
        "message": "" if zernio_is_configured(value) else zernio_config_hint(),
    }


def update_zernio_config(api_key: str, account_id: str, *, base_url: str = ZERNIO_BASE_URL) -> dict:
    api_key = str(api_key or "").strip()
    account_id = str(account_id or "").strip()
    base_url = str(base_url or ZERNIO_BASE_URL).strip().rstrip("/")
    if not api_key:
        raise ValueError("Zernio API key không được để trống.")
    if not account_id:
        raise ValueError("TikTok account ID của Zernio không được để trống.")
    if not base_url.startswith("https://"):
        raise ValueError("Zernio API URL phải bắt đầu bằng https://.")
    config = read_social_config()
    config["zernio"] = {"api_key": api_key, "account_id": account_id, "base_url": base_url}
    write_social_config(config)
    return {"ok": True, **zernio_status(config)}


def disconnect_zernio() -> dict:
    config = read_social_config()
    config.pop("zernio", None)
    write_social_config(config)
    return {"ok": True, "configured": False, "connected": False}


def _json_request(url: str, method: str, body: dict | None, config: dict, headers: dict | None = None) -> dict:
    encoded = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    request_headers = {"Authorization": f"Bearer {config['api_key']}", "Accept": "application/json"}
    if encoded is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    request = Request(url, data=encoded, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"Zernio HTTP {exc.code}: {detail[:800]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Zernio request failed: {exc}") from exc
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Zernio trả về dữ liệu JSON không hợp lệ.") from exc
    return parsed if isinstance(parsed, dict) else {}


def _put_file(url: str, video_path: Path) -> None:
    request = Request(url, data=video_path.read_bytes(), headers={"Content-Type": "video/mp4"}, method="PUT")
    try:
        with urlopen(request, timeout=600) as response:
            response.read()
    except (HTTPError, URLError) as exc:
        detail = exc.read().decode("utf-8", "replace") if isinstance(exc, HTTPError) else str(exc)
        raise RuntimeError(f"Zernio media upload failed: {detail[:800]}") from exc


def _unwrap(value: dict, key: str) -> object:
    data = value.get("data") if isinstance(value.get("data"), dict) else value
    return data.get(key) if isinstance(data, dict) else None


def _post_url(response: dict) -> str:
    post = _unwrap(response, "post")
    if not isinstance(post, dict):
        post = response
    for key in ("platformPostUrl", "url", "permalink"):
        value = post.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
        if isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, str) and nested.startswith("http"):
                    return nested
    return ""


def tiktok_upload_video(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    video_path = final_video_path_for_project(project)
    if video_path.stat().st_size > MAX_TIKTOK_VIDEO_BYTES:
        raise ValueError("TikTok qua Zernio chỉ nhận video tối đa 500 MB.")
    caption = str(payload.get("tiktokCaption") or payload.get("caption") or "").strip()
    if not caption:
        metadata = build_upload_metadata(project)
        caption = str(metadata.get("instagramCaption") or metadata.get("facebookCaption") or "").strip()
    if len(caption) > MAX_TIKTOK_CAPTION_LENGTH:
        raise ValueError("TikTok caption tối đa 2.200 ký tự.")
    config = read_social_config()
    zernio = resolve_zernio_config(zernio_config(config))
    if not zernio_is_configured(zernio):
        raise ValueError(zernio_config_hint())
    scheduled = parse_scheduled_publish_at(payload)
    if scheduled:
        validate_schedule_window(scheduled, timedelta(minutes=10), platform="TikTok")
    video_bytes = read_expected_video_bytes(video_path, payload)
    presign = _json_request(f"{zernio['base_url']}/media/presign", "POST", {
        "fileName": video_path.name,
        "contentType": "video/mp4",
        "fileSize": len(video_bytes),
    }, zernio)
    upload_url = str(_unwrap(presign, "uploadUrl") or "").strip()
    public_url = str(_unwrap(presign, "publicUrl") or "").strip()
    if not upload_url or not public_url:
        raise RuntimeError(f"Zernio presign không trả đủ uploadUrl/publicUrl: {presign}")
    _put_file(upload_url, video_path)
    post_body = {
        "content": caption,
        "mediaItems": [{"type": "video", "url": public_url}],
        "platforms": [{"platform": "tiktok", "accountId": zernio["account_id"]}],
    }
    if scheduled:
        post_body.update({"scheduledFor": scheduled, "timezone": "UTC"})
    else:
        post_body["publishNow"] = True
    response = _json_request(f"{zernio['base_url']}/posts", "POST", post_body, zernio, {"X-Request-ID": str(uuid.uuid4())})
    post = _unwrap(response, "post")
    post = post if isinstance(post, dict) else {}
    post_id = str(post.get("_id") or post.get("id") or response.get("postId") or "").strip()
    if not post_id:
        raise RuntimeError(f"Zernio tạo post không trả về post id: {response}")
    url = _post_url(response)
    details = {"url": url, "post_id": post_id, "state": "SCHEDULED" if scheduled else "PUBLISHED", "scheduled_at": scheduled or ""}
    record_social_upload(project, "tiktok", details)
    return {"ok": True, "platform": "tiktok", "project": project, "post_id": post_id, "url": url, "scheduledPublishAt": scheduled or "", "message": "Đã lên lịch TikTok qua Zernio." if scheduled else "Đã đăng TikTok qua Zernio."}
