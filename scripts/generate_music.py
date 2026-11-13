#!/usr/bin/env python3
"""Generate a song from lyrics.json through the local YoVoice HTTP API."""

from __future__ import annotations

import argparse
import json
import os
import math
import subprocess
from pathlib import Path
from typing import Any

import requests

DEFAULT_MODEL_ID = "ace-step-1.5-turbo-q8"
DEFAULT_DURATION = int(os.environ.get("MUSIC_DURATION", "180"))
PREFERRED_MODEL_IDS = (
    "ace-step-1.5-turbo-q8",
    "ace-step-1.5-q8",  # Compatibility with older YoVoice catalogs.
    "ace-step-1.5-xl-turbo-q8",
    "ace-step-1.5-turbo-bf16",
    "ace-step-1.5-xl-turbo-bf16",
)


def api_base() -> str:
    explicit = os.environ.get("YOVOICE_API_BASE")
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("YOVOICE_HOST", "127.0.0.1")
    port = os.environ.get("YOVOICE_PORT", "8080")
    return f"http://{host}:{port}"


def load_token() -> str:
    inline = os.environ.get("YOVOICE_API_TOKEN", "").strip()
    if inline:
        return inline

    root = Path(os.environ.get("MUSIC_VIDEO_ROOT", "~/Desktop/music-video")).expanduser()
    token_path = Path(
        os.environ.get("YOVOICE_TOKEN_FILE", str(root / ".yovoice-token"))
    ).expanduser()
    token = token_path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError(f"YoVoice token is empty: {token_path}")
    return token


def build_payload(data: dict[str, Any], duration: int = DEFAULT_DURATION) -> dict[str, Any]:
    lang = data.get("_lang")
    if lang not in ("zh", "en"):
        raise ValueError("lyrics.json must contain _lang set to 'zh' or 'en'")
    style, lyrics = data.get("style"), data.get("lyrics")
    if not isinstance(style, str) or not style.strip():
        raise ValueError("lyrics.json style must be a non-empty English string")
    if not isinstance(lyrics, str) or not lyrics.strip():
        raise ValueError("lyrics.json lyrics must be a non-empty string")

    return {
        "kind": "music",
        "modelOptions": {"ace_step": {"duration_seconds": duration}},
        "text": style,
        "lyrics": lyrics,
        "instrumental": False,
        "language": lang,
        "modelId": os.environ.get("YOVOICE_MODEL_ID", DEFAULT_MODEL_ID),
    }


def raise_for_status(response: requests.Response, stage: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        detail = response.text.strip()[:2000] or "<empty error response>"
        raise ValueError(f"{stage} failed with HTTP {response.status_code}: {detail}") from error


def resolve_model_id(base: str, headers: dict[str, str], requested: str) -> str:
    """Select an installed ACE-Step model instead of trusting an outdated ID."""
    response = requests.get(f"{base}/v1/models", headers=headers, timeout=(10, 30))
    raise_for_status(response, "YoVoice model listing")
    body = response.json()

    if isinstance(body, list):  # Compatibility with older server variants.
        catalog = body
        installed = body
    elif isinstance(body, dict):
        catalog = body.get("catalog", [])
        installed = body.get("installed", [])
    else:
        raise ValueError("YoVoice /v1/models returned an unexpected JSON shape")

    installed_ids = {
        item if isinstance(item, str) else item.get("id")
        for item in installed
        if isinstance(item, str) or isinstance(item, dict)
    }
    installed_ids.discard(None)

    explicit = os.environ.get("YOVOICE_MODEL_ID")
    if explicit:
        if explicit not in installed_ids:
            available = ", ".join(sorted(installed_ids)) or "<none>"
            raise ValueError(
                f"configured YOVOICE_MODEL_ID '{explicit}' is not installed; installed: {available}"
            )
        return explicit

    candidates = list(PREFERRED_MODEL_IDS)
    if isinstance(catalog, list):
        candidates.extend(
            item["id"]
            for item in catalog
            if isinstance(item, dict)
            and item.get("id")
            and (item.get("family") == "ace_step" or str(item["id"]).startswith("ace-step-"))
        )
    candidates.extend(sorted(model_id for model_id in installed_ids if model_id.startswith("ace-step-")))

    for candidate in dict.fromkeys(candidates):
        if candidate in installed_ids:
            return candidate

    available = ", ".join(sorted(installed_ids)) or "<none>"
    raise ValueError(
        "no installed ACE-Step music model found. Install ACE-Step 1.5 Turbo Q8 "
        f"in YoVoice first; currently installed: {available}"
    )


def generation_id(response: requests.Response) -> str:
    audio_id = response.headers.get("X-Yovoice-Generation-ID")
    if audio_id:
        return audio_id
    try:
        body = response.json()
    except ValueError:
        body = {}
    for key in ("id", "generationId", "generation_id"):
        if isinstance(body, dict) and body.get(key):
            return str(body[key])
    raise ValueError("YoVoice response did not include audio or a generation id")


def is_wav(content: bytes) -> bool:
    return len(content) > 44 and content[:4] in (b"RIFF", b"RF64") and content[8:12] == b"WAVE"


def write_wav_atomic(path: Path, content: bytes, target_duration: int) -> None:
    if not is_wav(content):
        raise ValueError(f"YoVoice returned {len(content)} bytes, but not a valid WAV audio stream")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    mastered = path.with_name(path.name + ".master.wav")
    try:
        temporary.write_bytes(content)
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(temporary),
        ], check=True, capture_output=True, text=True)
        duration = float(probe.stdout.strip())
        if not math.isfinite(duration) or abs(duration - target_duration) > 1:
            raise ValueError(f"expected {target_duration}-second music, received {duration:.3f}s; regenerate with duration support")
        # Correct only sub-second model rounding, without looping a short song.
        fade_start = max(target_duration - 2, 0)
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-i", str(temporary),
            "-af", f"apad,atrim=duration={target_duration},afade=t=in:d=0.05,afade=t=out:st={fade_start}:d=2",
            "-ar", "44100", "-c:a", "pcm_s16le", str(mastered),
        ], check=True)
        mastered.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
        mastered.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lyrics", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help="Song length in seconds (default: MUSIC_DURATION env or 180).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        with args.lyrics.open(encoding="utf-8") as handle:
            data = json.load(handle)
        payload = build_payload(data, duration=args.duration)
        headers = {"Authorization": f"Bearer {load_token()}"}
        base = api_base()
        payload["modelId"] = resolve_model_id(base, headers, payload["modelId"])
        print(f"YoVoice model: {payload['modelId']}", flush=True)

        response = requests.post(
            f"{base}/v1/generate",
            json=payload,
            headers=headers,
            timeout=(10, 1800),
        )
        raise_for_status(response, "YoVoice music generation")

        if is_wav(response.content):
            audio_content = response.content
        else:
            # Compatibility fallback for older asynchronous server variants.
            audio_id = generation_id(response)
            audio = requests.get(
                f"{base}/v1/audio/{audio_id}",
                headers=headers,
                timeout=(10, 300),
            )
            raise_for_status(audio, "YoVoice audio download")
            audio_content = audio.content

        write_wav_atomic(args.output, audio_content, target_duration=args.duration)
        print(f"OK wav -> {args.output}")
    except (OSError, ValueError, KeyError, json.JSONDecodeError, requests.RequestException, subprocess.SubprocessError) as error:
        raise SystemExit(f"music generation failed: {error}") from error


if __name__ == "__main__":
    main()
