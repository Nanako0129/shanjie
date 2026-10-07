"""predict3.py 的單元檢查（契約 docs/contracts/sp3-successor-first.md §3 第 1 項）。手造的小詞庫與替身模型。
用法：python3 experiments/sp/test_predict3.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import predict3 as P3  # noqa: E402
from test_predict import IDX, StubLM, U  # noqa: E402  第一片的小詞庫（奶 -2.0、哪 -1.5、你 -1.0、那 -1.2、奶茶 -3.0、拿鐵 -3.5…）


def tiers_for(keys, succ):
    lm = StubLM(succ)                      # 分數 = lp
    s = frozenset(succ)                    # 不經過 succ_words：它以 id(lm) 快取，替身被回收後 id 可能重用
    cands = P3.reference(IDX, U(keys), "P")
    sc = P3.scores(cands, lm, 0.5, "<s>")
    return P3.order_s(cands, sc, s), cands, sc


class Order(unittest.TestCase):
    def test_tiers(self):
        tiers, _, _ = tiers_for("ㄋ", ["奶茶", "拿鐵"])
        self.assertEqual([e[0] for e, _ in tiers[0]], ["奶茶", "拿鐵"])          # 後繼詞一層，依分數（-3.0 在 -3.5 前）
        self.assertEqual([e[0] for e, _ in tiers[1]], ["你", "那", "哪", "奶"])   # 其餘依分數

    def test_second_tier_rank_adds_first_tier(self):
        tiers, cands, sc = tiers_for("ㄋ", ["奶茶", "拿鐵"])
        self.assertEqual(P3.rank_s(tiers, "拿鐵"), 2)
        self.assertEqual(P3.rank_s(tiers, "你"), 3)     # 第二層第 1 個：前面有第一層的 2 個
        self.assertEqual(P3.rank_s(tiers, "奶"), 6)
        self.assertEqual(P3.rank_c(cands, sc, "奶茶"), 5)   # c 組依分數：你 那 哪 奶 奶茶

    def test_no_successor_equals_c(self):
        tiers, cands, sc = tiers_for("ㄋ", ["吧"])     # 吧 不和 ㄋ 相容
        self.assertEqual(tiers[0], [])
        for w in ("你", "那", "哪", "奶", "奶茶", "拿鐵"):
            self.assertEqual(P3.rank_s(tiers, w), P3.rank_c(cands, sc, w), w)


if __name__ == "__main__":
    unittest.main()
