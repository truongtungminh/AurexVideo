from pathlib import Path

from tts.vieneu import check_vieneu_health, list_available_voices
from tts.vieneu_adapter import _load_profile, _prepare_text


def test_vieneu_exposes_aurexvideo_reference_voices():
    voices = {voice["id"]: voice for voice in list_available_voices()}

    assert {"chautinhtri", "adam_elevenlabs"}.issubset(voices)
    assert Path(voices["chautinhtri"]["ref_audio"]).is_file()
    assert Path(voices["adam_elevenlabs"]["ref_audio"]).is_file()


def test_vieneu_health_reports_shared_profile():
    health = check_vieneu_health()

    assert health["ok"] is True
    assert health["profile_voice_ids"] == ["chautinhtri", "adam_elevenlabs"]
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
