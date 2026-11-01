"""Tests for r1e.py (tags, rows identity, 2b bootstrap, clause 3 pooling, and judge() end to end on synthetic files).
Run: python3 -m unittest test_r1e (in this directory)."""
import hashlib, json, os, random, shutil, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r1e as E  # noqa: E402

R, RD, Q, p4c, evalstats = E.R, E.RD, E.Q, E.p4c, E.evalstats
SHA = R.model_sha8("tinylm.sjtl")
# synthetic row counts (the real ones come from the run script's counts.json); user-reported at the pooled size the clause-3 test needs
COUNTS = {"chatclause": 40, "discordtune": 300, "cvtune-clean": 3286, "typing76": 76, "user-reported": 177, "dev302": 302, "wikitune-clean": 200}


def base_tag(setting):
    """The current shipping baseline tag of the setting (r1.ship_tag: tools/bench.py VERSIONS2["v0.6.1"] + data/*.sha256)."""
    profile, level, on = RD.SETTINGS[setting]
    return R.ship_tag(profile, level, on)


def rd_tag(setting):
    t = base_tag(setting)
    return (t.removesuffix("+rc") if RD.SETTINGS[setting][0] == "chat" and RD.SETTINGS[setting][2] else t) + "+rd"


class Tags(unittest.TestCase):
    def bad(self, setting, base, tag):
        with self.assertRaises(SystemExit):
            E.check_tags(setting, base, tag)

    def test_good_tags_all_settings(self):
        for s in RD.SETTINGS:
            E.check_tags(s, base_tag(s), rd_tag(s))

    def test_tag_expectation_is_benchs(self):
        """The baseline tag equals what tools/bench.py expects of the shipping v0.6.1 cell (expected_tag: spec_tag + colloq_tag) for one chat and
        one formal setting, and the R1f tag is it with +rd (chat: +rc dropped)."""
        import types
        bench = R.bench_mod()
        ver = types.SimpleNamespace(spec=bench.VERSIONS2["v0.6.1"], tl8=R.model_sha8("tinylm.sjtl"), kn8=R.model_sha8("kn.sjkn"), tri8=R.model_sha8("trigram.sjt3"),
                                    trichat8=R.model_sha8("trigram-chat.sjt3"), co8=R.model_sha8("colloquial.sjlm"))
        for prof, setting in (("chat", "chat-std"), ("formal", "formal-std")):
            self.assertEqual(base_tag(setting), bench.expected_tag(ver, prof, False))
            self.assertEqual(rd_tag(setting), bench.expected_tag(ver, prof, False).removesuffix("+rc") + "+rd" if prof == "chat"
                             else bench.expected_tag(ver, prof, False) + "+rd")
        self.assertEqual(R.ship_flags(), ["--context", "--packs", "acg,it"])

    def test_mislabels(self):
        s, b, t = "chat-std", base_tag("chat-std"), rd_tag("chat-std")
        kn = f"+kn:{R.model_sha8('kn.sjkn')}:θ1:β1"
        co = R.model_sha8("colloquial.sjlm")
        self.bad(s, b, t.replace(SHA, "00000000"))                          # R1f run with another small model
        self.bad(s, b.replace(SHA, "00000000"), t.replace(SHA, "00000000"))  # both arms with another small model
        self.bad(s, b + "+rd", t)                                           # baseline already an rd run
        self.bad(s, b, t.removesuffix("+rd"))                               # no +rd
        self.bad(s, b, t + "+rc")                                           # reading channel still on
        self.bad(s, b, t.replace("+rd", "+rc+rd"))
        self.bad(s, b.removesuffix("+rc"), t)                               # baseline without +rc
        self.bad(s, b.replace("+ctx", ""), t.replace("+ctx", ""))           # no +ctx
        self.bad(s, b.replace("+acg+it", "+acg"), t.replace("+acg+it", "+acg"))   # the pre-SJ-LM-7 pack set
        self.bad(s, b, t.replace("+acg+it", "+acg"))
        self.bad(s, b, t.replace(co, "00000000"))                           # other +colloq:
        self.bad(s, b.replace(co, "2984eeb5"), t.replace(co, "2984eeb5"))   # both arms with the old colloquial table
        self.bad(s, b.replace(kn, ""), t.replace(kn, ""))                   # no +kn:
        self.bad(s, b, t.replace(kn[:8], "+kn:11111111"))                   # different +kn: between the arms
        for x in ("-nodemote", "-noclasses", "-nonames", "+xo:3"):
            self.bad(s, b.replace("+acg", x + "+acg"), t.replace("+acg", x + "+acg"))
            self.bad(s, b, t.replace("+acg", x + "+acg"))
        self.bad(s, b.replace("+acg", "+rl:conservative+acg"), t.replace("+acg", "+rl:conservative+acg"))  # standard with +rl:
        self.bad("chat-cons", base_tag("chat-cons"), rd_tag("chat-cons").replace("+rl:conservative", ""))
        self.bad("chat-off", base_tag("chat-off"), rd_tag("chat-off").replace("+acg", f"+tl:{SHA}+acg"))   # off run with a small model
        self.bad("chat-off", base_tag("chat-off") + f"+colloq:{co}", rd_tag("chat-off") + "")
        f, fb, ft = "formal-std", base_tag("formal-std"), rd_tag("formal-std")
        tri = R.model_sha8("trigram.sjt3")
        self.bad(f, fb, ft.replace(tri, "00000000"))
        self.bad(f, fb.replace(f"+tri:{tri}", ""), ft.replace(f"+tri:{tri}", ""))
        self.bad(f, fb, ft.replace(f"+tri:{tri}", f"+colloq:{co}+tri:{tri}"))


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
        with open(os.path.join(self.d, "counts.json"), "w") as f:
            json.dump(COUNTS, f)
        for si, (s, roles) in enumerate(RD.ROLES.items()):
            for ni, n in enumerate(roles):
                b, e = pattern(COUNTS[n], 100 * si + ni, fixes, breaks)
                self.ok[s, n] = (b, e)
                write_run(self.d, "base", s, n, b, base_tag(s))
                write_run(self.d, "e", s, n, e, rd_tag(s))

    def test_end_to_end_matches_r1d_logic(self):
        self.fill()
        out = E.judge(self.d)
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
        n = COUNTS["cvtune-clean"]
        for fixes, breaks, want in ((0, 0, True), (0, 100, False)):       # 100 breaks of 3286 rows = -3.0 pp, bound below -1.0
            self.fill(1, 0)
            b, e = pattern(n, 7, fixes, breaks)
            write_run(self.d, "base", "formal-std", "cvtune-clean", b, base_tag("formal-std"))
            write_run(self.d, "e", "formal-std", "cvtune-clean", e, rd_tag("formal-std"))
            r = E.judge_one(self.d, "formal-std", "cvtune-clean", COUNTS)
            self.assertEqual(r["verdict"], want)

    def test_clause3_pools_the_two_pairs(self):
        self.fill(0, 0)
        n = COUNTS["user-reported"]
        # each setting alone has net 0 (1 fix, 1 break), but chat-off + formal-off pool to 4 fixes vs 0 breaks, chat-std + formal-std to 0 vs 4
        for s, (f, br) in (("chat-std", (0, 2)), ("formal-std", (0, 2)), ("chat-off", (2, 0)), ("formal-off", (2, 0))):
            b, e = pattern(n, hash(s) % 1000, f, br)
            write_run(self.d, "base", s, "user-reported", b, base_tag(s))
            write_run(self.d, "e", s, "user-reported", e, rd_tag(s))
        out = E.judge(self.d)
        self.assertEqual(out["clause3_stats"]["chat-std + formal-std"]["breaks"], 4)
        self.assertEqual(out["clause3_stats"]["chat-off + formal-off"]["fixes"], 4)
        self.assertFalse(out["clauses"]["3"]["chat-std + formal-std"])
        self.assertTrue(out["clauses"]["3"]["chat-off + formal-off"])
        self.assertFalse(out["all_pass"])

    def test_counts_are_enforced(self):
        self.fill()
        n = COUNTS["typing76"]
        b, e = pattern(n - 1, 1, 1, 0)             # one row short of counts.json
        write_run(self.d, "base", "formal-std", "typing76", b, base_tag("formal-std"))
        write_run(self.d, "e", "formal-std", "typing76", e, rd_tag("formal-std"))
        with self.assertRaises(SystemExit):
            E.judge(self.d)
        self.fill()
        counts = dict(COUNTS)
        del counts["dev302"]
        with open(os.path.join(self.d, "counts.json"), "w") as f:
            json.dump(counts, f)
        with self.assertRaises(SystemExit):
            E.judge(self.d)                         # a set without a count

    def test_clause1_reports_the_ratio_only_reading_too(self):
        self.fill(0, 0)
        b, e = pattern(COUNTS["discordtune"], 3, 0, 27)   # 27 breaks, 0 fixes
        write_run(self.d, "base", "chat-std", "discordtune", b, base_tag("chat-std"))
        write_run(self.d, "e", "chat-std", "discordtune", e, rd_tag("chat-std"))
        r = E.judge_one(self.d, "chat-std", "discordtune", COUNTS)
        self.assertEqual((r["verdict"], r["verdict_ratio_only"]), (False, False))
        b, e = pattern(COUNTS["discordtune"], 3, 100, 20)  # ratio 0.2 passes both
        write_run(self.d, "base", "chat-std", "discordtune", b, base_tag("chat-std"))
        write_run(self.d, "e", "chat-std", "discordtune", e, rd_tag("chat-std"))
        r = E.judge_one(self.d, "chat-std", "discordtune", COUNTS)
        self.assertEqual((r["verdict"], r["verdict_ratio_only"]), (True, True))
        b, e = pattern(COUNTS["discordtune"], 3, 30, 25)   # 25 breaks <= 26 passes the registered clause; 25 / 30 > 0.6 fails the ratio alone
        write_run(self.d, "base", "chat-std", "discordtune", b, base_tag("chat-std"))
        write_run(self.d, "e", "chat-std", "discordtune", e, rd_tag("chat-std"))
        r = E.judge_one(self.d, "chat-std", "discordtune", COUNTS)
        self.assertEqual((r["verdict"], r["verdict_ratio_only"]), (True, False))

    def test_cli_commands(self):
        """The 60 commands: the baseline is bench's shipping cell, the R1f run adds the decoder term and (chat, rerank on) drops the reading channel."""
        cmds = dict(E.commands("$ROWS", "$OUT", "shanjie-eval", 32))
        self.assertEqual(len(cmds), 60)
        flags = lambda s, arm: E.cli_args(s, arm)
        self.assertEqual(flags("chat-std", "base"), ["--context", "--packs", "acg,it", "--tinylm", "data/lm/tinylm.sjtl", "--colloquial-core", "data/lm/colloquial.sjlm"])
        self.assertEqual(flags("chat-aggr", "r1f")[-4:], ["--colloquial-core", "data/lm/colloquial.sjlm", "--reading-decode", "--no-reading-channel"])
        self.assertIn(["--rerank-level", "aggressive"], [flags("chat-aggr", "base")[i:i + 2] for i in range(8)])
        self.assertEqual(flags("chat-off", "base"), ["--context", "--packs", "acg,it"])
        self.assertEqual(flags("chat-off", "r1f"), ["--context", "--packs", "acg,it", "--reading-decode"])
        self.assertEqual(flags("formal-cons", "r1f")[-3:], ["--rerank-level", "conservative", "--reading-decode"])

    def test_r1g_commands_and_tag(self):
        """R1g: the same 60 commands and labels, the R1f arm gains `--reading-exempt <list>` last; its tag must end `+rd+rx:<sha8 of the committed list>`."""
        f, g = dict(E.commands("$ROWS", "$OUT", "shanjie-eval", 32)), dict(E.commands("$ROWS", "$OUT", "shanjie-eval", 32, "r1g"))
        self.assertEqual((len(g), list(g)), (60, list(f)))
        for stem, line in g.items():
            if "/e." in stem:
                self.assertEqual(line, f[stem].replace(" --threads", f" --reading-exempt {E.EXEMPT} --threads"))
            else:
                self.assertEqual(line, f[stem])
        self.assertEqual(E.cli_args("chat-std", "r1g")[-4:], ["--reading-decode", "--no-reading-channel", "--reading-exempt", E.EXEMPT])
        rx = E.exempt_sha8()
        self.assertEqual(rx, hashlib.sha256(open(os.path.join(E.HERE, "std-exempt.tsv"), "rb").read()).hexdigest()[:8])
        for s in RD.SETTINGS:
            E.check_tags(s, base_tag(s), rd_tag(s) + f"+rx:{rx}", rx=rx)
            with self.assertRaises(SystemExit):
                E.check_tags(s, base_tag(s), rd_tag(s), rx=rx)                    # an R1f tag is not an R1g tag
            with self.assertRaises(SystemExit):
                E.check_tags(s, base_tag(s), rd_tag(s) + f"+rx:{rx}")             # an R1g tag is not an R1f tag
            with self.assertRaises(SystemExit):
                E.check_tags(s, base_tag(s), rd_tag(s) + "+rx:00000000", rx=rx)   # another list

    def test_rows_identity_stops(self):
        self.fill()
        n = COUNTS["typing76"]
        b, e = pattern(n, 1, 1, 0)
        write_run(self.d, "base", "formal-std", "typing76", b, base_tag("formal-std"))
        write_run(self.d, "e", "formal-std", "typing76", e, rd_tag("formal-std"), glen=[5] * (n - 1) + [6])
        with self.assertRaises(SystemExit):
            E.judge_one(self.d, "formal-std", "typing76", COUNTS)


