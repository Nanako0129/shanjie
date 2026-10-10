"""model-v5 契約 §2.1：--extra-lexicon（build_counts._init、build_counts_text.main）。不需要真正的 dump 或 OpenCC 檔。
用法：python3 -m unittest experiments/s2/test_extra_lexicon.py -v
"""
import os
import pickle
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402

LEXDIR = os.path.join(bc.ROOT, "data", "lexicon")
WORD = "哈囉喵"   # 基底沒有的詞：沒給 --extra-lexicon 時被拆開
ENTRY = "ㄏㄚ-ㄌㄨㄛ-ㄇㄧㄠ\t哈囉喵\t-1.0\ttest\n"   # 分數遠高於拆開的乘積


def fake_conv():
    return {}, {}, 1   # 空的轉換表：convert() 原樣回傳


class ExtraLexicon(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.extra = os.path.join(self.tmp.name, "extra.tsv")
        open(self.extra, "w", encoding="utf-8").write(ENTRY)
        p = mock.patch.object(bc, "load_conv", fake_conv); p.start(); self.addCleanup(p.stop)

    def lex(self, *extra):
        bc._init(False, False, False, extra)
        return bc._W["lex"]

    def test_without_option_equals_old_lexicon(self):
        old = bc.ime.Lexicon(os.path.join(LEXDIR, "mcbpmf-data.txt"), overlay=os.path.join(LEXDIR, "overlay-add.tsv"))
        new = self.lex()
        self.assertEqual(new.by_word, old.by_word)
        self.assertEqual(new.max_len, old.max_len)
        for text in ["今天天氣很好", WORD, "我們哈囉"]:
            self.assertEqual(bc.segment(new, text), bc.segment(old, text))
            self.assertEqual(bc.expected_counts(new, text), bc.expected_counts(old, text))

    def test_extra_word_is_one_token(self):
        self.assertNotIn(WORD, self.lex().by_word)
        self.assertNotEqual(bc.segment(self.lex(), WORD), [WORD])
        lex = self.lex(self.extra)
        self.assertEqual(bc.segment(lex, WORD), [WORD])
        uni, bi = bc.expected_counts(lex, WORD)
        self.assertGreater(uni[WORD], 0.99)
        self.assertGreater(bi[("<s>", WORD)], 0.99)
        self.assertLess(bc.expected_counts(self.lex(), WORD)[0].get(WORD, 0), 0.01)

    def test_additive_counts_keep_the_plain_counts(self):
        """契約 §8：有詞包時，一般的計數等於沒有詞包時的計數，詞包詞與含它的二元組另外加上。"""
        text = "我說" + WORD + "很好"
        bc._init(False, True)
        plain_uni, plain_bi = bc.run_expected(text)
        self.assertEqual((plain_uni, plain_bi), bc.expected_counts(self.lex(), text))
        bc._init(False, True, extra_lexicons=(self.extra,))
        self.assertEqual(bc._W["pack"], {WORD})
        uni, bi = bc.run_expected(text)
        for w, e in plain_uni.items():
            self.assertAlmostEqual(uni[w], e, places=9)
        for k, e in plain_bi.items():
            self.assertAlmostEqual(bi[k], e, places=9)   # lex0 的二元組都不含詞包詞，原樣保留
        self.assertGreater(uni[WORD], 0.99)
        # 詞包詞的每一次出現各有一個後繼與一個前導，期望次數加總等於它的單詞次數
        self.assertAlmostEqual(sum(e for (a, b), e in bi.items() if a == WORD), uni[WORD], places=6)
        self.assertAlmostEqual(sum(e for (a, b), e in bi.items() if b == WORD), uni[WORD], places=6)
        self.assertEqual(set(uni) - set(plain_uni), {WORD})

    def test_option_given_twice(self):
        second = os.path.join(self.tmp.name, "second.tsv")
        open(second, "w", encoding="utf-8").write("ㄇㄧㄠ-ㄏㄚ-ㄌㄨㄛ\t喵哈囉\t-1.0\ttest\n")
        lex = self.lex(self.extra, second)
        self.assertEqual(bc.segment(lex, WORD + "喵哈囉"), [WORD, "喵哈囉"])

    def test_text_script_threads_option(self):
        src = os.path.join(self.tmp.name, "s.txt")
        open(src, "w", encoding="utf-8").write(WORD + "\n")
        for extra, want in [([], False), (["--extra-lexicon", self.extra], True)]:
            out = os.path.join(self.tmp.name, f"o{want}.pkl")
            bct.main(extra + [out, src])
            self.assertEqual(WORD in pickle.load(open(out, "rb"))["uni"], want)
            out = os.path.join(self.tmp.name, f"e{want}.pkl")
            bct.main(["--expected"] + extra + [out, src])
            self.assertEqual(pickle.load(open(out, "rb"))["uni"].get(WORD, 0) > 0.99, want)

    def test_wiki_script_parses_option(self):
        with mock.patch.object(sys, "argv", ["x", "--extra-lexicon", self.extra, "--extra-lexicon", self.extra]), \
             mock.patch("multiprocessing.Pool", side_effect=RuntimeError("stop")) as pool:
            with self.assertRaises(RuntimeError):
                bc.main()
        self.assertEqual(pool.call_args.kwargs["initargs"][3], (self.extra, self.extra))


if __name__ == "__main__":
    unittest.main()
