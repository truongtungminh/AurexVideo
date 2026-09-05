from __future__ import annotations

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


class TikTokWatcherFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixed_now = datetime(2030, 1, 1, 0, 0, tzinfo=timezone.utc)

    def test_provider_error_message_is_snapshot_failure_not_transport_error(self) -> None:
        response = {
            "post": {
                "_id": "post-1",
                "scheduledFor": "2030-01-01T00:00:00.000Z",
                "status": "failed",
                "platforms": [{
                    "platform": "tiktok",
                    "status": "failed",
                    "errorCategory": "account_issue",
                    "errorMessage": "TikTok direct posting is at capacity right now.",
                }],
            }
        }

        self.assertFalse(worker._response_has_error(response))
        snapshot = worker._tiktok_snapshot(response)
        self.assertEqual(snapshot["state"], "FAILED")
        self.assertIn("capacity", snapshot["error"])
        self.assertEqual(snapshot["error_category"], "account_issue")
        self.assertEqual(snapshot["scheduled_for"], "2030-01-01T00:00:00.000Z")
        self.assertTrue(worker._retryable_tiktok_failure(snapshot))

    def test_provider_error_category_alone_is_retryable_when_capacity(self) -> None:
        response = {
            "post": {
                "_id": "post-1",
                "status": "failed",
                "platforms": [{
                    "platform": "tiktok",
                    "status": "failed",
                    "errorCategory": "capacity",
                }],
            }
        }

        snapshot = worker._tiktok_snapshot(response)
        self.assertEqual(snapshot["state"], "FAILED")
        self.assertEqual(snapshot["error"], "capacity")
        self.assertTrue(worker._retryable_tiktok_failure(snapshot))

    def test_terminal_marker_wins_over_provider_error(self) -> None:
        for marker, expected_state, expected_delivery in (
            ("v_pub_url~v2.1", "PUBLISHED", "DIRECT_POST"),
            ("v_inbox_url~v2.1", "INBOX_DELIVERED", "CREATOR_INBOX"),
        ):
            with self.subTest(marker=marker):
                response = {
                    "post": {
                        "status": "failed",
                        "platforms": [{
                            "platform": "tiktok",
                            "status": "failed",
                            "platformPostId": marker,
                            "errorMessage": "capacity",
                        }],
                    }
                }
                snapshot = worker._tiktok_snapshot(response)
                self.assertEqual(snapshot["state"], expected_state)
                self.assertEqual(snapshot["delivery_status"], expected_delivery)

    def test_future_schedule_is_checked_after_scheduled_time(self) -> None:
        with patch.object(worker, "utc_now", return_value=self.fixed_now):
            next_check = worker._next_tiktok_check({
                "scheduled_for": "2030-01-01T00:10:00.000Z",
            })
        self.assertEqual(next_check, "2030-01-01T00:10:05Z")

    def test_invalid_or_elapsed_schedule_uses_poll_interval(self) -> None:
        with patch.object(worker, "utc_now", return_value=self.fixed_now):
            elapsed = worker._next_tiktok_check({
                "scheduled_for": "2029-12-31T23:59:00Z",
            })
            malformed = worker._next_tiktok_check({"scheduled_for": "not-a-time"})
        self.assertEqual(elapsed, "2030-01-01T00:01:00Z")
        self.assertEqual(malformed, "2030-01-01T00:01:00Z")

    def test_future_watch_is_not_polled_before_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "DB", root / "jobs.sqlite3"), \
                 patch.object(worker, "MEDIA", root / "media"), \
                 patch.object(worker, "TIKTOK_POLL_SECONDS", 0), \
                 patch.object(worker, "utc_now", return_value=self.fixed_now), \
                 patch.object(worker, "_tiktok_status") as status:
                worker.init_db()
                worker._record_tiktok_watch(
                    "post-1",
                    project="demo",
                    brand="bietchichomet",
                    account_id="acct-1",
                    scheduled_for="2030-01-01T00:10:00Z",
                )
                worker._poll_tiktok_watches()

                status.assert_not_called()
                with sqlite3.connect(root / "jobs.sqlite3") as conn:
                    row = conn.execute(
                        "SELECT state, next_check_at, result FROM tiktok_watches WHERE post_id=?",
                        ("post-1",),
                    ).fetchone()
                self.assertEqual(row[0], "monitoring")
                self.assertEqual(row[1], "2030-01-01T00:10:05Z")
                self.assertEqual(json.loads(row[2])["scheduled_for"], "2030-01-01T00:10:00Z")

    def test_capacity_snapshot_enters_five_minute_retry_wait(self) -> None:
        failure = {
            "state": "FAILED",
            "provider_status": "failed",
            "delivery_status": "DIRECT_POST",
            "error": "TikTok direct posting is at capacity right now.",
            "scheduled_for": "2029-12-31T23:59:00Z",
            "post_id": "post-1",
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "DB", root / "jobs.sqlite3"), \
                 patch.object(worker, "MEDIA", root / "media"), \
                 patch.object(worker, "TIKTOK_POLL_SECONDS", 0), \
                 patch.object(worker, "utc_now", return_value=self.fixed_now), \
                 patch.object(worker, "_tiktok_status", return_value=failure):
                worker.init_db()
                worker._record_tiktok_watch("post-1", scheduled_for="2029-12-31T23:59:00Z")
                worker._poll_tiktok_watches()

                with sqlite3.connect(root / "jobs.sqlite3") as conn:
                    row = conn.execute(
                        "SELECT state, retry_attempts, next_check_at, next_retry_at, error "
                        "FROM tiktok_watches WHERE post_id=?",
                        ("post-1",),
                    ).fetchone()
                self.assertEqual(row[0], "retry_wait")
                self.assertEqual(row[1], 1)
                self.assertEqual(row[2], "2030-01-01T00:05:00Z")
                self.assertEqual(row[3], "2030-01-01T00:05:00Z")
                self.assertIn("capacity", row[4])

    def test_duplicate_registration_preserves_terminal_watch(self) -> None:
        terminal = {
            "state": "INBOX_DELIVERED",
            "provider_status": "published",
            "delivery_status": "CREATOR_INBOX",
            "platform_post_id": "v_inbox_url~v2.1",
            "post_id": "post-1",
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(worker, "ROOT", root), \
                 patch.object(worker, "DB", root / "jobs.sqlite3"), \
                 patch.object(worker, "MEDIA", root / "media"), \
                 patch.object(worker, "utc_now", return_value=self.fixed_now):
                worker.init_db()
                worker._record_tiktok_watch("post-1", snapshot=terminal)
                result = worker._record_tiktok_watch(
                    "post-1",
                    project="demo",
                    brand="bietchichomet",
                    account_id="acct-1",
                    scheduled_for="2030-01-01T00:10:00Z",
                )

                with sqlite3.connect(root / "jobs.sqlite3") as conn:
                    row = conn.execute(
                        "SELECT state, next_check_at, result FROM tiktok_watches WHERE post_id=?",
                        ("post-1",),
                    ).fetchone()
                self.assertEqual(result["state"], "inbox_delivered")
                self.assertEqual(row[0], "inbox_delivered")
                self.assertIsNone(row[1])
                saved = json.loads(row[2])
                self.assertEqual(saved["platform_post_id"], "v_inbox_url~v2.1")
                self.assertEqual(saved["scheduled_for"], "2030-01-01T00:10:00Z")


if __name__ == "__main__":
    unittest.main()
