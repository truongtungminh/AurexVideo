from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

import m3_backend


EXPECTED_POPSY_CYCLE = ("pose-9", "pose-1", "pose-2", "pose-3", "pose-4", "pose-5", "pose-6")
POSE_ASSETS = {pose: {} for pose in EXPECTED_POPSY_CYCLE}
POSE_LABELS = {pose: pose for pose in EXPECTED_POPSY_CYCLE}


def topic_with_popsy_segments() -> dict:
    return {
        "brand": "Popsy test",
        "leftLabel": "Left",
        "rightLabel": "Right",
        "leftImage": "assets/placeholder-left.svg",
        "rightImage": "assets/placeholder-right.svg",
        "voiceover": "audio/silence.wav",
        "duration": 16,
        "segments": [
            {"start": float(index * 2), "end": float((index + 1) * 2), "text": f"Sentence {index + 1}"}
            for index in range(8)
        ],
        "characterId": "popsy",
        "poseTimeline": [{"time": 0.0, "pose": "pose-1"}],
        "poseAssets": POSE_ASSETS,
        "poseLabels": POSE_LABELS,
    }


class PopsyPoseCycleTests(unittest.TestCase):
    def test_backend_uses_the_seven_pose_popsy_cycle(self) -> None:
        sequence = m3_backend.default_pose_sequence("popsy", POSE_ASSETS)

        self.assertEqual(sequence, list(EXPECTED_POPSY_CYCLE))
        self.assertEqual([sequence[index % len(sequence)] for index in range(8)], [
            "pose-9", "pose-1", "pose-2", "pose-3", "pose-4", "pose-5", "pose-6", "pose-9",
        ])

    def test_missing_popsy_timeline_is_generated_from_segment_starts(self) -> None:
        with patch.object(m3_backend, "read_topic", return_value=topic_with_popsy_segments()), \
            patch.object(m3_backend, "character_pose_config", return_value=(POSE_ASSETS, POSE_LABELS)):
            topic = m3_backend.normalize_topic("popsy-test", {"characterId": "popsy"})

        self.assertEqual(
            [(event["time"], event["pose"]) for event in topic["poseTimeline"]],
            [
                (0.0, "pose-9"),
                (2.0, "pose-1"),
                (4.0, "pose-2"),
                (6.0, "pose-3"),
                (8.0, "pose-4"),
                (10.0, "pose-5"),
                (12.0, "pose-6"),
                (14.0, "pose-9"),
            ],
        )

    def test_explicit_popsy_timeline_is_preserved(self) -> None:
        timeline = [{"time": 0.0, "pose": "pose-6"}, {"time": 4.0, "pose": "pose-2"}]
        with patch.object(m3_backend, "read_topic", return_value=topic_with_popsy_segments()), \
            patch.object(m3_backend, "character_pose_config", return_value=(POSE_ASSETS, POSE_LABELS)):
            topic = m3_backend.normalize_topic("popsy-test", {"characterId": "popsy", "poseTimeline": timeline})

        self.assertEqual(
            [(event["time"], event["pose"]) for event in topic["poseTimeline"]],
            [(event["time"], event["pose"]) for event in timeline],
        )

    def test_editor_uses_the_same_popsy_cycle(self) -> None:
        editor_source = (ENGINE_ROOT / "webui" / "editor.js").read_text(encoding="utf-8")

        self.assertIn(
            'popsy: ["pose-9", "pose-1", "pose-2", "pose-3", "pose-4", "pose-5", "pose-6"],',
            editor_source,
        )


if __name__ == "__main__":
    unittest.main()
