import unittest
from unittest.mock import patch

import social_upload.facebook as facebook


class FacebookConfigTests(unittest.TestCase):
    def test_update_page_config_persists_name(self):
        config = {
            "facebook": {
                "pages": [],
                "graph_version": "v26.0",
            }
        }
        with patch.object(facebook, "read_social_config", return_value=config), patch.object(facebook, "write_social_config") as write:
            result = facebook.update_facebook_page_config("123456", "x" * 24, "Biết Chi Cho Mệt")

        self.assertTrue(result["configured"])
        self.assertEqual(config["facebook"]["pages"][0]["name"], "Biết Chi Cho Mệt")
        self.assertEqual(config["facebook"]["active_page_id"], "123456")
        write.assert_called_once_with(config)

    def test_update_page_config_keeps_existing_name_when_omitted(self):
        config = {
            "facebook": {
                "pages": [{"id": "123456", "name": "Biết Chi Cho Mệt", "thumbnail": "old", "page_access_token": "old-token"}],
            }
        }
        with patch.object(facebook, "read_social_config", return_value=config), patch.object(facebook, "write_social_config"):
            facebook.update_facebook_page_config("123456", "x" * 24)

        self.assertEqual(config["facebook"]["pages"][0]["name"], "Biết Chi Cho Mệt")
        self.assertEqual(config["facebook"]["pages"][0]["thumbnail"], "old")


if __name__ == "__main__":
    unittest.main()
