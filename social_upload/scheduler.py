from __future__ import annotations

"""Persistent local scheduler for providers without native scheduling APIs."""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .metadata import record_scheduled_social_failure
from .remote_worker import flush_tiktok_watch_outbox

_LOCK = threading.RLock()
_FACEBOOK_COMMENT_LOCK = threading.Lock()
_STARTED = False
_STOP = threading.Event()
FACEBOOK_COMMENT_RETRY_LIMIT = 5
FACEBOOK_COMMENT_POLL_LIMIT = 50
FACEBOOK_PUBLISH_CHECK_RETRY_LIMIT = 5
FACEBOOK_PUBLISH_CHECK_BACKOFF_MAX_SECONDS = 900


def _data_root() -> Path:
    return Path(os.environ.get("AUREX_DATA_ROOT") or (Path.home() / "Library/Application Support/app.aurexvideo/studio")).expanduser().resolve()


def _queue_path() -> Path:
    return _data_root() / "social-schedule.json"


def _read() -> list[dict]:
    try:
        value = json.loads(_queue_path().read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write(items: list[dict]) -> None:
    path = _queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _comment_retry_is_transient(error: object) -> bool:
    message = str(error or "").casefold()
    if any(marker in message for marker in (
        "timeout", "timed out", "temporar", "rate limit", "too many requests",
        "connection", "service unavailable", "internal error", "network",
    )):
        return True
    return any(f"http {status}" in message for status in range(500, 600))


def _facebook_comment_next_attempt(now: datetime, attempts: int, *, base_seconds: int = 30) -> str:
    exponent = max(0, min(int(attempts or 1) - 1, 8))
    wait_seconds = min(FACEBOOK_PUBLISH_CHECK_BACKOFF_MAX_SECONDS, base_seconds * (2 ** exponent))
    return _iso(now + timedelta(seconds=wait_seconds))


def _update_scheduled_affiliate_record(job: dict, *, status: str, comment_id: str = "", error: str = "") -> None:
    """Mirror deferred-comment state to the associated content record when known."""
    record_id = str(job.get("content_record_id") or "").strip()
    if not record_id:
        return
    from . import affiliate_store

    affiliate_store.update_content_product(
        record_id,
        page_id=str(job.get("page_id") or ""),
        facebook_post_id=str(job.get("post_id") or ""),
        facebook_comment_id=comment_id,
        status=status,
        error=error,
    )


def poll_facebook_scheduled_affiliate_comments() -> dict:
    """Run at most one deferred Facebook comment poller in this process."""
    empty = {"checked": 0, "pending": 0, "commented": 0, "retried": 0, "failed": 0, "skipped": 0}
    if not _FACEBOOK_COMMENT_LOCK.acquire(blocking=False):
        return empty
    try:
        return _poll_facebook_scheduled_affiliate_comments()
    finally:
        _FACEBOOK_COMMENT_LOCK.release()


def _poll_facebook_scheduled_affiliate_comments() -> dict:
    """Post opted-in affiliate comments after Graph explicitly marks a Reel published.

    This deliberately operates from the persisted job snapshot. It never
    chooses an active Page: each job is revalidated against its Brand route.
    """
    from . import affiliate_store
    from . import facebook as facebook_module

    now = _utc_now()
    jobs = affiliate_store.list_pending_facebook_comment_jobs(
        limit=FACEBOOK_COMMENT_POLL_LIMIT,
        now=_iso(now),
    )
    summary = {"checked": len(jobs), "pending": 0, "commented": 0, "retried": 0, "failed": 0, "skipped": 0}
    if not jobs:
        return summary

    config = facebook_module.read_social_config()
    facebook = facebook_module.facebook_config(config)
    for job in jobs:
        job_id = str(job.get("id") or "").strip()
        placement = str(job.get("placement") or "").strip().lower()
        if not bool(job.get("auto_comment")) or placement not in {"first_comment", "caption_and_comment"}:
            affiliate_store.update_publish_job(job_id, status="scheduled_no_comment", error="", next_comment_attempt_at="")
            summary["skipped"] += 1
            continue

        affiliate_url = str(job.get("affiliate_url") or "").strip()
        post_id = str(job.get("post_id") or "").strip()
        if not affiliate_url or not post_id:
            error = "Scheduled affiliate comment thiếu link hoặc Facebook object id."
            affiliate_store.update_publish_job(job_id, status="comment_failed", error=error, next_comment_attempt_at="")
            _update_scheduled_affiliate_record(job, status="comment_failed", error=error)
            summary["failed"] += 1
            continue

        try:
            page = facebook_module.facebook_upload_page(
                config,
                facebook,
                {"brand": str(job.get("brand_id") or ""), "pageId": str(job.get("page_id") or "")},
            )
            access_token = facebook_module.facebook_page_access_token(facebook, page)
            metadata = facebook_module.facebook_object_metadata(
                facebook,
                post_id,
                access_token,
                fields="id,status,is_published",
            )
        except (RuntimeError, ValueError) as exc:
            error = str(exc)[:1000]
            attempts = int(job.get("publish_check_attempts") or 0) + 1
            if _comment_retry_is_transient(error) and attempts < FACEBOOK_PUBLISH_CHECK_RETRY_LIMIT:
                next_attempt = _facebook_comment_next_attempt(now, attempts)
                affiliate_store.update_publish_job(
                    job_id,
                    status="scheduled",
                    error=error,
                    publish_check_attempts=attempts,
                    next_comment_attempt_at=next_attempt,
                )
                _update_scheduled_affiliate_record(job, status="scheduled", error=error)
                summary["retried"] += 1
            else:
                affiliate_store.update_publish_job(
                    job_id,
                    status="comment_failed",
                    error=error,
                    publish_check_attempts=attempts,
                    next_comment_attempt_at="",
                )
                _update_scheduled_affiliate_record(job, status="comment_failed", error=error)
                summary["failed"] += 1
            continue

        if not facebook_module.facebook_object_is_published(metadata):
            # A Graph id is expected before a scheduled Reel is visible; it is
            # not sufficient proof that comments are accepted yet.
            attempts = int(job.get("publish_check_attempts") or 0) + 1
            affiliate_store.update_publish_job(
                job_id,
                status="scheduled",
                error="",
                publish_check_attempts=attempts,
                next_comment_attempt_at=_facebook_comment_next_attempt(now, attempts),
            )
            summary["pending"] += 1
            continue

        comment_target = facebook_module.facebook_full_post_id(facebook, post_id, page)
        comment_id, error = facebook_module.post_facebook_source_comment(
            facebook,
            comment_target,
            facebook_module.affiliate_comment_text(
                affiliate_url,
                product_name=str(job.get("product_name") or ""),
            ),
            access_token,
            attempts=1,
        )
        if comment_id:
            affiliate_store.update_publish_job(
                job_id,
                comment_id=comment_id,
                status="published",
                error="",
                comment_attempts=0,
                publish_check_attempts=0,
                next_comment_attempt_at="",
            )
            _update_scheduled_affiliate_record(job, status="published", comment_id=comment_id)
            summary["commented"] += 1
            continue

        error = str(error or "Facebook comment did not return an id.")[:1000]
        attempts = int(job.get("comment_attempts") or 0) + 1
        if _comment_retry_is_transient(error) and attempts < FACEBOOK_COMMENT_RETRY_LIMIT:
            next_attempt = _facebook_comment_next_attempt(now, attempts)
            affiliate_store.update_publish_job(
                job_id,
                status="comment_retry",
                error=error,
                comment_attempts=attempts,
                next_comment_attempt_at=next_attempt,
            )
            _update_scheduled_affiliate_record(job, status="comment_retry", error=error)
            summary["retried"] += 1
        else:
            affiliate_store.update_publish_job(
                job_id,
                status="comment_failed",
                error=error,
                comment_attempts=attempts,
                next_comment_attempt_at="",
            )
            _update_scheduled_affiliate_record(job, status="comment_failed", error=error)
            summary["failed"] += 1
    return summary


def schedule_upload(platform: str, payload: dict, scheduled_at: str) -> dict:
    run_at = _parse(scheduled_at)
    if run_at <= _utc_now():
        raise ValueError(f"Scheduled publish time for {platform} must be in the future.")
    item = {
        "id": "social_schedule_" + uuid.uuid4().hex[:16],
        "platform": platform,
        "payload": {key: value for key, value in payload.items() if key not in {"scheduledPublishAt", "publishAt", "scheduled_publish_at"}},
        "scheduledPublishAt": run_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "status": "queued",
        "createdAt": _utc_now().isoformat(timespec="seconds").replace("+00:00", "Z"),
        "updatedAt": _utc_now().isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    with _LOCK:
        items = _read()
        items.append(item)
        _write(items)
    start_scheduler()
    return item


def scheduled_uploads() -> list[dict]:
    with _LOCK:
        return _read()


def _run_item(item: dict) -> None:
    platform = item.get("platform")
    payload = dict(item.get("payload") or {})
    try:
        if platform == "instagram":
            from .instagram import instagram_upload_video
            result = instagram_upload_video(payload)
        elif platform == "threads":
            from .threads import threads_upload_video
            result = threads_upload_video(payload)
        elif platform == "tiktok":
            from .tiktok import tiktok_upload_video
            result = tiktok_upload_video(payload)
        elif platform == "binance":
            from .binance import binance_upload_video
            result = binance_upload_video(payload)
        else:
            raise ValueError(f"Unsupported scheduled platform: {platform}")
        status = "completed"
        error = ""
    except Exception as exc:
        result = {}
        status = "failed"
        error = str(exc)[:1000]
        if platform == "tiktok":
            try:
                record_scheduled_social_failure(
                    str(payload.get("project") or "").strip(),
                    "tiktok",
                    error,
                    scheduled_at=str(item.get("scheduledPublishAt") or "").strip(),
                    brand=str(payload.get("brand") or "").strip(),
                )
            except Exception:
                # The queue remains the source of truth if the project was
                # removed or its metadata cannot be written.
                pass
    with _LOCK:
        items = _read()
        for current in items:
            if current.get("id") == item.get("id"):
                current["status"] = status
                current["updatedAt"] = _utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")
                current["result"] = result
                if error:
                    current["error"] = error
                break
        _write(items)


def _worker() -> None:
    while not _STOP.wait(5):
        try:
            flush_tiktok_watch_outbox()
        except Exception:
            pass
        try:
            poll_facebook_scheduled_affiliate_comments()
        except Exception:
            # The scheduler must keep executing unrelated local jobs if an
            # affiliate DB or Graph request is temporarily unavailable.
            pass
        now = _utc_now()
        due = []
        with _LOCK:
            items = _read()
            changed = False
            for item in items:
                if item.get("status") == "queued" and _parse(item.get("scheduledPublishAt")) <= now:
                    item["status"] = "running"
                    item["updatedAt"] = now.isoformat(timespec="seconds").replace("+00:00", "Z")
                    due.append(dict(item))
                    changed = True
            if changed:
                _write(items)
        for item in due:
            _run_item(item)


def start_scheduler() -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
        threading.Thread(target=_worker, name="aurex-social-scheduler", daemon=True).start()
