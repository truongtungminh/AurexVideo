from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import requests

from .common import generate_project_tts, normalize_speed, speed_adjust_audio, speed_cache_value

# ── Defaults ──────────────────────────────────────────────────────────
DEFAULT_API_KEY = "mz_9poZkrFDXAcEtLecGZ8dPzz-s3pQb8_N"
DEFAULT_API_BASE = "https://app.maziao.com"
DEFAULT_VOICE_ID = "clone_8ci7vkGMoJLyKe9IJ7MfV"   # OncoinX
# DEFAULT_VOICE_ID = "zv_vi_hn_female_ngochuyen"   # Ngọc Huyền
# DEFAULT_VOICE_ID = "clone_fY2_e5-7EbOgfEQlggwg0"   # Mạnh Dũng V2 (Henry default, updated 2026-07-16)
DEFAULT_MODEL_ID = "vietten_speech"
DEFAULT_SPEED = 1.0
API_POLL_INTERVAL = 2.0
COOLDOWN = 1.0
MIN_TEXT_LENGTH = 100  # Maziao requires > 100 chars per submission
MAX_TEXT_LENGTH = 250_000

# ── Known Vietnamese voices ───────────────────────────────────────────
VN_VOICES: dict[str, dict] = {
    "oncoinx": {
        "id": "clone_8ci7vkGMoJLyKe9IJ7MfV",
        "name": "OncoinX",
        "gender": "male",
        "modelId": "vieten_speech",
    },
    "manhdung": {
        "id": "clone_fY2_e5-7EbOgfEQlggwg0",
        "name": "Mạnh Dũng V2",
        "gender": "male",
        "modelId": "vieten_speech",
    },
    "ngochuyen": {
        "id": "zv_vi_hn_female_ngochuyen",
        "name": "Huyền V2",
        "gender": "female",
        "modelId": "vieten_speech",
    },
    "quananh": {
        "id": "Mb5KTBjEO5jIRCDI1lsgh",
        "name": "Quân Anh",
        "gender": "male",
        "modelId": "vietten_speech",
    },
    "cdmedia": {
        "id": "BGC20Gf5f36lf17lw2Y1N",
        "name": "CD Media",
        "gender": "male",
        "modelId": "vietten_speech",
    },
    "truyen-mix": {
        "id": "CxkcPrBRQuv5pMvgSIC76",
        "name": "Truyện Mix",
        "gender": "male",
        "modelId": "vieten_speech",
    },
    "ngoc-ngan": {
        "id": "-4nlK6yCZmRIWA5hVidlR",
        "name": "Ngọc Ngạn V2",
        "gender": "male",
        "modelId": "vieten_speech",
    },
    "adam": {
        "id": "3SRMFGscuNxpC2KtNNwio",
        "name": "Adam (Chào các con vợ)",
        "gender": "male",
        "modelId": "vietten_speech",
    },
    "dinh-doan": {
        "id": "LS3bfpRnrXc7utzd556Ex",
        "name": "Đinh Đoàn",
        "gender": "male",
        "modelId": "vietten_speech",
    },
}


def _resolve_api_config(
    api_key: str | None = None,
    api_base: str | None = None,
) -> tuple[str, str, dict]:
    key = str(api_key or DEFAULT_API_KEY).strip()
    base = str(api_base or DEFAULT_API_BASE).rstrip("/")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    return key, base, headers


def _resolve_voice(voice: str | None = None) -> tuple[str, str]:
    """Resolve a voice shortcut or preserve a direct voice ID."""
    raw = str(voice or "").strip()
    shortcut = raw.lower()
    if shortcut in VN_VOICES:
        resolved = VN_VOICES[shortcut]
        return resolved["id"], resolved["modelId"]
    if raw and len(raw) > 10 and re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        return raw, DEFAULT_MODEL_ID
    return DEFAULT_VOICE_ID, DEFAULT_MODEL_ID


