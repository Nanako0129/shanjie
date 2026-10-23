"""Tests for sample_single.py. Run: python3 -m unittest test_sample_single (in this directory). The lexicon tests read the repo's real lexicon."""
import collections, contextlib, io, os, random, shutil, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sample_single as S  # noqa: E402


def run(argv):
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        rc = S.main(argv)
    return rc, buf.getvalue()


class Classes(unittest.TestCase):
    def test_tone_only_differences_are_tone_class(self):
        self.assertEqual(S.classify(["ㄍㄜˋ", "ㄍㄜ˙"]), "tone")
        self.assertEqual(S.classify(["ㄇㄚ", "ㄇㄚ˙", "ㄇㄚˇ"]), "tone")
        self.assertEqual(S.classify(["ㄉㄜ˙", "ㄉㄜˊ", "ㄉㄧˊ"]), "seg")   # the final differs
        self.assertEqual(S.classify(["ㄏㄜˊ", "ㄏㄨㄛˋ"]), "seg")

    def test_the_registered_split_of_the_201_characters_on_the_real_lexicon(self):
        chars = S.load_chars(S.DEFAULT_CHARS)
        readings, _ = S.load_merged(chars)
        self.assertEqual(len(chars), 201)
        self.assertTrue(all(len(readings[c]) >= 2 for c in chars))
        self.assertEqual(collections.Counter(S.classify(readings[c]) for c in chars), {"tone": 89, "seg": 112})

    def test_examples_are_multi_character_words_reading_the_character_that_way(self):
        _, ex = S.load_merged(["個"])
        self.assertIn("個人", [w for _, w in ex["個", "ㄍㄜˋ"]])
        self.assertTrue(all(len(w) >= 2 and "個" in w for v in ex.values() for _, w in v))


class Primary(unittest.TestCase):
    R = {"甲": ["a", "b"], "乙": ["c", "d", "e"], "丙": ["f", "g"]}

    def test_t0_zero_reading_wins_and_ties_use_the_tiebreak(self):
        t0 = {("甲", "a"): -0.3, ("甲", "b"): 0.0, ("乙", "c"): 0.0, ("乙", "d"): 0.0, ("乙", "e"): -1.0, ("丙", "f"): 0.0, ("丙", "g"): 0.0}
        p = S.primary_readings(self.R, t0, tiebreak=lambda c: {"乙": "d", "丙": "zzz"}[c])
        self.assertEqual(p, {"甲": "b", "乙": "d", "丙": "f"})   # a tiebreak outside the tie falls back to the first of the tie
        self.assertEqual(S.primary_readings(self.R, None, tiebreak=lambda c: self.R[c][-1]), {"甲": "b", "乙": "e", "丙": "g"})


class Reservoir(unittest.TestCase):
    def test_same_draw_as_sample_labels_reservoir(self):
        items = [(f"s{i}", i) for i in range(500)]
        r = S.Reservoirs(["x"], 20, 20261023)
        for it in items:
            r.add("x", it)
        self.assertEqual(r.keep["x"], S.SL.reservoir(iter(items), 20, 20261023))
        self.assertEqual(len(r.keep["x"]), 20)

    def test_fewer_items_than_n_keeps_all(self):
        r = S.Reservoirs(["x"], 5, 1)
        for i in range(3):
            r.add("x", i)
        self.assertEqual(r.keep["x"], [0, 1, 2])


class Standalone(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lex = S.cut_lexicon()

    def test_a_character_inside_a_word_is_not_standalone(self):
        self.assertEqual(S.standalone("我個人覺得很好", self.lex, {"我", "個", "覺"}), [(0, "我")])
        self.assertEqual(S.standalone("hello", self.lex, {"我"}), [])
        self.assertEqual(S.standalone("我", self.lex, {"我"}), [])   # a run of one is not a cut (sample_labels.candidates)

    def test_positions_after_a_non_han_token_are_sentence_positions(self):
        self.assertEqual(S.standalone("ab我個人", self.lex, {"我"}), [(2, "我")])


class Sample(unittest.TestCase):
    def test_deterministic_unique_ids_and_per_limit(self):
        d = tempfile.mkdtemp()
        try:
            chars = os.path.join(d, "chars.txt")
            with open(chars, "w", encoding="utf-8") as f:
                f.write("我\n")
            text = os.path.join(d, "colloquial-train.txt")
            open(text, "w", encoding="utf-8").write("".join(f"我個人覺得第{i}個很好\n" for i in range(30)) + "no han\ttab我個人\n")
            outs = []
            lines = lambda a: (("t", l.rstrip("\n")) for l in open(text, encoding="utf-8"))   # the conversion tables are not this test's business
            for k in (1, 2):
                out = os.path.join(d, f"p{k}.tsv")
                with mock.patch.object(S.SL, "sentences", side_effect=lines):
                    rc, msg = run(["sample", "--chars", chars, "--text", text, "--per", "7", "--out", out])
                self.assertEqual(rc, 0)
                with open(out, encoding="utf-8") as f:
                    outs.append(f.read())
            self.assertEqual(outs[0], outs[1])
            rows = [l.split("\t") for l in outs[0].splitlines()]
            self.assertEqual(len(rows), 7)
            self.assertEqual({r[1] for r in rows}, {"text-colloquial-train"})
            self.assertEqual(len({r[0] for r in rows}), 7)
            self.assertTrue(all(r[0].startswith("text-colloquial-train-6211-") and r[2][int(r[3])] == "我" and r[4] == "我" for r in rows))
            self.assertIn("1 skipped for a tab", msg)
            with self.assertRaises(SystemExit):   # --out exists
                run(["sample", "--chars", chars, "--text", text, "--out", out])
        finally:
            shutil.rmtree(d)


class Human(unittest.TestCase):
    def test_human_and_tool_readings_at_standalone_positions(self):
        d = tempfile.mkdtemp()
        try:
            chars = os.path.join(d, "chars.txt")
            with open(chars, "w", encoding="utf-8") as f:
                f.write("我\n")
            rows = os.path.join(d, "tiny.txt")
            open(rows, "w", encoding="utf-8").write("|我個人覺得|ㄨㄛˇ ㄍㄜˋ ㄖㄣˊ ㄐㄩㄝˊ ㄉㄜ˙\n|我很好|ㄨㄛˇ ㄏㄣˇ ㄏㄠˇ\nctx|我個人覺得|ㄨㄛˇ\n")
            out = os.path.join(d, "h.tsv")
            fake = {"我個人覺得": ["ㄨㄛˇ", "ㄍㄜˋ", "ㄖㄣˊ", "ㄐㄩㄝˊ", "ㄉㄜˊ"], "我很好": ["ㄨㄛˇ", "ㄏㄣˇ", "ㄏㄠˇ"]}
            with mock.patch.object(S, "tool_readings", return_value=fake):
                rc, msg = run(["human", "--chars", chars, "--rows", rows, "--out", out])
            got = [l.split("\t") for l in open(out, encoding="utf-8").read().splitlines()]
            self.assertEqual(got, [["tiny-1-0", "tiny", "我個人覺得", "0", "我", "ㄨㄛˇ", "ㄨㄛˇ"], ["tiny-2-0", "tiny", "我很好", "0", "我", "ㄨㄛˇ", "ㄨㄛˇ"]])
            self.assertIn("1 skipped", msg)   # the third row: one syllable for five characters
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
