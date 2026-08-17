#!/usr/bin/env python3
"""Align topic subtitle lines and pose changes to a final voiceover with Whisper."""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from split_voiceover import detect_silences, snap_split_points_to_speech_starts
from subtitle_timing import _align_script_tokens


VIETNAMESE_RE = re.compile(r"[ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", re.IGNORECASE)
JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff]")
HANGUL_RE = re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text.lower().strip())).strip()


def normalize_word(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower().strip())


def script_char_counts(sample: str) -> dict[str, int]:
    return {
        "ja": len(JAPANESE_RE.findall(sample)),
        "ko": len(HANGUL_RE.findall(sample)),
        "han": len(HAN_RE.findall(sample)),
        "vi": len(VIETNAMESE_RE.findall(sample)),
        "latin": len(re.findall(r"[A-Za-z]", sample)),
    }


def detect_script_language(lines: list[str]) -> str | None:
    """Prefer the dominant script so a few Japanese terms in Vietnamese stay `vi`."""
    sample = " ".join(lines)
    counts = script_char_counts(sample)
    cjk = counts["ja"] + counts["ko"] + counts["han"]
    viet_body = counts["vi"] + counts["latin"]

    # Vietnamese narration with loanwords/terms like マイナンバー or 在留カード.
    if counts["vi"] > 0 and viet_body >= max(8, cjk * 2):
        return "vi"
    if counts["ja"] > 0 and counts["ja"] + counts["han"] >= max(4, viet_body):
        return "ja"
    if counts["ko"] > 0 and counts["ko"] >= max(4, viet_body):
        return "ko"
    if counts["vi"] > 0:
        return "vi"
    if counts["ja"] > 0:
        return "ja"
    if counts["han"] > 0 and counts["han"] >= max(4, counts["latin"]):
        return "zh"
    if counts["han"] > 0 and counts["latin"] == 0:
        return "zh"
    return None


def uses_cjk_alignment(language: str | None, lines: list[str]) -> bool:
    # Do not flip into character-level CJK alignment just because a Vietnamese
    # script contains a few Japanese/Chinese terms.
    if language in {"ja", "zh"}:
        return True
    if language == "vi":
        return False
    sample = " ".join(lines)
    counts = script_char_counts(sample)
    cjk = counts["ja"] + counts["han"]
    viet_body = counts["vi"] + counts["latin"]
    if counts["vi"] > 0 and viet_body >= max(8, cjk * 2):
        return False
    return bool(JAPANESE_RE.search(sample)) or (counts["han"] > 0 and counts["latin"] == 0)


def compact_characters(text: str) -> list[str]:
    return list(re.sub(r"[^\w]", "", text.lower()))


def alignment_tokens(text: str, *, cjk: bool) -> list[str]:
    if cjk:
        return [character for character in text.strip() if not character.isspace()]
    return text.split()


def expand_transcript_characters(words: list[dict]) -> list[dict]:
    characters = []
    for word in words:
        values = compact_characters(str(word.get("word") or ""))
        if not values:
            continue
        start = float(word["start"])
        end = max(start, float(word["end"]))
        span = (end - start) / len(values)
        for index, value in enumerate(values):
            characters.append({
                "word": value,
                "start": start + span * index,
                "end": start + span * (index + 1),
            })
    return characters


