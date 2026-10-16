"""Tests of a1_table.py, level1.py and buckets.py (contract docs/contracts/reading-aware-0.md 4.1). Synthetic lexicons, plus the shipped
data/lexicon (a repo file) for the contract's own claims about the 44 characters and the 8 level-1 characters."""
import contextlib
import io
import os
import tempfile
import unittest

import a1_table
import buckets
import level1
import rcommon
import testkit
import units
import lm as L

LEX = """ㄅㄚ˙ 巴 -2.0
ㄅㄚ 巴 -2.0
ㄅㄚ˙ 吧 -2.0
ㄅㄚ 吧 -2.0
ㄇㄚ˙ 嗎 -2.0
ㄇㄚ 嗎 -2.2
ㄇㄚˇ 嗎 -3.0
ㄇㄚ˙ 嘛 -2.0
ㄇㄚˊ 嘛 -2.5
ㄛ 喔 -3.0
ㄛˊ 喔 -3.5
ㄋㄚ˙ 哪 -4.0
ㄋㄚˇ 哪 -2.0
ㄋㄚ 哪 -4.5
ㄏㄠˇ 好 -1.5
ㄏㄠˇ 你 -1.0
ㄈㄥ 風 -2.0
ㄈㄥ˙ 風 -2.0
ㄓㄜ˙ 著 -2.0
"""
WORDS = LEX + "ㄏㄠˇ-ㄅㄚ˙ 好吧 -2.0\n"          # 好吧 is one word that beats 好 + 吧 (-3.5)


