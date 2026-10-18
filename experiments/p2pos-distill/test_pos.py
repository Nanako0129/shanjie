"""Tests of the position-level distillation code (synthetic data only). Everything but TestTorch runs without torch / onnx; TestTorch is
skipped without torch (main runs it on Colab before training):
  python3 -m unittest discover -s experiments/p2pos-distill -p 'test_*.py' -v"""
import gzip
import io
import json
import math
import os
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(HERE)
sys.path[:0] = [HERE, os.path.join(EXP, "p2-distill"), os.path.join(EXP, "p2b-reading"), os.path.join(EXP, "p2c-data"),
                os.path.join(EXP, "s7a-tinylm"), os.path.join(EXP, "x0c")]
import teacher_pos as tpos_t   # noqa: E402
import train_pos as tpos       # noqa: E402
import train_r                 # noqa: E402
import p2posana as ana         # noqa: E402
import tinylm                  # noqa: E402
import score_0c as z           # noqa: E402
from test_p2 import CHARS, make_data, write_files, tiny_npz   # noqa: E402  P2's synthetic rows / dump / teacher writers

LN10 = math.log(10.0)
LEX = """# format org.openvanilla.mcbopomofo.sorted
_ctrl_punctuation_! ！ 0.0
ㄋㄧˇ 你 -2.9
ㄋㄧˇ 妳 -4.0
ㄋㄧˇ 你 -5.0
ㄋㄧˇ-ㄏㄠˇ 你好 -3.0
ㄏㄠˇ 好 -3.1
ㄏㄠˇ 郝 -6.0
ㄅㄚ˙ 吧 -2.0
"""


# ------------------------------------------------------------------ teacher_pos
class FakeZuin(z.Zuin):
    """0c with a tiny vocabulary: characters 你妳好郝吧 (ids 8..12, output columns 2..6), bases ㄋㄧ ㄏㄠ ㄅㄚ."""

    def __init__(self):
        super().__init__(None, {"characters": list("你妳好郝吧"), "bases": ["ㄋㄧ", "ㄏㄠ", "ㄅㄚ"]}, {})


def fake_logsm(table):
    """logsm(context, reading, gold) -> the same (n, V) log-softmax rows for every call, plus the context seen."""
    seen = []

    def f(context, reading, gold):
        seen.append(context)
        return table[:len(gold)]
    f.seen = seen
    return f


def softmax_table(n, V=7, seed=3):
    x = np.random.default_rng(seed).normal(size=(n, V))
    return x - np.log(np.exp(x).sum(1, keepdims=True))


