#!/usr/bin/env python3
"""Generate one full-script voiceover with Maziao, Edge TTS or upload."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

LOCAL_ROOT = Path(__file__).resolve().parents[1]
if str(LOCAL_ROOT) not in sys.path:
    sys.path.insert(0, str(LOCAL_ROOT))


def read_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def read_topic(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("topic.json phải là một object JSON.")
    return value


def topic_tts_config(topic: dict) -> dict:
    config: dict = {}
    for key in ("tts", "ttsConfig", "tts_config"):
        value = topic.get(key)
        if isinstance(value, dict):
            config.update(value)
    if isinstance(topic.get("speakers"), (dict, list)) and "speakers" not in config:
        config["speakers"] = topic["speakers"]
    for key in ("ttsMode", "tts_mode", "modelId", "model_id"):
        if topic.get(key) is not None and key not in config:
            config[key] = topic[key]
    return config


def resolve_tts_mode(requested: str, config: dict) -> str:
    raw = str(requested or "").strip()
    if not raw or raw.casefold() == "auto":
        raw = str(config.get("mode") or config.get("ttsMode") or config.get("tts_mode") or "paragraph")
    from tts.maziao import normalize_tts_mode

    return normalize_tts_mode(raw)


def script_text(topic_path: Path) -> str:
    topic = read_topic(topic_path)
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


def generate_vieneu(
    text: str, output: Path, config: dict, voice: str, tts_config: dict
) -> None:
    """Synthesize voiceover using VieNeu-TTS directly."""
    from tts.vieneu_adapter import generate_vieneu_voiceover

    vieneu_cfg = config.get("vieneu") if isinstance(config.get("vieneu"), dict) else {}
    mode = str(tts_config.get("mode") or vieneu_cfg.get("mode") or "v3turbo").strip()
    device = str(tts_config.get("device") or vieneu_cfg.get("device") or "cpu").strip()
    ref_audio = str(
        tts_config.get("refAudio")
        or tts_config.get("ref_audio")
        or vieneu_cfg.get("ref_audio")
        or ""
    ).strip()

    generate_vieneu_voiceover(
        text=text,
        output_mp3=output,
        voice_id=voice,
        mode=mode,
        device=device,
        ref_audio=ref_audio or None,
    )


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
    parser.add_argument(
        "--engine", choices=["maziao", "elevenlabs", "edge", "vieneu", "aurextts"], required=True
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--voice", required=True)
    parser.add_argument("--model-id", default="")
    parser.add_argument("--tts-mode", choices=["auto", "paragraph", "multiSpeakers"], default="auto")
    parser.add_argument("--tts-config-json", default="")
    args = parser.parse_args()
    topic_path = args.topic.resolve()
    topic = read_topic(topic_path)
    text = script_text(topic_path)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    print(f"Tạo voiceover {args.engine}: {len(text)} ký tự")
    if args.engine == "edge":
        await generate_edge(text, output, args.voice)
    elif args.engine == "maziao":
        from tts.maziao import (
            _resolve_voice,
            generate_maziao_full_audio,
            generate_maziao_multispeakers_audio,
        )

        config = read_config(args.config.resolve())
        maziao_config = config.get("maziao") if isinstance(config.get("maziao"), dict) else {}
        api_key = str(maziao_config.get("api_key") or "").strip()
        api_base = str(maziao_config.get("api_base") or "https://app.maziao.com").strip()
        tts_config = topic_tts_config(topic)
        if args.tts_config_json.strip():
            try:
                override = json.loads(args.tts_config_json)
            except json.JSONDecodeError as exc:
                raise ValueError("--tts-config-json không phải JSON hợp lệ.") from exc
            if not isinstance(override, dict):
                raise ValueError("--tts-config-json phải là một JSON object.")
            tts_config = {**tts_config, **override}
        mode = resolve_tts_mode(args.tts_mode, tts_config)
        voice_id, resolved_voice_model = _resolve_voice(args.voice)
        model_id = str(
            args.model_id
            or tts_config.get("modelId")
            or tts_config.get("model_id")
            or resolved_voice_model
            or "vieten_speech"
        ).strip()
        if mode == "multiSpeakers":
            generated = await generate_maziao_multispeakers_audio(
                topic_path.parent,
                output.parent,
                text.splitlines(),
                voice=voice_id,
                model_id=model_id,
                segments=topic.get("segments"),
                speaker_config=tts_config,
                api_key=api_key or None,
                api_base=api_base or None,
                full_text=None,
            )
        else:
            generated = await generate_maziao_full_audio(
                topic_path.parent,
                output.parent,
                text.splitlines(),
                voice=voice_id,
                model_id=model_id,
                api_key=api_key or None,
                api_base=api_base or None,
                full_text=text,
            )
        if output.exists() is False and generated.exists():
            generated.replace(output)
    elif args.engine in {"vieneu", "aurextts"}:
        tts_config = topic_tts_config(topic)
        if args.tts_config_json.strip():
            try:
                override = json.loads(args.tts_config_json)
            except json.JSONDecodeError as exc:
                raise ValueError("--tts-config-json không phải JSON hợp lệ.") from exc
            if not isinstance(override, dict):
                raise ValueError("--tts-config-json phải là một JSON object.")
            tts_config = {**tts_config, **override}
        await asyncio.to_thread(
            generate_vieneu,
            text,
            output,
            read_config(args.config.resolve()),
            args.voice,
            tts_config,
        )
    else:
        model_id = args.model_id or "eleven_v3"
        await asyncio.to_thread(generate_elevenlabs, text, output, read_config(args.config.resolve()), args.voice, model_id)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("TTS không tạo được dữ liệu audio.")
    print(output)


if __name__ == "__main__":
    asyncio.run(main())
