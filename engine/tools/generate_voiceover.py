#!/usr/bin/env python3
"""Generate one full-script voiceover with ElevenLabs or Edge TTS."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path


def read_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def script_text(topic_path: Path) -> str:
    topic = json.loads(topic_path.read_text(encoding="utf-8"))
    lines = [str(segment.get("text") or "").strip() for segment in topic.get("segments", [])]
    text = "\n\n".join(line for line in lines if line)
    if not text:
        raise ValueError("Project chưa có nội dung script.")
    return text


async def generate_edge(text: str, output: Path, voice: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    with output.open("wb") as file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                file.write(chunk["data"])


def generate_elevenlabs(text: str, output: Path, config: dict, voice: str, model_id: str) -> None:
    from elevenlabs.client import ElevenLabs

    eleven = config.get("elevenlabs", {}) if isinstance(config.get("elevenlabs"), dict) else {}
    api_key = str(os.environ.get("ELEVENLABS_API_KEY") or eleven.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("Chưa cấu hình ElevenLabs API key.")
    output_format = str(eleven.get("output_format") or "mp3_44100_128")
    kwargs = {
        "text": text,
        "voice_id": voice,
        "model_id": model_id,
        "output_format": output_format,
    }
    raw_settings = eleven.get("voice_settings", {})
    if isinstance(raw_settings, dict) and raw_settings:
        try:
            from elevenlabs import VoiceSettings

            allowed = {key: raw_settings[key] for key in ("stability", "similarity_boost", "style", "use_speaker_boost") if key in raw_settings}
            if allowed:
                kwargs["voice_settings"] = VoiceSettings(**allowed)
        except Exception:
            pass
    audio = ElevenLabs(api_key=api_key).text_to_speech.convert(**kwargs)
    with output.open("wb") as file:
        if isinstance(audio, (bytes, bytearray)):
            file.write(bytes(audio))
        else:
            for chunk in audio:
                if chunk:
                    file.write(chunk)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("topic", type=Path)
    parser.add_argument("--engine", choices=["elevenlabs", "edge"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--voice", required=True)
    parser.add_argument("--model-id", default="eleven_v3")
    args = parser.parse_args()
    text = script_text(args.topic.resolve())
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    print(f"Tạo voiceover {args.engine}: {len(text)} ký tự")
    if args.engine == "edge":
        await generate_edge(text, output, args.voice)
    else:
        await asyncio.to_thread(generate_elevenlabs, text, output, read_config(args.config.resolve()), args.voice, args.model_id)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("TTS không tạo được dữ liệu audio.")
    print(output)


if __name__ == "__main__":
    asyncio.run(main())
