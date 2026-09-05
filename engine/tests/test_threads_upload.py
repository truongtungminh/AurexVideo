from __future__ import annotations

import hashlib
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_upload.threads as threads


class ThreadsUploadTests(unittest.TestCase):
    def test_scheduled_threads_uploads_to_r2_before_vps_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            video_path = project_dir / "final_video.mp4"
            video_path.write_bytes(b"fake video")
            scheduled_at = "2099-01-01T00:00:00Z"
            r2_config = {
                "account_id": "account",
                "bucket": "media",
                "access_key_id": "key",
                "secret_access_key": "secret",
                "public_base_url": "https://media.example.com",
            }
            threads_config = {
                "threads_user_id": "user-2",
                "access_token": "x" * 30,
                "_brand_connection": True,
            }
            queued = {
                "id": "vps-job-2",
                "scheduledPublishAt": scheduled_at,
                "worker_id": "vps-job-2",
            }
            with patch.object(
                threads,
                "read_social_config",
                return_value={"threads": threads_config, "r2": r2_config},
            ), patch.object(threads, "require_project", return_value=project_dir), \
                patch.object(
                    threads,
                    "resolve_social_brand_connection",
                    return_value=("connection-2", threads_config),
                ), patch.object(threads, "final_video_path_for_project", return_value=video_path), \
                patch.object(threads, "validate_upload_video", return_value={}), \
                patch.object(threads, "threads_text_for_project", return_value="Caption"), \
                patch.object(threads, "upload_file", return_value="https://media.example.com/threads/scheduled.mp4") as upload, \
                patch.object(threads, "schedule_on_vps", return_value=queued) as schedule, \
                patch.object(threads, "record_scheduled_social_upload") as record:
                result = threads.threads_upload_video(
                    {
                        "project": "demo",
                        "brand": "popsy",
                        "scheduledPublishAt": scheduled_at,
                    }
                )

        schedule.assert_called_once_with(
            "threads",
            "https://media.example.com/threads/scheduled.mp4",
            "Caption",
            scheduled_at,
            project="demo",
            brand="popsy",
            account_id="user-2",
            media_sha256=hashlib.sha256(b"fake video").hexdigest(),
            r2_key=f"threads/demo/scheduled-{hashlib.sha256(b'fake video').hexdigest()}.mp4",
        )
        upload.assert_called_once_with(
            video_path,
            f"threads/demo/scheduled-{hashlib.sha256(b'fake video').hexdigest()}.mp4",
            "video/mp4",
            r2_config,
        )
        record.assert_called_once_with(
            project_dir,
            "threads",
            scheduled_at,
            brand="popsy",
            connection_id="connection-2",
            worker_id="vps-job-2",
            media_sha256=hashlib.sha256(b"fake video").hexdigest(),
            r2_key=f"threads/demo/scheduled-{hashlib.sha256(b'fake video').hexdigest()}.mp4",
            r2_url="https://media.example.com/threads/scheduled.mp4",
        )
        self.assertEqual(result["state"], "SCHEDULED")
        self.assertEqual(result["worker_id"], "vps-job-2")
        self.assertEqual(result["r2_url"], "https://media.example.com/threads/scheduled.mp4")


if __name__ == "__main__":
    unittest.main()
