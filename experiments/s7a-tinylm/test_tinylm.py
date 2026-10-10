"""S7a unit tests, numpy only: LL (hand values, context, UNK), context keys, vocabulary, the numpy forward, latency / parity helpers.
Run: /opt/homebrew/bin/python3 -m unittest discover -s experiments/s7a-tinylm -p 'test_*.py'
"""
import json
import math
import os
import shutil
import tempfile
import unittest

import numpy as np

import latency
import parity
import tinylm
from tinylm import BOS, PAD, UNK, NumpyLM, Vocab, han_tail, ll, ll_many, row_key
from lm import context_key


class Toy:
    """A bigram 'model' with hand-chosen probabilities: P(next | previous token only). Records every batch it is asked about."""
    #            PAD   UNK   BOS   甲    乙
    T = np.array([[.2, .2, .2, .2, .2],     # after PAD
                  [.1, .1, .1, .4, .3],     # after UNK
                  [.05, .2, .05, .5, .2],   # after BOS
                  [.05, .1, .05, .3, .5],   # after 甲
                  [.05, .1, .05, .6, .2]])  # after 乙

    def __init__(self):
        self.vocab = Vocab(["甲", "乙"])
        self.seen = []

    def logp10(self, ids):
        self.seen.append(np.array(ids))
        return np.log10(self.T[ids])


class TestLL(unittest.TestCase):
    def test_hand_values(self):
        m = Toy()
        # [BOS] 甲 乙: log10 P(甲|BOS) + log10 P(乙|甲) = log10 .5 + log10 .5
        self.assertAlmostEqual(ll(m, "", "甲乙"), -0.6020599913, places=9)
        # [BOS] 乙 甲 乙 scored on the last two only: log10 P(甲|乙) + log10 P(乙|甲) = log10 .6 + log10 .5
        self.assertAlmostEqual(ll(m, "乙", "甲乙"), math.log10(.6) + math.log10(.5), places=9)
        self.assertAlmostEqual(ll(m, "乙", "甲乙"), -0.5228787453, places=9)

    def test_context_changes_ll(self):
        m = Toy()
        no_k, with_k = ll(m, "", "甲"), ll(m, "乙", "甲")
        self.assertAlmostEqual(no_k, math.log10(.5), places=9)
        self.assertAlmostEqual(with_k, math.log10(.6), places=9)
        self.assertNotAlmostEqual(no_k, with_k, places=3)

    def test_exact_input_sequence(self):
        m = Toy()
        ll(m, "乙", "甲乙")
        self.assertEqual(m.seen[-1].tolist(), [[BOS, 4, 3, 4]])      # [BOS] + k + c
        ll(m, "", "甲")
        self.assertEqual(m.seen[-1].tolist(), [[BOS, 3]])            # empty k (engine sentinel): no k token at all

    def test_unk(self):
        m = Toy()
        self.assertAlmostEqual(ll(m, "", "甲丙"), math.log10(.5) + math.log10(.1), places=9)    # 丙 is not in the table: UNK after 甲
        self.assertAlmostEqual(ll(m, "丙", "甲"), math.log10(.4), places=9)                       # UNK in the context: P(甲|UNK)
        self.assertEqual(m.seen[-1].tolist(), [[BOS, UNK, 3]])

    def test_batching_and_padding_do_not_change_values(self):
        m = Toy()
        items = [("", "甲乙"), ("乙", "甲"), ("甲乙", "乙甲乙"), ("", "甲")]
        together = ll_many(m, items, bs=64)
        one_by_one = np.array([ll(m, k, c) for k, c in items])
        two_at_a_time = ll_many(m, items, bs=2)
        np.testing.assert_allclose(together, one_by_one, atol=1e-12)
        np.testing.assert_allclose(together, two_at_a_time, atol=1e-12)

    def test_overlong_context_is_trimmed_from_the_left(self):
        m = Toy()
        ll_many(m, [("乙" * 70, "甲乙")])
        self.assertEqual(m.seen[-1].shape[1], tinylm.CTX)
        with self.assertRaises(ValueError):
            ll_many(m, [("", "甲" * 64)])

    def test_empty_candidate(self):
        self.assertEqual(ll(Toy(), "乙", ""), 0.0)


