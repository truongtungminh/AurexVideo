"""Persistent domain storage for native AurexVideo AutoProjects.

This module intentionally has no HTTP or browser dependencies.  The web server and
manual pipeline stages can use it without duplicating normalization, uniqueness, or
completion-retention rules.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    from aurexvideo_paths import CONFIG_ROOT
except ImportError:  # pragma: no cover - allows direct isolated imports
    CONFIG_ROOT = Path.home() / "Library/Application Support/app.aurexvideo/studio/config"


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS auto_projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    character_id TEXT NOT NULL,
    voice_id TEXT NOT NULL,
    tts_engine TEXT NOT NULL DEFAULT 'maziao',
    tts_mode TEXT NOT NULL DEFAULT 'full',
    template_id TEXT NOT NULL,
    aspect_ratio TEXT NOT NULL DEFAULT '9:16',
    fps INTEGER NOT NULL DEFAULT 30,
    duration_target_seconds INTEGER NOT NULL DEFAULT 60,
    branding_enabled INTEGER NOT NULL DEFAULT 1,
    execution_mode TEXT NOT NULL DEFAULT 'manual',
    prompt_template TEXT NOT NULL,
    title_caption_prompt_template TEXT NOT NULL DEFAULT '',
    youtube_title_prompt_template TEXT NOT NULL DEFAULT '',
    social_caption_prompt_template TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auto_keywords (
    id TEXT PRIMARY KEY,
    auto_project_id TEXT NOT NULL,
    value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    generated_project_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(auto_project_id, normalized_value),
    FOREIGN KEY(auto_project_id) REFERENCES auto_projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS auto_jobs (
    id TEXT PRIMARY KEY,
    auto_project_id TEXT NOT NULL,
    keyword_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL UNIQUE,
    generated_project_id TEXT,
    error_code TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY(auto_project_id) REFERENCES auto_projects(id) ON DELETE CASCADE,
    FOREIGN KEY(keyword_id) REFERENCES auto_keywords(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS auto_script_outputs (
    id TEXT PRIMARY KEY,
    keyword_id TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    normalized_script TEXT,
    title TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(keyword_id) REFERENCES auto_keywords(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS auto_job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT,
    payload_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES auto_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS generated_projects (
    generated_project_id TEXT PRIMARY KEY,
    auto_project_id TEXT,
    origin_auto_project_id TEXT NOT NULL,
    keyword_id TEXT,
    keyword_value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(auto_project_id) REFERENCES auto_projects(id) ON DELETE SET NULL,
    FOREIGN KEY(keyword_id) REFERENCES auto_keywords(id) ON DELETE SET NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex}"


DEFAULT_YOUTUBE_TITLE_PROMPT = """Từ nội dung video đã được tạo và duyệt dưới đây, viết một YouTube title rõ, hấp dẫn, dưới 100 ký tự cho chủ đề {{keyword}}.

Nội dung đã duyệt:
{{content}}

Chỉ trả về title, không thêm giải thích hoặc marker nội bộ. Bám sát nội dung, không tự bịa dữ kiện."""

DEFAULT_SOCIAL_CAPTION_PROMPT = """Từ nội dung video đã được tạo và duyệt dưới đây, viết caption dùng chung cho Facebook và YouTube về chủ đề {{keyword}}.

Nội dung đã duyệt:
{{content}}

