"""Tests for e4/build_e4ctx.py (synthetic texts only; no corpus, no conversion tables, no readings data).
Run: python3 -W ignore -m unittest experiments/derisk-1015/e4/test_e4ctx.py -v   (from the repo root)
"""
import bz2
import hashlib
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import build_e4ctx as b  # noqa: E402
from punct_ctx import MID  # noqa: E402  (imported, never redefined)
from subset import WORDLIKE  # noqa: E402

bc, bt = b.bc, b.bt
HAN_ONLY = re.compile(r"[一-鿿]{2,25}\Z")


def fake_conv(t):   # stands in for bc.convert (needs the OpenCC tables)
    return t.replace("后", "後")


class CutTest(unittest.TestCase):
    def test_module_uses_the_shared_definitions(self):
        self.assertIs(b.punct_ctx.MID, MID)
        self.assertIs(b.subset.WORDLIKE, WORDLIKE)

    def test_cut_points(self):
        self.assertEqual(b.cuts("我今天吃飯，然後去學校,再回家;好"),
                         [("e4ctx-punct", "我今天吃飯，", "然後去學校"), ("e4ctx-punct", "我今天吃飯，然後去學校,", "再回家")])   # the 1-character run after ; is too short
        self.assertEqual(b.cuts("量一下Colab的羊毛，快1.5倍速，２０２０年很多"),
                         [("e4ctx-alnum", "量一下Colab", "的羊毛"), ("e4ctx-alnum", "量一下Colab的羊毛，快1.5", "倍速"),
                          ("e4ctx-alnum", "量一下Colab的羊毛，快1.5倍速，２０２０", "年很多")])

    def test_every_row_ends_with_its_class_and_the_sentence_is_han_only(self):
        texts = ["我今天吃飯，然後去學校", "量一下Colab的羊毛", "第3天我們出發，到了ＡＢ公司開會", "只有漢字沒有標點", "，開頭的逗號不算", "他說：你好嗎、我很好;謝謝大家",
                 "一二" + "字" * 30 + "，" + "很" * 25 + "," + "短", "a|b中文字，這行有直線字元"]
        seen = {"e4ctx-punct": 0, "e4ctx-alnum": 0}
        for t in texts:
            for kind, ctx, sent in b.cuts(t):
                seen[kind] += 1
                self.assertTrue(t.startswith(ctx + sent))
                self.assertRegex(sent, HAN_ONLY)
                self.assertNotIn("|", ctx)
                if kind == "e4ctx-punct":
                    self.assertIn(ctx[-1], MID)
                else:
                    self.assertTrue(WORDLIKE.match(ctx[-1]))
        self.assertGreaterEqual(min(seen.values()), 2)

    def test_length_bounds_and_start_of_sentence(self):
        self.assertEqual(b.cuts("好，" + "字" * 25), [("e4ctx-punct", "好，", "字" * 25)])
        self.assertEqual(b.cuts("好，" + "字" * 26), [])
        self.assertEqual(b.cuts("好，字"), [])
        self.assertEqual(b.cuts("，你好嗎"), [("e4ctx-punct", "，", "你好嗎")])
        self.assertEqual(b.cuts("你好嗎"), [])


