from pathlib import Path
from types import SimpleNamespace
import json

from tts.vieneu import check_vieneu_health, list_available_voices
from tts.vieneu_adapter import _load_profile, _prepare_text
import tools.render_project as render_project


def test_vieneu_exposes_aurexvideo_reference_voices():
    voices = {voice["id"]: voice for voice in list_available_voices()}

    assert {"chautinhtri", "adam_elevenlabs", "adam_voiceai_vn"}.issubset(voices)
    assert Path(voices["chautinhtri"]["ref_audio"]).is_file()
    assert Path(voices["adam_elevenlabs"]["ref_audio"]).is_file()
    assert Path(voices["adam_voiceai_vn"]["ref_audio"]).is_file()


def test_vieneu_health_reports_shared_profile():
    health = check_vieneu_health()

    assert health["ok"] is True
    assert health["profile_voice_ids"] == ["chautinhtri", "adam_elevenlabs", "adam_voiceai_vn"]
    assert health["profile_path"].endswith("config/aurexvideo_voice.json")


def test_chau_delivery_profile_is_applied_by_aurexvideo_adapter():
    profile, profile_path = _load_profile()

    prepared = _prepare_text(
        "Mọi người tưởng dễ lắm ai ngờ lại rối như canh hẹ!",
        "chautinhtri",
        profile,
        profile_path,
        normalize=False,
    )

    assert "…" in prepared
    assert "[cười]" in prepared


def test_render_project_uses_vieneu_interpreter_for_vieneu_tts(tmp_path, monkeypatch):
    topic_path = tmp_path / "topic.json"
    topic_path.write_text(
        json.dumps({"segments": [{"text": "Một câu kiểm tra VieNeu."}]}),
        encoding="utf-8",
    )
    captured = []
    monkeypatch.setattr(render_project, "run", lambda command: captured.append(command))

    render_project.create_voiceover(
        SimpleNamespace(
            engine="vieneu",
            voice="chautinhtri",
            model_id="",
            tts_mode="auto",
            tts_config_json='{"mode":"v3turbo","device":"cpu"}',
            force_tts=True,
        ),
        tmp_path,
        topic_path,
        "test-token",
    )

    assert captured
    assert captured[0][0] == str(render_project.VIENEU_PYTHON)
