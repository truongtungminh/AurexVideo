from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

from tools import align_voiceover as align  # noqa: E402


class AlignVoiceoverTests(unittest.TestCase):
    def test_cache_requires_the_current_timing_algorithm_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "aligned-topic.json"
            payload = {
                "alignmentVersion": align.ALIGNMENT_VERSION,
                "duration": 4.0,
                "segments": [{"text": "First line"}, {"text": "Second line"}],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(align.existing_alignment_is_compatible(path, ["First line", "Second line"], 4.0))
            payload["alignmentVersion"] -= 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertFalse(align.existing_alignment_is_compatible(path, ["First line", "Second line"], 4.0))

    def test_split_indexes_finds_a_repeated_line_beyond_the_proportional_window(self) -> None:
        first_line = " ".join(f"a{index}" for index in range(10))
        second_line = " ".join([*(f"b{index}" for index in range(9)), "uniqueanchor"])
        words = [
            {"word": token, "start": index / 10, "end": index / 10 + 0.05}
            for index, token in enumerate(first_line.split())
        ]
        words.extend(
            {"word": "noise", "start": index / 10, "end": index / 10 + 0.05}
            for index in range(10, 180)
        )
        words.extend(
            {"word": token, "start": index / 10, "end": index / 10 + 0.05}
            for index, token in enumerate(second_line.split(), 180)
        )

        self.assertEqual(align.split_indexes(words, [first_line, second_line]), [180])

    def test_repair_short_line_start_uses_the_distinctive_word(self) -> None:
        words = [
            {"word": "mở", "start": 0.0, "end": 0.1},
            {"word": "đầu", "start": 0.1, "end": 0.2},
            {"word": "lặp", "start": 0.5, "end": 0.6},
            {"word": "đặcbiệt", "start": 0.6, "end": 0.7},
        ]
        repaired = align.repair_short_line_starts(
            words,
            ["mở đầu", "lặp đặcbiệt"],
            [0.0, 0.9],
            1.2,
        )

        self.assertEqual(repaired, [0.0, 0.5])

    def test_structural_silences_only_choose_clear_long_boundaries(self) -> None:
        silences = [
            (0.8, 1.35, 0.55),
            (2.0, 2.62, 0.62),
            (3.0, 3.1, 0.1),
        ]
        self.assertEqual(
            align.structural_silence_starts(silences, 3, first_word_start=0.1, last_word_end=3.8),
            [1.35, 2.62],
        )

    def test_word_timing_repair_keeps_all_words_positive_and_ordered(self) -> None:
        repaired = align.ensure_positive_word_timings(
            [
                {"word": "một", "start": 1.4, "end": 1.4},
                {"word": "hai", "start": 1.1, "end": 1.1},
                {"word": "ba", "start": 1.1, "end": 1.1},
            ],
            line_start=1.0,
            line_end=1.12,
        )

        self.assertEqual([word["word"] for word in repaired], ["một", "hai", "ba"])
        self.assertGreaterEqual(repaired[0]["start"], 1.0)
        self.assertLessEqual(repaired[-1]["end"], 1.12)
        self.assertTrue(all(word["end"] > word["start"] for word in repaired))
        self.assertTrue(all(
            repaired[index]["start"] >= repaired[index - 1]["end"]
            for index in range(1, len(repaired))
        ))


if __name__ == "__main__":
    unittest.main()
