from __future__ import annotations

import os
import json
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

    def _insert_job(self, root: Path, *, platform: str = "instagram", video_url: str = "") -> dict:
        video = root / "video.mp4"
        video.write_bytes(b"video")
        scheduled_at = "2029-12-31T23:59:00Z"
        with sqlite3.connect(root / "jobs.sqlite3") as conn:
            conn.execute(
                """INSERT INTO jobs
                   (id, platform, scheduled_at, caption, video_path, video_url, status,
                    result, error, created_at, updated_at, project, brand,
                    account_id, expected_media_sha256)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "vps-test-1",
                    platform,
                    scheduled_at,
                    "Caption",
                    str(video) if not video_url else "",
                    video_url,
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

    def test_scheduled_social_uses_existing_r2_url_without_worker_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(worker, "ROOT", root), \
                patch.object(worker, "DB", root / "jobs.sqlite3"), \
                patch.object(worker, "MEDIA", root / "media"), \
                patch.object(worker, "utc_now", return_value=self.NOW), \
                patch.object(worker, "_verify_public_video_url") as verify, \
                patch.object(
                    worker,
                    "graph_create_publish",
                    return_value={
                        "platform": "instagram",
                        "container_id": "container-1",
                        "media_id": "media-1",
                        "state": "PUBLISHED",
                    },
                ), patch.object(worker, "upload_r2") as upload:
                worker.init_db()
                job = self._insert_job(
                    root,
                    video_url="https://media.example.com/scheduled.mp4",
                )
                result = worker.execute(job)

        verify.assert_called_once_with("https://media.example.com/scheduled.mp4")
        upload.assert_not_called()
        self.assertEqual(result["video_url"], "https://media.example.com/scheduled.mp4")

    def test_scheduled_tiktok_uses_existing_r2_url_at_publish_time(self) -> None:
        calls = []

        def fake_zernio(url, method, body, connection, **kwargs):
            calls.append((url, method, body))
            return {
                "data": {
                    "post": {
                        "_id": "post-1",
                        "status": "published",
                        "platformPostUrl": {"tiktok": "https://www.tiktok.com/@acct/video/1"},
                    }
                }
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(worker, "ROOT", root), \
                patch.object(worker, "DB", root / "jobs.sqlite3"), \
                patch.object(worker, "MEDIA", root / "media"), \
                patch.object(worker, "_verify_public_video_url") as verify, \
                patch.object(worker, "_tiktok_connection", return_value={"api_key": "sk", "account_id": "connection-1", "base_url": "https://zernio.example"}), \
                patch.object(worker, "_zernio_request", side_effect=fake_zernio), \
                patch.object(worker, "upload_r2") as upload:
                worker.init_db()
                job = self._insert_job(
                    root,
                    platform="tiktok",
                    video_url="https://media.example.com/tiktok.mp4",
                )
                result = worker.execute(job)

        verify.assert_called_once_with("https://media.example.com/tiktok.mp4")
        upload.assert_not_called()
        self.assertEqual(calls[0][0], "https://zernio.example/posts")
        self.assertTrue(calls[0][2]["publishNow"])
        self.assertEqual(calls[0][2]["mediaItems"][0]["url"], "https://media.example.com/tiktok.mp4")
        self.assertEqual(result["post_id"], "post-1")

    def test_tiktok_schedule_validation_uses_zernio_connection(self) -> None:
        with patch.object(worker, "_tiktok_connection", return_value={"api_key": "sk", "account_id": "acct", "base_url": "https://zernio.example"}) as tiktok_connection, \
            patch.object(worker, "_social_connection") as social_connection:
            worker._validate_social_account("tiktok", "acct", "bietchichomet")

        tiktok_connection.assert_called_once_with("bietchichomet", "acct")
        social_connection.assert_not_called()

    def test_idempotency_index_rejects_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(worker, "ROOT", root), \
                patch.object(worker, "DB", root / "jobs.sqlite3"), \
                patch.object(worker, "MEDIA", root / "media"):
                worker.init_db()
                with sqlite3.connect(root / "jobs.sqlite3") as conn:
                    values = (
                        "vps-one", "instagram", "2030-01-01T00:00:00Z", "Caption", "",
                        "https://media.example.com/video.mp4", "queued", None, None,
                        "2030-01-01T00:00:00Z", "2030-01-01T00:00:00Z", "demo",
                        "bietchichomet", "1784", "", "", 0, None, "", "", "",
                        "a" * 64,
                    )
                    conn.execute(
                        """INSERT INTO jobs
                           (id, platform, scheduled_at, caption, video_path, video_url, status,
                            result, error, created_at, updated_at, project, brand, account_id,
                            tiktok_settings, expected_media_sha256, attempts, next_attempt_at,
                            provider_post_id, provider_status, delivery_status, idempotency_key)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        values,
                    )
                    duplicate = list(values)
                    duplicate[0] = "vps-two"
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(
                            """INSERT INTO jobs
                               (id, platform, scheduled_at, caption, video_path, video_url, status,
                                result, error, created_at, updated_at, project, brand, account_id,
                                tiktok_settings, expected_media_sha256, attempts, next_attempt_at,
                                provider_post_id, provider_status, delivery_status, idempotency_key)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            duplicate,
                        )

    def test_brand_scoped_connection_rejects_wrong_account(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            connections_file = Path(temp_dir) / "social-connections.json"
            connections_file.write_text(
                json.dumps(
                    {
                        "instagram": [
                            {
                                "brand": "engzy",
                                "user_id": "17841441341969769",
                                "access_token": "engzy-token",
                                "api_mode": "instagram_login",
                            },
                            {
                                "brand": "bietchichomet",
                                "user_id": "28632893609702302",
                                "access_token": "bietchichomet-token",
                                "api_mode": "instagram_login",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(worker, "SOCIAL_CONNECTIONS_FILE", connections_file):
                selected = worker._social_connection(
                    "instagram",
                    "17841441341969769",
                    "engzy",
                )
                self.assertEqual(selected["user_id"], "17841441341969769")
                self.assertEqual(selected["access_token"], "engzy-token")
                with self.assertRaisesRegex(RuntimeError, "không được bind"):
                    worker._social_connection(
                        "instagram",
                        "17841441341969769",
                        "bietchichomet",
                    )

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
