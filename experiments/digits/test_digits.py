"""Unit tests for experiments/digits (digit-context.md C.4): a hand-made lexicon, a stub language model and a hand-made count table.
No real data, no network.   Run: cd experiments/digits && python3 -W ignore -m unittest test_digits"""
import argparse
import collections
import contextlib
import io
import json
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import digits_score as DS  # noqa: E402
import golden_digits as GD  # noqa: E402
import measure as M  # noqa: E402

import build_digits as bd  # noqa: E402

P1, P3, ime = DS.P1, DS.P3, bd.bc.ime

LEX_ROWS = [
    ("ㄚˋ", "啊", -2.0), ("ㄚˋ", "阿", -2.5), ("ㄓㄢˋ", "戰", -2.5), ("ㄅㄟˋ", "倍", -2.5), ("ㄩㄝˋ", "月", -2.0), ("ㄩㄢˊ", "元", -2.0),
    ("ㄋㄧㄢˊ", "年", -2.0), ("ㄉㄧㄢˇ", "點", -2.0), ("ㄅ", "ㄅ", -5.0), ("ㄉㄢˋ", "蛋", -3.0), ("ㄉㄚ", "搭", -3.0),
    ("ㄋㄧㄢˊ-ㄉㄞˋ", "年代", -3.5), ("ㄉㄧㄢˇ-ㄒㄧㄣ", "點心", -3.0), ("ㄅㄞˇ", "百", -2.0),
    ("ㄅㄚˋ", "爸", -2.0), ("ㄘㄜˋ", "測", -2.5), ("ㄕˋ", "試", -2.5), ("ㄓㄨㄢˇ", "轉", -3.0), ("ㄧㄢˊ", "言", -2.5), ("ㄉㄜ˙", "的", -1.0),
]


def mklex(rows=LEX_ROWS):
    lex = ime.Lexicon.__new__(ime.Lexicon)
    lex.by_reading, lex.by_word = collections.defaultdict(list), {}
    for r, w, lp in rows:
        syls = tuple(r.split("-"))
        lex.by_reading[syls].append((w, lp))
        if len(w) == len(syls) and (w not in lex.by_word or lp > lex.by_word[w][1]):
            lex.by_word[w] = (syls, lp)
    for k in lex.by_reading:
        lex.by_reading[k].sort(key=lambda x: -x[1])
    lex.max_len = max(len(k) for k in lex.by_reading)
    return lex


class StubLM:
    """prob(v, w, pb) = pb unless `over` has (v, w); word() = lp (predict.py's StubLM); a vocabulary for succ_words."""

    def __init__(self, over=None, succ=()):
        self.over = over or {}
        self.vocab = ["<s>", "</s>"] + list(succ)
        self.ids = {w: i for i, w in enumerate(self.vocab)}
        self.ctx = {0: (1, 0.5, {self.ids[w]: 1 for w in succ})} if succ else {}
        self.kn = None

    def prob(self, v, w, pb):
        return self.over.get((v, w), pb)

    def word(self, lam, v, w, lp):
        return lp


TABLE_TEXT = """# test table
C\t.\t5
C\t-\t5
C\t0\t10
C\t1\t10
C\t2\t10
C\t3\t10
C\t4\t10
C\t5\t10
C\t6\t10
C\t7\t10
C\t8\t10
C\t9\t10
D\t<s>\t5\t100
D\t下午\t30\t40
L\t1\t30
L\t2\t30
L\t3\t20
L\t4\t15
L\t5\t5
N\t100
P\t下午\t點\t15
Q\t<s>\t3
Q\t下午\t30
S\t20
T\t1000
W\t年\t10
W\t點\t20
W\t元\t10
"""
PD = 100 / 1000


def identity_history(shown):
    """The context is the whole shown text, so a test can see exactly what the run context was."""
    return shown or "<s>"


def world(lm=None, table_text=TABLE_TEXT, **kw):
    lex = mklex()
    lm = lm or StubLM()
    sc = DS.Scorer(bd.Table(table_text), lm, P1.Index(lex), DS.Legal(lex), **kw)
    return lex, lm, sc


def run(sc, keys, left="", chars=(), hist=identity_history):
    return DS.simulate(sc, hist, list(keys), left, list(chars))


class Keys(unittest.TestCase):
    def test_keys_of_syllable(self):
        self.assertEqual(DS.keys_of_syllable("ㄋㄧㄢˊ"), list("su06"))
        self.assertEqual(DS.keys_of_syllable("ㄉㄧㄢˇ"), list("2u03"))
        self.assertEqual(DS.keys_of_syllable("ㄅㄚ˙"), list("187"))
        self.assertEqual(DS.keys_of_syllable("ㄚ"), ["8", " "])        # the first tone is the space bar

    def test_constants_match_engine_rs(self):
        src = open(os.path.join(ROOT, "core", "src", "engine.rs"), encoding="utf-8").read()
        self.assertIn(f'const SYMBOLS: &str = "{DS.SYMBOLS}";', src)
        self.assertIn(f'const KEYS_STANDARD: &str = "{DS.KEYS_STANDARD}";', src)
        self.assertIn("const TONE_KEYS_STANDARD: [char; 4] = ['6', '3', '4', '7'];", src)   # tones 2 3 4 5, in DS.TONE_KEYS' pairs
        self.assertEqual([DS.TONE_KEYS[k] for k in "6347"], ["ˊ", "ˇ", "ˋ", "˙"])


