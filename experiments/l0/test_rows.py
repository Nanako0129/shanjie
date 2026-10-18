import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rows  # noqa: E402


class Rows(unittest.TestCase):
    def test_clauses_and_prefix_within_a_document(self):
        got = rows.clauses_with_prefix(["今天天氣很好，我們去公園", "然後回家吃飯|好嗎"])
        self.assertEqual(got, [("", "今天天氣很好"), ("今天天氣很好，", "我們去公園"),
                               ("今天天氣很好，我們去公園", "然後回家吃飯")])   # 好嗎 is 2 characters; `|` never reaches a prefix

    def test_prefix_is_cut_and_sanitised(self):
        long = "甲" * 300
        (p, c), = rows.clauses_with_prefix([long[:250] + "|\n", "乙乙乙乙"])[-1:]
        self.assertEqual(c, "乙乙乙乙")
        self.assertEqual(len(p), rows.PREFIX_KEEP)
        self.assertNotIn("|", p); self.assertNotIn("\n", p)

    def test_pool_context_only_within_one_document(self):
        lines = ["yodas\tvidA:0\t大家好我是老師\n", "yodas\tvidA:2\t今天來分享運勢\n", "yodas\tvidB:0\t歡迎回到頻道\n",
                 "wildchat\th1:0\t請幫我寫一封信\n", "wildchat\th1:0:1\t內容是感謝老師\n", "wildchat\th1:2\t謝謝你的幫忙\n",
                 "tatoeba\t12\t我喜歡吃蘋果\n", "tatoeba\t13\t他不喜歡香蕉\n"]
        got = dict((c, p) for p, c in rows.pool_pairs(lines))
        self.assertEqual(got["今天來分享運勢"], "大家好我是老師")
        self.assertEqual(got["歡迎回到頻道"], "")
        self.assertEqual(got["內容是感謝老師"], "請幫我寫一封信")
        self.assertEqual(got["謝謝你的幫忙"], "")        # another turn of the same conversation
        self.assertEqual(got["他不喜歡香蕉"], "")

    def test_sample_unique_keeps_first_prefix_and_is_seeded(self):
        pairs = [("a", "甲乙丙丁"), ("b", "甲乙丙丁"), ("c", "戊己庚辛"), ("d", "壬癸子丑")]
        self.assertEqual(sorted(rows.sample_unique(pairs, None)), [("a", "甲乙丙丁"), ("c", "戊己庚辛"), ("d", "壬癸子丑")])
        self.assertEqual(rows.sample_unique(pairs, 2), rows.sample_unique(pairs, 2))
        self.assertEqual(len(rows.sample_unique(pairs, 2)), 2)

    def test_dedup_drops_exact_clause_matches(self):
        with tempfile.TemporaryDirectory() as d:
            r, a = os.path.join(d, "r.txt"), os.path.join(d, "a.txt")
            open(r, "w", encoding="utf-8").write("x|甲乙丙丁|ㄐㄧㄚˇ\ny|戊己庚辛|ㄨˋ\n")
            open(a, "w", encoding="utf-8").write("z|戊己庚辛|ㄨˋ\n")
            rows.dedup([r], [a], os.path.join(d, "out"))
            self.assertEqual(open(os.path.join(d, "out", "r.txt"), encoding="utf-8").read(), "x|甲乙丙丁|ㄐㄧㄚˇ\n")


if __name__ == "__main__":
    unittest.main()
