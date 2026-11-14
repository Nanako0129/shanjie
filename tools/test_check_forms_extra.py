"""tools/check_forms_extra.py：每個收錄條件各有一個故意違反的列（docs/spec/evaluation.md §3.6）。
條件 1、5 用一份迷你辭典（形狀同真的傾印）；真辭典的檢查在 SHANJIE_MOEDICT 沒設時印 NOTE 跳過。"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_forms_extra as C  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "check_forms_extra.py")


def entry(title, *hets):
    return {"title": title, "heteronyms": [{"bopomofo": b, "definitions": defs} for b, defs in hets]}


def d(text="", link=None):
    r = {"def": text}
    if link:
        r["link"] = link
    return r


DICT = [
    entry("佈", ("ㄅㄨˋ", [d("遍及、散滿。", ["通「布」。"])])),
    entry("布", ("ㄅㄨˋ", [d("織物。")])),
    entry("佔", ("ㄓㄢˋ", [d("強力奪取。", ["同「占」。"])]), ("ㄓㄢ", [d("窺視。", ["同「覘」。"])])),
    entry("占", ("ㄓㄢˋ", [d("據有。")])),
    entry("折", ("ㄓㄜˊ", [d("疊。也作「摺」。")])),
    entry("摺", ("ㄓㄜˊ", [d("疊。")])),
    entry("念", ("ㄋㄧㄢˋ", [d("讀。也作「唸」。")])),
    entry("唸", ("ㄋㄧㄢˋ", [d("")])),
    entry("覆", ("ㄈㄨˋ", [d("回、還。", ["同「復」。"])])),
    entry("復", ("ㄈㄨˋ", [d("返回。")])),
    entry("芽", ("ㄧㄚˊ", [d("植物初生的嫩莖。")])),
    entry("牙", ("ㄧㄚˊ", [d("牙齒。")])),
    entry("分佈", ("ㄈㄣ　ㄅㄨˋ", [d("散布。也作「分布」。")])),
    entry("分布", ("ㄈㄣ　ㄅㄨˋ", [d("散布各處。")])),
    entry("回復", ("ㄏㄨㄟˊ　ㄈㄨˋ", [d("還原。")])),
    entry("回覆", ("ㄏㄨㄟˊ　ㄈㄨˋ", [d("答覆。")])),
    entry("好的", ("ㄏㄠˇ　˙ㄉㄜ", [d("")])),
]
FORMS = "# h\nword\t嚎啕痛哭\t嚎啕慟哭\nchar\t牀\t床\tㄔㄨㄤˊ\n"
ORDER = "# h\nㄒㄧㄠˇ-ㄐㄧㄝˇ\t小姊\t小姐\tsrc\n"


def row(a, b, reading, counts="30/70", basis="x"):
    """spelling a, canonical form b (the smaller string, as the table has it)."""
    a, b = max(a, b), min(a, b)
    return f"word\t{a}\t{b}\t{reading}\t{counts}\t{basis}\n"


def conds(extra, forms=FORMS, order=ORDER, entries=DICT):
    bad, skipped = C.check(forms, extra, order, entries)
    return bad, skipped


def conditions_of(extra, line=1, **kw):
    return {c for ln, c, _ in conds(extra, **kw)[0] if ln == line}


class Conditions(unittest.TestCase):
    def test_a_good_row_passes_everything(self):
        bad, skipped = conds(row("分布", "分佈", "ㄈㄣ ㄅㄨˋ"))
        self.assertEqual((bad, skipped), ([], []))

    def test_empty_table_passes(self):
        self.assertEqual(conds("# only a header\n"), ([], []))

    def test_condition_1_reading_not_in_the_heteronym(self):
        self.assertEqual(conditions_of(row("分布", "分佈", "ㄈㄣ ㄆㄨˋ")), {1})

    def test_condition_1_no_dictionary_relation(self):
        self.assertIn(1, conditions_of(row("藍芽", "藍牙", "ㄌㄢˊ ㄧㄚˊ")))

    def test_condition_1_heteronym_must_carry_the_reading(self):
        # 佔's relation to 占 sits in the ㄓㄢˋ heteronym; the other one (ㄓㄢ) links to 覘
        self.assertEqual(conditions_of(row("佔有", "占有", "ㄓㄢˋ ㄧㄡˇ")), set())
        self.assertIn(1, conditions_of(row("佔有", "占有", "ㄓㄢ ㄧㄡˇ")))

    def test_condition_1_relation_words(self):
        # 通 (佈/布), 同 (佔/占), 也作 (折/摺, 念/唸 from the entry of the first), 同 (覆/復)
        for a, b, rd in (("佈局", "布局", "ㄅㄨˋ ㄐㄩˊ"), ("佔有", "占有", "ㄓㄢˋ ㄧㄡˇ"), ("摺疊", "折疊", "ㄓㄜˊ ㄉㄧㄝˊ"), ("唸書", "念書", "ㄋㄧㄢˋ ㄕㄨ"), ("覆原", "復原", "ㄈㄨˋ ㄩㄢˊ")):
            self.assertNotIn(1, conditions_of(row(a, b, rd)), (a, b))

    def test_condition_1_ru_dong_xiang_bu_do_not_count_as_tong(self):
        dic = DICT + [entry("甲", ("ㄐㄧㄚˇ", [d("如同「乙」。相同「乙」。不同「乙」。等同「乙」。")])), entry("乙", ("ㄧˇ", [d("")]))]
        self.assertIn(1, conditions_of(row("甲好", "乙好", "ㄐㄧㄚˇ ㄏㄠˇ"), entries=dic))
        dic[-2] = entry("甲", ("ㄐㄧㄚˇ", [d("同「乙」。")]))
        self.assertNotIn(1, conditions_of(row("甲好", "乙好", "ㄐㄧㄚˇ ㄏㄠˇ"), entries=dic))

    def test_condition_2_the_less_used_spelling_under_ten_percent(self):
        self.assertIn(2, conditions_of(row("分布", "分佈", "ㄈㄣ ㄅㄨˋ", counts="1/20")))
        self.assertIn(2, conditions_of(row("分布", "分佈", "ㄈㄣ ㄅㄨˋ", counts="0/50")))
        self.assertIn(2, conditions_of(row("分布", "分佈", "ㄈㄣ ㄅㄨˋ", counts="x/y")))
        self.assertNotIn(2, conditions_of(row("分布", "分佈", "ㄈㄣ ㄅㄨˋ", counts="2/18")), "exactly 10% is enough")

    def test_condition_3_excluded_character(self):
        self.assertIn(3, conditions_of(row("好的", "好得", "ㄏㄠˇ ˙ㄉㄜ")))

    def test_condition_4_pair_in_the_variant_order_table(self):
        self.assertIn(4, conditions_of(row("小姐", "小姊", "ㄒㄧㄠˇ ㄐㄧㄝˇ")))

    def test_condition_5_two_entries_that_do_not_link_each_other(self):
        self.assertIn(5, conditions_of(row("回覆", "回復", "ㄏㄨㄟˊ ㄈㄨˋ")))
        # a linked pair, or a pair where one side is no entry, is not stopped by it
        self.assertNotIn(5, conditions_of(row("分布", "分佈", "ㄈㄣ ㄅㄨˋ")))
        self.assertNotIn(5, conditions_of(row("佈局", "布局", "ㄅㄨˋ ㄐㄩˊ")))

    def test_condition_5_a_link_field_also_connects_two_entries(self):
        # the also-written-as sits in `link`, not in `def`: the two entries are linked, condition 5 does not stop the pair
        dic = [e for e in DICT if e["title"] not in ("回復", "回覆")] + [
            entry("回復", ("ㄏㄨㄟˊ　ㄈㄨˋ", [d("還原。", ["也作「回覆」。"])])), entry("回覆", ("ㄏㄨㄟˊ　ㄈㄨˋ", [d("答覆。")]))]
        self.assertNotIn(5, conditions_of(row("回覆", "回復", "ㄏㄨㄟˊ ㄈㄨˋ"), entries=dic))
        self.assertIn(5, conditions_of(row("回覆", "回復", "ㄏㄨㄟˊ ㄈㄨˋ")), "without the link the pair is two separate entries")

    def test_condition_4_matches_the_spellings_whatever_the_reading_or_order(self):
        order = "# h\nㄅㄨˋ-ㄐㄩˊ\t佈局\t布局\tsrc\n"
        self.assertIn(4, conditions_of(row("佈局", "布局", "ㄅㄨˋ ㄐㄩˊ"), order=order))
        self.assertNotIn(4, conditions_of(row("佔有", "占有", "ㄓㄢˋ ㄧㄡˇ"), order=order), "another pair is not stopped")

    def test_condition_6_three_spelling_chain(self):
        extra = row("布局", "佈局", "ㄅㄨˋ ㄐㄩˊ") + row("佈局", "佈佈", "ㄅㄨˋ ㄐㄩˊ")
        self.assertTrue({ln for ln, c, _ in conds(extra)[0] if c == 6} >= {1}, conds(extra)[0])

    def test_condition_6_spelling_with_tai_changes_under_the_lenient_comparison(self):
        self.assertIn(6, conditions_of("word\t臺北\t台北\tㄊㄞˊ ㄅㄟˇ\t30/70\tx\n"))

    def test_condition_6_spelling_already_in_forms_v1_or_an_earlier_line(self):
        forms = FORMS + "word\t分布\t分佈\n"
        self.assertIn(6, conditions_of(row("分布", "分佈", "ㄈㄣ ㄅㄨˋ"), forms=forms))
        twice = row("分布", "分佈", "ㄈㄣ ㄅㄨˋ") * 2
        self.assertEqual({ln for ln, c, _ in conds(twice)[0] if c == 6}, {2})

    def test_condition_6_canonical_form_is_the_smallest_of_the_union_group(self):
        # forms.tsv sends 分布 to 分佈 (the smaller string); a line sending 分佈 back to 分布 is the larger one's direction
        forms = FORMS + "word\t分布\t分佈\n"
        self.assertIn(6, conditions_of("word\t分佈\t分布\tㄈㄣ ㄅㄨˋ\t30/70\tx\n", forms=forms))

    def test_malformed_line_is_condition_0(self):
        self.assertEqual(conditions_of("word\t分布\t分佈\tㄈㄣ ㄅㄨˋ\n"), {0})
        self.assertEqual(conditions_of("word\t分布\t分佈佈\tㄈㄣ ㄅㄨˋ\t30/70\tx\n"), {0})

    def test_without_a_dictionary_conditions_1_and_5_are_skipped_and_the_rest_run(self):
        bad, skipped = C.check(FORMS, row("藍芽", "藍牙", "ㄌㄢˊ ㄧㄚˊ") + row("好的", "好得", "ㄏㄠˇ ˙ㄉㄜ"), ORDER, None)
        self.assertEqual(skipped, [1, 5])
        self.assertEqual({c for _, c, _ in bad}, {3})


class Script(unittest.TestCase):
    def run_script(self, env_extra=None, drop=()):
        env = {k: v for k, v in os.environ.items() if k not in drop}
        env.update(env_extra or {})
        return subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=env, cwd=ROOT)

    def test_repo_table_without_a_dictionary_prints_the_skip_line_and_exits_0(self):
        r = self.run_script(drop=("SHANJIE_MOEDICT",))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("skipped: conditions 1, 5 (SHANJIE_MOEDICT not set)", r.stdout.splitlines())

    @unittest.skipUnless(os.environ.get("SHANJIE_MOEDICT"), "NOTE: SHANJIE_MOEDICT not set: the real-dictionary checks are skipped")
    def test_repo_table_with_the_dictionary_passes_every_condition(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("skipped", r.stdout)

    @unittest.skipUnless(os.environ.get("SHANJIE_MOEDICT"), "NOTE: SHANJIE_MOEDICT not set: the real-dictionary checks are skipped")
    def test_real_dictionary_relations(self):
        titles = C.by_title(C.load_entries(os.environ["SHANJIE_MOEDICT"]))
        for x, y, syl in (("佈", "布", "ㄅㄨˋ"), ("佔", "占", "ㄓㄢˋ"), ("折", "摺", "ㄓㄜˊ"), ("念", "唸", "ㄋㄧㄢˋ"), ("覆", "復", "ㄈㄨˋ")):
            self.assertIsNotNone(C.char_related(titles, x, y, syl), (x, y))
        for x, y, syl in (("芽", "牙", "ㄧㄚˊ"), ("義", "意", "ㄧˋ")):
            self.assertIsNone(C.char_related(titles, x, y, syl), (x, y))
        # the contract's stop condition: 分佈／分布 must pass condition 1
        bad, _ = C.check(FORMS, row("分布", "分佈", "ㄈㄣ ㄅㄨˋ"), ORDER, C.load_entries(os.environ["SHANJIE_MOEDICT"]))
        self.assertNotIn(1, {c for _, c, _ in bad})


if __name__ == "__main__":
    unittest.main()
