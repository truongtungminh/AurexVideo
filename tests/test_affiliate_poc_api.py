import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import web_server
from social_upload import affiliate_store


class AffiliatePocApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="aurex-affiliate-poc-api-test-")
        self.db_patch = patch.object(
            affiliate_store,
            "AFFILIATE_DB_PATH",
            Path(self.temp_dir.name) / "affiliate.sqlite3",
        )
        self.config_patch = patch.object(
            web_server,
            "read_social_config",
            return_value={"brand_routes": {"knowzy": {"facebook": {"page_id": "page-1"}}}},
        )
        self.db_patch.start()
        self.config_patch.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.WebHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.config_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def request(self, path, payload=None):
        data = None
        headers = {}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode("utf-8"))
            finally:
                error.close()

    def test_poc_endpoint_is_brand_scoped_and_tracks_banner_evidence(self):
        status, initial = self.request("/api/affiliate/poc?brand=knowzy&contentId=video-1")
        self.assertEqual(status, 200)
        self.assertEqual([case["key"] for case in initial["cases"]], ["A", "B", "C", "D"])
        self.assertEqual(initial["runs"], [])

        status, started = self.request("/api/affiliate/poc", {
            "brand": "Knowzy",
            "contentId": "video-1",
            "caseKey": "A",
            "status": "running",
        })
        self.assertEqual(status, 200)
        self.assertEqual(started["page_id"], "page-1")
        self.assertEqual(started["summary"]["running"], 1)

        status, passed = self.request("/api/affiliate/poc", {
            "brand": "knowzy",
            "contentId": "video-1",
            "caseKey": "A",
            "status": "passed",
            "postId": "post-1",
            "commentId": "comment-1",
            "bannerObserved": "yes",
            "evidenceUrl": "https://example.test/poc-a",
            "notes": "Page owner comment verified",
        })
        self.assertEqual(status, 200)
        case_a = next(row for row in passed["runs"] if row["case_key"] == "A")
        self.assertEqual(case_a["status"], "passed")
        self.assertEqual(case_a["banner_observed"], "yes")
        self.assertEqual(case_a["post_id"], "post-1")
        self.assertEqual(case_a["comment_id"], "comment-1")
        self.assertEqual(case_a["evidence_url"], "https://example.test/poc-a")
        self.assertEqual(passed["summary"]["passed"], 1)

        status, reread = self.request("/api/affiliate/poc?brand=knowzy&contentId=video-1")
        self.assertEqual(status, 200)
        self.assertEqual(len(reread["runs"]), 4)
        self.assertEqual(next(row for row in reread["runs"] if row["case_key"] == "A")["status"], "passed")

        status, isolated = self.request("/api/affiliate/poc?brand=other&contentId=video-1")
        self.assertEqual(status, 200)
        self.assertEqual(isolated["runs"], [])

    def test_poc_endpoint_rejects_unknown_case_and_unbounded_note(self):
        status, response = self.request("/api/affiliate/poc", {
            "brand": "knowzy",
            "contentId": "video-1",
            "caseKey": "E",
            "status": "running",
        })
        self.assertEqual(status, 400)
        self.assertIn("case", response["error"].lower())

        status, response = self.request("/api/affiliate/poc", {
            "brand": "knowzy",
            "contentId": "video-1",
            "caseKey": "A",
            "status": "passed",
            "notes": "x" * 1001,
        })
        self.assertEqual(status, 400)
        self.assertIn("tối đa", response["error"])


if __name__ == "__main__":
    unittest.main()
