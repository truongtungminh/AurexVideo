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

    def test_page_read_uses_nested_attachment_description_field(self):
        calls = []

        def fake_get(url, fields):
            calls.append((url, fields))
            if url.endswith("/feed"):
                return {"data": [graph_post(attachments={"data": [{"description": "Máy hút bụi cầm tay"}]})]}
            if url.endswith("/video_reels"):
                return {"data": []}
            self.fail(f"unexpected URL {url}")

        with patch.object(backfill, "http_get_request", side_effect=fake_get):
            posts = backfill._read_page_posts(
                CONFIG["facebook"],
                CONFIG["facebook"]["pages"][0],
                limit=4,
                cutoff=NOW - timedelta(days=30),
            )

        self.assertEqual(posts[0]["description_preview"], "Máy hút bụi cầm tay")
        fields = calls[0][1]["fields"]
        self.assertIn("attachments{description}", fields)
        self.assertNotIn(",description,", fields)

    def test_page_read_falls_back_to_scalar_fields_for_meta_deprecation(self):
        calls = []

        def fake_get(url, fields):
            calls.append((url, fields))
            if url.endswith("/feed") and "attachments{description}" in fields.get("fields", ""):
                return {"error": {"message": "(#12) deprecate_post_aggregated_fields_for_attachement is deprecated for versions v3.3 and higher"}}
            if url.endswith("/feed"):
                return {"data": [graph_post()]}
            if url.endswith("/video_reels"):
                return {"data": []}
            self.fail(f"unexpected URL {url}")

        with patch.object(backfill, "http_get_request", side_effect=fake_get):
            posts = backfill._read_page_posts(
                CONFIG["facebook"],
                CONFIG["facebook"]["pages"][0],
                limit=4,
                cutoff=NOW - timedelta(days=30),
            )

        self.assertEqual([post["id"] for post in posts], ["page-1_1"])
        self.assertEqual(len(calls), 3)
        self.assertIn("attachments{description}", calls[0][1]["fields"])
        self.assertNotIn("attachments", calls[1][1]["fields"])

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

    def test_affiliate_comment_sends_tracking_url_as_plain_text(self):
        requests = []

        def fake_form(_url, fields):
            requests.append(dict(fields))
            return {"id": "comment-1"}

        with patch("social_upload.facebook.http_form_request", side_effect=fake_form) as request:
            comment_id, error = post_facebook_source_comment(
                {"graph_version": "v25.0"},
                "page-1_1",
                "🛒 Sản phẩm liên quan trong video:\nhttps://s.shopee.vn/an_redir?tracking=1",
                "page-token",
                attempts=1,
            )

        self.assertEqual(comment_id, "comment-1")
        self.assertEqual(error, "")
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("attachment_url", requests[0])
        self.assertIn("https://s.shopee.vn/an_redir?tracking=1", requests[0]["message"])

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
        self.assertTrue(backfill._has_existing_affiliate_comment([{"message": "🛒 Sản phẩm gợi ý trên Shopee:\n👉 Xem sản phẩm trên Shopee"}]))
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

    def test_addlivetag_keyword_search_is_used_when_cache_is_empty(self):
        candidate = {
            "provider_product_id": "p-3",
            "name": "Xtra Bình Giữ Nhiệt Cao Cấp Inox Cho Dân Văn Phòng",
            "origin_url": "https://shopee.vn/bc-i.1.3",
            "commission_rate": 0.2,
            "link_provider": "addlivetag",
            "raw": {"query": "bình giữ nhiệt dân văn phòng"},
        }
        with (
            patch.object(backfill.affiliate_store, "list_products", return_value=[]),
            patch.object(backfill, "search_addlivetag_products", return_value=[candidate]) as search,
        ):
            product, reason = backfill._select_product(
                "knowzy",
                "Bình giữ nhiệt cho dân văn phòng",
                CONTEXT,
            )

        self.assertFalse(reason)
        self.assertEqual(product["provider_product_id"], "p-3")
        self.assertEqual(product["link_provider"], "addlivetag")
        self.assertGreaterEqual(product["relevance_score"], CONTEXT["settings"]["min_relevance"])
        search.assert_called_once_with("bình giữ nhiệt dân văn phòng", limit=20)

    def test_empty_keyword_search_uses_stable_weighted_high_commission_fallback(self):
        fallback_products = [
            {"provider_product_id": "high-1", "name": "Sản phẩm hoa hồng cao 1", "origin_url": "https://shopee.vn/product/1/11", "commission_rate": 0.20},
            {"provider_product_id": "high-2", "name": "Sản phẩm hoa hồng cao 2", "origin_url": "https://shopee.vn/product/1/12", "commission_rate": 0.15},
            {"provider_product_id": "high-3", "name": "Sản phẩm hoa hồng cao 3", "origin_url": "https://shopee.vn/product/1/13", "commission_rate": 0.12},
            {"provider_product_id": "too-low", "name": "Sản phẩm hoa hồng thấp", "origin_url": "https://shopee.vn/product/1/14", "commission_rate": 0.03},
        ]

        def fake_search(keyword, *, limit):
            self.assertEqual(limit, 20)
            return [] if keyword == "không có sản phẩm" else fallback_products

        context = {**CONTEXT, "settings": {**CONTEXT["settings"], "min_commission": 0.05}}
        with (
            patch.object(backfill.affiliate_store, "list_products", return_value=[]),
            patch.object(backfill, "_product_search_queries", return_value=["không có sản phẩm"]),
            patch.object(backfill, "search_addlivetag_products", side_effect=fake_search),
        ):
            first, first_reason = backfill._select_product(
                "knowzy",
                "Bài không có từ khóa sản phẩm rõ ràng",
                context,
                selection_seed="page-1_1",
            )
            second, second_reason = backfill._select_product(
                "knowzy",
                "Bài không có từ khóa sản phẩm rõ ràng",
                context,
                selection_seed="page-1_1",
            )

        self.assertEqual(first["_aurex_selection_mode"], "random_high_commission")
        self.assertEqual(first["provider_product_id"], second["provider_product_id"])
        self.assertGreaterEqual(first["commission_rate"], 0.12)
        self.assertEqual(first_reason, second_reason)
        self.assertIn("hoa hồng cao", first_reason)

    def test_fallback_keeps_posts_without_a_provider_or_affiliate_id_skipped(self):
        context = {
            **CONTEXT,
            "addlivetag": {"enabled": True, "affiliate_id_configured": False},
            "connection": {"connected": False},
        }
        with patch.object(backfill.affiliate_store, "list_products", return_value=[]):
            product, reason = backfill._select_product(
                "knowzy",
                "Bài không có từ khóa sản phẩm rõ ràng",
                context,
            )

        self.assertFalse(product)
        self.assertIn("Affiliate ID", reason)

    def test_fallback_excludes_missing_or_zero_commission_even_when_threshold_is_zero(self):
        candidates = backfill._fallback_candidates(
            [
                {"provider_product_id": "missing", "name": "Thiếu commission", "origin_url": "https://shopee.vn/product/1/21"},
                {"provider_product_id": "zero", "name": "Commission bằng 0", "origin_url": "https://shopee.vn/product/1/22", "commission_rate": 0},
                {"provider_product_id": "positive", "name": "Commission hợp lệ", "origin_url": "https://shopee.vn/product/1/23", "commission_rate": 0.05},
            ],
            {"min_commission": 0},
        )

        self.assertEqual([candidate["provider_product_id"] for candidate in candidates], ["positive"])

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

    def test_addlivetag_fallback_runs_when_official_discovery_has_no_acceptable_product(self):
        context = {**CONTEXT, "connection": {"connected": True}}
        candidate = {
            "provider_product_id": "fallback-1",
            "name": "Sản phẩm fallback hoa hồng cao",
            "origin_url": "https://shopee.vn/product/1/31",
            "commission_rate": 0.20,
            "link_provider": "addlivetag",
        }

        def fake_search(keyword, *, limit):
            self.assertEqual(limit, 20)
            return [] if keyword == "keyword" else [candidate]

        with (
            patch.object(backfill, "discover_products", return_value={"products": []}) as discover,
            patch.object(backfill.affiliate_store, "list_products", return_value=[]),
            patch.object(backfill, "_product_search_queries", return_value=["keyword"]),
            patch.object(backfill, "search_addlivetag_products", side_effect=fake_search),
        ):
            product, reason = backfill._select_product(
                "knowzy",
                "Nội dung không có sản phẩm phù hợp",
                context,
                selection_seed="page-1_2",
            )

        self.assertEqual(product["provider_product_id"], "fallback-1")
        self.assertIn("hoa hồng cao", reason)
        discover.assert_called_once()

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
        self.assertTrue(result["preview_token"])
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
            preview_token = backfill._create_backfill_preview("knowzy", "page-1", 20, 30, {})
            result = backfill.run_affiliate_backfill("knowzy", dry_run=False, preview_token=preview_token)

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
        preview_token = backfill._create_backfill_preview(
            "knowzy",
            "page-1",
            20,
            30,
            {"page-1_1": {"product": cached[0]}},
        )
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
            result = backfill.run_affiliate_backfill("knowzy", dry_run=False, preview_token=preview_token)

        self.assertTrue(result["ok"])
        self.assertEqual(result["commented"], 1)
        self.assertEqual(result["items"][0]["status"], "commented")
        self.assertEqual(post_comment.call_args.args[2], "🛒 Sản phẩm liên quan trong video:\nhttps://s.shopee.vn/abc")
        self.assertNotIn("attachment_url", post_comment.call_args.kwargs)
        self.assertNotIn("fallback_message", post_comment.call_args.kwargs)
        self.assertEqual(post_comment.call_args.kwargs["attempts"], 1)
        self.assertFalse(create_link.call_args.kwargs["reuse_product_offer_url"])
        self.assertTrue(any(call.kwargs.get("affiliate_url") == "https://s.shopee.vn/abc" for call in update_record.call_args_list))
        self.assertTrue(any(call.kwargs.get("status") == "commented" for call in update_record.call_args_list))

    def test_execute_uses_the_previewed_product_instead_of_reselecting_catalog(self):
        def fake_get(url, fields):
            if url.endswith("/feed"):
                return {"data": [graph_post()]}
            if url.endswith("/video_reels"):
                return {"data": []}
            if url.endswith("/comments"):
                return {"data": []}
            self.fail(f"unexpected URL {url}")

        preview_product = {
            "id": "preview-product",
            "name": "Bình giữ nhiệt đã preview",
            "origin_url": "https://shopee.vn/product/1/2",
            "commission_rate": 0.20,
            "relevance_score": 1.0,
            "link_provider": "addlivetag",
            "raw": {"_aurex_link_provider": "addlivetag"},
        }
        preview_token = backfill._create_backfill_preview(
            "knowzy",
            "page-1",
            20,
            30,
            {"page-1_1": {"product": preview_product}},
        )
        with (
            patch.object(backfill, "read_social_config", return_value=CONFIG),
            patch.object(backfill, "brand_context", return_value=CONTEXT),
            patch.object(backfill, "http_get_request", side_effect=fake_get),
            patch.object(backfill.affiliate_store, "overview", return_value={"products": []}),
            patch.object(backfill.affiliate_store, "upsert_product", side_effect=lambda product: {**product, "id": "preview-product"}),
            patch.object(backfill.affiliate_store, "record_content_product", return_value={"id": "record-1"}),
            patch.object(backfill.affiliate_store, "update_content_product"),
            patch.object(backfill, "create_affiliate_link", return_value={"link": {"affiliate_url": "https://s.shopee.vn/preview"}}) as create_link,
            patch.object(backfill, "post_facebook_source_comment", return_value=("comment-1", "")),
            patch.object(backfill, "_select_product", side_effect=AssertionError("execute must consume preview selection")),
        ):
            result = backfill.run_affiliate_backfill("knowzy", dry_run=False, preview_token=preview_token)

        self.assertTrue(result["ok"])
        self.assertEqual(result["commented"], 1)
        self.assertEqual(create_link.call_args.kwargs["product_id"], "preview-product")
        self.assertEqual(create_link.call_args.kwargs["origin_url"], "https://shopee.vn/product/1/2")


if __name__ == "__main__":
    unittest.main()
