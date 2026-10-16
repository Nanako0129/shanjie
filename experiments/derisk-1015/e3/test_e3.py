"""Tests for experiments/derisk-1015/e3 (synthetic lexicon and language model; no data files).
Run: python3 -W ignore -m unittest experiments/derisk-1015/e3/test_e3.py -v   (from the repo root)
"""
import collections
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import abbrev  # noqa: E402
import decide  # noqa: E402
import gen_abbrev  # noqa: E402

L = abbrev.L

ROWS = ["前|他常常遲到|ㄊㄚ ㄔㄤˊ ㄔㄤˊ ㄔˊ ㄉㄠˋ", "|我要去銀行|ㄨㄛˇ ㄧㄠˋ ㄑㄩˋ ㄧㄣˊ ㄏㄤˊ", "|二|ㄦˋ"]


class Lex:
    """The part of ime.Lexicon / lm.cap_overlay's result that decode reads."""

    def __init__(self, entries):   # entries: {reading tuple: [(word, lp)]}
        self.by_reading = {k: sorted(v, key=lambda x: -x[1]) for k, v in entries.items()}
        self.max_len = max(len(k) for k in entries)
        self.demote = {}


class FakeLM:
    """Deterministic stand-in for BigramLM: favours a word after a fixed previous word, nothing else."""

    def word(self, lam, v, w, lp):
        return lam * (1.0 if (v, w) in {("<s>", "你"), ("你", "好")} else 0.0) + (1 - lam) * lp

    def eos(self, lam, v):
        return 0.0


LEX = Lex({
    ("ㄋㄧˇ",): [("你", -2.0), ("擬", -3.0)], ("ㄋㄚˋ",): [("那", -2.5)], ("ㄌㄧˇ",): [("李", -2.8)],
    ("ㄏㄠˇ",): [("好", -2.0)], ("ㄏㄨㄚˋ",): [("畫", -3.0)],
    ("ㄋㄧˇ", "ㄏㄠˇ"): [("你好", -2.2)], ("ㄋㄧˇ", "ㄏㄨㄚˋ"): [("擬畫", -6.0)],
    ("ㄍㄨㄛˇ", "ㄖㄣˊ"): [("國人", -4.0)],
    ("ㄓ",): [("之", -2.0)], ("ㄓㄤ",): [("張", -2.5)], ("ㄧㄠˋ",): [("要", -2.0)], ("ㄧㄠ",): [("腰", -3.0)],
})