class TestTeacher(unittest.TestCase):
    def test_homophones(self):
        H = tpos_t.load_homophones(io.StringIO(LEX))
        self.assertEqual(H["ㄋㄧˇ"], ["你", "妳"])                    # the duplicate 你 and the two-syllable word are not entries
        self.assertEqual(H["ㄏㄠˇ"], ["好", "郝"])
        self.assertNotIn("ㄋㄧˇ-ㄏㄠˇ", H)
        self.assertEqual(H["_ctrl_punctuation_!"], ["！"])
        self.assertNotIn("#", "".join(H))

    def test_restrict_mass_and_renormalisation(self):
        row = np.log(np.array([0.1, 0.2, 0.3, 0.15, 0.25]))
        lq, out = tpos_t.restrict(row, [1, 2])                      # inside H: 0.2 + 0.3
        self.assertAlmostEqual(out, 0.5, places=12)
        np.testing.assert_allclose(np.exp(lq), [0.4, 0.6], rtol=1e-12)
        lq, out = tpos_t.restrict(row, [0, 1, 2, 3, 4])
        self.assertEqual(out, 0.0)                                  # whole vocabulary: nothing outside (never negative)
        self.assertAlmostEqual(float(np.exp(lq).sum()), 1.0, places=12)

    def test_row_record_context_and_columns(self):
        m, H = FakeZuin(), tpos_t.load_homophones(io.StringIO(LEX))
        tab = softmax_table(3)
        f = fake_logsm(tab)
        rec, kind = tpos_t.teacher_row(m, H, "天氣", "你好吧", ["ㄋㄧˇ", "ㄏㄠˇ", "ㄅㄚ˙"], f)
        self.assertIsNone(kind)
        self.assertEqual(f.seen, ["天氣"])                           # the student's context_key goes to 0c as the context
        self.assertEqual([c for c, _ in rec["pos"]], ["你妳", "好郝", "吧"])
        # character 你 has id 8 -> column 2, 妳 id 9 -> column 3
        want = tab[0, [2, 3]]
        lse = np.log(np.exp(want).sum())
        np.testing.assert_allclose(rec["pos"][0][1], want - lse, atol=1e-5)
        self.assertAlmostEqual(rec["out"][0], 1 - math.exp(lse), places=5)
        self.assertEqual(rec["pos"][2][1], [0.0])                   # a one-character H: log q = 0
        json.dumps(rec)

    def test_skip_reasons(self):
        m, H = FakeZuin(), tpos_t.load_homophones(io.StringIO(LEX))
        f = fake_logsm(softmax_table(3))
        S = ["ㄋㄧˇ", "ㄏㄠˇ"]
        cases = [("你龘", S, "oov"), ("你好吧", S, "length"), ("你", ["ㄋㄧˇ"], "syllables"), ("你好", ["ㄋㄧˇ", "ㄉㄚˇ"], "base"),
                 ("你好", ["ㄋㄧˊ", "ㄏㄠˇ"], "empty_h")]          # ㄋㄧˊ is a known base but has no entry in the lexicon
        for gold, reading, why in cases:
            rec, kind = tpos_t.teacher_row(m, H, "", gold, reading, f)
            self.assertEqual((kind, rec), (why, {"skip": 1, "why": why}), gold)
        self.assertEqual(f.seen, [])                                # no 0c run for a skipped row

    def test_one_gold_candidate_projects_one_row_per_position(self):
        m = FakeZuin()
        f = z.feeds_for(m, "天氣", ["ㄋㄧˇ", "ㄏㄠˇ", "ㄅㄚ˙"], ["你好吧"])      # what logsm_onnx feeds the graph
        self.assertEqual(len(f["proj_pos"]), 3)                          # so log_softmax has n rows; row j predicts character j
        self.assertEqual(f["target_node"].tolist(), [[0, 1, 2]])
        self.assertEqual(f["target_ids"].tolist(), [[2, 4, 6]])           # 你 id 8, 好 id 10, 吧 id 12, minus 6

    def run_main(self, td, rows, table, extra=()):
        rp, lex, out = (os.path.join(td, n) for n in ("rows.txt", "lex.txt", "out.pos.jsonl.gz"))
        with open(rp, "w", encoding="utf-8") as f:
            f.write("".join(f"{p}|{g}|{' '.join(s)}\n" for p, g, s in rows))
        with open(lex, "w", encoding="utf-8") as f:
            f.write(LEX)
        old = tpos_t.load_model, tpos_t.logsm_onnx
        tpos_t.load_model, tpos_t.logsm_onnx = (lambda *a: FakeZuin()), (lambda m, c, r, g: table[:len(g)])
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                try:
                    tpos_t.main(["--model-dir", "x", "--rows", rp, "--lexicon", lex, "--out", out, *extra])
                    rc = 0
                except SystemExit as e:
                    rc = e.code
        finally:
            tpos_t.load_model, tpos_t.logsm_onnx = old
        return rc, buf.getvalue(), list(tpos.read_pos(out)), os.listdir(td)

    def test_main_writes_every_row_in_order_and_flags_the_stop(self):
        rows = [("天氣", "你好", ["ㄋㄧˇ", "ㄏㄠˇ"]), ("", "你龘", ["ㄋㄧˇ", "ㄏㄠˇ"]), ("你", "好吧", ["ㄏㄠˇ", "ㄅㄚ˙"])]
        in_h = np.full((3, 7), -30.0)
        in_h[0, 2:4] = in_h[1, 4:6] = np.log(0.5)                        # position 0: 你妳, position 1: 好郝 -- H holds all the mass
        off_h = np.full((3, 7), -30.0)
        off_h[:, 0] = 0.0                                                # all the mass on a column that is no character of any H
        with tempfile.TemporaryDirectory() as td:
            rc, text, recs, files = self.run_main(td, rows, in_h, extra=["--limit", "2"])
            self.assertEqual(rc, 0)
            self.assertEqual([(r["i"], r["skip"]) for r in recs], [(1, 0), (2, 1)])      # --limit 2; the skipped row is still a line
            self.assertIn("skipped: oov 1", text)
            self.assertFalse([f for f in files if ".part" in f])
        with tempfile.TemporaryDirectory() as td:
            rc, text, recs, _ = self.run_main(td, [rows[0], rows[2]], off_h)
            self.assertEqual(rc, 3)                                      # median outside-H mass ~100% > 20%
            self.assertIn("STOP", text)
            self.assertEqual(len(recs), 2)

    def test_summary_and_gz_roundtrip(self):
        text, med = tpos_t.summary({None: 3, "oov": 1, "empty_h": 2}, [0.0, 0.1, 0.3, 0.5, 0.9])
        self.assertAlmostEqual(med, 0.3)
        self.assertIn("scored 3", text); self.assertIn("empty_h 2, oov 1", text); self.assertIn("median 30.00%", text)
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.pos.jsonl.gz")
            with tpos_t.open_text(p, "wt") as f:
                f.write(json.dumps({"i": 1, "skip": 1, "why": "oov"}) + "\n")
            self.assertEqual(list(tpos.read_pos(p)), [{"i": 1, "skip": 1, "why": "oov"}])
            with gzip.open(p, "rt", encoding="utf-8") as g:
                self.assertEqual(json.loads(g.readline())["why"], "oov")


