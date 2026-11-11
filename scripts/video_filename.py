#!/usr/bin/env python3
"""Build the final MP4 filename from lyrics.title and the production date."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

UNSAFE_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
WHITESPACE = re.compile(r"\s+")
MAX_TITLE_CHARACTERS = 60
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def video_filename(title: str, date: str) -> str:
    if not DATE.fullmatch(date):
        raise ValueError("date must use YYYY-MM-DD")
    normalized = unicodedata.normalize("NFC", title)
    safe_title = UNSAFE_CHARACTERS.sub("-", normalized)
    safe_title = WHITESPACE.sub("_", safe_title).strip(" ._-")
    safe_title = safe_title[:MAX_TITLE_CHARACTERS].rstrip(" ._-")
    if not safe_title:
        safe_title = "untitled"
    return f"{safe_title}_{date}.mp4"


def from_lyrics(path: str | Path, date: str) -> str:
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("lyrics.json title must be a non-empty string")
    return video_filename(title, date)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lyrics", required=True, type=Path)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    try:
        print(from_lyrics(args.lyrics, args.date))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot build video filename: {error}") from error


if __name__ == "__main__":
    main()
