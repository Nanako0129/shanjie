"""Offline tests for S5p section 13 (provider clef27, the 27B). Fake HTTP layer, temporary dirs only."""
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p_run as P  # noqa: E402
import p_score as S  # noqa: E402
import test_prompts as T  # noqa: E402

R = P.R
ENV27 = T.ENVS["clef"]


def run27(argv, post, rows=T.ROWS, env=None, other=(), v0=True):
    """Like test_prompts.run for clef27; v0=False leaves the real check_clef27_v0 in place."""
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, ENV27 if env is None else env, clear=True), \
            (mock.patch.object(P, "check_clef27_v0", lambda: None) if v0 else contextlib.nullcontext()), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = 0
        try:
            P.cli(argv, post=post, sleep=lambda s: None, rows_for=lambda s: rows, out_for=lambda s, n: os.path.join(t, s),
                  dirs_for=lambda: [o(t) if callable(o) else o for o in other])
        except SystemExit as e:
            code = e.code
        d = os.path.join(t, "dev302")
        files = {f: open(os.path.join(d, f), encoding="utf-8").read() for f in os.listdir(d)} if os.path.isdir(d) else {}
    return code, out.getvalue() + err.getvalue(), files


A27 = lambda *more: ["--provider", "clef27", "--sets", "dev302", *more]  # noqa: E731


class Provider27(unittest.TestCase):
    def test_endpoint_body_and_prefix(self):
        seen = []
        def post(p, h, b):
            seen.append((p, json.loads(b)["model"]))
            return T.reply(T.choice(0))(p, h, b)
        code, _, files = run27(A27("--variants", "v2"), post)
        self.assertEqual(code, 0)
        self.assertEqual(set(files), {"clef27-v2f.jsonl", "clef27-v2r.jsonl"})
        self.assertEqual(set(seen), {(f"/client/v4/accounts/{T.ACCT}/ai/run/@cf/cloudflare/clef", "clef")})

    def test_clef_provider_unchanged(self):
        seen = []
        def post(p, h, b):
            seen.append((p, json.loads(b)["model"]))
            return T.reply(T.choice(0))(p, h, b)
        code, _, files = T.run(T.ARGS("clef", "--variants", "v2"), post, "clef")
        self.assertEqual((code, set(files)), (0, {"clef-v2f.jsonl", "clef-v2r.jsonl"}))
        self.assertEqual(set(seen), {(f"/client/v4/accounts/{T.ACCT}/ai/run/@cf/cloudflare/clef-flash", "clef-flash")})

    def test_allowlist_and_missing_env_for_clef27(self):
        code, _, _ = run27(["--provider", "clef27", "--sets", T.PRIV], T.reply(T.choice(0)))
        self.assertEqual(code, "s5p: set not on the allowlist (cvtune, dev302, typing76)")
        code, _, _ = run27(A27(), T.reply(T.choice(0)), env={})
        self.assertEqual(code, "s5p: CF_AI_TOKEN and CF_ACCOUNT_ID must be set in the environment")


class Caps27(unittest.TestCase):  # (f')
    def test_three_providers_cap_and_budget(self):
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, "old")
            os.makedirs(d)
            def put(name, rec, n=1):
                with open(os.path.join(d, name), "w") as f:
                    for k in range(n):
                        f.write(json.dumps({"key": k, **rec}) + "\n")
            put("clef-v1.jsonl", {"nq": 4, "tokens": 1_000_000, "pick": 0})       # 0.09
            put("clef27-v1.jsonl", {"nq": 4, "tokens": 1_000_000, "pick": 0})     # 0.24, not a Jev request
            put("jev-v1.jsonl", {"nq": 300, "tokens": 1, "pick": 0}, n=3)         # 3 requests, 900 questions
            usd, jr = P.spent([d])
            self.assertAlmostEqual(usd, 0.09 + 0.24 + 900 * 0.004 / 300)
            self.assertEqual(jr, 3)  # the 27B and flash requests are not in Jev's count
            # the Jev cap counts only Jev: 3 prior + 1 new = 4 reaches the cap, the clef27 file did not add to it
            calls = []
            code, _, _ = T.run(T.ARGS("jev", "--variants", "v2"), lambda p, h, b: calls.append(1) or T.reply(T.choice(0))(p, h, b),
                               cap=4, other=[d])
            self.assertEqual((code, len(calls)), ("s5p: stop: Jev request cap 4 reached", 1))
            # 27B is never held to Jev's cap
            code, _, _ = run27(A27("--variants", "v2"), T.reply(T.choice(0)), other=[d])
            self.assertEqual(code, 0)

    def test_27b_priced_at_024_toward_the_2_dollar_stop(self):
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, "old")
            os.makedirs(d)
            with open(os.path.join(d, "clef-v1.jsonl"), "w") as f:  # 10M tokens x 0.09 = 0.90
                f.write(json.dumps({"key": 0, "nq": 4, "tokens": 10_000_000, "pick": 0}) + "\n")
            with open(os.path.join(d, "jev-v1.jsonl"), "w") as f:   # 0.16 in Jev questions
                f.write(json.dumps({"key": 0, "nq": 12_000, "tokens": 1, "pick": 0}) + "\n")
            # 0.90 + 0.16 = 1.06; each 27B request of 1M tokens adds 0.24: 1.30, 1.54, 1.78, 2.02 -> stop before the 5th
            calls = []
            code, _, _ = run27(A27("--variants", "v1"), lambda p, h, b: calls.append(1) or T.reply(T.answer_by_variant, tokens=1_000_000)(p, h, b), other=[d])
            self.assertEqual((code, len(calls)), ("s5p: stop: budget exceeded", 4))
            # the same traffic priced as flash (0.09 each, 6 rows = 0.54) would have finished
            code, _, _ = T.run(T.ARGS("clef", "--variants", "v1"), T.reply(T.answer_by_variant, tokens=1_000_000), "clef", other=[d])
            self.assertEqual(code, 0)


