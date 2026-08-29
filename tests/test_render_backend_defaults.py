from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from web_server import coerce_render_backend  # noqa: E402
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

        self.assertIn('<option value="auto" selected>Auto · Native nhanh', server_source)
        self.assertIn('<option value="native">Aurex Render Core', server_source)
        self.assertIn("renderBackend: normalizeRenderBackend($('#renderBackend')?.value)", client_source)

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
                preferences = m3_backend.write_render_preferences({"renderBackend": "browser"})
                self.assertEqual(preferences["renderBackend"], "browser")
                self.assertEqual(
                    preferences["renderBackendPolicyVersion"],
                    m3_backend.RENDER_BACKEND_POLICY_VERSION,
                )


if __name__ == "__main__":
    unittest.main()