def split_cjk_indexes(words: list[dict], lines: list[str]) -> tuple[list[int], list[dict]]:
    transcript_characters = expand_transcript_characters(words)
    if len(transcript_characters) < len(lines):
        raise RuntimeError("Whisper nhận quá ít ký tự để căn subtitle CJK.")
    transcript_text = "".join(str(item["word"]) for item in transcript_characters)
    line_characters = [compact_characters(line) for line in lines]
    total_script = sum(len(line) for line in line_characters) or 1
    total_transcript = len(transcript_characters)
    indexes = []
    previous = 0
    script_cursor = 0
    for line_index, current_line in enumerate(line_characters[:-1]):
        script_cursor += len(current_line)
        target = max(previous + 1, int(script_cursor * total_transcript / total_script))
        query = "".join(line_characters[line_index + 1][:24])
        start = max(previous + 1, target - 70)
        end = min(total_transcript - 1, target + 70)
        best_index, best_score = min(max(target, start), end), float("-inf")
        for candidate in range(start, end + 1):
            window = transcript_text[candidate:candidate + len(query) + 6]
            sequence = SequenceMatcher(None, query, window).ratio() * 20
            common_prefix = 0
            for expected, actual in zip(query, window):
                if expected != actual:
                    break
                common_prefix += 1
            score = sequence + common_prefix * 1.5 - abs(candidate - target) * 0.025
            if score > best_score:
                best_index, best_score = candidate, score
        indexes.append(best_index)
        previous = best_index
        print(
            f"Dòng {line_index + 2} bắt đầu {transcript_characters[best_index]['start']:.2f}s: "
            f"{''.join(item['word'] for item in transcript_characters[best_index:best_index + 12])}"
        )
    return indexes, transcript_characters


def transcribe(audio: Path, model_size: str, language: str | None = None) -> list[dict]:
    sys.path.insert(0, str(ROOT))
    from faster_whisper import WhisperModel
    from whisper_models import describe_whisper_model, resolve_whisper_model

    model_ref = resolve_whisper_model(model_size)
    print(f"Whisper: {describe_whisper_model(model_size)} ({model_ref})")
    model = WhisperModel(model_ref, device="cpu", compute_type="int8")
    options = {"word_timestamps": True, "vad_filter": True, "beam_size": 5}
    if language:
        options["language"] = language
    segments, info = model.transcribe(str(audio), **options)
    words = []
    for segment in segments:
        for word in segment.words or []:
            if word.start is None or word.end is None:
                continue
            text = str(word.word or "").strip()
            if text:
                words.append({"word": text, "start": float(word.start), "end": float(word.end)})
    print(f"Whisper nhận {len(words)} từ, ngôn ngữ {info.language}")
    if len(words) < 3:
        raise RuntimeError("Whisper nhận quá ít từ để căn subtitle.")
    return words


def split_indexes(words: list[dict], lines: list[str]) -> list[int]:
    line_words = [normalize_text(line).split() for line in lines]
    total_script = sum(len(line) for line in line_words) or 1
    total_transcript = len(words)
    indexes = []
    previous = 0
    script_cursor = 0
    for line_index, current_line in enumerate(line_words[:-1]):
        script_cursor += len(current_line)
        target = max(previous + 1, int(script_cursor * total_transcript / total_script))
        next_words = line_words[line_index + 1][:10]
        start = max(previous + 1, target - 60)
        end = min(total_transcript - 1, target + 60)
        best_index, best_score = min(max(target, start), end), float("-inf")
        for candidate in range(start, end + 1):
            window = [normalize_word(words[candidate + offset]["word"]) for offset in range(min(len(next_words) + 4, total_transcript - candidate))]
            positional = sum(3 if offset < len(window) and value == window[offset] else 1 if offset < len(window) and value and (value in window[offset] or window[offset] in value) else 0 for offset, value in enumerate(next_words))
            sequence = SequenceMatcher(None, next_words, window).ratio() * max(1, len(next_words)) * 2
            first = 6 if next_words and window and next_words[0] == window[0] else 0
            score = positional + sequence + first - abs(candidate - target) * 0.015
            if score > best_score:
                best_index, best_score = candidate, score
        indexes.append(best_index)
        previous = best_index
        print(f"Dòng {line_index + 2} bắt đầu {words[best_index]['start']:.2f}s: {words[best_index]['word']}")
    return indexes


def pose_at(topic: dict, time: float) -> dict:
    fallback = next(iter(topic.get("poseAssets", {}) or {"question": {}}))
    current = {"pose": fallback}
    for event in topic.get("poseTimeline", []):
        if float(event.get("time", 0)) <= time:
            current = event
        else:
            break
    return current