class Legality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legal = DS.Legal(mklex())

    def units(self, keys):
        return self.legal.tail_units(list(keys))

    def test_prefixes_and_order(self):
        self.assertIsNotNone(self.units("8"))        # ㄚ is a prefix of ㄚˋ
        self.assertIsNotNone(self.units("s"))        # ㄋ
        self.assertIsNotNone(self.units("5j0"))      # ㄓㄨㄢ of ㄓㄨㄢˇ
        self.assertIsNone(self.units("81"))          # ㄚㄅ: not in the initial, medial, final order
        self.assertIsNone(self.units("10"))          # ㄅㄢ is not a prefix of any syllable in this lexicon
        self.assertIsNone(self.units("0u"))          # ㄢㄧ

    def test_tone_key_completes_a_syllable_in_the_lexicon(self):
        self.assertEqual(self.units("84"), [(("ㄚ",), True, "ˋ")])
        self.assertIsNotNone(self.units("2u03"))
        self.assertIsNone(self.units("8" + "3"))     # ㄚˇ is not a syllable here
        self.assertIsNone(self.units("4"))           # a tone alone is no tail
        self.assertIsNone(self.units("834"))         # a tone key inside a tail

    def test_first_tone_by_space(self):
        self.assertEqual(self.units("28 "), [(("ㄉ", "ㄚ"), True, "")])
        self.assertIsNone(self.units("2 "))          # "ㄉ" as a syllable: not in the lexicon
        self.assertIsNone(self.units(" "))

    def test_a_syllable_with_only_a_letter_word_is_not_a_word(self):
        self.assertIsNone(self.units("1 "))          # ㄅ exists only as the entry "ㄅ"
        self.assertIsNotNone(self.units("1"))        # but it is a legal prefix (ㄅㄟˋ, ㄅㄞˇ)

    def test_syllable_only_inside_a_longer_han_word_is_complete(self):
        legal = DS.Legal(mklex([("ㄍㄜˋ-ㄍㄜˋ", "個個", -3.0), ("ㄅ-ㄅ", "ㄅㄅ", -5.0)]))
        self.assertIsNotNone(legal.tail_units(list("ek4")))      # ㄍㄜˋ occurs in a Han word
        self.assertIsNone(legal.tail_units(["1", " "]))          # ㄅ occurs only in letter words


