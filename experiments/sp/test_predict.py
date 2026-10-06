"""predict.py 的單元檢查（契約 §2、§4.1）。用手造的小詞庫與替身語言模型，不讀真的資料、不連網。
用法：python3 experiments/sp/test_predict.py"""
import collections
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import predict as P  # noqa: E402
import ime  # noqa: E402


def mklex(rows):
    """rows: [(讀音 'ㄋㄞˇ-ㄔㄚˊ', 詞, lp)] -> 最小的 Lexicon（predict 只用 by_reading）。"""
    lex = ime.Lexicon.__new__(ime.Lexicon)
    lex.by_reading, lex.by_word = collections.defaultdict(list), {}
    for r, w, lp in rows:
        lex.by_reading[tuple(r.split("-"))].append((w, lp))
    for k in lex.by_reading:
        lex.by_reading[k].sort(key=lambda x: -x[1])
    lex.max_len = max(len(k) for k in lex.by_reading)
    return lex


class StubLM:
    """分數 = lp；succ 裡的詞是 v 的後繼詞（詞表與 BigramLM 同形：ids、vocab、ctx）。"""
    def __init__(self, succ=()):
        self.vocab = ["<s>"] + list(succ)
        self.ids = {w: i for i, w in enumerate(self.vocab)}
        self.ctx = {0: (1, 0.5, {self.ids[w]: 1 for w in succ})} if succ else {}

    def word(self, lam, v, w, lp):
        return lp


U = P.units_of
TINY = mklex([
    ("ㄋㄞˇ", "奶", -2.0), ("ㄋㄚˇ", "哪", -1.5), ("ㄋㄧˇ", "你", -1.0), ("ㄋㄚˋ", "那", -1.2),
    ("ㄋㄞˇ-ㄔㄚˊ", "奶茶", -3.0), ("ㄋㄚˊ-ㄊㄧㄝˇ", "拿鐵", -3.5),
    ("ㄔㄚˊ", "茶", -2.2), ("ㄔㄚˊ-ㄧㄝˋ", "茶葉", -1.8),
    ("ㄉㄚˋ", "大", -2.0), ("ㄉㄞˋ", "大", -3.5), ("ㄉㄜˊ", "得", -1.5), ("ㄉㄜ˙", "得", -2.5),
    ("ㄉㄚˋ-ㄐㄧㄚ", "大家", -3.0),
    ("ㄅㄚ˙", "吧", -2.0), ("ㄊㄚ", "他", -1.0), ("ㄊㄚˇ", "塔", -3.0), ("ㄊㄚˋ", "踏", -3.0),
    ("ㄐㄩㄝˊ", "絕", -2.0), ("ㄚ", "啊", -2.0),
    ("ㄇㄚ-ㄇㄚ-ㄇㄚ", "媽媽媽", -4.0), ("ㄇㄚ-ㄇㄧ-ㄇㄚ", "馬咪馬", -4.0),
])
IDX = P.Index(TINY)


def cand(keys=None, units=None):
    return {e[0]: e for e in IDX.candidates(units if units is not None else U(keys))}


def abbr(*cs):
    return [(tuple(c), False, "") for c in cs]