def align_line_words(
    text: str,
    timing_words: list[dict],
    line_start: float,
    line_end: float,
    *,
    cjk: bool,
) -> list[dict]:
    """Align one subtitle line against Whisper words that belong to that line only.

    Global SequenceMatcher over a whole script collapses when phrases repeat
    (common in comparison videos). Per-line matching keeps each occurrence in
    its own time window.
    """
    tokens = alignment_tokens(text, cjk=cjk)
    if not tokens:
        return []
    window_end = max(line_start + 0.12, line_end)
    timed = _align_script_tokens(tokens, timing_words, window_end)
    unique_starts = {round(float(token["start"]), 3) for token in timed}
    # If matching still collapses (empty/noisy Whisper slice), spread evenly.
    if len(tokens) > 1 and len(unique_starts) <= 1:
        span = max(0.12, window_end - line_start)
        step = span / len(tokens)
        timed = [
            {
                "text": token,
                "start": round(line_start + step * index, 3),
                "end": round(line_start + step * (index + 1), 3),
            }
            for index, token in enumerate(tokens)
        ]
    line_words = []
    for token in timed:
        word_start = max(line_start, min(window_end, float(token["start"])))
        word_end = min(window_end, max(word_start + 0.03, float(token["end"])))
        line_words.append({
            "word": str(token["text"]),
            "start": round(word_start, 3),
            "end": round(word_end, 3),
        })
    return line_words


def align_topic(
    topic: dict,
    words: list[dict],
    duration: float,
    *,
    audio: Path | None = None,
    silence_noise: str = "-35dB",
    silence_min_duration: float = 0.18,
    silence_max_distance: float = 0.15,
    language: str | None = None,
) -> dict:
    source_segments = topic.get("segments", [])
    lines = [str(segment.get("text") or "").strip() for segment in source_segments if str(segment.get("text") or "").strip()]
    if not lines:
        raise ValueError("Topic không có script để căn.")
    cjk = uses_cjk_alignment(language, lines)
    if cjk:
        indexes, timing_words = split_cjk_indexes(words, lines)
    else:
        indexes = split_indexes(words, lines)
        timing_words = words
    raw_starts = [max(0.0, timing_words[0]["start"])] + [timing_words[index]["start"] for index in indexes]
    starts = raw_starts
    if audio is not None:
        silences = detect_silences(audio, silence_noise, silence_min_duration)
        print(f"Phát hiện {len(silences)} khoảng lặng; chốt đầu câu theo lúc giọng bắt đầu lại.")
        starts = [raw_starts[0], *snap_split_points_to_speech_starts(raw_starts[1:], silences, silence_max_distance)]
    ends = [max(starts[index] + 0.12, starts[index + 1]) for index in range(len(starts) - 1)]
    ends.append(min(duration, max(starts[-1] + 0.12, words[-1]["end"] + 0.08)))

    # Slice Whisper words by the same boundary indexes used for line starts so
    # repeated phrases cannot steal timestamps from a later occurrence.
    word_boundaries = [0, *indexes, len(timing_words)]
    aligned = []
    pose_rows = []
    for index, text in enumerate(lines):
        source = source_segments[min(index, len(source_segments) - 1)]
        line_whisper = timing_words[word_boundaries[index]:word_boundaries[index + 1]]
        line_words = align_line_words(
            text,
            line_whisper,
            starts[index],
            min(duration, ends[index]),
            cjk=cjk,
        )
        aligned.append({
            "sentenceIndex": index,
            "start": round(starts[index], 3),
            "end": round(min(duration, ends[index]), 3),
            "text": text,
            "words": line_words,
        })
        pose_rows.append(pose_at(topic, float(source.get("start", 0))))
    timeline = []
    previous_pose = ""
    for index, event in enumerate(pose_rows):
        pose = str(event.get("pose") or next(iter(topic.get("poseAssets", {}) or {"question": {}})))
        if index == 0 or pose != previous_pose:
            next_event = {"time": round(starts[index], 3), "pose": pose, "sentenceIndex": index}
            sfx = event.get("sfx") or topic.get("poseSfx", {}).get(pose)
            if sfx and sfx in topic.get("sfx", {}):
                next_event["sfx"] = sfx
            timeline.append(next_event)
        previous_pose = pose
    topic["duration"] = round(duration, 3)
    topic["alignmentVersion"] = 2
    if language:
        topic["language"] = language
    topic["segments"] = aligned
    fallback = next(iter(topic.get("poseAssets", {}) or {"question": {}}))
    topic["poseTimeline"] = timeline or [{"time": 0.0, "pose": fallback}]
    return topic


