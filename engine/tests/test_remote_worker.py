from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from social_upload import remote_worker


class RemoteWorkerTests(unittest.TestCase):
    def test_schedule_on_vps_sends_r2_url_without_scp_or_secrets(self) -> None:
        video_url = "https://media.example.com/instagram/scheduled.mp4"
        digest = hashlib.sha256(b"video").hexdigest()
        with patch.object(
            remote_worker,
            "read_social_config",
            return_value={
                "social_worker": {
                    "url": "https://worker.example",
                    "api_key": "worker-secret",
                }
            },
        ), patch.object(
            remote_worker,
            "_worker_request",
            return_value={"id": "vps_1", "status": "queued"},
        ) as request:
            result = remote_worker.schedule_on_vps(
                "instagram",
                video_url,
                "caption",
                "2030-01-01T00:00:00Z",
                project="demo",
                brand="bietchichomet",
                account_id="acct_1",
                media_sha256=digest,
                r2_key="instagram/demo/scheduled-video.mp4",
            )

        payload = request.call_args.args[2]
        self.assertEqual(request.call_args.args[:2], ("/schedule", "POST"))
        self.assertEqual(payload["platform"], "instagram")
        self.assertEqual(payload["project"], "demo")
        self.assertEqual(payload["brand"], "bietchichomet")
        self.assertEqual(payload["accountId"], "acct_1")
        self.assertEqual(payload["videoUrl"], video_url)
        self.assertEqual(payload["r2Key"], "instagram/demo/scheduled-video.mp4")
        self.assertEqual(payload["expectedMediaSha256"], digest)
        self.assertEqual(
            payload["idempotencyKey"],
            remote_worker.schedule_idempotency_key(
                "instagram",
                video_url,
                "caption",
                "2030-01-01T00:00:00Z",
                project="demo",
                brand="bietchichomet",
                account_id="acct_1",
                media_sha256=digest,
            ),
        )
        self.assertNotIn("videoPath", payload)
        self.assertNotIn("api_key", payload)
        self.assertEqual(result["id"], "vps_1")

    def test_worker_job_status_uses_authenticated_jobs_endpoint(self) -> None:
        with patch.object(remote_worker, "_worker_request", return_value={"id": "vps-job-1", "status": "queued"}) as request:
            result = remote_worker.worker_job_status("vps-job-1")

        self.assertEqual(result["status"], "queued")
        request.assert_called_once_with("/jobs/vps-job-1")

    def test_watch_tiktok_post_is_best_effort_api_registration(self) -> None:
        with patch.object(remote_worker, "_worker_request", return_value={"ok": True, "postId": "post_1"}) as request:
            result = remote_worker.watch_tiktok_post(
                "post_1",
                project="demo",
                brand="bietchichomet",
                account_id="acct_1",
                scheduled_for="2030-01-01T00:00:00Z",
            )

        self.assertEqual(result["postId"], "post_1")
        self.assertEqual(request.call_args.args[:2], ("/watch-tiktok", "POST"))
        self.assertEqual(request.call_args.args[2]["accountId"], "acct_1")
        self.assertEqual(request.call_args.args[2]["scheduledFor"], "2030-01-01T00:00:00Z")

    def test_watch_outbox_survives_worker_registration_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.object(
            remote_worker,
            "_watch_outbox_path",
            return_value=Path(root) / "tiktok-watch-outbox.json",
        ), patch.object(
            remote_worker,
            "watch_tiktok_post",
            side_effect=RuntimeError("worker offline"),
        ):
            queued = remote_worker.queue_tiktok_watch(
                "post_1",
                project="demo",
                brand="bietchichomet",
                account_id="acct_1",
                scheduled_for="2030-01-01T00:00:00Z",
            )
            self.assertEqual(queued["postId"], "post_1")
            self.assertEqual(remote_worker.flush_tiktok_watch_outbox(), 0)
            saved = json.loads((Path(root) / "tiktok-watch-outbox.json").read_text())
            self.assertEqual(saved[0]["postId"], "post_1")
            self.assertEqual(saved[0]["attempts"], 1)
            self.assertEqual(saved[0]["scheduledFor"], "2030-01-01T00:00:00Z")

        with tempfile.TemporaryDirectory() as root, patch.object(
            remote_worker,
            "_watch_outbox_path",
            return_value=Path(root) / "tiktok-watch-outbox.json",
        ), patch.object(
            remote_worker,
            "watch_tiktok_post",
            return_value={"ok": True},
        ):
            remote_worker.queue_tiktok_watch("post_2")
            self.assertEqual(remote_worker.flush_tiktok_watch_outbox(), 1)
            self.assertEqual(json.loads((Path(root) / "tiktok-watch-outbox.json").read_text()), [])


if __name__ == "__main__":
    unittest.main()
