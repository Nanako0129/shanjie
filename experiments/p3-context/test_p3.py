"""P3 unit tests (synthetic data only; numpy only, the torch import of train_p2 is optional):
python3 -m unittest discover -s experiments/p3-context -p 'test_*.py' -v"""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import score_p3 as sp   # noqa: E402  (also puts s7a-tinylm and p2-distill on sys.path)
import score            # noqa: E402
import train_p2 as tp   # noqa: E402
import tinylm           # noqa: E402
from tinylm import BOS, CTX, NumpyLM, ll_many  # noqa: E402

CHARS = list("你我他好嗎的是不了天氣說今真")
PREFIXES = ["", "你好", "他說，我", "今天天氣。好a1 嗎", "，。", "是" * 70, "他說 好,的"]


def tiny_npz(td):
    path = os.path.join(td, "m.npz")
    tinylm.save_params(path, tinylm.random_params(CHARS, 2, 32, 2, seed=0), CHARS, 2, 2)
    return path


def tiny_rows():
    """rows [(gold, syl, prefix)] and dump cands; one candidate of 62 characters, so k is cut to 1 at context 64."""
    cands = [[("好嗎", -1.0), ("你我", -2.0)], [("天氣", -1.0), ("今真", -1.5), ("他說", -3.0)], [("好" * 62, -1.0), ("你", -2.0)],
             [("我", -1.0)], [("是不了", -1.0), ("嗎的", -2.0)], [("你好", -1.0), ("你我", -1.2)], [("說", -1.0), ("真", -1.1)]]
    return [(cs[-1][0], ["ㄋ"], PREFIXES[i % len(PREFIXES)]) for i, cs in enumerate(cands)], cands


class TestKCross(unittest.TestCase):
    def test_skips_non_han(self):
        self.assertEqual(tp.k_cross("他說，我", 3), "他說我")
        self.assertEqual(tp.k_cross("今天a1 氣。好", 4), "今天氣好")
        self.assertEqual(tp.k_cross("abc，。 1", 5), "")
        self.assertEqual(tp.k_cross("你好", 10), "你好")
        self.assertEqual(tp.k_cross("你好", 1), "好")

    def test_nonpositive(self):
        self.assertEqual(tp.k_cross("你好", 0), "")
        self.assertEqual(tp.k_cross("你好", -3), "")


