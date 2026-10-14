"""Tests for tools/bench.py's reference row, static table check and table rendering (sbench 7.1, 7.2, 7.4).
Run: python3 -m unittest tools/test_bench.py   (from the repo root). No network, no private data, no real builds."""
import argparse, contextlib, io, json, os, stat, sys, tempfile, types, unittest
from unittest import mock

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


@contextlib.contextmanager
def mock_hash():
    """The model files in the tests are one byte: make sha_file return the hash named by the file's folder."""
    real = bench.sha_file
    bench.sha_file = lambda p: os.path.basename(os.path.dirname(p)) if "/models/" in p else real(p)
    try:
        yield
    finally:
        bench.sha_file = real


HEX = {"bigram.sjlm": "a" * 64, "classes.sjc": "b" * 64, "tinylm.sjtl": "7e73ef67" + "c" * 56}


def fake_ver(label, td, models=True):
    spec = bench.VERSIONS2[label]
    v = types.SimpleNamespace(label=label, sha="d" * 40, ref=label, dir=os.path.join(td, "src-" + label), cli="cli", spec=spec,
                              lm="lm", tl="/m/tinylm.sjtl" if spec["tinylm"] else None, tl8=HEX["tinylm.sjtl"][:8] if spec["tinylm"] else "")
    return v


