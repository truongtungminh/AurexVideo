#!/usr/bin/env python3
"""Align topic subtitle lines and pose changes to a final voiceover with Whisper."""

from __future__ import annotations

import argparse
from collections import Counter
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
ALIGNMENT_VERSION = 3


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


def script_token_rarity(line_words: list[list[str]]) -> dict[str, float]:
    counts = Counter(token for line in line_words for token in line)
    return {token: 1.0 / count for token, count in counts.items()}


def distinctive_line_tokens(tokens: list[str], rarity: dict[str, float]) -> list[str]:
    """Prefer tokens that appear once in the script; they disambiguate repeated openings."""
    unique = [token for token in tokens if rarity.get(token, 0) >= 0.999]
    if unique:
        return unique
    ranked = sorted(tokens, key=lambda token: (-rarity.get(token, 0.0), -len(token)))
    return ranked[: max(1, min(3, len(ranked)))]


def window_contains_token(window: list[str], token: str) -> bool:
    if not token:
        return False
    for value in window:
        if not value:
            continue
        if value == token or token in value or value in token:
            return True
    return False


def score_line_start_candidate(
    words: list[dict],
    candidate: int,
    next_words: list[str],
    *,
    rarity: dict[str, float],
    target: int,
) -> float:
    total_transcript = len(words)
    window = [
        normalize_word(words[candidate + offset]["word"])
        for offset in range(min(len(next_words) + 4, total_transcript - candidate))
    ]
    positional = 0.0
    for offset, value in enumerate(next_words):
        weight = 2.0 + 8.0 * rarity.get(value, 0.2)
        if offset < len(window) and value == window[offset]:
            positional += 3.0 * weight
        elif offset < len(window) and value and (value in window[offset] or window[offset] in value):
            positional += 1.0 * weight
    sequence = SequenceMatcher(None, next_words, window).ratio() * max(1, len(next_words)) * 2
    first = 2.0 if next_words and window and next_words[0] == window[0] else 0.0
    distinctive = distinctive_line_tokens(next_words, rarity)
    distinctive_hits = sum(1 for token in distinctive if window_contains_token(window, token))
    distinctive_bonus = 14.0 * distinctive_hits
    if distinctive and distinctive_hits == 0:
        distinctive_bonus -= 18.0
    return positional + sequence + first + distinctive_bonus - abs(candidate - target) * 0.015


def split_indexes(words: list[dict], lines: list[str]) -> list[int]:
    line_words = [normalize_text(line).split() for line in lines]
    rarity = script_token_rarity(line_words)
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
        search_indexes = set(range(start, end + 1))
        for token in distinctive_line_tokens(next_words, rarity):
            for word_index, word in enumerate(words):
                if word_index <= previous:
                    continue
                if window_contains_token([normalize_word(str(word.get("word") or ""))], token):
                    for neighbor in range(max(previous + 1, word_index - 4), min(total_transcript, word_index + 1)):
                        search_indexes.add(neighbor)
        best_index, best_score = min(max(target, start), end), float("-inf")
        for candidate in sorted(search_indexes):
            score = score_line_start_candidate(
                words,
                candidate,
                next_words,
                rarity=rarity,
                target=target,
            )
            if score > best_score:
                best_index, best_score = candidate, score
        indexes.append(best_index)
        previous = best_index
        print(f"Dòng {line_index + 2} bắt đầu {words[best_index]['start']:.2f}s: {words[best_index]['word']}")
    return indexes


def minimum_line_duration(token_count: int) -> float:
    if token_count <= 1:
        return 0.18
    return max(0.4, min(1.6, token_count * 0.12))


def timeline_quality(
    starts: list[float],
    lines: list[str],
    final_end: float,
    *,
    cjk: bool,
) -> tuple[int, float]:
    """Return crushed-line count and normalized duration deficit."""
    if len(starts) != len(lines) or not starts:
        return len(lines) or 1, float("inf")
    if any(starts[index] <= starts[index - 1] for index in range(1, len(starts))):
        return len(lines), float("inf")

    ends = [*starts[1:], final_end]
    severe = 0
    deficit = 0.0
    for line, start, end in zip(lines, starts, ends):
        token_count = max(1, len(alignment_tokens(line, cjk=cjk)))
        required = max(minimum_line_duration(token_count), token_count * 0.04)
        actual = max(0.0, end - start)
        if actual < required * 0.5:
            severe += 1
        deficit += max(0.0, required - actual) / required
    return severe, deficit


