"""judge-v2 的 Python 參考實作（eval.py 的 Forms、load_accept、judge2；docs/spec/evaluation.md §3.5–§3.7），案例同 core/src/tests.rs 的
forms_v2_extra_pairs_join_one_longest_match、accept_file_parse_and_check、judge_v2_layers。"""
import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval as E  # noqa: E402

BASE = "# h\nword\t甲乙\t丙丁\nchar\t濕\t溼\tㄕ\n"


def forms(extra=None):
    f = E.Forms().parse(BASE)
    return f.add_extra(extra) if extra is not None else f


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class FormsV2(unittest.TestCase):
    def test_extra_pairs_join_one_longest_match(self):
        f = forms("# h\nword\t甲乙戊\t己己己\tㄧ ㄧ ㄧ\t3/7\t字條 0\n")
        self.assertEqual(f.apply("甲乙戊", ["a", "b", "c"]), "己己己")
        self.assertEqual(f.apply("甲乙", ["a", "b"]), "丙丁")
        self.assertEqual(forms().apply("甲乙戊", ["a", "b", "c"]), "丙丁戊")
        self.assertEqual(f.apply("濕", ["ㄕ"]), "溼")
        self.assertEqual(f.apply("濕", ["ㄒㄧˊ"]), "濕")
        self.assertEqual(forms("# header only\n").apply("甲乙", ["a", "b"]), "丙丁")

    def test_bad_extra_lines(self):
        for bad in ["word\t甲\t乙\tㄧ\t1/1\n", "word\t甲\t乙乙\tㄧ\t1/1\t字條\n", "word\t甲\t乙\t\t1/1\t字條\n", "word\t甲\t乙\tㄧ\t1\t字條\n",
                    "word\t甲\t乙\tㄧ\ta/1\t字條\n", "word\t甲\t乙\tㄧ\t1/1\t\n", "char\t甲\t乙\tㄧ\t1/1\t字條\n", "word\t甲乙\t戊戊\tㄧ ㄧ\t1/1\t字條\n",
                    "word\t甲\t乙\tㄧ\t1/1\t字條\nword\t甲\t丙\tㄧ\t1/1\t字條\n", "\n"]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                forms(bad)

    def test_bad_forms_lines(self):
        for bad in ["word\t甲\n", "char\t濕\t溼\n", "char\t濕濕\t溼\tㄕ\n", "char\t濕\t溼\t\n", "other\ta\tb\n", "word\ta\tb\nword\ta\tc\n", "word\t甲乙\t丙\n"]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                E.Forms().parse(bad)

    def test_a_syllable_count_that_differs_skips_the_char_step_only(self):
        self.assertEqual(forms().apply("濕a", ["ㄕ"]), "濕a")
        self.assertEqual(forms().apply("甲乙a", ["ㄕ"]), "丙丁a")


class Accept(unittest.TestCase):
    ROWS = ["|你好|ㄋㄧˇ ㄏㄠˇ", "|再見|ㄗㄞˋ ㄐㄧㄢˋ"]

    def load(self, text, rows=None):
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False, encoding="utf-8") as f:
            f.write(text)
        try:
            return E.load_accept(f.name, self.ROWS if rows is None else rows)
        finally:
            os.unlink(f.name)

    def test_parse_and_check(self):
        text = f"# h\n2\t{sha(self.ROWS[1])}\t再建\t字條 0\n2\t{sha(self.ROWS[1])}\t在見\t字條 1\n1\t{sha(self.ROWS[0])}\t妳好\t字條 2\n"
        got, c = self.load(text)
        self.assertEqual(got, {2: ["再建", "在見"], 1: ["妳好"]})
        self.assertEqual(c, hashlib.sha256(text.encode("utf-8")).hexdigest()[:8])
        with self.assertRaises(ValueError):
            self.load(text, ["|你好嗎|ㄋㄧˇ ㄏㄠˇ ˙ㄇㄚ", self.ROWS[1]])
        with self.assertRaises(ValueError):
            self.load(text, self.ROWS[:1])
        self.assertEqual(self.load("# only\n", [])[0], {})

    def test_bad_lines(self):
        s = sha(self.ROWS[0])
        for bad in [f"1\t{s}\t妳好\n", f"0\t{s}\t妳好\t字條\n", f"01\t{s}\t妳好\t字條\n", f"+1\t{s}\t妳好\t字條\n", f"x\t{s}\t妳好\t字條\n",
                    f"1\t{s.upper()}\t妳好\t字條\n", f"1\t{s[:63]}\t妳好\t字條\n", f"1\t{s}\t\t字條\n", f"1\t{s}\t妳好\t\n",
                    f"1\t{s}\t妳好\t字條\n1\t{s}\t妳好\t字條\n", f"1\t{s}\t妳好\t字條\n1\t{sha(self.ROWS[1])}\t你好嗎\t字條\n"]:
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.load(bad)


class Judge2(unittest.TestCase):
    F = forms("# h\nword\t布局\t佈局\tㄅㄨˋ ㄐㄩˊ\t30/70\t字條 0\n")

    def test_layers(self):
        ok = E.judge2(self.F, "她很好", ["ㄊㄚ", "ㄏㄣˇ", "ㄏㄠˇ"])      # L1
        self.assertTrue(ok("他很好") and not ok("他很壞"))
        ok = E.judge2(self.F, "潮濕", ["ㄔㄠˊ", "ㄕ"])                 # L2: the single-character pair
        self.assertTrue(ok("潮溼") and ok("潮濕") and not ok("朝濕"))
        ok = E.judge2(self.F, "佈局", ["ㄅㄨˋ", "ㄐㄩˊ"])              # L2: an extra word pair
        self.assertTrue(ok("布局") and not ok("佈居"))
        syls = ["ㄋㄧˇ", "ㄏㄠˇ"]                                     # L3
        self.assertFalse(E.judge2(self.F, "你好", syls)("泥好"))
        self.assertTrue(E.judge2(self.F, "你好", syls, ["泥好"])("泥好"))
        ok = E.judge2(self.F, "你好", syls, ["妳好嗎", "泥她"])
        self.assertTrue(ok("泥他") and not ok("泥好"))
        self.assertTrue(ok("你好") and not ok("你"))


if __name__ == "__main__":
    unittest.main()
