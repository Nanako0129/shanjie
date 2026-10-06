"""Tests for tools/bench.py's reference row, static table check and table rendering (sbench 7.1, 7.2, 7.4).
Run: python3 -m unittest tools/test_bench.py   (from the repo root). No network, no private data, no real builds."""
import argparse, contextlib, io, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402

MD = """# typing test
| 輸入法 | 錯句／錯字 | 句數 |
|---|---|---|
| 蘋果 | 3 句／3 字 | 23 |
| 小麥 | 5 句／12 字 | 23 |
"""


def ref(line, col, value, part=None):
    return {"line": line, "col": col, "part": part, "value": value}


STATIC_OK = {"date": "2026-10-03", "groups": [{"name": "第一輪", "n": ref(4, 2, 23), "ime": {
    "蘋果": {"sentences": ref(4, 1, 3, 0), "chars": ref(4, 1, 3, 1)},   # "3 句／3 字": same number twice, two positions
    "小麥": {"sentences": ref(5, 1, 5, 0), "chars": ref(5, 1, 12, 1)}}}]}


class Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = self._td.name
        self.addCleanup(self._td.cleanup)

    def write(self, name, text):
        p = os.path.join(self.td, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text if isinstance(text, str) else json.dumps(text, ensure_ascii=False))
        return p


class StaticCheck(Tmp):
    def check(self, static):
        return bench.check_static(self.write("s.json", static), self.write("t.md", MD))

    def test_correct_fixture_passes(self):
        self.assertEqual(self.check(STATIC_OK), [])

    def test_altering_any_cell_fails(self):
        refs = [r for _, r in bench._refs(STATIC_OK)]
        self.assertEqual(len(refs), 5)
        for i in range(len(refs)):
            bad = json.loads(json.dumps(STATIC_OK))
            list(r for _, r in bench._refs(bad))[i]["value"] += 1
            self.assertTrue(self.check(bad), f"altered cell {i} not detected")

    def test_part_position_matters(self):
        bad = json.loads(json.dumps(STATIC_OK))
        bad["groups"][0]["ime"]["小麥"]["chars"]["part"] = 0  # 5 instead of 12
        self.assertTrue(self.check(bad))

    def test_wrong_position_or_missing_part_fails(self):
        for edit in ({"col": 2}, {"line": 5}, {"part": None}):
            bad = json.loads(json.dumps(STATIC_OK))
            bad["groups"][0]["ime"]["蘋果"]["sentences"].update(edit)
            self.assertTrue(self.check(bad), edit)

    def test_cell_without_line_or_column_fails(self):
        self.assertTrue(self.check({"groups": [{"n": {"value": 23}}]}))


