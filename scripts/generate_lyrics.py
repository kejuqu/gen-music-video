#!/usr/bin/env python3
"""Generate lyrics and structured visual directions with a local Ollama model."""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import ollama

try:
    from scripts import diversity
except ImportError:  # running as a plain script: python3 scripts/generate_lyrics.py
    import diversity

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "style": {"type": "string", "minLength": 1},
        "lyrics": {"type": "string", "minLength": 100},
        "sections": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "visual": {
                        "type": "object",
                        "properties": {
                            "primary_shape": {
                                "type": "string",
                                "enum": [
                                    "particles",
                                    "crystal",
                                    "torus",
                                    "fluid_metaball",
                                    "wireframe_grid",
                                ],
                            },
                            "palette": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                                "maxItems": 4,
                            },
                            "motion_intensity": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "camera_behavior": {
                                "type": "string",
                                "enum": ["slow_orbit", "zoom_in", "shake", "static"],
                            },
                        },
                        "required": [
                            "primary_shape",
                            "palette",
                            "motion_intensity",
                            "camera_behavior",
                        ],
                    },
                },
                "required": ["label", "start_line", "end_line", "visual"],
            },
        },
    },
    "required": ["title", "style", "lyrics", "sections"],
}

MODEL_BY_LANG = {"zh": "qwen3:8b", "en": "llama3.1:8b"}
DEFAULT_THEME = {
    "zh": "阳光、希望、成长、幸福与欢乐",
    "en": "sunshine, hope, growth, happiness and joyful celebration",
}
SHAPES = set(SCHEMA["properties"]["sections"]["items"]["properties"]["visual"]["properties"]["primary_shape"]["enum"])
CAMERAS = set(SCHEMA["properties"]["sections"]["items"]["properties"]["visual"]["properties"]["camera_behavior"]["enum"])
HEX_COLOR = re.compile(r"^#?(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
POSITIVE_STYLE_KEYWORDS = (
    "joyful",
    "uplifting",
    "happy",
    "upbeat",
    "celebratory",
    "bright",
    "positive",
    "hopeful",
)

PROMPTS = {
    "zh": """创作一首中文歌曲的歌词，主题围绕“{theme}”。
整体情绪必须积极向上、幸福欢乐，传递希望、勇气、陪伴与热爱生活；副歌要明亮、有感染力。
避免悲伤、阴暗、孤独、失落、消亡等负面表达。lyrics 字段必须包含完整歌词，不能只写标题或摘要。
为 180 秒（3 分钟）完整歌曲写 32 行正文：依次 [verse] 8 行、[chorus] 8 行、[verse] 8 行、[chorus] 8 行。style 描述简短前奏、段间间奏和自然收尾。
然后为每个段落生成视觉指令：primary_shape 从枚举中选，
palette 使用 2–4 个明亮、温暖或清新的十六进制颜色码，motion_intensity 随欢乐情绪逐步上扬。
每个 section 的 start_line 和 end_line 必须是有效的歌词行范围，且 end_line 不小于 start_line。
camera_behavior 从枚举中选。style 字段必须是一句英文音乐风格描述，明确包含 joyful、uplifting
或其他积极情绪关键词，用于音频生成模型。只返回符合 JSON Schema 的 JSON，不要 Markdown 或解释。""",
    "en": """Write an original song in English about {theme}.
The mood must be positive, uplifting, happy and joyful, celebrating hope, courage and togetherness.
The lyrics field must contain ONLY English song lyrics and section markers, never visual settings.
Use exactly 32 lyric lines in this order: [verse], 8 lyric lines, [chorus], 8 lyric lines,
[verse], 8 lyric lines, [chorus], 8 lyric lines. Do not write Chinese or translate the lyrics.
The style field must be one English line describing joyful uplifting music, a short intro,
instrumental interludes and a natural outro. Keep style separate from lyrics.
Create one visual section for each of the four lyric sections. Choose primary_shape and
camera_behavior from the schema enums, use 2–4 hex colors, and keep motion_intensity between 0 and 1.
start_line and end_line must identify valid lines in the lyrics string, including section markers.
Return only JSON matching the JSON Schema, without Markdown or explanations.""",
}


CJK_CHARACTER = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
SECTION_MARKER = re.compile(
    r"^\s*[\[【]?\s*(verse|chorus|主歌|副歌)(?:\s*\d+)?\s*[:：]?\s*[\]】]?\s*[:：]?\s*$",
    re.IGNORECASE,
)
CANONICAL_SECTION_NAMES = {"verse": "verse", "chorus": "chorus", "主歌": "verse", "副歌": "chorus"}


def normalize_lyrics(data: Any) -> Any:
    """Normalize common, unambiguous formatting deviations from small local models."""
    if not isinstance(data, dict):
        return data

    lyrics = data.get("lyrics")
    if isinstance(lyrics, str):
        lines = []
        for line in lyrics.splitlines():
            marker = SECTION_MARKER.fullmatch(line)
            if marker:
                name = CANONICAL_SECTION_NAMES[marker.group(1).lower()]
                lines.append(f"[{name}]")
            else:
                lines.append(line)
        data["lyrics"] = "\n".join(lines)

    sections = data.get("sections")
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get("visual"), dict):
                continue
            visual = section["visual"]
            intensity = visual.get("motion_intensity")
            if isinstance(intensity, (int, float)) and not isinstance(intensity, bool) and 1 < intensity <= 10:
                visual["motion_intensity"] = intensity / 10

    return data


