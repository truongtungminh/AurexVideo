from __future__ import annotations

import json
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from social_upload import schedule
import social_upload.facebook as fb
import social_upload.youtube as yt

FUTURE_ISO = "2030-01-01T00:00:00Z"
FUTURE_UNIX = str(int(datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()))
FB_FUTURE_ISO = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
FB_FUTURE_UNIX = str(int(datetime.fromisoformat(FB_FUTURE_ISO.replace("Z", "+00:00")).timestamp()))

SOCIAL_CONFIG = {
    "youtube": {
        "client_id": "cid",
        "client_secret": "csec",
        "channels": [
            {
                "id": "UC123",
                "title": "Channel",
                "tokens": {"access_token": "tok", "expires_at": time.time() + 9999},
            }
        ],
        "active_channel_id": "UC123",
    },
    "facebook": {
        "pages": [{"id": "123", "page_access_token": "EAAfake"}],
        "active_page_id": "123",
        "graph_version": "v25.0",
    },
    "brand_routes": {
        "test-brand": {"youtube": {"channel_id": "UC123"}, "facebook": {"page_id": "123"}},
    },
}


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.headers = {"Location": "https://upload.example/up"} if isinstance(payload, dict) else {}

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        if isinstance(self.payload, dict):
            return json.dumps(self.payload).encode("utf-8")
        return bytes(self.payload)


class ScheduleHelperTests(unittest.TestCase):
    def test_parse_accepts_iso_with_offset_and_normalises_utc(self) -> None:
        iso = schedule.normalize_iso_datetime("2030-01-01T07:00:00+07:00")
        self.assertEqual(iso, "2030-01-01T00:00:00Z")

    def test_parse_accepts_unix_timestamp(self) -> None:
        self.assertEqual(schedule.normalize_iso_datetime(FUTURE_UNIX), FUTURE_ISO)

    def test_parse_accepts_all_payload_spellings(self) -> None:
        for key in ("scheduledPublishAt", "publishAt", "scheduled_publish_at"):
            self.assertEqual(schedule.parse_scheduled_publish_at({key: FUTURE_UNIX}), FUTURE_ISO)

    def test_parse_returns_none_without_schedule(self) -> None:
        self.assertIsNone(schedule.parse_scheduled_publish_at({"project": "demo"}))
        self.assertIsNone(schedule.parse_scheduled_publish_at({"scheduledPublishAt": ""}))

    def test_validate_rejects_too_soon(self) -> None:
        too_soon = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
        with self.assertRaises(ValueError):
            schedule.validate_schedule_window(too_soon, timedelta(minutes=10), platform="Facebook")

    def test_unix_timestamp_roundtrip(self) -> None:
        self.assertEqual(schedule.scheduled_unix_timestamp(FUTURE_ISO), int(FUTURE_UNIX))


