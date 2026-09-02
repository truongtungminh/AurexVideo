import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from social_upload import facebook_backfill as backfill
from social_upload.facebook import post_facebook_source_comment


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
    "pool": {"configured": True, "total": 1, "enabled": 1},
    "connection": {"connected": False},
}


def pool_product(
    product_id="pool-1",
    name="Bình giữ nhiệt tốt",
    origin_url="https://shopee.vn/product/1/2",
    affiliate_url="https://shp.today/9Owj",
    commission_rate=0.2,
    **extra,
):
    return {
        "id": product_id,
        "provider": "shopee",
        "provider_product_id": product_id,
        "name": name,
        "origin_url": origin_url,
        "affiliate_url": affiliate_url,
        "commission_rate": commission_rate,
        "priority": 0,
        "enabled": True,
        "link_provider": "pool",
        **extra,
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
                "🛒 Sản phẩm liên quan trong video:\nhttps://shp.today/9Owj",
                "page-token",
                attempts=1,
            )

        self.assertEqual(comment_id, "comment-1")
        self.assertEqual(error, "")
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("attachment_url", requests[0])
        self.assertIn("https://shp.today/9Owj", requests[0]["message"])

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
        self.assertEqual(len(calls), 3)

    def test_existing_comment_detects_marker_or_shopee_url(self):
        self.assertTrue(backfill._has_existing_affiliate_comment([{"message": "🛒 Sản phẩm liên quan trong video:\nhttps://shp.today/9Owj"}]))
        self.assertTrue(backfill._has_existing_affiliate_comment([{"message": "🛒 Sản phẩm gợi ý trên Shopee"}]))
        self.assertTrue(backfill._has_existing_affiliate_comment([{"message": "xem https://shopee.vn/product/1/2"}]))
        self.assertTrue(backfill._has_existing_affiliate_comment([{"message": "xem https://shopee.ee/product/1/2"}]))
        self.assertFalse(backfill._has_existing_affiliate_comment([{"message": "Một bình luận bình thường"}]))

    def test_pool_selection_matches_product_name_and_keeps_brand_link(self):
        rows = [
            pool_product(name="Máy hút bụi cầm tay", affiliate_url="https://shp.today/vacuum", commission_rate=0.2),
            pool_product(product_id="pool-2", name="Áo mưa đi đường", affiliate_url="https://shp.today/rain", commission_rate=0.8),
        ]
        context = {**CONTEXT}
        with patch.object(backfill.affiliate_store, "list_product_pool", return_value=rows) as load_pool:
            product, reason = backfill._select_product(
                "knowzy",
                "Review nhanh cho anh em: máy hút bụi cầm tay nhỏ gọn, lực hút mạnh, pin lâu.",
                context,
            )

        self.assertEqual(product["id"], "pool-1")
        self.assertEqual(product["link_provider"], "pool")
        self.assertEqual(product["affiliate_url"], "https://shp.today/vacuum")
        self.assertIn("Pool Shopee", reason)
        load_pool.assert_called_once_with("knowzy", enabled_only=False, limit=200)

    def test_pool_selection_uses_deterministic_high_commission_fallback(self):
        rows = [
            pool_product(product_id="high-1", name="Sản phẩm hoa hồng cao 1", affiliate_url="https://shp.today/high-1", commission_rate=0.20),
            pool_product(product_id="high-2", name="Sản phẩm hoa hồng cao 2", affiliate_url="https://shp.today/high-2", commission_rate=0.15),
            pool_product(product_id="high-3", name="Sản phẩm hoa hồng cao 3", affiliate_url="https://shp.today/high-3", commission_rate=0.12),
            pool_product(product_id="too-low", name="Sản phẩm hoa hồng thấp", affiliate_url="https://shp.today/low", commission_rate=0.03),
        ]
        context = {**CONTEXT, "settings": {**CONTEXT["settings"], "min_commission": 0.05}}
        with patch.object(backfill.affiliate_store, "list_product_pool", return_value=rows):
            first, first_reason = backfill._select_product("knowzy", "Bài không có từ khóa sản phẩm rõ ràng", context, selection_seed="page-1_1")
            second, second_reason = backfill._select_product("knowzy", "Bài không có từ khóa sản phẩm rõ ràng", context, selection_seed="page-1_1")

        self.assertEqual(first["link_provider"], "pool")
        self.assertEqual(first["_aurex_selection_mode"], "pool_high_commission")
        self.assertEqual(first["id"], second["id"])
        self.assertGreaterEqual(first["commission_rate"], 0.12)
        self.assertEqual(first_reason, second_reason)
        self.assertIn("hoa hồng cao", first_reason)

    def test_pool_selection_skips_when_pool_is_empty(self):
        with patch.object(backfill.affiliate_store, "list_product_pool", return_value=[]):
            product, reason = backfill._select_product("knowzy", "Nội dung bất kỳ", {**CONTEXT})

        self.assertFalse(product)
        self.assertIn("Pool Shopee chưa có sản phẩm", reason)

    def test_pool_candidates_reject_disabled_invalid_and_zero_commission_rows(self):
        candidates = backfill._pool_candidates(
            [
                pool_product(product_id="disabled", enabled=False),
                pool_product(product_id="invalid", affiliate_url="https://example.com/short", commission_rate=0.2),
                pool_product(product_id="zero", commission_rate=0),
                pool_product(product_id="valid", affiliate_url="https://shp.today/valid", commission_rate=0.05),
            ],
            {"min_commission": 0},
        )

        self.assertEqual({candidate["id"] for candidate in candidates}, {"zero", "valid"})

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

    def test_dry_run_never_creates_link_or_comment(self):
        def fake_get(url, fields):
            if url.endswith("/feed"):
                return {"data": [graph_post()]}
            if url.endswith("/video_reels"):
                return {"data": [graph_post()]}
            if url.endswith("/comments"):
                return {"data": []}
            self.fail(f"unexpected URL {url}")

        with (
            patch.object(backfill, "read_social_config", return_value=CONFIG),
            patch.object(backfill, "brand_context", return_value={**CONTEXT}),
            patch.object(backfill, "http_get_request", side_effect=fake_get),
            patch.object(backfill.affiliate_store, "overview", return_value={"products": []}),
            patch.object(backfill.affiliate_store, "list_product_pool", return_value=[pool_product()]),
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
            patch.object(backfill, "brand_context", return_value={**CONTEXT}),
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
            patch.object(backfill, "brand_context", return_value={**CONTEXT}),
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

    def test_execute_uses_previewed_pool_product_and_posts_plain_text_comment(self):
        def fake_get(url, fields):
            if url.endswith("/feed"):
                return {"data": [graph_post()]}
            if url.endswith("/video_reels"):
                return {"data": []}
            if url.endswith("/comments"):
                return {"data": []}
            self.fail(f"unexpected URL {url}")

        preview_product = pool_product(
            product_id="preview-product",
            name="Bình giữ nhiệt đã preview",
            affiliate_url="https://shp.today/preview",
            commission_rate=0.20,
            relevance_score=1.0,
        )
        current_pool_product = pool_product(
            product_id="preview-product",
            name="Bình giữ nhiệt bản hiện tại",
            origin_url="https://shopee.vn/product/9/10",
            affiliate_url="https://shp.today/current",
            commission_rate=0.25,
            enabled=True,
        )
        preview_token = backfill._create_backfill_preview(
            "knowzy",
            "page-1",
            20,
            30,
            {"page-1_1": {"product": preview_product}},
        )
        with (
            patch.object(backfill, "read_social_config", return_value=CONFIG),
            patch.object(backfill, "brand_context", return_value={**CONTEXT}),
            patch.object(backfill, "http_get_request", side_effect=fake_get),
            patch.object(backfill.affiliate_store, "overview", return_value={"products": []}),
            patch.object(backfill.affiliate_store, "get_product_pool", return_value=current_pool_product) as get_pool,
            patch.object(backfill.affiliate_store, "upsert_product") as upsert,
            patch.object(backfill.affiliate_store, "record_content_product", return_value={"id": "record-1"}),
            patch.object(backfill.affiliate_store, "update_content_product") as update_record,
            patch.object(backfill, "create_affiliate_link", return_value={"link": {"affiliate_url": "https://shp.today/current"}}) as create_link,
            patch.object(backfill, "post_facebook_source_comment", return_value=("comment-1", "")) as post_comment,
            patch.object(backfill, "_select_product", side_effect=AssertionError("execute must consume preview selection")),
        ):
            result = backfill.run_affiliate_backfill("knowzy", dry_run=False, preview_token=preview_token)

        self.assertTrue(result["ok"])
        self.assertEqual(result["commented"], 1)
        self.assertEqual(result["items"][0]["status"], "commented")
        self.assertEqual(create_link.call_args.kwargs["product_id"], "preview-product")
        self.assertEqual(create_link.call_args.kwargs["origin_url"], "https://shopee.vn/product/9/10")
        self.assertEqual(post_comment.call_args.args[2], "🛒 Sản phẩm liên quan: Bình giữ nhiệt bản hiện tại\nhttps://shp.today/current")
        self.assertNotIn("attachment_url", post_comment.call_args.kwargs)
        self.assertEqual(post_comment.call_args.kwargs["attempts"], 1)
        self.assertFalse(upsert.called)
        get_pool.assert_called_once_with("preview-product", brand_id="knowzy")
        self.assertTrue(any(call.kwargs.get("affiliate_url") == "https://shp.today/current" for call in update_record.call_args_list))
        self.assertTrue(any(call.kwargs.get("status") == "commented" for call in update_record.call_args_list))


if __name__ == "__main__":
    unittest.main()