class GeneratorTest(unittest.TestCase):
    def lines(self):
        return ROWS

    def test_same_seed_same_output_different_seed_differs(self):
        a = gen_abbrev.generate(self.lines(), "t", 0.5, seed=7)
        self.assertEqual(a, gen_abbrev.generate(self.lines(), "t", 0.5, seed=7))
        self.assertNotEqual(a, gen_abbrev.generate(self.lines(), "t", 0.5, seed=8))
        self.assertNotEqual(a, gen_abbrev.generate(self.lines(), "other", 0.5, seed=7))   # the set name is part of the seed

    def test_files_and_manifest_hashes_are_reproducible(self):
        import hashlib, json, subprocess
        with tempfile.TemporaryDirectory() as td:
            rows = os.path.join(td, "r.txt")
            open(rows, "w", encoding="utf-8").write("\n".join(ROWS) + "\nnot a row\n")
            hashes = []
            for k in (1, 2):
                out = os.path.join(td, f"o{k}")
                subprocess.run([sys.executable, os.path.join(HERE, "gen_abbrev.py"), "--rows", rows, "--name", "t", "--out-dir", out],
                               check=True, capture_output=True)
                man = json.load(open(os.path.join(out, "t.manifest.json")))
                self.assertEqual(man["rows"], 3)   # the non-3-field line is dropped, as lm_eval does
                for f, h in man["outputs"].items():
                    self.assertEqual(h, hashlib.sha256(open(os.path.join(out, f), "rb").read()).hexdigest())
                hashes.append(man["outputs"])
            self.assertEqual(hashes[0], hashes[1])
            self.assertEqual(sorted(hashes[0]), ["t.p0.2.txt", "t.p0.5.txt", "t.p1.0.txt"])

    def test_p0_changes_nothing_and_p1_abbreviates_every_syllable_with_an_initial(self):
        self.assertEqual(gen_abbrev.generate(ROWS, "t", 0.0), "\n".join(ROWS) + "\n")
        for row, line in zip(ROWS, gen_abbrev.generate(ROWS, "t", 1.0).splitlines()):
            ctx, sent, reading = row.split("|")
            c2, s2, r2 = line.split("|")
            self.assertEqual((ctx, sent), (c2, s2))
            for full, got in zip(reading.split(), r2.split()):
                self.assertEqual(got, abbrev.initial(full) or full)

    def test_syllables_without_an_initial_are_unchanged(self):
        for s in ["ㄧㄠˋ", "ㄨㄛˇ", "ㄩˋ", "ㄦˋ", "ㄚ", "ㄜˋ"]:
            self.assertIsNone(abbrev.initial(s))
            self.assertEqual(abbrev.abbreviate([s], [0.0], 1.0), [s])
        self.assertEqual(abbrev.abbreviate(["ㄔˊ", "ㄉㄠˋ"], [0.0, 0.0], 1.0), ["ㄔ", "ㄉ"])   # an initial-only syllable keeps its initial

    def test_p_levels_are_nested(self):
        rows = [r.split("|") for r in ROWS]
        draws = abbrev.draws_for(1, "t", [len(r[2].split()) for r in rows])
        for (_, _, r), d in zip(rows, draws):
            lo, hi = abbrev.abbreviate(r.split(), d, 0.2), abbrev.abbreviate(r.split(), d, 0.5)
            for s, a, b in zip(r.split(), lo, hi):
                self.assertTrue(a == s or b == a)   # abbreviated at 0.2 implies abbreviated at 0.5


class ExpandTest(unittest.TestCase):
    def setUp(self):
        self.ex = abbrev.Expander(LEX)

    def test_expansion_gives_only_lexicon_readings(self):
        self.assertEqual(self.ex.match(("ㄋ",)), [("ㄋㄚˋ",), ("ㄋㄧˇ",)])
        self.assertEqual(self.ex.match(("ㄋ", "ㄏ")), [("ㄋㄧˇ", "ㄏㄠˇ"), ("ㄋㄧˇ", "ㄏㄨㄚˋ")])
        self.assertEqual(self.ex.match(("ㄋㄧˇ", "ㄏ")), [("ㄋㄧˇ", "ㄏㄠˇ"), ("ㄋㄧˇ", "ㄏㄨㄚˋ")])
        self.assertEqual(self.ex.match(("ㄌ", "ㄏ")), [])   # ㄌㄧˇ ㄏㄠˇ is not a lexicon reading
        self.assertEqual(self.ex.match(("ㄅ",)), [])         # no lexicon syllable with that initial
        self.assertEqual(self.ex.match(("ㄍ",)), [])         # ㄍㄨㄛˇ only starts a longer reading: a prefix is not a key
        self.assertEqual(self.ex.match(("ㄍ", "ㄖ")), [("ㄍㄨㄛˇ", "ㄖㄣˊ")])
        for span in [("ㄋ",), ("ㄋ", "ㄏ"), ("ㄏ",)]:
            self.assertTrue(all(k in LEX.by_reading for k in self.ex.match(span)))

    def test_full_tokens_match_only_themselves(self):
        self.assertEqual(self.ex.match(("ㄋㄧˇ",)), [("ㄋㄧˇ",)])
        self.assertEqual(self.ex.match(("ㄧㄠ",)), [("ㄧㄠ",)])      # no initial, so it is never an abbreviation
        self.assertEqual(self.ex.match(("ㄧㄠˋ", "ㄏㄠˇ")), [])

    def test_bare_retroflex_token_is_an_abbreviation_that_includes_itself(self):
        self.assertEqual(self.ex.match(("ㄓ",)), [("ㄓ",), ("ㄓㄤ",)])

    def test_counters(self):
        self.assertEqual(self.ex.n_by_initial["ㄋ"], 2)   # ㄋㄧˇ ㄋㄚˋ
        self.assertEqual(self.ex.n_by_initial["ㄏ"], 2)


