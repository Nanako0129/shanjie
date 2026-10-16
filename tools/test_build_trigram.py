"""tools/build_trigram.py and reference/proto/trigram.py (docs/contracts/trigram-core.md). Run: python3 -B -m unittest tools/test_build_trigram.py

The remap tests use two tiny bigram models holding the same words under permuted ids; the scoring tests use the kn-tiny toy model and the committed
tri-tiny table with hand values (the numbers core/tests/tri_tiny.rs and the lm.rs unit tests check on the Rust side); the golden test checks that
eval/golden/tri-tiny/ is what tools/gen_tri_tiny.py writes now."""
import hashlib
import math
import os
import struct
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for sub in ("tools", "reference/proto"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import build_trigram  # noqa: E402
import gen_tri_tiny  # noqa: E402
import lm as L  # noqa: E402
import trigram  # noqa: E402

WORDS = ["<s>", "</s>", "我", "去", "學校", "吃", "飯", "他", "說"]
TRI = {("<s>", "<s>", "我"): 5, ("<s>", "我", "去"): 4, ("我", "去", "學校"): 3, ("我", "去", "吃"): 2, ("去", "吃", "飯"): 2,
       ("<s>", "<s>", "他"): 3, ("<s>", "他", "說"): 3, ("他", "說", "我"): 2, ("說", "我", "去"): 2}


def mk_sjlm(vocab):
    """A minimal SJLM holding no bigrams (only the vocabulary matters to the remap)."""
    raw = b"".join(struct.pack("<H", len(w.encode())) + w.encode() for w in vocab)
    return b"SJLM0001" + struct.pack("<IQQd", len(vocab), 1, 1, 0.75) + raw + struct.pack(f"<{len(vocab)}Q", *([0] * len(vocab))) \
        + struct.pack("<I", 0) + struct.pack("<I", 0) + struct.pack("<I", 0)


def totals_of(entries):
    t = {}
    for (u, v, _), c in entries.items():
        t[(u, v)] = t.get((u, v), 0) + c + 1   # one pruned count per context
    return t


def triples(tri, vocab):
    """{(u, v, w) strings: count} and {(u, v): pre-prune total}."""
    V, c3, t3 = len(vocab), {}, {}
    for i, k in enumerate(tri["key"]):
        u, v = divmod(int(k), V)
        t3[(vocab[u], vocab[v])] = int(tri["tot"][i])
        for j in range(tri["off"][i], tri["off"][i + 1]):
            c3[(vocab[u], vocab[v], vocab[int(tri["nxt"][j])])] = int(tri["cnt"][j])
    return c3, t3


class Remap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sv = WORDS
        cls.dv = WORDS[:2] + ["說", "他", "飯", "吃", "學校", "去", "我"]   # same words, other ids
        cls.src, cls.dst = mk_sjlm(cls.sv), mk_sjlm(cls.dv)
        cls.tri = build_trigram.from_counts(TRI, totals_of(TRI), cls.src)
        cls.out, _, _ = build_trigram.remap(cls.src, cls.tri, cls.dst)

    def test_counts_and_totals_preserved(self):
        a, b = trigram.read_tri(self.tri), trigram.read_tri(self.out)
        self.assertEqual(triples(a, self.sv), triples(b, self.dv))
        self.assertEqual(triples(b, self.dv), (TRI, totals_of(TRI)))
        self.assertEqual((len(a["key"]), len(a["nxt"])), (len(b["key"]), len(b["nxt"])))

    def test_sorted_and_bound_to_the_destination(self):
        b = trigram.read_tri(self.out)
        self.assertTrue((np.diff(b["key"].astype(np.int64)) > 0).all())
        for i in range(len(b["key"])):
            self.assertTrue((np.diff(b["nxt"][b["off"][i]:b["off"][i + 1]].astype(np.int64)) > 0).all())
        self.assertEqual((b["V"], b["model_sha"]), (len(self.dv), hashlib.sha256(self.dst).digest()))
        self.assertNotEqual(trigram.read_tri(self.tri)["model_sha"], b["model_sha"])

    def test_refusals(self):
        with self.assertRaises(ValueError):   # the table is bound to SRC, not to DST
            build_trigram.remap(self.dst, self.tri, self.src)
        with self.assertRaises(ValueError):   # a SRC word missing from DST
            build_trigram.remap(self.src, self.tri, mk_sjlm([w for w in self.dv if w != "吃"]))
        with self.assertRaises(ValueError):   # a total below the kept counts
            build_trigram.from_counts(TRI, {k: 1 for k in totals_of(TRI)}, self.src)
        with tempfile.TemporaryDirectory() as tmp:   # the command line stops with a message and writes nothing
            for name, b in (("src", self.src), ("tri", self.tri), ("dst", mk_sjlm([w for w in self.dv if w != "吃"]))):
                open(os.path.join(tmp, name), "wb").write(b)
            args = [f"--{k}-lm" if k != "tri" else "--tri" for k in ("src", "tri", "dst")]
            with self.assertRaises(SystemExit):
                build_trigram.main(sum(([a, os.path.join(tmp, n)] for a, n in zip(args, ("src", "tri", "dst"))), []) + ["--out", os.path.join(tmp, "out")])
            self.assertFalse(os.path.exists(os.path.join(tmp, "out")))


class Scoring(unittest.TestCase):
    """Hand values on the kn-tiny model (no classes) and the committed table."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(gen_tri_tiny.KN_TINY, "bigram.sjlm")
        cls.lm = L.BigramLM(path, classes=False)
        cls.tri = trigram.TriLM(cls.lm, trigram.TriTable.from_path(os.path.join(gen_tri_tiny.OUT, "trigram.sjt3"),
                                                                   hashlib.sha256(open(path, "rb").read()).digest()))

    def test_four_situations(self):
        lm, tri, lp, D = self.lm, self.tri, -3.0, 0.75
        pb = 10 ** lp
        # kept entry: (<s>, 他) -> 占 3 of 8, kept 3 + 1 + 2
        back3 = 1.0 - ((3 - D) + (1 - D) + (2 - D)) / 8
        self.assertEqual(tri.word(0.7, "<s>", "他", "占", lp), 0.7 * math.log10((3 - D) / 8 + back3 * lm.prob("他", "占", pb)) + (1 - 0.7) * lp)
        # the context is stored, the entry is not: (<s>, 他) -> 起牀
        self.assertEqual(tri.word(0.7, "<s>", "他", "起牀", lp), 0.7 * math.log10(back3 * lm.prob("他", "起牀", pb)) + (1 - 0.7) * lp)
        # the context is not stored: (占, 起床) -> 占, and u unknown: (?, 他) -> 占; both are the bigram term
        self.assertEqual(tri.word(0.7, "占", "起床", "占", lp), lm.word(0.7, "起床", "占", lp))
        self.assertEqual(tri.word(0.7, None, "他", "占", lp), lm.word(0.7, "他", "占", lp))
        # the closing term: (他, 占) -> </s> 2 of 9
        back3 = 1.0 - (2 - D) / 9
        self.assertEqual(tri.eos(0.7, "他", "占"), 0.7 * math.log10((2 - D) / 9 + back3 * lm.prob("占", "</s>", lm.p_eos)))
        self.assertEqual(tri.eos(0.7, None, "占"), lm.eos(0.7, "占"))

    def test_start_state(self):
        self.assertEqual(trigram.start_state("<s>"), ("<s>", "<s>"))
        self.assertEqual(trigram.start_state("他"), (None, "他"))


class Golden(unittest.TestCase):
    def test_committed_files_are_what_the_generator_writes(self):
        for name, b in gen_tri_tiny.generate().items():
            self.assertEqual(open(os.path.join(gen_tri_tiny.OUT, name), "rb").read(), b, name)


if __name__ == "__main__":
    unittest.main()
