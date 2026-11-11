import tempfile
import unittest
from pathlib import Path

from scripts.video_filename import from_lyrics, video_filename


class VideoFilenameTests(unittest.TestCase):
    def test_chinese_title_and_date(self):
        self.assertEqual(video_filename("消失的光", "2026-11-10"), "消失的光_2026-11-10.mp4")

    def test_whitespace_and_path_separator_are_safe(self):
        self.assertEqual(video_filename("Night / Flow", "2026-11-10"), "Night_-_Flow_2026-11-10.mp4")

    def test_empty_sanitized_title_uses_fallback(self):
        self.assertEqual(video_filename("///", "2026-11-10"), "untitled_2026-11-10.mp4")

    def test_title_is_bounded(self):
        name = video_filename("光" * 100, "2026-11-10")
        self.assertEqual(name, f"{'光' * 60}_2026-11-10.mp4")

    def test_invalid_date_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            video_filename("title", "../../date")

    def test_reads_title_from_lyrics_json(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "lyrics.zh.json"
        self.assertEqual(from_lyrics(example, "2026-11-10"), "向快乐出发_2026-11-10.mp4")


if __name__ == "__main__":
    unittest.main()
