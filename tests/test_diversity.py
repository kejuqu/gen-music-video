import json
import random
import tempfile
import unittest
from pathlib import Path

from scripts import diversity


def write_song(root: Path, date: str, lang: str, title: str, lyrics: str,
               style: str = "joyful pop", combo: dict | None = None) -> None:
    path = root / date / lang / "lyrics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"title": title, "style": style, "lyrics": lyrics}
    if combo:
        data["_combo"] = combo
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


ZH_LYRICS = "[verse]\n阳光洒满街角的笑\n风儿轻吹着童年的歌谣\n每一步都踩着希望的节奏\n[chorus]\n幸福像花儿一样绽放\n欢乐是心中不灭的光"
EN_LYRICS = "[verse]\nMorning light is calling out\nWe dance until the stars come down\n[chorus]\nJoy is all around us now\nSing it loud and sing it proud"


class HistoryTests(unittest.TestCase):
    def test_load_history_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_song(root, "2026-11-11", "zh", "旧歌", ZH_LYRICS)
            write_song(root, "2026-11-13", "zh", "新歌", ZH_LYRICS)
            write_song(root, "2026-11-13", "en", "Other Lang", EN_LYRICS)
            history = diversity.load_history(root, "zh")
            self.assertEqual([e.title for e in history], ["新歌", "旧歌"])

    def test_load_history_skips_corrupt_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bad = root / "2026-11-12" / "zh"
            bad.mkdir(parents=True)
            (bad / "lyrics.json").write_text("{not json", encoding="utf-8")
            write_song(root, "2026-11-13", "zh", "好歌", ZH_LYRICS)
            history = diversity.load_history(root, "zh")
            self.assertEqual(len(history), 1)

    def test_missing_root_returns_empty(self):
        self.assertEqual(diversity.load_history(Path("/nonexistent-dir"), "zh"), [])


class ComboTests(unittest.TestCase):
    def test_pick_combo_avoids_recent_themes(self):
        rng = random.Random(42)
        used_theme = diversity.SUB_THEMES["zh"][0]
        history = [
            diversity.HistoryEntry(
                date="2026-11-13", title="t", style="", tokens=frozenset(),
                combo={"theme": used_theme, "genre": "g", "vocal": "v"},
            )
        ]
        for _ in range(20):
            combo = diversity.pick_combo("zh", history, rng)
            self.assertNotEqual(combo["theme"], used_theme)

    def test_pick_combo_returns_all_fields(self):
        combo = diversity.pick_combo("en", [], random.Random(1))
        self.assertEqual(set(combo), {"theme", "genre", "vocal", "groove"})


class PromptBlockTests(unittest.TestCase):
    def test_zh_block_lists_recent_titles_and_combo(self):
        history = [
            diversity.HistoryEntry(
                date="2026-11-13", title="阳光下的微笑",
                style="A joyful and uplifting pop style", tokens=frozenset(), combo={},
            )
        ]
        combo = {"theme": "海边旅行", "genre": "city pop", "vocal": "duet", "groove": "100 BPM"}
        block = diversity.build_prompt_block("zh", combo, history)
        self.assertIn("海边旅行", block)
        self.assertIn("city pop", block)
        self.assertIn("阳光下的微笑", block)
        self.assertIn("具体意象", block)

    def test_en_block_mentions_imagery_requirement(self):
        combo = {"theme": "road trip", "genre": "indie pop", "vocal": "duet", "groove": "110 BPM"}
        block = diversity.build_prompt_block("en", combo, [])
        self.assertIn("concrete imagery", block)
        self.assertIn("road trip", block)


class UniquenessTests(unittest.TestCase):
    def entry(self, title: str, lyrics: str) -> diversity.HistoryEntry:
        return diversity.HistoryEntry(
            date="2026-11-13", title=title, style="",
            combo={}, tokens=diversity.tokenize(lyrics),
        )

    def test_title_normalization(self):
        history = [self.entry("Sunshine and Joy!", EN_LYRICS)]
        self.assertFalse(diversity.title_is_unique("sunshine_and_joy", history))
        self.assertTrue(diversity.title_is_unique("Different Title", history))

    def test_chinese_title_duplicate(self):
        history = [self.entry("阳光下的微笑", ZH_LYRICS)]
        self.assertFalse(diversity.title_is_unique("《阳光下的微笑》", history))

    def test_identical_lyrics_rejected(self):
        history = [self.entry("old", ZH_LYRICS)]
        self.assertGreaterEqual(diversity.max_lyric_similarity(ZH_LYRICS, history), 0.99)

    def test_different_lyrics_accepted(self):
        other = "[verse]\n海浪拍打银色沙滩\n我们赤着脚追逐晚霞\n背包里装满笑声与星光\n[chorus]\n自由的风吹过发梢\n这一刻世界属于我们"
        history = [self.entry("old", ZH_LYRICS)]
        self.assertLess(
            diversity.max_lyric_similarity(other, history),
            diversity.SIMILARITY_THRESHOLD,
        )

    def test_english_similarity(self):
        history = [self.entry("old", EN_LYRICS)]
        same = diversity.max_lyric_similarity(EN_LYRICS, history)
        different = diversity.max_lyric_similarity(
            "rainbow skies and open roads we ride until the morning glows", history
        )
        self.assertGreaterEqual(same, 0.99)
        self.assertLess(different, diversity.SIMILARITY_THRESHOLD)


if __name__ == "__main__":
    unittest.main()