class SuiteV2(Tmp):
    """Suite v2 (docs/contracts/sbench-v2.md): settings table, --as, model folder, rowstats scoring, guards. No real builds."""

    def setUp(self):
        super().setUp()
        self.saved = {k: getattr(bench, k) for k in ("CACHE", "MODELS", "sh", "RESULTS", "ROOT", "STATIC", "EXTERNAL")}
        bench.CACHE, bench.MODELS = os.path.join(self.td, "cache"), os.path.join(self.td, "models")
        self.addCleanup(lambda: [setattr(bench, k, v) for k, v in self.saved.items()])
        self.rows = self.write("rows.txt", "|你好|ㄋㄧˇ ㄏㄠˇ\n|世界|ㄕˋ ㄐㄧㄝˋ\n")
        self.ctx = types.SimpleNamespace(files={"dev302": self.rows, "pub": self.rows}, private_root=os.path.join(self.td, "priv"))
        bench.SETS2 = dict(bench.SETS2, pub=(self.rows, None, False))
        self.addCleanup(lambda: bench.SETS2.pop("pub", None))

    # --- 2.1 table, flags, tags
    def test_table_flags_and_tags(self):
        v0, v1 = fake_ver("v0.4.0", self.td), fake_ver("v0.4.1", self.td)
        self.assertEqual(bench.expected_tag(v0, "chat", False), "lm-chat+ctx+acg")
        self.assertEqual(bench.expected_tag(v1, "chat", False), "lm-chat+ctx+tl:7e73ef67+acg")
        self.assertEqual(bench.expected_tag(v1, "formal", False), "lm-formal+ctx+tl:7e73ef67+acg")
        self.assertEqual(bench.expected_tag(v1, "chat", True), "lm-chat+ctx+tl:7e73ef67")  # --override-packs off
        c0, c1 = (bench.cell_cmd(v, "chat", "r", "d", "s", False) for v in (v0, v1))
        self.assertEqual(c0[-3:], ["--context", "--packs", "acg"])
        self.assertNotIn("--tinylm", c0)
        self.assertEqual(c1[-5:], ["--context", "--packs", "acg", "--tinylm", "/m/tinylm.sjtl"])
        self.assertNotIn("--packs", bench.cell_cmd(v1, "chat", "r", "d", "s", True))
        self.assertIn("--rowstats", c1)
        self.assertNotIn("--dump", bench.cell_cmd(v1, "chat", "r", None, "s", False))  # only cells whose dump is read get one
        self.assertEqual(bench.cli_env(False)["SHANJIE_VARIANTS"], bench.VARIANTS)
        self.assertNotIn("SHANJIE_VARIANTS", bench.cli_env(True))

    def test_set_table_matches_the_contract(self):
        self.assertEqual([n for n in bench.SETS2 if n != "pub"], ["dev302", "typing76", "probe", "user-reported", "fw", "cvtune", "wikitune", "lytune", "wikitypo", "discordtune"])
        for n in ("dev302", "typing76", "probe"):
            self.assertEqual(bench.SETS2[n][:2], bench.SETS[n][:2])  # suite v1's files, same hashes
        for n, (f, want, private) in bench.SETS2.items():
            if want and not private and f.startswith(bench.SUITE2):
                self.assertEqual(bench.sha_file(f), want, n)
        self.assertEqual(bench.truths(bench.SETS2["user-reported"][0]).__len__(), 80)
        self.assertEqual(bench.truths(bench.SETS2["fw"][0]).__len__(), 186)

    # --- --as
    def test_as_names_the_last_ref(self):
        self.assertEqual(bench.version_labels(["v0.4.0", "abc123"], "v0.4.1"), ["v0.4.0", "v0.4.1"])
        self.assertEqual(bench.version_labels(["v0.4.0", "v0.4.1"], None), ["v0.4.0", "v0.4.1"])
        for refs, as_ in ((["v0.4.0", "abc123"], None), (["v0.4.0", "abc123"], "v9.9.9"), (["v0.4.0", "v0.4.0"], None)):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                bench.version_labels(refs, as_)

    # --- model folder
    def stage_models(self, label, names):
        v = fake_ver(label, self.td)
        os.makedirs(os.path.join(v.dir, "data"))
        for n in names:
            with open(os.path.join(v.dir, "data", n + ".sha256"), "w") as f:
                f.write(f"{HEX[n]}  data/lm/{n}\n")
        return v

    def put_model(self, n, content=None):
        d = os.path.join(bench.MODELS, HEX[n])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, n), "wb") as f:
            f.write(content or b"x")

    def test_model_folder_has_only_bigram_and_classes(self):
        v = self.stage_models("v0.4.1", HEX)
        for n in HEX:
            self.put_model(n)
        with mock_hash():
            bench.setup_models(v, bench.VERSIONS2["v0.4.1"], os.path.join(self.td, "mf"))
        self.assertEqual(sorted(os.listdir(os.path.dirname(v.lm))), ["bigram.sjlm", "classes.sjc"])  # never kn.sjkn, never the .sjtl
        self.assertTrue(v.tl.endswith("tinylm.sjtl"))
        self.assertEqual(v.tl8, "7e73ef67")

    def test_v040_has_no_tinylm_sha_file_and_needs_none(self):
        v = self.stage_models("v0.4.0", ["bigram.sjlm", "classes.sjc"])
        for n in ("bigram.sjlm", "classes.sjc"):
            self.put_model(n)
        with mock_hash():
            bench.setup_models(v, bench.VERSIONS2["v0.4.0"], os.path.join(self.td, "mf"))
        self.assertIsNone(v.tl)

    def test_missing_or_wrong_model_fails(self):
        v = self.stage_models("v0.4.1", HEX)
        err = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):  # nothing cached
            bench.setup_models(v, bench.VERSIONS2["v0.4.1"], os.path.join(self.td, "mf1"))
        self.assertIn("download bigram.sjlm from Release v0.4.1", err.getvalue())
        for n in HEX:
            self.put_model(n)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err):  # files exist, hashes do not match data/*.sha256
            bench.setup_models(v, bench.VERSIONS2["v0.4.1"], os.path.join(self.td, "mf2"))
        self.assertIn("does not match", err.getvalue())

    # --- rowstats scoring and the guards
    def fake_cli(self, tag, ok, first, extra_rows=0):
        def sh(cmd, **kw):
            if "--dump" in cmd:
                with open(cmd[cmd.index("--dump") + 1], "w", encoding="utf-8") as f:
                    for i, w in enumerate(first, 1):
                        f.write(f"{i}\t1\t{w}\t-1.0\n{i}\t2\t別的\t-2.0\n")
            if "--rowstats" in cmd:
                with open(cmd[cmd.index("--rowstats") + 1], "w") as f:
                    f.write("".join(f"{i}\t{int(o)}\t0\t2\n" for i, o in enumerate(ok, 1)))
            return f"## rows.txt  {tag}  {{'n': 2, 'top1': {sum(ok)}, 'oracle@64': 2, 'top1_sha256': '{bench.sha_str(chr(10).join(first))}'}}\n"
        bench.sh = sh

    def cell(self, label="v0.4.0", tag=None, ok=(True, True), first=("你好", "世界")):
        v = fake_ver(label, self.td)
        self.fake_cli(tag or bench.expected_tag(v, "chat", False), list(ok), first)
        return bench.run_cell2(self.ctx, v, "pub", "chat", False)

    def test_cell_scores_from_rowstats(self):
        res, ok = self.cell("v0.4.1", ok=(True, False), first=("別的", "世界"))  # rerank on: the chosen sentence is not the dump's first row
        self.assertEqual((res["n"], res["top1"]), (2, 1))
        self.assertEqual(ok, [True, False])
        self.assertEqual(res["tag"], "lm-chat+ctx+tl:7e73ef67+acg")

    def test_no_tinylm_cell_cross_checks_the_dump(self):
        # the first row of the dump is the chosen one here: 2 rows right by the tool's own lenient scoring
        res, ok = self.cell("v0.4.0")
        self.assertEqual(ok, [True, True])
        # fixture: rowstats says row 2 is wrong while the dump's first row is right -> the cross-check fails
        with self.assertRaisesRegex(RuntimeError, "disagrees with rowstats"):
            self.cell("v0.4.0", ok=(True, False))
        # and the reverse: rowstats says both right while the dump's first row 2 is wrong
        with self.assertRaisesRegex(RuntimeError, "disagrees with rowstats"):
            self.cell("v0.4.0", ok=(True, True), first=("你好", "別的"))

    def test_wrong_setting_tag_fails_the_cell(self):
        for tag in ("lm-chat+ctx", "lm-chat+ctx+acg+kn:abc", "lm-chat+acg"):  # flag dropped / a kn.sjkn picked up
            with self.assertRaisesRegex(RuntimeError, "setting tag"):
                self.cell("v0.4.0", tag=tag)
        with self.assertRaisesRegex(RuntimeError, "setting tag"):  # v0.4.1 without --packs acg
            self.cell("v0.4.1", tag="lm-chat+ctx+tl:7e73ef67")
        for lv in ("conservative", "aggressive"):  # --rerank-level other than standard: the CLI adds +rl:, which is not the shipping setting
            with self.assertRaisesRegex(RuntimeError, "setting tag"):
                self.cell("v0.4.1", tag=f"lm-chat+ctx+tl:7e73ef67+rl:{lv}+acg")

    def test_malformed_rowstats_fail(self):
        for text in ("1\t1\t0\n", "1\tx\t0\t2\n2\t1\t0\t2\n", "2\t1\t0\t2\n1\t1\t0\t2\n", "1\t1\t0\t2\n"):
            with self.assertRaises(RuntimeError):
                bench.parse_rowstats(self.write("rs.tsv", text), 2)
        self.assertEqual(bench.parse_rowstats(self.write("rs.tsv", "1\t1\t0\t2\n2\t0\t1\t2\n"), 2), [True, False])

    def test_summary_must_be_one_line(self):
        with self.assertRaises(RuntimeError):
            bench.parse_summary("nothing\n")

    # --- replay consistency (own CLI cell, replay settings)
    def replay_script(self, commits):
        p = self.write("replay.py", "#!/usr/bin/env python3\nimport sys\nopen(sys.argv[4], 'w').write(%r)\nprint('{\"keys\":9,\"load_ms\":1.0,\"p95_us\":2.0,\"max_us\":3.0}')\n" % commits)
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR)
        return p

    def speed(self, first, commits="你好\n世界\n"):
        v = fake_ver("v0.4.0", self.td)
        v.replay = self.replay_script(commits)
        self.fake_cli("lm-chat", [True, True], first)
        return bench.measure_speed2(self.ctx, [v])[v.sha]

    def test_replay_consistency(self):
        s = self.speed(("你好", "世界"))
        self.assertEqual((s["rows_match"], s["rounds"], s["p95_us"]), ("2/2", 3, 2.0))
        self.assertNotIn("rerank_p50_ms", s)

    def test_replay_differs_from_the_comparison_cell_fails(self):
        s = self.speed(("你好", "改掉的"))  # the comparison cell's row 2 changed
        self.assertIn("replay differs", s["error"])
        self.assertIn("[2]", s["error"])

    def test_comparison_cell_must_carry_the_replay_settings(self):
        v = fake_ver("v0.4.0", self.td)
        v.replay = self.replay_script("你好\n世界\n")
        self.fake_cli("lm-chat+ctx+acg", [True, True], ("你好", "世界"))
        self.assertIn("setting tag", bench.measure_speed2(self.ctx, [v])[v.sha]["error"])

    # --- table: suite v1 text unchanged, v2 appended, v2 files invisible to v1
    def test_external_section_rows(self):
        x = {"name": "m", "date": "2026-10-13", "conditions": "c", "label": "0c", "ours_label": "v3",
             "rows": [{"setting": "chat", "set": "dev302", "n": 4, "ngram": 1, "ours": 2, "external": 3, "fixed": 2, "broken": 1, "p": 1.0},
                      {"setting": "formal", "set": "cvtune", "n": 4, "ngram": 4, "ours": 4, "external": 4, "fixed": 0, "broken": 0, "p": 1.0,
                       "tuning": True},
                      {"setting": "chat", "set": "x", "n": 4, "ngram": 1, "ours": 1, "external": 4, "fixed": 3, "broken": 0, "p": 0.0}]}
        t = bench.render_external([x])
        self.assertIn("## 外部模型對照（不是善解的版本）", t)
        self.assertIn("| chat／dev302 | 4 | 25.00%<br>1/4 | 50.00%<br>2/4 | 75.00%<br>3/4 | +2 / −1，p=1 |", t)
        self.assertIn("| formal／cvtune（選值用） |", t)
        self.assertIn("+3 / −0，p<0.0001 |", t)

    def test_table_appends_v2_after_unchanged_v1_text(self):
        bench.RESULTS, bench.ROOT, bench.STATIC = (os.path.join(self.td, "results"), os.path.join(self.td, "root"), os.path.join(self.td, "none.json"))
        bench.EXTERNAL = os.path.join(self.td, "no-external")
        os.makedirs(bench.RESULTS)
        os.makedirs(os.path.join(bench.ROOT, "docs"))
        cell = {"n": 10, "top1": 7, "oracle64": 9, "top1_sha256": "x"}
        env = {"machine": "M", "macos": "27", "load_avg_start": 1.0, "load_avg_end": 2.0}
        v1 = {"label": "L", "date": "2026-10-05", "env": env, "versions": [{"ref": "v1", "sha": "c" * 40, "accuracy": {"dev302/chat": cell}}]}
        speed = {"p95_us": 1000, "max_us": 2000, "load_ms": 5, "rss_mb": 40, "keys": 99, "rows_match": "302/302"}
        recs = [{"ref": "v0.4.0", "version": "v0.4.0", "sha": "a" * 40, "models": {"bigram.sjlm": "a" * 64}, "flags": ["--context"], "accuracy": {"dev302/chat": cell}, "speed": speed},
                {"ref": "c", "version": "v0.4.1", "sha": "b" * 40, "models": {"bigram.sjlm": "a" * 64}, "flags": ["--context", "--tinylm", "<file>"],
                 "accuracy": {"dev302/chat": cell, "discordtune/chat": {"missing": True}}, "vs_prev": {"dev302/chat": {"fixed": 3, "broken": 1, "p": 0.625}},
                 "speed": dict(speed, rerank_p50_ms=7.0, rerank_p95_ms=12.0), "holdout": {"commit": "e" * 40, "chat": {"n": 227, "top1": 191, "fixed": 11, "broken": 0, "p": 0.001}}}]
        v2 = {"label": "v0.4.0+v0.4.1", "suite": "v2", "date": "2026-10-13", "env": env, "versions": recs}
        out = os.path.join(bench.ROOT, "docs/benchmark.md")
        with open(os.path.join(bench.RESULTS, "2026-10-05-baseline.json"), "w") as f:
            json.dump(v1, f)
        bench.cmd_table(None)
        before = open(out, encoding="utf-8").read()
        with open(os.path.join(bench.RESULTS, "v2-v0.4.0+v0.4.1.json"), "w") as f:
            json.dump(v2, f)
        bench.cmd_table(None)
        after = open(out, encoding="utf-8").read()
        self.assertTrue(after.startswith(before))
        self.assertEqual(after.count("\n## "), before.count("\n## ") + 1)
        tail = after[len(before):]
        self.assertIn("## 套件 v2", tail)
        self.assertIn("+3 / −1，p=0.625", tail)
        self.assertIn("191/227", tail)
        self.assertIn("| v0.4.1<br>`eeeeeee` |", tail)  # the holdout row names the commit the holdout ran on
        self.assertIn("| v0.4.0<br>`aaaaaaa` |", tail)  # no commit recorded: the measured one
        self.assertIn("7.00／12.00 ms", tail)
        self.assertNotIn("v0.4.1", before)
        bench.cmd_table(None)  # deterministic
        self.assertEqual(open(out, encoding="utf-8").read(), after)

    def test_rerun_never_overwrites_holdout_numbers(self):
        bench.RESULTS = os.path.join(self.td, "results")
        os.makedirs(bench.RESULTS)
        with open(os.path.join(bench.RESULTS, "v2-v0.4.0+v0.4.1.json"), "w") as f:
            json.dump({"versions": [{"version": "v0.4.1", "holdout": {"chat": {"n": 1, "top1": 1}}}]}, f)
        args = types.SimpleNamespace(lm=None, base=None, override_packs=None, refs=["v0.4.0", "v0.4.1"], as_=None, label=None, private_root=self.td)
        err = io.StringIO()
        # preflight is a tripwire: without the guard the test fails here, before any build starts
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(err), \
                mock.patch.object(bench, "preflight2", side_effect=AssertionError("reached preflight")):
            bench.cmd_run2(args)  # stops before preflight and any build
        self.assertIn("holdout numbers", err.getvalue())


if __name__ == "__main__":
    unittest.main()
