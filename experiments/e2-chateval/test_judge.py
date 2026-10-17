"""E2 judge.py (docs/contracts/e2-chat-eval.md section 5 item 1): synthetic score files, no data."""
import contextlib
import io
import json
import math
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import judge  # noqa: E402
import fusegrid as fg  # noqa: E402
import evalstats  # noqa: E402


def rec(i, ng, ll, ok):
    return {"i": i, "n": len(ng), "ng": ng, "ok": ok, "ed": [0 if o else 2 for o in ok], "glen": 6, "key": 2, "key16": 2,
            "ll": ll, "ll0": ll, "ll16": ll}


FIX = lambda i: rec(i, [-1.0, -1.5], [-4.0, -1.0], [0, 1])       # the fusion turns it right (gap 2.5 at alpha 1)
BRK = lambda i: rec(i, [-1.0, -1.5], [-4.0, -1.0], [1, 0])
MIX = lambda i, a: rec(i, [-1.0, -1.4 - 0.1 * (i % 3)], [-4.0 + a, -1.0], [i % 2, (i + 1) % 2])


def write_scores(d, setting, name, rows):
    with open(os.path.join(d, f"M8.{setting}.{name}.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def run(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cfg = judge.main(argv)
    return buf.getvalue(), cfg


def cells(out, label):
    """The cells of every table row whose first cell starts with `label`: {set: [cells]}."""
    return {r.split("|")[1].split()[-1]: [x.strip() for x in r.strip().strip("|").split("|")] for r in out.splitlines() if r.startswith(f"| {label} ")}


class Fixed(unittest.TestCase):
    def setUp(self):
        self.d, self.o = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.sets = {"cvtune": [FIX(1), FIX(2), BRK(3)] + [MIX(i, 0.3 * i) for i in range(4, 40)],
                     "judge": [FIX(1), BRK(2), BRK(3), MIX(4, 1.1)] + [MIX(i, 0.2 * i) for i in range(5, 30)]}
        for n, rows in self.sets.items():
            write_scores(self.d, "chat", n, rows)

    def test_fixed_values_equal_pick_computed_directly(self):
        out, _ = run(["--scores", self.d, "--sets", "cvtune", "judge", "--setting", "chat", "--out", self.o, "--fixed", "R2", "0.2", "1", "0.25",
                      "--fixed", "R1", "0.7", "2", "0"])
        for label, (a, t, de) in (("R2", (0.2, 1.0, 0.25)), ("R1", (0.7, 2.0, 0.0))):
            got = cells(out, label)
            for name, rows in self.sets.items():
                f, b = fg.fixed_broken(rows, a, t, de)
                x = got[name]
                self.assertEqual((int(x[1]), int(x[4]), int(x[5])), (len(rows), f, b), (label, name))
                self.assertEqual((x[-3], x[-2]), (str(f - b), f"{100 * b / len(rows):.2f}%"))
                # the rowstats judge wrote are those of pick, and the table row is evalstats' own
                base = os.path.join(self.o, "rowstats", f"{label}.chat.{name}.base.txt")
                new = os.path.join(self.o, "rowstats", f"{label}.chat.{name}.new.txt")
                self.assertEqual(open(new).read(), fg.rowstats(rows, [fg.pick(r, a, t, "ll", de) for r in rows]))
                want = evalstats.compare(base, new, f"{label} chat {name}", net=True).splitlines()[-1]
                self.assertIn(want, out)

    def test_no_net_prints_the_old_columns_only_and_other_files_are_not_needed(self):
        out, _ = run(["--scores", self.d, "--sets", "cvtune", "--setting", "chat", "--out", self.o, "--fixed", "R2", "0.2", "1", "0.25", "--no-net"])
        self.assertNotIn("淨值", out)
        self.assertEqual(len(cells(out, "R2")["cvtune"]), 11)            # no formal files, no discordtune: not looked for
        out, _ = run(["--scores", self.d, "--sets", "cvtune", "--setting", "chat", "--out", self.o, "--fixed", "R2", "0.2", "1", "0.25"])
        self.assertEqual(len(cells(out, "R2")["cvtune"]), 14)

    def test_a_set_with_no_score_file_exits(self):
        with self.assertRaises(SystemExit):
            run(["--scores", self.d, "--sets", "cvtune", "missing", "--setting", "chat", "--out", self.o, "--fixed", "R2", "0.2", "1", "0.25"])
        with self.assertRaises(SystemExit):     # the formal setting has no files here
            run(["--scores", self.d, "--sets", "cvtune", "--setting", "formal", "--out", self.o, "--fixed", "R2", "0.2", "1", "0.25"])

    def test_the_first_score_dir_with_the_file_wins(self):
        d2 = tempfile.mkdtemp()
        write_scores(d2, "chat", "judge", [FIX(1), FIX(2)])
        out, _ = run(["--scores", d2, self.d, "--sets", "cvtune", "judge", "--setting", "chat", "--out", self.o, "--fixed", "R2", "0.2", "1", "0.25"])
        self.assertEqual(cells(out, "R2")["judge"][1], "2")


class NewOptions(unittest.TestCase):
    """--cap-ub, --frontier, --jsubsets, --diagnose; every default output stays as it was."""
    def setUp(self):
        self.d, self.o = tempfile.mkdtemp(), tempfile.mkdtemp()
        ok = lambda i: rec(i, [-1.0, -1.5], [-4.0, -1.0], [1, 1])
        self.tune = [FIX(i) for i in range(1, 41)] + [BRK(41)] + [ok(42 + i) for i in range(199)]
        self.other = [FIX(1), BRK(2), rec(3, [-1.0, -1.5], [-1.0, -2.0], [0, 0])]
        write_scores(self.d, "chat", "tune", self.tune)
        write_scores(self.d, "chat", "other", self.other)
        self.base = ["--scores", self.d, "--sets", "tune", "other", "--setting", "chat", "--out", self.o]
        self.sel = self.base + ["--select", "R", "--tuning", "tune", "--weight", "1"]

    def test_defaults_print_nothing_new(self):
        out = run(self.sel + ["--cap", "0.01"])[0]
        for word in ("J-right", "n_Jright", "diagnose", "| cap |"):
            self.assertNotIn(word, out)

    def test_cap_ub_picks_a_stricter_cell_than_cap_at_the_same_number(self):
        _, c1 = run(self.sel + ["--cap", "0.0125"])              # point rate 1 / 240 = 0.42%: the fusing cell is allowed
        self.assertEqual(fg.fixed_broken(self.tune, *c1[-1][1]), (40, 1))
        with self.assertRaises(ValueError):                      # bound of 0 broken over 240 rows is 1.248%; of 1, 1.977%
            run(self.sel + ["--cap-ub", "0.01"])
        _, c2 = run(self.sel + ["--cap-ub", "0.0125"])           # only the zero-broken cell (fusion off) passes
        self.assertEqual(fg.fixed_broken(self.tune, *c2[-1][1]), (0, 0))

    def test_cap_ub_output_line(self):
        out, cfg = run(self.sel + ["--cap-ub", "0.02"])
        self.assertEqual(fg.fixed_broken(self.tune, *cfg[-1][1]), (40, 1))
        ub = 100 * evalstats.poisson_upper95(1) / 240
        self.assertIn(f"n 240, n_Jright 200, broken 1, point rate 0.417%, 95% upper bound {ub:.3f}%", out)

    def test_cap_ub_too_strict_raises_and_cap_ub_excludes_cap(self):
        with self.assertRaises(ValueError):
            run(self.sel + ["--cap-ub", "0.005"])
        for extra in (["--cap", "0.1", "--cap-ub", "0.1"], ["--cap-ub", "1.5"]):
            with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()):
                run(self.sel + extra)
            self.assertEqual(e.exception.code, 2)
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()):
            run(self.base + ["--fixed", "A", "1", "1", "0", "--frontier"])
        self.assertEqual(e.exception.code, 2)

    def test_frontier_rows(self):
        out, _ = run(self.sel + ["--cap-ub", "0.03", "--frontier"])
        rows = [l for l in out.splitlines() if l.startswith("| 0.")]
        self.assertEqual([r.split("|")[1].strip() for r in rows], ["0.1%", "0.2%", "0.3%", "0.4%", "0.5%"])
        self.assertTrue(all("no cell" in r for r in rows))        # even 0 broken has a bound of 1.25% over 240 rows
        out, _ = run(self.sel + ["--cap", "0.01", "--frontier"])
        rows = [l.split("|") for l in out.splitlines() if l.startswith("| 0.")]
        self.assertEqual([r[1].strip() for r in rows], ["0.1%", "0.2%", "0.3%", "0.4%", "0.5%"])
        want = fg.frontier({"tune": self.tune}, 1, names=("tune",))
        for r, (c, cell) in zip(rows, want):
            if cell is None:
                self.assertIn("no cell", r[2])
            else:
                self.assertEqual((int(r[5]), int(r[6]), int(r[7])), (cell[3], cell[4], 240))

    def test_jsubsets_lines(self):
        out, _ = run(self.base + ["--fixed", "A", "1", "inf", "0", "--jsubsets"])
        lines = [l for l in out.splitlines() if "J-right n" in l]
        self.assertEqual(len(lines), 2)
        self.assertIn("A chat tune: J-right n 200, broken 1 (0.50%, 95% upper 2.", lines[0])
        self.assertIn("J-wrong-in-top-8 n 40, fixed 40; J-wrong-outside-top-8 n 0", lines[0])
        self.assertIn("A chat other: J-right n 1, broken 1", lines[1])
        self.assertIn("J-wrong-outside-top-8 n 1", lines[1])
        plain, _ = run(self.base + ["--fixed", "A", "1", "inf", "0"])
        self.assertEqual("\n".join(l for l in out.splitlines() if "J-right n" not in l), plain.rstrip("\n"))

    def test_diagnose_lines_and_standalone(self):
        out, cfg = run(self.base + ["--diagnose"])               # no --fixed, no --select: allowed
        self.assertEqual(cfg, [])
        self.assertIn("chat tune: n 240, headroom 40 (16.67%), model alone right on headroom 40, keeps candidate 0 on J-right 0 of 200, oracle@8 240", out)
        self.assertIn("chat other: n 3, headroom 1 (33.33%)", out)


