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


if __name__ == "__main__":
    unittest.main()
