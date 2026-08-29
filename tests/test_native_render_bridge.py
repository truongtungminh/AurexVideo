from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from tools.native_render import (  # noqa: E402
    NativeRenderUnavailable,
    _prepare_manifest,
    audio_mux_command,
    resolve_native_manifest,
    resolve_native_scene,
    topic_scene_features,
)
from tools.native_scene import (  # noqa: E402
    _STYLE_PROFILES,
    _css_label_rects,
    compile_standard_topic,
)
from tools.render_project import (  # noqa: E402
    RenderBackendOutcome,
    character_specific_css_selectors,
    native_manifest_image_signatures,
    render_backend_report,
    render_native_backend,
    resolve_render_backend,
    requires_custom_intro_compatibility,
    requires_character_css_compatibility,
)
from tools.render_quality import get_render_profile  # noqa: E402


class NativeRenderBridgeTests(unittest.TestCase):
    def test_manifest_duration_updates_full_scene_layers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-native-test-") as temp:
            root = Path(temp)
            source = root / "native-render.json"
            destination = root / ".native-render-test.json"
            source.write_text(json.dumps({
                "schemaVersion": 1,
                "canvas": {
                    "width": 320,
                    "height": 576,
                    "frameRate": {"numerator": 24, "denominator": 1},
                    "frameCount": 48,
                    "backgroundColor": "#000000",
                },
                "layers": [
                    {"id": "full", "startFrame": 24, "endFrame": 48},
                    {"id": "short", "startFrame": 0, "endFrame": 24},
                ],
            }), encoding="utf-8")

            _prepare_manifest(source, destination, 1080, 1920, 30, 2.01)
            value = json.loads(destination.read_text(encoding="utf-8"))

            self.assertEqual(value["canvas"]["width"], 1080)
            self.assertEqual(value["canvas"]["frameCount"], 61)
            self.assertEqual(value["layers"][0]["startFrame"], 30)
            self.assertEqual(value["layers"][0]["endFrame"], 61)
            self.assertEqual(value["layers"][1]["endFrame"], 30)

    def test_manifest_path_stays_inside_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-native-test-") as temp:
            root = Path(temp)
            topic = root / "topic.json"
            topic.write_text(json.dumps({"nativeRenderManifest": "../outside.json"}), encoding="utf-8")

            with self.assertRaises(NativeRenderUnavailable):
                resolve_native_manifest(topic)

    def test_audio_mux_preserves_native_video_bitstream(self) -> None:
        command = audio_mux_command(
            ffmpeg=Path("/usr/local/bin/ffmpeg"),
            video=Path("native.mp4"),
            audio=Path("voice.wav"),
            output=Path("final.mp4"),
            audio_bitrate="256k",
        )

        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertIn("-shortest", command)

    def test_audio_mux_keeps_native_timestamps_linear(self) -> None:
        command = audio_mux_command(
            ffmpeg=Path("/usr/local/bin/ffmpeg"),
            video=Path("native.mp4"),
            audio=Path("voice.wav"),
            output=Path("final.mp4"),
            audio_bitrate="256k",
        )

        self.assertEqual(command[command.index("-use_editlist") + 1], "0")
        self.assertEqual(command[command.index("-avoid_negative_ts") + 1], "disabled")

    def test_native_image_bytes_participate_in_cache_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-native-test-") as temp:
            root = Path(temp)
            image = root / "card.png"
            manifest = root / "native-render.json"
            image.write_bytes(b"first")
            manifest.write_text(json.dumps({
                "layers": [{"type": "image", "source": "card.png"}],
            }), encoding="utf-8")
            first = native_manifest_image_signatures(manifest)
            image.write_bytes(b"second")
            second = native_manifest_image_signatures(manifest)
            self.assertNotEqual(first, second)

    def test_auto_is_default_and_backend_aliases_remain_supported(self) -> None:
        self.assertEqual(resolve_render_backend(None), "auto")
        self.assertEqual(resolve_render_backend("auto"), "auto")
        self.assertEqual(resolve_render_backend("native-core"), "native")
        self.assertEqual(resolve_render_backend("native"), "native")
        self.assertEqual(resolve_render_backend("compatibility"), "browser")

    def test_character_css_selector_detection_uses_effective_character_id(self) -> None:
        selectors = character_specific_css_selectors({"characterId": "popsy"})

        self.assertIn(".stage.character-popsy .topic-label-stack", selectors)
        self.assertNotIn(".stage.character-popsy .teacher", selectors)

    def test_character_css_guard_routes_auto_and_rejects_explicit_native(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-native-test-") as temp:
            stylesheet = Path(temp) / "style.css"
            stylesheet.write_text(
                "/* .stage.character-comment-only { color: red; } */\n"
                ".stage.character-brand-x .media-slot,\n"
                ".teacher-wrap.character-brand-x .teacher { color: blue; }\n",
                encoding="utf-8",
            )
            topic = {"brand": "brand-x"}

            self.assertEqual(
                character_specific_css_selectors(topic, stylesheet=stylesheet),
                (
                    ".stage.character-brand-x .media-slot",
                    ".teacher-wrap.character-brand-x .teacher",
                ),
            )
            self.assertTrue(requires_character_css_compatibility("auto", topic, stylesheet=stylesheet))
            self.assertFalse(requires_character_css_compatibility("browser", topic, stylesheet=stylesheet))
            with self.assertRaises(NativeRenderUnavailable) as raised:
                requires_character_css_compatibility("native", topic, stylesheet=stylesheet)

        self.assertEqual(raised.exception.reason, "character_css_parity_required")
        self.assertIn("Browser raster", str(raised.exception))

    def test_custom_intro_routes_auto_to_browser_and_rejects_native(self) -> None:
        topic = {"projectType": "custom", "intro": {"type": "color"}}

        self.assertTrue(requires_custom_intro_compatibility("auto", topic))
        self.assertFalse(requires_custom_intro_compatibility("browser", topic))
        with self.assertRaises(NativeRenderUnavailable) as raised:
            requires_custom_intro_compatibility("native", topic)

        self.assertEqual(raised.exception.reason, "custom_intro_browser_required")

    def test_standard_pose_video_text_scene_reports_unsupported_features(self) -> None:
        topic = {
            "leftLabel": "Hố đen",
            "rightLabel": "Sao neutron",
            "segments": [{"start": 0, "end": 1, "text": "Đây là hố đen."}],
            "poseTimeline": [{"time": 0, "pose": "pose-1"}],
            "poseAssets": {
                "pose-1": {"closed": "poses/pose-1.mp4", "speaking": "poses/pose-1.mp4"},
            },
            "leftImage": "assets/left.png",
            "rightImage": "assets/right.png",
        }

        self.assertEqual(
            topic_scene_features(topic),
            ("text", "karaoke", "pose-video", "pose-timeline", "comparison-layout"),
        )

    def test_standard_scene_without_native_contract_falls_back_explicitly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-native-test-") as temp:
            topic_path = Path(temp) / "topic.json"
            topic_path.write_text(json.dumps({
                "segments": [{"text": "Karaoke vẫn phải giữ nguyên."}],
                "poseTimeline": [{"time": 0, "pose": "pose-1"}],
                "poseAssets": {"pose-1": {"closed": "pose-1.mp4"}},
            }), encoding="utf-8")

            with self.assertRaises(NativeRenderUnavailable) as raised:
                resolve_native_scene(topic_path)

            self.assertEqual(raised.exception.reason, "native_scene_compile_requires_stage")

    def test_standard_topic_compiles_to_self_contained_scene_ir_v2(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-native-test-") as temp:
            root = Path(temp)
            (root / "assets").mkdir()
            (root / "poses").mkdir()
            (root / "assets/left.png").write_bytes(b"left")
            (root / "assets/right.png").write_bytes(b"right")
            (root / "poses/pose-1.mp4").write_bytes(b"video")
            topic_path = root / "topic.rendered.json"
            topic_path.write_text(json.dumps({
                "duration": 1.0,
                "segments": [{"start": 0, "end": 1, "text": "Đây là test."}],
                "poseTimeline": [{"time": 0, "pose": "pose-1"}],
                "poseAssets": {"pose-1": {"speaking": "poses/pose-1.mp4"}},
                "leftImage": "assets/left.png",
                "rightImage": "assets/right.png",
                "leftLabel": "Trái",
                "rightLabel": "Phải",
            }), encoding="utf-8")
            stage = root / ".stage"

            document = compile_standard_topic(
                topic_path,
                staging_dir=stage,
                resource_root=ENGINE_ROOT,
            )

            self.assertEqual(document["schemaVersion"], 2)
            self.assertEqual(
                {layer["type"] for layer in document["layers"]},
                {"image", "text", "video"},
            )
            for layer in document["layers"]:
                source = layer.get("source") or layer.get("fontSource")
                if source:
                    self.assertFalse(Path(source).is_absolute())
                    self.assertNotIn("..", Path(source).parts)
                    self.assertTrue((stage / source).is_file())

    def test_bietchichomet_label_box_matches_preview_css_anchor(self) -> None:
        profile = _STYLE_PROFILES["bietchichomet"]

        rect, sub_rect = _css_label_rects(
            profile,
            "left",
            label_text="Windows",
            sub_text="",
            show_sub_label=False,
            canvas_width=1080,
            canvas_height=1920,
        )

        self.assertIsNone(sub_rect)
        self.assertAlmostEqual(rect["x"], 0.030)
        self.assertAlmostEqual(rect["width"], 0.444)
        self.assertAlmostEqual(rect["y"] + rect["height"], 0.240)
        self.assertAlmostEqual(rect["height"], 54 * 0.98 / 1920)

    def test_single_image_scene_uses_one_centered_native_slot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-native-test-") as temp:
            root = Path(temp)
            (root / "assets").mkdir()
            (root / "assets/single.png").write_bytes(b"single")
            topic_path = root / "topic.rendered.json"
            topic_path.write_text(json.dumps({
                "duration": 1.0,
                "segments": [{"start": 0, "end": 1, "text": "Ảnh đơn."}],
                "baseComparisonEnabled": False,
                "comparisons": [{
                    "id": "single-image-example",
                    "layout": "single",
                    "startSentence": 1,
                    "leftLabel": "Ảnh đơn",
                    "leftImage": "assets/single.png",
                    "rightImage": "assets/ignored.png",
                }],
            }), encoding="utf-8")

            document = compile_standard_topic(
                topic_path,
                staging_dir=root / ".stage",
                resource_root=ENGINE_ROOT,
            )

            images = [layer for layer in document["layers"] if layer["type"] == "image"]
            self.assertEqual([layer["id"] for layer in images], ["comparison-000-single-image"])
            self.assertEqual(
                images[0]["rect"],
                {"x": 0.050, "y": 0.171, "width": 0.900, "height": 0.50625},
            )
            labels = [layer for layer in document["layers"] if layer["id"] == "comparison-000-single-label"]
            self.assertEqual(len(labels), 1)
            self.assertEqual(labels[0]["rect"]["x"], 0.050)
            self.assertEqual(labels[0]["rect"]["width"], 0.900)

    def test_inline_solid_image_scene_is_a_complete_native_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-native-test-") as temp:
            root = Path(temp)
            (root / "card.png").write_bytes(b"image fixture")
            topic_path = root / "topic.json"
            topic_path.write_text(json.dumps({
                "nativeRenderScene": {
                    "backgroundColor": "#102030",
                    "layers": [
                        {
                            "id": "accent",
                            "type": "solid",
                            "rect": {"x": 0, "y": 0, "width": 1, "height": 1},
                            "color": "#112233",
                        },
                        {
                            "id": "card",
                            "type": "image",
                            "rect": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
                            "source": "card.png",
                            "contentMode": "fit",
                        },
                    ],
                },
            }), encoding="utf-8")

            scene = resolve_native_scene(topic_path)

            self.assertEqual(scene.origin, "nativeRenderScene")
            self.assertEqual(scene.layer_types, ("image", "solid"))
            self.assertEqual(scene.document["schemaVersion"], 2)
            self.assertEqual(scene.document["canvas"]["backgroundColor"], "#102030")

    def test_inline_image_scene_rejects_parent_asset_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-native-test-") as temp:
            topic_path = Path(temp) / "topic.json"
            topic_path.write_text(json.dumps({
                "nativeRenderScene": {
                    "layers": [{
                        "id": "escape",
                        "type": "image",
                        "rect": {"x": 0, "y": 0, "width": 1, "height": 1},
                        "source": "../outside.png",
                    }],
                },
            }), encoding="utf-8")

            with self.assertRaises(NativeRenderUnavailable) as raised:
                resolve_native_scene(topic_path)

            self.assertEqual(raised.exception.reason, "native_image_path_unsafe")

    def test_auto_falls_back_when_native_process_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-native-test-") as temp:
            root = Path(temp)
            with patch(
                "tools.render_project.build_native_render_plan",
                side_effect=RuntimeError("simulated native failure"),
            ):
                outcome = render_native_backend(
                    backend="auto",
                    topic_path=root / "topic.rendered.json",
                    resource_root=root,
                    native_video=root / "native.mp4",
                    native_report=root / "native.report.json",
                    native_audio=root / "native.wav",
                    render_output=root / "output.mp4",
                    render_report_path=root / "output.report.json",
                    width=320,
                    height=576,
                    fps=30,
                    duration=2,
                    quality=get_render_profile("standard"),
                    token="test",
                )
            self.assertFalse(outcome.used_native)
            self.assertEqual(outcome.backend_used, "browser")
            self.assertTrue(outcome.fallback_reason.startswith("native_runtime_failed:"))

    def test_backend_report_contract_never_claims_fallback_is_native(self) -> None:
        report = render_backend_report(
            "auto",
            RenderBackendOutcome(
                backend_used="browser",
                fallback_reason="unsupported_scene_features:text,karaoke,pose-video",
                fallback_detail="Core MVP chỉ hỗ trợ solid/image.",
            ),
        )

        self.assertEqual(report["backend_requested"], "auto")
        self.assertEqual(report["backend_used"], "browser")
        self.assertEqual(
            report["fallback_reason"],
            "unsupported_scene_features:text,karaoke,pose-video",
        )
        self.assertEqual(report["capability_report"]["decision"], "fallback")


if __name__ == "__main__":
    unittest.main()
