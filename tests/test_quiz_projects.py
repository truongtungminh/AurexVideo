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
from tools.render_project import (  # noqa: E402
    create_quiz_segment_voiceover,
    quiz_audio_insertions,
    quiz_segment_timeline,
    quiz_segments_after_audio_pause,
)


class NewProjectPageRegressionTests(unittest.TestCase):
    def test_quiz_audio_insertions_hold_each_answer_for_one_second(self) -> None:
        topic = {
            "projectType": "quiz",
            "quizAnswerDelay": 5,
            "duration": 14,
            "segments": [
                {"start": 0, "end": 4, "text": "Question 1"},
                {"start": 4.5, "end": 7, "text": "Answer 1"},
                {"start": 7, "end": 10, "text": "Question 2"},
                {"start": 11, "end": 13, "text": "Answer 2"},
            ],
        }
        self.assertEqual(quiz_audio_insertions(topic, 1, 14), [(4, 4.5), (7, 1.0), (10, 4.0)])
        self.assertEqual(
            quiz_audio_insertions({"projectType": "comparison", "segments": topic["segments"]}, 1),
            [],
        )
        shifted = quiz_segments_after_audio_pause(topic, 14, 1)
        self.assertEqual((shifted[0]["start"], shifted[0]["end"]), (0.0, 4.0))
        self.assertEqual(shifted[2]["start"], 12.5)

    def test_quiz_segment_timeline_is_built_from_each_clip_duration(self) -> None:
        topic = {
            "projectType": "quiz",
            "quizAnswerDelay": 5,
            "segments": [
                {"text": "Question 1"},
                {"text": "Answer 1"},
                {"text": "Question 2"},
                {"text": "Answer 2"},
                {"text": "CTA"},
            ],
        }
        timeline, duration = quiz_segment_timeline(topic, [1.2, 0.8, 1.4, 0.9, 1.1])
        self.assertEqual([(row["start"], row["end"]) for row in timeline], [
            (0.0, 1.2),
            (6.2, 7.0),
            (8.0, 9.4),
            (14.4, 15.3),
            (16.3, 17.4),
        ])
        self.assertEqual(duration, 17.4)

    def test_quiz_maziao_uses_sentence_capable_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-quiz-segment-tts-") as tmp:
            project = Path(tmp)
            topic_path = project / "topic.json"
            topic_path.write_text(json.dumps({
                "projectType": "quiz",
                "segments": [
                    {"start": 0, "end": 2, "text": "Câu hỏi ngắn?"},
                    {"start": 2, "end": 4, "text": "Đáp án ngắn."},
                ],
            }), encoding="utf-8")
            args = type("Args", (), {
                "engine": "maziao",
                "voice": "OncoinX",
                "tts_config_json": "",
                "speed": 1.0,
                "volume": 1.0,
            })()
            rendered_engines = []

            def fake_create_voiceover(segment_args, _project, _topic_path, _token):
                rendered_engines.append((segment_args.engine, segment_args.voice))
                return project / f"segment-{len(rendered_engines)}.wav"

            with patch("tools.render_project.create_voiceover", side_effect=fake_create_voiceover), \
                patch("tools.render_project.build_quiz_segment_audio", return_value=(project / "mix.wav", [])):
                create_quiz_segment_voiceover(args, project, topic_path, "token")

            self.assertEqual(len(rendered_engines), 2)
            self.assertTrue(all(engine == "vieneu" for engine, _ in rendered_engines))
            self.assertTrue(all(voice == "chautinhtri" for _, voice in rendered_engines))

    def test_quiz_countdown_sound_is_configured_for_project_render(self) -> None:
        topic = json.loads(
            (Path(__file__).resolve().parents[1] / "../studio/project/quizzz/topic.json").resolve().read_text(encoding="utf-8")
        )
        self.assertTrue((topic_path := topic.get("quizCountdownSound", "audio/quiz-countdown.wav")))
        self.assertEqual(topic_path, "audio/quiz-countdown.wav")
        renderer = (ENGINE_ROOT / "tools" / "render_demo.py").read_text(encoding="utf-8")
        self.assertNotIn("silenceremove=start_periods=1", renderer)

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
            self.assertEqual(topic["quizCountdownSound"], "audio/quiz-countdown.wav")
            self.assertTrue((projects_root / "quiz-demo" / "audio" / "quiz-countdown.wav").is_file())
            self.assertEqual(topic["poseSfx"], {pose: "" for pose in topic["poseAssets"]})

    def test_quizz_default_pose_sequence_and_no_sound_contract(self) -> None:
        self.assertEqual(
            m3.default_pose_sequence("quizz", {f"pose-{index}": {} for index in range(1, 4)}),
            ["pose-1", "pose-2", "pose-1", "pose-2", "pose-1", "pose-2", "pose-3"],
        )
        editor = (ENGINE_ROOT / "webui" / "editor.js").read_text(encoding="utf-8")
        self.assertIn('quizz: ["pose-1", "pose-2", "pose-1", "pose-2", "pose-1", "pose-2", "pose-3"]', editor)
        self.assertIn('if (String(state.topic?.projectType || "").toLowerCase() === "quiz")', editor)

    def test_quiz_v2_requires_three_items_with_three_options(self) -> None:
        valid = [
            {"question": f"Question {index}", "options": ["A", "B", "C"], "correct_index": index % 3}
            for index in range(3)
        ]
        self.assertEqual(m3.normalize_quiz_items(valid)[1]["correct_index"], 1)
        with self.assertRaisesRegex(ValueError, "đúng 3 lựa chọn"):
            m3.normalize_quiz_items([{"question": "Only", "options": ["A", "B"], "correct_index": 0}] * 3)
        with self.assertRaisesRegex(ValueError, "0, 1 hoặc 2"):
            m3.normalize_quiz_items([{"question": "Q", "options": ["A", "B", "C"], "correct_index": 3}] * 3)

    def test_quiz_v2_hides_presenter_layer(self) -> None:
        styles = (ENGINE_ROOT / "style.css").read_text(encoding="utf-8")
        self.assertIn(".stage.quiz-text-only .teacher-wrap { display: none !important; }", styles)

    def test_editor_parses_five_line_quiz_tts_blocks(self) -> None:
        editor = (ENGINE_ROOT / "webui" / "editor.js").read_text(encoding="utf-8")
        self.assertIn("function quizItemsFromScript(lines = scriptLines())", editor)
        self.assertIn("Đáp án chính xác là|đáp án đúng là|correct answer is", editor)
        self.assertIn("quizItems: isQuizProject() ? (parsedQuizItems || state.topic.quizItems)", editor)

    def test_quiz_v2_tts_timeline_pauses_after_three_options(self) -> None:
        topic = {"projectType": "quiz", "quizAnswerDelay": 5, "quizItems": [{}, {}, {}], "segments": [{"text": str(i)} for i in range(15)]}
        timeline, duration = quiz_segment_timeline(topic, [1.0] * 15)
        self.assertEqual(timeline[3]["end"], 4.0)
        self.assertEqual(timeline[4]["start"], 9.0)
        self.assertEqual(timeline[5]["start"], 10.0)
        self.assertEqual(duration, 25.0)

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

    def test_quiz_keeps_primary_image_slot_visible_when_base_comparison_is_disabled(self) -> None:
        editor = (ENGINE_ROOT / "webui" / "editor.js").read_text(encoding="utf-8")
        styles = (ENGINE_ROOT / "webui" / "styles.css").read_text(encoding="utf-8")
        self.assertIn('block.hidden = !isQuizProject() && !baseComparisonEnabled();', editor)
        self.assertIn('.editor-mode-quiz .comparison-block-primary,', styles)
        self.assertIn('.editor-mode-quiz .comparison-list,', styles)

    def test_quiz_normalizes_answer_and_uses_five_second_delay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-quiz-answer-") as tmp:
            root = Path(tmp)
            project = root / "quiz-demo"
            project.mkdir(parents=True)
            (project / "topic.json").write_text(json.dumps({
                "id": "quiz-demo", "projectType": "quiz", "brand": "Aurex",
                "leftLabel": "Ảnh Quiz", "rightLabel": "", "leftImage": "assets/placeholder-left.svg",
                "rightImage": "", "voiceover": "audio/silence.wav", "duration": 8,
                "segments": [{"start": 0, "end": 4, "text": "Question"}, {"start": 4, "end": 8, "text": "Đáp án là cái hố."}],
                "comparisons": [{"id": "quiz-image-1", "layout": "single", "startSentence": 1,
                    "leftLabel": "Ảnh Quiz", "rightLabel": "", "leftImage": "assets/placeholder-left.svg", "rightImage": ""}],
                "baseComparisonEnabled": False,
            }), encoding="utf-8")
            with patch.object(m3, "PROJECTS_ROOT", root), patch.object(m3, "character_pose_config", return_value=(m3.DEFAULT_POSE_ASSETS, m3.DEFAULT_POSE_LABELS)):
                saved = m3.save_topic("quiz-demo", {
                    "projectType": "quiz", "brand": "Aurex", "leftLabel": "Ảnh Quiz", "rightLabel": "",
                    "leftImage": "assets/placeholder-left.svg", "rightImage": "", "voiceover": "audio/silence.wav",
                    "duration": 8, "segments": [{"start": 0, "end": 4, "text": "Question"}, {"start": 4, "end": 8, "text": "Đáp án là cái hố."}],
                    "comparisons": [{"id": "quiz-image-1", "layout": "single", "startSentence": 1,
                        "leftLabel": "Ảnh Quiz", "rightLabel": "", "leftImage": "assets/placeholder-left.svg", "rightImage": ""}],
                })
            self.assertEqual(saved["quizAnswer"], "Đáp án là cái hố.")
            self.assertEqual(saved["quizAnswerDelay"], 5.0)

    def test_quiz_text_renderer_uses_question_answer_pairs_and_hides_images(self) -> None:
        app = (ENGINE_ROOT / "app.js").read_text(encoding="utf-8")
        index = (ENGINE_ROOT / "index.html").read_text(encoding="utf-8")
        styles = (ENGINE_ROOT / "style.css").read_text(encoding="utf-8")
        self.assertIn("function quizPairs()", app)
        self.assertIn("Math.ceil(delay - elapsed)", app)
        self.assertIn("const sceneEnd = Math.max(nextQuestionStart, answerEnd + QUIZ_ANSWER_HOLD_SECONDS);", app)
        self.assertIn('elements.stage.classList.toggle("quiz-text-only", isQuizProject());', app)
        self.assertIn('id="quizQuestion"', index)
        self.assertIn(".stage.quiz-text-only .media-slot", styles)
        self.assertIn(".stage.quiz-text-only .teacher-wrap", styles)
        self.assertIn("setPose(poseAt(time), time, allowPoseSfx && !isQuizProject());", app)
        self.assertIn("if is_quiz:", (ENGINE_ROOT / "tools" / "render_demo.py").read_text(encoding="utf-8"))
        self.assertIn("quizQuestionFontFamily", app)
        self.assertIn("quizQuestionSize", app)
        self.assertIn("quizQuestionColor", app)
        self.assertIn("if (isQuizProject(topic)) renderAt(previewTime());", app)


if __name__ == "__main__":
    unittest.main()
