"""Shared filesystem layout for development and packaged AurexVideo builds."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


SOURCE_ROOT = Path(__file__).resolve().parent
RESOURCE_ROOT = Path(os.environ.get("AUREX_RESOURCE_ROOT") or SOURCE_ROOT).expanduser().resolve()
DATA_ROOT = Path(os.environ.get("AUREX_DATA_ROOT") or RESOURCE_ROOT).expanduser().resolve()

PROJECTS_ROOT = DATA_ROOT / "project"
OUTPUT_ROOT = DATA_ROOT / "output"
CONFIG_ROOT = DATA_ROOT / "config"
USER_ASSETS_ROOT = DATA_ROOT / "assets"
CHARACTERS_ROOT = USER_ASSETS_ROOT / "characters"


def bundled_python() -> Path:
    configured = str(os.environ.get("AUREX_PYTHON") or "").strip()
    if configured:
        # Do not resolve venv launchers: on macOS they are symlinks to the base
        # framework binary, and resolving drops site-packages such as Playwright.
        return Path(configured).expanduser().absolute()
    python_name = "Scripts/python.exe" if sys.platform.startswith("win") else "bin/python3.11"
    candidates = [
        RESOURCE_ROOT / ".venv" / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python"),
        RESOURCE_ROOT.parent / "runtime" / "python_base" / python_name,
        RESOURCE_ROOT.parent / "python_base" / python_name,
        RESOURCE_ROOT / "python_base" / python_name,
    ]
    return next((candidate for candidate in candidates if candidate.exists()), Path(sys.executable).absolute())


PYTHON_EXECUTABLE = bundled_python()


def resolve_vieneu_python() -> Path:
    """Resolve the interpreter that has the local VieNeu dependencies."""
    direct_candidates = [
        os.environ.get("AUREX_VIENEU_PYTHON"),
        os.environ.get("VIENEU_PYTHON"),
    ]
    root_candidates = [
        os.environ.get("VIENEU_HOME"),
        os.environ.get("VIENEU_TTS_ROOT"),
        "/Users/truongminh/VieNeu-TTS",
        str(Path.home() / "VieNeu-TTS"),
    ]

    candidates: list[Path] = [
        Path(value).expanduser()
        for value in direct_candidates
        if str(value or "").strip()
    ]
    for root in root_candidates:
        if not str(root or "").strip():
            continue
        root_path = Path(root).expanduser()
        candidates.extend(
            [
                root_path / ".venv" / "bin" / "python",
                root_path / ".venv" / "bin" / "python3",
            ]
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return PYTHON_EXECUTABLE if PYTHON_EXECUTABLE.is_file() else Path(sys.executable)


def ffmpeg_executable() -> Path:
    """Resolve the bundled FFmpeg binary; do not rely on a bare PATH lookup alone."""
    configured = str(os.environ.get("AUREX_FFMPEG") or "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.is_file():
            return path
        raise FileNotFoundError(f"AUREX_FFMPEG không tồn tại: {path}")

    name = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    candidates = (
        RESOURCE_ROOT.parent / "runtime" / "bin" / name,
        SOURCE_ROOT / "runtime" / "bin" / name,
        RESOURCE_ROOT / "runtime" / "bin" / name,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    found = shutil.which("ffmpeg")
    if found:
        return Path(found).resolve()
    raise FileNotFoundError(
        "Không tìm thấy FFmpeg trong runtime AurexVideo. Hãy cài lại engine hoặc kiểm tra runtime/bin."
    )


def ensure_user_layout() -> None:
    """Create writable folders without touching existing user data."""
    for path in (PROJECTS_ROOT, OUTPUT_ROOT, CONFIG_ROOT, CHARACTERS_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def ensure_utf8_stdio() -> None:
    """Keep Vietnamese log lines printable on Windows consoles/pipes."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def ensure_runtime_bin_on_path() -> Path | None:
    """Prepend bundled runtime/bin so child tools can still resolve ffmpeg by name."""
    name = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"
    try:
        ffmpeg = ffmpeg_executable()
    except FileNotFoundError:
        return None
    bin_dir = ffmpeg.parent if ffmpeg.name.lower() in {name, "ffmpeg", "ffmpeg.exe"} else None
    if bin_dir is None or not bin_dir.is_dir():
        return None
    current = os.environ.get("PATH", "")
    parts = [part for part in current.split(os.pathsep) if part]
    prefix = str(bin_dir)
    if not parts or parts[0] != prefix:
        os.environ["PATH"] = os.pathsep.join([prefix, *parts]) if parts else prefix
    return bin_dir


def ensure_windows_native_dll_directories() -> list[Path]:
    """Expose MSVC/ONNX/CTranslate DLL folders to Python's Windows loader."""
    if not sys.platform.startswith("win"):
        return []
    python_root = Path(sys.executable).resolve().parent
    candidates = [
        python_root,
        python_root / "Lib" / "site-packages" / "onnxruntime" / "capi",
        python_root / "Lib" / "site-packages" / "ctranslate2",
        python_root / "Lib" / "site-packages" / "rembg",
    ]
    added: list[Path] = []
    add_dll_directory = getattr(os, "add_dll_directory", None)
    for directory in candidates:
        if not directory.is_dir():
            continue
        if callable(add_dll_directory):
            try:
                add_dll_directory(str(directory))
            except Exception:
                continue
        added.append(directory)
    return added


def configure_native_runtime() -> None:
    """Apply Windows-safe encoding/PATH/DLL search before rendering or AI imports."""
    ensure_utf8_stdio()
    ensure_runtime_bin_on_path()
    ensure_windows_native_dll_directories()
