"""Tests for experiments/derisk-1015/common (synthetic data only).
Run: python3 -W ignore -m unittest experiments/derisk-1015/common/test_common.py -v   (from the repo root)
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import anchor  # noqa: E402
import concat_rowstats as cr  # noqa: E402
import setnames  # noqa: E402
from evalstats import compare  # noqa: E402  (concat_rowstats put tools/ on sys.path)

LINE = ("## dev302  lm-chat+ctx+kn:cbefbced:θ1:β1  "
        "{'n': 302, 'top1': 241, 'oracle@64': 290, 'top1_sha256': '75c0ff7c1f6fd64db3f5bfe565daef0cd0a03b1a18338935d877dbd49d5330cc'}")


def write(td, name, text):
    p = os.path.join(td, name)
    open(p, "w", encoding="utf-8").write(text)
    return p


class ConcatTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.a = write(self.td.name, "a", "1\t1\t0\t5\n2\t0\t2\t6\n")
        self.b = write(self.td.name, "b", "1\t0\t1\t4\n2\t1\t0\t7\n3\t1\t0\t3\n")

    def tearDown(self):
        self.td.cleanup()

    def test_renumbers_from_one_in_the_given_order(self):
        self.assertEqual(cr.concat([self.a, self.b]),
                         "1\t1\t0\t5\n2\t0\t2\t6\n3\t0\t1\t4\n4\t1\t0\t7\n5\t1\t0\t3\n")
        self.assertEqual(cr.concat([self.b, self.a]).splitlines()[0], "1\t0\t1\t4")   # the order is the caller's

    def test_same_order_on_both_sides_is_accepted_by_evalstats(self):
        base = write(self.td.name, "base", cr.concat([self.a, self.b]))
        cand = write(self.td.name, "cand", cr.concat([self.a, self.b]))
        self.assertIn("| 5 | 3 | 3 |", compare(base, cand))   # n=5, top1 3 vs 3
        swapped = write(self.td.name, "swapped", cr.concat([self.b, self.a]))
        with self.assertRaisesRegex(ValueError, "gold lengths differ"):   # different order on the two sides is caught
            compare(base, swapped)

    def test_rejects_malformed_input(self):
        bad = {"cols": "1\t1\t0\n", "nondigit": "1\t1\tx\t5\n", "misnumbered": "1\t1\t0\t5\n3\t1\t0\t5\n",
               "ok2": "1\t2\t0\t5\n", "negative": "1\t1\t-1\t5\n", "text": "1\t1\t0\t5\t好\n"}
        for k, text in bad.items():
            with self.subTest(k):
                with self.assertRaises(ValueError):
                    cr.concat([self.a, write(self.td.name, k, text)])
        with self.assertRaises(ValueError):
            cr.concat([write(self.td.name, "empty", "")])
        with self.assertRaises(ValueError):
            cr.concat([])

    def test_cli_exit_code_and_no_output_on_failure(self):
        out = os.path.join(self.td.name, "out")
        with self.assertRaises(SystemExit) as e:
            cr.main(["concat", out, self.a, write(self.td.name, "bad", "oops\n")])
        self.assertEqual(e.exception.code, 1)
        self.assertFalse(os.path.exists(out))
        cr.main(["concat", out, self.a, self.b])
        self.assertEqual(open(out).read(), cr.concat([self.a, self.b]))

    def test_select_keeps_and_renumbers(self):
        self.assertEqual(cr.select(self.b, [1, 3]), "1\t0\t1\t4\n2\t1\t0\t3\n")
        for idx in ([3, 1], [1, 1], [0], [4]):
            with self.subTest(idx):
                with self.assertRaises(ValueError):
                    cr.select(self.b, idx)


class AnchorTest(unittest.TestCase):
    def test_identical_lines_and_extra_lines_are_ignored(self):
        self.assertIsNone(anchor.first_diff(LINE + "\n#E3 abbrev=off n=302\n", "noise\n" + LINE + "\n"))

    def test_one_character_difference_is_found_in_each_field(self):
        sha = LINE.replace("75c0", "75c1")
        self.assertIn("top1_sha256", anchor.first_diff(sha, LINE))
        self.assertIn("top1", anchor.first_diff(LINE.replace("241", "242"), LINE))
        self.assertIn("profile tags", anchor.first_diff(LINE.replace("β1", "β2"), LINE))
        self.assertIn("name", anchor.first_diff(LINE.replace("dev302", "dev303"), LINE))

    def test_reports_the_first_difference_and_count_mismatch(self):
        two = LINE + "\n" + LINE.replace("chat", "formal")
        other = LINE.replace("241", "240") + "\n" + LINE.replace("chat", "formal").replace("302", "301")
        self.assertTrue(anchor.first_diff(two, other).startswith("line 1: top1"))
        self.assertIn("count differs", anchor.first_diff(two, LINE))
        self.assertIn("no summary line", anchor.first_diff("", ""))

    def test_unparsable_line_falls_back_to_character_position(self):
        self.assertIn("character at 5", anchor.first_diff("## abcd", "## abXd"))


class SetNamesTest(unittest.TestCase):
    def test_order_is_the_contract_order(self):
        self.assertEqual(setnames.ORDER, ["cvtune", "wikitune", "dev302", "typing76", "user-reported", "lytune", "wikitypo"])

    def test_e4_order_appends_the_e4ctx_sets_after_the_judging_sets(self):
        self.assertEqual(setnames.E4_ORDER, setnames.ORDER + ["e4ctx-punct", "e4ctx-alnum"])
        path, pin = setnames.location("e4ctx-alnum")
        self.assertTrue(path.endswith("e4ctx-alnum.txt"))
        self.assertIsNone(pin)

    def test_locations_come_from_bench_and_user_reported_is_unpinned(self):
        for n in setnames.ORDER:
            path, pin = setnames.location(n)
            self.assertTrue(path.endswith(".txt"))
            self.assertEqual(pin is None, n == "user-reported")


if __name__ == "__main__":
    unittest.main()
