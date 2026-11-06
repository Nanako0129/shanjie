"""The permanent conversion guard of docs/contracts/tw-conv.md section 3. Local only (`make test-python`); a few seconds, no corpus: it needs the
OpenCC files in ~/.cache/shanjie/sources/opencc (hash-checked by build_counts) and the committed tw-conv.tsv.

  test_a1_*  a Taiwan sentence is changed by the fold rows and by nothing else, whatever kind of sentence it is judged to be
  test_a2_*  Simplified-and-back: every character that does not come back is a listed one-to-many pair (tw-conv-ambiguous.tsv)
  test_a3_*  `--native` is counted as written (build_counts_text.py and derisk-1015/e1/count.py)
  test_a4_*  tw-conv-simp.tsv (Simplified sentence -> its Taiwan writing) and the coverage of the table's changed rows by it
  test_table_*, test_builder_*  the table follows the fixed rules of 2.3 (checked from its own counts column)
mutate_tw_conv.py runs the mutations M1-M18 against this file."""
import contextlib
import importlib.util
import io
import os
import random
import pickle
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402
import build_tw_conv as btc  # noqa: E402
import tw_conv_guard as guard  # noqa: E402

FIXTURE = os.path.join(HERE, "tw-conv-fixture.txt")
SIMP_ROWS = os.path.join(HERE, "tw-conv-simp.tsv")
MAX_AMBIGUOUS = 40   # contract 4.1
DEV = [os.path.join(ROOT, "eval", "dev", n) for n in ("user-typing.txt", "user-reported.txt")]
# a native file: every line has one of the spellings the table would change if the input were converted
NATIVE = ["爲了你好", "他着急了", "衣服溼了", "這個程序有問題", "安裝插件", "这个程序和插件"]


# A marker-only Simplified sentence that no STPhrases key covers (装 is in N, so it is converted; contract 7): assertion 4 row, kind and phrase check below.
NO_PHRASE = ("我装好了", "我裝好了")
MIXED = "這個國家的經濟發展关系着我們"   # a Traditional sentence with a few Simplified characters (contract 7: the S-phrases apply)
NOT_RARE = "后准于几里台干"
# contract 10.3: Traditional sentences with an S character, and 里 on the Simplified path (rows of tw-conv-simp.tsv; the kinds are asserted here)
R8_TRAD = [("秦國的首都咸陽在這裡，他们說", "秦國的首都咸陽在這裡，他們說"), ("朱兆由并州出兵，當時他們說这場戰役很難", "朱兆由并州出兵，當時他們說這場戰役很難"),
           ("他在逝世后被追封為國公，這是他们的榮譽", "他在逝世後被追封為國公，這是他們的榮譽"), ("這場戰爭的死者達万余人，他们說", "這場戰爭的死者達萬餘人，他們說")]
R8_LI = [("这位埃里克先生", "這位埃里克先生"), ("他们走了十万里路", "他們走了十萬里路"), ("在《红楼梦》里", "在《紅樓夢》裡"), ("在学校里", "在學校裡"),
         ("在这些人群里", "在這些人群裡"), ("在那个洞里有光", "在那個洞裡有光")]
LI_PTT = "在那个洞里有光"   # its 里 is decided by the PTT counts, not by the numeral, closing-mark or lexicon branch   # marker characters Taiwan writes as they are (contract 7): none of them may be in N


def taiwan_sentences():
    """The context and sentence fields of the two dev files and the fixture lines (comments and blanks left out; the fixture also holds the marker-only
    Taiwan sentences 我住在里 …, whose marker is not inside a phrase, so that M7 is caught)."""
    out = []
    for p in DEV:
        for line in open(p, encoding="utf-8"):
            f = line.rstrip("\n").split("|")
            out += [x for x in f[:2] if x]
    out += [l.rstrip("\n") for l in open(FIXTURE, encoding="utf-8") if l.strip() and not l.startswith("#")]
    return out


def simp_rows():
    return [tuple(l.rstrip("\n").split("\t")[:2]) for l in open(SIMP_ROWS, encoding="utf-8") if l.strip() and not l.startswith("#")]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ck = guard.Checker()
        cls.sentences = taiwan_sentences()


