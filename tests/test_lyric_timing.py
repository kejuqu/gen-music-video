import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lyric_timing import align_lines, extract_lines, fill_gaps, similarity


class ExtractLinesTests(unittest.TestCase):
    def test_skips_empty_lines_and_strips(self):
        lyrics = "第一行\n\n  第二行  \n\n[chorus]\n第三行\n"
        self.assertEqual(extract_lines(lyrics), ["第一行", "第二行", "第三行"])


class SimilarityTests(unittest.TestCase):
    def test_identical_lines_score_one(self):
        self.assertEqual(similarity("阳光洒满清晨的窗台", "阳光洒满清晨的窗台"), 1.0)

    def test_partial_misrecognition_still_scores(self):
        score = similarity("阳光洒满清晨的窗台", "洋灑漫清晨的状态")
        self.assertGreaterEqual(score, 0.3)

    def test_unrelated_lines_score_low(self):
        self.assertLess(similarity("阳光洒满清晨的窗台", "作曲 李宗盛"), 0.3)


class AlignLinesTests(unittest.TestCase):
    def test_monotonic_match_skips_hallucinated_segments(self):
        segments = [
            {"start": 0.0, "end": 8.0, "text": "作曲 李宗盛"},  # intro hallucination
            {"start": 9.0, "end": 13.0, "text": "洋灑漫清晨的状态"},
            {"start": 13.0, "end": 15.0, "text": "微风轻覆我脸庞"},
        ]
        lines = ["阳光洒满清晨的窗台", "微风轻拂我脸庞"]
        timed = align_lines(segments, lines)
        self.assertEqual(timed[0]["start"], 9.0)
        self.assertEqual(timed[0]["text"], "阳光洒满清晨的窗台")
        self.assertEqual(timed[1]["start"], 13.0)

    def test_unmatched_line_uses_previous_end(self):
        segments = [{"start": 5.0, "end": 9.0, "text": "唯一一句"}]
        lines = ["啦啦啦啦啦啦", "唯一一句"]
        timed = align_lines(segments, lines)
        self.assertEqual(timed[0]["start"], 0.0)
        self.assertEqual(timed[1]["start"], 5.0)


class WordLevelAlignTests(unittest.TestCase):
    def test_split_segments_align_each_line(self):
        """One transcript segment covering two lyric lines must not swallow
        the second line (the old bug: a line displayed for 70+ seconds)."""
        segments = [{
            "start": 54.0, "end": 62.0,
            "text": "With every step we'll find our way to a brighter tomorrow",
            "words": (
                [("With", 54.0, 54.4), ("every", 54.4, 54.8),
                 ("step", 54.8, 55.2), ("we'll", 55.2, 55.6),
                 ("find", 55.6, 56.0), ("our", 56.0, 56.3),
                 ("way", 56.3, 57.0)]
                + [("to", 57.5, 57.8), ("a", 57.8, 58.0),
                   ("brighter", 58.0, 58.6), ("tomorrow", 58.6, 59.5)]
            ),
        }]
        segments[0]["words"] = [
            {"word": w, "start": s, "end": e} for w, s, e in segments[0]["words"]
        ]
        lines = ["With every step, we'll find our way",
                 "To a brighter tomorrow, starting today"]
        timed = align_lines(segments, lines)
        self.assertAlmostEqual(timed[0]["start"], 54.0, places=1)
        self.assertAlmostEqual(timed[0]["end"], 57.0, places=1)
        self.assertGreaterEqual(timed[1]["start"], 57.0)

    def test_hallucinated_words_do_not_shift_lines(self):
        segments = [{
            "start": 0.0, "end": 6.0, "text": "作曲 作曲",
            "words": [{"word": "作曲", "start": 0.0, "end": 3.0},
                      {"word": "作曲", "start": 3.0, "end": 6.0}],
        }, {
            "start": 9.0, "end": 13.0, "text": "洋灑漫清晨的状态",
            "words": [{"word": "洋灑漫清晨的状态", "start": 9.0, "end": 13.0}],
        }]
        lines = ["阳光洒满清晨的窗台"]
        timed = align_lines(segments, lines)
        self.assertAlmostEqual(timed[0]["start"], 9.0, places=1)


class FillGapsTests(unittest.TestCase):
    def test_display_duration_capped_during_instrumental_gaps(self):
        timed = [{"start": 54.0, "end": 56.0, "text": "a"},
                 {"start": 131.0, "end": 135.0, "text": "b"}]
        fill_gaps(timed, duration=180.0)
        self.assertEqual(timed[0]["end"], 69.0)  # 54 + 15s cap
    def test_line_stays_visible_until_next_line(self):
        timed = [
            {"start": 9.0, "end": 11.0, "text": "a"},
            {"start": 13.0, "end": 13.5, "text": "b"},
        ]
        fill_gaps(timed, duration=30.0)
        self.assertEqual(timed[0]["end"], 13.0)
        self.assertEqual(timed[1]["end"], 28.0)  # capped: 13 + 15s max display

    def test_minimum_display_of_half_second(self):
        timed = [{"start": 10.0, "end": 10.1, "text": "a"}]
        fill_gaps(timed, duration=30.0)
        self.assertGreaterEqual(timed[0]["end"], 10.5)


if __name__ == "__main__":
    unittest.main()
