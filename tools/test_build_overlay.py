"""Tests for tools/build_overlay.py manual words (data/lexicon/overlay-manual.tsv) and their tail of overlay-add.tsv.
Synthetic files except the last test, which reads the committed lexicon files; no dumps, no network.
Run: python3 -m unittest tools/test_build_overlay.py   (from the repo root)."""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_overlay as B  # noqa: E402
import ime  # noqa: E402

SCORES = {2: -7.0, 3: -7.04116568, 4: -6.6, 5: -6.17843815}
SYLLABLES = {"ㄐㄧㄚˇ", "ㄧˇ", "ㄅㄧㄥˇ", "ㄉㄧㄥ", "ㄨˋ", "ㄧ", "ㄧˋ", "ㄒㄧㄡ", "ㄍㄜ"}


def manual(text, taken=frozenset(), encoding="utf-8"):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.tsv")
        with open(p, "w", encoding=encoding) as f:
            f.write(text)
        return B.manual_rows(set(taken), SCORES, SYLLABLES, p)


class ManualRows(unittest.TestCase):
    def test_rows_in_file_order_with_the_bonus_and_the_manual_tag(self):
        rows = manual("# comment\n\n甲乙丙\tㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ\tnote\n甲乙丙丁戊\tㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ ㄉㄧㄥ ㄨˋ\tfive characters\n")
        self.assertEqual(rows, [f"ㄐㄧㄚˇ-ㄧˇ-ㄅㄧㄥˇ\t甲乙丙\t{round(SCORES[3] + B.MANUAL_BONUS, 8)!r}\tmanual\n",
                                f"ㄐㄧㄚˇ-ㄧˇ-ㄅㄧㄥˇ-ㄉㄧㄥ-ㄨˋ\t甲乙丙丁戊\t{round(SCORES[5] + B.MANUAL_BONUS, 8)!r}\tmanual\n"])

    def test_a_bom_is_not_part_of_the_first_line(self):
        self.assertEqual(len(manual("# comment\n甲乙\tㄐㄧㄚˇ ㄧˇ\tx\n", encoding="utf-8-sig")), 1)

    def test_bad_rows_stop_the_build(self):
        bad = {
            "a word already in the lexicon": ("甲乙\tㄐㄧㄚˇ ㄧˇ\tx\n", {"甲乙"}),
            "syllable count": ("甲乙\tㄐㄧㄚˇ\tx\n", ()),
            "unknown syllable": ("甲乙\tjia3 ㄧˇ\tx\n", ()),
            "not Han": ("ab\tㄐㄧㄚˇ ㄧˇ\tx\n", ()),
            "one character": ("甲\tㄐㄧㄚˇ\tx\n", ()),
            "two fields": ("甲乙\tㄐㄧㄚˇ ㄧˇ\n", ()),
            "the same row twice": ("甲乙\tㄐㄧㄚˇ ㄧˇ\tx\n甲乙\tㄐㄧㄚˇ ㄧˇ\ty\n", ()),
            # 一 before a first tone gets a sandhi row ㄧˋ automatically, so writing ㄧˋ by hand repeats it
            "a reading the sandhi row already gives": ("一休哥\tㄧ ㄒㄧㄡ ㄍㄜ\tx\n一休哥\tㄧˋ ㄒㄧㄡ ㄍㄜ\ty\n", ()),
        }
        for why, (text, taken) in bad.items():
            with self.assertRaises(SystemExit, msg=why):
                manual(text, taken)


class ShippedOverlay(unittest.TestCase):
    def test_the_manual_tail_is_what_manual_rows_makes_today(self):
        """Without the dumps: the `manual` rows of overlay-add.tsv are its last rows and equal manual_rows() over the committed
        overlay-manual.tsv, so editing one file without regenerating the other fails here."""
        with open(B.OUT, encoding="utf-8") as f:
            lines = f.readlines()
        tags = [l.rstrip("\n").split("\t")[3] for l in lines]
        tail = tags.index("manual") if "manual" in tags else len(lines)
        self.assertTrue(all(t == "manual" for t in tags[tail:]), "manual rows are the tail")
        base = ime.Lexicon(B.BASE)
        taken = set(base.by_word) | {l.split("\t")[1] for l in lines[:tail]} | B.pack_words()
        self.assertEqual(lines[tail:], B.manual_rows(taken, B.length_scores(base), B.base_syllables(base)))


if __name__ == "__main__":
    unittest.main()
