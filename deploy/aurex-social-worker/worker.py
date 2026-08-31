#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(os.environ.get("WORKER_ROOT", "/opt/aurex-social-worker"))
DB = ROOT / "jobs.sqlite3"
MEDIA = ROOT / "media"
PORT = int(os.environ.get("WORKER_PORT", "8787"))
API_KEY = os.environ.get("WORKER_API_KEY", "")
INSTAGRAM_GRAPH_VERSION = os.environ.get("INSTAGRAM_GRAPH_VERSION", os.environ.get("META_GRAPH_VERSION", "v25.0"))
THREADS_GRAPH_VERSION = os.environ.get("THREADS_GRAPH_VERSION", "v1.0")
TIKTOK_RETRY_SECONDS = max(60, int(os.environ.get("TIKTOK_RETRY_SECONDS", "300")))
TIKTOK_POLL_SECONDS = max(30, int(os.environ.get("TIKTOK_POLL_SECONDS", "60")))
TIKTOK_MAX_ATTEMPTS = max(1, int(os.environ.get("TIKTOK_MAX_ATTEMPTS", "3")))
TIKTOK_SCHEDULE_GRACE_SECONDS = max(0, int(os.environ.get("TIKTOK_SCHEDULE_GRACE_SECONDS", "5")))
SOCIAL_MEDIA_RETRY_SECONDS = max(60, int(os.environ.get("SOCIAL_MEDIA_RETRY_SECONDS", "300")))
SOCIAL_MAX_ATTEMPTS = max(1, int(os.environ.get("SOCIAL_MAX_ATTEMPTS", "3")))
SOCIAL_CONNECTIONS_FILE = Path(
    os.environ.get("SOCIAL_CONNECTIONS_FILE", "/etc/aurex-social-worker-social.json")
)
ZERNIO_BASE_URL = os.environ.get("ZERNIO_BASE_URL", "https://zernio.com/api/v1").rstrip("/")
ZERNIO_CONNECTIONS_FILE = Path(
    os.environ.get("ZERNIO_CONNECTIONS_FILE", "/etc/aurex-social-worker-zernio.json")
)

PUBLISHED_STATUSES = {
    "published",
    "success",
    "succeeded",
    "complete",
    "completed",
    "publish_complete",
    "live",
}
FAILED_STATUSES = {"failed", "failure", "error", "expired", "rejected"}
CANCELLED_STATUSES = {"cancelled", "canceled", "deleted"}
TERMINAL_WATCH_STATES = {"published", "inbox_delivered", "cancelled", "failed"}
TRANSIENT_TERMS = (
    "capacity",
    "rate limit",
    "too many requests",
    "temporar",
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "connection timed out",
    "network is unreachable",
    "service unavailable",
    "temporarily unavailable",
    "try again",
    "5xx",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now() -> str:
    return utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


def iso_at(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if name not in columns:
        conn.execute("ALTER TABLE jobs ADD COLUMN {} {}".format(name, definition))


def _ensure_watch_column(conn: sqlite3.Connection, name: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tiktok_watches)").fetchall()}
    if name not in columns:
        conn.execute("ALTER TABLE tiktok_watches ADD COLUMN {} {}".format(name, definition))


def init_db() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    MEDIA.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY,
              platform TEXT NOT NULL,
              scheduled_at TEXT NOT NULL,
              caption TEXT NOT NULL,
              video_path TEXT NOT NULL,
              video_url TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL,
              result TEXT,
              error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              project TEXT NOT NULL DEFAULT '',
              brand TEXT NOT NULL DEFAULT '',
              account_id TEXT NOT NULL DEFAULT '',
              tiktok_settings TEXT NOT NULL DEFAULT '',
              expected_media_sha256 TEXT NOT NULL DEFAULT '',
              attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt_at TEXT,
              provider_post_id TEXT NOT NULL DEFAULT '',
              provider_status TEXT NOT NULL DEFAULT '',
              delivery_status TEXT NOT NULL DEFAULT '',
              phase TEXT NOT NULL DEFAULT 'queued',
              idempotency_key TEXT NOT NULL DEFAULT ''
            )"""
        )
        for name, definition in (
            ("project", "TEXT NOT NULL DEFAULT ''"),
            ("brand", "TEXT NOT NULL DEFAULT ''"),
            ("account_id", "TEXT NOT NULL DEFAULT ''"),
            ("video_url", "TEXT NOT NULL DEFAULT ''"),
            ("tiktok_settings", "TEXT NOT NULL DEFAULT ''"),
            ("expected_media_sha256", "TEXT NOT NULL DEFAULT ''"),
            ("attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("next_attempt_at", "TEXT"),
            ("provider_post_id", "TEXT NOT NULL DEFAULT ''"),
            ("provider_status", "TEXT NOT NULL DEFAULT ''"),
            ("delivery_status", "TEXT NOT NULL DEFAULT ''"),
            ("phase", "TEXT NOT NULL DEFAULT 'queued'"),
            ("idempotency_key", "TEXT NOT NULL DEFAULT ''"),
        ):
            _ensure_column(conn, name, definition)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS tiktok_watches (
              id TEXT PRIMARY KEY,
              post_id TEXT NOT NULL UNIQUE,
              job_id TEXT,
              project TEXT NOT NULL DEFAULT '',
              brand TEXT NOT NULL DEFAULT '',
              account_id TEXT NOT NULL DEFAULT '',
              state TEXT NOT NULL,
              provider_status TEXT NOT NULL DEFAULT '',
              delivery_status TEXT NOT NULL DEFAULT '',
              error TEXT,
              next_check_at TEXT,
              last_checked_at TEXT,
              result TEXT,
              retry_attempts INTEGER NOT NULL DEFAULT 0,
              next_retry_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )"""
        )
        _ensure_watch_column(conn, "retry_attempts", "INTEGER NOT NULL DEFAULT 0")
        _ensure_watch_column(conn, "next_retry_at", "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, scheduled_at, next_attempt_at)")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency "
            "ON jobs(idempotency_key) WHERE idempotency_key <> ''"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tiktok_watches_due ON tiktok_watches(state, next_check_at)")
    _recover_interrupted_jobs()
    _retire_legacy_tiktok_jobs()
    _recover_creator_inbox_watches()


def _recover_interrupted_jobs() -> None:
    """Recover work only when replaying it cannot duplicate a provider post.

    A worker restart must never blindly replay an unknown POST.  TikTok rows
    without a persisted provider post id are sent to manual review; provider
    POSTs are not assumed idempotent unless a post id was already persisted.
    """
    with db() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
        for row in rows:
            job = dict(row)
            if job["platform"] == "tiktok":
                if job.get("provider_post_id"):
                    conn.execute(
                        """UPDATE jobs SET status='monitoring', phase='monitoring',
                           next_attempt_at=?, updated_at=? WHERE id=?""",
                        (iso_at(utc_now() + timedelta(seconds=TIKTOK_POLL_SECONDS)), now(), job["id"]),
                    )
                else:
                    conn.execute(
                        """UPDATE jobs SET status='needs_review', phase='needs_review',
                           next_attempt_at=NULL, error=?, updated_at=? WHERE id=?""",
                        (
                            "Legacy TikTok job interrupted without a persisted Zernio post id; not replayed automatically.",
                            now(),
                            job["id"],
                        ),
                    )
            elif str(job.get("phase") or "") in {"queued", "claimed", "media"}:
                conn.execute(
                    """UPDATE jobs SET status='retry_wait', phase='media',
                       next_attempt_at=?, error=?, updated_at=? WHERE id=?""",
                    (
                        iso_at(utc_now() + timedelta(seconds=60)),
                            "Worker restarted before provider publish; safe pre-POST retry scheduled.",
                        now(),
                        job["id"],
                    ),
                )
            else:
                conn.execute(
                    """UPDATE jobs SET status='failed', phase='needs_review',
                       error=?, next_attempt_at=NULL, updated_at=? WHERE id=?""",
                    (
                        "Worker restarted while provider request was in progress; not replayed automatically.",
                        now(),
                        job["id"],
                    ),
                )


