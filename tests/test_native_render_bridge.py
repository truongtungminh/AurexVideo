from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from tools.native_render import (  # noqa: E402
    NativeRenderUnavailable,
    _prepare_manifest,
    audio_mux_command,
    resolve_native_manifest,
)
from tools.render_project import resolve_render_backend  # noqa: E402


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
                    "frameRate": {"numerator": 30, "denominator": 1},
                    "frameCount": 60,
                    "backgroundColor": "#000000",
                },
                "layers": [{"id": "full", "endFrame": 60}],
            }), encoding="utf-8")

            _prepare_manifest(source, destination, 1080, 1920, 30, 2.01)
            value = json.loads(destination.read_text(encoding="utf-8"))

            self.assertEqual(value["canvas"]["width"], 1080)
            self.assertEqual(value["canvas"]["frameCount"], 61)
            self.assertEqual(value["layers"][0]["endFrame"], 61)

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

    def test_backend_defaults_to_browser_and_supports_native_alias(self) -> None:
        self.assertEqual(resolve_render_backend(None), "browser")
        self.assertEqual(resolve_render_backend("native-core"), "native")


if __name__ == "__main__":
    unittest.main()
