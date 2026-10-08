"""S2k 契約 §4.5 第 3 項（Python 這邊）：classes.sjc 的讀寫來回，和檔案不存在、魔數、模型雜湊、長度錯誤時載入失敗，--no-classes 的行為。
用法：python3 -B -m unittest tools.test_build_classes（從 repo 根目錄）。小模型用 test_build_lm 的小語料。"""
import hashlib
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_classes  # noqa: E402
import lm as L  # noqa: E402
import test_build_lm as T  # noqa: E402


class Classes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        T.BuildLm.setUpClass()
        cls.dir = os.path.dirname(T.BuildLm.path)
        cls.base = L.BigramLM(T.BuildLm.path, classes=False)
        cls.V = len(cls.base.vocab)
        cls.K = 2
        cls.cls = [cls.K + 1, cls.K + 2] + [i % 2 for i in range(cls.V - 2)]
        cls.emit = [1.0, 1.0] + [0.25 + 0.01 * i for i in range(cls.V - 2)]
        cls.Pc = [0.001 * (i + 1) for i in range((cls.K + 3) ** 2)]
        cls.sha = hashlib.sha256(open(T.BuildLm.path, "rb").read()).digest()
        cls.path = os.path.join(cls.dir, "classes.sjc")

    @classmethod
    def tearDownClass(cls):
        T.BuildLm.tearDownClass()

    def write(self, blob):
        with open(self.path, "wb") as f:
            f.write(blob)

    def good(self, **kw):
        a = dict(model_sha=self.sha, K=self.K, mu=0.8, cls=self.cls, emit=self.emit, Pc=self.Pc)
        a.update(kw)
        return build_classes.pack(**a)

    def test_round_trip(self):
        self.write(self.good())
        lm = L.BigramLM(T.BuildLm.path)
        self.assertEqual((lm.mu, list(lm.cls), list(lm.emit), list(lm.Pc)), (0.8, self.cls, self.emit, self.Pc))
        self.assertEqual(len(self.good()), 8 + 32 + 4 + 8 + 4 + 10 * self.V + 8 * (self.K + 3) ** 2)

    def test_prob_uses_the_class_term_only_off_the_kept_entries(self):
        self.write(self.good())
        lm, plain = L.BigramLM(T.BuildLm.path), self.base
        v, w = "他", "起床"   # 他 → 起床 is a kept bigram
        self.assertEqual(lm.prob(v, w, 0.1), plain.prob(v, w, 0.1))
        v, w = "起床", "他"   # 起床 has no entry for 他 (and cls differ by id parity)
        i, j = lm.ids[v], lm.ids[w]
        back = lm.ctx[i][1] if i in lm.ctx else 1.0
        want = back * ((1 - 0.8) * 0.1 + 0.8 * self.Pc[self.cls[i] * (self.K + 3) + self.cls[j]] * self.emit[j])
        self.assertEqual(lm.prob(v, w, 0.1), want)
        self.assertEqual(lm.prob(v, "不存在", 0.1), plain.prob(v, "不存在", 0.1))

    def test_no_classes_is_the_plain_model(self):
        self.write(self.good())
        off = L.BigramLM(T.BuildLm.path, classes=False)
        self.assertIsNone(off.cls)
        for v in ("他", "起床", "<s>"):
            for w in ("起床", "他", "</s>"):
                self.assertEqual(off.prob(v, w, 0.1), self.base.prob(v, w, 0.1))

    def test_bad_files_fail(self):
        if os.path.exists(self.path):
            os.remove(self.path)
        with self.assertRaises(FileNotFoundError):
            L.BigramLM(T.BuildLm.path)
        other = hashlib.sha256(b"another model").digest()
        for name, blob in (("magic", b"SJCL0002" + self.good()[8:]), ("model hash", self.good(model_sha=other)),
                           ("short", self.good()[:-1]), ("trailing", self.good() + b"\0"),
                           ("vocabulary size", self.good(cls=self.cls[:-1], emit=self.emit[:-1])),
                           ("mu 1.5", self.good(mu=1.5)), ("negative emit", self.good(emit=[-0.1] + self.emit[1:])),
                           ("NaN in Pc", self.good(Pc=self.Pc[:-1] + [float("nan")])),
                           ("class out of range", self.good(cls=[self.K + 3] + self.cls[1:]))):
            self.write(blob)
            with self.assertRaises(ValueError, msg=name):
                L.BigramLM(T.BuildLm.path)


if __name__ == "__main__":
    unittest.main()