def _retire_legacy_tiktok_jobs() -> None:
    """Prevent legacy VPS TikTok jobs from creating posts after migration.

    New scheduled TikTok posts are created on Zernio and represented by a
    tiktok_watches row.  Rows left by the former VPS-creation flow therefore
    must never reach _create_tiktok_post.  A row with a persisted provider id
    is kept visible as monitoring; an unclaimed row without one is cancelled
    because replaying it could create a duplicate.
    """
    current = now()
    with db() as conn:
        conn.execute(
            """UPDATE jobs SET status='monitoring', phase='monitoring',
               next_attempt_at=NULL, updated_at=?
               WHERE platform='tiktok' AND status IN ('queued', 'retry_wait')
                 AND COALESCE(provider_post_id, '') <> ''""",
            (current,),
        )
        conn.execute(
            """UPDATE jobs SET status='cancelled', phase='cancelled',
               error=CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END,
               next_attempt_at=NULL, updated_at=?
               WHERE platform='tiktok' AND status IN ('queued', 'retry_wait')
                 AND COALESCE(provider_post_id, '') = ''""",
            (
                "Legacy TikTok VPS job retired; scheduled posts must be created on Zernio.",
                current,
            ),
        )


def _recover_creator_inbox_watches() -> None:
    """Re-check Creator Inbox deliveries after a worker restart.

    A legacy snapshot could have been marked published from Zernio's generic
    status even though TikTok only delivered it to Creator Inbox.  Keep those
    watches active until the platform post marker proves public delivery.
    """
    current = now()
    next_check = iso_at(utc_now() + timedelta(seconds=1))
    with db() as conn:
        conn.execute(
            """UPDATE tiktok_watches
               SET state='monitoring', next_check_at=?, updated_at=?
               WHERE state='published' AND delivery_status='CREATOR_INBOX'""",
            (next_check, current),
        )
        conn.execute(
            """UPDATE jobs
               SET status='monitoring', phase='monitoring', next_attempt_at=?, updated_at=?
               WHERE platform='tiktok' AND status='published'
                 AND delivery_status='CREATOR_INBOX'""",
            (next_check, current),
        )


def json_request(url: str, fields: Dict[str, Any], token: str) -> Dict[str, Any]:
    data = urlencode({**fields, "access_token": token}).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=120) as res:
            raw = res.read().decode("utf-8", "replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Graph API returned non-JSON HTTP {}: {}".format(res.status, raw[:1000])) from exc
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw[:1000]
        raise RuntimeError("Graph API HTTP {}: {}".format(exc.code, json.dumps(detail, ensure_ascii=False)[:1800])) from exc
    except URLError as exc:
        raise RuntimeError("Graph API connection error: {}".format(exc.reason)) from exc


def graph_get(url: str, fields: Dict[str, Any], token: str) -> Dict[str, Any]:
    query = urlencode({**fields, "access_token": token})
    req = Request(url + ("&" if "?" in url else "?") + query, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=120) as res:
            raw = res.read().decode("utf-8", "replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Graph API GET returned non-JSON HTTP {}: {}".format(res.status, raw[:1000])) from exc
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw[:1000]
        raise RuntimeError("Graph API GET HTTP {}: {}".format(exc.code, json.dumps(detail, ensure_ascii=False)[:1800])) from exc
    except URLError as exc:
        raise RuntimeError("Graph API GET connection error: {}".format(exc.reason)) from exc


class ZernioError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        response: Optional[Dict[str, Any]] = None,
        raw_response: str = "",
        retryable: bool = False,
        ambiguous: bool = False,
        phase: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response = response or {}
        self.raw_response = raw_response
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.phase = phase


def _error_values(value: Any, key: str = "") -> List[Tuple[str, str]]:
    if isinstance(value, dict):
        values: List[Tuple[str, str]] = []
        for child_key, child_value in value.items():
            values.extend(_error_values(child_value, str(child_key).lower()))
        return values
    if isinstance(value, list):
        values = []
        for child_value in value:
            values.extend(_error_values(child_value, key))
        return values
    if isinstance(value, (str, int, float)):
        return [(key, str(value))]
    return []


def _response_has_error(response: Dict[str, Any]) -> bool:
    if not isinstance(response, dict):
        return False
    containers: List[Dict[str, Any]] = [response]
    data = response.get("data")
    if isinstance(data, dict):
        containers.append(data)
    for container in containers:
        # A successful post/status response can contain provider-side
        # errorMessage/errorCategory inside post.platforms[]. Those are
        # publish results and must be parsed by _tiktok_snapshot, not treated
        # as a transport/API envelope error here.
        is_post_result = any(
            key in container
            for key in ("post", "existingPost", "platforms", "mediaItems", "_id", "scheduledFor")
        )
        if is_post_result:
            continue
        error = container.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").strip().casefold()
            message = str(error.get("message") or "").strip()
            if code not in {"", "ok", "0", "200"} or message:
                return True
        elif error:
            return True
        if container.get("errors") or container.get("success") is False or container.get("ok") is False:
            return True
        if container.get("errorMessage") or container.get("errorCategory"):
            return True
    return False


def _is_capacity(value: Any) -> bool:
    response = value.response if isinstance(value, ZernioError) else value
    values = _error_values(response)
    if isinstance(value, ZernioError) and value.raw_response:
        values.append(("raw_response", value.raw_response))
    text = " ".join(raw for _, raw in values).casefold()
    return "capacity" in text and ("tiktok" in text or "direct post" in text or "direct posting" in text)


def _is_transient_text(value: Any) -> bool:
    text = str(value or "").casefold()
    return any(term in text for term in TRANSIENT_TERMS)


