"""Tests for r1e.py (tags, rows identity, 2b bootstrap, clause 3 pooling, and judge() end to end on synthetic files).
Run: python3 -m unittest test_r1e (in this directory)."""
import os, random, shutil, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r1e as E  # noqa: E402

R, RD, Q, p4c, evalstats = E.R, E.RD, E.Q, E.p4c, E.evalstats
SHA = "e7c179d8"
TL, KN = f"+tl:{SHA}", "+kn:cbefbced:θ1:β1"


def base_tag(setting):
    """A real-shaped shipping baseline tag of the setting."""
    profile, level, on = RD.SETTINGS[setting]
    t = f"lm-{profile}+ctx" + (KN if profile == "chat" else "") + (TL if on else "")
    t += f"+rl:{level}" if on and level != "standard" else ""
    t += "+acg"
    if profile == "chat":
        t += f"+colloq:{R.COLLOQ8}+rc" if on else ""
    else:
        t += f"+tri:{R.TRI8}"
    return t


def rd_tag(setting):
    t = base_tag(setting)
    return (t.removesuffix("+rc") if RD.SETTINGS[setting][0] == "chat" and RD.SETTINGS[setting][2] else t) + "+rd"


class Tags(unittest.TestCase):
    def bad(self, setting, base, tag):
        with self.assertRaises(SystemExit):
            E.check_tags(setting, base, tag, SHA)

    def test_good_tags_all_settings(self):
        for s in RD.SETTINGS:
            E.check_tags(s, base_tag(s), rd_tag(s), SHA)

    def test_mislabels(self):
        s, b, t = "chat-std", base_tag("chat-std"), rd_tag("chat-std")
        self.bad(s, b, t.replace(SHA, "00000000"))                          # R1e run with another small model
        self.bad(s, b.replace(SHA, "00000000"), t.replace(SHA, "00000000"))  # both arms with another small model
        self.bad(s, b + "+rd", t)                                           # baseline already an rd run
        self.bad(s, b, t.removesuffix("+rd"))                               # no +rd
        self.bad(s, b, t + "+rc")                                           # reading channel still on
        self.bad(s, b, t.replace("+rd", "+rc+rd"))
        self.bad(s, b.removesuffix("+rc"), t)                               # baseline without +rc
        self.bad(s, b.replace("+ctx", ""), t.replace("+ctx", ""))           # no +ctx
        self.bad(s, b.replace("+acg", ""), t.replace("+acg", ""))           # no +acg
        self.bad(s, b, t.replace(R.COLLOQ8, "00000000"))                    # other +colloq:
        self.bad(s, b.replace(KN, ""), t.replace(KN, ""))                   # no +kn:
        self.bad(s, b, t.replace("cbefbced", "11111111"))                   # different +kn: between the arms
        for x in ("-nodemote", "-noclasses", "-nonames", "+xo:3"):
            self.bad(s, b.replace("+acg", "+acg" + x), t.replace("+acg", "+acg" + x))
            self.bad(s, b, t.replace("+acg", "+acg" + x))
        self.bad(s, b.replace("+acg", "+rl:conservative+acg"), t.replace("+acg", "+rl:conservative+acg"))  # standard with +rl:
        self.bad("chat-cons", base_tag("chat-cons"), rd_tag("chat-cons").replace("+rl:conservative", ""))
        self.bad("chat-off", base_tag("chat-off"), rd_tag("chat-off").replace("+acg", TL + "+acg"))        # off run with a small model
        self.bad("chat-off", base_tag("chat-off") + f"+colloq:{R.COLLOQ8}", rd_tag("chat-off") + "")
        f, fb, ft = "formal-std", base_tag("formal-std"), rd_tag("formal-std")
        self.bad(f, fb, ft.replace(R.TRI8, "00000000"))
        self.bad(f, fb.replace(f"+tri:{R.TRI8}", ""), ft.replace(f"+tri:{R.TRI8}", ""))
        self.bad(f, fb, ft.replace(f"+tri:{R.TRI8}", f"+colloq:{R.COLLOQ8}+tri:{R.TRI8}"))


def write_run(d, k, setting, name, ok, tag, glen=None):
    p = os.path.join(d, f"{k}.{setting}.{name}")
    glen = glen or [5] * len(ok)
    with open(p + ".r", "w") as f:
        f.writelines(f"{i}\t{o}\t{0 if o else 1}\t{g}\n" for i, (o, g) in enumerate(zip(ok, glen), 1))
    with open(p + ".cli.txt", "w") as f:
        f.write(f"## {name}  {tag}  {{'n': {len(ok)}, 'top1': {sum(ok)}, 'oracle@64': {len(ok)}, 'top1_sha256': 'x'}}\n")