class Scoring(unittest.TestCase):
    def test_lp_d_by_hand(self):
        _, _, sc = world()
        want = math.log10((5 + PD) / 101) + math.log10(30 / 100) + math.log10(10 / 110)
        self.assertAlmostEqual(sc.lp_d("<s>", ["8"], True), want)
        # not ended, two keys: P_len(>= 2) = (30 + 20 + 15 + 5) / 100
        self.assertAlmostEqual(sc.lp_d("下午", ["3", "2"], False),
                               math.log10((30 + PD) / 41) + math.log10(70 / 100) + math.log10(10 / 110) + math.log10(10 / 110))
        self.assertEqual(sc.lp_d("<s>", [], True), 0.0)
        self.assertEqual(sc.lp_d("<s>", ["/"], True), DS.NEG)     # the table never saw `/`

    def test_lp_z_is_a_plain_sum_in_candidate_order(self):
        lex, lm, sc = world()
        units = P1.units_of(["ㄚ"])
        self.assertEqual([c[0] for c in sc.candidates(units)], ["啊", "阿"])
        self.assertAlmostEqual(sc.lp_z("<s>", False, units), math.log10(10 ** -2.0 + 10 ** -2.5))
        sc.cache.sums.clear()
        tot = 0.0
        for w, _, pb in sc.candidates(units):
            tot += sc.tb.p_w_given_vD(w, "下午", pb, 1.0)
        self.assertEqual(sc.lp_z("下午", True, units), math.log10(tot))      # digit context: bit equal to the sum written out

    def test_context_changes_the_sums_and_digits_select_the_digit_context(self):
        lex, lm, sc = world(lm=StubLM(over={("下午", "啊"): 0.5}))
        units = P1.units_of(["ㄚ"])
        self.assertAlmostEqual(sc.lp_z("下午", False, units), math.log10(0.5 + 10 ** -2.5))      # the word context's own probability for 啊
        self.assertAlmostEqual(sc.lp_z("<s>", False, units), math.log10(10 ** -2.0 + 10 ** -2.5))
        c, _ = sc.decide("下午", [], ["8"], None, None)                                           # no digits before the tail: word context
        self.assertAlmostEqual(c[1][2], sc.lp_d("下午", [], False) + sc.lp_z("下午", False, units))
        c, _ = sc.decide("下午", ["3"], ["8"], None, 1)                                           # a digit before the tail: digit context (v, D)
        self.assertAlmostEqual(c[1][2], sc.lp_d("下午", ["3"], True) + sc.lp_z("下午", True, units))
        self.assertNotAlmostEqual(sc.lp_z("下午", True, units), sc.lp_z("下午", False, units))

    def test_split_scores_of_a_first_key(self):
        _, _, sc = world()
        cands, pick = sc.decide("<s>", [], ["8"], None, None)
        self.assertEqual([(m, t) for m, t, _ in cands], [(1, 0), (0, 1)])
        s_digit = sc.lp_d("<s>", ["8"], False)                    # the run goes on: not ended
        s_zh = math.log10(10 ** -2.0 + 10 ** -2.5)
        self.assertAlmostEqual(cands[0][2], s_digit)
        self.assertAlmostEqual(cands[1][2], s_zh)
        self.assertEqual(pick, 1)                                  # a lone 8 is zhuyin ㄚ (the digit split costs P(D|<s>) etc.)

    def test_theta_rule_with_a_forced_margin(self):
        """A table where digits beat zhuyin by a known margin: the pick follows `digit > zhuyin + theta` exactly (strict)."""
        _, _, sc = world()
        cands, _ = sc.decide("<s>", [], ["8", "4"], None, None)
        by_m = {m: s for m, _, s in cands}
        d, z = by_m[2], by_m[0]
        for th, want_digit in ((d - z - 0.001, True), (d - z, False), (d - z + 0.001, False)):
            _, _, s2 = world(theta=th)
            c2, p2 = s2.decide("<s>", [], ["8", "4"], None, None)
            self.assertEqual(c2[p2][1] == 0, want_digit, th)

    def test_theta_guards_only_the_splits_without_any_digit(self):
        """C.6 item 3 (the 1984 case): once the run holds a digit, the all-digit split is compared with the zhuyin splits by score alone."""
        _, _, sc = world(theta=10.0)
        sc.lp_d = lambda C, dkeys, ended: -1.0 if dkeys else 0.0
        sc.lp_z = lambda C, digit_ctx, units: -0.5
        # `1` already emitted, buffer 8 4 (ㄚˋ is legal): all digits -1.0 beats the zhuyin split (holds the 1) -1.5, theta 10 or not
        c, p = sc.decide("<s>", ["1"], ["8", "4"], None, 3)
        self.assertEqual([(m, t) for m, t, _ in c], [(2, 0), (0, 2)])
        self.assertEqual((c[p][0], c[p][1]), (2, 0))
        # the same keys as a pure-zhuyin split (nothing emitted): theta 10 puts ㄚˋ ahead of the digits; the margin below decides at a small theta
        c, p = sc.decide("<s>", [], ["8", "4"], None, None)
        self.assertEqual((c[p][0], c[p][1]), (0, 2))
        sc.lp_z = lambda C, digit_ctx, units: -1.3     # digits -1.0, pure zhuyin -1.3: zhuyin only because of theta
        for th, want in ((0.0, (2, 0)), (0.29, (2, 0)), (0.31, (0, 2))):
            sc.theta = th
            c, p = sc.decide("<s>", [], ["8", "4"], None, None)
            self.assertEqual((c[p][0], c[p][1]), want, th)

    def test_theta_among_zhuyin_splits_only_for_the_one_without_a_digit(self):
        _, _, sc = world()
        sc.lp_d = lambda C, dkeys, ended: 1.0 if dkeys else 0.0
        sc.lp_z = lambda C, digit_ctx, units: 0.0
        # buffer 1 8 4: all digits (1.0), 1 + ㄚˋ (1.0, holds a digit), ㄅㄚˋ (0.0, none)
        c, _ = sc.decide("<s>", [], ["1", "8", "4"], None, None)
        self.assertEqual([(m, t) for m, t, _ in c], [(3, 0), (1, 2), (0, 3)])
        for th, want in ((0.0, (1, 2)), (0.9, (1, 2)), (1.1, (0, 3)), (5.0, (0, 3))):   # a tie with the digits goes to zhuyin; theta lifts only ㄅㄚˋ
            sc.theta = th
            c, p = sc.decide("<s>", [], ["1", "8", "4"], None, None)
            self.assertEqual((c[p][0], c[p][1]), want, th)
        sc.lp_d = lambda C, dkeys, ended: 1.5 if len(dkeys) == 3 else (1.0 if dkeys else 0.0)   # digits strictly ahead of every effective score
        sc.theta = 0.4
        c, p = sc.decide("<s>", [], ["1", "8", "4"], None, None)
        self.assertEqual((c[p][0], c[p][1]), (3, 0))

    def test_theta_at_a_non_alphabet_key(self):
        """A.3.3 revision three: a non-alphabet key decides the run on the spot, so its digit-free zhuyin split gets theta too
        (dev302 row 196: 嗎 on screen before 碼 resolved, `1` then `u`, digit + ㄧ beat ㄅㄧ by plain score)."""
        _, _, sc = world()
        sc.lp_d = lambda C, dkeys, ended: -1.0 if dkeys else 0.0
        sc.lp_z = lambda C, digit_ctx, units: 0.0 if digit_ctx else -1.3     # 2 + ㄧ -1.0, ㄉㄧ -1.3
        for th, want in ((0.0, (1, 1)), (0.29, (1, 1)), (0.31, (0, 2))):
            sc.theta = th
            c, p = sc.decide("<s>", [], ["2"], "u", None)
            self.assertEqual([(m, t) for m, t, _ in c], [(1, 1), (0, 2)])
            self.assertEqual((c[p][0], c[p][1]), want, th)
        # a digit already emitted: every split holds one, theta guards nothing (5 2 + ㄧ -0.6 beats 5 + ㄉㄧ -0.8 by score alone)
        sc.lp_d = lambda C, dkeys, ended: -1.0 + 0.2 * len(dkeys) if dkeys else 0.0
        sc.theta = 10.0
        c, p = sc.decide("<s>", ["5"], ["2"], "u", 1)
        self.assertEqual((c[p][0], c[p][1]), (1, 1))

    def test_structural_rule_takes_the_largest_legal_tail(self):
        _, _, sc = world(mode="structural")
        c, p = sc.decide("<s>", [], ["8", "4"], None, None)
        self.assertEqual((c[p][0], c[p][1]), (0, 2))               # ㄚˋ is legal: zhuyin
        c, p = sc.decide("<s>", [], ["5", "0"], None, None)
        self.assertEqual((c[p][0], c[p][1]), (0, 2))               # ㄓㄢ is a prefix of ㄓㄢˋ
        self.assertTrue(all(s is None for _, _, s in c))           # no scores in the structural rule
        c, p = sc.decide("<s>", [], ["1", "0"], None, None)
        self.assertEqual((c[p][0], c[p][1]), (2, 0))               # neither ㄅㄢ nor ㄢ starts a syllable of this lexicon: digits

    def test_tie_keeps_the_display_else_prefers_zhuyin(self):
        _, _, sc = world()
        sc.lp_d = lambda C, dkeys, ended: -1.0 if dkeys else 0.0     # both splits of the second key score -1
        sc.lp_z = lambda C, digit_ctx, units: 0.0
        c, p = sc.decide("<s>", ["1"], ["8"], None, 2)                # 2 digits were on display: the digit reading stays
        self.assertEqual((c[p][0], c[p][1]), (1, 0))
        c, p = sc.decide("<s>", ["1"], ["8"], None, 1)                # 1 digit: the tail reading is the display
        self.assertEqual((c[p][0], c[p][1]), (0, 1))
        c, p = sc.decide("<s>", ["1"], ["8"], None, None)             # nothing displayed: tie goes to zhuyin
        self.assertEqual((c[p][0], c[p][1]), (0, 1))
        c, p = sc.decide("<s>", ["1"], ["8"], None, 5)                # a display that matches neither: zhuyin
        self.assertEqual((c[p][0], c[p][1]), (0, 1))