# ------------------------------------------------------------------ joining and packing
VOCAB = tinylm.Vocab(CHARS)


def pos_rec(rows, i, rnd, skip=False, oov_at=None, empty_at=None):
    """Teacher record for 1-based row i: 3..5 characters per position drawn from the student's CHARS plus the OOV 龘."""
    gold = rows[i - 1][0]
    if skip:
        return {"i": i, "skip": 1, "why": "oov"}
    pos = []
    for j in range(len(gold)):
        cs = rnd.sample(CHARS, rnd.randint(3, 5)) + ["龘"]
        if j == oov_at:
            cs = ["龘", "龍"]
        lq = np.log(np.array([rnd.uniform(0.1, 1) for _ in cs]))
        lq -= np.log(np.exp(lq).sum())
        pos.append(["".join(cs), [round(float(x), 5) for x in lq]])
    return {"i": i, "skip": 0, "pos": pos, "out": [0.1] * len(gold)}


def make_pos(rows, skip=(), empty=()):
    rnd = random.Random(5)
    return [pos_rec(rows, i, rnd, skip=i in skip, oov_at=0 if i in empty else None) for i in range(1, len(rows) + 1)]


class TestJoin(unittest.TestCase):
    def test_restrict_student(self):
        r = tpos.restrict_student("你龘好", [-1.0, -0.5, -2.0], VOCAB.ids)
        self.assertEqual(list(r[0]), [VOCAB.ids["你"], VOCAB.ids["好"]])
        np.testing.assert_allclose(np.exp(r[1]), np.exp([-1.0, -2.0]) / np.exp([-1.0, -2.0]).sum())
        self.assertIsNone(tpos.restrict_student("龘龍", [0.0, -1.0], VOCAB.ids))

    def test_join_counts_and_alignment(self):
        rows, cands, teacher = make_data()                           # unusable for P2: row 3 (n<2), 5 (too long), 7 (0c skip)
        recs = make_pos(rows, skip={2, 3}, empty={9})
        items, posrows, c = tpos.join_pos(rows, cands, teacher, iter(recs), VOCAB)
        self.assertEqual(len(items), len(rows) - 3)
        self.assertEqual(c["usable"], len(items))
        self.assertEqual(c["pos_unusable"], {"pos_skip": 1, "gold_too_long": 0, "empty_h_student": 1})   # row 3 was not P2-usable anyway
        self.assertEqual(c["pos_usable"], len(items) - 2)
        none = [n for n, p in enumerate(posrows) if p is None]
        self.assertEqual(len(none), 2)
        prefix, gold, ps = next(p for p in posrows if p is not None)
        self.assertEqual(len(ps), len(gold))
        self.assertTrue(all(abs(np.exp(l).sum() - 1) < 1e-9 and (t >= 3).all() for t, l in ps))   # no UNK: the OOV 龘 is gone
        n0 = next(n for n, p in enumerate(posrows) if p is not None)
        self.assertEqual(items[n0][0], posrows[n0][0])               # the P2 item and the position row are the same row

    def test_join_mismatches_stop(self):
        rows, cands, teacher = make_data()
        recs = make_pos(rows)
        for bad in (recs[:-1], recs + [dict(recs[0])], [dict(recs[0], i=2)] + recs[1:]):
            with self.assertRaises(tpos.Mismatch):
                tpos.join_pos(rows, cands, teacher, iter(bad), VOCAB)
        short = [dict(r, pos=r["pos"][:-1]) if r["i"] == 1 else r for r in recs]
        with self.assertRaises(tpos.Mismatch):
            tpos.join_pos(rows, cands, teacher, iter(short), VOCAB)

    def test_gold_too_long(self):
        rows, cands, teacher = make_data(10)
        rows[1] = ("好" * 64, ["ㄏㄠˇ"] * 64, "")                     # cands/teacher of row 2 are unchanged: only the gold is long
        rnd = random.Random(1)
        recs = [pos_rec(rows, i, rnd) for i in range(1, 11)]
        _, _, c = tpos.join_pos(rows, cands, teacher, iter(recs), VOCAB)
        self.assertEqual(c["pos_unusable"]["gold_too_long"], 1)


