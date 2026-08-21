from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .config import read_social_config


def _future(value: str) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def schedule_on_vps(platform: str, video_path: Path, caption: str, scheduled_at: str) -> dict:
    cfg = read_social_config().get("social_worker") or {}
    url = str(cfg.get("url") or os.environ.get("AUREX_SOCIAL_WORKER_URL") or "").rstrip("/")
    api_key = str(cfg.get("api_key") or os.environ.get("AUREX_SOCIAL_WORKER_API_KEY") or "")
    ssh_target = str(cfg.get("ssh") or os.environ.get("AUREX_SOCIAL_WORKER_SSH") or "")
    ssh_key = str(cfg.get("ssh_key") or os.environ.get("AUREX_SOCIAL_WORKER_SSH_KEY") or "")
    ssh_port = str(cfg.get("ssh_port") or os.environ.get("AUREX_SOCIAL_WORKER_SSH_PORT") or "54321")
    media_root = str(cfg.get("media_root") or os.environ.get("AUREX_SOCIAL_WORKER_MEDIA_ROOT") or "/opt/aurex-social-worker/media").rstrip("/")
    if not all((url, api_key, ssh_target, ssh_key)):
        raise RuntimeError("VPS social worker chưa được cấu hình trong social-upload.json.")
    video_path = Path(video_path).resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy video: {video_path}")
    scheduled_at = _future(scheduled_at)
    digest = hashlib.sha256(video_path.read_bytes()).hexdigest()
    remote_path = f"{media_root}/{digest}{video_path.suffix.lower() or '.mp4'}"
    copy = subprocess.run(
        ["scp", "-q", "-o", "IdentitiesOnly=yes", "-i", ssh_key, "-P", ssh_port, str(video_path), f"{ssh_target}:{remote_path}"],
        capture_output=True, text=True, timeout=300,
    )
    if copy.returncode:
        raise RuntimeError(f"Không copy được video lên VPS: {(copy.stderr or copy.stdout).strip()[:500]}")
    payload = {"platform": platform, "scheduledPublishAt": scheduled_at, "caption": caption, "videoPath": remote_path, "expectedMediaSha256": digest}
    request = urllib.request.Request(
        f"{url}/schedule", data=json.dumps(payload).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"VPS social worker HTTP {exc.code}: {detail}") from exc
    return {**body, "scheduledPublishAt": scheduled_at, "expectedMediaSha256": digest}