class Simulation(unittest.TestCase):
    def test_fifth_key_flushes_the_oldest(self):
        for mode in ("score", "structural"):
            _, _, sc = world(mode=mode)
            sim = run(sc, "12345")
            self.assertEqual([ev["flush"] for ev in sim.log[:5]], [0, 0, 0, 0, 1])
            self.assertEqual(sim.cls[0], "D")                      # the first key became a digit token
            self.assertEqual(len(sim.log[4]["buf"]), 4)

    def test_non_alphabet_key_forces_the_split(self):
        for mode in ("score", "structural"):
            _, _, sc = world(mode=mode)
            sim = run(sc, "100m06", chars="元")
            self.assertEqual("".join(sim.cls[:6]), "DDDZZZ", mode)
            self.assertEqual(sim.log[3]["choice"], (3, 1))
            self.assertEqual(sim.shown, "100元")

    def test_no_legal_tail_makes_the_buffer_digits_and_the_key_a_new_syllable(self):
        _, _, sc = world(mode="structural")
        sim = run(sc, "1j")                                        # ㄨ starts no syllable of this lexicon
        self.assertEqual("".join(sim.cls[:2]), "DZ")
        self.assertEqual(sim.log[1]["splits"], [])

    def test_space_with_no_syllable_is_a_boundary(self):
        for mode in ("score", "structural"):
            _, _, sc = world(mode=mode)
            sim = run(sc, "2 ")
            self.assertEqual("".join(sim.cls[:2]), "DS")

    def test_space_completes_a_first_tone_syllable_in_structural_mode(self):
        _, _, sc = world(mode="structural")
        self.assertEqual("".join(run(sc, "28 ").cls[:3]), "ZZZ")

    def test_enter_flushes_the_buffer_as_digits(self):
        _, _, sc = world(mode="structural")
        sim = run(sc, "8")                                         # ㄚ shown, no tone: a boundary makes it a digit
        self.assertEqual(sim.cls[0], "D")
        self.assertEqual(sim.log[-1]["flush"], 1)

    def test_tone_key_alone_is_a_digit_that_starts_a_run(self):
        _, _, sc = world()
        sim = run(sc, "3")
        self.assertEqual(sim.cls[0], "D")
        self.assertEqual(sim.log[0]["splits"], [])

    def test_run_context_and_shown_text(self):
        _, _, sc = world(mode="structural")
        sim = run(sc, "32u03" + "2", left="下午", chars="點")
        self.assertEqual("".join(sim.cls[:6]), "DZZZZD")             # the last 2 starts a run and Enter turns it into a digit
        ctxs = [ev["ctx"] for ev in sim.log if ev["ctx"]]
        self.assertEqual(ctxs[0], "下午")                          # the first run's context is the left text
        self.assertEqual(ctxs[-1], "下午3點")                      # a new run after the finished syllable: left + digit + the syllable's character

    def test_flips_count_changes_of_earlier_keys(self):
        _, _, sc = world(mode="structural")
        self.assertEqual(sum(ev["flips"] for ev in run(sc, "84").log), 0)    # ㄚ then ㄚˋ: the first key's reading never changes
        digits = TABLE_TEXT.replace("D\t<s>\t5\t100", "D\t<s>\t99\t100")   # a table that loves digits at the start of a paragraph
        _, _, sc2 = world(table_text=digits)
        sim = run(sc2, "84")
        self.assertEqual([ev["cls"] for ev in sim.log[:2]], ["D", "ZZ"])      # 8 is shown as a digit, the tone key turns it into ㄚˋ
        self.assertEqual([ev["flips"] for ev in sim.log[:2]], [0, 1])

    def test_shows_digit_marks_keys_of_a_run_that_displays_one(self):
        _, _, sc = world(mode="structural")
        sim = run(sc, "100m06", chars="元")
        self.assertEqual([ev["shows_digit"] for ev in sim.log[:6]], [False, True, True, True, False, False])   # the pending 0 6 are no longer part of the run

    def test_pure_chinese_syllable_is_never_digit_under_the_structural_rule(self):
        _, _, sc = world(mode="structural")
        for syl in ("ㄚˋ", "ㄓㄢˋ", "ㄅㄟˋ", "ㄉㄚ", "ㄉㄧㄢˇ", "ㄩㄢˊ", "ㄅㄞˇ", "ㄉㄜ˙", "ㄓㄨㄢˇ"):
            sim = run(sc, DS.keys_of_syllable(syl))
            self.assertNotIn("D", "".join(sim.cls), syl)

    def test_measure_rows(self):
        lex, lm, sc = world(mode="structural")
        env = type("E", (), {"history": staticmethod(identity_history), "lex": lex, "idx": sc.idx, "table": sc.tb, "lm": lm})()
        r = M.zh_row(env, sc, ("", "年", ["ㄋㄧㄢˊ"]))
        self.assertEqual((r["rows"], r["syl"], r["keys"], r["c"], r["d"]), (1, 1, 4, 0, 0))
        r = M.digit_row(env, sc, ("下午", "3", "點", ["ㄉㄧㄢˇ"]))
        self.assertEqual((r["rows"], r["a_ok"]), (1, 1))
        self.assertEqual(r["flips"], collections.Counter([0]))
        self.assertEqual(r["nsplit"][2] > 0, True)                  # timing counters: splits per scored key

    def test_flips_count_only_the_digit_runs_own_keys(self):
        """C.6 item 5: a run that starts inside the following word flips its own keys; those are not the digit run's display."""
        lex, lm, sc = world(table_text=TABLE_TEXT.replace("D\t<s>\t5\t100", "D\t<s>\t99\t100"))
        env = type("E", (), {"history": staticmethod(identity_history), "lex": lex, "idx": sc.idx, "table": sc.tb, "lm": lm})()
        row = ("", "3", "年代", ["ㄋㄧㄢˊ", "ㄉㄞˋ"])
        keys = ["3"] + DS.keys_of_syllable("ㄋㄧㄢˊ") + DS.keys_of_syllable("ㄉㄞˋ")
        sim = DS.simulate(sc, identity_history, keys, "", list("年代"))
        self.assertEqual(sum(ev["flips"] for ev in sim.log[:-1]), 1)       # the second syllable's own run: 2 shown as ㄉ, then flipped to a digit by the 9
        self.assertEqual(sim.rid_of[0], 0)
        self.assertEqual(M.digit_row(env, sc, row)["flips"], collections.Counter([0]))     # ... which the digit run (3) does not count


