"""Tests for experiments/p2-data/clean.py. Synthetic strings only; nothing here is corpus text.
Needs the OpenCC files under ~/.cache/shanjie/sources/opencc (build_counts.load_conv, hash-checked) like the other experiments."""
import importlib.util
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
# this file's sibling is named clean.py like experiments/s7f-corpus/clean.py: load it under another name
spec = importlib.util.spec_from_file_location("p2clean", os.path.join(HERE, "clean.py"))
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

CONV = C.bc.load_conv()
SIMP = C.bc.TRAD_SIMP_ONLY


P = "這是一段很長的中文說明文字用來湊足漢字比例還有更多的字數喔嗎啊哈哈哈哈"   # 35 Han: leaves room for 15 non-Han characters under the 70% rule


def vid(n):
    return ("%011d" % n).replace("0", "a")


def frag(v, seg, start, end, text):
    return f"{v}-{seg:05d}-{start:08d}-{end:08d} {text}\n"


class Split(unittest.TestCase):
    def test_split_on_final_punctuation_and_newlines(self):
        self.assertEqual(C.split_sentences("今天天氣很好。你吃飯了嗎?好啊!\n我們走吧"),
                         ["今天天氣很好", "你吃飯了嗎", "好啊", "我們走吧"])

    def test_whitespace_next_to_han_removed_and_leaders_stripped(self):
        self.assertEqual(C.split_sentences("  1. 今天 天氣 很好  "), ["今天天氣很好"])
        self.assertEqual(C.split_sentences("- 我 buy 了 iPhone"), ["我buy了iPhone"])
        self.assertEqual(C.split_sentences("3.5倍很多"), ["3.5倍很多"])      # a decimal is not a list marker
        self.assertEqual(C.split_sentences("他說「我走了。」"), ["他說「我走了"])   # stray closing quote piece is empty after the leader strip

    def test_empty(self):
        self.assertEqual(C.split_sentences("。。\n\n!!"), [])


class Yodas(unittest.TestCase):
    def units(self, lines):
        return [u for u in C.yodas_units(lines)]

    def test_merge_consecutive_fragments(self):
        v = vid(1)
        L = [frag(v, 0, 0, 100, "我們今天"), frag(v, 1, 100, 200, "要去公園"), frag(v, 2, 200, 300, "玩耍")]
        self.assertEqual(self.units(L), [(f"{v}:0", "我們今天要去公園玩耍")])

    def test_gap_breaks(self):
        v = vid(1)
        L = [frag(v, 0, 0, 100, "我們今天"), frag(v, 1, 100 + C.YODAS_GAP, 300, "要去公園")]
        self.assertEqual([u[1] for u in self.units(L)], ["我們今天", "要去公園"])
        L = [frag(v, 0, 0, 100, "我們今天"), frag(v, 1, 100 + C.YODAS_GAP - 1, 300, "要去公園")]
        self.assertEqual([u[1] for u in self.units(L)], ["我們今天要去公園"])

    def test_final_punctuation_video_change_and_segment_skip_break(self):
        a, b = vid(1), vid(2)
        L = [frag(a, 0, 0, 10, "你好嗎。"), frag(a, 1, 10, 20, "我很好"), frag(a, 3, 20, 30, "跳號了"), frag(b, 4, 30, 40, "換片了")]
        self.assertEqual([u[1] for u in self.units(L)], ["你好嗎。", "我很好", "跳號了", "換片了"])

    def test_length_cap_and_latin_join_and_unparsed(self):
        v = vid(1)
        long = "字" * 25
        L = [frag(v, 0, 0, 10, long), frag(v, 1, 10, 20, long), frag(v, 2, 20, 30, "ok"), frag(v, 3, 30, 40, "go"), "garbage line\n"]
        got = self.units(L)
        self.assertEqual([u[1] for u in got if u[0]], [long, long + "ok go"])
        self.assertEqual(sum(u[0] is None for u in got), 1)


