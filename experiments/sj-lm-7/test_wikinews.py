"""wikinews.py on a tiny synthetic .bz2 dump (docs/contracts/news-corpus.md section 3.2 / 3.6). The conversion table is a stub."""
import bz2
import contextlib
import io
import os
import pickle
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikinews as W  # noqa: E402

bc = W.bc
import build_counts_text as bct  # noqa: E402

# A phrase chain, so converting twice differs from converting once: 一伙人 -> 一夥人 -> 一群人.
CONV = ({"一伙人": "一夥人", "一夥人": "一群人"}, {"网": "網"}, 3)

# title -> body. SHA-256(title)[0], computed when this test was written: 條目3 -> 15 (< 32), 條目6 -> 32 (not < 32), 條目4 -> 33, 條目27 -> 28.
BODIES = {"條目0": "甲乙丙丁戊己庚辛", "條目1": "春夏秋冬風雨雷電", "條目2": "山川河海湖泊島嶼", "條目3": "金木水火土石雲霧",
          "條目4": "東南西北中外內左", "條目5": "紅橙黃綠藍靛紫黑", "條目6": "天地玄黃宇宙洪荒", "條目7": "日月盈昃辰宿列張",
          "條目27": "寒來暑往秋收冬藏", "條目8": "閏餘成歲律呂調陽"}
VAL = {"條目3", "條目27"}


def make_dump(path, pages=None):
    """pages: {title: raw wikitext}; default BODIES (each wrapped in a template and a full stop)."""
    pages = pages or {t: "{{x}}" + b + "。" for t, b in BODIES.items()}
    xml = [f"<page><title>{t}</title><ns>0</ns><id>{i}</id><revision><id>{1000 + i}</id><text xml:space=\"preserve\">{b}</text></revision></page>\n"
           for i, (t, b) in enumerate(pages.items())]
    xml.append("<page><title>條目9</title><ns>0</ns><text>#REDIRECT [[條目0]]</text></page>\n")
    xml.append("<page><title>討論:條目0</title><ns>1</ns><text>不該出現的討論頁內容</text></page>\n")
    with bz2.open(path, "wt", encoding="utf-8") as f:
        f.write("".join(xml))


class Split(unittest.TestCase):
    def test_same_title_same_decision(self):
        for t in BODIES:
            self.assertEqual(W.is_validation(t), W.is_validation(t))

    def test_fixed_threshold(self):
        self.assertTrue(W.is_validation("條目3"))      # first byte 15
        self.assertTrue(W.is_validation("條目27"))     # 28
        self.assertFalse(W.is_validation("條目6"))     # exactly 32: the boundary is "< 32"
        self.assertFalse(W.is_validation("條目4"))     # 33
        self.assertEqual({t for t in BODIES if W.is_validation(t)}, VAL)


