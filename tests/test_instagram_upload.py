from __future__ import annotations

import hashlib
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_upload.instagram as instagram
import social_upload.r2 as r2


class InstagramUploadTests(unittest.TestCase):
    def test_instagram_api_host_follows_login_mode(self) -> None:
        self.assertEqual(
            instagram.instagram_api_url(
                {"api_mode": "instagram_login", "graph_version": "v25.0"},
                "17840000000000000/media",
            ),
            "https://graph.instagram.com/v25.0/17840000000000000/media",
        )
        self.assertEqual(
            instagram.instagram_api_url(
                {"api_mode": "facebook_login", "graph_version": "25.0"},
                "17840000000000000/media_publish",
            ),
            "https://graph.facebook.com/v25.0/17840000000000000/media_publish",
        )

    def test_r2_public_url_quotes_object_key(self) -> None:
        config = {
            "account_id": "account",
            "bucket": "media",
            "access_key_id": "key",
            "secret_access_key": "secret",
            "public_base_url": "https://media.example.com/",
        }
        self.assertEqual(
            r2.r2_public_url("instagram/a project/video #1.mp4", config),
            "https://media.example.com/instagram/a%20project/video%20%231.mp4",
        )

    def test_instagram_object_key_omits_date_directory(self) -> None:
        with patch.object(instagram.secrets, "token_hex", return_value="deadbeef"):
            self.assertEqual(
                instagram.instagram_object_key("winter vs sullyoon", {"object_prefix": "instagram"}),
                "instagram/winter-vs-sullyoon/deadbeef.mp4",
            )

    def test_instagram_upload_runs_r2_container_poll_publish_and_cleanup(self) -> None:
        r2_config = {
            "account_id": "account",
            "bucket": "media",
            "access_key_id": "key",
            "secret_access_key": "secret",
            "public_base_url": "https://media.example.com",
            "object_prefix": "instagram",
            "retain_media": False,
        }
        instagram_config = {
            "ig_user_id": "17840000000000000",
            "access_token": "token-" + "x" * 30,
            "api_mode": "instagram_login",
            "graph_version": "v25.0",
            "poll_attempts": 1,
            "poll_interval_seconds": 1,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "final_video.mp4"
            video_path.write_bytes(b"fake video")
            (Path(temp_dir) / "topic.json").write_text('{"brand": "popsy"}', encoding="utf-8")
            form_calls = []

            def fake_form(url: str, fields: dict) -> dict:
                form_calls.append((url, fields))
                return {"id": "container-1"} if url.endswith("/media") else {"id": "media-1"}

            with patch.object(instagram, "read_social_config", return_value={"instagram": instagram_config, "r2": r2_config}), \
                patch.object(instagram, "require_project", return_value=Path(temp_dir)), \
                patch.object(instagram, "final_video_path_for_project", return_value=video_path), \
                patch.object(instagram, "validate_upload_video", return_value={}), \
                patch.object(instagram, "http_form_request", side_effect=fake_form), \
                patch.object(instagram, "upload_file", return_value="https://media.example.com/instagram/video.mp4") as upload, \
                patch.object(instagram, "wait_for_instagram_container", return_value={"status_code": "FINISHED"}) as wait, \
                patch.object(instagram, "instagram_media_metadata", return_value={"id": "media-1", "permalink": "https://instagram.com/reel/abc"}), \
                patch.object(instagram, "delete_file") as delete, \
                patch.object(instagram, "record_social_upload") as record:
                result = instagram.instagram_upload_video({
                    "project": "demo",
                    "brand": "popsy",
                    "instagramCaption": "Caption test",
                })

        self.assertEqual(result["media_id"], "media-1")
        self.assertEqual(result["url"], "https://instagram.com/reel/abc")
        self.assertEqual(len(form_calls), 2)
        self.assertEqual(form_calls[0][1]["media_type"], "REELS")
        self.assertEqual(form_calls[0][1]["video_url"], "https://media.example.com/instagram/video.mp4")
        upload.assert_called_once()
        wait.assert_called_once()
        delete.assert_called_once()
        record.assert_called_once_with(
            "demo",
            "instagram",
            {
                "url": "https://instagram.com/reel/abc",
                "video_id": "container-1",
                "post_id": "media-1",
                "state": "PUBLISHED",
                "r2_key": upload.call_args.args[1],
                "r2_url": "https://media.example.com/instagram/video.mp4",
                "brand": "popsy",
                "connection_id": "popsy-legacy",
            },
        )

    def test_scheduled_instagram_uploads_to_r2_before_vps_schedule(self) -> None:
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
                "object_prefix": "instagram",
            }
            instagram_config = {
                "ig_user_id": "1784",
                "access_token": "x" * 30,
                "_brand_connection": True,
            }
            queued = {
                "id": "vps-job-1",
                "scheduledPublishAt": scheduled_at,
                "worker_id": "vps-job-1",
            }
            with patch.object(
                instagram,
                "read_social_config",
                return_value={"instagram": instagram_config, "r2": r2_config},
            ), patch.object(instagram, "require_project", return_value=project_dir), \
                patch.object(
                    instagram,
                    "resolve_social_brand_connection",
                    return_value=("connection-1", instagram_config),
                ), patch.object(instagram, "final_video_path_for_project", return_value=video_path), \
                patch.object(instagram, "validate_upload_video", return_value={}), \
                patch.object(instagram, "instagram_caption_for_project", return_value="Caption"), \
                patch.object(instagram, "upload_file", return_value="https://media.example.com/instagram/scheduled.mp4") as upload, \
                patch.object(instagram, "schedule_on_vps", return_value=queued) as schedule, \
                patch.object(instagram, "record_scheduled_social_upload") as record:
                result = instagram.instagram_upload_video(
                    {
                        "project": "demo",
                        "brand": "popsy",
                        "scheduledPublishAt": scheduled_at,
                    }
                )

        schedule.assert_called_once_with(
            "instagram",
            "https://media.example.com/instagram/scheduled.mp4",
            "Caption",
            scheduled_at,
            project="demo",
            brand="popsy",
            account_id="1784",
            media_sha256=hashlib.sha256(b"fake video").hexdigest(),
            r2_key=f"instagram/demo/scheduled-{hashlib.sha256(b'fake video').hexdigest()}.mp4",
        )
        upload.assert_called_once_with(
            video_path,
            f"instagram/demo/scheduled-{hashlib.sha256(b'fake video').hexdigest()}.mp4",
            "video/mp4",
            r2_config,
        )
        record.assert_called_once_with(
            project_dir,
            "instagram",
            scheduled_at,
            brand="popsy",
            connection_id="connection-1",
            worker_id="vps-job-1",
            media_sha256=hashlib.sha256(b"fake video").hexdigest(),
            r2_key=f"instagram/demo/scheduled-{hashlib.sha256(b'fake video').hexdigest()}.mp4",
            r2_url="https://media.example.com/instagram/scheduled.mp4",
        )
        self.assertEqual(result["state"], "SCHEDULED")
        self.assertEqual(result["worker_id"], "vps-job-1")
        self.assertEqual(result["r2_url"], "https://media.example.com/instagram/scheduled.mp4")


if __name__ == "__main__":
    unittest.main()
