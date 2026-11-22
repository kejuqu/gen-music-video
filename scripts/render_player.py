"""Vertical (9:16) music-player style renderer for external songs.

Frosted-glass player card over a deterministic per-song harmonic gradient:
rotating vinyl with a generated cover label, a palette-colored circular
spectrum, floating note particles, a real progress bar, and word-level
karaoke lyrics timed by mlx-whisper alignment.

CLI:
  python scripts/render_player.py --song-wav song.wav --lyrics-txt lyrics.txt \
      --title "歌名" --artist "一千柯橘" --output video.mp4 [--duration 15]
"""

from __future__ import annotations

import argparse
import colorsys
import hashlib
import json
import math
import random
import time
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from render_visualizer import (
    EN_FONT,
    ZH_FONT,
    _has_cjk,
    encode_ffmpeg,
    make_cover,
    perceptual_gain,
    wrap_text,
)
from visgen.audio import AudioProcessor

from lyric_timing import alignment_coverage, compute_timing_tokens
from voice_lyrics import detect_language, parse_voice_lyrics

FPS = 30
RPM = 33.33
DEG_PER_FRAME = RPM * 360.0 / 60.0 / FPS
DEFAULT_ARTIST = "一千柯橘"
COVER_THRESHOLD = 0.5


@dataclass(frozen=True)
class Layout:
    aspect: str
    width: int
    height: int
    disc_radius: int
    label_radius: int
    bar_count: int
    bar_max_len: int
    card_box: tuple[int, int, int, int]
    cover_box: tuple[int, int, int, int]
    lyric_zone: tuple[int, int, int, int]
    card_radius: int = 30


LAYOUT = Layout(
    aspect="9:16",
    width=720,
    height=1280,
    disc_radius=150,
    label_radius=72,
    bar_count=96,
    bar_max_len=100,
    card_box=(50, 70, 670, 710),
    cover_box=(288, 308, 432, 452),
    lyric_zone=(40, 760, 680, 1130),
)


def song_palette(seed_text: str) -> list[tuple[int, int, int]]:
    """Return four deterministic, harmonized colors for a song."""
    seed = hashlib.sha1(seed_text.encode()).hexdigest()
    hue = int(seed[:8], 16) / 0x100000000
    hsv_colors = (
        (hue, 0.75, 1.0),
        (hue + 40 / 360, 0.7, 0.95),
        (hue - 40 / 360, 0.8, 1.0),
        (hue + 0.5, 0.6, 0.95),
    )
    return [
        tuple(int(channel * 255) for channel in colorsys.hsv_to_rgb(h % 1.0, s, v))
        for h, s, v in hsv_colors
    ]


def build_background(
    palette: list[tuple[int, int, int]], layout: Layout
) -> Image.Image:
    """Build a dark harmonic gradient with two soft lights and a vignette."""
    width, height = layout.width, layout.height
    top = np.asarray(palette[0], dtype=np.float32) * 0.11
    bottom = np.asarray(palette[3], dtype=np.float32) * 0.07
    blend = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
    pixels = top[None, None, :] * (1.0 - blend) + bottom[None, None, :] * blend
    pixels = np.broadcast_to(pixels, (height, width, 3)).copy()

    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    nx = (xx - width / 2) / (width / 2)
    ny = (yy - height / 2) / (height / 2)
    vignette = 1.0 - 0.38 * np.clip(np.sqrt(nx * nx + ny * ny), 0.0, 1.0)
    pixels *= vignette[:, :, None]
    background = Image.fromarray(
        np.clip(pixels, 0, 255).astype(np.uint8), mode="RGB"
    ).convert("RGBA")

    lights = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(lights)
    draw.ellipse(
        (-width * 0.35, -height * 0.05, width * 0.55, height * 0.42),
        fill=palette[0] + (72,),
    )
    draw.ellipse(
        (width * 0.48, height * 0.57, width * 1.38, height * 1.03),
        fill=palette[2] + (64,),
    )
    lights = lights.filter(ImageFilter.GaussianBlur(120))
    return Image.alpha_composite(background, lights).convert("RGB")


