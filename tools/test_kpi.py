"""Tests for tools/kpi.py (docs/contracts/kpi.md section 4.1). Run: python3 -m unittest tools/test_kpi.py (from the repo root).
Synthetic JSON in temp dirs and fake CLI scripts; no builds, no real models, no private data."""
import contextlib, hashlib, io, json, os, subprocess, sys, tempfile, types, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402
import kpi  # noqa: E402

H1, H2 = "1" * 64, "2" * 64


def acc(top1, n=100):
    return {"n": n, "top1": top1}


def v2_version(version, top1=80, vs_prev=None, speed=None, lytune=None):
    r = {"version": version, "accuracy": {"discordtune/chat": acc(top1)}}
    if lytune is not None:
        r["accuracy"]["lytune/formal"] = acc(lytune, 50)
    if vs_prev is not None:
        r["vs_prev"] = {"discordtune/chat": vs_prev}
    if speed is not None:
        r["speed"] = speed
    return r


def v2_file(date, versions, h=H1, load=(0.5, 0.5)):
    return {"date": date, "sets": {"discordtune": {"sha256": h, "private": True}, "lytune": {"sha256": H1, "private": False}},
            "versions": versions, "env": {"load_avg_start": load[0], "load_avg_end": load[1]}}


SPEED = {"p95_us": 6822.2, "rss_mb": 426.4, "rerank_p95_ms": 23.4}


class Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = self._td.name
        self.addCleanup(self._td.cleanup)
        self.res, self.kp = os.path.join(self.td, "results"), os.path.join(self.td, "kpi")
        os.makedirs(self.res)
        os.makedirs(self.kp)

    def put(self, d, name, data):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def table(self):
        """-> {version: {column title (before <br>) or title+<br>+detail: cell}}"""
        lines = [l for l in kpi.render(self.res, self.kp).split("\n") if l.startswith("| ")]
        head = [c.strip() for c in lines[0].strip("|").split(" | ")]
        return head, {r[0]: dict(zip(head[1:], r[1:])) for r in ([c.strip() for c in l.strip("|").split(" | ")] for l in lines[1:])}

    def col(self, rows, prefix):
        """cells of the version rows in the one column whose header starts with `prefix`"""
        k = [h for h in next(iter(rows.values())) if h.startswith(prefix)]
        self.assertEqual(len(k), 1, k)
        return {v: r[k[0]] for v, r in rows.items()}


