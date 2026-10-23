"""Tests for build_t1.py on synthetic lexicons. Run: python3 -m unittest test_build_t1 (in this directory)."""
import contextlib, io, math, os, shutil, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_t1 as B  # noqa: E402


def lexicon(base, overlays=()):
    """(scores, mass) of a synthetic mcbpmf text `base` (`reading character score` lines) and overlay texts (four tab fields), in the shapes
    load() returns: scores by d23's rule (largest per pair), mass by p5.mc_extra over the base alone."""
    scores, mcb = {}, {}
    for l in base.split("\n"):
        f = l.split()
        if l[:1] in ("#", "_") or len(f) != 3:
            continue
        k = (tuple(f[0].split("-")), f[1])
        mcb[k] = max(mcb.get(k, -1e9), float(f[2]))
    for (r, w), s in mcb.items():
        if len(w) == 1:
            scores[r[0], w] = s
    for text in overlays:
        for l in text.split("\n"):
            f = l.split("\t")
            if len(f) == 4 and len(f[1]) == 1:
                scores[f[0], f[1]] = max(scores.get((f[0], f[1]), -1e9), float(f[2]))
    return scores, B.P5_MC_EXTRA(mcb)


def run(base, t0, overlays=()):
    """T1 rows as {(char, reading): t} and the counts, for a synthetic mcbpmf text `base`, an overlay list and T0 {(char, reading): t}."""
    scores, mass = lexicon(base, list(overlays))
    rows, counts = B.build([(c, r, t) for (c, r), t in t0.items()], scores, mass)
    return {(c, r): t for c, r, t in rows}, counts


class Rule1(unittest.TestCase):
    BASE = "a 甲 -3.0\nb 甲 -3.0\nc 甲 -3.0\na-a 甲甲 -2.0\nb-a 甲乙 -2.5\nb-a 甲乙 -2.0\na 乙 -1.0\nb 乙 -2.0\nb-b 乙乙 -1.0\n_x _y 0\n# c\n"
    T0 = {("甲", "a"): 0.0, ("甲", "b"): 0.0, ("甲", "c"): 0.0, ("乙", "a"): 0.0, ("乙", "b"): -1.0}

    def test_equal_score_characters_get_multi_character_mass(self):
        t1, c = run(self.BASE, self.T0)
        # mass 甲: a = 10^-2 (甲甲, first position) + 10^-2 (甲甲, second); b = 10^-2 (甲乙 once, at its larger score); c = 0
        top = 2 * 1e-2 + 0
        self.assertAlmostEqual(t1["甲", "a"], 0.0, places=12)
        self.assertAlmostEqual(t1["甲", "b"], math.log10((1e-2 + 1e-9) / (top + 1e-9)), places=12)
        self.assertAlmostEqual(t1["甲", "c"], math.log10(1e-9 / (top + 1e-9)), places=12)
        self.assertEqual(t1["甲", "c"], math.log10(1e-9 / (top + 1e-9)))
        self.assertEqual((c["equal_score_chars"], c["rule1"]), (1, {"chars": 1, "pairs": 2}))

    def test_a_character_with_different_scores_is_untouched(self):
        t1, _ = run(self.BASE, self.T0)
        self.assertEqual((t1["乙", "a"], t1["乙", "b"]), (0.0, -1.0))

    def test_no_multi_character_word_keeps_zero(self):
        t1, c = run("a 丙 -3.0\nb 丙 -3.0\n", {("丙", "a"): 0.0, ("丙", "b"): 0.0})
        self.assertEqual((t1["丙", "a"], t1["丙", "b"]), (0.0, 0.0))
        self.assertEqual(c["rule1"], {"chars": 0, "pairs": 0})

    def test_the_overlay_decides_equality_but_adds_no_mass(self):
        # an overlay row gives 丙 b a higher score: no longer equal; an overlay multi-character word is not counted in the mass
        base = "a 丙 -3.0\nb 丙 -3.0\na-a 丁丁 -1.0\n"
        t0 = {("丙", "a"): 0.0, ("丙", "b"): 0.0}
        t1, _ = run(base, t0, ["b\t丙\t-3.0\tx\n"])   # same score again: still equal, but no multi-character word reads 丙, so t stays 0
        self.assertEqual(t1, t0)
        scores, mass = lexicon(base, ["b-b\t丙丙\t-1.0\tx\n", "b\t丙\t-2.0\tx\n"])
        self.assertEqual(scores["b", "丙"], -2.0)
        self.assertNotIn(("b", "丙"), mass)

    def test_the_mass_is_p5s(self):
        import p5
        self.assertIs(B.P5_MC_EXTRA, p5.mc_extra)

    def test_t0_and_the_lexicon_must_agree(self):
        with self.assertRaises(SystemExit):
            run(self.BASE, {("甲", "a"): 0.0})


