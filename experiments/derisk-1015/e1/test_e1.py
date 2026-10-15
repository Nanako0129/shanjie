"""Unit tests for experiments/derisk-1015/e1 (E1 trigram / grid / compression, E4 (b) mapping). Synthetic data only.
Run: python3 experiments/derisk-1015/e1/test_e1.py   (about a minute: it loads the real lexicon a few times)
"""
import collections
import contextlib
import bz2
import io
import json
import math
import os
import pickle
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402
import anchor  # noqa: E402
import build_models  # noqa: E402
import compress  # noqa: E402
import count  # noqa: E402
import grid  # noqa: E402
import harness  # noqa: E402
import mapping  # noqa: E402
import rowstats_concat  # noqa: E402
import subset_rowstats  # noqa: E402
import sjlm_io  # noqa: E402
import trimodel  # noqa: E402

import build_counts as bc  # noqa: E402
import build_lm  # noqa: E402
import iter2  # noqa: E402
import ime  # noqa: E402
import lm as L  # noqa: E402
import lm_eval  # noqa: E402

# (words, weight). Real lexicon words, so the same sentences also drive decoding tests. First 6 are "wiki", the rest "colloquial".
SENTS = [(["今天", "天氣", "很", "好"], 6), (["我", "去", "學校"], 5), (["我", "去", "吃", "飯"], 4), (["他", "說", "你好"], 3), (["今天", "我", "去", "學校"], 2),
         (["天氣", "很", "好"], 3), (["你好", "嗎"], 4), (["他", "說", "再見"], 3), (["他", "說", "在", "學校"], 2), (["他", "說", "再", "說"], 1)]
SYLS = {"今天天氣很好": "ㄐㄧㄣ ㄊㄧㄢ ㄊㄧㄢ ㄑㄧˋ ㄏㄣˇ ㄏㄠˇ", "我去學校": "ㄨㄛˇ ㄑㄩˋ ㄒㄩㄝˊ ㄒㄧㄠˋ", "他說再見": "ㄊㄚ ㄕㄨㄛ ㄗㄞˋ ㄐㄧㄢˋ",
        "他說在學校": "ㄊㄚ ㄕㄨㄛ ㄗㄞˋ ㄒㄩㄝˊ ㄒㄧㄠˋ", "你好嗎": "ㄋㄧˇ ㄏㄠˇ ㄇㄚ"}


def counts_of(sents, x_first=()):
    """uni, bi, tri Counters for sentences [(words, k)]; sentences whose index is in x_first start from <X> instead of <s> (mapped counts)."""
    uni, bi, tri = collections.Counter(), collections.Counter(), collections.Counter()
    for n, (words, k) in enumerate(sents):
        head = "<X>" if n in x_first else "<s>"
        for w in words:
            uni[w] += k
        if head == "<X>":
            uni["<X>"] += k
        seq = [head] + words + ["</s>"]
        for a, b in zip(seq, seq[1:]):
            bi[(a, b)] += k
        seq3 = ["<s>", head] + words + ["</s>"]
        for a, b, c in zip(seq3, seq3[1:], seq3[2:]):
            tri[(a, b, c)] += k
    return uni, bi, tri


def write_counts(d, sents, x_first=(), with_tri=True):
    """First 6 sentences are the "wiki" file, the rest the "colloquial" file; x_first are indexes into sents that start from <X>."""
    os.makedirs(d, exist_ok=True)
    for name, lo, hi in (("200000", 0, 6), ("colloquial3", 6, len(sents))):
        uni, bi, tri = counts_of(sents[lo:hi], {i - lo for i in x_first if lo <= i < hi})
        for obj, fn in (({"uni": uni, "bi": bi, "articles": 0, "runs": 1}, f"counts-{name}.pkl"), *((({"tri": tri}, f"tri-{name}.pkl"),) if with_tri else ())):
            with open(os.path.join(d, fn), "wb") as f:
                pickle.dump(obj, f)


def slurp(path, mode="r"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as f:
        return f.read()


def unpickle(path):
    with open(path, "rb") as f:
        return pickle.load(f)


def run_main(mod_main, argv):
    """Run a main() with sys.argv set; returns stdout."""
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["prog", *argv]), contextlib.redirect_stdout(out):
        mod_main()
    return out.getvalue()


_SHARED = {}


def shared_fixture():
    """B0 / T_k / B0m built by build_models from synthetic counts, once per test run."""
    if not _SHARED:
        tmp = tempfile.TemporaryDirectory()
        t = tmp.name
        counts, counts_m = os.path.join(t, "counts"), os.path.join(t, "counts-mapped")
        write_counts(counts, SENTS)
        mapped = SENTS + [(["你好", "嗎"], 3), (["今天", "天氣", "很", "好"], 2)]   # two more sentences that start from <X>
        write_counts(counts_m, mapped, x_first={10, 11}, with_tri=False)
        b0, tri_dir, b0m = os.path.join(t, "b0"), os.path.join(t, "tri"), os.path.join(t, "b0m")
        build_models.main(["bigram", "--counts", counts, "--out", b0])
        build_models.main(["bigram", "--counts", counts_m, "--out", b0m])
        build_models.main(["tri", "--counts", counts, "--lm", os.path.join(b0, "bigram.sjlm"), "--out", tri_dir, "--thresholds", "1,2,3,5"])
        rows = os.path.join(t, "rows.txt")
        with open(rows, "w", encoding="utf-8") as f:
            for text, syl in SYLS.items():
                f.write(f"|{text}|{syl}\n")
            f.write("他說|再見|ㄗㄞˋ ㄐㄧㄢˋ\n")
            f.write("快1.5|倍|ㄅㄟˋ\n")
        _SHARED.update(tmp=tmp, counts=counts, counts_m=counts_m, b0=b0, tri_dir=tri_dir, b0m=b0m, lm_path=os.path.join(b0, "bigram.sjlm"), rows=rows)
    return _SHARED


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for k, v in shared_fixture().items():
            setattr(cls, k, v)

    def tri_path(self, t):
        return os.path.join(self.tri_dir, f"tri-t{t}.sjt3")

    def load_lm(self):
        return L.BigramLM(self.lm_path, classes=False)

    def tri_table(self, t):
        return trimodel.TriTable.from_path(self.tri_path(t), bytes.fromhex(common.sha256_file(self.lm_path)))


