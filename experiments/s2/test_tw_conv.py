"""The permanent conversion guard of docs/contracts/tw-conv.md section 3. Local only (`make test-python`); a few seconds, no corpus: it needs the
OpenCC files in ~/.cache/shanjie/sources/opencc (hash-checked by build_counts) and the committed tw-conv.tsv.

  test_a1_*  a Taiwan sentence is changed by the fold rows and by nothing else, whatever kind of sentence it is judged to be
  test_a2_*  Simplified-and-back: every character that does not come back is a listed one-to-many pair (tw-conv-ambiguous.tsv)
  test_a3_*  `--native` is counted as written (build_counts_text.py and derisk-1015/e1/count.py)
  test_a4_*  tw-conv-simp.tsv (Simplified sentence -> its Taiwan writing) and the coverage of the table's changed rows by it
  test_table_*, test_builder_*  the table follows the fixed rules of 2.3 (checked from its own counts column)
  test_t_*   the tw-clean table of contract 11 (the tests write their own small tables to a temp dir, never the real one)
mutate_tw_conv.py runs the mutations M1-M27 against this file."""
import argparse
import collections
import contextlib
import hashlib
import importlib
import importlib.util
import json
import multiprocessing
import io
import os
import random
import pickle
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402
import build_tw_clean as btcl  # noqa: E402
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


# ---------------------------------------------------------------- contract 11: the tw-clean table
LEXICON = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
IDENT = ({}, {}, 1)   # convert() with empty tables: these sentences have no marker or Simplified characters, so it leaves them as they are
ARTICLE = "甲乙丙。丁戊己。庚辛壬。#井字。"   # four sentences: replaced, dropped, not in the table, a key that starts with #
CLEAN_ROWS = {"甲乙丙": "甲乙丁", "丁戊己": "", "#井字": "井號"}


