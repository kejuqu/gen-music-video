import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from voice_lyrics import parse_voice_lyrics, detect_language

class ParseTests(unittest.TestCase):
    def test_strips_bracket_tags_with_space_and_number(self):
        text = "[verse 1]\n闹钟又一次 卡在六点半\n[Pre-Chorus]\n好多委屈\n[chorus]\n我只是一个\n"
        self.assertEqual(parse_voice_lyrics(text), ["闹钟又一次 卡在六点半", "好多委屈", "我只是一个"])

    def test_strips_whole_line_parenthetical_annotation(self):
        text = "（8小节，轻柔钢琴+淡淡的木吉他）\n闹钟又一次\n(outro solo)\n好好活着\n"
        self.assertEqual(parse_voice_lyrics(text), ["闹钟又一次", "好好活着"])

    def test_keeps_inline_parenthetical_in_lyric_line(self):
        text = "我只是一个 (最普通的人)\n"
        self.assertEqual(parse_voice_lyrics(text), ["我只是一个 (最普通的人)"])

    def test_skips_empty_and_strips_whitespace(self):
        self.assertEqual(parse_voice_lyrics("  第一行  \n\n   \n第二行"), ["第一行", "第二行"])

class LanguageTests(unittest.TestCase):
    def test_chinese(self):
        self.assertEqual(detect_language(["闹钟又一次 卡在六点半"]), "zh")

    def test_english(self):
        self.assertEqual(detect_language(["Just an ordinary person"]), "en")

if __name__ == "__main__":
    unittest.main()
