"""Tests for judge_t1.py on synthetic run files (the file layout and helpers of test_r1e.py). Run: python3 -m unittest test_judge_t1 (in this directory)."""
import contextlib, io, json, os, shutil, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
R1 = os.path.join(os.path.dirname(HERE), "r1-reading-decode")
sys.path.insert(0, HERE)
sys.path.insert(0, R1)
import judge_t1 as J  # noqa: E402
import test_r1e as T  # noqa: E402  (write_run, pattern, base_tag, rd_tag)

E, R, RD = J.E, J.R, J.RD
SHA, RT, T08 = T.SHA, "0123abcd", "99887766"
ROOT = os.path.dirname(os.path.dirname(HERE))


def rt_tag(setting):
    return T.rd_tag(setting) + f"+rt:{RT}"


class Tags(unittest.TestCase):
    def check(self, setting, base, tag):
        J.make_tag_check(RT)(setting, base, tag, SHA)

    def test_good_tags_all_settings(self):
        for s in RD.SETTINGS:
            self.check(s, T.rd_tag(s), rt_tag(s))

    def test_bad_tags(self):
        for s in ("chat-std", "chat-off", "formal-aggr"):
            b, t = T.rd_tag(s), rt_tag(s)
            for base, tag in ((b, b),                                         # no +rt
                              (b, t.replace(RT, "ffffffff")),                 # another table
                              (b, b + "+rt:" + RT + "+rt:" + RT),
                              (b, t.replace("+rd", "")),                      # candidate without +rd
                              (T.base_tag(s), T.base_tag(s) + f"+rt:{RT}"),   # base is the shipping run, not R1e
                              (b.replace(SHA, "00000000"), t.replace(SHA, "00000000")) if SHA in b else (b, b),   # another small model (no model in -off)
                              (b, t.replace("+acg", "")),
                              (b.replace("+acg", ""), t.replace("+acg", ""))):
                with self.assertRaises(SystemExit):
                    self.check(s, base, tag)
        with self.assertRaises(SystemExit):   # chat with the reading channel still on in the R1e run
            self.check("chat-std", T.rd_tag("chat-std").replace("+rd", "+rc+rd"), rt_tag("chat-std").replace("+rd", "+rc+rd"))


def write_picks(d, k, setting, name, picks):
    """The run's `.dump` (two candidates per row) and, for a rerank-on setting, `.scores`. picks[i] in (0, 1) is the candidate the row picks
    (on: `c` = picks[i] over the same candidate order; off: the picked one is rank 1)."""
    p = os.path.join(d, f"{k}.{setting}.{name}")
    on = RD.SETTINGS[setting][2]
    with open(p + ".dump", "w") as f:
        for i, c in enumerate(picks, 1):
            order = (0, 1) if on or c == 0 else (1, 0)
            f.writelines(f"{i}\t{j + 1}\ts{i}_{x}\t-1.0\n" for j, x in enumerate(order))
    if on:
        with open(p + ".scores", "w") as f:
            f.writelines(json.dumps({"i": i, "c": c}) + "\n" for i, c in enumerate(picks, 1))


