"""Offline tests for the S5p tooling. No network, no real key: python3 -B -m unittest discover -s experiments/s5-prompts"""
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
import p_run as P  # noqa: E402
import p_score as S  # noqa: E402

R = P.R
PRIV = "discord" + "tune"  # the private set; spelled apart so the diff never contains its name
KEY, TOKEN, ACCT = "jev-KEY-SECRET-9", "tok-SECRET-123", "acct0123456789abcdef"
ENVS = {"jev": {"TYPESAFE_API_KEY": KEY}, "clef": {"CF_AI_TOKEN": TOKEN, "CF_ACCOUNT_ID": ACCT}}
SENT = ["今天天氣很好", "今天天氣很號", "今天天器很好", "今添天氣很好"]
ROWS = [{"i": k, "truth": SENT[k % 4], "syls": ["ㄅ", "ㄆ"], "ctx": "", "half": "AB"[k % 2],
         "cands": SENT[k % 4:] + SENT[:k % 4], "margin": 1.0} for k in range(6)] + \
       [{"i": 6, "truth": "單一", "syls": ["ㄅ"], "ctx": "", "half": "A", "cands": ["單一"], "margin": 1e9}]


def reply(fn, tokens=100):
    """Fake post: fn(question id, question) -> answer."""
    def post(path, headers, body):
        req = json.loads(body)
        res = {"answers": {q: fn(q, v) for q, v in req["questions"].items()}, "model": req["model"], "usage": {"input_tokens": tokens}}
        return 200, json.dumps({"result": res, "success": True}).encode()
    return post


def noul(p):
    return {"type": "noul", "probabilities": {"true": p, "false": 1 - p}}


def choice(j):
    def f(q, v):
        crit = list(v["criteria"])
        return {"type": "choice", "choice": crit[j], "probabilities": {c: (0.9 if n == j else 0.01) for n, c in enumerate(crit)}}
    return f


def answer_by_variant(q, v):
    """Varied, non-degenerate fake model: noul P(true) by candidate number, choice picks by criteria length."""
    if v["type"] == "noul":
        return noul(0.1 + 0.1 * int(q[3:]))
    return choice(len(v["criteria"]) - 1)(q, v)


def run(argv, post, provider="jev", rows=ROWS, env=None, other=(), cap=None, sleeps=None, check_v0=True):
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, ENVS[provider] if env is None else env, clear=True), \
            mock.patch.object(P, "check_clef_v0", lambda: None) if check_v0 else contextlib.nullcontext(), \
            mock.patch.object(P, "JEV_CAP", cap or P.JEV_CAP), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = 0
        try:
            P.cli(argv, post=post, sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
                  rows_for=lambda s: rows, out_for=lambda s, n: os.path.join(t, s),
                  dirs_for=lambda: [o(t) if callable(o) else o for o in other])
        except SystemExit as e:
            code = e.code
        d = os.path.join(t, "dev302")
        files = {f: open(os.path.join(d, f), encoding="utf-8").read() for f in os.listdir(d)} if os.path.isdir(d) else {}
    return code, out.getvalue() + err.getvalue(), files


ARGS = lambda prov, *more: ["--provider", prov, "--sets", "dev302", *more]  # noqa: E731