def sha_of(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def spawn_job(raw):
    """Runs in a spawn child that never calls load_conv(): contract 11.2 says article_sentences reads the table itself."""
    return list(bc.article_sentences(raw, IDENT))


def fake_dump(d, pages):
    path = os.path.join(d, "fake.xml.bz2")
    import bz2
    with bz2.open(path, "wt", encoding="utf-8") as f:
        f.write("".join(f"<page>\n<ns>0</ns>\n<text xml:space=\"preserve\">{t}</text>\n</page>\n" for t in pages))
    return path


class TwClean(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ)
        env.start(); self.addCleanup(env.stop)
        self.addCleanup(lambda: (bc.TW_CLEAN.clear(), bc._TW_CLEAN.update(state=None, off_said=False)))   # the next reader reads the table again
        bc._TW_CLEAN.update(state=None, off_said=False)

    def table(self, rows=CLEAN_ROWS, tw=None, lex=None, name="tw-clean.tsv", text=None):
        """Writes a table with a header of this checkout's two hashes (or the given ones); points SHANJIE_TW_CLEAN at it; -> its path."""
        path = os.path.join(self.tmp.name, name)
        head = f"# tw-conv.tsv {tw or sha_of(bc.TW_CONV)}\n# mcbpmf-data.txt {lex or sha_of(LEXICON)}\n# entries {len(rows)}\n"
        open(path, "w", encoding="utf-8", newline="\n").write(text if text is not None else head + "".join(f"{k}\t{v}\n" for k, v in rows.items()))
        os.environ["SHANJIE_TW_CLEAN"] = path
        return path

    def sentences(self, raw=ARTICLE, **kw):
        bc.load_tw_clean()
        return list(bc.article_sentences(raw, IDENT, **kw))

    def test_t_first_sentence_replaced(self):
        self.table()
        self.assertEqual(self.sentences()[0], "甲乙丁")

    def test_t_empty_second_column_not_yielded(self):
        self.table()
        self.assertNotIn("丁戊己", self.sentences())
        self.assertNotIn("", self.sentences())

    def test_t_a_sentence_not_in_the_table_is_unchanged(self):
        self.table()
        self.assertEqual(self.sentences(), ["甲乙丁", "庚辛壬", "井號"])

    def test_t_hash_key_is_looked_up(self):
        """Only the first three lines are the header: a data line that starts with `#` (even `# entries`) is a key."""
        self.table(rows={"#井字": "井號", "# entries 9": "x"})
        self.assertEqual(self.sentences()[-1], "井號")
        self.assertEqual(bc.TW_CLEAN["# entries 9"], "x")

    def test_t_table_format_keeps_spaces_and_carriage_returns(self):
        self.table(rows={" 甲 ": "乙 \r丙"})
        bc.load_tw_clean()
        self.assertEqual(bc.TW_CLEAN, {" 甲 ": "乙 \r丙"})

    def test_t_hits_are_counted_per_sentence_found(self):
        self.table()
        hits = collections.Counter()
        self.sentences(ARTICLE + "甲乙丙。", hits=hits)
        self.assertEqual(dict(hits), {"甲乙丙": 2, "丁戊己": 1, "#井字": 1})

    def test_t_header_hash_mismatch(self):
        for kw in ({"tw": "0" * 64}, {"lex": "0" * 64}):
            self.table(**kw)
            with self.assertRaises(SystemExit):
                bc.load_tw_clean()

    def test_t_bad_header_and_missing_tab_stop(self):
        for text in ("甲\t乙\n", "# tw-conv.tsv x\n# entries 0\n# mcbpmf-data.txt y\n"):
            self.table(text=text)
            with self.assertRaises(SystemExit):
                bc.load_tw_clean()
        self.table(text=open(self.table(), encoding="utf-8").read() + "沒有tab\n")
        with self.assertRaises(SystemExit):
            bc.load_tw_clean()

    def test_t_missing_table_stops(self):
        os.environ["SHANJIE_TW_CLEAN"] = os.path.join(self.tmp.name, "nope.tsv")
        with self.assertRaises(SystemExit) as cm:
            bc.load_tw_clean()
        self.assertIn("SHANJIE_TW_CLEAN=off", str(cm.exception.code))
        self.assertIn(os.path.join(self.tmp.name, "nope.tsv"), str(cm.exception.code))

    def test_t_unset_uses_the_pinned_default_and_a_placeholder_never_loads(self):
        path = self.table()
        del os.environ["SHANJIE_TW_CLEAN"]
        with mock.patch.object(bc, "TW_CLEAN_DEFAULT", path):
            with mock.patch.object(bc, "TW_CLEAN_SHA", "PLACEHOLDER"), self.assertRaises(SystemExit) as cm:
                bc.load_tw_clean()
            self.assertIn("SHANJIE_TW_CLEAN=off", str(cm.exception.code))
            with mock.patch.object(bc, "TW_CLEAN_SHA", sha_of(path)):
                bc.load_tw_clean()
                self.assertEqual(bc.TW_CLEAN, CLEAN_ROWS)
                self.assertEqual(bc.tw_clean_state(), "sha256:" + sha_of(path))
        with mock.patch.object(bc, "TW_CLEAN_DEFAULT", os.path.join(self.tmp.name, "nope.tsv")), self.assertRaises(SystemExit):
            bc.load_tw_clean()

    def test_t_off_is_the_output_before_contract_11(self):
        os.environ["SHANJIE_TW_CLEAN"] = "off"
        want = [bc.convert(x, *IDENT) for x in bc.SENT.findall(bc.article_text(ARTICLE))]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.sentences(), want)
            self.assertEqual(self.sentences(), want)
        self.assertEqual(err.getvalue(), "tw-clean: off\n", "said once per process")
        self.assertEqual(bc.tw_clean_state(), "off")

    def test_t_state_is_the_table_hash(self):
        path = self.table()
        bc.load_tw_clean()
        self.assertEqual(bc.tw_clean_state(), "sha256:" + sha_of(path))

    def test_t_a_spawned_process_reads_the_table_itself(self):
        self.table()
        with multiprocessing.get_context("spawn").Pool(1) as pool:
            got = pool.apply(spawn_job, (ARTICLE,))
        self.assertEqual(got[0], "甲乙丁")

    @unittest.skipUnless(os.path.isdir(os.path.join(bc.SRC, "opencc")), "needs the OpenCC tables in ~/.cache/shanjie/sources")
    def test_t_count_through_init_hits_what_a_direct_lookup_hits(self):
        conv = bc.load_conv()   # the real conversion: the table keys are what it outputs
        sents = [bc.convert(x, *conv) for x in ("今天天氣很好", "我在學校", "你好嗎")]
        self.table(rows={sents[0]: "今天天氣不好", sents[1]: ""})
        bc.load_tw_clean()
        raws = ["今天天氣很好。我在學校。", "你好嗎。今天天氣很好。"]
        direct = collections.Counter()
        for raw in raws:
            list(bc.article_sentences(raw, conv, hits=direct))
        with mock.patch.dict(bc._W):
            bc._init()
            got = bc._count(raws)[5]
        self.assertEqual(set(got), {sents[0], sents[1]})
        self.assertEqual(got, direct)

    def e1(self):
        """experiments/derisk-1015/e1/count.py and sj-lm-7/wiki_text.py as importable modules (spawn children import them by name too)."""
        for sub in (("derisk-1015", "e1"), ("sj-lm-7",)):
            p = os.path.join(ROOT, "experiments", *sub)
            sys.path.insert(0, p)
            self.addCleanup(sys.path.remove, p)
        return importlib.import_module("count"), importlib.import_module("wiki_text")

    def test_t_count_py_wiki_sentences_is_article_sentences(self):
        count, _ = self.e1()
        self.table()
        bc.load_tw_clean()
        with mock.patch.dict(bc._W, {"conv": IDENT}):
            got = list(count.wiki_sentences(ARTICLE))
        self.assertEqual(got, list(bc.article_sentences(ARTICLE, IDENT)))
        self.assertEqual(got[0], "甲乙丁")

    @unittest.skipUnless(os.path.isdir(os.path.join(bc.SRC, "opencc")), "needs the OpenCC tables in ~/.cache/shanjie/sources")
    def test_t_state_count_py_meta(self):
        count, _ = self.e1()
        dump = fake_dump(self.tmp.name, [ARTICLE])
        path = self.table()
        for state in ("off", "sha256:" + sha_of(path)):   # `off` first: a fixed `sha256:` value in the entry fails here (M27)
            os.environ["SHANJIE_TW_CLEAN"] = "off" if state == "off" else path
            out = os.path.join(self.tmp.name, state[:3])
            with mock.patch.object(bc, "DUMP", dump), mock.patch.dict(bc._W), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                count.main(["wiki", "--out", out, "--articles", "1", "--procs", "1"])
            self.assertEqual(json.load(open(os.path.join(out, "meta-1.json")))["tw_clean"], state)

    @unittest.skipUnless(os.path.isdir(os.path.join(bc.SRC, "opencc")), "needs the OpenCC tables in ~/.cache/shanjie/sources")
    def test_t_state_wiki_text_side_file(self):
        _, wiki_text = self.e1()
        dump = fake_dump(self.tmp.name, [ARTICLE])
        path = self.table()
        for state in ("off", "sha256:" + sha_of(path)):   # `off` first: a fixed `sha256:` value in the entry fails here (M27)
            os.environ["SHANJIE_TW_CLEAN"] = "off" if state == "off" else path
            out = os.path.join(self.tmp.name, state[:3] + ".txt")
            with mock.patch.object(bc, "DUMP", dump), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                wiki_text.main([out, "--articles", "1", "--procs", "1"])
            self.assertEqual(open(out + ".tw-clean", encoding="utf-8").read(), state + "\n")

    def test_t_english_next_writes_its_state_and_stops_early(self):
        """tools/build_english_next.py --wiki: the side file is written (review F1: `bc` was unbound in main), and a missing table stops in the
        parent before any pool starts (review F2: it hung in the workers)."""
        sys.path.insert(0, os.path.join(ROOT, "tools")); self.addCleanup(sys.path.remove, os.path.join(ROOT, "tools"))
        ben = importlib.import_module("build_english_next")
        out = os.path.join(self.tmp.name, "en.tsv")
        argv = ["build_english_next.py", "--lexicon", "x", "--out", out, "--wiki", "dump", "--jobs", "2"]
        os.environ["SHANJIE_TW_CLEAN"] = "off"
        with mock.patch.object(sys, "argv", argv), mock.patch.object(ben, "build", return_value=("t\n", {})), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            ben.main()
        self.assertEqual(open(out + ".tw-clean", encoding="utf-8").read(), "off\n")
        os.environ["SHANJIE_TW_CLEAN"] = os.path.join(self.tmp.name, "nope.tsv")
        with mock.patch.object(sys, "argv", argv), mock.patch.object(ben, "build", side_effect=AssertionError("build ran")), \
                self.assertRaises(SystemExit) as cm:
            ben.main()
        self.assertIn("SHANJIE_TW_CLEAN=off", str(cm.exception.code))

    @unittest.skipUnless(os.path.isdir(os.path.join(bc.SRC, "opencc")), "needs the OpenCC tables in ~/.cache/shanjie/sources")
    def test_t_s7a_prep_runs_stops_before_the_pool(self):
        """experiments/s7a-tinylm/prep.py runs: a missing table stops in the parent, before mp.Pool (review F2)."""
        p = os.path.join(ROOT, "experiments", "s7a-tinylm"); sys.path.insert(0, p); self.addCleanup(sys.path.remove, p)
        prep = importlib.import_module("prep")
        os.environ["SHANJIE_TW_CLEAN"] = os.path.join(self.tmp.name, "nope.tsv")
        with mock.patch.object(prep.mp, "Pool", side_effect=AssertionError("pool started")), self.assertRaises(SystemExit) as cm:
            prep.cmd_runs(argparse.Namespace())
        self.assertIn("SHANJIE_TW_CLEAN=off", str(cm.exception.code))