def existing_alignment_is_compatible(path: Path, lines: list[str], duration: float) -> bool:
    if not path.is_file():
        return False
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    existing_lines = [
        str(segment.get("text") or "").strip()
        for segment in existing.get("segments", [])
        if str(segment.get("text") or "").strip()
    ]
    try:
        existing_duration = float(existing.get("duration") or 0)
    except (TypeError, ValueError):
        return False
    return (
        existing.get("alignmentVersion") == 2
        and existing_lines == lines
        and abs(existing_duration - duration) <= 0.12
    )


def align_topic_without_whisper(
    topic: dict,
    duration: float,
    *,
    audio: Path,
    silence_noise: str,
    silence_min_duration: float,
) -> dict:
    """Deterministic packaged-runtime fallback using script weights + silence."""
    source_segments = topic.get("segments", [])
    rows = [
        segment for segment in source_segments
        if str(segment.get("text") or "").strip()
    ]
    if not rows:
        raise ValueError("Topic không có script để căn.")

    # The render topic has already been scaled to the final TTS duration.
    # Preserve those sentence boundaries when Whisper is unavailable. Character
    # weighting is only a last resort: it can move short opening lines several
    # seconds later than their actual audio, which makes poses lag speech.
    source_end = max((float(segment.get("end") or 0) for segment in rows), default=0.0)
    source_starts = [max(0.0, float(segment.get("start") or 0)) for segment in rows]
    use_source_timing = source_end > 0.12 and all(
        source_starts[index] >= source_starts[index - 1]
        for index in range(1, len(source_starts))
    )
    if use_source_timing:
        scale = duration / source_end
        raw_starts = [min(duration, start * scale) for start in source_starts]
    else:
        weights = [
            max(1, len(normalize_text(str(segment.get("text") or "")).replace(" ", "")))
            for segment in rows
        ]
        total_weight = sum(weights) or len(rows)
        raw_starts = [0.0]
        cursor = 0
        for weight in weights[:-1]:
            cursor += weight
            raw_starts.append(duration * cursor / total_weight)

    silences = detect_silences(audio, silence_noise, silence_min_duration)
    interior_silences = [
        silence for silence in silences
        if silence[0] > 0.05 and silence[1] < duration - 0.05
    ]
    if len(interior_silences) == len(rows) - 1:
        # Full-script TTS inserts one pause between each source line. In that
        # common case silence ends are more accurate than proportional guesses.
        starts = [0.0, *(float(end) for _start, end, _duration in interior_silences)]
        print("Số khoảng lặng khớp số câu; dùng trực tiếp thời điểm giọng bắt đầu lại.")
    else:
        starts = [
            raw_starts[0],
            *snap_split_points_to_speech_starts(raw_starts[1:], silences, 0.75),
        ]
    # Preserve order even if two expected boundaries snap to one silence.
    for index in range(1, len(starts)):
        starts[index] = max(starts[index], starts[index - 1] + 0.12)
    ends = [max(starts[index] + 0.12, starts[index + 1]) for index in range(len(starts) - 1)]
    ends.append(duration)

    aligned = []
    pose_rows = []
    for index, source in enumerate(rows):
        text = str(source.get("text") or "").strip()
        tokens = alignment_tokens(text, cjk=uses_cjk_alignment(detect_script_language([text]), [text]))
        line_start = min(duration, starts[index])
        line_end = min(duration, max(line_start + 0.12, ends[index]))
        step = max(0.03, (line_end - line_start) / max(1, len(tokens)))
        words = [
            {
                "word": token,
                "start": round(min(line_end, line_start + step * token_index), 3),
                "end": round(min(line_end, line_start + step * (token_index + 1)), 3),
            }
            for token_index, token in enumerate(tokens)
        ]
        aligned.append({
            "sentenceIndex": index,
            "start": round(line_start, 3),
            "end": round(line_end, 3),
            "text": text,
            "words": words,
        })
        pose_rows.append(pose_at(topic, float(source.get("start", 0))))

    timeline = []
    previous_pose = ""
    for index, event in enumerate(pose_rows):
        pose = str(event.get("pose") or next(iter(topic.get("poseAssets", {}) or {"question": {}})))
        if index == 0 or pose != previous_pose:
            next_event = {"time": aligned[index]["start"], "pose": pose, "sentenceIndex": index}
            sfx = event.get("sfx") or topic.get("poseSfx", {}).get(pose)
            if sfx and sfx in topic.get("sfx", {}):
                next_event["sfx"] = sfx
            timeline.append(next_event)
        previous_pose = pose

    topic["duration"] = round(duration, 3)
    topic["alignmentVersion"] = 2
    topic["segments"] = aligned
    fallback = next(iter(topic.get("poseAssets", {}) or {"question": {}}))
    topic["poseTimeline"] = timeline or [{"time": 0.0, "pose": fallback}]
    topic["alignmentMethod"] = "script-silence-fallback"
    method = "timing nháp đã scale" if use_source_timing else "độ dài câu"
    print(f"Fallback căn {len(aligned)} dòng bằng {method} và {len(silences)} khoảng lặng.")
    return topic


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("topic", type=Path)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="base")
    parser.add_argument("--silence-noise", default="-35dB")
    parser.add_argument("--silence-min-duration", type=float, default=0.18)
    parser.add_argument("--silence-max-distance", type=float, default=0.15)
    args = parser.parse_args()
    topic = json.loads(args.topic.read_text(encoding="utf-8"))
    lines = [str(segment.get("text") or "").strip() for segment in topic.get("segments", [])]
    language = detect_script_language([line for line in lines if line])
    print(f"Ngôn ngữ căn subtitle: {language or 'auto'}")
    duration = float(topic.get("duration") or 0)
    if existing_alignment_is_compatible(args.output, lines, duration):
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        # Reuse only timing results. Visual/editor properties must come from the
        # current topic so background/layout changes are reflected in renders.
        result = dict(topic)
        for key in ("duration", "segments", "poseTimeline", "language", "alignmentMethod"):
            if key in existing:
                result[key] = existing[key]
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Alignment cache hit: giữ timing, cập nhật visual config tại {args.output}")
        return
    try:
        words = transcribe(args.audio.resolve(), args.model, language=language)
    except ModuleNotFoundError as exc:
        if exc.name != "faster_whisper":
            raise
        print("Whisper không có trong runtime; chuyển sang căn theo script + khoảng lặng.")
        result = align_topic_without_whisper(
            topic,
            duration,
            audio=args.audio.resolve(),
            silence_noise=args.silence_noise,
            silence_min_duration=args.silence_min_duration,
        )
    else:
        duration = duration or float(words[-1]["end"])
        result = align_topic(
            topic,
            words,
            duration,
            audio=args.audio.resolve(),
            silence_noise=args.silence_noise,
            silence_min_duration=args.silence_min_duration,
            silence_max_distance=args.silence_max_distance,
            language=language,
        )
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Đã căn {len(result['segments'])} dòng subtitle.")


if __name__ == "__main__":
    main()
