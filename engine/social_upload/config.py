from __future__ import annotations

import json
import os
from pathlib import Path

from fastscene_paths import CONFIG_ROOT, RESOURCE_ROOT

REPO_ROOT = RESOURCE_ROOT
SOCIAL_UPLOAD_CONFIG = CONFIG_ROOT / "social-upload.json"
SOCIAL_UPLOAD_EXAMPLE = REPO_ROOT / "config" / "social-upload.example.json"


def read_social_config() -> dict:
    if not SOCIAL_UPLOAD_CONFIG.exists():
        return {}
    try:
        data = json.loads(SOCIAL_UPLOAD_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Social upload config is invalid JSON: {SOCIAL_UPLOAD_CONFIG}") from exc
    return data if isinstance(data, dict) else {}


def write_social_config(data: dict) -> None:
    SOCIAL_UPLOAD_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    SOCIAL_UPLOAD_CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(SOCIAL_UPLOAD_CONFIG, 0o600)
    except OSError:
        pass


def social_config_hint() -> str:
    return "YouTube chưa cấu hình. Bấm Thêm channel để nhập OAuth Client ID và Client Secret."
