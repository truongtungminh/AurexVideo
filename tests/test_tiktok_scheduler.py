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
    def test_scheduled_upload_creates_zernio_post_and_registers_vps_watch(self) -> None:
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
        requests = []

        def fake_request(url, method, body, config, headers=None):
            requests.append((url, method, body, headers or {}))
            if url.endswith("/media/presign"):
                return {"data": {"uploadUrl": "https://upload.example/put", "publicUrl": "https://cdn.example/video.mp4"}}
            return {"data": {"post": {"_id": "post-scheduled-1", "status": "scheduled"}}}

        try:
            with patch.object(tiktok, "read_social_config", return_value={"zernio": {"api_key": "sk_test", "account_id": "acct_1"}}), \
                 patch.object(tiktok, "require_project", return_value=Path("/tmp")), \
                 patch.object(tiktok, "upload_brand_for_project", return_value="popsy"), \
                 patch.object(tiktok, "resolve_social_brand_connection", return_value=("connection-1", {"api_key": "sk_test", "account_id": "acct_1", "_brand_connection": True})), \
                 patch.object(tiktok, "final_video_path_for_project", return_value=path), \
                 patch.object(tiktok, "read_expected_video_bytes", return_value=b"video"), \
                 patch.object(tiktok, "_json_request", side_effect=fake_request), \
                 patch.object(tiktok, "_put_file") as put_file, \
                 patch.object(tiktok, "watch_tiktok_post", return_value={"id": "watch-1"}) as watch, \
                 patch.object(tiktok, "record_scheduled_social_upload") as record, \
                 patch.object(tiktok, "queue_tiktok_watch") as outbox:
                result = tiktok.tiktok_upload_video(payload)

            self.assertEqual(len(requests), 2)
            self.assertTrue(requests[0][0].endswith("/media/presign"))
            self.assertEqual(requests[1][0], "https://zernio.com/api/v1/posts")
            scheduled_body = requests[1][2]
            self.assertEqual(scheduled_body["scheduledFor"], scheduled_at)
            self.assertEqual(scheduled_body["timezone"], "UTC")
            self.assertFalse(scheduled_body["isDraft"])
            self.assertNotIn("publishNow", scheduled_body)
            self.assertEqual(scheduled_body["platforms"][0]["accountId"], "acct_1")
            self.assertRegex(requests[1][3]["X-Request-ID"], r"^[0-9a-f-]{36}$")
            put_file.assert_called_once_with("https://upload.example/put", path)
            watch.assert_called_once_with("post-scheduled-1", project="demo", brand="popsy", account_id="acct_1")
            record.assert_called_once_with(
                Path("/tmp"),
                "tiktok",
                scheduled_at,
                brand="popsy",
                connection_id="connection-1",
                post_id="post-scheduled-1",
                worker_id="watch-1",
            )
            outbox.assert_not_called()
            self.assertEqual(result["state"], "SCHEDULED")
            self.assertEqual(result["schedule_id"], "post-scheduled-1")
            self.assertEqual(result["post_id"], "post-scheduled-1")
            self.assertEqual(result["worker_id"], "watch-1")
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