def _zernio_request(
    url: str,
    method: str,
    body: Optional[Dict[str, Any]],
    config: Dict[str, str],
    *,
    headers: Optional[Dict[str, str]] = None,
    phase: str = "",
) -> Dict[str, Any]:
    encoded = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    request_headers = {"Authorization": "Bearer {}".format(config["api_key"]), "Accept": "application/json"}
    if encoded is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    request = Request(url, data=encoded, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8", "replace")
            status_code = response.status
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        retryable = exc.code in {408, 425, 429} or exc.code >= 500
        probe = ZernioError("capacity probe", response=parsed if isinstance(parsed, dict) else {}, raw_response=raw)
        ambiguous = method == "POST" and phase == "post" and not _is_capacity(probe)
        raise ZernioError(
            "Zernio HTTP {}: {}".format(exc.code, raw[:800]),
            status_code=exc.code,
            response=parsed if isinstance(parsed, dict) else {},
            raw_response=raw,
            retryable=retryable,
            ambiguous=ambiguous,
            phase=phase,
        ) from exc
    except (URLError, socket.timeout, TimeoutError) as exc:
        ambiguous = method == "POST" and phase == "post"
        raise ZernioError(
            "Zernio request failed: {}".format(exc),
            retryable=True,
            ambiguous=ambiguous,
            phase=phase,
        ) from exc
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ZernioError(
            "Zernio returned invalid JSON.",
            status_code=status_code,
            raw_response=raw,
            retryable=status_code >= 500,
            ambiguous=method == "POST" and phase == "post",
            phase=phase,
        ) from exc
    if not isinstance(parsed, dict):
        parsed = {"value": parsed}
    if _response_has_error(parsed):
        text = json.dumps(parsed, ensure_ascii=False)[:1000]
        probe = ZernioError("capacity probe", response=parsed, raw_response=raw)
        raise ZernioError(
            "Zernio rejected request: {}".format(text),
            status_code=status_code,
            response=parsed,
            raw_response=raw,
            retryable=status_code in {408, 425, 429} or status_code >= 500 or _is_transient_text(text),
            ambiguous=method == "POST" and phase == "post" and not _is_capacity(probe),
            phase=phase,
        )
    return parsed


def _put_file(url: str, path: Path) -> None:
    request = Request(url, data=path.read_bytes(), headers={"Content-Type": "video/mp4"}, method="PUT")
    try:
        with urlopen(request, timeout=600) as response:
            response.read()
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise ZernioError(
            "Zernio media upload HTTP {}: {}".format(exc.code, raw[:800]),
            status_code=exc.code,
            raw_response=raw,
            retryable=exc.code in {408, 425, 429} or exc.code >= 500,
            phase="media",
        ) from exc
    except (URLError, socket.timeout, TimeoutError) as exc:
        raise ZernioError("Zernio media upload failed: {}".format(exc), retryable=True, phase="media") from exc


def _unwrap(value: Dict[str, Any], key: str) -> Any:
    data = value.get("data") if isinstance(value.get("data"), dict) else value
    return data.get(key) if isinstance(data, dict) else None


def _post_from_response(response: Dict[str, Any]) -> Dict[str, Any]:
    post = _unwrap(response, "post")
    return post if isinstance(post, dict) else response


def _post_id(response: Dict[str, Any]) -> str:
    post = _post_from_response(response)
    return str(post.get("_id") or post.get("id") or response.get("postId") or "").strip()


def _post_url(value: Dict[str, Any]) -> str:
    post = _post_from_response(value)
    for key in ("platformPostUrl", "url", "permalink"):
        candidate = post.get(key)
        if isinstance(candidate, str) and candidate.startswith("http"):
            return candidate
        if isinstance(candidate, dict):
            for nested in candidate.values():
                if isinstance(nested, str) and nested.startswith("http"):
                    return nested
    return ""


def _platform_post_id(value: Any, platform: str = "tiktok") -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("platformPostId", "platform_post_id", "postId", "post_id"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        if isinstance(candidate, dict):
            for nested_key in (platform, platform.casefold(), platform.upper(), "id"):
                nested = candidate.get(nested_key)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return ""


def _tiktok_target(response: Dict[str, Any]) -> Dict[str, Any]:
    post = _post_from_response(response)
    for container in (post.get("platforms"), response.get("platforms")):
        if isinstance(container, list):
            for item in container:
                if isinstance(item, dict) and str(item.get("platform") or "").casefold() == "tiktok":
                    return item
    return {}


def _tiktok_snapshot(response: Dict[str, Any]) -> Dict[str, Any]:
    post = _post_from_response(response)
    target = _tiktok_target(response)
    post_status = str(post.get("status") or response.get("status") or "").strip().casefold()
    target_status = str(target.get("status") or "").strip().casefold()
    scheduled_for = str(
        target.get("scheduledFor")
        or post.get("scheduledFor")
        or response.get("scheduledFor")
        or ""
    ).strip()
    url = _post_url(response)
    settings = target.get("platformSpecificData") if isinstance(target.get("platformSpecificData"), dict) else {}
    tiktok_settings = settings.get("tiktokSettings") if isinstance(settings.get("tiktokSettings"), dict) else {}
    delivery = "CREATOR_INBOX" if tiktok_settings.get("draft") is True else "DIRECT_POST"
    platform_post_id = (
        _platform_post_id(target)
        or _platform_post_id(settings)
        or _platform_post_id(post)
        or _platform_post_id(response)
    )
    error = (
        target.get("error")
        or target.get("errorMessage")
        or post.get("error")
        or post.get("errorMessage")
        or response.get("error")
        or response.get("errorMessage")
        or ""
    )
    error_category = str(
        target.get("errorCategory")
        or post.get("errorCategory")
        or response.get("errorCategory")
        or ""
    ).strip()
    error_source = str(
        target.get("errorSource")
        or post.get("errorSource")
        or response.get("errorSource")
        or ""
    ).strip()
    if not error and error_category:
        error = error_category
    error_text = json.dumps(error, ensure_ascii=False) if isinstance(error, (dict, list)) else str(error or "")
    statuses = {post_status, target_status}
    if platform_post_id.startswith("v_pub_url~"):
        state = "PUBLISHED"
        delivery = "DIRECT_POST"
    elif platform_post_id.startswith("v_inbox_url~"):
        state = "INBOX_DELIVERED"
        delivery = "CREATOR_INBOX"
    elif statuses.intersection(CANCELLED_STATUSES):
        state = "CANCELLED"
    elif statuses.intersection(FAILED_STATUSES) or error_text.strip():
        state = "FAILED"
    elif delivery == "CREATOR_INBOX":
        state = "DRAFT_PENDING"
    elif url or statuses.intersection(PUBLISHED_STATUSES):
        state = "PUBLISHED"
    elif post_status == "draft":
        state = "DRAFT_PENDING"
    else:
        state = "PENDING"
    provider_status = target_status or post_status or "pending"
    return {
        "state": state,
        "provider_status": provider_status,
        "delivery_status": delivery,
        "url": url,
        "error": error_text[:1600],
        "error_category": error_category,
        "error_source": error_source,
        "scheduled_for": scheduled_for,
        "post_id": _post_id(response),
        "platform_post_id": platform_post_id,
    }


def _next_tiktok_check(snapshot: Dict[str, Any]) -> str:
    scheduled_for = str(snapshot.get("scheduled_for") or "").strip()
    if scheduled_for:
        try:
            scheduled_at = parse_time(scheduled_for)
        except (TypeError, ValueError, OverflowError):
            scheduled_at = None
        if scheduled_at is not None and scheduled_at > utc_now():
            return iso_at(scheduled_at + timedelta(seconds=TIKTOK_SCHEDULE_GRACE_SECONDS))
    return iso_at(utc_now() + timedelta(seconds=TIKTOK_POLL_SECONDS))


def _load_tiktok_connections() -> Dict[str, Dict[str, str]]:
    if not ZERNIO_CONNECTIONS_FILE.is_file():
        return {}
    try:
        value = json.loads(ZERNIO_CONNECTIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    connections = value.get("connections") if isinstance(value, dict) else None
    if not isinstance(connections, dict):
        connections = value if isinstance(value, dict) else {}
    return {
        str(key).strip().casefold(): {
            str(field): str(raw_value or "").strip()
            for field, raw_value in item.items()
            if isinstance(item, dict)
        }
        for key, item in connections.items()
        if isinstance(item, dict)
    }


def _tiktok_connection(brand: str = "", account_id: str = "") -> Dict[str, str]:
    brand_key = str(brand or "").strip().casefold()
    account_key = str(account_id or "").strip()
    connections = _load_tiktok_connections()
    selected: Dict[str, str] = {}
    if brand_key:
        selected = dict(connections.get(brand_key) or {})
    if not selected and account_key:
        for item in connections.values():
            if str(item.get("account_id") or "").strip() == account_key:
                selected = dict(item)
                break
    if not selected:
        selected = {
            "api_key": os.environ.get("ZERNIO_API_KEY", "").strip(),
            "account_id": os.environ.get("ZERNIO_TIKTOK_ACCOUNT_ID", "").strip(),
            "base_url": os.environ.get("ZERNIO_BASE_URL", ZERNIO_BASE_URL).strip().rstrip("/"),
        }
    selected["base_url"] = str(selected.get("base_url") or ZERNIO_BASE_URL).strip().rstrip("/")
    selected["api_key"] = str(selected.get("api_key") or "").strip()
    selected["account_id"] = str(selected.get("account_id") or "").strip()
    if account_key and selected["account_id"] and selected["account_id"] != account_key:
        raise RuntimeError("TikTok accountId không khớp connection trên VPS.")
    if not selected["api_key"] or not selected["account_id"]:
        raise RuntimeError("VPS chưa cấu hình Zernio API key và TikTok account.")
    return selected


def upload_r2(path: Path, job_id: str) -> str:
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required for R2 uploads") from exc
    account = os.environ["R2_ACCOUNT_ID"]
    bucket = os.environ["R2_BUCKET"]
    key = "social-worker/{}-{}".format(job_id, path.name)
    client = boto3.client(
        "s3",
        endpoint_url="https://{}.r2.cloudflarestorage.com".format(account),
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": "video/mp4"})
    return os.environ["R2_PUBLIC_BASE_URL"].rstrip("/") + "/" + key


def _tiktok_settings(job: Dict[str, Any]) -> Dict[str, Any]:
    raw = job.get("tiktok_settings") or ""
    if isinstance(raw, dict):
        return dict(raw)
    if not str(raw).strip():
        return {}
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _set_job_phase(job_id: str, phase: str) -> None:
    with db() as conn:
        conn.execute("UPDATE jobs SET phase=?, updated_at=? WHERE id=?", (phase, now(), job_id))


def _validated_public_video_url(value: Any) -> str:
    video_url = str(value or "").strip()
    if not video_url or "\\" in video_url or any(character.isspace() or ord(character) < 32 for character in video_url):
        raise ValueError("Scheduled social job cần public video URL từ R2.")
    try:
        parsed = urlparse(video_url)
        hostname = parsed.hostname or ""
        parsed.port
    except ValueError as exc:
        raise ValueError("Public video URL không hợp lệ.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
        raise ValueError("Public video URL phải là HTTP hoặc HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError("Public video URL không được chứa thông tin đăng nhập.")
    return video_url


def _load_social_connections() -> Dict[str, List[Dict[str, str]]]:
    try:
        raw = json.loads(SOCIAL_CONNECTIONS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, List[Dict[str, str]]] = {}
    for platform in ("instagram", "threads"):
        values = raw.get(platform)
        if isinstance(values, dict):
            values = list(values.values())
        if not isinstance(values, list):
            continue
        result[platform] = [
            {str(key): str(item.get(key) or "").strip() for key in item}
            for item in values
            if isinstance(item, dict)
        ]
    return result


def _social_connection(
    platform: str,
    account_id: str = "",
    brand: str = "",
) -> Dict[str, str]:
    requested = str(account_id or "").strip()
    brand_key = str(brand or "").strip().casefold()
    entries = _load_social_connections().get(platform, [])
    account_matches = [
        item for item in entries
        if str(item.get("user_id") or item.get("account_id") or "").strip() == requested
    ] if requested else []
    selected: Dict[str, str] = {}
    if requested:
        for item in account_matches:
            item_brand = str(item.get("brand") or "").strip().casefold()
            if not brand_key or not item_brand or item_brand == brand_key:
                selected = item
                break
        if account_matches and not selected:
            raise RuntimeError(
                "{} accountId không được bind với Brand {} trên VPS.".format(platform, brand or "đã yêu cầu")
            )
    elif brand_key:
        selected = next(
            (
                item for item in entries
                if str(item.get("brand") or "").strip().casefold() == brand_key
            ),
            {},
        )
    if entries and (brand_key or requested) and not selected:
        raise RuntimeError(
            "{} connection cho Brand {} / account {} chưa được cấu hình trên VPS.".format(
                platform,
                brand or "<trống>",
                requested or "<trống>",
            )
        )
    if selected:
        user_id = str(selected.get("user_id") or selected.get("account_id") or "").strip()
        token = str(selected.get("access_token") or "").strip()
        if not user_id or not token:
            raise RuntimeError("{} connection trên VPS chưa có user id hoặc access token.".format(platform))
        return {
            "user_id": user_id,
            "access_token": token,
            "brand": str(selected.get("brand") or "").strip(),
            "api_mode": str(selected.get("api_mode") or "instagram_login").strip().lower(),
            "graph_version": str(selected.get("graph_version") or "").strip(),
        }
    if platform == "instagram":
        user_id = os.environ.get("INSTAGRAM_USER_ID", "").strip()
        token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
        api_mode = os.environ.get("INSTAGRAM_API_MODE", "instagram_login").strip().lower()
        graph_version = os.environ.get("INSTAGRAM_GRAPH_VERSION", INSTAGRAM_GRAPH_VERSION).strip()
    else:
        user_id = os.environ.get("THREADS_USER_ID", "").strip()
        token = os.environ.get("THREADS_ACCESS_TOKEN", "").strip()
        api_mode = "threads"
        graph_version = os.environ.get("THREADS_GRAPH_VERSION", THREADS_GRAPH_VERSION).strip()
    if requested and user_id and requested != user_id:
        raise RuntimeError(
            "{} accountId không khớp tài khoản được cấu hình trên VPS.".format(platform)
        )
    if not user_id or not token:
        raise RuntimeError("VPS chưa cấu hình {} user id và access token.".format(platform))
    return {
        "user_id": user_id,
        "access_token": token,
        "brand": "",
        "api_mode": api_mode,
        "graph_version": graph_version,
    }


def _validate_social_account(platform: str, account_id: str, brand: str = "") -> None:
    # Blank accountId is retained as a backwards-compatible path for old rows.
    # New callers resolve a Brand-scoped connection and fail closed if the VPS
    # has no matching credentials.
    if str(account_id or "").strip() or str(brand or "").strip():
        _social_connection(platform, account_id, brand)


def _verify_public_video_url(video_url: str) -> None:
    """Prove R2 is readable before any provider POST can begin."""
    request = Request(
        video_url,
        headers={"Accept": "video/mp4", "Range": "bytes=0-0", "User-Agent": "AurexSocialWorker/1"},
        method="HEAD",
    )
    try:
        with urlopen(request, timeout=30) as response:
            if response.status >= 400:
                raise RuntimeError("R2 public video URL returned HTTP {}.".format(response.status))
    except HTTPError as exc:
        if exc.code != 405:
            raise RuntimeError("R2 public video URL returned HTTP {}.".format(exc.code)) from exc
        # A few public gateways disable HEAD; a one-byte ranged GET is still a
        # bounded read and keeps the check before the non-idempotent provider POST.
        fallback = Request(
            video_url,
            headers={"Accept": "video/mp4", "Range": "bytes=0-0", "User-Agent": "AurexSocialWorker/1"},
            method="GET",
        )
        try:
            with urlopen(fallback, timeout=30) as response:
                if response.status >= 400:
                    raise RuntimeError("R2 public video URL returned HTTP {}.".format(response.status))
                response.read(1)
        except HTTPError as fallback_exc:
            raise RuntimeError("R2 public video URL returned HTTP {}.".format(fallback_exc.code)) from fallback_exc
        except URLError as fallback_exc:
            raise RuntimeError("R2 public video URL is not reachable: {}".format(fallback_exc.reason)) from fallback_exc
    except URLError as exc:
        raise RuntimeError("R2 public video URL is not reachable: {}".format(exc.reason)) from exc


def _create_tiktok_post(job: Dict[str, Any]) -> Dict[str, Any]:
    path = Path(job["video_path"])
    connection = _tiktok_connection(job.get("brand", ""), job.get("account_id", ""))
    _set_job_phase(job["id"], "media")
    public_url = upload_r2(path, job["id"])
    settings = _tiktok_settings(job)
    requested_draft = bool(settings.get("draft"))
    post_body: Dict[str, Any] = {
        "content": str(job.get("caption") or ""),
        "mediaItems": [{"type": "video", "url": public_url}],
        "platforms": [{"platform": "tiktok", "accountId": connection["account_id"]}],
    }
    if settings:
        post_body["tiktokSettings"] = settings
    if not requested_draft:
        post_body["publishNow"] = True
    request_id = "aurex-social-worker-{}".format(job["id"])
    _set_job_phase(job["id"], "post")
    try:
        response = _zernio_request(
            connection["base_url"] + "/posts",
            "POST",
            post_body,
            connection,
            headers={"X-Request-ID": request_id},
            phase="post",
        )
        used_creator_inbox = requested_draft
    except ZernioError as exc:
        if not _is_capacity(exc) or requested_draft:
            raise
        fallback_body = dict(post_body)
        fallback_body["tiktokSettings"] = dict(settings)
        fallback_body["tiktokSettings"]["draft"] = True
        fallback_body.pop("publishNow", None)
        response = _zernio_request(
            connection["base_url"] + "/posts",
            "POST",
            fallback_body,
            connection,
            headers={"X-Request-ID": request_id + "-creator-inbox"},
            phase="post",
        )
        used_creator_inbox = True
    post_id = _post_id(response)
    if not post_id:
        raise RuntimeError("Zernio tạo TikTok post không trả về post id.")
    snapshot = _tiktok_snapshot(response)
    if used_creator_inbox:
        snapshot["state"] = "DRAFT_PENDING"
        snapshot["delivery_status"] = "CREATOR_INBOX"
    elif snapshot["state"] == "FAILED":
        raise RuntimeError(snapshot["error"] or "Zernio TikTok post failed.")
    elif snapshot["state"] != "PUBLISHED":
        snapshot["state"] = "PENDING"
    snapshot["post_id"] = post_id
    snapshot["video_url"] = public_url
    snapshot["fallback_reason"] = "TIKTOK_DIRECT_POST_CAPACITY" if used_creator_inbox and not requested_draft else ""
    return snapshot


def _tiktok_status(post_id: str, brand: str, account_id: str) -> Dict[str, Any]:
    connection = _tiktok_connection(brand, account_id)
    response = _zernio_request(
        connection["base_url"] + "/posts/" + post_id,
        "GET",
        None,
        connection,
        phase="status",
    )
    snapshot = _tiktok_snapshot(response)
    snapshot["post_id"] = post_id
    return snapshot


def _validate_media(job: Dict[str, Any]) -> Path:
    path = Path(job["video_path"])
    if not path.is_file():
        raise FileNotFoundError(str(path))
    expected = str(job.get("expected_media_sha256") or "").strip().lower()
    if expected:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError("Media checksum mismatch before publish.")
    return path


def graph_create_publish(
    platform: str,
    caption: str,
    video_url: str,
    *,
    account_id: str = "",
    brand: str = "",
) -> Dict[str, Any]:
    connection = _social_connection(platform, account_id, brand)
    user_id = connection["user_id"]
    token = connection["access_token"]
    if platform == "instagram":
        graph_version = connection["graph_version"] or INSTAGRAM_GRAPH_VERSION
        if not graph_version.startswith("v"):
            graph_version = "v" + graph_version
        graph_host = "https://graph.facebook.com" if connection["api_mode"] == "facebook_login" else "https://graph.instagram.com"
        base = "{}/{}/{}".format(graph_host, graph_version, user_id)
        created = json_request(
            base + "/media",
            {"media_type": "REELS", "video_url": video_url, "caption": caption, "share_to_feed": "true"},
            token,
        )
        container = created.get("id")
        if not container:
            raise RuntimeError("Instagram container creation failed: {}".format(created))
        for _ in range(60):
            status = graph_get(
                "https://graph.instagram.com/{}/{}".format(INSTAGRAM_GRAPH_VERSION, container),
                {"fields": "status_code,status"},
                token,
            )
            if status.get("status_code") == "FINISHED":
                break
            if status.get("status_code") in {"ERROR", "EXPIRED"}:
                raise RuntimeError("Instagram container failed: {}".format(status))
            time.sleep(5)
        published = json_request(base + "/media_publish", {"creation_id": container}, token)
        media_id = str(published.get("id") or "").strip()
        if not media_id:
            raise RuntimeError("Instagram publish returned no media id: {}".format(published))
        return {"platform": platform, "container_id": container, "media_id": media_id, "state": "PUBLISHED"}
    if platform == "threads":
        base = "https://graph.threads.net"
        created = json_request(
            base + "/me/threads",
            {"media_type": "VIDEO", "video_url": video_url, "text": caption},
            token,
        )
        container = created.get("id")
        if not container:
            raise RuntimeError("Threads container creation failed: {}".format(created))
        for _ in range(60):
            status = graph_get(base + "/" + container, {"fields": "status,error_message"}, token)
            if status.get("status") in {"FINISHED", "PUBLISHED"}:
                break
            if status.get("status") in {"ERROR", "EXPIRED"}:
                raise RuntimeError("Threads container failed: {}".format(status))
            time.sleep(5)
        published = json_request(base + "/me/threads_publish", {"creation_id": container}, token)
        media_id = str(published.get("id") or "").strip()
        if not media_id:
            raise RuntimeError("Threads publish returned no media id: {}".format(published))
        return {"platform": platform, "container_id": container, "media_id": media_id, "state": "PUBLISHED"}
    raise ValueError("Unsupported scheduled platform: {}".format(platform))


def execute(job: Dict[str, Any]) -> Dict[str, Any]:
    if job["platform"] == "tiktok":
        path = _validate_media(job)
        return _create_tiktok_post(job)
    if job["platform"] not in {"instagram", "threads"}:
        raise ValueError("Unsupported scheduled platform: {}".format(job["platform"]))
    # New schedules already contain a public R2 URL. The worker only verifies
    # that URL and calls the provider; the legacy path remains solely for old
    # rows created before the R2-at-schedule contract.
    job["phase"] = "media"
    _set_job_phase(job["id"], "media")
    url = str(job.get("video_url") or "").strip()
    if url:
        url = _validated_public_video_url(url)
        _verify_public_video_url(url)
    else:
        path = _validate_media(job)
        url = upload_r2(path, job["id"])
    job["phase"] = "provider"
    _set_job_phase(job["id"], "provider")
    return {
        **graph_create_publish(
            job["platform"],
            job["caption"],
            url,
            account_id=str(job.get("account_id") or ""),
            brand=str(job.get("brand") or ""),
        ),
        "video_url": url,
    }


def _claim_due_jobs() -> List[Dict[str, Any]]:
    current = now()
    due: List[Dict[str, Any]] = []
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM jobs
               WHERE platform <> 'tiktok'
                 AND ((status='queued' AND scheduled_at <= ?)
                  OR (status='retry_wait' AND COALESCE(next_attempt_at, scheduled_at) <= ?))
               ORDER BY scheduled_at, created_at""",
            (current, current),
        ).fetchall()
        for row in rows:
            job = dict(row)
            attempts = int(job.get("attempts") or 0) + 1
            updated = conn.execute(
                """UPDATE jobs SET status='running', phase='running', attempts=?,
                   updated_at=? WHERE id=? AND status IN ('queued','retry_wait')""",
                (attempts, current, job["id"]),
            )
            if updated.rowcount == 1:
                job["attempts"] = attempts
                job["phase"] = "running"
                due.append(job)
    return due


def _record_tiktok_watch(
    post_id: str,
    *,
    job_id: str = "",
    project: str = "",
    brand: str = "",
    account_id: str = "",
    scheduled_for: str = "",
    snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snapshot_provided = snapshot is not None
    snapshot = dict(snapshot or {"state": "PENDING", "provider_status": "pending", "delivery_status": "DIRECT_POST"})
    if scheduled_for and not snapshot.get("scheduled_for"):
        snapshot["scheduled_for"] = str(scheduled_for).strip()
    state = str(snapshot.get("state") or "PENDING")
    watch_state = {
        "PUBLISHED": "published",
        "INBOX_DELIVERED": "inbox_delivered",
        "CANCELLED": "cancelled",
        "FAILED": "failed",
    }.get(state, "monitoring")
    current = now()
    next_check = (
        None
        if watch_state in {"published", "inbox_delivered", "cancelled"}
        else _next_tiktok_check(snapshot)
    )
    result_json = json.dumps(snapshot, ensure_ascii=False)
    with db() as conn:
        existing = conn.execute("SELECT * FROM tiktok_watches WHERE post_id=?", (post_id,)).fetchone()
        if existing:
            existing_state = str(existing["state"] or "")
            stored_result = _json_value(existing["result"])
            stored_result = dict(stored_result) if isinstance(stored_result, dict) else {}
            schedule_added = bool(
                scheduled_for and not str(stored_result.get("scheduled_for") or "").strip()
            )
            # /watch-tiktok is an idempotent registration endpoint.  A retry
            # from the local outbox must not resurrect a terminal watch or
            # erase a pending retry window.
            preserve_existing = (
                not snapshot_provided
                or (
                    existing_state in TERMINAL_WATCH_STATES
                    and watch_state not in TERMINAL_WATCH_STATES
                )
            )
            if preserve_existing:
                merged_result = dict(stored_result)
                if schedule_added:
                    merged_result["scheduled_for"] = str(scheduled_for).strip()
                preserved_next_check = existing["next_check_at"]
                if existing_state == "monitoring" and schedule_added:
                    preserved_next_check = _next_tiktok_check(merged_result)
                if existing_state in TERMINAL_WATCH_STATES:
                    preserved_next_check = None
                conn.execute(
                    """UPDATE tiktok_watches
                       SET job_id=COALESCE(NULLIF(?, ''), job_id),
                           project=COALESCE(NULLIF(?, ''), project),
                           brand=COALESCE(NULLIF(?, ''), brand),
                           account_id=COALESCE(NULLIF(?, ''), account_id),
                           next_check_at=?, result=?, updated_at=?
                       WHERE post_id=?""",
                    (
                        job_id,
                        project,
                        brand,
                        account_id,
                        preserved_next_check,
                        json.dumps(merged_result, ensure_ascii=False),
                        current,
                        post_id,
                    ),
                )
                return {
                    "id": existing["id"],
                    "post_id": post_id,
                    **merged_result,
                    "state": existing_state,
                }
            conn.execute(
                """UPDATE tiktok_watches
                   SET job_id=COALESCE(NULLIF(?, ''), job_id),
                       project=COALESCE(NULLIF(?, ''), project),
                       brand=COALESCE(NULLIF(?, ''), brand),
                       account_id=COALESCE(NULLIF(?, ''), account_id),
                       state=?, provider_status=?, delivery_status=?, error=?,
                       next_check_at=?, next_retry_at=NULL, result=?, updated_at=?
                   WHERE post_id=?""",
                (
                    job_id,
                    project,
                    brand,
                    account_id,
                    watch_state,
                    str(snapshot.get("provider_status") or ""),
                    str(snapshot.get("delivery_status") or ""),
                    str(snapshot.get("error") or "")[:1600],
                    next_check,
                    result_json,
                    current,
                    post_id,
                ),
            )
            watch_id = existing["id"]
        else:
            watch_id = "tiktok_watch_" + uuid.uuid4().hex[:16]
            conn.execute(
                """INSERT INTO tiktok_watches
                   (id, post_id, job_id, project, brand, account_id, state,
                    provider_status, delivery_status, error, next_check_at,
                    last_checked_at, result, retry_attempts, next_retry_at,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    watch_id,
                    post_id,
                    job_id or None,
                    project,
                    brand,
                    account_id,
                    watch_state,
                    str(snapshot.get("provider_status") or ""),
                    str(snapshot.get("delivery_status") or ""),
                    str(snapshot.get("error") or "")[:1600],
                    next_check,
                    None,
                    result_json,
                    0,
                    None,
                    current,
                    current,
                ),
            )
    return {"id": watch_id, "post_id": post_id, "state": watch_state, **snapshot}


def _job_succeeded(job: Dict[str, Any], result: Dict[str, Any]) -> None:
    if job["platform"] == "tiktok":
        post_id = str(result.get("post_id") or "")
        if post_id:
            _record_tiktok_watch(
                post_id,
                job_id=job["id"],
                project=str(job.get("project") or ""),
                brand=str(job.get("brand") or ""),
                account_id=str(job.get("account_id") or ""),
                snapshot=result,
            )
        state = "published" if result.get("state") == "PUBLISHED" else "monitoring"
        phase = "published" if state == "published" else "monitoring"
        next_check = None if state == "published" else iso_at(utc_now() + timedelta(seconds=TIKTOK_POLL_SECONDS))
        with db() as conn:
            conn.execute(
                """UPDATE jobs
                   SET status=?, phase=?, result=?, error=NULL, next_attempt_at=?,
                       provider_post_id=?, provider_status=?, delivery_status=?, updated_at=?
                   WHERE id=?""",
                (
                    state,
                    phase,
                    json.dumps(result, ensure_ascii=False),
                    next_check,
                    post_id,
                    str(result.get("provider_status") or ""),
                    str(result.get("delivery_status") or ""),
                    now(),
                    job["id"],
                ),
            )
        return
    with db() as conn:
        conn.execute(
            """UPDATE jobs SET status='published', phase='published', result=?,
               provider_post_id=?, provider_status=?, error=NULL, updated_at=?
               WHERE id=?""",
            (
                json.dumps(result, ensure_ascii=False),
                str(result.get("media_id") or result.get("post_id") or "").strip(),
                str(result.get("state") or "").strip(),
                now(),
                job["id"],
            ),
        )


def _job_failed(job: Dict[str, Any], exc: Exception) -> None:
    error = str(exc)[:2000]
    retryable = bool(getattr(exc, "retryable", False)) and not bool(getattr(exc, "ambiguous", False))
    attempts = int(job.get("attempts") or 0)
    if job["platform"] == "tiktok" and retryable and attempts < TIKTOK_MAX_ATTEMPTS:
        retry_at = iso_at(utc_now() + timedelta(seconds=TIKTOK_RETRY_SECONDS))
        with db() as conn:
            conn.execute(
                """UPDATE jobs SET status='retry_wait', phase='media', error=?, next_attempt_at=?,
                   updated_at=? WHERE id=?""",
                (error, retry_at, now(), job["id"]),
            )
        print("job {} {} retry scheduled at {}: {}".format(job["id"], job["platform"], retry_at, error), flush=True)
        return
    if job["platform"] in {"instagram", "threads"} and job.get("phase") == "media" and attempts < SOCIAL_MAX_ATTEMPTS:
        retry_at = iso_at(utc_now() + timedelta(seconds=SOCIAL_MEDIA_RETRY_SECONDS))
        with db() as conn:
            conn.execute(
                """UPDATE jobs SET status='retry_wait', phase='media', error=?,
                   next_attempt_at=?, updated_at=? WHERE id=?""",
                (error, retry_at, now(), job["id"]),
            )
        print("job {} {} media URL retry scheduled at {}: {}".format(job["id"], job["platform"], retry_at, error), flush=True)
        return
    if job["platform"] == "tiktok" and bool(getattr(exc, "ambiguous", False)):
        error = "Zernio không xác định request đã tạo post hay chưa; không tự retry để tránh đăng trùng. " + error
    phase = "needs_review" if job["platform"] == "tiktok" and bool(getattr(exc, "ambiguous", False)) else "failed"
    with db() as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', phase=?, error=?, next_attempt_at=NULL, updated_at=? WHERE id=?",
            (phase, error, now(), job["id"]),
        )
    print("job {} {} failed: {}".format(job["id"], job["platform"], error), flush=True)


def _update_watch(
    watch_id: str,
    *,
    state: str,
    snapshot: Dict[str, Any],
    next_check_at: Optional[str],
    error: str = "",
    retry_attempts: Optional[int] = None,
    next_retry_at: Optional[str] = None,
) -> None:
    current = now()
    with db() as conn:
        conn.execute(
            """UPDATE tiktok_watches SET state=?, provider_status=?, delivery_status=?,
               error=?, next_check_at=?, retry_attempts=COALESCE(?, retry_attempts),
               next_retry_at=?, last_checked_at=?, result=?, updated_at=?
               WHERE id=?""",
            (
                state,
                str(snapshot.get("provider_status") or ""),
                str(snapshot.get("delivery_status") or ""),
                (error or str(snapshot.get("error") or ""))[:1600],
                next_check_at,
                retry_attempts,
                next_retry_at,
                current,
                json.dumps(snapshot, ensure_ascii=False),
                current,
                watch_id,
            ),
        )


def _retryable_tiktok_failure(snapshot: Dict[str, Any]) -> bool:
    if snapshot.get("state") != "FAILED":
        return False
    text = "{} {}".format(snapshot.get("provider_status") or "", snapshot.get("error") or "")
    return _is_transient_text(text)


def _is_missing_tiktok_post(exc: Exception) -> bool:
    if isinstance(exc, ZernioError) and exc.status_code == 404:
        return True
    values = [str(exc)]
    if isinstance(exc, ZernioError):
        values.append(json.dumps(exc.response, ensure_ascii=False))
        values.append(exc.raw_response)
    text = " ".join(values).casefold()
    return any(
        term in text
        for term in ("post not found", "post_not_found", "not found", "deleted", "cancelled", "canceled")
    )


def _retry_tiktok_post(watch: Dict[str, Any], attempt: int) -> Dict[str, Any]:
    connection = _tiktok_connection(
        str(watch.get("brand") or ""),
        str(watch.get("account_id") or ""),
    )
    request_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        "aurexvideo:tiktok-retry:{}:{}".format(watch["post_id"], attempt),
    ))
    response = _zernio_request(
        connection["base_url"] + "/posts/" + quote(str(watch["post_id"]), safe="") + "/retry",
        "POST",
        None,
        connection,
        headers={"X-Request-ID": request_id},
        phase="retry",
    )
    snapshot = _tiktok_snapshot(response)
    snapshot["post_id"] = str(watch["post_id"])
    return snapshot


def _schedule_tiktok_retry(watch: Dict[str, Any], snapshot: Dict[str, Any], attempts: int) -> None:
    retry_at = iso_at(utc_now() + timedelta(seconds=TIKTOK_RETRY_SECONDS))
    enriched = dict(snapshot)
    enriched["retry_attempts"] = attempts
    enriched["next_retry_at"] = retry_at
    _update_watch(
        watch["id"],
        state="retry_wait",
        snapshot=enriched,
        next_check_at=retry_at,
        error=str(snapshot.get("error") or "TikTok provider reported a temporary failure.")[:1600],
        retry_attempts=attempts,
        next_retry_at=retry_at,
    )
    job_id = str(watch.get("job_id") or "")
    if job_id:
        with db() as conn:
            conn.execute(
                """UPDATE jobs SET status='retry_wait', phase='monitoring', error=?,
                   provider_status=?, delivery_status=?, next_attempt_at=?, updated_at=?
                   WHERE id=?""",
                (
                    str(snapshot.get("error") or "TikTok provider reported a temporary failure.")[:2000],
                    str(snapshot.get("provider_status") or ""),
                    str(snapshot.get("delivery_status") or ""),
                    retry_at,
                    now(),
                    job_id,
                ),
            )
    print(
        "TikTok post {} temporary failure; retry {} scheduled at {}".format(
            watch["post_id"], attempts, retry_at,
        ),
        flush=True,
    )


def _mark_tiktok_watch_failed(watch: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
    _record_tiktok_provider_failure(watch, snapshot)
    _update_watch(watch["id"], state="failed", snapshot=snapshot, next_check_at=None, next_retry_at=None)
    if not watch.get("job_id"):
        print(
            "TikTok post {} is failed: {}".format(
                watch["post_id"], snapshot.get("error") or snapshot.get("provider_status"),
            ),
            flush=True,
        )


def _mark_tiktok_watch_cancelled(watch: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
    message = str(
        snapshot.get("error")
        or "Zernio scheduled TikTok post was deleted or cancelled; no retry will be attempted."
    )[:1600]
    terminal = dict(snapshot)
    terminal["state"] = "CANCELLED"
    terminal["post_id"] = str(watch.get("post_id") or terminal.get("post_id") or "")
    terminal["error"] = message
    _update_watch(
        watch["id"],
        state="cancelled",
        snapshot=terminal,
        next_check_at=None,
        error=message,
        next_retry_at=None,
    )
    job_id = str(watch.get("job_id") or "")
    if job_id:
        with db() as conn:
            conn.execute(
                """UPDATE jobs SET status='cancelled', phase='cancelled', result=?, error=NULL,
                   provider_post_id=?, provider_status=?, delivery_status=?,
                   next_attempt_at=NULL, updated_at=? WHERE id=?""",
                (
                    json.dumps(terminal, ensure_ascii=False),
                    terminal["post_id"],
                    str(terminal.get("provider_status") or ""),
                    str(terminal.get("delivery_status") or ""),
                    now(),
                    job_id,
                ),
            )
    print("TikTok post {} was deleted or cancelled on Zernio; no retry.".format(terminal["post_id"]), flush=True)


def _finish_tiktok_watch(watch: Dict[str, Any], snapshot: Dict[str, Any]) -> bool:
    if snapshot["state"] == "CANCELLED":
        _mark_tiktok_watch_cancelled(watch, snapshot)
        return True
    if snapshot["state"] == "PUBLISHED":
        _update_watch(watch["id"], state="published", snapshot=snapshot, next_check_at=None, next_retry_at=None)
        if watch.get("job_id"):
            with db() as conn:
                conn.execute(
                    """UPDATE jobs SET status='published', phase='published', result=?, error=NULL,
                       provider_post_id=?, provider_status=?, delivery_status=?,
                       next_attempt_at=NULL, updated_at=? WHERE id=?""",
                    (
                        json.dumps(snapshot, ensure_ascii=False),
                        watch["post_id"],
                        str(snapshot.get("provider_status") or ""),
                        str(snapshot.get("delivery_status") or ""),
                        now(),
                        watch["job_id"],
                    ),
                )
        print("TikTok post {} is published.".format(watch["post_id"]), flush=True)
        return True
    if snapshot["state"] == "INBOX_DELIVERED":
        _update_watch(watch["id"], state="inbox_delivered", snapshot=snapshot, next_check_at=None, next_retry_at=None)
        if watch.get("job_id"):
            with db() as conn:
                conn.execute(
                    """UPDATE jobs SET status='monitoring', phase='monitoring', result=?, error=NULL,
                       provider_post_id=?, provider_status=?, delivery_status=?,
                       next_attempt_at=NULL, updated_at=? WHERE id=?""",
                    (
                        json.dumps(snapshot, ensure_ascii=False),
                        watch["post_id"],
                        str(snapshot.get("provider_status") or ""),
                        str(snapshot.get("delivery_status") or ""),
                        now(),
                        watch["job_id"],
                    ),
                )
        print("TikTok post {} delivered to Creator Inbox; no further polling.".format(watch["post_id"]), flush=True)
        return True
    return False


def _record_tiktok_provider_failure(watch: Dict[str, Any], snapshot: Dict[str, Any]) -> None:
    job_id = str(watch.get("job_id") or "")
    if not job_id:
        return
    with db() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return
        error = str(snapshot.get("error") or "TikTok provider reported failed.").strip()
        conn.execute(
            """UPDATE jobs SET status='failed', phase='failed', error=?,
               provider_status=?, next_attempt_at=NULL, updated_at=? WHERE id=?""",
            (error[:2000], str(snapshot.get("provider_status") or ""), now(), job_id),
        )
    print("TikTok post {} is failed; no automatic repost because provider already has a post id: {}".format(watch["post_id"], error), flush=True)


def _poll_tiktok_watches() -> None:
    current = now()
    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM tiktok_watches
               WHERE state IN ('monitoring', 'retry_wait')
                 AND (next_check_at IS NULL OR next_check_at <= ?)
               ORDER BY next_check_at LIMIT 50""",
            (current,),
        ).fetchall()
    for row in rows:
        watch = dict(row)
        retry_wait = watch.get("state") == "retry_wait"
        retry_attempts = int(watch.get("retry_attempts") or 0)
        try:
            snapshot = _tiktok_status(
                watch["post_id"],
                str(watch.get("brand") or ""),
                str(watch.get("account_id") or ""),
            )
        except Exception as exc:
            if _is_missing_tiktok_post(exc):
                terminal = {
                    "state": "CANCELLED",
                    "provider_status": "not_found",
                    "delivery_status": "DIRECT_POST",
                    "error": "Zernio scheduled TikTok post was deleted or cancelled; no retry will be attempted.",
                    "post_id": watch["post_id"],
                }
                _mark_tiktok_watch_cancelled(watch, terminal)
                continue
            if retry_wait and retry_attempts >= TIKTOK_MAX_ATTEMPTS:
                terminal = {
                    "state": "FAILED",
                    "provider_status": "status_error",
                    "delivery_status": "DIRECT_POST",
                    "error": "TikTok status check failed after retry limit: {}".format(exc),
                    "post_id": watch["post_id"],
                }
                _mark_tiktok_watch_failed(watch, terminal)
                continue
            previous = _json_value(watch.get("result"))
            previous_snapshot = previous if isinstance(previous, dict) else {}
            next_check = (
                iso_at(utc_now() + timedelta(seconds=TIKTOK_RETRY_SECONDS))
                if retry_wait
                else _next_tiktok_check(previous_snapshot)
            )
            state = "retry_wait" if retry_wait else "monitoring"
            _update_watch(
                watch["id"],
                state=state,
                snapshot={"state": "PENDING", "provider_status": "status_error"},
                next_check_at=next_check,
                error=str(exc),
                retry_attempts=retry_attempts,
                next_retry_at=next_check if retry_wait else None,
            )
            print("TikTok watch {} status check failed; next check at {}: {}".format(watch["post_id"], next_check, exc), flush=True)
            continue
        if _finish_tiktok_watch(watch, snapshot):
            continue
        if snapshot["state"] != "FAILED":
            next_check = _next_tiktok_check(snapshot)
            _update_watch(watch["id"], state="monitoring", snapshot=snapshot, next_check_at=next_check, next_retry_at=None)
            continue
        if not _retryable_tiktok_failure(snapshot):
            _mark_tiktok_watch_failed(watch, snapshot)
            continue
        if not retry_wait:
            if retry_attempts < TIKTOK_MAX_ATTEMPTS:
                _schedule_tiktok_retry(watch, snapshot, retry_attempts + 1)
            else:
                _mark_tiktok_watch_failed(watch, snapshot)
            continue
        if retry_attempts >= TIKTOK_MAX_ATTEMPTS:
            _mark_tiktok_watch_failed(watch, snapshot)
            continue
        try:
            retry_snapshot = _retry_tiktok_post(watch, retry_attempts)
        except Exception as exc:
            next_attempts = retry_attempts + 1
            failed = dict(snapshot)
            failed["error"] = "Zernio retry request failed: {}".format(exc)
            if next_attempts < TIKTOK_MAX_ATTEMPTS:
                _schedule_tiktok_retry(watch, failed, next_attempts)
            else:
                _mark_tiktok_watch_failed(watch, failed)
            continue
        if _finish_tiktok_watch(watch, retry_snapshot):
            continue
        if retry_snapshot["state"] == "FAILED" and _retryable_tiktok_failure(retry_snapshot):
            next_attempts = retry_attempts + 1
            if next_attempts < TIKTOK_MAX_ATTEMPTS:
                _schedule_tiktok_retry(watch, retry_snapshot, next_attempts)
            else:
                _mark_tiktok_watch_failed(watch, retry_snapshot)
        elif retry_snapshot["state"] == "FAILED":
            _mark_tiktok_watch_failed(watch, retry_snapshot)
        else:
            next_check = _next_tiktok_check(retry_snapshot)
            _update_watch(watch["id"], state="monitoring", snapshot=retry_snapshot, next_check_at=next_check, next_retry_at=None)


def worker_loop() -> None:
    while True:
        try:
            for job in _claim_due_jobs():
                try:
                    _job_succeeded(job, execute(job))
                except Exception as exc:
                    _job_failed(job, exc)
            _poll_tiktok_watches()
        except Exception as exc:
            print("worker loop error: {}".format(exc), flush=True)
        time.sleep(5)


def body(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    value = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object.")
    return value


def _json_value(raw: Any) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _public_job(row: Dict[str, Any]) -> Dict[str, Any]:
    result = _json_value(row.get("result"))
    return {
        "id": row["id"],
        "platform": row["platform"],
        "project": row.get("project") or "",
        "brand": row.get("brand") or "",
        "accountId": row.get("account_id") or "",
        "videoUrl": row.get("video_url") or "",
        "scheduledPublishAt": row["scheduled_at"],
        "status": row["status"],
        "phase": row.get("phase") or "",
        "attempts": int(row.get("attempts") or 0),
        "nextAttemptAt": row.get("next_attempt_at") or "",
        "providerPostId": row.get("provider_post_id") or "",
        "providerStatus": row.get("provider_status") or "",
        "deliveryStatus": row.get("delivery_status") or "",
        "idempotencyKey": row.get("idempotency_key") or "",
        "result": result if isinstance(result, dict) else {},
        "error": row.get("error") or "",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _public_watch(row: Dict[str, Any]) -> Dict[str, Any]:
    result = _json_value(row.get("result"))
    result_dict = result if isinstance(result, dict) else {}
    return {
        "id": row["id"],
        "postId": row["post_id"],
        "jobId": row.get("job_id") or "",
        "project": row.get("project") or "",
        "brand": row.get("brand") or "",
        "status": row["state"],
        "providerStatus": row.get("provider_status") or "",
        "deliveryStatus": row.get("delivery_status") or "",
        "error": row.get("error") or "",
        "scheduledFor": result_dict.get("scheduled_for") or "",
        "nextCheckAt": row.get("next_check_at") or "",
        "lastCheckedAt": row.get("last_checked_at") or "",
        "retryAttempts": int(row.get("retry_attempts") or 0),
        "nextRetryAt": row.get("next_retry_at") or "",
        "result": result if isinstance(result, dict) else {},
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _upsert_watch_from_request(value: Dict[str, Any]) -> Dict[str, Any]:
    post_id = str(value.get("postId") or value.get("post_id") or "").strip()
    if not post_id:
        raise ValueError("postId is required")
    _record_tiktok_watch(
        post_id,
        project=str(value.get("project") or "").strip(),
        brand=str(value.get("brand") or "").strip(),
        account_id=str(value.get("accountId") or value.get("account_id") or "").strip(),
        scheduled_for=str(value.get("scheduledFor") or value.get("scheduled_for") or "").strip(),
    )
    with db() as conn:
        row = conn.execute("SELECT * FROM tiktok_watches WHERE post_id=?", (post_id,)).fetchone()
    return _public_watch(dict(row)) if row else {}


def _schedule_request_matches(
    row: Dict[str, Any],
    *,
    platform: str,
    scheduled_at: str,
    caption: str,
    video_url: str,
    project: str,
    brand: str,
    account_id: str,
    expected_media_sha256: str,
) -> bool:
    return all(
        (
            row.get("platform") == platform,
            row.get("scheduled_at") == scheduled_at,
            row.get("caption") == caption,
            (row.get("video_url") or "") == video_url,
            (row.get("project") or "") == project,
            (row.get("brand") or "") == brand,
            (row.get("account_id") or "") == account_id,
            (row.get("expected_media_sha256") or "") == expected_media_sha256,
        )
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_: Any) -> None:
        return

    def send(self, code: int, value: Dict[str, Any]) -> None:
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authorized(self) -> bool:
        return bool(API_KEY) and self.headers.get("Authorization") == "Bearer {}".format(API_KEY)

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/health":
            self.send(200, {"ok": True, "service": "aurex-social-worker", "time": now()})
            return
        if not self.authorized():
            self.send(401, {"error": "unauthorized"})
            return
        parts = [part for part in path.split("/") if part]
        if len(parts) == 2 and parts[0] == "jobs":
            with db() as conn:
                row = conn.execute("SELECT * FROM jobs WHERE id=?", (parts[1],)).fetchone()
            if not row:
                self.send(404, {"error": "job not found"})
                return
            self.send(200, _public_job(dict(row)))
            return
        if len(parts) == 3 and parts[0] == "tiktok" and parts[1] in {"status", "watches"}:
            with db() as conn:
                row = conn.execute("SELECT * FROM tiktok_watches WHERE post_id=?", (parts[2],)).fetchone()
            if not row:
                self.send(404, {"error": "TikTok watch not found"})
                return
            self.send(200, _public_watch(dict(row)))
            return
        self.send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self.authorized():
            self.send(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        try:
            value = body(self)
            if path == "/watch-tiktok":
                self.send(201, {"ok": True, **_upsert_watch_from_request(value)})
                return
            if path != "/schedule":
                self.send(404, {"error": "not found"})
                return
            platform = str(value.get("platform") or "").lower()
            scheduled = str(value.get("scheduledPublishAt") or "")
            caption = str(value.get("caption") or "").strip()
            video_path = str(value.get("videoPath") or "").strip()
            video_url = str(value.get("videoUrl") or value.get("video_url") or "").strip()
            if platform not in {"instagram", "threads", "tiktok"} or not caption:
                raise ValueError("platform and caption are required")
            if platform == "tiktok":
                raise ValueError("TikTok scheduled posts must be created on Zernio; VPS only watches and retries them.")
            if not video_url:
                raise ValueError("Instagram/Threads scheduled jobs must provide videoUrl from R2.")
            video_url = _validated_public_video_url(video_url)
            if parse_time(scheduled) <= utc_now():
                raise ValueError("scheduledPublishAt must be in the future")
            project = str(value.get("project") or "").strip()
            brand = str(value.get("brand") or "").strip()
            account_id = str(value.get("accountId") or value.get("account_id") or "").strip()
            _validate_social_account(platform, account_id, brand)
            expected_media_sha256 = str(
                value.get("expectedMediaSha256") or value.get("expected_media_sha256") or ""
            ).strip().lower()
            if expected_media_sha256 and not re.fullmatch(r"[0-9a-f]{64}", expected_media_sha256):
                raise ValueError("expectedMediaSha256 must be a SHA-256 hex digest.")
            idempotency_key = str(
                value.get("idempotencyKey") or value.get("idempotency_key") or ""
            ).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", idempotency_key):
                raise ValueError("idempotencyKey must be a SHA-256 hex digest.")
            tiktok_settings = value.get("tiktokSettings")
            if tiktok_settings is not None and not isinstance(tiktok_settings, dict):
                raise ValueError("tiktokSettings must be an object")
            job_id = "vps_" + uuid.uuid4().hex[:16]
            normalized_scheduled = iso_at(parse_time(scheduled))
            with db() as conn:
                existing = conn.execute(
                    "SELECT * FROM jobs WHERE idempotency_key=?",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    existing_dict = dict(existing)
                    if not _schedule_request_matches(
                        existing_dict,
                        platform=platform,
                        scheduled_at=normalized_scheduled,
                        caption=caption,
                        video_url=video_url,
                        project=project,
                        brand=brand,
                        account_id=account_id,
                        expected_media_sha256=expected_media_sha256,
                    ):
                        raise ValueError("idempotencyKey đã được dùng cho payload khác.")
                    self.send(
                        200,
                        {"ok": True, "worker_id": existing_dict["id"], **_public_job(existing_dict)},
                    )
                    return
                conn.execute(
                    """INSERT INTO jobs
                       (id, platform, scheduled_at, caption, video_path, video_url, status,
                        result, error, created_at, updated_at, project, brand,
                        account_id, tiktok_settings, expected_media_sha256,
                        attempts, next_attempt_at, provider_post_id,
                        provider_status, delivery_status, idempotency_key)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        job_id,
                        platform,
                        normalized_scheduled,
                        caption,
                        video_path,
                        video_url,
                        "queued",
                        None,
                        None,
                        now(),
                        now(),
                        project,
                        brand,
                        account_id,
                        json.dumps(tiktok_settings or {}, ensure_ascii=False) if platform == "tiktok" else "",
                        expected_media_sha256,
                        0,
                        None,
                        "",
                        "",
                        "",
                        idempotency_key,
                    ),
                )
            self.send(
                201,
                {
                    "ok": True,
                    "id": job_id,
                    "worker_id": job_id,
                    "status": "queued",
                    "scheduledPublishAt": normalized_scheduled,
                    "idempotencyKey": idempotency_key,
                    "videoUrl": video_url,
                },
            )
        except Exception as exc:
            self.send(400, {"error": str(exc)})


if __name__ == "__main__":
    init_db()
    threading.Thread(target=worker_loop, name="social-worker", daemon=True).start()
    ThreadingHTTPServer((os.environ.get("WORKER_BIND", "127.0.0.1"), PORT), Handler).serve_forever()