Viết caption dễ đọc, tóm tắt đúng nội dung, không tự bịa dữ kiện và không thêm marker hoặc giải thích nội bộ."""

DEFAULT_TITLE_CAPTION_PROMPT = f"{DEFAULT_YOUTUBE_TITLE_PROMPT}\n\n{DEFAULT_SOCIAL_CAPTION_PROMPT}"


def normalize_keyword(value: object) -> str:
    """Return the canonical comparison/display form for one keyword."""
    text = unicodedata.normalize("NFC", str(value if value is not None else ""))
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    if not text:
        raise ValueError("Keyword không được để trống.")
    return text[:500]


def normalize_project_id(value: object) -> str:
    """Create the same lowercase, ASCII, hyphenated slug style as AurexVideo."""
    text = unicodedata.normalize("NFKD", str(value if value is not None else ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if not text:
        raise ValueError("AutoProject name không được để trống.")
    if len(text) > 64:
        text = text[:64].rstrip("-")
    if len(text) < 2:
        raise ValueError("AutoProject name phải có ít nhất 2 ký tự.")
    return text


class AutoProjectStore:
    """SQLite store for AutoProject configuration, keywords and durable mappings."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path).expanduser() if db_path else CONFIG_ROOT / "autoproject.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(auto_projects)")}
            if "title_caption_prompt_template" not in columns:
                connection.execute(
                    "ALTER TABLE auto_projects ADD COLUMN title_caption_prompt_template TEXT NOT NULL DEFAULT ''"
                )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(auto_projects)")}
            if "youtube_title_prompt_template" not in columns:
                connection.execute(
                    "ALTER TABLE auto_projects ADD COLUMN youtube_title_prompt_template TEXT NOT NULL DEFAULT ''"
                )
            if "social_caption_prompt_template" not in columns:
                connection.execute(
                    "ALTER TABLE auto_projects ADD COLUMN social_caption_prompt_template TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                """
                UPDATE auto_projects
                SET title_caption_prompt_template = ?
                WHERE title_caption_prompt_template IS NULL OR trim(title_caption_prompt_template) = ''
                """,
                (DEFAULT_TITLE_CAPTION_PROMPT,),
            )
            connection.execute(
                """
                UPDATE auto_projects
                SET youtube_title_prompt_template = ?, social_caption_prompt_template = ?
                WHERE youtube_title_prompt_template IS NULL OR trim(youtube_title_prompt_template) = ''
                   OR social_caption_prompt_template IS NULL OR trim(social_caption_prompt_template) = ''
                """,
                (DEFAULT_YOUTUBE_TITLE_PROMPT, DEFAULT_SOCIAL_CAPTION_PROMPT),
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row is not None else None

    def create_autoproject(self, payload: dict) -> dict:
        project_id = normalize_project_id(payload.get("id") or payload.get("name"))
        name = str(payload.get("name") or project_id).strip()
        prompt = str(payload.get("prompt_template") or "").strip()
        if "{{keyword}}" not in prompt:
            raise ValueError("Prompt phải chứa biến {{keyword}}.")
        youtube_title_prompt = str(
            payload.get("youtube_title_prompt_template") or DEFAULT_YOUTUBE_TITLE_PROMPT
        ).strip()
        social_caption_prompt = str(
            payload.get("social_caption_prompt_template") or DEFAULT_SOCIAL_CAPTION_PROMPT
        ).strip()
        if "{{content}}" not in youtube_title_prompt:
            raise ValueError("Prompt YouTube title phải chứa biến {{content}}.")
        if "{{content}}" not in social_caption_prompt:
            raise ValueError("Prompt caption Facebook/YouTube phải chứa biến {{content}}.")
        title_caption_prompt = str(
            payload.get("title_caption_prompt_template") or f"{youtube_title_prompt}\n\n{social_caption_prompt}"
        ).strip()
        now = _now()
        values = (
            project_id,
            name,
            str(payload.get("character_id") or "").strip(),
            str(payload.get("voice_id") or "").strip(),
            str(payload.get("template_id") or "").strip(),
            prompt,
            title_caption_prompt,
            youtube_title_prompt,
            social_caption_prompt,
            now,
            now,
        )
        if not all(values[index] for index in (2, 3, 4)):
            raise ValueError("AutoProject cần character_id, voice_id và template_id.")
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO auto_projects
                    (id, name, character_id, voice_id, template_id, prompt_template,
                     title_caption_prompt_template, youtube_title_prompt_template,
                     social_caption_prompt_template, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
        except sqlite3.IntegrityError as exc:
            raise FileExistsError(f"AutoProject '{project_id}' đã tồn tại.") from exc
        return self.get_autoproject(project_id)

    def get_autoproject(self, project_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM auto_projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(f"Không tìm thấy AutoProject: {project_id}")
        return self._row(row) or {}

    def list_autoprojects(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM auto_projects ORDER BY updated_at DESC").fetchall()
        return [self._row(row) or {} for row in rows]

    def update_autoproject(self, project_id: str, payload: dict) -> dict:
        current = self.get_autoproject(project_id)
        def value_or_current(key: str):
            value = payload.get(key)
            return current[key] if value is None else value

        prompt = str(value_or_current("prompt_template") or "").strip()
        if "{{keyword}}" not in prompt:
            raise ValueError("Prompt phải chứa biến {{keyword}}.")
        youtube_title_prompt = str(value_or_current("youtube_title_prompt_template") or "").strip()
        social_caption_prompt = str(value_or_current("social_caption_prompt_template") or "").strip()
        if "{{content}}" not in youtube_title_prompt:
            raise ValueError("Prompt YouTube title phải chứa biến {{content}}.")
        if "{{content}}" not in social_caption_prompt:
            raise ValueError("Prompt caption Facebook/YouTube phải chứa biến {{content}}.")
        title_caption_prompt = str(
            value_or_current("title_caption_prompt_template") or f"{youtube_title_prompt}\n\n{social_caption_prompt}"
        ).strip()
        name = str(value_or_current("name") or "").strip()
        if not name:
            raise ValueError("AutoProject name không được để trống.")
        values = (
            name,
            str(value_or_current("character_id") or "").strip(),
            str(value_or_current("voice_id") or "").strip(),
            str(value_or_current("template_id") or "").strip(),
            prompt,
            title_caption_prompt,
            youtube_title_prompt,
            social_caption_prompt,
            str(value_or_current("status") or "active").strip(),
            _now(),
            project_id,
        )
        if not all(values[index] for index in (1, 2, 3)):
            raise ValueError("AutoProject cần character_id, voice_id và template_id.")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE auto_projects
                SET name = ?, character_id = ?, voice_id = ?, template_id = ?,
                    prompt_template = ?, title_caption_prompt_template = ?,
                    youtube_title_prompt_template = ?, social_caption_prompt_template = ?,
                    status = ?, updated_at = ?
                WHERE id = ?
                """,
                values,
            )
        return self.get_autoproject(project_id)

    def preview_keywords(self, project_id: str, values: Iterable[object]) -> dict:
        self.get_autoproject(project_id)
        with self._connect() as connection:
            existing = {
                row[0]
                for row in connection.execute(
                    "SELECT normalized_value FROM auto_keywords WHERE auto_project_id = ?",
                    (project_id,),
                )
            }
        normalized: list[str] = []
        duplicates: list[str] = []
        invalid: list[str] = []
        seen = set(existing)
        for raw in values:
            try:
                keyword = normalize_keyword(raw)
            except ValueError:
                invalid.append(str(raw))
                continue
            if keyword in seen:
                duplicates.append(keyword)
            else:
                normalized.append(keyword)
                seen.add(keyword)
        return {
            "added": normalized,
            "duplicates": duplicates,
            "invalid": invalid,
            "unsupported_rows": [],
            "existing": sorted(existing),
        }

    def add_keywords(self, project_id: str, values: Iterable[object]) -> dict:
        self.get_autoproject(project_id)
        added: list[str] = []
        duplicates: list[str] = []
        invalid: list[str] = []
        now = _now()
        with self._connect() as connection:
            existing = {
                row[0]
                for row in connection.execute(
                    "SELECT normalized_value FROM auto_keywords WHERE auto_project_id = ?",
                    (project_id,),
                )
            }
            seen = set(existing)
            for raw in values:
                try:
                    keyword = normalize_keyword(raw)
                except ValueError:
                    invalid.append(str(raw))
                    continue
                if keyword in seen:
                    duplicates.append(keyword)
                    continue
                keyword_id = _new_id("kw")
                connection.execute(
                    """
                    INSERT INTO auto_keywords
                    (id, auto_project_id, value, normalized_value, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'ready', ?, ?)
                    """,
                    (keyword_id, project_id, keyword, keyword, now, now),
                )
                seen.add(keyword)
                added.append(keyword)
        return {"added": added, "duplicates": duplicates, "invalid": invalid, "existing": list(existing)}

    def list_keywords(self, project_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM auto_keywords WHERE auto_project_id = ? ORDER BY created_at, id",
                (project_id,),
            ).fetchall()
        return [self._row(row) or {} for row in rows]

    def delete_keywords(self, project_id: str, keyword_ids: Iterable[str]) -> int:
        self.get_autoproject(project_id)
        ids = [str(value).strip() for value in keyword_ids if str(value).strip()]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            result = connection.execute(
                f"DELETE FROM auto_keywords WHERE auto_project_id = ? AND id IN ({placeholders})",
                [project_id, *ids],
            )
        return result.rowcount

    def mark_keyword_completed(self, keyword_id: str, generated_project_id: str) -> None:
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT auto_project_id, value, status FROM auto_keywords WHERE id = ?",
                (keyword_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Không tìm thấy keyword: {keyword_id}")
            if row["status"] == "completed":
                raise ValueError("Keyword đã completed và không được chạy lại trong v1.")
            connection.execute(
                "UPDATE auto_keywords SET status = 'completed', generated_project_id = ?, updated_at = ? WHERE id = ?",
                (generated_project_id, now, keyword_id),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO generated_projects
                (generated_project_id, auto_project_id, origin_auto_project_id, keyword_id, keyword_value, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (generated_project_id, row["auto_project_id"], row["auto_project_id"], keyword_id, row["value"], now),
            )

    def create_job(self, project_id: str, keyword_id: str, job_type: str) -> dict:
        self.get_autoproject(project_id)
        now = _now()
        job_id = _new_id("job")
        idempotency_key = f"{project_id}:{keyword_id}:{job_type}"
        with self._connect() as connection:
            keyword = connection.execute(
                "SELECT status, auto_project_id FROM auto_keywords WHERE id = ?",
                (keyword_id,),
            ).fetchone()
            if keyword is None or keyword["auto_project_id"] != project_id:
                raise KeyError(f"Không tìm thấy keyword: {keyword_id}")
            if keyword["status"] == "completed":
                raise ValueError("Keyword đã completed và không được chạy lại trong v1.")
            try:
                connection.execute(
                    """
                    INSERT INTO auto_jobs
                    (id, auto_project_id, keyword_id, job_type, stage, status, idempotency_key, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (job_id, project_id, keyword_id, job_type, job_type, "queued", idempotency_key, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Job này đã tồn tại hoặc đang được xử lý.") from exc
            row = connection.execute("SELECT * FROM auto_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row) or {}

    def get_generated_project(self, generated_project_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(auto_project_id, origin_auto_project_id) FROM generated_projects WHERE generated_project_id = ?",
                (generated_project_id,),
            ).fetchone()
        return row[0] if row and row[0] is not None else None

    def delete_autoproject(self, project_id: str) -> None:
        self.get_autoproject(project_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE generated_projects SET auto_project_id = NULL, keyword_id = NULL WHERE auto_project_id = ?",
                (project_id,),
            )
            connection.execute("DELETE FROM auto_projects WHERE id = ?", (project_id,))