def numpy_pos_loss(m, b):
    """Reference for pos_loss_rows: m a NumpyLM, b a collate_pos batch -> (B,) sum_j KL(q_j || softmax over H of the logits)."""
    lp = m.logp10(b["ids"]) * LN10
    out = np.zeros(b["ids"].shape[0])
    for p in range(len(b["pos_row"])):
        k = b["hmask"][p]
        g = lp[b["pos_row"][p], b["pos_col"][p]][b["htok"][p][k]]
        logp = g - (g.max() + np.log(np.exp(g - g.max()).sum()))
        q, lq = b["hq"][p][k].astype(np.float64), b["hlq"][p][k].astype(np.float64)
        out[b["pos_row"][p]] += float((q * (lq - logp)).sum())
    return out


def brute_pos_loss(m, posrows, r):
    """The same number for one row without the batching: [BOS] k gold through the model, position by position."""
    prefix, gold, ps = posrows[r]
    key = tinylm.row_key(prefix, 2)
    k = key[max(0, len(key) - (tinylm.CTX - 1 - len(gold))):]
    ids = np.array([[tinylm.BOS] + VOCAB.enc(k) + VOCAB.enc(gold)])
    lp = m.logp10(ids)[0] * LN10
    tot = 0.0
    for j, (tok, lq) in enumerate(ps):
        g = lp[len(k) + j][tok]
        logp = g - (g.max() + np.log(np.exp(g - g.max()).sum()))
        tot += float((np.exp(lq) * (lq - logp)).sum())
    return tot


class TestPackAndLoss(unittest.TestCase):
    def setUp(self):
        rows, cands, teacher = make_data(40)
        rows.append(("好" * 62, ["ㄏㄠˇ"] * 62, "你好"))               # 62 characters: k is cut from the left to fit the context
        cands.append([("好" * 62, -1.0), ("好" * 61 + "你", -2.0)])
        teacher.append({"i": 41, "n": 2, "skip": 0, "ll": [-1.0, -2.0]})
        rows.append(("今天", ["ㄐㄧㄣ", "ㄊㄧㄢ"], ""))               # empty key
        cands.append([("今天", -1.0), ("天氣", -2.0)])
        teacher.append({"i": 42, "n": 2, "skip": 0, "ll": [-1.0, -2.0]})
        self.items, self.posrows, _ = tpos.join_pos(rows, cands, teacher, iter(make_pos(rows, skip={2})), VOCAB)
        self.P = tpos.PackedPos(self.posrows, VOCAB)
        with tempfile.TemporaryDirectory() as td:
            self.m = tinylm.NumpyLM(tiny_npz(td))

    def test_valid_and_collate_layout(self):
        P = self.P
        self.assertEqual(int(P.valid.sum()), sum(p is not None for p in self.posrows))
        idx = np.array([n for n in range(len(P)) if P.valid[n]][::-1][:9])
        b = tpos.collate_pos(P, idx)
        for p in range(len(b["pos_row"])):                            # every scored position predicts the next gold character
            r = idx[b["pos_row"][p]]
            j = int(b["pos_col"][p] - (P.st[r] - 1))
            self.assertEqual(b["ids"][b["pos_row"][p], b["pos_col"][p] + 1], VOCAB.ids[self.posrows[r][1][j]])
        self.assertEqual(len(b["pos_row"]), sum(len(self.posrows[r][1]) for r in idx))
        np.testing.assert_allclose(b["hq"].sum(1), 1.0, atol=1e-6)    # each position's q sums to 1 over its own slots
        self.assertTrue((b["hq"][~b["hmask"]] == 0).all() and (b["hlq"][~b["hmask"]] == 0).all())
        self.assertLessEqual(b["ids"].shape[1], tinylm.CTX)
        # the dense H of a position equals the packed one
        r0 = idx[0]
        np.testing.assert_array_equal(b["htok"][0][b["hmask"][0]], self.posrows[r0][2][0][0])

    def test_loss_reference_matches_brute_force(self):
        idx = np.array([n for n in range(len(self.P)) if self.P.valid[n]])
        for s in range(0, len(idx), 7):
            sel = idx[s:s + 7]
            got = numpy_pos_loss(self.m, tpos.collate_pos(self.P, sel))
            for g, r in zip(got, sel):
                self.assertAlmostEqual(g, brute_pos_loss(self.m, self.posrows, r), places=5)   # hlq is float32
        self.assertTrue(len(idx) > 30)

    def test_weights_for_arm_r(self):
        q = np.array([[0.6, 0.4, 0, 0], [0.3, 0.7, 0, 0], [0.5, 0.5, 0, 0], [0.1, 0.1, 0.8, 0]])
        np.testing.assert_array_equal(train_r.row_weights(q, 4), [1, 4, 1, 4])
        self.assertEqual(train_r.row_weights(q, 4).dtype, np.float32)
        items, _ = tpos.tp.join_rows(*make_data())
        P2 = tpos.tp.Packed(items, VOCAB)
        w = train_r.row_weights(P2.q, 4)
        want = [1 if int(np.argmax(ll)) == 0 else 4 for _, _, ll in items]
        np.testing.assert_array_equal(w, want)
        self.assertTrue(set(w.tolist()) <= {1.0, 4.0})


