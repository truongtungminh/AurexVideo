from __future__ import annotations

import ipaddress
import os
import re
import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, quote_plus, urlsplit

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
    record_social_upload,
    require_project,
    upload_brand_for_project,
)
from .r2 import delete_file, r2_config, r2_config_hint, r2_is_configured, resolve_r2_config, upload_file
from .schedule import parse_scheduled_publish_at, validate_schedule_window
from .scheduler import schedule_upload
from .remote_worker import schedule_on_vps


THREADS_API_HOST = "https://graph.threads.net"
DEFAULT_THREADS_GRAPH_VERSION = "v1.0"
DEFAULT_POLL_ATTEMPTS = 60
DEFAULT_POLL_INTERVAL_SECONDS = 5.0
MAX_THREADS_TEXT_LENGTH = 500
MAX_POLL_ATTEMPTS = 240
MAX_POLL_INTERVAL_SECONDS = 60.0


class _ThreadsContainerTerminalError(RuntimeError):
    """A terminal container state after which temporary source media is safe to remove."""


def threads_config(config: dict | None = None) -> dict:
    config = read_social_config() if config is None else config
    value = config.get("threads", {}) if isinstance(config, dict) else {}
    return value if isinstance(value, dict) else {}


def threads_graph_version(threads: dict | None = None) -> str:
    value = threads if isinstance(threads, dict) else threads_config()
    version = str(value.get("graph_version") or DEFAULT_THREADS_GRAPH_VERSION).strip()
    return version if version.startswith("v") else f"v{version}"


def threads_user_id(threads: dict | None = None) -> str:
    value = threads if isinstance(threads, dict) else threads_config()
    env_value = "" if value.get("_brand_connection") else os.environ.get("THREADS_USER_ID")
    return str(env_value or value.get("threads_user_id") or "").strip()


def threads_access_token(threads: dict | None = None) -> str:
    value = threads if isinstance(threads, dict) else threads_config()
    env_value = "" if value.get("_brand_connection") else os.environ.get("THREADS_ACCESS_TOKEN")
    return str(env_value or value.get("access_token") or "").strip()


def threads_is_configured(threads: dict | None = None) -> bool:
    value = threads if isinstance(threads, dict) else threads_config()
    return bool(threads_user_id(value) and threads_access_token(value))


def threads_config_hint(threads: dict | None = None) -> str:
    value = threads if isinstance(threads, dict) else threads_config()
    if not threads_user_id(value) and not threads_access_token(value):
        return "Threads chưa cấu hình. Nhập Threads User ID và access token."
    if not threads_user_id(value):
        return "Threads chưa cấu hình Threads User ID."
    if not threads_access_token(value):
        return "Threads chưa cấu hình access token."
    return ""


def _poll_attempts(value: object, *, validate: bool = False) -> int:
    raw = DEFAULT_POLL_ATTEMPTS if value in (None, "") else value
    raw_text = str(raw).strip()
    if isinstance(raw, bool) or not re.fullmatch(r"[0-9]+", raw_text):
        if validate:
            raise ValueError("Threads poll_attempts phải là số nguyên.")
        return DEFAULT_POLL_ATTEMPTS
    attempts = int(raw_text)
    if not 1 <= attempts <= MAX_POLL_ATTEMPTS:
        if validate:
            raise ValueError(f"Threads poll_attempts phải từ 1 đến {MAX_POLL_ATTEMPTS}.")
        return DEFAULT_POLL_ATTEMPTS
    return attempts


def _poll_interval_seconds(value: object, *, validate: bool = False) -> float:
    raw = DEFAULT_POLL_INTERVAL_SECONDS if value in (None, "") else value
    try:
        interval = float(raw)
    except (TypeError, ValueError) as exc:
        if validate:
            raise ValueError("Threads poll_interval_seconds phải là một số.") from exc
        return DEFAULT_POLL_INTERVAL_SECONDS
    if not 1.0 <= interval <= MAX_POLL_INTERVAL_SECONDS:
        if validate:
            raise ValueError(
                f"Threads poll_interval_seconds phải từ 1 đến {int(MAX_POLL_INTERVAL_SECONDS)} giây."
            )
        return DEFAULT_POLL_INTERVAL_SECONDS
    return interval


