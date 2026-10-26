"""wikinews.py on a tiny synthetic .bz2 dump (docs/contracts/news-corpus.md section 3.2 / 3.6). The conversion table is a stub."""
import bz2
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wikinews as W  # noqa: E402

CONV = ({}, {"网": "網"}, 1)

# title -> body. SHA-256(title)[0], computed when this test was written: 條目3 -> 15 (< 32), 條目6 -> 32 (not < 32), 條目4 -> 33, 條目27 -> 28.
BODIES = {"條目0": "甲乙丙丁戊己庚辛", "條目1": "春夏秋冬風雨雷電", "條目2": "山川河海湖泊島嶼", "條目3": "金木水火土石雲霧",
          "條目4": "東南西北中外內左", "條目5": "紅橙黃綠藍靛紫黑", "條目6": "天地玄黃宇宙洪荒", "條目7": "日月盈昃辰宿列張",
          "條目27": "寒來暑往秋收冬藏", "條目8": "閏餘成歲律呂調陽"}
VAL = {"條目3", "條目27"}


def make_dump(path):
    pages = [f"<page><title>{t}</title><ns>0</ns><text xml:space=\"preserve\">{{{{x}}}}{b}。</text></page>\n" for t, b in BODIES.items()]
    pages.append("<page><title>條目9</title><ns>0</ns><text>#REDIRECT [[條目0]]</text></page>\n")
    pages.append("<page><title>討論:條目0</title><ns>1</ns><text>不該出現的討論頁內容</text></page>\n")
    with bz2.open(path, "wt", encoding="utf-8") as f:
        f.write("".join(pages))


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
        p = mock.patch.object(W.bc, "load_conv", lambda: CONV); p.start(); self.addCleanup(p.stop)

    def run_export(self, split, *extra):
        out = os.path.join(self.d.name, f"{split}.txt")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            W.main(["export", "--dump", self.dump, "--split", split, "--out", out, *extra])
        return open(out, encoding="utf-8").read().splitlines(), buf.getvalue()

    def test_train_and_val_are_disjoint_and_cover_every_article(self):
        train, so_t = self.run_export("train")
        val, so_v = self.run_export("val")
        self.assertEqual(len(train) + len(val), len(BODIES))             # one run per article; redirect and non-article namespace are not in either
        self.assertFalse(set(train) & set(val))
        self.assertEqual(set(train) | set(val), set(BODIES.values()))
        self.assertEqual(set(val), {BODIES[t] for t in VAL})
        self.assertIn(f"train: {len(train)} articles", so_t)
        self.assertIn(f"val: {len(val)} articles", so_v)

    def test_train_output_has_no_text_of_a_val_article(self):
        train, _ = self.run_export("train")
        blob = "\n".join(train)
        for t in VAL:
            self.assertNotIn(BODIES[t][:4], blob)
            self.assertNotIn(BODIES[t][4:], blob)
        self.assertNotIn("討論頁", blob)
        self.assertNotIn("重定向", blob)

    def test_output_is_what_the_counting_scripts_read(self):
        """One Han run per line, converted; the pipeline is build_counts' own (templates dropped, 网 -> 網)."""
        with bz2.open(self.dump, "wt", encoding="utf-8") as f:
            f.write("<page><title>條目4</title><ns>0</ns><text>{{x}}[[上网]]很好，真的。第二句</text></page>\n")
        train, _ = self.run_export("train")
        self.assertEqual(train, ["上網很好", "真的", "第二句"])

if __name__ == "__main__":
    unittest.main()