class TestMapping(unittest.TestCase):
    def test_han_pattern_is_the_counters(self):
        self.assertEqual(mapping.HAN.pattern, bc.HAN.pattern)

    def test_tokens(self):
        self.assertEqual(mapping.tokenize("現在Windows，快1.5倍"), [
            ("han", "現在", "現在"), ("x", "Windows", "<X>"), ("other", "，", "，"), ("han", "快", "快"), ("x", "1", "<X>"),
            ("other", ".", "."), ("x", "5", "<X>"), ("han", "倍", "倍")])
        self.assertEqual([t for k, _, t in mapping.tokenize("ＡＢ１２你")], ["<X>", "<X>", "你"])   # full-width letters / digits are runs too

    def test_adjacency_decides_the_left_neighbour(self):
        self.assertEqual(mapping.han_runs_with_left("買3個人，再見 你好iPhone"), [("買", False), ("個人", True), ("再見", False), ("你好", False)])
        self.assertTrue(mapping.ends_with_x("量一下Colab"))
        self.assertTrue(mapping.ends_with_x("快1.5"))
        self.assertFalse(mapping.ends_with_x("約，"))
        self.assertFalse(mapping.ends_with_x("Colab "))
        self.assertFalse(mapping.ends_with_x(""))

    def test_counting_and_decoding_call_the_same_tokenizer(self):
        """Replace the one tokenizer by a stub that calls everything an <X>: both consumers change, so neither has its own."""
        stub = lambda text: [("x", text, mapping.X)] if text else []
        with mock.patch.object(mapping, "tokenize", stub):
            self.assertTrue(mapping.ends_with_x("你好"))
            self.assertEqual(mapping.han_runs_with_left("你好"), [])
        lm = SimpleNamespace(ids={"<X>": 7}, ctx={7: object()})
        with mock.patch.object(mapping, "tokenize", stub):
            self.assertEqual(harness.start_state("我們", lm, True, True), (None, "<X>"))       # decoder start: <X>
        self.assertEqual(harness.start_state("我們", lm, True, True), ("<s>", "<s>"))              # real tokenizer: no <X>, no usable history
        acc = count.Acc()
        with mock.patch.object(mapping, "tokenize", lambda t: [("x", "1", "<X>"), ("han", t, t)]):
            with mock.patch.object(count, "run_expected3", lambda run, m: ({"我們": 1.0}, {("<s>", "我們"): 1.0, ("我們", "</s>"): 1.0}, {})):
                acc.add_text("我們", 0.01)
        self.assertEqual(acc.nx, 1)                                                            # counting: the run started after an <X>
        self.assertEqual(acc.dbi, {("<s>", "我們"): -1.0, ("<X>", "我們"): 1.0})                    # and (<s>, w) became (<X>, w)


def fake_lexicon(**scores):
    return SimpleNamespace(by_word={w: (None, s) for w, s in scores.items()}, max_len=max(map(len, scores)))


LEX = fake_lexicon(**{"今天": -3.0, "天氣": -3.5, "今": -2.0, "天": -2.0, "氣": -2.5, "很": -2.0, "好": -2.0, "他": -2.0, "說": -2.0, "你好": -4.0,
                      "你": -2.0, "嗎": -2.0, "不錯": -4.0, "不": -2.0, "錯": -2.5, "再見": -4.0, "再": -2.0, "見": -2.0})