# ------------------------------------------------------------------ the readout
def srec(i, ok, ng, ll):
    return {"i": i, "n": len(ok), "ok": ok, "ng": ng, "ll": ll}


class TestAna(unittest.TestCase):
    def test_formal_noninferiority(self):
        # formal values (0.7, 1, 2): rerank only when the n-gram margin < 1, and the fused winner must beat the first by >= 2
        ng_close = [-1.0, -1.2]
        m = [srec(1, [0, 1], ng_close, [-3.0, -1.0]),      # fused: 1 beats 0 by 1.2 < 2 -> stays first (wrong), M and T alike
             srec(2, [0, 1], ng_close, [-5.0, -1.0]),      # M: 2.6 >= 2 -> picks 1 (fixed)
             srec(3, [1, 0], ng_close, [-5.0, -1.0]),      # first right; M: switches to the wrong 1 (broken)
             srec(4, [1, 0], [-1.0, -3.0], [-9.0, -1.0]),  # margin 2 >= tau -> never reranked
             srec(5, [0, 1], ng_close, [-5.0, -1.0])]
        t = [dict(r) for r in m]
        t[2] = srec(3, [1, 0], ng_close, [-1.0, -1.0])     # T keeps the first right
        t[4] = srec(5, [0, 1], ng_close, [-1.0, -5.0])     # T does not fix row 5
        r = ana.formal_noninferiority(m, t)
        self.assertEqual((r["M"]["fixed"], r["M"]["broken"], r["M"]["net"]), (2, 1, 1))
        self.assertEqual((r["T"]["fixed"], r["T"]["broken"], r["T"]["net"]), (1, 0, 1))
        self.assertEqual((r["T_vs_M"]["new_right"], r["T_vs_M"]["old_right"]), (1, 1))     # T right where M wrong: row 3; reverse: row 5
        self.assertAlmostEqual(r["T_vs_M"]["p"], 1.0)
        buf = io.StringIO()
        with redirect_stdout(buf):
            ana.print_formal(r, ana.FORMAL_SHIPPED)
        self.assertIn("T vs M", buf.getvalue())
        bad = [dict(x) for x in t]
        bad[0]["ok"] = [1, 0]
        with self.assertRaises(SystemExit):
            ana.formal_noninferiority(m, bad)


# ------------------------------------------------------------------ torch (Colab)
try:
    import torch
except ImportError:
    torch = None


def write_pos_file(td, name, rows, recs):
    p = os.path.join(td, name + ".pos.jsonl.gz")
    with tpos_t.open_text(p, "wt") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