class Confirm(unittest.TestCase):
    """R1g confirmation shot (`--confirm`): 16 commands and the two registered verdicts, on synthetic rowstats."""
    N = {"wikijudge5": 300, "cjudge-1": 500}

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.rx = E.exempt_sha8()

    def fill(self, formal=(3, 1), chat=(1, 0), chat_n=None, formal_n=None):
        """(fixes, breaks) of formal-std/wikijudge5 and chat-std/cjudge-1; every other pair gets (1, 1)."""
        n = {"wikijudge5": formal_n or self.N["wikijudge5"], "cjudge-1": chat_n or self.N["cjudge-1"]}
        with open(os.path.join(self.d, "counts.json"), "w") as f:
            json.dump(n, f)
        for i, (s, name) in enumerate(E.CONFIRM_PAIRS):
            fx, br = formal if (s, name) == ("formal-std", "wikijudge5") else chat if (s, name) == ("chat-std", "cjudge-1") else (1, 1)
            b, e = pattern(n[name], i, fx, br)
            write_run(self.d, "base", s, name, b, base_tag(s))
            write_run(self.d, "e", s, name, e, rd_tag(s) + f"+rx:{self.rx}")

    def judge(self):
        return E.judge_confirm(self.d, rx=self.rx)

    def test_16_commands(self):
        cmds = dict(E.commands("$ROWS", "$OUT", "shanjie-eval", 32, "r1g", confirm=True))
        want = [f"{k}.{s}.{n}" for s, n in [(f"{p}-{l}", n) for p, n in (("formal", "wikijudge5"), ("chat", "cjudge-1")) for l in ("std", "off", "cons", "aggr")]
                for k in ("base", "e")]
        self.assertEqual([os.path.basename(k) for k in cmds], want)
        self.assertEqual(len(cmds), 16)
        full = dict(E.commands("$ROWS", "$OUT", "shanjie-eval", 32, "r1g"))
        for s, name in E.CONFIRM_PAIRS:
            for k in ("base", "e"):
                stem = f"$OUT/{k}.{s}.{name}"
                line = cmds[stem]
                self.assertIn(f'--rows "$ROWS/{name}.txt"', line)
                self.assertIn("--threads 32", line)
                self.assertTrue(line.endswith(f'> "{stem}.cli.txt"'))
                self.assertEqual("--reading-exempt" in line, k == "e")
        self.assertEqual(sum(1 for line in cmds.values() if "--no-reading-channel" in line), 3)   # e of chat-std / chat-cons / chat-aggr
        self.assertEqual(cmds["$OUT/e.formal-std.wikijudge5"], full["$OUT/e.formal-std.wikitune-clean"].replace("wikitune-clean", "wikijudge5"))   # same flags as the R1g run
        self.assertEqual(cmds["$OUT/base.chat-aggr.cjudge-1"], dict(E.commands("$ROWS", "$OUT", "shanjie-eval", 32))["$OUT/base.chat-aggr.discordtune"].replace("discordtune", "cjudge-1"))
        with self.assertRaises(SystemExit):
            E.commands("$ROWS", "$OUT", "shanjie-eval", 32, "r1f", confirm=True)

    def test_default_commands_unchanged(self):
        self.assertEqual(len(E.commands("$ROWS", "$OUT", "shanjie-eval", 32)), 60)
        self.assertEqual(len(E.commands("$ROWS", "$OUT", "shanjie-eval", 32, "r1g")), 60)
        self.assertFalse(any("wikijudge5" in l or "cjudge-1" in l for _, l in E.commands("$ROWS", "$OUT", "shanjie-eval", 32, "r1g")))
        self.assertEqual(E.judge_one.__defaults__, (None, None, None))      # role override defaults to ROLES

    def test_primary_pass_and_report_only(self):
        self.fill()
        out = self.judge()
        self.assertEqual(out["primary"], {"formal-std/wikijudge5": False, "chat-std/cjudge-1": True})   # 3 fixes / 1 break: net > 0 but p = 0.625
        self.assertEqual(len(out["runs"]), 8)
        self.assertNotIn("verdict", out["runs"]["formal-off/wikijudge5"])        # report only
        self.assertNotIn("verdict", out["runs"]["chat-aggr/cjudge-1"])
        self.assertEqual(out["runs"]["chat-off/cjudge-1"]["net"], 0)
        self.assertFalse(out["all_pass"])
        self.fill(formal=(30, 5))
        out = self.judge()
        self.assertTrue(out["all_pass"] and all(out["primary"].values()))

    def test_formal_edges(self):
        cases = ((0, 0, False),        # net 0
                 (10, 10, False),      # net 0
                 (6, 0, True),         # p 0.031, net 6
                 (5, 0, False),        # p 0.0625
                 (22, 9, True),        # p 0.0294
                 (22, 10, False),      # p 0.0501 just above
                 (100, 65, True),      # ratio exactly 0.65, p < 0.05
                 (100, 66, False),     # ratio 0.66
                 (20, 13, False),      # ratio exactly 0.65 but p large
                 (50, 40, False))      # net > 0, ratio 0.8
        for fx, br, want in cases:
            self.fill(formal=(fx, br))
            self.assertEqual(self.judge()["primary"]["formal-std/wikijudge5"], want, (fx, br))

    def test_p_exactly_005_fails(self):
        from unittest import mock
        self.fill(formal=(30, 5))
        with mock.patch.object(evalstats, "mcnemar_exact", return_value=0.05):
            self.assertFalse(self.judge()["primary"]["formal-std/wikijudge5"])
        with mock.patch.object(evalstats, "mcnemar_exact", return_value=0.0499999):
            self.assertTrue(self.judge()["primary"]["formal-std/wikijudge5"])

    def test_chat_bootstrap_edge(self):
        # 500 rows, k breaks: the 90% lower bound is -0.6 pp (k=1), exactly -1.0 pp (k=2, passes), -1.2 pp (k=3)
        for k, lb, want in ((0, 0.0, True), (1, -0.6, True), (2, -1.0, True), (3, -1.2, False), (15, None, False)):
            self.fill(chat=(0, k))
            r = self.judge()["runs"]["chat-std/cjudge-1"]
            if lb is not None:
                self.assertAlmostEqual(r["bootstrap90_lower_pp"], lb, places=9)
            self.assertEqual(r["verdict"], want, k)
        self.fill(chat=(10, 2))                                   # fixes lift the bound
        self.assertTrue(self.judge()["runs"]["chat-std/cjudge-1"]["verdict"])

    def test_stops(self):
        self.fill()
        write_run(self.d, "e", "chat-cons", "cjudge-1", [1] * 500, rd_tag("chat-cons"))     # R1f tag in an R1g run
        with self.assertRaises(SystemExit):
            self.judge()
        self.fill()
        b, e = pattern(499, 1, 1, 0)                                                         # one row short
        write_run(self.d, "base", "formal-off", "wikijudge5", b, base_tag("formal-off"))
        write_run(self.d, "e", "formal-off", "wikijudge5", e, rd_tag("formal-off") + f"+rx:{self.rx}")
        with self.assertRaises(SystemExit):
            self.judge()
        self.fill()
        b, e = pattern(300, 1, 1, 0)
        write_run(self.d, "base", "formal-off", "wikijudge5", b, base_tag("formal-off"))
        write_run(self.d, "e", "formal-off", "wikijudge5", e, rd_tag("formal-off") + f"+rx:{self.rx}", glen=[5] * 299 + [6])   # gold lengths differ
        with self.assertRaises(SystemExit):
            self.judge()
        self.fill()
        with open(os.path.join(self.d, "counts.json"), "w") as f:
            json.dump({"wikijudge5": 300}, f)                                                # no cjudge-1 count
        with self.assertRaises(SystemExit):
            self.judge()

    def test_main_prints_verdict(self):
        import contextlib, io
        from unittest import mock
        self.fill(formal=(30, 5))
        out = os.path.join(self.d, "out.json")
        buf = io.StringIO()
        real = R.sha_file
        patch = mock.patch.object(R, "sha_file", lambda p: SHA + "0" * 56 if p.endswith("tinylm.sjtl") else real(p))   # the fake model file check only
        patch.start()
        self.addCleanup(patch.stop)
        with contextlib.redirect_stdout(buf):
            self.assertEqual(E.main(["judge", self.d, "--arm", "r1g", "--confirm", "--out", out]), 0)
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 9)
        self.assertEqual(lines[-1], "CONFIRMED")
        with open(out) as f:
            self.assertTrue(json.load(f)["all_pass"])
        self.fill()
        os.remove(out)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            E.main(["judge", self.d, "--arm", "r1g", "--confirm", "--out", out])
        self.assertEqual(buf.getvalue().splitlines()[-1], "NOT CONFIRMED: 照登記不過")
        with self.assertRaises(SystemExit):
            E.main(["judge", self.d, "--confirm"])               # r1f arm


if __name__ == "__main__":
    unittest.main()