class Assertion1(Base):
    def test_a1_unchanged(self):
        bad = [(s, got, want) for s in self.sentences for got, want in [self.ck.unchanged(s)] if got != want]
        self.assertEqual(bad, [], f"{len(bad)} of {len(self.sentences)} Taiwan sentences are changed by convert() beyond the fold rows")

    def test_a1_the_fixture_has_the_incident_sentences(self):
        text = open(FIXTURE, encoding="utf-8").read()
        for s in ("在床上看程序", "為裡面的群眾提供支持", "今天去台南玩", "管理員權限", "插件", "吃不出來", "排泄物", "竈門炭治郎"):
            self.assertIn(s, text)

    def test_a1_a_mixed_example_gets_the_fold_rows(self):
        """Fixed example: the fold rows change 隨着時間他説 although it is a Traditional sentence (M5 removes the fold)."""
        self.assertEqual(self.ck.convert("隨着時間他説"), "隨著時間他說")
        self.assertEqual(bc.fold_tw("隨着時間他説"), "隨著時間他說")


class Assertion2(Base):
    def test_a2_roundtrip_pairs_are_listed(self):
        amb = guard.load_ambiguous()
        n, f1, f2, bad = guard.run(self.sentences, self.ck, amb)
        unlisted = {p: c for p, c in bad.items() if p not in amb}
        self.assertFalse(unlisted, f"{len(unlisted)} (expected, got) pairs after Simplified-and-back are not in tw-conv-ambiguous.tsv: {sorted(unlisted)}")

    def test_a2_the_ambiguous_list_is_small_and_every_row_has_a_reason(self):
        rows = [l.rstrip("\n").split("\t") for l in open(guard.AMBIGUOUS, encoding="utf-8") if l.strip() and not l.startswith("#")]
        self.assertLessEqual(len(rows), MAX_AMBIGUOUS)
        for r in rows:
            self.assertTrue(len(r) >= 3 and len(r[0]) == 1 and len(r[1]) == 1 and r[2].strip(), r)

    def test_a2_the_terms_are_replaced_longest_first(self):
        self.assertEqual(self.ck.replace_terms("軟件和互聯網絡"), "軟體和網際網路")


class Assertion3(unittest.TestCase):
    def counted_native_bct(self, text):
        with tempfile.TemporaryDirectory() as d:
            src, out = os.path.join(d, "s.txt"), os.path.join(d, "o.pkl")
            open(src, "w", encoding="utf-8").write(text + "\n")
            with mock.patch.dict(bc._W), contextlib.redirect_stdout(io.StringIO()):
                bct.main([out, "--native", src])
            return list(pickle.load(open(out, "rb"))["uni"])

    def counted_native_e1(self, text):
        e1 = os.path.join(ROOT, "experiments", "derisk-1015", "e1")
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "s.txt")
            open(src, "w", encoding="utf-8").write(text + "\n")
            sys.path.insert(0, e1)
            try:
                spec = importlib.util.spec_from_file_location("e1_count_for_tw_conv", os.path.join(e1, "count.py"))
                count = importlib.util.module_from_spec(spec); spec.loader.exec_module(count)
                with mock.patch.dict(bc._W), contextlib.redirect_stdout(io.StringIO()):
                    count.main(["text", "--out", d, "--tag", "t", "--native", src])
            finally:
                sys.path.remove(e1)
            return list(pickle.load(open(os.path.join(d, "unmapped", "counts-t.pkl"), "rb"))["uni"])

    def check(self, counted):
        words = counted("\n".join(NATIVE))
        for w in ("爲", "着", "溼", "程序", "插件", "这", "个"):
            self.assertIn(w, words, f"{w} is not counted as written")
        for w in ("為", "著", "程式", "外掛", "這", "個"):
            self.assertNotIn(w, words, f"{w} appears: a native input was converted")

    def test_a3_build_counts_text_native_as_written(self):
        self.check(self.counted_native_bct)

    def test_a3_count_py_native_as_written(self):
        self.check(self.counted_native_e1)


