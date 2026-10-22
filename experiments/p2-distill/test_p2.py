"""P2 trainer unit tests (synthetic data only). The joining / tokenising / collating half runs without torch; the torch half is skipped
without it (main runs it on Colab / GCP before training):  python3 -m unittest discover -s experiments/p2-distill -p 'test_*.py' -v"""
import json
import math
import os
import random
import tempfile
import unittest

import numpy as np

import train_p2 as tp
import tinylm
from tinylm import NumpyLM, ll_many

CHARS = list("你我他好嗎的是不了天氣說今真")
LN10 = math.log(10.0)


def make_data(n_rows=40, seed=1):
    """-> rows [(gold, syl, prefix)], dump cands, teacher records. Mixed n (1..8), one skipped row, one row with an over-long candidate."""
    rnd = random.Random(seed)
    rows, cands, teacher = [], [], []
    prefixes = ["", "你好", "他說。我", "今天天氣", "是"]
    for i in range(1, n_rows + 1):
        n = 1 if i == 3 else rnd.randint(2, 8)
        cs = [("".join(rnd.choice(CHARS) for _ in range(rnd.randint(2, 6))), -float(j)) for j in range(n)]
        if i == 5:
            cs[1] = ("好" * 64, -1.0)                       # does not fit the context: ll_many would raise
        rows.append((cs[0][0], ["ㄋㄧˇ"] * len(cs[0][0]), rnd.choice(prefixes)))
        cands.append(cs)
        skip = int(i == 7)
        t = {"i": i, "n": n, "skip": skip}
        if n >= 2:
            t["ll"] = [0.0] * n if skip else [-rnd.uniform(0.5, 6.0) for _ in range(n)]
        teacher.append(t)
    return rows, cands, teacher


def write_files(td, name, rows, cands, teacher):
    rp, dp, tpth = (os.path.join(td, name + e) for e in (".txt", ".dump", ".0c.jsonl"))
    with open(rp, "w", encoding="utf-8", newline="\n") as f:
        for g, syl, pre in rows:
            f.write(f"{pre}|{g}|{' '.join(syl)}\n")
    with open(dp, "w", encoding="utf-8", newline="\n") as f:
        for i, cs in enumerate(cands, 1):
            for r, (s, sc) in enumerate(cs, 1):
                f.write(f"{i}\t{r}\t{s}\t{sc}\n")
    with open(tpth, "w", encoding="utf-8", newline="\n") as f:
        for t in teacher:
            f.write(json.dumps(t) + "\n")
    return rp, dp, tpth


class TestJoin(unittest.TestCase):
    def test_filter_and_counts(self):
        rows, cands, teacher = make_data()
        items, c = tp.join_rows(rows, cands, teacher)
        self.assertEqual(c["unusable"], {"n<2": 1, "skip": 1, "too_long": 1})
        self.assertEqual(c["usable"], len(items))
        self.assertEqual(len(items), len(rows) - 3)
        self.assertEqual(c["rows_read"], len(rows))
        prefix, surfs, ll = items[0]
        self.assertEqual(surfs, [s for s, _ in cands[0]])
        self.assertEqual(ll, teacher[0]["ll"])

    def test_mismatch_stops(self):
        rows, cands, teacher = make_data()
        for bad in (lambda: (rows, cands, teacher[:-1]), lambda: (rows, cands[:-1], teacher), lambda: (rows[:-1], cands, teacher)):
            with self.assertRaises(tp.Mismatch):
                tp.join_rows(*bad())
        t2 = [dict(t) for t in teacher]
        t2[10]["n"] += 1                                    # n disagrees with the dump
        with self.assertRaises(tp.Mismatch):
            tp.join_rows(rows, cands, t2)
        t3 = [dict(t) for t in teacher]
        t3[10]["i"] = 99
        with self.assertRaises(tp.Mismatch):
            tp.join_rows(rows, cands, t3)
        t4 = [dict(t) for t in teacher]
        t4[10]["ll"] = t4[10]["ll"][:-1]
        with self.assertRaises(tp.Mismatch):
            tp.join_rows(rows, cands, t4)

    def test_files_roundtrip_and_dump_count_stop(self):
        rows, cands, teacher = make_data(10)
        with tempfile.TemporaryDirectory() as td:
            rp, dp, tpth = write_files(td, "a", rows, cands, teacher)
            r2 = tp.score.load_rows(rows=rp)
            with open(dp, encoding="utf-8") as f:
                c2 = tp.score.load_dump(f, len(r2))
            items, c = tp.join_rows(r2, c2, tp.read_teacher(tpth))
            self.assertEqual(c["rows_read"], 10)
            self.assertEqual([s for s, _ in c2[0]], [s for s, _ in cands[0]])


