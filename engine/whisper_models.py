"""Helpers for resolving bundled faster-whisper models."""

from pathlib import Path

from fastscene_paths import RESOURCE_ROOT

ROOT_DIR = RESOURCE_ROOT

LOCAL_WHISPER_MODELS = {
    "base": ROOT_DIR / "models" / "faster-whisper-base",
}


def local_whisper_model_path(model_size: str) -> Path | None:
    path = LOCAL_WHISPER_MODELS.get(model_size)
    if path and (path / "model.bin").exists():
        return path
    return None


def resolve_whisper_model(model_size: str) -> str:
    local_path = local_whisper_model_path(model_size)
    if local_path:
        return str(local_path)
    return model_size


def describe_whisper_model(model_size: str) -> str:
    local_path = local_whisper_model_path(model_size)
    if local_path:
        return f"{model_size} ({local_path.relative_to(ROOT_DIR)})"
    return model_size
