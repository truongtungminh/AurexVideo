#!/usr/bin/env python3
"""Transcribe media with this project's bundled faster-whisper model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--template-root",
        type=Path,
        default=ROOT,
    )
    parser.add_argument("--model", default="base")
    parser.add_argument("--language", default="vi")
    args = parser.parse_args()

    sys.path.insert(0, str(args.template_root))
    from faster_whisper import WhisperModel
    from whisper_models import describe_whisper_model, resolve_whisper_model

    model_ref = resolve_whisper_model(args.model)
    print(f"Loading {describe_whisper_model(args.model)} from {model_ref}")
    model = WhisperModel(model_ref, device="cpu", compute_type="int8")
    segments_iter, info = model.transcribe(
        str(args.audio),
        language=args.language,
        word_timestamps=True,
        vad_filter=True,
        beam_size=5,
    )

    segments = []
    words = []
    for index, segment in enumerate(segments_iter, start=1):
        segment_words = []
        for word in segment.words or []:
            if word.start is None or word.end is None:
                continue
            item = {
                "word": str(word.word or "").strip(),
                "start": round(float(word.start), 3),
                "end": round(float(word.end), 3),
                "probability": round(float(word.probability or 0), 4),
            }
            if item["word"]:
                words.append(item)
                segment_words.append(item)
        segments.append(
            {
                "index": index,
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": segment.text.strip(),
                "words": segment_words,
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "engine": "faster-whisper",
        "model": str(model_ref),
        "language": info.language,
        "language_probability": round(float(info.language_probability), 4),
        "duration": round(float(info.duration), 3),
        "segments": segments,
        "words": words,
    }
    (args.output_dir / "transcript.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    transcript_lines = [
        f"[{segment['start']:06.2f} - {segment['end']:06.2f}] {segment['text']}"
        for segment in segments
    ]
    (args.output_dir / "transcript.txt").write_text(
        "\n".join(transcript_lines) + "\n",
        encoding="utf-8",
    )
    srt_blocks = [
        f"{segment['index']}\n{srt_time(segment['start'])} --> {srt_time(segment['end'])}\n{segment['text']}"
        for segment in segments
    ]
    (args.output_dir / "transcript.srt").write_text(
        "\n\n".join(srt_blocks) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(segments)} segments and {len(words)} words to {args.output_dir}")


if __name__ == "__main__":
    main()
