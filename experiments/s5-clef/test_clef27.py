"""Offline tests for S5c section 10 (--model clef, the 27B). Fake HTTP layer, temporary dirs only; no network, no token."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clef_run as R  # noqa: E402
import clef_score as S  # noqa: E402
import test_clef as T  # noqa: E402

ENV, ROWS = T.ENV, T.ROWS
FLASH_FILES = {f"clef-{c}.jsonl" for c in R.CONDS}
C27_FILES = {f"clef27-{c}.jsonl" for c in R.CONDS}


def run(argv, post, other=(), d=None, rows=ROWS):
    """-> (exit code, stdout+stderr, {file: text} of the out dir). d: reuse an out dir, else a fresh temp one."""
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, ENV, clear=True), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        dd = d or os.path.join(t, "dev302")
        code = 0
        try:
            R.cli(argv, post=post, sleep=lambda s: None, rows_for=lambda s: rows, out_for=lambda s, n: dd,
                  dirs_for=lambda: list(other))
        except SystemExit as e:
            code = e.code
        files = {f: open(os.path.join(dd, f), encoding="utf-8").read() for f in os.listdir(dd)} if os.path.isdir(dd) else {}
    return code, out.getvalue() + err.getvalue(), files


def recording(fn):
    seen = []
    def post(path, headers, body):
        seen.append((path, json.loads(body), body))
        return T.reply(fn)(path, headers, body)
    return seen, post


def prior(d, prefix, tokens, cond="sent-fwd"):
    os.makedirs(d, exist_ok=True)
    rec = {"keys": ["0"], "secs": 0.1, "tokens": tokens, "answers": [], "picks": [0], "agree": [None]}
    with open(os.path.join(d, f"{prefix}-{cond}.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")


class DefaultUnchanged(unittest.TestCase):  # (i)
    def test_no_model_equals_explicit_flash_and_keeps_shape(self):
        seen0, post0 = recording(T.top1)
        seen1, post1 = recording(T.top1)
        c0, _, f0 = run(["--sets", "dev302"], post0)
        c1, _, f1 = run(["--sets", "dev302", "--model", "clef-flash"], post1)
        self.assertEqual((c0, c1), (0, 0))
        self.assertEqual(set(f0), FLASH_FILES)
        self.assertEqual(set(f1), FLASH_FILES)
        for path, req, raw in seen0:
            self.assertEqual(path, f"/client/v4/accounts/{T.ACCT}/ai/run/@cf/cloudflare/clef-flash")
            self.assertEqual(list(req), ["state", "model", "questions"])
            self.assertEqual(req["model"], "clef-flash")
        self.assertEqual([(p, b) for p, _, b in seen0], [(p, b) for p, _, b in seen1])  # byte-identical requests
        for text in f0.values():
            for line in text.strip().split("\n"):
                keys = set(json.loads(line))
                base = {"keys", "secs", "tokens", "tok_est", "model", "date", "answers"}
                self.assertIn(keys, (base | {"picks", "agree"}, base | {"decisions"}))


class Model27(unittest.TestCase):  # (ii)
    def test_clef_goes_to_27b_endpoint_and_clef27_files(self):
        seen, post = recording(T.top1)
        code, _, files = run(["--sets", "dev302", "--model", "clef"], post)
        self.assertEqual(code, 0)
        self.assertEqual(set(files), C27_FILES)
        self.assertTrue(seen)
        for path, req, _ in seen:
            self.assertEqual(path, f"/client/v4/accounts/{T.ACCT}/ai/run/@cf/cloudflare/clef")
            self.assertEqual(req["model"], "clef")
        rec = json.loads(files["clef27-sent-fwd.jsonl"].split("\n")[0])
        self.assertEqual(set(rec) - {"picks", "agree"}, {"keys", "secs", "tokens", "tok_est", "model", "date", "answers"})

    def test_unknown_model_is_refused(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                R.main(["--sets", "dev302", "--model", "other"])


class Spend(unittest.TestCase):  # (iii)
    def test_per_prefix_prices_over_all_dirs_including_smoke(self):
        with tempfile.TemporaryDirectory() as t:
            prior(os.path.join(t, "cvtune"), "clef", 1_000_000)
            prior(os.path.join(t, "dev302-n20"), "clef", 1_000_000, "pos")
            prior(os.path.join(t, "dev302-n20"), "clef27", 1_000_000)
            prior(os.path.join(t, "typing76"), "clef27", 500_000, "sent-rev")
            self.assertAlmostEqual(R.spent_usd(sorted(os.path.join(t, x) for x in os.listdir(t))), 0.18 + 0.36)

    def test_cumulative_over_cap_sends_nothing_for_either_model(self):
        with tempfile.TemporaryDirectory() as t:
            prior(os.path.join(t, "cvtune"), "clef", 5_000_000)         # 0.45
            prior(os.path.join(t, "dev302-n20"), "clef27", 2_500_000)   # 0.60: 1.05 together
            for model in ("clef-flash", "clef"):
                calls = []
                code, _, _ = run(["--sets", "dev302", "--model", model], lambda p, h, b: calls.append(1) or T.reply(T.top1)(p, h, b),
                                 other=[os.path.join(t, "cvtune"), os.path.join(t, "dev302-n20")])
                self.assertEqual((code, calls), ("s5c: stop: budget exceeded", []), model)

    def test_under_cap_runs(self):
        with tempfile.TemporaryDirectory() as t:
            prior(os.path.join(t, "cvtune"), "clef", 5_000_000)         # 0.45
            prior(os.path.join(t, "dev302-n20"), "clef27", 1_000_000)   # 0.24: 0.69
            code, _, _ = run(["--sets", "dev302", "--model", "clef"], T.reply(T.top1), other=[os.path.join(t, "cvtune"), os.path.join(t, "dev302-n20")])
        self.assertEqual(code, 0)

    def test_running_total_uses_the_models_own_price(self):
        with tempfile.TemporaryDirectory() as t:
            prior(os.path.join(t, "old"), "clef27", 3_000_000)          # 0.72
            for model, sent in (("clef-flash", 2), ("clef", 1)):        # 2M tokens/request: 0.18 vs 0.48
                calls = []
                code, _, _ = run(["--sets", "dev302", "--model", model],
                                 lambda p, h, b: calls.append(1) or T.reply(T.top1, tokens=2_000_000)(p, h, b), other=[os.path.join(t, "old")])
                self.assertEqual((code, len(calls)), ("s5c: stop: budget exceeded", sent), model)


class Score27(unittest.TestCase):  # (iv)
    def fake(self, model):
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, ENV, clear=True), contextlib.redirect_stdout(io.StringIO()):
            R.main(["--sets", "dev302", "--model", model], post=T.reply(T.top1), sleep=lambda s: None, rows_for=lambda s: ROWS,
                   out_for=lambda s, n: os.path.join(t, s), dirs_for=lambda: [])
            pre = R.MODELS[model][0]
            return {c: open(os.path.join(t, "dev302", f"{pre}-{c}.jsonl"), encoding="utf-8").read() for c in R.CONDS}

    def test_c27_conditions_and_pairs_appear_and_flash_rows_do_not_change(self):
        tf, t27 = self.fake("clef-flash"), self.fake("clef")
        tj = {}
        for c, text in tf.items():
            out = []
            for l in text.strip().split("\n"):
                r = json.loads(l)
                r["answers"] = [{"choice": "c1"} for _ in r["keys"]]
                if c == "pos":
                    r["decisions"] = [[0, "prob"]] * len(r["keys"])
                out.append(json.dumps(r, ensure_ascii=False))
            tj[c] = "\n".join(out) + "\n"
        base = S.score_set("dev302", tj, tf, ROWS)
        both = S.score_set("dev302", tj, tf, ROWS, texts_c27=t27)
        self.assertEqual([r for r in both if not str(r.get("cond", "")).startswith("C27") and "pair27" not in r and "pairflash" not in r], base)
        self.assertEqual({r["cond"] for r in both if r.get("cond", "").startswith("C27")}, {"C27-sent-fwd", "C27-sent-rev", "C27-pos"})
        self.assertEqual(len([r for r in both if "pair27" in r]), 3)
        self.assertEqual(len([r for r in both if "pairflash" in r]), 3)
        self.assertEqual(S.verdict(both)[:len(S.verdict(base))], S.verdict(base))  # flash judgement lines unchanged

    def test_c27_has_own_judgement_and_flash_pairs_never_enter_it(self):
        flash = [{"cond": "C-sent-fwd", "p": 0.5, "net": 1}, {"pair": "a", "p": 0.5, "net": 1}]
        c27 = [{"cond": "C27-sent-fwd", "p": 0.001, "net": 9}, {"pair27": "b", "p": 0.001, "net": 9}]
        v = S.verdict(flash + c27)
        self.assertEqual(v[:2], S.verdict(flash))
        self.assertIn("vs rank 1: C-sent-fwd not a candidate (net=1 p=0.5)", v)
        self.assertIn("C27 vs rank 1: C27-sent-fwd CANDIDATE for H/S6 (net=9 p=0.001)", v)
        self.assertIn("vs Jev: no significant difference", v)
        self.assertIn("C27 vs Jev: Clef27 better than Jev", v)
        worse_flash = {"pairflash": "C27-sent-fwd vs C-sent-fwd", "p": 0.001, "net": -50}
        v2 = S.verdict(flash + c27 + [worse_flash])
        self.assertEqual([l for l in v2 if not l.startswith("report only")], v)  # report-only line, no effect on any judgement
        self.assertTrue(any(l.startswith("report only") for l in v2))
        mixed = S.verdict(c27 + [{"pair27": "c", "p": 0.001, "net": -9}])
        self.assertIn("C27 vs Jev: mixed", mixed)
        self.assertEqual(S.verdict(flash), ["vs rank 1: C-sent-fwd not a candidate (net=1 p=0.5)", "vs Jev: no significant difference"])


class Smoke27(unittest.TestCase):  # (vi)
    def flash_smoke(self, d, pick):
        os.makedirs(d)
        for c in ("sent-fwd", "sent-rev"):
            rec = {"keys": [str(k) for k in range(5)], "secs": 0.1, "tokens": 1, "answers": [{"probabilities": {"c1": .1, "c2": .9}}] * 5,
                   "picks": [pick] * 5, "agree": [None] * 5}
            with open(os.path.join(d, f"clef-{c}.jsonl"), "w") as f:
                f.write(json.dumps(rec) + "\n")

    def test_27b_always_c1_stops_at_095_even_though_flash_smoke_files_are_varied(self):
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, "dev302-n20")
            self.flash_smoke(d, 1)  # flash smoke: never the first option
            code, text, files = run(["--sets", "dev302", "--model", "clef", "--limit", "5"], T.reply(T.top1), d=d)
            self.assertEqual(code, "s5c: stop: C-sent first-pick ratio >= 0.95 in both orders (parser broken, or the model picks by position)")
            self.assertIn("answer_fields=['choice', 'probabilities', 'type'] prob_keys=['c1', 'c2'", text)
            self.assertIn("first_pick fwd=1.0 rev=1.0", text)

    def test_flash_always_c1_does_not_stop_a_varied_27b(self):
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, "dev302-n20")
            self.flash_smoke(d, 0)  # flash smoke would trip the stop
            second = lambda q: {"type": "choice", "choice": "c2", "probabilities": {c: 0.9 if c == "c2" else 0.01 for c in q["criteria"]}}  # noqa: E731
            code, text, _ = run(["--sets", "dev302", "--model", "clef", "--limit", "5"], T.reply(second), d=d)
            self.assertEqual(code, 0)
            self.assertIn("first_pick fwd=0.0 rev=0.0", text)


if __name__ == "__main__":
    unittest.main()
