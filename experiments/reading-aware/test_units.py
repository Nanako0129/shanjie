"""Tests of units.py, count_units.py and build_model.py (contract docs/contracts/reading-aware-0.md 4.1: labels, units, counting and the
counting anchor). Synthetic text; the lattice lexicon of the counting tests is the repo's real lexicon where a test needs the real model
builder (variant classes) and a SimpleNamespace lexicon otherwise."""
import collections
import contextlib
import io
import os
import pickle
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import build_model
import count_units as C
import rcommon
import testkit
import units
from testkit import U
import build_counts as bc  # noqa: E402
import build_lm  # noqa: E402
import count  # noqa: E402
import mapping  # noqa: E402

# One entry per rule; the first reading listed is the best-scored one, so it is the primary reading unless the rule table says otherwise.
RULES = """ㄌㄜ˙ 了 -2.0
ㄌㄧㄠˇ 了 -2.5
ㄉㄧˊ 的 -3.0
ㄉㄜ˙ 的 -1.0
ㄉㄜˊ 得 -2.5
ㄉㄜ˙ 得 -2.0
ㄉㄟˇ 得 -3.0
ㄉㄧˋ 地 -2.0
ㄉㄜ˙ 地 -3.0
ㄏㄞˊ 還 -2.0
ㄏㄨㄢˊ 還 -2.5
ㄓㄨㄥˋ 重 -2.0
ㄔㄨㄥˊ 重 -2.5
ㄎㄨㄥ 空 -2.0
ㄎㄨㄥˋ 空 -3.0
ㄔㄤˊ 長 -2.0
ㄓㄤˇ 長 -2.5
ㄒㄧㄥˊ 行 -2.0
ㄏㄤˊ 行 -2.5
ㄧˋ 一 -2.0
ㄧ 一 -3.0
ㄧˊ 一 -3.5
ㄅㄚ˙ 吧 -2.0
ㄅㄚ 吧 -2.1
ㄛ 喔 -3.0
ㄛˊ 喔 -3.5
ㄏㄠˇ 好 -1.5
ㄏㄠˋ 好 -3.5
ㄈㄥ 風 -2.0
"""


def make(overrides=None, hetero=None, text=RULES):
    td = tempfile.TemporaryDirectory()
    lex = testkit.lexicon(td.name, text)
    return td, units.Labeller(rcommon.char_readings(lex), overrides, hetero)


