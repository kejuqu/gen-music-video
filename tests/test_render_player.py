import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import render_player as rp

class PaletteTests(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(rp.song_palette("平凡底片"), rp.song_palette("平凡底片"))

    def test_distinct_between_songs(self):
        a, b = rp.song_palette("平凡底片"), rp.song_palette("love you")
        self.assertNotEqual(a[0], b[0])

class LayerTests(unittest.TestCase):
    def test_background_size_and_dark(self):
        bg = rp.build_background(rp.song_palette("x"), rp.LAYOUT)
        self.assertEqual(bg.size, (720, 1280))
        px = bg.convert("L").getpixel((360, 640))
        self.assertLess(px, 90)  # 背景必须暗

    def test_card_within_canvas(self):
        bg = rp.build_background(rp.song_palette("x"), rp.LAYOUT)
        card = rp.build_card(bg, rp.LAYOUT)
        x0, y0, x1, y1 = rp.LAYOUT.card_box
        self.assertTrue(0 <= x0 < x1 <= 720 and 0 <= y0 < y1 <= 1280)