class Prediction(unittest.TestCase):
    def test_digit_scores_and_tiers(self):
        lex, lm, sc = world(lm=StubLM(succ=["點"]))
        tb, idx = sc.tb, sc.idx
        units = P1.units_of(["ㄉ"])
        cands = P3.reference(idx, units, "P")
        self.assertEqual({e[0] for e in cands}, {"蛋", "搭", "點", "點心", "的"})
        scores = DS.digit_scores(tb, lm, cands, "下午", 0.5)
        for e, s in zip(cands, scores):
            want = 0.5 * math.log10(tb.p_w_given_vD(e[0], "下午", 10 ** e[1], 1.0)) + 0.5 * e[1]
            self.assertAlmostEqual(s, want)
        tiers = DS.digit_tiers(tb, lm, idx, units, "下午", 0.5)
        self.assertEqual([e[0] for e, _ in tiers[0]], ["點"])      # the successor layer: W row 點 and the P row (下午, 點)
        self.assertEqual({e[0] for e, _ in tiers[1]}, {"蛋", "搭", "點心", "的"})
        self.assertEqual(P3.rank_s(tiers, "點"), 1)

    def test_baseline_and_digit_context_differ_by_the_layer(self):
        """Today's `<s>` row puts the model's successors of `<s>` first; the digit row puts the words that follow digits first."""
        lex, lm, sc = world(lm=StubLM(succ=["蛋"]))
        units = P1.units_of(["ㄉ"])
        cands = P3.reference(sc.idx, units, "P")
        base = P3.order_s(cands, P1.scores(cands, lm, 0.5, "<s>"), P1.succ_words(lm, "<s>"))
        self.assertEqual(base[0][0][0][0], "蛋")
        self.assertEqual(DS.digit_tiers(sc.tb, lm, sc.idx, units, "<s>", 0.5)[0][0][0][0], "點")

    def test_rank_before_any_key(self):
        lex, lm, sc = world()
        self.assertEqual(DS.digit_rank_empty(sc.tb, lm, lex, "下午", "點"), 1)    # P(點|下午,D) is the largest of the three
        self.assertEqual(DS.digit_rank_empty(sc.tb, lm, lex, "下午", "年"), 3)
        self.assertIsNone(DS.digit_rank_empty(sc.tb, lm, lex, "下午", "蛋"))       # not a successor of a digit run
        self.assertIsNone(DS.digit_rank_empty(sc.tb, lm, lex, "下午", "沒有"))

    def test_digit_row_both_cases(self):
        """digits.md A.3.5: with units the two tiers in order, without units the successor layer; `digit_rank_empty` agrees with the position."""
        lex, lm, sc = world(lm=StubLM(succ=["蛋"]))
        units = P1.units_of(["ㄉ"])
        t0, t1 = DS.digit_tiers(sc.tb, lm, sc.idx, units, "下午", 0.5)
        row = DS.digit_row(sc.tb, lm, lex, sc.idx, "下午", units, 0.5)
        self.assertEqual([w for w, _ in row], [e[0] for e, _ in t0 + t1])
        self.assertEqual([s for _, s in row], [s for _, s in t0 + t1])
        self.assertEqual(len(DS.digit_row(sc.tb, lm, lex, sc.idx, "下午", units, 0.5, limit=2)), 2)
        empty = DS.digit_row(sc.tb, lm, lex, sc.idx, "下午", [], 0.5)
        words = [w for w, _ in empty]
        self.assertEqual(set(words), {"點", "年", "元"})                      # the successor layer, all in the lexicon
        self.assertEqual(words[0], "點")
        for pos, w in enumerate(words):
            self.assertLessEqual(pos + 1, DS.digit_rank_empty(sc.tb, lm, lex, "下午", w))   # the pessimistic rank counts equal scores ahead
        self.assertEqual([DS.digit_rank_empty(sc.tb, lm, lex, "下午", w) for w in words], [1, 3, 3])      # 年 and 元 tie (W 10 each)
        scores = [s for _, s in empty]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertAlmostEqual(scores[0], math.log10(sc.tb.p_w_given_vD("點", "下午", 10 ** lex.by_word["點"][1], 1.0)))

    def test_pred_row(self):
        lex, lm, sc = world(lm=StubLM(succ=["蛋"]))
        env = type("E", (), {"history": staticmethod(identity_history), "lex": lex, "idx": sc.idx, "table": sc.tb, "lm": lm})()
        lams = (("chat", 0.5), ("formal", 0.3))
        r = M.pred_row(env, ("下午", "3", "點", ["ㄉㄧㄢˇ"]), lams)
        self.assertEqual((r["n"], r["i_hit"], r["i_in_layer"]), (1, 1, 1))                  # (i) once per row, no profile
        self.assertEqual((r["chat.new"], r["chat.base"], r["formal.new"], r["formal.base"]), (1, 1, 1, 1))
        self.assertEqual(M.pred_row(env, ("下午", "3", "沒有", ["ㄇㄟˊ", "ㄧㄡˇ"]), lams), {"dropped": 1})   # no lexicon entry with that reading

    def test_pred_row_scores_formal_at_the_cores_kn_beta(self):
        seen = []

        class Spy(StubLM):
            def word(self, lam, v, w, lp):
                seen.append((lam, self.kn[0]))
                return lp

        lex, _, sc = world(lm=Spy(succ=["蛋"]))
        lm = Spy(succ=["蛋"])
        lm.kn = (1.0, [1] * 4, 10)                                                 # the side file's beta (chat keeps it)
        env = type("E", (), {"history": staticmethod(identity_history), "lex": lex, "idx": sc.idx, "table": sc.tb, "lm": lm})()
        M.pred_row(env, ("下午", "3", "點", ["ㄉㄧㄢˇ"]), (("chat", 0.5), ("formal", 0.3)))
        self.assertEqual({lam: b for lam, b in seen if lam in (0.5, 0.3)}, {0.5: 1.0, 0.3: 0.75})
        self.assertEqual(lm.kn[0], 1.0)                                       # the shared model is not changed


