#!/usr/bin/env python3
"""Desktop-safe AurexVideo server used by the Tauri launcher."""

from __future__ import annotations

import argparse
from functools import partial
import json
import os
from pathlib import Path
import shutil
import signal
import sys
from http.server import ThreadingHTTPServer
from threading import Thread

from aurexvideo_paths import (
    CHARACTERS_ROOT,
    CONFIG_ROOT,
    DATA_ROOT,
    PROJECTS_ROOT,
    RESOURCE_ROOT,
    configure_native_runtime,
    ensure_user_layout,
)

configure_native_runtime()


def copy_tree_missing(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            copy_tree_missing(item, target)
        elif not target.exists():
            shutil.copy2(item, target)


def seed_user_data() -> None:
    ensure_user_layout()
    copy_tree_missing(RESOURCE_ROOT / "assets" / "characters", CHARACTERS_ROOT)
    if not any(PROJECTS_ROOT.iterdir()):
        copy_tree_missing(RESOURCE_ROOT / "project", PROJECTS_ROOT)
    for name in ("tts.example.json", "social-upload.example.json"):
        source = RESOURCE_ROOT / "config" / name
        destination = CONFIG_ROOT / name
        if source.is_file() and not destination.exists():
            shutil.copy2(source, destination)


def write_state(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="AurexVideo Desktop local engine")
    parser.add_argument("--host", default="127.0.0.1")
    # Google OAuth requires an exact redirect URI. Keep the desktop app on a
    # stable local port so users only need to register the callback once.
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-file", type=Path)
    args = parser.parse_args()

    seed_user_data()

    import m3_backend as m3
    import social_upload.config as social_config
    import social_upload.metadata as social_metadata
    import social_upload.status as social_status_module
    import tts.elevenlabs as elevenlabs
    import web_server

    # Modules keep constants for CLI compatibility. Point every writable surface
    # at the per-user app data directory before the first request is handled.
    m3.PROJECTS_ROOT = PROJECTS_ROOT
    m3.OUTPUT_ROOT = DATA_ROOT / "output"
    m3.CONFIG_ROOT = CONFIG_ROOT
    m3.CHARACTERS_ROOT = CHARACTERS_ROOT
    m3.TTS_CONFIG_PATH = CONFIG_ROOT / "tts.json"
    m3.SOCIAL_CONFIG_PATH = CONFIG_ROOT / "social-upload.json"
    m3.TTS_PYTHON = Path(os.environ.get("AUREX_PYTHON") or sys.executable).resolve()

    social_metadata.PROJECT_ROOT = PROJECTS_ROOT
    social_config.CONFIG_ROOT = CONFIG_ROOT
    social_config.SOCIAL_UPLOAD_CONFIG = CONFIG_ROOT / "social-upload.json"
    social_config.SOCIAL_UPLOAD_EXAMPLE = CONFIG_ROOT / "social-upload.example.json"
    social_status_module.SOCIAL_UPLOAD_CONFIG = social_config.SOCIAL_UPLOAD_CONFIG
    elevenlabs.DEFAULT_CONFIG_PATH = CONFIG_ROOT / "tts.json"

    web_server.DEFAULT_PROJECT_ROOT = PROJECTS_ROOT
    web_server.PROJECT_ROOT = PROJECTS_ROOT
    web_server.SOURCE_ROOT_IS_PROJECT = False
    web_server.BRANDING_CONFIG_PATH = CONFIG_ROOT / "branding.json"
    web_server.BRANDING_ASSET_DIR = CONFIG_ROOT / "branding"
    web_server.UPLOAD_DEFAULTS_CONFIG_PATH = CONFIG_ROOT / "upload-defaults.json"
    web_server.RENDER_PYTHON = Path(os.environ.get("AUREX_PYTHON") or sys.executable).resolve()
    web_server.configure_source_root(PROJECTS_ROOT)

    server = ThreadingHTTPServer((args.host, args.port), partial(web_server.WebHandler))
    actual_port = int(server.server_address[1])
    public_origin = f"http://localhost:{actual_port}"
    os.environ["AUREX_SERVER_ORIGIN"] = public_origin
    state = {
        "status": "ready",
        "host": args.host,
        "port": actual_port,
        "url": public_origin,
        "pid": os.getpid(),
        "dataRoot": str(DATA_ROOT),
    }
    write_state(args.state_file, state)
    print(json.dumps(state, ensure_ascii=False), flush=True)

    def stop_server(_signum: int, _frame: object) -> None:
        Thread(target=server.shutdown, daemon=True).start()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop_server)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        write_state(args.state_file, {**state, "status": "stopped"})


if __name__ == "__main__":
    main()
