from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import (
    canonical_brand,
    read_social_config,
    resolve_social_brand_connection,
    store_social_brand_connection,
    write_social_config,
)
from .metadata import (
    build_upload_metadata,
    final_video_path_for_project,
    read_expected_video_bytes,
    record_scheduled_social_upload,
    record_social_upload,
    require_project,
    upload_brand_for_project,
)
from .schedule import parse_scheduled_publish_at, validate_schedule_window
from .scheduler import schedule_upload

ZERNIO_BASE_URL = "https://zernio.com/api/v1"
MAX_TIKTOK_VIDEO_BYTES = 500 * 1024 * 1024
MAX_TIKTOK_CAPTION_LENGTH = 2200
TIKTOK_DIRECT_POST_CAPACITY_CODES = frozenset({
    "TIKTOK_DIRECT_POST_CAPACITY",
    "TIKTOK_DIRECT_POST_CAPACITY_EXCEEDED",
    "TIKTOK_DIRECT_POST_CAPACITY_REACHED",
    "TIKTOK_DIRECT_POST_DAILY_LIMIT_REACHED",
    "TIKTOK_DIRECT_POST_LIMIT_EXCEEDED",
    "TIKTOK_DIRECT_POST_LIMIT_REACHED",
    "TIKTOK_DIRECT_POST_QUOTA_EXCEEDED",
    "TIKTOK_DIRECT_POST_QUOTA_REACHED",
})
TIKTOK_DIRECT_POST_CAPACITY_MESSAGES = frozenset({
    "tiktok creator has reached the daily posting limit. please try again later.",
    "tiktok direct post capacity reached.",
    "tiktok direct post capacity exceeded.",
})


