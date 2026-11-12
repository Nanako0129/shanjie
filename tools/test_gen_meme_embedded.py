"""Tests for tools/gen_meme_embedded.py . Run: python3 -m unittest tools/test_gen_meme_embedded.py (from the repo root)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_meme_embedded as G  # noqa: E402

WORDS = "|甲乙|ㄅㄧ ㄅㄧ\n|丙丁戊|ㄉㄧ ㄉㄧ ㄉㄧ\n"


class Gen(unittest.TestCase):
    def test_six_rows_per_word_in_input_order(self):
        rows = G.generate(WORDS).splitlines()
        self.assertEqual(len(rows), 12)
        self.assertEqual(rows[:6], ["|他說甲乙了|ㄊㄚ ㄕㄨㄛ ㄅㄧ ㄅㄧ ㄌㄜ˙", "|他說甲乙吧|ㄊㄚ ㄕㄨㄛ ㄅㄧ ㄅㄧ ㄅㄚ˙",
                                    "|真的是甲乙了|ㄓㄣ ㄉㄜ˙ ㄕˋ ㄅㄧ ㄅㄧ ㄌㄜ˙", "|真的是甲乙吧|ㄓㄣ ㄉㄜ˙ ㄕˋ ㄅㄧ ㄅㄧ ㄅㄚ˙",
                                    "|我覺得甲乙了|ㄨㄛˇ ㄐㄩㄝˊ ㄉㄜ˙ ㄅㄧ ㄅㄧ ㄌㄜ˙", "|我覺得甲乙吧|ㄨㄛˇ ㄐㄩㄝˊ ㄉㄜ˙ ㄅㄧ ㄅㄧ ㄅㄚ˙"])
        self.assertTrue(rows[6].startswith("|他說丙丁戊了|"))

    def test_cli_writes_deterministically_and_check_notices_a_difference(self):
        with tempfile.TemporaryDirectory() as d:
            w, a, b = (os.path.join(d, n) for n in ("w.txt", "a.txt", "b.txt"))
            with open(w, "w", encoding="utf-8") as f:
                f.write(WORDS)
            G.main([w, a])
            G.main([w, b])
            with open(a, "rb") as fa, open(b, "rb") as fb:
                self.assertEqual(fa.read(), fb.read())
            G.main(["--check", w, a])
            with open(a, "a", encoding="utf-8") as f:
                f.write("x\n")
            with self.assertRaises(SystemExit):
                G.main(["--check", w, a])


if __name__ == "__main__":
    unittest.main()