class TableRules(Tmp):
    def test_version_comes_from_the_file_where_it_is_the_newer_side(self):
        # v0.4.1 is the older side of file A (top1 70) and the newer side of file B (top1 90): the table must say 90
        self.put(self.res, "v2-A.json", v2_file("2026-10-01", [v2_version("v0.4.1", 70), v2_version("v0.4.2", 75, {"fixed": 1, "broken": 1})]))
        self.put(self.res, "v2-B.json", v2_file("2026-10-02", [v2_version("v0.4.0", 60), v2_version("v0.4.1", 90, {"fixed": 1, "broken": 1})]))
        _, rows = self.table()
        c = self.col(rows, "discordtune 聊天 top1")
        self.assertTrue(c["v0.4.1"].startswith("90.00%"), c)
        self.assertTrue(c["v0.4.2"].startswith("75.00%"), c)
        self.assertTrue(c["v0.4.0"].startswith("60.00%"), c)  # only on an older side: its one file

    def test_error_ratio_is_broken_over_fixed_and_dash_without_fixed(self):
        self.put(self.res, "v2-A.json", v2_file("2026-10-01", [v2_version("v0.4.0"), v2_version("v0.4.1", vs_prev={"fixed": 4, "broken": 1})]))
        self.put(self.res, "v2-B.json", v2_file("2026-10-02", [v2_version("v0.4.1"), v2_version("v0.4.2", vs_prev={"fixed": 0, "broken": 3})]))
        self.put(self.res, "v2-C.json", v2_file("2026-10-03", [v2_version("v0.4.2"), v2_version("v0.4.3", vs_prev={"error": "x"})]))
        _, rows = self.table()
        c = self.col(rows, "error ratio")
        self.assertEqual(c["v0.4.1"], "0.25（1/4）")  # broken / fixed, not fixed / broken
        self.assertEqual(c["v0.4.2"], "—")  # nothing fixed
        self.assertEqual(c["v0.4.3"], "—")  # the cell has no counts
        self.assertEqual(c["v0.4.0"], "—")  # no previous version

    def test_speed_only_when_load_is_low_at_both_ends(self):
        for name, load, shown in (("a", (0.5, 2.0), True), ("b", (2.01, 0.5), False), ("c", (0.5, 2.01), False), ("d", (37.52, 31.4), False)):
            self.put(self.res, f"v2-{name}.json", v2_file("2026-10-01", [v2_version("v0." + name, speed=SPEED)], load=load))
        _, rows = self.table()
        c = self.col(rows, "速度與記憶體")
        self.assertIn("RSS 426 MB", c["v0.a"])  # exactly 2.0 still counts
        self.assertEqual(c["v0.b"], "—（負載 2.01）")  # two decimals: 2.01 is not shown as the threshold itself
        self.assertEqual(c["v0.c"], "—（負載 2.01）")
        self.assertEqual(c["v0.d"], "—（負載 37.52）")

    def test_speed_columns_split_per_machine(self):
        for name, machine in (("a", "Mac17,3"), ("b", "gcp-c4d")):
            d = v2_file("2026-10-01", [v2_version("v0." + name, speed=SPEED)])
            d["env"]["machine"] = machine
            self.put(self.res, f"v2-{name}.json", d)
        head, rows = self.table()
        cols = [h for h in head if h.startswith("速度與記憶體")]
        self.assertEqual(len(cols), 2, head)
        a, b = (next(h for h in cols if m in h) for m in ("Mac17,3", "gcp-c4d"))
        self.assertEqual((rows["v0.b"][a], rows["v0.a"][b]), ("—", "—"))
        self.assertIn("RSS", rows["v0.a"][a])

    def test_version_without_replay_gets_dashes_not_an_error(self):
        self.put(self.res, "v2-A.json", v2_file("2026-10-01", [v2_version("v0.4.0")]))
        self.put(self.kp, "v0.4.0.json", {"version": "v0.4.0"})  # a kpi file without a replay field
        _, rows = self.table()
        for p in ("錯字回報重播 top1", "錯字回報重播 學習開改壞", "按鍵分數 KySS（dev302", "按鍵分數 KySS（錯字", "顯示後被改掉", "保留集口語", "保留集新聞", "wikitune-clean"):
            self.assertEqual(self.col(rows, p), {"v0.4.0": "—"}, p)

    def test_split_columns_when_v2_set_hashes_differ(self):
        self.put(self.res, "v2-A.json", v2_file("2026-10-01", [v2_version("v0.4.0", 60), v2_version("v0.4.1", 70, {"fixed": 1, "broken": 1})], h=H1))
        self.put(self.res, "v2-B.json", v2_file("2026-10-02", [v2_version("v0.4.1"), v2_version("v0.4.2", 80, {"fixed": 1, "broken": 1})], h=H2))
        head, rows = self.table()
        cols = [h for h in head if h.startswith("discordtune 聊天 top1")]
        self.assertEqual(len(cols), 2, head)
        self.assertTrue(any("#11111111" in h for h in cols) and any("#22222222" in h for h in cols))
        a = next(h for h in cols if "#11111111" in h)
        b = next(h for h in cols if "#22222222" in h)
        self.assertEqual((rows["v0.4.1"][a][:6], rows["v0.4.1"][b]), ("70.00%", "—"))  # a number never sits in the other set's column
        self.assertEqual((rows["v0.4.2"][a], rows["v0.4.2"][b][:6]), ("—", "80.00%"))

    def test_split_columns_when_kpi_rows_hashes_differ(self):
        def replay(h, k):
            blk = {"rows_sha256": h, "n": 10, "off": {"top1": k, "kyss": 0.9, "rewrites": 1}}
            return {"replay": {"user-reported": {**blk, "learn_broken": 2}, "dev302": blk}, "wikitune_clean": {"n": 20, "top1": k, "rows_sha256": h}}
        self.put(self.kp, "v0.4.6.json", replay(H1, 5))
        self.put(self.kp, "v0.4.7.json", replay(H2, 6))
        head, rows = self.table()
        for p, n in (("錯字回報重播 top1", 2), ("錯字回報重播 學習開改壞", 2), ("按鍵分數 KySS（dev302", 2), ("顯示後被改掉", 2), ("wikitune-clean", 2)):
            cols = [h for h in head if h.startswith(p)]
            self.assertEqual(len(cols), n, (p, head))
            ca, cb = (next(h for h in cols if x in h) for x in ("#11111111", "#22222222"))
            self.assertEqual((rows["v0.4.6"][cb], rows["v0.4.7"][ca]), ("—", "—"), p)
            self.assertNotEqual(rows["v0.4.6"][ca], "—", p)
            self.assertNotEqual(rows["v0.4.7"][cb], "—", p)

    def test_replay_cells(self):
        blk = {"rows_sha256": H1, "n": 80, "off": {"top1": 50, "kyss": 0.91234, "rewrites": 4}, "learn_broken": 3, "learn_fixed": 1}
        self.put(self.kp, "v0.4.7.json", {"replay": {"user-reported": blk, "dev302": {**blk, "n": 302}}})
        _, rows = self.table()
        self.assertEqual(self.col(rows, "錯字回報重播 top1")["v0.4.7"], "62.50%（50/80）")
        self.assertEqual(self.col(rows, "錯字回報重播 學習開改壞")["v0.4.7"], "3")
        self.assertEqual(self.col(rows, "按鍵分數 KySS（錯字")["v0.4.7"], "0.9123")
        self.assertEqual(self.col(rows, "顯示後被改掉")["v0.4.7"], "1.3 次／百列（4/302）")  # dev302 rewrites per 100 rows, a count not a share of rows

    def test_holdout_halves_from_kpi_json(self):
        self.put(self.kp, "v0.4.7.json", {"holdout_talk_chat": {"n": 150, "top1": 120, "fixed": 3, "broken": 1, "p": 0.6, "rows_sha256": H1},
                                          "holdout_news_formal": {"n": 150, "top1": 135, "fixed": 2, "broken": 0, "p": 0.5, "rows_sha256": H1}})
        head, rows = self.table()
        self.assertEqual(self.col(rows, "保留集口語半邊（聊天）")["v0.4.7"], "80.00%（120/150）")
        self.assertEqual(self.col(rows, "保留集新聞半邊（書面）")["v0.4.7"], "90.00%（135/150）")

    def test_holdout_columns_split_by_rows_hash_even_with_equal_n(self):
        for v, h in (("v0.4.7", H1), ("v0.4.8", H2)):  # two shards, both 150 rows
            self.put(self.kp, v + ".json", {"holdout_talk_chat": {"n": 150, "top1": 120, "rows_sha256": h}, "holdout_news_formal": {"n": 150, "top1": 135, "rows_sha256": h}})
        head, rows = self.table()
        for p in ("保留集口語半邊", "保留集新聞半邊"):
            cols = [h for h in head if h.startswith(p)]
            self.assertEqual(len(cols), 2, head)
            a, b = (next(h for h in cols if x in h) for x in ("#11111111", "#22222222"))
            self.assertEqual((rows["v0.4.7"][a] != "—", rows["v0.4.7"][b], rows["v0.4.8"][a], rows["v0.4.8"][b] != "—"), (True, "—", "—", True), p)

    def test_table_is_byte_identical_across_runs(self):
        for i, h in enumerate((H1, H2, "3" * 64)):  # several column groups, so an unordered iteration would show
            self.put(self.res, f"v2-{i}.json", v2_file(f"2026-10-0{i + 1}", [v2_version(f"v0.4.{i}"), v2_version(f"v0.4.{i + 1}", 70, {"fixed": 2, "broken": 1}, SPEED)], h=h))
            self.put(self.kp, f"v0.4.{i}.json", {"wikitune_clean": {"n": 5, "top1": 4, "rows_sha256": h}})
        outs = []
        for seed in ("1", "2", "3"):  # different string-hash seeds: any set/dict-of-str ordering leaking into the output changes it
            out = os.path.join(self.td, f"kpi{seed}.md")
            subprocess.run([sys.executable, kpi.__file__, "table", "--results", self.res, "--kpi", self.kp, "--out", out], check=True,
                           env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"})
            outs.append(open(out, "rb").read())
        self.assertTrue(outs[0] == outs[1] == outs[2])
        self.assertGreater(len(outs[0]), 500)


# ---------- replay ----------
SPEC = bench.VERSIONS2["v0.4.3"]  # bigram + classes + tinylm


def make_root(td, names=("bigram.sjlm", "classes.sjc", "tinylm.sjtl")):  # (a version that ships the colloquial table adds "colloquial.sjlm")
    root = os.path.join(td, "root")
    os.makedirs(os.path.join(root, "data/lm"))
    for n in names:
        body = n.encode()
        open(os.path.join(root, "data/lm", n), "wb").write(body)
        open(os.path.join(root, "data", n + ".sha256"), "w").write(hashlib.sha256(body).hexdigest() + "  " + n + "\n")
    return root


class ReplayModels(Tmp):
    def test_matching_models_pass(self):
        root = make_root(self.td)
        self.assertEqual(set(kpi.check_models(root, SPEC)), {"bigram.sjlm", "classes.sjc", "tinylm.sjtl"})

    def test_the_colloquial_table_is_a_model_only_for_a_version_that_ships_it(self):
        root = make_root(self.td, ("bigram.sjlm", "classes.sjc", "tinylm.sjtl", "colloquial.sjlm"))
        self.assertEqual(set(kpi.check_models(root, dict(SPEC, colloquial=True))), {"bigram.sjlm", "classes.sjc", "tinylm.sjtl", "colloquial.sjlm"})
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            kpi.check_models(root, SPEC)
        self.assertIn("colloquial.sjlm", err.getvalue())

    def test_hash_mismatch_fails(self):
        root = make_root(self.td)
        open(os.path.join(root, "data/lm/classes.sjc"), "wb").write(b"changed")
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            kpi.check_models(root, SPEC)
        self.assertIn("classes.sjc", err.getvalue())

    def test_extra_model_file_fails(self):
        root = make_root(self.td, ("bigram.sjlm", "classes.sjc", "tinylm.sjtl", "kn.sjkn"))  # v0.4.3 does not ship kn.sjkn
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            kpi.check_models(root, SPEC)
        self.assertIn("kn.sjkn", err.getvalue())

    def test_missing_model_file_fails(self):
        root = make_root(self.td)
        os.remove(os.path.join(root, "data/lm/tinylm.sjtl"))  # its data/tinylm.sjtl.sha256 is there: the model file itself is missing
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            kpi.check_models(root, SPEC)
        self.assertIn("data/lm/tinylm.sjtl is missing", err.getvalue())

    def test_missing_hash_file_fails_clearly(self):
        root = make_root(self.td)
        os.remove(os.path.join(root, "data/tinylm.sjtl.sha256"))
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit):
            kpi.check_models(root, SPEC)
        self.assertIn("data/tinylm.sjtl.sha256 is missing", err.getvalue())


