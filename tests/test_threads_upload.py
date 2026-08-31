from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_upload.threads as threads


class ThreadsUploadTests(unittest.TestCase):
    def test_scheduled_threads_uses_vps_context_and_persists_worker_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            video_path = project_dir / "final_video.mp4"
            video_path.write_bytes(b"fake video")
            scheduled_at = "2099-01-01T00:00:00Z"
            queued = {
                "id": "vps-job-2",
                "scheduledPublishAt": scheduled_at,
                "worker_id": "vps-job-2",
            }
            with patch.object(
                threads,
                "read_social_config",
                return_value={"social_worker": {"url": "https://worker.example"}},
            ), patch.object(threads, "require_project", return_value=project_dir), \
                patch.object(
                    threads,
                    "resolve_social_brand_connection",
                    return_value=("connection-2", {"threads_user_id": "user-2", "access_token": "x" * 30}),
                ), patch.object(threads, "final_video_path_for_project", return_value=video_path), \
                patch.object(threads, "validate_upload_video", return_value={}), \
                patch.object(threads, "threads_text_for_project", return_value="Caption"), \
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
            video_path,
            "Caption",
            scheduled_at,
            project="demo",
            brand="popsy",
            account_id="connection-2",
        )
        record.assert_called_once_with(
            project_dir,
            "threads",
            scheduled_at,
            brand="popsy",
            connection_id="connection-2",
            worker_id="vps-job-2",
        )
        self.assertEqual(result["state"], "SCHEDULED")
        self.assertEqual(result["worker_id"], "vps-job-2")


if __name__ == "__main__":
    unittest.main()
