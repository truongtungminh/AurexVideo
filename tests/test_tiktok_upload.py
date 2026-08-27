from __future__ import annotations

import json
import io
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_upload.tiktok as tt


class Response:
    def __init__(self, payload: dict):
        self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.payload).encode()


class TiktokUploadTests(unittest.TestCase):
    def test_config_status_does_not_expose_key(self):
        result = tt.zernio_status({"api_key": "sk_test_123456789", "account_id": "acct_1"})
        self.assertTrue(result["connected"])
        self.assertNotIn("api_key", result)
        self.assertEqual(result["masked_api_key"], "sk_t...6789")

    def test_upload_presigns_uploads_and_creates_post(self):
        calls = []
        config = {"zernio": {"api_key": "sk_test", "account_id": "acct_1"}}
        def fake_urlopen(request, timeout=0):
            calls.append(request)
            if request.full_url.endswith("/media/presign"):
                return Response({"data": {"uploadUrl": "https://upload.example/put", "publicUrl": "https://cdn.example/video.mp4"}})
            if request.full_url.endswith("/posts"):
                return Response({"data": {"post": {"_id": "post_1", "platformPostUrl": {"tiktok": "https://tiktok.com/@a/video/1"}}}})
            return Response({})
        path = Path("/tmp/tiktok-test.mp4")
        path.write_bytes(b"video")
        try:
            with patch.object(tt, "read_social_config", return_value=config), \
                 patch.object(tt, "require_project", return_value=Path("/tmp")), \
                 patch("social_upload.metadata.project_brand_from_topic", return_value="popsy"), \
                 patch.object(tt, "final_video_path_for_project", return_value=path), \
                 patch.object(tt, "read_expected_video_bytes", return_value=b"video"), \
                 patch.object(tt, "build_upload_metadata", return_value={"instagramCaption": "Caption"}), \
                 patch.object(tt, "record_social_upload") as record, \
                 patch.object(tt, "urlopen", side_effect=fake_urlopen):
                result = tt.tiktok_upload_video({"project": "demo", "brand": "popsy", "tiktokCaption": "Hello"})
            self.assertEqual(result["post_id"], "post_1")
            self.assertEqual(result["url"], "https://tiktok.com/@a/video/1")
            self.assertEqual(result["state"], "PUBLISHED")
            self.assertEqual(result["delivery"], "DIRECT_POST")
            self.assertEqual(calls[0].method, "POST")
            presign_body = json.loads(calls[0].data.decode())
            self.assertEqual(presign_body["filename"], "tiktok-test.mp4")
            self.assertEqual(presign_body["contentType"], "video/mp4")
            self.assertEqual(presign_body["size"], 5)
            self.assertEqual(calls[1].method, "PUT")
            post_body = json.loads(calls[2].data.decode())
            self.assertTrue(post_body["publishNow"])
            self.assertEqual(post_body["platforms"][0]["accountId"], "acct_1")
            record.assert_called_once()
        finally:
            path.unlink(missing_ok=True)

    def test_upload_retries_tiktok_capacity_as_creator_inbox_draft_for_http_and_json_errors(self):
        for error_mode in ("http", "json"):
            with self.subTest(error_mode=error_mode):
                calls = []
                post_attempts = 0
                capacity_message = "TikTok direct posting is at capacity right now. Use tiktokSettings.draft: true to deliver via Creator Inbox, or try again in a few hours as capacity frees up."
                capacity_error = {"error": {"code": "TIKTOK_DIRECT_POST_CAPACITY_EXCEEDED", "message": "TikTok direct post capacity reached."}}

                def fake_urlopen(request, timeout=0):
                    nonlocal post_attempts
                    calls.append(request)
                    if request.full_url.endswith("/media/presign"):
                        return Response({"data": {"uploadUrl": "https://upload.example/put", "publicUrl": "https://cdn.example/video.mp4"}})
                    if request.full_url.endswith("/posts"):
                        post_attempts += 1
                        if post_attempts == 1:
                            if error_mode == "http":
                                raise HTTPError(request.full_url, 429, "Too Many Requests", hdrs=None, fp=io.BytesIO(capacity_message.encode()))
                            return Response(capacity_error)
                        return Response({"data": {"post": {"_id": "draft_1"}}})
                    return Response({})

                path = Path("/tmp/tiktok-capacity-test.mp4")
                path.write_bytes(b"video")
                try:
                    with patch.object(tt, "read_social_config", return_value={"zernio": {"api_key": "sk_test", "account_id": "acct_1"}}), \
                         patch.object(tt, "require_project", return_value=Path("/tmp")), \
                         patch("social_upload.metadata.project_brand_from_topic", return_value="popsy"), \
                         patch.object(tt, "final_video_path_for_project", return_value=path), \
                         patch.object(tt, "read_expected_video_bytes", return_value=b"video"), \
                         patch.object(tt, "record_social_upload") as record, \
                         patch.object(tt, "urlopen", side_effect=fake_urlopen):
                        result = tt.tiktok_upload_video({"project": "demo", "brand": "popsy", "tiktokCaption": "Hello", "scheduleTimezone": "UTC"})

                    post_bodies = [json.loads(call.data.decode()) for call in calls if call.full_url.endswith("/posts")]
                    self.assertEqual(len(post_bodies), 2)
                    self.assertEqual(len([call for call in calls if call.full_url.endswith("/media/presign")]), 1)
                    self.assertEqual(len([call for call in calls if call.full_url.endswith("/put")]), 1)
                    self.assertNotIn("tiktokSettings", post_bodies[0])
                    self.assertEqual(post_bodies[1]["tiktokSettings"], {"draft": True})
                    self.assertEqual(post_bodies[1]["content"], post_bodies[0]["content"])
                    self.assertEqual(post_bodies[1]["mediaItems"], post_bodies[0]["mediaItems"])
                    self.assertEqual(post_bodies[1]["platforms"][0]["accountId"], "acct_1")
                    self.assertNotIn("publishNow", post_bodies[1])
                    self.assertEqual(result["state"], "DRAFT")
                    self.assertEqual(result["delivery"], "CREATOR_INBOX")
                    self.assertIn("Creator Inbox", result["message"])
                    self.assertEqual(record.call_args.args[2]["state"], "DRAFT")
                finally:
                    path.unlink(missing_ok=True)

    def test_upload_does_not_retry_non_capacity_post_errors(self):
        calls = []
        error_response = {"error": {"code": "TIKTOK_ACCOUNT_NOT_FOUND", "message": "TikTok account is not connected."}}

        def fake_urlopen(request, timeout=0):
            calls.append(request)
            if request.full_url.endswith("/media/presign"):
                return Response({"data": {"uploadUrl": "https://upload.example/put", "publicUrl": "https://cdn.example/video.mp4"}})
            if request.full_url.endswith("/posts"):
                return Response(error_response)
            return Response({})

        path = Path("/tmp/tiktok-non-capacity-test.mp4")
        path.write_bytes(b"video")
        try:
            with patch.object(tt, "read_social_config", return_value={"zernio": {"api_key": "sk_test", "account_id": "acct_1"}}), \
                 patch.object(tt, "require_project", return_value=Path("/tmp")), \
                 patch("social_upload.metadata.project_brand_from_topic", return_value="popsy"), \
                 patch.object(tt, "final_video_path_for_project", return_value=path), \
                 patch.object(tt, "read_expected_video_bytes", return_value=b"video"), \
                 patch.object(tt, "record_social_upload") as record, \
                 patch.object(tt, "urlopen", side_effect=fake_urlopen):
                with self.assertRaisesRegex(tt.ZernioRequestError, "TIKTOK_ACCOUNT_NOT_FOUND"):
                    tt.tiktok_upload_video({"project": "demo", "brand": "popsy", "tiktokCaption": "Hello"})
            self.assertEqual(len([call for call in calls if call.full_url.endswith("/posts")]), 1)
            record.assert_not_called()
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