class TestCounting(unittest.TestCase):
    def test_trigram_expected_counts_are_consistent_with_bigrams(self):
        for text in ["今天天氣很好", "你好嗎", "他說再見", "不錯"]:
            uni, bi = bc.expected_counts(LEX, text, 0)
            tri = count.expected_tri(LEX, text, 0)
            self.assertAlmostEqual(sum(tri.values()), sum(uni.values()) + 1, delta=1e-9)   # n words -> n + 1 trigrams per path
            by_ctx = collections.defaultdict(float)
            for (a, b, c), e in tri.items():
                by_ctx[(a, b)] += e
            for k, e in bi.items():                                                          # successors of (a, b) sum to the bigram count
                if k[0] != "<s>" and k[1] != "</s>":
                    self.assertAlmostEqual(by_ctx[k], e, delta=1e-9, msg=(text, k))
            for (a, b), e in by_ctx.items():
                if a == "<s>" and b != "<s>":
                    self.assertAlmostEqual(e, bi[("<s>", b)], delta=1e-9)

    def test_prune_threshold_and_unseen_text(self):
        self.assertIsNone(count.expected_tri(LEX, "嗨", 0))
        small = count.expected_tri(LEX, "今天天氣很好", 0.01)
        self.assertTrue(all(e >= 0.01 - 1e-12 for e in small.values()))

    def _setup_bc(self):
        p = mock.patch.object(bc, "load_conv", lambda: ({}, {}, 1)); p.start(); self.addCleanup(p.stop)
        bc._init(False, True, False, ())
        bc._W["lex"] = LEX
        bc._W["tri_min"] = bc.EXPECTED_MIN

    TEXT = ["今天天氣很好。他說12345你好嗎ABC天氣不錯，再見", "<ref>x</ref>你好嗎。"]

    def test_batch_unmapped_unigram_bigram_equal_the_original_counter(self):
        self._setup_bc()
        u0, b0, _, _ = bc.count_batch(self.TEXT)[:4]
        u1, b1, tri, dbi, nx, runs = count.count_batch3(self.TEXT)
        self.assertEqual(dict(u1), dict(u0))
        self.assertEqual(dict(b1), dict(b0))
        self.assertEqual(runs, bc.count_batch(self.TEXT)[3])
        self.assertGreater(len(tri), 0)
        self.assertEqual(nx, 2)                                  # "你好嗎" after 12345 and "天氣不錯" after ABC

    def test_mapped_counts_move_only_the_left_boundary(self):
        self._setup_bc()
        u, b, tri, dbi, nx, runs = count.count_batch3(self.TEXT)
        with tempfile.TemporaryDirectory() as d:
            count.write_outputs(d, "t", u, b, tri, dbi, nx, runs, 0, {})
            un = unpickle(os.path.join(d, "unmapped", "counts-t.pkl"))
            mp = unpickle(os.path.join(d, "mapped", "counts-t.pkl"))
            self.assertNotIn("tri", mp)
            self.assertEqual(sorted(un), ["articles", "bi", "runs", "uni"])                        # the original shape: build_lm never loads the trigram table
        self.assertAlmostEqual(sum(mp["bi"].values()), sum(un["bi"].values()), delta=1e-9)       # mass is moved, not created
        self.assertAlmostEqual(mp["uni"]["<X>"], 2)
        self.assertEqual({k: v for k, v in mp["uni"].items() if k != "<X>"}, dict(un["uni"]))
        moved = mp["bi"]["<X>", "你好"] + mp["bi"]["<X>", "你"]
        self.assertAlmostEqual(moved, 1.0, delta=1e-9)                                           # "你好嗎" starts from <X>
        self.assertAlmostEqual(mp["bi"].get(("<s>", "你好"), 0.0) + mp["bi"].get(("<s>", "你"), 0.0), un["bi"][("<s>", "你好")] + un["bi"][("<s>", "你")] - 1.0, delta=1e-9)
        self.assertGreater(un["bi"][("<s>", "再見")], 0)
        self.assertEqual(mp["bi"][("<s>", "再見")], un["bi"][("<s>", "再見")])                          # after a comma: untouched

    @unittest.skipUnless(os.path.isdir(os.path.join(bc.SRC, "opencc")), "needs the OpenCC tables in ~/.cache/shanjie/sources (the real conversion)")
    def test_wiki_command_end_to_end_on_a_fake_dump(self):
        pages = ["今天天氣很好。我在12345之後去吃飯{{box}}[[學校|學校]]", "<ref>x</ref>他說hello你好嗎，再見。"]
        xml = "".join(f"<page>\n<ns>0</ns>\n<text xml:space=\"preserve\">{t}</text>\n</page>\n" for t in pages)
        with tempfile.TemporaryDirectory() as d:
            dump = os.path.join(d, "fake.xml.bz2")
            with bz2.open(dump, "wt", encoding="utf-8") as f:
                f.write(xml)
            with mock.patch.object(bc, "DUMP", dump):
                count.main(["wiki", "--out", d, "--articles", "2", "--procs", "2"])
            got = unpickle(os.path.join(d, "unmapped", "counts-2.pkl"))
            tri = unpickle(os.path.join(d, "unmapped", "tri-2.pkl"))["tri"]
            mp = unpickle(os.path.join(d, "mapped", "counts-2.pkl"))
            meta = json.loads(slurp(os.path.join(d, "meta-2.json")))
        bc._init(False, True, False, ())                       # the original counter, in this process, with the real lexicon and conversion
        u, b, _, sents = bc.count_batch(pages)
        self.assertEqual(dict(got["uni"]), dict(u)); self.assertEqual(dict(got["bi"]), dict(b))
        self.assertEqual((got["runs"], got["articles"]), (sents, 2))
        self.assertGreater(len(tri), 0)
        self.assertEqual(mp["uni"]["<X>"], 2)                 # "之後去吃飯" after 12345, "你好嗎" after hello
        self.assertEqual(meta["x_runs"], 2)

    def test_pack_words_add_trigrams_like_the_additive_scheme(self):
        """With a pack word (model-v5 §8) the trigram table is lex0's plus the trigrams that contain the pack word."""
        lex0 = fake_lexicon(**{"哈": -2.0, "囉": -2.0, "喵": -2.0})
        lex1 = fake_lexicon(**{"哈": -2.0, "囉": -2.0, "喵": -2.0, "哈囉喵": -1.0})
        with mock.patch.dict(bc._W, {"lex": lex1, "lex0": lex0, "pack": {"哈囉喵"}, "pack_lens": [3]}, clear=True):
            uni, bi, tri = count.run_expected3("哈囉喵", 0)
            self.assertEqual(tri[("<s>", "<s>", "哈囉喵")], tri[("<s>", "哈囉喵", "</s>")])
            self.assertAlmostEqual(tri[("<s>", "<s>", "哈")], 1.0)                                # lex0's one-best path stays
            r = bc.run_expected("哈囉喵")
            self.assertEqual((uni, bi), r)                                                         # uni / bi are bc's own


