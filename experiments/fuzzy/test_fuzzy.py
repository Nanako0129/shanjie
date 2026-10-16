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


class Seq:
    """Stand-in for random.Random: random() cycles through `rolls`, choice() returns `pick`."""

    def __init__(self, rolls, pick):
        self.rolls, self.pick, self.i = rolls, pick, 0

    def random(self):
        self.i += 1
        return self.rolls[(self.i - 1) % len(self.rolls)]

    def choice(self, seq):
        assert self.pick in seq
        return self.pick


class FakeLex:
    by_reading = {("ㄇㄧㄥˊ",): 1, ("ㄇㄧㄥˇ",): 1, ("ㄇㄧㄣˊ",): 1, ("ㄇㄧㄥ",): 1, ("ㄙㄜˋ",): 1, ("ㄕㄜˋ",): 1,
                  ("ㄅㄚ", "ㄅㄚˋ"): 1}


class Static(unittest.TestCase):
    def test_keys_match_rust_source(self):
        # A source scan, not an observation of the shipping config (the Rust constants are private): it fails, not skips, when
        # engine.rs moves or the declarations change shape, so the Python copy cannot drift unnoticed.
        p = os.path.join(F.ROOT, "core", "src", "engine.rs")
        self.assertTrue(os.path.exists(p), "core/src/engine.rs moved: update this test and the copies in fuzzy.py")
        src = open(p, encoding="utf-8").read()
        for name, mine in (("KEYS_STANDARD", F.KEYS_STANDARD), ("SYMBOLS", F.SYMBOLS)):
            m = re.search(r'const %s: &str = "([^"]*)"' % name, src)
            self.assertIsNotNone(m, "no `const %s: &str = \"...\"` in engine.rs: update this test and fuzzy.py" % name)
            self.assertEqual(m.group(1), mine)
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
        # adjacent p = 1: every key with a neighbour is pressed as one (the draws replayed here), the composer shows the slot
        # composition of those keys; the tone mark stays
        out, _, _ = F.inject(["ㄇㄧㄥˊ"], "adjacent", 1.0, random.Random(3), valid)
        rng = random.Random(3)
        pressed = [(rng.random(), rng.choice(F.NEIGHBOURS[c]))[1] for c in "ㄇㄧㄥ"]
        self.assertEqual(out, [F.compose(pressed) + "ˊ"])
        # invalid flag: tone ˇ->? result absent from valid set is invalid
        out, ch, inv = F.inject(["ㄊㄧㄢ"], "tone", 1.0, random.Random(1), valid)
        self.assertTrue(inv)

    def test_adjacent_follows_slot_composition(self):
        # s3a rule 9: a key fills its own column, a second key in the same column replaces the first.
        self.assertEqual(F.compose("ㄇㄛㄥ"), "ㄇㄥ")
        self.assertEqual(F.compose("ㄇㄗㄥ"), "ㄗㄥ")
        self.assertEqual(F.compose("ㄇㄧㄥ"), "ㄇㄧㄥ")
        self.assertIn("ㄛ", F.NEIGHBOURS["ㄧ"]); self.assertIn("ㄗ", F.NEIGHBOURS["ㄧ"])
        valid = {"ㄇㄧㄥ", "ㄇㄥ", "ㄗㄥ"}
        # intended ㄇㄧㄥ, only the medial key is pressed wrong (rolls: ㄇ kept, ㄧ replaced, ㄥ kept)
        for wrong_key, shown in (("ㄛ", "ㄇㄥ"), ("ㄗ", "ㄗㄥ")):
            out, changed, invalid = F.inject(["ㄇㄧㄥ"], "adjacent", 0.5, Seq([0.9, 0.0, 0.9], wrong_key), valid)
            self.assertEqual((out, changed, invalid), ([shown], True, False))
        # the tone mark stays last
        out, _, _ = F.inject(["ㄇㄧㄥˊ"], "adjacent", 0.5, Seq([0.9, 0.0, 0.9], "ㄛ"), valid)
        self.assertEqual(out, ["ㄇㄥˊ"])
        # the tolerant variants undo the replacement: what is shown could have been ㄇㄧㄥ
        class Lex:
            by_reading = {(s,): 1 for s in valid}
        t = F.Tolerance(Lex, "adjacent")
        self.assertIn("ㄇㄧㄥ", t.variants("ㄇㄥ"))
        self.assertIn("ㄇㄧㄥ", t.variants("ㄗㄥ"))
        # a plain same-column swap still works: ㄛ (key i) and ㄟ (key o) are row neighbours
        t2 = F.Tolerance(type("L", (), {"by_reading": {("ㄇㄛ",): 1, ("ㄇㄟ",): 1}}), "adjacent")
        self.assertEqual(t2.variants("ㄇㄛ"), ["ㄇㄟ"])

    def test_rescue_counts(self):
        mk = lambda **k: dict(dict(gold="g", changed=True, invalid=False, clean_ok=True, failed=False, ex=["x"], tol=[("g", True)]), **k)
        recs = [mk(), mk(clean_ok=False), mk(ex=[], failed=True), mk(ex=["g"]), mk(changed=False)]
        # changed 4; one of them already wrong on clean input (excluded); wrong = rows 0 and 2 (row 3 is right); row 2 has an empty exact list
        self.assertEqual(F.rescue(recs, "G2"), (4, 1, 2, 2, 2, 1, 1))

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
        cls.rows = F.load_set("dev299")[:15]

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
        rows = F.load_set("dev299")[:4]
        sets = {"cvtune": rows[:3], "wikitune": rows[1:], "dev299": rows[:3], "typing76": F.load_set("typing76")[:3]}
        outs = []
        with tempfile.TemporaryDirectory() as td:
            for j in (1, 2):
                o = os.path.join(td, f"g{j}.tsv")
                grid.run_grid(sets, 2, 1, "chat", j, [0.1], [1.0, 3.0], o)
                rs = [l.rstrip("\n").split("\t") for l in open(o, encoding="utf-8")][1:]  # drop the '#' header
                wall = [rs[0].index("time_ratio"), rs[0].index("elapsed_s")]  # wall-clock: differ by run
                outs.append([[c for i, c in enumerate(r) if i not in wall] for r in rs])
            self.assertEqual(outs[0], outs[1])
            self.assertGreater(len(outs[0]), 10)

    def test_tiny_timing(self):
        recs = F.process(ctx(), self.rows, "tone", 0.0, 1, 2.0)
        print("\ntiming ratio G2 %.2f G1 %.2f G0 %.2f" % tuple(F.time_ratio(recs, g) for g in ("G2", "G1", "G0")))


if __name__ == "__main__":
    unittest.main()