class Verdicts(unittest.TestCase):
    def test_choose_theta(self):
        self.assertEqual(M.choose_theta({0.0: 3, 0.5: 0, 1.0: 0, 2.0: 0}), 0.5)
        self.assertEqual(M.choose_theta({0.0: 0, 0.5: 0}), 0.0)
        self.assertIsNone(M.choose_theta({0.0: 3, 0.5: 2, 1.0: 1, 2.0: 1}))
        self.assertEqual(M.choose_theta({2.0: 0, 1.0: 4, 0.5: 1, 0.0: 9}), 2.0)    # the smallest theta with zero, whatever the dict order

    def test_r1(self):
        self.assertEqual(M.r1_verdict({"chat": (10, 12), "formal": (9, 9)})[0], "digit-context")        # more wrong now, but not significant
        self.assertEqual(M.r1_verdict({"chat": (0, 12), "formal": (9, 9)})[0], "<s>")                   # p = 2/4096 < 0.05, wrong now > right now
        self.assertEqual(M.r1_verdict({"chat": (12, 0), "formal": (30, 2)})[0], "digit-context")        # significantly better is fine
        self.assertEqual(M.r1_verdict({"chat": (20, 20), "formal": (1, 9)})[0], "<s>")                   # one setting is enough
        self.assertEqual(M.r1_verdict({"chat": (3, 8), "formal": (3, 8)})[1]["chat"], P1.mcnemar(3, 8))

    def test_r2(self):
        self.assertEqual(M.r2_verdict(0.5, 90, 100, 0, 0, 0, 85), ("score", False))
        self.assertEqual(M.r2_verdict(0.5, 85, 100, 0, 0, 0, 85), ("score", False))        # as right as the structural rule
        self.assertEqual(M.r2_verdict(0.5, 84, 100, 0, 0, 0, 85), ("structural", False))   # (a) below the structural rule
        self.assertEqual(M.r2_verdict(0.5, 95, 100, 1, 0, 0, 85), ("structural", False))   # dev302 (c) > 0
        self.assertEqual(M.r2_verdict(0.5, 95, 100, 0, 1, 0, 85), ("structural", False))   # typing76 (c) > 0
        self.assertEqual(M.r2_verdict(None, 99, 100, 0, 0, 0, 85), ("structural", False))  # no theta passed the tuning keys
        self.assertTrue(M.r2_verdict(0.5, 95, 100, 0, 0, 2, 85)[1])                         # the structural rule itself misjudges: stop
        self.assertEqual(M.r2_verdict(0.0, 0, 0, 0, 0, 0, 0), ("structural", False))       # nothing measured

    def test_merge_adds_counters_and_numbers(self):
        a = {"x": 1, "h": collections.Counter({1: 2})}
        M.merge(a, {"x": 4, "h": collections.Counter({1: 1, 2: 5}), "y": 3})
        self.assertEqual(a, {"x": 5, "h": collections.Counter({1: 3, 2: 5}), "y": 3})

    def test_quantile_and_percent(self):
        self.assertEqual(M.q(collections.Counter({1: 90, 5: 10}), 0.95), 5)
        self.assertEqual(M.q(collections.Counter({1: 90, 5: 10}), 0.5), 1)
        self.assertEqual(M.pct(1, 4), "25.0%")


class DigitRunSet(unittest.TestCase):
    def test_digit_cuts(self):
        lex = mklex([("ㄒㄧㄚˋ-ㄨˇ", "下午", -3), ("ㄉㄧㄢˇ", "點", -2), ("ㄉㄧㄢˇ-ㄒㄧㄣ", "點心", -3), ("ㄩㄢˊ", "元", -2), ("ㄐㄧㄚˋ-ㄍㄜˊ", "價格", -3)])
        self.assertEqual(M.digit_cuts("下午３點心，價格300元，12", lex), [("下午", "3", "點心"), ("價格", "300", "元")])    # full-width folded; 12 has no word after it
        self.assertEqual(M.digit_cuts("5元", lex), [("", "5", "元")])                                                  # empty context
        self.assertEqual(M.digit_cuts("A5元", lex), [("", "5", "元")])                                                 # English before it is no context
        self.assertEqual(M.digit_cuts("3嗯嗯嗯", lex), [])                                                            # an unsegmentable word does not make a row
        self.assertEqual(M.digit_cuts("3，元", lex), [])                                                              # a comma between: not adjacent
        self.assertEqual(M.digit_cuts("一二三四五六七八九十下午3點", lex)[0][0], "三四五六七八九十下午"[-M.CTX_MAX:])        # context capped at CTX_MAX

    def test_pick_one_per_sentence_and_cap_and_determinism(self):
        cands = [(s, (str(s), "1", str(i))) for s in range(50) for i in range(3)] + [(0, ("0", "1", "0"))]
        rows = M.pick(cands, 7, 1000)
        self.assertEqual(len(rows), 50)                                    # one per sentence, the duplicate row merged
        self.assertEqual(rows, M.pick(cands, 7, 1000))
        self.assertNotEqual(rows, M.pick(cands, 8, 1000))
        capped = M.pick(cands, 7, 20)
        self.assertEqual(len(capped), 20)
        self.assertTrue(set(capped) <= set(rows))
        self.assertEqual(capped, [r for r in rows if r in set(capped)])    # the input order is kept

    def test_halves_are_disjoint_deterministic_and_cover_everything(self):
        rows = [(str(i), "1", "w") for i in range(101)]
        t, j = M.split_halves(rows, 5)
        self.assertEqual((len(t), len(j)), (50, 51))
        self.assertEqual(sorted(t + j), sorted(rows))
        self.assertEqual((t, j), M.split_halves(rows, 5))
        self.assertNotEqual(t, M.split_halves(rows, 6)[0])

    def test_make_rows_drops_words_without_a_reading(self):
        cands = [(i, ("", str(i), "點" if i % 2 else "沒")) for i in range(10)]
        kept, (tune, judge), counts = M.make_rows(cands, 3, 100, lambda ws: {"點": "ㄉㄧㄢˇ"})
        self.assertEqual(len(kept), 5)
        self.assertEqual(counts, {"picked": 10, "with_reading": 5, "tuning": 2, "judging": 3})
        self.assertTrue(all(r[3] == "ㄉㄧㄢˇ" for r in kept))

    def test_row_loading(self):
        lex = mklex()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "zh.txt")
            open(p, "w", encoding="utf-8").write("|年|ㄋㄧㄢˊ\n下午|點|\n|年代|ㄋㄧㄢˊ\nbad line\n#comment\n|年|\n|年|ㄋㄧㄢˊx\n")
            rows, dropped = M.load_zh_rows(p, lex)
            self.assertEqual(rows, [("", "年", ["ㄋㄧㄢˊ"]), ("下午", "點", ["ㄉㄧㄢˇ"]), ("", "年", ["ㄋㄧㄢˊ"])])   # blank readings come from the lexicon
            self.assertEqual(dropped, 3)                                                       # 年代 with one syllable; a line without three fields; a reading with a latin letter
            q = os.path.join(d, "digit.txt")
            open(q, "w", encoding="utf-8").write("下午|3|點|ㄉㄧㄢˇ\n|2026|年|ㄋㄧㄢˊ\n")
            self.assertEqual(M.load_digit_rows(q), [("下午", "3", "點", ["ㄉㄧㄢˇ"]), ("", "2026", "年", ["ㄋㄧㄢˊ"])])


