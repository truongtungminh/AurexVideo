"""
VieNeu-TTS engine integration for AurexVideo.
Directly uses the local VieNeu-TTS (v3turbo 48 kHz / standard) without external server.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AurexVideo.VieNeuTTS")

# Tự động tìm VieNeu-TTS package trong hệ thống
POSSIBLE_VIENEU_PATHS = [
    Path("/Users/truongminh/VieNeu-TTS"),
    Path.home() / "VieNeu-TTS",
    Path(os.environ.get("VIENEU_HOME", "")),
]

_vieneu_root: Optional[Path] = None
for p in POSSIBLE_VIENEU_PATHS:
    if p and p.is_dir() and (p / "src" / "vieneu").is_dir():
        _vieneu_root = p
        src_path = str(p / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)
        break

# Import shared voice logic from the single source of truth
from vieneu.anhtinh import (  # noqa: E402
    PRONUNCIATION_MAP,
    DEFAULT_TONE,
    FALLBACK_REF_NAME,
    get_default_ref,
    load_tone_references,
    resolve_ref_for_tone,
    normalize_text,
    list_tone_references,
    check_health as anhtinh_check_health,
)

# Back-compat: old names used by generate_vieneu_voiceover
DEFAULT_REF_AUDIO = get_default_ref()
RAW_AUDIO_DIR = None
if _vieneu_root:
    RAW_AUDIO_DIR = _vieneu_root / "finetune" / "dataset" / "raw_audio"

# Re-export for back-compat (any code importing from this module)
normalize_text_for_vieneu = normalize_text
load_tone_references = load_tone_references
resolve_ref_for_tone = resolve_ref_for_tone


def list_available_voices() -> List[Dict[str, str]]:
    """Liệt kê các giọng preset và clone có sẵn (engine-facing API)."""
    voices = [
        {
            "id": "chautinhtri",
            "name": "Châu Tinh Trì (Clone v3-Turbo 48kHz)",
            "mode": "v3turbo",
            "ref_audio": get_default_ref() or "",
            "description": "Giọng hài hước, hóm hỉnh đặc trưng lồng tiếng Châu Tinh Trì",
        },
        {
            "id": "v3turbo_xuanvinh",
            "name": "Xuân Vĩnh (v3-Turbo)",
            "mode": "v3turbo",
            "voice": "Xuân Vĩnh",
            "description": "Giọng nam miền Bắc trầm ấm",
        },
        {
            "id": "v3turbo_thanhha",
            "name": "Thanh Hà (v3-Turbo)",
            "mode": "v3turbo",
            "voice": "Thanh Hà",
            "description": "Giọng nữ miền Bắc nhẹ nhàng",
        },
        {
            "id": "v3turbo_quocbao",
            "name": "Quốc Bảo (v3-Turbo)",
            "mode": "v3turbo",
            "voice": "Quốc Bảo",
            "description": "Giọng nam miền Nam truyền cảm",
        },
        {
            "id": "v3turbo_maiphuong",
            "name": "Mai Phương (v3-Turbo)",
            "mode": "v3turbo",
            "voice": "Mai Phương",
            "description": "Giọng nữ miền Nam trẻ trung",
        },
    ]
    return voices


def check_vieneu_health() -> Dict[str, Any]:
    """Kiểm tra môi trường VieNeu-TTS (engine-facing API)."""
    try:
        health = anhtinh_check_health()
        return {
            "ok": health["ok"],
            "installed": health["installed"],
            "version": health.get("version", "v3turbo-48kHz"),
            "root": str(_vieneu_root) if _vieneu_root else "installed_package",
            "voices": list_available_voices(),
            "tone_references": health.get("tone_references", {}),
        }
    except Exception as exc:
        return {
            "ok": False,
            "installed": False,
            "error": f"Chưa cài đặt hoặc không tìm thấy VieNeu-TTS: {exc}",
            "voices": [],
        }


def generate_vieneu_voiceover(
    text: str,
    output_mp3: Path,
    voice_id: str = "chautinhtri",
    mode: str = "v3turbo",
    ref_audio: Optional[str] = None,
    device: str = "cpu",
    normalize: bool = True,
) -> None:
    """Sinh audio voiceover bằng VieNeu-TTS và convert sang MP3 cho AurexVideo."""
    from vieneu import Vieneu

    processed_text = normalize_text_for_vieneu(text) if normalize else text

    # Khởi tạo engine
    tts = Vieneu(mode=mode, device=device)

    # Xác định voice / ref_audio
    actual_ref = ref_audio
    actual_voice = None

    if voice_id == "chautinhtri":
        actual_ref = actual_ref or get_default_ref()
    elif voice_id.startswith("v3turbo_"):
        preset_name = voice_id.replace("v3turbo_", "")
        # Map tên hiển thị
        preset_map = {
            "xuanvinh": "Xuân Vĩnh",
            "thanhha": "Thanh Hà",
            "quocbao": "Quốc Bảo",
            "maiphuong": "Mai Phương",
        }
        actual_voice = preset_map.get(preset_name, preset_name)

    logger.info(f"🎙️ Sinh audio VieNeu-TTS (voice={voice_id}, mode={mode}, ref={actual_ref})")

    if actual_ref and Path(actual_ref).is_file():
        wav = tts.infer(processed_text, ref_audio=str(actual_ref))
    elif actual_voice:
        wav = tts.infer(processed_text, voice=actual_voice)
    else:
        # Mặc định fallback dùng audio Châu Tinh Trì
        if get_default_ref() and Path(get_default_ref()).is_file():
            wav = tts.infer(processed_text, ref_audio=get_default_ref())
        else:
            wav = tts.infer(processed_text)

    # Lưu file tạm và convert sang MP3
    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vieneu-") as tmp:
        temp_wav = Path(tmp) / "temp_voiceover.wav"
        tts.save(wav, str(temp_wav))

        # Dùng ffmpeg convert sang MP3 chuẩn cho pipeline AurexVideo
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(temp_wav),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(output_mp3),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0 or not output_mp3.is_file():
            raise RuntimeError(f"Lỗi chuyển đổi ffmpeg: {res.stderr}")