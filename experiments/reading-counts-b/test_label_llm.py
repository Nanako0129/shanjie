"""Tests for label_llm.py with a mocked urllib (no network, no real key). Run: python3 -m unittest test_label_llm (in this directory)."""
import contextlib, io, json, os, re, shutil, sys, tempfile, unittest, urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import label_llm as L  # noqa: E402

KEY = "sk-ant-TEST-SECRET-0123456789"
READINGS = {"個": ["ㄍㄜˋ", "ㄍㄜ˙"], "得": ["ㄉㄜ˙", "ㄉㄜˊ", "ㄉㄟˇ"]}
EXAMPLES = {("個", "ㄍㄜˋ"): [(-3.4, "個人"), (-4.0, "個別"), (-4.0, "個個"), (-4.1, "個性"), (-5.0, "個案")],
            ("個", "ㄍㄜ˙"): [(-2.9, "一個"), (-2.9, "一個"), (-2.9, "兩個")], ("得", "ㄉㄜˊ"): [(-3.0, "獲得")]}
POS = [("a-1", "src", "我有個朋友", 2, "個"), ("a-2", "src", "跑得很快", 1, "得")]


class Resp:
    def __init__(self, data):
        self.data = data if isinstance(data, bytes) else json.dumps(data).encode()

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Requests(unittest.TestCase):
    def test_request_shape_and_prompt(self):
        reqs, side, est = L.build_requests(POS, READINGS, EXAMPLES)
        r = reqs[0]
        self.assertEqual(r["custom_id"], "a-1")
        p = r["params"]
        self.assertEqual((p["model"], p["max_tokens"], p["thinking"], p["output_config"]), ("claude-opus-5-5", 1024, {"type": "adaptive"}, {"effort": "low"}))
        self.assertNotIn("temperature", p)
        self.assertEqual(p["messages"][0]["role"], "user")
        text = p["messages"][0]["content"]
        self.assertIn("我有【個】朋友", text)
        self.assertIn("A. ㄍㄜˋ（例：個人、個個、個別）", text)   # three words, highest score first, ties by word
        self.assertIn("B. ㄍㄜ˙（例：一個、兩個）", text)           # duplicates dropped
        self.assertIn("C. ㄉㄟˇ", reqs[1]["params"]["messages"][0]["content"])   # no example words: just the Zhuyin
        self.assertNotIn("C. ㄉㄟˇ（", reqs[1]["params"]["messages"][0]["content"])
        self.assertEqual(side["a-2"], {"char": "得", "options": {"A": "ㄉㄜ˙", "B": "ㄉㄜˊ", "C": "ㄉㄟˇ"}})
        self.assertGreater(est, 50)
        self.assertNotIn("temperature", L.build_requests(POS, READINGS, EXAMPLES, temperature=False)[0][0]["params"])

    def test_long_and_pre_marked_sentences(self):
        sentence = "甲" * 100 + "【乙】" + "丙" * 20 + "個" + "丁" * 100 + "】"
        pos = sentence.index("個")
        text = L.build_requests([("a-1", "s", sentence, pos, "個")], READINGS, EXAMPLES)[0][0]["params"]["messages"][0]["content"]
        line = [l for l in text.splitlines() if l.startswith("句子：")][0][3:]
        self.assertEqual(line, "甲" * 37 + "「乙」" + "丙" * 20 + "【個】" + "丁" * 60)   # 60 characters kept on each side, 【乙】 became 「乙」
        self.assertEqual(line.count("【"), 1)
        left, right = line.split("【個】")
        self.assertEqual((len(left), len(right)), (60, 60))
        short = L.build_requests([("a-2", "s", "有【個】朋友", 2, "個")], READINGS, EXAMPLES)[0][0]["params"]["messages"][0]["content"]
        self.assertIn("句子：有「【個】」朋友", short)   # an existing bracket pair around the target is replaced, then the target is marked

    def test_bad_ids_and_misplaced_characters_stop(self):
        for bad in ([("a b", "s", "我有個朋友", 2, "個")], [POS[0], POS[0]], [("a-1", "s", "我有個朋友", 1, "個")], [("a-1", "s", "我有X朋友", 2, "X")]):
            with self.assertRaises(SystemExit):
                L.build_requests(bad, READINGS, EXAMPLES)

    def test_requests_command_writes_jsonl_and_sidecar(self):
        d = tempfile.mkdtemp()
        try:
            pos, out = os.path.join(d, "p.tsv"), os.path.join(d, "b.jsonl")
            with open(pos, "w", encoding="utf-8") as f:
                f.writelines("\t".join(map(str, p)) + "\n" for p in POS)
            with mock.patch.object(L.S, "load_merged", return_value=(READINGS, EXAMPLES)), mock.patch.object(L.S, "load_chars", return_value=["個", "得"]), \
                    contextlib.redirect_stdout(io.StringIO()) as buf:
                self.assertEqual(L.main(["requests", pos, "--out", out, "--no-temperature", "--model", "m-x"]), 0)
            self.assertRegex(buf.getvalue(), r"requests 2; ESTIMATED input tokens \d+")
            lines = [json.loads(l) for l in open(out, encoding="utf-8")]
            self.assertEqual([x["custom_id"] for x in lines], ["a-1", "a-2"])
            self.assertTrue(all("temperature" not in x["params"] and x["params"]["model"] == "m-x" for x in lines))
            self.assertEqual(json.load(open(out + ".map.json", encoding="utf-8"))["a-1"]["options"]["B"], "ㄍㄜ˙")
        finally:
            shutil.rmtree(d)