class TestScore(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.model = NumpyLM(tiny_npz(self.td.name))
        self.rows, self.cands = tiny_rows()

    def tearDown(self):
        self.td.cleanup()

    def test_context2_equals_score_py(self):
        want = score.build_records(self.model, self.rows, self.cands)
        got = sp.build_records(self.model, self.rows, self.cands, 2)
        for w, g in zip(want, got):
            for f in ("i", "n", "ng", "ok", "ed", "glen", "key", "key16", "ll", "ll0"):
                self.assertEqual(w.get(f), g.get(f), f)
        self.assertEqual(got[3].get("ll"), None)          # n = 1: no ll

    def test_context64_uses_cross_key_and_truncates(self):
        got = sp.build_records(self.model, self.rows, self.cands, 64)
        for r, (_g, _s, prefix), cs in zip(got, self.rows, self.cands):
            if len(cs) < 2:
                continue
            want = ll_many(self.model, [(tp.k_cross(prefix, CTX - 1 - len(s)), s) for s, _ in cs])
            np.testing.assert_allclose(r["ll"], want, rtol=0, atol=1e-9)   # batch composition differs: float noise only
            self.assertEqual(r["key"], len(tp.k_cross(prefix, CTX - 1 - len(cs[0][0]))))
        self.assertEqual((got[0]["key"], got[1]["key"]), (0, 2))     # prefixes "" and "你好"
        # row 2 (prefix "他說，我"): the 62-character candidate leaves room for 1 character, the 2-character one for the whole key
        self.assertEqual(tp.k_cross(PREFIXES[2], CTX - 1 - 62), "我")
        self.assertEqual(tp.k_cross(PREFIXES[2], CTX - 1 - 1), "他說我")
        self.assertEqual(got[2]["key"], 1)
        a = sp.build_records(self.model, self.rows, self.cands, 2)
        self.assertEqual(a[1]["ll"], got[1]["ll"])         # prefix "你好": the same key either way
        self.assertNotEqual(a[5]["ll"], got[5]["ll"])      # prefix of 70 Han characters: 2 vs 61 characters of context
        self.assertEqual(a[5]["ll0"], got[5]["ll0"])


    def test_strict_ok(self):
        rows = [("他", ["ㄊ"], ""), ("你", ["ㄋ"], "")]
        cands = [[("他", -1.0), ("她", -2.0)], [("妳", -1.0), ("你", -2.0)]]
        r = sp.build_records(self.model, rows, cands, 2)
        self.assertEqual((r[0]["ok"], r[0]["oks"]), ([1, 1], [1, 0]))     # lenient: 她 counts as 他
        self.assertEqual((r[1]["ok"], r[1]["oks"]), ([1, 1], [0, 1]))

    def test_main_writes_jsonl(self):
        td = self.td.name
        rp, dp, out = (os.path.join(td, n) for n in ("r.txt", "d.dump", "o.jsonl"))
        with open(rp, "w", encoding="utf-8") as f:
            for g, _s, p in self.rows:
                f.write(f"{p}|{g}|ㄋ\n")
        with open(dp, "w", encoding="utf-8") as f:
            for i, cs in enumerate(self.cands, 1):
                for k, (s, sc) in enumerate(cs, 1):
                    f.write(f"{i}\t{k}\t{s}\t{sc}\n")
        t1 = score.top1_from_dump(score.load_rows(rows=rp), score.load_dump(open(dp, encoding="utf-8"), len(self.rows)))
        base = ["--npz", os.path.join(td, "m.npz"), "--dump", dp, "--rows", rp, "--name", "t", "--out", out]
        with self.assertRaises(SystemExit):
            sp.main(base + ["--expect-top1", str(t1 + 1)])
        sp.main(base + ["--expect-top1", str(t1), "--context", "64"])
        recs = [json.loads(line) for line in open(out, encoding="utf-8")]
        self.assertEqual(len(recs), len(self.rows))
        self.assertTrue(all("oks" in r and "ll" in r for r in recs if r["n"] >= 2))


def old_packed(items, vocab):
    """train_p2.Packed.__init__ as it was before --context: (tok, off, st)."""
    tok, off, st = [], [0], []
    for prefix, surfs, _ll in items:
        key = tinylm.row_key(prefix, 2)
        for c in surfs:
            k = key[max(0, len(key) - (CTX - 1 - len(c))):]
            tok.extend([BOS] + vocab.enc(k) + vocab.enc(c))
            off.append(len(tok)); st.append(1 + len(k))
    return np.array(tok, dtype=np.int32), np.array(off), np.array(st)


class TestPacked(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.vocab = NumpyLM(tiny_npz(self.td.name)).vocab
        _rows, cands = tiny_rows()
        self.items = [(PREFIXES[i % len(PREFIXES)], [s for s, _ in cs], [-float(j) for j in range(len(cs))])
                      for i, cs in enumerate(cands) if len(cs) >= 2]

    def tearDown(self):
        self.td.cleanup()

    def test_context2_is_unchanged(self):
        tok, off, st = old_packed(self.items, self.vocab)
        for P in (tp.Packed(self.items, self.vocab), tp.Packed(self.items, self.vocab, 2)):
            np.testing.assert_array_equal(P.tok, tok)
            np.testing.assert_array_equal(P.off, off)
            np.testing.assert_array_equal(P.st, st)

    def test_context64_uses_k_cross(self):
        P = tp.Packed(self.items, self.vocab, 64)
        s = 0
        for prefix, surfs, _ll in self.items:
            for c in surfs:
                k = tp.k_cross(prefix, CTX - 1 - len(c))
                self.assertEqual(list(P.tok[P.off[s]:P.off[s + 1]]), [BOS] + self.vocab.enc(k) + self.vocab.enc(c))
                self.assertEqual(P.st[s], 1 + len(k))
                self.assertLessEqual(P.off[s + 1] - P.off[s], CTX)
                s += 1
        self.assertTrue((P.st != tp.Packed(self.items, self.vocab, 2).st).any())


if __name__ == "__main__":
    unittest.main()