class TestVariantMergeAndArrays(unittest.TestCase):
    def test_merge_variants_tri(self):
        cls = {"起床": ("起床", "起牀"), "起牀": ("起床", "起牀")}
        tri = {("他", "起牀", "了"): 2, ("他", "起床", "了"): 3, ("起牀", "了", "起牀"): 4, ("甲", "乙", "丙"): 1}
        out = trimodel.merge_variants_tri(dict(tri), cls)
        self.assertEqual(out[("他", "起床", "了")], 5)
        self.assertEqual(out[("他", "起牀", "了")], 5)                   # history words are written to every member
        self.assertEqual(out[("起床", "了", "起床")], 4)                 # the predicted word only on the representative
        self.assertEqual(out[("起牀", "了", "起床")], 4)
        self.assertNotIn(("起牀", "了", "起牀"), out)
        self.assertEqual(out[("甲", "乙", "丙")], 1)

    def test_arrays_sorted_and_unknown_words_dropped(self):
        ids = {"a": 0, "b": 1, "c": 2}
        u, v, w, c = trimodel.tri_arrays({("c", "a", "b"): 3, ("a", "b", "c"): 2, ("a", "b", "zzz"): 9, ("a", "a", "a"): 1}, ids)
        self.assertEqual(list(zip(u, v, w, c)), [(0, 0, 0, 1), (0, 1, 2, 2), (2, 0, 1, 3)])