def _submit_tts(
    text: str,
    voice_id: str,
    model_id: str,
    api_base: str,
    headers: dict,
) -> dict:
    """Submit TTS task and return {taskId, transactionId, totalCost, remainingCredits}."""
    payload = {
        "mode": "paragraph",
        "modelId": model_id,
        "parts": [
            {
                "text": text,
                "startTime": 0,
                "voiceId": voice_id,
            }
        ],
        "settings": {
            "speed": 1,
            "volume": 0,
            "pitch": 1,
            "export_srt": False,
        },
    }
    r = requests.post(
        f"{api_base}/api/tts/submit",
        json=payload,
        headers=headers,
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if "data" not in data or "taskId" not in data["data"]:
        raise RuntimeError(f"Unexpected submit response: {data}")
    return data["data"]


def _poll_task(task_id: str, api_base: str, headers: dict) -> str:
    """Poll until completed, return resultUrl."""
    while True:
        r = requests.get(
            f"{api_base}/api/tts/{task_id}",
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data", {})
        status = data.get("status", "")
        if status == "completed":
            result_url = data.get("resultUrl", "")
            if not result_url:
                raise RuntimeError(f"Task {task_id} completed but no resultUrl: {data}")
            return result_url
        if status == "failed":
            raise RuntimeError(
                f"Task {task_id} failed: {data.get('msg', 'unknown error')}"
            )
        time.sleep(API_POLL_INTERVAL)


def _download_audio(audio_url: str, output_path: Path, headers: dict) -> None:
    """Download audio from URL to local file.

    Maziao stores results on R2 which may block certain IPs.
    Falls back to downloading via the API with auth headers.
    """
    # Try direct download first
    try:
        r = requests.get(audio_url, timeout=300)
        r.raise_for_status()
        output_path.write_bytes(r.content)
        return
    except requests.HTTPError:
        pass  # Fall through to retry with auth

    # Retry with auth headers (some R2 configs need this)
    r = requests.get(audio_url, headers=headers, timeout=300)
    r.raise_for_status()
    output_path.write_bytes(r.content)


def full_script_text(lines: list[str]) -> str:
    return "\n\n".join(line.strip() for line in lines if line.strip())


async def generate_maziao_full_audio(
    slide_dir: Path,
    output_dir: Path,
    lines: list[str],
    *,
    voice: str | None = None,
    full_text: str | None = None,
    speed: float | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    force: bool = False,
) -> Path:
    """Generate full voiceover via Maziao API (async job, full script).

    Returns path to the generated MP3 file. Suitable for use with
    split_voiceover.py (Whisper alignment).

    Maziao requires text > 100 chars — full-script mode naturally meets this.
    """
    del slide_dir
    resolved_speed = normalize_speed(speed)
    _, resolved_base, headers = _resolve_api_config(api_key, api_base)
    voice_id, model_id = _resolve_voice(voice)
    text = full_text if full_text is not None else full_script_text(lines)
    if not text.strip():
        raise ValueError("No script text to send to Maziao API.")
    if len(text) < MIN_TEXT_LENGTH:
        raise ValueError(
            f"Maziao requires text > {MIN_TEXT_LENGTH} chars (got {len(text)}). "
            f"Use full-script mode or group multiple slides."
        )
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(
            f"Maziao text limit is {MAX_TEXT_LENGTH} chars (got {len(text)})."
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    audio_file = output_dir / "maziao_full_voiceover.mp3"
    meta_file = output_dir / "maziao_full_voiceover.meta.json"
    metadata = {
        "engine": "maziao",
        "mode": "full_voiceover",
        "text": text,
        "lines": lines,
        "voice_id": voice_id,
        "model_id": model_id,
        "speed": resolved_speed,
        "api_base": resolved_base,
    }
    cache_matches = False
    if meta_file.exists():
        try:
            cache_matches = json.loads(meta_file.read_text(encoding="utf-8")) == metadata
        except json.JSONDecodeError:
            cache_matches = False
    if not force and cache_matches and audio_file.exists() and audio_file.stat().st_size > 0:
        print(f"Full Maziao TTS: {audio_file} (cached)")
        return audio_file

    if audio_file.exists():
        audio_file.unlink()

    voice_name = next((k for k, v in VN_VOICES.items() if v["id"] == voice_id), voice_id)
    print(f"Voice: Maziao {voice_name} (model: {model_id}, speed {resolved_speed:g}x, full script)")
    print(f"Submitting full script to Maziao: {len(lines)} slides, {len(text)} chars")

    # Submit job
    loop = asyncio.get_running_loop()
    submit_result = await loop.run_in_executor(
        None, _submit_tts, text, voice_id, model_id, resolved_base, headers,
    )
    task_id = submit_result["taskId"]
    cost = submit_result.get("totalCost", "?")
    remaining = submit_result.get("remainingCredits", "?")
    print(f"  Task submitted: {task_id} (cost: {cost} credits, remaining: {remaining})")

    # Poll until completed
    print(f"  Polling every {API_POLL_INTERVAL}s...")
    audio_url = await loop.run_in_executor(
        None, _poll_task, task_id, resolved_base, headers,
    )
    print(f"  Done: {audio_url}")

    # Download
    print("  Downloading audio...")
    wav_path = output_dir / "maziao_full_voiceover.wav"
    if wav_path.exists():
        wav_path.unlink()
    await loop.run_in_executor(None, _download_audio, audio_url, wav_path, headers)

    # Convert to MP3 (Maziao returns whatever format)
    import subprocess

    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(wav_path),
            "-c:a", "libmp3lame", "-q:a", "2", str(audio_file),
        ],
        check=True, capture_output=True,
    )
    wav_path.unlink(missing_ok=True)

    # Speed adjustment
    if abs(resolved_speed - 1.0) > 0.001:
        await loop.run_in_executor(
            None, speed_adjust_audio, audio_file, audio_file, resolved_speed,
        )
        print(f"  ↳ FFmpeg speed {speed_cache_value(resolved_speed)}x")

    meta_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Full Maziao TTS saved: {audio_file}")
    return audio_file


def _submit_and_poll_single(text: str, voice_id: str, model_id: str, api_base: str, headers: dict) -> bytes:
    """Submit TTS task, poll, download, return raw audio bytes."""
    submit = _submit_tts(text, voice_id, model_id, api_base, headers)
    audio_url = _poll_task(submit["taskId"], api_base, headers)
    # Download to temp, return bytes
    r = requests.get(audio_url, headers=headers, timeout=300)
    r.raise_for_status()
    return r.content


def _group_lines_for_min_length(lines: list[str], min_len: int = MIN_TEXT_LENGTH) -> list[list[tuple[int, str]]]:
    """Group consecutive short lines so each group exceeds min_len chars.

    Returns list of groups, each group is list of (original_index, text).
    """
    groups: list[list[tuple[int, str]]] = []
    current_group: list[tuple[int, str]] = []
    current_len = 0

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        current_group.append((i, line))
        current_len += len(line)

        if current_len >= min_len:
            groups.append(current_group)
            current_group = []
            current_len = 0

    # Don't leave a trailing short group — merge into last group
    if current_group:
        if groups:
            groups[-1].extend(current_group)
        else:
            groups.append(current_group)

    return groups


async def generate_maziao_tts(
    slide_dir: Path,
    output_dir: Path,
    lines: list[str],
    *,
    voice: str | None = None,
    speed: float | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    force: bool = False,
) -> None:
    """Generate per-slide TTS via Maziao API.

    Because Maziao requires 100+ chars per submission, short slides are
    grouped together into one API call, then the resulting audio is split
    using the timing information.
    """
    # ... existing per-slide grouped mode unchanged ...
    resolved_speed = normalize_speed(speed)
    _, resolved_base, headers = _resolve_api_config(api_key, api_base)
    voice_id, model_id = _resolve_voice(voice)
    voice_name = next((k for k, v in VN_VOICES.items() if v["id"] == voice_id), voice_id)

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Voice: Maziao {voice_name} (model: {model_id}, ffmpeg speed {resolved_speed:g}x)")

    # Group lines to meet 100-char minimum
    groups = _group_lines_for_min_length(lines)
    print(f"Grouped {len(lines)} lines into {len(groups)} API calls (100-char minimum)")

    def cache_metadata(index: int, text: str) -> dict:
        return {
            "engine": "maziao",
            "text": text,
            "voice_id": voice_id,
            "model_id": model_id,
            "ffmpeg_speed": f"{resolved_speed:.6g}",
            "api_base": resolved_base,
        }

    async def line_generator(index: int, text: str, audio_file: Path, subtitle_file: Path) -> None:
        del subtitle_file
        loop = asyncio.get_running_loop()
        wav_tmp = audio_file.with_suffix(".wav")
        try:
            raw = await loop.run_in_executor(
                None, _submit_and_poll_single, text, voice_id, model_id, resolved_base, headers,
            )
            wav_tmp.write_bytes(raw)

            # Convert WAV to MP3
            import subprocess
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(wav_tmp),
                    "-c:a", "libmp3lame", "-q:a", "2", str(audio_file),
                ],
                check=True, capture_output=True,
            )

            if abs(resolved_speed - 1.0) > 0.001:
                await loop.run_in_executor(
                    None, speed_adjust_audio, audio_file, audio_file, resolved_speed,
                )
        finally:
            if wav_tmp.exists():
                wav_tmp.unlink()

    await generate_project_tts(
        slide_dir,
        output_dir,
        lines,
        line_generator,
        force=force,
        cooldown=COOLDOWN,
        cache_metadata=cache_metadata,
    )


