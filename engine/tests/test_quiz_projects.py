from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

QUIZ_SCRIPT = """Con vật nào thường ngủ khi đang đứng?
A. Con ngựa.
B. Con mèo.
C. Con cá.
Đáp án chính xác là A. Con ngựa.
Thủ đô của Việt Nam là gì?
A. Đà Nẵng.
B. Hà Nội.
C. TP.HCM.
Đáp án chính xác là B. Dòng chữ cố ý sai để kiểm tra canonicalize.
Hành tinh nào gần Mặt Trời nhất?
A. Sao Hỏa.
B. Trái Đất.
C. Sao Thủy.
Đáp án chính xác là C. Sao Thủy."""

PICTURE_QUIZ_SCRIPT = """What is this?
Parrot
Owl
Penguin
Correct answer: Parrot
What is this?
Chair
Lamp
Table
Correct answer: Lamp
What is this?
Hat
Shoe
Bag
Correct answer: Hat"""

PICTURE_QUIZ_SCRIPT_WITH_CTA = f"""{PICTURE_QUIZ_SCRIPT}
How many did you get right? Comment your score and follow Quizzy for more!"""

import m3_backend as m3  # noqa: E402
from tools.render_project import (  # noqa: E402
    create_quiz_segment_voiceover,
    quiz_audio_insertions,
    quiz_hook_question_delay,
    quiz_segment_timeline,
    quiz_segments_after_audio_pause,
    quiz_v2_no_narration_requested,
)
from tools.render_demo import quiz_countdown_starts  # noqa: E402


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

    def test_suvietky_default_countdown_is_three_seconds(self) -> None:
        topic = {
            "projectType": "quiz",
            "brand": "suvietky",
            "quizHookSegmentCount": 1,
            "quizItems": [{"question": "Q1", "options": ["A", "B", "C"], "correct_index": 0}, {"question": "Q2", "options": ["A", "B", "C"], "correct_index": 0}, {"question": "Q3", "options": ["A", "B", "C"], "correct_index": 0}],
            "segments": [{"text": "Hook"}] + [{"text": str(index)} for index in range(15)],
        }
        timeline, duration = quiz_segment_timeline(topic, [1.0] * 16)
        self.assertEqual(quiz_hook_question_delay(topic), 1.0)
        self.assertEqual((timeline[0]["start"], timeline[0]["end"]), (0.0, 1.0))
        self.assertEqual((timeline[1]["start"], timeline[1]["end"]), (2.0, 3.0))
        self.assertEqual((timeline[5]["start"], timeline[5]["end"]), (9.0, 10.0))
        self.assertEqual(duration, 28.0)

    def test_quiz_v2_hook_segment_stays_before_quiz_pause_schedule(self) -> None:
        topic = {
            "projectType": "quiz",
            "quizAnswerDelay": 5,
            "quizHookSegmentCount": 1,
            "quizItems": [{}, {}, {}],
            "segments": [{"text": "Hook"}] + [{"text": str(index)} for index in range(15)],
        }
        timeline, duration = quiz_segment_timeline(topic, [1.0] * 16)
        self.assertEqual((timeline[0]["start"], timeline[0]["end"]), (0.0, 1.0))
        self.assertEqual((timeline[1]["start"], timeline[1]["end"]), (2.0, 3.0))
        self.assertEqual((timeline[5]["start"], timeline[5]["end"]), (11.0, 12.0))
        self.assertEqual((timeline[6]["start"], timeline[6]["end"]), (13.0, 14.0))
        self.assertEqual(duration, 34.0)

    def test_suvietky_hook_art_and_pool_are_wired_in_engine_contract(self) -> None:
        app = (ENGINE_ROOT / "app.js").read_text(encoding="utf-8")
        styles = (ENGINE_ROOT / "style.css").read_text(encoding="utf-8")
        index = (ENGINE_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("quizHookSegmentCount", app)
        self.assertIn("quizHookArt", app)
        self.assertIn("quizHookQuestionDelay", app)
        self.assertIn("quizHookText", app)
        self.assertIn('id="quizHookText"', index)
        self.assertIn(".quiz-hook-text", styles)
        self.assertNotIn("quiz-hook-wash", index)
        self.assertNotIn("quiz-hook-wash", styles)
        self.assertIn("quiz-hook-active", styles)

    def test_suvietky_hook_fields_survive_topic_normalization(self) -> None:
        current = {
            "projectType": "quiz",
            "brand": "suvietky",
            "leftLabel": "Quiz",
            "rightLabel": "",
            "leftImage": "assets/background-default.png",
            "rightImage": "",
            "voiceover": "audio/voiceover.wav",
            "duration": 45,
            "segments": [],
            "quizItems": [{"question": "Q1", "options": ["A", "B", "C"], "correct_index": 0}, {"question": "Q2", "options": ["A", "B", "C"], "correct_index": 0}, {"question": "Q3", "options": ["A", "B", "C"], "correct_index": 0}],
        }
        payload = {
            "projectType": "quiz",
            "brand": "suvietky",
            "leftLabel": "Quiz",
            "rightLabel": "",
            "leftImage": "assets/background-default.png",
            "rightImage": "",
            "voiceover": "audio/voiceover.wav",
            "duration": 45,
            "segments": [{"start": 0, "end": 2, "text": "Hook"}] + [{"start": 2, "end": 3, "text": str(i)} for i in range(16)],
            "quizItems": [{"question": "Q1", "options": ["A", "B", "C"], "correct_index": 0}, {"question": "Q2", "options": ["A", "B", "C"], "correct_index": 0}, {"question": "Q3", "options": ["A", "B", "C"], "correct_index": 0}],
            "quizHookSegmentCount": 1,
            "quizHookQuestionDelay": 1,
            "quizHookArt": "assets/hook-suvietky.png",
            "quizHookText": "Cố đô Hoa Lư",
            "quizHook": {"text": "Hook", "art": "assets/hook-suvietky.png", "pool": "hook_pool_suvietky_300_v3", "poolIndex": 116},
            "quizCtaArt": "assets/cta-suvietky.png",
        }
        with patch.object(m3, "read_topic", return_value=current):
            normalized = m3.normalize_topic("hook-test", payload)
        self.assertEqual(normalized["quizHookSegmentCount"], 1)
        self.assertEqual(normalized["quizHookQuestionDelay"], 1.0)
        self.assertEqual(normalized["quizHookArt"], "assets/hook-suvietky.png")
        self.assertEqual(normalized["quizHookText"], "Cố đô Hoa Lư")
        self.assertEqual(normalized["quizHook"]["poolIndex"], 116)
        self.assertEqual(normalized["quizHook"]["text"], "Hook")
        self.assertEqual(normalized["quizCtaArt"], "assets/cta-suvietky.png")
        self.assertEqual(normalized["quizAnswerDelay"], 3.0)

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

    def test_new_project_page_accepts_quiz_script_and_auto_cta(self) -> None:
        source = (ENGINE_ROOT / "webui" / "new-project.html").read_text(encoding="utf-8")
        self.assertIn('name="quizScript"', source)
        self.assertIn("validateQuizScript", source)
        self.assertIn("CTA tự động", source)
        self.assertIn("Countdown 3s sau C", source)

    def test_quiz_script_parser_builds_items_canonical_answers_and_default_cta(self) -> None:
        lines, items = m3.parse_quiz_script(QUIZ_SCRIPT, "vi")
        self.assertEqual(len(lines), 16)
        self.assertEqual([item["correct_index"] for item in items], [0, 1, 2])
        self.assertEqual(lines[9], "Đáp án chính xác là B. Hà Nội.")
        self.assertEqual(lines[-1], m3.QUIZ_DEFAULT_CTA_VI)
        custom_lines, _ = m3.parse_quiz_script(QUIZ_SCRIPT + "\nCTA dòng một\nCTA dòng hai", "vi")
        self.assertEqual(custom_lines[-1], "CTA dòng một CTA dòng hai")

    def test_create_quiz_project_from_script_writes_sixteen_segments_and_correct_indices(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-quiz-auto-project-") as tmp:
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
                summary = m3.create_project({
                    "id": "quiz-auto-demo",
                    "projectType": "quiz",
                    "language": "vi",
                    "quizScript": QUIZ_SCRIPT,
                })
                topic = json.loads((projects_root / "quiz-auto-demo" / "topic.json").read_text(encoding="utf-8"))
                saved_lines = (projects_root / "quiz-auto-demo" / "script.txt").read_text(encoding="utf-8").splitlines()

            self.assertEqual(summary["projectType"], "quiz")
            self.assertEqual(len(topic["segments"]), 16)
            self.assertEqual(len(topic["quizItems"]), 3)
            self.assertEqual([item["correct_index"] for item in topic["quizItems"]], [0, 1, 2])
            self.assertEqual(topic["segments"][9]["text"], "Đáp án chính xác là B. Hà Nội.")
            self.assertEqual(saved_lines[-1], m3.QUIZ_DEFAULT_CTA_VI)
            self.assertEqual(
                [round(topic["segments"][index + 1]["start"] - topic["segments"][index]["end"], 3) for index in (3, 8, 13)],
                [5.0, 5.0, 5.0],
            )

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

    def test_create_picture_quiz_reads_only_question_and_correct_answer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-picture-quiz-project-") as tmp:
            root = Path(tmp)
            projects_root = root / "projects"
            config_root = root / "config"
            with (
                patch.object(m3, "PROJECTS_ROOT", projects_root),
                patch.object(m3, "OUTPUT_ROOT", root / "output"),
                patch.object(m3, "CONFIG_ROOT", config_root),
                patch.object(m3, "PROJECT_DEFAULTS_PATH", config_root / "project-defaults.json"),
                patch.object(m3, "character_manifest", side_effect=FileNotFoundError),
                patch.object(m3, "list_brands", return_value=[{"id": "suvietky", "name": "Sử Việt Ký"}]),
            ):
                m3.create_project({
                    "id": "picture-quiz-demo",
                    "projectType": "quiz",
                    "quizTemplate": "picture",
                    "brand": "suvietky",
                    "language": "en",
                    "quizScript": PICTURE_QUIZ_SCRIPT,
                })
                topic = json.loads((projects_root / "picture-quiz-demo" / "topic.json").read_text(encoding="utf-8"))
                script = (projects_root / "picture-quiz-demo" / "script.txt").read_text(encoding="utf-8")

            self.assertEqual(topic["quizTemplate"], "picture")
            self.assertEqual(len(topic["quizItems"]), 3)
            self.assertEqual(len(topic["quizScriptLines"]), 15)
            self.assertEqual([segment["text"] for segment in topic["segments"]], [
                "What is this?",
                "Correct answer: Parrot.",
                "What is this?",
                "Correct answer: Lamp.",
                "What is this?",
                "Correct answer: Hat.",
            ])
            self.assertIn("Penguin", script)
            self.assertNotIn("A. Parrot", script)

    def test_create_picture_quiz_preserves_trailing_cta(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-picture-quiz-cta-") as tmp:
            root = Path(tmp)
            projects_root = root / "projects"
            config_root = root / "config"
            with (
                patch.object(m3, "PROJECTS_ROOT", projects_root),
                patch.object(m3, "OUTPUT_ROOT", root / "output"),
                patch.object(m3, "CONFIG_ROOT", config_root),
                patch.object(m3, "PROJECT_DEFAULTS_PATH", config_root / "project-defaults.json"),
                patch.object(m3, "character_manifest", side_effect=FileNotFoundError),
                patch.object(m3, "list_brands", return_value=[{"id": "bietchichomet", "name": "bietchichomet"}, {"id": "quizzy", "name": "Quizzy"}]),
            ):
                m3.create_project({
                    "id": "picture-quiz-cta-demo",
                    "projectType": "quiz",
                    "quizTemplate": "picture",
                    "brand": "bietchichomet",
                    "language": "en",
                    "quizScript": PICTURE_QUIZ_SCRIPT_WITH_CTA,
                })
                project = projects_root / "picture-quiz-cta-demo"
                topic = json.loads((project / "topic.json").read_text(encoding="utf-8"))
                script_lines = (project / "script.txt").read_text(encoding="utf-8").splitlines()

            cta = "How many did you get right? Comment your score and follow Quizzy for more!"
            self.assertEqual(topic["brand"], "quizzy")
            self.assertEqual(len(topic["quizScriptLines"]), 15)
            self.assertEqual(topic["quizCtaText"], cta)
            self.assertEqual(script_lines[-1], cta)
            self.assertEqual(len(script_lines), 16)
            self.assertEqual(topic["segments"][-1]["text"], cta)

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

    def test_preview_scopes_brand_specific_css_from_topic_brand(self) -> None:
        app = (ENGINE_ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn('cls.startsWith("brand-")', app)
        self.assertIn('stageClasses.add(`brand-${topicBrand}`)', app)

    def test_suvietky_uses_only_dynamic_overlays_on_baked_background(self) -> None:
        styles = (ENGINE_ROOT / "style.css").read_text(encoding="utf-8")
        self.assertIn(".stage.brand-suvietky .quiz-text.quiz-v2", styles)
        self.assertIn(".stage.brand-suvietky .quiz-v2 .quiz-question-card", styles)
        self.assertIn(".stage.brand-suvietky .quiz-v2 .quiz-option.is-correct::before", styles)
        self.assertIn(".stage.brand-suvietky .quiz-v2 .quiz-option.is-correct .quiz-option-label", styles)

    def test_editor_parses_five_line_quiz_tts_blocks(self) -> None:
        editor = (ENGINE_ROOT / "webui" / "editor.js").read_text(encoding="utf-8")
        self.assertIn("function quizItemsFromScript(lines = scriptLines())", editor)
        self.assertIn("function quizScriptLinesWithCta(lines = scriptLines())", editor)
        self.assertIn("đáp án chính xác là", editor)
        self.assertIn("đáp án đúng là", editor)
        self.assertIn("correct answer is", editor)
        self.assertIn("quizItems: isQuizProject() ? (parsedQuizItems || state.topic.quizItems)", editor)
        self.assertIn("Kịch bản Quiz chưa hợp lệ", editor)

    def test_quiz_v2_tts_timeline_pauses_after_three_options(self) -> None:
        topic = {"projectType": "quiz", "quizAnswerDelay": 5, "quizItems": [{}, {}, {}], "segments": [{"text": str(i)} for i in range(15)]}
        timeline, duration = quiz_segment_timeline(topic, [1.0] * 15)
        self.assertEqual(timeline[3]["end"], 4.0)
        self.assertEqual(timeline[4]["start"], 9.0)
        self.assertEqual(timeline[5]["start"], 11.0)
        self.assertEqual(duration, 32.0)

    def test_explicit_quiz_tts_engine_wins_over_stale_none_provider(self) -> None:
        topic = {"projectType": "quiz", "quizItems": [{}, {}, {}], "ttsProvider": "none"}
        self.assertFalse(quiz_v2_no_narration_requested(topic, "vieneu"))
        self.assertFalse(quiz_v2_no_narration_requested(topic, "aurextts"))
        self.assertTrue(quiz_v2_no_narration_requested(topic, "project"))

    def test_quiz_v2_countdown_starts_after_option_c(self) -> None:
        segments = []
        cursor = 0.0
        for _ in range(3):
            for duration in (1.0, 1.0, 1.0, 1.0):
                segments.append({"start": cursor, "end": cursor + duration})
                cursor += duration
            cursor += 5.0
            segments.append({"start": cursor, "end": cursor + 1.0})
            cursor += 2.0
        topic = {
            "projectType": "quiz",
            "quizAnswerDelay": 5,
            "quizItems": [{}, {}, {}],
            "segments": segments,
        }
        self.assertEqual(quiz_countdown_starts(topic), [4.0, 15.0, 26.0])

    def test_quiz_v2_visual_countdown_waits_for_option_c_narration(self) -> None:
        app = (ENGINE_ROOT / "app.js").read_text(encoding="utf-8")
        index = (ENGINE_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="quizCountdown">3</div>', index)
        self.assertIn("quizHookText", index)
        self.assertIn("app.js?v=20260914-thegioidoday-countdown-zero-1", index)
        self.assertIn("const last = segments[segments.length - 1];", app)
        self.assertIn("if (segments.length >= quizStart + quizNarrationCount)", app)
        self.assertIn("const QUIZ_V2_DEFAULT_THINKING_SECONDS = 3;", app)
        self.assertIn("const countdownStartAt = Math.max(0, optionsEnd - start);", app)
        self.assertIn("const countdownElapsed = elapsed - countdownStartAt;", app)
        self.assertIn("function quizThinkingSeconds", app)
        self.assertIn("const thinkingSeconds = quizThinkingSeconds();", app)
        self.assertIn(
            "const countdownActive = countdownElapsed >= 0 && countdownElapsed < thinkingSeconds;",
            app,
        )
        self.assertIn('const alwaysShowCountdown = ["suvietky", "thegioidoday"].includes(String(topic?.brand || "").toLowerCase());', app)
        self.assertIn("const countdownVisible = countdownActive || alwaysShowCountdown;", app)
        self.assertIn("elements.quizCountdownWrap.hidden = !countdownVisible;", app)
        self.assertIn('alwaysShowCountdown ? (reveal ? "0" : String(thinkingSeconds)) : ""', app)

    def test_quiz_v2_countdown_and_timeline_allow_trailing_cta(self) -> None:
        topic = {
            "projectType": "quiz",
            "quizAnswerDelay": 5,
            "quizItems": [{}, {}, {}],
            "segments": [{"text": str(i)} for i in range(16)],
        }
        timeline, duration = quiz_segment_timeline(topic, [1.0] * 16)
        self.assertEqual(timeline[3]["end"], 4.0)
        self.assertEqual(timeline[4]["start"], 9.0)
        self.assertEqual(timeline[8]["end"], 15.0)
        self.assertEqual(timeline[9]["start"], 20.0)
        self.assertEqual(timeline[13]["end"], 26.0)
        self.assertEqual(timeline[14]["start"], 31.0)
        self.assertEqual(timeline[15]["start"], 33.0)
        self.assertEqual(duration, 34.0)

        extended_topic = {**topic, "segments": topic["segments"] + [{"text": "CTA 2"}, {"text": "CTA 3"}, {"text": "CTA 4"}, {"text": "CTA 5"}]}
        extended_timeline, extended_duration = quiz_segment_timeline(extended_topic, [1.0] * 20)
        self.assertEqual(extended_timeline[18]["end"], 37.0)
        self.assertEqual(extended_timeline[19]["start"], 37.0)
        self.assertEqual(extended_duration, 38.0)

        timed_segments = []
        cursor = 0.0
        for item_index in range(3):
            for _ in range(4):
                timed_segments.append({"start": cursor, "end": cursor + 1.0})
                cursor += 1.0
            cursor += 5.0
            timed_segments.append({"start": cursor, "end": cursor + 1.0})
            cursor += 2.0
        timed_segments.append({"start": cursor, "end": cursor + 1.0, "text": "CTA"})
        countdown_topic = {**topic, "segments": timed_segments}
        self.assertEqual(quiz_countdown_starts(countdown_topic), [4.0, 15.0, 26.0])

        app = (ENGINE_ROOT / "app.js").read_text(encoding="utf-8")
        index = (ENGINE_ROOT / "index.html").read_text(encoding="utf-8")
        styles = (ENGINE_ROOT / "style.css").read_text(encoding="utf-8")
        self.assertIn("function quizV2CtaAt(time)", app)
        self.assertIn("function renderQuizCta()", app)
        self.assertIn("if (elements.quizResultArt) elements.quizResultArt.hidden = true;", app)
        self.assertIn('id="quizCtaArt"', index)
        self.assertIn('/assets/quiz-cta-like.webp', index)
        self.assertIn('.quiz-cta-art', styles)
        self.assertTrue((ENGINE_ROOT / "assets" / "quiz-cta-like.webp").is_file())

    def test_suvietky_uses_baked_pill_geometry_and_project_cta_art(self) -> None:
        styles = (ENGINE_ROOT / "style.css").read_text(encoding="utf-8")
        app = (ENGINE_ROOT / "app.js").read_text(encoding="utf-8")
        project = (Path.home() / "Library/Application Support/app.aurexvideo/studio/project"
                   / "suvietky-vinh-ha-long-cat-ba-077")
        topic = json.loads((project / "topic.json").read_text(encoding="utf-8"))

        self.assertIn("left: 15.5%;", styles)
        self.assertIn("top: 45.8%;", styles)
        self.assertIn("width: 66.5%;", styles)
        self.assertIn("height: 23.5%;", styles)
        self.assertIn("gap: 0%;", styles)
        self.assertIn(".stage.brand-suvietky .quiz-cta-art", styles)
        self.assertIn("inset: 0;", styles)
        self.assertIn("width: 100%;", styles)
        self.assertIn("height: 100%;", styles)
        self.assertIn("object-fit: cover;", styles)
        overlay_start = styles.index(".stage.brand-suvietky .quiz-v2 .quiz-option.is-correct::before,")
        overlay_end = styles.index("\n}", overlay_start) + 2
        self.assertIn("box-sizing: border-box;", styles[overlay_start:overlay_end])
        self.assertIn("const quizCtaSource = String(nextTopic?.quizCtaArt || \"\").trim();", app)
        self.assertIn("elements.quizCtaArt.src = resolveTopicAsset(quizCtaSource);", app)
        self.assertEqual(topic.get("quizCtaArt"), "assets/cta-suvietky.png")
        self.assertTrue((project / "assets/cta-suvietky.png").is_file())

    def test_quiz_v2_countdown_has_fixed_fallback_without_tts(self) -> None:
        topic = {
            "projectType": "quiz",
            "quizAnswerDelay": 5,
            "quizItems": [{}, {}, {}],
            "segments": [],
        }
        self.assertEqual(quiz_countdown_starts(topic), [0.0, 7.4, 14.8])

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
        self.assertIn("function fitSuvietkyOptions()", app)
        self.assertIn("text.scrollHeight <= text.clientHeight + 1", app)
        self.assertIn("fitSuvietkyOptions();", app)
        self.assertIn("if (isQuizProject(topic)) renderAt(previewTime());", app)


if __name__ == "__main__":
    unittest.main()