def build_card(background: Image.Image, layout: Layout) -> Image.Image:
    """Build a full-canvas frosted-glass card from the background."""
    x0, y0, x1, y1 = layout.card_box
    card = background.crop(layout.card_box).convert("RGBA")
    card = card.filter(ImageFilter.GaussianBlur(25))
    card = Image.blend(card, Image.new("RGBA", card.size, (0, 0, 0, 255)), 0.24)
    card = Image.alpha_composite(
        card, Image.new("RGBA", card.size, (255, 255, 255, 26))
    )

    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, card.width - 1, card.height - 1),
        radius=layout.card_radius,
        fill=255,
    )
    card.putalpha(mask)

    layer = Image.new("RGBA", background.size, (0, 0, 0, 0))
    layer.alpha_composite(card, (x0, y0))
    ImageDraw.Draw(layer).rounded_rectangle(
        (x0, y0, x1 - 1, y1 - 1),
        radius=layout.card_radius,
        outline=(255, 255, 255, 52),
        width=1,
    )
    return layer


def cover_for(
    title: str,
    language: str,
    palette: list[tuple[int, int, int]],
    path: Path,
) -> Path:
    """Generate the shared wave-pattern cover using this song's palette."""
    path = Path(path)
    data = {
        "title": title,
        "_lang": language,
        "sections": [
            {
                "visual": {
                    "palette": [
                        "#{:02x}{:02x}{:02x}".format(*color) for color in palette
                    ]
                }
            }
        ],
    }
    make_cover(data, path)
    return path


def build_disc_base(layout: Layout) -> Image.Image:
    """Build the rotation-symmetric vinyl body, rim, and grooves."""
    size = layout.disc_radius * 2
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(disc)
    cx = cy = layout.disc_radius
    draw.ellipse((0, 0, size - 1, size - 1), fill=(14, 14, 17, 255))
    draw.ellipse((0, 0, size - 1, size - 1), outline=(58, 58, 66, 255), width=3)
    for radius in range(layout.label_radius + 10, layout.disc_radius - 10, 4):
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            outline=(255, 255, 255, 10),
            width=1,
        )
    return disc


