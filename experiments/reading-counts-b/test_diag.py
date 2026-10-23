"""Tests for diag.py on synthetic dumps. Run: python3 -m unittest test_diag (in this directory)."""
import contextlib, io, os, shutil, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diag as D  # noqa: E402

# two rows over the syllables a b c; row 1 truth 甲乙丙, row 2 truth 丁乙
ROWS = D.parse_rows("|甲乙丙|a b c\nbad line|x\n前|丁乙|d b\n")
DUMP = "1\t1\t甲丁丙\t-1.0\n1\t2\t甲乙丙\t-1.5\n1\t3\t戊乙丙\t-4.0\n2\t1\t丁乙\t-2.0\n2\t2\t丁己\t-2.25\n"
WORDS = "1\t0\t甲乙 丙\n1\t1\t甲 丁 丙\n1\t2\t甲 乙 丙\n1\t3\t戊乙 丙\n2\t0\t丁 乙\n2\t1\t丁 乙\n2\t2\t丁 己\n"
TABLE = {("丁", "b"): -2.0, ("乙", "b"): -0.5, ("丙", "c"): 0.0, ("甲", "a"): -1.0, ("乙", "d"): -3.0}
EXACT = lambda x, y: x == y


class Parse(unittest.TestCase):
    def test_rows_skip_non_three_field_lines(self):
        self.assertEqual(ROWS, [("甲乙丙", ["a", "b", "c"]), ("丁乙", ["d", "b"])])

    def test_singles(self):
        self.assertEqual(D.singles(["甲", "乙", "丙"], ["a", "b", "c"]), [("甲", "a"), ("乙", "b"), ("丙", "c")])
        self.assertEqual(D.singles(["戊乙", "丙"], ["a", "b", "c"]), [("丙", "c")])           # a two-character word is no single
        self.assertIsNone(D.singles(["甲", "乙"], ["a", "b", "c"]))                          # does not cover the row


class Ignition(unittest.TestCase):
    def test_counts_gaps_and_zero_terms(self):
        out, skipped = D.ignition(ROWS, DUMP, WORDS, TABLE, EXACT)
        self.assertEqual(skipped, 0)
        by = {p: (r, w, gr, gw) for p, r, w, gr, gw, _ in out}
        # 甲 a: in candidates 1 (wrong, gap 0), 2 (correct, gap 0.5), 3 not (戊乙 is a two-character word)
        self.assertEqual(by["甲", "a"], (1, 1, 0.5, 0.0))
        # 丁 b: row 1 candidate 1 has 丁 at position 2 (syllable b): wrong, gap 0; row 2 reads 丁 as d, not in the table
        self.assertEqual(by["丁", "b"], (0, 1, None, 0.0))
        # 乙 b: row 1 candidate 2 correct (gap 0.5), row 2 candidate 1 correct (gap 0)
        self.assertEqual(by["乙", "b"], (2, 0, 0.25, None))
        self.assertNotIn(("丙", "c"), by)                                                    # t == 0: not an ignition
        self.assertNotIn(("乙", "d"), by)                                                    # not the row's reading
        self.assertEqual(out[0][0], ("乙", "b"))                                             # most frequent first

    def test_top_and_mismatch(self):
        out, _ = D.ignition(ROWS, DUMP, WORDS, TABLE, EXACT, top=1)
        self.assertEqual(len(out), 1)
        with self.assertRaises(ValueError):
            D.ignition(ROWS, DUMP, WORDS.replace("甲 丁 丙", "甲 丁 乙"), TABLE, EXACT)

    def test_uncovered_candidate_is_skipped(self):
        _, skipped = D.ignition(ROWS, DUMP.replace("丁己", "丁"), WORDS.replace("2\t2\t丁 己", "2\t2\t丁"), TABLE, EXACT)
        self.assertEqual(skipped, 1)


class Exposure(unittest.TestCase):
    def test_gold_single_positions_with_negative_t(self):
        out = D.exposure(ROWS, WORDS, TABLE)
        # row 1 gold 甲乙 丙: 甲乙 is one word, 丙 has t 0; row 2 gold 丁 乙: 丁 b? no, 丁 is read d: not in the table; 乙 b -0.5
        self.assertEqual(out, [(2, "乙", "b", -0.5)])
        out = D.exposure(ROWS, WORDS.replace("1\t0\t甲乙 丙", "1\t0\t甲 乙 丙"), TABLE)
        self.assertEqual(out, [(1, "甲", "a", -1.0), (1, "乙", "b", -0.5), (2, "乙", "b", -0.5)])
        self.assertEqual(D.exposure(ROWS, WORDS.replace("1\t0\t甲乙 丙", "1\t0\t-"), TABLE), [(2, "乙", "b", -0.5)])   # no gold split


class Delta(unittest.TestCase):
    def test_threshold_and_missing_pairs(self):
        t0, t1 = {("甲", "a"): 0.0, ("乙", "b"): -1.0, ("丙", "c"): -0.2, ("丁", "d"): -0.5}, {("甲", "a"): -0.5, ("乙", "b"): -1.25, ("丙", "c"): -0.6, ("戊", "e"): -0.5}
        got = {(c, r) for c, r, _, _ in D.delta(t0, t1)}
        self.assertEqual(got, {("甲", "a"), ("丁", "d"), ("戊", "e")})                          # 乙 moves 0.25, 丙 0.4
        self.assertEqual({(c, r) for c, r, _, _ in D.delta(t0, t1, 0.25)}, got | {("乙", "b"), ("丙", "c")})


class Main(unittest.TestCase):
    def test_cli_prints_no_sentence(self):
        d = tempfile.mkdtemp()
        try:
            def w(n, text):
                with open(os.path.join(d, n), "w", encoding="utf-8") as f:
                    f.write(text)
                return os.path.join(d, n)
            rows = w("rows", "|甲乙丙|a b c\n前|丁乙|d b\n")
            table = w("t", "".join(f"{c}\t{r}\t{t!r}\n" for (c, r), t in TABLE.items()))
            args = {"ig": ["ignition", "--rows", rows, "--dump", w("d", DUMP), "--words", w("w", WORDS), "--table", table, "--exact"],
                    "ex": ["exposure", "--rows", rows, "--words", os.path.join(d, "w"), "--table", table],
                    "de": ["delta", "--t0", table, "--t1", table]}
            for k, a in args.items():
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(D.main(a), 0)
                self.assertNotIn("甲乙丙", buf.getvalue())
                self.assertNotIn("丁乙", buf.getvalue())
            self.assertTrue(buf.getvalue().startswith("pairs 0"))
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
