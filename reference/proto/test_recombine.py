"""解碼器依（字串, 最後一個詞）合併（docs/contracts/dec-recombine.md §1）：只依字串合併的舊規則在 `下來分析` 丟掉 `下｜來`，
新規則留著它；輸出的 n-best 每個字串只出現一次。不需要模型檔：假 LM 與假詞庫，分數和 core/src/lm.rs 同名測試相同的形狀。
執行：python3 -m unittest reference/proto/test_recombine.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lm as L  # noqa: E402


class FakeLex:
    max_len = 2
    by_reading = {
        ("ㄒㄧㄚˋ",): [("下", -1.0)],
        ("ㄌㄞˊ",): [("來", -1.0)],
        ("ㄒㄧㄚˋ", "ㄌㄞˊ"): [("下來", -1.0)],
        ("ㄈㄣ", "ㄒㄧ"): [("分析", -3.0)],
    }
    demote = {}


class FakeLM:
    """詞的分數只看（前一個詞, 這個詞）：`下來 -> 分析` 沒見過（只剩詞庫分數），`來 -> 分析` 見過很多次。"""
    bigram = {("<s>", "下"): -0.7, ("<s>", "下來"): -0.65, ("下", "來"): -0.5, ("來", "分析"): -0.5}

    def word(self, lam, v, w, lp):
        return self.bigram.get((v, w), lp)

    def eos(self, lam, v):
        return 0.0


def decode_by_surface(lex, syls, lm, lam=0.5):
    """合併前的規則（每個字串一條路徑），只給這個測試證明「舊規則會輸」。"""
    n = len(syls)
    hyps = [[] for _ in range(n + 1)]
    hyps[0] = [(0.0, ())]
    for i in range(1, n + 1):
        cand = {}
        for k in range(1, min(lex.max_len, i) + 1):
            for word, lp in lex.by_reading.get(tuple(syls[i - k:i]), ()):
                for s, ws in hyps[i - k]:
                    sc = s + lm.word(lam, ws[-1] if ws else "<s>", word, lp)
                    surface = "".join(ws) + word
                    if surface not in cand or sc > cand[surface][0]:
                        cand[surface] = (sc, ws + (word,))
        hyps[i] = sorted(cand.values(), key=lambda x: -x[0])
    return hyps[n]


SYLS = ["ㄒㄧㄚˋ", "ㄌㄞˊ", "ㄈㄣ", "ㄒㄧ"]


class Recombine(unittest.TestCase):
    def test_old_rule_loses_the_split_path(self):
        self.assertEqual(decode_by_surface(FakeLex, SYLS, FakeLM())[0][1], ("下來", "分析"))

    def test_new_rule_keeps_it_and_lists_the_surface_once(self):
        out = L.decode(FakeLex, SYLS, FakeLM(), "chat")
        self.assertEqual([ws for _, ws in out], [("下", "來", "分析")])

    def test_surface_reached_by_two_last_words_is_listed_once(self):
        # `下來` and `下｜來` end in different words, so both reach the end; the n-best keeps the better one only.
        out = L.decode(FakeLex, SYLS[:2], FakeLM(), "chat")
        self.assertEqual([ws for _, ws in out], [("下來",)])


if __name__ == "__main__":
    unittest.main()
