#!/usr/bin/env python3
"""Render the music video: rotating vinyl + rainbow circular spectrum + lyrics.

Replaces the previous headless-Blender stage (and the earlier visgen stock
visualizer). The frame layout:

- blurred, darkened cover-art background with vignette
- a vinyl record at center whose label is the cover art, rotating at 33 RPM
- a 96-bar circular spectrum radiating from the disc edge, rainbow-colored
  (hue by bar index, slowly drifting), brightness driven by frequency energy
  through a perceptual log gain
- the currently-sung lyric line at the bottom (timed via lyric_timing)

Supports both 16:9 (landscape, default) and 9:16 (portrait) output.

Example:
  python scripts/render_visualizer.py --lyrics-json lyrics.json \
      --song-wav song.wav --output video.mp4 [--duration 30] [--aspect 9:16]
"""

from __future__ import annotations

import argparse
import colorsys
import json
import math
import shutil
import subprocess
import sys
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lyric_timing import compute_timing  # noqa: E402

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402
from visgen.audio import AudioProcessor  # noqa: E402

FPS = 30
BAR_COUNT = 128
BAR_WIDTH = 3  # thin bars: 128 delicate rays instead of chunky columns
RPM = 33.33  # classic vinyl speed
DEG_PER_FRAME = RPM * 360.0 / 60.0 / FPS
ZH_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
EN_FONT = "/System/Library/Fonts/Helvetica.ttc"


@dataclass(frozen=True)
class Layout:
    aspect: str
    width: int
    height: int
    disc_radius: int
    label_radius: int
    bar_max_len: int
    lyric_y: int
    lyric_size: int