class Labels(unittest.TestCase):
    def setUp(self):
        td, self.lab = make()
        self.addCleanup(td.cleanup)

    def at(self, text, c):
        """Label of the first occurrence of c in text."""
        return self.lab.label(text, text.index(c))

    def test_each_rule_has_a_case_for_and_a_case_against(self):
        cases = [
            # (text, char, expected)
            ("你好了", "了", "ㄌㄜ˙"), ("好的", "的", "ㄉㄜ˙"),
            ("不得", "得", "ㄉㄟˇ"), ("就得", "得", "ㄉㄟˇ"), ("不得很", "得", "ㄉㄜ˙"), ("不得太", "得", "ㄉㄜ˙"), ("跑得", "得", "ㄉㄜ˙"), ("得", "得", "ㄉㄜ˙"),
            ("慢慢地", "地", "ㄉㄜ˙"), ("慢地", "地", "ㄉㄧˋ"), ("地", "地", "ㄉㄧˋ"), ("土地", "地", "ㄉㄧˋ"),
            ("還錢", "還", "ㄏㄨㄢˊ"), ("還清", "還", "ㄏㄨㄢˊ"), ("還好", "還", "ㄏㄞˊ"), ("還", "還", "ㄏㄞˊ"),
            ("重新", "重", "ㄔㄨㄥˊ"), ("重複", "重", "ㄔㄨㄥˊ"), ("重要", "重", "ㄓㄨㄥˋ"),
            ("有空", "空", "ㄎㄨㄥˋ"), ("抽空", "空", "ㄎㄨㄥˋ"), ("空出", "空", "ㄎㄨㄥˋ"), ("空檔", "空", "ㄎㄨㄥˋ"), ("空下來", "空", "ㄎㄨㄥˋ"),
            ("空下去", "空", "ㄎㄨㄥ"), ("天空", "空", "ㄎㄨㄥ"), ("空下", "空", "ㄎㄨㄥ"),
            ("長大", "長", "ㄓㄤˇ"), ("長在", "長", "ㄓㄤˇ"), ("長相", "長", "ㄓㄤˇ"), ("長城", "長", "ㄔㄤˊ"), ("長", "長", "ㄔㄤˊ"),
            ("三行", "行", "ㄏㄤˊ"), ("兩行", "行", "ㄏㄤˊ"), ("5行", "行", "ㄏㄤˊ"), ("這行", "行", "ㄏㄤˊ"), ("第行", "行", "ㄏㄤˊ"),
            ("銀行", "行", "ㄒㄧㄥˊ"), ("行", "行", "ㄒㄧㄥˊ"), ("a行", "行", "ㄒㄧㄥˊ"), ("七行", "行", "ㄏㄤˊ"), ("百行", "行", "ㄏㄤˊ"), ("好行", "行", "ㄒㄧㄥˊ"),
            ("好吧", "吧", "ㄅㄚ˙"),            # a whitelist particle with a ˙ reading: ˙
            ("好喔", "喔", "ㄛ"),               # a whitelist particle without one: the primary reading
            ("風", "風", "ㄈㄥ"),                # any other character: the primary reading
            ("好", "好", "ㄏㄠˇ"),
        ]
        for text, c, want in cases:
            with self.subTest(text=text, c=c):
                self.assertEqual(self.at(text, c), want)

    def test_the_context_symbols_are_single_characters_at_fixed_offsets(self):
        # p2 == p needs p to exist: at the start of the text the empty p and p2 must not look like a reduplication
        self.assertEqual(self.lab.label("地", 0), "ㄉㄧˋ")
        self.assertEqual(self.lab.label("慢慢地好", 2), "ㄉㄜ˙")       # n (好) does not matter
        self.assertEqual(self.lab.label("空下來", 0), "ㄎㄨㄥˋ")        # n = 下 and n2 = 來
        self.assertEqual(self.lab.label("空下", 0), "ㄎㄨㄥ")           # n2 = "" at the end of the text

    def test_primary_reading_follows_readings_py_rules_0_1_3_4(self):
        self.assertEqual(self.lab.primary("一"), "ㄧ")                   # rule 1: citation tone although ㄧˋ scores best
        self.assertEqual(self.lab.primary("好"), "ㄏㄠˇ")                # rule 4: lexicon first
        _, with_hetero = make(hetero={"好": "ㄏㄠˋ"})
        self.assertEqual(with_hetero.primary("好"), "ㄏㄠˋ")             # rule 3
        _, with_over = make(overrides={"好": "ㄏㄠˋ"}, hetero={"好": "ㄏㄠˇ"})
        self.assertEqual(with_over.primary("好"), "ㄏㄠˋ")               # rule 0 first
        _, not_in_lexicon = make(overrides={"好": "ㄌㄌ"}, hetero={"好": "ㄍㄍ"})
        self.assertEqual(not_in_lexicon.primary("好"), "ㄏㄠˇ")          # a reading the lexicon lacks is never used

    def test_a_rule_reading_the_lexicon_lacks_becomes_the_primary_and_is_counted(self):
        td, lab = make(text=RULES.replace("ㄌㄜ˙ 了 -2.0\n", ""))
        self.addCleanup(td.cleanup)
        self.assertEqual(lab.label("你了", 1), "ㄌㄧㄠˇ")
        self.assertEqual(lab.fallbacks, collections.Counter({"了": 1}))

    def test_every_label_is_a_lexicon_reading_of_the_character(self):
        text = "不得很慢慢地還錢重新有空長大三行好吧喔了的風一"
        for i, c in enumerate(text):
            if c in self.lab.table:
                self.assertIn(self.lab.label(text, i), {r for r, _ in self.lab.table[c]}, (i, c))

    def test_units(self):
        self.assertEqual(self.lab.unit("好吧", 0, "好吧"), "好吧")                       # a multi-character word is not split
        self.assertEqual(self.lab.unit("風", 0, "風"), "風")                              # one reading: the character itself
        self.assertEqual(self.lab.unit("吧", 1, "好吧"), "吧" + U + "ㄅㄚ˙")
        self.assertEqual(self.lab.unit("還", 0, "還錢"), "還" + U + "ㄏㄨㄢˊ")
        self.assertEqual(self.lab.unit("口", 0, "口"), "口")                              # not in the lexicon at all

    def test_the_label_depends_on_the_position_only(self):
        text = "不得很慢慢地還錢"
        first = [self.lab.label(text, i) for i, c in enumerate(text) if c in self.lab.table]
        self.assertEqual(first, [self.lab.label(text, i) for i, c in enumerate(text) if c in self.lab.table])

    def test_loaders(self):
        with tempfile.TemporaryDirectory() as d:
            o = testkit.write(d, "o.tsv", "# c\n那邊\tㄋㄚˋ ㄅㄧㄢ\n了\tㄌㄜ˙\n吧\tㄅㄚ˙\n")
            h = testkit.write(d, "h.list", "好 ㄏㄠˇ\n壞 ㄏㄨㄞˋ\n行\n")
            self.assertEqual(units.load_overrides(o), {"了": "ㄌㄜ˙", "吧": "ㄅㄚ˙"})     # words are not positions
            self.assertEqual(units.load_hetero(h), {"好": "ㄏㄠˇ", "壞": "ㄏㄨㄞˋ"})
        self.assertIn("了", units.load_overrides(os.path.join(rcommon.ROOT, "tools", "reading_overrides.tsv")))


