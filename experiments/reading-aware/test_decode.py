"""Tests of decode_units.py (contract docs/contracts/reading-aware-0.md 4.1, "單位的解碼"). Synthetic data only."""
import math
import tempfile
import unittest

import decode_units as D
import testkit
from testkit import U
import lm as L

R1, R2 = "ㄅㄚ", "ㄅㄚ˙"
BARE_UNI = {"好": 100, "吧": 50, "你": 100, "了": 80, "好吧": 20, "嗎": 30}
BARE_BI = {("<s>", "好"): 50, ("好", "吧"): 10, ("好", "</s>"): 5, ("<s>", "你"): 40, ("你", "好"): 20, ("吧", "</s>"): 10,
           ("<s>", "吧"): 6, ("你", "嗎"): 9, ("嗎", "</s>"): 9, ("好吧", "</s>"): 4, ("<s>", "好吧"): 4, ("你", "了"): 5, ("了", "</s>"): 5}
UNIT_UNI = {"好": 100, "你": 100, "了": 80, "嗎": 30, "吧" + U + R1: 50}
UNIT_BI = {("<s>", "好"): 50, ("好", "吧" + U + R1): 10, ("<s>", "吧" + U + R1): 6, ("吧" + U + R1, "</s>"): 10, ("好", "</s>"): 5,
           ("<s>", "你"): 40, ("你", "好"): 20, ("你", "嗎"): 9, ("嗎", "</s>"): 9}
SEQS = [["ㄅㄚ˙"], ["ㄅㄚ"], ["ㄏㄠˇ", "ㄅㄚ˙"], ["ㄏㄠˇ", "ㄅㄚ"], ["ㄋㄧˇ", "ㄏㄠˇ", "ㄇㄚ˙"], ["ㄋㄧˇ", "ㄌㄜ˙"], ["ㄋㄧˇ", "ㄏㄠˇ"]]


def lex_with_table(d, table=None):
    lex = testkit.lexicon(d)
    out = L.cap_overlay(lex, set(), type("M", (), {"count": lambda s, w: 0, "N": 1})(), demote_path=None)
    out.demote = table or {}
    return out


class BareModel(unittest.TestCase):
    """A model with only word-string keys: the extended decoder is lm.decode, bit for bit (contract: B0-equivalence)."""

    def test_bit_for_bit_equal_to_lm_decode(self):
        with tempfile.TemporaryDirectory() as d:
            _, lm = testkit.sjlm(d, BARE_UNI, BARE_BI)
            for table in ({}, {(R2, "吧"): 0.3, ("ㄏㄠˇ-ㄅㄚ˙", "好吧"): 0.7}):
                lex = lex_with_table(d, table)
                for profile in ("chat", "formal"):
                    for start in ("<s>", "好", "你", "不在模型裡"):
                        for demote in (True, False):
                            for syls in SEQS:
                                with self.subTest(table=bool(table), profile=profile, start=start, demote=demote, syls=syls):
                                    self.assertEqual(D.decode(lex, syls, lm, profile, start=start, demote=demote),
                                                     L.decode(lex, syls, lm, profile, start=start, demote=demote))
                                    self.assertEqual(D.decode(lex, syls, lm, profile, beam=3, start=start),
                                                     L.decode(lex, syls, lm, profile, beam=3, start=start))

    def test_history_equals_lm_history(self):
        with tempfile.TemporaryDirectory() as d:
            _, lm = testkit.sjlm(d, BARE_UNI, BARE_BI)
            for left in ("", "好", "好吧", "你好", "嗎", "巴", "不在", "風好"):
                self.assertEqual(D.history(left, lm), L.history(left, lm), left)

    def test_count_view_is_the_models_count(self):
        with tempfile.TemporaryDirectory() as d:
            _, lm = testkit.sjlm(d, BARE_UNI, BARE_BI)
            v = D.CountView(lm)
            self.assertEqual((v.N, [v.count(w) for w in ("好", "吧", "巴", "好吧")]), (lm.N, [lm.count(w) for w in ("好", "吧", "巴", "好吧")]))


