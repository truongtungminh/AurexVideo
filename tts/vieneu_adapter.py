"""
VieNeu-TTS profile adapter for AurexVideo.

This module is kept separate from the legacy ``tts.vieneu`` wrapper so the
existing AurexVideo API remains backwards compatible.  It consumes the
profile and reference audio owned by the VieNeu-TTS checkout.
"""

from __future__ import annotations

import logging
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("AurexVideo.VieNeuTTS")


def _find_vieneu_root() -> Optional[Path]:
    candidates: List[Path] = []
    for env_name in ("VIENEU_HOME", "VIENEU_TTS_ROOT"):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            candidates.append(Path(value).expanduser())
    candidates.extend(
        [
            Path("/Users/truongminh/VieNeu-TTS"),
            Path.home() / "VieNeu-TTS",
        ]
    )

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_dir() and (candidate / "src" / "vieneu").is_dir():
            src_path = str(candidate / "src")
            if src_path not in sys.path:
                sys.path.insert(0, src_path)
            return candidate
    return None


_vieneu_root = _find_vieneu_root()


def _vieneu_python() -> Optional[Path]:
    candidates: List[Path] = []
    for env_name in ("AUREX_VIENEU_PYTHON", "VIENEU_PYTHON"):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            candidates.append(Path(value).expanduser())
    if _vieneu_root:
        candidates.extend(
            [
                _vieneu_root / ".venv" / "bin" / "python",
                _vieneu_root / ".venv" / "bin" / "python3",
            ]
        )
    candidates.extend(
        [
            Path("/Users/truongminh/VieNeu-TTS/.venv/bin/python"),
            Path.home() / "VieNeu-TTS" / ".venv" / "bin" / "python",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


_PROBE_CACHE: Optional[Tuple[float, Dict[str, Any]]] = None


def _probe_vieneu_runtime() -> Dict[str, Any]:
    """Verify the dependencies used by the real render subprocess."""
    global _PROBE_CACHE
    now = time.monotonic()
    if _PROBE_CACHE and now - _PROBE_CACHE[0] < 30:
        return dict(_PROBE_CACHE[1])

    python = _vieneu_python()
    if python is None or _vieneu_root is None:
        result = {
            "ok": False,
            "python": str(python or ""),
            "error": "Không tìm thấy VieNeu-TTS/.venv để chạy dependency probe.",
        }
        _PROBE_CACHE = (now, result)
        return dict(result)

    env = os.environ.copy()
    env["VIENEU_HOME"] = str(_vieneu_root)
    env["VIENEU_TTS_ROOT"] = str(_vieneu_root)
    src_path = str(_vieneu_root / "src")
    env["PYTHONPATH"] = src_path + os.pathsep + str(env.get("PYTHONPATH") or "")
    probe_code = (
        "import sea_g2p, onnxruntime, soundfile, soxr, kaldi_native_fbank; "
        "import vieneu.v3turbo; print('ok')"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", probe_code],
            cwd=str(_vieneu_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as exc:
        result = {"ok": False, "python": str(python), "error": str(exc)}
    else:
        detail = (completed.stderr or completed.stdout or "").strip()
        result = {
            "ok": completed.returncode == 0,
            "python": str(python),
            "error": detail[-1200:] if completed.returncode else "",
        }
    _PROBE_CACHE = (now, result)
    return dict(result)

from vieneu.anhtinh import (  # noqa: E402
    DEFAULT_TONE,
    FALLBACK_REF_NAME,
    PRONUNCIATION_MAP,
    check_health as anhtinh_check_health,
    get_default_ref,
    list_tone_references,
    load_tone_references,
    normalize_text,
    resolve_ref_for_tone,
)

try:
    from vieneu.aurexvideo_voice import (  # noqa: E402
        get_delivery_settings,
        load_profile,
        prepare_delivery_text,
        resolve_reference,
    )
except Exception as exc:  # pragma: no cover - only for old VieNeu installs
    logger.warning("VieNeu AurexVideo profile helpers unavailable: %s", exc)
    get_delivery_settings = None
    load_profile = None
    prepare_delivery_text = None
    resolve_reference = None


RAW_AUDIO_DIR = _vieneu_root / "finetune" / "dataset" / "raw_audio" if _vieneu_root else None
DEFAULT_REF_AUDIO = get_default_ref()
normalize_text_for_vieneu = normalize_text


def _profile_path() -> Optional[Path]:
    configured = str(os.environ.get("AUREXVIDEO_VOICE_PROFILE") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    candidates: List[Path] = []
    if _vieneu_root:
        candidates.append(_vieneu_root / "config" / "aurexvideo_voice.json")
    # Supports a future self-contained packaged engine profile.
    candidates.append(Path(__file__).resolve().parents[1] / "config" / "aurexvideo_voice.json")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _load_profile() -> Tuple[Optional[Dict[str, Any]], Optional[Path]]:
    path = _profile_path()
    if path is None:
        return None, path
    try:
        if load_profile is not None:
            return load_profile(path), path
        return json.loads(path.read_text(encoding="utf-8")), path
    except Exception as exc:
        logger.warning("Không đọc được profile AurexVideo %s: %s", path, exc)
        try:
            return json.loads(path.read_text(encoding="utf-8")), path
        except Exception:
            return None, path


def _profile_voice_key(voice_id: str, profile: Optional[Dict[str, Any]]) -> str:
    """Map the legacy Châu id to the canonical profile id."""
    value = str(voice_id or "").strip()
    references = profile.get("reference_voices", {}) if profile else {}
    if value in references:
        return value
    if value == "chautinhtri" and "chau_tinh_tri" in references:
        return "chau_tinh_tri"
    return value


def _public_profile_voice_id(profile_voice_id: str) -> str:
    # Existing AurexVideo projects store Châu's id without underscores.
    return "chautinhtri" if profile_voice_id == "chau_tinh_tri" else profile_voice_id


def _resolve_profile_reference(
    profile: Optional[Dict[str, Any]],
    profile_path: Optional[Path],
    profile_voice_id: str,
) -> Optional[Path]:
    if not profile or profile_path is None:
        return None
    references = profile.get("reference_voices", {})
    if profile_voice_id not in references:
        return None
    if resolve_reference is not None:
        return resolve_reference(
            voice_id=profile_voice_id,
            profile=profile,
            profile_path=profile_path,
        )
    spec = references.get(profile_voice_id, {})
    audio_name = str(spec.get("audio") or "").strip()
    audio_root = str(profile.get("dataset", {}).get("audio_root") or "").strip()
    if not audio_name or not audio_root:
        return None
    candidates = [
        profile_path.parent / audio_root / audio_name,
        profile_path.parent.parent / audio_root / audio_name,
    ]
    return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)


def list_available_voices() -> List[Dict[str, str]]:
    """Return preset and AurexVideo reference voices for the engine UI."""
    voices: List[Dict[str, str]] = []
    seen: set[str] = set()
    profile, profile_path = _load_profile()
    references = profile.get("reference_voices", {}) if profile else {}

    for profile_voice_id, spec in references.items():
        if not isinstance(spec, dict):
            continue
        public_id = _public_profile_voice_id(str(profile_voice_id))
        try:
            reference = _resolve_profile_reference(profile, profile_path, str(profile_voice_id))
        except Exception as exc:
            logger.warning("Không resolve được reference voice %s: %s", profile_voice_id, exc)
            reference = None
        voices.append(
            {
                "id": public_id,
                "name": str(spec.get("display_name") or public_id),
                "mode": str(profile.get("engine", {}).get("backend") or "v3turbo"),
                "ref_audio": str(reference or ""),
                "description": str(spec.get("description") or ""),
                "style": str(spec.get("style") or ""),
            }
        )
        seen.add(public_id)

    # Keep the old AurexVideo ids working when the profile is unavailable.
    legacy_voices = [
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
    for voice in legacy_voices:
        if voice["id"] not in seen:
            voices.append(voice)
    return voices


def check_vieneu_health() -> Dict[str, Any]:
    """Check VieNeu and expose the loaded AurexVideo voice profile."""
    try:
        health = anhtinh_check_health()
        runtime = _probe_vieneu_runtime()
        profile, profile_path = _load_profile()
        profile_voice_ids = []
        reference_audio: Dict[str, str] = {}
        references_ok = True
        if profile:
            for voice_id in profile.get("reference_voices", {}):
                profile_voice_id = str(voice_id)
                public_id = _public_profile_voice_id(profile_voice_id)
                profile_voice_ids.append(public_id)
                try:
                    reference = _resolve_profile_reference(profile, profile_path, profile_voice_id)
                except Exception as exc:
                    logger.warning("Không resolve được reference voice %s: %s", profile_voice_id, exc)
                    reference = None
                reference_audio[public_id] = str(reference or "")
                references_ok = references_ok and reference is not None and reference.is_file()
        return {
            "ok": bool(health["ok"] and runtime["ok"] and references_ok),
            "installed": bool(health["installed"] and runtime["ok"] and references_ok),
            "version": health.get("version", "v3turbo-48kHz"),
            "root": str(_vieneu_root) if _vieneu_root else "installed_package",
            "runtime": runtime,
            "profile_path": str(profile_path or ""),
            "profile_voice_ids": profile_voice_ids,
            "reference_audio": reference_audio,
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


def _delivery_settings(
    profile: Optional[Dict[str, Any]], profile_path: Optional[Path], voice_id: str
) -> Dict[str, Any]:
    if not profile:
        return {}
    profile_voice_id = _profile_voice_key(voice_id, profile)
    if get_delivery_settings is None:
        engine = profile.get("engine", {})
        settings = {
            key: engine[key]
            for key in ("temperature", "top_k", "top_p", "repetition_penalty", "max_chars_per_chunk")
            if key in engine
        }
        voice_spec = profile.get("reference_voices", {}).get(profile_voice_id, {})
        delivery_name = voice_spec.get("delivery_profile")
        selected = profile.get("delivery_profiles", {}).get(delivery_name, {})
        if isinstance(selected, dict):
            settings.update(selected)
        return settings
    try:
        return get_delivery_settings(
            profile,
            voice_id=profile_voice_id,
            profile_path=profile_path,
        )
    except Exception as exc:
        logger.warning("Không áp dụng delivery profile cho %s: %s", voice_id, exc)
        return {}


def _prepare_text(
    text: str,
    voice_id: str,
    profile: Optional[Dict[str, Any]],
    profile_path: Optional[Path],
    normalize: bool,
) -> str:
    prepared = str(text)
    if profile and prepare_delivery_text is not None:
        prepared = prepare_delivery_text(
            prepared,
            profile=profile,
            voice_id=_profile_voice_key(voice_id, profile),
            profile_path=profile_path,
        )
    return normalize_text_for_vieneu(prepared) if normalize else prepared


def _ffmpeg_command() -> str:
    try:
        from aurexvideo_paths import ffmpeg_executable

        return str(ffmpeg_executable())
    except Exception:
        return shutil.which("ffmpeg") or "ffmpeg"


def _uses_mps(device: str, tts: Any = None) -> bool:
    requested = str(device or "").strip().lower()
    if "mps" in requested:
        return True
    engine = getattr(tts, "engine", None)
    return "mps" in str(getattr(engine, "device", "")).strip().lower()


def _is_mps_numerical_failure(error: BaseException) -> bool:
    """Recognize the known MPS sampling failure without hiding other errors."""
    current: Optional[BaseException] = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if (
            "probability tensor contains either" in message
            or "mps produced non-finite acoustic" in message
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _infer_with_tieneu(
    tts: Any,
    text: str,
    actual_ref: Optional[Path],
    actual_voice: Optional[str],
    infer_kwargs: Dict[str, Any],
) -> Any:
    if actual_ref is not None:
        return tts.infer(text, ref_audio=str(actual_ref), **infer_kwargs)
    if actual_voice:
        return tts.infer(text, voice=actual_voice, **infer_kwargs)
    return tts.infer(text, **infer_kwargs)


def generate_vieneu_voiceover(
    text: str,
    output_mp3: Path,
    voice_id: str = "chautinhtri",
    mode: str = "v3turbo",
    ref_audio: Optional[str] = None,
    device: str = "cpu",
    normalize: bool = True,
) -> None:
    """Generate an AurexVideo-compatible MP3 using local VieNeu-TTS."""
    from vieneu import Vieneu

    profile, profile_path = _load_profile()
    profile_voice_id = _profile_voice_key(voice_id, profile)
    processed_text = _prepare_text(text, voice_id, profile, profile_path, normalize)
    delivery = _delivery_settings(profile, profile_path, voice_id)

    tts = Vieneu(mode=mode, device=device)
    actual_ref: Optional[Path] = Path(ref_audio).expanduser() if ref_audio else None
    actual_voice: Optional[str] = None

    if actual_ref is not None and not actual_ref.is_file():
        logger.warning("Không tìm thấy ref_audio được cấu hình: %s", actual_ref)
        actual_ref = None

    if actual_ref is None and profile and profile_voice_id in profile.get("reference_voices", {}):
        try:
            actual_ref = _resolve_profile_reference(profile, profile_path, profile_voice_id)
        except Exception as exc:
            logger.warning("Không resolve được reference voice %s: %s", profile_voice_id, exc)
    if actual_ref is None and voice_id == "chautinhtri":
        actual_ref = Path(get_default_ref()) if get_default_ref() else None
    if actual_ref is None and profile and profile_voice_id in profile.get("reference_voices", {}):
        raise FileNotFoundError(
            f"Không tìm thấy reference audio cho giọng VieNeu '{voice_id}' trong profile {profile_path}"
        )
    if actual_ref is None and voice_id.startswith("v3turbo_"):
        preset_name = voice_id.replace("v3turbo_", "")
        preset_map = {
            "xuanvinh": "Xuân Vĩnh",
            "thanhha": "Thanh Hà",
            "quocbao": "Quốc Bảo",
            "maiphuong": "Mai Phương",
        }
        actual_voice = preset_map.get(preset_name, preset_name)
    if actual_ref is None and not actual_voice:
        actual_voice = voice_id or None

    logger.info(
        "🎙️ Sinh audio VieNeu-TTS (voice=%s, mode=%s, ref=%s, preset=%s)",
        voice_id,
        mode,
        actual_ref,
        actual_voice,
    )

    infer_kwargs: Dict[str, Any] = {
        "temperature": float(delivery.get("temperature", 0.8)),
        "top_k": int(delivery.get("top_k", 25)),
        "top_p": float(delivery.get("top_p", 0.95)),
        "repetition_penalty": float(delivery.get("repetition_penalty", 1.2)),
        "max_chars": int(delivery.get("max_chars_per_chunk", 256)),
        "apply_watermark": True,
    }
    try:
        wav = _infer_with_tieneu(
            tts, processed_text, actual_ref, actual_voice, infer_kwargs
        )
    except Exception as exc:
        if not (_uses_mps(device, tts) and _is_mps_numerical_failure(exc)):
            raise
        logger.warning(
            "VieNeu gặp lỗi số học khi chạy MPS (%s); tự chuyển sang CPU để render ổn định.",
            exc,
        )
        tts = Vieneu(mode=mode, device="cpu")
        wav = _infer_with_tieneu(
            tts, processed_text, actual_ref, actual_voice, infer_kwargs
        )

    output_mp3 = Path(output_mp3)
    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vieneu-") as tmp:
        temp_wav = Path(tmp) / "temp_voiceover.wav"
        tts.save(wav, str(temp_wav))
        codec_options = ["-c:a", "pcm_s16le"] if output_mp3.suffix.casefold() == ".wav" else ["-c:a", "libmp3lame", "-q:a", "2"]
        result = subprocess.run(
            [
                _ffmpeg_command(), "-y", "-i", str(temp_wav), "-vn",
                *codec_options, str(output_mp3),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not output_mp3.is_file():
            raise RuntimeError(f"Lỗi chuyển đổi ffmpeg: {result.stderr}")


__all__ = [
    "DEFAULT_REF_AUDIO",
    "DEFAULT_TONE",
    "FALLBACK_REF_NAME",
    "PRONUNCIATION_MAP",
    "RAW_AUDIO_DIR",
    "check_vieneu_health",
    "generate_vieneu_voiceover",
    "list_available_voices",
    "list_tone_references",
    "load_tone_references",
    "normalize_text_for_vieneu",
    "resolve_ref_for_tone",
]
