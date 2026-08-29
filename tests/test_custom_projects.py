from __future__ import annotations

import base64
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
            "mediaType": "video",
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

    def test_intro_upload_accepts_video_media_and_logo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "demo").mkdir()
            (root / "demo" / "topic.json").write_text("{}", encoding="utf-8")
            encoded = base64.b64encode(b"test-media").decode("ascii")
            with patch.object(m3, "PROJECTS_ROOT", root):
                media = m3.decode_upload("demo", {"kind": "introMedia", "name": "opening.mp4", "data": encoded})
                logo = m3.decode_upload("demo", {"kind": "introLogo", "name": "logo.png", "data": encoded})

            self.assertEqual(media["mediaType"], "video")
            self.assertTrue((root / "demo" / media["path"]).is_file())
            self.assertEqual(logo["kind"], "introLogo")
            self.assertTrue((root / "demo" / logo["path"]).is_file())

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

    def test_custom_editor_defaults_are_portable_and_applied_to_new_custom_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects_root = root / "projects"
            config_root = root / "config"
            defaults_path = config_root / "project-defaults.json"
            with (
                patch.object(m3, "PROJECTS_ROOT", projects_root),
                patch.object(m3, "CONFIG_ROOT", config_root),
                patch.object(m3, "PROJECT_DEFAULTS_PATH", defaults_path),
                patch.object(m3, "character_manifest", side_effect=FileNotFoundError),
            ):
                m3.create_project({"id": "source-project", "projectType": "custom"})
                source_dir = projects_root / "source-project"
                logo = source_dir / "assets" / "intro-logo.png"
                media = source_dir / "assets" / "intro-media.webp"
                logo.write_bytes(b"logo-bytes")
                media.write_bytes(b"media-bytes")
                source = m3.read_topic("source-project")
                source["intro"] = {
                    "type": "media", "color": "#123456", "media": "assets/intro-media.webp",
                    "mediaZoom": 1.6, "mediaX": 8, "mediaY": -4, "logo": "assets/intro-logo.png",
                    "logoScale": 1.3, "title": "Reusable opening", "titleColor": "#fedcba", "titleSize": 1.2,
                }
                source["karaokeColor"] = "#112233"
                source["karaokeActiveColor"] = "#445566"
                source["karaokeSize"] = 1.35
                slide = source["slides"][0]
                slide["enterEffect"] = "rise"
                text = next(layer for layer in slide["layers"] if layer["type"] == "text")
                text.update({"font": '"Playfair Display", Georgia, serif', "color": "#654321", "fontSize": 1.45})
                m3.save_topic("source-project", source)

                defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
                preset = defaults["customEditorDefaults"]
                self.assertEqual(preset["intro"]["color"], "#123456")
                self.assertEqual(preset["intro"]["titleColor"], "#fedcba")
                self.assertEqual(preset["slide"]["enterEffect"], "rise")
                self.assertEqual(preset["slide"]["text"]["font"], '"Playfair Display", Georgia, serif')
                self.assertEqual(preset["subtitle"]["color"], "#112233")
                defaults_assets = config_root / "project-defaults-assets"
                self.assertTrue((defaults_assets / preset["intro"]["logo"]).is_file())
                self.assertTrue((defaults_assets / preset["intro"]["media"]).is_file())

                m3.create_project({"id": "target-project", "projectType": "custom"})
                target_dir = projects_root / "target-project"
                target = json.loads((target_dir / "topic.json").read_text(encoding="utf-8"))
                target_text = next(layer for layer in target["slides"][0]["layers"] if layer["type"] == "text")
                self.assertEqual(target["intro"]["color"], "#123456")
                self.assertEqual(target["intro"]["titleColor"], "#fedcba")
                self.assertEqual(target["intro"]["title"], "Reusable opening")
                self.assertEqual(target["slides"][0]["enterEffect"], "rise")
                self.assertEqual(target_text["font"], '"Playfair Display", Georgia, serif')
                self.assertEqual(target_text["color"], "#654321")
                self.assertEqual(target["karaokeColor"], "#112233")
                self.assertEqual(target["karaokeActiveColor"], "#445566")
                self.assertEqual(target["karaokeSize"], 1.35)
                self.assertEqual((target_dir / target["intro"]["logo"]).read_bytes(), b"logo-bytes")
                self.assertEqual((target_dir / target["intro"]["media"]).read_bytes(), b"media-bytes")

                m3.create_project({"id": "comparison-project", "projectType": "comparison"})
                comparison = json.loads((projects_root / "comparison-project" / "topic.json").read_text(encoding="utf-8"))
                self.assertEqual(comparison["slides"], [])
                self.assertEqual(comparison["intro"], m3.default_custom_intro())
                m3.remember_project_defaults("comparison-project", comparison)
                self.assertEqual(
                    json.loads(defaults_path.read_text(encoding="utf-8"))["customEditorDefaults"],
                    preset,
                )

                remembered_logo = preset["intro"]["logo"]
                remembered_media = preset["intro"]["media"]
                source["intro"].update({"type": "color", "logo": "", "media": ""})
                m3.save_topic("source-project", source)
                cleared = json.loads(defaults_path.read_text(encoding="utf-8"))["customEditorDefaults"]
                self.assertEqual(cleared["intro"]["logo"], "")
                self.assertEqual(cleared["intro"]["media"], "")
                self.assertFalse((defaults_assets / remembered_logo).exists())
                self.assertFalse((defaults_assets / remembered_media).exists())
                self.assertTrue(logo.is_file())
                self.assertTrue(media.is_file())


if __name__ == "__main__":
    unittest.main()
