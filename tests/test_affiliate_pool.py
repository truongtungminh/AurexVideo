import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from social_upload import affiliate_store
from social_upload.affiliate import (
    create_affiliate_link,
    delete_product_pool,
    is_valid_affiliate_url,
    list_product_pool,
    prepare_affiliate_for_publish,
    save_product_pool,
    select_pool_product,
)


ORIGIN = "https://shopee.vn/product/775125376/18824975414"
SHORT = "https://shp.today/9Owj"


class AffiliatePoolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aurex-affiliate-pool-")
        self.db_patch = patch.object(affiliate_store, "AFFILIATE_DB_PATH", Path(self.temp_dir.name) / "affiliate.sqlite3")
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_pool_is_brand_scoped_and_update_keeps_same_row(self):
        first = save_product_pool(
            "Knowzy",
            {
                "name": "Bình giữ nhiệt 500ml",
                "originUrl": ORIGIN,
                "affiliateUrl": SHORT,
                "commissionRate": 10.5,
                "priority": 4,
            },
        )
        self.assertEqual(first["brand_id"], "knowzy")
        self.assertEqual(first["link_provider"], "pool")
        self.assertAlmostEqual(first["commission_rate"], 0.105)
        self.assertEqual(len(list_product_pool("knowzy")["products"]), 1)
        self.assertEqual(list_product_pool("bietchichomet")["products"], [])

        updated = save_product_pool(
            "knowzy",
            {
                "id": first["id"],
                "name": "Bình giữ nhiệt 500ml bản mới",
                "originUrl": ORIGIN,
                "affiliateUrl": "https://shp.today/updated",
                "commissionRate": 12,
                "enabled": False,
            },
        )
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["affiliate_url"], "https://shp.today/updated")
        self.assertFalse(updated["enabled"])
        self.assertEqual(len(list_product_pool("knowzy")["products"]), 1)

    def test_pool_rejects_invalid_origin_or_short_link(self):
        with self.assertRaisesRegex(ValueError, "Link gốc"):
            save_product_pool("knowzy", {"name": "Sai", "originUrl": "https://example.com/product/1", "affiliateUrl": SHORT})
        with self.assertRaisesRegex(ValueError, "Link rút gọn"):
            save_product_pool("knowzy", {"name": "Sai", "originUrl": ORIGIN, "affiliateUrl": "https://example.com/short"})
        self.assertTrue(is_valid_affiliate_url(SHORT))
        self.assertTrue(is_valid_affiliate_url("https://s.shopee.vn/an_redir?origin_link=x"))
        self.assertFalse(is_valid_affiliate_url("http://shp.today/9Owj"))

    def test_pool_selection_prefers_matching_product_then_high_commission_fallback(self):
        save_product_pool("knowzy", {"name": "Máy hút bụi cầm tay", "originUrl": ORIGIN, "affiliateUrl": "https://shp.today/vacuum", "commissionRate": 8})
        save_product_pool("knowzy", {"name": "Áo mưa đi đường", "originUrl": "https://shopee.vn/product/775125376/18824975415", "affiliateUrl": "https://shp.today/rain", "commissionRate": 20})
        settings = {"min_relevance": 0.5, "min_commission": 0.05}

        matching = select_pool_product("knowzy", "máy hút bụi cầm tay", settings=settings, selection_seed="post-1")
        self.assertEqual(matching["name"], "Máy hút bụi cầm tay")
        self.assertEqual(matching["link_provider"], "pool")
        self.assertEqual(matching["affiliate_url"], "https://shp.today/vacuum")
        self.assertEqual(matching["_aurex_selection_mode"], "pool_match")

        fallback = select_pool_product("knowzy", "không có sản phẩm tương ứng", settings=settings, selection_seed="post-2")
        self.assertEqual(fallback["link_provider"], "pool")
        self.assertEqual(fallback["_aurex_selection_mode"], "pool_high_commission")
        self.assertEqual(fallback["affiliate_url"], "https://shp.today/rain")

    def test_create_link_reuses_authoritative_pool_short_link_without_shopee_call(self):
        pool_row = save_product_pool(
            "knowzy",
            {"name": "Bình giữ nhiệt", "originUrl": ORIGIN, "affiliateUrl": SHORT, "commissionRate": 10.5},
        )
        config = {"brand_routes": {"knowzy": {"facebook": {"page_id": "page-1"}}}}
        with (
            patch("social_upload.affiliate.read_social_config", return_value=config),
            patch("social_upload.affiliate.generate_short_link") as generate,
        ):
            result = create_affiliate_link(
                brand="knowzy",
                content_id="video-1",
                product_id=pool_row["id"],
                origin_url="https://shopee.vn/product/other/999",
                affiliate_url="https://shp.today/client-value",
                page_id="page-1",
                product_payload=pool_row,
                link_provider="pool",
            )

        self.assertEqual(result["link"]["affiliate_url"], SHORT)
        self.assertEqual(result["link"]["origin_url"], ORIGIN)
        self.assertEqual(result["product"]["id"], pool_row["id"])
        self.assertEqual(result["product"]["brand_id"], "knowzy")
        generate.assert_not_called()
        self.assertEqual(affiliate_store.list_products(), [])

    def test_delete_pool_is_scoped_to_brand(self):
        pool_row = save_product_pool("knowzy", {"name": "Bình giữ nhiệt", "originUrl": ORIGIN, "affiliateUrl": SHORT, "commissionRate": 10})
        self.assertFalse(delete_product_pool("bietchichomet", pool_row["id"])["deleted"])
        self.assertTrue(delete_product_pool("knowzy", pool_row["id"])["deleted"])
        self.assertFalse(list_product_pool("knowzy")["configured"])

    def test_auto_ignores_client_provider_product_and_uses_brand_pool(self):
        pool_row = save_product_pool(
            "knowzy",
            {"name": "Bình giữ nhiệt trong Pool", "originUrl": ORIGIN, "affiliateUrl": SHORT, "commissionRate": 10.5},
        )
        affiliate_store.upsert_settings(
            "knowzy",
            {"enabled": True, "mode": "auto", "min_relevance": 0, "min_commission": 0.05},
        )
        config = {"brand_routes": {"knowzy": {"facebook": {"page_id": "page-1"}}}}
        payload = {
            "affiliate": {
                "enabled": True,
                "mode": "auto",
                "productId": "provider-catalog-product",
                "linkProvider": "shopee",
                "originUrl": "https://shopee.vn/product/client/999",
                "affiliateUrl": "https://s.shopee.vn/client-link",
                "product": {
                    "id": "provider-catalog-product",
                    "name": "Sản phẩm provider không được dùng",
                    "origin_url": "https://shopee.vn/product/client/999",
                    "offer_url": "https://s.shopee.vn/client-link",
                    "link_provider": "shopee",
                },
            }
        }
        with (
            patch("social_upload.affiliate.read_social_config", return_value=config),
            patch("social_upload.affiliate.generate_short_link") as generate,
        ):
            result = prepare_affiliate_for_publish(payload, "video-1", "knowzy", "page-1")

        self.assertEqual(result["link_provider"], "pool")
        self.assertEqual(result["product"]["id"], pool_row["id"])
        self.assertEqual(result["link"]["affiliate_url"], SHORT)
        self.assertEqual(result["link"]["origin_url"], ORIGIN)
        generate.assert_not_called()

    def test_same_affiliate_url_is_isolated_between_brands(self):
        affiliate_store.record_link({
            "content_id": "video-knowzy",
            "brand_id": "knowzy",
            "origin_url": ORIGIN,
            "affiliate_url": SHORT,
        })
        affiliate_store.record_link({
            "content_id": "video-bietchichomet",
            "brand_id": "bietchichomet",
            "origin_url": "https://shopee.vn/product/775125376/18824975415",
            "affiliate_url": SHORT,
        })

        knowzy_links = affiliate_store.overview(brand_id="knowzy")["links"]
        bietchichomet_links = affiliate_store.overview(brand_id="bietchichomet")["links"]
        self.assertEqual(len(knowzy_links), 1)
        self.assertEqual(len(bietchichomet_links), 1)
        self.assertEqual(knowzy_links[0]["content_id"], "video-knowzy")
        self.assertEqual(bietchichomet_links[0]["content_id"], "video-bietchichomet")

    def test_existing_global_link_unique_key_migrates_without_losing_rows(self):
        with sqlite3.connect(str(affiliate_store.AFFILIATE_DB_PATH)) as connection:
            connection.execute(
                """
                CREATE TABLE affiliate_links (
                    id TEXT PRIMARY KEY,
                    content_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    product_id TEXT NOT NULL DEFAULT '',
                    origin_url TEXT NOT NULL,
                    affiliate_url TEXT NOT NULL,
                    sub_id_1 TEXT NOT NULL DEFAULT '',
                    sub_id_2 TEXT NOT NULL DEFAULT '',
                    sub_id_3 TEXT NOT NULL DEFAULT '',
                    sub_id_4 TEXT NOT NULL DEFAULT '',
                    sub_id_5 TEXT NOT NULL DEFAULT '',
                    placement TEXT NOT NULL DEFAULT 'first_comment',
                    status TEXT NOT NULL DEFAULT 'created',
                    created_at TEXT NOT NULL,
                    UNIQUE (affiliate_url)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO affiliate_links
                  (id, content_id, brand_id, provider, product_id, origin_url, affiliate_url, created_at)
                VALUES ('legacy-link', 'legacy-video', 'knowzy', 'shopee', '', ?, ?, '2026-09-02T00:00:00Z')
                """,
                (ORIGIN, SHORT),
            )

        affiliate_store.init_db()
        affiliate_store.record_link({
            "content_id": "new-video",
            "brand_id": "bietchichomet",
            "origin_url": "https://shopee.vn/product/775125376/18824975415",
            "affiliate_url": SHORT,
        })
        self.assertEqual(len(affiliate_store.overview(brand_id="knowzy")["links"]), 1)
        self.assertEqual(len(affiliate_store.overview(brand_id="bietchichomet")["links"]), 1)


if __name__ == "__main__":
    unittest.main()