def result(i, text, kind="succeeded"):
    r = {"type": kind}
    if kind == "succeeded":
        r["message"] = {"content": [{"type": "text", "text": text}]}
    return json.dumps({"custom_id": i, "result": r})


class Collect(unittest.TestCase):
    SIDE = {f"i{k}": {"char": "個", "options": {"A": "ㄍㄜˋ", "B": "ㄍㄜ˙"}} for k in range(100)}

    def test_only_exactly_one_valid_letter_counts(self):
        for text, want in ((" B\n", "ㄍㄜ˙"), ("A", "ㄍㄜˋ"), ("A.", ""), ("AB", ""), ("C", ""), ("a", ""), ("", ""), ("答案是A", "")):
            rows, bad = L.collect_labels([result("i0", text)], {"i0": self.SIDE["i0"]})
            self.assertEqual((rows[0][2], bad), (want, int(not want)), text)
        rows, bad = L.collect_labels([result("i0", "", "errored")], {"i0": self.SIDE["i0"]})
        self.assertEqual(bad, 1)
        self.assertEqual(L.collect_labels([], {"i0": self.SIDE["i0"]})[1], 1)   # a missing result is invalid

    def run_collect(self, bad):
        d = tempfile.mkdtemp()
        try:
            side = os.path.join(d, "m.json")
            json.dump(self.SIDE, open(side, "w", encoding="utf-8"))
            lines = "\n".join(result(i, "A" if k >= bad else "oops") for k, i in enumerate(self.SIDE))
            seen = []

            def fake(req, timeout=None, context=None):
                seen.append(req)
                if req.full_url.endswith("/batches/msgbatch_1"):
                    return Resp({"id": "msgbatch_1", "processing_status": "ended", "results_url": L.API + "/v1/messages/batches/msgbatch_1/results"})
                return Resp(lines.encode())
            out = os.path.join(d, "labels.tsv")
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch("urllib.request.urlopen", fake), \
                    contextlib.redirect_stdout(io.StringIO()) as so, contextlib.redirect_stderr(io.StringIO()) as se:
                rc = L.main(["collect", "msgbatch_1", "--map", side, "--out", out])
            return rc, so.getvalue() + se.getvalue(), open(out, encoding="utf-8").read(), seen
        finally:
            shutil.rmtree(d)

    def test_exit_3_only_when_more_than_5_percent_invalid(self):
        self.assertEqual(self.run_collect(5)[0], 0)    # 5 of 100 = 5%: not above
        rc, shown, labels, seen = self.run_collect(6)
        self.assertEqual(rc, 3)
        self.assertIn("invalid 6", shown)
        self.assertEqual(labels.count("\t\n"), 6)       # empty reading for the invalid ones
        self.assertNotIn(KEY, shown + labels)
        for req in seen:
            self.assertEqual(req.get_header("X-api-key"), KEY)
            self.assertEqual(req.get_header("Anthropic-version"), "2023-06-01")


