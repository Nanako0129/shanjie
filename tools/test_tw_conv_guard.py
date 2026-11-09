"""The permanent conversion guard of docs/spec/models.md §9.10. Local only (`make test-python`); a few seconds, no corpus: it needs the
OpenCC files in the sources folder of the cache (hash-checked by build_counts) and the committed data/lexicon/tw-conv.tsv.

  test_a1_*  a Taiwan sentence is changed by the fold rows and by nothing else, whatever kind of sentence it is judged to be
  test_a2_*  Simplified-and-back: every character that does not come back is a listed one-to-many pair (tw-conv-ambiguous.tsv)
  test_a3_*  `--native` is counted as written (build_counts_text.py)
  test_a4_*  tw-conv-simp.tsv (Simplified sentence -> its Taiwan writing) and the coverage of the table's changed rows by it
  test_t_*   the tw-clean table of docs/spec/models.md §9.8 (the tests write their own small tables to a temp dir, never the real one)
"""
import collections
import contextlib
import hashlib
import multiprocessing
import io
import os
import pickle
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))
import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402
import tw_conv_guard as guard  # noqa: E402

TESTDATA = os.path.join(HERE, "testdata", "tw-conv")
FIXTURE = os.path.join(TESTDATA, "tw-conv-fixture.txt")
SIMP_ROWS = os.path.join(TESTDATA, "tw-conv-simp.tsv")
MAX_AMBIGUOUS = 40   # docs/spec/models.md §9.10
DEV = [os.path.join(ROOT, "eval", "dev", n) for n in ("user-typing.txt", "user-reported.txt")]
# a native file: every line has one of the spellings the table would change if the input were converted
NATIVE = ["爲了你好", "他着急了", "衣服溼了", "這個程序有問題", "安裝插件", "这个程序和插件"]


# A marker-only Simplified sentence that no STPhrases key covers (装 is in N, so it is converted; docs/spec/models.md §9.2, §9.3): assertion 4 row, kind and phrase check below.
NO_PHRASE = ("我装好了", "我裝好了")
MIXED = "這個國家的經濟發展关系着我們"   # a Traditional sentence with a few Simplified characters (docs/spec/models.md §9.2, §9.3: the S-phrases apply)
NOT_RARE = "后准于几里台干"
# docs/spec/models.md §9.10: Traditional sentences with an S character, and 里 on the Simplified path (rows of tw-conv-simp.tsv; the kinds are asserted here)
R8_TRAD = [("秦國的首都咸陽在這裡，他们說", "秦國的首都咸陽在這裡，他們說"), ("朱兆由并州出兵，當時他們說这場戰役很難", "朱兆由并州出兵，當時他們說這場戰役很難"),
           ("他在逝世后被追封為國公，這是他们的榮譽", "他在逝世後被追封為國公，這是他們的榮譽"), ("這場戰爭的死者達万余人，他们說", "這場戰爭的死者達萬餘人，他們說")]
R8_LI = [("这位埃里克先生", "這位埃里克先生"), ("他们走了十万里路", "他們走了十萬里路"), ("在《红楼梦》里", "在《紅樓夢》裡"), ("在学校里", "在學校裡"),
         ("在这些人群里", "在這些人群裡"), ("在那个洞里有光", "在那個洞裡有光")]
# docs/spec/models.md §9.10: (a)-(e) of Revision10, Traditional sentences with an S character; (f), (g) are in TwClean
R10 = {"a": ("他說韋斯咸在这裡", "他說韋斯咸在這裡"), "b": ("人群涌入了廣場，他们說", "人群湧入了廣場，他們說"), "c": ("他住在東涌站附近，他们說", "他住在東涌站附近，他們說"),
       "d": ("他们在這個國家的河邊看見水涌", "他們在這個國家的河邊看見水涌"), "e": ("咸丰年間他们說", "咸豐年間他們說")}
R10_F = "他在韋斯咸，并說他们來了"   # an uncovered 咸 and an uncovered 并 (並 in the old output)
R10_G = "韋斯咸在咸水湖旁，他们說汹涌的河邊很美"   # an uncovered 咸 and the phrases 咸水湖, 汹涌
LI_COUNTS = "在那个洞里有光"   # its 里 is decided by the counts, not by the numeral, closing-mark or lexicon branch   # marker characters Taiwan writes as they are (docs/spec/models.md §9.2, §9.3): none of them may be in N


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


    def check(self, counted):
        words = counted("\n".join(NATIVE))
        for w in ("爲", "着", "溼", "程序", "插件", "这", "个"):
            self.assertIn(w, words, f"{w} is not counted as written")
        for w in ("為", "著", "程式", "外掛", "這", "個"):
            self.assertNotIn(w, words, f"{w} appears: a native input was converted")

    def test_a3_build_counts_text_native_as_written(self):
        self.check(self.counted_native_bct)



class Assertion4(Base):
    def test_a4_simplified_rows(self):
        rows = simp_rows()
        bad = [(s, self.ck.convert(s), want) for s, want in rows if self.ck.convert(s) != want]
        self.assertEqual(bad, [], f"{len(bad)} of {len(rows)} Simplified sentences do not convert to the expected Taiwan writing")



