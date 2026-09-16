from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_upload.facebook as facebook
import social_upload.meta_connections as meta


SYSTEM_TOKEN = "system-user-token-xxxxxxxxxxxxxxxxxxxxxxxx"
PAGE_TOKEN_1 = "page-token-one-xxxxxxxxxxxxxxxxxxxxxxxx"
PAGE_TOKEN_2 = "page-token-two-xxxxxxxxxxxxxxxxxxxxxxxx"


def connection_config(*, pages=None) -> dict:
    return {
        "meta_connections": {
            "meta-1": {
                "id": "meta-1",
                "name": "Aurex Business",
                "system_user_access_token": SYSTEM_TOKEN,
                "token_status": "unchecked",
            },
        },
        "facebook": {"graph_version": "v26.0", "pages": list(pages or [])},
        "brand_routes": {},
    }


class MetaConnectionsTests(unittest.TestCase):
    def test_valid_system_user_token_returns_accessible_pages(self) -> None:
        config = connection_config()

        def graph_get(_config, path, _token, params=None, **_kwargs):
            if path == "debug_token":
                return {"data": {"is_valid": True, "app_id": "app-1", "user_id": "sys-1", "expires_at": 0, "scopes": ["pages_show_list"]}}
            if path == "me":
                return {"id": "sys-1", "name": "Aurex System User"}
            if path == "me/accounts":
                return {"data": [{"id": "101", "name": "Page One", "access_token": PAGE_TOKEN_1, "tasks": ["CREATE_CONTENT"]}]}
            raise AssertionError(path)

        with patch.object(meta, "_graph_get", side_effect=graph_get):
            result = meta.diagnose_system_user_token(SYSTEM_TOKEN, config)

        self.assertTrue(result["valid"])
        self.assertEqual(result["system_user_id"], "sys-1")
        self.assertEqual(result["app_id"], "app-1")
        self.assertEqual(result["accessible_pages_count"], 1)

    def test_me_accounts_pagination_collects_every_page(self) -> None:
        config = connection_config()
        first = {
            "data": [{"id": "101", "name": "Page One", "access_token": PAGE_TOKEN_1, "tasks": ["CREATE_CONTENT"]}],
            "paging": {"next": "https://graph.facebook.com/v26.0/me/accounts?after=cursor&access_token=hidden"},
        }
        second = {
            "data": [{"id": "202", "name": "Page Two", "access_token": PAGE_TOKEN_2, "tasks": ["MODERATE"]}],
        }
        with patch.object(meta, "_graph_get", return_value=first), patch.object(meta, "_graph_get_next", return_value=second) as get_next:
            result = meta.fetch_accessible_pages(SYSTEM_TOKEN, config)

        self.assertTrue(result["complete"])
        self.assertEqual([page["id"] for page in result["pages"]], ["101", "202"])
        get_next.assert_called_once()

    def test_sync_inserts_new_page(self) -> None:
        config = connection_config()
        fetched = {"pages": [{"id": "101", "name": "Page One", "page_access_token": PAGE_TOKEN_1, "tasks": ["CREATE_CONTENT"]}], "errors": [], "complete": True, "examined": 1}
        with patch.object(meta, "fetch_accessible_pages", return_value=fetched):
            result = meta.sync_meta_pages("meta-1", config, persist=False)

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(len(config["facebook"]["pages"]), 1)

    def test_sync_updates_existing_page_without_duplicate(self) -> None:
        config = connection_config(pages=[{"id": "101", "name": "Old Name", "page_access_token": PAGE_TOKEN_1}])
        fetched = {"pages": [{"id": "101", "name": "New Name", "page_access_token": PAGE_TOKEN_1, "tasks": ["CREATE_CONTENT"]}], "errors": [], "complete": True, "examined": 1}
        with patch.object(meta, "fetch_accessible_pages", return_value=fetched):
            result = meta.sync_meta_pages("meta-1", config, persist=False)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(len(config["facebook"]["pages"]), 1)
        self.assertEqual(config["facebook"]["pages"][0]["name"], "New Name")

    def test_sync_replaces_changed_page_token(self) -> None:
        config = connection_config(pages=[{"id": "101", "name": "Page One", "page_access_token": PAGE_TOKEN_1}])
        fetched = {"pages": [{"id": "101", "name": "Page One", "page_access_token": PAGE_TOKEN_2, "tasks": []}], "errors": [], "complete": True, "examined": 1}
        with patch.object(meta, "fetch_accessible_pages", return_value=fetched):
            meta.sync_meta_pages("meta-1", config, persist=False)

        self.assertEqual(config["facebook"]["pages"][0]["page_access_token"], PAGE_TOKEN_2)

    def test_missing_page_is_marked_inaccessible_not_deleted(self) -> None:
        config = connection_config(pages=[{
            "id": "101",
            "name": "Page One",
            "page_access_token": PAGE_TOKEN_1,
            "meta_connection_id": "meta-1",
            "status": "active",
        }])
        fetched = {"pages": [], "errors": [], "complete": True, "examined": 0}
        with patch.object(meta, "fetch_accessible_pages", return_value=fetched):
            meta.sync_meta_pages("meta-1", config, persist=False)

        self.assertEqual(len(config["facebook"]["pages"]), 1)
        self.assertEqual(config["facebook"]["pages"][0]["status"], "inaccessible")

    def test_invalid_system_user_token_returns_invalid_diagnostic(self) -> None:
        config = connection_config()
        with patch.object(meta, "_graph_get", return_value={"data": {"is_valid": False, "error": {"message": "Invalid OAuth access token."}}}):
            result = meta.diagnose_system_user_token(SYSTEM_TOKEN, config)

        self.assertFalse(result["valid"])
        self.assertIn("Invalid OAuth", result["error"])
        self.assertNotIn(SYSTEM_TOKEN, json.dumps(result))

    def test_rate_limit_is_structured(self) -> None:
        body = io.BytesIO(json.dumps({"error": {"message": "Application request limit reached", "code": 4}}).encode("utf-8"))
        error = HTTPError("https://graph.facebook.com/v26.0/me", 429, "Too Many Requests", {}, body)
        self.addCleanup(error.close)
        with patch.object(meta, "urlopen", side_effect=error):
            with self.assertRaises(meta.MetaGraphError) as caught:
                meta._request_json("https://graph.facebook.com/v26.0/me", {"access_token": SYSTEM_TOKEN}, redaction_tokens=(SYSTEM_TOKEN,))

        self.assertEqual(caught.exception.kind, "rate_limit")
        self.assertEqual(caught.exception.http_status, 429)

    def test_public_responses_never_include_tokens(self) -> None:
        config = connection_config(pages=[{
            "id": "101",
            "name": "Page One",
            "page_access_token": PAGE_TOKEN_1,
            "meta_connection_id": "meta-1",
            "status": "active",
        }])
        payload = {
            "connections": meta.list_meta_connections(config),
            "pages": meta.list_meta_pages("meta-1", config),
        }
        serialized = json.dumps(payload)

        self.assertNotIn(SYSTEM_TOKEN, serialized)
        self.assertNotIn(PAGE_TOKEN_1, serialized)
        self.assertTrue(payload["connections"][0]["token_configured"])
        self.assertTrue(payload["pages"][0]["token_configured"])

    def test_existing_publisher_resolves_synced_page_access_token(self) -> None:
        config = connection_config(pages=[{
            "id": "101",
            "name": "Page One",
            "page_access_token": PAGE_TOKEN_2,
            "meta_connection_id": "meta-1",
            "status": "active",
        }])
        config["brand_routes"] = {"brand-a": {"facebook": {"page_id": "101"}}}
        page = facebook.facebook_upload_page(config, config["facebook"], {"brand": "brand-a"})

        self.assertEqual(facebook.facebook_page_access_token(config["facebook"], page), PAGE_TOKEN_2)

    def test_existing_publisher_rejects_inaccessible_page(self) -> None:
        config = connection_config(pages=[{
            "id": "101",
            "name": "Page One",
            "page_access_token": PAGE_TOKEN_1,
            "meta_connection_id": "meta-1",
            "status": "inaccessible",
        }])
        config["brand_routes"] = {"brand-a": {"facebook": {"page_id": "101"}}}

        with self.assertRaisesRegex(ValueError, "mất quyền truy cập"):
            facebook.facebook_upload_page(config, config["facebook"], {"brand": "brand-a"})


if __name__ == "__main__":
    unittest.main()