def structural_silence_starts(
    silences: list[tuple[float, float, float]],
    line_count: int,
    first_word_start: float,
    last_word_end: float,
) -> list[float] | None:
    """Pick a clearly separated cluster of long inter-line pauses."""
    needed = line_count - 1
    if needed <= 0:
        return None
    internal = [
        (start, end, pause_duration)
        for start, end, pause_duration in silences
        if start > 0.05
        and end > first_word_start + 0.05
        and start < last_word_end - 0.05
    ]
    if len(internal) < needed:
        return None
    ranked = sorted(internal, key=lambda item: (-item[2], item[1]))
    selected = ranked[:needed]
    rejected = ranked[needed:]
    shortest_selected = min(item[2] for item in selected)
    if shortest_selected < 0.42:
        return None
    if rejected:
        longest_rejected = max(item[2] for item in rejected)
        if not (
            shortest_selected >= longest_rejected * 1.5
            or shortest_selected - longest_rejected >= 0.18
        ):
            return None
    starts = sorted(item[1] for item in selected)
    if any(starts[index] <= starts[index - 1] for index in range(1, len(starts))):
        return None
    if starts[0] <= first_word_start or starts[-1] >= last_word_end:
        return None
    return starts


def ensure_positive_word_timings(
    line_words: list[dict],
    line_start: float,
    line_end: float,
) -> list[dict]:
    """Guarantee every karaoke word has a positive duration inside the line window."""
    if not line_words:
        return []
    window_start = float(line_start)
    count = len(line_words)
    min_span = max(0.12, count * 0.04)
    window_end = max(window_start + min_span, float(line_end))
    step = (window_end - window_start) / count
    needs_spread = False
    provisional = []
    for word in line_words:
        word_start = max(window_start, min(window_end, float(word.get("start", window_start))))
        word_end = min(window_end, max(word_start, float(word.get("end", word_start))))
        if word_end - word_start < 0.03:
            needs_spread = True
        provisional.append((str(word.get("word") or ""), word_start, word_end))
    if needs_spread:
        provisional = [
            (text, window_start + step * index, window_start + step * (index + 1))
            for index, (text, _, _) in enumerate(provisional)
        ]
    repaired = []
    previous_end = window_start
    for text, word_start, word_end in provisional:
        start = max(window_start, min(window_end, max(previous_end, word_start)))
        end = max(start + 0.03, min(window_end, word_end))
        if end > window_end:
            end = window_end
            start = min(start, max(window_start, end - 0.03))
        repaired.append({"word": text, "start": round(start, 3), "end": round(end, 3)})
        previous_end = repaired[-1]["end"]
    return repaired


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
    return ensure_positive_word_timings(line_words, line_start, window_end)


def sync_boundary_indexes_to_starts(
    timing_words: list[dict],
    starts: list[float],
) -> list[int]:
    """Keep Whisper word slices aligned after silence snapping moves line starts."""
    indexes: list[int] = []
    search_from = 0
    for start in starts[1:]:
        best_index = min(search_from, len(timing_words) - 1)
        best_distance = abs(float(timing_words[best_index]["start"]) - start)
        for word_index in range(search_from, len(timing_words)):
            distance = abs(float(timing_words[word_index]["start"]) - start)
            if distance < best_distance:
                best_index = word_index
                best_distance = distance
            if float(timing_words[word_index]["start"]) > start + 0.25:
                break
        indexes.append(best_index)
        search_from = best_index + 1
    return indexes


