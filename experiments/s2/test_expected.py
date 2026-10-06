"""S2n 契約 §6.3 的實作檢查：期望次數（build_counts.expected_counts）。
用法：python3 -m unittest experiments.s2.test_expected -v（或 python3 experiments/s2/test_expected.py）
"""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_counts as bc  # noqa: E402


def lexicon(**scores):
    """小詞庫：詞 -> log10 分數，介面和 ime.Lexicon 的 by_word／max_len 相同。"""
    by_word = {w: (None, s) for w, s in scores.items()}
    return SimpleNamespace(by_word=by_word, max_len=max(map(len, by_word)))


# 合起來的詞 10^-3，拆開 10^-2 * 10^-2 = 10^-4：最佳切法是合起來的詞；拆開的後驗 = 1e-4 / (1e-3 + 1e-4) = 1/11 = 0.0909，
# 嚴格介於 0.01 與 0.5 之間（契約 §6.3）。
POST_SPLIT = 1 / 11
LEX_HOU = lexicon(**{"十分鐘": -2.0, "後到": -3.0, "後": -2.0, "到": -2.0})
LEX_HAO = lexicon(**{"好吧": -3.0, "好": -2.0, "吧": -2.0})


def best_only(lex, text, min_count=bc.EXPECTED_MIN):
    """突變：只數最高分那一條切法（等於 segment()），其餘和 expected_counts 同形。"""
    ws = bc.segment(lex, text)
    if ws is None:
        return None
    uni, bi, prev = {}, {}, "<s>"
    for w in ws:
        uni[w] = uni.get(w, 0) + 1; bi[(prev, w)] = bi.get((prev, w), 0) + 1; prev = w
    bi[(prev, "</s>")] = 1
    return uni, bi


class Checks:
    """三個檢查；用 self.fn 當被測的 expected_counts，這樣同一組檢查也能拿去跑突變。"""
    fn = None

    def check_hou_dao(self):
        _, bi = self.fn(LEX_HOU, "十分鐘後到")
        self.assertAlmostEqual(bi.get(("後", "到"), 0.0), POST_SPLIT, delta=1e-9)

    def check_hao_ba(self):
        _, bi = self.fn(LEX_HAO, "好吧")
        self.assertAlmostEqual(bi.get(("好", "吧"), 0.0), POST_SPLIT, delta=1e-9)

    def check_conservation(self):
        # 詞長加權的期望詞數 = 字數；每個字位置被詞覆蓋的後驗總和 = 1（後者只有真正的前向後向看得到，見 TestExpected）
        lex = lexicon(**{"十分": -2.1, "分鐘": -2.3, "十": -1.5, "分": -1.2, "鐘": -2.0, "十分鐘": -3.0, "後": -2.0, "到": -2.2, "後到": -3.1})
        for text in ["十分鐘後到", "十分鐘", "分鐘後到到"]:
            uni, _ = self.fn(lex, text, 0)
            self.assertAlmostEqual(sum(len(w) * e for w, e in uni.items()), len(text), delta=1e-9)


class TestExpected(Checks, unittest.TestCase):
    fn = staticmethod(bc.expected_counts)

    def test_hou_dao(self):
        self.check_hou_dao()

    def test_hao_ba(self):
        self.check_hao_ba()

    def test_conservation_total(self):
        self.check_conservation()

    def test_best_path_is_joined(self):   # 前提：兩個小詞庫的最佳切法是合起來的詞，只數最佳切法時拆開的二元組是 0
        self.assertEqual(bc.segment(LEX_HOU, "十分鐘後到"), ["十分鐘", "後到"])
        self.assertEqual(bc.segment(LEX_HAO, "好吧"), ["好吧"])
        self.assertNotIn(("後", "到"), best_only(LEX_HOU, "十分鐘後到")[1])

    def test_conservation_per_position(self):
        lex = lexicon(**{"十分": -2.1, "分鐘": -2.3, "十": -1.5, "分": -1.2, "鐘": -2.0, "十分鐘": -3.0, "後": -2.0, "到": -2.2, "後到": -3.1})
        text = "十分鐘後到"
        n, ends, _, alpha, beta = bc.forward_backward(lex, text)
        cover = [0.0] * n
        for j in range(1, n + 1):
            for i, w, lp in ends[j]:
                if alpha[i] > -float("inf") and beta[j] > -float("inf"):
                    for p in range(i, j):
                        cover[p] += 10 ** (alpha[i] + lp + beta[j] - beta[0])
        for c in cover:
            self.assertAlmostEqual(c, 1.0, delta=1e-9)

    def test_prune_and_boundaries(self):
        uni, bi = bc.expected_counts(LEX_HAO, "好吧")
        self.assertAlmostEqual(bi[("<s>", "好吧")] + bi[("<s>", "好")], 1.0, delta=1e-9)   # 句首：每條從 0 開始的邊
        self.assertAlmostEqual(bi[("好吧", "</s>")] + bi[("吧", "</s>")], 1.0, delta=1e-9)
        lex = lexicon(**{"好吧": -3.0, "好": -2.0, "吧": -2.0, "甲": 0.0, "乙": 0.0, "甲乙": -9.0})
        self.assertNotIn("甲乙", bc.expected_counts(lex, "甲乙")[0])   # 後驗 1e-9 < EXPECTED_MIN
        self.assertIsNone(bc.expected_counts(LEX_HAO, "嗨"))


class TestMutation(Checks, unittest.TestCase):
    """突變（只數最高分切法）下：兩個手算檢查必須失敗，守恆照樣通過。"""
    fn = staticmethod(best_only)

    def test_mutant_fails_unit_checks(self):
        with self.assertRaises(AssertionError):
            self.check_hou_dao()
        with self.assertRaises(AssertionError):
            self.check_hao_ba()

    def test_mutant_passes_conservation(self):
        self.check_conservation()


if __name__ == "__main__":
    unittest.main()