def git(root, *a):
    return subprocess.run(["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True, text=True).stdout.strip()


class ReplayGit(Tmp):
    def setUp(self):
        super().setUp()
        self.repo = os.path.join(self.td, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        open(os.path.join(self.repo, "a.txt"), "w").write("1")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "one")
        self.first = git(self.repo, "rev-parse", "HEAD")

    def head(self, version="v9.9.9"):
        with contextlib.redirect_stderr(io.StringIO()):
            return kpi.git_clean_head(self.repo, version)

    def test_clean_tree_returns_head(self):
        self.assertEqual(self.head(), self.first)

    def test_untracked_results_do_not_block(self):  # what bench.py / kpi.py run write beside the checkout's tracked files
        os.makedirs(os.path.join(self.repo, "eval/bench/kpi"))
        open(os.path.join(self.repo, "eval/bench/kpi/v9.9.9.json"), "w").write("{}")
        self.assertEqual(self.head(), self.first)

    def test_tracked_change_blocks(self):
        open(os.path.join(self.repo, "a.txt"), "w").write("2")
        with self.assertRaises(SystemExit):
            self.head()

    def test_existing_tag_must_be_head(self):
        git(self.repo, "tag", "v9.9.9")
        self.assertEqual(self.head(), self.first)  # tag at HEAD: fine
        open(os.path.join(self.repo, "a.txt"), "w").write("2")
        git(self.repo, "commit", "-q", "-am", "two")
        with self.assertRaises(SystemExit):
            self.head()  # the tag is one commit behind HEAD
        self.assertNotEqual(self.head("v9.9.10"), self.first)  # no such tag yet: any HEAD


class ReplayBuild(unittest.TestCase):
    def test_executable_comes_from_the_compiler_artifact_message(self):
        out = "\n".join(json.dumps(m) for m in (
            {"reason": "compiler-artifact", "target": {"name": "cli", "kind": ["lib"]}, "executable": None},
            {"reason": "compiler-artifact", "target": {"name": "shanjie-eval", "kind": ["bin"]}, "executable": "/x/shanjie-eval"},
            {"reason": "compiler-artifact", "target": {"name": "shanjie-replay", "kind": ["bin"]}, "executable": "/x/shanjie-replay"},
            {"reason": "build-finished", "success": True})) + "\nnot json\n"
        r = subprocess.CompletedProcess([], 0, out, "")
        with mock.patch.object(subprocess, "run", return_value=r) as run:
            self.assertEqual(kpi.build_replay("/root"), "/x/shanjie-replay")
        self.assertIn("--locked", run.call_args.args[0])
        self.assertIn("--message-format=json", run.call_args.args[0])

    def test_no_artifact_fails(self):
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            kpi.build_replay("/root")


class Counts(Tmp):
    def test_fixed_broken_from_rowstats(self):
        def rs(name, oks):
            p = os.path.join(self.td, name)
            open(p, "w").write("".join(f"{i}\t{int(o)}\t0\t3\n" for i, o in enumerate(oks, 1)))
            return p
        # rows: kept right, fixed, broken, broken, kept wrong
        self.assertEqual(kpi.fixed_broken(rs("b", [1, 0, 1, 1, 0]), rs("c", [1, 1, 0, 0, 0]), 5), (1, 2))

    def test_write_is_atomic(self):
        self.put(self.td, "v0.4.0.json", {"keep": 1})
        with mock.patch.object(kpi.json, "dump", side_effect=OSError("disk")), self.assertRaises(OSError):
            kpi.write_kpi(self.td, "v0.4.0", {"x": 2})
        self.assertEqual(json.load(open(os.path.join(self.td, "v0.4.0.json"))), {"keep": 1})


FAKE = """#!{py}
import os, sys
a = sys.argv[1:]
opt = lambda k: a[a.index(k) + 1]
rows = [l for l in open(opt("--rows"), encoding="utf-8").read().split("\\n") if l.count("|") == 2]
n, learn = len(rows), opt("--learn") == "on"
bad = 2 if learn else 1
open(opt("--rowstats"), "w").write("".join(f"{{i}}\\t{{int(i > bad)}}\\t0\\t3\\n" for i in range(1, n + 1)))
sys.stderr.write(os.environ.get("FAKE_MODELS", {models!r}) + "\\n")
if ("--no-colloquial" in a) != (not {co8!r}):  # the replay is told to leave the table out exactly when the version does not ship it
    sys.exit(3)
tag = "replay-chat+ctx+acg+nopred+noen+lm:" + {lm8!r} + "+tl:" + {tl8!r} + ("+learn" if learn else "") + "+correct" + ("+colloq:" + {co8!r} if {co8!r} else "")
if os.environ.get("FAKE_TAG"): tag = os.environ["FAKE_TAG"]
print("## " + os.path.basename(opt("--rows")) + "  " + tag + "  {{'n': %d, 'top1': %d, 'final_ok': %d, 'unfixable': 0, 'autocommits': 0, 'kyss': 0.9123, 'picks_per100': 1.5, 'rewrites': 3, 'top1_sha256': 'aa'}}" % (n, n - bad, n - bad))
"""


class ReplayRun(Tmp):
    NAMES, SPEC = ("bigram.sjlm", "classes.sjc", "tinylm.sjtl"), SPEC

    def setUp(self):
        super().setUp()
        self.root = make_root(self.td, self.NAMES)
        self.models = kpi.check_models(self.root, self.SPEC)
        self.rows = {}
        for name, k in (("user-reported", 4), ("dev302", 6)):
            p = os.path.join(self.td, name + ".txt")
            open(p, "w", encoding="utf-8").write("".join(f"c|句{i}|ㄅㄚ\n" for i in range(k)))
            self.rows[name] = (p, bench.sha_file(p))
        self.fake = os.path.join(self.td, "fake-replay")
        lm8, tl8, co8 = self.models["bigram.sjlm"][:8], self.models["tinylm.sjtl"][:8], self.models.get("colloquial.sjlm", "")[:8]
        open(self.fake, "w").write(FAKE.format(py=sys.executable, models=kpi.models_line(self.models), lm8=lm8, tl8=tl8, co8=co8))
        os.chmod(self.fake, 0o755)

    def replay(self, **env):
        a = types.SimpleNamespace(as_="v0.4.3")
        with mock.patch.object(kpi, "REPLAY_ROWS", self.rows), mock.patch.object(kpi, "git_clean_head", return_value="a" * 40), \
                mock.patch.dict(bench.VERSIONS2, {"v0.4.3": self.SPEC}), mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            kpi.cmd_replay(a, root=self.root, kpi_dir=self.kp, build=lambda root: self.fake)
        return json.load(open(os.path.join(self.kp, "v0.4.3.json"), encoding="utf-8"))

    def test_replay_field_from_the_binary_and_evalstats(self):
        r = self.replay()["replay"]
        self.assertEqual(r["sha"], "a" * 40)
        self.assertEqual(r["models"], self.models)
        ur, dv = r["user-reported"], r["dev302"]
        self.assertEqual((ur["n"], ur["off"]["top1"], ur["off"]["kyss"], ur["rows_sha256"]), (4, 3, 0.9123, self.rows["user-reported"][1]))
        self.assertEqual((ur["learn_fixed"], ur["learn_broken"]), (0, 1))  # learning on loses row 2 on top of row 1
        self.assertEqual((dv["n"], dv["off"]["rewrites"], dv["rows_sha256"]), (6, 3, self.rows["dev302"][1]))
        self.assertNotIn("on", dv)  # learning on is measured on the user-reported rows only

    def test_existing_kpi_fields_are_kept(self):
        self.put(self.kp, "v0.4.3.json", {"holdout_talk_chat": {"n": 150, "top1": 1}, "wikitune_clean": {"top1": 7}})
        d = self.replay()
        self.assertEqual(d["holdout_talk_chat"], {"n": 150, "top1": 1})
        self.assertEqual(d["wikitune_clean"], {"top1": 7})
        self.assertIn("replay", d)

    def test_models_line_of_the_binary_must_match(self):
        with self.assertRaises(SystemExit):
            self.replay(FAKE_MODELS="models: bigram.sjlm:00000000 classes.sjc:00000000 tinylm.sjtl:00000000")
        self.assertFalse(os.path.exists(os.path.join(self.kp, "v0.4.3.json")))

    def test_setting_tag_must_match(self):
        with self.assertRaises(SystemExit):
            self.replay(FAKE_TAG="replay-formal+whatever")


class ReplayRunWithTheColloquialTable(ReplayRun):
    """The same checks for a version whose data/lm holds colloquial.sjlm: the models line and the tag carry it, and --no-colloquial is not passed."""
    NAMES, SPEC = ("bigram.sjlm", "classes.sjc", "tinylm.sjtl", "colloquial.sjlm"), dict(SPEC, colloquial=True)

    def test_the_table_is_in_the_record(self):
        self.assertIn("colloquial.sjlm", self.replay()["replay"]["models"])
        self.assertIn(" colloquial.sjlm:", kpi.models_line(self.models))

    def test_the_tag_without_the_table_is_refused(self):
        lm8, tl8 = self.models["bigram.sjlm"][:8], self.models["tinylm.sjtl"][:8]
        with self.assertRaises(SystemExit):
            self.replay(FAKE_TAG=f"replay-chat+ctx+acg+nopred+noen+lm:{lm8}+tl:{tl8}+correct")


# ---------- run ----------
CLI = """#!{py}
import os, sys
a = sys.argv[1:]
opt = lambda k: a[a.index(k) + 1]
n = len([l for l in open(opt("--rows"), encoding="utf-8").read().split("\\n") if l.count("|") == 2]) + int(os.environ.get("FAKE_ROWS_OFF", "0"))
top1 = n - 1
open(opt("--rowstats"), "w").write("".join(f"{{i}}\\t{{int(i > 1)}}\\t0\\t3\\n" for i in range(1, n + 1)))
print("## " + os.path.basename(opt("--rows")) + "  " + os.environ.get("FAKE_TAG", "lm-formal+ctx+acg") + "  {{'n': %d, 'top1': %d, 'oracle@64': %d, 'top1_sha256': '" % (n, top1 + int(os.environ.get("FAKE_TOP1_OFF", "0")), n) + "b" * 64 + "'}}")
"""


class RunWikitune(Tmp):
    def setUp(self):
        super().setUp()
        self.rows = os.path.join(self.td, "wikitune-clean.txt")
        open(self.rows, "w", encoding="utf-8").write("".join(f"c|句{i}|ㄅㄚ\n" for i in range(5)))
        self.cli = os.path.join(self.td, "fake-cli")
        open(self.cli, "w").write(CLI.format(py=sys.executable))
        os.chmod(self.cli, 0o755)

    class Ver:
        def __init__(self, ref):
            self.ref, self.sha = ref, "c" * 40

        def build(self):
            self.cli, self.lm, self.tl, self.dir = self.fake_cli, "lm", None, self.vdir

    def run_cmd(self, rows=None, **env):
        os.makedirs(os.path.join(self.td, "vdir"), exist_ok=True)  # the CLI runs in the version's directory, not where the rows file is
        ver = type("V", (self.Ver,), {"fake_cli": self.cli, "vdir": os.path.join(self.td, "vdir")})

        def setup(v, spec, td):
            v.spec, v.tl8, v.kn8, v.tri8 = spec, "", "", ""
        a = types.SimpleNamespace(refs=["abc"], as_="v0.4.0", wikitune_clean=rows or self.rows)
        with mock.patch.object(bench, "Ver", ver), mock.patch.object(bench, "setup_models", setup), mock.patch.dict(os.environ, env), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            kpi.cmd_run(a, kpi_dir=self.kp)
        return json.load(open(os.path.join(self.kp, "v0.4.0.json"), encoding="utf-8"))

    def test_record(self):
        d = self.run_cmd()
        self.assertEqual(d["sha"], "c" * 40)
        self.assertEqual(d["wikitune_clean"], {"n": 5, "top1": 4, "top1_sha256": "b" * 64, "tag": "lm-formal+ctx+acg", "rows_sha256": bench.sha_file(self.rows)})

    def test_checks_of_run_cell2(self):
        for env in ({"FAKE_TAG": "lm-formal+ctx"}, {"FAKE_ROWS_OFF": "1"}, {"FAKE_TOP1_OFF": "1"}):  # setting tag, row count, rowstats vs summary
            with self.subTest(env), self.assertRaises(SystemExit):
                self.run_cmd(**env)
        self.assertFalse(os.path.exists(os.path.join(self.kp, "v0.4.0.json")))

    def test_relative_rows_path_is_made_absolute(self):
        old = os.getcwd()
        os.chdir(self.td)
        try:
            d = self.run_cmd(rows="wikitune-clean.txt")
        finally:
            os.chdir(old)
        self.assertEqual(d["wikitune_clean"]["n"], 5)

    def test_rows_under_eval_holdout_are_refused(self):
        link = os.path.join(self.td, "innocent.txt")
        os.symlink(os.path.join(kpi.ROOT, "eval/holdout/news.txt"), link)  # only the resolved path is looked at, nothing is read
        for path in (os.path.join(kpi.ROOT, "eval/holdout/talk.txt"), link):
            with self.subTest(path), mock.patch.object(kpi, "die", side_effect=SystemExit) as die, self.assertRaises(SystemExit):
                self.run_cmd(rows=path)
            self.assertIn("eval/holdout", die.call_args.args[0])

    def test_unknown_version_name_is_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bench.version_labels(["abc"], "v9.9.9")

    def test_write_keeps_fields_this_run_does_not_set(self):
        self.put(self.kp, "v0.4.0.json", {"holdout_talk_chat": {"n": 150}, "wikitune_clean": {"top1": 1}})
        d = self.run_cmd()
        self.assertEqual(d["holdout_talk_chat"], {"n": 150})
        self.assertEqual(d["wikitune_clean"]["top1"], 4)  # the field this run sets is replaced


if __name__ == "__main__":
    unittest.main()
