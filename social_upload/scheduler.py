from __future__ import annotations

"""Persistent local scheduler for providers without native scheduling APIs."""

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.RLock()
_STARTED = False
_STOP = threading.Event()


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
