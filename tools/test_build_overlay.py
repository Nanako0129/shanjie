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


class Cantonese(unittest.TestCase):
    def test_rule(self):
        headwords = {"凝睇"}
        self.assertTrue(B.cantonese("冇陰公", headwords))
        self.assertTrue(B.cantonese("唔該", headwords))
        self.assertTrue(B.cantonese("買餸", headwords), "a character the review added")
        self.assertFalse(B.cantonese("凝睇", headwords), "dictionary headwords stay")
        for w in ("俾斯麥", "羊咩咩", "口眼喎斜", "好嘞", "沒問題"):
            self.assertFalse(B.cantonese(w, headwords), f"{w}: no Cantonese-only character")

    def test_the_shipped_overlay_keeps_only_these_cantonese_character_words(self):
        """The rule's observed result on the committed overlay (the build reads the MOE dictionary, which CI does not have): the
        non-manual words with a CANTO_CHARS character are exactly these dictionary headwords. Regenerating with another rule or
        dictionary changes this set and has to update it here on purpose."""
        with open(B.OUT, encoding="utf-8") as f:
            words = {l.split("\t")[1] for l in f if l.rstrip("\n").split("\t")[3] not in ("manual", "slang")}
        self.assertEqual({w for w in words if not B.CANTO_CHARS.isdisjoint(w)},
                         {"付畀", "凝睇", "咿唔", "唔篤", "噏呷", "投畀豺虎", "曼睩", "睇眄", "迎睇"})


class ShippedOverlay(unittest.TestCase):
    def test_the_tails_are_what_tail_rows_makes_today(self):
        """Without the dumps: overlay-add.tsv ends with the `manual` rows, then the `slang` rows (docs/contracts/net-slang.md), and the
        tail equals tail_rows() over the committed overlay-manual.tsv and slang-add.tsv, so editing one file without regenerating fails here."""
        with open(B.OUT, encoding="utf-8") as f:
            lines = f.readlines()
        tags = [l.rstrip("\n").split("\t")[3] for l in lines]
        tail = next((i for i, t in enumerate(tags) if t in ("manual", "slang")), len(lines))
        self.assertEqual(tags[tail:], sorted(tags[tail:], key=("manual", "slang").index), "manual rows, then slang rows, are the tail")
        base = ime.Lexicon(B.BASE)
        taken = set(base.by_word) | {l.split("\t")[1] for l in lines[:tail]}
        self.assertEqual(lines[tail:], B.tail_rows(taken, B.length_scores(base), B.base_syllables(base)))
        self.assertEqual(lines, B.rebuild_tails())

    def test_no_slang_file_leaves_the_manual_tail_alone(self):
        base = ime.Lexicon(B.BASE)
        args = (set(base.by_word), B.length_scores(base), B.base_syllables(base))
        with tempfile.TemporaryDirectory() as d:
            without = B.tail_rows(*args, slang=os.path.join(d, "absent.tsv"))
        self.assertEqual(without, B.manual_rows(args[0] | B.pack_words() | B.pack_words(B.IT_PACK) | B.names_words(), *args[1:]))
        self.assertNotIn("slang", {r.rstrip("\n").split("\t")[3] for r in without})


class SlangBehind(unittest.TestCase):
    def test_later_words_on_a_reading_drop_by_their_rank(self):
        rows = ["a\t甲\t-5.0\tslang\n", "a\t乙\t-5.0\tslang\n", "b\t丙\t-5.0\tslang\n", "a\t丁\t-5.0\tslang\n"]
        got = [float(r.split("\t")[2]) for r in B.slang_behind(rows)]
        self.assertEqual(got, [-5.0, -5.0 - B.SLANG_BEHIND, -5.0, -5.0 - 2 * B.SLANG_BEHIND])

if __name__ == "__main__":
    unittest.main()