def threads_status(threads: dict | None = None) -> dict:
    value = threads if isinstance(threads, dict) else threads_config()
    configured = threads_is_configured(value)
    user_id = threads_user_id(value)
    return {
        "configured": configured,
        "connected": configured,
        "available": configured,
        "ready": configured,
        "user_id": user_id,
        "threads_user_id": user_id,
        "display_name": str(value.get("display_name") or value.get("name") or "").strip(),
        "connection_id": str(value.get("_connection_id") or value.get("connection_id") or "").strip(),
        "graph_version": threads_graph_version(value),
        "poll_attempts": _poll_attempts(value.get("poll_attempts")),
        "poll_interval_seconds": _poll_interval_seconds(value.get("poll_interval_seconds")),
        "message": "" if configured else threads_config_hint(value),
    }


def update_threads_config(
    threads_user_id: str,
    access_token: str,
    graph_version: str = DEFAULT_THREADS_GRAPH_VERSION,
    display_name: str = "",
    poll_attempts: int = DEFAULT_POLL_ATTEMPTS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    *,
    brand: str = "",
    connection_id: str = "",
    config: dict | None = None,
    persist: bool = True,
) -> dict:
    user_id = str(threads_user_id or "").strip()
    token = str(access_token or "").strip()
    version = str(graph_version or DEFAULT_THREADS_GRAPH_VERSION).strip()
    if not user_id:
        raise ValueError("Threads User ID không được để trống.")
    if not re.fullmatch(r"[0-9]+", user_id):
        raise ValueError("Threads User ID phải là chuỗi số.")
    if len(token) < 20 or re.search(r"\s", token):
        raise ValueError("Threads access token có vẻ không hợp lệ.")
    if not re.fullmatch(r"v?\d+\.\d+", version):
        raise ValueError("Threads graph_version phải có dạng v1.0.")

    value = {
        "threads_user_id": user_id,
        "access_token": token,
        "graph_version": version if version.startswith("v") else f"v{version}",
        "display_name": str(display_name or "").strip(),
        "poll_attempts": _poll_attempts(poll_attempts, validate=True),
        "poll_interval_seconds": _poll_interval_seconds(poll_interval_seconds, validate=True),
    }
    config = read_social_config() if config is None else config
    brand = canonical_brand(brand)
    if brand:
        saved_id = store_social_brand_connection(
            config,
            brand,
            "threads",
            value,
            connection_id=connection_id,
            name=display_name,
        )
        if persist:
            write_social_config(config)
        return {"ok": True, "brand": brand, "connection_id": saved_id, **threads_status({**value, "_brand_connection": True, "_connection_id": saved_id})}
    existing = threads_config(config)
    if isinstance(existing.get("connections"), dict):
        value["connections"] = existing["connections"]
    config["threads"] = value
    if persist:
        write_social_config(config)
    return threads_status(value)


def disconnect_threads() -> dict:
    config = read_social_config()
    section = config.get("threads")
    connections = section.get("connections") if isinstance(section, dict) else None
    if isinstance(connections, dict) and connections:
        config["threads"] = {"connections": connections}
    else:
        config.pop("threads", None)
    write_social_config(config)
    return {"ok": True, "configured": False, "connected": False}


def threads_api_url(path: str) -> str:
    # graph_version remains a saved compatibility setting, but current Threads
    # publishing endpoints intentionally have no version segment.
    encoded_path = quote(str(path or "").strip().lstrip("/"), safe="/")
    return f"{THREADS_API_HOST}/{encoded_path}" if encoded_path else THREADS_API_HOST


def threads_text_for_project(project: str, supplied_text: str = "") -> str:
    require_project(project)
    text = str(supplied_text or "").strip()
    if not text:
        metadata = build_upload_metadata(project)
        text = str(
            metadata.get("threadsText")
            or metadata.get("threadsCaption")
            or metadata.get("instagramCaption")
            or metadata.get("facebookCaption")
            or ""
        ).strip()
    if len(text) > MAX_THREADS_TEXT_LENGTH:
        raise ValueError(f"Threads text tối đa {MAX_THREADS_TEXT_LENGTH} ký tự.")
    return text


def threads_object_key(project: str) -> str:
    safe_project = re.sub(r"[^A-Za-z0-9._-]+", "-", str(project or "")).strip("-") or "project"
    return f"threads/{safe_project}/{secrets.token_hex(8)}.mp4"


