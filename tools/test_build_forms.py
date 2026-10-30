"""tools/build_forms.py on a miniature dictionary shaped like the real dump entries (docs/contracts/forms-score.md section 3).
Runs anywhere: the real dump is only needed by `build_forms.py --check`, which this file does not call."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_forms as B  # noqa: E402


def entry(title, *hets):
    return {"title": title, "heteronyms": [{"bopomofo": b, "definitions": defs} for b, defs in hets]}


def d(text="", link=None):
    r = {"def": text}
    if link:
        r["link"] = link
    return r


ENTRIES = [
    entry("姊姊", ("ㄗˇ　˙ㄗ　（語音）ㄐㄧㄝˇ　˙ㄐㄧㄝ", [d("稱謂。也作「姐姐」。")])),
    entry("姐姐", ("ㄐㄧㄝˇ　˙ㄐㄧㄝ", [d("稱謂。")])),
    entry("小姐", ("ㄒㄧㄠˇ　ㄐㄧㄝˇ", [d("對女性的通稱。")])),
    entry("小姊", ("ㄒㄧㄠˇ　ㄗˇ", [d("")])),
    entry("牀", (None, [d("「床」的異體字。")])),
    entry("床", ("ㄔㄨㄤˊ", [d("供人睡臥的家具。")])),
    entry("濕", ("ㄕ", [d("", ["同「溼」。"])]), ("ㄒㄧˊ", [d("低溼的地方。")])),
    entry("溼", ("ㄕ", [d("水分多。")])),
    entry("沈", ("ㄔㄣˊ", [d("", ["同「沉」。"])]), ("ㄕㄣˇ", [d("姓。如宋代有沈括。")])),
    entry("沉", ("ㄔㄣˊ", [d("沒入水中。")])),
    entry("姐", ("ㄐㄧㄝˇ", [d("稱謂。通「姊」。如：「大姐」。")])),
    entry("姊", ("ㄗˇ", [d("稱謂。")])),
    entry("佈", ("ㄅㄨˋ", [d("遍及、散滿。", ["通「布」。"]), d("宣布。", ["通「布」。"])])),
    entry("布", ("ㄅㄨˋ", [d("織物。")])),
    entry("喜", ("ㄒㄧˇ", [d("", ["同「歡」。"])])),
    entry("歡", ("ㄏㄨㄢ", [d("高興。")])),
]


def table(entries=ENTRIES):
    text, info = B.build(entries)
    lines = [l.split("\t") for l in text.splitlines() if not l.startswith("#")]
    return text, info, {(r[1]): r for r in lines if r[0] == "word"}, {(r[1]): r for r in lines if r[0] == "char"}


class Forms(unittest.TestCase):
    def test_word_pair_found_only_because_the_reading_is_split(self):
        _, _, w, _ = table()
        self.assertEqual(w["姐姐"][2], "姊姊")

    def test_char_rule_a_variant_of(self):
        _, _, _, c = table()
        self.assertEqual(c["牀"][2], "床")
        self.assertEqual(c["牀"][3], "ㄔㄨㄤˊ")  # no reading of its own: the target's

    def test_char_rule_b_same_as_with_reading_condition(self):
        _, _, _, c = table()
        self.assertEqual(c["濕"][2:], ["溼", "ㄕ"])

    def test_shen_only_chen_reading(self):
        _, _, _, c = table()
        self.assertEqual(c["沈"][2:], ["沉", "ㄔㄣˊ"])

    def test_must_not_have(self):
        _, _, w, c = table()
        for x in "姐姊布佈":
            self.assertNotIn(x, c)
        for x in ("小姐", "小姊"):
            self.assertNotIn(x, w)
        self.assertNotIn("ㄒㄧˊ", c["濕"][3].split(","))
        self.assertNotIn("ㄕㄣˇ", c["沈"][3].split(","))

    def test_neutral_tone_moved_to_the_end(self):
        self.assertEqual(B.to_row_form("˙ㄗ"), "ㄗ˙")
        self.assertEqual(B.to_row_form("ㄐㄧㄝˇ ˙ㄐㄧㄝ"), "ㄐㄧㄝˇ ㄐㄧㄝ˙")
        entries = ENTRIES + [entry("嘀", ("˙ㄉㄧ", [d("", ["同「滴」。"])])), entry("滴", ("ㄉㄧ", [d("水點。")]))]
        _, _, _, c = table(entries)
        self.assertEqual(c["嘀"][3], "ㄉㄧ˙")

    def test_check_flags_leading_neutral_mark_and_bad_counts(self):
        text, _, _, _ = table()
        self.assertIsNone(B.check_invariants(text))
        self.assertIn("leading", B.check_invariants(text.replace("濕\t溼\tㄕ", "濕\t溼\t˙ㄕ")))
        self.assertIn("header", B.check_invariants(text.replace("單字對 ", "單字對 9")))

    def test_char_chains_resolve_to_the_end(self):
        es = [entry(x, ("ㄧ", [d("", [f"同「{y}」。"])])) for x, y in ("甲乙", "乙丙")] + [entry("丙", ("ㄧ", [d("字")]))]
        _, _, _, c = table(es)
        self.assertEqual(c["甲"][2], "丙")
        self.assertEqual(c["乙"][2], "丙")

    def test_char_cycle_fails_the_build(self):
        es = [entry(x, ("ㄧ", [d("", [f"同「{y}」。"])])) for x, y in ("甲乙", "乙甲")]
        with self.assertRaises(SystemExit):
            B.build(es)


if __name__ == "__main__":
    unittest.main()
