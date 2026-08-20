from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from social_upload.config import (
    migrate_legacy_social_connections,
    resolve_social_brand_connection,
    social_brand_routes,
    social_brand_route_records,
    store_social_brand_connection,
)
from social_upload.r2 import merge_r2_config_values
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

    def test_same_public_account_cannot_be_registered_to_two_brands(self) -> None:
        cases = (
            ("instagram", {"ig_user_id": "17840000000000001", "access_token": "instagram-token-xxxxxxxxxxxxxxxxxxxx"}),
            ("tiktok", {"account_id": "acct-shared", "api_key": "zernio-key-xxxxxxxxxxxxxxxxxxxx", "base_url": "https://zernio.example/api/v1"}),
            ("threads", {"threads_user_id": "27990449530574492", "access_token": "threads-token-xxxxxxxxxxxxxxxxxxxx"}),
        )
        for platform, connection in cases:
            config: dict = {"brand_routes": {}}
            store_social_brand_connection(config, "popsy", platform, connection)
            public_id = next(value for key, value in connection.items() if key in {"ig_user_id", "account_id", "threads_user_id"})
            with self.assertRaisesRegex(ValueError, "đang thuộc brand popsy"):
                store_social_brand_connection(
                    config,
                    "aurex",
                    platform,
                    {**connection, next(key for key in ("ig_user_id", "account_id", "threads_user_id") if key in connection): public_id},
                )

    def test_environment_accounts_are_migrated_to_popsy_only(self) -> None:
        config: dict = {"brand_routes": {}}
        environment = {
            "INSTAGRAM_IG_USER_ID": "17840000000000003",
            "INSTAGRAM_ACCESS_TOKEN": "instagram-env-token-xxxxxxxxxxxxxxxxxxxx",
            "ZERNIO_TIKTOK_ACCOUNT_ID": "acct-env-popsy",
            "ZERNIO_API_KEY": "zernio-env-key-xxxxxxxxxxxxxxxxxxxx",
            "ZERNIO_BASE_URL": "https://zernio.example/api/v1",
            "THREADS_USER_ID": "27990449530574493",
            "THREADS_ACCESS_TOKEN": "threads-env-token-xxxxxxxxxxxxxxxxxxxx",
        }
        with patch.dict("os.environ", environment, clear=False):
            migrated, changed = migrate_legacy_social_connections(config)

        self.assertTrue(changed)
        self.assertEqual(
            migrated["instagram"]["connections"]["popsy-legacy"]["access_token"],
            environment["INSTAGRAM_ACCESS_TOKEN"],
        )
        self.assertEqual(
            migrated["zernio"]["connections"]["popsy-legacy"]["account_id"],
            environment["ZERNIO_TIKTOK_ACCOUNT_ID"],
        )
        self.assertEqual(
            migrated["threads"]["connections"]["popsy-legacy"]["threads_user_id"],
            environment["THREADS_USER_ID"],
        )
        with self.assertRaisesRegex(ValueError, "route chưa cấu hình"):
            resolve_social_brand_connection(migrated, "aurex", "instagram")

    def test_partial_r2_payload_keeps_existing_credentials(self) -> None:
        current = {
            "account_id": "account",
            "bucket": "media",
            "access_key_id": "key",
            "secret_access_key": "secret",
            "public_base_url": "https://media.example.com",
            "region": "auto",
            "object_prefix": "instagram",
            "retain_media": True,
        }
        merged = merge_r2_config_values(
            {"r2Bucket": "media", "r2PublicBaseUrl": "https://media.example.com"},
            current,
        )
        self.assertEqual(merged["account_id"], "account")
        self.assertEqual(merged["access_key_id"], "key")
        self.assertEqual(merged["secret_access_key"], "secret")
        self.assertTrue(merged["retain_media"])

    def test_global_updates_keep_brand_connection_collection(self) -> None:
        tiktok_config = {"zernio": {"connections": {"popsy": {"brand": "popsy"}}}}
        update_zernio_config("global-key", "global-account", config=tiktok_config, persist=False)
        self.assertIn("popsy", tiktok_config["zernio"]["connections"])

        threads_config = {"threads": {"connections": {"popsy": {"brand": "popsy"}}}}
        update_threads_config("123456789", "threads-token-xxxxxxxxxxxxxxxxxxxx", config=threads_config, persist=False)
        self.assertIn("popsy", threads_config["threads"]["connections"])


if __name__ == "__main__":
    unittest.main()
