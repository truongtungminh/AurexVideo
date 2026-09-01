import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from unittest.mock import patch

import web_server


class AffiliateBackfillApiTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.WebHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _post(self, payload):
        request = Request(
            self.base_url + "/api/affiliate/backfill",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return getattr(exc, "code", 0), json.loads(exc.read().decode("utf-8"))

    def test_defaults_to_dry_run_and_forwards_limits(self):
        expected = {"ok": True, "dry_run": True, "scanned": 2, "eligible": 1}
        with patch("web_server.run_affiliate_backfill", return_value=expected) as runner:
            status, payload = self._post({"brand": "knowzy", "limit": 7, "lookbackDays": 14})

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        runner.assert_called_once_with("knowzy", limit=7, lookback_days=14, dry_run=True, page_id="")

    def test_execute_requires_explicit_comment_confirmation(self):
        status, payload = self._post({"brand": "knowzy", "dryRun": False})

        self.assertEqual(status, 400)
        self.assertIn("xác nhận COMMENT", payload["error"])

    def test_execute_forwards_page_and_confirmation(self):
        expected = {"ok": True, "dry_run": False, "commented": 1}
        with patch("web_server.run_affiliate_backfill", return_value=expected) as runner:
            status, payload = self._post({
                "brand": "knowzy",
                "pageId": "page-1",
                "limit": 3,
                "lookback_days": 90,
                "dry_run": False,
                "confirm": "comment",
            })

        self.assertEqual(status, 200)
        self.assertEqual(payload, expected)
        runner.assert_called_once_with("knowzy", limit=3, lookback_days=90, dry_run=False, page_id="page-1")


if __name__ == "__main__":
    unittest.main()
