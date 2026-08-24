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
    def test_api_defaults_to_auto(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUREXVIDEO_RENDER_BACKEND", None)
            self.assertEqual(coerce_render_backend(None), "auto")

    def test_explicit_browser_compatibility_remains_available(self) -> None:
        self.assertEqual(coerce_render_backend("browser"), "browser")
        self.assertEqual(coerce_render_backend("compatibility"), "browser")

    def test_primary_ui_and_client_default_to_auto(self) -> None:
        server_source = (ENGINE_ROOT / "web_server.py").read_text(encoding="utf-8")
        client_source = (ENGINE_ROOT / "web" / "render_page.js").read_text(encoding="utf-8")

        self.assertIn('<option value="auto" selected>Auto · Core-first', server_source)
        self.assertIn("renderBackend: $('#renderBackend')?.value || 'auto'", client_source)


if __name__ == "__main__":
    unittest.main()
