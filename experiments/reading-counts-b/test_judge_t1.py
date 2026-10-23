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
SHA, RT = T.SHA, "0123abcd"
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


class Judge(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        # r1e's 90% bootstrap (test_r1e.py covers it) takes about a minute over the 30 runs; the verdict is not what these tests look at
        p = mock.patch.object(E.evalstats, "paired_bootstrap", return_value=((0, 0), None))
        p.start()
        self.addCleanup(p.stop)

    def fill(self, fixes=3, breaks=0, over=None):
        """Every run with `fixes`/`breaks`; `over` {(setting, set): (fixes, breaks)} replaces single runs."""
        self.ok = {}
        for si, (s, roles) in enumerate(RD.ROLES.items()):
            for ni, n in enumerate(roles):
                f, br = (over or {}).get((s, n), (fixes, breaks))
                b, e = T.pattern(R.ROW_COUNTS[n], 100 * si + ni, f, br)
                self.ok[s, n] = (b, e)
                T.write_run(self.d, "base", s, n, b, T.rd_tag(s))
                T.write_run(self.d, "t", s, n, e, rt_tag(s))

    def test_end_to_end(self):
        self.fill()
        out = J.judge(self.d, SHA, RT)
        self.assertEqual(len(out["runs"]), 30)
        for (s, n), (b, e) in self.ok.items():
            r = out["runs"][f"{s}/{n}"]
            self.assertEqual((r["fixes"], r["breaks"], r["changed"]), (3, 0, 3))
            self.assertEqual((r["base_ok"], r["cell_ok"]), ("".join(map(str, b)), "".join(map(str, e))))
        self.assertTrue(out["all_pass"] and out["guard_pass"])
        self.assertEqual(set(out["level_order"]), {"chat/discordtune", "chat/typing76", "formal/typing76", "formal/wikitune-clean"})
        self.assertEqual(sorted(out["guard"]), sorted(["user-reported: chat-std + formal-std", "user-reported: chat-off + formal-off"]
                                                      + [f"typing76: {s}" for s in RD.ROLES]))

    def test_a_candidate_tag_without_rt_stops(self):
        self.fill()
        b, e = self.ok["chat-std", "typing76"]
        T.write_run(self.d, "t", "chat-std", "typing76", e, T.rd_tag("chat-std"))
        with self.assertRaises(SystemExit):
            J.judge(self.d, SHA, RT)

    def test_guard_typing76_per_run(self):
        self.fill(over={("chat-cons", "typing76"): (0, 2), ("formal-aggr", "typing76"): (0, 3)})
        g = J.judge(self.d, SHA, RT)["guard"]
        self.assertTrue(g["typing76: chat-cons"]["pass"])
        self.assertEqual((g["typing76: formal-aggr"]["breaks"], g["typing76: formal-aggr"]["pass"]), (3, False))
        self.assertFalse(J.judge(self.d, SHA, RT)["guard_pass"])

    def test_guard_user_reported_is_pooled_over_the_two_profiles(self):
        self.fill(over={("chat-std", "user-reported"): (0, 1), ("formal-std", "user-reported"): (0, 1)})
        g = J.judge(self.d, SHA, RT)["guard"]
        self.assertEqual((g["user-reported: chat-std + formal-std"]["breaks"], g["user-reported: chat-std + formal-std"]["pass"]), (2, True))
        self.fill(over={("chat-std", "user-reported"): (0, 2), ("formal-std", "user-reported"): (0, 1)})
        g = J.judge(self.d, SHA, RT)["guard"]
        self.assertEqual((g["user-reported: chat-std + formal-std"]["breaks"], g["user-reported: chat-std + formal-std"]["pass"]), (3, False))
        self.assertTrue(g["user-reported: chat-off + formal-off"]["pass"])

    def test_stop_rule(self):
        self.fill(over={("chat-std", "chatclause"): (2, 2), ("formal-std", "wikitune-clean"): (4, 0)})
        out = J.judge(self.d, SHA, RT)
        self.assertEqual(out["changed_rows"], {"chat-std/chatclause": 4, "formal-std/wikitune-clean": 4})
        self.assertTrue(out["shape_not_the_bottleneck"])
        self.fill(over={("chat-std", "chatclause"): (3, 2), ("formal-std", "wikitune-clean"): (0, 0)})
        out = J.judge(self.d, SHA, RT)
        self.assertEqual(out["changed_rows"], {"chat-std/chatclause": 5, "formal-std/wikitune-clean": 0})
        self.assertFalse(out["shape_not_the_bottleneck"])

    @unittest.skipUnless(os.path.exists(os.path.join(ROOT, "data/lm/tinylm.sjtl")), "data/lm/tinylm.sjtl")
    def test_main_prints_and_writes_json(self):
        self.fill(fixes=6)
        table = os.path.join(self.d, "t1.tsv")
        with open(table, "w") as f:
            f.write("x")
        sha8 = R.sha_file(table)[:8]
        for s in RD.SETTINGS:   # re-tag the candidates with the real table hash
            for n in RD.ROLES[s]:
                b, e = self.ok[s, n]
                T.write_run(self.d, "t", s, n, e, T.rd_tag(s) + f"+rt:{sha8}")
        out = os.path.join(self.d, "out.json")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(J.main(["judge", self.d, "--table", table, "--sjtl", os.path.join(ROOT, "data/lm/tinylm.sjtl"), "--out", out]), 0)
        self.assertIn("GUARD PASS", buf.getvalue())
        self.assertNotIn(J.SHAPE_TEXT, buf.getvalue())
        self.assertTrue(json.load(open(out))["guard_pass"])


if __name__ == "__main__":
    unittest.main()