class Kinds(Base):
    """docs/spec/models.md §9.2, §9.3: the kind a sentence is judged to be, and the set N."""
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
    """docs/spec/models.md §9.5: the R2 path of a Traditional sentence with an S character, the keep words, the 里 rule."""
    def test_r8_rows_are_in_the_table_and_their_kinds(self):
        have = dict(simp_rows())
        for s, want in R8_TRAD:
            self.assertEqual(have.get(s), want, s)
            self.assertEqual(bc.sentence_kind(s), "trad", s)
            self.assertTrue(any(c in bc.TRAD_SIMP_ONLY for c in s), s)
        for s, want in R8_LI:
            self.assertEqual(have.get(s), want, s)
            self.assertEqual(bc.sentence_kind(s), "simp", s)

    def test_r8_the_counts_row_is_decided_by_the_counts(self):
        phrase, char, maxp = self.ck.conv
        i = LI_COUNTS.index("里")
        self.assertEqual(bc.li_decide(bc._longest(LI_COUNTS[:i], phrase, maxp, char), char.get(LI_COUNTS[i + 1], LI_COUNTS[i + 1])), ("裡", "counts"))

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


class Revision10(Base):
    """docs/spec/models.md §9.7: an uncovered 咸 of a Traditional sentence with an S character stays, an uncovered 涌 becomes 湧 only before 入現出至起動."""
    def row(self, k):
        s, want = R10[k]
        self.assertIn((s, want), simp_rows())
        self.assertEqual(bc.sentence_kind(s), "trad", s)
        self.assertTrue(any(c in bc.TRAD_SIMP_ONLY for c in s), s)
        self.assertEqual(self.ck.convert(s), want)

    def test_r10_a_a_name_with_xian_stays(self):
        self.row("a")

    def test_r10_b_yong_before_ru_converts(self):
        self.row("b")

    def test_r10_c_yong_before_zhan_stays(self):
        self.row("c")

    def test_r10_d_yong_at_the_end_stays(self):
        self.row("d")

    def test_r10_e_phrases_are_as_before(self):
        self.row("e")

    def test_r10_rev9_is_the_output_before_contract_12(self):
        self.assertEqual(bc.convert("他說韋斯咸在这裡", *self.ck.conv, rev9=True), "他說韋斯鹹在這裡")
        self.assertEqual(bc.convert(R10["c"][0], *self.ck.conv, rev9=True), "他住在東湧站附近，他們說")




# ---------------------------------------------------------------- docs/spec/models.md §9.8: the tw-clean table
LEXICON = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
IDENT = ({}, {}, 1)   # convert() with empty tables: these sentences have no marker or Simplified characters, so it leaves them as they are
ARTICLE = "甲乙丙。丁戊己。庚辛壬。#井字。"   # four sentences: replaced, dropped, not in the table, a key that starts with #
CLEAN_ROWS = {"甲乙丙": "甲乙丁", "丁戊己": "", "#井字": "井號"}


def sha_of(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def spawn_job(raw):
    """Runs in a spawn child that never calls load_conv(): docs/spec/models.md §9.8 says article_sentences reads the table itself."""
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

    def clean_with_real_tables(self, rows, raw):
        self.table(rows=rows)
        return list(bc.article_sentences(raw, bc.load_conv()))

    def test_r10_f_the_key_is_the_output_before_contract_12(self):
        """docs/spec/models.md §9.8: the table is keyed by the old output (咸 -> 鹹); a hit keeps its other corrections and takes the new 咸; a sentence not in the table uses the new output."""
        conv = guard.Checker().conv
        key = bc.convert(R10_F, *conv, rev9=True)
        self.assertEqual(key, "他在韋斯鹹，並說他們來了")
        self.assertEqual(bc.convert(R10_F, *conv), "他在韋斯咸，並說他們來了")
        got = self.clean_with_real_tables({key: "他在韋斯鹹，併說他們來了"}, R10_F + "。他說韋斯咸在这裡。")
        self.assertEqual(got, ["他在韋斯咸，併說他們來了", "他說韋斯咸在這裡"])

    def test_r10_g_a_phrase_covered_xian_and_yong_stay_converted_on_a_hit(self):
        conv = guard.Checker().conv
        new = bc.convert(R10_G, *conv)
        key = bc.convert(R10_G, *conv, rev9=True)
        self.assertEqual(new, "韋斯咸在鹹水湖旁，他們說洶湧的河邊很美")
        self.assertEqual(key, "韋斯鹹在鹹水湖旁，他們說洶湧的河邊很美")
        hit = self.clean_with_real_tables({key: key.replace("很美", "很大")}, R10_G + "。")
        self.assertEqual(hit, [new.replace("很美", "很大")])
        self.assertEqual(self.clean_with_real_tables({"無關": "x"}, R10_G + "。"), [new])

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










if __name__ == "__main__":
    unittest.main()
