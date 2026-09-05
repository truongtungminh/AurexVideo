#!/usr/bin/env python3
"""OCR images with DeepSeek-OCR-2 (Universal fork)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def choose_device(requested: str) -> str:
    requested = (requested or "auto").strip().lower()
    if requested == "cpu":
        return "cpu"
    if requested == "mps":
        return "mps"
    if requested == "cuda":
        return "cuda"
    try:
        import torch
    except Exception:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    # Prefer CPU by default here; the MPS path is available via --device mps,
    # but this runtime has shown allocator issues on first load.
    return "cpu"


def read_best_text(output_dir: Path) -> dict[str, object]:
    files = []
    best_text = ""
    best_markdown = ""
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(output_dir).as_posix()
        files.append(rel)
        if path.suffix.lower() in {".md", ".txt", ".json"}:
            try:
                text = path.read_text(encoding="utf-8").strip()
            except Exception:
                continue
            if path.suffix.lower() == ".md" and len(text) > len(best_markdown):
                best_markdown = text
            elif path.suffix.lower() == ".txt" and len(text) > len(best_text):
                best_text = text
            elif path.suffix.lower() == ".json" and not best_text:
                best_text = text
    return {
        "files": files,
        "markdown": best_markdown,
        "text": best_text or best_markdown,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path, help="Image path to OCR")
    parser.add_argument("--prompt", default="<image>\n<|grounding|>Convert the document to markdown.")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "mps", "cuda"])
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--model", default="Dogacel/Universal-DeepSeek-OCR-2")
    args = parser.parse_args()

    image_path = args.image.expanduser().resolve()
    if not image_path.is_file():
        raise SystemExit(f"image not found: {image_path}")

    device = choose_device(args.device)
    if args.output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="dsh-ocr-"))
    else:
        output_dir = args.output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

    # Import lazily so the script can still print a helpful error if dependencies are absent.
    import torch
    from transformers import AutoModel, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    model = AutoModel.from_pretrained(args.model, trust_remote_code=True, use_safetensors=True)
    model = model.eval()
    if device == "mps":
        model = model.to("mps").to(torch.float16)
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    elif device == "cuda":
        model = model.cuda().to(torch.float16)
    else:
        model = model.to("cpu").to(torch.float16)

    result = model.infer(
        tokenizer,
        prompt=args.prompt,
        image_file=str(image_path),
        output_path=str(output_dir),
        base_size=1024,
        image_size=768,
        crop_mode=True,
        save_results=True,
    )

    payload = {
        "model": args.model,
        "device": device,
        "image": str(image_path),
        "output_dir": str(output_dir),
        "result_type": type(result).__name__,
        "result_repr": repr(result)[:4000],
    }
    payload.update(read_best_text(output_dir))
    # Prefer parsed JSON if any JSON file exists.
    for rel in payload["files"]:
        if not rel.endswith(".json"):
            continue
        try:
            data = json.loads((output_dir / rel).read_text(encoding="utf-8"))
            payload["json"] = data
            break
        except Exception:
            continue

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
