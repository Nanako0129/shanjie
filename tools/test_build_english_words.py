"""中英混打單字表建置程式（docs/contracts/zhen-mixed.md §3.4、§5.6）的測試：過濾、切法、排序、輸入雜湊檢查。
合成輸入，不連網、不讀快取。用法：python3 -m unittest tools/test_build_english_words.py（從 repo 根目錄）。"""
import contextlib
import hashlib
import io
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_english_words as B  # noqa: E402


def lines(*words):
    """頻率遞減的合成輸入：每個字附一個遞減的次數。"""
    return [f"{w} {1000 - i}" for i, w in enumerate(words)]


class Filter(unittest.TestCase):
    def test_keeps_only_three_to_thirty_two_lowercase_letters(self):
        ok32, bad33 = "a" * 32, "a" * 33
        got = B.build(lines("the", "it's", "ab", "x1y", "Abc", "abc.", "café", ok32, bad33, "über", "and"))
        self.assertEqual(got, sorted(["the", "and", ok32], key=lambda w: w.encode()))

    def test_blank_lines_and_extra_columns(self):
        self.assertEqual(B.build(["", "the 5 extra", "  ", "and"]), ["and", "the"])

    def test_duplicates_keep_the_first(self):
        self.assertEqual(B.build(lines("the", "and", "the", "and", "for")), ["and", "for", "the"])


class Cut(unittest.TestCase):
    def test_caps_count_in_file_order_per_length_class(self):
        # Length 3 and length >= 4 are counted apart; each cap takes the first ones in the file.
        words = ["aaa", "aaaa", "bbb", "bbbb", "ccc", "cccc", "ddd", "dddd", "eeeee"]
        self.assertEqual(B.build(lines(*words), len3=2, len4=3), ["aaa", "aaaa", "bbb", "bbbb", "cccc"])

    def test_the_cap_skips_words_it_does_not_take_but_later_classes_continue(self):
        self.assertEqual(B.build(lines("aaa", "bbb", "cccc", "ccc"), len3=1, len4=5), ["aaa", "cccc"])

    def test_zero_caps_and_no_cap(self):
        self.assertEqual(B.build(lines("aaa", "bbbb"), len3=0, len4=0), [])
        self.assertEqual(B.build(lines("aaa", "bbbb", "ccccc"), len3=0, len4=None), ["bbbb", "ccccc"])

    def test_filtered_words_do_not_use_up_the_cap(self):
        self.assertEqual(B.build(lines("it's", "aaa", "b2b", "ccc"), len3=2, len4=0), ["aaa", "ccc"])


class Render(unittest.TestCase):
    def test_sorted_by_bytes_and_one_word_per_line(self):
        out = B.render(B.build(lines("zebra", "apple", "mango", "the")))
        self.assertEqual(out, b"apple\nmango\nthe\nzebra\n")

    def test_output_satisfies_the_engine_loader_rules(self):
        ws = B.build(lines(*["w" + "a" * k for k in range(2, 40)]))
        out = B.render(ws).decode("ascii")
        self.assertTrue(out.endswith("\n") and "\r" not in out and "﻿" not in out)
        rows = out.splitlines()
        self.assertEqual(len(rows), len(set(rows)))
        self.assertTrue(all(re.fullmatch(r"[a-z]{3,32}", r) for r in rows))
        self.assertLessEqual(len(rows), 200_000)
        self.assertLessEqual(len(out), 4 << 20)


class InputHash(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "in.txt")
        with open(self.path, "wb") as f:
            f.write(b"the 5\nand 4\nit's 3\n")

    def test_a_mismatching_input_stops_with_an_error_naming_both_hashes(self):
        with self.assertRaises(SystemExit) as cm:
            B.check_input(self.path)
        msg = str(cm.exception)
        self.assertIn(hashlib.sha256(b"the 5\nand 4\nit's 3\n").hexdigest(), msg)
        self.assertIn(B.INPUT_SHA256, msg)

    def test_main_refuses_a_mismatch_and_writes_nothing(self):
        out = os.path.join(self.dir.name, "out.txt")
        with self.assertRaises(SystemExit):
            B.main(["--input", self.path, "--out", out])
        self.assertFalse(os.path.exists(out))

    def test_main_end_to_end_with_the_hash_of_the_synthetic_input(self):
        out = os.path.join(self.dir.name, "out.txt")
        saved = B.INPUT_SHA256
        with open(self.path, "rb") as f:
            B.INPUT_SHA256 = hashlib.sha256(f.read()).hexdigest()
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                B.main(["--input", self.path, "--out", out, "--len3", "5", "--len4", "all"])
        finally:
            B.INPUT_SHA256 = saved
        with open(out, "rb") as f:
            data = f.read()
        self.assertEqual(data, b"and\nthe\n")
        self.assertEqual(buf.getvalue().strip(), f"2 lines, SHA-256 {hashlib.sha256(data).hexdigest()}")

    def test_print_options_give_the_pins_build_app_uses(self):
        for flag, want in (("--print-url", B.SYMSPELL_URL), ("--print-input-sha256", B.INPUT_SHA256)):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                B.main([flag])
            self.assertEqual(buf.getvalue().strip(), want)

    def test_the_pin_is_a_sha256_and_a_commit(self):
        self.assertRegex(B.INPUT_SHA256, r"^[0-9a-f]{64}$")
        self.assertRegex(B.SYMSPELL_COMMIT, r"^[0-9a-f]{40}$")
        self.assertIn(B.SYMSPELL_COMMIT, B.SYMSPELL_URL)


if __name__ == "__main__":
    unittest.main()
