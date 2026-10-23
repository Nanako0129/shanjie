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
        self.assertEqual((p["model"], p["max_tokens"], p["temperature"]), ("claude-opus-5-5", 8, 0))
        self.assertNotIn("thinking", p)
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

            def fake(req, timeout=None):
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


class Api(unittest.TestCase):
    def test_submit_status_endpoints_and_the_key_never_shown_or_written(self):
        d = tempfile.mkdtemp()
        try:
            b = os.path.join(d, "b.jsonl")
            with open(b, "w", encoding="utf-8") as f:
                f.write(json.dumps({"custom_id": "a-1", "params": {"max_tokens": 8}}) + "\n")
            calls = []

            def fake(req, timeout=None):
                calls.append((req.get_method(), req.full_url, req.data))
                return Resp({"id": "msgbatch_9", "processing_status": "in_progress", "request_counts": {"processing": 1}})
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": KEY}), mock.patch("urllib.request.urlopen", fake), \
                    contextlib.redirect_stdout(io.StringIO()) as so, contextlib.redirect_stderr(io.StringIO()) as se:
                self.assertEqual(L.main(["submit", b]), 0)
                self.assertEqual(L.main(["status", "msgbatch_9"]), 0)
            self.assertEqual(calls[0][:2], ("POST", "https://api.anthropic.com/v1/messages/batches"))
            self.assertEqual(json.loads(calls[0][2]), {"requests": [{"custom_id": "a-1", "params": {"max_tokens": 8}}]})
            self.assertEqual(calls[1][:2], ("GET", "https://api.anthropic.com/v1/messages/batches/msgbatch_9"))
            shown = so.getvalue() + se.getvalue()
            self.assertIn("msgbatch_9", shown)
            self.assertNotIn(KEY, shown)
            self.assertNotIn(KEY, os.listdir(d).__repr__() + open(b, encoding="utf-8").read())
            self.assertEqual(os.listdir(d), ["b.jsonl"])
        finally:
            shutil.rmtree(d)

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


if __name__ == "__main__":
    unittest.main()