class Judge(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        # r1e's 90% bootstrap (test_r1e.py covers it) takes about a minute over the 30 runs; the verdict is not what these tests look at
        p = mock.patch.object(E.evalstats, "paired_bootstrap", return_value=((0, 0), None))
        p.start()
        self.addCleanup(p.stop)

    def fill(self, fixes=3, breaks=0, over=None, anchor=True):
        """Every run with `fixes`/`breaks`; `over` {(setting, set): (fixes, breaks)} replaces single runs. The candidate's pick differs from the base's
        exactly in the rows whose ok differs. The anchor runs (base + T0) repeat the base picks."""
        self.ok, self.picks = {}, {}
        for si, (s, roles) in enumerate(RD.ROLES.items()):
            for ni, n in enumerate(roles):
                f, br = (over or {}).get((s, n), (fixes, breaks))
                b, e = T.pattern(R.ROW_COUNTS[n], 100 * si + ni, f, br)
                self.ok[s, n] = (b, e)
                self.picks[s, n] = ([0] * len(b), [int(x != y) for x, y in zip(b, e)])
                T.write_run(self.d, "base", s, n, b, T.rd_tag(s))
                T.write_run(self.d, "t", s, n, e, rt_tag(s))
                write_picks(self.d, "base", s, n, self.picks[s, n][0])
                write_picks(self.d, "t", s, n, self.picks[s, n][1])
                if anchor and s in J.ANCHOR_SETTINGS:
                    T.write_run(self.d, "a", s, n, b, T.rd_tag(s) + f"+rt:{T08}")
                    write_picks(self.d, "a", s, n, self.picks[s, n][0])

    def judge(self):
        return J.judge(self.d, SHA, RT, T08)

    def test_end_to_end(self):
        self.fill()
        out = self.judge()
        self.assertEqual(len(out["runs"]), 30)
        for (s, n), (b, e) in self.ok.items():
            r = out["runs"][f"{s}/{n}"]
            self.assertEqual((r["fixes"], r["breaks"], r["changed"]), (3, 0, 3))
            self.assertEqual((r["base_ok"], r["cell_ok"]), ("".join(map(str, b)), "".join(map(str, e))))
        self.assertTrue(out["all_pass"] and out["guard_pass"] and out["anchor_pass"])
        self.assertEqual(out["anchor_runs"], 11)   # chat-std 6 sets + formal-std 5 sets
        self.assertEqual(sorted(out["guard"]), ["typing76: chat-std + formal-std", "user-reported: chat-std + formal-std"])

    def test_changed_rows_are_counted_from_the_picks_not_ok_or_edit_distance(self):
        self.fill(fixes=0)
        # a wrong -> wrong swap with the same (ok, errors): rowstats identical, one pick differs (rerank on and off)
        for s in ("chat-std", "formal-off"):
            b, e = self.picks[s, "chatclause" if s == "chat-std" else "wikitune-clean"]
            e = list(e)
            e[7] = 1
            write_picks(self.d, "t", s, "chatclause" if s == "chat-std" else "wikitune-clean", e)
        out = self.judge()
        self.assertEqual((out["runs"]["chat-std/chatclause"]["changed"], out["runs"]["chat-std/chatclause"]["fixes"]), (1, 0))
        self.assertEqual(out["runs"]["formal-off/wikitune-clean"]["changed"], 1)
        self.assertEqual(out["runs"]["chat-std/discordtune"]["changed"], 0)
        self.assertEqual(out["changed_rows"]["chat-std/chatclause"], 1)

    def test_r1e_messages_name_the_candidate(self):
        self.fill()
        b, e = self.ok["chat-std", "typing76"]
        T.write_run(self.d, "t", "chat-std", "typing76", e, rt_tag("chat-std"), glen=[6] * len(e))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), self.assertRaises(SystemExit):
            self.judge()
        self.assertIn("candidate rowstats differ", buf.getvalue())
        self.assertNotIn("R1e rowstats", buf.getvalue())

    def test_a_candidate_tag_without_rt_stops(self):
        self.fill()
        b, e = self.ok["chat-std", "typing76"]
        T.write_run(self.d, "t", "chat-std", "typing76", e, T.rd_tag("chat-std"))
        with self.assertRaises(SystemExit):
            self.judge()

    def test_guard_typing76_is_pooled_over_the_two_standard_profiles_and_other_settings_are_not_guarded(self):
        self.fill(over={("chat-std", "typing76"): (0, 1), ("formal-std", "typing76"): (0, 1)})
        g = self.judge()["guard"]["typing76: chat-std + formal-std"]
        self.assertEqual((g["breaks"], g["pass"]), (2, True))
        self.fill(over={("chat-std", "typing76"): (0, 2), ("formal-std", "typing76"): (0, 1)})
        out = self.judge()
        self.assertFalse(out["guard"]["typing76: chat-std + formal-std"]["pass"] or out["guard_pass"])
        self.fill(over={("chat-cons", "typing76"): (0, 5), ("chat-off", "user-reported"): (0, 5)})
        self.assertTrue(self.judge()["guard_pass"])

    def test_guard_fails_when_a_required_run_is_missing(self):
        self.fill()
        runs = {tuple(k.split("/")): v for k, v in self.judge()["runs"].items()}
        del runs["formal-std", "user-reported"]
        g = J.guard(runs)
        self.assertFalse(g["user-reported: chat-std + formal-std"]["pass"])
        self.assertTrue(g["typing76: chat-std + formal-std"]["pass"])
        self.fill()
        os.remove(os.path.join(self.d, "t.formal-std.user-reported.r"))   # the judge itself cannot read a missing file either
        with self.assertRaises((SystemExit, OSError)):
            self.judge()

    def test_anchor_failures(self):
        self.fill()
        self.assertEqual(self.judge()["anchor_failures"], [])
        b, e = self.ok["chat-std", "typing76"]
        write_picks(self.d, "a", "chat-std", "typing76", [1] + [0] * (len(b) - 1))                  # one pick differs (rerank on)
        T.write_run(self.d, "a", "formal-std", "typing76", self.ok["formal-std", "typing76"][0], T.rd_tag("formal-std") + "+rt:ffffffff")   # wrong T0 hash
        os.remove(os.path.join(self.d, "a.chat-std.discordtune.dump"))                                # missing file
        with open(os.path.join(self.d, "a.formal-std.dev302.cli.txt"), "w") as f:                    # other top1_sha256
            f.write(f"## dev302  {T.rd_tag('formal-std')}+rt:{T08}  {{'n': 302, 'top1': {sum(self.ok['formal-std', 'dev302'][0])}, 'oracle@64': 302, 'top1_sha256': 'y'}}\n")
        out = self.judge()
        self.assertFalse(out["anchor_pass"])
        self.assertEqual(sorted(out["anchor_failures"]), ["chat-std/discordtune: FileNotFoundError", "chat-std/typing76: picks",
                                                          "formal-std/dev302: top1_sha256", "formal-std/typing76: tag"])

    def test_exit_code_one_on_a_guard_or_anchor_failure(self):
        self.fill(over={("chat-std", "user-reported"): (0, 3)})
        table = os.path.join(self.d, "t1.tsv")
        t0 = os.path.join(self.d, "t0.tsv")
        for p, x in ((table, "1"), (t0, "0")):
            with open(p, "w") as f:
                f.write(x)
        for s in RD.SETTINGS:
            for n in RD.ROLES[s]:
                T.write_run(self.d, "t", s, n, self.ok[s, n][1], T.rd_tag(s) + f"+rt:{R.sha_file(table)[:8]}")
                if s in J.ANCHOR_SETTINGS:
                    T.write_run(self.d, "a", s, n, self.ok[s, n][0], T.rd_tag(s) + f"+rt:{R.sha_file(t0)[:8]}")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = J.main(["judge", self.d, "--table", table, "--t0", t0, "--sjtl", os.path.join(ROOT, "data/lm/tinylm.sjtl")])
        self.assertEqual(rc, 1)
        self.assertLess(buf.getvalue().index("GUARD FAIL"), buf.getvalue().index("CLAUSES"))

    def test_guard_user_reported_is_pooled_over_the_two_profiles(self):
        self.fill(over={("chat-std", "user-reported"): (0, 1), ("formal-std", "user-reported"): (0, 1)})
        g = self.judge()["guard"]
        self.assertEqual((g["user-reported: chat-std + formal-std"]["breaks"], g["user-reported: chat-std + formal-std"]["pass"]), (2, True))
        self.fill(over={("chat-std", "user-reported"): (0, 2), ("formal-std", "user-reported"): (0, 1)})
        g = self.judge()["guard"]
        self.assertEqual((g["user-reported: chat-std + formal-std"]["breaks"], g["user-reported: chat-std + formal-std"]["pass"]), (3, False))

    def test_stop_rule(self):
        self.fill(over={("chat-std", "chatclause"): (2, 2), ("formal-std", "wikitune-clean"): (4, 0)})
        out = self.judge()
        self.assertEqual(out["changed_rows"], {"chat-std/chatclause": 4, "formal-std/wikitune-clean": 4})
        self.assertTrue(out["shape_not_the_bottleneck"])
        self.fill(over={("chat-std", "chatclause"): (3, 2), ("formal-std", "wikitune-clean"): (0, 0)})
        out = self.judge()
        self.assertEqual(out["changed_rows"], {"chat-std/chatclause": 5, "formal-std/wikitune-clean": 0})
        self.assertFalse(out["shape_not_the_bottleneck"])

    @unittest.skipUnless(os.path.exists(os.path.join(ROOT, "data/lm/tinylm.sjtl")), "data/lm/tinylm.sjtl")
    def test_main_prints_and_writes_json(self):
        self.fill(fixes=6)
        table, t0 = os.path.join(self.d, "t1.tsv"), os.path.join(self.d, "t0.tsv")
        for p, x in ((table, "x"), (t0, "y")):
            with open(p, "w") as f:
                f.write(x)
        for s in RD.SETTINGS:   # re-tag the candidates and anchors with the real table hashes
            for n in RD.ROLES[s]:
                b, e = self.ok[s, n]
                T.write_run(self.d, "t", s, n, e, T.rd_tag(s) + f"+rt:{R.sha_file(table)[:8]}")
                if s in J.ANCHOR_SETTINGS:
                    T.write_run(self.d, "a", s, n, b, T.rd_tag(s) + f"+rt:{R.sha_file(t0)[:8]}")
        out = os.path.join(self.d, "out.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(J.main(["judge", self.d, "--table", table, "--t0", t0, "--sjtl", os.path.join(ROOT, "data/lm/tinylm.sjtl"), "--out", out]), 0)
        self.assertIn("GUARD PASS", buf.getvalue())
        self.assertIn("ANCHOR PASS", buf.getvalue())
        self.assertNotIn(J.SHAPE_TEXT, buf.getvalue())
        self.assertTrue(json.load(open(out))["guard_pass"])


if __name__ == "__main__":
    unittest.main()
