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

    @patch("scripts.generate_lyrics.ollama.chat")
    def test_main_writes_plain_text_lyrics_with_blank_line_separators(self, chat):
        chat.return_value = {"message": {"content": json.dumps(self.data)}}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "lyrics.json"
            with patch("sys.argv", ["generate_lyrics.py", "--output", str(output)]):
                generate_lyrics.main()
            lyric_text = output.with_suffix(".txt").read_text(encoding="utf-8")

        self.assertEqual(lyric_text, """晨光落在微笑的脸庞
新的一天充满新希望
我们迎着风自由歌唱
每一步都有温暖方向
把梦想装进行囊
和伙伴分享每道光
勇敢推开那扇窗
世界回应热烈的掌声

手牵手向快乐出发
让幸福开满每个春夏
笑声穿过白云和晚霞
心中有爱就不会害怕
手牵手向未来出发
让美好在此刻发芽
我们一起大声歌唱
把欢乐送到千万家
""")

    @patch("scripts.generate_lyrics.time.sleep")
    @patch("scripts.generate_lyrics.ollama.chat")
    def test_main_normalizes_common_chinese_model_output(self, chat, _sleep):
        data = copy.deepcopy(self.data)
        data["lyrics"] = data["lyrics"].replace("[verse]", "[主歌 1]").replace("[chorus]", "[副歌 1]")
        data["sections"][0]["visual"]["motion_intensity"] = 5
        data["sections"][1]["visual"]["motion_intensity"] = 9
        chat.return_value = {"message": {"content": json.dumps(data, ensure_ascii=False)}}

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lyrics.json"
            with patch("sys.argv", ["generate_lyrics.py", "--output", str(output)]):
                generate_lyrics.main()
            written = json.loads(output.read_text(encoding="utf-8"))

        self.assertIn("[verse]\n", written["lyrics"])
        self.assertIn("[chorus]\n", written["lyrics"])
        self.assertNotIn("主歌 1", written["lyrics"])
        self.assertEqual(written["sections"][0]["visual"]["motion_intensity"], 0.5)
        self.assertEqual(written["sections"][1]["visual"]["motion_intensity"], 0.9)

    @patch("scripts.generate_lyrics.time.sleep")
    @patch("scripts.generate_lyrics.ollama.chat")
    def test_main_retries_when_english_model_returns_chinese_lyrics(self, chat, _sleep):
        english_data = copy.deepcopy(self.data)
        english_data["title"] = "Brighter Days"
        english_data["lyrics"] = """[verse]
Morning light is calling us
Every step is full of hope
We can chase the sun together
Every road leads us back home
Raise your hands and sing it out
Let the whole world hear our song
We are stronger side by side
This is where we both belong
[chorus]
We are running toward the light
Hearts are shining through the night
Every dream is coming true
All I need is here with you
We are running toward the light
Hearts are shining through the night
Every dream is coming true
All I need is here with you"""
        chat.side_effect = [
            {"message": {"content": json.dumps(self.data, ensure_ascii=False)}},
            {"message": {"content": json.dumps(english_data)}},
        ]

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "lyrics.json"
            with patch("sys.argv", ["generate_lyrics.py", "--output", str(output), "--lang", "en"]):
                generate_lyrics.main()
            written = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(written["_lang"], "en")
        self.assertEqual(written["title"], "Brighter Days")
        self.assertEqual(chat.call_count, 2)

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
