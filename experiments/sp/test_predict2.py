"""predict2.py 的單元檢查（契約 docs/contracts/sp2-successor-prediction.md §3 第 1 項）。手造的小詞庫與替身模型，不讀真的資料。
用法：python3 experiments/sp/test_predict2.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import predict2 as P2  # noqa: E402
from test_predict import IDX, StubLM, U, abbr  # noqa: E402  第一片的小詞庫（奶茶、拿鐵、奶、哪、大…）


def words(units, mode, succ):
    return {e[0] for e in P2.candidates(IDX, units, mode, succ)}


class Successors(unittest.TestCase):
    def test_i_non_successor_excluded(self):
        # ㄋ 和 奶、哪、你、那、奶茶、拿鐵 都相容；只有後繼詞留下
        self.assertEqual(words(U("ㄋ"), "P", {"奶茶", "拿鐵", "哪"}), {"奶茶", "拿鐵", "哪"})

    def test_ii_abbreviation_only_in_PA(self):
        succ = {"奶茶", "拿鐵"}
        self.assertEqual(words(abbr("ㄋ", "ㄔ"), "P", succ), set())
        self.assertEqual(words(abbr("ㄋ", "ㄔ"), "PA", succ), {"奶茶"})

    def test_iii_empty_when_no_compatible_successor(self):
        self.assertEqual(words(U("ㄅ"), "P", {"奶茶"}), set())
        self.assertEqual(words(U("ㄅ"), "PA", {"奶茶"}), set())

    def test_iv_same_score_as_reference(self):
        lm = StubLM(["大", "大家"])                # 分數 = lp；大 的 ㄉㄚˋ -2.0 比 ㄉㄞˋ -3.5 高
        succ = P2.succ_words(lm, "<s>")
        for mode in ("P", "PA"):
            c = P2.candidates(IDX, U("ㄉ"), mode, succ)
            ref = {e[0]: s for e, s in zip(IDX.candidates(U("ㄉ")), P2.scores(IDX.candidates(U("ㄉ")), lm, 0.5, "<s>"))}
            for e, s in zip(c, P2.scores(c, lm, 0.5, "<s>")):
                self.assertEqual(s, ref[e[0]], (mode, e[0]))
        self.assertEqual({e[0]: e[1] for e in P2.candidates(IDX, U("ㄉ"), "P", succ)}["大"], -2.0)


class Metrics(unittest.TestCase):
    def test_v_population(self):
        self.assertEqual(P2.population(1, "喝", "我想喝"), "U")
        self.assertEqual(P2.population(2, "喝", "我想喝"), "M")
        self.assertEqual(P2.population(2, "<s>", ""), "S0")
        self.assertEqual(P2.population(2, "<s>", "OK，"), "S1")    # 前文非空，但 context_key 是空的 → v = <s>

    def test_v_summary(self):
        # P1 上 5 個樣本：(有顯示, 名次, |C|, 秒)
        rows = [(True, 1, 3, 0), (True, 7, 12, 0), (True, None, 5, 0), (False, None, 0, 0), (True, 2, 40, 0)]
        recs = [{"pos": {"P": {"P1": x}}} for x in rows]
        s = P2.summarize(recs, "P", "P1")
        self.assertEqual(s["n"], 5)
        self.assertEqual(s["shown"], 4)                      # 顯示率 4/5
        self.assertEqual(s["hit"], {1: 1, 5: 2, 9: 3})        # 分母是全部 5 個
        self.assertEqual(s["empty"], 1)                      # 空顯示率 1/4（有顯示但 W* 不在 C）
        self.assertEqual(s["size"], (12, 40, 40))            # 只算有顯示的 [3, 5, 12, 40]：p50 取第 2 個（0 起算）、p95 取第 3 個


if __name__ == "__main__":
    unittest.main()