class DecodeTest(unittest.TestCase):
    def test_without_abbreviation_it_is_lm_decode(self):
        stats = collections.Counter()
        mine = abbrev.make_decode(stats)
        for syls in [("ㄋㄧˇ", "ㄏㄠˇ"), ("ㄋㄧˇ", "ㄏㄨㄚˋ", "ㄧㄠˋ"), ("ㄧㄠˋ",), ("ㄅㄅ",)]:
            for prof in ("chat", "formal"):
                self.assertEqual(mine(LEX, syls, FakeLM(), prof, start="你"), L.decode(LEX, syls, FakeLM(), prof, start="你"))
                self.assertEqual(mine(LEX, syls, FakeLM(), prof), L.decode(LEX, syls, FakeLM(), prof))
        self.assertEqual(stats["abbr_tokens"], 0)

    def test_every_path_is_a_lexicon_reading_that_fits_the_input(self):
        word_key = {w: k for k, v in LEX.by_reading.items() for w, _ in v}
        stats = collections.Counter()
        syls = ("ㄋ", "ㄏ", "ㄧㄠˋ")
        nb = abbrev.make_decode(stats)(LEX, syls, FakeLM(), "chat")
        self.assertTrue(nb)
        for _, ws in nb:
            read = [s for w in ws for s in word_key[w]]
            self.assertEqual(len(read), 3)
            self.assertTrue(read[0].startswith("ㄋ") and read[1].startswith("ㄏ") and read[2] == "ㄧㄠˋ")
        self.assertEqual("".join(nb[0][1]), "你好要")   # the language model picks 你好 over 擬畫 and the single-character paths
        self.assertEqual(stats["abbr_tokens"], 2)
        self.assertEqual(stats["readings_sum"], 4)   # 2 + 2 lexicon syllables
        self.assertGreater(stats["entries_tried"], 0)

    def test_abbreviated_input_still_contains_the_full_input_answer(self):
        full = {"".join(ws) for _, ws in L.decode(LEX, ("ㄋㄧˇ", "ㄏㄠˇ"), FakeLM(), "chat")}
        abbr = {"".join(ws) for _, ws in abbrev.make_decode(collections.Counter())(LEX, ("ㄋ", "ㄏ"), FakeLM(), "chat")}
        self.assertLessEqual(full, abbr)


class DecideTest(unittest.TestCase):
    def line(self, td, name, top1, n=302):
        p = os.path.join(td, name)
        open(p, "w").write(f"## dev302  lm-chat  {{'n': {n}, 'top1': {top1}, 'oracle@64': 1, 'top1_sha256': 'x'}}\n#E3 abbrev=on\n")
        return p

    def test_threshold_is_per_setting_and_inclusive(self):
        with tempfile.TemporaryDirectory() as td:
            f = self.line(td, "f", 200)
            ok, _ = decide.decide({"chat": (f, self.line(td, "a", 180)), "formal": (f, self.line(td, "b", 181))})
            self.assertTrue(ok)                                   # 180 / 200 = 0.9 exactly
            ok, msgs = decide.decide({"chat": (f, self.line(td, "c", 179)), "formal": (f, self.line(td, "d", 200))})
            self.assertFalse(ok)                                  # one setting below is a FAIL
            self.assertIn("<", msgs[0])
            with self.assertRaises(ValueError):
                decide.decide({"chat": (f, self.line(td, "e", 180, n=300))})   # different sets


if __name__ == "__main__":
    unittest.main()
