"""P1a unit tests (docs/contracts/p1a-confusion.md section 3 item 6). No corpus, no network; the few tests that read data/lm (git-ignored)
skip when it is missing.
Run: /opt/homebrew/bin/python3 -W ignore -m unittest experiments/p1a-confusion/test_p1a.py -v   (from the repo root; needs numpy)
"""
import collections
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p1a  # noqa: E402
from p1a import bc, bct  # noqa: E402
import fuse  # noqa: E402
import prep  # noqa: E402
import train  # noqa: E402

LEXICON = """\
ㄗㄞˋ 在 -2.4
ㄗㄞˋ 再 -3.3
ㄗㄞˇ 載 -3.0
ㄗㄞˋ 載 -3.5
ㄗㄞˋ-ㄎㄜˋ 載客 -4.0
ㄐㄧˋ-ㄗㄞˇ 記載 -3.5
ㄐㄧˋ 記 -3.0
ㄎㄜˋ 客 -3.0
ㄐㄧㄡˋ 就 -2.0
ㄐㄧㄡˋ-ㄗㄞˋ 就在 -3.0
ㄅㄨˊ-ㄧㄠˋ-ㄗㄞˋ 不要再 -4.0
ㄅㄨˊ 不 -2.0
ㄧㄠˋ 要 -2.0
ㄊㄚ 他 -2.0
ㄐㄧㄚ 家 -2.0
ㄋㄧˇ 你 -2.0
ㄐㄧㄣ 今 -3.0
ㄊㄧㄢ 天 -3.0
ㄑㄧˋ 氣 -3.0
ㄏㄣˇ 很 -2.0
ㄏㄠˇ 好 -2.0
ㄗㄨㄛˋ 做 -3.0
ㄗㄨㄛˋ 作 -2.5
ㄕˋ 是 -2.0
ㄕˋ 事 -3.0
"""


def tiny_lexicon(tmp):
    p = os.path.join(tmp, "lex.txt")
    open(p, "w", encoding="utf-8").write(LEXICON)
    return p1a.ime.Lexicon(p)


def stub_model(reading="ㄗㄞˋ", probs=(0.5, 0.3, 0.2), other_counts=None, other_total=100, v_other=20):
    """W = 0, so every feature vector gives exactly softmax(b) = probs."""
    meta = {"reading": reading, "classes": ["在", "再"], "majority": "在", "other_counts": other_counts or {"載": 10, "坐": 2},
            "other_total": other_total, "v_other": v_other, "n_keep": 0}
    return p1a.Model(meta, np.zeros(0, np.int32), np.zeros((0, 3)), np.log(np.array(probs)))


