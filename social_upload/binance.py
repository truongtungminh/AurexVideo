from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from pathlib import Path

from aurexvideo_paths import ffmpeg_executable

from .config import canonical_brand, read_social_config, write_social_config
from .http import http_form_request
from .metadata import (
    build_upload_metadata,
    final_video_path_for_project,
    project_brand_from_topic,
    record_scheduled_social_upload,
    record_social_upload,
    require_project,
)
from .schedule import parse_scheduled_publish_at
from .scheduler import schedule_upload


BASE_URL_V1 = "https://www.binance.com/bapi/composite/v1/public/pgc/openApi"
BASE_URL_V2 = "https://www.binance.com/bapi/composite/v2/public/pgc/openApi"
POLL_INTERVAL_MS = 3000
MAX_POLL_RETRIES = 10
BINANCE_SQUARE_OPENAPI_KEY_ENV = "BINANCE_SQUARE_OPENAPI_KEY"
DEFAULT_OPENAPI_KEY_PATH = Path.home() / ".config" / "binance-square" / "openapi-key"
BINANCE_BRAND = "july"


def binance_config(config: dict | None = None) -> dict:
    config = read_social_config() if config is None else config
    binance = config.get("binance", {}) if isinstance(config, dict) else {}
    if not isinstance(binance, dict):
        binance = {}
    return binance


def resolve_binance_api_key(binance: dict | None = None) -> str:
    env_key = str(os.environ.get(BINANCE_SQUARE_OPENAPI_KEY_ENV) or "").strip()
    if env_key:
        return env_key
    binance = binance if binance is not None else binance_config()
    saved_key = str(binance.get("apiKey") or "").strip()
    if saved_key:
        return saved_key
    if DEFAULT_OPENAPI_KEY_PATH.exists():
        saved_key = DEFAULT_OPENAPI_KEY_PATH.read_text(encoding="utf-8", errors="replace").strip()
        if saved_key:
            return saved_key
    raise ValueError(
        "Binance Square chưa cấu hình. Bấm Thêm Binance rồi nhập OpenAPI key, "
        "hoặc đặt biến môi trường BINANCE_SQUARE_OPENAPI_KEY."
    )


def binance_is_configured(binance: dict | None = None) -> bool:
    try:
        return bool(resolve_binance_api_key(binance))
    except ValueError:
        return False


def mask_api_key(api_key: str) -> str:
    api_key = str(api_key or "")
    if len(api_key) <= 9:
        return f"{api_key[:2]}..."
    return f"{api_key[:5]}...{api_key[-4:]}"


def binance_config_hint() -> str:
    return "Binance Square chưa cấu hình. Bấm Thêm Binance để nhập OpenAPI key."


def _binance_json_request(endpoint: str, api_key: str, body: dict | None = None, base_url: str = BASE_URL_V2) -> dict:
    url = f"{base_url}{endpoint}"
    payload = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=payload,
        headers={
            "X-Square-OpenAPI-Key": api_key,
            "Content-Type": "application/json",
            "clienttype": "binanceSkill",
        },
        method="POST",
    )
    import sys
    try:
        response = urlopen(request, timeout=60)
        raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(f"[BINANCE-API] {endpoint} HTTP {exc.code}: {detail}", file=sys.stderr, flush=True)
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    if endpoint == "/content/add" and response.status == 504:
        return {"id": None, "shareLink": None, "publishStatus": "success_without_post_id"}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[BINANCE-API] {endpoint} non-JSON: {raw[:500]}", file=sys.stderr, flush=True)
        raise RuntimeError(f"API returned non-JSON response: {response.status} {response.status}") from exc
    code = str(data.get("code") or "")
    print(f"[BINANCE-API] {endpoint} -> code={code} data={json.dumps(data, ensure_ascii=False)[:800]}", file=sys.stderr, flush=True)
    if code != "000000":
        raise RuntimeError(f"API error [{code}]: {data.get('message')}")
    return data.get("data") or {}


def _binance_upload_to_s3(presigned_url: str, file_path: Path, content_type: str) -> None:
    data = file_path.read_bytes()
    request = Request(
        presigned_url,
        data=data,
        headers={"Content-Type": content_type},
        method="PUT",
    )
    try:
        response = urlopen(request, timeout=300)
        response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"S3 upload failed: HTTP {exc.code}: {detail}") from exc


def _binance_poll_upload_status(api_key: str, file_ticket: str) -> dict:
    for attempt in range(MAX_POLL_RETRIES):
        data = _binance_json_request("/image/imageStatus", api_key, {"fileTicket": file_ticket})
        status = data.get("status")
        if status == 1:
            return data
        if status == 2:
            raise RuntimeError(f"Processing failed: {data.get('failedReason') or 'unknown reason'}")
        if attempt < MAX_POLL_RETRIES - 1:
            time.sleep(POLL_INTERVAL_MS / 1000)
    raise RuntimeError(f"Poll timed out after {MAX_POLL_RETRIES} retries")


def _upload_image_to_binance(api_key: str, file_path: Path) -> str:
    file_name = file_path.name
    content_type = _content_type_for_path(file_path)
    presigned = _binance_json_request("/image/presignedUrl", api_key, {"imageName": file_name})
    presigned_url = str(presigned.get("presignedUrl") or "").strip()
    file_ticket = str(presigned.get("fileTicket") or "").strip()
    if not presigned_url or not file_ticket:
        raise RuntimeError(f"Presigned image response missing URL/ticket: {presigned}")
    _binance_upload_to_s3(presigned_url, file_path, content_type)
    status = _binance_poll_upload_status(api_key, file_ticket)
    image_url = str(status.get("imageUrl") or "").strip()
    if not image_url:
        raise RuntimeError(f"Image upload did not return imageUrl: {status}")
    return image_url