class TestTrigramModel(Fixture):
    def test_interpolated_probabilities_sum_to_one_over_the_vocabulary(self):
        lm = self.load_lm()
        V = len(lm.vocab)
        pb = 1.0 / V                                               # a normalised lower-order distribution, so the sum measures the trigram level
        ctxs = [("<s>", "<s>"), ("<s>", "今天"), ("我", "去"), ("他", "說"), ("今天", "天氣"), ("學校", "很"), (None, "他"), ("沒", "見過")]
        for t in (1, 2, 3, 5):
            tl = trimodel.TriLM(lm, self.tri_table(t))
            stored = sum(tl._ctx(u, v) >= 0 for u, v in ctxs)
            self.assertGreaterEqual(stored, 4)                     # the contexts exercise stored and not-stored cases
            for u, v in ctxs:
                s = sum(tl.prob(u, v, w, pb) for w in lm.vocab)
                self.assertAlmostEqual(s, 1.0, delta=1e-9, msg=(t, u, v))

    def test_pruning_keeps_pre_prune_context_totals(self):
        full = {}
        for name, w in build_lm.CORPORA:
            for k, c in unpickle(os.path.join(self.counts, name.replace("counts-", "tri-")))["tri"].items():
                full[k] = full.get(k, 0) + c * w
        lex = ime.Lexicon(os.path.join(common.ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.overlays(None))
        self.assertIn("說", build_lm.variant_classes(lex.by_reading))                 # 說 / 説 are one class: contexts are written to both members
        full = trimodel.merge_variants_tri(full, build_lm.variant_classes(lex.by_reading))
        ctx_total = collections.Counter()
        for (a, b, c_), n in full.items():
            ctx_total[(a, b)] += n
        lm = self.load_lm()
        sizes = {}
        for t in (1, 2, 3, 5):
            tab = self.tri_table(t)
            sizes[t] = tab.entries
            self.assertTrue((tab.cnt >= t).all())
            for i in range(tab.contexts):
                key = tuple(lm.vocab[x] for x in tab.context_words(i))
                self.assertEqual(int(tab.tot[i]), ctx_total[key], msg=(t, key))           # pre-prune total, not the kept sum
        self.assertGreaterEqual(sizes[1], sizes[2]); self.assertGreaterEqual(sizes[2], sizes[3]); self.assertGreaterEqual(sizes[3], sizes[5])
        self.assertGreater(sizes[1], sizes[5])
        t5 = self.tri_table(5)
        kept = sum(int(c) for c in t5.cnt)
        self.assertLess(kept, sum(int(x) for x in t5.tot))                               # something was pruned and the totals still count it

    def test_equals_round_ten_class_on_the_same_counts(self):
        """iter2.LM (order 3) is round 10's implementation of the same formula; compare every (u, v, w) with a random lower-order pb."""
        lm = self.load_lm()
        uni, bi, tri = collections.Counter(), collections.Counter(), collections.Counter()
        for name, w in build_lm.CORPORA:
            c = unpickle(os.path.join(self.counts, name))
            t3 = unpickle(os.path.join(self.counts, name.replace("counts-", "tri-")))["tri"]
            for k, v in c["uni"].items(): uni[k] += v * w
            for k, v in c["bi"].items(): bi[k] += v * w
            for k, v in t3.items(): tri[k] += v * w
        for thr in (2, 3):
            ref = iter2.LM({"order": 3, "D": 0.75, "model": "interp"}, uni, bi, tri, {k: v for k, v in bi.items() if v >= 2}, {k: v for k, v in tri.items() if v >= thr})
            tl = trimodel.TriLM(lm, self.tri_table(thr))
            rng = np.random.default_rng(3)
            words = [w for w in lm.vocab]
            for u in words[:12]:
                for v in words[:12]:
                    for w in words[:12]:
                        pb = float(rng.uniform(1e-4, 1e-1))
                        self.assertAlmostEqual(tl.prob(u, v, w, pb), ref._p(v, w, pb, u), delta=1e-12, msg=(thr, u, v, w))

    def test_table_size_formula_is_the_file_size(self):
        d = self.tri_dir
        info = json.loads(slurp(os.path.join(d, "tri-sizes.json")))
        for row in info["tables"]:
            n = os.path.getsize(self.tri_path(row["threshold"]))
            self.assertEqual(n, row["tri_bytes"])
            self.assertEqual(n, sjlm_io.TRI_FIXED + 20 * row["contexts"] + 8 * row["entries"])
            self.assertEqual(row["total_bytes"], row["tri_bytes"] + os.path.getsize(self.lm_path))

    def test_table_is_bound_to_its_model(self):
        with self.assertRaises(ValueError):
            trimodel.TriTable.from_path(self.tri_path(1), b"\0" * 32)

    def test_pick_thresholds_gives_at_least_four(self):
        sizes = {t: (0, 0, int(1e9 / t ** 2)) for t in range(1, 40)}
        for targets in ([100, 200, 400], [1000, 1000, 1000], [1, 2, 3]):
            p = build_models.pick_thresholds(sizes, 80e6, targets)
            self.assertGreaterEqual(len(p), 4); self.assertEqual(p, sorted(set(p))); self.assertIn(1, p)


class TestDecoding(Fixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base = ime.Lexicon(os.path.join(common.ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.overlays(None))
        cls.lm = L.BigramLM(cls.lm_path, classes=False)
        cls.lex = L.cap_overlay(cls.base, ime.capping_words(None), cls.lm, demote_path=None)

    def test_null_configuration_is_lm_decode(self):
        for text, syl in SYLS.items():
            for start, ctx in ((("<s>", "<s>"), "<s>"), ((None, "他"), "他"), ((None, "學校"), "學校")):
                ref = L.decode(self.lex, syl.split(), self.lm, "chat", start=ctx)
                got = harness.decode3(self.lex, syl.split(), self.lm, 0.5, start, None)
                self.assertEqual(got, ref, msg=(text, start))
                got2 = harness.decode3(self.lex, syl.split(), self.lm, 0.5, start, trimodel.TriLM(self.lm, None))
                self.assertEqual(got2[0], ref[0], msg=(text, start))                          # a TriLM without a table: same best candidate and score
                                                                                              # (the recombination key is the two-word one, so the beam holds more paths)

    def test_state_carries_two_words(self):
        """Every candidate's score is the sum of trigram word scores along its own path (rescored independently) plus the end term."""
        tl = trimodel.TriLM(self.lm, self.tri_table(1))
        differs = False
        for text, syl in SYLS.items():
            start = ("<s>", "<s>")
            nb = harness.decode3(self.lex, syl.split(), self.lm, 0.5, start, tl)
            ref = {ws: s for s, ws in L.decode(self.lex, syl.split(), self.lm, "chat")}
            for score, ws in nb:
                hist = ["<s>", "<s>", *ws]
                tot = 0.0
                pos = 0
                for i, w in enumerate(ws):
                    lp = next(lp for ww, lp in self.lex.by_reading[tuple(syl.split()[pos:pos + len(w)])] if ww == w)
                    pos += len(w)
                    tot += tl.word(0.5, hist[i], hist[i + 1], w, lp)
                tot += tl.eos(0.5, hist[-2], hist[-1])
                self.assertAlmostEqual(score, tot, delta=1e-9, msg=(text, ws))
                differs |= ws in ref and abs(ref[ws] - score) > 1e-9
        self.assertTrue(differs)                                                                  # the trigram level really changes scores

    def test_context_start_skips_the_trigram_level_for_the_first_word(self):
        tl = trimodel.TriLM(self.lm, self.tri_table(1))
        self.assertEqual(harness.start_state("", self.lm), ("<s>", "<s>"))
        self.assertEqual(harness.start_state("我們他", self.lm), (None, "他"))
        # u = None: the first word is scored by B0 alone
        self.assertEqual(tl.word(0.5, None, "他", "說", -2.9), self.lm.word(0.5, "他", "說", -2.9))

    def test_x_context_start_state(self):
        mapped = L.BigramLM(os.path.join(self.b0m, "bigram.sjlm"), classes=False)
        self.assertEqual(harness.start_state("現在Windows", mapped, True, True), (None, "<X>"))
        self.assertEqual(harness.start_state("快1.5", mapped, True, True), (None, "<X>"))
        self.assertEqual(harness.start_state("約，", mapped, True, True), ("<s>", "<s>"))          # a comma is not an <X>
        self.assertEqual(harness.start_state("現在Windows", mapped, True, False), ("<s>", "<s>"))  # off: the old rule (no Han tail -> <s>)
        self.assertEqual(harness.start_state("現在Windows", mapped, False, True), ("<s>", "<s>"))  # --context off
        with self.assertRaises(ValueError):
            harness.Evaluator(self.lm_path, classes=False, x_context=True)                          # the unmapped model has no <X>
        ev = harness.Evaluator(os.path.join(self.b0m, "bigram.sjlm"), classes=False, x_context=True)
        self.assertGreater(len(ev.row("快1.5", ["ㄅㄟˋ"], 0.5)), 0)
        ev0 = harness.Evaluator(os.path.join(self.b0m, "bigram.sjlm"), classes=False, x_context=False)
        syl = "ㄋㄧˇ ㄏㄠˇ ㄇㄚ".split()
        on, off = ev.row("快1.5", syl, 0.5), ev0.row("快1.5", syl, 0.5)
        self.assertEqual(on[0][1], ("你好", "嗎"))
        self.assertGreater(on[0][0], off[0][0])                                                   # <X> as the history word really changes the first word's score

    def test_beta_override_reaches_trigram_scores_and_can_be_undone(self):
        ev = harness.Evaluator(self.lm_path, tri_path=self.tri_path(1), classes=False)
        w = lambda: ev.tri_lm.word(0.5, "<s>", "<s>", "他", -2.7)
        base = w()
        ev.set_beta(0.5)
        self.assertNotEqual(w(), base)                                                            # the word-score cache was cleared with the beta
        ev.set_beta(None)
        self.assertEqual(w(), base)


class TestCliAndAnchor(Fixture):
    def test_anchor_null_configuration_on_synthetic_model(self):
        with tempfile.TemporaryDirectory() as w:
            bad, lines = anchor.check(self.lm_path, ["chat", "formal"], ["--rows", self.rows, "--context", "--no-classes"], w)
        self.assertEqual(bad, [])
        self.assertEqual(len(lines), 2)
        self.assertIn("+kn:", lines[0][1])                          # the KN side file next to the model was used (kn-v1 style, beta 1)

    def test_anchor_detects_a_one_character_difference(self):
        line = "## x  lm-chat+ctx+kn:cbefbced:θ1:β1  {'n': 20, 'top1': 8, 'oracle@64': 19, 'top1_sha256': '6eb3e756'}\n"
        self.assertEqual(anchor.compare_lines(line, line), [])
        for i in (3, len(line) // 2, len(line) - 3):
            other = line[:i] + ("0" if line[i] != "0" else "1") + line[i + 1:]
            self.assertNotEqual(anchor.compare_lines(line, other), [])
        self.assertNotEqual(anchor.compare_lines(line, line[:-1]), [])

    def test_summary_tags_only_when_not_null(self):
        base = lambda **k: harness.summary_line("n", "chat", 1, 1, 1, "s", kn_path=os.path.join(self.b0, "kn.sjkn"), **k)
        self.assertNotIn("+lam", base(lam=0.5)); self.assertNotIn("+beta", base(beta=1.0))
        self.assertIn("+lam:0.45", base(lam=0.45)); self.assertIn("+beta:0.5", base(beta=0.5)); self.assertIn("+xctx", base(x_context=True))

    def test_trigram_cli_runs_and_rowstats_has_only_digits(self):
        with tempfile.TemporaryDirectory() as w:
            rs = os.path.join(w, "r.rs")
            out = run_main(harness.main, ["--lm", self.lm_path, "--profile", "chat", "--rows", self.rows, "--context", "--no-classes", "--tri", self.tri_path(2),
                                          "--lam", "0.45", "--beta", "0.5", "--rowstats", rs])
            self.assertIn("+tri:", out); self.assertIn("+lam:0.45", out); self.assertIn("+beta:0.5", out)
            self.assertRegex(slurp(rs), r"\A[0-9\t\n]+\Z")


class TestRowstatsAndGrid(Fixture):
    @staticmethod
    def write(path, text):
        with open(path, "w") as f:
            f.write(text)

    def test_concat_renumbers_and_orders(self):
        a, b = [(1, 0, 3), (0, 2, 4)], [(1, 0, 5)]
        self.assertEqual(rowstats_concat.concat_rows([a, b]), "1\t1\t0\t3\n2\t0\t2\t4\n3\t1\t0\t5\n")
        self.assertEqual(rowstats_concat.JUDGE5, ("dev302", "typing76", "user-reported", "lytune", "wikitypo"))
        self.assertEqual(rowstats_concat.GROUPS["all"], ("cvtune", "wikitune") + rowstats_concat.JUDGE5)
        with tempfile.TemporaryDirectory() as d:
            for arm in ("base", "cand"):
                os.makedirs(os.path.join(d, arm))
                for i, n in enumerate(rowstats_concat.JUDGE5):
                    self.write(os.path.join(d, arm, f"{n}.rowstats"), f"1\t1\t0\t{i + 3}\n2\t0\t1\t{i + 3}\n")
                rowstats_concat.main(["--dir", os.path.join(d, arm), "--group", "judge5", "--out", os.path.join(d, arm, "j.rowstats")])
            x, y = (slurp(os.path.join(d, arm, "j.rowstats")) for arm in ("base", "cand"))
            self.assertEqual(x, y)
            self.assertEqual(len(x.splitlines()), 10); self.assertEqual(x.splitlines()[-1].split("\t")[0], "10")
            self.assertEqual([l.split("\t")[3] for l in x.splitlines()[::2]], ["3", "4", "5", "6", "7"])    # contract order

    def test_subset_rows_are_the_contexts_with_punctuation_digits_or_english(self):
        ctxs = ["", "一次付清，另外", "快1.5", "現在Windows", "約，", "你好。", "他說", "管理費10/10", "希莉卡·奇希里斯，", "只有中文"]
        rows = [("甲", ["ㄅ"], c) for c in ctxs]
        self.assertEqual(subset_rowstats.subset_rows(rows), [2, 3, 4, 5, 8, 9])
        with tempfile.TemporaryDirectory() as d:
            self.write(os.path.join(d, "s.rowstats"), "".join(f"{i}\t{i % 2}\t0\t{i}\n" for i in range(1, 11)))
            self.write(os.path.join(d, "rows.txt"), "".join(f"{c}|甲|ㄅ\n" for c in ctxs))
            subset_rowstats.main(["--arm", d, "--set", f"s={os.path.join(d, 'rows.txt')}", "--out", os.path.join(d, "o.rowstats")])
            got = slurp(os.path.join(d, "o.rowstats"))
        self.assertEqual(got, "".join(f"{k}\t{n % 2}\t0\t{n}\n" for k, n in enumerate([2, 3, 4, 5, 8, 9], 1)))

    def test_grid_has_15_points_with_the_shipped_one(self):
        for prof, lam0, lams in (("chat", 0.5, [0.4, 0.45, 0.5, 0.55, 0.6]), ("formal", 0.7, [0.6, 0.65, 0.7, 0.75, 0.8])):
            pts = grid.grid_points(prof)
            self.assertEqual(len(pts), 15)
            self.assertEqual(sorted({p[0] for p in pts}), lams)
            self.assertEqual(sorted({p[1] for p in pts}), [0.5, 0.75, 1.0])
            self.assertIn((L.PROFILES[prof], 1.0), pts)

    def test_selection_and_tie_break(self):
        pts = grid.grid_points("chat")
        self.assertEqual(grid.choose({p: 10 for p in pts}, "chat"), (0.5, 1.0))                     # all tied: the shipped point
        scores = {p: 10 for p in pts}
        scores[(0.4, 1.0)] = scores[(0.55, 0.75)] = scores[(0.45, 1.0)] = 12                         # three-way tie at the top
        self.assertEqual(grid.choose(scores, "chat"), (0.45, 1.0))                                   # nearest the shipped point (1 step vs 2)
        scores[(0.6, 0.5)] = 13
        self.assertEqual(grid.choose(scores, "chat"), (0.6, 0.5))                                    # a strict maximum wins whatever the distance

    def test_parallel_equals_serial_and_judge_equals_the_cli(self):
        rows, _ = harness.load_rows(rows=self.rows)
        kw = dict(tri_path=self.tri_path(2), classes=False, context=True)
        jobs = {"a": (0.5, 1.0, rows), "b": (0.45, 0.5, rows)}
        serial = grid.decode_points(self.lm_path, kw, jobs, 1, chunk=2)
        par = grid.decode_points(self.lm_path, kw, jobs, 2, chunk=2)
        self.assertEqual(serial, par)
        with tempfile.TemporaryDirectory() as d:
            grid.main(["select", "--label", "T2", "--lm", self.lm_path, "--tri", self.tri_path(2), "--no-classes", "--profile", "chat",
                       "--set", f"tiny={self.rows}", "--procs", "1", "--out", d])
            sel = json.loads(slurp(os.path.join(d, "T2-chat-selection.json")))
            self.assertEqual(len(sel["scores"]), 15)
            self.assertEqual(sel["top1"], max(s["top1"] for s in sel["scores"]))
            self.assertEqual(sel["tuning_sets"]["tiny"]["n"], len(rows))
            grid.main(["judge", "--label", "B0", "--lm", self.lm_path, "--no-classes", "--profile", "chat", "--lam", "0.5", "--beta", "1",
                       "--set", f"tiny={self.rows}", "--procs", "1", "--out", d])
            ref = os.path.join(d, "ref.rowstats")
            line = run_main(lm_eval.main, ["--lm", self.lm_path, "--profile", "chat", "--rows", self.rows, "--context", "--no-classes", "--rowstats", ref])
            self.assertEqual(slurp(os.path.join(d, "B0", "chat", "tiny.rowstats")), slurp(ref))   # shipped point = lm_eval.py, bit for bit
            self.assertEqual(slurp(os.path.join(d, "B0", "chat", "tiny.summary")).replace("## tiny", "## rows.txt"), line)


class TestCompression(Fixture):
    def test_varint_round_trip(self):
        a = np.array([0, 1, 127, 128, 16383, 16384, 2 ** 32, 2 ** 63, 2 ** 64 - 1] + list(range(0, 5000, 7)), dtype=np.uint64)
        enc = compress.varint_encode(a)
        self.assertTrue((compress.varint_decode(enc, len(a)) == a).all())
        self.assertEqual(compress.varint_encode(np.zeros(0)), b"")
        with self.assertRaises(ValueError):
            compress.varint_decode(enc, len(a) + 1)

    def test_lossless_round_trip_is_byte_identical(self):
        model = slurp(self.lm_path, "rb")
        for t in (1, 3):
            tri = slurp(self.tri_path(t), "rb")
            m2, t2 = compress.decode(compress.encode(model, tri, 0))
            self.assertEqual(m2, model); self.assertEqual(t2, tri)
        m2, t2 = compress.decode(compress.encode(model, None, 0))
        self.assertEqual((m2, t2), (model, None))

    def test_lossy_levels_are_monotone_and_decoded_values_stay_in_their_level(self):
        rng = np.random.default_rng(5)
        cnt = np.concatenate([rng.integers(2, 40, 3000), (rng.pareto(0.8, 5000) * 3 + 2).astype(np.int64), [2 ** 31]])
        for bits in (8, 4):
            reps = compress.make_levels(cnt, bits)
            self.assertLessEqual(len(reps), 1 << bits)
            self.assertTrue((np.diff(reps.astype(np.int64)) > 0).all())                              # strictly increasing levels
            allc = np.arange(int(cnt.min()), 5000)
            idx = compress.level_index(allc, reps)
            self.assertTrue((np.diff(idx) >= 0).all())                                               # monotone in the count
            decoded = reps[compress.level_index(cnt, reps)]
            self.assertTrue((compress.level_index(decoded, reps) == compress.level_index(cnt, reps)).all())   # decoded value is in the original's level
            self.assertTrue((compress.level_index(reps, reps) == np.arange(len(reps))).all())
        few = np.array([2, 2, 5, 9])
        self.assertEqual(list(compress.make_levels(few, 4)), [2, 5, 9])                              # fewer distinct counts than levels: lossless

    @staticmethod
    def synthetic_model(rng, V=300, C=120, per=40):
        words = ["<s>", "</s>"] + [f"w{i}" for i in range(V - 2)]
        vocab_raw = b"".join(struct.pack("<H", len(w.encode())) + w.encode() for w in words)
        ctx = np.sort(rng.choice(V, C, replace=False)).astype(np.uint32)
        nxt = np.concatenate([np.sort(rng.choice(V, per, replace=False)) for _ in range(C)]).astype(np.uint32)
        cnt = np.minimum(rng.pareto(0.7, C * per) * 2 + 2, 2 ** 31).astype(np.uint32)
        off = (np.arange(C + 1) * per).astype(np.uint32)
        tot = np.add.reduceat(cnt.astype(np.uint64), off[:-1].astype(np.int64)) + 3      # every kept count is inside the total, barely
        uni = rng.integers(1, 1000, V).astype(np.uint64)
        return sjlm_io.write_sjlm(dict(V=V, N=int(uni.sum()), eos_total=77, D=0.75, vocab_raw=vocab_raw, uni=uni, ctx=ctx, tot=tot, off=off, nxt=nxt, cnt=cnt))

    def test_lossy_on_a_model_with_many_distinct_counts(self):
        model = self.synthetic_model(np.random.default_rng(11))
        orig = sjlm_io.read_sjlm(model)
        self.assertEqual(compress.decode(compress.encode(model, None, 0))[0], model)
        sizes = {}
        for bits in (8, 4):
            blob = compress.encode(model, None, bits)
            sizes[bits] = len(blob)
            dec = sjlm_io.read_sjlm(compress.decode(blob)[0])
            reps = compress.make_levels(orig["cnt"], bits)
            self.assertGreater(len(np.unique(orig["cnt"])), 1 << bits)                              # really lossy
            self.assertTrue((dec["nxt"] == orig["nxt"]).all() and (dec["ctx"] == orig["ctx"]).all() and (dec["off"] == orig["off"]).all())
            self.assertTrue((compress.level_index(dec["cnt"], reps) == compress.level_index(orig["cnt"], reps)).all())
            self.assertTrue(np.isin(dec["cnt"], reps).all())                                         # decoded values are level representatives
            self.assertTrue((dec["tot"] >= orig["tot"]).all())
            kept = np.add.reduceat(dec["cnt"].astype(np.float64) - 0.75, dec["off"][:-1].astype(np.int64)) / dec["tot"].astype(np.float64)
            self.assertTrue((kept < 1.0).all())                                                      # back-off weight stays positive
            if bits == 4:
                self.assertTrue((dec["tot"] > orig["tot"]).any())                                    # the fix-up was needed somewhere
        self.assertLess(sizes[4], sizes[8])

    def test_pack_unpack(self):
        idx = np.array([0, 15, 3, 7, 9], dtype=np.uint8)
        self.assertEqual(list(compress.unpack_idx(compress.pack_idx(idx, 4), 5, 4)), list(idx))
        self.assertEqual(list(compress.unpack_idx(compress.pack_idx(idx, 8), 5, 8)), list(idx))

    def test_lossy_decode_loads_and_scores(self):
        model = slurp(self.lm_path, "rb")
        tri = slurp(self.tri_path(1), "rb")
        for bits in (8, 4):
            m2, t2 = compress.decode(compress.encode(model, tri, bits))
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, "bigram.sjlm")
                for name, data in (("bigram.sjlm", m2), ("tri.sjt3", t2), ("kn.sjkn", compress.rebind(slurp(os.path.join(self.b0, "kn.sjkn"), "rb"), m2, 12))):
                    with open(os.path.join(d, name), "wb") as f:
                        f.write(data)
                ev = harness.Evaluator(p, tri_path=os.path.join(d, "tri.sjt3"), classes=False, context=True)   # loads: hashes re-bound
                for text, syl in SYLS.items():
                    nb = ev.row("", syl.split(), 0.5)
                    self.assertTrue(all(math.isfinite(s) for s, _ in nb))
                for c in ev.lm.ctx.values():
                    self.assertGreaterEqual(c[1], 0.0)                                             # back-off weights stay >= 0 after the totals fix-up
                self.assertTrue(all(ev.tri.back(i) > 0 for i in range(ev.tri.contexts)))
            orig = sjlm_io.read_sjlm(model)
            dec = sjlm_io.read_sjlm(m2)
            self.assertTrue((dec["nxt"] == orig["nxt"]).all())                                       # ids exact
            self.assertTrue((dec["uni"] == orig["uni"]).all())
            reps = compress.make_levels(orig["cnt"], bits)
            self.assertTrue((compress.level_index(dec["cnt"], reps) == compress.level_index(orig["cnt"], reps)).all())

    def test_totals_fix_up_when_rounding_overshoots(self):
        tot = np.array([10, 10], dtype=np.uint64)
        off = np.array([0, 2, 3], dtype=np.uint32)
        cnt = np.array([6, 6, 3], dtype=np.uint32)                                                 # context 0 now sums to 12 > 10
        self.assertEqual(list(compress.fix_totals(tot, off, cnt)), [12, 10])

    def test_zip_report_has_all_modes(self):
        model = slurp(self.lm_path, "rb")
        r = compress.report(model, slurp(self.tri_path(1), "rb"))
        self.assertEqual(sorted(r), ["lossless", "q4", "q8", "raw"])
        self.assertTrue(all(v["bytes"] > 0 and v["zip"] > 0 for v in r.values()))


if __name__ == "__main__":
    unittest.main()
