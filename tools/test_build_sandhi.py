"""Unit tests for the `neutral` (§8.3) and `fusion` (R7, §8.1) rows of tools/build_sandhi.py on tiny synthetic lexicons (docs/spec/decoder.md §8.3).
Run: python3 -W ignore -m unittest tools/test_build_sandhi.py"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_sandhi as bs  # noqa: E402


def run(base_lines, overlay_lines="", penalty=1.0):
    """回傳 {(讀音, 詞): (分數文字, 標籤)}，只含 neutral 列。"""
    with tempfile.TemporaryDirectory() as d:
        b, o = os.path.join(d, "base.txt"), os.path.join(d, "overlay.tsv")
        open(b, "w", encoding="utf-8").write(base_lines)
        open(o, "w", encoding="utf-8").write(overlay_lines)
        text, _ = bs.build(b, o, penalty)
    out = {}
    for line in text.splitlines():
        r, w, s, tag = line.split("\t")
        if tag == "neutral":
            out[(r, w)] = s
    return out


class Neutral(unittest.TestCase):
    def test_highest_same_rime_tone_and_penalty(self):
        # 子：ㄗˇ 最高；ㄗ˙（輕聲）與不同韻的 ㄗㄜˇ 分數更高但不算，ㄗˋ 同韻但較低
        got = run("ㄓㄨㄛ-ㄗ˙ 桌子 -5.0\nㄗˇ 子 -4.0\nㄗ˙ 子 -1.0\nㄗㄜˇ 子 -0.5\nㄗˋ 子 -6.0\n")
        self.assertEqual(got, {("ㄓㄨㄛ-ㄗˇ", "桌子"): "-6.00000000"})

    def test_penalty_override(self):
        got = run("ㄓㄨㄛ-ㄗ˙ 桌子 -5.0\nㄗˇ 子 -4.0\n", penalty=0.5)
        self.assertEqual(got, {("ㄓㄨㄛ-ㄗˇ", "桌子"): "-5.50000000"})

    def test_duplicate_single_reading_keeps_the_highest(self):
        # 子 ㄗˇ 出現兩次：取高的 -4.0，不是後面的 -9.0（ㄗˋ -6.0 因此不是最高）
        got = run("ㄓㄨㄛ-ㄗ˙ 桌子 -5.0\nㄗˇ 子 -4.0\nㄗˋ 子 -6.0\nㄗˇ 子 -9.0\n")
        self.assertEqual(set(got), {("ㄓㄨㄛ-ㄗˇ", "桌子")})

    def test_ties_one_row_each(self):
        got = run("ㄓㄨㄛ-ㄗ˙ 桌子 -5.0\nㄗˇ 子 -4.0\nㄗˋ 子 -4.0\nㄗˊ 子 -9.0\n")
        self.assertEqual(set(got), {("ㄓㄨㄛ-ㄗˇ", "桌子"), ("ㄓㄨㄛ-ㄗˋ", "桌子")})

    def test_no_same_rime_keeps_the_neutral_and_no_change_writes_nothing(self):
        # 的 has no same-rime non-neutral reading: the core rejects a toneless ㄉㄜ, so that position stays neutral
        self.assertFalse(run("ㄅㄞˊ-ㄙㄜˋ-ㄉㄜ˙ 白色的 -5.0\nㄉㄜ˙ 的 -2.0\nㄉㄧˊ 的 -3.0\n"))
        got = run("ㄓㄨㄛ-ㄗ˙-ㄉㄜ˙ 桌子的 -5.0\nㄗˇ 子 -4.0\nㄉㄜ˙ 的 -2.0\n")
        self.assertEqual(set(got), {("ㄓㄨㄛ-ㄗˇ-ㄉㄜ˙", "桌子的")})

    def test_several_neutral_positions_replaced_together(self):
        got = run("ㄇㄚ-ㄇㄚ˙-ㄗ˙ 媽媽子 -5.0\nㄇㄚˇ 媽 -3.0\nㄗˇ 子 -4.0\n")
        # 兩個輕聲位置只換成一列
        self.assertEqual(set(got), {("ㄇㄚ-ㄇㄚˇ-ㄗˇ", "媽媽子")})

    def test_several_positions_with_ties_expand_to_combinations(self):
        got = run("ㄗ˙-ㄗ˙ 子子 -5.0\nㄗˇ 子 -4.0\nㄗˋ 子 -4.0\n")
        self.assertEqual(len(got), 4)

    def test_existing_reading_word_not_written(self):
        base = "ㄓㄨㄛ-ㄗ˙ 桌子 -5.0\nㄗˇ 子 -4.0\n"
        self.assertEqual(len(run(base)), 1)
        self.assertEqual(run(base + "ㄓㄨㄛ-ㄗˇ 桌子 -5.5\n"), {})                       # 基底已有
        self.assertEqual(run(base, "ㄓㄨㄛ-ㄗˇ\t桌子\t-5.5\toverlay\n"), {})            # 疊加層已有

    def test_single_character_gets_no_row(self):
        self.assertEqual(run("ㄗ˙ 子 -1.0\nㄗˇ 子 -4.0\n"), {})

    def test_overlay_only_neutral_word_gets_no_row(self):
        self.assertEqual(run("ㄗˇ 子 -4.0\n", "ㄓㄨㄛ-ㄗ˙\t桌子\t-5.0\toverlay\n"), {})


def run_tagged(base_lines, tag):
    with tempfile.TemporaryDirectory() as d:
        b, o = os.path.join(d, "base.txt"), os.path.join(d, "overlay.tsv")
        open(b, "w", encoding="utf-8").write(base_lines)
        open(o, "w", encoding="utf-8").write("")
        text, per_rule = bs.build(b, o)
    return {(r, w): s for r, w, s, t in (line.split("\t") for line in text.splitlines()) if t == tag}, per_rule


class Fusion(unittest.TestCase):
    """R7（docs/spec/decoder.md §8.1）：詞首的這／那／哪接量詞時加合音 ㄓㄟˋ／ㄋㄟˋ／ㄋㄟˇ 的列，分數照抄。"""

    def test_word_initial_demonstrative_before_a_classifier(self):
        got, per_rule = run_tagged("ㄋㄚˋ-ㄍㄜ˙ 那個 -4.0\nㄋㄚˇ-ㄍㄜ˙ 哪個 -5.0\nㄓㄜˋ-ㄒㄧㄝ 這些 -4.5\nㄓㄜˋ-ㄅㄣˇ-ㄕㄨ 這本書 -6.0\n", "fusion")
        self.assertEqual(got, {("ㄋㄟˋ-ㄍㄜ˙", "那個"): "-4.0", ("ㄋㄟˇ-ㄍㄜ˙", "哪個"): "-5.0",
                               ("ㄓㄟˋ-ㄒㄧㄝ", "這些"): "-4.5", ("ㄓㄟˋ-ㄅㄣˇ-ㄕㄨ", "這本書"): "-6.0"})
        self.assertEqual(per_rule["R7"], 4)

    def test_not_a_classifier_not_word_initial_or_other_reading(self):
        base = ("ㄋㄚˋ-ㄅㄚˋ 那霸 -5.0\nㄋㄚˋ-ㄇㄜ˙ 那麼 -4.0\nㄅㄛ-ㄓㄚˊ-ㄋㄚˋ 波札那 -6.0\nㄓㄜˋ-ㄧ-ㄍㄜ˙ 這一個 -6.0\n"
                "ㄋㄚˇ-ㄍㄜ˙ 那個 -7.0\nㄓㄜˋ 這 -2.0\n")
        self.assertEqual(run_tagged(base, "fusion")[0], {})

    def test_existing_reading_not_written(self):
        self.assertEqual(run_tagged("ㄋㄚˋ-ㄍㄜ˙ 那個 -4.0\nㄋㄟˋ-ㄍㄜ˙ 那個 -4.5\n", "fusion")[0], {})


if __name__ == "__main__":
    unittest.main()
