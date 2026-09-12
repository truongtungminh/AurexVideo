from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_upload.config as social_config
import social_upload.youtube as youtube


class YoutubeOauthTests(unittest.TestCase):
    def setUp(self) -> None:
        with youtube.OAUTH_STATES_LOCK:
            youtube.OAUTH_STATES.clear()

    def tearDown(self) -> None:
        with youtube.OAUTH_STATES_LOCK:
            youtube.OAUTH_STATES.clear()

    def test_social_reconnect_can_start_without_a_project(self) -> None:
        config = {
            "youtube": {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "redirect_uri": "http://localhost:4173/api/social/youtube/callback",
            }
        }
        with patch.object(youtube, "read_social_config", return_value=config), \
             patch.object(youtube, "require_project") as require_project:
            url = youtube.start_youtube_oauth(
                "",
                brand="july",
                channel_id="UC-old",
            )

        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["client_id"], ["client-id"])
        self.assertEqual(query["redirect_uri"], ["http://localhost:4173/api/social/youtube/callback"])
        self.assertEqual(query["scope"], [youtube.YOUTUBE_OAUTH_SCOPE])
        state = query["state"][0]
        with youtube.OAUTH_STATES_LOCK:
            self.assertEqual(
                youtube.OAUTH_STATES[state],
                {
                    "project": "",
                    "brand": "july",
                    "channel_id": "UC-old",
                    "created_at": youtube.OAUTH_STATES[state]["created_at"],
                },
            )
        require_project.assert_not_called()

    def test_social_reconnect_replaces_route_after_oauth(self) -> None:
        config = {
            "brand_routes": {
                "july": {
                    "youtube": {
                        "channel_id": "UC-old",
                        "name": "Tin Tức Bitcoin",
                    }
                }
            },
            "youtube": {
                "client_id": "client-id",
                "client_secret": "client-secret",
                "channels": [
                    {
                        "id": "UC-old",
                        "title": "Tin Tức Bitcoin",
                        "tokens": {"refresh_token": "old-refresh"},
                    }
                ],
            },
        }
        with youtube.OAUTH_STATES_LOCK:
            youtube.OAUTH_STATES["state-social"] = {
                "project": "",
                "brand": "july",
                "channel_id": "UC-old",
                "created_at": 0,
            }

        with patch.object(youtube, "read_social_config", return_value=config), \
             patch.object(
                 youtube,
                 "youtube_exchange_code",
                 return_value={
                     "access_token": "new-access",
                     "refresh_token": "new-refresh",
                     "expires_in": 3600,
                 },
             ), \
             patch.object(
                 youtube,
                 "youtube_fetch_channel",
                 return_value={"id": "UC-new", "title": "Kênh mới"},
             ), \
             patch.object(youtube, "write_social_config"), \
             patch.object(social_config, "write_social_config"):
            result = youtube.finish_youtube_oauth(
                {"code": ["oauth-code"], "state": ["state-social"]}
            )

        self.assertEqual(result, "")
        self.assertEqual(config["brand_routes"]["july"]["youtube"]["channel_id"], "UC-new")
        self.assertEqual(config["brand_routes"]["july"]["youtube"]["name"], "Kênh mới")
        channels = {channel["id"]: channel for channel in config["youtube"]["channels"]}
        self.assertEqual(channels["UC-new"]["tokens"]["refresh_token"], "new-refresh")
        self.assertIn("UC-old", channels)


if __name__ == "__main__":
    unittest.main()
