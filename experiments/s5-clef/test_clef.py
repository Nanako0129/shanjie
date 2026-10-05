"""Offline tests for the S5c tooling. No network, no real token: python3 -m unittest discover -s experiments/s5-clef"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clef_run as R  # noqa: E402
import clef_score as S  # noqa: E402

TOKEN, ACCT = "tok-SECRET-123", "acct0123456789abcdef"
ENV = {"CF_AI_TOKEN": TOKEN, "CF_ACCOUNT_ID": ACCT}
SENT = ["今天天氣很好", "今天天氣很號", "今天天器很好", "今添天氣很好"]  # synthetic, same length
ROWS = [{"i": k, "truth": SENT[k % 4], "syls": [], "ctx": "", "half": "A", "cands": SENT[k % 4:] + SENT[:k % 4], "margin": 1.0}
        for k in range(5)]


def probs(crit, hi, tie=()):
    return {c: (0.9 if j == hi else 0.4 if j in tie else 0.01) for j, c in enumerate(crit)}


def reply(answers_for, status=200, tokens=100):
    """Fake post: answers_for(question dict) -> answer or None."""
    def post(path, headers, body):
        req = json.loads(body)
        ans = {q: answers_for(v) for q, v in req["questions"].items()}
        res = {"answers": {q: a for q, a in ans.items() if a is not None}, "model": "clef-flash"}
        res["usage"] = {"input_tokens": tokens}
        return status, json.dumps({"result": res, "success": True}).encode()
    return post


def top1(q):
    crit = list(q["criteria"])
    return {"type": "choice", "choice": crit[0], "probabilities": probs(crit, 0)}


def run_cli(argv, post, rows=ROWS, env=ENV, sleeps=None):
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, env, clear=True), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = 0
        try:
            R.cli(argv, post=post, sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
                  rows_for=lambda s: rows, out_for=lambda s, n: os.path.join(t, s))
        except SystemExit as e:
            code = e.code
        files = {f: open(os.path.join(t, "dev302", f), encoding="utf-8").read() for f in os.listdir(os.path.join(t, "dev302"))} \
            if os.path.isdir(os.path.join(t, "dev302")) else {}
    return code, out.getvalue() + err.getvalue(), files


os.environ_real = dict(os.environ)


class Allowlist(unittest.TestCase):  # (a)
    def test_discordtune_exits_before_env_and_network(self):
        class Poison(dict):  # argparse/gettext read LANG etc.; only the Cloudflare variables are off limits
            def __getitem__(self, k):
                if k.startswith("CF_"):
                    raise AssertionError("Cloudflare environment read")
                return dict.__getitem__(self, k)

            def get(self, k, *d):
                return self[k] if k in self else d[0] if d else None

        for sets in ("discordtune", "cvtune,discordtune", "dev302,nope"):
            with mock.patch.object(R.os, "environ", Poison(os.environ_real)), \
                    mock.patch("http.client.HTTPSConnection", side_effect=AssertionError("network")), \
                    mock.patch("socket.socket", side_effect=AssertionError("network")), \
                    mock.patch.object(R, "load_rows", side_effect=AssertionError("input opened")):
                with self.assertRaises(SystemExit) as cm:
                    R.main(["--sets", sets])
                self.assertEqual(cm.exception.code, "s5c: set not on the allowlist (cvtune, dev302, typing76)")

    def test_subprocess_exit_code(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CF_")}
        r = subprocess.run([sys.executable, "-B", os.path.join(R.HERE, "clef_run.py"), "--sets", "discordtune"],
                           capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("allowlist", r.stderr)
        self.assertNotIn("CF_AI_TOKEN", r.stderr)  # the environment check was never reached

    def test_missing_env_is_a_fixed_message(self):
        code, text, _ = run_cli(["--sets", "dev302"], reply(top1), env={})
        self.assertEqual(code, "s5c: CF_AI_TOKEN and CF_ACCOUNT_ID must be set in the environment")

    def test_hash_mismatch_never_reads(self):
        with tempfile.TemporaryDirectory() as t, mock.patch.object(R, "J_RESULTS", t):
            os.makedirs(os.path.join(t, "dev302"))
            open(os.path.join(t, "dev302", "rows.jsonl"), "w").write("{}\n")
            with self.assertRaises(SystemExit) as cm:
                R.verified_text("dev302", "rows.jsonl")
            self.assertEqual(cm.exception.code, "s5c: input dev302/rows.jsonl missing or hash mismatch")
            os.remove(os.path.join(t, "dev302", "rows.jsonl"))
            with self.assertRaises(SystemExit):
                R.verified_text("dev302", "rows.jsonl")


class ErrorPaths(unittest.TestCase):  # (b)
    def check_clean(self, text):
        for bad in (TOKEN, ACCT, "api.cloudflare.com", "/accounts/", "/ai/run", *SENT):
            self.assertNotIn(bad, text)

    def fake_status(self, status):
        # the server echoes secrets and sentences back; none of it may reach the output
        return lambda path, h, body: (status, (TOKEN + ACCT + "".join(SENT) + path).encode())

    def test_401_stops_without_retry(self):
        calls = []
        def post(path, h, body):
            calls.append(1)
            return self.fake_status(401)(path, h, body)
        code, text, _ = run_cli(["--sets", "dev302"], post)
        self.assertEqual((code, len(calls)), ("s5c: stop: HTTP 401: token or permission", 1))
        self.check_clean(text + code)

    def test_403_and_404_stop(self):
        for st, msg in ((403, "HTTP 403: token or permission"), (404, "HTTP 404: model or endpoint not found")):
            code, text, _ = run_cli(["--sets", "dev302"], self.fake_status(st))
            self.assertEqual(code, "s5c: stop: " + msg)

    def test_429_retries_three_times_then_stops(self):
        calls, sleeps = [], []
        def post(path, h, body):
            calls.append(1)
            return self.fake_status(429)(path, h, body)
        code, text, files = run_cli(["--sets", "dev302"], post, sleeps=sleeps)
        self.assertEqual((len(calls), sleeps), (4, [2, 4, 8]))
        self.assertEqual(code, "s5c: stop: 429 after 3 retries")
        self.assertEqual(files, {})
        self.check_clean(text + code)

    def test_5xx_then_recovery(self):
        n = []
        def post(path, h, body):
            n.append(1)
            return self.fake_status(503)(path, h, body) if len(n) < 3 else reply(top1)(path, h, body)
        sleeps = []
        code, text, files = run_cli(["--sets", "dev302"], post, sleeps=sleeps)
        self.assertEqual((code, sleeps[:2]), (0, [2, 4]))
        self.assertEqual(set(files), {"clef-sent-fwd.jsonl", "clef-sent-rev.jsonl", "clef-pos.jsonl"})

    def test_5xx_persistent(self):
        code, text, _ = run_cli(["--sets", "dev302"], self.fake_status(502))
        self.assertEqual(code, "s5c: stop: 502 after 3 retries")
        self.check_clean(text + code)

    def test_timeout(self):
        def post(path, h, body):
            raise TimeoutError(f"timed out https://api.cloudflare.com{path} {TOKEN}")
        sleeps = []
        code, text, _ = run_cli(["--sets", "dev302"], post, sleeps=sleeps)
        self.assertEqual((code, sleeps), ("s5c: stop: timeout/connection error after 3 retries", [2, 4, 8]))
        self.check_clean(text + code)

    def test_other_4xx_stops(self):
        code, _, _ = run_cli(["--sets", "dev302"], self.fake_status(400))
        self.assertEqual(code, "s5c: stop: HTTP 400")

    def test_no_probabilities_stops_and_records_nothing(self):
        for ans in ({"type": "choice", "choice": "c1"}, {"c1": 0.9, "c2": 0.1}, None):
            code, text, files = run_cli(["--sets", "dev302"], reply(lambda q, a=ans: a))
            self.assertIn(code, ("s5c: stop: no per-option probabilities in the response",
                                 "s5c: stop: response is not the expected JSON (no answers)"))
            self.assertEqual(files, {})
            self.check_clean(text + code)

    def test_non_json(self):
        code, _, _ = run_cli(["--sets", "dev302"], lambda p, h, b: (200, b"<html>"))
        self.assertEqual(code, "s5c: stop: response is not the expected JSON (no answers)")

    def test_foreign_exception_in_http_layer_prints_only_the_type(self):
        def boom(p, h, b):
            raise RuntimeError(TOKEN + SENT[0] + p)
        code, text, _ = run_cli(["--sets", "dev302"], boom)
        self.assertEqual(code, "s5c: unexpected RuntimeError")
        self.check_clean(text + code)

    def test_unexpected_error_prints_only_the_type(self):
        def post(p, h, b):
            return 200, json.dumps({"result": {"answers": {"q0": {"probabilities": 1}}, "usage": {"input_tokens": 1}}}).encode()
        with mock.patch.object(R, "parse_sent", side_effect=ValueError(TOKEN + SENT[0])):
            code, text, _ = run_cli(["--sets", "dev302"], post)
        self.assertEqual(code, "s5c: unexpected ValueError")
        self.check_clean(text + code)

    def test_success_output_is_clean(self):
        code, text, files = run_cli(["--sets", "dev302"], reply(top1))
        self.assertEqual(code, 0)
        self.check_clean(text)
        for f in files.values():  # the per-row files hold answers, not sentences
            self.check_clean(f)

    def test_parse_failures_over_one_percent_stop(self):
        def some(q):
            return None if q["criteria"]["c1"] == SENT[0] else top1(q)
        code, text, _ = run_cli(["--sets", "dev302"], reply(some))
        self.assertTrue(code.startswith("s5c: stop: parse failures above 1% in sent-fwd"), code)

    def test_resume_sends_nothing_twice(self):
        calls = []
        def post(path, h, body):
            calls.append(1)
            return reply(top1)(path, h, body)
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, ENV, clear=True), contextlib.redirect_stdout(io.StringIO()):
            kw = dict(post=post, sleep=lambda s: None, rows_for=lambda s: ROWS, out_for=lambda s, n: os.path.join(t, s))
            R.main(["--sets", "dev302"], **kw)
            first = len(calls)
            R.main(["--sets", "dev302"], **kw)
        self.assertEqual((first, len(calls)), (3, 3))

    def test_budget(self):
        pricey = reply(top1, tokens=20_000_000)  # about 1.8 USD
        code, _, _ = run_cli(["--sets", "dev302"], pricey)
        self.assertEqual(code, "s5c: stop: budget exceeded")

    def test_smoke_stop_at_095(self):
        code, text, _ = run_cli(["--sets", "dev302", "--limit", "5"], reply(top1))
        self.assertEqual(code, "s5c: stop: C-sent first-pick ratio >= 0.95 in both orders (parser broken, or the model picks by position)")
        self.assertIn("answer_fields=['choice', 'probabilities', 'type'] prob_keys=['c1', 'c2'", text)


class Decoding(unittest.TestCase):  # (c)
    CRIT = [f"c{j}" for j in range(1, 9)]

    def test_forward_picks_third_when_c3_highest(self):
        ans = {"probabilities": probs(self.CRIT, 2)}
        self.assertEqual(R.parse_sent(ans, self.CRIT), (2, None))

    def test_forward_and_reverse_index_mapping(self):
        rows = [{"cands": list("ABCDEFGH"), "truth": "A", "ctx": ""}]
        ans = {"probabilities": probs(self.CRIT, 2)}
        for cond, expect in (("sent-fwd", "C"), ("sent-rev", "F")):  # reverse: presented c3 is candidate 6
            rec = {"keys": ["0"], "secs": 0.1, "tokens": 1, "answers": [ans], "picks": [2], "agree": [None]}
            c = S.load_conds({cond: json.dumps(rec)}, rows, True)[cond]
            self.assertEqual(c["sent"][0], expect)
            self.assertEqual(c["first"][0], False)

    def test_choice_field_preferred_and_agreement_recorded(self):
        ans = {"choice": "c2", "probabilities": probs(self.CRIT, 4)}
        self.assertEqual(R.parse_sent(ans, self.CRIT), (1, False))
        ans = {"choice": "c5", "probabilities": probs(self.CRIT, 4)}
        self.assertEqual(R.parse_sent(ans, self.CRIT), (4, True))
        self.assertEqual(R.parse_sent({"choice": "zz", "probabilities": probs(self.CRIT, 4)}, self.CRIT), (4, None))

    def test_tie_goes_to_lowest_number(self):
        ans = {"probabilities": {c: (0.4 if c in ("c2", "c4", "c7") else 0.0) for c in self.CRIT}}
        self.assertEqual(R.parse_sent(ans, self.CRIT), (1, None))

    def test_sent_missing_probability_is_a_failure_even_with_choice(self):
        self.assertEqual(R.parse_sent({"choice": "c1"}, self.CRIT), (None, None))
        p = probs(self.CRIT, 0)
        del p["c8"]
        self.assertEqual(R.parse_sent({"choice": "c1", "probabilities": p}, self.CRIT), (None, None))

    def test_pos_rule(self):
        crit = ["c1", "c2", "c3"]
        dec = lambda p, **kw: R.parse_pos({"probabilities": p, **kw}, crit)  # noqa: E731
        self.assertEqual(dec({"c1": .2, "c2": .5, "c3": .3}), [1, "prob"])       # >= .5 and >= 2x unchanged
        self.assertEqual(dec({"c1": .3, "c2": .5, "c3": .2}), [0, "prob"])       # .5 < 2 * .3
        self.assertEqual(dec({"c1": .5, "c2": .5, "c3": 0}), [0, "prob"])
        self.assertEqual(dec({"c1": .1, "c2": .4, "c3": .4}), [0, "prob"])       # best below .5; tie -> c2 but rejected
        self.assertEqual(dec({"c1": .1, "c2": .1, "c3": .8}), [2, "prob"])

    def test_pos_missing_probability_fails_before_pos_decision(self):
        crit = ["c1", "c2", "c3"]
        with mock.patch.object(R.s5, "pos_decision", side_effect=AssertionError("called")):
            self.assertIsNone(R.parse_pos({"choice": "c2"}, crit))
            self.assertIsNone(R.parse_pos({"choice": "c2", "probabilities": {"c1": .5, "c2": .5}}, crit))
            self.assertIsNone(R.parse_pos({"choice": "c2", "probabilities": {"c1": .5, "c2": "x", "c3": 0}}, crit))
        # the S5j function itself would have fallen back to `choice`; that is why the check comes first
        self.assertEqual(R.s5.pos_decision({"choice": "c2"}, crit), (1, "choice"))


class Reproduce(unittest.TestCase):  # (d)
    def test_scorer_reproduces_s5j_jev_rows_exactly(self):
        for name in R.ALLOW:
            try:
                rows, ref = R.load_rows(name), json.loads(R.verified_text(name, "score.json"))
            except SystemExit:
                self.skipTest(f"{name}: S5j input not available on this machine")
            tj = {c: R.verified_text(name, f"jev-{c}.jsonl") for c in R.CONDS}
            got = {r["cond"]: json.loads(json.dumps(r)) for r in S.score_set(name, tj, {c: None for c in R.CONDS}, rows)}
            want = {r["cond"]: r for r in ref if r["cond"].startswith("J-") and r["subset"] == "all"}
            self.assertEqual(set(got), {"J-sent-fwd", "J-sent-rev", "J-pos"})
            for cn in got:
                self.assertEqual(got[cn], want[cn], f"{name} {cn}")

    def test_s5j_folders_unchanged(self):
        def snap():
            out = {}
            for base in (R.J_CACHE, R.J_RESULTS):
                for dp, _, fs in os.walk(base):
                    out.update({os.path.join(dp, f): os.stat(os.path.join(dp, f)).st_mtime_ns for f in fs})
            return out
        before = snap()
        rows = R.load_rows("typing76")
        tj = {c: R.verified_text("typing76", f"jev-{c}.jsonl") for c in R.CONDS}
        S.score_set("typing76", tj, {c: None for c in R.CONDS}, rows)
        self.assertEqual(before, snap())

    def test_clef_pairing_and_verdict(self):
        rows = ROWS
        def run(post):
            with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, ENV, clear=True), contextlib.redirect_stdout(io.StringIO()):
                R.main(["--sets", "dev302"], post=post, sleep=lambda s: None, rows_for=lambda s: rows, out_for=lambda s, n: os.path.join(t, s))
                return {c: "".join(open(os.path.join(t, "dev302", f"clef-{c}.jsonl"), encoding="utf-8")) for c in R.CONDS}
        tc = run(reply(top1))
        # a Jev that also always picks the presented first: every pair is identical
        def jev_text(cond):
            out = []
            for l in tc[cond].split("\n"):
                if l.strip():
                    r = json.loads(l)
                    r["answers"] = [{"choice": "c1"} for _ in r["keys"]]
                    if cond == "pos":
                        r["decisions"] = [[0, "prob"]] * len(r["keys"])
                    out.append(json.dumps(r, ensure_ascii=False))
            return "\n".join(out) + "\n"
        tj = {c: jev_text(c) for c in R.CONDS}
        recs = S.score_set("dev302", tj, tc, rows)
        pairs = [r for r in recs if "pair" in r]
        self.assertEqual(len(pairs), 3)
        self.assertTrue(all(r["fixed"] == 0 and r["broken"] == 0 and r["p"] == 1.0 for r in pairs))
        self.assertEqual(S.verdict(recs)[-1], "vs Jev: no significant difference")
        better = {"pair": "x", "p": 0.01, "net": 3}
        worse = {"pair": "y", "p": 0.01, "net": -3}
        self.assertEqual(S.verdict([better])[-1], "vs Jev: Clef better than Jev")
        self.assertEqual(S.verdict([worse])[-1], "vs Jev: Clef worse than Jev")
        self.assertEqual(S.verdict([better, worse])[-1], "vs Jev: mixed")
        self.assertEqual(S.verdict([{"pair": "z", "p": 0.2, "net": 5}])[-1], "vs Jev: no significant difference")


if __name__ == "__main__":
    unittest.main()
