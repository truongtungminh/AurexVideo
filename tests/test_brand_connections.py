from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from social_upload.config import (
    migrate_legacy_social_connections,
    resolve_social_brand_connection,
    social_brand_routes,
    social_brand_route_records,
    store_social_brand_connection,
)
from social_upload.threads import update_threads_config
from social_upload.tiktok import update_zernio_config


class BrandConnectionTests(unittest.TestCase):
    def test_migrates_legacy_accounts_to_popsy_without_exposing_secrets(self) -> None:
        config = {
            "instagram": {
                "ig_user_id": "17840000000000000",
                "access_token": "instagram-token-xxxxxxxxxxxxxxxxxxxx",
                "api_mode": "instagram_login",
                "graph_version": "v25.0",
            },
            "zernio": {
                "api_key": "zernio-key-xxxxxxxxxxxxxxxxxxxx",
                "account_id": "acct-popsy",
                "base_url": "https://zernio.example/api/v1",
            },
            "threads": {
                "threads_user_id": "27990449530574491",
                "access_token": "threads-token-xxxxxxxxxxxxxxxxxxxx",
                "graph_version": "v1.0",
            },
            "brand_routes": {},
        }

        migrated, changed = migrate_legacy_social_connections(config)

        self.assertTrue(changed)
        self.assertIn("instagram", social_brand_routes(migrated)["popsy"])
        self.assertIn("tiktok", social_brand_routes(migrated)["popsy"])
        self.assertIn("threads", social_brand_routes(migrated)["popsy"])
        routes = social_brand_route_records(migrated)
        self.assertEqual(set(routes["popsy"]), {"instagram", "tiktok", "threads"})
        self.assertNotIn("access_token", json.dumps(routes))
        self.assertNotIn("api_key", json.dumps(routes))
        self.assertEqual(
            resolve_social_brand_connection(migrated, "popsy", "tiktok")[1]["account_id"],
            "acct-popsy",
        )

    def test_brand_connections_are_isolated(self) -> None:
        config: dict = {"brand_routes": {}, "zernio": {}}
        popsy_id = store_social_brand_connection(
            config,
            "popsy",
            "tiktok",
            {"api_key": "popsy-key", "account_id": "acct-popsy", "base_url": "https://zernio.example/api/v1"},
            name="Popsy TikTok",
        )
        july_id = store_social_brand_connection(
            config,
            "july",
            "tiktok",
            {"api_key": "july-key", "account_id": "acct-july", "base_url": "https://zernio.example/api/v1"},
            name="July TikTok",
        )

        self.assertNotEqual(popsy_id, july_id)
        self.assertEqual(resolve_social_brand_connection(config, "popsy", "tiktok")[1]["account_id"], "acct-popsy")
        self.assertEqual(resolve_social_brand_connection(config, "july", "tiktok")[1]["account_id"], "acct-july")
        with self.assertRaisesRegex(ValueError, "route chưa cấu hình"):
            resolve_social_brand_connection(config, "other", "tiktok")

        with self.assertRaisesRegex(ValueError, "đang thuộc brand popsy"):
            store_social_brand_connection(
                config,
                "july",
                "tiktok",
                {"api_key": "replacement-key", "account_id": "acct-july-2", "base_url": "https://zernio.example/api/v1"},
                connection_id=popsy_id,
            )

    def test_global_updates_keep_brand_connection_collection(self) -> None:
        tiktok_config = {"zernio": {"connections": {"popsy": {"brand": "popsy"}}}}
        update_zernio_config("global-key", "global-account", config=tiktok_config, persist=False)
        self.assertIn("popsy", tiktok_config["zernio"]["connections"])

        threads_config = {"threads": {"connections": {"popsy": {"brand": "popsy"}}}}
        update_threads_config("123456789", "threads-token-xxxxxxxxxxxxxxxxxxxx", config=threads_config, persist=False)
        self.assertIn("popsy", threads_config["threads"]["connections"])


if __name__ == "__main__":
    unittest.main()
