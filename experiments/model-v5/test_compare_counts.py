"""compare_counts.py 的單元測試。用法：python3 -m unittest experiments/model-v5/test_compare_counts.py"""
import os
import pickle
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare_counts as cc  # noqa: E402


def counts(**over):
    d = {"uni": {"a": 3.0, "b": 0.5}, "bi": {("<s>", "a"): 2.0}, "articles": 10, "runs": 4}
    d.update(over)
    return d


class Compare(unittest.TestCase):
    def test_same_and_float_noise(self):
        self.assertEqual(cc.compare(counts(), counts()), [])
        self.assertEqual(cc.compare(counts(), counts(uni={"b": 0.5 * (1 + 1e-12), "a": 3.0})), [])

    def test_differences(self):
        self.assertTrue(cc.compare(counts(), counts(uni={"a": 3.0, "b": 0.5001})))   # 相對誤差 2e-4
        self.assertTrue(cc.compare(counts(), counts(uni={"a": 3.0})))                # 鍵集合
        self.assertTrue(cc.compare(counts(), counts(runs=5)))
        self.assertTrue(cc.compare(counts(), counts(articles=11)))

    def test_cli_exit_code(self):
        with tempfile.TemporaryDirectory() as t:
            p = []
            for i, c in enumerate([counts(), counts(runs=5)]):
                p.append(os.path.join(t, f"{i}.pkl"))
                with open(p[-1], "wb") as f:
                    pickle.dump(c, f)
            self.assertEqual(cc.main([p[0], p[0]]), 0)
            self.assertEqual(cc.main([p[0], p[1]]), 1)


if __name__ == "__main__":
    unittest.main()
