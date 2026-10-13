import os
import random
import re
import unittest

import fuzzy as F

_ctx = None


def ctx():
    global _ctx
    if _ctx is None:
        _ctx = F.Ctx("chat")
    return _ctx


class FakeLex:
    by_reading = {("ㄇㄧㄥˊ",): 1, ("ㄇㄧㄥˇ",): 1, ("ㄇㄧㄣˊ",): 1, ("ㄇㄧㄥ",): 1, ("ㄙㄜˋ",): 1, ("ㄕㄜˋ",): 1,
                  ("ㄅㄚ", "ㄅㄚˋ"): 1}


class Static(unittest.TestCase):
    def test_keys_match_rust_source(self):
        p = os.path.join(F.ROOT, "core", "src", "engine.rs")
        if not os.path.exists(p):
            self.skipTest("no Rust source")
        src = open(p, encoding="utf-8").read()
        self.assertEqual(re.search(r'const KEYS_STANDARD: &str = "([^"]*)"', src).group(1), F.KEYS_STANDARD)
        self.assertEqual(re.search(r'const SYMBOLS: &str = "([^"]*)"', src).group(1), F.SYMBOLS)
        key_sym = dict(zip(F.KEYS_STANDARD, F.SYMBOLS))
        self.assertEqual((key_sym["1"], key_sym["q"], key_sym["8"], key_sym["-"]), ("ㄅ", "ㄆ", "ㄚ", "ㄦ"))

    def test_adjacent_neighbours(self):
        ks = dict(zip(F.KEYS_STANDARD, F.SYMBOLS))
        # q row: q w e r ...: ㄆ(q) has ㄅ? no, 1 is another row; row neighbours of w are q and e
        self.assertEqual(set(F.NEIGHBOURS[ks["w"]]), {ks["q"], ks["e"]})
        # 3 (tone) is not a symbol key: ㄅ? '2' (ㄉ) neighbours in the digit row are 1 and 3; 3 dropped
        self.assertEqual(F.NEIGHBOURS[ks["2"]], [ks["1"]])
        # row end: '-' (ㄦ) has left neighbour 0 (ㄤ) and right neighbour '=' (not a symbol key)
        self.assertEqual(F.NEIGHBOURS[ks["-"]], [ks["0"]])
        for sym, nb in F.NEIGHBOURS.items():
            self.assertTrue(set(nb) <= set(F.SYMBOLS))
        # no key has more than two neighbours; tone keys 3 4 6 7 never contribute
        self.assertTrue(all(len(v) <= 2 for v in F.NEIGHBOURS.values()))

    def test_normalised_index(self):
        t = F.Tolerance(FakeLex, "tone")
        self.assertEqual(t.normal("ㄇㄧㄥˇ"), "ㄇㄧㄥ")
        self.assertEqual(t.variants("ㄇㄧㄥˊ"), ["ㄇㄧㄥ", "ㄇㄧㄥˇ"])  # ㄇㄧㄣˊ is another base
        self.assertEqual(t.variants("ㄇㄧㄥˋ"), ["ㄇㄧㄥ", "ㄇㄧㄥˇ", "ㄇㄧㄥˊ"])
        f = F.Tolerance(FakeLex, "fuzzy")
        self.assertEqual(f.normal("ㄙㄜˋ"), "ㄕㄜˋ")
        self.assertEqual(f.variants("ㄙㄜˋ"), ["ㄕㄜˋ"])
        self.assertEqual(f.variants("ㄇㄧㄥˊ"), ["ㄇㄧㄣˊ"])
        f1 = F.Tolerance(FakeLex, "fuzzy", ["ㄓㄗ"])  # per-pair switch: ㄣㄥ off
        self.assertEqual(f1.variants("ㄇㄧㄥˊ"), [])
        a = F.Tolerance(FakeLex, "adjacent")
        # ㄥ is key '/' ; row neighbours '.'(ㄡ) and none on the right: ㄇㄧㄥˊ -> one-symbol replacements that are valid
        self.assertEqual(a.variants("ㄇㄧㄥˊ"), [])
        self.assertEqual(a.variants("ㄇㄧㄥ"), [])
        self.assertEqual(a.variants("ㄅㄚ"), [])  # no valid neighbour variant in the fake lexicon

    def test_injection_reproducible_and_units(self):
        valid = {"ㄇㄧㄥˊ", "ㄇㄧㄥˇ"}
        syls = ["ㄇㄧㄥˊ", "ㄊㄧㄢ", "ㄗㄠˇ", "ㄕㄤˋ"] * 5
        for kind in F.KINDS:
            r = lambda: F.inject(syls, kind, 0.3, random.Random(f"7|{kind}|0.3|0"), valid)
            self.assertEqual(r(), r())
        # p = 1: tone changes every syllable's tone and keeps the base
        out, ch, inv = F.inject(syls, "tone", 1.0, random.Random(1), valid)
        self.assertTrue(ch)
        for a, b in zip(syls, out):
            self.assertEqual(F.strip_tone(a), F.strip_tone(b)); self.assertNotEqual(a, b)
        # fuzzy p = 1 flips exactly the paired symbols (no ㄣㄥㄓㄗㄔㄘㄕㄙ in ㄊㄧㄢ: ㄢ is not a pair member)
        out, _, _ = F.inject(["ㄗㄠˇ", "ㄊㄧㄢ", "ㄕㄤˋ"], "fuzzy", 1.0, random.Random(1), valid)
        self.assertEqual(out, ["ㄓㄠˇ", "ㄊㄧㄢ", "ㄙㄤˋ"])
        # fuzzy unit is per paired symbol: p = 0 changes nothing
        self.assertEqual(F.inject(syls, "fuzzy", 0.0, random.Random(1), valid)[1], False)
        # adjacent p = 1: every symbol with a neighbour moves to a neighbour; tone marks stay
        out, _, _ = F.inject(["ㄇㄧㄥˊ"], "adjacent", 1.0, random.Random(3), valid)
        o = out[0]
        self.assertEqual(F.tone_of(o), "ˊ")
        for a, b in zip(F.strip_tone("ㄇㄧㄥˊ"), F.strip_tone(o)):
            self.assertIn(b, F.NEIGHBOURS[a])
        # invalid flag: tone ˇ->? result absent from valid set is invalid
        out, ch, inv = F.inject(["ㄊㄧㄢ"], "tone", 1.0, random.Random(1), valid)
        self.assertTrue(inv)

    def test_gate_lists(self):
        ex = ["a", "b", "c", "d", "e", "f", "g"]
        tol = [("x", True), ("a", False), ("y", True), ("b", True), ("z", True), ("w", True), ("v", True)]
        g = F.gate_lists(ex, tol, False)
        self.assertEqual(g["G1"], ["a", "b", "c", "d", "e", "x", "y", "z", "w", "f", "g"])
        self.assertEqual(g["G0"], ex)
        self.assertEqual(F.gate_lists(ex, tol, True)["G0"], [s for s, _ in tol])
        self.assertEqual(g["G2"], [s for s, _ in tol])


class RealModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = F.load_set("dev302")[:15]

    def test_tol_decoder_equals_exact_without_variants(self):
        c = ctx()
        t = F.Tolerance(c.lex, "tone")
        t.variants = lambda s: []
        import lm as L
        for gold, syls in self.rows[:5]:
            e = [(s, ws) for s, ws in L.decode(c.lex, syls, c.lm, "chat")]
            d = [(s, ws) for s, ws, f in F.decode_tol(c.lex, t, syls, c.lm, "chat", 2.0)]
            self.assertEqual(e, d)

    def test_clean_top1_unchanged_g0_g1(self):
        c = ctx()
        for kind in F.KINDS:
            recs = F.process(c, self.rows, kind, 0, 1, 2.0)
            for gate in ("G0", "G1"):
                for r in recs:
                    self.assertEqual(F.gate_lists(r["ex"], r["tol"], r["failed"])[gate][0], r["ex"][0])
                self.assertEqual(F.clean_stats(recs, gate)[3], 0)  # broken

    def test_process_reproducible(self):
        c = ctx()
        a = F.process(c, self.rows[:5], "adjacent", 0.2, 3, 2.0)
        b = F.process(c, self.rows[:5], "adjacent", 0.2, 3, 2.0)
        key = lambda rs: [(r["ex"][:3], r["tol"][:3], r["changed"], r["invalid"]) for r in rs]
        self.assertEqual(key(a), key(b))

    def test_grid_jobs_identical(self):
        import tempfile
        import grid
        rows = F.load_set("dev302")[:4]
        sets = {"cvtune": rows[:3], "wikitune": rows[1:], "dev302": rows[:3], "typing76": F.load_set("typing76")[:3]}
        outs = []
        with tempfile.TemporaryDirectory() as td:
            for j in (1, 2):
                o = os.path.join(td, f"g{j}.tsv")
                grid.run_grid(sets, 2, 1, "chat", j, [0.1], [1.0, 3.0], o)
                rs = [l.rstrip("\n").split("\t") for l in open(o, encoding="utf-8")][1:]  # drop the '#' header
                outs.append([r[:15] + r[17:] for r in rs])  # drop time_ratio and elapsed_s: wall-clock, differ by run
            self.assertEqual(outs[0], outs[1])
            self.assertGreater(len(outs[0]), 10)

    def test_tiny_timing(self):
        recs = F.process(ctx(), self.rows, "tone", 0.0, 1, 2.0)
        print("\ntiming ratio G2 %.2f G1 %.2f G0 %.2f" % tuple(F.time_ratio(recs, g) for g in ("G2", "G1", "G0")))


if __name__ == "__main__":
    unittest.main()
