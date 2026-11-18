"""Unit tests for the third-tone sandhi flip of tools/build_probe.py (docs/spec/evaluation.md §1.1, contract tone3-sandhi §3.1).
Run: python3 -W ignore -m unittest tools/test_build_probe.py"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_probe as bp  # noqa: E402

# 本調是上聲的輕聲字（測試用，不讀詞庫）：想、姐、虎
THIRD_NEUTRAL = {("想", "ㄒㄧㄤ˙"), ("姐", "ㄐㄧㄝ˙"), ("虎", "ㄏㄨ˙")}


def flip(sent, reading, variant):
    out = bp.tone3_flip(sent, reading.split(), variant, third_neutral=lambda ch, s: (ch, s) in THIRD_NEUTRAL)
    return None if out is None else " ".join(out)


class Tone3Flip(unittest.TestCase):
    def test_two_thirds_are_the_same_in_both_versions(self):
        for v in ("ds", "sd"):
            self.assertEqual(flip("眨眼", "ㄓㄚˇ ㄧㄢˇ", v), "ㄓㄚˊ ㄧㄢˇ")

    def test_three_thirds_double_single_and_single_double(self):
        self.assertEqual(flip("展覽館", "ㄓㄢˇ ㄌㄢˇ ㄍㄨㄢˇ", "ds"), "ㄓㄢˊ ㄌㄢˊ ㄍㄨㄢˇ")
        self.assertEqual(flip("小老虎", "ㄒㄧㄠˇ ㄌㄠˇ ㄏㄨˇ", "sd"), "ㄒㄧㄠˇ ㄌㄠˊ ㄏㄨˇ")

    def test_four_thirds(self):
        self.assertEqual(flip("好幾把傘", "ㄏㄠˇ ㄐㄧˇ ㄅㄚˇ ㄙㄢˇ", "ds"), "ㄏㄠˊ ㄐㄧˊ ㄅㄚˊ ㄙㄢˇ")
        self.assertEqual(flip("好幾把傘", "ㄏㄠˇ ㄐㄧˇ ㄅㄚˇ ㄙㄢˇ", "sd"), "ㄏㄠˇ ㄐㄧˊ ㄅㄚˊ ㄙㄢˇ")

    def test_a_third_before_a_third_tone_neutral(self):
        self.assertEqual(flip("小姐", "ㄒㄧㄠˇ ㄐㄧㄝ˙", "ds"), "ㄒㄧㄠˊ ㄐㄧㄝ˙")
        self.assertEqual(flip("想想", "ㄒㄧㄤˇ ㄒㄧㄤ˙", "ds"), "ㄒㄧㄤˊ ㄒㄧㄤ˙")
        self.assertEqual(flip("我想想", "ㄨㄛˇ ㄒㄧㄤˇ ㄒㄧㄤ˙", "ds"), "ㄨㄛˊ ㄒㄧㄤˊ ㄒㄧㄤ˙")
        self.assertEqual(flip("我想想", "ㄨㄛˇ ㄒㄧㄤˇ ㄒㄧㄤ˙", "sd"), "ㄨㄛˇ ㄒㄧㄤˊ ㄒㄧㄤ˙")
        self.assertIsNone(flip("我們", "ㄨㄛˇ ㄇㄣ˙", "ds"), "們 is no third-tone neutral")

    def test_punctuation_splits_clauses(self):
        self.assertEqual(flip("好，我想", "ㄏㄠˇ ， ㄨㄛˇ ㄒㄧㄤˇ", "ds"), "ㄏㄠˇ ， ㄨㄛˊ ㄒㄧㄤˇ")
        self.assertIsNone(flip("好，好", "ㄏㄠˇ ， ㄏㄠˇ", "ds"))

    def test_rows_without_a_change_are_none(self):
        self.assertIsNone(flip("大家", "ㄉㄚˋ ㄐㄧㄚ", "ds"))
        self.assertIsNone(flip("我", "ㄨㄛˇ", "sd"))
        self.assertIsNone(flip("我們", "ㄨㄛˇ", "ds"), "a sentence whose length is not the syllable count")

    def test_the_real_base_tone_of_a_neutral(self):
        self.assertTrue(bp.is_third_neutral("想", "ㄒㄧㄤ˙"))
        self.assertFalse(bp.is_third_neutral("們", "ㄇㄣ˙"))
        self.assertFalse(bp.is_third_neutral("想", "ㄒㄧㄤˇ"), "not a neutral")

    def test_cli_writes_only_changed_rows_and_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            rows, out = os.path.join(d, "rows.txt"), os.path.join(d, "probe.txt")
            open(rows, "w", encoding="utf-8").write("前|眨眼|ㄓㄚˇ ㄧㄢˇ\n|大家|ㄉㄚˋ ㄐㄧㄚ\n")
            cmd = [sys.executable, os.path.join(HERE, "build_probe.py"), "--tone3", "ds", "--rows", rows, "--out", out]
            subprocess.run(cmd, check=True, capture_output=True)
            self.assertEqual(open(out, encoding="utf-8").read(), "前|眨眼|ㄓㄚˊ ㄧㄢˇ\n")
            self.assertNotEqual(subprocess.run(cmd, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
