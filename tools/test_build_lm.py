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

# 起床／起牀 同讀音（MERGE 牀→床）；占／佔 同讀音 ㄓㄢˋ，占（ㄓㄢ，占卜）不同讀音所以不併；他 是前文。
BY_READING = {
    ("ㄑㄧˇ", "ㄔㄨㄤˊ"): [("起床", -4.9), ("起牀", -7.2)],
    ("ㄓㄢˋ",): [("占", -3.0), ("佔", -3.5)],
    ("ㄓㄢ",): [("占", -3.0)],
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
        cls.lm = L.BigramLM(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_classes(self):
        self.assertEqual(self.cls, {"起床": ("起床", "起牀"), "起牀": ("起床", "起牀"), "占": ("佔", "占"), "佔": ("佔", "占")})
        self.assertNotIn("他", self.cls)   # 單獨成類的不進表；占卜 ㄓㄢ 的讀音只有 占 一個詞，不影響 ㄓㄢˋ 的類

    def test_members_equal(self):
        lm = self.lm
        self.assertEqual(lm.count("起床"), lm.count("起牀"))
        self.assertEqual(lm.count("起床"), 19)    # 10 + 4 + 口語 1×5
        self.assertEqual(lm.count("占"), lm.count("佔"))
        self.assertEqual(lm.count("佔"), 15)      # 佔 5 + 口語 占 2×5；佔 在詞庫裡沒有計數的成員也拿到同樣的數
        for a, b in (("起床", "起牀"), ("占", "佔")):
            for v in ("<s>", "他"):
                ent = lm.ctx[lm.ids[v]][2]
                self.assertEqual(ent.get(lm.ids[a], 0), ent.get(lm.ids[b], 0), (v, a, b))
        ent = lm.ctx[lm.ids["他"]][2]
        self.assertEqual(ent[lm.ids["起床"]], 4)   # 1 + 3
        self.assertEqual(ent[lm.ids["占"]], 2 + 5)  # 佔 2 + 口語 占 1×5

    def test_backoff_nonnegative(self):
        for v, (t, back, entries) in self.lm.ctx.items():
            self.assertGreaterEqual(back, 0, self.lm.vocab[v])
            self.assertGreaterEqual(t, sum(entries.values()), self.lm.vocab[v])   # 總數在寫回之後才算：先算會小於保留的條目總和
        self.assertEqual(self.lm.ctx[self.lm.ids["他"]][0], 4 + 4 + 7 + 7)   # (他,起床)(他,起牀)(他,占)(他,佔) 各寫回後的和

    def test_rust_loads(self):
        rows = os.path.join(self.tmp.name, "rows.txt")
        open(rows, "w", encoding="utf-8").write("|起床|ㄑㄧˇ ㄔㄨㄤˊ\n")
        r = subprocess.run(["cargo", "run", "--locked", "-q", "-p", "cli", "--bin", "shanjie-eval", "--", "--lm", self.path, "--profile", "chat", "--rows", rows],
                           cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("'n': 1", r.stdout)


if __name__ == "__main__":
    unittest.main()