class Rule2(unittest.TestCase):
    def test_exemptions(self):
        base = "".join(f"{r} {c} -{s}\n" for c, rs in {"嗎": ("ㄇㄚ", "ㄇㄚ˙", "ㄇㄚˇ"), "波": ("ㄅㄛ", "ㄆㄛ"), "和": ("ㄏㄜˊ", "ㄏㄢˋ"), "麼": ("ㄇㄛ˙", "ㄇㄜ˙"),
                                                       "癌": ("ㄞˊ", "ㄧㄢˊ"), "一": ("ㄧ", "ㄧˊ", "ㄧˋ"), "不": ("ㄅㄨˋ", "ㄅㄨˊ"), "它": ("ㄊㄚ", "ㄊㄚ˙")}.items()
                       for r, s in zip(rs, (1, 4, 5)))
        t0 = {(c, r): -3.0 for l in base.split("\n") if l for r, c, _ in [l.split()]}
        t1, c = run(base, t0)
        for pair in [("嗎", "ㄇㄚ"), ("嗎", "ㄇㄚ˙"), ("波", "ㄅㄛ"), ("和", "ㄏㄜˊ"), ("麼", "ㄇㄛ˙"), ("癌", "ㄞˊ"), ("一", "ㄧˊ"), ("一", "ㄧˋ"), ("不", "ㄅㄨˊ")]:
            self.assertEqual(t1[pair], 0.0, pair)
        for pair in [("嗎", "ㄇㄚˇ"), ("波", "ㄆㄛ"), ("和", "ㄏㄢˋ"), ("麼", "ㄇㄜ˙"), ("癌", "ㄧㄢˊ"), ("一", "ㄧ"), ("不", "ㄅㄨˋ"), ("它", "ㄊㄚ˙")]:
            self.assertEqual(t1[pair], -3.0, pair)
        self.assertEqual(c["rule2"]["pairs"], 9)

    def test_tone_helpers(self):
        self.assertTrue(B.first_tone("ㄇㄚ") and not B.first_tone("ㄇㄚ˙") and not B.first_tone("ㄇㄚˊ") and not B.first_tone("ㄇㄚˇ") and not B.first_tone("ㄇㄚˋ"))
        self.assertTrue(B.neutral("ㄇㄚ˙") and B.neutral("˙ㄇㄚ") and not B.neutral("ㄇㄚ"))


