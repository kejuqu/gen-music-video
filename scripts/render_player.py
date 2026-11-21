"""Theme palette and static layers for the vertical music-player renderer."""

from __future__ import annotations

import colorsys
import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from render_visualizer import EN_FONT, ZH_FONT, make_cover


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
