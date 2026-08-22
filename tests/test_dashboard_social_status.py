from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from social_upload import metadata


class DashboardSocialStatusTests(unittest.TestCase):
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

    def test_scheduled_status_has_machine_time_and_display_label(self) -> None:
        temp_dir, project_dir = self._project_with_metadata(
            {"threads": {"state": "SCHEDULED", "scheduledAt": "2030-01-01T00:00:00Z"}}
        )
        try:
            status = metadata.project_social_status(project_dir)
        finally:
            temp_dir.cleanup()

        self.assertEqual(status["label"], "Đã lên lịch")
        self.assertTrue(status["scheduled"])
        self.assertEqual(status["scheduled_at"], "2030-01-01T00:00:00Z")
        self.assertTrue(status["scheduled_label"].startswith("Lúc "))
        self.assertEqual(status["scheduled_platforms"], ["Threads"])

    def test_scheduled_status_precedes_completed_social_upload(self) -> None:
        temp_dir, project_dir = self._project_with_metadata(
            {
                "youtube": {"videoId": "published-video", "state": "PUBLISHED"},
                "instagram": {"scheduled_publish_at": "2030-02-01T00:00:00Z"},
            }
        )
        try:
            status = metadata.project_social_status(project_dir)
        finally:
            temp_dir.cleanup()

        self.assertEqual(status["label"], "Đã lên lịch")
        self.assertTrue(status["posted"])
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
            )
            saved = metadata.read_project_upload_metadata(project_dir)["social"]["instagram"]
        finally:
            temp_dir.cleanup()

        self.assertEqual(saved["state"], "SCHEDULED")
        self.assertEqual(saved["scheduledAt"], "2030-03-01T00:00:00Z")
        self.assertEqual(saved["brand"], "popsy")
        self.assertEqual(saved["connectionId"], "account-1")
        self.assertNotIn("access_token", saved)
        self.assertNotIn("schedule_id", saved)
        self.assertNotIn("postedAt", saved)

    def test_complete_and_pending_labels_remain_unchanged(self) -> None:
        complete_dir, complete_project = self._project_with_metadata(
            {"binance": {"postId": "post-1", "state": "PUBLISHED"}}
        )
        pending_dir, pending_project = self._project_with_metadata({})
        try:
            self.assertEqual(metadata.project_social_status(complete_project)["label"], "Complete")
            self.assertEqual(metadata.project_social_status(pending_project)["label"], "Pending")
        finally:
            complete_dir.cleanup()
            pending_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
