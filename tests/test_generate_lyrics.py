import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import generate_lyrics


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "lyrics.zh.json"


class LyricsTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_example_is_valid(self):
        self.assertIs(generate_lyrics.validate_lyrics(self.data), self.data)

    def test_rejects_non_positive_music_style(self):
        data = copy.deepcopy(self.data)
        data["style"] = "Melancholic ambient with dark pads"
        with self.assertRaisesRegex(ValueError, "positive"):
            generate_lyrics.validate_lyrics(data)

    def test_prompts_require_happy_and_uplifting_works(self):
        zh_prompt = generate_lyrics.PROMPTS["zh"].format(theme="测试主题")
        en_prompt = generate_lyrics.PROMPTS["en"].format(theme="test theme")
        self.assertIn("积极向上、幸福欢乐", zh_prompt)
        self.assertIn("joyful", zh_prompt)
        self.assertIn("happy and joyful", en_prompt)
        self.assertIn("uplifting", en_prompt)

    def test_rejects_unknown_shape(self):
        data = copy.deepcopy(self.data)
        data["sections"][0]["visual"]["primary_shape"] = "unknown"
        with self.assertRaisesRegex(ValueError, "primary_shape"):
            generate_lyrics.validate_lyrics(data)

    def test_rejects_invalid_palette(self):
        data = copy.deepcopy(self.data)
        data["sections"][0]["visual"]["palette"] = ["not-a-color", "#fff"]
        with self.assertRaisesRegex(ValueError, "palette"):
            generate_lyrics.validate_lyrics(data)

    def test_rejects_title_instead_of_full_lyrics(self):
        data = copy.deepcopy(self.data)
        data["lyrics"] = "sunshine and hope"
        with self.assertRaisesRegex(ValueError, "12–32 content lines"):
            generate_lyrics.validate_lyrics(data)

    def test_rejects_empty_sections(self):
        data = copy.deepcopy(self.data)
        data["sections"] = []
        with self.assertRaisesRegex(ValueError, "sections"):
            generate_lyrics.validate_lyrics(data)

    @patch("scripts.generate_lyrics.ollama.chat")
    def test_main_routes_chinese_model_and_disables_thinking(self, chat):
        chat.return_value = {"message": {"content": json.dumps(self.data)}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "lyrics.json"
            with patch("sys.argv", ["generate_lyrics.py", "--output", str(output)]):
                generate_lyrics.main()

            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(written["_lang"], "zh")
            self.assertEqual(written["_model"], "qwen3:8b")

        self.assertEqual(chat.call_args.kwargs["model"], "qwen3:8b")
        self.assertIs(chat.call_args.kwargs["think"], False)
        self.assertIs(chat.call_args.kwargs["format"], generate_lyrics.SCHEMA)

    @patch("scripts.generate_lyrics.time.sleep")
    @patch("scripts.generate_lyrics.ollama.chat")
    def test_main_retries_malformed_json(self, chat, _sleep):
        chat.side_effect = [
            {"message": {"content": "not json"}},
            {"message": {"content": json.dumps(self.data)}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lyrics.json"
            with patch("sys.argv", ["generate_lyrics.py", "--output", str(output)]):
                generate_lyrics.main()
            self.assertTrue(output.exists())
        self.assertEqual(chat.call_count, 2)


if __name__ == "__main__":
    unittest.main()
