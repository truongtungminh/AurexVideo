"""Offline background removal via rembg (isnet-general-use).

Models download on first use into the FastScene data directory; they are not
bundled inside the engine archive.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from fastscene_paths import DATA_ROOT, configure_native_runtime

configure_native_runtime()

# rembg / u2net look up this env var for the ONNX cache directory.
MODEL_HOME = DATA_ROOT / "models" / "rembg"
DEFAULT_MODEL = "isnet-general-use"
# isnet-general-use.onnx is ~170 MB; treat smaller/partial files as not ready.
_MIN_MODEL_BYTES = 50_000_000

_session = None


def ensure_model_home() -> Path:
    MODEL_HOME.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("U2NET_HOME", str(MODEL_HOME))
    return MODEL_HOME


def model_path(model_name: str = DEFAULT_MODEL) -> Path:
    return MODEL_HOME / f"{model_name}.onnx"


def model_is_ready(model_name: str = DEFAULT_MODEL) -> bool:
    path = model_path(model_name)
    try:
        return path.is_file() and path.stat().st_size >= _MIN_MODEL_BYTES
    except OSError:
        return False


def remove_background_status(model_name: str = DEFAULT_MODEL) -> dict:
    ready = model_is_ready(model_name)
    return {
        "ok": True,
        "model": model_name,
        "ready": ready,
        "firstLoad": not ready,
    }


def _session_for_model(model_name: str = DEFAULT_MODEL):
    global _session
    ensure_model_home()
    if _session is not None and getattr(_session, "model_name", None) == model_name:
        return _session
    try:
        from rembg import new_session
    except ImportError as exc:
        raise RuntimeError(
            "Thiếu rembg trong runtime FastScene. Hãy cập nhật engine để dùng Xóa nền."
        ) from exc
    _session = new_session(model_name)
    try:
        _session.model_name = model_name
    except Exception:
        pass
    return _session


def remove_background_png(image_bytes: bytes, model_name: str = DEFAULT_MODEL) -> bytes:
    if not image_bytes:
        raise ValueError("Ảnh rỗng; không thể xóa nền.")
    try:
        from rembg import remove
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError(
            "Thiếu rembg/Pillow trong runtime FastScene. Hãy cập nhật engine để dùng Xóa nền."
        ) from exc

    session = _session_for_model(model_name)
    try:
        result = remove(image_bytes, session=session)
    except Exception as exc:
        raise RuntimeError(f"Xóa nền thất bại: {exc}") from exc

    # Normalize to PNG with alpha so callers can rely on transparency.
    image = Image.open(io.BytesIO(result)).convert("RGBA")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