def repair_short_line_starts(
    timing_words: list[dict],
    lines: list[str],
    starts: list[float],
    duration: float,
) -> list[float]:
    """Pull a crushed line start earlier when Whisper still has that line's rare tokens."""
    line_words = [normalize_text(line).split() for line in lines]
    rarity = script_token_rarity(line_words)
    repaired = list(starts)
    for index, tokens in enumerate(line_words):
        line_end = repaired[index + 1] if index + 1 < len(repaired) else duration
        if line_end - repaired[index] >= minimum_line_duration(len(tokens)):
            continue
        distinctive = distinctive_line_tokens(tokens, rarity)
        if not distinctive:
            continue
        previous_end = repaired[index - 1] + 0.2 if index > 0 else 0.0
        next_start = repaired[index + 1] if index + 1 < len(repaired) else duration
        candidates = []
        first_token = normalize_word(tokens[0]) if tokens else ""
        for word_index, word in enumerate(timing_words):
            word_start = float(word["start"])
            if word_start < previous_end or word_start >= next_start - 0.15:
                continue
            normalized = normalize_word(str(word.get("word") or ""))
            if not any(window_contains_token([normalized], token) for token in distinctive):
                continue
            phrase_start_index = word_index
            for back in range(word_index - 1, max(-1, word_index - 5), -1):
                previous = normalize_word(str(timing_words[back].get("word") or ""))
                if first_token and window_contains_token([previous], first_token):
                    phrase_start_index = back
                elif phrase_start_index != word_index:
                    break
            candidates.append(float(timing_words[phrase_start_index]["start"]))
        if not candidates:
            continue
        chosen = min(candidates)
        if chosen < repaired[index] - 0.05:
            print(
                f"Sửa biên câu {index + 1}: {repaired[index]:.3f}s → {chosen:.3f}s "
                f"(khôi phục token đặc trưng {', '.join(distinctive[:3])})"
            )
            repaired[index] = chosen
    for index in range(1, len(repaired)):
        if repaired[index] <= repaired[index - 1] + 0.12:
            repaired[index] = repaired[index - 1] + 0.12
    return repaired


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
    silences: list[tuple[float, float, float]] = []
    if audio is not None:
        silences = detect_silences(audio, silence_noise, silence_min_duration)
        print(f"Phát hiện {len(silences)} khoảng lặng; chốt đầu câu theo lúc giọng bắt đầu lại.")
        starts = [raw_starts[0], *snap_split_points_to_speech_starts(raw_starts[1:], silences, silence_max_distance)]
    starts = repair_short_line_starts(timing_words, lines, starts, duration)
    final_end = min(duration, max(starts[-1] + 0.12, words[-1]["end"] + 0.08))
    severe, deficit = timeline_quality(starts, lines, final_end, cjk=cjk)
    used_structural_silences = False
    if silences and (severe > 0 or deficit >= 0.75):
        silence_boundaries = structural_silence_starts(
            silences,
            len(lines),
            float(timing_words[0]["start"]),
            float(timing_words[-1]["end"]),
        )
        if silence_boundaries is not None:
            candidate_starts = [max(0.0, float(timing_words[0]["start"])), *silence_boundaries]
            candidate_final_end = min(
                duration,
                max(candidate_starts[-1] + 0.12, words[-1]["end"] + 0.08),
            )
            candidate_severe, candidate_deficit = timeline_quality(
                candidate_starts,
                lines,
                candidate_final_end,
                cjk=cjk,
            )
            clearly_better = (
                candidate_severe == 0
                and (severe > 0 or candidate_deficit + 0.25 < deficit)
            )
            if clearly_better:
                print("Whisper chia câu không đáng tin cậy; khôi phục biên câu theo các khoảng lặng dài.")
                starts = candidate_starts
                final_end = candidate_final_end
                severe, deficit = candidate_severe, candidate_deficit
                used_structural_silences = True
    if not cjk or used_structural_silences:
        indexes = sync_boundary_indexes_to_starts(timing_words, starts)
    ends = [max(starts[index] + 0.12, starts[index + 1]) for index in range(len(starts) - 1)]
    ends.append(final_end)

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
    for segment in aligned:
        segment_start = float(segment["start"])
        segment_end = float(segment["end"])
        for word in segment["words"]:
            if (
                float(word["start"]) < segment_start - 0.001
                or float(word["end"]) > segment_end + 0.001
                or float(word["end"]) <= float(word["start"])
            ):
                raise RuntimeError(
                    "Whisper căn timing không đáng tin cậy; hãy thử lại hoặc đổi voice."
                )
    topic["duration"] = round(duration, 3)
    topic["alignmentVersion"] = ALIGNMENT_VERSION
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
        existing.get("alignmentVersion") == ALIGNMENT_VERSION
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
    topic["alignmentVersion"] = ALIGNMENT_VERSION
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
        for key in ("duration", "segments", "poseTimeline", "language", "alignmentMethod", "alignmentVersion"):
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