LAYOUTS = {
    "16:9": Layout("16:9", 1280, 720, 205, 96, 150, 90, 44),
    "9:16": Layout("9:16", 720, 1280, 150, 70, 100, 90, 40),
}


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def palette_from_lyrics(data: dict) -> list[tuple[int, int, int]]:
    colors: list[tuple[int, int, int]] = []
    for section in data.get("sections", []):
        for raw in section.get("visual", {}).get("palette", []):
            try:
                colors.append(_hex_rgb(raw))
            except (ValueError, IndexError):
                continue
    return colors or [(255, 215, 0), (147, 112, 219)]


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def make_cover(data: dict, path: Path, size: int = 800) -> None:
    """Generative wave-pattern cover in the song's palette.

    Flowing horizontal wave lines colored by interpolating the palette —
    a nod to classic album-art waveforms, distinct from any single reference.
    """
    palette = palette_from_lyrics(data)
    base = palette[0]
    dark = tuple(int(c * 0.16) for c in palette[-1])

    image = Image.new("RGB", (size, size), dark)
    draw = ImageDraw.Draw(image, "RGBA")
    line_count = 26
    for i in range(line_count):
        t = i / (line_count - 1)
        # interpolate through the full palette (wrap back to base at t=1)
        seg = t * (len(palette) - 1)
        idx = min(int(seg), len(palette) - 2)
        frac = seg - idx
        c0, c1 = palette[idx], palette[idx + 1]
        color = tuple(int(c0[c] * (1 - frac) + c1[c] * frac) for c in range(3))
        alpha = int(70 + 150 * math.sin(t * math.pi) ** 2)
        amp = size * (0.04 + 0.05 * math.sin(t * math.pi))
        phase = t * 4.2
        y0 = size * (0.12 + 0.76 * t)
        points = [
            (
                x,
                y0
                + amp * math.sin(x / size * 4.4 * math.pi + phase)
                + amp * 0.4 * math.sin(x / size * 11 * math.pi + phase * 1.7),
            )
            for x in range(0, size + 8, 8)
        ]
        draw.line(points, fill=color + (alpha,), width=5)
    title = str(data.get("title", "")).strip()
    if title:
        try:
            font = ImageFont.truetype(
                ZH_FONT if _has_cjk(title) else EN_FONT, size // 15
            )
        except OSError:
            font = ImageFont.load_default()
        draw.text(
            (size // 2, size - size // 9), title,
            font=font, fill=(245, 245, 245), anchor="mm",
        )
    image.save(path)


def perceptual_gain(values: np.ndarray) -> np.ndarray:
    """Log gain: lift weak bands, compress peaks — smoother, livelier bars."""
    return np.log1p(values * 4.0) / np.log1p(4.0)


def make_background(cover_path: Path, layout: Layout) -> Image.Image:
    bg = Image.open(cover_path).convert("RGB").resize((layout.width, layout.height))
    bg = bg.filter(ImageFilter.GaussianBlur(38))
    dim = Image.new("RGB", (layout.width, layout.height), (6, 6, 10))
    return Image.blend(bg, dim, 0.55)


def make_vignette(layout: Layout) -> Image.Image:
    """L-mode mask: 255 in the center, 0 at the edges."""
    mask = Image.new("L", (layout.width, layout.height), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse(
        [-layout.width * 0.25, -layout.height * 0.35,
         layout.width * 1.25, layout.height * 1.35],
        fill=255,
    )
    return mask.filter(ImageFilter.GaussianBlur(120))


def make_disc_base(layout: Layout) -> Image.Image:
    """Static vinyl disc: body + rim + concentric grooves (rotation-symmetric)."""
    size = layout.disc_radius * 2
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(disc)
    cx = cy = layout.disc_radius
    draw.ellipse([0, 0, size, size], fill=(14, 14, 17, 255))
    draw.ellipse([0, 0, size, size], outline=(58, 58, 66, 255), width=3)
    for r in range(layout.label_radius + 10, layout.disc_radius - 10, 4):
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            outline=(255, 255, 255, 10),
            width=1,
        )
    return disc


def make_label_layer(cover: Image.Image, layout: Layout) -> Image.Image:
    """Rotating layer: cover-art label + light sheen + spindle hole."""
    size = layout.disc_radius * 2
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    label_size = layout.label_radius * 2
    label = cover.convert("RGB").resize((label_size, label_size))
    mask = Image.new("L", label.size, 0)
    ImageDraw.Draw(mask).ellipse([0, 0, *label.size], fill=255)
    layer.paste(
        label,
        (layout.disc_radius - layout.label_radius,) * 2,
        mask,
    )
    # label rim
    ImageDraw.Draw(layer).ellipse(
        [layout.disc_radius - layout.label_radius] * 2
        + [layout.disc_radius + layout.label_radius] * 2,
        outline=(30, 30, 34, 255), width=3,
    )
    # radial sheen: two soft light arcs, fixed to the disc so rotation shows
    sheen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(sheen)
    band = layout.disc_radius - layout.label_radius
    for start, alpha in ((-30, 26), (150, 16)):
        sdraw.arc(
            [8, 8, size - 8, size - 8],
            start=start, end=start + 70,
            fill=(255, 255, 255, alpha), width=band,
        )
    sheen = sheen.filter(ImageFilter.GaussianBlur(18))
    disc_mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(disc_mask).ellipse([0, 0, size, size], fill=255)
    layer = Image.composite(Image.alpha_composite(layer, sheen), layer, disc_mask)
    # spindle hole
    draw = ImageDraw.Draw(layer)
    draw.ellipse(
        [layout.disc_radius - 5] * 2 + [layout.disc_radius + 5] * 2,
        fill=(10, 10, 12, 255),
    )
    return layer


def make_glow_sprite(color: tuple[int, int, int], layout: Layout) -> Image.Image:
    sprite = Image.new("RGBA", (layout.width, layout.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sprite)
    r = int(layout.disc_radius * 2.1)
    draw.ellipse(
        [layout.width // 2 - r, layout.height // 2 - r,
         layout.width // 2 + r, layout.height // 2 + r],
        fill=color + (60,),
    )
    return sprite.filter(ImageFilter.GaussianBlur(90))


def rainbow_color(index: int, count: int, drift: float) -> tuple[int, int, int]:
    hue = (index / count + drift) % 1.0
    return tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.85, 1.0))


def timed_text_overlays(timing: dict, language: str, layout: Layout):
    font_path = ZH_FONT if language == "zh" else EN_FONT
    font = ImageFont.truetype(font_path, layout.lyric_size)
    lines = [
        (line["start"], line["end"], line["text"])
        for line in timing.get("lines", [])
    ]
    starts = [line[0] for line in lines]
    return font, lines, starts


def wrap_text(text: str, font, max_width: float) -> list[str]:
    """Greedy wrap: CJK text wraps per character, Latin per word."""
    tokens = list(text) if _has_cjk(text) else text.split(" ")
    separator = "" if _has_cjk(text) else " "
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    lines: list[str] = []
    current = ""
    for token in tokens:
        trial = token if not current else current + separator + token
        if not current or probe.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = token
    if current:
        lines.append(current)
    return lines


def draw_lyric(frame: Image.Image, font, lines, starts, t: float, layout: Layout) -> None:
    if not lines:
        return
    idx = bisect_right(starts, t) - 1
    if idx < 0 or t > lines[idx][1]:
        return
    wrapped = wrap_text(
        lines[idx][2], font, layout.width * 0.86
    )[:2]  # at most two subtitle lines
    if not wrapped:
        return

    draw = ImageDraw.Draw(frame, "RGBA")
    line_boxes = [draw.textbbox((0, 0), line, font=font) for line in wrapped]
    widths = [box[2] - box[0] for box in line_boxes]
    heights = [box[3] - box[1] for box in line_boxes]
    line_gap = 8
    block_w = max(widths)
    block_h = sum(heights) + line_gap * (len(wrapped) - 1)
    x, y = layout.width // 2, layout.height - layout.lyric_y
    pad_x, pad_y = 26, 14
    draw.rounded_rectangle(
        [x - block_w // 2 - pad_x, y - block_h // 2 - pad_y,
         x + block_w // 2 + pad_x, y + block_h // 2 + pad_y],
        radius=12, fill=(0, 0, 0, 150),
    )
    cursor_y = y - block_h // 2
    for line, w, h in zip(wrapped, widths, heights):
        draw.text((x, cursor_y + h // 2), line, font=font,
                  fill=(255, 255, 255), anchor="mm")
        cursor_y += h + line_gap


def encode_ffmpeg(output: Path, layout: Layout) -> subprocess.Popen:
    encoder = "h264_videotoolbox" if _has_videotoolbox() else "libx264"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{layout.width}x{layout.height}", "-r", str(FPS),
        "-i", "-",
        "-an", "-c:v", encoder,
        *( ["-b:v", "8M"] if encoder == "h264_videotoolbox" else ["-crf", "20", "-preset", "fast"] ),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output),
    ]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def _has_videotoolbox() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True,
    )
    return "h264_videotoolbox" in probe.stdout