class TmpCase(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.tmp = t.name
        self.lex = tiny_lexicon(self.tmp)


class DataPrep(TmpCase):
    def test_wiki_runs_equal_count_batch(self):
        texts = ["{{tpl}}今天[[天氣|天氣]]很好。他就在&lt;ref&gt;註&lt;/ref&gt;家！好\n記載&amp;你，不要再",
                 "'''他'''的［］ABC 載客；今天很好 x", "<!-- c -->你今"]
        rec, orig = [], bc.segment

        def spy(lex, run):
            ws = orig(lex, run)
            if ws:
                rec.append(run)
            return ws
        conv = ({}, {}, 1)
        with mock.patch.dict(bc._W, {"mw": None, "trigram": False, "expected": False, "lex": self.lex, "conv": conv}), \
                mock.patch.object(bc, "segment", spy):
            bc.count_batch(texts)
        mine = list(p1a.wiki_runs(self.lex, texts, conv))
        self.assertGreaterEqual(len(rec), 4)
        self.assertEqual(rec, mine)

    def test_colloquial_runs_equal_build_counts_text_and_lexicon(self):
        for name, body in (("a.txt", "今天天氣很好\n好\n他就在家，你\n"), ("b.tsv", "1\t他今天很好\n2\t載客不要再\n")):
            open(os.path.join(self.tmp, name), "w", encoding="utf-8").write(body)
        paths = [os.path.join(self.tmp, n) for n in ("a.txt", "b.tsv")]
        rec, orig = [], bc.segment

        def spy(lex, run):
            ws = orig(lex, run)
            if ws:
                rec.append(run)
            return ws
        with mock.patch.dict(bc._W), mock.patch.object(bc, "load_conv", lambda: ({}, {}, 1)), mock.patch.object(bc, "segment", spy), \
                redirect_stdout(io.StringIO()):   # patch.dict restores the module-global _W that bct.main overwrites
            bct.main([os.path.join(self.tmp, "o.pkl"), *paths])
            lex = bc._W["lex"]
        mine = list(p1a.colloquial_runs(lex, paths, ({}, {}, 1)))
        self.assertGreaterEqual(len(rec), 3)
        self.assertEqual(rec, mine)
        self.assertEqual(p1a.training_lexicon().by_word, lex.by_word)   # the lexicon the counts segment with


class Positions(TmpCase):
    def test_reading_of_a_position_comes_from_its_word(self):
        # 載客: 載 is ㄗㄞˋ; 記載: 載 is ㄗㄞˇ; the single word 載 (best score) is ㄗㄞˇ too
        self.assertEqual(p1a.target_positions(self.lex, ["載客"]), [(0, "載", "ㄗㄞˋ"), (1, "客", "ㄎㄜˋ")])
        self.assertEqual(p1a.target_positions(self.lex, ["記載"])[1], (1, "載", "ㄗㄞˇ"))
        self.assertEqual(p1a.target_positions(self.lex, ["他", "載"])[1], (1, "載", "ㄗㄞˇ"))

    def test_extract_keeps_only_positions_with_the_target_reading(self):
        tables = {"ㄗㄞˋ": {"在": 0, "再": 1}}
        with mock.patch.dict(train._G, {"lex": self.lex, "cls_of": lambda w: -1, "tables": tables}):
            labels, chars, ids = train._extract((["載客", "記載", "他就在家", "不要再"], "w", set()))["ㄗㄞˋ"]
        self.assertEqual([chr(c) for c in chars], ["載", "在", "再"])   # 載客's 載 counts; 記載's does not; 就在's 在 and 不要再's 再 count
        self.assertEqual(labels, [2, 0, 1])                               # 載 is not in the table: OTHER (index 2)
        self.assertEqual(np.array(ids).shape, (3, p1a.NF))

    def test_class_counts_use_the_reading_rule(self):
        with mock.patch.dict(prep._G, {"lex": self.lex}):
            c = prep._count(["載客", "記載", "他就在家"])
        self.assertEqual(c[("ㄗㄞˋ", "載")], 1)
        self.assertNotIn(("ㄗㄞˇ", "載"), c)   # not a target reading

    def test_class_table(self):
        counts = {"在": 5000, "再": 1000, "載": 999, "坐": 1000, "a": 7000, "b": 2000, "c": 2000, "d": 1500, "e": 1100}
        self.assertEqual(p1a.class_table(counts), ["a", "在", "b", "c", "d", "e"])   # >= 1000, 6 at most, most frequent first, ties by code point

    def test_quota_keeps_the_first_samples_in_order(self):
        q = p1a.Quota([("r", 0, "w"), ("r", 0, "c")], cap=2)
        self.assertEqual([q.accept(("r", 0, "w")) for _ in range(3)], [True, True, False])
        self.assertTrue(q.done("w")); self.assertFalse(q.done("c")); self.assertEqual(q.full(), {("r", 0, "w")})


class Features(TmpCase):
    cls_of = staticmethod({"就": 5, "家": 7, "他": 3}.get)

    def feats(self, text, pos, ctx=""):
        return p1a.sample_features(self.lex, lambda w: self.cls_of(w, -1), text, pos, ctx)

    def test_masked_position_features_do_not_see_the_character(self):
        # 就在 is one word, 就再 is not: the sentence segments differently, the features must not
        a, b = self.feats("他就在家", 2), self.feats("他就再家", 2)
        self.assertEqual(p1a.bc.segment(self.lex, "他就在家"), ["他", "就在", "家"])
        self.assertEqual(p1a.bc.segment(self.lex, "他就再家"), ["他", "就", "再", "家"])
        self.assertEqual(a.tolist(), b.tolist())
        self.assertNotEqual(a.tolist(), self.feats("你就在家", 2).tolist())   # but the surroundings do count
        self.assertNotEqual(a.tolist(), self.feats("他就在你", 2).tolist())

    def test_feature_list(self):
        f = p1a.mask_features(self.lex, lambda w: self.cls_of(w, -1), "他就", "家")
        self.assertEqual(dict(x.split("=") for x in f), {"bos": "0", "eos": "0", "l1": "就", "l2": "他", "l12": "他就", "r1": "家", "r2": "^", "r12": "家^",
                                                         "l1r1": "就|家", "lw": "就", "rw": "家", "lc": "5", "rc": "7", "lcrc": "5|7"})
        e = dict(x.split("=") for x in p1a.mask_features(self.lex, lambda w: -1, "", ""))
        self.assertEqual((e["bos"], e["eos"], e["l1"], e["lw"], e["rw"]), ("1", "1", "^", "^", "^"))

    def test_hash_is_stable_and_bounded(self):
        self.assertEqual(p1a.hash_ids(["bos=1"]).tolist(), [zlib_crc("bos=1") & ((1 << 20) - 1)])
        self.assertTrue(0 <= p1a.hash_ids(["x"] * 3).max() < 1 << 20)

    def test_context_goes_in_front_of_the_left_part(self):
        calls = []
        spy = lambda left, right: calls.append((left, right)) or np.zeros(1, np.int32)   # noqa: E731
        m = stub_model()
        p1a.candidate_f(m, spy, "了我", ["就", "在"], ["ㄐㄧㄡˋ", "ㄗㄞˋ"])
        self.assertEqual(calls, [("了我就", "")])
        calls.clear()
        p1a.candidate_f(m, spy, "", ["就", "在"], ["ㄐㄧㄡˋ", "ㄗㄞˋ"])   # a sentinel context is the empty string
        self.assertEqual(calls, [("就", "")])
        # the left part is not empty with a context, and empty without (bos feature)
        self.assertEqual(p1a.mask_features(self.lex, lambda w: -1, "了我", "家")[0], "bos=0")
        self.assertEqual(p1a.mask_features(self.lex, lambda w: -1, "", "家")[0], "bos=1")
        # sample_features takes the context the same way
        self.assertNotEqual(self.feats("在家", 0).tolist(), self.feats("在家", 0, "了我").tolist())


def zlib_crc(s):
    import zlib
    return zlib.crc32(s.encode("utf-8"))


class Scoring(unittest.TestCase):
    P = (0.5, 0.3, 0.2)

    def test_multi_character_word_positions_are_scored(self):
        syls = ["ㄅㄨˊ", "ㄧㄠˋ", "ㄗㄞˋ"]
        self.assertEqual(p1a.scored_positions(["不要再"], syls, "ㄗㄞˋ"), [2])
        self.assertEqual(p1a.scored_positions(["不要", "再"], syls, "ㄗㄞˋ"), [2])
        calls = []
        spy = lambda left, right: calls.append((left, right)) or np.zeros(1, np.int32)   # noqa: E731
        f = p1a.candidate_f(stub_model(), spy, "", ["不要再"], syls)
        self.assertEqual(calls, [("不要", "")])
        self.assertAlmostEqual(f, math.log10(0.3) - math.log10(0.5), places=9)

    def test_term_values_by_hand(self):
        m = stub_model()
        p = np.array(self.P)
        self.assertAlmostEqual(p1a.char_term(m, p, "在"), 0.0, places=9)
        self.assertAlmostEqual(p1a.char_term(m, p, "再"), math.log10(0.3 / 0.5), places=9)
        # outside the table: P(OTHER) * q(c | OTHER), q = (n + 0.5) / (N + 0.5 V) = (n + 0.5) / 110
        self.assertAlmostEqual(p1a.char_term(m, p, "載"), math.log10(0.2 * 10.5 / 110) - math.log10(0.5), places=9)
        self.assertAlmostEqual(p1a.char_term(m, p, "坐"), math.log10(0.2 * 2.5 / 110) - math.log10(0.5), places=9)
        self.assertAlmostEqual(p1a.char_term(m, p, "鬻"), math.log10(0.2 * 0.5 / 110) - math.log10(0.5), places=9)   # never seen in OTHER
        # same features, two characters with different q: different terms, and none is 0 or the bare log P(OTHER)
        a, b = p1a.char_term(m, p, "載"), p1a.char_term(m, p, "坐")
        self.assertNotAlmostEqual(a, b, places=3)
        self.assertLess(max(a, b), math.log10(0.2) - math.log10(0.5) - 1e-3)
        self.assertLess(a, 0)

    def test_f_sums_every_scored_position(self):
        m = stub_model()
        spy = lambda left, right: np.zeros(1, np.int32)   # noqa: E731
        f = p1a.candidate_f(m, spy, "", ["在", "再", "載"], ["ㄗㄞˋ", "ㄗㄞˋ", "ㄗㄞˋ"])
        self.assertAlmostEqual(f, 0 + math.log10(0.6) + math.log10(0.2 * 10.5 / 110) - math.log10(0.5), places=9)
        self.assertEqual(p1a.candidate_f(m, spy, "", ["在"], ["ㄕˋ"]), 0.0)   # other readings are not this classifier's business

    def test_predicted_character(self):
        self.assertEqual(stub_model().predict_char(np.array(self.P)), "在")
        self.assertEqual(stub_model().predict_char(np.array([0.02, 0.02, 0.96])), "載")   # OTHER with its most common character


def cand(s, w, **f):
    return {"s": s, "w": list(w), "f": {k: [v, v] for k, v in f.items()}}


def row(gold, cands, gp=None, syls=None):
    return {"gold": gold, "syls": syls or ["x"] * len(gold), "cands": cands, "gp": gp or {}}


class Fusion(unittest.TestCase):
    def test_gate_and_rerank(self):
        r = row("B", [cand(-10.0, "A", r=-1.0), cand(-10.5, "B", r=0.0)])
        self.assertEqual(p1a.pick(r, ["r"], 1, 1), 1)         # gap 0.5 < 1: rerank, -10.5 > -11
        self.assertEqual(p1a.pick(r, ["r"], 1, 0.5), 0)       # gap 0.5 is not < 0.5
        self.assertEqual(p1a.pick(r, ["r"], 1, math.inf), 1)  # no threshold
        self.assertEqual(p1a.pick(r, ["r"], 0.5, 1), 0)       # -10.5 vs -10.5: the tie keeps the earlier candidate
        self.assertEqual(p1a.pick(r, ["r"], 0, math.inf), 0)  # mu = 0 is the n-gram order
        self.assertEqual(p1a.pick(r, [], 3, math.inf), 0)     # no active reading
        self.assertEqual(p1a.pick(r, ["other"], 3, math.inf), 0)   # f of a reading that is not active is ignored


    def sets(self, gap, advantage, n=3):
        rows = [row("B", [cand(-10.0, "A", r=-advantage), cand(-10.0 - gap, "B", r=0.0)]) for _ in range(n)]
        return rows

    def test_selection_ties_and_tuning_sets_only(self):
        # gap 0.5 and f advantage 1: needs mu > 0.5 and tau > 0.5 -> (0.7, 1) is the smallest tau, then the smallest mu
        sets = {"cvtune": self.sets(0.5, 1), "wikitune": self.sets(0.5, 1), "dev302": []}
        self.assertEqual(p1a.select(sets, ["r"]), (0.7, 1, 6))
        # gap 4 and advantage 10: needs tau > 4 and mu > 0.4; tau 5 and "no threshold" tie -> 5; the smallest mu is 0.5
        sets = {"cvtune": self.sets(4, 10), "wikitune": self.sets(4, 10)}
        self.assertEqual(p1a.select(sets, ["r"]), (0.5, 5, 6))
        # gap 6: only "no threshold" works
        sets = {"cvtune": self.sets(6, 10), "wikitune": []}
        self.assertEqual(p1a.select(sets, ["r"]), (0.7, math.inf, 3))
        # a set outside the tuning sets, big enough to change the answer if it were read, changes nothing
        base = {"cvtune": self.sets(0.5, 1), "wikitune": self.sets(0.5, 1)}
        extra = dict(base, dev302=self.sets(0.2, 0.3, n=50), discordtune=self.sets(0.2, 0.3, n=50))
        self.assertEqual(p1a.select(extra, ["r"]), p1a.select(base, ["r"]))

    def test_pooled_disable_rule(self):
        def sets(cv, wk):   # (classifier correct, majority correct) out of 10 positions per set
            def rows(c, m):
                pairs = []
                for i in range(10):
                    g = "在" if i < m else "再"   # the majority character 在 is right exactly when the gold is 在
                    pairs.append([g, g if i < c else ("再" if g == "在" else "在")])
                return [row("在", [], gp={"X": pairs})]
            return {"cvtune": rows(*cv), "wikitune": rows(*wk)}
        major = {"X": "在"}
        # wins one set by 3, loses the other by 1: pooled 11 vs 9 -> enabled
        self.assertEqual(p1a.disabled_readings(sets((8, 5), (3, 4)), major), [])
        # +1 and -1: pooled tie -> disabled (not strictly greater)
        self.assertEqual(p1a.disabled_readings(sets((6, 5), (3, 4)), major), ["X"])
        self.assertEqual(p1a.disabled_readings(sets((4, 5), (3, 4)), major), ["X"])
        self.assertEqual(p1a.pooled_accuracy(sets((8, 5), (3, 4)), major), {"X": (11, 9, 20)})

    def test_stages_activate_readings_and_reselect(self):
        ZAI, ZUO = "ㄗㄞˋ", "ㄗㄨㄛˋ"
        fs = lambda zai, zuo: {ZAI: [zai, zai], ZUO: [zuo, zuo]}   # noqa: E731
        gp = {ZAI: [["在", "在"], ["再", "再"]], ZUO: [["作", "作"], ["做", "做"]]}
        rows = [row("G", [{"s": -10.0, "w": ["H"], "f": fs(-1.0, -9.0)}, {"s": -10.5, "w": ["G"], "f": fs(0.0, 0.0)}], gp=gp),   # the first reading is enough
                row("G", [{"s": -10.0, "w": ["H"], "f": fs(0.0, -4.0)}, {"s": -14.0, "w": ["G"], "f": fs(0.0, 0.0)}], gp=gp)]    # only the second helps (needs mu > 1)
        by_prof = {p: {"cvtune": rows, "wikitune": rows} for p in p1a.PROFILES}
        major = {ZAI: "在", ZUO: "作"}
        s1, s2 = p1a.run_stage(1, by_prof, major), p1a.run_stage(2, by_prof, major)
        self.assertEqual((s1["stage"], s1["active"], s2["active"]), (1, [ZAI], [ZAI, ZUO]))
        self.assertEqual((s1["chat"]["top1"], s2["chat"]["top1"]), (2, 4))
        self.assertEqual((s1["chat"]["mu"], s1["chat"]["tau"]), (0.7, 1))
        self.assertEqual((s2["chat"]["mu"], s2["chat"]["tau"]), (1.5, 5))   # selected again for the new active set
        # a trained reading whose classifier does not beat the most common character stays out
        for r in rows:
            r["gp"] = {ZAI: gp[ZAI], ZUO: [["做", "作"]] * 2}   # always wrong, and the most common character is 做
        s2 = p1a.run_stage(2, by_prof, {ZAI: "在", ZUO: "做"})
        self.assertEqual((s2["active"], s2["disabled"]), ([ZAI], [ZUO]))


    def test_disabling_is_decided_on_both_tuning_sets_together(self):
        ZAI, ZUO = "ㄗㄞˋ", "ㄗㄨㄛˋ"
        fs = lambda zai, zuo: {ZAI: [zai, zai], ZUO: [zuo, zuo]}   # noqa: E731
        cands = [{"s": -10.0, "w": ["H"], "f": fs(-1.0, -9.0)}, {"s": -10.5, "w": ["G"], "f": fs(0.0, 0.0)}]
        # each reading beats the most common character in ONE tuning set and loses in the other, and wins pooled: enabled
        cv = row("G", cands, gp={ZAI: [["再", "再"]] * 3, ZUO: [["作", "做"]]})        # ZAI 3 vs 0; ZUO 0 vs 1 (majority 作)
        wk = row("G", cands, gp={ZAI: [["在", "再"]], ZUO: [["做", "做"]] * 3})        # ZAI 0 vs 1 (majority 在); ZUO 3 vs 0
        by_prof = {p: {"cvtune": [cv], "wikitune": [wk]} for p in p1a.PROFILES}
        s = p1a.run_stage(2, by_prof, {ZAI: "在", ZUO: "作"})
        self.assertEqual((s["active"], s["disabled"]), ([ZAI, ZUO], []))
        # a reading that wins one set but loses pooled is disabled
        wk2 = row("G", cands, gp={ZAI: [["在", "再"]] * 4, ZUO: [["做", "做"]] * 3})
        s = p1a.run_stage(2, {p: {"cvtune": [cv], "wikitune": [wk2]} for p in p1a.PROFILES}, {ZAI: "在", ZUO: "作"})
        self.assertEqual((s["active"], s["disabled"]), ([ZUO], [ZAI]))


class Control(TmpCase):
    def test_equalization_only_touches_zai_and_zuo(self):
        eq = p1a.equalize(self.lex)
        score = lambda lex, r, w: dict(lex.by_reading[(r,)])[w]   # noqa: E731
        self.assertEqual(score(eq, "ㄗㄞˋ", "再"), score(self.lex, "ㄗㄞˋ", "在"))
        self.assertEqual(eq.by_word["再"][1], score(self.lex, "ㄗㄞˋ", "在"))
        self.assertEqual(score(eq, "ㄗㄨㄛˋ", "做"), score(self.lex, "ㄗㄨㄛˋ", "作"))
        self.assertEqual(score(self.lex, "ㄗㄞˋ", "再"), -3.3)   # the original is not modified
        changed = {k for k in self.lex.by_reading if eq.by_reading[k] != self.lex.by_reading[k]}
        self.assertEqual(changed, {("ㄗㄞˋ",), ("ㄗㄨㄛˋ",)})   # ㄕˋ (是/事) and everything else is untouched
        self.assertEqual({w: v for w, v in eq.by_word.items() if w not in "再做"}, {w: v for w, v in self.lex.by_word.items() if w not in "再做"})
        only_zai = p1a.equalize(self.lex, ("ㄗㄞˋ",))
        self.assertEqual(score(only_zai, "ㄗㄨㄛˋ", "做"), score(self.lex, "ㄗㄨㄛˋ", "做"))


def synthetic(n=40000):
    rng = np.random.RandomState(1)
    ids = rng.randint(0, 3000, size=(n, p1a.NF)).astype(np.int32)
    y = rng.randint(0, 3, size=n)
    ids[:, 3] = 7000 + y   # slot 3 carries the class
    return ids, y


class Training(unittest.TestCase):

    def test_learns_and_is_byte_reproducible(self):
        ids, y = synthetic()
        shas = []
        with tempfile.TemporaryDirectory() as d:
            for _ in range(2):
                keep, cols = p1a.compact(ids)
                W, b = p1a.train_lr(cols, y, np.ones(len(y)), 3, len(keep) + 1)
                meta = {"reading": "ㄗㄞˋ", "classes": ["在", "再"], "other_counts": {}, "other_total": 0, "v_other": 1, "n_keep": int(len(keep)), "majority": "在"}
                shas.append(p1a.save_weights(os.path.join(d, "w.sjw"), meta, keep, W, b))
            self.assertEqual(shas[0], shas[1])
            m = p1a.load_weights(os.path.join(d, "w.sjw"))
        acc = np.mean([m.predict(ids[i]).argmax() == y[i] for i in range(0, 4000)])
        self.assertGreater(acc, 0.9)   # chance is 0.33
        self.assertEqual(np.sort(keep).tolist(), keep.tolist())

    def test_rare_features_are_dropped(self):
        ids = np.zeros((10, p1a.NF), np.int32)
        ids[:, 0] = 1
        ids[:3, 1] = 5          # seen 3 times: kept
        ids[3:5, 2] = 6         # seen twice: dropped
        keep, cols = p1a.compact(ids)
        self.assertIn(5, keep.tolist()); self.assertNotIn(6, keep.tolist())
        self.assertTrue((cols[3:5, 2] == len(keep)).all())   # a dropped feature points at the zero row

    def test_int8_size(self):
        W = np.zeros((4, 3), np.float32); W[1, 2] = 1
        self.assertEqual(p1a.int8_sparse_bytes(W, np.zeros(3)), (1, 16 + 1 * 7 + 24))


REAL = os.path.join(p1a.ROOT, "data", "lm", "bigram.sjlm")


@unittest.skipUnless(os.path.exists(REAL), "data/lm is git-ignored")
class RealModel(unittest.TestCase):
    def test_class_ids_equal_the_reference_loader(self):
        cls_of, lm = p1a.load_cls_of(REAL), p1a.L.BigramLM(REAL)
        for w in ("在", "再", "我們", "不要", "做"):
            self.assertEqual(cls_of(w), lm.cls[lm.ids[w]], w)
        self.assertNotEqual(cls_of("在"), cls_of("再"))
        self.assertEqual(cls_of("沒有這個詞的詞"), -1)

    def test_candidates_score_and_fuse_end_to_end(self):
        rows_f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, rows_f.name)
        rows_f.write("了我|他說他不要再告訴你|ㄊㄚ ㄕㄨㄛ ㄊㄚ ㄅㄨˊ ㄧㄠˋ ㄗㄞˋ ㄍㄠˋ ㄙㄨˋ ㄋㄧˇ\n|我們今天在家|ㄨㄛˇ ㄇㄣ˙ ㄐㄧㄣ ㄊㄧㄢ ㄗㄞˋ ㄐㄧㄚ\n")
        rows_f.close()
        with tempfile.TemporaryDirectory() as d:
            ids, y = synthetic(3000)
            keep, cols = p1a.compact(ids)
            W, b = p1a.train_lr(cols, y, np.ones(len(y)), 3, len(keep) + 1)
            os.makedirs(os.path.join(d, "weights"))
            p1a.save_weights(os.path.join(d, "weights", "ㄗㄞˋ.sjw"), {"reading": "ㄗㄞˋ", "classes": ["在", "再"], "other_counts": {"載": 3}, "other_total": 3,
                                                                    "v_other": 5, "n_keep": int(len(keep)), "majority": "在"}, keep, W, b)
            import candidates, score
            out = os.path.join(d, "c.jsonl")
            buf = io.StringIO()
            with mock.patch.object(sys, "argv", ["candidates.py", "--lm", REAL, "--profile", "chat", "--rows", rows_f.name, "--name", "t", "--out", out]), redirect_stdout(buf):
                candidates.main()
            ref = subprocess.run([sys.executable, os.path.join(p1a.ROOT, "reference", "proto", "lm_eval.py"), "--lm", REAL, "--profile", "chat", "--rows", rows_f.name,
                                  "--name", "t", "--context"], capture_output=True, text=True, check=True).stdout
            self.assertEqual(buf.getvalue().split("'top1_sha256'")[1], ref.split("'top1_sha256'")[1])   # same first candidate for every row as lm_eval.py --context
            got = [json.loads(l) for l in open(out, encoding="utf-8")]
            self.assertEqual(got[0]["ctxk"], "了我")
            self.assertEqual(len(got[0]["cands"]), 8)
            sc = os.path.join(d, "s.jsonl")
            with mock.patch.object(sys, "argv", ["score.py", "--lm", REAL, "--weights", os.path.join(d, "weights"), "--in", out, "--out", sc]):
                score.main()
            scored = [json.loads(l) for l in open(sc, encoding="utf-8")]
            self.assertTrue(all(c["f"]["ㄗㄞˋ"][0] <= 0 for r in scored for c in r["cands"]))
            self.assertEqual(len(scored[0]["gp"]["ㄗㄞˋ"]), 1)
            self.assertEqual(scored[0]["gp"]["ㄗㄞˋ"][0][0], "再")


