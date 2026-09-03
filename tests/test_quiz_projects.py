from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

import m3_backend as m3  # noqa: E402


class NewProjectPageRegressionTests(unittest.TestCase):
    def test_page_declares_project_type_before_quiz_sync_function(self) -> None:
        source = (ENGINE_ROOT / "webui" / "new-project.html").read_text(encoding="utf-8")
        self.assertLess(source.index("const projectTypeInput"), source.index("const syncQuizProjectType"))
        self.assertEqual(source.count("const syncQuizProjectType"), 1)

    def test_page_has_a_safe_character_library_failure_state(self) -> None:
        source = (ENGINE_ROOT / "webui" / "new-project.html").read_text(encoding="utf-8")
        self.assertIn("Không tải được thư viện", source)
        self.assertIn("characterSelect.disabled=true", source)

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
            self.assertEqual(topic["comparisons"][0]["rightImage"], "")
            self.assertTrue(topic["comparisons"][0]["leftImage"])

    def test_quiz_save_keeps_one_single_image_scene_and_clears_right_asset(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-quiz-save-") as tmp:
            root = Path(tmp)
            project = root / "quiz-demo"
            project.mkdir(parents=True)
            (project / "topic.json").write_text(json.dumps({
                "id": "quiz-demo",
                "projectType": "quiz",
                "brand": "Aurex",
                "leftLabel": "Ảnh Quiz",
                "rightLabel": "",
                "leftImage": "assets/placeholder-left.svg",
                "rightImage": "",
                "voiceover": "audio/silence.wav",
                "duration": 1,
                "segments": [{"start": 0, "end": 1, "text": "Quiz"}],
                "comparisons": [{
                    "id": "quiz-image-1",
                    "layout": "single",
                    "startSentence": 1,
                    "leftLabel": "Ảnh Quiz",
                    "rightLabel": "",
                    "leftImage": "assets/placeholder-left.svg",
                    "rightImage": "",
                }],
                "baseComparisonEnabled": False,
            }), encoding="utf-8")
            with patch.object(m3, "PROJECTS_ROOT", root):
                saved = m3.save_topic("quiz-demo", {
                    "projectType": "quiz",
                    "brand": "Aurex",
                    "leftLabel": "Ảnh Quiz",
                    "rightLabel": "",
                    "leftImage": "assets/placeholder-left.svg",
                    "rightImage": "assets/ignored-right.svg",
                    "voiceover": "audio/silence.wav",
                    "duration": 1,
                    "segments": [{"start": 0, "end": 1, "text": "Quiz"}],
                    "comparisons": [
                        {"id": "one", "layout": "pair", "startSentence": 1, "leftLabel": "A", "rightLabel": "B", "leftImage": "assets/a.svg", "rightImage": "assets/b.svg"},
                        {"id": "two", "layout": "single", "startSentence": 1, "leftLabel": "B", "leftImage": "assets/b.svg"},
                    ],
                })

            self.assertEqual(saved["projectType"], "quiz")
            self.assertFalse(saved["baseComparisonEnabled"])
            self.assertEqual(len(saved["comparisons"]), 1)
            self.assertEqual(saved["comparisons"][0]["layout"], "single")
            self.assertEqual(saved["comparisons"][0]["rightImage"], "")

    def test_quiz_is_listed_and_editor_hides_pair_controls(self) -> None:
        new_project = (ENGINE_ROOT / "webui" / "new-project.html").read_text(encoding="utf-8")
        editor = (ENGINE_ROOT / "webui" / "editor.js").read_text(encoding="utf-8")
        self.assertIn('data-project-type="quiz"', new_project)
        self.assertIn("projectTypeInput.value==='quiz'", new_project)
        self.assertIn("editor-mode-quiz", editor)
        self.assertIn("addComparisonButton.hidden", editor)


if __name__ == "__main__":
    unittest.main()