class V0Check27(unittest.TestCase):  # (h')
    def test_unset_exits_nonzero_before_any_request_or_key(self):
        calls = []
        with mock.patch.object(P, "CLEF27_V0_SHA", None):  # as before main recorded the hash
            code, _, _ = run27(A27(), lambda p, h, b: calls.append(1), env={}, v0=False)
        self.assertEqual((code, calls), ("s5p: Clef27 V0 hash is not recorded in contract section 12 yet", []))

    def test_mismatch_and_missing_exit_nonzero_before_any_request(self):
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "clef27-sent-fwd.jsonl")
            with open(f, "w") as fh:
                fh.write("{}\n")
            calls = []
            with mock.patch.object(P, "CLEF27_V0_SHA", "0" * 64), mock.patch.object(P, "CLEF27_V0_PATH", f):
                code, _, _ = run27(A27(), lambda p, h, b: calls.append(1), v0=False)
                self.assertEqual(code, "s5p: input clef27-sent-fwd.jsonl missing or hash mismatch")
                os.remove(f)
                code, _, _ = run27(A27(), lambda p, h, b: calls.append(1), v0=False)
                self.assertEqual(code, "s5p: input clef27-sent-fwd.jsonl missing or hash mismatch")
            self.assertEqual(calls, [])

    def test_flash_constant_does_not_satisfy_the_27b_check_and_match_passes(self):
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "v0")
            with open(f, "w") as fh:
                fh.write("x")
            with mock.patch.object(P, "CLEF27_V0_SHA", hashlib.sha256(b"x").hexdigest()), mock.patch.object(P, "CLEF27_V0_PATH", f):
                P.check_clef27_v0()
                code, _, _ = run27(A27("--variants", "v2"), T.reply(T.choice(0)), v0=False)
                self.assertEqual(code, 0)
            self.assertNotEqual(P.CLEF27_V0_PATH, P.CLEF_V0_PATH)


class EightTests(unittest.TestCase):  # (s)
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(S, "D", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)
        self.rows = [{"truth": f"s{k}", "cands": [f"s{k}", f"x{k}"], "half": "AB"[k % 2]} for k in range(40)]
        self.B = [k for k, r in enumerate(self.rows) if r["half"] == "B"]

    def put(self, prov, v, picks, tokens=1_000_000):
        with open(os.path.join(self.tmp.name, f"{prov}-{v}.jsonl"), "w") as f:
            for k, j in picks.items():
                f.write(json.dumps({"key": k, "pick": j, "tie": False, "secs": .1, "tokens": tokens, "nq": 1, "tok_est": False}) + "\n")

    def test_exactly_eight_test_lines_and_flash_vs_27b_report_only(self):
        for prov, v in (("jev", "v2f"), ("jev", "v2r"), ("clef", "v3"), ("clef27", "v1")):
            self.put(prov, v, {k: (1 if prov == "jev" else 0) for k in self.B})
        v0 = {k: 0 for k in self.B}
        out = io.StringIO()
        with mock.patch.object(S, "v0_picks", lambda prov: v0), contextlib.redirect_stdout(out):
            S.test(self.rows, lambda s: s, {"jev": "v2", "clef": "v3", "clef27": "v1"})
        lines = out.getvalue().strip().split("\n")
        tests = [l for l in lines if " vs " in l]
        self.assertEqual(len(tests), 8, tests)
        self.assertEqual([l.split(":")[0] for l in tests], [
            "jev v2 vs V0", "jev v2 vs rank1", "clef v3 vs V0", "clef v3 vs rank1", "clef27 v1 vs V0", "clef27 v1 vs rank1",
            "clef vs jev", "clef27 vs jev"])
        report = [l for l in lines if "report-only" in l]
        self.assertEqual(len(report), 1)
        self.assertIn("flash->27B", report[0])
        self.assertNotIn(" vs ", report[0])
        # the per-provider cost line is priced per provider: 27B at 0.24 per million tokens
        self.assertTrue(any(l.startswith("clef27 v1 B-half") and "tokens x 0.24/M" in l for l in lines))
        self.assertTrue(any(l.startswith("clef v3 B-half") and "tokens x 0.09/M" in l for l in lines))

    def test_27b_v0_picks_come_from_the_27b_file_and_its_check(self):
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "v0")
            rec = {"keys": ["0", "1"], "picks": [1, None], "answers": [None, None]}
            with open(f, "w") as fh:
                fh.write(json.dumps(rec) + "\n")
            with open(f, "rb") as fh:
                sha = hashlib.sha256(fh.read()).hexdigest()
            with mock.patch.object(P, "CLEF27_V0_SHA", sha), mock.patch.object(P, "CLEF27_V0_PATH", f), \
                    mock.patch.object(P, "check_clef_v0", side_effect=AssertionError("flash check used")):
                self.assertEqual(S.v0_picks("clef27"), {0: 1})
            with mock.patch.object(P, "CLEF27_V0_SHA", None), mock.patch.object(P, "CLEF27_V0_PATH", f):
                with self.assertRaises(SystemExit):
                    S.v0_picks("clef27")

    def test_select_handles_three_providers(self):
        A = [k for k, r in enumerate(self.rows) if r["half"] == "A"]
        for prov in ("jev", "clef", "clef27"):
            for v in ("v1", "v2f", "v3", "v4f"):
                self.put(prov, v, {k: 0 for k in A})
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(S.select(self.rows, lambda s: s, ("jev", "clef", "clef27")), {"jev": "v1", "clef": "v1", "clef27": "v1"})


if __name__ == "__main__":
    unittest.main()
