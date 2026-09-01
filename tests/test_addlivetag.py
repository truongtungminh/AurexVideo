import io
import json
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from social_upload.addlivetag import (
    DEFAULT_PRODUCT_DATA_URL,
    DEFAULT_SEARCH_URL,
    AddLiveTagApiError,
    extract_shopee_reference,
    fetch_product_data,
    fetch_normalized_product_data,
    generate_short_link,
    normalize_config,
    normalize_product_payload,
    search_addlivetag_products,
)


class _FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size=-1):
        return self.payload if _size < 0 else self.payload[:_size]


class AddLiveTagTests(unittest.TestCase):
    def test_fetch_success_normalizes_explicit_shopee_url(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return _FakeResponse({
                "status": "success",
                "productInfo": {
                    "itemId": "1234567890",
                    "shopId": "9988",
                    "productName": "Bình giữ nhiệt",
                    "originLink": "https://shopee.vn/binh-giu-nhiet",
                    "affiliateLink": "https://s.shopee.vn/affiliate-123",
                    "imageUrl": "https://cf.shopee.vn/image.jpg",
                    "price": {"min": "120.000", "max": "150.000"},
                    "totalRatePercent": "8",
                    "sales": 350,
                    "rating": 4.8,
                    "discountRate": 15,
                    "shopQuality": 95,
                    "accessToken": "must-not-leak",
                },
                "legalNotice": {"text": "demo", "api_key": "must-not-leak"},
            })

        with patch("social_upload.addlivetag.urlopen", fake_urlopen):
            payload = fetch_product_data("https://shopee.vn/binh-giu-nhiet")
            product = normalize_product_payload(payload)

        self.assertEqual(payload["status"], "success")
        self.assertEqual(product["provider"], "shopee")
        self.assertEqual(product["provider_product_id"], "1234567890")
        self.assertEqual(product["price_min"], 120000.0)
        self.assertEqual(product["price_max"], 150000.0)
        self.assertEqual(product["commission_rate"], 0.08)
        self.assertEqual(product["offer_url"], "https://s.shopee.vn/affiliate-123")
        self.assertEqual(product["relevance_score"], 1.0)
        self.assertNotIn("accessToken", product["raw"]["productInfo"])
        self.assertNotIn("api_key", product["raw"]["legalNotice"])
        query = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(query["url"], ["https://shopee.vn/binh-giu-nhiet"])
        self.assertEqual(captured["timeout"], 20.0)

    def test_fetch_item_id_and_commission_amount(self):
        def fake_urlopen(_request, timeout):
            self.assertEqual(timeout, 7.0)
            return _FakeResponse({
                "status": "success",
                "productInfo": {
                    "itemId": "12345",
                    "productLink": "https://shopee.vn/product-1",
                    "price": 200000,
                    "commission": 10000,
                    "affLink": "https://example.com/not-shopee",
                    "latestPriceHistory": {"priceStats": {"discountPercent": 17}},
                },
                "legalNotice": {},
            })

        with patch("social_upload.addlivetag.urlopen", fake_urlopen):
            product = fetch_normalized_product_data({"item_id": "12345"}, timeout=7)
        self.assertEqual(product["commission_rate"], 0.05)
        self.assertEqual(product["offer_url"], "")
        self.assertEqual(product["discount_rate"], 17.0)

    def test_public_search_normalizes_visible_product_rows_and_skips_login_rows(self):
        captured = {}
        html = """
        <table>
          <tr>
            <td>0</td><td><a href="https://shopee.vn/bc-i.123.456"><img src="x"/></a></td>
            <td><a href="https://shopee.vn/bc-i.123.456">Miếng Ghép Nam Châm</a></td>
            <td class="click-to-copy">https://shopee.vn/bc-i.123.456</td>
            <td>8,000</td><td>40,000</td><td>120</td>
          </tr>
          <tr>
            <td>2</td><td><a href="https://shopee.vn/bc-i.123.789">Sản phẩm cần đăng nhập</a></td>
            <td class="click-to-copy">https://shopee.vn/bc-i.123.789</td>
            <td><small>đăng nhập để xem</small></td><td><small>đăng nhập để xem</small></td>
          </tr>
        </table>
        """

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return _FakeResponse(html.encode("utf-8"))

        with patch("social_upload.addlivetag.urlopen", fake_urlopen):
            products = search_addlivetag_products("nam châm", limit=20)

        self.assertEqual(len(products), 1)
        product = products[0]
        self.assertEqual(product["provider_product_id"], "456")
        self.assertEqual(product["shop_id"], "123")
        self.assertEqual(product["price_min"], 40000.0)
        self.assertEqual(product["commission_amount_vnd"], 8000.0)
        self.assertEqual(product["commission_rate"], 0.2)
        self.assertNotIn("relevance_score", product)
        self.assertEqual(product["link_provider"], "addlivetag")
        self.assertEqual(product["origin_url"], "https://shopee.vn/bc-i.123.456")
        query = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(query["keyword"], ["nam châm"])
        self.assertEqual(query["sort"], ["com"])
        self.assertEqual(captured["timeout"], 20.0)

    def test_invalid_payload_and_provider_errors_are_safe(self):
        with self.assertRaises(AddLiveTagApiError):
            normalize_product_payload({"status": "error", "message": "secret=do-not-return"})

        with patch("social_upload.addlivetag.urlopen", return_value=_FakeResponse({"status": "error"})):
            with self.assertRaisesRegex(AddLiveTagApiError, "không trả về product data"):
                fetch_product_data({"item_id": "12345"})

        def http_error(request, timeout):
            raise HTTPError(request.full_url, 502, "bad gateway", hdrs=None, fp=io.BytesIO(b"sensitive body"))

        def url_error(_request, timeout):
            raise URLError("connection refused")

        def timeout_error(_request, timeout):
            raise TimeoutError("timed out")

        with patch("social_upload.addlivetag.urlopen", http_error):
            with self.assertRaisesRegex(AddLiveTagApiError, r"HTTP 502") as error:
                fetch_product_data({"item_id": "12345"})
        self.assertNotIn("item_id", str(error.exception))
        with patch("social_upload.addlivetag.urlopen", url_error):
            with self.assertRaisesRegex(AddLiveTagApiError, "request failed"):
                fetch_product_data({"item_id": "12345"})
        with patch("social_upload.addlivetag.urlopen", timeout_error):
            with self.assertRaisesRegex(AddLiveTagApiError, "request failed"):
                fetch_product_data({"item_id": "12345"})

    def test_validates_reference_and_endpoints_without_matching_prose_numbers(self):
        self.assertEqual(extract_shopee_reference("item id: 12345"), {"item_id": "12345"})
        self.assertEqual(extract_shopee_reference("12345"), {"item_id": "12345"})
        with self.assertRaises(ValueError):
            extract_shopee_reference("Video đạt 12345 lượt xem hôm nay")
        with self.assertRaises(ValueError):
            fetch_product_data("https://example.com/product")
        with self.assertRaises(ValueError):
            fetch_product_data({"item_id": "12345"}, endpoint="http://data.addlivetag.com/product-data/product-data.php")
        with self.assertRaises(ValueError):
            fetch_product_data({"item_id": "12345"}, endpoint="https://data.addlivetag.com:444/product-data/product-data.php")
        with self.assertRaises(ValueError):
            fetch_product_data({"item_id": "12345", "url": "https://shopee.vn/product"})

    def test_short_link_is_experimental_explicit_and_validated(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return _FakeResponse({"success": True, "affiliateLink": "https://s.shopee.vn/abc123"})

        with patch("social_upload.addlivetag.urlopen", fake_urlopen):
            link = generate_short_link(
                "https://shopee.vn/product-1",
                "affiliate-1",
                ["brand", "page", "video", "item", "comment"],
            )
        self.assertEqual(link, "https://s.shopee.vn/abc123")
        self.assertEqual(captured["timeout"], 30.0)
        query = parse_qs(urlparse(captured["url"]).query)
        self.assertEqual(query["aff_id"], ["affiliate-1"])
        self.assertEqual(query["subid5"], ["comment"])
        with self.assertRaises(ValueError):
            generate_short_link("https://shopee.vn/product-1", "")

    def test_short_link_rejects_invalid_provider_response(self):
        with patch(
            "social_upload.addlivetag.urlopen",
            return_value=_FakeResponse({"success": True, "affiliateLink": "https://example.com/not-shopee"}),
        ):
            with self.assertRaisesRegex(AddLiveTagApiError, "không hợp lệ"):
                generate_short_link("https://shopee.vn/product-1", "affiliate-1")

    def test_normalize_config_prefers_selected_brand_value_then_environment(self):
        value = normalize_config({"affiliate_id": "brand-a"}, environ={"ADDLIVETAG_AFFILIATE_ID": "global"})
        self.assertEqual(value["affiliate_id"], "brand-a")
        self.assertEqual(value["product_data_url"], DEFAULT_PRODUCT_DATA_URL)
        self.assertEqual(normalize_config(environ={"ADDLIVETAG_AFFILIATE_ID": "env-id"})["affiliate_id"], "env-id")


if __name__ == "__main__":
    unittest.main()
