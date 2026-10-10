"""count_lexicon.py（model-v5 契約 §9）：衝突讀音與它連續兩個以上音節的片段，詞包詞都不進計數。
執行：python3 -m unittest experiments/model-v5/test_count_lexicon.py"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import count_lexicon as cl  # noqa: E402

PACK = [
    ("ㄞˋ-ㄌㄧˋ-ㄎㄚˇ", "艾莉卡"),   # 衝突讀音本身
    ("ㄞˋ-ㄌㄧˋ", "艾莉"),           # 前兩個音節：片段
    ("ㄌㄧˋ-ㄎㄚˇ", "莉卡"),         # 後兩個音節：片段
    ("ㄞˋ-ㄎㄚˇ", "艾卡"),           # 不連續，不是片段
    ("ㄙㄨˋ-ㄋㄨㄛˊ", "宿儺"),       # 別的讀音，照常計數
    ("ㄙㄨˋ-ㄋㄨㄛˊ-ㄎㄚˇ", "艾莉"),  # 同一個詞的另一個讀音：詞被排除就整個詞排除
]


class CountLexicon(unittest.TestCase):
    def test_conflict_runs_and_whole_words_are_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            p = lambda n: os.path.join(d, n)
            with open(p("pack.tsv"), "w", encoding="utf-8") as f:
                f.write("# 註解\n" + "".join(f"{r}\t{w}\t-7.0\tacg\n" for r, w in PACK))
            with open(p("col.tsv"), "w", encoding="utf-8") as f:
                f.write("# 讀音\t保留\t排除\t理由\n")
            with open(p("extra.txt"), "w", encoding="utf-8") as f:
                f.write("ㄞˋ ㄌㄧˋ ㄎㄚˇ\n")
            cl.main(p("pack.tsv"), p("col.tsv"), p("extra.txt"), p("out.tsv"))
            with open(p("out.tsv"), encoding="utf-8") as f:
                kept = [l.split("\t")[1] for l in f]
        self.assertEqual(kept, ["艾卡", "宿儺"])


if __name__ == "__main__":
    unittest.main()
