from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy/aurex-social-worker"))
import worker


class SocialWorkerTests(unittest.TestCase):
    NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def _insert_job(self, root: Path, *, platform: str = "instagram") -> dict:
        video = root / "video.mp4"
        video.write_bytes(b"video")
        scheduled_at = "2029-12-31T23:59:00Z"
        with sqlite3.connect(root / "jobs.sqlite3") as conn:
            conn.execute(
                """INSERT INTO jobs
                   (id, platform, scheduled_at, caption, video_path, status,
                    result, error, created_at, updated_at, project, brand,
                    account_id, expected_media_sha256)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "vps-test-1",
                    platform,
                    scheduled_at,
                    "Caption",
                    str(video),
                    "queued",
                    None,
                    None,
                    scheduled_at,
                    scheduled_at,
                    "demo",
                    "bietchichomet",
                    "connection-1",
                    "",
                ),
            )
        with sqlite3.connect(root / "jobs.sqlite3") as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM jobs WHERE id='vps-test-1'").fetchone())

    def test_r2_failure_retries_before_provider_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(worker, "ROOT", root), \
                patch.object(worker, "DB", root / "jobs.sqlite3"), \
                patch.object(worker, "MEDIA", root / "media"), \
                patch.object(worker, "utc_now", return_value=self.NOW), \
                patch.object(worker, "SOCIAL_MEDIA_RETRY_SECONDS", 300), \
                patch.object(worker, "SOCIAL_MAX_ATTEMPTS", 3):
                worker.init_db()
                self._insert_job(root)
                job = worker._claim_due_jobs()[0]
                job["phase"] = "media"
                worker._job_failed(job, RuntimeError("R2 upload timed out"))

                with sqlite3.connect(root / "jobs.sqlite3") as conn:
                    row = conn.execute(
                        "SELECT status, phase, attempts, next_attempt_at, error FROM jobs WHERE id=?",
                        ("vps-test-1",),
                    ).fetchone()

        self.assertEqual(row[0], "retry_wait")
        self.assertEqual(row[1], "media")
        self.assertEqual(row[2], 1)
        self.assertEqual(row[3], "2030-01-01T00:05:00Z")
        self.assertIn("R2 upload timed out", row[4])

    def test_success_persists_provider_media_id_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(worker, "ROOT", root), \
                patch.object(worker, "DB", root / "jobs.sqlite3"), \
                patch.object(worker, "MEDIA", root / "media"), \
                patch.object(worker, "utc_now", return_value=self.NOW):
                worker.init_db()
                job = self._insert_job(root, platform="threads")
                worker._job_succeeded(
                    job,
                    {
                        "platform": "threads",
                        "container_id": "container-1",
                        "media_id": "media-1",
                        "state": "PUBLISHED",
                    },
                )

                with sqlite3.connect(root / "jobs.sqlite3") as conn:
                    row = conn.execute(
                        "SELECT status, phase, provider_post_id, provider_status FROM jobs WHERE id=?",
                        ("vps-test-1",),
                    ).fetchone()

        self.assertEqual(row, ("published", "published", "media-1", "PUBLISHED"))

    def test_graph_publish_rejects_success_without_provider_id(self) -> None:
        with patch.dict(
            os.environ,
            {"INSTAGRAM_USER_ID": "1784", "INSTAGRAM_ACCESS_TOKEN": "x" * 30},
            clear=False,
        ), patch.object(
            worker,
            "json_request",
            side_effect=[{"id": "container-1"}, {"unexpected": "ok"}],
        ), patch.object(worker, "graph_get", return_value={"status_code": "FINISHED"}):
            with self.assertRaisesRegex(RuntimeError, "no media id"):
                worker.graph_create_publish("instagram", "Caption", "https://media.example/video.mp4")


if __name__ == "__main__":
    unittest.main()
