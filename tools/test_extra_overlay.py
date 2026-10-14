"""model-v6 §7 P3-4：build_lm、build_classes、kn_cont、lm_eval 的預設關 `--extra-overlay FILE`。
用小詞庫（暫存目錄裡的基底＋兩份疊加層）和 test_build_lm 的小計數跑：沒給參數時輸出和以前逐位元組相同；給了，檔案裡的詞進詞庫、
進異體類（起牀 只在額外檔裡）、進封頂詞集合，lm_eval 的摘要行最後多 `+xo:<sha8>`（Rust CLI 同格式）。
用法：python3 -B -m unittest tools/test_extra_overlay.py"""
import contextlib
import functools
import hashlib
import io
import os
import pickle
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for sub in ("tools", "reference/proto", "experiments/s2"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import build_classes  # noqa: E402
import build_lm  # noqa: E402
import ime  # noqa: E402
import kn_cont  # noqa: E402
import lm as L  # noqa: E402
import lm_eval  # noqa: E402
from test_build_lm import BY_READING, COLL, WIKI  # noqa: E402
from test_kn_cont import GOLDEN_TINY_MODEL_SHA  # noqa: E402

# 基底詞庫是 test_build_lm 的 BY_READING 拿掉 起牀；額外檔只有 起牀（同讀音，所以起床／起牀 併成一類只在給了 --extra-overlay 時發生）。
BASE = "".join(f"{'-'.join(r)} {w} {s}\n" for r, es in BY_READING.items() for w, s in es if w != "起牀")
OVERLAY_ADD = "ㄊㄚ\t他\t-2.0\tt\n".replace("他", "牠")     # 一列疊加層，確認 overlay-add.tsv 的詞照舊進封頂集合
EXTRA = "ㄑㄧˇ-ㄔㄨㄤˊ\t起牀\t-5.0\tnames\nㄎㄨˋ-ㄆㄥˊ\t酷澎\t-3.0\tnames\n"


def sha8(b):
    return hashlib.sha256(b).hexdigest()[:8]


class ExtraOverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        lexdir = os.path.join(t, "data", "lexicon")
        os.makedirs(lexdir)
        for name, text in (("mcbpmf-data.txt", BASE), ("overlay-add.tsv", OVERLAY_ADD), ("sandhi-add.tsv", ""), ("names-add.tsv", ""), ("extra.tsv", EXTRA)):
            open(os.path.join(lexdir, name), "w", encoding="utf-8").write(text)
        cls.extra = os.path.join(lexdir, "extra.tsv")
        for name, c in (("counts-200000.pkl", WIKI), ("counts-colloquial3.pkl", COLL)):
            pickle.dump(c, open(os.path.join(t, name), "wb"))
        cls.patches = [mock.patch.object(build_lm, "WORK", t), mock.patch.object(build_lm, "LEXDIR", lexdir),
                       mock.patch.object(build_classes, "ROOT", t), mock.patch.object(lm_eval, "ROOT", t),
                       mock.patch.object(ime, "OVERLAYS", [os.path.join(lexdir, f) for f in ("overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv")]),
                       mock.patch.object(L, "cap_overlay", functools.partial(L.cap_overlay, demote_path=None))]   # 真的 demote.tsv 對不上小詞庫
        for p in cls.patches:
            p.start()
        cls.cls_x = build_lm.variant_classes(BY_READING)         # 起牀 在內的類（test_build_lm 的小詞庫）
        cls.model_x = build_lm.build(extra_overlay=cls.extra)[0]
        cls.model_0 = build_lm.build()[0]
        cls.path_x, cls.path_0 = (os.path.join(t, n) for n in ("x.sjlm", "0.sjlm"))
        open(cls.path_x, "wb").write(cls.model_x)
        open(cls.path_0, "wb").write(cls.model_0)

    @classmethod
    def tearDownClass(cls):
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    # --- ime / build_lm ---
    def test_overlays_helper(self):
        self.assertEqual(ime.overlays(), ime.OVERLAYS)
        self.assertEqual(ime.overlays(self.extra), ime.OVERLAYS + [self.extra])

    def test_lexicon_gets_the_extra_words_and_nothing_else_changes(self):
        with_x = ime.Lexicon(os.path.join(self.tmp.name, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.overlays(self.extra))
        without = ime.Lexicon(os.path.join(self.tmp.name, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.overlays())
        self.assertTrue({"起牀", "酷澎"} <= set(with_x.by_word))
        self.assertTrue({"起牀", "酷澎"}.isdisjoint(without.by_word))
        self.assertEqual({w: v for w, v in with_x.by_word.items() if w not in ("起牀", "酷澎")}, without.by_word)

    def test_absent_flag_is_the_old_model_and_present_merges_the_extra_variant(self):
        plain = ime.Lexicon(os.path.join(self.tmp.name, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.OVERLAYS)
        self.assertEqual(self.model_0, build_lm.build(build_lm.variant_classes(plain.by_reading))[0])   # 沒給：只讀 ime.OVERLAYS，和以前同一條路
        self.assertEqual(build_lm.build(self.cls_x)[0], self.model_x)    # 給 --extra-overlay = 直接用含 起牀 的類表建
        self.assertEqual(hashlib.sha256(self.model_x).hexdigest(), GOLDEN_TINY_MODEL_SHA)
        self.assertEqual(build_lm.merged_counts()[2].get("起床"), None)
        self.assertEqual(build_lm.merged_counts(extra_overlay=self.extra)[2]["起床"], ("起床", "起牀"))
        lm_x, lm_0 = (L.BigramLM(p, classes=False) for p in (self.path_x, self.path_0))
        self.assertEqual(lm_x.count("起牀"), lm_x.count("起床"))        # 併成一類：計數相同
        self.assertNotEqual(lm_0.count("起牀"), lm_0.count("起床"))

    def test_build_lm_main_passes_the_flag(self):
        for extra, want in (((), self.model_0), (("--extra-overlay", self.extra), self.model_x)):
            out = os.path.join(self.tmp.name, "main.sjlm")
            with mock.patch.object(sys, "argv", ["build_lm.py", "--out", out, *extra]), contextlib.redirect_stdout(io.StringIO()):
                build_lm.main()
            self.assertEqual(open(out, "rb").read(), want)

    # --- kn_cont ---
    def run_kn(self, *extra):
        out = os.path.join(self.tmp.name, "kn.sjkn")
        with mock.patch.object(sys, "argv", ["kn_cont.py", "--lm", self.path_x, "--theta", "1", "--beta", "1", "--out", out, *extra]), \
                contextlib.redirect_stdout(io.StringIO()):
            kn_cont.main()
        return open(out, "rb").read()

    def test_kn_cont_variant_classes_include_the_extra_words(self):
        vocab = L.BigramLM(self.path_x, classes=False).vocab
        bi = build_lm.merged_counts(self.cls_x)[1]
        want = kn_cont.side_bytes(self.path_x, kn_cont.continuation(bi, self.cls_x, 1, vocab), self.cls_x, 1, 1.0)
        self.assertEqual(self.run_kn("--extra-overlay", self.extra), want)
        self.assertNotEqual(self.run_kn(), want)      # 沒給：起牀 不在類裡，ΣN′ 與 N 都不同

    # --- build_classes ---
    def test_build_classes_producible_includes_the_extra_words(self):
        seen = []
        def fake(edges, cls_file, lm_path, producible):
            seen.append(set(producible))
            return b"", {}
        for extra in ((), ("--extra-overlay", self.extra)):
            out = os.path.join(self.tmp.name, "c.sjc")
            with mock.patch.object(sys, "argv", ["build_classes.py", "--edges", "e", "--cls", "c", "--lm", "m", "--out", out, *extra]), \
                    mock.patch.object(build_classes, "build", fake), contextlib.redirect_stdout(io.StringIO()):
                build_classes.main()
        self.assertNotIn("起牀", seen[0])
        self.assertEqual(seen[1], seen[0] | {"起牀", "酷澎"})

    # --- lm_eval ---
    def run_eval(self, *extra):
        rows = os.path.join(self.tmp.name, "rows.txt")
        open(rows, "w", encoding="utf-8").write("|起牀|ㄑㄧˇ ㄔㄨㄤˊ\n")
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["lm_eval.py", "--lm", self.path_x, "--profile", "chat", "--rows", rows, "--no-classes", *extra]), \
                contextlib.redirect_stdout(buf):
            lm_eval.main()
        return buf.getvalue().rstrip("\n")

    def test_lm_eval_marker_is_last_before_the_stats_and_absent_without_the_flag(self):
        want = sha8(open(self.extra, "rb").read())
        self.assertRegex(self.run_eval("--extra-overlay", self.extra), rf"^## rows\.txt  lm-chat-noclasses\+xo:{want}  \{{'n': 1,")
        self.assertNotIn("+xo", self.run_eval())
        dump = os.path.join(self.tmp.name, "d.txt")
        for extra, has in (((), False), (("--extra-overlay", self.extra), True)):    # 額外檔的詞真的進了解碼用的詞庫
            self.run_eval("--dump", dump, *extra)
            self.assertEqual("起牀" in open(dump, encoding="utf-8").read(), has)
        self.assertEqual(lm_eval.xo_tag(None), "")

    def test_lm_eval_marker_follows_the_kn_tag(self):
        kn = os.path.join(self.tmp.name, "kn2.sjkn")
        open(kn, "wb").write(self.run_kn("--extra-overlay", self.extra))
        line = self.run_eval("--extra-overlay", self.extra, "--kn", kn)
        self.assertRegex(line, rf"-noclasses\+kn:[0-9a-f]{{8}}:θ1:β1\+xo:{sha8(open(self.extra, 'rb').read())}  \{{")

    def test_capping_set_has_the_names_layer(self):
        """model-v6 §2.5：names-add.tsv 的詞進封頂集合（Rust 的 capping_overlay 同一組）。"""
        oa = os.path.join(self.tmp.name, "data", "lexicon", "overlay-add.tsv")
        names = os.path.join(self.tmp.name, "names.tsv")
        open(names, "w", encoding="utf-8").write("ㄎㄨˋ-ㄆㄥˊ\t酷澎\t-3.0\tnames\n")
        self.assertEqual(lm_eval.overlay_words(oa, names=names), {"牠", "酷澎"})

    def test_capping_set_has_the_overlay_add_and_the_extra_words(self):
        oa = os.path.join(self.tmp.name, "data", "lexicon", "overlay-add.tsv")
        self.assertEqual(lm_eval.overlay_words(oa), {"牠"})
        self.assertEqual(lm_eval.overlay_words(oa, self.extra), {"牠", "起牀", "酷澎"})
        lm = L.BigramLM(self.path_x, classes=False)
        lex = ime.Lexicon(os.path.join(self.tmp.name, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.overlays(self.extra))
        capped = L.cap_overlay(lex, lm_eval.overlay_words(oa, self.extra), lm)
        self.assertAlmostEqual(capped.by_word["酷澎"][1], -3.0 - L.UNSEEN_OVERLAY_PENALTY)   # 語料沒見過的扣罰分，和 overlay-add.tsv 的詞一樣
        self.assertEqual(L.cap_overlay(lex, lm_eval.overlay_words(oa), lm).by_word["酷澎"][1], -3.0)   # 不在集合裡就不封頂


if __name__ == "__main__":
    unittest.main()