class PickTest(unittest.TestCase):
    CAND = [(i // 3, f"前{i}，", "你好嗎") for i in range(300)]   # 100 sentences, 3 candidates each

    def test_same_seed_same_output_and_other_seed_or_source_differs(self):
        a = b.pick(self.CAND, "cv", "e4ctx-punct", 1, 40)
        self.assertEqual(a, b.pick(self.CAND, "cv", "e4ctx-punct", 1, 40))
        self.assertNotEqual(a, b.pick(self.CAND, "cv", "e4ctx-punct", 2, 40))
        self.assertNotEqual(a, b.pick(self.CAND, "wiki", "e4ctx-punct", 1, 40))
        self.assertEqual(len(a), 40)

    def test_at_most_one_row_per_original_sentence_and_the_cap(self):
        rows = b.pick(self.CAND, "cv", "e4ctx-punct", 1, 1500)
        self.assertEqual(len(rows), 100)
        self.assertEqual(len({int(c[1:c.index("，")]) // 3 for c, _ in rows}), 100)
        self.assertEqual(rows, sorted(rows, key=lambda r: int(r[0][1:r[0].index("，")])))   # input order kept


class ProcessingTest(unittest.TestCase):
    ARTICLE = ("{{Infobox|a={{b}}}}開頭的句子。今天天氣很好,我們去[[公園|大公園]]玩<ref>註</ref>！第2章后有三節：結構、內容<!--c-->；"
               "'''粗體'''在2020年出現\n新行的句子很短")

    def test_wiki_processing_equals_build_tunes_steps(self):
        # build_tune.main's loop body before HAN, written out again
        t = self.ARTICLE
        for _ in range(3):
            t = bc.TEMPLATE.sub("", t)
        for pat, rep in bc.MARKUP:
            t = pat.sub(rep, t)
        want = [fake_conv(s) for s in bc.SENT.findall(t)]
        self.assertEqual(b.wiki_sentences(self.ARTICLE, fake_conv), want)
        self.assertIn("第2章後有三節：結構、內容", want)           # punctuation, digits kept; converted
        self.assertTrue(all("{{" not in s and "[[" not in s and "<ref" not in s for s in want))

    def test_articles_before_300000_are_skipped_and_cv_uses_the_unconverted_line(self):
        def arts(limit):
            self.assertEqual(limit, 305_000)
            for i in range(300_000):
                yield "舊文章的句子，不要取這一句"
            yield "第一句的前半，後半也夠長。第二句的前半，後半也夠長"
        with tempfile.TemporaryDirectory() as td:
            cv = os.path.join(td, "colloquial"); os.makedirs(cv)
            tune = next(l for l in (f"我們先吃飯，然後去學校{i}" for i in range(50)) if bt.is_tune(l))
            rest = next(l for l in (f"我們先吃飯，然後去學校{i}" for i in range(50)) if not bt.is_tune(l))
            open(os.path.join(cv, "cv-x.tsv"), "w", encoding="utf-8").write(f"1\t{tune}\n2\t{rest}\n")
            with bz2.open(os.path.join(cv, "cmn_sentences.tsv.bz2"), "wt", encoding="utf-8") as f:
                f.write("")
            a = type("A", (), {"src": td, "part": "all"})
            with mock.patch.object(bc, "articles", arts), mock.patch.object(bc, "DUMP", os.path.join(td, "dump")):
                got, files = b.collect(lambda t: t, a)
        self.assertEqual([(c, s) for _, c, s in got["cv"]["e4ctx-punct"]], [("我們先吃飯，", "然後去學校")])   # only the is_tune line; its digit suffix is not a cut
        self.assertEqual([(c, s) for _, c, s in got["wiki"]["e4ctx-punct"]],
                         [("第一句的前半，", "後半也夠長"), ("第二句的前半，", "後半也夠長")])
        self.assertEqual(len({sid for sid, _, _ in got["wiki"]["e4ctx-punct"]}), 2)   # two original sentences


class MainTest(unittest.TestCase):
    def run_main(self, out, seed, src):
        def fake_readings(rows, workdir, name):
            return [f"{c}|{s}|{' '.join('ㄅ' for _ in s)}\n" for c, s in rows]
        with open(os.devnull, "w") as devnull, mock.patch.object(bc, "load_conv", lambda: (None, None, 0)), \
                mock.patch.object(bc, "convert", lambda t, *a: t), mock.patch.object(b, "add_readings", fake_readings), \
                mock.patch.object(sys, "stdout", devnull), mock.patch.object(bc, "SRC", bc.SRC), mock.patch.object(bt, "SRC", bt.SRC), \
                mock.patch.object(bc, "DUMP", bc.DUMP):   # main() assigns these three globals from --src; the patches restore them
            b.main(["--out-dir", out, "--src", src, "--part", "cv", "--seed", str(seed), "--cap", "30"])
        return out

    def test_end_to_end_on_a_synthetic_source(self):
        with tempfile.TemporaryDirectory() as td:
            cv = os.path.join(td, "src", "colloquial"); os.makedirs(cv)
            lines = [f"{i}\t我們{i}先吃飯，然後去學校，再回家休息{i}天,看電視Ab接著睡覺" for i in range(400)]
            open(os.path.join(cv, "cv-x.tsv"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
            with bz2.open(os.path.join(cv, "cmn_sentences.tsv.bz2"), "wt", encoding="utf-8") as f:
                f.write("")
            src = os.path.join(td, "src")
            o1, o2, o3 = (self.run_main(os.path.join(td, n), seed, src) for n, seed in (("o1", 1), ("o2", 1), ("o3", 2)))
            h = lambda d, n: hashlib.sha256(open(os.path.join(d, n), "rb").read()).hexdigest()
            for n in ("e4ctx-punct.txt", "e4ctx-alnum.txt"):
                self.assertEqual(h(o1, n), h(o2, n))      # same seed, same file
                self.assertNotEqual(h(o1, n), h(o3, n))   # other seed, other sample
            man = json.load(open(os.path.join(o1, "e4ctx.manifest.json")))
            self.assertEqual(man["outputs"]["e4ctx-punct.txt"], h(o1, "e4ctx-punct.txt"))
            for kind in ("punct", "alnum"):
                rows = [l.rstrip("\n").split("|") for l in open(os.path.join(o1, f"e4ctx-{kind}.txt"), encoding="utf-8")]
                self.assertTrue(0 < len(rows) <= 30)
                for ctx, sent, reading in rows:
                    self.assertRegex(sent, HAN_ONLY)
                    self.assertEqual(len(sent), len(reading.split()))
                    if kind == "punct":
                        self.assertIn(ctx[-1], MID)
                    else:
                        self.assertTrue(WORDLIKE.match(ctx[-1]))
                self.assertEqual(len({(c, s) for c, s, _ in rows}), len(rows))


if __name__ == "__main__":
    unittest.main()
