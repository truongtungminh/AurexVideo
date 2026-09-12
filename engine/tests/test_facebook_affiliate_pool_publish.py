import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from social_upload import affiliate_store
from social_upload.affiliate import save_product_pool
import social_upload.facebook as facebook
import social_upload.scheduler as scheduler


CONFIG = {
    "facebook": {"pages": [{"id": "123", "page_access_token": "test-token"}], "graph_version": "v25.0"},
    "brand_routes": {"brand-a": {"facebook": {"page_id": "123"}}},
}
ORIGIN = "https://shopee.vn/product/775125376/18824975414"
POOL_LINK = "https://s.shopee.vn/caption-selected"


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({"success": True}).encode()


class FacebookAffiliatePoolPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aurex-facebook-pool-publish-")
        self.db_patch = patch.object(affiliate_store, "AFFILIATE_DB_PATH", Path(self.temp_dir.name) / "affiliate.sqlite3")
        self.db_patch.start()
        self.pool_row = save_product_pool("brand-a", {"name": "Máy hút bụi cầm tay", "originUrl": ORIGIN, "affiliateUrl": POOL_LINK, "commissionRate": 10})
        affiliate_store.upsert_settings("brand-a", {"enabled": True, "mode": "auto", "min_relevance": 0.5})

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _upload(self, *, scheduled_at=""):
        form_calls = []

        def fake_form(_url, fields):
            form_calls.append(dict(fields))
            return {"video_id": "vid-1"} if fields.get("upload_phase") == "start" else {"post_id": "post-1"}

        with (
            patch.object(facebook, "read_social_config", return_value=CONFIG),
            patch("social_upload.affiliate.read_social_config", return_value=CONFIG),
            patch.object(facebook, "final_video_path_for_project", return_value=Path("/tmp/video.mp4")),
            patch.object(facebook, "project_brand_from_topic", return_value="brand-a"),
            patch.object(facebook, "build_upload_metadata", return_value={"facebookCaption": "metadata", "facebookVideoState": "PUBLISHED"}),
            patch.object(facebook, "facebook_caption_for_project", return_value=("Máy hút bụi cầm tay đang giảm giá", "")),
            patch.object(facebook, "read_expected_video_bytes", return_value=b"video"),
            patch.object(facebook, "record_social_upload"),
            patch.object(facebook, "http_form_request", side_effect=fake_form),
            patch.object(facebook, "urlopen", return_value=FakeResponse()),
            patch.object(facebook, "post_facebook_source_comment", return_value=("comment-1", "")) as comment,
        ):
            result = facebook.facebook_upload_video({
                "project": "demo",
                "affiliate": {"enabled": True, "mode": "auto", "placement": "first_comment", "autoComment": True},
                **({"scheduledPublishAt": scheduled_at} if scheduled_at else {}),
            })
        return result, form_calls, comment

    def test_immediate_publish_comments_with_caption_selected_pool_link(self):
        result, _calls, comment = self._upload()

        self.assertEqual(result["affiliate"]["product"]["id"], self.pool_row["id"])
        comment.assert_called_once()
        self.assertEqual(comment.call_args.args[1], "123_post-1")
        self.assertIn(POOL_LINK, comment.call_args.args[2])

    def test_scheduled_publish_persists_caption_selected_link_and_comments_it_later(self):
        scheduled_at = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        result, _calls, comment = self._upload(scheduled_at=scheduled_at)

        comment.assert_not_called()
        job = affiliate_store.get_publish_job(result["affiliate"]["job_id"])
        self.assertEqual(job["status"], "scheduled")
        self.assertEqual(job["affiliate_url"], POOL_LINK)
        affiliate_store.update_publish_job(job["id"], scheduled_at="2026-01-01T00:00:00Z")
        with (
            patch.object(facebook, "read_social_config", return_value=CONFIG),
            patch.object(facebook, "facebook_object_metadata", return_value={"id": "post-1", "is_published": True}),
            patch.object(facebook, "post_facebook_source_comment", return_value=("comment-later", "")) as deferred_comment,
        ):
            poll_result = scheduler.poll_facebook_scheduled_affiliate_comments()

        self.assertEqual(poll_result["commented"], 1)
        self.assertIn(POOL_LINK, deferred_comment.call_args.args[2])
        self.assertEqual(affiliate_store.get_publish_job(job["id"])["comment_id"], "comment-later")


if __name__ == "__main__":
    unittest.main()
