"""Smoke tests of the X0C-6 readout and plumbing on synthetic files (no CLI, no model, no private rows)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import prep  # noqa: E402
import x0c6ana as ana  # noqa: E402


def rec(i, ok, ll, skip=0):
    n = len(ok)
    return {"i": i, "n": n, "ng": [-1.0 - 0.1 * k for k in range(n)], "ok": ok, "ed": [0 if o else 2 for o in ok], "glen": 4,
            "skip": skip, "ll": ll, "ll0": ll, "llr": ll}


def write(d, s, recs, ship, ng):
    with open(os.path.join(d, f"X0C.{s}.jsonl"), "w") as f:
        f.writelines(json.dumps(r) + "\n" for r in recs)
    for tag, oks in (("ship", ship), ("ng", ng)):
        with open(os.path.join(d, f"{tag}.{s}.r"), "w") as f:
            f.writelines(f"{i}\t{int(o)}\t{0 if o else 2}\t4\n" for i, o in enumerate(oks, 1))


class Readout(unittest.TestCase):
    def synth(self, d, n_tune):
        # row kinds: A n-gram right, 0c agrees (ll favours 0); B n-gram wrong, 0c fixes (ll favours 1, big); C unreadable (skip) n-gram wrong
        def rows(n):
            recs = []
            for i in range(1, n + 1):
                k = i % 4
                if k == 0:
                    recs.append(rec(i, [0, 1], [0.0, 3.0]))            # 0c fixes
                elif k == 3:
                    recs.append(rec(i, [0, 1], [0.0, 0.0], skip=1))    # unreadable, stays wrong
                else:
                    recs.append(rec(i, [1, 0], [0.0, -3.0]))           # n-gram right, 0c agrees
            return recs
        for s, n in [(ana.TUNE, n_tune), (ana.JUDGE, 40)] + [(p, 12) for p in ana.PUBLIC]:
            recs = rows(n)
            ng = [bool(r["ok"][0]) for r in recs]
            ship = [bool(r["ok"][1]) if r["i"] % 8 == 0 else bool(r["ok"][0]) for r in recs]   # shipping fixes half of the fixable rows
            write(d, s, recs, ship, ng)

    def test_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            self.synth(d, 1000)
            em = os.path.join(d, "em")
            out = ana.main(d, em)
            fused = open(os.path.join(em, f"arm.{ana.JUDGE}.fused.txt")).read().splitlines()
            self.assertEqual(len(fused), 40)
            self.assertEqual(sum(int(l.split("\t")[1]) for l in fused), 30)
            prep.main(["digits"] + [os.path.join(em, f) for f in os.listdir(em)])
            self.assertEqual(len(os.listdir(em)), 15)
            open(os.path.join(em, "bad.txt"), "w").write("1\tx\n")
            with self.assertRaises(SystemExit):
                prep.main(["digits", os.path.join(em, "bad.txt")])
            self.assertEqual(out[ana.JUDGE]["skip"], 10)
            self.assertEqual(out[ana.JUDGE]["0c fused vs n-gram first"], (10, 0))   # 10 of 40 rows are fixable, none broken
            self.assertEqual(out[ana.JUDGE]["shipping vs n-gram first"], (5, 0))
            self.assertEqual(out[ana.JUDGE]["0c fused vs shipping"], (5, 0))
            self.assertEqual(out[ana.JUDGE]["top1"], (20, 25, 30))
            self.assertEqual(out["dev302"]["skip"], 3)

    def test_no_cell_keeps_ngram_first(self):
        with tempfile.TemporaryDirectory() as d:
            self.synth(d, 20)     # 20 rows: UB(0 broken) / 20 is 15%, above the cap, so no cell qualifies
            out = ana.main(d)
            self.assertEqual(out["cell"][3:], (0, 0))
            self.assertEqual(out[ana.JUDGE]["0c fused vs n-gram first"], (0, 0))

    def test_stop_when_ngram_stats_disagree(self):
        with tempfile.TemporaryDirectory() as d:
            self.synth(d, 1000)
            with open(os.path.join(d, f"ng.{ana.TUNE}.r"), "w") as f:
                f.writelines(f"{i}\t1\t0\t4\n" for i in range(1, 1001))
            with self.assertRaises(SystemExit):
                ana.main(d)


class Plumbing(unittest.TestCase):
    def test_build_split_check(self):
        with tempfile.TemporaryDirectory() as d:
            rows = [("", "我們", "ㄨㄛˇ ㄇㄣ˙"), ("你好", "你好", "ㄋㄧˇ ㄏㄠˇ")]
            cands = {1: [("我們", -1.0), ("窩們", -2.5)], 2: [("你好", -0.5), ("擬好", -3.0)]}
            names = {}
            for s, rr in (("a", rows[:1]), ("b", rows[1:])):
                p = os.path.join(d, s + ".txt")
                open(p, "w").write("".join(f"{c}|{t}|{r}\n" for c, t, r in rr) + "bad|line\n")
                names[s] = p
                open(os.path.join(d, s + ".dump"), "w").write("".join(f"1\t{k}\t{x}\t{sc!r}\n" for k, (x, sc) in enumerate(cands[1 if s == 'a' else 2], 1)))
                open(os.path.join(d, s + ".ng"), "w").write("1\t1\t0\t2\n")
            out = os.path.join(d, "o")
            prep.main(["build", "--out", out] + sum([["--set", s, names[s], os.path.join(d, s + ".dump"), os.path.join(d, s + ".ng")] for s in "ab"], []))
            dump = open(os.path.join(out, "all.dump")).read().splitlines()
            self.assertEqual(sorted(l.split("\t")[0] for l in dump), ["1", "1", "2", "2"])
            meta = json.load(open(os.path.join(out, "sets.json")))
            self.assertEqual(sorted(meta["perm"]), [0, 1])
            self.assertEqual(len(open(os.path.join(out, "all.rows.txt")).read().splitlines()), 2)
            v3 = [json.loads(l) for l in open(os.path.join(out, "all.v3.jsonl"))]
            self.assertEqual([r["i"] for r in v3], [1, 2])
            self.assertEqual(v3[0]["ok"], [1, 0])
            self.assertEqual(prep.permutation(50), prep.permutation(50))
            self.assertNotEqual(prep.permutation(50), list(range(50)))
            allscores = os.path.join(d, "all.jsonl")
            open(allscores, "w").write("".join(json.dumps({**r, "skip": 0, "ll": [0.0, 0.0]}) + "\n" for r in v3))
            prep.main(["split", "--dir", out, "--scores", allscores, "--out", os.path.join(d, "s")])
            self.assertEqual(json.loads(open(os.path.join(d, "s", "X0C.b.jsonl")).read())["i"], 1)
            # the combined order is the permutation: score rows carry their combined i; split puts each back in its set
            rr = open(os.path.join(out, "all.rows.txt")).read().splitlines()
            orig = [l.rstrip("\n") for s in "ab" for l in open(names[s]) if l.count("|") == 2]
            self.assertEqual(rr, [orig[g] for g in meta["perm"]])
            prep.main(["check", os.path.join(d, "s", "X0C.a.jsonl")])
            open(allscores, "w").write(json.dumps({"i": 1, "text": "我們"}) + "\n")
            with self.assertRaises(SystemExit):
                prep.main(["check", allscores])
            open(os.path.join(d, "a.ng"), "w").write("1\t0\t2\t2\n")    # n-gram-only run says wrong, the dump's rank 1 is right
            with self.assertRaises(SystemExit):
                prep.main(["build", "--out", out, "--set", "a", names["a"], os.path.join(d, "a.dump"), os.path.join(d, "a.ng")])


if __name__ == "__main__":
    unittest.main()