def batch_lines(n, model="claude-opus-5-5"):
    return "".join(json.dumps({"custom_id": f"a-{k}", "params": {"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": f"q{k}"}]}}) + "\n"
                   for k in range(n))


class Api(unittest.TestCase):
    def test_price_then_submit_status_endpoints_and_the_key_never_shown_or_written(self):
        d = tempfile.mkdtemp()
        try:
            b = os.path.join(d, "b.jsonl")
            with open(b, "w", encoding="utf-8") as f:
                f.write(batch_lines(10))
            calls = []

            def fake(req, timeout=None, context=None):
                calls.append((req.get_method(), req.full_url, req.data))
                if req.full_url.endswith("/count_tokens"):
                    return Resp({"input_tokens": 1000})
                return Resp({"id": "msgbatch_9", "processing_status": "in_progress", "request_counts": {"processing": 1}})
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch("urllib.request.urlopen", fake), \
                    contextlib.redirect_stdout(io.StringIO()) as so, contextlib.redirect_stderr(io.StringIO()) as se:
                self.assertEqual(L.main(["price", b, "--sample", "4"]), 0)
                self.assertEqual(len(calls), 4)
                self.assertEqual(calls[0][:2], ("POST", "https://api.anthropic.com/v1/messages/count_tokens"))
                self.assertEqual(set(json.loads(calls[0][2])), {"model", "messages"})   # no max_tokens / temperature on count_tokens
                est = json.load(open(b + ".price.json", encoding="utf-8"))
                # 10 requests x 1000 input tokens at $2/MTok + 10 x 8 output tokens at $10/MTok
                self.assertEqual((est["input_tokens"], est["output_tokens_upper"], est["usd"]), (10000, 80, round((10000 * 2 + 80 * 10) / 1e6, 4)))
                self.assertEqual(L.main(["submit", b, "--max-usd", "1"]), 0)
                self.assertEqual(L.main(["status", "msgbatch_9"]), 0)
            self.assertEqual(calls[4][:2], ("POST", "https://api.anthropic.com/v1/messages/batches"))
            self.assertEqual(len(json.loads(calls[4][2])["requests"]), 10)
            self.assertEqual(calls[5][:2], ("GET", "https://api.anthropic.com/v1/messages/batches/msgbatch_9"))
            shown = so.getvalue() + se.getvalue()
            self.assertIn("msgbatch_9", shown)
            self.assertNotIn(KEY, shown)
            files = "".join(open(os.path.join(d, n), encoding="utf-8").read() for n in os.listdir(d))
            self.assertNotIn(KEY, files)
            self.assertEqual(sorted(os.listdir(d)), ["b.jsonl", "b.jsonl.price.json"])
        finally:
            shutil.rmtree(d)

    def test_submit_needs_a_fresh_price_for_this_batch_and_a_budget(self):
        d = tempfile.mkdtemp()
        try:
            b = os.path.join(d, "b.jsonl")
            with open(b, "w", encoding="utf-8") as f:
                f.write(batch_lines(10))
            calls = []

            def fake(req, timeout=None, context=None):
                calls.append(req.full_url)
                return Resp({"input_tokens": 1000} if req.full_url.endswith("/count_tokens") else {"id": "m", "processing_status": "in_progress"})
            env = mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY})
            with env, mock.patch("urllib.request.urlopen", fake), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    L.main(["submit", b, "--max-usd", "100"])         # no price file
                L.main(["price", b, "--sample", "3"])
                with self.assertRaises(SystemExit):
                    L.main(["submit", b, "--max-usd", "0.01"])        # estimate $0.0208 > $0.01
                with open(b, "a", encoding="utf-8") as f:
                    f.write(batch_lines(1).replace("a-0", "a-99"))
                with self.assertRaises(SystemExit):
                    L.main(["submit", b, "--max-usd", "100"])         # the batch changed after the price
                with self.assertRaises(SystemExit):
                    L.main(["submit", b])                             # --max-usd is required (argparse)
                self.assertFalse([c for c in calls if c.endswith("/batches")])
                L.main(["price", b, "--sample", "3"])
                self.assertEqual(L.main(["submit", b, "--max-usd", "100"]), 0)
            self.assertTrue([c for c in calls if c.endswith("/batches")])
        finally:
            shutil.rmtree(d)

    def test_estimate_scales_the_sample_and_prices_by_model(self):
        reqs = [{"params": {"model": "claude-fable-5-1", "max_tokens": 8}} for _ in range(100)]
        e = L.estimate(reqs, 600, 6)   # 100 tokens a request
        self.assertEqual((e["input_tokens"], e["output_tokens_upper"], e["usd"]), (10000, 800, round((10000 * 5 + 800 * 25) / 1e6, 4)))
        with contextlib.redirect_stderr(io.StringIO()):
            for bad in ([{"params": {"model": "gpt", "max_tokens": 8}}], reqs[:1] + [{"params": {"model": "claude-opus-5-5", "max_tokens": 8}}]):
                with self.assertRaises(SystemExit):
                    L.estimate(bad, 10, 1)

    def test_every_call_uses_the_extra_ca_file_when_set(self):
        seen = []

        def fake(req, timeout=None, context=None):
            seen.append(context)
            return Resp({"processing_status": "in_progress"})
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY, "NODE_EXTRA_CA_CERTS": "/x/ca.pem"}), mock.patch("urllib.request.urlopen", fake), \
                mock.patch("ssl.SSLContext.load_verify_locations") as load:
            L.api("GET", L.API + "/v1/messages/batches/x")
            L.api("POST", L.API + "/v1/messages/count_tokens", {})
        load.assert_called_with("/x/ca.pem")
        self.assertEqual(load.call_count, 2)
        self.assertTrue(all(isinstance(c, L.ssl.SSLContext) for c in seen) and len(seen) == 2)
        env = {"ANTHROPIC_API_KEY": KEY, "SSL_CERT_FILE": "/y/ca.pem"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("ssl.SSLContext.load_verify_locations") as load:
            L.ssl_context()
        load.assert_called_once_with("/y/ca.pem")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("ssl.SSLContext.load_verify_locations") as load:
            L.ssl_context()
        load.assert_not_called()

    def test_no_key_no_call_and_http_errors_show_only_the_status(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("urllib.request.urlopen") as u, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                L.api("GET", L.API + "/v1/messages/batches/x")
            u.assert_not_called()
        err = urllib.error.HTTPError(L.API + "/x", 401, "bad", {}, io.BytesIO(("invalid key " + KEY).encode()))
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch("urllib.request.urlopen", side_effect=err), \
                contextlib.redirect_stderr(io.StringIO()) as se:
            with self.assertRaises(SystemExit):
                L.api("GET", L.API + "/v1/messages/batches/x")
        self.assertIn("HTTP 401", se.getvalue())
        self.assertNotIn(KEY, se.getvalue())

    def test_the_key_is_not_sent_to_another_host(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch("urllib.request.urlopen") as u, contextlib.redirect_stderr(io.StringIO()):
            for url in ("https://evil.example/results", "http://api.anthropic.com/x", "https://api.anthropic.com.evil.example/x"):
                with self.assertRaises(SystemExit):
                    L.api("GET", url)
            u.assert_not_called()


class Direct(unittest.TestCase):
    def test_direct_resumes_records_errors_and_collect_reads_the_file(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        batch, out = os.path.join(d, "b.jsonl"), os.path.join(d, "r.jsonl")
        with open(batch, "w", encoding="utf-8") as f:
            for i in ("x-1", "x-2", "x-3"):
                f.write(json.dumps({"custom_id": i, "params": {"model": "claude-opus-5-5", "max_tokens": 8, "messages": []}}) + "\n")
        with open(out, "w", encoding="utf-8") as f:
            f.write(json.dumps({"custom_id": "x-1", "result": {"type": "succeeded", "message": {"content": [{"type": "text", "text": "A"}]}}}) + "\n")
        seen = []
        def sender(params):
            seen.append(1)
            return {"type": "succeeded", "message": {"content": [{"type": "text", "text": "B"}]}} if len(seen) == 1 else {"type": "errored", "error": {"status": 400}}
        n, err, _ = L.direct(batch, out, 2, sender=sender)
        self.assertEqual((n, err, len(seen)), (2, 1, 2))                         # x-1 was already done: not sent again
        lines = open(out, encoding="utf-8").read().splitlines()
        self.assertEqual(sorted(json.loads(l)["custom_id"] for l in lines), ["x-1", "x-2", "x-3"])
        side = {i: {"char": "個", "options": {"A": "ㄍㄜˋ", "B": "ㄍㄜ˙"}} for i in ("x-1", "x-2", "x-3")}
        rows, bad = L.collect_labels(lines, side)
        self.assertEqual(bad, 1)

    def test_send_retries_overload_then_succeeds_and_gives_up_on_400(self):
        calls = []
        def opener(req, timeout=None, context=None):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.HTTPError(req.full_url, 529, "overloaded", {}, None)
            return Resp({"content": [{"type": "text", "text": "A"}]})
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch.object(L.urllib.request, "urlopen", opener), mock.patch.object(L.time, "sleep"):
            self.assertEqual(L.send({"model": "m"})["type"], "succeeded")
            self.assertEqual(len(calls), 3)
            calls.clear()
            def bad(req, timeout=None, context=None):
                calls.append(1)
                raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, None)
            with mock.patch.object(L.urllib.request, "urlopen", bad):
                self.assertEqual(L.send({"model": "m"}), {"type": "errored", "error": {"status": 400}})
            self.assertEqual(len(calls), 1)

    def test_direct_budget_is_twice_the_batch_estimate(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        batch = os.path.join(d, "b.jsonl")
        open(batch, "w").write("{}\n")
        json.dump({"usd": 10.0, "sha256": L.file_sha256(batch)}, open(batch + ".price.json", "w"))
        with self.assertRaises(SystemExit):
            L.check_price(batch, 19.0 / L.DIRECT_FACTOR)                      # 10 at Batch price = 20 direct > 19
        self.assertEqual(L.check_price(batch, 21.0 / L.DIRECT_FACTOR)["usd"], 10.0)


class Budget(unittest.TestCase):
    def test_direct_stops_sending_once_the_responses_spend_the_budget(self):
        d = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, d)
        batch, out = os.path.join(d, "b.jsonl"), os.path.join(d, "r.jsonl")
        with open(batch, "w", encoding="utf-8") as f:
            for i in range(5):
                f.write(json.dumps({"custom_id": f"x-{i}", "params": {"model": "claude-opus-5-5", "max_tokens": 8, "messages": []}}) + "\n")
        msg = {"content": [{"type": "text", "text": "A"}], "usage": {"input_tokens": 1_000_000, "output_tokens": 0}}   # US$4 each: 2 x the Batch US$2/MTok
        n, err, usd = L.direct(batch, out, 1, sender=lambda p: {"type": "succeeded", "message": msg}, max_usd=10.0)
        self.assertEqual((n, usd), (3, 12.0))                                   # the 3rd call crosses US$10; nothing after it is sent or written
        self.assertEqual(len(open(out).read().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
