from __future__ import annotations

"""Local, API-agnostic persistence for the Shopee x Facebook A-D POC."""

import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from types import MappingProxyType

from . import affiliate_store
from .config import canonical_brand


CASE_MATRIX = MappingProxyType({
    "A": MappingProxyType({"publish": "manual", "comment": "manual"}),
    "B": MappingProxyType({"publish": "api", "comment": "manual"}),
    "C": MappingProxyType({"publish": "manual", "comment": "api"}),
    "D": MappingProxyType({"publish": "api", "comment": "api"}),
})
CASE_CODES = tuple(CASE_MATRIX)
ALLOWED_STATUSES = frozenset({"pending", "running", "passed", "failed", "blocked"})

_BRAND_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_RUN_ID_RE = re.compile(r"poc_[a-f0-9]{24}\Z")
_IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_TEXT_LIMITS = {"note": 1000, "message": 1000, "evidence": 500, "reference": 500}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS affiliate_poc_runs (
    id TEXT PRIMARY KEY,
    brand_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'passed', 'failed', 'blocked')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (brand_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS affiliate_poc_cases (
    run_id TEXT NOT NULL,
    case_code TEXT NOT NULL CHECK (case_code IN ('A', 'B', 'C', 'D')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'passed', 'failed', 'blocked')),
    note TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    evidence TEXT NOT NULL DEFAULT '',
    reference TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, case_code),
    FOREIGN KEY (run_id) REFERENCES affiliate_poc_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_affiliate_poc_runs_brand_created
    ON affiliate_poc_runs (brand_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_affiliate_poc_runs_brand_status
    ON affiliate_poc_runs (brand_id, status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_affiliate_poc_cases_run_status
    ON affiliate_poc_cases (run_id, status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _require_brand(brand: object) -> str:
    if not isinstance(brand, str):
        raise ValueError("Brand không hợp lệ.")
    value = canonical_brand(brand)
    if not _BRAND_RE.fullmatch(value):
        raise ValueError("Brand không hợp lệ.")
    return value


def _require_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("POC run id không hợp lệ.")
    return run_id


def _require_status(status: object) -> str:
    if not isinstance(status, str) or status not in ALLOWED_STATUSES:
        raise ValueError("POC status không hợp lệ.")
    return status


def _require_case(case_code: object) -> str:
    if not isinstance(case_code, str) or case_code not in CASE_MATRIX:
        raise ValueError("POC case không hợp lệ.")
    return case_code


def _require_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or not _IDEMPOTENCY_RE.fullmatch(value):
        raise ValueError("Idempotency key không hợp lệ.")
    return value


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str) or len(value) > _TEXT_LIMITS[name]:
        raise ValueError(f"{name} không hợp lệ hoặc quá dài.")
    return value


def _require_pagination(limit: object, offset: object) -> tuple[int, int]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit phải là số nguyên từ 1 đến 100.")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset phải là số nguyên không âm.")
    return limit, offset


@contextmanager
def _connection():
    """Reuse affiliate_store's patched path/pragmas and close every handle."""
    connection = affiliate_store._connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def init_db() -> None:
    """Install the POC tables beside the existing affiliate-store tables."""
    with _connection() as connection:
        connection.executescript(_SCHEMA)


def _run_record(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "brand": row["brand_id"],
        "idempotency_key": row["idempotency_key"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _case_record(row: sqlite3.Row) -> dict:
    case_code = row["case_code"]
    return {
        "case": case_code,
        **dict(CASE_MATRIX[case_code]),
        "status": row["status"],
        "note": row["note"],
        "message": row["message"],
        "evidence": row["evidence"],
        "reference": row["reference"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _require_owned_run(connection: sqlite3.Connection, brand: str, run_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM affiliate_poc_runs WHERE id = ? AND brand_id = ?", (run_id, brand)
    ).fetchone()
    if row is None:
        raise ValueError("POC run không tồn tại.")
    return row


def _aggregate_status(statuses: list[str]) -> str:
    if statuses and all(status == "passed" for status in statuses):
        return "passed"
    for status in ("failed", "blocked", "running"):
        if status in statuses:
            return status
    return "pending"


def _summary_from_connection(connection: sqlite3.Connection, brand: str, run_id: str) -> dict:
    run = _require_owned_run(connection, brand, run_id)
    rows = connection.execute(
        "SELECT * FROM affiliate_poc_cases WHERE run_id = ? ORDER BY case_code", (run_id,)
    ).fetchall()
    cases = [_case_record(row) for row in rows]
    counts = {status: 0 for status in sorted(ALLOWED_STATUSES)}
    for case in cases:
        counts[case["status"]] += 1
    status = _aggregate_status([case["status"] for case in cases])
    return {**_run_record(run), "status": status, "counts": counts, "cases": cases}


def start_run(brand: object, idempotency_key: object) -> dict:
    """Create the fixed A-D matrix, or return the existing Brand-local run."""
    brand_id = _require_brand(brand)
    key = _require_idempotency_key(idempotency_key)
    init_db()
    now = _now()
    run_id = f"poc_{uuid.uuid4().hex[:24]}"
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO affiliate_poc_runs (id, brand_id, idempotency_key, status, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?)
            ON CONFLICT (brand_id, idempotency_key) DO NOTHING
            """,
            (run_id, brand_id, key, now, now),
        )
        run = connection.execute(
            "SELECT * FROM affiliate_poc_runs WHERE brand_id = ? AND idempotency_key = ?", (brand_id, key)
        ).fetchone()
        if run["id"] == run_id:
            connection.executemany(
                """
                INSERT INTO affiliate_poc_cases (run_id, case_code, status, created_at, updated_at)
                VALUES (?, ?, 'pending', ?, ?)
                """,
                [(run_id, case_code, now, now) for case_code in CASE_CODES],
            )
        return _summary_from_connection(connection, brand_id, run["id"])


def record_case_result(
    brand: object,
    run_id: object,
    case_code: object,
    status: object,
    *,
    note: object = "",
    message: object = "",
    evidence: object = "",
    reference: object = "",
) -> dict:
    """Record one safe scalar result for one Brand-owned A-D case."""
    brand_id = _require_brand(brand)
    run_key = _require_run_id(run_id)
    case = _require_case(case_code)
    result_status = _require_status(status)
    values = tuple(_require_text(name, value) for name, value in (
        ("note", note), ("message", message), ("evidence", evidence), ("reference", reference)
    ))
    init_db()
    now = _now()
    with _connection() as connection:
        _require_owned_run(connection, brand_id, run_key)
        connection.execute(
            """
            UPDATE affiliate_poc_cases
            SET status = ?, note = ?, message = ?, evidence = ?, reference = ?, updated_at = ?
            WHERE run_id = ? AND case_code = ?
            """,
            (result_status, *values, now, run_key, case),
        )
        summary = _summary_from_connection(connection, brand_id, run_key)
        connection.execute(
            "UPDATE affiliate_poc_runs SET status = ?, updated_at = ? WHERE id = ? AND brand_id = ?",
            (summary["status"], now, run_key, brand_id),
        )
        return _summary_from_connection(connection, brand_id, run_key)


def list_runs(brand: object, *, status: object = None, limit: object = 20, offset: object = 0) -> list[dict]:
    """List only a Brand's runs, with optional exact status filtering."""
    brand_id = _require_brand(brand)
    limit_value, offset_value = _require_pagination(limit, offset)
    if status is not None:
        status = _require_status(status)
    init_db()
    query = "SELECT * FROM affiliate_poc_runs WHERE brand_id = ?"
    values: list[object] = [brand_id]
    if status is not None:
        query += " AND status = ?"
        values.append(status)
    query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    values.extend((limit_value, offset_value))
    with _connection() as connection:
        rows = connection.execute(query, values).fetchall()
    return [_run_record(row) for row in rows]


def summarize_run(brand: object, run_id: object) -> dict:
    """Return deterministic aggregate status, all five counts, and all A-D cases."""
    brand_id = _require_brand(brand)
    run_key = _require_run_id(run_id)
    init_db()
    with _connection() as connection:
        return _summary_from_connection(connection, brand_id, run_key)


__all__ = [
    "ALLOWED_STATUSES", "CASE_CODES", "CASE_MATRIX", "init_db", "list_runs",
    "record_case_result", "start_run", "summarize_run",
]