class Filters(unittest.TestCase):
    def r(self, s):
        return C.reject(s, SIMP)

    def test_pass(self):
        self.assertIsNone(self.r("今天天氣真的很好"))
        self.assertIsNone(self.r(P + "iPhone"))

    def test_length(self):
        self.assertEqual(self.r("你好嗎"), "han_len")                 # 3 Han
        self.assertIsNone(self.r("你好嗎啊"))                          # 4 Han
        self.assertIsNone(self.r("字" * 40))
        self.assertEqual(self.r("字" * 41), "han_len")

    def test_han_ratio(self):
        self.assertEqual(self.r("今天去買了new iPhone"), "han_ratio")  # 5 Han of 17 non-space characters
        self.assertIsNone(self.r("我們去買ab吧好嗎"))                   # 7 of 9 = 78%
        self.assertEqual(self.r("我們去買abcd吧好嗎"), "han_ratio")     # 7 of 11 = 64%

    def test_url_email_handle(self):
        for s in (P + "https://x.org", P + "www.abc", P + "google.com", P + "a@b.example.com", P + "@someone"):
            self.assertEqual(self.r(s), "url", s)

    def test_digits(self):
        self.assertEqual(self.r(P + "0912345678"), "digits")
        self.assertEqual(self.r(P + "０９１２３４５６７８"), "digits")      # full-width digits
        self.assertEqual(self.r(P + " 0912-345-678"), "digits")           # phone-like, 9 digits with separators
        self.assertIsNone(self.r(P + "2026-10-18"))                       # a date: 8 digits but not phone-shaped
        self.assertIsNone(self.r(P + "123456"))                          # 6 digits

    def test_code(self):
        for s in (P + " foo()", P + " a => b", P + "{x}", P + "\\n", P + "a.b(1)"):
            self.assertEqual(self.r(s), "code", s)

    def test_simplified_only_char(self):
        self.assertEqual(self.r("今天我们去公园玩耍"), "simplified")
        self.assertIsNone(self.r("今天我們去公園玩耍"))
        self.assertIsNone(self.r("我們在社群裡面聊天吧"))       # 群 is Taiwan-standard: the project's reduced set does not drop it
        self.assertIn("群", C.bc.SIMP_ONLY)                     # ... although the full OpenCC set maps it
        self.assertNotIn("群", SIMP)
        self.assertIsNone(self.r("我們後面再說好不好"))           # 后/後: a valid Traditional character is not Simplified-only

    def test_tw_phrase_flag(self):
        tw = C.TwFlag(C.bc.TW_PHRASE)
        self.assertTrue(tw.hits("我們用軟件來處理這件事"))        # 軟件 -> 軟體
        self.assertEqual(tw.hits("我們用軟體來處理這件事"), [])
        self.assertIsNone(self.r("我們用軟件來處理這件事"))        # flagged, not dropped


class Dedupe(unittest.TestCase):
    def test_key_normalisation(self):
        k = C.dedupe_key
        self.assertEqual(k("你好,世界"), k("你好，世界"))          # full-width comma
        self.assertEqual(k("我愛 iPhone"), k("我愛iphone"))        # spaces and case
        self.assertEqual(k("ＡＢＣ我愛你"), k("abc我愛你"))
        self.assertNotEqual(k("我愛你"), k("你愛我"))


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        with open(os.path.join(d, "rows.txt"), "w", encoding="utf-8") as f:
            f.write("昨天下午我們去看電影|明天早上我要先去銀行辦事|ㄇㄧㄥˊ\n|短句|ㄉ\n# comment\n")
        with open(os.path.join(d, "words.list"), "w", encoding="utf-8") as f:
            f.write("今天天氣很好\n")
        self.g = C.Guard(CONV)
        self.g.add_rows_file("rows", os.path.join(d, "rows.txt"), "rows.txt")
        self.g.add_rows_file("words", os.path.join(d, "words.list"), "words.list")
        self.g.build()

    def tearDown(self):
        self.tmp.cleanup()

    def test_sentence_field_contained_whole(self):
        self.assertEqual(self.g.hits("他說明天早上我要先去銀行辦事情")[0], {"rows"})
        self.assertEqual(self.g.hits("明天早上我要先去銀行辦事")[0], {"rows"})

    def test_context_field_and_punctuation_runs(self):
        self.assertEqual(self.g.hits("昨天下午我們去看電影，很好看")[0], {"rows"})
        self.assertEqual(self.g.hits("明天早上，我要先去銀行辦事")[0], {"rows"})    # equal once punctuation is ignored: the exact check catches it
        self.assertEqual(self.g.hits("明天早上，我要先去銀行辦事情")[0], set())   # containment is on Han runs, so punctuation inside breaks it

    def test_short_entries_are_not_patterns(self):
        self.assertEqual(self.g.hits("這個短句很短"), (set(), set()))

    def test_word_list_is_exact_only(self):
        self.assertEqual(self.g.hits("今天天氣很好")[0], {"words"})
        self.assertEqual(self.g.hits("今天天氣很好呢"), (set(), set()))

    def test_variant_fold(self):
        self.assertEqual(self.g.hits("明天早上我要先去銀行辦事")[0], {"rows"})
        self.assertEqual(self.g.hits("明天早上我要先去银行办事")[0], {"rows"})      # converted form matches too

    def test_short_entries_are_counted_not_dropped(self):
        # "今天天氣很好" (6 Han, a word-list line) is exact-only; a 6-character row sentence is `short`: dropped when equal, only counted when contained
        with open(os.path.join(self.tmp.name, "r2.txt"), "w", encoding="utf-8") as f:
            f.write("|不要這樣啦|ㄅ\n")
        g = C.Guard(CONV)
        g.add_rows_file("r2", os.path.join(self.tmp.name, "r2.txt"), "r2.txt")
        g.build()
        self.assertEqual(g.hits("不要這樣啦"), ({"r2"}, set()))
        self.assertEqual(g.hits("你不要這樣啦好嗎"), (set(), {"r2"}))
        g0 = C.Guard(CONV, contain_min=4)
        g0.add_rows_file("r2", os.path.join(self.tmp.name, "r2.txt"), "r2.txt")
        g0.build()
        self.assertEqual(g0.hits("你不要這樣啦好嗎"), ({"r2"}, set()))

    def test_unrelated(self):
        self.assertEqual(self.g.hits("完全不相干的一句話"), (set(), set()))


if __name__ == "__main__":
    unittest.main()
