from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from web_server import coerce_render_backend  # noqa: E402


class RenderBackendDefaultTests(unittest.TestCase):
    def test_api_defaults_to_browser(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUREXVIDEO_RENDER_BACKEND", None)
            self.assertEqual(coerce_render_backend(None), "browser")

    def test_legacy_backends_are_normalized_to_browser(self) -> None:
        self.assertEqual(coerce_render_backend("browser"), "browser")
        self.assertEqual(coerce_render_backend("auto"), "browser")
        self.assertEqual(coerce_render_backend("native"), "browser")
        self.assertEqual(coerce_render_backend("native-core"), "browser")
        self.assertEqual(coerce_render_backend("compatibility"), "browser")

    def test_primary_ui_and_client_default_to_browser(self) -> None:
        server_source = (ENGINE_ROOT / "web_server.py").read_text(encoding="utf-8")
        client_source = (ENGINE_ROOT / "web" / "render_page.js").read_text(encoding="utf-8")

        self.assertIn('<option value="browser" selected>Browser · giữ đúng CSS preview', server_source)
        self.assertNotIn('<option value="native"', server_source)
        self.assertIn("renderBackend: $('#renderBackend')?.value || 'browser'", client_source)


if __name__ == "__main__":
    unittest.main()
