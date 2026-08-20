from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_upload.meta_bundle as bundle


class MetaBundleTests(unittest.TestCase):
    def test_publish_runs_all_three_and_forwards_captions(self) -> None:
        with patch.object(bundle, "instagram_upload_video", return_value={"url": "https://instagram.example/reel"}) as instagram, \
            patch.object(bundle, "facebook_upload_video", return_value={"source_comment_target_id": "page_post"}) as facebook, \
            patch.object(bundle, "threads_upload_video", return_value={"url": "https://threads.example/post"}) as threads, \
            patch.object(bundle, "facebook_comment_source") as comment:
            result = bundle.publish_instagram_facebook_threads({
                "project": "winter-vs-sullyoon",
                "instagramCaption": "IG caption",
                "facebookCaption": "FB caption",
                "threadsText": "Threads text",
                "facebookSourceComment": "Source: https://example.com",
            })

        self.assertTrue(result["ok"])
        self.assertFalse(result["partial"])
        self.assertEqual(result["successful"], ["instagram", "facebook", "threads"])
        instagram.assert_called_once_with({"project": "winter-vs-sullyoon", "instagramCaption": "IG caption"})
        facebook.assert_called_once_with({
            "project": "winter-vs-sullyoon",
            "facebookCaption": "FB caption",
            "facebookVideoState": "PUBLISHED",
        })
        threads.assert_called_once_with({"project": "winter-vs-sullyoon", "threadsText": "Threads text"})
        comment.assert_called_once_with({
            "project": "winter-vs-sullyoon",
            "sourceCommentTargetId": "page_post",
            "facebookSourceComment": "Source: https://example.com",
        })

    def test_publish_keeps_other_platforms_when_one_fails(self) -> None:
        with patch.object(bundle, "instagram_upload_video", side_effect=RuntimeError("Instagram failed")), \
            patch.object(bundle, "facebook_upload_video", return_value={}), \
            patch.object(bundle, "threads_upload_video", return_value={}):
            result = bundle.publish_instagram_facebook_threads({"project": "demo"})

        self.assertFalse(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["successful"], ["facebook", "threads"])
        self.assertEqual(result["failed"], ["instagram"])
        self.assertEqual(result["platforms"]["instagram"]["error"], "Instagram failed")

    def test_publish_forwards_brand_to_brand_scoped_platforms(self) -> None:
        with patch.object(bundle, "instagram_upload_video", return_value={"url": "https://instagram.example/post"}) as instagram, \
             patch.object(bundle, "facebook_upload_video", return_value={"source_comment_target_id": "fb_1"}) as facebook, \
             patch.object(bundle, "threads_upload_video", return_value={"url": "https://threads.example/post"}) as threads:
            result = bundle.publish_instagram_facebook_threads({
                "project": "demo",
                "brand": "popsy",
                "instagramCaption": "Instagram",
                "threadsText": "Threads",
            })

        self.assertTrue(result["ok"])
        self.assertEqual(instagram.call_args.args[0]["brand"], "popsy")
        self.assertEqual(facebook.call_args.args[0]["brand"], "popsy")
        self.assertEqual(threads.call_args.args[0]["brand"], "popsy")

    def test_publish_rejects_facebook_draft_state(self) -> None:
        with self.assertRaisesRegex(ValueError, "PUBLISHED"):
            bundle.publish_instagram_facebook_threads({
                "project": "demo",
                "facebookVideoState": "DRAFT",
            })

    def test_comment_failure_is_reported_without_hiding_platform_success(self) -> None:
        with patch.object(bundle, "instagram_upload_video", return_value={}), \
            patch.object(bundle, "facebook_upload_video", return_value={"source_comment_target_id": "page_post"}), \
            patch.object(bundle, "threads_upload_video", return_value={}), \
            patch.object(bundle, "facebook_comment_source", side_effect=RuntimeError("comment failed")):
            result = bundle.publish_instagram_facebook_threads({
                "project": "demo",
                "facebookSourceComment": "Source: https://example.com",
            })

        self.assertFalse(result["ok"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["successful"], ["instagram", "facebook", "threads"])
        self.assertEqual(result["failed"], ["facebook_comment"])
        self.assertIn("Facebook comment", result["message"])


if __name__ == "__main__":
    unittest.main()
