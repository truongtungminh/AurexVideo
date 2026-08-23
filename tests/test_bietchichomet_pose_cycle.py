from __future__ import annotations

import sys
import unittest
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

import m3_backend


EXPECTED_BIETCHICHOMET_CYCLE = (
    "pose-1",
    "pose-2",
    "pose-3",
    "pose-1",
    "pose-2",
    "pose-4",
    "pose-1",
    "pose-2",
    "pose-5",
)
POSE_ASSETS = {pose: {} for pose in (*EXPECTED_BIETCHICHOMET_CYCLE, "pose-6")}


class BietchichometPoseCycleTests(unittest.TestCase):
    def test_backend_uses_only_the_exact_bietchichomet_cycle(self) -> None:
        sequence = m3_backend.default_pose_sequence("bietchichomet", POSE_ASSETS)

        self.assertEqual(sequence, list(EXPECTED_BIETCHICHOMET_CYCLE))
        self.assertTrue(set(sequence) <= {f"pose-{index}" for index in range(1, 6)})
        self.assertEqual(
            [sequence[index % len(sequence)] for index in range(11)],
            [*EXPECTED_BIETCHICHOMET_CYCLE, "pose-1", "pose-2"],
        )

    def test_backend_timeline_repeats_the_cycle_at_segment_starts(self) -> None:
        segments = [{"start": float(index * 2)} for index in range(11)]

        timeline = m3_backend.default_character_pose_timeline("bietchichomet", POSE_ASSETS, segments)

        self.assertEqual(
            [event["pose"] for event in timeline],
            [*EXPECTED_BIETCHICHOMET_CYCLE, "pose-1", "pose-2"],
        )

    def test_editor_uses_the_same_exact_cycle(self) -> None:
        editor_source = (ENGINE_ROOT / "webui" / "editor.js").read_text(encoding="utf-8")

        self.assertIn(
            'bietchichomet: ["pose-1", "pose-2", "pose-3", "pose-1", "pose-2", "pose-4", "pose-1", "pose-2", "pose-5"],',
            editor_source,
        )


if __name__ == "__main__":
    unittest.main()
