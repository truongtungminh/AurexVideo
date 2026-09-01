import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from social_upload.facebook import post_facebook_source_comment
from social_upload import facebook_backfill as backfill


NOW = datetime.now(timezone.utc)
CONFIG = {
    "facebook": {
        "graph_version": "v25.0",
        "pages": [{"id": "page-1", "page_access_token": "page-token"}],
    },
    "brand_routes": {"knowzy": {"facebook": {"page_id": "page-1"}}},
}
CONTEXT = {
    "settings": {"enabled": True, "mode": "auto", "min_relevance": 0.5, "min_commission": 0.05},
    "addlivetag": {"enabled": True, "affiliate_id_configured": True},
    "connection": {"connected": False},
}


def graph_post(post_id="page-1_1", text="Bình giữ nhiệt tốt", *, days=1, **extra):
    return {
        "id": post_id,
        "message": text,
        "created_time": (NOW - timedelta(days=days)).isoformat(),
        "permalink_url": f"https://www.facebook.com/{post_id}",
        "is_published": True,
        **extra,
    }


class FacebookBackfillTests(unittest.TestCase):
    def test_normalize_posts_dedupes_and_filters_unpublished_and_old(self):
        cutoff = NOW - timedelta(days=30)
        posts = backfill._normalize_posts(
            [
                graph_post("page-1_1", "short"),
                graph_post("page-1_1", "a longer description", description="also here"),
                graph_post("page-1_2", days=40),
                graph_post("page-1_3", is_published=False),
            ],
            page_id="page-1",
            cutoff=cutoff,
        )
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["id"], "page-1_1")
        self.assertEqual(posts[0]["post_id"], "page-1_1")
        self.assertEqual(posts[0]["message_preview"], "a longer description")

    def test_parse_created_time_accepts_graph_offset_without_colon(self):
        parsed = backfill._parse_created_time("2026-09-01T08:30:00+0000")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.utcoffset(), timedelta(0))

    def test_single_attempt_comment_mode_does_not_retry_ambiguous_post(self):
        with (
            patch("social_upload.facebook.http_form_request", side_effect=RuntimeError("network")) as request,
            patch("social_upload.facebook.time.sleep") as sleep,
        ):
            comment_id, error = post_facebook_source_comment(
                {"graph_version": "v25.0"},
                "page-1_1",
                "affiliate comment",
                "page-token",
                attempts=1,
            )

        self.assertFalse(comment_id)
        self.assertEqual(error, "network")
        request.assert_called_once()
        sleep.assert_not_called()

    def test_read_page_posts_follows_graph_owned_paging_for_each_edge(self):
        next_feed = "https://graph.facebook.com/v25.0/page-1/feed?after=feed-2&access_token=page-token"
        next_reels = "https://graph.facebook.com/v25.0/page-1/video_reels?after=reel-2&access_token=page-token"
        calls = []

        def fake_get(url, fields):
            calls.append((url, fields))
            if url.endswith("/feed"):
                return {"data": [graph_post("page-1_1")], "paging": {"next": next_feed}}
            if url == next_feed:
                return {"data": [graph_post("page-1_2")]}
            if url.endswith("/video_reels"):
                return {"data": [graph_post("page-1_3")], "paging": {"next": next_reels}}
            if url == next_reels:
                return {"data": [graph_post("page-1_4")]}
            self.fail(f"unexpected URL {url}")

        with patch.object(backfill, "http_get_request", side_effect=fake_get):
            posts = backfill._read_page_posts(
                CONFIG["facebook"],
                CONFIG["facebook"]["pages"][0],
                limit=4,
                cutoff=NOW - timedelta(days=30),
            )

        self.assertEqual([post["id"] for post in posts], ["page-1_1", "page-1_2", "page-1_3", "page-1_4"])
        self.assertEqual(calls[1], (next_feed, {}))
        self.assertEqual(calls[3], (next_reels, {}))
        self.assertEqual(calls[0][1]["since"], str(int((NOW - timedelta(days=30)).timestamp())))

    def test_page_read_stops_after_old_graph_page_instead_of_exhausting_history(self):
        next_feed = "https://graph.facebook.com/v25.0/page-1/feed?after=old-2&access_token=page-token"
        calls = []

        def fake_get(url, fields):
            calls.append((url, fields))
            if url.endswith("/feed"):
                return {"data": [graph_post("page-1_recent")], "paging": {"next": next_feed}}
            if url == next_feed:
                return {"data": [graph_post("page-1_old", days=40)], "paging": {"next": "https://graph.facebook.com/v25.0/page-1/feed?after=old-3"}}
            if url.endswith("/video_reels"):
                return {"data": []}
            self.fail(f"paging should stop after old page: {url}")

        with patch.object(backfill, "http_get_request", side_effect=fake_get):
            posts = backfill._read_page_posts(
                CONFIG["facebook"],
                CONFIG["facebook"]["pages"][0],
                limit=4,
                cutoff=NOW - timedelta(days=30),
            )

        self.assertEqual([post["id"] for post in posts], ["page-1_recent"])
        self.assertEqual([url for url, _ in calls[:2]], ["https://graph.facebook.com/v25.0/page-1/feed", next_feed])
        self.assertEqual(len(calls), 3)  # the bounded scan still checks the Reels edge once

    def test_existing_comment_detects_marker_or_shopee_url(self):
        self.assertTrue(backfill._has_existing_affiliate_comment([{"message": "🛒 Sản phẩm liên quan trong video:\nhttps://s.shopee.vn/abc"}]))
        self.assertTrue(backfill._has_existing_affiliate_comment([{"message": "xem https://shopee.vn/product/1/2"}]))
        self.assertTrue(backfill._has_existing_affiliate_comment([{"message": "xem https://shopee.ee/product/1/2"}]))
        self.assertFalse(backfill._has_existing_affiliate_comment([{"message": "Một bình luận bình thường"}]))

    def test_cached_selection_matches_product_name_inside_long_caption(self):
        cached = [
            {"id": "p-1", "name": "Máy hút bụi cầm tay", "origin_url": "https://shopee.vn/product/1/2", "commission_rate": 0.2},
            {"id": "p-2", "name": "Áo mưa đi đường", "origin_url": "https://shopee.vn/product/1/3", "commission_rate": 0.8},
        ]
        with patch.object(backfill.affiliate_store, "list_products", return_value=cached) as list_products:
            product, reason = backfill._select_product(
                "knowzy",
                "Review nhanh cho anh em: máy hút bụi cầm tay nhỏ gọn, lực hút mạnh, pin lâu, phù hợp căn hộ và văn phòng. Xem thêm mẹo dọn nhà cuối tuần.",
                CONTEXT,
            )

        self.assertFalse(reason)
        self.assertEqual(product["id"], "p-1")
        self.assertEqual(product["link_provider"], "addlivetag")
        self.assertGreaterEqual(product["relevance_score"], CONTEXT["settings"]["min_relevance"])
        list_products.assert_called_once_with(limit=backfill.MAX_LIMIT)

    def test_connected_discovery_is_read_only_for_backfill_preview(self):
        context = {**CONTEXT, "addlivetag": {"enabled": False}, "connection": {"connected": True}}
        discovered = {"products": [{"id": "p-1", "name": "Bình giữ nhiệt", "origin_url": "https://shopee.vn/product/1/2", "commission_rate": 0.2}]}
        with (
            patch.object(backfill, "discover_products", return_value=discovered) as discover,
            patch.object(backfill.affiliate_store, "upsert_product") as upsert,
        ):
            product, reason = backfill._select_product("knowzy", "Bình giữ nhiệt cho dân văn phòng", context)

        self.assertFalse(reason)
        self.assertEqual(product["id"], "p-1")
        discover.assert_called_once_with("knowzy", "bình giữ nhiệt dân văn phòng", limit=10, persist=False)
        upsert.assert_not_called()

    def test_policy_uses_relevance_before_commission(self):
        selected = backfill._policy_product(
            [
                {"id": "relevant", "name": "Bình giữ nhiệt", "origin_url": "https://shopee.vn/relevant", "relevance_score": 0.95, "commission_rate": 0.05},
                {"id": "commission-heavy", "name": "Bình giữ nhiệt", "origin_url": "https://shopee.vn/heavy", "relevance_score": 0.80, "commission_rate": 0.80},
            ],
            "Bình giữ nhiệt",
            {"min_relevance": 0.5, "min_commission": 0.05},
        )

        self.assertEqual(selected["id"], "relevant")

    def test_selection_is_not_eligible_without_a_link_provider(self):
        context = {
            **CONTEXT,
            "addlivetag": {"enabled": True, "affiliate_id_configured": False},
            "connection": {"connected": False},
        }
        cached = [{"id": "p-1", "name": "Bình giữ nhiệt", "origin_url": "https://shopee.vn/product/1/2", "commission_rate": 0.2}]
        with patch.object(backfill.affiliate_store, "list_products", return_value=cached):
            product, reason = backfill._select_product("knowzy", "Bình giữ nhiệt cho dân văn phòng", context)

        self.assertFalse(product)
        self.assertIn("Affiliate ID", reason)

    def test_dry_run_never_creates_link_or_comment(self):
        def fake_get(url, fields):
            if url.endswith("/feed"):
                return {"data": [graph_post()]}
            if url.endswith("/video_reels"):
                return {"data": [graph_post()]}
            if url.endswith("/comments"):
                return {"data": []}
            self.fail(f"unexpected URL {url}")

        cached = [{"id": "p-1", "name": "Bình giữ nhiệt tốt", "origin_url": "https://shopee.vn/product/1/2", "commission_rate": 0.2}]
        with (
            patch.object(backfill, "read_social_config", return_value=CONFIG),
            patch.object(backfill, "brand_context", return_value=CONTEXT),
            patch.object(backfill, "http_get_request", side_effect=fake_get),
            patch.object(backfill.affiliate_store, "overview", return_value={"products": []}),
            patch.object(backfill.affiliate_store, "list_products", return_value=cached),
            patch.object(backfill, "create_affiliate_link") as create_link,
            patch.object(backfill, "post_facebook_source_comment") as post_comment,
            patch.object(backfill.affiliate_store, "record_content_product") as record,
        ):
            result = backfill.run_affiliate_backfill("knowzy", dry_run=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["eligible"], 1)
        self.assertEqual(result["commented"], 0)
        self.assertEqual(result["items"][0]["status"], "eligible")
        self.assertEqual(result["items"][0]["post_id"], "page-1_1")
        self.assertEqual(result["items"][0]["permalink_url"], "https://www.facebook.com/page-1_1")
        self.assertEqual(result["items"][0]["post"]["post_id"], "page-1_1")
        create_link.assert_not_called()
        post_comment.assert_not_called()
        record.assert_not_called()

    def test_comment_inspection_failure_is_safe_and_renderable_in_dry_run(self):
        def fake_get(url, fields):
            if url.endswith("/feed"):
                return {"data": [graph_post()]}
            if url.endswith("/video_reels"):
                return {"data": []}
            if url.endswith("/comments"):
                raise RuntimeError("HTTP 400: access_token=very-secret-token")
            self.fail(f"unexpected URL {url}")

        with (
            patch.object(backfill, "read_social_config", return_value=CONFIG),
            patch.object(backfill, "brand_context", return_value=CONTEXT),
            patch.object(backfill, "http_get_request", side_effect=fake_get),
            patch.object(backfill.affiliate_store, "overview", return_value={"products": []}),
            patch.object(backfill, "create_affiliate_link") as create_link,
            patch.object(backfill, "post_facebook_source_comment") as post_comment,
        ):
            result = backfill.run_affiliate_backfill("knowzy", dry_run=True)

        item = result["items"][0]
        self.assertTrue(result["ok"])
        self.assertEqual(item["status"], "skipped")
        self.assertEqual(item["post_id"], "page-1_1")
        self.assertIn("Comments cannot be inspected", item["reason"])
        self.assertNotIn("very-secret-token", item["reason"])
        self.assertNotIn("json-secret", backfill._safe_error('{"access_token": "json-secret"}'))
        create_link.assert_not_called()
        post_comment.assert_not_called()

    def test_addlivetag_explicit_reference_is_selected_without_discovery(self):
        context = {**CONTEXT, "addlivetag": {"enabled": True, "affiliate_id_configured": True}}
        raw = {
            "status": "success",
            "productInfo": {
                "itemId": "2", "shopId": "1", "productName": "Bình giữ nhiệt", "originLink": "https://shopee.vn/product/1/2",
                "totalRatePercent": 20,
            },
        }
        with (
            patch.object(backfill, "fetch_product_data", return_value=raw) as fetch,
            patch.object(backfill, "discover_products") as discover,
        ):
            product, reason = backfill._select_product("knowzy", "Xem https://shopee.vn/product/1/2", context)

        self.assertFalse(reason)
        self.assertEqual(product["link_provider"], "addlivetag")
        self.assertEqual(product["provider_product_id"], "2")
        fetch.assert_called_once()
        discover.assert_not_called()

    def test_execute_skips_successful_local_comment_record(self):
        def fake_get(url, fields):
            if url.endswith("/feed"):
                return {"data": [graph_post()]}
            if url.endswith("/video_reels"):
                return {"data": []}
            self.fail(f"comments should not be read after local idempotency: {url}")

        existing = {
            "id": "record-1", "page_id": "page-1", "facebook_post_id": "page-1_1",
            "facebook_comment_id": "comment-1", "status": "commented",
        }
        with (
            patch.object(backfill, "read_social_config", return_value=CONFIG),
            patch.object(backfill, "brand_context", return_value=CONTEXT),
            patch.object(backfill, "http_get_request", side_effect=fake_get),
            patch.object(backfill.affiliate_store, "overview", return_value={"products": [existing]}),
            patch.object(backfill, "post_facebook_source_comment") as post_comment,
        ):
            result = backfill.run_affiliate_backfill("knowzy", dry_run=False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["items"][0]["status"], "skipped")
        self.assertIn("local affiliate", result["items"][0]["reason"])
        post_comment.assert_not_called()

    def test_execute_records_commented_status_after_mocked_provider_writes(self):
        def fake_get(url, fields):
            if url.endswith("/feed"):
                return {"data": [graph_post()]}
            if url.endswith("/video_reels"):
                return {"data": []}
            if url.endswith("/comments"):
                return {"data": []}
            self.fail(f"unexpected URL {url}")

        cached = [{"id": "p-1", "name": "Bình giữ nhiệt tốt", "origin_url": "https://shopee.vn/product/1/2", "offer_url": "https://shopee.vn/brand-a-affiliate", "commission_rate": 0.2}]
        with (
            patch.object(backfill, "read_social_config", return_value=CONFIG),
            patch.object(backfill, "brand_context", return_value=CONTEXT),
            patch.object(backfill, "http_get_request", side_effect=fake_get),
            patch.object(backfill.affiliate_store, "overview", return_value={"products": []}),
            patch.object(backfill.affiliate_store, "list_products", return_value=cached),
            patch.object(backfill.affiliate_store, "upsert_product", side_effect=lambda product: {**product, "id": "p-1"}),
            patch.object(backfill.affiliate_store, "record_content_product", return_value={"id": "record-1"}),
            patch.object(backfill.affiliate_store, "update_content_product") as update_record,
            patch.object(backfill, "create_affiliate_link", return_value={"link": {"affiliate_url": "https://s.shopee.vn/abc"}}) as create_link,
            patch.object(backfill, "post_facebook_source_comment", return_value=("comment-1", "")) as post_comment,
        ):
            result = backfill.run_affiliate_backfill("knowzy", dry_run=False)

        self.assertTrue(result["ok"])
        self.assertEqual(result["commented"], 1)
        self.assertEqual(result["items"][0]["status"], "commented")
        self.assertEqual(post_comment.call_args.args[2], "🛒 Sản phẩm liên quan trong video:\nhttps://s.shopee.vn/abc")
        self.assertEqual(post_comment.call_args.kwargs["attempts"], 1)
        self.assertFalse(create_link.call_args.kwargs["reuse_product_offer_url"])
        self.assertTrue(any(call.kwargs.get("affiliate_url") == "https://s.shopee.vn/abc" for call in update_record.call_args_list))
        self.assertTrue(any(call.kwargs.get("status") == "commented" for call in update_record.call_args_list))


if __name__ == "__main__":
    unittest.main()