class TestTeacherAndPlan(unittest.TestCase):
    def test_teacher_dist(self):
        q, lq = tp.teacher_dist([-1.0, -2.0, -3.0], 3)       # q proportional to 10^ll
        w = np.array([0.1, 0.01, 0.001])
        np.testing.assert_allclose(q[:3], w / w.sum(), rtol=1e-12)
        np.testing.assert_allclose(np.exp(lq[:3]), q[:3], rtol=1e-12)
        self.assertEqual(list(q[3:]), [0.0] * 5)
        self.assertEqual(list(lq[3:]), [0.0] * 5)

    def test_plan_reproducible_and_disjoint(self):
        v1, b1 = tp.plan(1000, 0.02, 64, 3, 7)
        v2, b2 = tp.plan(1000, 0.02, 64, 3, 7)
        self.assertEqual(len(v1), 20)
        np.testing.assert_array_equal(v1, v2)
        self.assertTrue(all(np.array_equal(a, b) for a, b in zip(b1, b2)))
        per_epoch = math.ceil(980 / 64)
        self.assertEqual(len(b1), 3 * per_epoch)
        for e in range(3):                                   # every training row exactly once per epoch, never a held-out one
            rows = np.concatenate(b1[e * per_epoch:(e + 1) * per_epoch])
            self.assertEqual(sorted(rows.tolist()), sorted(set(range(1000)) - set(v1.tolist())))
        self.assertFalse(np.array_equal(np.concatenate(b1[:per_epoch]), np.concatenate(b1[per_epoch:2 * per_epoch])))
        v3, b3 = tp.plan(1000, 0.02, 64, 3, 8)
        self.assertFalse(np.array_equal(v1, v3))
        self.assertEqual(len(tp.plan(1000, 0.02, 64, 3, 7, max_steps=5)[1]), 5)

    def test_diverged(self):
        self.assertTrue(tp.diverged(float("nan"), None, 1, 100))
        self.assertTrue(tp.diverged(float("inf"), 1.0, 300, 100))
        self.assertTrue(tp.diverged(1.1, 1.0, 200, 100))
        self.assertFalse(tp.diverged(1.1, 1.0, 100, 100))     # inside warm-up
        self.assertFalse(tp.diverged(0.9, 1.0, 200, 100))


def tiny_npz(td, name="m.npz", seed=0):
    path = os.path.join(td, name)
    p = tinylm.random_params(CHARS, 2, 32, 2, seed=seed)
    tinylm.save_params(path, p, CHARS, 2, 2)
    return path


class TestCollateWithoutTorch(unittest.TestCase):
    def test_positions_reproduce_ll_many(self):
        """The scored positions / targets / slots of collate(), applied to the numpy model's log-probs, give ln10 * ll_many per candidate."""
        rows, cands, teacher = make_data()
        items, _ = tp.join_rows(rows, cands, teacher)
        items.append(("你好", ["好" * 62, "你我"], [-1.0, -2.0]))     # 62 characters: k is cut from the left to fit the context
        items.append(("", ["今天", "天氣", "說"], [-1.0, -2.0, -3.0]))  # empty key
        with tempfile.TemporaryDirectory() as td:
            m = NumpyLM(tiny_npz(td))
        P = tp.Packed(items, m.vocab)
        idx = np.array([len(items) - 1, 0, len(items) - 2, 5, 11])
        b = tp.collate(P, idx)
        lp = m.logp10(b["ids"])
        s = np.zeros(len(b["seq_row"]))
        np.add.at(s, b["pos_seq"], lp[b["pos_seq"], b["pos_col"], b["tgt"]] * LN10)
        table = np.full((len(idx), tp.TOPK), np.nan)
        table[b["seq_row"], b["seq_slot"]] = s
        for r, i in enumerate(idx):
            prefix, surfs, _ = items[i]
            want = ll_many(m, [(tinylm.row_key(prefix, 2), c) for c in surfs]) * LN10
            np.testing.assert_allclose(table[r, :len(surfs)], want, atol=1e-9)
            self.assertTrue(np.isnan(table[r, len(surfs):]).all())
        np.testing.assert_allclose(b["q"].sum(1), 1.0)
        self.assertEqual(b["ids"].shape[1] <= tinylm.CTX, True)


try:
    import torch
except ImportError:
    torch = None


