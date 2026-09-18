from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
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
    return _normalize_worker_job(_worker_request(f"/jobs/{worker_id}"))


def _first_value(job: dict, *keys: str) -> object:
    for key in keys:
        value = job.get(key)
        if value not in (None, ""):
            return value
    return ""


def _normalize_worker_job(job: object) -> dict:
    if not isinstance(job, dict):
        return {}
    normalized = dict(job)
    aliases = {
        "accountId": ("accountId", "account_id"),
        "videoUrl": ("videoUrl", "video_url"),
        "scheduledPublishAt": ("scheduledPublishAt", "scheduled_at"),
        "nextAttemptAt": ("nextAttemptAt", "next_attempt_at"),
        "providerPostId": ("providerPostId", "provider_post_id"),
        "providerStatus": ("providerStatus", "provider_status"),
        "deliveryStatus": ("deliveryStatus", "delivery_status"),
        "idempotencyKey": ("idempotencyKey", "idempotency_key"),
        "r2Key": ("r2Key", "r2_key"),
        "r2DeletedAt": ("r2DeletedAt", "r2_deleted_at"),
        "r2CleanupError": ("r2CleanupError", "r2_cleanup_error"),
        "createdAt": ("createdAt", "created_at"),
        "updatedAt": ("updatedAt", "updated_at"),
    }
    for target, keys in aliases.items():
        if not normalized.get(target):
            normalized[target] = _first_value(normalized, *keys)
    return normalized


def _is_orphan_worker_job(job: dict) -> bool:
    return (
        not str(job.get("project") or "").strip()
        and not str(job.get("brand") or "").strip()
        and not str(job.get("videoUrl") or job.get("video_url") or "").strip()
        and not str(job.get("r2Key") or job.get("r2_key") or "").strip()
    )


def worker_jobs(limit: int = 100, status: str = "", platform: str = "") -> dict:
    query = []
    if limit:
        query.append(("limit", str(max(1, min(int(limit), 500)))))
    if status:
        query.append(("status", str(status).strip()))
    if platform:
        query.append(("platform", str(platform).strip().lower()))
    suffix = ""
    if query:
        from urllib.parse import urlencode

        suffix = "?" + urlencode(query)
    body = _worker_request("/jobs" + suffix)
    if isinstance(body.get("jobs"), list):
        body["jobs"] = [
            normalized
            for job in body["jobs"]
            for normalized in [_normalize_worker_job(job)]
            if not _is_orphan_worker_job(normalized)
        ]
    return body


def cancel_worker_job(worker_id: str) -> dict:
    worker_id = str(worker_id or "").strip()
    if not worker_id:
        raise ValueError("VPS worker job id is required.")
    return _worker_request(f"/jobs/{worker_id}/cancel", "POST", {})


def retry_worker_job(worker_id: str) -> dict:
    worker_id = str(worker_id or "").strip()
    if not worker_id:
        raise ValueError("VPS worker job id is required.")
    return _worker_request(f"/jobs/{worker_id}/retry", "POST", {})


def _brand_for_route(config: dict, platform: str, account_id: str) -> str:
    routes = config.get("brand_routes") if isinstance(config, dict) else {}
    routes = routes if isinstance(routes, dict) else {}
    for brand, platforms in routes.items():
        if not isinstance(platforms, dict):
            continue
        item = platforms.get(platform)
        if not isinstance(item, dict):
            continue
        values = {
            str(item.get("connection_id") or "").strip(),
            str(item.get("page_id") or "").strip(),
            str(item.get("channel_id") or "").strip(),
            str(item.get("account_id") or "").strip(),
        }
        if account_id and account_id in values:
            return str(brand or "").strip()
    return ""


def _worker_social_payload(config: dict) -> dict:
    social = {"instagram": [], "threads": [], "facebook": [], "youtube": [], "version": 2}
    instagram = config.get("instagram") if isinstance(config.get("instagram"), dict) else {}
    for connection_id, item in (instagram.get("connections") or {}).items():
        if not isinstance(item, dict):
            continue
        user_id = str(item.get("ig_user_id") or item.get("user_id") or "").strip()
        token = str(item.get("access_token") or "").strip()
        if user_id and token:
            social["instagram"].append({
                "connection_id": str(connection_id),
                "brand": str(item.get("brand") or _brand_for_route(config, "instagram", str(connection_id))).strip(),
                "user_id": user_id,
                "account_id": user_id,
                "access_token": token,
                "api_mode": str(item.get("api_mode") or instagram.get("api_mode") or "instagram_login").strip(),
                "graph_version": str(item.get("graph_version") or instagram.get("graph_version") or "v26.0").strip(),
                "display_name": str(item.get("display_name") or item.get("name") or "").strip(),
            })

    threads = config.get("threads") if isinstance(config.get("threads"), dict) else {}
    for connection_id, item in (threads.get("connections") or {}).items():
        if not isinstance(item, dict):
            continue
        user_id = str(item.get("threads_user_id") or item.get("user_id") or item.get("id") or "").strip()
        token = str(item.get("access_token") or "").strip()
        if user_id and token:
            social["threads"].append({
                "connection_id": str(connection_id),
                "brand": str(item.get("brand") or _brand_for_route(config, "threads", str(connection_id))).strip(),
                "user_id": user_id,
                "account_id": user_id,
                "access_token": token,
                "graph_version": str(item.get("graph_version") or threads.get("graph_version") or "v1.0").strip(),
                "display_name": str(item.get("display_name") or item.get("name") or "").strip(),
            })

    facebook = config.get("facebook") if isinstance(config.get("facebook"), dict) else {}
    for item in facebook.get("pages") or []:
        if not isinstance(item, dict):
            continue
        page_id = str(item.get("id") or "").strip()
        token = str(item.get("page_access_token") or item.get("access_token") or "").strip()
        if page_id and token:
            social["facebook"].append({
                "brand": _brand_for_route(config, "facebook", page_id),
                "user_id": page_id,
                "account_id": page_id,
                "page_id": page_id,
                "access_token": token,
                "page_access_token": token,
                "graph_version": str(facebook.get("graph_version") or "v26.0").strip(),
                "display_name": str(item.get("name") or "").strip(),
            })

    youtube = config.get("youtube") if isinstance(config.get("youtube"), dict) else {}
    client_id = str(youtube.get("client_id") or "").strip()
    client_secret = str(youtube.get("client_secret") or "").strip()
    channels = youtube.get("channels") if isinstance(youtube.get("channels"), list) else []
    for item in channels:
        if not isinstance(item, dict):
            continue
        channel_id = str(item.get("id") or "").strip()
        tokens = item.get("tokens") if isinstance(item.get("tokens"), dict) else {}
        access_token = str(tokens.get("access_token") or "").strip()
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        if channel_id and (access_token or (refresh_token and client_id and client_secret)):
            social["youtube"].append({
                "brand": _brand_for_route(config, "youtube", channel_id),
                "user_id": channel_id,
                "account_id": channel_id,
                "channel_id": channel_id,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "display_name": str(item.get("title") or "").strip(),
            })
    return social


