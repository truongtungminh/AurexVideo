from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

import web_server  # noqa: E402
from web_server import build_render_command, coerce_render_backend  # noqa: E402
import m3_backend  # noqa: E402


class RenderBackendDefaultTests(unittest.TestCase):
    def test_api_defaults_to_auto(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUREXVIDEO_RENDER_BACKEND", None)
            self.assertEqual(coerce_render_backend(None), "auto")

    def test_explicit_backends_and_aliases_are_preserved(self) -> None:
        self.assertEqual(coerce_render_backend("browser"), "browser")
        self.assertEqual(coerce_render_backend("auto"), "auto")
        self.assertEqual(coerce_render_backend("native"), "native")
        self.assertEqual(coerce_render_backend("native-core"), "native")
        self.assertEqual(coerce_render_backend("compatibility"), "browser")

    def test_primary_ui_and_client_default_to_auto(self) -> None:
        server_source = (ENGINE_ROOT / "web_server.py").read_text(encoding="utf-8")
        client_source = (ENGINE_ROOT / "web" / "render_page.js").read_text(encoding="utf-8")

        self.assertIn('<option value="auto" selected>Auto · Aurex Render Core ưu tiên', server_source)
        self.assertIn('<option value="native">Aurex Render Core', server_source)
        self.assertIn('data-engine="upload"', server_source)
        self.assertIn('id="uploadAudioFile"', server_source)
        self.assertIn('accept=".mp3,.wav,.mav', server_source)
        self.assertIn("'upload'", client_source)
        self.assertIn("renderBackend: normalizeRenderBackend($('#renderBackend')?.value)", client_source)

    def test_upload_engine_reuses_project_audio_upload_pipeline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-upload-render-test-") as temp:
            root = Path(temp)
            project = root / "demo"
            project.mkdir()
            (project / "topic.json").write_text("{}", encoding="utf-8")
            encoded = "ZmFrZS1tcDM="
            payload = {
                "project": "demo",
                "engine": "upload",
                "audio": {"name": "narration.mav", "data": encoded},
                "branding": False,
            }
            with patch.object(web_server, "PROJECT_ROOT", root), patch.object(
                web_server, "SOURCE_ROOT_IS_PROJECT", False
            ), patch.object(m3_backend, "PROJECTS_ROOT", root), patch.object(
                m3_backend, "media_duration", return_value=1.25
            ), patch.object(web_server, "trial_branding_required", return_value=False):
                command, engine = build_render_command(payload)

            self.assertEqual(engine, "upload")
            self.assertEqual(command[command.index("--engine") + 1], "upload")
            audio_path = Path(command[command.index("--audio") + 1])
            self.assertEqual(audio_path.parent.resolve(), (project / "audio").resolve())
            self.assertTrue(audio_path.name.startswith("voiceover-"))
            self.assertEqual(audio_path.suffix, ".mav")
            self.assertTrue(audio_path.is_file())
            self.assertIn("--no-branding", command)

    def test_progress_parser_recognizes_native_render_and_mux_stages(self) -> None:
        client_source = (ENGINE_ROOT / "web" / "render_page.js").read_text(encoding="utf-8")

        self.assertIn("Rendering with Aurex Render Core", client_source)
        self.assertIn("aurex-render render", client_source)
        self.assertIn("-c:v copy", client_source)

    def test_legacy_browser_preference_migrates_to_auto(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-preferences-test-") as temp:
            root = Path(temp)
            path = root / "project-defaults.json"
            path.write_text(
                '{"renderPreferences":{"renderBackend":"browser"}}',
                encoding="utf-8",
            )
            with patch.object(m3_backend, "PROJECT_DEFAULTS_PATH", path):
                preferences = m3_backend.read_render_preferences()
            self.assertEqual(preferences["renderBackend"], "auto")

    def test_versioned_backend_preference_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-preferences-test-") as temp:
            root = Path(temp)
            path = root / "project-defaults.json"
            with patch.object(m3_backend, "PROJECT_DEFAULTS_PATH", path), patch.object(
                m3_backend, "CONFIG_ROOT", root
            ):
                preferences = m3_backend.write_render_preferences({
                    "renderBackend": "browser",
                    "renderBackendPolicyVersion": 0,
                })
                self.assertEqual(preferences["renderBackend"], "browser")
                self.assertEqual(
                    preferences["renderBackendPolicyVersion"],
                    m3_backend.RENDER_BACKEND_POLICY_VERSION,
                )


if __name__ == "__main__":
    unittest.main()