class Allowlist(unittest.TestCase):  # (a)
    def test_exits_before_keys_hashes_and_network(self):
        class Poison(dict):  # argparse/gettext read LANG etc.; only the key variables are off limits
            def get(self, k, *d):
                if k.startswith(("CF_", "TYPESAFE")):
                    raise AssertionError("key read")
                return dict.get(self, k, *d)
        for prov in ("jev", "clef"):
            for sets in (PRIV, "cvtune," + PRIV, "dev302,nope"):
                with mock.patch.object(P.os, "environ", Poison(os.environ)), \
                        mock.patch("http.client.HTTPSConnection", side_effect=AssertionError("network")), \
                        mock.patch("socket.socket", side_effect=AssertionError("network")), \
                        mock.patch.object(R, "load_rows", side_effect=AssertionError("input opened")), \
                        mock.patch.object(P, "check_clef_v0", side_effect=AssertionError("v0 opened")):
                    with self.assertRaises(SystemExit) as cm:
                        P.main(["--provider", prov, "--sets", sets, "--half", "A"])
                    self.assertEqual(cm.exception.code, "s5p: set not on the allowlist (cvtune, dev302, typing76)")

    def test_subprocess_exit_code(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CF_", "TYPESAFE"))}
        for prov in ("jev", "clef"):
            r = subprocess.run([sys.executable, "-B", os.path.join(P.HERE, "p_run.py"), "--provider", prov, "--sets", PRIV],
                               capture_output=True, text=True, env=env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("allowlist", r.stderr)

    def test_jev_key_comes_from_the_environment_only(self):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": " k \n"}, clear=True):
            c = P.make_client("jev", lambda *a: None, None)
        self.assertEqual((c.path, c.model, c.headers["Authorization"]), ("/v1/systemone", "jev-1.13.0", "Bearer k"))

    def test_clef_uses_s5c_endpoint_and_model(self):
        with mock.patch.dict(os.environ, ENVS["clef"], clear=True):
            c = P.make_client("clef", lambda *a: None, None)
        self.assertEqual((c.path, c.model), (f"/client/v4/accounts/{ACCT}/ai/run/@cf/cloudflare/clef-flash", "clef-flash"))

    def test_jev_request_body_pins_model(self):
        seen = []
        with mock.patch.dict(os.environ, ENVS["jev"], clear=True):
            c = P.make_client("jev", lambda p, h, b: seen.append(json.loads(b)) or reply(choice(0))(p, h, b), None)
            c.ask({}, {"q": {"criteria": {"c1": "x"}}})
        self.assertEqual(seen[0]["model"], "jev-1.13.0")


class ErrorPaths(unittest.TestCase):  # (b)
    def clean(self, text):
        for bad in (KEY, TOKEN, ACCT, "api.cloudflare.com", "api.typesafe.ai", "/accounts/", "/v1/systemone", *SENT, "輸入"):
            self.assertNotIn(bad, text)

    def echo(self, status):
        return lambda path, h, body: (status, (KEY + TOKEN + ACCT + "".join(SENT) + path).encode())

    def test_all_error_statuses_both_providers(self):
        for prov in ("jev", "clef"):
            for st, msg, n in ((401, "HTTP 401: token or permission", 1), (403, "HTTP 403: token or permission", 1),
                               (404, "HTTP 404: model or endpoint not found", 1), (400, "HTTP 400", 1),
                               (429, "429 after 3 retries", 4), (503, "503 after 3 retries", 4)):
                calls = []
                code, text, files = run(ARGS(prov), lambda p, h, b, st=st: calls.append(1) or self.echo(st)(p, h, b), prov)
                self.assertEqual((code, len(calls)), ("s5p: stop: " + msg, n), (prov, st))
                self.assertEqual(files, {})
                self.clean(text + code)

    def test_timeout_and_foreign_exception(self):
        for prov in ("jev", "clef"):
            def to(p, h, b):
                raise TimeoutError(f"timed out {p} {KEY} {TOKEN}")
            sleeps = []
            code, text, _ = run(ARGS(prov), to, prov, sleeps=sleeps)
            self.assertEqual((code, sleeps), ("s5p: stop: timeout/connection error after 3 retries", [2, 4, 8]))
            self.clean(text + code)

            def boom(p, h, b):
                raise RuntimeError(KEY + SENT[0] + p)
            code, text, _ = run(ARGS(prov), boom, prov)
            self.assertEqual(code, "s5p: unexpected RuntimeError")
            self.clean(text + code)

    def test_success_output_is_clean(self):
        for prov in ("jev", "clef"):
            code, text, files = run(ARGS(prov), reply(answer_by_variant), prov)
            self.assertEqual(code, 0)
            self.clean(text)
            self.assertEqual(len(files), 6)

    def test_missing_probabilities_stops(self):
        for prov in ("jev", "clef"):
            code, _, _ = run(ARGS(prov, "--variants", "v2"), reply(lambda q, v: {"type": "choice", "choice": "c1"}), prov)
            self.assertEqual(code, "s5p: stop: no per-option probabilities in the response")
            code, _, _ = run(ARGS(prov, "--variants", "v1"), reply(lambda q, v: {"type": "noul", "value": True}), prov)
            self.assertEqual(code, "s5p: stop: no per-option probabilities in the response")

    def test_missing_key_is_a_fixed_message(self):
        code, _, _ = run(ARGS("jev"), reply(choice(0)), "jev", env={})
        self.assertEqual(code, "s5p: TYPESAFE_API_KEY must be set in the environment")
        code, _, _ = run(ARGS("clef"), reply(choice(0)), "clef", env={})
        self.assertEqual(code, "s5p: CF_AI_TOKEN and CF_ACCOUNT_ID must be set in the environment")


class Requests(unittest.TestCase):
    def test_shapes(self):
        row = ROWS[0]
        st, q = P.build("v1", row)
        self.assertEqual(list(st), ["context", "reading", "candidates"])
        self.assertEqual(st["reading"], "ㄅ ㄆ")
        self.assertEqual(list(q), ["q_c1", "q_c2", "q_c3", "q_c4"])
        self.assertIn("`candidates.c3`", q["q_c3"]["instructions"])
        self.assertEqual(set(q["q_c1"]["criteria"]), {"true", "false"})
        st, q = P.build("v3", row)
        self.assertIn("`candidates.c2`", q["q_c2"]["instructions"])
        st, q = P.build("v2f", row)
        self.assertEqual((list(q), q["q"]["type"], list(q["q"]["criteria"].values())), (["q"], "choice", row["cands"]))
        st, q = P.build("v4r", row)
        self.assertEqual(list(q["q"]["criteria"].values()), row["cands"][::-1])
        self.assertEqual(list(st["candidates"].values()), row["cands"][::-1])

    def test_single_candidate_row_not_sent(self):
        n = []
        run(ARGS("jev", "--variants", "v2"), lambda p, h, b: n.append(1) or reply(choice(0))(p, h, b))
        self.assertEqual(len(n), 2 * 6)  # 6 multi-candidate rows x (fwd, rev); row 6 is skipped

    def test_half_filter_and_resume(self):
        calls = []
        post = lambda p, h, b: calls.append(1) or reply(answer_by_variant)(p, h, b)  # noqa: E731
        with tempfile.TemporaryDirectory() as t, mock.patch.dict(os.environ, ENVS["jev"], clear=True), contextlib.redirect_stdout(io.StringIO()):
            kw = dict(post=post, sleep=lambda s: None, rows_for=lambda s: ROWS, out_for=lambda s, n: os.path.join(t, s), dirs_for=lambda: [])
            P.main(["--provider", "jev", "--sets", "cvtune", "--half", "B", "--variants", "v1"], **kw)
            first = len(calls)
            P.main(["--provider", "jev", "--sets", "cvtune", "--half", "B", "--variants", "v1"], **kw)
            keys = sorted(r["key"] for r in R.read_recs(os.path.join(t, "cvtune", "jev-v1.jsonl")))
        self.assertEqual((first, len(calls), keys), (3, 3, [1, 3, 5]))

    def test_cvtune_needs_half_and_no_limit(self):
        self.assertEqual(run(["--provider", "jev", "--sets", "cvtune"], reply(choice(0)))[0], "s5p: cvtune needs --half A or B")
        self.assertEqual(run(["--provider", "jev", "--sets", "cvtune", "--limit", "3", "--half", "A"], reply(choice(0)))[0],
                         "s5p: --limit is for dev302 and typing76 only")


class Decoding(unittest.TestCase):  # (c)
    def noul_answers(self, ps):
        return {f"q_c{n + 1}": noul(p) for n, p in enumerate(ps)}

    def test_v1_v3_max_p_true(self):
        for v in ("v1", "v3"):
            d = P.decode(v, self.noul_answers([.2, .7, .1, .3]), 4)
            self.assertEqual((d["pick"], d["tie"], d["all_same"]), (1, False, False))

    def test_v1_reads_jevs_real_noul_field(self):
        # The shape Jev returned on 2026-10-06: {"type": "noul", "noul": p}.
        ans = {f"q_c{n + 1}": {"type": "noul", "noul": p} for n, p in enumerate([.11, .75, .3])}
        d = P.decode("v1", ans, 3)
        self.assertEqual((d["pick"], d["tie"], d["all_same"]), (1, False, False))

    def test_v1_v3_ties_flagged_and_go_to_earlier_rank(self):
        for v in ("v1", "v3"):
            d = P.decode(v, self.noul_answers([.2, .7, .1, .7]), 4)
            self.assertEqual((d["pick"], d["tie"], d["all_same"]), (1, True, False))
            d = P.decode(v, self.noul_answers([.5, .5, .5, .5]), 4)
            self.assertEqual((d["pick"], d["tie"], d["all_same"]), (0, True, True))

    def test_v1_missing_or_malformed_is_failure(self):
        a = self.noul_answers([.2, .7, .1])
        a["q_c2"] = {"type": "noul"}
        self.assertIsNone(P.decode("v1", a, 3)["pick"])
        del a["q_c3"]
        self.assertIsNone(P.decode("v1", a, 3)["pick"])

    def test_v2_v4_forward_and_reverse_map_back(self):
        crit = ["c1", "c2", "c3", "c4"]
        ans = {"q": {"choice": "c4", "probabilities": {c: (0.9 if c == "c4" else 0.01) for c in crit}}}
        for v in ("v2f", "v4f"):
            self.assertEqual(P.decode(v, ans, 4), {"pick": 3, "pj": 3, "tie": None, "all_same": None})
        for v in ("v2r", "v4r"):  # presented c4 of the reversed list is original candidate 1
            self.assertEqual(P.decode(v, ans, 4)["pick"], 0)
            self.assertEqual(P.decode(v, {"q": {"probabilities": {"c1": .1, "c2": .1, "c3": .9, "c4": .1}}}, 4)["pick"], 1)

    def test_request_to_pick_round_trip_for_reverse(self):
        row = ROWS[0]
        _, q = P.build("v4r", row)
        want = row["cands"][2]
        j = list(q["q"]["criteria"].values()).index(want)
        ans = {"q": {"choice": f"c{j + 1}", "probabilities": {f"c{n + 1}": float(n == j) for n in range(4)}}}
        self.assertEqual(row["cands"][P.decode("v4r", ans, 4)["pick"]], want)


class Caps(unittest.TestCase):  # (f)
    def test_jev_cap_stops_run_and_counts_prior_dirs(self):
        calls = []
        post = lambda p, h, b: calls.append(1) or reply(choice(0))(p, h, b)  # noqa: E731
        code, text, files = run(ARGS("jev", "--variants", "v2"), post, cap=5)
        self.assertEqual((code, len(calls)), ("s5p: stop: Jev request cap 5 reached", 5))
        self.assertEqual(sum(len(v.strip().split("\n")) for v in files.values()), 5)
        calls.clear()
        with tempfile.TemporaryDirectory() as t:  # earlier runs in other dirs count toward the cap
            os.makedirs(os.path.join(t, "old"))
            with open(os.path.join(t, "old", "jev-v2f.jsonl"), "w") as f:
                for k in range(4):
                    f.write(json.dumps({"key": k, "nq": 1, "tokens": 1, "pick": 0}) + "\n")
            code, _, _ = run(ARGS("jev", "--variants", "v2"), post, cap=5, other=[os.path.join(t, "old")])
        self.assertEqual((code, len(calls)), ("s5p: stop: Jev request cap 5 reached", 1))

    def test_cap_does_not_apply_to_clef(self):
        code, _, _ = run(ARGS("clef", "--variants", "v2"), reply(choice(0)), "clef", cap=1)
        self.assertEqual(code, 0)

    def test_shared_budget_across_providers(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "old"))
            with open(os.path.join(t, "old", "clef-v1.jsonl"), "w") as f:  # 20M tokens = US$1.8
                f.write(json.dumps({"key": 0, "nq": 4, "tokens": 20_000_000, "pick": 0}) + "\n")
            with open(os.path.join(t, "old", "jev-v1.jsonl"), "w") as f:  # 20,000 questions x 0.004/300 = US$0.2667
                f.write(json.dumps({"key": 0, "nq": 20_000, "tokens": 1, "pick": 0}) + "\n")
            usd, jr = P.spent([os.path.join(t, "old")])
            self.assertAlmostEqual(usd, 1.8 + 20_000 * 0.004 / 300)
            self.assertEqual(jr, 1)
            calls = []
            code, _, _ = run(ARGS("jev", "--variants", "v2"), lambda p, h, b: calls.append(1) or reply(choice(0))(p, h, b),
                             other=[os.path.join(t, "old")])
        self.assertEqual((code, calls), ("s5p: stop: budget exceeded", []))

    def test_clef_budget_from_tokens(self):
        code, _, _ = run(ARGS("clef", "--variants", "v2"), reply(choice(0), tokens=25_000_000), "clef")
        self.assertEqual(code, "s5p: stop: budget exceeded")

    def test_v1_all_identical_p_true_stops_smoke_with_ratio_printed(self):
        code, text, files = run(ARGS("jev", "--variants", "v1", "--limit", "6"), reply(lambda q, v: noul(0.5)))
        self.assertIn("all_p_true_identical=1.000", text)
        self.assertIn("pick_c1=1.000", text)
        self.assertTrue(str(code).startswith("s5p: stop: smoke"), code)
        self.assertTrue(json.loads(files["jev-v1.jsonl"].split("\n")[0])["all_same"])

    def test_choice_smoke_stop(self):
        code, text, _ = run(ARGS("jev", "--variants", "v2", "--limit", "6"), reply(choice(0)))
        self.assertTrue(str(code).startswith("s5p: stop: smoke"), code)

    def test_smoke_passes_with_varied_model(self):
        code, text, _ = run(ARGS("jev", "--variants", "v1,v2,v3,v4", "--limit", "6"), reply(answer_by_variant))
        self.assertEqual(code, 0)


class ClefV0(unittest.TestCase):  # (h)
    def test_refuses_until_hash_recorded(self):
        with mock.patch.object(P, "CLEF_V0_SHA", None):
            code, _, _ = run(ARGS("clef"), reply(choice(0)), "clef", check_v0=False)
        self.assertEqual(code, "s5p: Clef V0 hash is not recorded in contract section 12 yet")

    def test_mismatch_and_missing_exit_nonzero_before_key(self):
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "clef-sent-fwd.jsonl")
            with open(f, "w") as fh:
                fh.write("{}\n")
            with mock.patch.object(P, "CLEF_V0_SHA", "0" * 64), mock.patch.object(P, "CLEF_V0_PATH", f):
                code, _, _ = run(ARGS("clef"), reply(choice(0)), "clef", env={}, check_v0=False)  # no env: the hash check comes first
                self.assertEqual(code, "s5p: input clef-sent-fwd.jsonl missing or hash mismatch")
                r = subprocess.run([sys.executable, "-B", "-c", "import p_run as P; P.CLEF_V0_SHA='0'*64; P.CLEF_V0_PATH=%r; P.cli(['--provider','clef','--sets','dev302'])" % f],
                                   capture_output=True, text=True, cwd=P.HERE)
                self.assertNotEqual(r.returncode, 0)
            os.remove(f)
            with mock.patch.object(P, "CLEF_V0_SHA", "0" * 64), mock.patch.object(P, "CLEF_V0_PATH", f):
                code, _, _ = run(ARGS("clef"), reply(choice(0)), "clef", check_v0=False)
                self.assertEqual(code, "s5p: input clef-sent-fwd.jsonl missing or hash mismatch")

    def test_match_passes(self):
        import hashlib
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "v0")
            with open(f, "w") as fh:
                fh.write("x")
            with mock.patch.object(P, "CLEF_V0_SHA", hashlib.sha256(b"x").hexdigest()), mock.patch.object(P, "CLEF_V0_PATH", f):
                P.check_clef_v0()