class TestKeys(unittest.TestCase):
    def test_context_prefix_cases(self):
        self.assertEqual(row_key("今天天氣很好"), "很好")        # ends in Han: the last 2
        self.assertEqual(row_key("今天天氣很好，"), "")          # ends in punctuation: sentinel -> empty k
        self.assertEqual(row_key("abc你"), "你")
        self.assertEqual(row_key(""), "")
        self.assertEqual(context_key("今天天氣很好，"), "")       # reference/proto/lm.py returns "" where core/src/learn.rs returns the sentinel "^"

    def test_sixteen_character_variant(self):
        p = "。" + "一二三四五六七八九十甲乙丙丁戊己庚辛"          # 18 Han after a full stop
        self.assertEqual(row_key(p, 16), "三四五六七八九十甲乙丙丁戊己庚辛")
        self.assertEqual(len(row_key(p, 16)), 16)
        self.assertEqual(row_key("好嗎？你好", 16), "你好")      # stops at the punctuation
        self.assertEqual(row_key("好，", 16), "")

    def test_han_tail_matches_context_key(self):
        rng = np.random.default_rng(1)
        pool = list("你好嗎，。a1 ！〇𠮷").copy()
        for _ in range(500):
            s = "".join(rng.choice(pool, size=rng.integers(0, 8)))
            self.assertEqual(han_tail(s, 2), context_key(s), s)


class TestVocab(unittest.TestCase):
    def test_ids(self):
        v = Vocab(["你", "好"])
        self.assertEqual((PAD, UNK, BOS), (0, 1, 2))
        self.assertEqual(v.enc("你好嗎a"), [3, 4, UNK, UNK])
        self.assertEqual(v.size, 5)


def reference_logits(p, n_layer, n_head, ids):
    """Plain python-loop float64 forward for one sequence, written independently of NumpyLM (explicit loops, math.tanh)."""
    d = p["emb"].shape[1]
    dh = d // n_head
    T = len(ids)

    def ln(x, w, b):
        mu = sum(x) / d
        var = sum((t - mu) ** 2 for t in x) / d
        return [(t - mu) / math.sqrt(var + 1e-5) * w[i] + b[i] for i, t in enumerate(x)]

    def lin(x, W, b):
        return [sum(x[i] * W[i, j] for i in range(len(x))) + b[j] for j in range(W.shape[1])]

    x = [[p["emb"][ids[t], i] + p["pos"][t, i] for i in range(d)] for t in range(T)]
    for l in range(n_layer):
        g = lambda k: p[f"l{l}.{k}"]  # noqa: E731
        qkv = [lin(ln(x[t], g("ln1_w"), g("ln1_b")), g("qkv_w"), g("qkv_b")) for t in range(T)]
        att = []
        for t in range(T):
            row = []
            for h in range(n_head):
                q = qkv[t][h * dh:(h + 1) * dh]
                sc = [sum(q[i] * qkv[u][d + h * dh + i] for i in range(dh)) / math.sqrt(dh) for u in range(t + 1)]   # causal: u <= t
                mx = max(sc)
                w = [math.exp(s - mx) for s in sc]
                z = sum(w)
                row += [sum(w[u] / z * qkv[u][2 * d + h * dh + i] for u in range(t + 1)) for i in range(dh)]
            att.append(row)
        for t in range(T):
            a = lin(att[t], g("proj_w"), g("proj_b"))
            x[t] = [x[t][i] + a[i] for i in range(d)]
            hdn = lin(ln(x[t], g("ln2_w"), g("ln2_b")), g("fc1_w"), g("fc1_b"))
            hdn = [0.5 * u * (1 + math.tanh(math.sqrt(2 / math.pi) * (u + 0.044715 * u ** 3))) for u in hdn]
            m = lin(hdn, g("fc2_w"), g("fc2_b"))
            x[t] = [x[t][i] + m[i] for i in range(d)]
    return np.array([[sum(f[i] * p["emb"][v, i] for i in range(d)) for v in range(p["emb"].shape[0])]
                     for f in (ln(xt, p["lnf_w"], p["lnf_b"]) for xt in x)])