class Golden(unittest.TestCase):
    def test_thirty_one_rows_with_a_fifth_key_flush_and_a_split_forced_by_a_non_alphabet_key(self):
        _, _, sc = world()
        text = GD.generate(sc, identity_history, GD.ROWS, ["header sha256=abc", "second line"])
        lines = text.split("\n")
        self.assertEqual(lines[:2], ["# header sha256=abc", "# second line"])
        self.assertEqual(sum(l.startswith("## ") for l in lines), 31)
        keyl = [l.split("\t") for l in lines if l and l[0].isdigit()]
        self.assertTrue(any(f[2] == "flush=1" and f[1] != GD.EN for f in keyl))            # the fifth key pushed the oldest out
        forced = [f for f in keyl if f[1] in "umj,ok" and f[5] != "splits=" and f[6] != "choice=-"]
        self.assertTrue(forced)                                                           # a non-alphabet key made a split
        self.assertTrue(all(f[0].isdigit() and f[7].startswith("cls=") for f in keyl))
        self.assertEqual(text, GD.generate(sc, identity_history, GD.ROWS, ["header sha256=abc", "second line"]))   # deterministic

    def test_strict_refuses_a_syllable_without_its_character_in_the_middle_of_a_row(self):
        _, _, sc = world()
        GD.generate(sc, identity_history, [("", "84", "")], ["h"], strict=True)                      # a syllable at the very end is read by nobody
        with self.assertRaises(SystemExit):
            GD.generate(sc, identity_history, [("", "841", "")], ["h"], strict=True)                 # ㄚˋ, then a new run: its context needs the character
        GD.generate(sc, identity_history, [("", "841", "啊")], ["h"], strict=True)
        self.assertIn(("", "1258901258", ""), GD.ROWS)                                               # the long digit string finishes no syllable
        self.assertNotIn("123456789", [k for _, k, _ in GD.ROWS])

    def test_scores_are_python_reprs(self):
        _, _, sc = world()
        text = GD.generate(sc, identity_history, [("", "84", "")], ["h"])
        row = [l for l in text.split("\n") if l.startswith("0\t8")][0]
        splits = row.split("\t")[5][len("splits="):].split(";")
        self.assertEqual([s.split(",")[:2] for s in splits], [["1", "0"], ["0", "1"]])
        self.assertEqual(float(splits[1].split(",", 2)[2]), math.log10(10 ** -2.0 + 10 ** -2.5))


class PredGolden(unittest.TestCase):
    def test_twelve_rows_with_a_header_and_the_row_of_digit_row(self):
        lex, lm, sc = world(lm=StubLM(succ=["蛋"]))
        env = type("E", (), {"history": staticmethod(identity_history), "lex": lex, "idx": sc.idx, "table": sc.tb, "lm": lm})()
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False, encoding="utf-8") as f:
            f.write(TABLE_TEXT)
        self.addCleanup(os.unlink, f.name)
        text = GD.generate_pred(env, f.name)
        lines = text.split("\n")
        self.assertEqual(sum(l.startswith("## ") for l in lines), 12)
        self.assertEqual(text, GD.generate_pred(env, f.name))                                 # deterministic
        self.assertTrue(lines[1].startswith("# digits.tsv sha256=") and "bigram.sjlm sha256=" in lines[2])
        block = lines[lines.index([l for l in lines if l.startswith("## 6\t")][0]) + 1:]
        first = block[0].split("\t")                                                          # 下午, no symbols: the successor layer
        self.assertEqual((first[0], first[1]), ("1", "點"))
        want = DS.digit_row(sc.tb, lm, lex, sc.idx, "下午", [], DS.L.PROFILES["chat"])
        self.assertEqual([float(l.split("\t")[2]) for l in block[:len(want)]], [s for _, s in want])
        with_tail = [l for l in lines if l.startswith("## 5\t")][0]
        self.assertIn("syms=ㄩ", with_tail)


class FakeRunner:
    """Stands in for measure.Runner: records the calls, answers with canned counts."""
    calls = []
    struct_c = 0
    c_below = 2.0   # theta below this has (c) = 3

    def __init__(self, table_path, procs):
        pass

    def run(self, kind, rows, theta=0.0, mode="score", chunk=40):
        FakeRunner.calls.append((kind, theta, mode, len(rows)))
        if kind == "pred":
            r = {"n": 10, "i_hit": 5, "i_in_layer": 9}
            for prof, _ in M.LAMBDAS:
                r.update({f"{prof}.base": 4, f"{prof}.new": 8, f"{prof}.new_only": 4, f"{prof}.base_only": 0})
            return r
        if kind == "digit":
            return {"rows": 10, "a_ok": 9, "flips": collections.Counter({0: 10}), "nsplit": collections.Counter({2: 5}), "nscan": collections.Counter({1: 5})}
        c = FakeRunner.struct_c if mode == "structural" else (3 if theta < FakeRunner.c_below else 0)
        return {"rows": len(rows), "syl": 100, "keys": 400, "c": c, "c_rows": int(bool(c)), "d": 1}