class UnitModel(unittest.TestCase):
    """A model that holds only `吧 U+0001 ㄅㄚ`: the other reading of the character, ㄅㄚ˙, is never seen."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        _, self.lm = testkit.sjlm(self.td.name, UNIT_UNI, UNIT_BI)
        self.lex = lex_with_table(self.td.name)
        self.lam = L.PROFILES["chat"]

    def score(self, syls, surface, start="<s>"):
        return {"".join(ws): s for s, ws in D.decode(self.lex, syls, self.lm, "chat", start=start)}[surface]

    def test_unit_in_model_is_looked_up_by_unit(self):
        lm, lam = self.lm, self.lam
        unit = "吧" + U + R1
        want = lm.word(lam, "<s>", unit, -2.0) + lm.eos(lam, unit)
        self.assertEqual(self.score([R1], "吧"), want)
        self.assertGreater(lm.prob("<s>", unit, 10 ** -2.0), 10 ** -2.0)    # the bigram entry really is used, not the back-off alone

    def test_unseen_reading_is_the_character_probability_times_the_share(self):
        lm, lam = self.lm, self.lam
        u1, u2 = "吧" + U + R1, "吧" + U + R2
        self.assertNotIn(u2, lm.ids)
        p_char = lm.prob("<s>", u1, 10 ** -2.0)                  # the only unit's probability is the character's
        share = 10 ** -2.0 / (10 ** -2.0 + 10 ** -2.0)           # lexicon: 吧 ㄅㄚ and ㄅㄚ˙ both -2.0
        want = lam * math.log10(p_char * share) + (1 - lam) * -2.0 + lm.eos(lam, u2)
        self.assertEqual(self.score([R2], "吧"), want)
        # the same thing seen from the seen reading: exactly lam * log10(share) lower, no UNSEEN_OVERLAY_PENALTY anywhere
        idx = D.index_of(lm)
        t1 = idx.word(lam, "<s>", "吧", (R1,), -2.0, idx.char_table(self.lex))[0]
        t2 = idx.word(lam, "<s>", "吧", (R2,), -2.0, idx.char_table(self.lex))[0]
        self.assertAlmostEqual(t1 - t2, -lam * math.log10(share), places=12)

    def test_share_follows_the_lexicon_scores(self):
        lex = testkit.lexicon(self.td.name, testkit.LEXICON.replace("ㄅㄚ˙ 吧 -2.0", "ㄅㄚ˙ 吧 -2.3"), "lex2.txt")
        lex = L.cap_overlay(lex, set(), type("M", (), {"count": lambda s, w: 0, "N": 1})(), demote_path=None)
        idx = D.index_of(self.lm)
        term, unit = idx.word(self.lam, "<s>", "吧", (R2,), -2.3, idx.char_table(lex))
        p_char = self.lm.prob("<s>", "吧" + U + R1, 10 ** -2.0)
        share = 10 ** -2.3 / (10 ** -2.3 + 10 ** -2.0)
        self.assertEqual((term, unit), (self.lam * math.log10(p_char * share) + (1 - self.lam) * -2.3, "吧" + U + R2))

    def test_character_absent_from_the_model_takes_the_out_of_vocabulary_path(self):
        # 巴 is in no key of the model: the bare lookup, which is lm.decode's own (the unit model is just an unknown word to it)
        ref = {"".join(ws): s for s, ws in L.decode(self.lex, [R2], self.lm, "chat")}
        self.assertEqual(self.score([R2], "巴"), ref["巴"])

    def test_bare_characters_in_a_unit_model_stay_reading_agnostic(self):
        ref = {"".join(ws): s for s, ws in L.decode(self.lex, ["ㄋㄧˇ", "ㄌㄜ˙"], self.lm, "chat")}
        got = {"".join(ws): s for s, ws in D.decode(self.lex, ["ㄋㄧˇ", "ㄌㄜ˙"], self.lm, "chat")}
        self.assertEqual(got["你了"], ref["你了"])

    def test_the_next_word_after_an_unseen_unit_has_no_bigram_context(self):
        lm, lam = self.lm, self.lam
        w1 = {"".join(ws): s for s, ws in D.decode(self.lex, [R2, "ㄏㄠˇ"], lm, "chat")}["吧好"]
        first = self.score([R2], "吧") - lm.eos(lam, "吧" + U + R2)
        want = first + lm.word(lam, "吧" + U + R2, "好", -1.5) + lm.eos(lam, "好")
        self.assertAlmostEqual(w1, want, places=12)
        self.assertEqual(lm.prob("吧" + U + R2, "好", 10 ** -1.5), 10 ** -1.5)   # backed off to the lexicon

    def test_history_is_the_most_frequent_unit(self):
        with tempfile.TemporaryDirectory() as d:
            u1, u2 = "吧" + U + R1, "吧" + U + R2
            uni = {**UNIT_UNI, u1: 50, u2: 70}
            bi = {**UNIT_BI, (u2, "</s>"): 8, (u1, "</s>"): 10}
            _, lm = testkit.sjlm(d, uni, bi)
            self.assertEqual(D.history("吧", lm), u2)          # 70 > 50
            self.assertEqual(D.history("好吧", lm), u2)         # "好吧" (two characters) has no entry; the last character is looked up as a unit
            self.assertEqual(D.history("你好", lm), "好")
            uni2 = {**uni, u1: 90}
            _, lm2 = testkit.sjlm(d, uni2, bi, "m2.sjlm")
            self.assertEqual(D.history("吧", lm2), u1)

    def test_count_view_sums_the_units(self):
        with tempfile.TemporaryDirectory() as d:
            u1, u2 = "吧" + U + R1, "吧" + U + R2
            _, lm = testkit.sjlm(d, {**UNIT_UNI, u1: 50, u2: 70}, UNIT_BI)
            self.assertEqual(D.CountView(lm).count("吧"), 120)
            self.assertEqual(D.CountView(lm).count("好"), 100)
            self.assertEqual(D.CountView(lm).N, lm.N)


if __name__ == "__main__":
    unittest.main()
