from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ENGINE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_ROOT))

import m3_backend  # noqa: E402
from tools.render_project import load_render_topic, render_signature  # noqa: E402


MANIFEST_POSE_ASSETS = {
    "pose-1": {
        "closed": "../../assets/characters/test-character/pose-1.mp4",
        "speaking": "../../assets/characters/test-character/pose-1.mp4",
        "loop": True,
        "loopStart": 0.0,
        "loopEnd": 2.5,
    },
}
MANIFEST_POSE_LABELS = {"pose-1": "Manifest pose"}


def stale_character_topic() -> dict:
    return {
        "characterId": "test-character",
        "poseAssets": {
            "pose-1": {
                "closed": "../../assets/characters/test-character/pose-1.mp4",
                "speaking": "../../assets/characters/test-character/pose-1.mp4",
                "loop": True,
                "loopStart": 1.0,
                "loopEnd": 2.5,
            },
        },
        "poseLabels": {"pose-1": "Stale pose"},
        "poseTimeline": [{"time": 0.0, "pose": "pose-1"}],
    }


class CharacterPoseConfigSyncTests(unittest.TestCase):
    def test_read_topic_replaces_stale_loop_values_when_pose_ids_match(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-character-pose-") as temp:
            projects_root = Path(temp) / "project"
            project = projects_root / "stale-pose"
            project.mkdir(parents=True)
            (project / "topic.json").write_text(
                json.dumps(stale_character_topic()), encoding="utf-8"
            )
            with patch.object(m3_backend, "PROJECTS_ROOT", projects_root), patch.object(
                m3_backend,
                "character_pose_config",
                return_value=(MANIFEST_POSE_ASSETS, MANIFEST_POSE_LABELS),
            ):
                topic = m3_backend.read_topic("stale-pose")
                persisted = json.loads((project / "topic.json").read_text(encoding="utf-8"))
                self.assertEqual(persisted["poseAssets"]["pose-1"]["loopStart"], 0.0)
                self.assertEqual(persisted["poseLabels"], MANIFEST_POSE_LABELS)

        self.assertEqual(list(topic["poseAssets"]), ["pose-1"])
        self.assertEqual(topic["poseAssets"]["pose-1"]["loopStart"], 0.0)
        self.assertEqual(topic["poseAssets"]["pose-1"]["loopEnd"], 2.5)
        self.assertEqual(topic["poseLabels"], MANIFEST_POSE_LABELS)

    def test_cli_render_topic_uses_the_same_manifest_config(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aurex-character-pose-") as temp:
            topic_path = Path(temp) / "topic.json"
            topic_path.write_text(json.dumps(stale_character_topic()), encoding="utf-8")
            with patch.object(
                m3_backend,
                "character_pose_config",
                return_value=(MANIFEST_POSE_ASSETS, MANIFEST_POSE_LABELS),
            ):
                topic = load_render_topic(topic_path)

        self.assertEqual(topic["poseAssets"]["pose-1"]["loopStart"], 0.0)
        self.assertEqual(topic["poseLabels"], MANIFEST_POSE_LABELS)

    def test_cli_cache_signature_includes_the_synchronized_pose_config(self) -> None:
        args = SimpleNamespace(
            engine="project",
            audio=None,
            speed=1.0,
            volume=1.0,
            fps=30,
            size="1080x1920",
            voice="test",
            model_id="",
            whisper_model="base",
            force_tts=False,
            outro=False,
            outro_video=None,
            no_branding=True,
            brand_logo=None,
            brand_name="",
            render_backend="browser",
            quality_profile=None,
        )
        with tempfile.TemporaryDirectory(prefix="aurex-character-pose-") as temp:
            topic_path = Path(temp) / "topic.json"
            topic_path.write_text(json.dumps(stale_character_topic()), encoding="utf-8")
            stale_signature = render_signature(topic_path, args)
            with patch.object(
                m3_backend,
                "character_pose_config",
                return_value=(MANIFEST_POSE_ASSETS, MANIFEST_POSE_LABELS),
            ):
                current_topic = load_render_topic(topic_path)
            current_signature = render_signature(topic_path, args, topic_value=current_topic)

        self.assertNotEqual(stale_signature, current_signature)


if __name__ == "__main__":
    unittest.main()