class Commands(unittest.TestCase):
    """measure.judge / tune / build_set end to end on tiny files with a fake Runner (no model, no processes)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.tmp.name
        self.files = {}
        for name, text in (("table", TABLE_TEXT), ("digits", "下午|3|點|ㄉㄧㄢˇ\n|2026|年|ㄋㄧㄢˊ\n"), ("zh", "|年|ㄋㄧㄢˊ\n|點|ㄉㄧㄢˇ\n")):
            self.files[name] = os.path.join(d, name + ".txt")
            open(self.files[name], "w", encoding="utf-8").write(text)
        FakeRunner.calls, FakeRunner.struct_c, FakeRunner.c_below, self.n = [], 0, 2.0, 0
        for patch in (mock.patch.object(M, "Runner", FakeRunner), mock.patch.object(M, "load_lexicon", mklex)):
            patch.start()
            self.addCleanup(patch.stop)

    def run_cmd(self, name, **kw):
        f = self.files
        self.n += 1
        a = argparse.Namespace(table=f["table"], procs=1, out=os.path.join(self.tmp.name, f"{name}-report{self.n}.txt"), **kw)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                getattr(M, name)(a)
                code = None
            except SystemExit as e:
                code = e.code
        return out.getvalue(), code

    def judge(self, theta):
        f = self.files
        return self.run_cmd("judge", theta=theta, digit_judge=f["digits"], dev302=f["zh"], typing76=f["zh"])

    def test_judge_with_theta_none_runs_only_R1_and_the_structural_c(self):
        text, code = self.judge("none")
        self.assertIsNone(code)
        self.assertEqual(sorted(set((k, m) for k, _, m, _ in FakeRunner.calls)), [("pred", "score"), ("zh", "structural")])   # no digit rows, no score mode
        self.assertEqual(sum(1 for k, *_ in FakeRunner.calls if k == "pred"), 1)               # (i)(ii) both profiles from one pass
        self.assertIn("R1: the prediction row uses digit-context", text)
        self.assertIn("R2: A uses the structural design", text)
        self.assertNotIn("(a)", text)
        self.assertNotIn("STOP", text)

    def test_judge_with_a_theta_reports_both_rules_and_stops_on_a_structural_c(self):
        text, code = self.judge("3")
        self.assertIsNone(code)
        self.assertIn("score  digit-judge", text)
        self.assertIn("structural  digit-judge", text)
        self.assertIn("R2: A uses the score design", text)
        FakeRunner.struct_c = 2
        text, code = self.judge("none")
        self.assertEqual(code, 1)
        self.assertIn("STOP", text)

    def test_tune_prints_stop_and_exits_nonzero_on_a_structural_c(self):
        f = self.files
        kw = dict(digit_tune=f["digits"], cvtune=f["zh"], wikitune=f["zh"])
        text, code = self.run_cmd("tune", **kw)
        self.assertIsNone(code)
        self.assertIn("R2 theta: 2.0", text)                                   # the canned (c) is 3 below theta 2, 0 from 2 on
        self.assertEqual(sum(1 for k, th, m, _ in FakeRunner.calls if k == "digit" and th == 0.0 and m == "score"), 1)   # the timing line reuses theta 0's pass
        self.assertIn("time (theta 0.0", text)
        FakeRunner.calls, FakeRunner.struct_c = [], 1
        text, code = self.run_cmd("tune", **kw)
        self.assertEqual(code, 1)
        self.assertIn("STOP", text)
        self.assertNotIn("R2 theta", text)

    def test_tune_message_lists_the_theta_grid_from_THETAS(self):
        self.assertEqual(M.THETA_SET, "{0, 0.5, 1, 2, 3, 4, 6, 8}")
        FakeRunner.c_below = 100.0
        with mock.patch.object(M, "THETAS", (9.0, 12.0)), mock.patch.object(M, "THETA_SET", "{9, 12}"):
            text, code = self.run_cmd("tune", digit_tune=self.files["digits"], cvtune=self.files["zh"], wikitune=self.files["zh"])
        self.assertIsNone(code)
        self.assertIn("R2 theta: None  (no theta in {9, 12}", text)

    def build_set(self, articles, lo, hi, cap=1500):
        import build_counts as bc
        out_dir = os.path.join(self.tmp.name, "set")
        args = argparse.Namespace(out_dir=out_dir, src=self.tmp.name, seed=M.SEED, cap=cap, range=[lo, hi], single="judge2.txt")
        raws = [f"下午{n}點" for n in articles]
        out = io.StringIO()
        with mock.patch.object(bc, "load_conv", lambda: ({}, {}, 1)), mock.patch.object(bc, "articles", lambda n: iter(raws[:n])), \
                mock.patch.object(M, "readings_by_word", lambda ws, d: {w: "ㄉㄧㄢˇ" for w in ws}), contextlib.redirect_stdout(out):
            try:
                M.build_set(args)
                code = None
            except SystemExit as e:
                code = str(e.code)
        return out_dir, code

    def test_build_set_range_cuts_articles_and_writes_one_file(self):
        out_dir, code = self.build_set(range(1000, 1700), 100, 650)
        self.assertIsNone(code)
        rows = open(os.path.join(out_dir, "judge2.txt"), encoding="utf-8").read().splitlines()
        self.assertEqual(len(rows), 550)                                         # articles 100..649, one row each
        self.assertEqual(sorted(int(r.split("|")[1]) for r in rows), list(range(1100, 1650)))   # nothing before LO, nothing from HI on
        self.assertEqual(sorted(os.listdir(out_dir)), ["judge2.manifest.json"] + ["judge2.txt"])
        man = json.load(open(os.path.join(out_dir, "judge2.manifest.json"), encoding="utf-8"))
        self.assertEqual((man["articles"], man["candidates"]["cv"], man["counts"]["judging"] + man["counts"]["tuning"], man["seed"]), ([100, 650], 0, 550, 20261017))
        out_dir2, _ = self.build_set(range(1000, 1700), 100, 650)                # deterministic
        self.assertEqual(open(os.path.join(out_dir2, "judge2.txt"), "rb").read(), open(os.path.join(out_dir, "judge2.txt"), "rb").read())

    def test_build_set_range_caps_and_stops_below_the_minimum(self):
        out_dir, code = self.build_set(range(1000, 2400), 0, 1400, cap=520)
        self.assertIsNone(code)
        self.assertEqual(len(open(os.path.join(out_dir, "judge2.txt"), encoding="utf-8").read().splitlines()), 520)
        out_dir, code = self.build_set(range(1000, 1700), 0, 499)                # 499 rows: one below SINGLE_MIN
        self.assertIn("STOP: 499 digit-run rows, fewer than 500", code)
        self.assertTrue(os.path.exists(os.path.join(out_dir, "judge2.txt")))     # still written, as before


if __name__ == "__main__":
    unittest.main(verbosity=2)