def sync_social_connections_to_vps(config: dict | None = None) -> dict:
    """Merge local social credentials into the VPS worker secret file."""
    config = read_social_config() if config is None else config
    _, _, ssh_target, ssh_key, ssh_port, _ = _worker_config()
    if not ssh_target:
        raise RuntimeError("VPS social worker SSH chưa được cấu hình.")
    payload = _worker_social_payload(config)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    remote_tmp = "/tmp/aurex-social-worker-social-sync.json"
    try:
        scp_cmd = ["scp", "-P", ssh_port, "-o", "BatchMode=yes"]
        ssh_cmd = ["ssh", "-p", ssh_port, "-o", "BatchMode=yes"]
        if ssh_key:
            scp_cmd[1:1] = ["-i", ssh_key]
            ssh_cmd[1:1] = ["-i", ssh_key]
        subprocess.run([*scp_cmd, str(temp_path), f"{ssh_target}:{remote_tmp}"], check=True, capture_output=True, text=True, timeout=30)
        merge_script = r'''
import json, os, shutil, time
from pathlib import Path
target = Path("/etc/aurex-social-worker-social.json")
incoming_path = Path("/tmp/aurex-social-worker-social-sync.json")
def load(path):
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}
def key(item):
    return str(item.get("connection_id") or item.get("user_id") or item.get("account_id") or item.get("page_id") or item.get("channel_id") or "").strip()
existing = load(target)
incoming = load(incoming_path)
merged = dict(existing)
for platform in ("instagram", "threads", "facebook", "youtube"):
    old = existing.get(platform) if isinstance(existing.get(platform), list) else []
    new = incoming.get(platform) if isinstance(incoming.get(platform), list) else []
    by_key, order = {}, []
    for item in old:
        if isinstance(item, dict) and key(item):
            by_key[key(item)] = item
            order.append(key(item))
    for item in new:
        if isinstance(item, dict) and key(item):
            if key(item) not in order:
                order.append(key(item))
            by_key[key(item)] = item
    merged[platform] = [by_key[item_key] for item_key in order if item_key in by_key]
merged["version"] = max(int(existing.get("version") or 0), int(incoming.get("version") or 0), 2)
if target.exists():
    backup = str(target) + ".bak-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    shutil.copy2(str(target), backup)
with open(str(target), "w", encoding="utf-8") as f:
    json.dump(merged, f, ensure_ascii=False, indent=2)
os.chmod(str(target), 0o600)
try:
    incoming_path.unlink()
except OSError:
    pass
print(json.dumps({"ok": True, "counts": {p: len(merged.get(p) or []) for p in ("instagram","threads","facebook","youtube")}}))
'''
        result = subprocess.run([*ssh_cmd, ssh_target, f"python3 - <<'PY'\n{merge_script}\nPY"], check=True, capture_output=True, text=True, timeout=30)
        try:
            return json.loads(result.stdout.strip().splitlines()[-1])
        except Exception:
            return {"ok": True, "message": result.stdout.strip()}
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def update_worker_job(worker_id: str, values: dict) -> dict:
    worker_id = str(worker_id or "").strip()
    if not worker_id:
        raise ValueError("VPS worker job id is required.")
    if not isinstance(values, dict):
        raise ValueError("Worker job update payload must be an object.")
    return _worker_request(f"/jobs/{worker_id}/update", "POST", values)


def delete_worker_job_r2(worker_id: str) -> dict:
    worker_id = str(worker_id or "").strip()
    if not worker_id:
        raise ValueError("VPS worker job id is required.")
    return _worker_request(f"/jobs/{worker_id}/delete-r2", "POST", {})


def cleanup_worker_r2(limit: int = 50) -> dict:
    return _worker_request("/cleanup-r2", "POST", {"limit": max(1, min(int(limit or 50), 200))})


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