class Assertion4(Base):
    def test_a4_simplified_rows(self):
        rows = simp_rows()
        bad = [(s, self.ck.convert(s), want) for s, want in rows if self.ck.convert(s) != want]
        self.assertEqual(bad, [], f"{len(bad)} of {len(rows)} Simplified sentences do not convert to the expected Taiwan writing")

    def test_a4_coverage_of_the_changed_rows(self):
        """The rows fixed by the contract are there; every key the table changes against OpenCC's first value (folded), and each of the 31 MediaWiki
        keys, is in a Simplified sentence of the rows."""
        have = dict(simp_rows())
        absent = [s for s, want in (("这里", "這裡"), ("家里", "家裡"), ("公里", "公里"), ("冲积平原", "沖積平原"), ("之后他去了", "之後他去了"),
                                    ("奇希里斯", "奇希里斯"), ("后里", "后里"), ("今天去台南玩", "今天去台南玩")) if have.get(s) != want]
        self.assertFalse(absent, f"the fixed rows of contract 3.4 are missing or differ: {absent}")
        text = "\n".join(s for s, _ in simp_rows())
        o = bc.read_opencc()
        t = bc.read_tw_conv()
        F = bc.FOLD
        fold = lambda x: "".join(F.get(c, c) for c in x)
        need = set(btc.MEDIAWIKI)
        need |= {k for k, v in t["char"].items() if v != fold(o["st"][k][0])}
        changed = sorted(k for k, v in t["phrase"].items() if v != fold(o["stp"][k]))       # contract 8: phrases by a fixed sample
        tai = lambda k: fold(o["stp"][k]).replace("臺", "台").replace("檯", "台") == t["phrase"][k]
        rng = random.Random(20261106)
        other, only_tai = [k for k in changed if not tai(k)], [k for k in changed if tai(k)]
        need |= set(rng.sample(other, min(30, len(other)))) | set(rng.sample(only_tai, min(10, len(only_tai))))
        missing = sorted(k for k in need if k not in text)
        self.assertFalse(missing, f"{len(missing)} of {len(need)} changed rows have no Simplified sentence in tw-conv-simp.tsv (first: {missing[:8]})")


class Kinds(Base):
    """Contract 7: the kind a sentence is judged to be, and the set N."""
    def test_a4_sentence_kinds(self):
        self.assertEqual(bc.sentence_kind(MIXED), "trad")           # 么 and 关系着 mixed into Traditional text
        self.assertEqual(bc.sentence_kind("你在想什么"), "marker")    # 么 is a marker character, not Traditional evidence
        s, want = NO_PHRASE
        self.assertEqual(bc.sentence_kind(s), "marker")
        self.assertEqual(self.ck.convert(s), want)
        self.assertIn("装", bc.RARE)
        self.assertEqual([k for k in bc.read_opencc()["stp"] if "装" in k and k in s], [], "a STPhrases key covers 装: the row would not test N")

    def test_a4_the_new_rows_are_in_the_table(self):
        have = dict(simp_rows())
        for s, want in (("我们住在这条街里的房子", "我們住在這條街裡的房子"), ("你在想什么", "你在想什麼"), (MIXED, "這個國家的經濟發展關係著我們"), NO_PHRASE):
            self.assertEqual(have.get(s), want, s)

    def test_n_excludes_the_marker_characters_taiwan_writes(self):
        self.assertFalse([c for c in NOT_RARE if c in bc.RARE], "a character Taiwan writes as it is is in N")
        self.assertTrue(bc.RARE, "N is empty: the table has no `N` column")


class Revision8(Base):
    """Contract 10: the R2 path of a Traditional sentence with an S character, the keep words, the 里 rule."""
    def test_r8_rows_are_in_the_table_and_their_kinds(self):
        have = dict(simp_rows())
        for s, want in R8_TRAD:
            self.assertEqual(have.get(s), want, s)
            self.assertEqual(bc.sentence_kind(s), "trad", s)
            self.assertTrue(any(c in bc.TRAD_SIMP_ONLY for c in s), s)
        for s, want in R8_LI:
            self.assertEqual(have.get(s), want, s)
            self.assertEqual(bc.sentence_kind(s), "simp", s)

    def test_r8_the_ptt_row_is_decided_by_the_counts(self):
        phrase, char, maxp = self.ck.conv
        i = LI_PTT.index("里")
        self.assertEqual(bc.li_decide(bc._longest(LI_PTT[:i], phrase, maxp, char), char.get(LI_PTT[i + 1], LI_PTT[i + 1])), ("裡", "ptt"))

    def test_r8_tables(self):
        for c in "于后几万据种么":
            self.assertIn(c, bc.R2, c)
        for c in "里余采云党":
            self.assertNotIn(c, bc.R2, c)
        self.assertTrue({"咸陽", "并州", "皇后", "准許"} <= bc.KEEP)
        self.assertFalse([w for w in bc.KEEP if any(c in bc.TRAD_SIMP_ONLY for c in w)], "a keep word has an S character")
        self.assertTrue(bc.LI["prev"] and bc.LI["next"], "the li rows are not read")

    def test_r8_the_gate_sentence_is_in_the_fixture(self):
        self.assertIn("于美人說她明天會來", open(FIXTURE, encoding="utf-8").read())


