from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
