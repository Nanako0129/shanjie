"""S2f 契約 §2.4／§3.1：build_lm 的異體寫法合併。用法：python3 -B -m unittest tools.test_build_lm（從 repo 根目錄）。
小語料、小類別表跑 build()，用參考實作 lm.BigramLM 讀回來檢查，再交給 Rust 的 shanjie-eval 載入。"""
import os
import pickle
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for sub in ("tools", "reference/proto", "experiments/s2"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import build_lm  # noqa: E402
import lm as L  # noqa: E402

# 起床／起牀 同讀音（MERGE 牀→床），代表 起床（分數高）；占／佔 都有 ㄓㄢˋ，但 占 另有 ㄓㄢ（占卜），讀音集合不同所以不併（修訂一 6.2.4）；
# 世界線／世界綫 同分，代表取 MERGE 正規形 世界線（綫→線 在 VARIANTS 裡；只比 code point 會選 綫 U+7DAB）；他 是前文。
BY_READING = {
    ("ㄑㄧˇ", "ㄔㄨㄤˊ"): [("起床", -4.9), ("起牀", -7.2)],
    ("ㄓㄢˋ",): [("占", -3.0), ("佔", -3.5)],
    ("ㄓㄢ",): [("占", -3.0)],
    ("ㄕˋ", "ㄐㄧㄝˋ", "ㄒㄧㄢˋ"): [("世界綫", -7.17), ("世界線", -7.17)],
    ("ㄊㄚ",): [("他", -2.0)],
}
WIKI = {"uni": {"起床": 10, "起牀": 4, "佔": 5, "他": 30},
        "bi": {("<s>", "起床"): 6, ("<s>", "起牀"): 2, ("他", "起床"): 1, ("他", "起牀"): 3, ("他", "佔"): 2, ("起牀", "</s>"): 4, ("起床", "</s>"): 10}}
COLL = {"uni": {"起牀": 1, "占": 2}, "bi": {("他", "占"): 1, ("<s>", "他"): 3}}


class BuildLm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        for name, c in (("counts-200000.pkl", WIKI), ("counts-colloquial3.pkl", COLL)):
            pickle.dump(c, open(os.path.join(cls.tmp.name, name), "wb"))
        build_lm.WORK = cls.tmp.name
        cls.cls = build_lm.variant_classes(BY_READING)
        data = build_lm.build(cls.cls)[0]
        cls.path = os.path.join(cls.tmp.name, "tiny.sjlm")
        open(cls.path, "wb").write(data)
        cls.lm = L.BigramLM(cls.path, classes=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_classes(self):
        self.assertEqual(self.cls, {"起床": ("起床", "起牀"), "起牀": ("起床", "起牀"),
                                    "世界線": ("世界線", "世界綫"), "世界綫": ("世界線", "世界綫")})   # 第一個是代表
        self.assertNotIn("占", self.cls)   # 讀音集合不同
        self.assertNotIn("他", self.cls)   # 單獨成類的不進表

    def test_merge_needs_same_readings(self):
        no_zhan1 = {k: v for k, v in BY_READING.items() if k != ("ㄓㄢ",)}
        self.assertEqual(build_lm.variant_classes(no_zhan1)["占"], ("占", "佔"))   # 拿掉 ㄓㄢ 就併（占 -3.0 分數高，是代表）

    def test_members_in_vocab_and_contexts_equal(self):
        lm = self.lm
        self.assertEqual(lm.count("起床"), lm.count("起牀"))
        self.assertEqual(lm.count("起床"), 19)    # 10 + 4 + 口語 1×5
        self.assertNotEqual(lm.count("占"), lm.count("佔"))
        self.assertEqual(lm.ctx[lm.ids["起床"]][2], lm.ctx[lm.ids["起牀"]][2])   # 當前文時條目相同
        self.assertEqual(lm.ctx[lm.ids["起床"]][2][lm.ids["</s>"]], 14)          # 4 + 10

    def test_next_word_only_on_representative(self):
        lm = self.lm
        for v in ("<s>", "他"):
            ent = lm.ctx[lm.ids[v]][2]
            self.assertNotIn(lm.ids["起牀"], ent, v)
        self.assertEqual(lm.ctx[lm.ids["<s>"]][2][lm.ids["起床"]], 8)   # 6 + 2
        self.assertEqual(lm.ctx[lm.ids["他"]][2][lm.ids["起床"]], 4)    # 1 + 3

    def test_context_total_counts_class_once(self):
        for v, (t, back, entries) in self.lm.ctx.items():
            self.assertGreaterEqual(back, 0, self.lm.vocab[v])
            self.assertGreaterEqual(t, sum(entries.values()), self.lm.vocab[v])
        self.assertEqual(self.lm.ctx[self.lm.ids["他"]][0], 11)   # 原始：1 + 3 + 2 + 口語 1×5（寫回每個成員會變 22）

    def test_totals_count_class_once(self):
        """修訂二 7.2.2：N 與 eos_total 每類只算一次（代表成員）。手算：起床類 19、佔 5、占 10、他 30 → 64；</s>：起床類 14。"""
        self.assertEqual(self.lm.N, 64)
        self.assertEqual(round(self.lm.p_eos * self.lm.N), 14)

    def test_real_lexicon_ties_pick_normal_form(self):
        """真實詞庫（基底＋疊加層）：最高分同分、而且類裡有 MERGE 正規形的，代表一律是正規形（修訂一 6.3 的同分列）。"""
        import build_counts as bc
        import ime
        lex = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.OVERLAYS)
        best = {}
        for ents in lex.by_reading.values():
            for w, lp in ents:
                best[w] = max(best.get(w, lp), lp)
        fold = lambda w: "".join(bc.MERGE.get(c, c) for c in w)
        cls = build_lm.variant_classes(lex.by_reading)
        tied = 0
        for g in set(cls.values()):
            top = max(best[w] for w in g)
            normal = [w for w in g if best[w] == top and fold(w) == w]
            if normal and sum(best[w] == top for w in g) > 1:
                tied += 1
                self.assertEqual(fold(g[0]), g[0], g)
        self.assertGreater(tied, 10)     # 修訂二的延伸規則拿掉多數疊加層舊字形後，同分而有正規形的類量到 23 個（修訂一時約 1,656）

    def test_rust_loads(self):
        rows = os.path.join(self.tmp.name, "rows.txt")
        open(rows, "w", encoding="utf-8").write("|起床|ㄑㄧˇ ㄔㄨㄤˊ\n")
        r = subprocess.run(["cargo", "run", "--locked", "-q", "-p", "cli", "--bin", "shanjie-eval", "--", "--lm", self.path, "--no-classes", "--profile", "chat", "--rows", rows],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("'n': 1", r.stdout)


if __name__ == "__main__":
    unittest.main()