class ZernioRequestError(RuntimeError):
    """A Zernio API error retaining machine-readable response details."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response: dict | None = None,
        raw_response: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response = response or {}
        self.raw_response = raw_response


def zernio_config(config: dict | None = None) -> dict:
    config = read_social_config() if config is None else config
    value = config.get("zernio", {}) if isinstance(config, dict) else {}
    return value if isinstance(value, dict) else {}


def resolve_zernio_config(value: dict | None = None) -> dict:
    saved = value if isinstance(value, dict) else zernio_config()
    use_environment = not bool(saved.get("_brand_connection"))
    return {
        "api_key": str((os.environ.get("ZERNIO_API_KEY") if use_environment else "") or saved.get("api_key") or "").strip(),
        "account_id": str((os.environ.get("ZERNIO_TIKTOK_ACCOUNT_ID") if use_environment else "") or saved.get("account_id") or "").strip(),
        "base_url": str((os.environ.get("ZERNIO_BASE_URL") if use_environment else "") or saved.get("base_url") or ZERNIO_BASE_URL).strip().rstrip("/"),
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
        "available": zernio_is_configured(value),
        "ready": zernio_is_configured(value),
        "account_id": config["account_id"],
        "connection_id": str(value.get("_connection_id") or value.get("connection_id") or "").strip() if isinstance(value, dict) else "",
        "display_name": str(value.get("display_name") or value.get("name") or "").strip() if isinstance(value, dict) else "",
        "masked_api_key": masked,
        "base_url": config["base_url"],
        "message": "" if zernio_is_configured(value) else zernio_config_hint(),
    }


def update_zernio_config(
    api_key: str,
    account_id: str,
    *,
    base_url: str = ZERNIO_BASE_URL,
    brand: str = "",
    connection_id: str = "",
    display_name: str = "",
    config: dict | None = None,
    persist: bool = True,
) -> dict:
    api_key = str(api_key or "").strip()
    account_id = str(account_id or "").strip()
    base_url = str(base_url or ZERNIO_BASE_URL).strip().rstrip("/")
    if not api_key:
        raise ValueError("Zernio API key không được để trống.")
    if not account_id:
        raise ValueError("TikTok account ID của Zernio không được để trống.")
    if not base_url.startswith("https://"):
        raise ValueError("Zernio API URL phải bắt đầu bằng https://.")
    config = read_social_config() if config is None else config
    value = {"api_key": api_key, "account_id": account_id, "base_url": base_url, "display_name": str(display_name or "").strip()}
    brand = canonical_brand(brand)
    if brand:
        saved_id = store_social_brand_connection(
            config,
            brand,
            "tiktok",
            value,
            connection_id=connection_id,
            name=display_name,
        )
        if persist:
            write_social_config(config)
        return {"ok": True, "brand": brand, "connection_id": saved_id, **zernio_status({**value, "_brand_connection": True, "_connection_id": saved_id})}
    existing = zernio_config(config)
    if isinstance(existing.get("connections"), dict):
        value["connections"] = existing["connections"]
    config["zernio"] = value
    if persist:
        write_social_config(config)
    return {"ok": True, **zernio_status(value)}


def disconnect_zernio() -> dict:
    config = read_social_config()
    section = config.get("zernio")
    connections = section.get("connections") if isinstance(section, dict) else None
    if isinstance(connections, dict) and connections:
        config["zernio"] = {"connections": connections}
    else:
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
        try:
            detail = exc.read().decode("utf-8", "replace")
        finally:
            exc.close()
        try:
            parsed = json.loads(detail) if detail.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        response = parsed if isinstance(parsed, dict) else {}
        raise ZernioRequestError(
            f"Zernio HTTP {exc.code}: {detail[:800]}",
            status_code=exc.code,
            response=response,
            raw_response=detail,
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Zernio request failed: {exc}") from exc
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ZernioRequestError(
            "Zernio trả về dữ liệu JSON không hợp lệ.",
            raw_response=raw,
        ) from exc
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):
        return {"error": parsed}
    return {}


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


def _error_values(value: object, *, key: str = "") -> list[tuple[str, str]]:
    """Return scalar error values while preserving their response keys."""
    if isinstance(value, dict):
        values: list[tuple[str, str]] = []
        for child_key, child_value in value.items():
            values.extend(_error_values(child_value, key=str(child_key).lower()))
        return values
    if isinstance(value, list):
        values: list[tuple[str, str]] = []
        for child_value in value:
            values.extend(_error_values(child_value, key=key))
        return values
    if isinstance(value, (str, int)):
        return [(key, str(value))]
    return []


def _response_has_error(response: dict) -> bool:
    if not isinstance(response, dict):
        return False
    error = response.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "").strip().casefold()
        message = str(error.get("message") or "").strip()
        if code not in {"", "ok", "0", "200"} or message:
            return True
    elif error:
        return True
    if response.get("errors"):
        return True
    if response.get("success") is False or response.get("ok") is False:
        return True
    if str(response.get("status") or "").strip().lower() in {"error", "failed", "failure"}:
        return True
    for key, raw_value in _error_values(response):
        normalized = raw_value.strip().lower()
        if key in {"error", "errors", "error_message", "errormessage"} and normalized:
            return True
        if key == "status" and normalized in {"error", "failed", "failure"}:
            return True
    return False


def _is_tiktok_direct_post_capacity_error(value: object) -> bool:
    """Match only TikTok's direct-post capacity response, never generic failures."""
    response = value.response if isinstance(value, ZernioRequestError) else value
    values = _error_values(response)
    if isinstance(value, ZernioRequestError) and value.raw_response:
        values.append(("raw_response", value.raw_response))
    for _, raw_value in values:
        normalized = raw_value.strip().upper().replace("-", "_").replace(" ", "_")
        if normalized in TIKTOK_DIRECT_POST_CAPACITY_CODES:
            return True
        message = raw_value.casefold()
        if message in TIKTOK_DIRECT_POST_CAPACITY_MESSAGES:
            return True
        if "capacity" in message and (
            "tiktok" in message
            or "direct post" in message
            or "direct posting" in message
        ):
            return True
    raw_text = str(value).casefold()
    return "capacity" in raw_text and (
        "tiktok" in raw_text
        or "direct post" in raw_text
        or "direct posting" in raw_text
    )


def _post_error(response: dict) -> ZernioRequestError:
    detail = json.dumps(response, ensure_ascii=False)[:800]
    return ZernioRequestError(f"Zernio tạo post bị từ chối: {detail}", response=response)


def _post_with_creator_inbox_fallback(post_body: dict, zernio: dict) -> tuple[dict, bool]:
    """Create one direct post and retry it once as a Creator Inbox draft on capacity."""
    fallback_attempted = False
    body = post_body
    while True:
        try:
            response = _json_request(
                f"{zernio['base_url']}/posts",
                "POST",
                body,
                zernio,
                {"X-Request-ID": str(uuid.uuid4())},
            )
            if _is_tiktok_direct_post_capacity_error(response) or _response_has_error(response):
                raise _post_error(response)
            return response, fallback_attempted
        except RuntimeError as exc:
            if fallback_attempted or not _is_tiktok_direct_post_capacity_error(exc):
                raise
            fallback_attempted = True
            settings = post_body.get("tiktokSettings")
            if settings is not None and not isinstance(settings, dict):
                raise RuntimeError("TikTok post có tiktokSettings không hợp lệ cho Creator Inbox fallback.") from exc
            body = {**post_body, "tiktokSettings": {**(settings or {}), "draft": True}}
            body.pop("publishNow", None)


