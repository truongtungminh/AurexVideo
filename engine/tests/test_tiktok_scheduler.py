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
    def test_scheduled_upload_queues_vps_just_in_time_publish(self) -> None:
        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload = {
            "project": "demo",
            "brand": "popsy",
            "tiktokCaption": "Scheduled post",
            "scheduledPublishAt": scheduled_at,
            "scheduleTimezone": "Asia/Ho_Chi_Minh",
        }
        path = Path("/tmp/tiktok-scheduled-test.mp4")
        path.write_bytes(b"video")
        try:
            with patch.object(tiktok, "read_social_config", return_value={
                    "zernio": {"api_key": "sk_test", "account_id": "acct_1"},
                    "r2": {"account_id": "r2", "bucket": "bucket", "access_key_id": "ak", "secret_access_key": "sk", "public_base_url": "https://media.example.com"},
                }), \
                 patch.object(tiktok, "require_project", return_value=Path("/tmp")), \
                 patch.object(tiktok, "upload_brand_for_project", return_value="popsy"), \
                 patch.object(tiktok, "resolve_social_brand_connection", return_value=("connection-1", {"api_key": "sk_test", "account_id": "acct_1", "_brand_connection": True})), \
                 patch.object(tiktok, "final_video_path_for_project", return_value=path), \
                 patch.object(tiktok, "read_expected_video_bytes", return_value=b"video"), \
                 patch.object(tiktok, "upload_scheduled_video_asset", return_value={"media_sha256": "a" * 64, "r2_key": "instagram/popsy/videos/demo/final.mp4", "r2_url": "https://media.example.com/final.mp4"}) as upload_r2, \
                 patch.object(tiktok, "schedule_on_vps", return_value={"id": "vps-tiktok-1", "worker_id": "vps-tiktok-1", "scheduledPublishAt": scheduled_at}) as schedule, \
                 patch.object(tiktok, "watch_tiktok_post") as watch, \
                 patch.object(tiktok, "record_scheduled_social_upload") as record, \
                 patch.object(tiktok, "queue_tiktok_watch") as outbox:
                result = tiktok.tiktok_upload_video(payload)

            upload_r2.assert_called_once()
            schedule.assert_called_once()
            self.assertEqual(schedule.call_args.args[:4], ("tiktok", "https://media.example.com/final.mp4", "Scheduled post", scheduled_at))
            self.assertEqual(schedule.call_args.kwargs["account_id"], "acct_1")
            watch.assert_not_called()
            record.assert_called_once_with(
                Path("/tmp"),
                "tiktok",
                scheduled_at,
                brand="popsy",
                connection_id="connection-1",
                worker_id="vps-tiktok-1",
                media_sha256="a" * 64,
                r2_key="instagram/popsy/videos/demo/final.mp4",
                r2_url="https://media.example.com/final.mp4",
            )
            outbox.assert_not_called()
            self.assertEqual(result["state"], "SCHEDULED")
            self.assertEqual(result["schedule_id"], "vps-tiktok-1")
            self.assertEqual(result["post_id"], "")
            self.assertEqual(result["worker_id"], "vps-tiktok-1")
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

    def test_run_item_records_tiktok_failure_for_dashboard(self) -> None:
        item = {
            "id": "social_schedule_tiktok_failed",
            "platform": "tiktok",
            "payload": {"project": "demo", "brand": "popsy"},
            "scheduledPublishAt": "2030-01-01T00:00:00Z",
        }
        with patch.object(tiktok, "tiktok_upload_video", side_effect=RuntimeError("Zernio capacity unavailable")), \
             patch.object(scheduler, "record_scheduled_social_failure") as record_failure, \
             patch.object(scheduler, "_read", return_value=[item]), \
             patch.object(scheduler, "_write") as write:
            scheduler._run_item(item)

        record_failure.assert_called_once_with(
            "demo",
            "tiktok",
            "Zernio capacity unavailable",
            scheduled_at="2030-01-01T00:00:00Z",
            brand="popsy",
        )
        saved = write.call_args.args[0][0]
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["error"], "Zernio capacity unavailable")


if __name__ == "__main__":
    unittest.main()