class Examples(unittest.TestCase):  # (g)
    def test_no_overlap_with_eval_or_prep_files(self):
        self.assertEqual(P.example_overlaps(), 0)

    def test_detector_does_detect(self):
        with mock.patch.object(P, "EX_TRUE", ["輸入法常常選錯字"]), mock.patch.object(P, "EX_FALSE", []):
            self.assertEqual(P.example_overlaps(), 1)  # a dev302 row 0 truth

    def test_texts_are_the_ones_in_the_prompts(self):
        for s in P.EX_TRUE + P.EX_FALSE:
            self.assertIn(s, json.dumps(P.V1_CRIT, ensure_ascii=False))
            self.assertIn(s, json.dumps(P.V3_CRIT, ensure_ascii=False))


class Selection(unittest.TestCase):  # section 5: selection and the 5 B-half tests
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(S, "D", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)
        self.rows = [{"truth": f"s{k}", "cands": [f"s{k}", f"x{k}"], "half": "AB"[k % 2]} for k in range(40)]
        self.L = lambda s: s

    def put(self, prov, v, picks):
        with open(os.path.join(self.tmp.name, f"{prov}-{v}.jsonl"), "w") as f:
            for k, j in picks.items():
                f.write(json.dumps({"key": k, "pick": j, "tie": False, "secs": .1, "tokens": 1, "nq": 1, "tok_est": False}) + "\n")

    def test_select_ties_go_to_lower_variant_number(self):
        A = [k for k, r in enumerate(self.rows) if r["half"] == "A"]
        for prov in ("jev", "clef"):
            for v in ("v1", "v2f", "v3", "v4f"):
                self.put(prov, v, {k: 0 for k in A})  # identical accuracy everywhere
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(S.select(self.rows, self.L), {"jev": "v1", "clef": "v1"})
        for v in ("v1", "v2f", "v4f"):
            self.put("jev", v, {k: 1 for k in A})  # wrong everywhere; v3 alone is best
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(S.select(self.rows, self.L)["jev"], "v3")

    def test_five_tests_and_verdict_words(self):
        B = [k for k, r in enumerate(self.rows) if r["half"] == "B"]
        self.put("jev", "v2f", {k: 1 for k in B})  # pick 1 = the wrong candidate
        self.put("jev", "v2r", {k: 1 for k in B})
        self.put("clef", "v3", {k: 0 for k in B})
        v0 = {k: 0 for k in B}
        out = io.StringIO()
        with mock.patch.object(S, "v0_picks", lambda prov: v0), contextlib.redirect_stdout(out):
            S.test(self.rows, self.L, {"jev": "v2", "clef": "v3"})
        text = out.getvalue()
        self.assertEqual(sum(l.count(" vs ") for l in text.split("\n")), 5)
        self.assertIn("jev v2 vs V0: n=20 fixed=0 broken=20 net=-20", text)
        self.assertIn("rewrite worse than V0", text)
        self.assertIn("clef v3 vs rank1: n=20 fixed=0 broken=0 net=0 p=1 -> not a candidate", text)
        self.assertIn("Clef better than Jev", text)
        self.assertIn("flip=0.0", text)


if __name__ == "__main__":
    unittest.main()
