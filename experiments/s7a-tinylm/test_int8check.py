"""S7b section 2.3 tests (numpy only): the weight-only int8 copy and the int8-vs-fp32 verdict with fixed (alpha, tau)."""
import contextlib
import io
import json
import os
import tempfile
import unittest

import numpy as np

import int8check
import tinylm
from test_fusegrid import rec

FIX = dict(ng=[0.0, -0.1], ll=[-10.0, 0.0], ok=[0, 1])     # n-gram first is wrong; the reranker (alpha 0.7, tau 2) picks the right one
KEEP = dict(ng=[0.0, -0.1], ll=[0.0, -10.0], ok=[1, 0])    # n-gram first is right and stays


def write_sets(d, model, rows_by_set):
    for setting in ("chat", "formal"):
        for s, rows in rows_by_set.items():
            with open(os.path.join(d, f"{model}.{setting}.{s}.jsonl"), "w", encoding="utf-8") as f:
                f.writelines(json.dumps(rec(i + 1, r["ng"], r["ll"], r["ok"])) + "\n" for i, r in enumerate(rows))


def run(d, sets=("cvtune", "wikitune", "dev302", "typing76", "user-reported", "discordtune"), frozen=(0.1, 0.5)):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        enough = int8check.compare(int8check_args(d, sets, frozen))
    return enough, out.getvalue()


def int8check_args(d, sets, frozen=(0.1, 0.5)):
    # (0.1, 0.5): what fusegrid.select picks on these tuning sets (every cell ties, or the smallest one that fixes FIX rows)
    import argparse
    return argparse.Namespace(scores=d, out=os.path.join(d, "out"), fp32="M", int8="M8", chat=list(frozen), formal=list(frozen), sets=list(sets))


GOOD = {"cvtune": [KEEP] * 20, "wikitune": [KEEP] * 20, "dev302": [KEEP] * 10, "typing76": [KEEP] * 10, "user-reported": [KEEP] * 10,
        "discordtune": [FIX] * 100 + [KEEP] * 100}


class TestDequant(unittest.TestCase):
    def test_matrices_are_int8_per_row_and_vectors_unchanged(self):
        p = tinylm.random_params("一二三四五", n_layer=1, d=8, n_head=2, seed=3)
        with tempfile.TemporaryDirectory() as td:
            src, out = os.path.join(td, "m.npz"), os.path.join(td, "m8.npz")
            tinylm.save_params(src, p, "一二三四五", 1, 2)
            int8check.dequant(src, out)
            with np.load(src) as za, np.load(out) as zb:          # closed before the temp dir goes (Windows)
                a, b = {k: za[k] for k in za.files}, {k: zb[k] for k in zb.files}
            self.assertEqual(sorted(a), sorted(b))
            for k in a:
                x, y = a[k], b[k]
                if x.dtype.kind == "f" and x.ndim == 2:
                    scale = np.abs(x).max(axis=1, keepdims=True) / 127.0
                    self.assertTrue(np.all(np.abs(x - y) <= scale / 2 + 1e-7), k)              # rounding error at most half a step
                    self.assertTrue(np.allclose(np.round(y / scale), y / scale, atol=1e-3), k)  # on the int8 grid
                    self.assertFalse(np.array_equal(x, y), k)
                else:
                    self.assertTrue(np.array_equal(x, y), k)


class TestCompare(unittest.TestCase):
    def test_fp32_failing_section_4_is_not_applicable(self):
        flat = dict(GOOD, discordtune=[KEEP] * 200)                      # nothing to gain on discordtune: fp32 fails section 4 too
        with tempfile.TemporaryDirectory() as td:
            write_sets(td, "M", flat); write_sets(td, "M8", flat)
            enough, out = run(td)
            self.assertIsNone(enough, out)
            self.assertIn("verdict: not applicable", out)
            self.assertNotIn("FP32 needed", out)

    def test_alpha_tau_must_be_the_fp32_selection(self):
        with tempfile.TemporaryDirectory() as td:
            write_sets(td, "M", GOOD); write_sets(td, "M8", GOOD)
            with self.assertRaises(SystemExit) as e:
                run(td, frozen=(0.7, 2.0))
            self.assertIn("frozen alpha/tau", str(e.exception.code))

    def test_every_set_in_both_settings_or_stop_before_the_verdict(self):
        with tempfile.TemporaryDirectory() as td:
            write_sets(td, "M", GOOD); write_sets(td, "M8", GOOD)
            os.remove(os.path.join(td, "M8.formal.discordtune.jsonl"))     # not a section-4 cell, still one of the 12
            out = io.StringIO()
            with self.assertRaises(SystemExit) as e, contextlib.redirect_stdout(out):
                int8check.compare(int8check_args(td, ("cvtune", "wikitune", "dev302", "typing76", "user-reported", "discordtune")))
            self.assertEqual(e.exception.code, 3)
            self.assertIn("('formal', 'discordtune')", out.getvalue())
            self.assertNotIn("verdict", out.getvalue())

    def test_identical_int8_is_enough(self):
        with tempfile.TemporaryDirectory() as td:
            write_sets(td, "M", GOOD); write_sets(td, "M8", GOOD)
            enough, out = run(td)
            self.assertTrue(enough, out)
            self.assertIn("verdict: int8 enough", out)

    def test_int8_losing_the_fp32_fixes_needs_fp32_even_when_section_4_passes(self):
        fp32 = dict(GOOD, cvtune=[FIX] * 30 + [KEEP] * 20)
        lost = dict(FIX, ll=[0.0, -10.0])                          # same candidates, but int8 keeps the wrong n-gram first
        int8 = dict(GOOD, cvtune=[lost] * 30 + [KEEP] * 20)
        with tempfile.TemporaryDirectory() as td:
            write_sets(td, "M", fp32); write_sets(td, "M8", int8)
            enough, out = run(td)
            self.assertFalse(enough, out)
            self.assertIn("PASS: PASS (all above)", out)               # int8 still passes section 4 against the n-gram ...
            self.assertIn("('chat', 'cvtune')", out)                   # ... but loses 30 rows against fp32
            self.assertIn("verdict: FP32 needed", out)

    def test_different_candidates_and_missing_cells_stop(self):
        with tempfile.TemporaryDirectory() as td:
            write_sets(td, "M", GOOD); write_sets(td, "M8", dict(GOOD, dev302=[dict(KEEP, ng=[0.0, -0.2])] * 10))
            with self.assertRaises(SystemExit) as e:
                run(td)
            self.assertIn("same candidates", str(e.exception.code))
        with tempfile.TemporaryDirectory() as td:
            write_sets(td, "M", GOOD); write_sets(td, "M8", GOOD)
            with self.assertRaises(SystemExit) as e:
                run(td, sets=("cvtune", "wikitune", "dev302", "typing76", "user-reported"))
            self.assertEqual(e.exception.code, 3)


if __name__ == "__main__":
    unittest.main()