def _content_type_for_path(file_path: Path) -> str:
    ext = file_path.suffix.lower().lstrip(".")
    mapping = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
        "mp4": "video/mp4",
        "mov": "video/quicktime",
        "avi": "video/x-ms-avi",
        "webm": "video/webm",
    }
    return mapping.get(ext, "application/octet-stream")


def update_binance_config(api_key: str) -> dict:
    api_key = str(api_key or "").strip()
    if not api_key or len(api_key) < 10:
        raise ValueError("Binance Square OpenAPI key looks invalid.")
    config = read_social_config()
    binance = binance_config(config)
    binance["apiKey"] = api_key
    config["binance"] = binance
    write_social_config(config)
    return {"ok": True, "configured": True, "masked": mask_api_key(api_key)}


def disconnect_binance() -> dict:
    config = read_social_config()
    binance = binance_config(config)
    binance.pop("apiKey", None)
    config["binance"] = binance
    write_social_config(config)
    return {"ok": True}


def resolve_binance_upload_brand(payload: dict) -> str:
    """Return the Binance-eligible brand, rejecting non-July projects and payloads."""
    project = str(payload.get("project") or "").strip()
    declared_brand = canonical_brand(payload.get("brand") or payload.get("brandId"))
    project_brand = canonical_brand(project_brand_from_topic(require_project(project)))
    effective_brand = declared_brand or project_brand
    if project_brand not in {"", BINANCE_BRAND} or effective_brand != BINANCE_BRAND:
        raise ValueError("Binance Square is only available for Brand july.")
    return effective_brand


def binance_upload_video(payload: dict) -> dict:
    project = str(payload.get("project") or "").strip()
    project_dir = require_project(project)
    brand = resolve_binance_upload_brand(payload)
    video_path = final_video_path_for_project(project)
    duration = float(payload.get("duration") or 0)
    if duration <= 0:
        raise ValueError("Duration is required for Binance Square video upload.")
    text = str(payload.get("text") or "").strip()
    scheduled = parse_scheduled_publish_at(payload)
    if scheduled:
        queued = schedule_upload("binance", payload, scheduled)
        record_scheduled_social_upload(
            project_dir,
            "binance",
            queued["scheduledPublishAt"],
            brand=brand,
        )
        return {
            "ok": True,
            "platform": "binance",
            "project": project,
            "brand": brand,
            "state": "SCHEDULED",
            "scheduledPublishAt": queued["scheduledPublishAt"],
            "schedule_id": queued["id"],
            "message": "Đã xếp lịch Binance Square; worker sẽ publish đúng giờ.",
        }

    api_key = resolve_binance_api_key()
    file_name = video_path.name
    size = video_path.stat().st_size
    content_type = _content_type_for_path(video_path)

    presigned = _binance_json_request("/video/preSign", api_key, {"fileName": file_name, "size": size})
    presigned_url = str(presigned.get("presignedUrl") or "").strip()
    file_ticket = str(presigned.get("fileTicket") or "").strip()
    if not presigned_url or not file_ticket:
        raise RuntimeError(f"Video presigned response missing URL/ticket: {presigned}")

    _binance_upload_to_s3(presigned_url, video_path, content_type)
    _binance_poll_upload_status(api_key, file_ticket)

    cover_path, temp_dir = _extract_video_cover(video_path)
    try:
        cover_url = _upload_image_to_binance(api_key, cover_path)
    finally:
        if cover_path.exists():
            cover_path.unlink()
        if temp_dir and temp_dir.exists():
            temp_dir.rmdir()

    body = {
        "contentType": 3,
        "fileTicket": file_ticket,
        "cover": cover_url,
        "videoTimeSeconds": duration,
        "isPublish": True,
    }
    if text:
        body["bodyTextOnly"] = text

    result = _binance_json_request("/content/add", api_key, body, base_url=BASE_URL_V1)
    record_details = {
        "url": str(result.get("shareLink") or ""),
        "videoId": str(result.get("id") or file_ticket),
        "postId": str(result.get("id") or ""),
        "state": "published" if result.get("id") or result.get("shareLink") else "published_without_post_id",
    }
    if brand:
        record_details["brand"] = brand
    record_social_upload(project, "binance", record_details)
    return {
        "ok": True,
        "platform": "binance",
        "project": project,
        "brand": brand,
        "videoId": str(result.get("id") or file_ticket),
        "postId": str(result.get("id") or ""),
        "url": str(result.get("shareLink") or ""),
        "message": "Uploaded to Binance Square.",
    }


def _extract_video_cover(video_path: Path) -> tuple[Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="aurex-binance-cover-"))
    cover_path = temp_dir / f"{video_path.stem}-cover.png"
    from subprocess import run, PIPE
    result = run(
        [
            str(ffmpeg_executable()),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(cover_path),
        ],
        stdout=PIPE,
        stderr=PIPE,
    )
    if result.returncode != 0 or not cover_path.exists() or cover_path.stat().st_size == 0:
        raise RuntimeError(f"Failed to extract video cover: {result.stderr.decode('utf-8', 'replace')}")
    return cover_path, temp_dir


def binance_status() -> dict:
    config = read_social_config()
    binance = binance_config(config)
    configured = binance_is_configured(binance)
    masked = mask_api_key(binance.get("apiKey", "")) if configured else ""
    return {
        "configured": configured,
        "connected": configured,
        "available": configured,
        "masked": masked,
        "message": "" if configured else binance_config_hint(),
    }