@unittest.skipUnless(torch, "torch is not installed")
class TestTorch(unittest.TestCase):
    def test_loss_matches_numpy_reference_and_has_gradient(self):
        import torch_model as tm
        rows, cands, teacher = make_data(40)
        items, posrows, _ = tpos.join_pos(rows, cands, teacher, iter(make_pos(rows)), VOCAB)
        P = tpos.PackedPos(posrows, VOCAB)
        idx = np.array([n for n in range(len(P)) if P.valid[n]][:12])
        with tempfile.TemporaryDirectory() as td:
            path = tiny_npz(td)
            model, _ = tm.from_npz(path)
            ref = tinylm.NumpyLM(path)
        b = tpos.collate_pos(P, idx)
        t = tpos.to_tensors(b, "cpu")
        with torch.no_grad():
            got = tpos.pos_loss_rows(model.eval(), t).numpy()
        np.testing.assert_allclose(got, numpy_pos_loss(ref, b), atol=1e-4)
        model.train()
        tpos.pos_loss_rows(model, t).mean().backward()
        g = [p.grad for p in model.parameters() if p.grad is not None]
        self.assertTrue(all(torch.isfinite(x).all() for x in g))
        self.assertGreater(sum(float(x.abs().sum()) for x in g), 0)

    def common(self, td):
        rows, cands, teacher = make_data(40)
        a = write_files(td, "a", rows, cands, teacher)
        pa = write_pos_file(td, "a", rows, make_pos(rows, skip={4, 6}, empty={8}))
        rows2, cands2, teacher2 = make_data(30, seed=2)
        b = write_files(td, "b", rows2, cands2, teacher2)
        pb = write_pos_file(td, "b", rows2, make_pos(rows2))
        return a, pa, b, pb

    def args(self, td, out):
        return ["--npz", os.path.join(td, "m.npz"), "--out", out, "--epochs", "2", "--batch", "4", "--max-steps", "30", "--warmup", "5",
                "--lr", "1e-3", "--val-frac", "0.1", "--log-every", "1000", "--val-every", "1000", "--device", "cpu"]

    def test_arm_t_end_to_end(self):
        import score
        with tempfile.TemporaryDirectory() as td:
            tiny_npz(td)
            a, pa, b, pb = self.common(td)
            out = os.path.join(td, "out")
            tpos.main([*self.args(td, out), "--data", *a, pa, *b, pb])
            info = json.load(open(os.path.join(out, "train-T.json")))
            self.assertEqual(info["steps"] <= 30, True)
            self.assertEqual(info["counts"]["usable"], 64)
            self.assertLess(info["counts"]["pos_usable"], 64)
            self.assertEqual([v[0] for v in info["val_curve"]], [0, info["steps"]])
            self.assertEqual(set(info["val_curve"][0][1]), {"pos_kl", "pos_top1", "cand8_kl", "cand8_top1"})
            self.assertLessEqual(info["val_rows"], info["val_rows_p2"])
            self.assertEqual(len(info["input_sha256"]["files"]), 8)
            before, after = tinylm.NumpyLM(os.path.join(td, "m.npz")), score.load_model(os.path.join(out, "tinylm-T.npz"), "numpy")
            self.assertEqual(after.vocab.chars, before.vocab.chars)
            items = [("你好", "今天"), ("", "天氣")]
            self.assertGreater(np.abs(tinylm.ll_many(before, items) - tinylm.ll_many(after, items)).max(), 1e-5)
            out2 = os.path.join(td, "out2")
            tpos.main([*self.args(td, out2), "--data", *a, pa, *b, pb])
            self.assertEqual(info["train_order_sha256"], json.load(open(os.path.join(out2, "train-T.json")))["train_order_sha256"])

    def test_arm_r_end_to_end(self):
        import score
        with tempfile.TemporaryDirectory() as td:
            tiny_npz(td)
            a, _, b, _ = self.common(td)
            out = os.path.join(td, "out")
            train_r.main([*self.args(td, out), "--data", *a, *b])
            info = json.load(open(os.path.join(out, "train-R.json")))
            self.assertEqual(info["steps"], 30)
            self.assertEqual(info["counts"]["usable"], 64)
            self.assertTrue(0 < info["counts"]["boosted"] < 64)
            self.assertEqual(info["boost"], 4.0)
            self.assertEqual(score.load_model(os.path.join(out, "tinylm-R.npz"), "numpy").vocab.chars, list(CHARS))

    def test_count_mismatch_stops(self):
        with tempfile.TemporaryDirectory() as td:
            a, pa, _, _ = self.common(td)
            tiny_npz(td)
            rows, _, _ = make_data(40)
            short = write_pos_file(td, "short", rows, make_pos(rows)[:-1])
            with self.assertRaises(SystemExit) as cm:
                tpos.main([*self.args(td, os.path.join(td, "o")), "--data", *a, short])
            self.assertIn("STOP", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