class A1Table(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.lex = testkit.lexicon(self.td.name, LEX)

    def test_only_the_dot_rows_of_the_listed_characters_at_delta(self):
        rows = a1_table.rows(self.lex, 1.25, "巴風")
        self.assertEqual(rows, [("ㄅㄚ˙", "巴", "1.25", "reading-neutral", "reading-aware-0"), ("ㄈㄥ˙", "風", "1.25", "reading-neutral", "reading-aware-0")])
        self.assertEqual([r[1] for r in a1_table.rows(self.lex, chars="吧")], ["吧"])   # unlisted 嗎 嘛 never appear
        self.assertEqual(a1_table.rows(self.lex, chars="巴")[0][2], "1.0")                    # default delta

    def test_a_listed_character_without_both_kinds_of_reading_stops_the_run(self):
        for c in ("好", "著", "喔", "無"):      # no ˙ / only ˙ / no ˙ / not in the lexicon
            with self.subTest(c), self.assertRaisesRegex(ValueError, "needs both"):
                a1_table.rows(self.lex, chars=c)
        with self.assertRaisesRegex(ValueError, "twice"):
            a1_table.rows(self.lex, chars="巴巴")

    def test_the_written_table_loads_like_the_engine_loads_it(self):
        path = os.path.join(self.td.name, "a1.tsv")
        self.assertEqual(a1_table.write_checked(path, self.lex, 1.0, "巴風"), 2)
        self.assertEqual(L.load_demote(path, self.lex), {("ㄅㄚ˙", "巴"): 1.0, ("ㄈㄥ˙", "風"): 1.0})
        other = testkit.lexicon(self.td.name, LEX.replace("ㄅㄚ˙ 巴 -2.0\n", ""), "other.txt")
        with self.assertRaisesRegex(ValueError, "no lexicon entry"):
            L.load_demote(path, other)

    def test_the_shipped_lexicon_has_all_44_and_the_table_has_44_rows(self):
        self.assertEqual(len(a1_table.DEMOTE_44), 44)
        real = rcommon.load_lexicon()
        path = os.path.join(self.td.name, "real.tsv")
        self.assertEqual(a1_table.write_checked(path, real, 1.0), 44)
        table = L.load_demote(path, real)
        self.assertEqual({w for _, w in table}, set(a1_table.DEMOTE_44))
        self.assertTrue(all(rcommon.is_dot(r.split("-")[-1]) and d == 1.0 for (r, _), d in table.items()))
        self.assertIn(("ㄅㄚ˙", "巴"), table)
        for c in "的了著麼得地吧啊呀啦嗎呢嘛哪哩嘞唄嗯們個下子頭":
            self.assertNotIn(c, a1_table.DEMOTE_44)

    def test_cli(self):
        path = os.path.join(self.td.name, "cli.tsv")
        with contextlib.redirect_stdout(io.StringIO()):
            a1_table.main(["--out", path, "--delta", "1.5"])
        rows = [l.split("\t") for l in open(path, encoding="utf-8") if not l.startswith("#")]
        self.assertEqual((len(rows), {r[2] for r in rows}, {r[3] for r in rows}), (44, {"1.5"}, {"reading-neutral"}))


class Level1(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.lex = testkit.lexicon(self.td.name, LEX)

    def test_level1_rows_are_the_one_tone_reading_of_the_dot_syllable_only(self):
        # 吧 ㄅㄚ, 嗎 ㄇㄚ (not ㄇㄚˇ); 嘛 has ㄇㄚˊ instead; 喔 has no ˙; 哪 is not in the whitelist even with ˙ and a one-tone reading
        self.assertEqual(level1.level1(self.lex), {("ㄅㄚ", "吧"): "ㄅㄚ˙", ("ㄇㄚ", "嗎"): "ㄇㄚ˙"})
        self.assertEqual(level1.tone_rows(self.lex), [("ㄅㄚ", "吧"), ("ㄇㄚ", "嗎")])

    def test_the_shipped_lexicon_has_the_contracts_eight_characters(self):
        real = rcommon.load_lexicon()
        self.assertEqual({c for _, c in level1.level1(real)}, set("吧嗎呢啦啊呀嘞哩"))
        self.assertEqual(level1.level1(real)[("ㄇㄚ", "嗎")], "ㄇㄚ˙")
        self.assertNotIn(("ㄇㄚˇ", "嗎"), level1.level1(real))

    def test_flip_changes_only_standalone_dot_particles_to_one_tone(self):
        lex = self.lex
        self.assertEqual(level1.flip("好吧", ["ㄏㄠˇ", "ㄅㄚ˙"], lex), ["ㄏㄠˇ", "ㄅㄚ"])
        self.assertEqual(level1.flip("吧嗎", ["ㄅㄚ˙", "ㄇㄚ˙"], lex), ["ㄅㄚ", "ㄇㄚ"])       # every changeable position in one row
        self.assertEqual(level1.flip("吧嗎", ["ㄅㄚ˙", "ㄇㄚ"], lex), ["ㄅㄚ", "ㄇㄚ"])         # a position that is already one-tone stays
        self.assertIsNone(level1.flip("吧嗎", ["ㄅㄚ", "ㄇㄚ"], lex))                           # nothing to change: row skipped
        self.assertIsNone(level1.flip("好你", ["ㄏㄠˇ", "ㄏㄠˇ"], lex))
        self.assertIsNone(level1.flip("嘛", ["ㄇㄚ˙"], lex))                                    # 嘛 is not level 1
        self.assertIsNone(level1.flip("哪", ["ㄋㄚ˙"], lex))                                    # 哪 is not on the whitelist
        self.assertIsNone(level1.flip("喔", ["ㄛ"], lex))

    def test_a_particle_inside_a_word_is_not_standalone(self):
        lex = testkit.lexicon(self.td.name, WORDS, "words.txt")      # 好吧 (-2.0) beats 好 + 吧 (-3.5)
        self.assertIsNone(level1.flip("好吧", ["ㄏㄠˇ", "ㄅㄚ˙"], lex))
        self.assertEqual(level1.flip("吧", ["ㄅㄚ˙"], lex), ["ㄅㄚ"])
        self.assertEqual(level1.analyse("好吧", ["ㄏㄠˇ", "ㄅㄚ˙"], lex), ([], []))

    def test_probe_counts_and_lines(self):
        rows = [("好吧", ["ㄏㄠˇ", "ㄅㄚ˙"], "你好"), ("吧", ["ㄅㄚ"], ""), ("好你", ["ㄏㄠˇ", "ㄏㄠˇ"], ""), ("吧嗎", ["ㄅㄚ˙", "ㄇㄚ"], "x")]
        lines, idx, already, n = level1.probe(rows, self.lex)
        self.assertEqual(lines, ["你好|好吧|ㄏㄠˇ ㄅㄚ\n", "x|吧嗎|ㄅㄚ ㄇㄚ\n"])
        self.assertEqual((idx, already, n), ([1, 4], 2, 4))       # rows 2 and 4 already have a one-tone particle; rows 1 and 4 got a probe row

    def test_probe_cli_writes_the_files_outside_the_repo(self):
        rows = os.path.join(self.td.name, "r.txt")
        open(rows, "w", encoding="utf-8").write("|吧|ㄅㄚ˙\n|吧|ㄅㄚ\n|巴|ㄅㄚ˙\n")
        out, idx = os.path.join(self.td.name, "p.txt"), os.path.join(self.td.name, "i.txt")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            level1.main(["probe", "--rows", rows, "--out", out, "--idx", idx])
        self.assertEqual(buf.getvalue(), f"{rows}\t3\t1\t1\n")       # rows, rows already one-tone (row 2), probe rows (row 1)
        self.assertEqual(open(out, encoding="utf-8").read(), "|吧|ㄅㄚ\n")
        self.assertEqual(open(idx).read(), "1\n")


    def test_onetone_cli_lists_the_rows_whose_gold_is_already_one_tone(self):
        rows, idx = os.path.join(self.td.name, "r2.txt"), os.path.join(self.td.name, "i2.txt")
        open(rows, "w", encoding="utf-8").write("|吧|ㄅㄚ˙\n|吧|ㄅㄚ\n|巴|ㄅㄚ\n|吧嗎|ㄅㄚ˙ ㄇㄚ\n")
        with contextlib.redirect_stdout(io.StringIO()):
            level1.main(["onetone", "--rows", rows, "--idx", idx])
        self.assertEqual(open(idx).read(), "2\n4\n")


class Buckets(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.lex = testkit.lexicon(self.td.name, LEX + "ㄨㄟˇ-ㄅㄚ˙ 尾巴 -3.0\nㄓㄤˇ 長 -2.5\nㄔㄤˊ 長 -2.0\n")
        self.l1 = level1.level1(self.lex)
        self.lab = units.Labeller(rcommon.char_readings(self.lex))

    def classify(self, rows, firsts):
        return buckets.classify(rows, dict(enumerate(firsts, 1)), self.lex, self.lab, self.l1)

    def test_level_2_the_dot_reading_of_a_listed_character_standing_alone(self):
        c = self.classify([("吧", ["ㄅㄚ˙"], "")], ["巴"])          # wrong, the first candidate 巴 ㄅㄚ˙ stands where the gold 吧 is
        self.assertEqual((c["wrong"], c["i"], c["i_err"], c["ii"]), (1, 1, 1, 0))
        c = self.classify([("巴", ["ㄅㄚ˙"], "")], ["巴"])          # right: nothing counted
        self.assertEqual((c["wrong"], c["i"]), (0, 0))
        c = self.classify([("吧", ["ㄅㄚ"], "")], ["巴"])           # 巴 read ㄅㄚ (one tone) is not the ˙ reading
        self.assertEqual((c["wrong"], c["i"], c["ii"]), (1, 0, 0))

    def test_a_character_inside_a_lexicon_word_is_not_standalone(self):
        c = self.classify([("尾吧", ["ㄨㄟˇ", "ㄅㄚ˙"], "")], ["尾巴"])    # 尾巴 is the only cut of these readings
        self.assertEqual((c["wrong"], c["i"]), (1, 0))

    def test_level_3_a_reading_with_another_consonant_or_vowel_than_the_primary(self):
        c = self.classify([("漲", ["ㄓㄤˇ"], "")], ["長"])          # primary of 長 is ㄔㄤˊ (best score); ㄓㄤˇ differs in the consonant
        self.assertEqual((c["wrong"], c["ii"], c["ii_err"], c["i"]), (1, 1, 1, 0))
        c = self.classify([("媽", ["ㄇㄚˇ"], "")], ["嗎"])          # primary ㄇㄚ˙: same sound, another tone: not level 3
        self.assertEqual((c["wrong"], c["ii"]), (1, 0))
        c = self.classify([("漲", ["ㄔㄤˊ"], "")], ["長"])          # the primary reading itself
        self.assertEqual((c["wrong"], c["ii"]), (1, 0))

    def test_level_1_counts_all_rows_whose_gold_has_the_one_tone_particle(self):
        rows = [("吧", ["ㄅㄚ"], ""), ("吧", ["ㄅㄚ"], ""), ("吧", ["ㄅㄚ˙"], ""), ("好", ["ㄏㄠˇ"], "")]
        c = self.classify(rows, ["吧", "巴", "吧", "好"])
        self.assertEqual((c["n"], c["wrong"], c["iii"], c["iii_wrong"]), (4, 1, 2, 1))

    def test_dump_reader_keeps_rank_one(self):
        dump = os.path.join(self.td.name, "d.dump")
        open(dump, "w", encoding="utf-8").write("1\t1\t巴\t-1.0\n1\t2\t吧\t-1.1\n2\t1\t吧\t-2.0\n")
        self.assertEqual(buckets.read_first(dump), {1: "巴", 2: "吧"})

    def test_cli_prints_one_line_per_set_and_checks_the_row_count(self):
        rows, dump = os.path.join(self.td.name, "r.txt"), os.path.join(self.td.name, "d.dump")
        open(rows, "w", encoding="utf-8").write("|吧|ㄅㄚ˙\n|吧|ㄅㄚ\n")
        open(dump, "w", encoding="utf-8").write("1\t1\t巴\t-1.0\n2\t1\t吧\t-1.0\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            buckets.main(["--label", "chat", "--set", "t", rows, dump])
        lines = buf.getvalue().splitlines()
        self.assertEqual(lines[0].split("\t")[:3], ["label", "set", "n"])
        self.assertEqual(lines[1].split("\t")[:5], ["chat", "t", "2", "1", "1"])    # n 2, wrong 1, i 1
        open(dump, "w", encoding="utf-8").write("1\t1\t巴\t-1.0\n")
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            buckets.main(["--label", "chat", "--set", "t", rows, dump])


if __name__ == "__main__":
    unittest.main()