# --- counting ---------------------------------------------------------------------------------------------------------------------------

def lattice(**scores):
    """A lattice lexicon for bc.forward_backward: word -> log10 score, with by_word / max_len like test_expected."""
    by_word = {w: (None, s) for w, s in scores.items()}
    return SimpleNamespace(by_word=by_word, max_len=max(map(len, by_word)))


LAT = lattice(**{"好吧": -3.0, "好": -1.5, "吧": -2.0, "不得": -3.2, "不": -1.0, "得": -2.0, "了": -1.0, "你": -1.5, "行": -2.0, "三行": -3.5, "三": -1.5,
                 "好得": -4.0, "慢慢": -3.0, "慢": -2.0, "地": -2.5, "還": -2.0, "錢": -2.0})
TEXTS = ["你好吧。不得了，三行好。", "慢慢地還錢！你不得好吧。", "好 123行好吧"]


class Counting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conv = mock.patch.object(bc, "load_conv", lambda: ({}, {}, 1)); cls.conv.start()
        bc._init(False, True, False, ())                      # the real lexicon and the mocked conversion, like derisk-1015/e1/test_e1.py
        cls.real_lex = bc._W["lex"]
        td, cls.lab = make()
        cls._td = td

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()
        cls.conv.stop()

    def setUp(self):
        bc._W["lex"], bc._W["pack"] = LAT, set()
        self.addCleanup(lambda: bc._W.update(lex=self.real_lex, pack=set()))

    def test_han_runs_with_offset_match_the_e1_tokenizer(self):
        for t in TEXTS + ["", "abc", "你12好吧ABC不得"]:
            got = C.han_runs_with_offset(t)
            self.assertEqual([r for r, _ in got], [r for r, _ in mapping.han_runs_with_left(t)])
            self.assertTrue(all(t[o:o + len(r)] == r for r, o in got), t)

    def test_identity_units_reproduce_expected_counts_exactly(self):
        for run in ("好吧", "你好吧", "不得好吧", "三行好", "慢慢地還錢", "錢錢"):
            with self.subTest(run):
                self.assertEqual(C.expected_units(LAT, run, lambda w, i: w), bc.expected_counts(LAT, run))
                self.assertEqual(C.expected_units(LAT, run, lambda w, i: w, 0), bc.expected_counts(LAT, run, 0))

    def test_identity_units_reproduce_the_additive_pack_scheme(self):
        bc._W["lex0"] = lattice(**{k: v[1] for k, v in LAT.by_word.items() if k not in ("好吧", "三行")})
        bc._W["pack"], bc._W["pack_lens"] = {"好吧", "三行"}, [2]
        for run in ("你好吧", "三行好", "不得好吧", "你好", "錢錢"):
            with self.subTest(run):
                self.assertEqual(C.run_units(run, lambda w, i: w), bc.run_expected(run))

    def test_identity_acc_equals_the_e1_acc_on_the_same_sentences(self):
        e1 = count.Acc()
        mine = C.UnitAcc(None)
        for t in TEXTS:
            e1.add_text(t, bc.EXPECTED_MIN)
            mine.add_text(t)
        self.assertEqual((dict(mine.uni), dict(mine.bi), mine.runs), (dict(e1.uni), dict(e1.bi), e1.runs))
        self.assertGreater(mine.runs, 0)

    def test_labelled_counts_split_a_word_by_reading_and_keep_the_total(self):
        ident, lab = C.UnitAcc(None), C.UnitAcc(self.lab)
        for t in TEXTS:
            ident.add_text(t)
            lab.add_text(t)
        by_word = collections.Counter()
        for u, e in lab.uni.items():
            by_word[units.base_word(u)] += e
        self.assertEqual(sorted(by_word), sorted(ident.uni))
        for w, e in ident.uni.items():
            self.assertAlmostEqual(by_word[w], e, places=9, msg=w)
        self.assertAlmostEqual(sum(lab.bi.values()), sum(ident.bi.values()), places=9)
        # 得 after 不 reads ㄉㄟˇ, 得 after 你不 ... reads ㄉㄜ˙; 行 after 三 reads ㄏㄤˊ and 行 after 好 does not exist; 吧 is always ˙; 地 after 慢慢 reads ˙
        self.assertGreater(lab.uni.get("得" + U + "ㄉㄟˇ", 0), 0)
        self.assertGreater(lab.uni.get("行" + U + "ㄏㄤˊ", 0), 0)
        self.assertGreater(lab.uni.get("吧" + U + "ㄅㄚ˙", 0), 0)
        self.assertEqual(lab.uni.get("吧" + U + "ㄅㄚ", 0), 0)
        self.assertGreater(lab.uni.get("地" + U + "ㄉㄜ˙", 0), 0)
        self.assertGreater(lab.uni.get("還" + U + "ㄏㄨㄢˊ", 0), 0)    # 還錢
        self.assertNotIn("好吧" + U + "ㄏㄠˇ", lab.uni)
        self.assertTrue(all(rcommon.U in k or k in ident.uni for k in lab.uni))   # a unit is either a lexicon word or character+U+reading
        for k in lab.uni:
            if U in k:
                c, r = k.split(U)
                self.assertIn(r, {x for x, _ in self.lab.table[c]})   # every unit's reading is a lexicon reading of the character

    def test_the_context_may_lie_outside_the_han_run(self):
        # 行 is the first character of its run "行好": its p is the ASCII digit 3 of "3行好", outside the run
        acc = C.UnitAcc(self.lab)
        acc.add_text("3行好")
        self.assertIn("行" + U + "ㄏㄤˊ", acc.uni)
        acc = C.UnitAcc(self.lab)
        acc.add_text("a行好")
        self.assertNotIn("行" + U + "ㄏㄤˊ", acc.uni)
        self.assertIn("行" + U + "ㄒㄧㄥˊ", acc.uni)

    def test_the_labels_come_from_the_variant_merged_text(self):
        # the labeller sees bc.fold(text) (臺 -> 台, 佔 -> 占 ...), position for position; the lattice still runs on the original text
        seen = []
        spy = SimpleNamespace(unit=lambda w, pos, text: seen.append((w, pos, text)) or w, fallbacks=collections.Counter())
        acc = C.UnitAcc(spy)
        bc._W["lex"] = lattice(**{"臺": -2.0, "佔": -2.0, "好": -1.0, "吧": -1.0})
        acc.add_text("臺佔好吧")
        self.assertEqual({t for _, _, t in seen}, {"台占好吧"})
        self.assertTrue(all("台占好吧"[pos] == bc.fold(w)[0] for w, pos, _ in seen))
        self.assertEqual(bc.fold("臺佔"), "台占")

    def test_every_edge_gets_its_own_position(self):
        seen = []
        C.expected_units(LAT, "不得好吧", lambda w, i: seen.append((w, i)) or w)
        self.assertTrue(seen)
        self.assertTrue(all("不得好吧"[i:i + len(w)] == w for w, i in seen))

    def test_a_cut_does_not_change_a_positions_unit(self):
        # 好吧 as one word or as 好 + 吧: the unit of the 吧 position is whatever the labeller says, independent of the cut
        run, memo = "好吧", {}
        for lat in (lattice(**{"好吧": -1.0, "好": -9.0, "吧": -9.0}), lattice(**{"好": -1.0, "吧": -1.0}), LAT):
            seen = {}
            C.expected_units(lat, run, lambda w, i: seen.setdefault((w, i), self.lab.unit(w, i, run)))
            for (w, i), u in seen.items():
                if len(w) == 1:
                    self.assertEqual(u, memo.setdefault(i, u))

    def test_batch_function_equals_the_e1_batch_in_identity_mode(self):
        bc._W["tri_min"] = bc.EXPECTED_MIN
        C._U["labeller"] = None
        pages = ["你好吧。不得了，三行好。{{x}}", "<ref>y</ref>慢慢地還錢！"]
        u, b, runs, fb = C.count_batch_units(pages)
        u1, b1, _, _, _, runs1 = count.count_batch3(pages)
        self.assertEqual((dict(u), dict(b), runs, dict(fb)), (dict(u1), dict(b1), runs1, {}))

    def test_fallbacks_are_per_batch_so_the_parents_sum_is_exact(self):
        bc._W["tri_min"] = bc.EXPECTED_MIN
        lab = SimpleNamespace(fallbacks=collections.Counter(), unit=lambda w, pos, text: w)
        C._U["labeller"] = lab
        real = C.UnitAcc

        def acc(labeller):
            labeller.fallbacks["了"] += 1       # one fallback per batch, as if the labeller hit one
            return real(labeller)
        with mock.patch.object(C, "UnitAcc", acc):
            total = collections.Counter()
            for _ in range(2):
                total.update(C.count_batch_units(["你好吧。"])[3])
        self.assertEqual(total, collections.Counter({"了": 2}))


