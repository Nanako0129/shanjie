"""Tests for tools/build_quote_pack.py (docs/contracts/quote-pack.md section 4). Run: python3 -m unittest tools/test_build_quote_pack.py   (from the repo root).
The quote lists here are synthetic fixtures: no real quote is claimed to be checked. The rule tests fake the readings tool and the decoder;
`RealCli` runs the evaluation CLI on the shipped model. Needs the repo's data (data/lexicon, data/lm) and the CLI (built on demand
with cargo); it fails loudly without them, never skips."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_quote_pack as Q  # noqa: E402

READINGS = {  # tools/readings.py needs the MOE dictionary; the fixture fixes the readings instead
    "甲乙丙丁": "ㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ ㄉㄧㄥ", "戊己庚辛": "ㄨˋ ㄐㄧˇ ㄍㄥ ㄒㄧㄣ", "你不要一個人": "ㄋㄧˇ ㄅㄨˊ ㄧㄠˋ ㄧ ㄍㄜ˙ ㄖㄣˊ",
    "天地玄黃宇宙洪荒": "ㄊㄧㄢ ㄉㄧˋ ㄒㄩㄢˊ ㄏㄨㄤˊ ㄩˇ ㄓㄡˋ ㄏㄨㄥˊ ㄏㄨㄤ", "壬癸子丑寅卯辰巳": "ㄖㄣˊ ㄍㄨㄟˇ ㄗˇ ㄔㄡˇ ㄧㄣˊ ㄇㄠˇ ㄔㄣˊ ㄙˋ",
    "一二三四五六七八九十": "ㄧ ㄦˋ ㄙㄢ ㄙˋ ㄨˇ ㄌㄧㄡˋ ㄑㄧ ㄅㄚ ㄐㄧㄡˇ ㄕˊ", "一二三四五六七八九十一": "ㄧ ㄦˋ ㄙㄢ ㄙˋ ㄨˇ ㄌㄧㄡˋ ㄑㄧ ㄅㄚ ㄐㄧㄡˇ ㄕˊ ㄧ",
    "同音甲": "ㄊㄨㄥˊ ㄧㄣ ㄐㄧㄚˇ", "同音乙": "ㄊㄨㄥˊ ㄧㄣ ㄐㄧㄚˇ",
}


def fake_readings(words):
    return {w: (READINGS[w].split(), w == "戊己庚辛") for w in words if w in READINGS}   # 戊己庚辛 is marked CHECK


def same(pairs, prof, packs=None):
    """A decoder that always returns the word itself (the pack changes nothing)."""
    return [w for w, _ in pairs]


NONE = frozenset()


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        for n in Q.DECODE_PACKS:        # build() reads (and hashes) the ACG and IT pack files in acg_dir; the fixtures have none of their own
            open(os.path.join(cls.tmp, n), "w").close()

    def build(self, lines, decode=same, have=NONE, acg_words=NONE, readings=fake_readings):
        path = os.path.join(self.tmp, "list.tsv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# fixture\n" + "".join(l + "\n" for l in lines))
        return Q.build(path, acg_dir=self.tmp, decode=decode, readings=readings, have=have, acg_words=acg_words)


def row(text, source="synthetic", reading=""):
    return f"{text}\t出處\t2000\t{source}\t測試\t{reading}"


class Rules(Fixture):
    def test_unchecked_lines_are_excluded_and_reported(self):
        files, manifest, report = self.build([row("甲乙丙丁", source=""), row("戊己庚辛", source="待核對"), row("天地玄黃宇宙洪荒")])
        self.assertEqual((manifest["quotes_listed"], manifest["quotes_checked"], manifest["quotes_unchecked"]), (3, 1, 2))
        words = {l.split("\t")[1] for l in files["quotes-add.tsv"].splitlines()}
        self.assertEqual(words, {"天地玄黃宇宙洪荒"})
        unchecked = report.split("## 核對來源空白")[1].split("## 不收")[0]
        self.assertIn("甲乙丙丁", unchecked)
        self.assertIn("戊己庚辛", unchecked)
        self.assertNotIn("天地玄黃宇宙洪荒", unchecked)

    def test_nothing_checked_gives_an_empty_pack_without_a_decode(self):
        def boom(*a, **k):
            raise AssertionError("no decode is needed with no clause")
        files, manifest, _ = self.build([row("甲乙丙丁", source="")], decode=boom)
        self.assertEqual((files["quotes-add.tsv"], manifest["clauses"], manifest["rows"]), ("", 0, 0))

    def test_clauses_split_at_punctuation_and_the_cap_is_ten_characters(self):
        text = "甲乙丙丁，戊己庚辛。一二三四五六七八九十一！天地玄黃宇宙洪荒… 一二三四五六七八九十"
        files, manifest, report = self.build([row(text)])
        words = {l.split("\t")[1] for l in files["quotes-add.tsv"].splitlines()}
        self.assertEqual(words, {"甲乙丙丁", "戊己庚辛", "天地玄黃宇宙洪荒", "一二三四五六七八九十"})      # ten characters is in
        self.assertEqual(manifest["skipped"]["too long"], 1)                                          # eleven is reported, and nothing crashed
        self.assertIn("too long\t2\t一二三四五六七八九十一", report)

    def test_clauses_with_non_han_characters_or_one_character_are_reported_not_collected(self):
        files, manifest, report = self.build([row("甲乙丙丁，Hello 世界，路人 ABC，我，四五，甲乙丙丁")], readings=lambda ws: {w: (["ㄅ"] * len(w), False) for w in ws})
        self.assertEqual(manifest["skipped"], {"too long": 0, "too short": 1, "not Han": 2, "in the lexicon": 0})
        self.assertIn("not Han\t2\tABC", report)
        self.assertIn("too short\t2\t我", report)
        self.assertEqual({l.split("\t")[1] for l in files["quotes-add.tsv"].splitlines()}, {"甲乙丙丁", "世界", "路人", "四五"})

    def test_a_clause_already_in_the_lexicon_or_the_acg_pack_is_not_collected(self):
        # `have` stands for the base, overlay-add.tsv, sandhi-add.tsv and names-add.tsv (the real names-add.tsv below), acg_words for the ACG pack
        files, manifest, report = self.build([row("甲乙丙丁，戊己庚辛，天地玄黃宇宙洪荒")], have={"甲乙丙丁"}, acg_words={"戊己庚辛"})
        self.assertEqual({l.split("\t")[1] for l in files["quotes-add.tsv"].splitlines()}, {"天地玄黃宇宙洪荒"})
        self.assertEqual(manifest["skipped"]["in the lexicon"], 2)
        self.assertIn("in the lexicon\t2\t甲乙丙丁", report)

    def test_a_clause_equal_to_a_names_add_word_is_not_collected(self):
        names = os.path.join(Q.bap.LEX, "names-add.tsv")
        with open(names, encoding="utf-8") as f:
            word = next(l.split("\t")[1] for l in f if 2 <= len(l.split("\t")[1]) <= 10 and Q.HAN.match(l.split("\t")[1]))
        self.assertIn(word, Q.bap.lexicon_words())          # the default `have` of the real build reads names-add.tsv
        path = os.path.join(self.tmp, "names-list.tsv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(row(word) + "\n")
        files, manifest, _ = Q.build(path, acg_dir=self.tmp, decode=same, readings=lambda ws: {w: (["ㄅ"] * len(w), False) for w in ws}, acg_words=NONE)
        self.assertEqual((files["quotes-add.tsv"], manifest["skipped"]["in the lexicon"]), ("", 1), word)

    def test_sandhi_rows_for_bu_and_yi(self):
        files, _, _ = self.build([row("你不要一個人")])
        rows = [l.split("\t") for l in files["quotes-add.tsv"].splitlines()]
        self.assertEqual(len(rows), 2)
        main, var = rows
        self.assertEqual(main[0], "ㄋㄧˇ-ㄅㄨˊ-ㄧㄠˋ-ㄧ-ㄍㄜ˙-ㄖㄣˊ")
        self.assertEqual(var[0], "ㄋㄧˇ-ㄅㄨˋ-ㄧㄠˋ-ㄧˊ-ㄍㄜ˙-ㄖㄣˊ")          # build_overlay.sandhi_variant: the other reading of 不 and 一
        self.assertEqual(round(float(main[2]) - float(var[2]), 8), Q.bo.VARIANT_PENALTY)
        self.assertEqual((main[1], var[1], main[3].strip(), var[3].strip()), ("你不要一個人",) * 2 + ("quotes",) * 2)

    def test_score_is_the_acg_rule_for_the_length(self):
        files, _, _ = self.build([row("甲乙丙丁，天地玄黃宇宙洪荒")])
        sc = Q.bap.scores(Q.bap.base_lexicon())
        got = {l.split("\t")[1]: float(l.split("\t")[2]) for l in files["quotes-add.tsv"].splitlines()}
        self.assertEqual(got, {"甲乙丙丁": sc[4], "天地玄黃宇宙洪荒": sc[8]})

    def test_the_reading_column_wins_over_the_tool_and_is_checked(self):
        files, _, report = self.build([row("甲乙丙丁，戊己庚辛", reading="｜ㄨˋ ㄐㄧˇ ㄍㄥ ㄒㄧㄣ")])
        rows = {l.split("\t")[1]: l.split("\t")[0] for l in files["quotes-add.tsv"].splitlines()}
        self.assertEqual(rows["戊己庚辛"], "ㄨˋ-ㄐㄧˇ-ㄍㄥ-ㄒㄧㄣ")
        self.assertNotIn("戊己庚辛", report.split("## 讀音工具標 CHECK")[1].split("## 依規則")[0])      # the maintainer's reading is not a CHECK row
        self.build([row("甲乙丙丁，戊己庚辛")])
        self.assertIn("戊己庚辛", self.build([row("甲乙丙丁，戊己庚辛")])[2].split("## 讀音工具標 CHECK")[1])
        with self.assertRaises(SystemExit):
            self.build([row("甲乙丙丁", reading="ㄐㄧㄚˇ ㄧˇ")])                 # three syllables short
        with self.assertRaises(SystemExit):
            self.build([row("甲乙丙丁", reading="ㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ ㄉㄧㄥ｜ㄅ ㄅ")])    # more readings than clauses
        with self.assertRaises(SystemExit):
            self.build(["甲乙丙丁\t出處\t2000"])                                      # fewer than five columns

    def test_same_clause_in_two_quotes_is_one_word_with_two_sources_and_sorts_first_among_homophones(self):
        files, _, _ = self.build([row("同音甲"), row("同音乙"), row("同音乙，甲乙丙丁")])
        rows = [l.split("\t") for l in files["quotes-add.tsv"].splitlines() if l.startswith("ㄊ")]
        self.assertEqual([r[1] for r in rows], ["同音乙", "同音甲"])                  # more sources first
        self.assertEqual(round(float(rows[0][2]) - float(rows[1][2]), 8), 1e-6)     # the TIE step that makes the order count

    def test_a_clause_that_pushes_a_lexicon_word_off_first_place_is_dropped_and_reported(self):
        def decode(pairs, prof, packs=None):
            return ["既有詞" if packs is None and w == "甲乙丙丁" else w for w, _ in pairs]      # without the pack 甲乙丙丁's reading gives the lexicon word
        files, manifest, report = self.build([row("甲乙丙丁，戊己庚辛")], decode=decode, have={"既有詞"})
        self.assertEqual({l.split("\t")[1] for l in files["quotes-add.tsv"].splitlines()}, {"戊己庚辛"})
        self.assertEqual(manifest["dropped_by_lexicon_rule"], 1)
        self.assertIn("ㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ ㄉㄧㄥ\t甲乙丙丁\t既有詞\tchat", report)
        self.assertIn("ㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ ㄉㄧㄥ\t甲乙丙丁\t既有詞\tformal", report)

    def test_a_collision_between_two_quote_clauses_is_reported_not_dropped(self):
        def decode(pairs, prof, packs=None):
            return [("同音乙" if w == "同音甲" else w) if packs is not None else w for w, _ in pairs]       # with the pack the first of the shared reading is 同音乙
        files, manifest, report = self.build([row("同音甲，同音乙")], decode=decode)
        self.assertEqual(manifest["clauses"], 2)
        self.assertIn("ㄊㄨㄥˊ ㄧㄣ ㄐㄧㄚˇ\t同音甲\t同音乙", report.split("## 台詞分句之間的同音")[1])

    def test_a_clause_that_is_not_first_with_the_pack_is_listed_not_dropped(self):
        def decode(pairs, prof, packs=None):
            return ["別句" if w == "甲乙丙丁" else w for w, _ in pairs]          # first place is another string with and without the pack
        files, manifest, report = self.build([row("甲乙丙丁，戊己庚辛")], decode=decode)
        self.assertEqual((manifest["clauses"], manifest["not_first_with_pack"]), (2, 1))
        self.assertIn("甲乙丙丁\tㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ ㄉㄧㄥ\tchat\t別句\t別句", report)
        self.assertIn("甲乙丙丁", files["quotes-add.tsv"])
        self.assertEqual(manifest["first_without_quote_pack"], {"chat": 1, "formal": 1})

    def test_taiwan_ellipsis_and_full_width_marks_split_and_extension_han_counts_as_han(self):
        self.assertEqual(Q.split_clauses("甲乙⋯⋯丙丁‧戊己．庚辛／壬癸"), ["甲乙", "丙丁", "戊己", "庚辛", "壬癸"])
        self.assertTrue(Q.HAN.match("〇㐀𠀀豈"))
        self.assertFalse(Q.HAN.match("甲a"))

    def test_a_reading_given_for_a_clause_that_is_not_collected_does_not_stop_the_build(self):
        _, manifest, _ = self.build([row("甲乙3丙，戊己庚辛", reading="ㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ｜ㄨˋ ㄐㄧˇ ㄍㄥ ㄒㄧㄣ")])
        self.assertEqual((manifest["skipped"]["not Han"], manifest["clauses"]), (1, 1))

    def test_two_lines_giving_one_clause_different_readings_stop_the_build(self):
        with self.assertRaises(SystemExit):
            self.build([row("甲乙丙丁", reading="ㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ ㄉㄧㄥ"), row("甲乙丙丁", reading="ㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ ㄉㄧㄥˋ")])

    def test_a_dropped_clause_is_not_listed_as_collected_under_check(self):
        def decode(pairs, prof, packs=None):
            return ["既有詞" if packs is None and w == "戊己庚辛" else w for w, _ in pairs]      # 戊己庚辛 (CHECK in the fixture) loses to a lexicon word
        _, manifest, report = self.build([row("甲乙丙丁，戊己庚辛")], decode=decode, have={"既有詞"})
        self.assertEqual(manifest["dropped_by_lexicon_rule"], 1)
        self.assertNotIn("戊己庚辛", report.split("## 讀音工具標 CHECK")[1].split("## 依規則")[0])

    def test_output_is_deterministic_and_independent_of_the_line_order(self):
        lines = [row("甲乙丙丁，戊己庚辛"), row("你不要一個人"), row("天地玄黃宇宙洪荒，同音乙"), row("同音甲，甲乙丙丁")]
        a = self.build(lines)
        self.assertEqual(a, self.build(lines))
        b = self.build(list(reversed(lines)))
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[1]["version"], b[1]["version"])


class RealCli(unittest.TestCase):
    """The shipped model through the evaluation CLI: a clause the n-gram spells wrong without the pack is first with it (chat and formal),
    and the build tool's check says so. 安全第一條 is spelled 安全第一調 without the pack (measured 2026-10-29 on the 通語 SJ-LM-7 files; 不要迷戀哥, the fixture before the model change, is spelled right without it now). The list gives the reading, so the readings tool is not needed."""

    def test_the_pack_makes_the_clause_first_through_the_cli(self):
        Q.check_shipped_lm()
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "list.tsv")
            with open(path, "w", encoding="utf-8") as f:
                f.write(row("安全第一條", reading="ㄢ ㄑㄩㄢˊ ㄉㄧˋ ㄧ ㄊㄧㄠˊ") + "\n")
            files, manifest, report = Q.build(path)
        self.assertEqual(manifest["clauses"], 1)
        self.assertEqual(manifest["not_first_with_pack"], 0, report)
        self.assertEqual(manifest["first_without_quote_pack"], {"chat": 0, "formal": 0}, "the fixture clause must be one the pack changes")
        self.assertEqual(manifest["dropped_by_lexicon_rule"], 0)


class Stale(unittest.TestCase):
    """The committed pack was decoded with the models and the ACG and IT pack files recorded in quotes.json; a model swap or a pack rebuild
    since then leaves the collision rule and the first-place report stale, so the pack must be rebuilt."""

    def test_quotes_json_matches_the_shipped_models_and_packs(self):
        rec = json.load(open(os.path.join(Q.PACKS, "quotes.json"), encoding="utf-8"))
        fix = f"rebuild with `{Q.REBUILD}`"
        self.assertEqual(rec["decode_models_sha256"], Q.shipped_lm_sha(), f"quotes.json was built with other models than data/*.sha256: {fix}")
        self.assertEqual(rec["decode_packs_sha256"], {n: Q.sha256_file(os.path.join(Q.PACKS, n)) for n in Q.DECODE_PACKS},
                         f"the ACG or IT pack changed since quotes.json was built: {fix}")

    def test_a_missing_hash_file_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            real = Q.bap.ROOT
            Q.bap.ROOT = d
            try:
                with self.assertRaises(SystemExit) as cm:
                    Q.shipped_lm_sha()
            finally:
                Q.bap.ROOT = real
        self.assertIn("is missing", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
