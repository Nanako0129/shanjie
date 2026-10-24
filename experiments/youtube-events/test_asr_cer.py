"""Tests of asr_cer.py on tiny synthetic VTT/JSON fixtures. No network, no models, no OpenCC files (conv is a fake)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asr_cer as A  # noqa: E402

VTT = """WEBVTT

00:00:05.000 --> 00:00:07.000
你好，<c>世界</c>

00:09:59.900 --> 00:10:02.000
邊界內

1
00:10:00.000 --> 00:10:03.000
邊界外

01:00:00.000 --> 01:00:01.000
太晚
"""
conv = lambda s: s.replace("你", "妳").replace("简", "簡").replace("单", "單")  # noqa: E731


class Cer(unittest.TestCase):
    def test_cue_cutting(self):
        self.assertEqual([s for s, _ in A.cues(VTT)], [5, 599.9, 600, 3600])
        self.assertEqual(A.cues(VTT)[0][1], "你好，世界")
        self.assertEqual(A.reference(VTT, 0), "你好，世界\n邊界內")
        self.assertEqual(A.reference(VTT, 600), "邊界外")          # start inside, not end
        self.assertEqual(A.reference(VTT, 5), "你好，世界\n邊界內\n邊界外")

    def test_normalise_keeps_han_only_after_conversion(self):
        self.assertEqual(A.normalise("你好, abc 123 简单！", conv), "妳好簡單")

    def test_edit_distance(self):
        self.assertEqual(A.levenshtein("", "abc"), 3)
        self.assertEqual(A.levenshtein("kitten", "sitting"), 3)
        self.assertEqual(A.levenshtein("你好嗎", "你好嗎"), 0)

    def test_trad_share(self):
        self.assertEqual(A.trad_counts("你好 简单 abc", conv), (4, 1))   # 你, 简, 单 changed

    def test_score_and_totals(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "asr", "m"))
            with open(os.path.join(d, "v1.zh-TW.vtt"), "w", encoding="utf-8") as f:
                f.write(VTT)
            for i, text in (("v1", "你好世界邊界內"), ("v2", "完全不同")):
                with open(os.path.join(d, "asr", "m", i + ".json"), "w", encoding="utf-8") as f:
                    json.dump({"text": text, "segments": [], "wall_s": 30, "audio_s": 600}, f)
            with open(os.path.join(d, "v2.zh-TW.vtt"), "w", encoding="utf-8") as f:
                f.write("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n完全相同\n")
            picks = [{"id": "v1", "lang": "zh-TW", "start": 0}, {"id": "v2", "lang": "zh-TW", "start": 0}]
            rows = A.report(picks, d, os.path.join(d, "asr"), conv)
        self.assertEqual(rows[0], ["v1", "m", "0.0000", 0, 7, 7, "0.8571", "0.050"])   # conv changes 你 only: 6 of 7 Han unchanged
        self.assertEqual(rows[1][:6], ["v2", "m", "0.2500", 1, 4, 4])
        self.assertEqual(rows[2][:7], ["TOTAL", "m", "0.0909", 1, 11, 11, "0.9091"])
        self.assertEqual(rows[2][7], "0.050")


if __name__ == "__main__":
    unittest.main()
