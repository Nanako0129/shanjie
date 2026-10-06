"""Offline tests for run_ll_cuda.py (no torch, no model): python3 -m unittest test_run_ll_cuda   (from this directory)"""
import contextlib
import io
import json
import math
import os
import random
import tempfile
import unittest

import run_ll_cuda as R
import s5k

HERE = os.path.dirname(os.path.abspath(__file__))
V = 12
POOL = "今天氣很好我們等一下要去吃飯再見謝你的他她是不在有人這那說會對可以"


def enc(text):  # char-level fake tokenizer; prefix tokens always lead the full encoding
    return [ord(c) % V for c in text]


def fake_lps(ids):  # deterministic, causal fake logits (row i depends only on ids[:i+1]) -> token log-probs
    logits = [[random.Random(f"{ids[: i + 1]}{v}").gauss(0, 2) for v in range(V)] for i in range(len(ids))]
    return R.logprobs_from_logits(logits, ids)


def fixture_rows(n=20):
    rng = random.Random(5)
    return [{"cands": ["".join(rng.choice(POOL) for _ in range(6)) for _ in range(8)], "ctx": "", "truth": "今天天氣很好",
             "margin": 0.1, "half": "A"} for _ in range(n)]


def sentence_chars(rows):
    return {c for r in rows for s in r["cands"] + [r["truth"]] for c in [s]} | {R.HEAD}


def run_cli(argv, rows, load, tmp):
    """Run R.cli() with the fixture rows and a fake model; -> (exit code or None, stdout, stderr)."""
    out, err, code = io.StringIO(), io.StringIO(), None
    old = (s5k.load_rows, s5k.out_dir, R.load_model, os.sys.argv)
    s5k.load_rows = lambda name, limit=0: rows[:limit] if limit else rows
    s5k.out_dir = lambda name, limit=0: tmp
    R.load_model, os.sys.argv = load, ["run_ll_cuda.py"] + argv
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                R.cli()
            except SystemExit as e:
                code = e.code
    finally:
        s5k.load_rows, s5k.out_dir, R.load_model, os.sys.argv = old
    return code, out.getvalue(), err.getvalue()


def good_model():
    return enc, fake_lps, {"torch": "x", "gpu": "fake", "kv_reuse": False}, lambda: 1.0


class Pure(unittest.TestCase):
    def test_score_equals_hand_computed(self):
        ln = math.log
        ids, n = [0, 1, 2, 1], 2  # prefix = ids[:2]
        logits = [[0, 0, 0], [0, 0, 0], [0, ln(2), 0]]  # row i predicts ids[i+1]
        lps = R.logprobs_from_logits(logits, ids)
        self.assertAlmostEqual(lps[1], -ln(3))
        self.assertAlmostEqual(R.candidate_score(lps, n), -ln(3) - ln(2))  # P(2|..)=1/3, P(1|..)=2/4
        self.assertAlmostEqual(R.candidate_score(lps, n), sum(lps) - sum(lps[: n - 1]))  # logP(full) - logP(prefix)

    def test_boundary_mismatch_skips_and_counts(self):
        self.assertIsNone(R.split_candidate([1, 2], [1, 3, 4]))
        self.assertIsNone(R.score_row([1, 2], [[1, 2, 5], [1, 3, 4]], lambda f: [0.0] * (len(f) - 1)))
        self.assertEqual(R.split_candidate([1, 2], [1, 2, 9]), 2)
        rows, tmp = fixture_rows(), tempfile.mkdtemp()
        bad_enc = lambda t: enc(t) if t.endswith("句子：") else [99] + enc(t)[1:]  # full != prefix + ...
        path = os.path.join(tmp, "x.jsonl")
        bad = R.run_loop(rows, [(k, "") for k in range(3)], path, bad_enc, fake_lps)
        self.assertEqual(bad, 3)
        self.assertTrue(all(json.loads(l)["scores"] is None for l in open(path, encoding="utf-8")))
        with self.assertRaises(SystemExit) as cm:
            R.finish(path, 0)
        self.assertEqual(cm.exception.code, "s5k: STOP token boundary mismatch above 1%")

    def test_ties_go_to_earlier_rank(self):
        self.assertEqual(s5k.pick([-1.0, -1.0, -1.0]), 0)
        self.assertEqual(s5k.pick([-3.0, -1.0, -1.0]), 1)
        same = R.score_row([1], [[1, 2], [1, 2]], lambda f: [-2.0])
        self.assertEqual(same, [-2.0, -2.0])
        self.assertEqual(s5k.pick(same), 0)

    def test_prefix_only_triggers_degeneracy_stop(self):
        rows = fixture_rows()
        todo = [(k, "") for k in range(len(rows))]

        def recs(score):
            p = os.path.join(tempfile.mkdtemp(), "x.jsonl")
            R.run_loop(rows, todo, p, enc, fake_lps, score)
            return [json.loads(l)["scores"] for l in open(p, encoding="utf-8")]

        def prefix_only(pids, fulls, lps_fn):  # wrong: logP(prefix) only, the candidate never contributes
            return [sum(lps_fn(f)[: len(pids) - 1]) for f in fulls]

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(s5k.degeneracy(recs(R.score_row), 1e-6, "Q8-ll", "none"))
            self.assertTrue(s5k.degeneracy(recs(prefix_only), 1e-6, "Q8-ll", "none"))


