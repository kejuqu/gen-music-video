#!/usr/bin/env python3
"""Diversity engine: make every generated song different from previous ones.

Strategy:
1. Randomly combine a sub-theme, a genre, a vocal timbre and a groove from
   curated pools, avoiding combinations used in recent runs.
2. Inject recent titles/styles into the prompt as an explicit "avoid" list.
3. Provide similarity checks so generate_lyrics.py can reject a duplicate
   title or lyrics that overlap too much with an earlier song, triggering a
   retry with concrete feedback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --- Content pools ---------------------------------------------------------
# All entries must stay inside the project's positive/happy creative direction.

SUB_THEMES = {
    "zh": [
        "清晨第一缕阳光与全新开始",
        "一场说走就走的海边旅行",
        "老朋友重逢的欢笑时光",
        "追逐梦想路上的坚持与热爱",
        "四季轮转中的美好小确幸",
        "城市夜晚灯火里的温暖相遇",
        "运动场上的汗水、冲刺与欢呼",
        "雨后彩虹与重新出发的勇气",
        "星空下许下的愿望与约定",
        "厨房里飘出的香气与家的味道",
        "毕业季里的祝福、拥抱与展望",
        "骑行穿过乡间小路的风与自由",
        "咖啡馆窗边的悠闲午后",
        "登顶那一刻看到的风景",
        "节日集市上的灯火与歌声",
        "种下一颗种子等待它发芽开花",
    ],
    "en": [
        "the first ray of morning sunlight and a brand-new start",
        "a spontaneous road trip to the ocean",
        "laughing with old friends at a long-awaited reunion",
        "persistence and passion on the road to a dream",
        "small joys hidden in the turning seasons",
        "a warm encounter under city lights at night",
        "sweat, sprint and cheers on the sports field",
        "the rainbow after the rain and the courage to start over",
        "wishes and promises made under a starry sky",
        "the smell of home cooking drifting from the kitchen",
        "hugs, blessings and hope at graduation",
        "riding a bicycle down country lanes in the wind",
        "a lazy afternoon in a cafe by the window",
        "the view from the summit after the climb",
        "lights and songs at a holiday market",
        "planting a seed and watching it bloom",
    ],
}

GENRES = [
    "upbeat acoustic folk pop",
    "sunny city pop",
    "feel-good indie pop",
    "bright electronic dance pop",
    "cheerful pop rock",
    "jazzy swing pop",
    "smooth R&B pop",
    "tropical house pop",
    "country-tinged folk pop",
    "uplifting orchestral pop",
    "retro disco funk pop",
    "bouncy synth pop",
    "gospel-inspired celebration pop",
    "light reggae pop",
    "energetic dance pop",
    "warm mandolin folk pop",
]

VOCALS = [
    "bright youthful female vocals",
    "warm heartfelt male vocals",
    "playful female-male duet",
    "cheerful mixed group vocals with harmonies",
    "soft airy female vocals",
    "energetic powerful male vocals",
    "sweet light female vocals",
    "soulful male vocals with ad-libs",
    "layered choir-style chorus vocals",
    "crisp clear female vocals",
    "gentle acoustic male vocals",
    "joyful call-and-response vocals",
]

GROOVES = [
    "around 96 BPM with fingerpicked acoustic guitar and light percussion",
    "around 104 BPM with piano chords, hand claps and a warm bass line",
    "around 112 BPM with a four-on-the-floor beat and bright synths",
    "around 120 BPM with driving drums and jangly electric guitars",
    "around 88 BPM with a laid-back groove, rhodes piano and soft drums",
    "around 126 BPM with a punchy kick, funk bass and brass hits",
    "around 100 BPM with ukulele, whistling and tambourine",
    "around 116 BPM with string ostinatos and a soaring chorus",
    "around 92 BPM with swing drums and walking double bass",
    "around 108 BPM with marimba plucks and tropical percussion",
    "around 124 BPM with sidechained pads and festival energy",
    "around 98 BPM with banjo, stomps and gang-vocal chants",
]

HISTORY_LIMIT = 60          # how many past songs to load per language
RECENT_WINDOW = 7           # combos within the last N entries are avoided
PROMPT_HISTORY_ENTRIES = 10 # how many recent works are listed in the prompt
SIMILARITY_THRESHOLD = 0.45 # lyric token Jaccard at/above this is rejected

CJK_RUN = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")
WORD = re.compile(r"[a-z0-9']+")
TITLE_NOISE = re.compile(r"[\s_\-—–·.,!?！？。、《》\"'()（）]+")


@dataclass
class HistoryEntry:
    date: str
    title: str
    style: str
    combo: dict[str, str]
    tokens: frozenset[str]


def tokenize(text: str) -> frozenset[str]:
    """CJK lyrics become character-bigram sets; other lyrics become word sets."""
    cjk_runs = CJK_RUN.findall(text)
    if cjk_runs:
        joined = "".join(cjk_runs)
        return frozenset(joined[i : i + 2] for i in range(len(joined) - 1))
    return frozenset(WORD.findall(text.lower()))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_history(root: Path, lang: str, limit: int = HISTORY_LIMIT) -> list[HistoryEntry]:
    """Read past lyrics.json files, newest first. Corrupt entries are skipped."""
    entries: list[HistoryEntry] = []
    day_dirs = sorted(
        (d for d in root.glob("????-??-??") if d.is_dir()), reverse=True
    )
    for day_dir in day_dirs:
        path = day_dir / lang / "lyrics.json"
        if not path.is_file():
            continue
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        lyrics = data.get("lyrics")
        if not isinstance(lyrics, str) or not lyrics.strip():
            continue
        combo = data.get("_combo")
        entries.append(
            HistoryEntry(
                date=day_dir.name,
                title=data.get("title") if isinstance(data.get("title"), str) else "",
                style=data.get("style") if isinstance(data.get("style"), str) else "",
                combo={k: v for k, v in combo.items() if isinstance(v, str)} if isinstance(combo, dict) else {},
                tokens=tokenize(lyrics),
            )
        )
        if len(entries) >= limit:
            break
    return entries


def pick_combo(lang: str, history: list[HistoryEntry], rng: Any) -> dict[str, str]:
    """Randomly pick theme x genre x vocal x groove, avoiding recent combos."""
    recent = history[:RECENT_WINDOW]
    recent_themes = {e.combo.get("theme") for e in recent if e.combo}
    recent_pairs = {
        (e.combo.get("genre"), e.combo.get("vocal")) for e in recent if e.combo
    }
    themes = [t for t in SUB_THEMES[lang] if t not in recent_themes] or SUB_THEMES[lang]
    combo: dict[str, str] = {}
    for _ in range(50):
        combo = {
            "theme": rng.choice(themes),
            "genre": rng.choice(GENRES),
            "vocal": rng.choice(VOCALS),
            "groove": rng.choice(GROOVES),
        }
        if (combo["genre"], combo["vocal"]) not in recent_pairs:
            return combo
    return combo


def build_prompt_block(lang: str, combo: dict[str, str], history: list[HistoryEntry]) -> str:
    """Prompt suffix: mandatory diversity directions plus an explicit avoid list."""
    recent_lines = []
    for entry in history[:PROMPT_HISTORY_ENTRIES]:
        if not entry.title:
            continue
        style = entry.style.strip()
        summary = f" — {style[:80]}" if style else ""
        recent_lines.append(f"- {entry.title} ({entry.date}){summary}")
    avoid = "\n".join(recent_lines) if recent_lines else ("（暂无历史作品）" if lang == "zh" else "(no previous songs yet)")

    if lang == "zh":
        return (
            "\n\n差异化要求（本次必须遵守）：\n"
            f"- 本次子主题：「{combo['theme']}」。歌词必须围绕这个具体场景展开，而不是泛泛的励志口号。\n"
            "- 至少 6 行歌词包含与子主题相关的具体意象（场景、物品、动作或感官细节）。\n"
            "- style 字段必须体现以下方向（仍为一句英文，并保留 joyful/uplifting 等积极情绪关键词）：\n"
            f"  曲风 {combo['genre']}；{combo['groove']}；人声 {combo['vocal']}。\n"
            "- 以下是最近已经生成过的作品，新歌的标题、核心意象和句式必须与它们明显不同：\n"
            f"{avoid}"
        )
    return (
        "\n\nDiversity requirements (mandatory for this run):\n"
        f"- This song's specific sub-theme: {combo['theme']}. Write about this concrete scenario, "
        "not generic motivational slogans.\n"
        "- At least 6 lyric lines must contain vivid, concrete imagery related to the sub-theme "
        "(places, objects, actions, sensory details).\n"
        "- The style field must reflect this direction (one English line, keep a positive keyword "
        "such as joyful/uplifting):\n"
        f"  genre {combo['genre']}; {combo['groove']}; vocals {combo['vocal']}.\n"
        "- Recently generated songs are listed below; the new title, imagery and phrasing must "
        "differ clearly from all of them:\n"
        f"{avoid}"
    )


def normalize_title(title: str) -> str:
    return TITLE_NOISE.sub("", title.lower())


def title_is_unique(title: str, history: list[HistoryEntry]) -> bool:
    normalized = normalize_title(title)
    if not normalized:
        return False
    return all(
        normalize_title(entry.title) != normalized for entry in history if entry.title
    )


def max_lyric_similarity(lyrics: str, history: list[HistoryEntry]) -> float:
    """Highest token-set Jaccard similarity against any historical song."""
    tokens = tokenize(lyrics)
    if not tokens:
        return 0.0
    return max((jaccard(tokens, entry.tokens) for entry in history), default=0.0)