class Rule3(unittest.TestCase):
    def test_a1_neutral_readings_are_capped(self):
        self.assertEqual(len(B.A1_CHARS), 44)
        base = "ㄅㄚ 巴 -2.0\nㄅㄚ˙ 巴 -2.5\nㄅㄚˊ 琶 -2.0\nㄆㄚ˙ 琶 -2.0\nㄉㄜ˙ 的 -1.0\nㄉㄧˋ 的 -2.0\nㄏㄜ˙ 和 -3.0\n"
        t0 = {("巴", "ㄅㄚ"): 0.0, ("巴", "ㄅㄚ˙"): -0.5, ("琶", "ㄅㄚˊ"): 0.0, ("琶", "ㄆㄚ˙"): -0.2, ("的", "ㄉㄜ˙"): -3.0, ("的", "ㄉㄧˋ"): -3.0, ("和", "ㄏㄜ˙"): -2.5}
        t1, c = run(base, t0)
        self.assertEqual((t1["巴", "ㄅㄚ˙"], t1["琶", "ㄆㄚ˙"]), (-1.0, -1.0))         # capped
        self.assertEqual(t1["和", "ㄏㄜ˙"], -2.5)                                      # min: already lower
        self.assertEqual((t1["巴", "ㄅㄚ"], t1["琶", "ㄅㄚˊ"]), (0.0, 0.0))             # non-neutral readings
        self.assertEqual((t1["的", "ㄉㄜ˙"], t1["的", "ㄉㄧˋ"]), (-3.0, -3.0))          # not an A1 character
        self.assertEqual(c["rule3"], {"chars": 2, "pairs": 2})
        self.assertEqual(c["a1_chars_without_neutral_pair"], 41)

    def test_rules_apply_in_order(self):
        # 巴 has equal scores: rule 1 gives the neutral reading all the mass (0) and the first-tone reading none; rule 3 then caps the neutral one
        base = "ㄅㄚ 巴 -3.0\nㄅㄚ˙ 巴 -3.0\nㄅㄚ˙-ㄅㄚ˙ 巴巴 -1.0\n"
        t1, c = run(base, {("巴", "ㄅㄚ"): 0.0, ("巴", "ㄅㄚ˙"): 0.0})
        self.assertEqual(t1["巴", "ㄅㄚ"], math.log10(1e-9 / (0.2 + 1e-9)))
        self.assertEqual(t1["巴", "ㄅㄚ˙"], -1.0)
        self.assertEqual((c["rule1"]["pairs"], c["rule2"]["pairs"], c["rule3"]["pairs"], c["total"]["pairs"]), (1, 0, 1, 2))


class Exempt(unittest.TestCase):
    def test_every_exempt_pair_must_be_in_the_lexicon(self):
        scores = {(r, c): 0.0 for c, r in B.EXEMPT_PAIRS}
        B.check_exempt(scores)
        for pair in sorted(B.EXEMPT_PAIRS):
            bad = {k: v for k, v in scores.items() if k != (pair[1], pair[0])}
            with self.assertRaises(SystemExit):
                B.check_exempt(bad)
        typo = dict(scores)
        del typo["ㄅㄛ", "波"]
        typo["ㄅㄛˊ", "波"] = 0.0   # a typo'd reading
        with self.assertRaises(SystemExit):
            B.check_exempt(typo)

    def test_the_repo_lexicon_has_them_all(self):
        scores, mass = B.load()
        B.check_exempt(scores)
        self.assertTrue(mass)


class Files(unittest.TestCase):
    def test_table_round_trip_and_reproducible_main(self):
        d = tempfile.mkdtemp()
        try:
            scores, mass = lexicon("ㄅㄚ 巴 -2.0\nㄅㄚ˙ 巴 -2.0\nㄅㄚ-ㄅㄚ˙ 巴巴 -1.0\n")
            for p in B.EXEMPT_PAIRS:
                scores[p[1], p[0]] = 0.0
            exempt_t0 = "".join(f"{c}\t{r}\t0.0\n" for c, r in sorted(B.EXEMPT_PAIRS))
            with open(os.path.join(d, "t0.tsv"), "w", encoding="utf-8") as f:
                f.write("巴\tㄅㄚ\t0.0\n巴\tㄅㄚ˙\t0.0\n" + exempt_t0)
            outs = []
            for k in (1, 2):
                with mock.patch.object(B, "load", return_value=(scores, mass)), contextlib.redirect_stdout(io.StringIO()) as buf:
                    self.assertEqual(B.main(["--t0", os.path.join(d, "t0.tsv"), "--out", os.path.join(d, f"t1.{k}")]), 0)
                outs.append(B.read_text(os.path.join(d, f"t1.{k}")))
            self.assertEqual(outs[0], outs[1])
            self.assertIn("T0 sha256", buf.getvalue())
            self.assertIn("T1 sha256", buf.getvalue())
            self.assertIn("exempt 波 ㄅㄛ t0 0.0 t1 0.0 unchanged", buf.getvalue())
            rows = B.read_table(outs[0])
            self.assertEqual([(c, r) for c, r, _ in rows][:2], [("巴", "ㄅㄚ"), ("巴", "ㄅㄚ˙")])
            self.assertEqual(B.write_table(rows), outs[0])
            with self.assertRaises(SystemExit):   # --out exists
                B.main(["--t0", os.path.join(d, "t0.tsv"), "--out", os.path.join(d, "t1.1")])
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
