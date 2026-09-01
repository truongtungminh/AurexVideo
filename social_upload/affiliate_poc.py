from __future__ import annotations

"""Local, API-agnostic persistence for the Shopee x Facebook A-D POC."""

import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Iterator
from urllib.parse import urlsplit

from . import affiliate_store
from .config import canonical_brand


CASES = ("A", "B", "C", "D")
STATUSES = ("pending", "running", "passed", "failed", "blocked")
ALLOWED_STATUSES = frozenset(STATUSES)

_CASE_MATRIX = MappingProxyType({
    "A": ("manual", "manual"),
    "B": ("api", "manual"),
    "C": ("manual", "api"),
    "D": ("api", "api"),
})
_BRAND_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_SAFE_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_RUN_ID_RE = re.compile(r"poc_[a-f0-9]{24}\Z")
_FACEBOOK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_UNSET = object()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS affiliate_poc_runs (
    id TEXT PRIMARY KEY,
    brand_id TEXT NOT NULL CHECK (length(brand_id) BETWEEN 1 AND 64),
    content_id TEXT NOT NULL CHECK (length(content_id) BETWEEN 1 AND 128),
    idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 128),
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'passed', 'failed', 'blocked')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (brand_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS affiliate_poc_cases (
    run_id TEXT NOT NULL,
    case_key TEXT NOT NULL CHECK (case_key IN ('A', 'B', 'C', 'D')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'passed', 'failed', 'blocked')),
    page_id TEXT NOT NULL DEFAULT '' CHECK (length(page_id) <= 256),
    post_id TEXT NOT NULL DEFAULT '' CHECK (length(post_id) <= 256),
    comment_id TEXT NOT NULL DEFAULT '' CHECK (length(comment_id) <= 256),
    banner_observed INTEGER CHECK (banner_observed IS NULL OR banner_observed IN (0, 1)),
    evidence_url TEXT NOT NULL DEFAULT '' CHECK (length(evidence_url) <= 500),
    notes TEXT NOT NULL DEFAULT '' CHECK (length(notes) <= 1000),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, case_key),
    FOREIGN KEY (run_id) REFERENCES affiliate_poc_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_affiliate_poc_runs_brand_content_created
    ON affiliate_poc_runs (brand_id, content_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_affiliate_poc_runs_brand_status
    ON affiliate_poc_runs (brand_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_affiliate_poc_cases_run_status
    ON affiliate_poc_cases (run_id, status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_brand(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Brand không hợp lệ.")
    brand = canonical_brand(value)
    if not _BRAND_RE.fullmatch(brand):
        raise ValueError("Brand không hợp lệ.")
    return brand


def _require_safe_key(name: str, value: object) -> str:
    if not isinstance(value, str) or not _SAFE_KEY_RE.fullmatch(value):
        raise ValueError(f"{name} không hợp lệ.")
    return value


def _require_run_id(value: object) -> str:
    if not isinstance(value, str) or not _RUN_ID_RE.fullmatch(value):
        raise ValueError("POC run id không hợp lệ.")
    return value


def _require_case(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("POC case không hợp lệ.")
    case_key = value.strip().upper()
    if case_key not in _CASE_MATRIX:
        raise ValueError("POC case không hợp lệ.")
    return case_key


def _require_status(value: object) -> str:
    if not isinstance(value, str) or value not in ALLOWED_STATUSES:
        raise ValueError("POC status không hợp lệ.")
    return value


def _require_bounded_text(name: str, value: object, limit: int) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise ValueError(f"{name} không hợp lệ hoặc quá dài.")
    return value


def _require_facebook_id(name: str, value: object) -> str:
    text = _require_bounded_text(name, value, 256)
    if text and not _FACEBOOK_ID_RE.fullmatch(text):
        raise ValueError(f"{name} không hợp lệ.")
    return text


def _require_evidence_url(value: object) -> str:
    evidence_url = _require_bounded_text("evidenceUrl", value, 500).strip()
    if not evidence_url:
        return ""
    parsed = urlsplit(evidence_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("evidenceUrl không hợp lệ.")
    return evidence_url


def _require_banner_observed(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("bannerObserved phải là boolean hoặc null.")
    return int(value)


def _require_pagination(limit: object, offset: object) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit phải là số nguyên từ 1 đến 100.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset phải là số nguyên không âm.")
    return limit, offset


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """Reuse affiliate_store's dynamic database path/pragmas and close it."""
    connection = affiliate_store._connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}


def _legacy_reference(value: object) -> dict:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = __import__("json").loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _migrate_legacy_schema(connection: sqlite3.Connection) -> None:
    """Upgrade the first POC draft without dropping its locally saved runs."""
    run_columns = _columns(connection, "affiliate_poc_runs")
    case_columns = _columns(connection, "affiliate_poc_cases")
    if not run_columns and not case_columns:
        return
    if "content_id" in run_columns and "case_key" in case_columns:
        return
    expected_legacy_runs = {"id", "brand_id", "idempotency_key", "status", "created_at", "updated_at"}
    expected_legacy_cases = {
        "run_id", "case_code", "status", "note", "message", "evidence", "reference", "created_at", "updated_at"
    }
    if not (expected_legacy_runs.issubset(run_columns) and expected_legacy_cases.issubset(case_columns)):
        raise RuntimeError("Không nhận diện được schema POC Affiliate hiện tại để migrate an toàn.")

    connection.execute("ALTER TABLE affiliate_poc_runs RENAME TO affiliate_poc_runs_legacy_v1")
    connection.execute("ALTER TABLE affiliate_poc_cases RENAME TO affiliate_poc_cases_legacy_v1")
    connection.executescript(_SCHEMA)

    legacy_runs = connection.execute(
        "SELECT id, brand_id, idempotency_key, status, created_at, updated_at "
        "FROM affiliate_poc_runs_legacy_v1 ORDER BY created_at, id"
    ).fetchall()
    for row in legacy_runs:
        # The first draft had no content_id. Its idempotency key was the only
        # safe content-like identifier, so keep it as the migrated content id.
        content_id = str(row["idempotency_key"] or "")
        if not content_id or not _SAFE_KEY_RE.fullmatch(content_id):
            content_id = f"legacy-{row['id']}"
        connection.execute(
            """
            INSERT INTO affiliate_poc_runs
              (id, brand_id, content_id, idempotency_key, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["brand_id"], content_id, row["idempotency_key"], row["status"],
                row["created_at"], row["updated_at"],
            ),
        )

    legacy_cases = connection.execute(
        "SELECT run_id, case_code, status, note, message, evidence, reference, created_at, updated_at "
        "FROM affiliate_poc_cases_legacy_v1 ORDER BY run_id, case_code"
    ).fetchall()
    for row in legacy_cases:
        reference = _legacy_reference(row["reference"])
        notes = str(row["note"] or "")
        if not notes:
            notes = str(row["message"] or "")
        banner = reference.get("banner_observed")
        banner_value = None
        if banner is True or str(banner).casefold() in {"true", "yes", "1"}:
            banner_value = 1
        elif banner is False or str(banner).casefold() in {"false", "no", "0"}:
            banner_value = 0
        connection.execute(
            """
            INSERT INTO affiliate_poc_cases
              (run_id, case_key, status, page_id, post_id, comment_id, banner_observed,
               evidence_url, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["run_id"], row["case_code"], row["status"],
                str(reference.get("page_id") or "")[:256],
                str(reference.get("post_id") or "")[:256],
                str(reference.get("comment_id") or "")[:256],
                banner_value,
                str(row["evidence"] or "")[:500],
                notes[:1000],
                row["created_at"], row["updated_at"],
            ),
        )


def init_db() -> None:
    """Install the POC tables beside the existing affiliate-store tables."""
    with _connection() as connection:
        _migrate_legacy_schema(connection)
        connection.executescript(_SCHEMA)


def case_definitions() -> list[dict]:
    """Return fresh JSON-safe definitions for the immutable A-D matrix."""
    return [
        {"caseKey": case_key, "publishMode": _CASE_MATRIX[case_key][0], "commentMode": _CASE_MATRIX[case_key][1]}
        for case_key in CASES
    ]


def _run_record(row: sqlite3.Row) -> dict:
    return {
        "runId": row["id"],
        "brand": row["brand_id"],
        "contentId": row["content_id"],
        "idempotencyKey": row["idempotency_key"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _case_record(row: sqlite3.Row) -> dict:
    case_key = row["case_key"]
    return {
        "caseKey": case_key,
        "publishMode": _CASE_MATRIX[case_key][0],
        "commentMode": _CASE_MATRIX[case_key][1],
        "status": row["status"],
        "pageId": row["page_id"],
        "postId": row["post_id"],
        "commentId": row["comment_id"],
        "bannerObserved": None if row["banner_observed"] is None else bool(row["banner_observed"]),
        "evidenceUrl": row["evidence_url"],
        "notes": row["notes"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _empty_case(case: dict) -> dict:
    return {
        **case,
        "status": "pending",
        "pageId": "",
        "postId": "",
        "commentId": "",
        "bannerObserved": None,
        "evidenceUrl": "",
        "notes": "",
        "createdAt": "",
        "updatedAt": "",
    }


def _empty_summary(brand: str, content_id: str) -> dict:
    counts = {status: 0 for status in STATUSES}
    counts["pending"] = len(CASES)
    return {
        "started": False,
        "runId": None,
        "brand": brand,
        "contentId": content_id,
        "idempotencyKey": "",
        "status": "pending",
        "createdAt": "",
        "updatedAt": "",
        "counts": counts,
        "cases": [_empty_case(case) for case in case_definitions()],
    }


def _owned_run(connection: sqlite3.Connection, brand: str, run_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM affiliate_poc_runs WHERE id = ? AND brand_id = ?",
        (run_id, brand),
    ).fetchone()
    if row is None:
        raise ValueError("POC run không tồn tại trong Brand/content đã chọn.")
    return row


def _aggregate_status(statuses: list[str]) -> str:
    if statuses and all(status == "passed" for status in statuses):
        return "passed"
    for status in ("failed", "blocked", "running"):
        if status in statuses:
            return status
    if "passed" in statuses:
        return "running"
    return "pending"


def _summary(connection: sqlite3.Connection, brand: str, run_id: str) -> dict:
    run = _owned_run(connection, brand, run_id)
    rows = connection.execute(
        "SELECT * FROM affiliate_poc_cases WHERE run_id = ? ORDER BY case_key",
        (run_id,),
    ).fetchall()
    if tuple(row["case_key"] for row in rows) != CASES:
        raise RuntimeError("POC run không có đủ case A-D.")
    cases = [_case_record(row) for row in rows]
    counts = {status: 0 for status in STATUSES}
    for case in cases:
        counts[case["status"]] += 1
    return {
        "started": True,
        **_run_record(run),
        "status": _aggregate_status([case["status"] for case in cases]),
        "counts": counts,
        "cases": cases,
    }


def start_run(brand_id: object, content_id: object, *, idempotency_key: object = None) -> dict:
    """Create A-D once per Brand/key, defaulting the key to content_id."""
    brand = _require_brand(brand_id)
    content = _require_safe_key("contentId", content_id)
    key = _require_safe_key("idempotencyKey", content if idempotency_key is None else idempotency_key)
    init_db()
    now = _now()
    new_run_id = f"poc_{uuid.uuid4().hex[:24]}"
    with _connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO affiliate_poc_runs
              (id, brand_id, content_id, idempotency_key, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT (brand_id, idempotency_key) DO NOTHING
            """,
            (new_run_id, brand, content, key, now, now),
        )
        run = connection.execute(
            "SELECT * FROM affiliate_poc_runs WHERE brand_id = ? AND idempotency_key = ?",
            (brand, key),
        ).fetchone()
        if run["content_id"] != content:
            raise ValueError("Idempotency key đã thuộc một content khác trong Brand này.")
        if run["id"] == new_run_id:
            connection.executemany(
                """
                INSERT INTO affiliate_poc_cases (run_id, case_key, status, created_at, updated_at)
                VALUES (?, ?, 'pending', ?, ?)
                """,
                [(new_run_id, case_key, now, now) for case_key in CASES],
            )
        return _summary(connection, brand, run["id"])


def _resolve_run(
    connection: sqlite3.Connection,
    brand: str,
    *,
    run_id: object,
    idempotency_key: object,
    content_id: str | None,
) -> sqlite3.Row:
    if (run_id is None) == (idempotency_key is None):
        raise ValueError("Cần đúng một trong runId hoặc idempotencyKey.")
    if run_id is not None:
        run_key = _require_run_id(run_id)
        row = connection.execute(
            "SELECT * FROM affiliate_poc_runs WHERE id = ? AND brand_id = ?",
            (run_key, brand),
        ).fetchone()
    else:
        idempotency = _require_safe_key("idempotencyKey", idempotency_key)
        row = connection.execute(
            "SELECT * FROM affiliate_poc_runs WHERE idempotency_key = ? AND brand_id = ?",
            (idempotency, brand),
        ).fetchone()
    if row is None or (content_id is not None and row["content_id"] != content_id):
        raise ValueError("POC run không tồn tại trong Brand/content đã chọn.")
    return row


def record_result(
    brand_id: object,
    case_key: object,
    status: object,
    *,
    run_id: object = None,
    idempotency_key: object = None,
    content_id: object = None,
    page_id: object = _UNSET,
    post_id: object = _UNSET,
    comment_id: object = _UNSET,
    banner_observed: object = _UNSET,
    evidence_url: object = _UNSET,
    notes: object = _UNSET,
) -> dict:
    """Idempotently record one bounded, Brand-owned case result."""
    brand = _require_brand(brand_id)
    case = _require_case(case_key)
    result_status = _require_status(status)
    content = None if content_id is None else _require_safe_key("contentId", content_id)
    updates: dict[str, object] = {"status": result_status}
    if page_id is not _UNSET:
        updates["page_id"] = _require_facebook_id("pageId", page_id)
    if post_id is not _UNSET:
        updates["post_id"] = _require_facebook_id("postId", post_id)
    if comment_id is not _UNSET:
        updates["comment_id"] = _require_facebook_id("commentId", comment_id)
    if banner_observed is not _UNSET:
        updates["banner_observed"] = _require_banner_observed(banner_observed)
    if evidence_url is not _UNSET:
        updates["evidence_url"] = _require_evidence_url(evidence_url)
    if notes is not _UNSET:
        updates["notes"] = _require_bounded_text("notes", notes, 1000)

    init_db()
    with _connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        run = _resolve_run(
            connection,
            brand,
            run_id=run_id,
            idempotency_key=idempotency_key,
            content_id=content,
        )
        current = connection.execute(
            "SELECT * FROM affiliate_poc_cases WHERE run_id = ? AND case_key = ?",
            (run["id"], case),
        ).fetchone()
        if current is None:
            raise RuntimeError("POC run không có đủ case A-D.")
        if all(current[column] == value for column, value in updates.items()):
            return _summary(connection, brand, run["id"])

        now = _now()
        updates["updated_at"] = now
        assignments = ", ".join(f"{column} = ?" for column in updates)
        connection.execute(
            f"UPDATE affiliate_poc_cases SET {assignments} WHERE run_id = ? AND case_key = ?",
            (*updates.values(), run["id"], case),
        )
        summary = _summary(connection, brand, run["id"])
        connection.execute(
            "UPDATE affiliate_poc_runs SET status = ?, updated_at = ? WHERE id = ? AND brand_id = ?",
            (summary["status"], now, run["id"], brand),
        )
        return _summary(connection, brand, run["id"])


def list_runs(
    brand_id: object,
    content_id: object,
    limit: object = 20,
    *,
    status: object = None,
    offset: object = 0,
) -> list[dict]:
    """List safe run records for exactly one Brand/content pair."""
    brand = _require_brand(brand_id)
    content = _require_safe_key("contentId", content_id)
    limit_value, offset_value = _require_pagination(limit, offset)
    status_value = None if status is None else _require_status(status)
    init_db()
    query = "SELECT * FROM affiliate_poc_runs WHERE brand_id = ? AND content_id = ?"
    values: list[object] = [brand, content]
    if status_value is not None:
        query += " AND status = ?"
        values.append(status_value)
    query += " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?"
    values.extend((limit_value, offset_value))
    with _connection() as connection:
        rows = connection.execute(query, values).fetchall()
    return [_run_record(row) for row in rows]


def summarize_run(brand_id: object, run_id: object) -> dict:
    """Summarize one run without allowing cross-Brand lookup."""
    brand = _require_brand(brand_id)
    run_key = _require_run_id(run_id)
    init_db()
    with _connection() as connection:
        return _summary(connection, brand, run_key)


def poc_summary(brand_id: object, content_id: object) -> dict:
    """Return the latest run for a Brand/content, or an unstarted A-D matrix."""
    brand = _require_brand(brand_id)
    content = _require_safe_key("contentId", content_id)
    init_db()
    with _connection() as connection:
        row = connection.execute(
            """
            SELECT id FROM affiliate_poc_runs
            WHERE brand_id = ? AND content_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 1
            """,
            (brand, content),
        ).fetchone()
        if row is None:
            return _empty_summary(brand, content)
        return _summary(connection, brand, row["id"])


__all__ = [
    "ALLOWED_STATUSES",
    "CASES",
    "STATUSES",
    "case_definitions",
    "init_db",
    "list_runs",
    "poc_summary",
    "record_result",
    "start_run",
    "summarize_run",
]