class FuseSmoke(unittest.TestCase):
    """The reports run end to end on synthetic candidate files."""

    def make(self, d, wrong_gp=False):
        for sub in ("cands", "scored", "eq", "weights"):
            os.makedirs(os.path.join(d, sub))
        meta = {"reading": "ㄗㄞˋ", "classes": ["在", "再"], "other_counts": {}, "other_total": 0, "v_other": 1, "n_keep": 0, "majority": "在"}
        p1a.save_weights(os.path.join(d, "weights", "ㄗㄞˋ.sjw"), meta, np.zeros(0, np.int32), np.zeros((0, 3)), np.zeros(3))
        for name in fuse.SETS:
            for prof in p1a.PROFILES:
                rows, eq_rows = [], []
                for i in range(12):
                    a = i % 3 != 0   # a: gold 在, the n-gram says 再; b: gold 再, the n-gram is right
                    gold = "在" if a else "再"
                    c = [{"s": -1.0, "w": ["再"], "f": {"ㄗㄞˋ": [-1.0 if a else 0.0, -0.9]}}, {"s": -1.2, "w": ["在"], "f": {"ㄗㄞˋ": [0.0 if a else -1.0, 0.0]}}]
                    rows.append({"i": i + 1, "gold": gold, "syls": ["ㄗㄞˋ"], "ctxk": "", "cands": c,
                                 "gp": {"ㄗㄞˋ": [[gold, "再" if gold == "在" else "在"] if wrong_gp else [gold, gold]]}})
                    eq_rows.append({"i": i + 1, "gold": gold, "syls": ["ㄗㄞˋ"], "ctxk": "", "cands": [{"s": -1.0, "w": ["在"]}]})
                for sub, rs in (("scored", rows), ("eq", eq_rows), ("cands", [{k: v for k, v in r.items() if k != "gp"} for r in rows])):
                    with open(os.path.join(d, sub, f"{name}.{prof}.jsonl"), "w", encoding="utf-8") as f:
                        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rs))

    def run_cmd(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fuse.main(list(argv))
        return buf.getvalue()

    def test_commands(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d)
            self.assertIn("wrong=8", self.run_cmd("fable", "--work", d))
            self.assertIn("disabled (", self.run_cmd("accuracy", "--work", d))
            out = self.run_cmd("stage", "1", "--work", d, "--out", os.path.join(d, "stage1.json"))
            st = json.load(open(os.path.join(d, "stage1.json")))
            self.assertEqual((st["active"], st["chat"]["mu"], st["chat"]["tau"]), (["ㄗㄞˋ"], 0.3, 0.5))
            self.assertIn("PASS, continue", out)
            rep = self.run_cmd("report", "--work", d, "--stage", os.path.join(d, "stage1.json"), "--sets", ",".join(fuse.SETS))
            self.assertIn("discordtune chat 分類器", rep)
            self.assertIn("verdict: build it into the core", rep)
            self.assertIn("PASS  reported chat: 12 rows where f(c) changes with the context", rep)
            self.assertNotIn("ERROR", rep)
            self.assertEqual((st["decision"], st["reasons"]), ("PASS", []))
            # a stage that recorded STOP never yields the build verdict
            st["decision"], st["reasons"] = "STOP", ["x"]
            json.dump(st, open(os.path.join(d, "stop.json"), "w"))
            rep = self.run_cmd("report", "--work", d, "--stage", os.path.join(d, "stop.json"), "--sets", ",".join(fuse.SETS))
            self.assertIn("verdict: STOP", rep)
            self.assertNotIn("build it into the core (next", rep)
            self.assertNotIn("PASS  ", rep)

    def test_missing_input_and_unknown_set_are_errors(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d)
            self.run_cmd("stage", "1", "--work", d, "--out", os.path.join(d, "s.json"))
            gone = os.path.join(d, "scored", "typing.chat.jsonl")
            os.unlink(gone)
            with self.assertRaises(SystemExit) as e:
                self.run_cmd("report", "--work", d, "--stage", os.path.join(d, "s.json"), "--sets", ",".join(fuse.SETS))
            self.assertIn(gone, str(e.exception.code))
            with self.assertRaises(SystemExit) as e:
                self.run_cmd("accuracy", "--work", d, "--sets", "cvtune,nosuchset")
            self.assertIn("nosuchset", str(e.exception.code))

    def test_stage_stops_when_more_than_half_the_classifiers_are_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d, wrong_gp=True)
            out = self.run_cmd("stage", "1", "--work", d, "--out", os.path.join(d, "s.json"))
            st = json.load(open(os.path.join(d, "s.json")))
            self.assertEqual(st["decision"], "STOP")
            self.assertIn("more than half", st["reasons"][0])
            rep = self.run_cmd("report", "--work", d, "--stage", os.path.join(d, "s.json"), "--sets", ",".join(fuse.SETS))
            self.assertIn("verdict: STOP", rep)


if __name__ == "__main__":
    unittest.main()
