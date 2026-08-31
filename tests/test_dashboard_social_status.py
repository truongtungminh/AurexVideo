from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from social_upload import metadata


class DashboardSocialStatusTests(unittest.TestCase):
    NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)

    def _project_with_metadata(self, social: dict) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp_dir = tempfile.TemporaryDirectory()
        project_dir = Path(temp_dir.name) / "demo"
        project_dir.mkdir()
        (project_dir / "topic.json").write_text('{"brand": "popsy"}', encoding="utf-8")
        (project_dir / "upload-metadata.json").write_text(
            json.dumps({"social": social}), encoding="utf-8"
        )
        return temp_dir, project_dir

    def test_normalizes_and_formats_scheduled_time(self) -> None:
        scheduled_at = metadata.normalize_scheduled_social_time("2030-01-01T07:00:00+07:00")

        self.assertEqual(scheduled_at, "2030-01-01T00:00:00Z")
        self.assertRegex(
            metadata.localized_scheduled_social_label(scheduled_at),
            r"^Lúc \d{2}:\d{2} · 01/01/2030$",
        )

    def test_future_scheduled_status_has_machine_time_and_display_label(self) -> None:
        temp_dir, project_dir = self._project_with_metadata(
            {"threads": {"state": "SCHEDULED", "scheduledAt": "2030-01-01T07:30:00+07:00"}}
        )
        try:
            status = metadata.project_social_status(project_dir, now=self.NOW)
        finally:
            temp_dir.cleanup()

        self.assertEqual(status["label"], "Đã lên lịch")
        self.assertTrue(status["scheduled"])
        self.assertEqual(status["scheduled_at"], "2030-01-01T00:30:00Z")
        self.assertTrue(status["scheduled_label"].startswith("Lúc "))
        self.assertEqual(status["scheduled_platforms"], ["Threads"])

    def test_past_scheduled_status_is_published_without_post_metadata(self) -> None:
        temp_dir, project_dir = self._project_with_metadata(
            {"threads": {"state": "SCHEDULED", "scheduledAt": "2030-01-01T07:00:00+07:00"}}
        )
        try:
            status = metadata.project_social_status(project_dir, now=self.NOW)
        finally:
            temp_dir.cleanup()

        self.assertEqual(status["label"], "Published")
        self.assertTrue(status["posted"])
        self.assertFalse(status["scheduled"])
        self.assertEqual(status["title"], "Threads")
        self.assertEqual(status["platforms"], ["Threads"])
        self.assertEqual(status["published_at"], "2030-01-01T00:00:00Z")
        self.assertTrue(status["published_label"].startswith("Lúc "))
        self.assertEqual(status["scheduled_at"], "")
        self.assertEqual(status["scheduled_label"], "")

    def test_future_scheduled_status_keeps_past_scheduled_platform_published(self) -> None:
        temp_dir, project_dir = self._project_with_metadata(
            {
                "youtube": {"state": "SCHEDULED", "scheduledAt": "2030-01-01T00:00:00Z"},
                "instagram": {"scheduled_publish_at": "2030-01-01T07:30:00+07:00"},
            }
        )
        try:
            status = metadata.project_social_status(project_dir, now=self.NOW)
        finally:
            temp_dir.cleanup()

        self.assertEqual(status["label"], "Đã lên lịch")
        self.assertTrue(status["posted"])
        self.assertTrue(status["scheduled"])
        self.assertEqual(status["scheduled_at"], "2030-01-01T00:30:00Z")
        self.assertEqual(status["scheduled_platforms"], ["Instagram"])
        self.assertEqual(status["platforms"], ["Instagram", "YouTube"])

    def test_queued_record_stores_only_dashboard_safe_schedule_fields(self) -> None:
        temp_dir, project_dir = self._project_with_metadata({})
        try:
            metadata.record_scheduled_social_upload(
                project_dir,
                "instagram",
                "2030-03-01T00:00:00Z",
                brand="popsy",
                connection_id="account-1",
                worker_id="vps-job-1",
            )
            saved = metadata.read_project_upload_metadata(project_dir)["social"]["instagram"]
        finally:
            temp_dir.cleanup()

        self.assertEqual(saved["state"], "SCHEDULED")
        self.assertEqual(saved["scheduledAt"], "2030-03-01T00:00:00Z")
        self.assertEqual(saved["brand"], "popsy")
        self.assertEqual(saved["connectionId"], "account-1")
        self.assertEqual(saved["workerId"], "vps-job-1")
        self.assertNotIn("access_token", saved)
        self.assertNotIn("schedule_id", saved)
        self.assertNotIn("postedAt", saved)

    def test_published_and_pending_labels(self) -> None:
        published_dir, published_project = self._project_with_metadata(
            {"binance": {"postId": "post-1", "state": "PUBLISHED"}}
        )
        pending_dir, pending_project = self._project_with_metadata({})
        try:
            self.assertEqual(metadata.project_social_status(published_project, now=self.NOW)["label"], "Published")
            self.assertEqual(metadata.project_social_status(pending_project)["label"], "Pending")
        finally:
            published_dir.cleanup()
            pending_dir.cleanup()

    def test_draft_is_not_reported_as_published(self) -> None:
        temp_dir, project_dir = self._project_with_metadata(
            {"tiktok": {"postId": "post-1", "state": "DRAFT"}}
        )
        try:
            status = metadata.project_social_status(project_dir, now=self.NOW)
        finally:
            temp_dir.cleanup()

        self.assertEqual(status["label"], "Draft")
        self.assertFalse(status["posted"])
        self.assertTrue(status["drafted"])
        self.assertEqual(status["draft_platforms"], ["TikTok"])
        self.assertIn("Creator Inbox", status["title"])

    def test_failed_scheduled_status_is_not_reported_as_published(self) -> None:
        temp_dir, project_dir = self._project_with_metadata(
            {
                "tiktok": {
                    "state": "FAILED",
                    "scheduledAt": "2030-01-01T00:00:00Z",
                    "failedAt": "2030-01-01T00:01:00Z",
                    "error": "Zernio tạo post bị từ chối.",
                }
            }
        )
        try:
            status = metadata.project_social_status(project_dir, now=self.NOW)
        finally:
            temp_dir.cleanup()

        self.assertEqual(status["label"], "Lỗi đăng")
        self.assertEqual(status["state"], "failed")
        self.assertFalse(status["posted"])
        self.assertTrue(status["failed"])
        self.assertEqual(status["failed_platforms"], ["TikTok"])
        self.assertIn("Zernio tạo post bị từ chối", status["title"])

    def test_failed_platform_is_visible_alongside_a_future_schedule(self) -> None:
        temp_dir, project_dir = self._project_with_metadata(
            {
                "youtube": {"state": "SCHEDULED", "scheduledAt": "2030-01-01T01:00:00Z"},
                "tiktok": {"state": "FAILED", "error": "TikTok account unavailable."},
            }
        )
        try:
            status = metadata.project_social_status(project_dir, now=self.NOW)
        finally:
            temp_dir.cleanup()

        self.assertEqual(status["label"], "Đã lên lịch · Có lỗi")
        self.assertTrue(status["scheduled"])
        self.assertTrue(status["failed"])
        self.assertEqual(status["failed_platforms"], ["TikTok"])
        self.assertIn("Lỗi: TikTok", status["title"])

    def test_record_scheduled_failure_persists_dashboard_error(self) -> None:
        temp_dir, project_dir = self._project_with_metadata({})
        try:
            with patch.object(metadata, "PROJECT_ROOT", Path(temp_dir.name)):
                saved = metadata.record_scheduled_social_failure(
                    "demo",
                    "tiktok",
                    "TikTok direct posting is at capacity right now.",
                    scheduled_at="2030-01-01T00:00:00Z",
                    brand="popsy",
                )
        finally:
            temp_dir.cleanup()

        self.assertEqual(saved["state"], "FAILED")
        self.assertEqual(saved["scheduledAt"], "2030-01-01T00:00:00Z")
        self.assertTrue(saved["failedAt"])
        self.assertEqual(saved["error"], "TikTok direct posting is at capacity right now.")

    def test_published_and_draft_status_is_mixed(self) -> None:
        temp_dir, project_dir = self._project_with_metadata(
            {
                "youtube": {"postId": "video-1", "state": "PUBLISHED"},
                "tiktok": {"postId": "post-1", "state": "DRAFT"},
            }
        )
        try:
            status = metadata.project_social_status(project_dir, now=self.NOW)
        finally:
            temp_dir.cleanup()

        self.assertEqual(status["label"], "Published + Draft")
        self.assertTrue(status["posted"])
        self.assertTrue(status["drafted"])
        self.assertEqual(status["platforms"], ["YouTube"])
        self.assertEqual(status["draft_platforms"], ["TikTok"])

    def test_published_status_exposes_posted_time(self) -> None:
        temp_dir, project_dir = self._project_with_metadata(
            {"youtube": {"postId": "video-1", "postedAt": "2030-01-01T07:45:00+07:00"}}
        )
        try:
            status = metadata.project_social_status(project_dir, now=self.NOW)
        finally:
            temp_dir.cleanup()

        self.assertEqual(status["published_at"], "2030-01-01T00:45:00Z")
        self.assertRegex(status["published_label"], r"^Lúc \d{2}:\d{2} · 01/01/2030$")


if __name__ == "__main__":
    unittest.main()
