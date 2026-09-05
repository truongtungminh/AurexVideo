#!/usr/bin/env python3
"""Build and stage the macOS Aurex Render Core executable for packaging."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "native" / "AurexRenderCore"
DEFAULT_OUTPUT = ROOT / "native" / "bin" / "aurex-render"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configuration", choices=["debug", "release"], default="release")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    if sys.platform != "darwin":
        raise SystemExit("Aurex Render Core hiện chỉ hỗ trợ build trên macOS.")
    subprocess.run([
        "swift",
        "build",
        "--package-path",
        str(PACKAGE),
        "-c",
        args.configuration,
    ], check=True, cwd=ROOT)
    bin_dir = subprocess.check_output([
        "swift",
        "build",
        "--package-path",
        str(PACKAGE),
        "-c",
        args.configuration,
        "--show-bin-path",
    ], cwd=ROOT, text=True).strip()
    source = Path(bin_dir) / "aurex-render"
    if not source.is_file():
        raise FileNotFoundError(f"Swift build không tạo ra aurex-render: {source}")
    destination = args.output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    destination.chmod(0o755)
    print(destination)


if __name__ == "__main__":
    main()
