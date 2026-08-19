"""
VieNeu-TTS engine integration for AurexVideo.
Directly uses the local VieNeu-TTS (v3turbo 48 kHz / standard) without external server.
"""

import logging
import os
import re
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

PRONUNCIATION_MAP = {
    r"\bqubit\b": "cu-bít",
    r"\bqubits\b": "cu-bít",
    r"\bbit\b": "bít",
    r"\bbits\b": "bít",
    r"\bAI\b": "A I",
    r"\bLike\b": "Lai",
    r"\blike\b": "lai",
    r"\bFollow\b": "Pho-lâu",
    r"\bfollow\b": "pho-lâu",
    r"\bComment\b": "Còm-men",
    r"\bcomment\b": "còm-men",
    r"\bSub\b": "Sắp",
    r"\bsub\b": "sắp",
    r"\bSubscribe\b": "Sắp-scrai",
    r"\bsubscribe\b": "sắp-scrai",
    r"\bShare\b": "Se",
    r"\bshare\b": "se",
    r"\bVideo\b": "Vi-đê-ô",
    r"\bvideo\b": "vi-đê-ô",
    r"\bClip\b": "Cờ-líp",
    r"\bclip\b": "cờ-líp",
    r"\bCovid\b": "Cô-vít",
    r"\bcovid\b": "cô-vít",
    r"\bBitcoin\b": "Bít-coin",
    r"\bbitcoin\b": "bít-coin",
    r"\bCrypto\b": "Cờ-ríp-tô",
    r"\bcrypto\b": "cờ-ríp-tô",
    r"\bBlockchain\b": "Bờ-lốc-chên",
    r"\bblockchain\b": "bờ-lốc-chên",
}

DEFAULT_REF_AUDIO = None
if _vieneu_root:
    candidate = _vieneu_root / "finetune" / "dataset" / "raw_audio" / "audio_050.wav"
    if candidate.is_file():
        DEFAULT_REF_AUDIO = str(candidate)


def normalize_text_for_vieneu(text: str) -> str:
    """Nối dòng và chuẩn hóa cách đọc từ ngoại lai cho TTS."""
    cleaned = " ".join(text.strip().split())
    for pattern, rep in PRONUNCIATION_MAP.items():
        cleaned = re.sub(pattern, rep, cleaned)
    return cleaned


def list_available_voices() -> List[Dict[str, str]]:
    """Liệt kê các giọng preset và clone có sẵn."""
    voices = [
        {
            "id": "chautinhtri",
            "name": "Châu Tinh Trì (Clone v3-Turbo 48kHz)",
            "mode": "v3turbo",
            "ref_audio": DEFAULT_REF_AUDIO or "",
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
    """Kiểm tra môi trường VieNeu-TTS."""
    try:
        from vieneu import Vieneu
        return {
            "ok": True,
            "installed": True,
            "version": "v3turbo-48kHz",
            "root": str(_vieneu_root) if _vieneu_root else "installed_package",
            "voices": list_available_voices(),
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
        actual_ref = actual_ref or DEFAULT_REF_AUDIO
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
        if DEFAULT_REF_AUDIO and Path(DEFAULT_REF_AUDIO).is_file():
            wav = tts.infer(processed_text, ref_audio=str(DEFAULT_REF_AUDIO))
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