def tiktok_upload_video(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    project_dir = require_project(project)
    brand = upload_brand_for_project(payload, project_dir, "TikTok")
    payload = {**payload, "brand": brand}
    config = read_social_config()
    connection_id, connection = resolve_social_brand_connection(config, brand, "tiktok")
    zernio = resolve_zernio_config(connection)
    if not zernio_is_configured(zernio):
        raise ValueError(zernio_config_hint())
    video_path = final_video_path_for_project(project)
    if video_path.stat().st_size > MAX_TIKTOK_VIDEO_BYTES:
        raise ValueError("TikTok qua Zernio chỉ nhận video tối đa 500 MB.")
    caption = str(payload.get("tiktokCaption") or payload.get("caption") or "").strip()
    if not caption:
        metadata = build_upload_metadata(project)
        caption = str(metadata.get("instagramCaption") or metadata.get("facebookCaption") or "").strip()
    if len(caption) > MAX_TIKTOK_CAPTION_LENGTH:
        raise ValueError("TikTok caption tối đa 2.200 ký tự.")
    tiktok_settings = payload.get("tiktokSettings")
    if tiktok_settings is not None and not isinstance(tiktok_settings, dict):
        raise ValueError("TikTok tiktokSettings phải là object.")
    scheduled = parse_scheduled_publish_at(payload)
    if scheduled:
        validate_schedule_window(scheduled, timedelta(minutes=10), platform="TikTok")
    video_bytes = read_expected_video_bytes(video_path, payload)
    if scheduled:
        queued = schedule_upload("tiktok", payload, scheduled)
        record_scheduled_social_upload(
            project_dir,
            "tiktok",
            queued["scheduledPublishAt"],
            brand=brand,
            connection_id=connection_id,
        )
        return {
            "ok": True,
            "platform": "tiktok",
            "project": project,
            "brand": brand,
            "connection_id": connection_id,
            "state": "SCHEDULED",
            "scheduledPublishAt": queued["scheduledPublishAt"],
            "schedule_id": queued["id"],
            "worker_id": queued["id"],
            "message": "Đã xếp lịch TikTok trên máy này; worker sẽ thử đăng đúng giờ và tự chuyển sang Creator Inbox/Draft nếu Zernio quá tải. Hãy mở AurexVideo vào giờ hẹn; nếu app đóng, mở lại để queue chạy bù.",
        }
    presign = _json_request(f"{zernio['base_url']}/media/presign", "POST", {
        "filename": video_path.name,
        "contentType": "video/mp4",
        "size": len(video_bytes),
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
    if tiktok_settings is not None:
        post_body["tiktokSettings"] = dict(tiktok_settings)
    requested_draft = bool(isinstance(tiktok_settings, dict) and tiktok_settings.get("draft"))
    if not requested_draft:
        post_body["publishNow"] = True
    response, used_creator_inbox = _post_with_creator_inbox_fallback(post_body, zernio)
    post = _unwrap(response, "post")
    post = post if isinstance(post, dict) else {}
    post_id = str(post.get("_id") or post.get("id") or response.get("postId") or "").strip()
    if not post_id:
        raise RuntimeError(f"Zernio tạo post không trả về post id: {response}")
    url = _post_url(response)
    used_creator_inbox = used_creator_inbox or requested_draft
    fallback_due_to_capacity = used_creator_inbox and not requested_draft
    state = "DRAFT" if used_creator_inbox else "PUBLISHED"
    details = {
        "url": url,
        "post_id": post_id,
        "state": state,
        "scheduled_at": scheduled or "",
        "connection_id": connection_id,
        "delivery": "CREATOR_INBOX" if used_creator_inbox else "DIRECT_POST",
        "fallback_reason": "TIKTOK_DIRECT_POST_CAPACITY" if fallback_due_to_capacity else "",
    }
    if brand:
        details["brand"] = brand
    record_social_upload(project, "tiktok", details)
    return {
        "ok": True,
        "platform": "tiktok",
        "project": project,
        "brand": brand,
        "connection_id": connection_id,
        "post_id": post_id,
        "url": url,
        "state": state,
        "delivery": "CREATOR_INBOX" if used_creator_inbox else "DIRECT_POST",
        "fallbackReason": "TIKTOK_DIRECT_POST_CAPACITY" if fallback_due_to_capacity else "",
        "scheduledPublishAt": scheduled or "",
        "message": (
            "TikTok direct đang quá tải; đã gửi video vào Creator Inbox dưới dạng bản nháp. Hãy hoàn tất đăng trong TikTok."
            if fallback_due_to_capacity
            else (
                "Đã gửi TikTok vào Creator Inbox dưới dạng bản nháp; hãy hoàn tất đăng trong TikTok."
                if used_creator_inbox
                else "Đã đăng TikTok qua Zernio."
            )
        ),
    }