def render(
    data: dict,
    timing: dict,
    cover_path: Path,
    song_wav: Path,
    output: Path,
    layout: Layout,
    duration: float | None,
) -> None:
    audio = AudioProcessor(str(song_wav), fps=FPS)
    total_frames = int((duration or audio.duration) * FPS)

    palette = palette_from_lyrics(data)
    background = make_background(cover_path, layout)
    vignette = make_vignette(layout)
    disc_base = make_disc_base(layout)
    label_layer = make_label_layer(Image.open(cover_path), layout)
    glow_sprite = make_glow_sprite(palette[-1], layout)
    font, lines, starts = timed_text_overlays(timing, data.get("_lang", "zh"), layout)

    encoder = encode_ffmpeg(output, layout)
    assert encoder.stdin is not None

    prev_bars = np.zeros(BAR_COUNT)
    disc_angle = 0.0
    cx, cy = layout.width // 2, layout.height // 2
    try:
        for frame_idx in range(total_frames):
            bars = perceptual_gain(
                audio.get_bar_values(frame_idx, BAR_COUNT, prev_bars, 0.45)
            )
            prev_bars = bars
            energy = float(np.mean(bars))

            frame = background.copy()

            # energy glow behind the disc
            glow = glow_sprite.copy()
            glow.putalpha(glow_sprite.getchannel("A").point(lambda a: int(a * (0.3 + 1.6 * energy))))
            frame = Image.alpha_composite(frame.convert("RGBA"), glow)

            # vinyl: static body + rotating label/sheen
            disc = disc_base.copy()
            rotated = label_layer.rotate(
                disc_angle, resample=Image.BICUBIC,
                center=(layout.disc_radius, layout.disc_radius),
            )
            disc = Image.alpha_composite(disc, rotated)
            frame.alpha_composite(disc, (cx - layout.disc_radius, cy - layout.disc_radius))

            # rainbow spectrum bars radiating from the disc edge
            draw = ImageDraw.Draw(frame, "RGBA")
            inner = layout.disc_radius + 16
            hue_drift = frame_idx * 0.0007  # slow drift keeps the ring alive
            for i in range(BAR_COUNT):
                angle = (i / BAR_COUNT) * 2 * math.pi - math.pi / 2
                length = layout.bar_max_len * bars[i]
                if length < 2:
                    continue
                color = rainbow_color(i, BAR_COUNT, hue_drift)
                cos_a, sin_a = math.cos(angle), math.sin(angle)
                x0 = cx + cos_a * inner
                y0 = cy + sin_a * inner
                x1 = cx + cos_a * (inner + length)
                y1 = cy + sin_a * (inner + length)
                alpha = int(120 + 135 * bars[i])
                draw.line([x0, y0, x1, y1], fill=color + (alpha,), width=BAR_WIDTH)

            # vignette + lyrics
            black = Image.new("RGBA", frame.size, (0, 0, 0, 255))
            frame_rgb = Image.composite(frame, black, vignette).convert("RGB")
            draw_lyric(frame_rgb, font, lines, starts, frame_idx / FPS, layout)

            encoder.stdin.write(np.asarray(frame_rgb, dtype=np.uint8).tobytes())
            disc_angle = (disc_angle + DEG_PER_FRAME) % 360.0

            if frame_idx % 300 == 0:
                print(f"[{layout.aspect}] Frame {frame_idx}/{total_frames}", flush=True)
    finally:
        if encoder.stdin:
            encoder.stdin.close()
        encoder.wait()
    if encoder.returncode != 0:
        raise SystemExit(f"ffmpeg encoding failed (exit={encoder.returncode})")
    print(f"[{layout.aspect}] video -> {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lyrics-json", required=True, type=Path)
    parser.add_argument("--song-wav", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Cap the rendered video at this many seconds (default: full song).",
    )
    parser.add_argument(
        "--aspect",
        choices=sorted(LAYOUTS),
        default="16:9",
        help="Output aspect ratio (default: 16:9).",
    )
    args = parser.parse_args()

    data = json.loads(args.lyrics_json.read_text(encoding="utf-8"))
    language = data.get("_lang", "zh")

    work_dir = args.output.parent
    cover_path = work_dir / "cover.png"
    timing_path = work_dir / "timing.json"
    if not timing_path.exists():
        timing = {
            "language": language,
            "lines": compute_timing(args.song_wav, data.get("lyrics", ""), language),
        }
        timing_path.write_text(
            json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        timing = json.loads(timing_path.read_text(encoding="utf-8"))
    if not cover_path.exists():
        make_cover(data, cover_path)

    render(
        data, timing, cover_path, args.song_wav, args.output,
        LAYOUTS[args.aspect], args.duration,
    )


if __name__ == "__main__":
    main()
