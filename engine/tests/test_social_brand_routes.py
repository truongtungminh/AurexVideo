from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from social_upload.config import delete_social_brand_route


class DeleteSocialBrandRouteTests(unittest.TestCase):
    def test_deletes_only_the_matching_owned_connection_and_route(self) -> None:
        config = {
            "brand_routes": {
                "popsy": {"tiktok": {"connection_id": "popsy-tiktok"}},
                "july": {"tiktok": {"connection_id": "july-tiktok"}},
            },
            "zernio": {
                "connections": {
                    "popsy-tiktok": {"brand": "popsy", "account_id": "popsy-account", "api_key": "secret-popsy"},
                    "july-tiktok": {"brand": "july", "account_id": "july-account", "api_key": "secret-july"},
                },
            },
        }

        with patch("social_upload.config.write_social_config") as write_config:
            result = delete_social_brand_route("Popsy", "TikTok", "popsy-tiktok", config)

        self.assertTrue(result["connection_deleted"])
        self.assertNotIn("api_key", json.dumps(result))
        self.assertNotIn("popsy", config["brand_routes"])
        self.assertNotIn("popsy-tiktok", config["zernio"]["connections"])
        self.assertIn("july-tiktok", config["zernio"]["connections"])
        self.assertEqual(config["brand_routes"]["july"]["tiktok"]["connection_id"], "july-tiktok")
        write_config.assert_called_once_with(config)

    def test_unlinks_youtube_without_touching_global_account(self) -> None:
        config = {
            "brand_routes": {"popsy": {"youtube": {"channel_id": "channel-1"}}},
            "youtube": {"channels": [{"id": "channel-1", "client_secret": "global-secret"}]},
        }

        with patch("social_upload.config.write_social_config"):
            result = delete_social_brand_route("popsy", "youtube", config=config)

        self.assertFalse(result["connection_deleted"])
        self.assertNotIn("popsy", config["brand_routes"])
        self.assertEqual(config["youtube"]["channels"][0]["client_secret"], "global-secret")

    def test_rejects_missing_or_mismatched_routes_without_persisting(self) -> None:
        config = {"brand_routes": {"popsy": {"threads": {"connection_id": "popsy-threads"}}}}

        with patch("social_upload.config.write_social_config") as write_config:
            with self.assertRaisesRegex(ValueError, "không khớp route"):
                delete_social_brand_route("popsy", "threads", "other", config)
            with self.assertRaisesRegex(ValueError, "chưa cấu hình"):
                delete_social_brand_route("july", "threads", config=config)

        self.assertEqual(config["brand_routes"]["popsy"]["threads"]["connection_id"], "popsy-threads")
        write_config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