class YouTubeScheduleTests(unittest.TestCase):
    def _run_upload(self, extra_payload: dict) -> tuple[dict, dict, dict]:
        captured: dict = {"init_body": None, "record": None}

        def fake_urlopen(request, timeout=60):
            if request.method == "POST":
                captured["init_body"] = json.loads(request.data.decode("utf-8"))
                return FakeResponse({})
            captured["upload_body"] = request.data
            return FakeResponse({"id": "vid123"})

        with patch.object(yt, "read_social_config", return_value=SOCIAL_CONFIG), \
            patch.object(yt, "final_video_path_for_project", return_value=Path("/tmp/video.mp4")), \
            patch.object(yt, "project_brand_from_topic", return_value="test-brand"), \
            patch.object(yt, "build_upload_metadata", return_value={"title": "T", "description": "D", "tags": ["tag"], "privacyStatus": "public"}), \
            patch.object(yt, "read_expected_video_bytes", return_value=b"video-bytes"), \
            patch.object(yt, "record_social_upload", side_effect=lambda *args: captured.__setitem__("record", args[2])), \
            patch.object(yt, "urlopen", side_effect=fake_urlopen):
            result = yt.youtube_upload_video({"project": "demo", **extra_payload})
        return result, captured, {"status": captured["init_body"]["status"]}

    def test_scheduled_upload_sends_publish_at_and_forces_private(self) -> None:
        result, captured, _ = self._run_upload({"scheduledPublishAt": FUTURE_ISO})
        status = captured["init_body"]["status"]
        self.assertEqual(status["privacyStatus"], "private")
        self.assertEqual(status["publishAt"], FUTURE_ISO)
        self.assertEqual(result["privacyStatus"], "private")
        self.assertEqual(result["scheduledPublishAt"], FUTURE_ISO)
        self.assertIn("lên lịch", result["message"])
        self.assertEqual(captured["record"]["scheduled_at"], FUTURE_ISO)

    def test_plain_upload_keeps_privacy_without_publish_at(self) -> None:
        result, captured, _ = self._run_upload({"privacyStatus": "unlisted"})
        status = captured["init_body"]["status"]
        self.assertEqual(status["privacyStatus"], "unlisted")
        self.assertNotIn("publishAt", status)
        self.assertEqual(result["scheduledPublishAt"], "")

    def test_scheduled_upload_rejects_past_time(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with self.assertRaises(ValueError):
            self._run_upload({"scheduledPublishAt": past})


class FacebookScheduleTests(unittest.TestCase):
    def _run_upload(self, extra_payload: dict) -> tuple[dict, list[tuple[str, dict]]]:
        form_calls: list[tuple[str, dict]] = []

        def fake_form(url: str, fields: dict) -> dict:
            form_calls.append((url, fields))
            if fields.get("upload_phase") == "start":
                return {"video_id": "vid1"}
            return {"post_id": "post_1"}

        def fake_urlopen(request, timeout=600) -> FakeResponse:
            return FakeResponse({"success": True})

        with patch.object(fb, "read_social_config", return_value=SOCIAL_CONFIG), \
            patch.object(fb, "final_video_path_for_project", return_value=Path("/tmp/video.mp4")), \
            patch.object(fb, "project_brand_from_topic", return_value="test-brand"), \
            patch.object(fb, "build_upload_metadata", return_value={"title": "T", "description": "D", "tags": [], "facebookCaption": "C", "facebookVideoState": "PUBLISHED"}), \
            patch.object(fb, "read_expected_video_bytes", return_value=b"video-bytes"), \
            patch.object(fb, "facebook_caption_for_project", return_value=("caption", "https://src.example")), \
            patch.object(fb, "record_social_upload", return_value={}), \
            patch.object(fb, "http_form_request", side_effect=fake_form), \
            patch.object(fb, "urlopen", side_effect=fake_urlopen):
            result = fb.facebook_upload_video({"project": "demo", **extra_payload})
        return result, form_calls

    def test_scheduled_upload_sends_scheduled_state_and_time(self) -> None:
        result, calls = self._run_upload({"scheduledPublishAt": FB_FUTURE_ISO})
        finish = next(fields for _, fields in calls if fields.get("upload_phase") == "finish")
        self.assertEqual(finish["video_state"], "SCHEDULED")
        self.assertEqual(finish["scheduled_publish_time"], FB_FUTURE_UNIX)
        self.assertEqual(result["video_state"], "SCHEDULED")
        self.assertEqual(result["scheduledPublishAt"], FB_FUTURE_ISO)
        self.assertIn("lên lịch", result["message"])

    def test_plain_upload_has_no_schedule_fields(self) -> None:
        result, calls = self._run_upload({})
        finish = next(fields for _, fields in calls if fields.get("upload_phase") == "finish")
        self.assertEqual(finish["video_state"], "PUBLISHED")
        self.assertNotIn("scheduled_publish_time", finish)
        self.assertEqual(result["scheduledPublishAt"], "")

    def test_scheduled_upload_rejects_time_under_10_minutes(self) -> None:
        too_soon = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        with self.assertRaises(ValueError):
            self._run_upload({"scheduledPublishAt": too_soon})

    def test_scheduled_state_requires_time(self) -> None:
        with self.assertRaises(ValueError):
            self._run_upload({"facebookVideoState": "SCHEDULED"})


if __name__ == "__main__":
    unittest.main()