# ── multiFiles mode ───────────────────────────────────────────────────

def _get_audio_duration(filepath: Path) -> float:
    """Return exact audio duration in seconds via ffprobe."""
    import subprocess, json as _json
    result = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(filepath),
    ], capture_output=True, text=True, check=True)
    data = _json.loads(result.stdout)
    return float(data["format"]["duration"])


async def generate_maziao_multifiles_audio(
    slide_dir: Path,
    output_dir: Path,
    lines: list[str],
    *,
    voice: str | None = None,
    speed: float | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    force: bool = False,
) -> None:
    """Generate per-slide TTS via Maziao multiFiles API.

    Sends all slides in one request (mode: multiFiles), gets back N separate
    audio files with exact durations — no Whisper splitting needed.
    Lines shorter than 100 chars are auto-merged into groups so each part
    meets Maziao's minimum; merged group durations are split proportionally
    by character count per slide.
    """
    resolved_speed = normalize_speed(speed)
    _, resolved_base, headers = _resolve_api_config(api_key, api_base)
    voice_id, model_id = _resolve_voice(voice)
    voice_name = next((k for k, v in VN_VOICES.items() if v["id"] == voice_id), voice_id)

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Voice: Maziao {voice_name} (model: {model_id}, multiFiles mode, speed {resolved_speed:g}x)")

    # Group slides to meet 100-char minimum
    groups = _group_lines_for_min_length(lines)  # list of list[(orig_idx, text)]
    # Each group becomes one part. Build parts list + slide-to-group mapping.
    group_texts: list[str] = []        # joined text per group
    group_slides: list[list[int]] = []  # orig slide indices per group
    for group in groups:
        joined = "\n\n".join(t for _, t in group)
        group_texts.append(joined)
        group_slides.append([orig_idx for orig_idx, _ in group])

    num_groups = len(group_texts)
    print(f"  {len(lines)} slides → {num_groups} multiFiles groups ({num_groups} parts)")

    # ── Submit ──────────────────────────────────────────────────────────
    payload = {
        "mode": "multiFiles",
        "modelId": model_id,
        "parts": [
            {"text": gt, "voiceId": voice_id, "speed": resolved_speed, "pitch": 1.0}
            for gt in group_texts
        ],
        "config": {"volume": 1, "pitch": 1},
    }
    print("Submitting multiFiles TTS task...")
    submit = requests.post(f"{resolved_base}/api/tts/submit", json=payload, headers=headers, timeout=60)
    submit.raise_for_status()
    task_id = submit.json()["data"]["taskId"]
    print(f"Task {task_id} submitted ({num_groups} parts, ~{sum(len(gt) for gt in group_texts)} chars)")

    # ── Poll ────────────────────────────────────────────────────────────
    while True:
        time.sleep(API_POLL_INTERVAL)
        r = requests.get(f"{resolved_base}/api/tts/{task_id}", headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json().get("data", {})
        status = data.get("status", "")
        completed = data.get("completedTasks", 0)
        total = data.get("totalTasks", num_groups)
        print(f"  status={status} ({completed}/{total} completed)")
        if status == "completed":
            result_urls = data.get("resultUrls", [])
            if not result_urls:
                raise RuntimeError(f"multiFiles completed but no resultUrls: {data}")
            print(f"Got {len(result_urls)} result URLs")
            break
        if status == "failed":
            raise RuntimeError(f"Task {task_id} failed: {data.get('msg', 'unknown')}")

    # ── Download each group audio ───────────────────────────────────────
    import subprocess
    group_audio_files: list[Path] = []
    for idx, url in enumerate(result_urls):
        fname = f"tts_group_{idx:02d}.mp3"
        path = output_dir / fname
        if not path.exists():
            print(f"Downloading group {idx}: {url[:80]}...")
            dl = requests.get(url, headers=headers, timeout=60)
            dl.raise_for_status()
            path.write_bytes(dl.content)
        else:
            print(f"Group {idx} already cached: {path}")
        group_audio_files.append(path)

    # ── Split group audio → per-slide audio + timing.json ───────────────
    # For groups with 1 slide → direct mapping
    # For groups with N slides → split total duration by char ratio
    timing: list[dict] = []
    slide_audio_files: list[Path] = []

    for g_idx, af in enumerate(group_audio_files):
        total_dur = _get_audio_duration(af)
        slide_indices = group_slides[g_idx]
        # If single slide in group → direct
        if len(slide_indices) == 1:
            si = slide_indices[0]
            out_name = f"tts_slide_{si:02d}.mp3"
            out_path = output_dir / out_name
            if not out_path.exists():
                import shutil
                shutil.copy2(af, out_path)
            slide_audio_files.append(out_path)
            timing.append({"line": si, "duration": round(total_dur, 3), "text": lines[si].strip()})
            print(f"  slide {si}: {total_dur:.2f}s ← group {g_idx}")
        else:
            # Multiple slides in one group → split by char ratio
            char_lens = [len(lines[si].strip()) for si in slide_indices]
            total_chars = sum(char_lens)
            cumulative = 0.0
            for j, si in enumerate(slide_indices):
                ratio = char_lens[j] / total_chars
                slide_dur = total_dur * ratio
                out_name = f"tts_slide_{si:02d}.mp3"
                out_path = output_dir / out_name
                # Use ffmpeg to cut segment from group audio
                start_sec = cumulative
                end_sec = cumulative + slide_dur
                if not out_path.exists() or force:
                    subprocess.run([
                        "ffmpeg", "-y",
                        "-i", str(af),
                        "-ss", f"{start_sec:.3f}",
                        "-to", f"{end_sec:.3f}",
                        "-c", "copy", str(out_path),
                    ], check=True, capture_output=True)
                slide_audio_files.append(out_path)
                timing.append({"line": si, "duration": round(slide_dur, 3), "text": lines[si].strip()})
                print(f"  slide {si}: {slide_dur:.2f}s (ratio {ratio:.1%}) ← group {g_idx}")
                cumulative += slide_dur

    total = sum(t["duration"] for t in timing)
    print(f"Total multiFiles duration: {total:.2f}s")

    # Write timing.json (flat array format expected by auto_render)
    timing_path = output_dir / "timing.json"
    timing_path.write_text(json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"timing.json written: {timing_path}")

    # Concatenate all per-slide audios into single voiceover
    concat_path = output_dir / "voiceover_concat.mp3"
    concat_list = output_dir / "concat_list.txt"
    concat_list.write_text("\n".join(f"file '{p.name}'" for p in slide_audio_files))
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(concat_path),
    ], check=True, capture_output=True)
    print(f"voiceover_concat.mp3 written ({total:.1f}s)")

    # Save metadata
    meta = {
        "engine": "maziao",
        "mode": "multiFiles",
        "voice": voice_name,
        "voice_id": voice_id,
        "model_id": model_id,
        "speed": resolved_speed,
        "num_groups": num_groups,
        "num_slides": len(lines),
        "total_duration": total,
        "task_id": task_id,
    }
    (output_dir / "maziao_multifiles.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("multiFiles TTS complete.")
