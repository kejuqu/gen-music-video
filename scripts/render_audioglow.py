#!/usr/bin/env python3
"""Render a 9:16 (1080x1920) mobile music video with audioglow.

Replaces the Blender stage of the daily pipeline: audioglow analyzes the
song, renders an audio-reactive cover visual via OpenGL shaders, and muxes
the audio with ffmpeg — output is the final publishable MP4 for
TikTok/Reels/Shorts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIDTH = 1080
HEIGHT = 1920
COVER_SIZE = 1080
FPS = 30

FONT_CANDIDATES_CJK = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
]
FONT_CANDIDATES_LATIN = [
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
]


def load_title(lyrics_json: Path) -> str:
    with lyrics_json.open(encoding="utf-8") as handle:
        data = json.load(handle)
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("lyrics.json title must be a non-empty string")
    return title.strip()


def is_cjk(text: str) -> bool:
    return any("⺀" <= ch <= "鿿" or "豈" <= ch <= "﫿" for ch in text)


def pick_font(text: str, size: int) -> ImageFont.FreeTypeFont:
    candidates = FONT_CANDIDATES_CJK if is_cjk(text) else FONT_CANDIDATES_LATIN
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def title_palette(title: str) -> list[tuple[int, int, int]]:
    """Derive a pleasant 3-color palette from the song title."""
    digest = hashlib.sha256(title.encode("utf-8")).digest()
    colors = []
    for i in range(3):
        hue = ((digest[i] / 255.0) + i * 0.33) % 1.0
        sat = 0.55 + (digest[3 + i] / 255.0) * 0.3
        val = 0.55 + (digest[6 + i] / 255.0) * 0.4
        colors.append(hsv_to_rgb(hue, sat, val))
    return colors


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
    return int(r * 255), int(g * 255), int(b * 255)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    if not is_cjk(text):
        # Break Latin titles at word boundaries.
        lines: list[str] = []
        current = ""
        for word in text.split(" "):
            trial = f"{current} {word}".strip()
            if draw.textlength(trial, font=font) <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines
    lines = []
    current = ""
    for ch in text:
        trial = current + ch
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


def make_cover(title: str, output: Path) -> Path:
    """Abstract gradient cover with the song title, sized for the visualizer."""
    colors = title_palette(title)
    small = Image.new("RGB", (64, 64))
    px = small.load()
    c1, c2, c3 = colors
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126.0
            if t < 0.5:
                u = t * 2
                rgb = tuple(int(c1[i] + (c2[i] - c1[i]) * u) for i in range(3))
            else:
                u = (t - 0.5) * 2
                rgb = tuple(int(c2[i] + (c3[i] - c2[i]) * u) for i in range(3))
            px[x, y] = rgb
    base = small.resize((COVER_SIZE, COVER_SIZE), Image.BICUBIC)

    blobs = Image.new("RGB", (COVER_SIZE, COVER_SIZE), (0, 0, 0))
    bd = ImageDraw.Draw(blobs)
    digest = hashlib.sha256(("blobs:" + title).encode("utf-8")).digest()
    for i, color in enumerate(colors):
        cx = COVER_SIZE * (0.2 + digest[i * 2] / 255.0 * 0.6)
        cy = COVER_SIZE * (0.2 + digest[i * 2 + 1] / 255.0 * 0.6)
        r = COVER_SIZE * (0.18 + digest[6 + i] / 255.0 * 0.15)
        bd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    blobs = blobs.filter(ImageFilter.GaussianBlur(COVER_SIZE / 6))
    cover = Image.blend(base, blobs, 0.45)

    draw = ImageDraw.Draw(cover)
    font = pick_font(title, 96)
    lines = wrap_text(draw, title, font, COVER_SIZE * 0.82)
    line_height = 96 * 1.35
    total = line_height * len(lines)
    y = (COVER_SIZE - total) / 2
    for line in lines:
        w = draw.textlength(line, font=font)
        x = (COVER_SIZE - w) / 2
        # Soft shadow + dark outline keep the title readable on any palette.
        shadow = Image.new("RGBA", cover.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        sd.text((x, y), line, font=font, fill=(0, 0, 0, 200))
        shadow = shadow.filter(ImageFilter.GaussianBlur(10))
        cover = Image.alpha_composite(cover.convert("RGBA"), shadow).convert("RGB")
        draw = ImageDraw.Draw(cover)
        draw.text((x, y), line, font=font, fill=(255, 255, 255),
                  stroke_width=3, stroke_fill=(0, 0, 0))
        y += line_height

    cover.save(output, "PNG")
    return output


def _create_cgl_context():
    """Create a windowless OpenGL 4.1 core context via CGL (ctypes).

    glcontext 3.1.0's darwin backend fails with 'cannot choose pixel format'
    on macOS 15, so we build the CGL context ourselves; moderngl's
    DefaultLoader then binds to the now-current context.
    """
    import ctypes

    cgl = ctypes.CDLL("/System/Library/Frameworks/OpenGL.framework/OpenGL")
    kCGLPFAOpenGLProfile = 99
    kCGLOGLPVersionGL4Core = 0x4100
    kCGLPFAColorSize = 8
    kCGLPFADepthSize = 12
    kCGLPFAAccelerated = 73
    Attribute = ctypes.c_int
    attribs = (Attribute * 8)(
        kCGLPFAAccelerated,
        kCGLPFAOpenGLProfile, kCGLOGLPVersionGL4Core,
        kCGLPFAColorSize, 24,
        kCGLPFADepthSize, 24,
        0,
    )
    pix = ctypes.c_void_p()
    npix = ctypes.c_int()
    err = cgl.CGLChoosePixelFormat(attribs, ctypes.byref(pix), ctypes.byref(npix))
    if err != 0 or not pix:
        raise RuntimeError(f"CGLChoosePixelFormat failed (err={err})")
    ctx = ctypes.c_void_p()
    err = cgl.CGLCreateContext(pix, None, ctypes.byref(ctx))
    cgl.CGLDestroyPixelFormat(pix)
    if err != 0 or not ctx:
        raise RuntimeError(f"CGLCreateContext failed (err={err})")
    err = cgl.CGLSetCurrentContext(ctx)
    if err != 0:
        raise RuntimeError(f"CGLSetCurrentContext failed (err={err})")
    return ctx


def _patch_moderngl_standalone() -> None:
    """Fall back to a ctypes CGL context when glcontext's darwin backend fails."""
    if sys.platform != "darwin":
        return
    import moderngl

    original = moderngl.create_context
    state = {"cgl_context": None}

    def patched(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except Exception:
            if state["cgl_context"] is None:
                state["cgl_context"] = _create_cgl_context()
            kwargs = {k: v for k, v in kwargs.items() if k != "standalone"}
            # standalone=False with an active CGL context -> moderngl detects it.
            return original(*args, **kwargs)

    moderngl.create_context = patched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--song", required=True, type=Path, help="Path to song.wav")
    parser.add_argument("--lyrics-json", required=True, type=Path,
                        help="lyrics.json (title used for the cover art)")
    parser.add_argument("--output", required=True, type=Path,
                        help="Final MP4 path (audio included)")
    parser.add_argument("--video-type", default="cover_audio_reactive",
                        choices=["cover_audio_reactive", "cover_overlay_spectrum"])
    parser.add_argument("--fps", type=int, default=FPS)
    args = parser.parse_args()

    if not args.song.is_file():
        raise SystemExit(f"song not found: {args.song}")
    if not args.lyrics_json.is_file():
        raise SystemExit(f"lyrics.json not found: {args.lyrics_json}")
    if not 1 <= args.fps <= 120:
        raise SystemExit("--fps must be between 1 and 120")

    try:
        title = load_title(args.lyrics_json)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read title: {error}") from error

    _patch_moderngl_standalone()
    try:
        from audioglow import render_video
    except ImportError:
        raise SystemExit(
            "audioglow is not installed for this Python; run: "
            f"{sys.executable} -m pip install audioglow"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="audioglow-", dir=args.output.parent))
    try:
        track_dir = workdir / "track"
        track_dir.mkdir()
        (track_dir / "song.wav").symlink_to(args.song.resolve())
        make_cover(title, track_dir / "cover.png")

        result = render_video(
            [track_dir],
            workdir / "video.mp4",
            args.video_type,
            resolution=(WIDTH, HEIGHT),
            fps=args.fps,
        )
        shutil.move(str(Path(result.video_path)), args.output)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"Saved 9:16 video ({WIDTH}x{HEIGHT}) to: {args.output}")


if __name__ == "__main__":
    main()
