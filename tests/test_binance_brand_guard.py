from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_upload.binance as binance


class BinanceBrandGuardTests(unittest.TestCase):
    def test_uses_project_topic_brand_when_payload_brand_is_absent(self) -> None:
        with patch.object(binance, "require_project", return_value=Path("/tmp/project")), \
             patch.object(binance, "project_brand_from_topic", return_value="july"):
            self.assertEqual(binance.resolve_binance_upload_brand({"project": "demo"}), "july")

    def test_accepts_legacy_tintucbitcoin_project_as_july(self) -> None:
        with patch.object(binance, "require_project", return_value=Path("/tmp/project")), \
             patch.object(binance, "project_brand_from_topic", return_value="tintucbitcoin"):
            self.assertEqual(
                binance.resolve_binance_upload_brand({"project": "demo", "brand": "tintucbitcoin"}),
                "july",
            )

    def test_rejects_non_july_project_even_when_payload_claims_july(self) -> None:
        with patch.object(binance, "require_project", return_value=Path("/tmp/project")), \
             patch.object(binance, "project_brand_from_topic", return_value="other-brand"):
            with self.assertRaisesRegex(ValueError, "only available for Brand july"):
                binance.resolve_binance_upload_brand({"project": "demo", "brand": "july"})

    def test_rejects_non_july_payload_for_july_project(self) -> None:
        with patch.object(binance, "require_project", return_value=Path("/tmp/project")), \
             patch.object(binance, "project_brand_from_topic", return_value="july"):
            with self.assertRaisesRegex(ValueError, "only available for Brand july"):
                binance.resolve_binance_upload_brand({"project": "demo", "brand": "other-brand"})

    def test_upload_stops_before_video_or_social_calls_for_non_july_brand(self) -> None:
        with patch.object(binance, "require_project", return_value=Path("/tmp/project")), \
             patch.object(binance, "project_brand_from_topic", return_value="other-brand"), \
             patch.object(binance, "final_video_path_for_project") as video_path:
            with self.assertRaisesRegex(ValueError, "only available for Brand july"):
                binance.binance_upload_video({"project": "demo", "brand": "july", "duration": 10})
        video_path.assert_not_called()


if __name__ == "__main__":
    unittest.main()