def validate_lyrics(data: Any, lang: str | None = None) -> dict[str, Any]:
    """Validate fields that the renderer and music generator rely on."""
    if not isinstance(data, dict):
        raise ValueError("model response must be a JSON object")

    for key in ("title", "style", "lyrics"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    if lang == "en" and CJK_CHARACTER.search(data["lyrics"]):
        raise ValueError("English lyrics must not contain Chinese characters")
    if lang == "zh" and not CJK_CHARACTER.search(data["lyrics"]):
        raise ValueError("Chinese lyrics must contain Chinese characters")
    if not any(keyword in data["style"].lower() for keyword in POSITIVE_STYLE_KEYWORDS):
        raise ValueError("style must explicitly describe a positive, happy or uplifting mood")

    lyric_lines = [line.strip() for line in data["lyrics"].splitlines() if line.strip()]
    content_lines = [
        line for line in lyric_lines
        if not (line.startswith("[") and line.endswith("]") and len(line) > 2)
    ]
    if not 12 <= len(content_lines) <= 32:
        raise ValueError(
            f"lyrics must contain 12–32 content lines (verse 8 + chorus 8 = 16), got {len(content_lines)}"
        )
    lowered_markers = {line.lower() for line in lyric_lines if line.startswith("[")}
    if "[verse]" not in lowered_markers or "[chorus]" not in lowered_markers:
        raise ValueError("lyrics must contain [verse] and [chorus] markers")
    if len(data["lyrics"]) > 4000:
        raise ValueError("lyrics exceeds YoVoice's 4000-character limit")

    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("sections must be a non-empty array")

    for index, section in enumerate(sections):
        prefix = f"sections[{index}]"
        if not isinstance(section, dict):
            raise ValueError(f"{prefix} must be an object")
        if not isinstance(section.get("label"), str) or not section["label"].strip():
            raise ValueError(f"{prefix}.label must be a non-empty string")
        start, end = section.get("start_line"), section.get("end_line")
        # bool is an int subclass, but is not a valid line number here.
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 1
            or end < start
            or end > len(lyric_lines)
        ):
            raise ValueError(f"{prefix} has an invalid lyric-line range")

        visual = section.get("visual")
        if not isinstance(visual, dict):
            raise ValueError(f"{prefix}.visual must be an object")
        if visual.get("primary_shape") not in SHAPES:
            raise ValueError(f"{prefix}.visual.primary_shape is unsupported")
        if visual.get("camera_behavior") not in CAMERAS:
            raise ValueError(f"{prefix}.visual.camera_behavior is unsupported")

        palette = visual.get("palette")
        if (
            not isinstance(palette, list)
            or not 2 <= len(palette) <= 4
            or any(not isinstance(color, str) or not HEX_COLOR.fullmatch(color) for color in palette)
        ):
            raise ValueError(f"{prefix}.visual.palette must contain 2–4 hex colors")

        intensity = visual.get("motion_intensity")
        if (
            isinstance(intensity, bool)
            or not isinstance(intensity, (int, float))
            or not 0 <= intensity <= 1
        ):
            raise ValueError(f"{prefix}.visual.motion_intensity must be between 0 and 1")

    return data


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_lyrics_text_atomic(path: Path, lyrics: str) -> None:
    """Write plain lyrics without structural markers or visual metadata."""
    lines = []
    for line in lyrics.splitlines():
        if line.strip().startswith("[") and line.strip().endswith("]"):
            line = ""
        if not line.strip() and (not lines or not lines[-1].strip()):
            continue
        lines.append(line)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--lang", default=os.environ.get("LYRICS_LANG", "zh"), choices=("zh", "en"))
    parser.add_argument("--theme", default=os.environ.get("THEME"))
    parser.add_argument("--attempts", type=int, default=int(os.environ.get("LYRICS_ATTEMPTS", "5")))
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="project root for diversity history (default: MUSIC_VIDEO_ROOT or derived from --output)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.attempts < 1:
        raise SystemExit("--attempts must be at least 1")

    lang = args.lang.lower()
    model = MODEL_BY_LANG[lang]
    if args.root is not None:
        root = args.root
    elif os.environ.get("MUSIC_VIDEO_ROOT"):
        root = Path(os.environ["MUSIC_VIDEO_ROOT"]).expanduser()
    else:  # <root>/<date>/<lang>/lyrics.json -> root
        root = args.output.resolve().parents[2]
    history = diversity.load_history(root, lang)
    rng = random.SystemRandom()
    combo = diversity.pick_combo(lang, history, rng)
    if args.theme:
        combo["theme"] = args.theme
    base_prompt = PROMPTS[lang].format(theme=combo["theme"])
    base_prompt += diversity.build_prompt_block(lang, combo, history)
    print(
        f"Diversity combo: theme={combo['theme']!r} genre={combo['genre']!r} "
        f"vocal={combo['vocal']!r}; history={len(history)} songs",
        flush=True,
    )
    last_error: Exception | None = None

    for attempt in range(args.attempts):
        print(f"Lyrics: {model}, attempt {attempt + 1}/{args.attempts}; waiting for Ollama...", flush=True)
        try:
            prompt = base_prompt
            if last_error is not None:
                prompt = (
                    f"{base_prompt}\n\nYour previous attempt failed validation: {last_error}\n"
                    "Fix exactly that problem and keep the exact section structure described above."
                )
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                format=SCHEMA,
                think=False,
                options={
                    "temperature": 0.9 if attempt == 0 else 0.5,
                    "seed": rng.randrange(0, 2**31),
                },
            )
            content = response["message"]["content"]
            # Preserve the model reply for diagnosis if validation fails.
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.with_suffix(".raw.txt").write_text(content, encoding="utf-8")
            data = validate_lyrics(normalize_lyrics(json.loads(content)), lang=lang)
            if not diversity.title_is_unique(data["title"], history):
                raise ValueError(
                    f"title '{data['title']}' duplicates a previously generated song; "
                    "choose a clearly different title"
                )
            similarity = diversity.max_lyric_similarity(data["lyrics"], history)
            if similarity >= diversity.SIMILARITY_THRESHOLD:
                raise ValueError(
                    f"lyrics are too similar to a previously generated song "
                    f"(token similarity {similarity:.2f}); use new imagery and sentence patterns"
                )
            data["_lang"] = lang
            data["_model"] = model
            data["_combo"] = combo
            write_json_atomic(args.output, data)
            write_lyrics_text_atomic(args.output.with_suffix(".txt"), data["lyrics"])
            print(f"OK {model} -> {args.output}")
            return
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            last_error = error
        except Exception as error:  # Ollama transport/server errors are retryable too.
            last_error = error

        print(
            f"lyrics attempt {attempt + 1}/{args.attempts} failed: {last_error}",
            file=sys.stderr,
        )
        if attempt + 1 < args.attempts:
            time.sleep(2)

    raise SystemExit(f"lyrics generation failed after {args.attempts} attempts: {last_error}")


if __name__ == "__main__":
    main()
