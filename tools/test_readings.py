"""tools/readings.py rule 3b and the inverted-character report (docs/contracts/sj-lm-7.md section 2.3), on a synthetic lexicon:
no MoeDict, no McBopomofo files, no real Unihan (a three-line stand-in)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import readings as R  # noqa: E402

LEXICON = """# format test
ㄅㄚ 甲 -6.8
ㄆㄚ 甲 -6.8
ㄆㄚ-ㄅㄧˇ 甲乙 -4.0
ㄅㄚ-ㄅㄧˇ 甲丁 -5.5
ㄅㄚ 丙 -6.8
ㄆㄚˊ 丙 -6.8
ㄅㄚ 戊 -6.8
ㄆㄚ 戊 -6.8
ㄅㄚ 己 -6.8
ㄆㄚ 己 -6.8
ㄅㄚ-ㄅㄧˇ 己乙 -5.0
ㄆㄚ-ㄅㄧˇ 己丁 -5.0
ㄅㄚ 庚 -6.8
ㄆㄚ 庚 -6.0
ㄧˇ 乙 -7.3
ㄧˋ 乙 -6.8
ㄧˇ-ㄧˇ 乙乙 -4.0
ㄧˋ 辛 -6.8
ㄧˇ 辛 -7.0
ㄧˋ 壬 -6.8
ㄧˇ 壬 -7.0
ㄧˋ 癸 -6.8
ㄧˇ 癸 -7.0
ㄧˇ-ㄧˇ 壬壬 -4.0
ㄧˋ-ㄧˋ 辛辛 -4.0
"""
UNIHAN = "# comment\nU+4E19\tkMandarin\tpá\nU+620A\tkMandarin\tbā pā\nU+5DF1\tkMandarin\tzhōng\n"   # 丙 pá; 戊 CN bā, TW pā; 己 unreadable here
BASE = {"甲": ("ㄆㄚ", "multi-char"), "乙": ("ㄧˇ", "multi-char")}


def write(d, name, text):
    p = os.path.join(d, name)
    open(p, "w", encoding="utf-8").write(text)
    return p


class Pinyin(unittest.TestCase):
    def test_syllables(self):
        for py, want in [("zhōng", "ㄓㄨㄥ"), ("shì", "ㄕˋ"), ("jué", "ㄐㄩㄝˊ"), ("yuè", "ㄩㄝˋ"), ("wèi", "ㄨㄟˋ"), ("ế", "ㄝˊ"),
                         ("lǜ", "ㄌㄩˋ"), ("liú", "ㄌㄧㄡˊ"), ("de", "ㄉㄜ˙"), ("xióng", "ㄒㄩㄥˊ"), ("qún", "ㄑㄩㄣˊ"), ("yīng", "ㄧㄥ"),
                         ("huí", "ㄏㄨㄟˊ"), ("wēng", "ㄨㄥ"), ("yòng", "ㄩㄥˋ"), ("zhī", "ㄓ"), ("cí", "ㄘˊ"), ("nǚ", "ㄋㄩˇ"), ("lüè", "ㄌㄩㄝˋ"),
                         ("yīn", "ㄧㄣ"), ("bō", "ㄅㄛ"), ("er", "ㄦ˙"), ("duó", "ㄉㄨㄛˊ"), ("zhuǎi", "ㄓㄨㄞˇ"), ("xiù", "ㄒㄧㄡˋ")]:
            self.assertEqual(R.pinyin_to_bopomofo(py), want, py)
        self.assertIsNone(R.pinyin_to_bopomofo("ng"))

    def test_unihan_takes_the_taiwan_value(self):
        with tempfile.TemporaryDirectory() as d:
            u = R.load_unihan(write(d, "u.txt", UNIHAN))
        self.assertEqual((u["丙"], u["戊"]), ("ㄆㄚˊ", "ㄆㄚ"))


class Rule3b(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)
        self.lex = R.ime.Lexicon(write(self.d.name, "lex.txt", LEXICON))
        self.ev = R.char_evidence(self.lex)

    def test_evidence_is_score_weighted_over_multi_character_words(self):
        self.assertAlmostEqual(self.ev["甲"]["ㄆㄚ"], 1e-4)
        self.assertAlmostEqual(self.ev["甲"]["ㄅㄚ"], 10 ** -5.5)
        self.assertNotIn("丙", self.ev)

    def test_decide(self):
        d = R.decide_reading
        self.assertEqual(d("甲", ["ㄅㄚ", "ㄆㄚ"], self.ev), ("ㄆㄚ", "multi-char"))
        self.assertEqual(d("丙", ["ㄅㄚ", "ㄆㄚˊ"], self.ev), (None, None))                                  # no evidence, no Unihan
        self.assertEqual(d("丙", ["ㄅㄚ", "ㄆㄚˊ"], self.ev, {"丙": "ㄆㄚˊ"}), ("ㄆㄚˊ", "unihan"))
        self.assertEqual(d("丙", ["ㄅㄚ", "ㄆㄚˊ"], self.ev, {"丙": "ㄇㄚ"}), (None, None))                  # Unihan value not among the tied readings
        self.assertEqual(d("己", ["ㄅㄚ", "ㄆㄚ"], self.ev), (None, None))                                  # evidence tied exactly: undecided
        self.assertEqual(d("己", ["ㄅㄚ", "ㄆㄚ"], self.ev, {"己": "ㄅㄚ"}), ("ㄅㄚ", "unihan"))

    def run_tool(self, sents, unihan=None, hetero="", rule_3b=True):
        """readings.main on the synthetic lexicon; returns (out rows, stderr)."""
        d = self.d.name
        src = write(d, "in.txt", "".join(f"|{s}\n" for s in sents))
        dst = os.path.join(d, "out.txt")
        moe = write(d, "moe.json", json.dumps([]))
        err = io.StringIO()
        with mock.patch.object(R.ime, "Lexicon", lambda path: self.lex), mock.patch.object(R, "MOEDICT", moe), \
             mock.patch.object(R, "HETERO", write(d, "hetero.list", hetero)), mock.patch.object(R, "OVERRIDES", write(d, "ov.tsv", "# none\n")), \
             contextlib.redirect_stderr(err):
            R.main(src, dst, unihan, rule_3b)
        return open(dst, encoding="utf-8").read().splitlines(), err.getvalue()

    def test_main_breaks_a_single_character_tie(self):
        out, err = self.run_tool(["甲"])
        self.assertEqual(out, ["|甲|ㄆㄚ"])               # file order would give ㄅㄚ
        self.assertNotIn("CHECK\t", err)

    def test_rule_3b_is_off_by_default(self):
        out, err = self.run_tool(["甲"], rule_3b=False)    # the old behaviour: file order, flagged CHECK
        self.assertEqual(out, ["|甲|ㄅㄚ"])
        self.assertEqual(err.count("CHECK\t"), 1)
        u = write(self.d.name, "u.txt", UNIHAN)
        out, _ = self.run_tool(["丙", "戊"], unihan=u, rule_3b=False)   # a Unihan file alone changes nothing
        self.assertEqual(out, ["|丙|ㄅㄚ", "|戊|ㄅㄚ"])
        self.assertFalse(R.Reader(self.lex, {}, {}, {}).rule_3b)

    def test_main_falls_back_to_unihan_then_to_check(self):
        u = write(self.d.name, "u.txt", UNIHAN)
        out, err = self.run_tool(["丙", "戊", "己"], unihan=u)
        self.assertEqual(out[0:2], ["|丙|ㄆㄚˊ", "|戊|ㄆㄚ"])
        self.assertEqual(out[2], "|己|ㄅㄚ")                  # undecided: first in file order, flagged
        self.assertEqual(err.count("CHECK\t"), 1)
        self.assertIn("己: 暫用 ㄅㄚ", err)
        out, err = self.run_tool(["丙"])                      # no Unihan file: the old behaviour (CHECK, file order)
        self.assertEqual(out, ["|丙|ㄅㄚ"]); self.assertEqual(err.count("CHECK\t"), 1)

    def test_rules_around_it_are_unchanged(self):
        out, err = self.run_tool(["庚"])                       # a 0.8 lead: rule 4 takes the best score, not file order
        self.assertEqual(out, ["|庚|ㄆㄚ"]); self.assertNotIn("CHECK\t", err)
        out, _ = self.run_tool(["甲"], hetero="甲 ㄅㄚ\n")      # rule 3 (heterophony list) comes first
        self.assertEqual(out, ["|甲|ㄅㄚ"])


class InvertedReport(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.lex = R.ime.Lexicon(write(d.name, "lex.txt", LEXICON))

    def rows(self, chars, unihan=None):
        return {(r[0], r[1]): r[2:] for r in R.inverted_report(self.lex, unihan, chars)}

    def test_main_reading_goes_above_the_alternate(self):
        rows = self.rows("乙")
        self.assertEqual(rows[("乙", "ㄧˇ")], (-7.3, -6.8 + R.INVERTED_LEAD, "main", "multi-char"))
        self.assertEqual(rows[("乙", "ㄧˋ")], (-6.8, -6.8, "alt", "multi-char"))

    def test_already_in_order_is_left_alone_and_undecided_is_flagged(self):
        rows = self.rows("辛壬癸")                      # 辛: evidence for ㄧˋ (already on top, no change), 壬: for ㄧˇ (lifted), 癸: none
        self.assertEqual(rows[("辛", "ㄧˋ")][:3], (-6.8, -6.8, "main"))
        self.assertEqual(rows[("壬", "ㄧˇ")][1], -6.8 + R.INVERTED_LEAD)
        self.assertEqual({rows[("癸", r)][2:] for r in ("ㄧˇ", "ㄧˋ")}, {("CHECK", "")})
        self.assertEqual(rows[("癸", "ㄧˋ")][:2], (-6.8, -6.8))

    def test_maintainer_decision_wins(self):
        with mock.patch.dict(R.INVERTED_DECIDED, {"癸": "ㄧˇ"}):   # 癸 has no evidence (CHECK without the decision)
            rows = self.rows("癸")
        self.assertEqual(rows[("癸", "ㄧˇ")], (-7.0, -6.8 + R.INVERTED_LEAD, "main", "maintainer"))
        self.assertEqual(rows[("癸", "ㄧˋ")][2], "alt")

    def test_apply_changes_only_the_main_scores(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        src, out = write(d.name, "src.txt", LEXICON), os.path.join(d.name, "out.txt")
        self.assertEqual(R.apply_inverted(src, out, R.inverted_report(R.ime.Lexicon(src), None, "乙辛癸")), 1)   # 辛 already on top, 癸 CHECK
        a, b = open(src, encoding="utf-8").read().splitlines(), open(out, encoding="utf-8").read().splitlines()
        self.assertEqual([(x, y) for x, y in zip(a, b) if x != y], [(next(l for l in a if l.startswith("ㄧˇ 乙 ")), "ㄧˇ 乙 -6.300000")])
        self.assertEqual(len(a), len(b))

    def test_report_does_not_write_the_lexicon(self):
        before = os.path.getmtime(os.path.join(R.REPO, "data", "lexicon", "mcbpmf-data.txt"))
        self.rows("乙")
        self.assertEqual(os.path.getmtime(os.path.join(R.REPO, "data", "lexicon", "mcbpmf-data.txt")), before)


if __name__ == "__main__":
    unittest.main()
