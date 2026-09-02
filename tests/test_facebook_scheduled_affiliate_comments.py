import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from social_upload import affiliate_store
import social_upload.facebook as facebook
import social_upload.scheduler as scheduler


CONFIG = {
    "facebook": {
        "pages": [{"id": "123", "page_access_token": "test-page-token"}],
        "graph_version": "v25.0",
    },
    "brand_routes": {"brand-a": {"facebook": {"page_id": "123"}}},
}


class FacebookScheduledAffiliateCommentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aurex-facebook-affiliate-schedule-")
        self.db_patch = patch.object(affiliate_store, "AFFILIATE_DB_PATH", Path(self.temp_dir.name) / "affiliate.sqlite3")
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _scheduled_job(self, **overrides):
        values = {
            "content_id": "video-1",
            "brand_id": "brand-a",
            "provider": "shopee",
            "platform": "facebook",
            "page_id": "123",
            "post_id": "post-1",
            "placement": "first_comment",
            "auto_comment": True,
            "affiliate_url": "https://s.shopee.vn/affiliate-1",
            "product_name": "Bình giữ nhiệt",
            "status": "scheduled",
        }
        values.update(overrides)
        return affiliate_store.record_publish_job(values)

    def _poll(self, metadata, comment_result=("comment-1", "")):
        with (
            patch.object(facebook, "read_social_config", return_value=CONFIG),
            patch.object(facebook, "facebook_object_metadata", return_value=metadata) as read_metadata,
            patch.object(facebook, "post_facebook_source_comment", return_value=comment_result) as post_comment,
        ):
            result = scheduler.poll_facebook_scheduled_affiliate_comments()
        return result, read_metadata, post_comment

    def test_scheduled_pending_does_not_comment_until_graph_says_published(self):
        job = self._scheduled_job()

        result, metadata, comment = self._poll({"id": "post-1", "status": "SCHEDULED"})

        self.assertEqual(result["pending"], 1)
        metadata.assert_called_once()
        comment.assert_not_called()
        saved = affiliate_store.get_publish_job(job["id"])
        self.assertEqual(saved["status"], "scheduled")
        self.assertEqual(saved["comment_id"], "")

    def test_published_posts_once_and_second_poll_is_idempotent(self):
        job = self._scheduled_job()

        result, _metadata, comment = self._poll({"id": "post-1", "is_published": True})

        self.assertEqual(result["commented"], 1)
        comment.assert_called_once()
        self.assertEqual(comment.call_args.args[1], "123_post-1")
        self.assertIn("Bình giữ nhiệt", comment.call_args.args[2])
        saved = affiliate_store.get_publish_job(job["id"])
        self.assertEqual(saved["status"], "published")
        self.assertEqual(saved["comment_id"], "comment-1")

        result, metadata, comment = self._poll({"id": "post-1", "is_published": True})
        self.assertEqual(result["checked"], 0)
        metadata.assert_not_called()
        comment.assert_not_called()

    def test_auto_comment_false_is_not_polled_or_commented(self):
        job = self._scheduled_job(auto_comment=False)

        result, metadata, comment = self._poll({"id": "post-1", "is_published": True})

        self.assertEqual(result["skipped"], 1)
        metadata.assert_not_called()
        comment.assert_not_called()
        self.assertEqual(affiliate_store.get_publish_job(job["id"])["status"], "scheduled_no_comment")

    def test_transient_comment_error_is_retried_with_backoff(self):
        job = self._scheduled_job()

        result, _metadata, comment = self._poll({"id": "post-1", "status": "PUBLISHED"}, ("", "temporary timeout"))

        self.assertEqual(result["retried"], 1)
        comment.assert_called_once()
        saved = affiliate_store.get_publish_job(job["id"])
        self.assertEqual(saved["status"], "comment_retry")
        self.assertEqual(saved["comment_attempts"], 1)
        self.assertTrue(saved["next_comment_attempt_at"])

    def test_migrates_legacy_publish_jobs_with_auto_comment_disabled(self):
        path = Path(affiliate_store.AFFILIATE_DB_PATH)
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                """
                CREATE TABLE affiliate_publish_jobs (
                    id TEXT PRIMARY KEY, content_id TEXT NOT NULL, brand_id TEXT NOT NULL,
                    provider TEXT NOT NULL, link_id TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL DEFAULT 'facebook',
                    page_id TEXT NOT NULL DEFAULT '', post_id TEXT NOT NULL DEFAULT '', comment_id TEXT NOT NULL DEFAULT '',
                    placement TEXT NOT NULL DEFAULT 'first_comment', status TEXT NOT NULL DEFAULT 'queued',
                    error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO affiliate_publish_jobs VALUES (?, ?, ?, ?, '', 'facebook', '', '', '', 'first_comment', 'scheduled', '', '2026-01-01Z', '2026-01-01Z')",
                ("legacy-1", "video", "brand-a", "shopee"),
            )
            connection.commit()
        finally:
            connection.close()

        affiliate_store.init_db()

        saved = affiliate_store.get_publish_job("legacy-1")
        self.assertFalse(saved["auto_comment"])
        self.assertIn("affiliate_url", saved)


if __name__ == "__main__":
    unittest.main()
