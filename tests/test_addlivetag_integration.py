import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import patch

import web_server
from social_upload import affiliate_store
from social_upload.addlivetag import AddLiveTagApiError
from social_upload.affiliate import (
    create_affiliate_link,
    prepare_affiliate_for_publish,
    resolve_addlivetag_product,
    save_addlivetag_settings,
)


PRODUCT_URL = "https://shopee.vn/product/38003654/1589295236"
RAW_PRODUCT_DATA = {
    "status": "success",
    "productInfo": {
        "itemId": 1589295236,
        "shopId": 38003654,
        "productName": "Áo len thử nghiệm",
        "originLink": PRODUCT_URL,
        "price": 175000,
        "sales": 160,
        "rating": "4.8",
        "totalRatePercent": 20.5,
        "imageUrl": "https://cf.shopee.vn/file/test-image",
    },
    "legalNotice": {"apiUnofficial": True, "nonCommercialOnly": True},
}


class AddLiveTagIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aurex-addlivetag-integration-")
        self.db_patch = patch.object(
            affiliate_store,
            "AFFILIATE_DB_PATH",
            Path(self.temp_dir.name) / "affiliate.sqlite3",
        )
        self.config = {
            "affiliate": {
                "brands": {
                    "knowzy": {"enabled": True, "affiliate_id": "aff-123"},
                },
            },
            "brand_routes": {"knowzy": {"facebook": {"page_id": "page-1"}}},
        }
        self.config_patch = patch("social_upload.affiliate.read_social_config", return_value=self.config)
        self.db_patch.start()
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_auto_resolver_fetches_and_stores_brand_product_without_official_shopee_api(self):
        with patch("social_upload.affiliate.fetch_product_data", return_value=RAW_PRODUCT_DATA):
            result = resolve_addlivetag_product(
                "Knowzy",
                "video-1",
                origin_url=PRODUCT_URL,
            )

        self.assertEqual(result["provider"], "addlivetag")
        self.assertTrue(result["can_create_link"])
        self.assertEqual(result["product"]["provider_product_id"], "1589295236")
        self.assertEqual(result["product"]["link_provider"], "addlivetag")
        self.assertEqual(affiliate_store.list_products()[0]["raw"]["_aurex_link_provider"], "addlivetag")

    def test_auto_prepare_uses_addlivetag_short_link(self):
        with (
            patch("social_upload.affiliate._project_query", return_value="áo len"),
            patch("social_upload.affiliate.fetch_product_data", return_value=RAW_PRODUCT_DATA),
                patch(
                "social_upload.affiliate.generate_addlivetag_short_link",
                return_value="https://s.shopee.vn/addlive-test?affiliate_id=aff-123",
            ) as short_link,
        ):
            result = prepare_affiliate_for_publish(
                {"affiliate": {"enabled": True, "mode": "auto", "originUrl": PRODUCT_URL}},
                "video-1",
                "knowzy",
                page_id="page-1",
            )

        self.assertEqual(result["mode"], "auto")
        self.assertEqual(result["link_provider"], "addlivetag")
        self.assertEqual(result["link"]["affiliate_url"], "https://s.shopee.vn/addlive-test?affiliate_id=aff-123")
        short_link.assert_called_once()
        self.assertEqual(short_link.call_args.args[1], "aff-123")

    def test_auto_prepare_stops_before_publish_when_affiliate_id_is_missing(self):
        self.config["affiliate"]["brands"]["knowzy"] = {"enabled": True}
        with (
            patch("social_upload.affiliate._project_query", return_value="áo len"),
            patch("social_upload.affiliate.fetch_product_data", return_value=RAW_PRODUCT_DATA),
        ):
            with self.assertRaisesRegex(ValueError, "Affiliate ID"):
                prepare_affiliate_for_publish(
                    {"affiliate": {"enabled": True, "mode": "auto", "originUrl": PRODUCT_URL}},
                    "video-1",
                    "knowzy",
                    page_id="page-1",
                )

    def test_existing_addlivetag_product_can_create_link_from_dashboard(self):
        with patch(
            "social_upload.affiliate.generate_addlivetag_short_link",
            return_value="https://s.shopee.vn/dashboard-test?affiliate_id=aff-123",
        ):
            resolved = resolve_addlivetag_product("knowzy", "dashboard", origin_url=PRODUCT_URL)
            result = create_affiliate_link(
                brand="knowzy",
                content_id="dashboard",
                product_id=resolved["product"]["id"],
                page_id="page-1",
            )

        self.assertEqual(result["link"]["affiliate_url"], "https://s.shopee.vn/dashboard-test?affiliate_id=aff-123")

    def test_addlivetag_rejects_an_explicit_link_from_another_account(self):
        with self.assertRaisesRegex(AddLiveTagApiError, "khác Brand"):
            create_affiliate_link(
                brand="knowzy",
                content_id="wrong-account",
                origin_url=PRODUCT_URL,
                affiliate_url="https://s.shopee.vn/wrong?affiliate_id=another-account",
                link_provider="addlivetag",
                page_id="page-1",
            )

    def test_save_addlivetag_settings_is_brand_scoped_and_does_not_return_id(self):
        saved = {}

        def fake_write(config):
            saved.update(config)

        with patch("social_upload.config.write_social_config", fake_write):
            context = save_addlivetag_settings(
                "Knowzy",
                {"enabled": True, "affiliateId": "brand-aff-9"},
            )

        self.assertTrue(context["enabled"])
        self.assertTrue(context["configured"])
        self.assertNotIn("brand-aff-9", json.dumps(context, ensure_ascii=False))
        self.assertEqual(saved["affiliate"]["brands"]["knowzy"]["affiliate_id"], "brand-aff-9")


class AddLiveTagAutoResolveApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aurex-addlivetag-api-")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.WebHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.require_project_patch = patch("web_server.require_project", return_value=Path(self.temp_dir.name))
        self.require_project_patch.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.require_project_patch.stop()
        self.temp_dir.cleanup()

    def test_auto_resolve_endpoint_forwards_only_reference_fields(self):
        expected = {"provider": "addlivetag", "product": {"id": "prod-1"}, "can_create_link": False}
        with patch("web_server.resolve_addlivetag_product", return_value=expected) as resolver:
            request = Request(
                self.base_url + "/api/affiliate/auto-resolve",
                data=json.dumps({
                    "brand": "knowzy",
                    "project": "video-1",
                    "reference": PRODUCT_URL,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=10) as response:
                status = response.status
                payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        resolver.assert_called_once_with("knowzy", "video-1", origin_url=PRODUCT_URL, item_id="")


if __name__ == "__main__":
    unittest.main()
