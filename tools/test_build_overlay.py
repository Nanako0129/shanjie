"""Tests for tools/build_overlay.py manual_rows (data/lexicon/overlay-manual.tsv): synthetic files, no dumps, no network.
Run: python3 -m unittest tools/test_build_overlay.py   (from the repo root)."""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_overlay as B  # noqa: E402


def manual(text, taken=frozenset()):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.tsv")
        open(p, "w", encoding="utf-8").write(text)
        return B.manual_rows(set(taken), p)


class ManualRows(unittest.TestCase):
    def test_rows_in_file_order_with_the_bonus_and_the_manual_tag(self):
        rows = manual("# comment\n\n甲乙丙\tㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ\tnote\n甲乙丙丁戊\tㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ ㄉㄧㄥ ㄨˋ\tfive characters use the four-character score\n")
        self.assertEqual(rows, [f"ㄐㄧㄚˇ-ㄧˇ-ㄅㄧㄥˇ\t甲乙丙\t{round(B.SCORE[3] + B.MANUAL_BONUS, 8)!r}\tmanual\n",
                                f"ㄐㄧㄚˇ-ㄧˇ-ㄅㄧㄥˇ-ㄉㄧㄥ-ㄨˋ\t甲乙丙丁戊\t{round(B.SCORE[4] + B.MANUAL_BONUS, 8)!r}\tmanual\n"])

    def test_a_word_already_in_the_lexicon_or_a_wrong_syllable_count_stops(self):
        with self.assertRaises(SystemExit):
            manual("甲乙\tㄐㄧㄚˇ ㄧˇ\tx\n", taken={"甲乙"})
        with self.assertRaises(SystemExit):
            manual("甲乙\tㄐㄧㄚˇ\tx\n")


if __name__ == "__main__":
    unittest.main()