class TestNumpyForward(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chars = list("你我他好嗎的是不了")
        cls.tmp = tempfile.mkdtemp()
        cls.path = os.path.join(cls.tmp, "m.npz")
        cls.params = tinylm.random_params(cls.chars, 2, 16, 4, seed=3)
        tinylm.save_params(cls.path, cls.params, cls.chars, 2, 4)
        cls.m64 = NumpyLM(cls.path, dtype=np.float64)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_matches_loop_reference(self):
        # the saved file is float32; compare in float64 against the reference built from the same float32 values
        p = {k: v.astype(np.float64) for k, v in self.m64.p.items()}
        ids = [BOS, 3, 5, 7, 4, UNK, 9]
        got = self.m64.logits(np.array([ids]))[0]
        np.testing.assert_allclose(got, reference_logits(p, 2, 4, ids), atol=1e-9)

    def test_causal(self):
        a = np.array([[BOS, 3, 4, 5, 6, 7]])
        b = a.copy(); b[0, 4:] = [9, 10]
        la, lb = self.m64.logits(a), self.m64.logits(b)
        np.testing.assert_allclose(la[:, :4], lb[:, :4], atol=1e-12)     # positions before the change do not move
        self.assertGreater(np.abs(la[:, 4:] - lb[:, 4:]).max(), 1e-3)

    def test_pad_and_batch_invariance(self):
        short, long_ = [BOS, 3, 4], [BOS, 5, 6, 7, 8, 9]
        batch = np.array([long_, short + [PAD] * 3])
        lb = self.m64.logp10(batch)
        np.testing.assert_allclose(lb[0], self.m64.logp10(np.array([long_]))[0], atol=1e-12)
        np.testing.assert_allclose(lb[1, :3], self.m64.logp10(np.array([short]))[0], atol=1e-12)

    def test_probabilities_normalise(self):
        lp = self.m64.logp10(np.array([[BOS, 3, 4]]))
        np.testing.assert_allclose((10 ** lp).sum(-1), 1.0, atol=1e-12)

    def test_float32_close_to_float64(self):
        m32 = NumpyLM(self.path, dtype=np.float32)
        items = [("你好", "我他"), ("", "嗎的是")]
        np.testing.assert_allclose(ll_many(m32, items), ll_many(self.m64, items), atol=1e-4)

    def test_context_reaches_the_numpy_model(self):
        self.assertGreater(abs(ll(self.m64, "你好", "我") - ll(self.m64, "", "我")), 1e-6)

    def test_vocab_roundtrip(self):
        self.assertEqual(self.m64.vocab.chars, self.chars)

    def test_latency_and_int8_helpers(self):
        p50, p95 = latency.measure(self.m64, runs=5, warmup=1, n_char=17)
        self.assertTrue(0 < p50 <= p95)
        q = os.path.join(self.tmp, "q.npz")
        latency.quantize_int8(self.path, q)
        self.assertLess(os.path.getsize(q), os.path.getsize(self.path))


class TestParity(unittest.TestCase):
    def rec(self, i, ll, ll0=None):
        return {"i": i, "n": 2, "ng": [-1.0, -2.0], "ll": ll, "ll0": ll0 or ll, "ll16": ll}

    def test_max_delta(self):
        a = [self.rec(1, [-3.0, -4.0]), self.rec(2, [-1.0, -2.0])]
        b = [self.rec(1, [-3.0, -4.00005]), self.rec(2, [-1.0, -2.0], [-1.0, -2.0002])]
        d = parity.max_delta(a, b)
        self.assertAlmostEqual(d["ll"][0], 5e-5, places=12)
        self.assertAlmostEqual(d["ll0"][0], 2e-4, places=12)
        self.assertEqual(d["ll"][1], 4)

    def test_different_candidates_are_an_error(self):
        a, b = [self.rec(1, [-1.0, -2.0])], [self.rec(1, [-1.0, -2.0])]
        b[0]["ng"] = [-1.0, -2.5]
        with self.assertRaises(ValueError):
            parity.max_delta(a, b)

    def test_gate_exit_codes(self):
        with tempfile.TemporaryDirectory() as td:
            def write(name, rows):
                p = os.path.join(td, name)
                with open(p, "w") as f:
                    f.write("".join(json.dumps(r) + "\n" for r in rows))
                return p
            good = write("a", [self.rec(1, [-3.0, -4.0])])
            near = write("b", [self.rec(1, [-3.0, -4.00005])])
            far = write("c", [self.rec(1, [-3.0, -4.001])])
            parity.main([good, near])
            with self.assertRaises(SystemExit):
                parity.main([good, far])


if __name__ == "__main__":
    unittest.main()
