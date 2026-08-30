import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from social_upload import affiliate_store
from social_upload.affiliate import (
    brand_context as affiliate_brand_context,
    build_sub_ids,
    create_affiliate_link,
    list_saved_products,
    rank_products,
)
from social_upload.shopee import generate_short_link, update_shopee_config


class _FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class AffiliateEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aurex-affiliate-test-")
        root = Path(self.temp_dir.name)
        self.db_patch = patch.object(affiliate_store, "AFFILIATE_DB_PATH", root / "affiliate.sqlite3")
        self.config_patch = patch("social_upload.config.SOCIAL_UPLOAD_CONFIG", root / "social-upload.json")
        self.db_patch.start()
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_sub_ids_have_five_safe_tracking_dimensions(self):
        sub_ids = build_sub_ids("Brand Name", "123/Page", "video 01", "item/2", "first_comment")

        self.assertEqual(len(sub_ids), 5)
        self.assertTrue(all(re.fullmatch(r"[a-z0-9_-]+", value) for value in sub_ids))
        self.assertEqual(sub_ids[0], "brand-name")
        self.assertEqual(sub_ids[4], "first-comment")

    def test_rank_keeps_relevance_a_first_class_signal(self):
        ranked = rank_products(
            [
                {
                    "itemId": "relevant",
                    "name": "Máy xay mini cho bếp nhỏ",
                    "productLink": "https://shopee.vn/relevant",
                    "relevance": 0.95,
                    "commissionRate": 0.06,
                    "sales": 100000,
                    "rating": 4.8,
                    "priceDiscountRate": 20,
                    "shopQuality": 0.95,
                },
                {
                    "itemId": "commission-heavy",
                    "name": "Máy xay mini",
                    "productLink": "https://shopee.vn/commission-heavy",
                    "relevance": 0.55,
                    "commissionRate": 0.20,
                    "sales": 1000,
                    "rating": 4.5,
                    "priceDiscountRate": 50,
                    "shopQuality": 0.8,
                },
            ],
            "máy xay mini",
        )

        self.assertEqual(ranked[0]["provider_product_id"], "relevant")
        self.assertGreater(ranked[0]["ranking_score"], ranked[1]["ranking_score"])

    def test_brand_connection_is_scoped_and_secret_is_not_in_public_context(self):
        config = {}
        secret = "s" * 32
        result = update_shopee_config(
            "app-123",
            secret,
            brand="Knowzy",
            config=config,
            persist=False,
        )

        self.assertEqual(result["brand"], "knowzy")
        self.assertNotEqual(result["masked_secret"], secret)
        context = affiliate_brand_context(config, "knowzy")
        self.assertTrue(context["connection"]["connected"])
        self.assertNotIn("secret", context["connection"])
        self.assertEqual(context["connection"]["app_id"], "app-123")
        self.assertEqual(config["brand_routes"]["knowzy"]["shopee"]["connection_id"], result["connection_id"])

    def test_store_overview_and_cached_products(self):
        affiliate_store.upsert_settings("knowzy", {"enabled": True, "mode": "manual"})
        product = affiliate_store.upsert_product(
            {
                "provider_product_id": "item-1",
                "name": "Bình giữ nhiệt",
                "origin_url": "https://shopee.vn/binh-giu-nhiet",
                "commission_rate": 0.08,
            }
        )
        affiliate_store.record_link(
            {
                "content_id": "video-1",
                "brand_id": "knowzy",
                "product_id": product["id"],
                "origin_url": product["origin_url"],
                "affiliate_url": "https://s.shopee.vn/link-1",
            }
        )
        affiliate_store.upsert_daily_stats(
            "2026-08-30",
            brand_id="knowzy",
            content_id="video-1",
            product_id=product["id"],
            clicks=12,
            orders=2,
            gmv=450000,
            commission=36000,
        )

        overview = affiliate_store.overview(brand_id="knowzy")
        cached = list_saved_products("Bình giữ nhiệt")
        self.assertEqual(overview["kpis"]["clicks"], 12)
        self.assertEqual(overview["kpis"]["orders"], 2)
        self.assertEqual(overview["kpis"]["commission"], 36000.0)
        self.assertEqual(len(overview["links"]), 1)
        self.assertEqual(cached["products"][0]["id"], product["id"])

    def test_graphql_short_link_uses_signed_official_request_shape(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return _FakeResponse({"data": {"generateShortLink": {"shortLink": "https://s.shopee.vn/abc"}}})

        connection = {
            "app_id": "app-123",
            "secret": "s" * 32,
            "api_base_url": "https://open-api.affiliate.shopee.vn/graphql",
            "_brand_connection": True,
        }
        with patch("social_upload.shopee.urlopen", fake_urlopen):
            link = generate_short_link(
                connection,
                "https://shopee.vn/product-1",
                ["knowzy", "page-1", "video-1", "item-1", "first-comment"],
            )

        self.assertEqual(link, "https://s.shopee.vn/abc")
        request = captured["request"]
        self.assertTrue(request.headers["Authorization"].startswith("SHA256 Credential=app-123, Signature="))
        body = json.loads(request.data.decode("utf-8"))
        self.assertIn("generateShortLink", body["query"])
        self.assertEqual(body["variables"]["input"]["subIds"][2], "video-1")
        self.assertEqual(captured["timeout"], 60)

    def test_dashboard_can_reuse_existing_affiliate_url_without_provider_call(self):
        result = create_affiliate_link(
            brand="knowzy",
            content_id="affiliate-dashboard",
            product_id="content-record-1",
            placement="first_comment",
            product_payload={
                "product_name": "Bình giữ nhiệt",
                "original_url": "https://shopee.vn/binh-giu-nhiet",
                "affiliate_url": "https://s.shopee.vn/already-created",
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["link"]["affiliate_url"], "https://s.shopee.vn/already-created")
        self.assertEqual(result["link"]["content_id"], "affiliate-dashboard")


if __name__ == "__main__":
    unittest.main()
