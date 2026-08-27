from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_upload.scheduler as scheduler
import social_upload.tiktok as tiktok


class TiktokSchedulerTests(unittest.TestCase):
    def test_scheduled_upload_queues_locally_without_calling_zernio(self) -> None:
        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload = {
            "project": "demo",
            "brand": "popsy",
            "tiktokCaption": "Scheduled post",
            "scheduledPublishAt": scheduled_at,
        }
        path = Path("/tmp/tiktok-scheduled-test.mp4")
        path.write_bytes(b"video")
        try:
            with patch.object(tiktok, "read_social_config", return_value={"zernio": {"api_key": "sk_test", "account_id": "acct_1"}}), \
                 patch.object(tiktok, "require_project", return_value=Path("/tmp")), \
                 patch.object(tiktok, "upload_brand_for_project", return_value="popsy"), \
                 patch.object(tiktok, "resolve_social_brand_connection", return_value=("connection-1", {"api_key": "sk_test", "account_id": "acct_1", "_brand_connection": True})), \
                 patch.object(tiktok, "final_video_path_for_project", return_value=path), \
                 patch.object(tiktok, "read_expected_video_bytes", return_value=b"video"), \
                 patch.object(tiktok, "schedule_upload", return_value={"id": "schedule-1", "scheduledPublishAt": scheduled_at}) as queue, \
                 patch.object(tiktok, "record_scheduled_social_upload") as record, \
                 patch.object(tiktok, "urlopen") as urlopen:
                result = tiktok.tiktok_upload_video(payload)

            queue.assert_called_once_with("tiktok", {**payload, "brand": "popsy"}, scheduled_at)
            record.assert_called_once_with(Path("/tmp"), "tiktok", scheduled_at, brand="popsy", connection_id="connection-1")
            urlopen.assert_not_called()
            self.assertEqual(result["state"], "SCHEDULED")
            self.assertEqual(result["schedule_id"], "schedule-1")
        finally:
            path.unlink(missing_ok=True)

    def test_run_item_dispatches_tiktok_to_upload_worker(self) -> None:
        item = {
            "id": "social_schedule_tiktok_1",
            "platform": "tiktok",
            "payload": {"project": "demo"},
        }
        with patch.object(tiktok, "tiktok_upload_video", return_value={"state": "DRAFT"}) as upload, \
             patch.object(scheduler, "_read", return_value=[item]), \
             patch.object(scheduler, "_write") as write:
            scheduler._run_item(item)

        upload.assert_called_once_with({"project": "demo"})
        saved = write.call_args.args[0][0]
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["result"], {"state": "DRAFT"})
        self.assertNotIn("error", saved)


if __name__ == "__main__":
    unittest.main()
