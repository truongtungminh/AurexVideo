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
)
from tools.render_project import (  # noqa: E402
    native_manifest_image_signatures,
    render_native_backend,
    resolve_render_backend,
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

    def test_backend_defaults_to_browser_and_supports_native_alias(self) -> None:
        self.assertEqual(resolve_render_backend(None), "browser")
        self.assertEqual(resolve_render_backend("native-core"), "native")

    def test_auto_falls_back_when_native_process_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-native-test-") as temp:
            root = Path(temp)
            with patch(
                "tools.render_project.build_native_render_plan",
                side_effect=RuntimeError("simulated native failure"),
            ):
                used_native = render_native_backend(
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
            self.assertFalse(used_native)


if __name__ == "__main__":
    unittest.main()