def build_label_layer(cover: Image.Image, layout: Layout) -> Image.Image:
    """Build the rotating cover label, radial sheen, and spindle hole."""
    size = layout.disc_radius * 2
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    label_size = layout.label_radius * 2
    label = cover.convert("RGB").resize((label_size, label_size))
    mask = Image.new("L", label.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, label_size - 1, label_size - 1), fill=255)
    offset = layout.disc_radius - layout.label_radius
    layer.paste(label, (offset, offset), mask)
    ImageDraw.Draw(layer).ellipse(
        (offset, offset, offset + label_size - 1, offset + label_size - 1),
        outline=(30, 30, 34, 255),
        width=3,
    )

    sheen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sheen_draw = ImageDraw.Draw(sheen)
    band = layout.disc_radius - layout.label_radius
    for start, alpha in ((-30, 26), (150, 16)):
        sheen_draw.arc(
            (8, 8, size - 8, size - 8),
            start=start,
            end=start + 70,
            fill=(255, 255, 255, alpha),
            width=band,
        )
    sheen = sheen.filter(ImageFilter.GaussianBlur(18))
    disc_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(disc_mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    layer = Image.composite(Image.alpha_composite(layer, sheen), layer, disc_mask)

    ImageDraw.Draw(layer).ellipse(
        (
            layout.disc_radius - 5,
            layout.disc_radius - 5,
            layout.disc_radius + 5,
            layout.disc_radius + 5,
        ),
        fill=(10, 10, 12, 255),
    )
    return layer


def note_sprites() -> list[Image.Image]:
    """Draw three white music-note sprites without font glyphs."""
    white = (255, 255, 255, 255)

    single = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(single)
    draw.ellipse((10, 43, 32, 56), fill=white)
    draw.rectangle((28, 9, 32, 48), fill=white)
    draw.polygon(((31, 9), (49, 16), (44, 30), (31, 23)), fill=white)

    beamed = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(beamed)
    draw.ellipse((6, 44, 26, 56), fill=white)
    draw.ellipse((38, 37, 58, 49), fill=white)
    draw.rectangle((22, 13, 26, 48), fill=white)
    draw.rectangle((54, 8, 58, 41), fill=white)
    draw.polygon(((23, 9), (58, 4), (58, 13), (23, 18)), fill=white)

    clef = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(clef)
    draw.arc((21, 2, 46, 39), start=65, end=335, fill=white, width=4)
    draw.arc((12, 20, 52, 62), start=15, end=350, fill=white, width=4)
    draw.arc((21, 31, 44, 54), start=0, end=360, fill=white, width=4)
    draw.line((35, 8, 30, 57), fill=white, width=3)

    return [single, beamed, clef]


def control_icons() -> dict[str, Image.Image]:
    """Draw white transport-control icons on transparent 64px canvases."""
    white = (255, 255, 255, 255)

    def canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        return image, ImageDraw.Draw(image)

    prev, draw = canvas()
    draw.rectangle((11, 14, 15, 50), fill=white)
    draw.polygon(((47, 14), (47, 50), (19, 32)), fill=white)

    pause, draw = canvas()
    draw.rectangle((17, 14, 27, 50), fill=white)
    draw.rectangle((37, 14, 47, 50), fill=white)

    next_icon, draw = canvas()
    draw.polygon(((17, 14), (17, 50), (45, 32)), fill=white)
    draw.rectangle((49, 14, 53, 50), fill=white)

    shuffle, draw = canvas()
    draw.line(((9, 18), (20, 18), (42, 46), (52, 46)), fill=white, width=4)
    draw.line(((9, 46), (20, 46), (42, 18), (52, 18)), fill=white, width=4)
    draw.polygon(((48, 10), (59, 18), (48, 26)), fill=white)
    draw.polygon(((48, 38), (59, 46), (48, 54)), fill=white)

    repeat, draw = canvas()
    draw.arc((10, 11, 54, 53), start=195, end=350, fill=white, width=4)
    draw.arc((10, 11, 54, 53), start=15, end=170, fill=white, width=4)
    draw.polygon(((47, 8), (59, 17), (46, 24)), fill=white)
    draw.polygon(((17, 40), (5, 47), (18, 56)), fill=white)

    return {
        "prev": prev,
        "pause": pause,
        "next": next_icon,
        "shuffle": shuffle,
        "repeat": repeat,
    }


def karaoke_color(
    f: float, palette: list[tuple[int, int, int]]
) -> tuple[int, int, int]:
    """Interpolate a token from dim white to the sung warm-gold color."""
    del palette  # The karaoke foreground intentionally stays theme-independent.
    f = max(0.0, min(1.0, float(f)))
    dim = tuple(channel * 0.55 for channel in (255, 255, 255))
    gold = (255, 209, 102)
    return tuple(
        round(start + (end - start) * f) for start, end in zip(dim, gold)
    )


def _wrap_tokens(
    line_tokens: list[dict], font, max_width: float
) -> list[list[dict]]:
    """Wrap text with render_visualizer and retain each token's timing.

    Latin word tokens are joined with spaces so wrapping keeps word
    boundaries (and rendering re-inserts the gaps); CJK character tokens
    join without separators. Mixed-language lines follow the CJK rule.
    """
    texts = [str(token.get("text", "")) for token in line_tokens]
    separator = "" if any(_has_cjk(text) for text in texts) else " "
    text = separator.join(texts)
    lines = wrap_text(text, font, max_width)[:2]
    if not lines:
        return []

    spans: list[tuple[int, int, dict]] = []
    position = 0
    for token, token_text in zip(line_tokens, texts):
        spans.append((position, position + len(token_text), token))
        position += len(token_text) + len(separator)

    rows: list[list[dict]] = []
    cursor = 0
    for line in lines:
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        line_start = cursor
        line_end = line_start + len(line)
        row: list[dict] = []
        for token_start, token_end, token in spans:
            overlap_start = max(line_start, token_start)
            overlap_end = min(line_end, token_end)
            if overlap_start >= overlap_end:
                continue
            piece = dict(token)
            token_text = str(token.get("text", ""))
            piece["text"] = token_text[
                overlap_start - token_start : overlap_end - token_start
            ]
            row.append(piece)
        rows.append(row)
        cursor = line_end
    return rows


def draw_karaoke_line(
    draw,
    line_tokens: list[dict],
    font,
    center: tuple[float, float],
    t: float,
    palette: list[tuple[int, int, int]],
    max_width: float,
) -> None:
    """Draw centered, token-colored karaoke text with a soft sung-text glow."""
    rows = _wrap_tokens(line_tokens, font, max_width)
    if not rows:
        return
    line_height = getattr(font, "size", 34) + 8
    first_y = center[1] - (len(rows) - 1) * line_height / 2

    for row_index, row in enumerate(rows):
        widths = [draw.textlength(str(token.get("text", "")), font=font) for token in row]
        spaced = not any(
            _has_cjk(str(token.get("text", ""))) for token in row
        )
        gap = draw.textlength(" ", font=font) if spaced else 0.0
        x = center[0] - (sum(widths) + gap * (len(widths) - 1)) / 2
        y = first_y + row_index * line_height
        for token, width in zip(row, widths):
            text = str(token.get("text", ""))
            start = float(token.get("start", 0.0))
            end = float(token.get("end", start))
            if end <= start:
                fraction = float(t >= end)
            else:
                fraction = (t - start) / (end - start)
            fraction = max(0.0, min(1.0, fraction))
            color = karaoke_color(fraction, palette)
            if fraction > 0:
                draw.text(
                    (x + 2, y + 2), text, font=font,
                    fill=color + (80,), anchor="lm",
                )
            draw.text((x, y), text, font=font, fill=color + (255,), anchor="lm")
            x += width + gap


def format_time(seconds: float) -> str:
    """Format a playback position as m:ss."""
    whole_seconds = max(0, int(seconds))
    return f"{whole_seconds // 60}:{whole_seconds % 60:02d}"


def layout_lyric_zone(
    layout: Layout,
    prev_line: str | None,
    cur_tokens: list[dict],
    next_line: str | None,
    fonts,
) -> list[dict]:
    """Lay out dim neighboring lyrics and up to two timed current rows.

    ``fonts`` may be a ``(small, current)`` pair, a ``(prev, current, next)``
    triple, or a mapping with ``small``/``current`` (or per-row) keys. When
    omitted, CJK-capable module defaults are loaded at the spec'd sizes.
    """
    if fonts is None:
        prev_font = next_font = ImageFont.truetype(ZH_FONT, 22)
        current_font = ImageFont.truetype(ZH_FONT, 34)
    elif isinstance(fonts, dict):
        small_font = fonts.get("small")
        prev_font = fonts.get("prev", small_font)
        current_font = fonts["current"]
        next_font = fonts.get("next", small_font)
    elif len(fonts) == 2:
        prev_font = next_font = fonts[0]
        current_font = fonts[1]
    else:
        prev_font, current_font, next_font = fonts

    x0, y0, x1, y1 = layout.lyric_zone
    centers = (y0 + (y1 - y0) / 6, (y0 + y1) / 2, y1 - (y1 - y0) / 6)
    entries: list[dict] = []
    if prev_line:
        entries.append({"text": prev_line, "y": centers[0], "size": 22, "alpha": 70})

    rows = _wrap_tokens(cur_tokens, current_font, x1 - x0)
    line_height = 44
    current_y = centers[1] - (len(rows) - 1) * line_height / 2
    for row in rows:
        entries.append(
            {
                "tokens": row,
                "y": current_y,
                "size": 34,
                "alpha": 255,
                "karaoke": True,
            }
        )
        current_y += line_height

    if next_line:
        entries.append({"text": next_line, "y": centers[2], "size": 22, "alpha": 90})
    return entries


def draw_progress(draw, layout: Layout, t: float, duration: float) -> None:
    """Draw the playback track, glowing position knob, and elapsed/duration."""
    x0, x1 = 130, layout.width - 130
    y = min(layout.height - 48, layout.lyric_zone[3] + 66)
    fraction = 0.0 if duration <= 0 else max(0.0, min(1.0, t / duration))
    knob_x = x0 + (x1 - x0) * fraction
    gold = (255, 209, 102)

    draw.line((x0, y, x1, y), fill=(255, 255, 255, 55), width=4)
    if knob_x > x0:
        draw.line((x0, y, knob_x, y), fill=gold + (230,), width=4)
    draw.ellipse((knob_x - 12, y - 12, knob_x + 12, y + 12), fill=gold + (24,))
    draw.ellipse((knob_x - 8, y - 8, knob_x + 8, y + 8), fill=gold + (60,))
    draw.ellipse((knob_x - 4, y - 4, knob_x + 4, y + 4), fill=gold + (255,))

    font = ImageFont.truetype(EN_FONT, 18)
    draw.text((x0 - 18, y), format_time(t), font=font,
              fill=(255, 255, 255, 180), anchor="rm")
    draw.text((x1 + 18, y), format_time(duration), font=font,
              fill=(255, 255, 255, 180), anchor="lm")


class NoteParticles:
    """Deterministic rising-note particle field, seeded by the song title.

    Twelve slots loop independently: each rises from the bottom edge with a
    per-frame step scaled by audio energy, sways horizontally, fades in and
    out, and respawns at the bottom when it leaves the top.
    """

    SLOT_COUNT = 12
    LIFE_FRAMES = 240  # ~8 s at 30 fps for one bottom-to-top traversal

    def __init__(self, seed_text: str, sprites: list, layout: Layout) -> None:
        self._rng = random.Random(int(hashlib.sha1(seed_text.encode()).hexdigest(), 16))
        self._sprites = sprites
        self._layout = layout
        self._particles = [self._spawn() for _ in range(self.SLOT_COUNT)]

    def _spawn(self) -> dict:
        layout = self._layout
        return {
            "sprite": self._rng.randrange(len(self._sprites)),
            "base_x": self._rng.uniform(0.04, 0.96) * layout.width,
            "phase": self._rng.uniform(0.0, 2.0 * math.pi),
            "sway": self._rng.uniform(10.0, 34.0),
            "scale": self._rng.uniform(0.5, 1.0),
            # staggered so the column always has notes at every height
            "progress": self._rng.uniform(0.0, 1.0),
        }

    def update(self, energy: float) -> list:
        """Advance one frame; return [(sprite, x, y, alpha), ...]."""
        layout = self._layout
        step = (0.6 + 0.8 * max(0.0, min(1.0, energy))) / self.LIFE_FRAMES
        rise = layout.height + 96
        out = []
        for particle in self._particles:
            particle["progress"] += step
            if particle["progress"] >= 1.0:
                particle.update(self._spawn())
                particle["progress"] = step
            progress = particle["progress"]
            sprite = self._sprites[particle["sprite"]]
            x = particle["base_x"] + particle["sway"] * math.sin(
                particle["phase"] + progress * 2.4 * math.pi
            )
            y = layout.height + 48 - rise * progress
            fade = math.sin(min(progress, 1.0) * math.pi)
            alpha = int(50 + 70 * fade)
            out.append((sprite, x, y, alpha))
        return out


def timing_cache_valid(
    timing_path: Path, lyrics_txt_path: Path, wav_path: Path
) -> bool:
    """True iff a cached timing.json exists and is newer than both sources."""
    timing_path = Path(timing_path)
    if not timing_path.exists():
        return False
    timing_mtime = timing_path.stat().st_mtime
    return timing_mtime > Path(lyrics_txt_path).stat().st_mtime and (
        timing_mtime > Path(wav_path).stat().st_mtime
    )


def _line_at(lines: list[dict], starts: list[float], t: float) -> int | None:
    """Index of the lyric line active at time t, or None during gaps."""
    if not lines:
        return None
    idx = bisect_right(starts, t) - 1
    if idx < 0 or t > lines[idx]["end"]:
        return None
    return idx


def render(
    song_wav: Path,
    output: Path,
    title: str,
    artist: str,
    timing: dict,
    palette: list[tuple[int, int, int]],
    layout: Layout,
    duration: float | None = None,
) -> None:
    """Render the silent player-style video; audio is merged later by ffmpeg."""
    audio = AudioProcessor(str(song_wav), fps=FPS)
    total_frames = int((duration or audio.duration) * FPS)
    song_duration = total_frames / FPS

    cover_path = Path(output).parent / "cover.png"
    background = build_background(palette, layout).convert("RGBA")
    card = build_card(background.convert("RGB"), layout)
    disc_base = build_disc_base(layout)
    label_layer = build_label_layer(Image.open(cover_path), layout)
    sprites = note_sprites()
    icons = control_icons()
    particles = NoteParticles(title, sprites, layout)

    title_font = ImageFont.truetype(
        ZH_FONT if _has_cjk(title) else EN_FONT, 34
    )
    artist_font = ImageFont.truetype(
        ZH_FONT if _has_cjk(artist) else EN_FONT, 20
    )
    small_font = ImageFont.truetype(ZH_FONT, 22)
    current_font = ImageFont.truetype(ZH_FONT, 34)

    lines = timing.get("lines", [])
    starts = [line["start"] for line in lines]

    # static glow sprite behind the disc (breathing with energy)
    glow_sprite = Image.new("RGBA", (layout.width, layout.height), (0, 0, 0, 0))
    gx0, gy0, gx1, gy1 = layout.cover_box
    gc_x, gc_y = (gx0 + gx1) // 2, (gy0 + gy1) // 2
    glow_r = int(layout.disc_radius * 1.7)
    ImageDraw.Draw(glow_sprite).ellipse(
        (gc_x - glow_r, gc_y - glow_r, gc_x + glow_r, gc_y + glow_r),
        fill=palette[0] + (70,),
    )
    glow_sprite = glow_sprite.filter(ImageFilter.GaussianBlur(70))

    encoder = encode_ffmpeg(Path(output), layout)
    assert encoder.stdin is not None

    prev_bars = np.zeros(layout.bar_count)
    disc_angle = 0.0
    disc_cx, disc_cy = gc_x, gc_y
    zx0, zy0, zx1, zy1 = layout.lyric_zone
    icon_size = 34
    controls = [
        (icons["prev"], layout.width / 2 - 120),
        (icons["pause"], layout.width / 2 - 17),
        (icons["next"], layout.width / 2 + 86),
        (icons["shuffle"], layout.width / 2 + 180),
        (icons["repeat"], layout.width / 2 - 214),
    ]
    controls_y = layout.card_box[3] - 44

    try:
        for frame_idx in range(total_frames):
            bars = perceptual_gain(
                audio.get_bar_values(
                    frame_idx, layout.bar_count, prev_bars, 0.45
                )
            )
            prev_bars = bars
            energy = float(np.mean(bars))
            t = frame_idx / FPS

            frame = background.copy()

            glow = glow_sprite.copy()
            glow.putalpha(
                glow_sprite.getchannel("A").point(
                    lambda a: int(a * (0.3 + 1.4 * energy))
                )
            )
            frame = Image.alpha_composite(frame, glow)

            # floating notes rise behind the card and lyrics stay on top
            for sprite, x, y, alpha in particles.update(energy):
                scaled = sprite.resize(
                    (int(64 * 0.7), int(64 * 0.7)), Image.LANCZOS
                )
                faded = scaled.copy()
                faded.putalpha(
                    scaled.getchannel("A").point(lambda a: int(a * alpha / 255))
                )
                frame.alpha_composite(faded, (int(x - 22), int(y - 22)))

            frame = Image.alpha_composite(frame, card)

            # vinyl: static body + rotating label/sheen
            disc = disc_base.copy()
            rotated = label_layer.rotate(
                disc_angle,
                resample=Image.BICUBIC,
                center=(layout.disc_radius, layout.disc_radius),
            )
            disc = Image.alpha_composite(disc, rotated)
            frame.alpha_composite(
                disc, (disc_cx - layout.disc_radius, disc_cy - layout.disc_radius)
            )

            draw = ImageDraw.Draw(frame, "RGBA")

            # palette-colored circular spectrum radiating from the disc edge
            inner = layout.disc_radius + 14
            for i in range(layout.bar_count):
                angle = (i / layout.bar_count) * 2 * math.pi - math.pi / 2
                length = layout.bar_max_len * bars[i]
                if length < 2:
                    continue
                color = palette[i % len(palette)]
                cos_a, sin_a = math.cos(angle), math.sin(angle)
                draw.line(
                    [
                        (disc_cx + cos_a * inner, disc_cy + sin_a * inner),
                        (
                            disc_cx + cos_a * (inner + length),
                            disc_cy + sin_a * (inner + length),
                        ),
                    ],
                    fill=color + (int(120 + 135 * bars[i]),),
                    width=3,
                )

            # card header: title + artist
            draw.text(
                (layout.width / 2, layout.card_box[1] + 54),
                title, font=title_font, fill=(255, 255, 255, 255), anchor="mm",
            )
            draw.text(
                (layout.width / 2, layout.card_box[1] + 92),
                artist, font=artist_font, fill=(255, 255, 255, 165), anchor="mm",
            )

            # transport controls
            for icon, cx in controls:
                sprite = icon.resize((icon_size, icon_size), Image.LANCZOS)
                frame.alpha_composite(
                    sprite, (int(cx - icon_size / 2), int(controls_y - icon_size / 2))
                )
            draw = ImageDraw.Draw(frame, "RGBA")

            draw_progress(draw, layout, t, song_duration)

            # three-line lyric zone with karaoke highlighting
            idx = _line_at(lines, starts, t)
            prev_line = lines[idx - 1]["text"] if idx is not None and idx > 0 else (
                lines[-1]["text"] if idx is not None and idx == 0 else None
            )
            next_line = (
                lines[idx + 1]["text"]
                if idx is not None and idx + 1 < len(lines)
                else None
            )
            entries = layout_lyric_zone(
                layout,
                prev_line,
                lines[idx]["tokens"] if idx is not None else [],
                next_line,
                (small_font, current_font),
            )
            for entry in entries:
                if entry.get("karaoke"):
                    draw_karaoke_line(
                        draw,
                        entry["tokens"],
                        current_font,
                        (layout.width / 2, entry["y"]),
                        t,
                        palette,
                        zx1 - zx0,
                    )
                else:
                    font = small_font
                    draw.text(
                        (layout.width / 2, entry["y"]),
                        entry["text"],
                        font=font,
                        fill=(255, 255, 255, entry["alpha"]),
                        anchor="mm",
                    )

            encoder.stdin.write(
                np.asarray(frame.convert("RGB"), dtype=np.uint8).tobytes()
            )
            disc_angle = (disc_angle + DEG_PER_FRAME) % 360.0

            if frame_idx % 300 == 0:
                print(
                    f"[player] Frame {frame_idx}/{total_frames}", flush=True
                )
    finally:
        if encoder.stdin:
            encoder.stdin.close()
        encoder.wait()
    if encoder.returncode != 0:
        raise SystemExit(f"ffmpeg encoding failed (exit={encoder.returncode})")
    print(f"[player] video -> {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--song-wav", required=True, type=Path)
    parser.add_argument("--lyrics-txt", required=True, type=Path)
    parser.add_argument("--title", default=None,
                        help="Song title (default: wav parent folder name).")
    parser.add_argument("--artist", default=DEFAULT_ARTIST)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--duration", type=float, default=None,
                        help="Cap the rendered video at this many seconds.")
    parser.add_argument("--timing", type=Path, default=None,
                        help="timing.json cache path (default: beside output).")
    args = parser.parse_args()

    title = args.title or args.song_wav.parent.name
    language = detect_language(parse_voice_lyrics(args.lyrics_txt.read_text(encoding="utf-8")))

    timing_path = args.timing or (Path(args.output).parent / "timing.json")
    if timing_cache_valid(timing_path, args.lyrics_txt, args.song_wav):
        timing = json.loads(timing_path.read_text(encoding="utf-8"))
        print(f"[player] reused timing cache -> {timing_path}")
    else:
        lines = parse_voice_lyrics(args.lyrics_txt.read_text(encoding="utf-8"))
        timed = compute_timing_tokens(args.song_wav, lines, language)
        coverage = alignment_coverage(timed)
        if coverage < COVER_THRESHOLD:
            raise SystemExit(
                f"alignment coverage below threshold: {coverage:.2f} < {COVER_THRESHOLD}"
            )
        timing = {"language": language, "lines": timed}
        timing_path.parent.mkdir(parents=True, exist_ok=True)
        timing_path.write_text(
            json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[player] timing -> {timing_path} (coverage={coverage:.2f})")

    palette = song_palette(title)
    cover_path = Path(args.output).parent / "cover.png"
    if not cover_path.exists():
        cover_for(title, language, palette, cover_path)

    render(
        args.song_wav,
        args.output,
        title,
        args.artist,
        timing,
        palette,
        LAYOUT,
        duration=args.duration,
    )


if __name__ == "__main__":
    main()