class ModelAnchor(unittest.TestCase):
    """The counting anchor in its unit-test form: identity counts through the wrapper and build_model.py give the model the E1 route gives."""

    @classmethod
    def setUpClass(cls):
        cls.conv = mock.patch.object(bc, "load_conv", lambda: ({}, {}, 1)); cls.conv.start()
        bc._init(False, True, False, ())
        bc._W["tri_min"] = bc.EXPECTED_MIN

    @classmethod
    def tearDownClass(cls):
        cls.conv.stop()

    TEXTS_A = ["今天天氣很好。我去學校，他說你好嗎。", "我去吃飯。他說再見。今天我去學校。", "你好嗎。我在學校說再見。"]
    TEXTS_B = ["你好嗎。我去學校。", "他說在學校。我去吃飯。今天天氣很好。"]

    def counts(self, d, mine):
        for tag, texts in (("200000", self.TEXTS_A), ("colloquial3", self.TEXTS_B)):
            if mine:
                acc = C.UnitAcc(None)
                for t in texts:
                    acc.add_text(t)
                C.write_outputs(d, tag, acc.uni, acc.bi, acc.runs, 0, collections.Counter(), {})
            else:
                acc = count.Acc()
                for t in texts:
                    acc.add_text(t, bc.EXPECTED_MIN)
                count.write_outputs(d, tag, acc.uni, acc.bi, acc.tri, acc.dbi, acc.nx, acc.runs, 0, {})
        return os.path.join(d, "unmapped")

    def test_identity_counts_build_the_same_model_bytes_as_the_e1_route(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2, contextlib.redirect_stdout(io.StringIO()):
            ref_dir, mine_dir = self.counts(d1, False), self.counts(d2, True)
            for name in ("counts-200000.pkl", "counts-colloquial3.pkl"):
                self.assertEqual(pickle.load(open(os.path.join(ref_dir, name), "rb")), pickle.load(open(os.path.join(mine_dir, name), "rb")))
            with mock.patch.object(build_lm, "WORK", ref_dir):
                ref, *rest = build_lm.build()            # what experiments/derisk-1015/e1/build_models.py bigram does (cls from the lexicon)
            mine, *rest2, nunits = build_model.build_bytes(mine_dir)
            self.assertEqual(mine, ref)
            self.assertEqual((rest, nunits), (rest2, 0))

    def test_units_build_a_model_with_reading_units_and_merge_variant_classes(self):
        # a unit of a variant-class character merges with its variant's unit: 台 and 臺 have the same lexicon readings, so they are one class
        cls = build_lm.variant_classes(bc._W["lex"].by_reading)
        self.assertIn("台", cls)
        full = build_model.unit_classes(cls, {"台" + U + "ㄊㄞˊ", "好" + U + "ㄏㄠˇ", "行" + U + "ㄏㄤˊ"})
        members = cls["台"]
        self.assertEqual(full["台" + U + "ㄊㄞˊ"], tuple(m + U + "ㄊㄞˊ" for m in members))
        self.assertEqual(full[members[-1] + U + "ㄊㄞˊ"], full["台" + U + "ㄊㄞˊ"])
        self.assertNotIn("好" + U + "ㄏㄠˇ", full)                    # not in a class: unchanged
        self.assertEqual({k: v for k, v in full.items() if U not in k}, cls)
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            for name in ("counts-200000.pkl", "counts-colloquial3.pkl"):
                uni = {"今天": 5, "天氣": 5, "我": 5, "去": 5, "好" + U + "ㄏㄠˇ": 5, "台" + U + "ㄊㄞˊ": 7, "臺" + U + "ㄊㄞˊ": 3}
                bi = {("<s>", "今天"): 4, ("今天", "好" + U + "ㄏㄠˇ"): 4, ("好" + U + "ㄏㄠˇ", "</s>"): 4, ("<s>", "臺" + U + "ㄊㄞˊ"): 3,
                      ("臺" + U + "ㄊㄞˊ", "</s>"): 3, ("<s>", "台" + U + "ㄊㄞˊ"): 6, ("台" + U + "ㄊㄞˊ", "</s>"): 6}
                pickle.dump({"uni": uni, "bi": bi}, open(os.path.join(d, name), "wb"))
            data, V, C_, E, nunits = build_model.build_bytes(d)
            path = os.path.join(d, "m.sjlm")
            open(path, "wb").write(data)
            import lm as L
            lm = L.BigramLM(path, classes=False)
            self.assertEqual(nunits, 3)
            counts = {m: lm.count(m + U + "ㄊㄞˊ") for m in members}
            self.assertEqual(set(counts.values()), {(7 + 3) * (1 + 5)})  # 台 + 臺 summed, the same count written to every member; weights 1 and 5
