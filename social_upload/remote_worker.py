from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .config import read_social_config

_WATCH_OUTBOX_LOCK = threading.RLock()


def _future(value: str) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _worker_config() -> tuple[str, str, str, str, str, str]:
    cfg = read_social_config().get("social_worker") or {}
    url = str(cfg.get("url") or os.environ.get("AUREX_SOCIAL_WORKER_URL") or "").rstrip("/")
    api_key = str(cfg.get("api_key") or os.environ.get("AUREX_SOCIAL_WORKER_API_KEY") or "")
    ssh_target = str(cfg.get("ssh") or os.environ.get("AUREX_SOCIAL_WORKER_SSH") or "")
    ssh_key = str(cfg.get("ssh_key") or os.environ.get("AUREX_SOCIAL_WORKER_SSH_KEY") or "")
    ssh_port = str(cfg.get("ssh_port") or os.environ.get("AUREX_SOCIAL_WORKER_SSH_PORT") or "54321")
    media_root = str(cfg.get("media_root") or os.environ.get("AUREX_SOCIAL_WORKER_MEDIA_ROOT") or "/opt/aurex-social-worker/media").rstrip("/")
    return url, api_key, ssh_target, ssh_key, ssh_port, media_root


def _validated_public_video_url(value: object) -> str:
    video_url = str(value or "").strip()
    if not video_url or "\\" in video_url or any(character.isspace() or ord(character) < 32 for character in video_url):
        raise ValueError("VPS social worker cần public video URL từ R2.")
    try:
        parsed = urlsplit(video_url)
        hostname = parsed.hostname or ""
        parsed.port
    except ValueError as exc:
        raise ValueError("Public video URL không hợp lệ.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValueError("Public video URL phải là HTTP hoặc HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError("Public video URL không được chứa thông tin đăng nhập.")
    return video_url


def schedule_idempotency_key(
    platform: str,
    video_url: str,
    caption: str,
    scheduled_at: str,
    *,
    project: str = "",
    brand: str = "",
    account_id: str = "",
    media_sha256: str = "",
) -> str:
    material = "\n".join(
        (
            str(platform or "").strip().lower(),
            str(project or "").strip(),
            str(brand or "").strip().lower(),
            str(account_id or "").strip(),
            str(scheduled_at or "").strip(),
            str(media_sha256 or "").strip().lower(),
            str(video_url or "").strip(),
            str(caption or "").strip(),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _worker_request(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    url, api_key, _, _, _, _ = _worker_config()
    if not url or not api_key:
        raise RuntimeError("VPS social worker chưa được cấu hình trong social-upload.json.")
    encoded = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{url}{path}",
        data=encoded,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"VPS social worker HTTP {exc.code}: {detail}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("VPS social worker returned an invalid JSON object.")
    return value


def schedule_on_vps(
    platform: str,
    video_url: str,
    caption: str,
    scheduled_at: str,
    *,
    project: str = "",
    brand: str = "",
    account_id: str = "",
    media_sha256: str = "",
    r2_key: str = "",
    tiktok_settings: dict | None = None,
) -> dict:
    url, api_key, _, _, _, _ = _worker_config()
    if not url or not api_key:
        raise RuntimeError("VPS social worker chưa được cấu hình trong social-upload.json.")
    video_url = _validated_public_video_url(video_url)
    scheduled_at = _future(scheduled_at)
    digest = str(media_sha256 or "").strip().lower()
    if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("media_sha256 phải là SHA-256 hex digest.")
    idempotency_key = schedule_idempotency_key(
        platform,
        video_url,
        caption,
        scheduled_at,
        project=project,
        brand=brand,
        account_id=account_id,
        media_sha256=digest,
    )
    payload = {
        "platform": platform,
        "scheduledPublishAt": scheduled_at,
        "caption": caption,
        "videoUrl": video_url,
        "idempotencyKey": idempotency_key,
    }
    if digest:
        payload["expectedMediaSha256"] = digest
    if r2_key:
        payload["r2Key"] = str(r2_key).strip()[:500]
    if project:
        payload["project"] = project
    if brand:
        payload["brand"] = brand
    if account_id:
        payload["accountId"] = account_id
    if tiktok_settings is not None:
        payload["tiktokSettings"] = dict(tiktok_settings)
    body = _worker_request("/schedule", "POST", payload)
    return {
        **body,
        "scheduledPublishAt": scheduled_at,
        "expectedMediaSha256": digest,
        "idempotencyKey": idempotency_key,
        "videoUrl": video_url,
    }


def watch_tiktok_post(
    post_id: str,
    *,
    project: str = "",
    brand: str = "",
    account_id: str = "",
    scheduled_for: str = "",
) -> dict:
    payload = {
        "postId": str(post_id or "").strip(),
        "project": str(project or "").strip(),
        "brand": str(brand or "").strip(),
        "accountId": str(account_id or "").strip(),
    }
    if scheduled_for:
        payload["scheduledFor"] = _future(scheduled_for)
    if not payload["postId"]:
        raise ValueError("TikTok post id is required.")
    return _worker_request("/watch-tiktok", "POST", payload)


def worker_job_status(worker_id: str) -> dict:
    worker_id = str(worker_id or "").strip()
    if not worker_id:
        raise ValueError("VPS worker job id is required.")
    return _worker_request(f"/jobs/{worker_id}")


def worker_tiktok_status(post_id: str) -> dict:
    post_id = str(post_id or "").strip()
    if not post_id:
        raise ValueError("TikTok post id is required.")
    return _worker_request(f"/tiktok/status/{post_id}")


def _watch_outbox_path() -> Path:
    root = Path(
        os.environ.get("AUREX_DATA_ROOT")
        or (Path.home() / "Library/Application Support/app.aurexvideo/studio")
    ).expanduser().resolve()
    return root / "tiktok-watch-outbox.json"


def _read_watch_outbox() -> list[dict]:
    try:
        value = json.loads(_watch_outbox_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _write_watch_outbox(items: list[dict]) -> None:
    path = _watch_outbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def queue_tiktok_watch(
    post_id: str,
    *,
    project: str = "",
    brand: str = "",
    account_id: str = "",
    scheduled_for: str = "",
) -> dict:
    post_id = str(post_id or "").strip()
    if not post_id:
        raise ValueError("TikTok post id is required.")
    item = {
        "postId": post_id,
        "project": str(project or "").strip(),
        "brand": str(brand or "").strip(),
        "accountId": str(account_id or "").strip(),
        "attempts": 0,
        "nextAttemptAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    if scheduled_for:
        item["scheduledFor"] = _future(scheduled_for)
    with _WATCH_OUTBOX_LOCK:
        items = _read_watch_outbox()
        if not any(str(current.get("postId") or "").strip() == post_id for current in items):
            items.append(item)
            _write_watch_outbox(items)
    return item


def flush_tiktok_watch_outbox() -> int:
    """Register deferred watches when the local app is online again."""
    with _WATCH_OUTBOX_LOCK:
        items = _read_watch_outbox()
        if not items:
            return 0
        remaining: list[dict] = []
        synced = 0
        current_time = datetime.now(timezone.utc)
        for item in items:
            try:
                next_attempt = datetime.fromisoformat(
                    str(item.get("nextAttemptAt") or "").replace("Z", "+00:00")
                )
            except ValueError:
                next_attempt = current_time
            if next_attempt > current_time:
                remaining.append(item)
                continue
            try:
                watch_tiktok_post(
                    str(item.get("postId") or ""),
                    project=str(item.get("project") or ""),
                    brand=str(item.get("brand") or ""),
                    account_id=str(item.get("accountId") or ""),
                    scheduled_for=str(item.get("scheduledFor") or ""),
                )
                synced += 1
            except Exception as exc:
                failed = dict(item)
                failed["attempts"] = int(failed.get("attempts") or 0) + 1
                failed["lastError"] = str(exc)[:500]
                failed["nextAttemptAt"] = (
                    current_time + timedelta(minutes=5)
                ).isoformat(timespec="seconds").replace("+00:00", "Z")
                remaining.append(failed)
        _write_watch_outbox(remaining)
        return synced
