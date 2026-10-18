"""L0 unit tests, synthetic data only. Everything above the torch class runs with plain numpy (the Mac's python3); the torch class is
skipped without torch and never needs a GPU (main runs it on GCP / Colab on CPU before the real run).
Run: python3 -m unittest discover -s experiments/l0 -p 'test_*.py' -v
"""
import json
import math
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate  # noqa: E402
import l0core  # noqa: E402
import score_l0  # noqa: E402
import tinylm  # noqa: E402
from tinylm import BOS, Vocab  # noqa: E402
import evalstats  # noqa: E402

try:
    import torch
    HAVE_TORCH = True
except ImportError:
    HAVE_TORCH = False

CHARS = list("你我他好嗎的是不了天氣")


def rec(ok, s=None, **kw):
    r = {"i": 1, "n": len(ok), "ok": ok}
    if s is not None:
        r["s"] = s
    r.update(kw)
    return r


def write_rows(path, rows):
    """rows: [(prefix, gold)]"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(f"{p}|{g}|ㄅ ㄆ\n" for p, g in rows))


def write_dump(path, cands):
    """cands: per row [(surface, score)]"""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for i, cs in enumerate(cands, 1):
            f.write("".join(f"{i}\t{r}\t{s}\t{sc}\n" for r, (s, sc) in enumerate(cs, 1)))


def synthetic(rng, n_rows):
    """(rows, cands): a mix of trainable rows, rows without a match, one-candidate rows, one row with a too-long candidate."""
    rows, cands = [], []
    for i in range(n_rows):
        n = int(rng.integers(1, 9))
        cs = ["".join(rng.choice(CHARS, int(rng.integers(2, 5)))) for _ in range(n)]
        sc = sorted((-float(x) for x in rng.uniform(0.5, 6, n)), reverse=True)
        gold = cs[int(rng.integers(0, n))] if rng.random() < 0.8 else "不不不不不不"
        prefix = "".join(rng.choice(CHARS + ["，", "。"], int(rng.integers(0, 30))))
        rows.append((prefix, gold))
        cands.append(list(zip(cs, sc)))
    rows[0], cands[0] = ("你我", "你好"), [("你好", -1.0), ("你我", -2.0), ("好" * 70, -3.0)]      # too long
    rows[2], cands[2] = ("", "你好"), [("你好", -1.0)]                                                  # one candidate
    rows[1], cands[1] = ("天氣，", "他好"), [("你好", -1.0), ("她好", -2.0)]                          # 她 = 他 under the lenient match
    return rows, cands


class TestCore(unittest.TestCase):
    def test_k_cross(self):
        self.assertEqual(l0core.k_cross("你好，嗎。abc我", 3), "好嗎我")
        self.assertEqual(l0core.k_cross("你好，嗎。abc我", 99), "你好嗎我")
        self.assertEqual(l0core.k_cross("abc，", 5), "")
        self.assertEqual(l0core.k_cross("你好", 0), "")
        self.assertEqual(l0core.k_cross("你好", -3), "")

    def test_ok_and_target(self):
        ok = l0core.ok_flags("她好", ["你好", "他好", "她好"])           # 她 and 他 are the same under lenient
        self.assertEqual(ok, [0, 1, 1])
        self.assertEqual(l0core.target_of(ok), 1)                      # two right candidates: the earliest rank
        self.assertEqual(l0core.target_of([1, 0, 1]), 0)               # rank 1 right -> keep
        self.assertIsNone(l0core.target_of([0, 0]))

    def test_untrainable_reasons(self):
        self.assertEqual(l0core.untrainable_reason([1], ["a"]), "n<2")
        self.assertEqual(l0core.untrainable_reason([], ["a", "好" * 63]), "too_long")
        self.assertIsNone(l0core.untrainable_reason([0, 0, 1], ["a", "b", "好" * 62]))     # 62 still fits
        self.assertEqual(l0core.untrainable_reason([0, 0], ["a", "b"]), "no_match")

    def test_features(self):
        f = l0core.features([-1.0, -2.0, -3.5])
        self.assertEqual(f.shape, (3, 8))
        self.assertTrue((f[0] == 0).all())
        np.testing.assert_allclose(f[1], [-1, 1, 0, 0, 0, 0, 0, 0])
        np.testing.assert_allclose(f[2], [-2.5, 0, 1, 0, 0, 0, 0, 0])
        f = l0core.features([-i for i in range(8)])
        np.testing.assert_allclose(f[7], [-7, 0, 0, 0, 0, 0, 0, 1])      # rank 8 -> last one-hot
        with self.assertRaises(ValueError):
            l0core.features([0.0] * 9)

    def test_pack_and_collate(self):
        v = Vocab(CHARS)
        long_prefix = "你我他好嗎的是不了天氣" * 7                        # 77 Han characters
        p = l0core.pack_row(v, long_prefix + "，", [("你好", -1.0), ("我他嗎" * 20, -2.0)], 64, target=1)
        # candidate lengths 2 and 60: k = last 60 and last 2 Han characters
        want0 = [BOS] + v.enc(long_prefix[-60:]) + [BOS] + v.enc("你好")
        want1 = [BOS] + v.enc(long_prefix[-2:]) + [BOS] + v.enc("我他嗎" * 20)
        self.assertEqual(p.lens.tolist(), [len(want0), len(want1)])
        self.assertEqual(p.toks.tolist(), want0 + want1)
        self.assertLessEqual(max(p.lens), 64)
        self.assertEqual((len(want0), len(want1)), (64, 64))
        # context 2: context_key (<= 2 characters, stops at punctuation) -> here "，" ends the prefix, so k is empty
        p2 = l0core.pack_row(v, long_prefix + "，", [("你好", -1.0)] * 2, 2)
        self.assertEqual(p2.toks.tolist(), 2 * ([BOS, BOS] + v.enc("你好")))
        p3 = l0core.pack_row(v, "你我他", [("好" * 62, -1.0), ("好" * 61, -2.0), ("好", -3.0)], 2)
        self.assertEqual(p3.lens.tolist(), [64, 64, 5])                     # k cut to what fits: "", "他", "我他"
        self.assertEqual(p3.toks[64 + 1:64 + 2].tolist(), v.enc("他"))
        # collate two rows
        a = l0core.pack_row(v, "", [("你好", -1.0), ("我", -2.0)], 64, target=1)
        b = l0core.pack_row(v, "你", [("好嗎", -1.0), ("我他", -2.0), ("是", -3.0)], 64, target=0)
        c = l0core.collate([a, b])
        self.assertEqual(c["ids"].shape, (5, 5))                           # longest sequence: [BOS] 你 [BOS] 好 嗎
        self.assertEqual(c["last"].tolist(), [3, 2, 4, 4, 3])
        self.assertEqual(c["opt"].tolist(), [[0, 1, -1, -1, -1, -1, -1, -1], [2, 3, 4, -1, -1, -1, -1, -1]])
        self.assertEqual(c["target"].tolist(), [1, 0])
        self.assertEqual(c["ids"][1].tolist(), [BOS, BOS] + v.enc("我") + [0, 0])
        self.assertEqual(c["feat"].shape, (5, 8))

    def test_decision_rule(self):
        s = [0.0, 0.5, 1.2, 1.2]
        self.assertEqual(l0core.pick(rec([0, 0, 1, 0], s), 0.5), 2)      # margin 1.2 > 0.5, tie between 2 and 3 -> lower index
        self.assertEqual(l0core.pick(rec([0, 0, 1, 0], s), 1.2), 0)      # strict: margin 1.2 is not > 1.2
        self.assertEqual(l0core.pick(rec([0, 0, 1, 0], s), math.inf), 0)
        self.assertEqual(l0core.pick(rec([0, 0, 1, 0], s)), 2)           # no threshold: argmax over all options
        self.assertEqual(l0core.pick(rec([0, 1], [0.7, 0.7])), 0)        # ties go to keep
        self.assertEqual(l0core.pick(rec([0, 1], [0.7, 0.7]), 0), 0)     # margin 0 is not > 0
        self.assertEqual(l0core.pick(rec([0, 1], noscore=1), 0), 0)
        self.assertEqual(l0core.pick(rec([1], [0.0]), 0), 0)

    def test_threshold_selection(self):
        # 1000 rows: 30 turn right at margin 3, one breaks at margin 2, one breaks at margin 0.6
        recs = [rec([0, 1], [0, 3.0]) for _ in range(30)] + [rec([1, 0], [0, 2.0]), rec([1, 0], [0, 0.6])] + [rec([1, 1], [0, 0]) for _ in range(968)]
        t, table = l0core.select_t(recs)
        by = {row[0]: row for row in table}
        self.assertEqual((by[0][1], by[0][2], by[0][5]), (30, 2, False))      # 2 breaks: UB 6.3 / 1000 > 0.5%
        self.assertEqual((by[1][1], by[1][2], by[1][5]), (30, 1, True))       # 1 break: UB 4.74 / 1000 = 0.47%
        self.assertEqual((by[2][1], by[2][2], by[2][5]), (30, 0, True))
        self.assertEqual((by[3][1], by[3][2]), (0, 0))
        self.assertAlmostEqual(by[2][4], evalstats.poisson_upper95(0) / 1000)
        self.assertEqual(t, 2.5)       # net 30 at t = 2 and 2.5, ties -> larger t; t = 3 has net 0
        self.assertEqual(len(table), 16)
        # nothing within the cap: 20 breaks that no grid point removes
        bad = [rec([1, 0], [0, 100.0]) for _ in range(20)] + [rec([1, 1], [0, 0]) for _ in range(980)]
        self.assertIsNone(l0core.select_t(bad)[0])
        # a tiny set can never satisfy the cap (UB(0) / n = 3 / n > 0.5% for n < 600)
        self.assertIsNone(l0core.select_t([rec([0, 1], [0, 3.0])] * 10)[0])

    def test_alone_fix_and_retain(self):
        recs = [rec([0, 1], [0, 1]),                  # fixable, hit
                rec([0, 1], [1, 0]),                  # fixable, miss
                rec([0, 1, 1], [0, 1, 2]),            # two right: either is a hit (argmax = 2)
                rec([0, 0], [0, 1]),                  # nothing right: in no denominator
                rec([1, 0], [1, 0]),                  # retain, kept
                rec([1, 0], [0, 1]),                  # retain, lost
                rec([1, 0], noscore=1),               # retain, kept (noscore keeps rank 1)
                rec([0, 1], noscore=1),               # fixable, miss
                rec([1], [0.0])]                      # retain, kept
        self.assertEqual(l0core.alone_retain(recs), (2, 4, 3, 4))
        # M's plain LL as score lists: keep = argmax at index 0
        m = l0core.m_view({"i": 1, "n": 3, "ok": [0, 0, 1], "ll": [-5.0, -6.0, -4.0], "ng": [0, 0, 0]})
        self.assertEqual(l0core.pick(m), 2)
        self.assertEqual(l0core.alone_retain([m]), (1, 1, 0, 0))
        self.assertIsNone(l0core.m_view({"i": 2, "n": 1, "ok": [1]})["s"])


def pattern_set(kind):
    """The hand-made 1000-row set of the gate tests. kind: 'l0' (fixes 200 rows at margin 3) or 'keep' (never switches) or 'm'.
    700 rows rank 1 right (and 2 right), 200 rank 1 wrong / rank 2 right, 100 rank 1 right / rank 2 wrong."""
    out = []
    for i in range(1000):
        ok = [1, 1] if i < 700 else [0, 1] if i < 900 else [1, 0]
        if kind == "l0":
            s = [0.0, 3.0] if 700 <= i < 900 else [0.0, 0.0] if i < 700 else [0.0, -1.0]
            out.append({"i": i + 1, "n": 2, "ok": ok, "s": s})
        elif kind == "keep":
            out.append({"i": i + 1, "n": 2, "ok": ok, "s": [0.0, -1.0]})
        else:   # score.py record: fusion never leaves rank 1 (ng margin .1, ll prefers rank 1)
            out.append({"i": i + 1, "n": 2, "ok": ok, "ng": [-1.0, -1.1], "ll": [0.0, -1.0]})
    return out


def dump_files(d, name, w3="keep"):
    spec = {f"L0w1.c64.{name}": "l0", f"L0w1.c2.{name}": "l0", f"L0w3.c64.{name}": w3, f"L0w3.c2.{name}": w3, f"M.{name}": "m"}
    for f, kind in spec.items():
        with open(os.path.join(d, f + ".jsonl"), "w") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in pattern_set(kind)))
    if name == "tune":
        with open(os.path.join(d, "M64.tune.jsonl"), "w") as fh:
            fh.write("".join(json.dumps(dict(r, s=r["ll"])) + "\n" for r in pattern_set("m")))


class TestGate(unittest.TestCase):
    def test_tables_and_verdicts(self):
        with tempfile.TemporaryDirectory() as d:
            dump_files(d, "tune")
            dump_files(d, "dev")
            text = gate.run("tune", ["dev"], d, (1, 3), b_set="dev")
        lines = text.split("\n")

        def row(prefix):
            return [l for l in lines if l.startswith(prefix)][0]
        # (A): w1 fixes all 200 fixable and keeps all 800 retainable; M never leaves rank 1 for a wrong one... M picks rank 1 everywhere
        self.assertEqual(row("| L0 w1 c64 | 200"), "| L0 w1 c64 | 200 | 200 | 100.0% | 800 | 800 | 100.0% | 0 |")
        self.assertEqual(row("| L0 w3 c64 | 200"), "| L0 w3 c64 | 200 | 0 | 0.0% | 800 | 800 | 100.0% | 0 |")
        self.assertEqual(row("| M@64 | 200"), "| M@64 | 200 | 0 | 0.0% | 800 | 800 | 100.0% | 0 |")
        self.assertIn("L0 w1 c64: t = 2.5", text)              # 200 fixes at every t < 3, no breaks, tie -> larger t
        self.assertIn("L0 w3 c64: t = 8", text)                # w3 never switches: net 0 everywhere, ties -> larger t
        # dev: L0 w1 vs n-gram: 200 fixes, 0 breaks, UB(0) = 3.0 over 1000 = 0.30%
        ub0 = evalstats.poisson_upper95(0)
        self.assertEqual(row("| L0 w1 c64 (t = 2.5)"), f"| L0 w1 c64 (t = 2.5) | 200 | 0 | 200 | {ub0:.1f} | {100 * ub0 / 1000:.2f}% | 0.000 |")
        self.assertEqual(row("| M shipped"), f"| M shipped | 0 | 0 | 0 | {ub0:.1f} | {100 * ub0 / 1000:.2f}% | n/a |")
        self.assertEqual(row("| L0 w1 c64 | 200 | 0 | 200 |"), f"| L0 w1 c64 | 200 | 0 | 200 | {evalstats.mcnemar_exact(0, 200):.4g} |")
        self.assertIn("- (A) w1: PASS", text)
        self.assertIn("- (A) w3: FAIL", text)
        self.assertIn("- (B) w1: PASS (net vs M shipped +200", text)
        self.assertIn("- (B) w3: FAIL (net vs M shipped +0", text)
        self.assertIn("- registered reading (any w, any of A / B): PASS", text)

    def test_nothing_passes(self):
        with tempfile.TemporaryDirectory() as d:
            dump_files(d, "tune", w3="keep")
            dump_files(d, "dev", w3="keep")
            for f in ("L0w1.c64", "L0w1.c2"):        # w1 as well never switches
                for name in ("tune", "dev"):
                    with open(os.path.join(d, f"{f}.{name}.jsonl"), "w") as fh:
                        fh.write("".join(json.dumps(r) + "\n" for r in pattern_set("keep")))
            text = gate.run("tune", ["dev"], d, (1, 3), b_set="dev")
        self.assertIn("- (A) overall: FAIL", text)
        self.assertIn("- (B) overall: FAIL", text)
        self.assertIn("- registered reading (any w, any of A / B): FAIL", text)

    def test_misaligned_files_stop(self):
        with tempfile.TemporaryDirectory() as d:
            dump_files(d, "tune")
            recs = pattern_set("l0")
            recs[0]["ok"] = [0, 0]
            with open(os.path.join(d, "L0w1.c64.tune.jsonl"), "w") as fh:
                fh.write("".join(json.dumps(r) + "\n" for r in recs))
            with self.assertRaises(SystemExit):
                gate.load_set(d, "tune", (1, 3), with_m64=True)


class TestScoreCli(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = self.td.name
        self.rows, self.cands = synthetic(np.random.default_rng(1), 25)
        self.rows_path, self.dump_path = os.path.join(d, "r.txt"), os.path.join(d, "r.dump")
        write_rows(self.rows_path, self.rows)
        write_dump(self.dump_path, self.cands)
        self.top1 = sum(l0core.lenient(c[0][0]) == l0core.lenient(g) for (_p, g), c in zip(self.rows, self.cands))
        self.npz = os.path.join(d, "m.npz")
        tinylm.save_params(self.npz, tinylm.random_params(CHARS, 2, 32, 2, seed=3), CHARS, 2, 2)

    def tearDown(self):
        self.td.cleanup()

    def args(self, cmd, expect=None, out="o.jsonl"):
        a = [cmd, "--rows", self.rows_path, "--dump", self.dump_path, "--name", "t", "--expect-top1", str(self.top1 if expect is None else expect),
             "--out", os.path.join(self.td.name, out)]
        return a + (["--npz", self.npz] if cmd == "m64" else [])

    def test_expect_top1_mismatch_stops(self):
        for cmd in ("m64", "l0"):
            with self.assertRaises(SystemExit) as cm:
                score_l0.main(self.args(cmd, expect=self.top1 + 1) + (["--model", "x.pt", "--context", "64"] if cmd == "l0" else []))
            self.assertIn("STOP", str(cm.exception))
            self.assertFalse(os.path.exists(os.path.join(self.td.name, "o.jsonl")))

    def test_m64_end_to_end(self):
        score_l0.main(self.args("m64"))
        recs = [json.loads(l) for l in open(os.path.join(self.td.name, "o.jsonl"))]
        self.assertEqual(len(recs), 25)
        m = tinylm.NumpyLM(self.npz)
        self.assertEqual(recs[0]["noscore"], 1)                               # a 70-character candidate cannot be scored
        self.assertNotIn("s", recs[0])
        for r, (prefix, gold), cs in zip(recs[1:], self.rows[1:], self.cands[1:]):
            self.assertEqual(r["n"], len(cs))
            self.assertEqual(r["ok"], l0core.ok_flags(gold, [s for s, _ in cs]))
            if len(cs) < 2:
                self.assertEqual(r["s"], [0.0])
            else:
                want = tinylm.ll_many(m, [(l0core.k_cross(prefix, 63), s) for s, _ in cs])
                np.testing.assert_allclose(r["s"], want, atol=1e-12)
        self.assertEqual(recs[1]["ok"], [0, 1])                               # 她好 matches 他好 leniently


@unittest.skipUnless(HAVE_TORCH, "torch is not installed")
class TestTorch(unittest.TestCase):
    def tiny_gpt(self, seed=0):
        from torch_model import GPT
        torch.manual_seed(seed)
        g = GPT(len(CHARS) + 3, n_layer=2, d=32, n_head=2)
        with torch.no_grad():
            for p in g.parameters():      # default init has identity LayerNorms and zero biases: make every code path matter
                p.add_(torch.randn_like(p) * 0.15)
        return g

    def make_npz(self, td):
        p = os.path.join(td, "m.npz")
        tinylm.save_params(p, tinylm.random_params(CHARS, 2, 32, 2, seed=5), CHARS, 2, 2)
        return p

    def test_padding_invariance(self):
        import torch_l0
        torch.manual_seed(1)
        m = torch_l0.L0Model(self.tiny_gpt())
        with torch.no_grad():
            for p in list(m.keep_head.parameters()) + list(m.cand_head.parameters()):
                p.add_(torch.randn_like(p) * 0.5)          # the heads start nearly zero: make them matter
        m.eval()
        v = Vocab(CHARS)
        rows = [l0core.pack_row(v, "你我他好嗎的", [("你好", -1.0), ("我他嗎的是", -2.0), ("是", -3.0)], 64),
                l0core.pack_row(v, "天氣", [("好", -1.0), ("不了天氣好嗎", -2.5)], 64)]
        with torch.no_grad():
            both = m(**{k: v_ for k, v_ in torch_l0.to_tensors(l0core.collate(rows), "cpu").items() if k != "target"})
            for b, r in enumerate(rows):
                one = m(**{k: v_ for k, v_ in torch_l0.to_tensors(l0core.collate([r]), "cpu").items() if k != "target"})
                n = len(r.lens)
                np.testing.assert_allclose(both[b, :n].numpy(), one[0, :n].numpy(), atol=1e-5)
                self.assertTrue(torch.isinf(both[b, n:]).all())

    def test_loss_equals_masked_weighted_ce(self):
        import torch_l0
        s = torch.tensor([[0.3, 1.0, -0.5, float("-inf"), float("-inf"), float("-inf"), float("-inf"), float("-inf")],
                          [2.0, 0.1, 0.4, 0.9, float("-inf"), float("-inf"), float("-inf"), float("-inf")],
                          [0.0, -1.0, float("-inf"), float("-inf"), float("-inf"), float("-inf"), float("-inf"), float("-inf")]])
        tgt = torch.tensor([2, 0, 1])
        w = 3
        loss, ce, acc = torch_l0.list_loss(s, tgt, w)
        sn = s.numpy().astype(np.float64)
        tot = wt = 0.0
        ces = []
        for row, t in zip(sn, tgt.tolist()):
            fin = row[np.isfinite(row)]
            c = -(row[t] - np.log(np.exp(fin).sum()))
            ces.append(c)
            wi = w if t == 0 else 1
            tot += wi * c
            wt += wi
        self.assertAlmostEqual(float(loss), tot / wt, places=5)
        self.assertAlmostEqual(float(ce), float(np.mean(ces)), places=5)
        self.assertAlmostEqual(float(acc), 1 / 3, places=6)               # only the second row's argmax is its target

    def test_save_load_identical_scores(self):
        import torch_l0
        v = Vocab(CHARS)
        torch.manual_seed(2)
        m = torch_l0.L0Model(self.tiny_gpt(3)).eval()
        packs = [l0core.pack_row(v, "你我他好", [("你好", -1.0), ("我他嗎", -2.0)], 64), l0core.pack_row(v, "", [("是", -1.0), ("不了", -3.0), ("天", -4.0)], 2)]
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.pt")
            torch_l0.save_l0(m, CHARS, "ab" * 32, path)
            m2, v2, meta = torch_l0.load_l0(path)
            self.assertEqual(v2.chars, CHARS)
            self.assertEqual(meta["npz_sha256"], "ab" * 32)
            self.assertEqual(torch_l0.score_packs(m, packs), torch_l0.score_packs(m2, packs))

    def run_train(self, td, ws, out, **kw):
        import train_l0
        rng = np.random.default_rng(7)
        pairs = []
        for k in range(2):
            rows, cands = synthetic(rng, 30)
            r, d = os.path.join(td, f"r{k}.txt"), os.path.join(td, f"r{k}.dump")
            write_rows(r, rows)
            write_dump(d, cands)
            pairs += [r, d]
        argv = ["--npz", self.make_npz(td), "--data"] + pairs + ["--w"] + [str(w) for w in ws] + ["--out", out, "--batch", "4", "--max-steps", "5",
                "--warmup", "2", "--val-frac", "0.1", "--val-every", "3", "--device", "cpu"]
        seen = []
        orig = train_l0.prefetch
        train_l0.prefetch = lambda packs, batches, ahead=4: (seen.append([b.tolist() for b in batches]), orig(packs, batches, ahead))[1]
        try:
            train_l0.main(argv)
        finally:
            train_l0.prefetch = orig
        return seen

    def test_train_end_to_end_two_cells(self):
        import torch_l0
        with tempfile.TemporaryDirectory() as td:
            out1, out2 = os.path.join(td, "o1"), os.path.join(td, "o2")
            seen = self.run_train(td, [1, 3], out1)
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[0], seen[1])                              # same batches, same order for both cells
            self.assertEqual(len(seen[0]), 5)
            js = [json.load(open(os.path.join(out1, f"train-w{w}.json"))) for w in (1, 3)]
            c = js[0]["counts"]
            self.assertEqual(c["rows_read"], 60)
            self.assertEqual(c["trainable"] + sum(c["untrainable"].values()), 60)
            self.assertGreaterEqual(c["untrainable"]["too_long"], 2)        # the first row of each file
            self.assertGreater(c["untrainable"]["n<2"], 0)
            self.assertEqual(js[0]["train_order_sha256"], js[1]["train_order_sha256"])
            self.assertEqual(js[0]["steps"], 5)
            self.assertEqual([v[0] for v in js[0]["val_curve"]], [3, 5])
            self.assertEqual(len(js[0]["curve"]), 1)
            self.assertTrue(all(math.isfinite(x[1]) for x in js[0]["curve"]))
            self.assertEqual(len(js[0]["input_sha256"]["files"]), 4)
            m1, _, meta = torch_l0.load_l0(os.path.join(out1, "l0-w1.pt"))
            self.assertEqual(meta["npz_sha256"], js[0]["input_sha256"]["npz"])
            # reproducible on CPU, and a cell does not depend on the cell trained before it
            self.run_train(td, [1], out2)
            a, b = (torch.load(os.path.join(o, "l0-w1.pt"), weights_only=True)["state"] for o in (out1, out2))
            for k in a:
                self.assertTrue(torch.equal(a[k], b[k]), k)
            w3 = torch.load(os.path.join(out1, "l0-w3.pt"), weights_only=True)["state"]
            self.assertFalse(all(torch.equal(a[k], w3[k]) for k in a))      # the cells differ

    def test_divergence_rule(self):
        import train_l0
        self.assertFalse(train_l0.diverged(2.0, None, 100, 100))             # the first logged value is never compared
        self.assertFalse(train_l0.diverged(1.9, 2.0, 200, 100))
        self.assertTrue(train_l0.diverged(2.1, 2.0, 200, 100))
        self.assertFalse(train_l0.diverged(2.1, 2.0, 100, 100))              # still in warm-up
        self.assertTrue(train_l0.diverged(float("nan"), 2.0, 50, 100))
        self.assertTrue(train_l0.diverged(float("inf"), None, 50, 100))

    def test_score_l0_end_to_end(self):
        import torch_l0
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "o")
            self.run_train(td, [1], out)
            rows, cands = synthetic(np.random.default_rng(11), 12)
            rows[3], cands[3] = ("你我他好嗎的是不了天氣" * 4, "我他"), [("你好", -1.0), ("我他", -2.0), ("嗎", -3.0)]   # long prefix: contexts 64 and 2 differ
            write_rows(os.path.join(td, "s.txt"), rows)
            write_dump(os.path.join(td, "s.dump"), cands)
            top1 = sum(l0core.lenient(c[0][0]) == l0core.lenient(g) for (_p, g), c in zip(rows, cands))
            res = {}
            for ctx in (64, 2):
                p = os.path.join(td, f"c{ctx}.jsonl")
                score_l0.main(["l0", "--model", os.path.join(out, "l0-w1.pt"), "--rows", os.path.join(td, "s.txt"), "--dump", os.path.join(td, "s.dump"),
                               "--name", "t", "--expect-top1", str(top1), "--context", str(ctx), "--out", p, "--device", "cpu", "--bs", "3"])
                res[ctx] = [json.loads(l) for l in open(p)]
            model, v, _ = torch_l0.load_l0(os.path.join(out, "l0-w1.pt"))
            for ctx, recs in res.items():
                self.assertEqual(len(recs), 12)
                self.assertEqual(recs[0].get("noscore"), 1)                     # the 70-character candidate
                for r, (prefix, gold), cs in zip(recs[1:], rows[1:], cands[1:]):
                    self.assertEqual(r["ok"], l0core.ok_flags(gold, [s for s, _ in cs]))
                    if len(cs) < 2:
                        self.assertEqual(r["s"], [0.0])
                        continue
                    # one candidate at a time, no padding, sequences spelled out by hand
                    h = []
                    with torch.no_grad():
                        for s, _ in cs:
                            k = l0core.k_cross(prefix, 62 - len(s)) if ctx == 64 else tinylm.context_key(prefix)
                            ids = torch.tensor([[BOS] + v.enc(k) + [BOS] + v.enc(s)])
                            h.append(torch_l0.hidden(model.gpt, ids)[0, -1])
                        f = torch.from_numpy(l0core.features([sc for _, sc in cs]))
                        want = [float(model.keep_head(h[0]))] + [float(model.cand_head(torch.cat([h[i], f[i]]))) for i in range(1, len(cs))]
                    np.testing.assert_allclose(r["s"], want, atol=1e-5)
            self.assertNotEqual(res[64][3]["s"], res[2][3]["s"])


if __name__ == "__main__":
    unittest.main()
