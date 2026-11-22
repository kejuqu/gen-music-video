import sys, tempfile, unittest
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

class SpriteTests(unittest.TestCase):
    def test_three_note_sprites(self):
        sprites = rp.note_sprites()
        self.assertEqual(len(sprites), 3)
        for s in sprites:
            self.assertEqual(s.mode, "RGBA")
            self.assertGreaterEqual(s.height, 48)

    def test_control_icon_keys(self):
        self.assertEqual(set(rp.control_icons()), {"prev","pause","next","shuffle","repeat"})

class KaraokeTests(unittest.TestCase):
    def test_color_unsung_is_dim_white(self):
        c = rp.karaoke_color(0.0, rp.song_palette("x"))
        self.assertLess(max(c), 200)

    def test_color_sung_is_gold(self):
        c = rp.karaoke_color(1.0, rp.song_palette("x"))
        self.assertGreater(c[0], 220)   # 红分量高
        self.assertGreater(c[1], 170)   # 绿分量高
        self.assertLess(c[2], 140)      # 蓝分量低

    def test_color_midpoint_between(self):
        mid = rp.karaoke_color(0.5, rp.song_palette("x"))
        self.assertLess(mid[0], rp.karaoke_color(1.0, rp.song_palette("x"))[0])

    def test_format_time(self):
        self.assertEqual(rp.format_time(135.0), "2:15")
        self.assertEqual(rp.format_time(8.0), "0:08")

class LyricZoneTests(unittest.TestCase):
    def test_zone_entries_inside_canvas(self):
        entries = rp.layout_lyric_zone(rp.LAYOUT, "上一句", [{"text":"a","start":0,"end":1}], "下一句", None)
        for e in entries:
            self.assertTrue(0 <= e["y"] <= 1280)

    def test_long_line_wraps_to_two(self):
        long_tokens = [{"text": ch, "start": i*0.1, "end": (i+1)*0.1} for i, ch in enumerate("我"*60)]
        entries = rp.layout_lyric_zone(rp.LAYOUT, None, long_tokens, None, None)
        cur = [e for e in entries if e.get("karaoke")]
        self.assertLessEqual(len(cur), 2)

    def test_english_tokens_keep_word_spaces_when_wrapped(self):
        from PIL import ImageFont
        font = ImageFont.truetype(rp.EN_FONT, 16)
        tokens = [
            {"text": "Just", "start": 0.0, "end": 0.3},
            {"text": "an", "start": 0.3, "end": 0.6},
            {"text": "ordinary", "start": 0.6, "end": 1.0},
            {"text": "person", "start": 1.0, "end": 1.4},
        ]
        rows = rp._wrap_tokens(tokens, font, 120)
        self.assertGreaterEqual(len(rows), 2)  # 窄宽度必须折行
        for row in rows:
            self.assertTrue(all(" " not in t["text"] for t in row))
            self.assertGreaterEqual(len(row), 1)
        # 行内词间空格由渲染时 gap 恢复；此处按 spaced 行重建验证不丢词
        joined = " ".join(
            " ".join(t["text"] for t in row) for row in rows
        )
        self.assertEqual(joined, "Just an ordinary person")

class ParticleTests(unittest.TestCase):
    def test_deterministic_sequence(self):
        a = rp.NoteParticles("平凡底片", rp.note_sprites(), rp.LAYOUT)
        b = rp.NoteParticles("平凡底片", rp.note_sprites(), rp.LAYOUT)
        sa, sb = a.update(0.5), b.update(0.5)
        self.assertEqual([(s[1], s[2], s[3]) for s in sa], [(s[1], s[2], s[3]) for s in sb])
        self.assertTrue(0 < len(sa) <= 14)

    def test_energy_speeds_rise(self):
        p = rp.NoteParticles("平凡底片", rp.note_sprites(), rp.LAYOUT)
        p.update(0.0); y_low = p.update(0.0)[0][2]
        p2 = rp.NoteParticles("平凡底片", rp.note_sprites(), rp.LAYOUT)
        y_high = p2.update(1.0)[0][2]
        self.assertLess(y_high, y_low)  # 高能时升得更多(y 向上减小)

class LineAtTests(unittest.TestCase):
    def test_first_line_active(self):
        lines = [{"start": 1.0, "end": 3.0, "text": "a"}, {"start": 4.0, "end": 6.0, "text": "b"}]
        self.assertEqual(rp._line_at(lines, [l["start"] for l in lines], 2.0), 0)

    def test_gap_between_lines_is_none(self):
        lines = [{"start": 1.0, "end": 3.0, "text": "a"}, {"start": 4.0, "end": 6.0, "text": "b"}]
        self.assertIsNone(rp._line_at(lines, [l["start"] for l in lines], 3.5))

    def test_before_first_line_is_none(self):
        lines = [{"start": 1.0, "end": 3.0, "text": "a"}]
        self.assertIsNone(rp._line_at(lines, [1.0], 0.5))

    def test_empty_lines_is_none(self):
        self.assertIsNone(rp._line_at([], [], 1.0))

class CacheTests(unittest.TestCase):
    def _touch(self, path, mtime):
        path.write_text("x"); import os; os.utime(path, (mtime, mtime))

    def test_invalid_when_wav_newer(self):
        import tempfile, time
        with tempfile.TemporaryDirectory() as d:
            d = Path(d); tp = d/"timing.json"; lw = d/"lyrics.txt"; wv = d/"a.wav"
            now = time.time()
            self._touch(lw, now-30); self._touch(wv, now-20); self._touch(tp, now-10)
            self.assertTrue(rp.timing_cache_valid(tp, lw, wv))
            self._touch(wv, now)          # wav 更新
            self.assertFalse(rp.timing_cache_valid(tp, lw, wv))

    def test_invalid_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(rp.timing_cache_valid(Path(d)/"t.json", Path(d)/"l", Path(d)/"w"))