@unittest.skipUnless(torch, "torch is not installed")
class TestTorch(unittest.TestCase):
    def model(self, td):
        import torch_model as tm
        path = tiny_npz(td)
        m, vocab = tm.from_npz(path)
        return path, m.eval(), vocab

    def test_candidate_score_equals_ll_many(self):
        rows, cands, teacher = make_data()
        items, _ = tp.join_rows(rows, cands, teacher)
        items.append(("你好", ["好" * 62, "你我"], [-1.0, -2.0]))
        with tempfile.TemporaryDirectory() as td:
            path, model, vocab = self.model(td)
            P = tp.Packed(items, vocab)
            idx = np.arange(len(items))
            t = tp.to_tensors(tp.collate(P, idx), "cpu")
            with torch.no_grad():
                S = tp.cand_scores(model, t).numpy()
            m = NumpyLM(path)
            for r, (prefix, surfs, _) in enumerate(items):
                want = ll_many(m, [(tinylm.row_key(prefix, 2), c) for c in surfs]) * LN10
                np.testing.assert_allclose(S[r, :len(surfs)], want, atol=1e-4)
                self.assertTrue((S[r, len(surfs):] == tp.NEG).all())

    def test_kl_matches_hand_computation(self):
        rows = [([-1.0, -2.0], [0.3, -1.2]), ([-0.5, -0.7, -3.0], [1.0, 0.5, -2.0]),
                ([-float(j) / 3 for j in range(8)], [0.1 * j for j in range(8)])]   # n = 2, 3, 8
        S = torch.full((3, tp.TOPK), tp.NEG)
        q = np.zeros((3, tp.TOPK)); lq = np.zeros((3, tp.TOPK))
        for r, (ll, s) in enumerate(rows):
            S[r, :len(s)] = torch.tensor(s)
            q[r], lq[r] = tp.teacher_dist(ll, len(ll))
        got = tp.kl_rows(S, torch.from_numpy(q).float(), torch.from_numpy(lq).float()).numpy()
        for r, (ll, s) in enumerate(rows):
            qq = 10 ** np.array(ll); qq /= qq.sum()
            pp = np.exp(s); pp /= pp.sum()
            self.assertAlmostEqual(got[r], float((qq * np.log(qq / pp)).sum()), places=5)

    def test_gradient_is_finite_and_nonzero(self):
        rows, cands, teacher = make_data()
        items, _ = tp.join_rows(rows, cands, teacher)
        with tempfile.TemporaryDirectory() as td:
            _, model, vocab = self.model(td)
            t = tp.to_tensors(tp.collate(tp.Packed(items, vocab), np.arange(8)), "cpu")
            loss = tp.kl_rows(tp.cand_scores(model, t), t["q"], t["logq"]).mean()
            loss.backward()
            g = [p.grad for p in model.parameters() if p.grad is not None]
            self.assertTrue(all(torch.isfinite(x).all() for x in g))
            self.assertGreater(sum(float(x.abs().sum()) for x in g), 0)

    def run_main(self, td, out, extra=()):
        rows, cands, teacher = make_data(40)
        a = write_files(td, "a", rows, cands, teacher)
        rows, cands, teacher = make_data(30, seed=2)
        b = write_files(td, "b", rows, cands, teacher)
        tp.main(["--npz", os.path.join(td, "m.npz"), "--data", *a, *b, "--out", out, "--epochs", "2", "--batch", "4",
                 "--max-steps", "30", "--warmup", "5", "--lr", "1e-3", "--val-frac", "0.1", "--log-every", "1000",
                 "--val-every", "1000", "--device", "cpu", *extra])

    def test_end_to_end_tiny_run(self):
        import score
        with tempfile.TemporaryDirectory() as td:
            tiny_npz(td)
            out = os.path.join(td, "out")
            self.run_main(td, out)
            info = json.load(open(os.path.join(out, "train-S.json")))
            self.assertEqual(info["steps"], 30)
            self.assertEqual(info["counts"]["unusable"], {"n<2": 2, "skip": 2, "too_long": 2})
            self.assertEqual(info["counts"]["usable"], 64)
            self.assertEqual([v[0] for v in info["val_curve"]], [0, 30])      # the start and the end
            self.assertEqual(len(info["input_sha256"]["files"]), 6)
            before, after = NumpyLM(os.path.join(td, "m.npz")), score.load_model(os.path.join(out, "tinylm-S.npz"), "numpy")
            self.assertEqual(after.vocab.chars, before.vocab.chars)           # the scorer's own loader reads it
            items = [("你好", "今天"), ("", "天氣"), ("他", "我是不了")]
            self.assertGreater(np.abs(ll_many(before, items) - ll_many(after, items)).max(), 1e-5)   # training moved the weights
            out2 = os.path.join(td, "out2")
            self.run_main(td, out2)
            self.assertEqual(info["train_order_sha256"], json.load(open(os.path.join(out2, "train-S.json")))["train_order_sha256"])

    def test_count_mismatch_stops(self):
        with tempfile.TemporaryDirectory() as td:
            rows, cands, teacher = make_data(10)
            a = write_files(td, "a", rows, cands, teacher[:-1])
            with self.assertRaises(SystemExit) as cm:
                tp.main(["--npz", tiny_npz(td), "--data", *a, "--out", os.path.join(td, "o"), "--device", "cpu"])
            self.assertIn("STOP", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
