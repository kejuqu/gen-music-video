#!/usr/bin/env python3
"""Align sung lyrics with wall-clock time using local mlx-whisper.

The pipeline generates songs from lyrics.json, but YoVoice returns only the
mixed wav — no per-line timing. This module transcribes the generated song
locally (mlx-whisper, Apple Silicon) and matches transcript segments to the
known lyric lines so the video can display the line currently being sung.

Example:
  python scripts/lyric_timing.py --song song.wav --lyrics-json lyrics.json \
      --output timing.json
"""

from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"
SECTION_TAG = re.compile(r"^\[(verse|chorus|bridge|intro|outro|hook|pre-chorus|instrumental)\d*\]$", re.IGNORECASE)


def extract_lines(lyrics: str) -> list[str]:
    """Return the non-empty, stripped lyric lines in order.

    Section tags like ``[verse]`` are structural metadata, not sung text, so
    they are dropped instead of being shown as subtitle lines.
    """
    return [
        line.strip()
        for line in lyrics.splitlines()
        if line.strip() and not SECTION_TAG.match(line.strip())
    ]


def _normalize(text: str) -> str:
    """Lowercase and drop everything that is not a letter or digit."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def similarity(a: str, b: str) -> float:
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _flatten_words(segments: list[dict[str, Any]]) -> list[tuple[str, float, float]]:
    """Expand transcript words to (normalized char, start, end) entries."""
    chars: list[tuple[str, float, float]] = []
    for seg in segments:
        for word in seg.get("words", []):
            text = _normalize(str(word.get("word", "")))
            for ch in text:
                chars.append((ch, float(word["start"]), float(word["end"])))
    return chars


def align_lines(
    segments: list[dict[str, Any]], lines: list[str]
) -> list[dict[str, Any]]:
    """Align lyric lines to transcript times via global char-level matching.

    Uses per-word timestamps when available (word_timestamps=True): both the
    lyric lines and the transcript are flattened to normalized character
    streams, a global SequenceMatcher alignment maps lyric characters to
    transcript characters, and each line takes the time range of its mapped
    characters. Lines with too few mapped characters fall back to the
    segment-level greedy matcher so sparse transcripts still work.
    """
    chars = _flatten_words(segments)
    if chars:
        timed = _align_by_chars(chars, lines)
        if timed is not None:
            return timed
    return _align_by_segments(segments, lines)


def _align_by_chars(
    chars: list[tuple[str, float, float]], lines: list[str]
) -> list[dict[str, Any]] | None:
    transcript = "".join(ch for ch, _, _ in chars)
    lyric = "".join(_normalize(line) for line in lines)
    if not lyric:
        return None

    # line boundaries within the flattened lyric stream
    bounds: list[tuple[int, int]] = []
    cursor = 0
    for line in lines:
        end = cursor + len(_normalize(line))
        bounds.append((cursor, end))
        cursor = end

    matcher = SequenceMatcher(None, lyric, transcript, autojunk=False)
    mapped: list[list[int]] = [[] for _ in lines]  # transcript char indices per line
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            continue
        for lyric_idx in range(i1, i2):
            line_no = next(
                (n for n, (b0, b1) in enumerate(bounds) if b0 <= lyric_idx < b1),
                None,
            )
            if line_no is None:
                continue
            # replace blocks: only genuinely-equal pairs count as mapped
            if tag == "replace":
                offset = lyric_idx - i1
                if offset >= j2 - j1:
                    continue
                if transcript[j1 + offset] == lyric[lyric_idx]:
                    mapped[line_no].append(j1 + offset)
            else:  # equal: full range maps
                mapped[line_no].append(j1 + (lyric_idx - i1))

    timed: list[dict[str, Any]] = []
    previous_end = 0.0
    any_mapped = False
    for idx, line in enumerate(lines):
        indices = mapped[idx]
        coverage = len(indices) / max(1, bounds[idx][1] - bounds[idx][0])
        if coverage >= 0.5:
            starts = [chars[i][1] for i in indices]
            ends = [chars[i][2] for i in indices]
            start, end = min(starts), max(ends)
            any_mapped = True
        else:
            # unmapped line: slot it just after the previous line
            start = previous_end
            end = previous_end
        timed.append({"start": start, "end": end, "text": line})
        previous_end = max(previous_end, end)
    return timed if any_mapped else None


def _align_by_segments(
    segments: list[dict[str, Any]], lines: list[str]
) -> list[dict[str, Any]]:
    """Fallback: monotonic greedy match on whole transcript segments."""
    matched: list[dict[str, Any] | None] = [None] * len(lines)
    cursor = 0
    for idx, line in enumerate(lines):
        best_pos, best_score = -1, 0.0
        for pos in range(cursor, len(segments)):
            score = similarity(line, segments[pos].get("text", ""))
            if score > best_score:
                best_pos, best_score = pos, score
        if best_pos >= 0 and best_score >= 0.3:
            matched[idx] = segments[best_pos]
            cursor = best_pos + 1

    timed: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        seg = matched[idx]
        if seg is not None:
            timed.append(
                {"start": float(seg["start"]), "end": float(seg["end"]), "text": line}
            )
        elif timed:
            timed.append({"start": timed[-1]["end"], "end": timed[-1]["end"], "text": line})
        else:
            timed.append({"start": 0.0, "end": 0.0, "text": line})
    return timed


def fill_gaps(timed: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    """Extend each line so it stays visible until the next line starts.

    A line never displays longer than MAX_DISPLAY seconds: during long
    instrumental gaps the subtitle clears instead of freezing on one line.
    """
    max_display = 15.0
    for idx, item in enumerate(timed):
        next_start = timed[idx + 1]["start"] if idx + 1 < len(timed) else duration
        end = max(item["end"], next_start, item["start"] + 0.5)
        item["end"] = min(end, item["start"] + max_display, duration)
    return timed


def transcribe(song_path: Path, language: str) -> list[dict[str, Any]]:
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(song_path),
        path_or_hf_repo=WHISPER_MODEL,
        language=language,
        word_timestamps=True,
        verbose=False,
    )
    return list(result.get("segments", []))


def song_duration(song_path: Path) -> float:
    import wave

    with wave.open(str(song_path), "rb") as handle:
        return handle.getnframes() / handle.getframerate()


def compute_timing(
    song_path: Path, lyrics: str, language: str
) -> list[dict[str, Any]]:
    lines = extract_lines(lyrics)
    if not lines:
        return []
    segments = transcribe(song_path, language)
    return fill_gaps(align_lines(segments, lines), song_duration(song_path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--song", required=True, type=Path)
    parser.add_argument("--lyrics-json", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    data = json.loads(args.lyrics_json.read_text(encoding="utf-8"))
    language = data.get("_lang", "zh")
    timed = compute_timing(args.song, data.get("lyrics", ""), language)

    args.output.write_text(
        json.dumps({"language": language, "lines": timed}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(timed)} timed lines -> {args.output}")


if __name__ == "__main__":
    main()
