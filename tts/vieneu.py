"""
VieNeu-TTS engine integration for AurexVideo.
Directly uses the local VieNeu-TTS (v3turbo 48 kHz / standard) without external server.
"""

import json
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
    # ── Công nghệ / mạng ──────────────────────────────────────────────
    r"\bqubit\b": "cu-bít",
    r"\bqubits\b": "cu-bít",
    r"\bbit\b": "bít",
    r"\bbits\b": "bít",
    r"\bAI\b": "Ây Ai",
    r"\bInternet\b": "In-tơ-nét",
    r"\binternet\b": "in-tơ-nét",
    r"\bWi-Fi\b": "Wai-phai",
    r"\bWiFi\b": "Wai-phai",
    r"\bWifi\b": "Wai-phai",
    r"\bwifi\b": "wai-phai",
    r"\bCPU\b": "Xê Pê U",
    r"\bcpu\b": "xê-pê-u",
    r"\bGPU\b": "Giê Pê U",
    r"\bgpu\b": "giê-pê-u",
    r"\bRAM\b": "Ram",
    r"\bram\b": "ram",
    r"\bSSD\b": "Ét-xét-đi",
    r"\bssd\b": "ét-xét-đi",
    r"\bHDD\b": "Hát-đê-đê",
    r"\bhdd\b": "hát-đê-đê",
    r"\bBluetooth\b": "Blu-tút",
    r"\bbluetooth\b": "blu-tút",
    r"\bGPS\b": "Giê Pê Ét",
    r"\bgps\b": "giê-pê-ét",
    r"\b4G\b": "bốn giê",
    r"\b5G\b": "năm giê",
    r"\bRobot\b": "Rô-bốt",
    r"\brobot\b": "rô-bốt",
    r"\bMachine Learning\b": "Ma-chin Lơ-ninh",
    r"\bmachine learning\b": "ma-chin lơ-ninh",
    r"\bML\b": "Em El",
    r"\bml\b": "em-el",
    r"\bIQ\b": "Ai Kiu",
    r"\biq\b": "ai-kiu",
    r"\bEQ\b": "E Kiu",
    r"\beq\b": "e-kiu",
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
    # ── Sức khỏe / khoa học ────────────────────────────────────────────
    r"\bVirus\b": "Vi-rút",
    r"\bvirus\b": "vi-rút",
    r"\bvi khuẩn\b": "vi khuẩn",
    r"\bCalo\b": "Ca-lo",
    r"\bcalo\b": "ca-lo",
    r"\bcalorie\b": "ca-lo",
    r"\bcalories\b": "ca-lo",
    r"\bkcal\b": "ki-lô ca-lo",
    r"\bProtein\b": "Prô-tê-in",
    r"\bprotein\b": "prô-tê-in",
    r"\bVitamin\b": "Vi-ta-min",
    r"\bvitamin\b": "vi-ta-min",
    r"\bCarbohydrate\b": "Các-bô-hi-đrát",
    r"\bcarbohydrate\b": "các-bô-hi-đrát",
    r"\bcarb\b": "các",
    r"\bcarbs\b": "các",
    r"\bmineral\b": "mi-nê-ran",
    r"\bminerals\b": "mi-nê-ran",
    r"\bmmHg\b": "mi-li-mét thủy ngân",
    r"\bbpm\b": "nhịp mỗi phút",
    r"\bProbiotic\b": "Prô-bai-ô-tíc",
    r"\bprobiotic\b": "prô-bai-ô-tíc",
    r"\bPrebiotic\b": "Prê-bai-ô-tíc",
    r"\bprebiotic\b": "prê-bai-ô-tíc",
    # ── Tài chính ──────────────────────────────────────────────────────
    r"\bdebit\b": "đê-bít",
    r"\bcredit\b": "cờ-rê-đít",
    r"\bVISA\b": "Vi-za",
    r"\bvisa\b": "vi-za",
    r"\bMastercard\b": "Ma-tơ-cạc",
    r"\bmastercard\b": "ma-tơ-cạc",
    r"\bATM\b": "A Tê Em",
    r"\batm\b": "a-tê-em",
}

RAW_AUDIO_DIR = None
DEFAULT_REF_AUDIO = None
VOICE_REFS_PATH = None
if _vieneu_root:
    RAW_AUDIO_DIR = _vieneu_root / "finetune" / "dataset" / "raw_audio"
    VOICE_REFS_PATH = _vieneu_root / "finetune" / "voices_anhtinh.json"
    candidate = RAW_AUDIO_DIR / "audio_050.wav"
    if candidate.is_file():
        DEFAULT_REF_AUDIO = str(candidate)


def load_tone_references() -> Dict[str, str]:
    """Đọc bộ reference theo sắc thái (tone → absolute path clip wav)."""
    tones: Dict[str, str] = {}
    if not (VOICE_REFS_PATH and VOICE_REFS_PATH.is_file() and RAW_AUDIO_DIR):
        return tones
    try:
        data = json.loads(VOICE_REFS_PATH.read_text(encoding="utf-8"))
        raw = data.get("tones") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            for tone, spec in raw.items():
                if not isinstance(spec, dict):
                    continue
                name = spec.get("audio")
                if not name:
                    continue
                path = (RAW_AUDIO_DIR / name).resolve()
                if path.is_file():
                    tones[str(tone)] = str(path)
    except Exception as exc:
        logger.warning(f"Không đọc được voice reference library: {exc}")
    return tones


def resolve_ref_for_tone(tone: str) -> Optional[str]:
    """Trả về reference audio cho một sắc thái; fallback về default nếu không có."""
    tones = load_tone_references()
    if tone and tone in tones:
        return tones[tone]
    default = tones.get("explain") or tones.get("intro") or DEFAULT_REF_AUDIO
    return default


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
        tones = load_tone_references()
        return {
            "ok": True,
            "installed": True,
            "version": "v3turbo-48kHz",
            "root": str(_vieneu_root) if _vieneu_root else "installed_package",
            "voices": list_available_voices(),
            "tone_references": {tone: Path(path).name for tone, path in sorted(tones.items())},
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