class Compat(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(P.parse_syl("ㄋㄞˇ"), (("ㄋ", "ㄞ"), "ˇ"))
        self.assertEqual(P.parse_syl("ㄐㄩㄝˊ"), (("ㄐ", "ㄩ", "ㄝ"), "ˊ"))       # 介音
        self.assertEqual(P.parse_syl("ㄚ"), (("ㄚ",), ""))                          # 首字元是 ㄚ，一聲
        self.assertEqual(P.parse_syl("ㄅㄚ˙"), (("ㄅ", "ㄚ"), "˙"))                # 輕聲

    def test_prefix_nai_cha(self):
        for ks in ("ㄋ", "ㄋㄞ", "ㄋㄞˇㄔ"):
            self.assertIn("奶茶", cand(ks), ks)
        self.assertNotIn("奶茶", cand("ㄋㄧ"))

    def test_abbreviation(self):
        self.assertIn("奶茶", cand(units=abbr("ㄋ", "ㄔ")))
        self.assertIn("奶茶", cand(units=abbr("ㄋㄞ", "ㄔㄚ")))
        self.assertNotIn("媽媽媽", cand(units=abbr("ㄇ", "ㄇ")))       # 兩個單位不和三字詞相容
        self.assertNotIn("馬咪馬", cand(units=abbr("ㄇ", "ㄇ")))
        self.assertIn("媽媽媽", cand(units=abbr("ㄇ", "ㄇ", "ㄇ")))
        self.assertFalse(P.compat_abbr(abbr("ㄇ", "ㄇ"), IDX.readings["媽媽媽"][0][0]))

    def test_first_tone(self):
        done = {"他", "塔", "踏"} & set(cand("ㄊㄚ "))
        self.assertEqual(done, {"他"})                                      # 完成：只和一聲
        self.assertEqual({"他", "塔", "踏"} & set(cand("ㄊㄚ")), {"他", "塔", "踏"})   # 未完成：四個聲調

    def test_neutral_tone(self):
        self.assertIn("吧", cand("ㄅㄚ˙"))
        self.assertNotIn("吧", cand("ㄅㄚˋ"))

    def test_dedupe_once(self):
        cs = IDX.candidates(U("ㄉ"))
        self.assertEqual([e[0] for e in cs].count("大"), 1)                  # 兩個讀音只出現一次
        self.assertEqual(len({e[0] for e in cs}), len(cs))

    def test_deterministic_order(self):
        a, b, c = ("甲", 0, (), "ㄅ"), ("乙乙", 0, (), "ㄅ-ㄅ"), ("乙", 0, (), "ㄆ")
        order = sorted([b, c, a], key=lambda e: P.det_key(e, -1.0))
        self.assertEqual([e[0] for e in order], ["甲", "乙", "乙乙"])        # 同分：音節數少、讀音字串小在前
        self.assertEqual(sorted([b, a], key=lambda e: P.det_key(e, -1.0))[0], a)

    def test_empty_query(self):
        self.assertEqual(IDX.candidates([]), [])
        self.assertEqual(IDX.candidates(U("")), [])


class ReadingSwitch(unittest.TestCase):
    def check(self, word, k_early, k_late, lp_early, lp_late):
        e, l = {x[0]: x for x in IDX.candidates(U(k_early))}, {x[0]: x for x in IDX.candidates(U(k_late))}
        self.assertLessEqual(set(l), set(e))                                # 集合單調
        self.assertIn(word, l)                                              # 字串仍在
        self.assertEqual(e[word][1], lp_early)
        self.assertEqual(l[word][1], lp_late)                               # 分數換成較晚讀音的 lp

    def test_da(self):
        self.check("大", "ㄉ", "ㄉㄞ", -2.0, -3.5)       # ㄉㄚˋ -> ㄉㄞˋ

    def test_de(self):
        self.check("得", "ㄉㄜ", "ㄉㄜ˙", -1.5, -2.5)    # ㄉㄜˊ -> ㄉㄜ˙


class Monotone(unittest.TestCase):
    def test_skip_check_of_earlier_units_breaks_it(self):
        # 三個單位時第 2 個單位必須和第 2 音節相容：馬咪馬（第 2 音節 ㄇㄧ）在第二個「ㄇㄚ 」之後已被排除
        sets = [{e[0] for e in IDX.candidates(U(ks))} for ks in ("ㄇㄚ ㄇ", "ㄇㄚ ㄇㄚ ㄇ")]
        self.assertIn("媽媽媽", sets[1])
        self.assertNotIn("馬咪馬", sets[1])
        self.assertTrue(sets[1] <= sets[0])


class Metrics(unittest.TestCase):
    """手算：小詞庫、替身模型（分數 = lp），v = <s>。"""
    LM = StubLM()

    def sample(self, W, syls):
        return P.eval_sample(IDX, self.LM, 0.5, W, syls, "<s>")

    def test_flip_rate(self):
        # 奶茶（K=6）：ㄋ→你、ㄋㄞ→奶（翻）、ㄋㄞˇ→奶、…ㄔ→奶茶（翻）、之後不變：2 次／(6−1)
        # 拿鐵（K=7）：ㄋ→你、ㄋㄚ→那（翻）、ㄋㄚˊ→拿鐵（翻）、之後不變：2 次／(7−1)
        r1, r2 = self.sample("奶茶", ("ㄋㄞˇ", "ㄔㄚˊ")), self.sample("拿鐵", ("ㄋㄚˊ", "ㄊㄧㄝˇ"))
        self.assertEqual((r1["K"], r1["flips"], r2["K"], r2["flips"]), (6, 2, 7, 2))
        self.assertEqual((r1["flips"] + r2["flips"], (r1["K"] - 1) + (r2["K"] - 1)), (4, 11))

    def test_single_char_interference(self):
        # 奶：P3 的候選是 奶(−2)、奶茶(−3)，第 1 名是單字；茶：茶葉(−1.8) 勝過 茶(−2.2)，第 1 名是兩音節
        rs = [self.sample("奶", ("ㄋㄞˇ",)), self.sample("茶", ("ㄔㄚˊ",))]
        self.assertEqual([r["p3_top_multi"] for r in rs], [False, True])    # 1／2

    def test_scan_depth(self):
        # ㄋ 的桶依 (−lp, 音節數, 讀音, 詞)：你 那 哪 奶 奶茶 拿鐵（共 6 條）
        u = U("ㄋ")
        self.assertEqual(P.scan_depth(IDX, u, frozenset({"你"}), limit=2), (3, True))   # 你（後繼，略過）那 哪
        self.assertEqual(P.scan_depth(IDX, u, frozenset(), limit=9), (6, False))        # 整桶不到 9 個：記桶的大小
        self.assertEqual(P.scan_depth(IDX, U("ㄋㄞ "), frozenset(), limit=9)[0], 6)     # 一聲 ㄋㄞ 沒有詞，掃完整桶

    def test_mcnemar_exact(self):
        self.assertEqual(P.mcnemar(0, 0), 1.0)
        self.assertAlmostEqual(P.mcnemar(0, 5), 2 / 32)
        self.assertAlmostEqual(P.mcnemar(3, 3), 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
