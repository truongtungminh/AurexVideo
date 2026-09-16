from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import web_server


class MetaConnectionsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web_server.WebHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _request(self, path: str, *, method: str = "GET", payload: dict | None = None, origin: str = "") -> tuple[int, dict]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_create_connection_never_echoes_token(self) -> None:
        safe = {"id": "meta-1", "name": "Aurex Business", "token_configured": True, "token_hint": "EAAB••••xxxx"}
        token = "system-user-token-xxxxxxxxxxxxxxxxxxxxxxxx"
        with patch("web_server.save_meta_connection", return_value=safe) as save:
            status, payload = self._request(
                "/api/meta/connections",
                method="POST",
                payload={"name": "Aurex Business", "systemUserAccessToken": token},
            )

        self.assertEqual(status, 201)
        self.assertEqual(payload["connection"], safe)
        self.assertNotIn(token, json.dumps(payload))
        save.assert_called_once_with("Aurex Business", token, connection_id="", business_id="")

    def test_list_connections_and_pages(self) -> None:
        with patch("web_server.list_meta_connections", return_value=[{"id": "meta-1"}]):
            status, payload = self._request("/api/meta/connections")
        self.assertEqual(status, 200)
        self.assertEqual(payload["connections"], [{"id": "meta-1"}])

        with patch("web_server.list_meta_pages", return_value=[{"id": "101", "token_configured": True}]):
            status, payload = self._request("/api/meta/connections/meta-1/pages")
        self.assertEqual(status, 200)
        self.assertEqual(payload["pages"][0]["id"], "101")

    def test_diagnose_and_sync_actions(self) -> None:
        with patch("web_server.diagnose_meta_connection", return_value={"valid": True}) as diagnose:
            status, payload = self._request("/api/meta/connections/meta-1/diagnose", method="POST", payload={})
        self.assertEqual(status, 200)
        self.assertTrue(payload["valid"])
        diagnose.assert_called_once_with("meta-1")

        with patch("web_server.sync_meta_pages", return_value={"ok": True, "imported": 2}) as sync:
            status, payload = self._request("/api/meta/connections/meta-1/sync-pages", method="POST", payload={})
        self.assertEqual(status, 200)
        self.assertEqual(payload["imported"], 2)
        sync.assert_called_once_with("meta-1")

    def test_meta_mutation_rejects_foreign_origin(self) -> None:
        status, payload = self._request(
            "/api/meta/connections",
            method="POST",
            payload={"name": "Blocked", "systemUserAccessToken": "x" * 30},
            origin="https://evil.example",
        )

        self.assertEqual(status, 403)
        self.assertIn("Origin", payload["error"])


if __name__ == "__main__":
    unittest.main()