class BuildTwClean(unittest.TestCase):
    """experiments/s2/build_tw_clean.py on a small hand-made gap file (the real gap data is not read)."""
    GAPS = {"gap1": [[0, 0, "甲"], [5, 1, "乙"], [3000, 2, "丙"], [7, 3, "丙"]], "gap2": [[1, 0, "丁"], [300000, 1, "戊"], [10, 2, "甲"]]}
    FINAL = {"accepted": {"g1-0": "甲一", "g1-1": "乙一", "g1-2": "丙一", "g2-0": "丁一", "g2-1": "戊一", "g2-2": "甲二"}, "review_issues": {"g1-1": ["still wrong"]}}

    def run_builder(self, d, tw=None, lex=None, out="t.tsv"):
        files = []
        for name, obj in (("gaps.json", self.GAPS), ("final.json", self.FINAL)):
            files.append(os.path.join(d, name))
            json.dump(obj, open(files[-1], "w", encoding="utf-8"), ensure_ascii=False)
        bdir = os.path.join(d, "batches"); os.makedirs(bdir, exist_ok=True)   # the proofread ids: the accepted and the review_issues ones
        with open(os.path.join(bdir, "b0001.tsv"), "w", encoding="utf-8") as f:
            f.writelines(f"{i}\tx\n" for i in sorted(set(self.FINAL["accepted"]) | set(self.FINAL["review_issues"])))
        argv = ["--gaps", files[0], "--final", files[1], "--gap-tw-conv-sha", tw or sha_of(btcl.TW_CONV), "--gap-lexicon-sha", lex or sha_of(btcl.LEXICON),
                "--out", os.path.join(d, out), "--batches", bdir]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            btcl.main(argv)
        return os.path.join(d, out), buf.getvalue()

    def test_t_builder_refuses_when_a_hash_differs(self):
        with tempfile.TemporaryDirectory() as d:
            for kw in ({"tw": "0" * 64}, {"lex": "0" * 64}):
                with self.assertRaises(SystemExit) as cm:
                    self.run_builder(d, **kw)
                self.assertIn("轉換表或詞庫改過", str(cm.exception.code))
                self.assertFalse(os.path.exists(os.path.join(d, "t.tsv")))
            r = subprocess.run([sys.executable, btcl.__file__, "--gaps", "x", "--final", "x", "--gap-tw-conv-sha", "0" * 64, "--gap-lexicon-sha", "0" * 64, "--out", os.path.join(d, "o"), "--batches", d],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)

    def test_t_builder_table_and_counts(self):
        """甲 has two different corrections (a conflict: dropped); 乙 is in review_issues (empty wins over its accepted value); 丙 is proofread from the
        a = 3000 row but also occurs in the a = 7 row, which has no id; 戊 only occurs at a = 300000."""
        with tempfile.TemporaryDirectory() as d:
            path, printed = self.run_builder(d)
            self.assertEqual(open(path, encoding="utf-8").read().split("\n"),
                             [f"# tw-conv.tsv {sha_of(btcl.TW_CONV)}", f"# mcbpmf-data.txt {sha_of(btcl.LEXICON)}", "# entries 4", "丁\t丁一", "丙\t丙一", "乙\t", "戊\t戊一", ""])
            self.assertEqual(printed.split("\n")[:2], ["rows 4, empty rows 1, conflicts 1", "expected hits (a < 2000): 3 expected hits (a < 200000): 3"])
            with self.assertRaises(FileExistsError):
                self.run_builder(d)   # never overwrites

    def test_t_builder_sent_but_unchanged_is_a_result(self):
        """A sentence proofread twice, changed once and left unchanged once (后髮座 in the real run), is a conflict and is dropped; a sentence
        proofread once and left unchanged gets no row."""
        gaps = {"gap1": [[0, 0, "后髮座"], [1, 1, "后髮座"], [2, 2, "其后"]], "gap2": [[3, 0, "里"]]}
        final = {"accepted": {"g1-0": "後髮座", "g1-2": "其後"}, "review_issues": {}}
        rows, conflicts, expected = btcl.build(gaps, final, sent=["g1-0", "g1-1", "g1-2", "g2-0"])
        self.assertEqual((rows, conflicts, expected), ({"其后": "其後"}, 1, (1, 1)))
        self.assertEqual(btcl.build(gaps, final)[0], {"后髮座": "後髮座", "其后": "其後"})   # without sent the unchanged copy is invisible

    def test_t_builder_reads_gap3_ids(self):
        """gap3 (Traditional sentences with an S character, contract 11): g3-<n> ids index gaps["gap3"] the same way."""
        gaps = {"gap1": [], "gap2": [], "gap3": [[2500, 0, "其余", "余", ""], [10, 1, "建筑", "筑", ""]]}
        final = {"accepted": {"g3-0": "其餘", "g3-1": "建築"}, "review_issues": {}}
        self.assertEqual(btcl.build(gaps, final, sent=["g3-0", "g3-1"]), ({"其余": "其餘", "建筑": "建築"}, 0, (1, 2)))

    def test_t_builder_table_loads_back(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ):
            path, _ = self.run_builder(d)
            os.environ["SHANJIE_TW_CLEAN"] = path
            try:
                bc.load_tw_clean()
                self.assertEqual(bc.TW_CLEAN, {"丁": "丁一", "丙": "丙一", "乙": "", "戊": "戊一"})
            finally:
                bc.TW_CLEAN.clear(); bc._TW_CLEAN["state"] = None


if __name__ == "__main__":
    unittest.main()