class Reference(Tmp):
    def setUp(self):
        super().setUp()
        rows = self.write("typing.txt", "|你好|ㄋㄧˇ ㄏㄠˇ\n")
        self.cmds, self.sha = [], "a" * 40
        self.saved = {k: getattr(bench, k) for k in ("SETS", "RESULTS", "CACHE", "sh", "head_sha", "tree_dirty", "BASE_LEXICON")}
        bench.SETS = {"pub": (rows, None, ["chat"], False), "priv": ("none.txt", None, ["chat"], True)}
        bench.RESULTS, bench.CACHE = os.path.join(self.td, "results"), os.path.join(self.td, "cache")
        bench.BASE_LEXICON = self.write("lex.txt", "lexicon")
        bench.head_sha, bench.tree_dirty, bench.sh = lambda: self.sha, lambda: False, self.fake_sh
        self.addCleanup(lambda: [setattr(bench, k, v) for k, v in self.saved.items()])
        self.args = argparse.Namespace(private_root=os.path.join(self.td, "empty"))
        os.makedirs(self.args.private_root)

    def fake_sh(self, cmd, **kw):
        if cmd[0] == "cargo":
            return ""
        self.cmds.append(cmd)
        dump = cmd[cmd.index("--dump") + 1]
        with open(dump, "w", encoding="utf-8") as f:
            f.write("1\t1\t你好\t-1.0\n1\t2\t妳好\t-2.0\n")
        res, _ = bench.score(cmd[cmd.index("--rows") + 1], dump)
        return f"## x  unigram  {{'n': {res['n']}, 'top1': {res['top1']}, 'oracle@64': {res['oracle64']}, 'top1_sha256': '{res['top1_sha256']}'}}\n"

    def run_ref(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            bench.cmd_reference(self.args)
        return err.getvalue()

    def result(self):
        return json.load(open(os.path.join(bench.RESULTS, bench.REFERENCE), encoding="utf-8"))

    def test_command_has_no_overlay_and_no_lm(self):
        self.run_ref()
        (cmd,) = self.cmds
        self.assertIn("--no-overlay", cmd)
        for bad in ("--lm", "--profile"):
            self.assertNotIn(bad, cmd)
        self.assertEqual(self.result()["accuracy"]["priv"], {"missing": True})
        self.assertEqual(self.result()["accuracy"]["pub"]["top1"], 1)

    def test_missing_private_set_leaves_private_root_empty(self):
        self.run_ref()
        self.assertEqual(os.listdir(self.args.private_root), [])

    def test_failed_set_is_retried_and_says_why(self):
        real = self.fake_sh

        def broken(cmd, **kw):
            if cmd[0] != "cargo":
                raise RuntimeError("simulated failure")
            return real(cmd, **kw)
        bench.sh = broken
        with self.assertRaises(SystemExit):
            self.run_ref()
        self.assertIn("error", self.result()["accuracy"]["pub"])
        bench.sh = real
        err = self.run_ref()
        self.assertIn("failed last time: pub", err)
        self.assertEqual(self.result()["accuracy"]["pub"]["top1"], 1)

    def test_same_commit_is_not_recomputed(self):
        self.run_ref()
        err = self.run_ref()
        self.assertEqual(len(self.cmds), 1)
        self.assertIn("current", err)

    def test_new_commit_recomputes_and_says_why(self):
        self.run_ref()
        self.sha = "b" * 40
        err = self.run_ref()
        self.assertEqual(len(self.cmds), 2)
        self.assertIn("commit changed", err)
        self.assertEqual(self.result()["commit"], self.sha)

    def test_changed_fingerprint_recomputes_and_says_why(self):
        self.run_ref()
        with open(bench.BASE_LEXICON, "a") as f:
            f.write("x")
        err = self.run_ref()
        self.assertEqual(len(self.cmds), 2)
        self.assertIn("fingerprint", err)

    def test_reference_row_has_no_fixed_broken(self):
        self.run_ref()
        row = bench.reference_row(self.result(), ["pub/chat", "priv/chat"])
        self.assertIn("小麥資料基準", row)
        self.assertIn(self.sha[:7], row)
        self.assertIn("不是小麥本身的解碼器", row)
        self.assertNotIn("修好", row)
        self.assertNotIn("p=", row)
        self.assertNotIn("/ −", row)


class Table(Tmp):
    def test_version_rows_and_holdout_table_unchanged(self):
        cell = {"n": 10, "top1": 7, "oracle64": 9, "top1_sha256": "x"}
        res = [{"label": "L", "date": "2026-10-05", "env": {"machine": "M", "macos": "27", "load_avg_start": 1.0, "load_avg_end": 2.0},
                "versions": [{"ref": "v1", "sha": "c" * 40, "accuracy": {"dev302/chat": cell}, "vs_prev": {"dev302/chat": {"fixed": 2, "broken": 1, "p": 1.0}},
                              "holdout": {"chat": cell}, "speed": {"p95_us": 1000, "max_us": 2000, "load_ms": 5, "rss_mb": 40, "keys": 99, "rows_match": "302/302"}}]}]
        refj = {"commit": "d" * 40, "accuracy": {"dev302": cell}}
        plain = bench.render_table(res)
        full = bench.render_table(res, refj, STATIC_OK)
        self.assertNotIn("## 實打對照", plain)
        head, rest = full.split("\n## 實打對照", 1)
        static, tail = rest.split("\n## 量測規則", 1)
        refrow = bench.reference_row(refj, [f"{n}/{p}" for n, (_, _, ps, _) in bench.SETS.items() for p in ps])
        self.assertEqual(head.replace(refrow + "\n", "") + "\n## 量測規則" + tail, plain)
        self.assertEqual(head.count(refrow), 1)
        self.assertLess(head.index(refrow), head.index("| v1<br>"))  # first row of the main table
        self.assertIn("2026-10-03", static)
        self.assertIn("第 202 行", static)
        self.assertIn("tools/bench.py reference", plain)


if __name__ == "__main__":
    unittest.main()