class Cli(unittest.TestCase):
    def test_exit_code_3_on_prefix_only_and_stdout_has_no_sentences(self):
        rows, tmp = fixture_rows(), tempfile.mkdtemp()
        orig = R.score_row
        R.score_row = lambda pids, fulls, f: [sum(f(x)[: len(pids) - 1]) for x in fulls]
        try:
            code, out, err = run_cli(["--set", "dev302", "--limit", "20", "--ctx", "none"], rows, good_model, tmp)
        finally:
            R.score_row = orig
        self.assertEqual(code, 3)
        self.assertIn("DEGENERATE", err)
        self.assert_no_text(rows, out, err)

    def assert_no_text(self, rows, *streams):
        for s in streams:
            for r in rows[:5]:
                self.assertNotIn(r["cands"][0], s)
            self.assertNotIn("句子", s)
            self.assertNotIn(R.HEAD, s)

    def test_schema_matches_run_ll_and_stdout_clean(self):
        rows, tmp = fixture_rows(), tempfile.mkdtemp()
        code, out, err = run_cli(["--set", "dev302", "--limit", "20", "--ctx", "synth"], rows, good_model, tmp)
        self.assertIsNone(code, err)
        self.assertEqual(out.split()[0], "ctx_chars=10")
        mine = json.loads(open(os.path.join(tmp, "Q8-ll.ctx.jsonl"), encoding="utf-8").readline())
        ref = json.loads(open(os.path.join(HERE, "results/dev302/Q-ll.noctx.jsonl"), encoding="utf-8").readline())
        self.assertEqual(set(mine), set(ref))  # {"k", "scores", "ms"}
        meta = json.loads(open(os.path.join(tmp, "meta-188.jsonl"), encoding="utf-8").readline())
        ref_meta = [json.loads(l) for l in open(os.path.join(HERE, "results/dev302/meta.jsonl"), encoding="utf-8")][1]
        self.assertTrue(set(ref_meta) <= set(meta))  # run_ll's meta keys plus version info
        self.assertEqual(len(mine["scores"]), 8)
        self.assert_no_text(rows, out, err)

    def test_error_paths_carry_no_sentences(self):
        rows, tmp = fixture_rows(), tempfile.mkdtemp()
        argv = ["--set", "dev302", "--limit", "20", "--ctx", "none"]
        # (1) wrong hash, real s5k.load_rows on a file that contains sentences
        f = os.path.join(tmp, "rows.jsonl")
        open(f, "w", encoding="utf-8").write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
        old = s5k.HASHES[("dev302", "rows.jsonl")]
        s5k.HASHES[("dev302", "rows.jsonl")] = (f, "0" * 64)
        out, err, code = io.StringIO(), io.StringIO(), None
        oldargv = os.sys.argv
        os.sys.argv = ["x"] + argv
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    R.cli()
                except SystemExit as e:
                    code = e.code
        finally:
            s5k.HASHES[("dev302", "rows.jsonl")], os.sys.argv = old, oldargv
        self.assertEqual(code, "s5k: INPUT HASH MISMATCH")
        self.assert_no_text(rows, out.getvalue(), err.getvalue(), str(code))
        # (2) unexpected exception whose message carries a sentence
        def boom_model():
            def boom(ids):
                raise RuntimeError(rows[0]["cands"][0])
            return enc, boom, {}, lambda: 0.0
        code, out, err = run_cli(argv, rows, boom_model, tempfile.mkdtemp())
        self.assertEqual(code, "s5k: unexpected RuntimeError")
        self.assert_no_text(rows, out, err, str(code))
        # (3) boundary mismatch above 1%
        def bad_model():
            return (lambda t: enc(t) if t.endswith("句子：") else [99] + enc(t)[1:]), fake_lps, {}, lambda: 0.0
        code, out, err = run_cli(argv, rows, bad_model, tempfile.mkdtemp())
        self.assertEqual(code, "s5k: STOP token boundary mismatch above 1%")
        self.assert_no_text(rows, out, err, str(code))
        # (4) non-finite scores
        def nan_model():
            return enc, (lambda ids: [float("nan")] * (len(ids) - 1)), {}, lambda: 0.0
        code, out, err = run_cli(argv, rows, nan_model, tempfile.mkdtemp())
        self.assertEqual(code, "s5k: STOP non-finite scores")
        self.assert_no_text(rows, out, err, str(code))


if __name__ == "__main__":
    unittest.main()
