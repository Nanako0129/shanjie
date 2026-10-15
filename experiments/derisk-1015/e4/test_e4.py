"""Tests for experiments/derisk-1015/e4 (a) (synthetic contexts and a stub language model; no data files).
Run: python3 -W ignore -m unittest experiments/derisk-1015/e4/test_e4.py -v   (from the repo root)
"""
import contextlib
import io
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import patched_eval  # noqa: E402
import punct_ctx as pc  # noqa: E402
import subset  # noqa: E402

L = patched_eval.L
BASE = "我們吃飯"      # context_key is "吃飯" (2 Han characters)


class StubLM:
    """lm.history reads .ids and .ctx: which words have bigram history."""
    ids = {"吃飯": 1, "飯": 2}
    ctx = {1: object(), 2: object()}


class PunctuationClassTest(unittest.TestCase):
    def test_every_mid_character_keeps_the_context_and_the_control_resets(self):
        self.assertEqual(L.context_key(BASE), "吃飯")
        for ch in sorted(pc.MID):
            with self.subTest(ch=hex(ord(ch))):
                self.assertEqual(L.context_key(BASE + ch), "")             # today: reset
                self.assertEqual(pc.context_key_mid(BASE + ch), "吃飯")    # E4 (a): keep
                self.assertEqual(L.history(pc.context_key_mid(BASE + ch), StubLM), "吃飯")
                self.assertEqual(L.history(L.context_key(BASE + ch), StubLM), "<s>")

    def test_every_final_character_still_resets(self):
        for ch in sorted(pc.FINAL):
            with self.subTest(ch=hex(ord(ch))):
                self.assertEqual(pc.context_key_mid(BASE + ch), "")
                self.assertEqual(L.history(pc.context_key_mid(BASE + ch), StubLM), "<s>")

    def test_the_two_sets_are_disjoint_and_cover_the_documented_characters(self):
        self.assertFalse(pc.MID & pc.FINAL)
        self.assertEqual(len(pc.MID), 12)
        self.assertEqual(len(pc.FINAL), 10)
        for ch in "，、；：,;:。？！.?!":
            self.assertIn(ch, pc.MID | pc.FINAL)

    def test_other_non_han_characters_keep_resetting(self):
        for tail in ["「", "」", "（", "）", " ", "\t", "—", "·", "7", "Colab", "1.5", "😀", "ㄅ"]:
            with self.subTest(tail=tail):
                self.assertEqual(pc.context_key_mid(BASE + tail), L.context_key(BASE + tail))
                self.assertEqual(pc.context_key_mid(BASE + tail), "")

    def test_only_trailing_mid_punctuation_matters(self):
        self.assertEqual(pc.context_key_mid(BASE + "，，、"), "吃飯")        # a run is skipped
        self.assertEqual(pc.context_key_mid("你好，" + BASE + "，"), "吃飯")
        self.assertEqual(pc.context_key_mid("你好，"), "你好")
        self.assertEqual(pc.context_key_mid("你好。，"), "")                # final punctuation before the comma still ends the run
        self.assertEqual(pc.context_key_mid("快1.5，"), "")                 # digits and English before the comma still reset
        self.assertEqual(pc.context_key_mid("量一下Colab,"), "")
        self.assertEqual(pc.context_key_mid("，"), "")
        self.assertEqual(pc.context_key_mid(""), "")

    def test_history_falls_back_to_the_last_character_then_start(self):
        only_last = type("M", (), {"ids": {"飯": 2}, "ctx": {2: object()}})
        self.assertEqual(L.history(pc.context_key_mid(BASE + "，"), only_last), "飯")
        none = type("M", (), {"ids": {}, "ctx": {}})
        self.assertEqual(L.history(pc.context_key_mid(BASE + "，"), none), "<s>")

    def test_contexts_without_mid_punctuation_are_untouched(self):
        for c in ["", "你", BASE, "我們。", "「你好」", "a1 吃飯", "吃飯 ", "😀"]:
            self.assertEqual(pc.context_key_mid(c), L.context_key(c))

    def test_run_lm_eval_swaps_the_function_only_for_the_run(self):
        before = L.context_key
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):   # no --profile: argparse stops the run, the patch must still be undone
            patched_eval.run_lm_eval([], context_key=pc.context_key_mid)
        self.assertIs(L.context_key, before)

    def test_e4_eval_refuses_to_run_without_context(self):
        import e4_eval
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()):
            e4_eval.main(["--punct", "mid", "--profile", "chat", "--rows", "x"])
        self.assertEqual(e.exception.code, 2)


class SubsetTest(unittest.TestCase):
    def test_classification_and_concat_offsets(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "a.txt"), os.path.join(td, "b.txt")
            open(a, "w", encoding="utf-8").write("你好，|今天|ㄐㄧㄣ ㄊㄧㄢ\n|今天|ㄐㄧㄣ ㄊㄧㄢ\nbad line\n")
            open(b, "w", encoding="utf-8").write("快1.5|倍|ㄅㄟˋ\n量一下Colab|羊毛|ㄧㄤˊ ㄇㄠˊ\n吃飯。|好|ㄏㄠˇ\n好嗎，|好|ㄏㄠˇ\n")
            table = subset.report([("a", a), ("b", b)], os.path.join(td, "out"))
            idx = lambda n: open(os.path.join(td, "out", n)).read().split()
            self.assertEqual(idx("a.any.idx"), ["1"])
            self.assertEqual(idx("a.changed.idx"), ["1"])
            self.assertEqual(idx("b.any.idx"), ["1", "2", "4"])
            self.assertEqual(idx("b.changed.idx"), ["4"])   
            self.assertEqual(idx("concat.any.idx"), ["1", "3", "4", "6"])   # b's rows are shifted by a's 2 rows (the 1-field line is not a row)
            self.assertEqual(idx("concat.changed.idx"), ["1", "6"])
            self.assertEqual(table[-1], "| concat | 6 | 4 | 2 | |")

    def test_a_sentence_with_a_non_han_character_is_counted(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "s.txt")
            open(p, "w", encoding="utf-8").write("|你，好|ㄋㄧˇ ㄏㄠˇ\n|你好|ㄋㄧˇ ㄏㄠˇ\n")
            self.assertEqual(subset.classify(open(p, encoding="utf-8").read().splitlines())[2], 1)


if __name__ == "__main__":
    unittest.main()