class Select(unittest.TestCase):
    def test_selection_reads_only_the_tuning_sets_and_honours_the_cap(self):
        d, o = tempfile.mkdtemp(), tempfile.mkdtemp()
        tune = [FIX(1), FIX(2), BRK(3)]                    # 2 fixed and 1 broken for every cell that fixes: 33% broken
        write_scores(d, "chat", "tune", tune)
        write_scores(d, "chat", "judge", [BRK(i) for i in range(1, 30)])
        args = ["--scores", d, "--sets", "tune", "judge", "--setting", "chat", "--out", o, "--select", "R3", "--tuning", "tune", "--weight", "1"]
        _, cfg = run(args)
        chosen = cfg[-1]
        self.assertEqual(chosen[0], "R3")
        self.assertEqual(fg.fixed_broken(tune, *chosen[1]), (2, 1))             # weight 1: 2 - 1 > 0
        write_scores(d, "chat", "judge", [FIX(i) for i in range(1, 30)])        # the other set changes: the selection does not
        self.assertEqual(run(args)[1][-1], chosen)
        _, capped = run(args + ["--cap", "0.1"])                                 # 1 / 3 > 0.1: no fusing cell is allowed
        self.assertEqual(fg.fixed_broken(tune, *capped[-1][1]), (0, 0))
        tune2 = [BRK(1), BRK(2), FIX(3)]                                         # the tuning set changes: the selection does
        write_scores(d, "chat", "tune", tune2)
        self.assertEqual(fg.fixed_broken(tune2, *run(args)[1][-1][1]), (0, 0))      # weight 1: 1 - 2 < 0, so the fusion is off

    def test_select_needs_its_options(self):
        d, o = tempfile.mkdtemp(), tempfile.mkdtemp()
        write_scores(d, "chat", "tune", [FIX(1)])
        for extra in (["--select", "R3"], ["--select", "R3", "--tuning", "other", "--weight", "1"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                run(["--scores", d, "--sets", "tune", "--setting", "chat", "--out", o] + extra)

    def test_bad_weight_cap_and_orphan_options_are_clean_errors(self):
        d, o = tempfile.mkdtemp(), tempfile.mkdtemp()
        write_scores(d, "chat", "tune", [FIX(1)])
        write_scores(d, "chat", "empty", [])
        base = ["--scores", d, "--sets", "tune", "--setting", "chat", "--out", o]
        sel = base + ["--select", "R3", "--tuning", "tune"]
        for extra in (sel + ["--weight", "-1"], sel + ["--weight", "nan"], sel + ["--weight", "1", "--cap", "nan"],
                      sel + ["--weight", "1", "--cap", "1.5"], sel + ["--weight", "1", "--cap", "-0.1"],
                      base + ["--fixed", "A", "1", "1", "0", "--weight", "1"], base + ["--fixed", "A", "1", "1", "0", "--cap", "0.1"]):
            with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()):
                run(extra)
            self.assertEqual(e.exception.code, 2, extra)
        err = io.StringIO()
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(err):
            run(["--scores", d, "--sets", "tune", "empty", "--setting", "chat", "--out", o, "--select", "R3", "--tuning", "empty", "--weight", "1"])
        self.assertEqual(e.exception.code, 2)
        self.assertIn("no tuning rows", err.getvalue())


if __name__ == "__main__":
    unittest.main()