def pattern(n, seed, fixes, breaks):
    """(base, new): base ok except `fixes` rows that new fixes, plus `breaks` rows base had right that new breaks."""
    rng = random.Random(seed)
    base = [1] * n
    new = [1] * n
    for i in rng.sample(range(n), fixes + breaks):
        if fixes:
            base[i], fixes = 0, fixes - 1
        else:
            new[i] = 0
    return base, new


class Judge(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)

    def fill(self, fixes=3, breaks=1):
        self.ok = {}
        for si, (s, roles) in enumerate(RD.ROLES.items()):
            for ni, n in enumerate(roles):
                b, e = pattern(R.ROW_COUNTS[n], 100 * si + ni, fixes, breaks)
                self.ok[s, n] = (b, e)
                write_run(self.d, "base", s, n, b, base_tag(s))
                write_run(self.d, "e", s, n, e, rd_tag(s))

    def test_end_to_end_matches_r1d_logic(self):
        self.fill()
        out = E.judge(self.d, SHA)
        self.assertEqual(len(out["runs"]), sum(len(r) for r in RD.ROLES.values()))
        for (s, n), (b, e) in self.ok.items():
            role, r = RD.ROLES[s][n], out["runs"][f"{s}/{n}"]
            st = p4c.stats([bool(x) for x in b], [bool(x) for x in e])
            self.assertEqual((r["fixes"], r["breaks"]), (st["fixes"], st["breaks"]))
            self.assertEqual(r["base_ok"], "".join(map(str, b)))
            if role in ("1", "2a"):
                self.assertEqual(r["verdict"], bool(Q.verdict(role, st)))
            elif role == "2b":
                (lo, _), _ = evalstats.paired_bootstrap([(x, 0, 5) for x in b], [(x, 0, 5) for x in e], level=0.90)
                self.assertEqual(r["verdict"], 100 * lo / len(b) >= -1.0)
            else:
                self.assertNotIn("verdict", r)
        for pair in RD.CLAUSE3_PAIRS:
            b = [x for s in pair for x in self.ok[s, "user-reported"][0]]
            e = [x for s in pair for x in self.ok[s, "user-reported"][1]]
            self.assertEqual(out["clauses"]["3"][" + ".join(pair)], bool(Q.verdict("3", p4c.stats([bool(x) for x in b], [bool(x) for x in e]))))
        self.assertEqual(set(out["level_order"]), {"chat/discordtune", "chat/typing76", "formal/typing76", "formal/wikitune-clean"})

    def test_2b_bootstrap_bound(self):
        n = R.ROW_COUNTS["cvtune-clean"]
        for fixes, breaks, want in ((0, 0, True), (0, 100, False)):       # 100 breaks of 3286 rows = -3.0 pp, bound below -1.0
            self.fill(1, 0)
            b, e = pattern(n, 7, fixes, breaks)
            write_run(self.d, "base", "formal-std", "cvtune-clean", b, base_tag("formal-std"))
            write_run(self.d, "e", "formal-std", "cvtune-clean", e, rd_tag("formal-std"))
            r = E.judge_one(self.d, "formal-std", "cvtune-clean", SHA)
            self.assertEqual(r["verdict"], want)

    def test_clause3_pools_the_two_pairs(self):
        self.fill(0, 0)
        n = R.ROW_COUNTS["user-reported"]
        # each setting alone has net 0 (1 fix, 1 break), but chat-off + formal-off pool to 4 fixes vs 0 breaks, chat-std + formal-std to 0 vs 4
        for s, (f, br) in (("chat-std", (0, 2)), ("formal-std", (0, 2)), ("chat-off", (2, 0)), ("formal-off", (2, 0))):
            b, e = pattern(n, hash(s) % 1000, f, br)
            write_run(self.d, "base", s, "user-reported", b, base_tag(s))
            write_run(self.d, "e", s, "user-reported", e, rd_tag(s))
        out = E.judge(self.d, SHA)
        self.assertEqual(out["clause3_stats"]["chat-std + formal-std"]["breaks"], 4)
        self.assertEqual(out["clause3_stats"]["chat-off + formal-off"]["fixes"], 4)
        self.assertFalse(out["clauses"]["3"]["chat-std + formal-std"])
        self.assertTrue(out["clauses"]["3"]["chat-off + formal-off"])
        self.assertFalse(out["all_pass"])

    def test_rows_identity_stops(self):
        self.fill()
        n = R.ROW_COUNTS["typing76"]
        b, e = pattern(n, 1, 1, 0)
        write_run(self.d, "base", "formal-std", "typing76", b, base_tag("formal-std"))
        write_run(self.d, "e", "formal-std", "typing76", e, rd_tag("formal-std"), glen=[5] * (n - 1) + [6])
        with self.assertRaises(SystemExit):
            E.judge_one(self.d, "formal-std", "typing76", SHA)


if __name__ == "__main__":
    unittest.main()
