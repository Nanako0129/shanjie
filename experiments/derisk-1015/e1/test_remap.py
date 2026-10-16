"""remap_tri.py on synthetic tiny models (SRC and DST hold the same counts under permuted word ids)."""
import hashlib
import os
import struct
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common  # noqa: E402,F401
import remap_tri  # noqa: E402
import sjlm_io  # noqa: E402
import trimodel  # noqa: E402
import lm as L  # noqa: E402

WORDS = ["<s>", "</s>", "我", "去", "學校", "吃", "飯", "他", "說"]
BI = {("<s>", "我"): 5, ("我", "去"): 4, ("去", "學校"): 3, ("去", "吃"): 2, ("吃", "飯"): 2, ("學校", "</s>"): 3, ("飯", "</s>"): 2,
      ("<s>", "他"): 3, ("他", "說"): 3, ("說", "我"): 2}
TRI = {("<s>", "<s>", "我"): 5, ("<s>", "我", "去"): 4, ("我", "去", "學校"): 3, ("我", "去", "吃"): 2, ("去", "吃", "飯"): 2,
       ("<s>", "<s>", "他"): 3, ("<s>", "他", "說"): 3, ("他", "說", "我"): 2, ("說", "我", "去"): 2}


def mk_sjlm(vocab):
    """Tiny SJLM (ids 0 and 1 stay <s>, </s>) holding BI under the word order `vocab`."""
    ids = {w: i for i, w in enumerate(vocab)}
    BI = {k: c for k, c in globals()["BI"].items() if k[0] in ids and k[1] in ids}   # words outside `vocab` drop their bigrams
    uni = [sum(c for (a, b), c in BI.items() if b == w) for w in vocab]
    ctxs = sorted({ids[a] for a, _ in BI})
    nxt, cnt, off, tot = [], [], [0], []
    for c in ctxs:
        ents = sorted((ids[b], n) for (a, b), n in BI.items() if ids[a] == c)
        nxt += [e[0] for e in ents]; cnt += [e[1] for e in ents]; off.append(len(nxt)); tot.append(sum(e[1] for e in ents))
    raw = b"".join(struct.pack("<H", len(w.encode())) + w.encode() for w in vocab)
    return sjlm_io.write_sjlm({"V": len(vocab), "N": sum(uni), "eos_total": uni[1], "D": 0.75, "vocab_raw": raw, "uni": uni, "ctx": ctxs,
                               "tot": tot, "off": off, "nxt": nxt, "cnt": cnt})


def mk_tri(vocab, sha, entries=TRI):
    ids, V = {w: i for i, w in enumerate(vocab)}, len(vocab)
    ctx = {}
    for (u, v, w), c in entries.items():
        ctx.setdefault(ids[u] * V + ids[v], {})[ids[w]] = c
    keys = sorted(ctx)
    off = [0]
    for k in keys:
        off.append(off[-1] + len(ctx[k]))
    return sjlm_io.write_tri({"V": V, "D": 0.75, "model_sha": sha, "key": keys, "tot": [sum(ctx[k].values()) + 1 for k in keys], "off": off,
                              "nxt": [w for k in keys for w in sorted(ctx[k])], "cnt": [ctx[k][w] for k in keys for w in sorted(ctx[k])]})


def triples(tri, vocab):
    """{(u, v, w) strings: count} and {(u, v): pre-prune total}."""
    V, c3, t3 = len(vocab), {}, {}
    for i, k in enumerate(tri["key"]):
        u, v = divmod(int(k), V)
        t3[(vocab[u], vocab[v])] = int(tri["tot"][i])
        for j in range(tri["off"][i], tri["off"][i + 1]):
            c3[(vocab[u], vocab[v], vocab[int(tri["nxt"][j])])] = int(tri["cnt"][j])
    return c3, t3


class TestRemap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sv = WORDS
        cls.dv = WORDS[:2] + ["說", "他", "飯", "吃", "學校", "去", "我"]          # same words, other ids
        cls.src, cls.dst = mk_sjlm(cls.sv), mk_sjlm(cls.dv)
        cls.tri = mk_tri(cls.sv, hashlib.sha256(cls.src).digest())
        cls.out, _, _ = remap_tri.remap(cls.src, cls.tri, cls.dst)

    def test_counts_and_totals_preserved(self):
        a, b = sjlm_io.read_tri(self.tri), sjlm_io.read_tri(self.out)
        self.assertEqual(triples(a, self.sv), triples(b, self.dv))
        self.assertEqual((len(a["key"]), len(a["nxt"])), (len(b["key"]), len(b["nxt"])))
        self.assertTrue((np.diff(b["key"].astype(np.int64)) > 0).all())
        for i in range(len(b["key"])):
            self.assertTrue((np.diff(b["nxt"][b["off"][i]:b["off"][i + 1]].astype(np.int64)) > 0).all())

    def test_trilm_scores_equal_under_permuted_ids(self):
        with tempfile.TemporaryDirectory() as d:
            ps, pd = os.path.join(d, "s.sjlm"), os.path.join(d, "d.sjlm")
            for p, b in ((ps, self.src), (pd, self.dst)):
                open(p, "wb").write(b)
            ts = trimodel.TriLM(L.BigramLM(ps, classes=False), trimodel.TriTable(self.tri))
            td = trimodel.TriLM(L.BigramLM(pd, classes=False), trimodel.TriTable(self.out))
        n = 0
        for (u, v, w) in list(TRI) + [("我", "去", "飯"), ("他", "他", "說"), (None, "我", "去")]:
            self.assertEqual(ts.prob(u, v, w, 0.01), td.prob(u, v, w, 0.01))
            n += 1
        self.assertGreater(ts._ctx("我", "去"), -1)
        self.assertGreater(n, 9)

    def test_missing_word_stops(self):
        dst = mk_sjlm(self.dv[:-1] + ["他們"])                                     # 我 is gone
        with self.assertRaises(SystemExit) as cm:
            remap_tri.remap(self.src, self.tri, dst)
        self.assertIn("1 SRC words missing", str(cm.exception))

    def test_output_loads_bound_to_dst(self):
        t = trimodel.TriTable(self.out, hashlib.sha256(self.dst).digest())
        self.assertEqual(t.V, len(self.dv))
        with self.assertRaises(ValueError):
            trimodel.TriTable(self.out, hashlib.sha256(self.src).digest())

    def test_cli_prints_one_json_line(self):
        import contextlib, io, json
        with tempfile.TemporaryDirectory() as d:
            p = {n: os.path.join(d, n) for n in ("s", "t", "d", "o")}
            for n, b in (("s", self.src), ("t", self.tri), ("d", self.dst)):
                open(p[n], "wb").write(b)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                remap_tri.main(["--src-lm", p["s"], "--tri", p["t"], "--dst-lm", p["d"], "--out", p["o"]])
            r = json.loads(buf.getvalue())
            self.assertEqual(r["entries"], len(TRI))
            self.assertEqual(r["out_sha256"], hashlib.sha256(open(p["o"], "rb").read()).hexdigest())


if __name__ == "__main__":
    unittest.main()