def _validated_public_url(value: object) -> str:
    public_url = str(value or "").strip()
    if not public_url:
        return ""
    if "\\" in public_url or any(character.isspace() or ord(character) < 32 for character in public_url):
        raise ValueError("Threads video URL không hợp lệ.")
    try:
        parsed = urlsplit(public_url)
        hostname = parsed.hostname or ""
        parsed.port
    except ValueError as exc:
        raise ValueError("Threads video URL không hợp lệ.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValueError("Threads video URL phải là public HTTP hoặc HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError("Threads video URL không được chứa thông tin đăng nhập.")
    normalized_hostname = hostname.rstrip(".").lower()
    if (
        normalized_hostname == "localhost"
        or normalized_hostname.endswith(".localhost")
        or normalized_hostname.endswith(".local")
    ):
        raise ValueError("Threads video URL phải truy cập được công khai, không dùng địa chỉ local.")
    try:
        address = ipaddress.ip_address(normalized_hostname)
    except ValueError:
        address = None
        if re.fullmatch(r"(?:0[xX][0-9A-Fa-f]+|[0-9.]+)", normalized_hostname):
            raise ValueError("Threads video URL phải dùng hostname hoặc địa chỉ IP công khai hợp lệ.")
    if address and (not address.is_global or address.is_multicast):
        raise ValueError("Threads video URL phải dùng địa chỉ IP public unicast.")
    return public_url


def _redact_sensitive(message: object, *secret_values: object) -> str:
    safe = str(message or "").strip()
    candidates: set[str] = set()
    for value in secret_values:
        secret = str(value or "")
        if secret:
            candidates.update((secret, quote_plus(secret)))
    for candidate in sorted(candidates, key=len, reverse=True):
        safe = safe.replace(candidate, "[REDACTED]")
    safe = re.sub(
        r"(?i)((?:access[_-]?token|secret[_-]?access[_-]?key|access[_-]?key[_-]?id)"
        r"(?:%3[dD]|[\"']?\s*[:=]\s*[\"']?))[^&\s,\"'}]+",
        r"\1[REDACTED]",
        safe,
    )
    return safe or "unknown error"


def _redact_access_token(message: object, access_token: str) -> str:
    return _redact_sensitive(message, access_token)


def _threads_form_request(url: str, fields: dict, action: str, access_token: str) -> dict:
    detail = ""
    try:
        return http_form_request(url, fields)
    except Exception as exc:
        detail = _redact_access_token(exc, access_token)
    # Raise outside the except block so the original, potentially token-bearing
    # exception is not retained as traceback context.
    raise RuntimeError(f"Threads {action} failed: {detail}")


def threads_container_status(container_id: str, access_token: str) -> dict:
    detail = ""
    try:
        return http_get_request(
            threads_api_url(container_id),
            {
                "fields": "id,status,error_message",
                "access_token": access_token,
            },
        )
    except Exception as exc:
        detail = _redact_access_token(exc, access_token)
    raise RuntimeError(f"Threads container status check failed: {detail}")


def wait_for_threads_container(
    container_id: str,
    access_token: str,
    attempts: int = DEFAULT_POLL_ATTEMPTS,
    delay_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
) -> dict:
    attempts = _poll_attempts(attempts, validate=True)
    delay_seconds = _poll_interval_seconds(delay_seconds, validate=True)
    last: dict = {}
    for attempt in range(attempts):
        last = threads_container_status(container_id, access_token)
        status = str(last.get("status") or "").strip().upper()
        if status == "FINISHED":
            return last
        if status in {"ERROR", "EXPIRED"}:
            error_message = _redact_access_token(last.get("error_message") or status, access_token)
            raise _ThreadsContainerTerminalError(f"Threads media container failed: {error_message}")
        if attempt < attempts - 1:
            time.sleep(delay_seconds)
    final_status = _redact_access_token(last.get("status") or "unknown", access_token)
    raise RuntimeError(f"Threads media container timed out after {attempts} checks: {final_status}")


def _payload_retain_media(payload: dict, r2: dict) -> bool:
    for key in ("retain_media", "retainMedia"):
        if key not in payload:
            continue
        value = payload.get(key)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    return bool(resolve_r2_config(r2).get("retain_media"))


def threads_upload_video(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise TypeError("Threads upload payload must be an object.")
    project = str(payload.get("project") or "").strip()
    project_dir = require_project(project)
    brand = upload_brand_for_project(payload, project_dir, "Threads")
    payload = {**payload, "brand": brand}
    config = read_social_config()
    connection_id, threads = resolve_social_brand_connection(config, brand, "threads")
    if not threads_is_configured(threads):
        raise ValueError(threads_config_hint(threads))
    scheduled = parse_scheduled_publish_at(payload)
    supplied_text = str(payload.get("threadsText") or payload.get("threadsCaption") or "")
    text = threads_text_for_project(project, supplied_text)
    if scheduled:
        validate_schedule_window(scheduled, timedelta(minutes=10), platform="Threads")
        video_path = Path(final_video_path_for_project(project))
        queued = schedule_on_vps("threads", video_path, text, scheduled)
        return {"ok": True, "platform": "threads", "project": project, "brand": brand, "connection_id": connection_id, "state": "SCHEDULED", "scheduledPublishAt": queued["scheduledPublishAt"], "schedule_id": queued["id"], "worker_id": queued.get("id"), "message": "Đã chuyển lịch Threads lên VPS; worker sẽ publish đúng giờ."}

    supplied_url = (
        payload.get("publicUrl")
        or payload.get("videoUrl")
        or payload.get("public_url")
        or payload.get("video_url")
        or ""
    )
    public_url = _validated_public_url(supplied_url)
    video_path: Path | None = None
    if not public_url:
        video_path = Path(final_video_path_for_project(project))
        if not video_path.is_file():
            raise FileNotFoundError(f"final_video.mp4 not found for project '{project}'.")

    access_token = threads_access_token(threads)

    r2 = r2_config(config)
    resolved_r2 = resolve_r2_config(r2)
    known_secrets = (
        access_token,
        resolved_r2.get("access_key_id"),
        resolved_r2.get("secret_access_key"),
    )
    object_key = ""
    uploaded_to_r2 = False
    container_id = ""
    media_id = ""
    url = ""
    cleanup_error = ""
    cleanup_safe = False
    retain_media = _payload_retain_media(payload, r2)
    try:
        if not public_url:
            if not r2_is_configured(r2):
                raise ValueError(
                    "Threads cần publicUrl/videoUrl hoặc Cloudflare R2 để cung cấp video công khai. "
                    + r2_config_hint()
                )
            _validated_public_url(resolved_r2.get("public_base_url"))
            object_key = threads_object_key(project)
            upload_error = ""
            try:
                public_url = upload_file(video_path, object_key, "video/mp4", r2)
            except Exception as exc:
                upload_error = _redact_sensitive(exc, *known_secrets)
            if upload_error:
                raise RuntimeError(f"Threads R2 video upload failed: {upload_error}")
            uploaded_to_r2 = True
            try:
                public_url = _validated_public_url(public_url)
            except (TypeError, ValueError):
                cleanup_safe = True
                raise

        create_data = _threads_form_request(
            threads_api_url("me/threads"),
            {
                "media_type": "VIDEO",
                "video_url": public_url,
                "text": text,
                "access_token": access_token,
            },
            "container creation",
            access_token,
        )
        container_id = str(create_data.get("id") or "").strip()
        if not container_id:
            cleanup_safe = True
            raise RuntimeError("Threads container creation did not return an id.")

        try:
            wait_for_threads_container(
                container_id,
                access_token,
                attempts=_poll_attempts(threads.get("poll_attempts"), validate=True),
                delay_seconds=_poll_interval_seconds(threads.get("poll_interval_seconds"), validate=True),
            )
        except _ThreadsContainerTerminalError:
            cleanup_safe = True
            raise
        cleanup_safe = True
        publish_data = _threads_form_request(
            threads_api_url("me/threads_publish"),
            {
                "creation_id": container_id,
                "access_token": access_token,
            },
            "publish",
            access_token,
        )
        media_id = str(publish_data.get("id") or "").strip()
        if not media_id:
            raise RuntimeError("Threads publish did not return a media id.")
        url = str(publish_data.get("permalink") or publish_data.get("url") or "").strip()
    finally:
        if uploaded_to_r2 and cleanup_safe and not retain_media:
            try:
                delete_file(object_key, r2)
            except Exception as exc:
                cleanup_error = _redact_sensitive(exc, *known_secrets)

    metadata_error = ""
    try:
        record_details = {
            "url": url,
            "video_id": container_id,
            "post_id": media_id,
            "state": "PUBLISHED",
        }
        if brand:
            record_details["brand"] = brand
        record_details["connection_id"] = connection_id
        record_social_upload(project, "threads", record_details)
    except Exception as exc:
        metadata_error = _redact_sensitive(exc, *known_secrets)
    message = "Đã đăng video lên Threads."
    if cleanup_error:
        message += f" Không xóa được file tạm trên R2: {cleanup_error}"
    if metadata_error:
        message += f" Không lưu được metadata cục bộ: {metadata_error}"
    return {
        "ok": True,
        "platform": "threads",
        "project": project,
        "brand": brand,
        "connection_id": connection_id,
        "user_id": threads_user_id(threads),
        "container_id": container_id,
        "media_id": media_id,
        "url": url,
        "message": message,
    }
