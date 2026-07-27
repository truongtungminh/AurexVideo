"""Helpers for resolving bundled faster-whisper models."""

from pathlib import Path

from aurexvideo_paths import RESOURCE_ROOT, DATA_ROOT

ROOT_DIR = RESOURCE_ROOT
# engineBase is the parent of engine/ (e.g. ~/Library/Application Support/app.aurexvideo)
ENGINE_BASE = ROOT_DIR.parent

# Search order: runtime/models (shipped with heavy runtime tar, downloaded once)
# then engine/models (legacy location) then studio models.
SEARCH_DIRS = [
    ENGINE_BASE / "runtime" / "models",
    ROOT_DIR / "models",
    DATA_ROOT / "models",
]

LOCAL_WHISPER_MODELS = {
    "base": "faster-whisper-base",
}


def local_whisper_model_path(model_size: str) -> Path | None:
    rel = LOCAL_WHISPER_MODELS.get(model_size)
    if not rel:
        return None
    for base in SEARCH_DIRS:
        path = base / rel
        if (path / "model.bin").exists():
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
        # Show path relative to one of the known roots when possible
        for base in SEARCH_DIRS:
            try:
                return f"{model_size} ({local_path.relative_to(base)})"
            except ValueError:
                continue
        return f"{model_size} ({local_path})"
    return model_size
