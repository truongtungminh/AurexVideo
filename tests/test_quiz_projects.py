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


class QuizProjectTemplateTests(unittest.TestCase):
    def test_quiz_project_is_single_image_only(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-quiz-project-") as tmp:
            root = Path(tmp)
            projects_root = root / "projects"
            config_root = root / "config"
            with (
                patch.object(m3, "PROJECTS_ROOT", projects_root),
                patch.object(m3, "OUTPUT_ROOT", root / "output"),
                patch.object(m3, "CONFIG_ROOT", config_root),
                patch.object(m3, "PROJECT_DEFAULTS_PATH", config_root / "project-defaults.json"),
                patch.object(m3, "character_manifest", side_effect=FileNotFoundError),
            ):
                summary = m3.create_project({"id": "quiz-demo", "projectType": "quiz", "language": "vi"})
                topic = json.loads((projects_root / "quiz-demo" / "topic.json").read_text(encoding="utf-8"))

            self.assertEqual(m3.normalize_project_type("quiz"), "quiz")
            self.assertEqual(summary["projectType"], "quiz")
            self.assertEqual(topic["projectType"], "quiz")
            self.assertFalse(topic["baseComparisonEnabled"])
            self.assertEqual(len(topic["comparisons"]), 1)
            self.assertEqual(topic["comparisons"][0]["layout"], "single")
            self.assertEqual(topic["comparisons"][0]["rightLabel"], "")
            self.assertTrue(topic["comparisons"][0]["leftImage"])


if __name__ == "__main__":
    unittest.main()
