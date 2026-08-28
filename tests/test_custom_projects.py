from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

import m3_backend as m3  # noqa: E402


class CustomProjectSchemaTests(unittest.TestCase):
    def test_custom_intro_is_fixed_to_three_seconds_and_normalized(self) -> None:
        intro = m3.normalize_custom_intro({
            "type": "image",
            "backgroundImage": "assets/intro.png",
            "logo": "assets/logo.webp",
            "title": "  Bitcoin\tđối mặt 80.000 USD  ",
            "color": "#ABC",
            "logoScale": 9,
            "titleSize": 0,
        })
        self.assertEqual(intro["type"], "media")
        self.assertEqual(intro["duration"], 3.0)
        self.assertEqual(intro["media"], "assets/intro.png")
        self.assertEqual(intro["mediaType"], "image")
        self.assertEqual(intro["logo"], "assets/logo.webp")
        self.assertEqual(intro["title"], "Bitcoin đối mặt 80.000 USD")
        self.assertEqual(intro["color"], "#aabbcc")
        self.assertEqual(intro["logoScale"], 1.6)
        self.assertEqual(intro["titleSize"], 0.6)

    def test_custom_intro_rejects_unsupported_media_extension(self) -> None:
        with self.assertRaises(ValueError):
            m3.normalize_custom_intro({"type": "media", "media": "assets/intro.exe"})

    def test_default_custom_slide_uses_vertical_stage_layout(self) -> None:
        slide = m3.default_custom_slide()
        text = next(layer for layer in slide["layers"] if layer["type"] == "text")
        media = next(layer for layer in slide["layers"] if layer["type"] == "image")
        self.assertEqual((text["x"], text["y"], text["w"], text["h"]), (0, 20, 100, 12))
        self.assertEqual((media["x"], media["y"], media["w"], media["h"]), (0, 0, 100, 100))

    def test_legacy_custom_media_layout_migrates_to_full_vertical_frame(self) -> None:
        slides = m3.normalize_custom_slides([
            {"layers": [{"type": "image", "src": "assets/example.png", "x": 0, "y": 34, "w": 100, "h": 32}]},
        ], 1)
        media = next(layer for layer in slides[0]["layers"] if layer["type"] == "image")
        self.assertEqual((media["x"], media["y"], media["w"], media["h"]), (0, 0, 100, 100))

    def test_custom_slide_normalization_clamps_and_removes_unknown_layers(self) -> None:
        slides = m3.normalize_custom_slides([
            {
                "id": "slide one!", "startSentence": 99, "enterEffect": "untrusted",
                "layers": [
                    {"id": "text?", "type": "text", "text": "hello" * 80, "x": -2, "y": 96, "w": 80, "h": 20, "color": "bad", "fontSize": 99},
                    {"type": "script", "text": "must not persist"},
                ],
            },
        ], 2)
        self.assertEqual(slides[0]["id"], "slide-one")
        self.assertEqual(slides[0]["startSentence"], 2)
        self.assertEqual(slides[0]["enterEffect"], "fade")
        self.assertEqual(len(slides[0]["layers"]), 1)
        layer = slides[0]["layers"][0]
        self.assertEqual(layer["type"], "text")
        self.assertEqual(layer["x"], 0)
        self.assertEqual(layer["y"], 92)
        self.assertEqual(layer["h"], 8)
        self.assertEqual(layer["color"], "#090909")
        self.assertEqual(layer["fontSize"], 2)

    def test_save_custom_topic_persists_type_and_slides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "demo"
            project.mkdir()
            (project / "assets").mkdir()
            (project / "assets" / "placeholder-left.svg").write_text("<svg/>", encoding="utf-8")
            initial = {
                "id": "demo", "brand": "Aurex", "projectType": "custom", "leftLabel": "A", "rightLabel": "B",
                "leftImage": "assets/placeholder-left.svg", "rightImage": "assets/placeholder-left.svg", "voiceover": "audio/silence.wav",
                "duration": 3, "segments": [{"start": 0, "end": 3, "text": "Line"}], "characterId": "human-presenter",
                "poseAssets": m3.DEFAULT_POSE_ASSETS, "poseLabels": m3.DEFAULT_POSE_LABELS, "poseTimeline": [{"time": 0, "pose": "question"}],
                "sfx": {}, "poseSfx": {}, "slides": [],
            }
            (project / "topic.json").write_text(json.dumps(initial), encoding="utf-8")
            with patch.object(m3, "PROJECTS_ROOT", root), patch.object(m3, "remember_project_defaults"), patch.object(m3, "character_pose_config", return_value=(m3.DEFAULT_POSE_ASSETS, m3.DEFAULT_POSE_LABELS)):
                result = m3.save_topic("demo", {
                    **initial,
                    "projectType": "custom",
                    "slides": [{"layers": [{"type": "text", "text": "Overlay"}]}],
                    "intro": {"type": "color", "color": "#123456", "title": "Opening"},
                })
            persisted = json.loads((project / "topic.json").read_text(encoding="utf-8"))
            self.assertEqual(result["projectType"], "custom")
            self.assertEqual(persisted["projectType"], "custom")
            self.assertFalse(persisted["baseComparisonEnabled"])
            self.assertEqual(persisted["slides"][0]["layers"][0]["text"], "Overlay")
            self.assertEqual(persisted["intro"]["duration"], 3.0)
            self.assertEqual(persisted["intro"]["title"], "Opening")


if __name__ == "__main__":
    unittest.main()