class Table(unittest.TestCase):
    """tw-conv.tsv follows the fixed rules of contract 2.3, read back from its own counts column (manual rows excluded)."""
    @classmethod
    def setUpClass(cls):
        cls.t = btc.read_table()
        cls.variants = btc.dictionary_variants()

    @staticmethod
    def counts(s):
        return [(w, int(n)) for w, n in (x.rsplit(":", 1) for x in s.split())]

    def test_table_fold_rows(self):
        for x, (y, status, manual, counts, _) in self.t["fold"].items():
            c = dict(self.counts(counts))
            self.assertEqual(status == "fold", c[y] >= btc.FOLD_MIN and c[y] >= btc.FOLD_RATIO * c[x], x)

    def test_table_char_and_phrase_rows(self):
        """Contract 9: char rows change only on word-level evidence (>= WORD_RATIO and >= PICK_MIN) or by the MediaWiki rule; phrase rows
        follow the dictionary (dict, or over-dict at >= WORD_RATIO) or the 2x rule."""
        for k, (value, status, manual, counts, _) in self.t["char"].items():
            if manual:
                continue
            chars, _, words = counts.partition(" words ")
            if status == "evidence":
                w = self.counts(words)
                first, best = w[0], max(w[1:], key=lambda x: x[1])
                self.assertTrue(best[1] >= btc.WORD_RATIO * first[1] and best[1] >= btc.PICK_MIN, ("char", k))
                self.assertEqual(value, best[0], ("char", k))
            elif status == "mediawiki":
                self.assertIn(k, btc.MEDIAWIKI)
                self.assertLess(sum(n for _, n in self.counts(chars)), btc.PICK_MIN, ("char", k))
            elif status == "dict":           # contract 10.6: the dictionary's standard form; the variant is not >= 10x
                self.assertEqual(self.variants.get(self.t["char"][k][0]), None, ("char", k))
                self.assertIn(value, dict(self.counts(chars)), ("char", k))
            else:
                self.assertEqual(status, "hold", ("char", k))
        for k, (value, status, manual, counts, _) in self.t["phrase"].items():
            if manual:
                continue
            c = dict(self.counts(counts))
            if status == "over-dict":
                self.assertTrue(any(c[value] >= btc.WORD_RATIO * n and c[value] >= btc.PICK_MIN for v, n in c.items() if v != value), ("phrase", k))
            elif status == "dict":
                self.assertIn(value, c, ("phrase", k))
            else:
                order = sorted(c.items(), key=lambda x: -x[1])
                top, second = order[0][1], (order[1][1] if len(order) > 1 else 0)
                evidence = top >= btc.PICK_RATIO * second and top >= btc.PICK_MIN
                self.assertEqual(status, "evidence" if evidence else "hold", ("phrase", k))
                if evidence:
                    self.assertEqual(value, order[0][0], ("phrase", k))

    def test_table_has_the_terms_and_the_31_keys(self):
        self.assertEqual(len(self.t["term"]), 331)
        for k in btc.MEDIAWIKI:
            self.assertIn(k, self.t["char"])

    def test_builder_pick_thresholds(self):
        counts = {"a": 60, "b": 30, "c": 29, "d": 0, "e": 100, "f": 51}
        self.assertEqual(btc.pick(["a", "b"], counts, "b"), ("a", "evidence"))      # 2 times the second, >= 30
        self.assertEqual(btc.pick(["a", "f"], counts, "f"), ("f", "hold"))          # 60 < 2 * 51: hold keeps the first OpenCC value
        self.assertEqual(btc.pick(["b", "d"], counts, "d"), ("b", "evidence"))      # exactly 30
        self.assertEqual(btc.pick(["c", "d"], counts, "d"), ("d", "hold"))          # below 30
        self.assertEqual(btc.pick(["e"], counts, "e"), ("e", "evidence"))           # one candidate

    def test_builder_manual_beats_the_rule_and_a_manual_fold_changes_the_map(self):
        t = {"fold": {"爲": ("為", "fold", "", ""), "泄": ("洩", "nofold", "", ""), "仝": ("同", "fold", "仝@2026-11-07", "")}, "char": {}, "phrase": {}, "term": {}}
        self.assertEqual(btc.fold_of(t), {"爲": "為"})                                # a manual value equal to the key un-folds it
        t["fold"]["泄"] = ("洩", "nofold", "洩@2026-11-07", "")
        self.assertEqual(btc.fold_of(t), {"爲": "為", "泄": "洩"})


if __name__ == "__main__":
    unittest.main()