class Export(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.dump = os.path.join(self.d.name, "d.xml.bz2")
        make_dump(self.dump)

    def run_export(self, split, *extra, name=None, dump=None):
        out = os.path.join(self.d.name, name or f"{split}.txt")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            W.main(["export", "--dump", dump or self.dump, "--split", split, "--out", out, *extra])
        return open(out, encoding="utf-8").read().splitlines(), buf.getvalue()

    def test_train_and_val_are_disjoint_and_cover_every_article(self):
        train, so_t = self.run_export("train")
        val, so_v = self.run_export("val")
        self.assertEqual(len(train) + len(val), len(BODIES))             # one sentence per article; redirect and non-article namespace are in neither
        self.assertFalse(set(train) & set(val))
        self.assertEqual(set(train) | set(val), set(BODIES.values()))
        self.assertEqual(set(val), {BODIES[t] for t in VAL})
        self.assertIn(f"train: {len(BODIES) - len(VAL)} articles", so_t)
        self.assertIn(f"val: {len(VAL)} articles", so_v)

    def test_train_output_has_no_text_of_a_val_article(self):
        train, _ = self.run_export("train")
        blob = "\n".join(train)
        for t in VAL:
            self.assertNotIn(BODIES[t][:4], blob)
            self.assertNotIn(BODIES[t][4:], blob)
        self.assertNotIn("討論頁", blob)
        self.assertNotIn("重定向", blob)

    def test_export_converts_nothing(self):
        """One SENT sentence per line after TEMPLATE / MARKUP, still in the source's own characters; there is no conversion option."""
        make_dump(self.dump, {"條目4": "{{x}}[[上网]]很好，真的。第二句\n另一行&lt;b&gt;一伙人"})
        train, _ = self.run_export("train")
        self.assertEqual(train, ["上网很好，真的", "第二句", "另一行一伙人"])   # entity unescaped, then the tag markup stripped
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            W.main(["export", "--dump", self.dump, "--split", "train", "--out", os.path.join(self.d.name, "x.txt"), "--no-trad-tw-phrases"])

    def test_counting_the_export_equals_counting_the_dump(self):
        """build_counts_text on the train export gives the counts build_counts' own article path gives on the same articles: one conversion."""
        pages = {"條目4": "{{x}}'''他們是一伙人'''，今天去[[學校|学校]]。我们上网。\n&lt;ref&gt;x&lt;/ref&gt;他說再見",
                 "條目6": "你好嗎。一伙人今天天氣很好{{y|z}}", "條目3": "金木水火土石雲霧。他說再見"}
        make_dump(self.dump, pages)
        train, _ = self.run_export("train")
        self.assertIn("今天去学校", "".join(train).replace("，", ""))                      # exported unconverted (the dump's 学)
        out_txt = os.path.join(self.d.name, "train.txt")
        raws = [r for t, r in pages.items() if not W.is_validation(t)]
        with mock.patch.object(bc, "load_conv", lambda: CONV), contextlib.redirect_stdout(io.StringIO()):
            bct.main([os.path.join(self.d.name, "c.pkl"), out_txt])
            bc._init(False, False)
            uni, bi, _, runs = bc.count_batch(raws)
        got = pickle.load(open(os.path.join(self.d.name, "c.pkl"), "rb"))
        self.assertGreater(runs, 3)
        self.assertEqual((got["runs"], dict(got["uni"]), dict(got["bi"])), (runs, dict(uni), dict(bi)))
        # a converted export would be converted again by the reader: 一伙人 -> 一夥人 -> 一群人
        self.assertEqual(bc.convert("一伙人", *CONV), "一夥人")
        self.assertEqual(bc.convert(bc.convert("一伙人", *CONV), *CONV), "一群人")

    def test_rows_format_for_validation(self):
        make_dump(self.dump, {"條目3": "{{x}}第一段，有123和ABC。還有二句！\n\n第二段<ref>x</ref>\t與tab", "條目4": "不該在val"})
        rows, so = self.run_export("val", "--format", "rows", name="val.tsv")
        self.assertEqual(rows, ["條目3\t1000\t第一段，有123和ABC。還有二句！", "條目3\t1000\t第二段 與tab"])   # blank lines dropped, tab flattened, nothing converted
        self.assertNotIn("條目4", "\n".join(rows))
        self.assertTrue(all(len(r.split("\t")) == 3 for r in rows))
        self.assertIn("val: 1 articles", so)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            W.main(["export", "--dump", self.dump, "--split", "val", "--format", "rows", "--out", os.path.join(self.d.name, "x.txt")])

    def test_articles_default_output_is_unchanged_and_meta_adds_title_and_revid(self):
        want = ["{{x}}" + b + "。" for b in BODIES.values()]
        self.assertEqual(list(bc.articles(100, dump=self.dump)), want)
        self.assertEqual(list(bc.articles(3, dump=self.dump)), want[:3])
        meta = list(bc.articles(100, dump=self.dump, meta=True))
        self.assertEqual([m[2] for m in meta], want)
        self.assertEqual(meta[0][:2], ("條目0", "1000"))
        self.assertEqual(meta[3][:2], ("條目3", "1003"))


if __name__ == "__main__":
    unittest.main()
