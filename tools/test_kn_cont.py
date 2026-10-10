"""kn-smoothing 契約 §2.1／§2.2 的單元測試，加上 build_lm 重構後輸出位元組不變的金標準。用法：python3 -B -m unittest tools/test_kn_cont.py
玩具語料沿用 test_build_lm（手算見各測試）。異體類的資訊不在側檔裡：lm.py 由呼叫端傳 kn_classes（和 lm_eval 一樣用 build_lm.variant_classes 算）。"""
import hashlib
import math
import os
import pickle
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for sub in ("tools", "reference/proto", "experiments/s2"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import build_lm  # noqa: E402
import kn_cont  # noqa: E402
import lm as L  # noqa: E402
from test_build_lm import BY_READING, COLL, WIKI  # noqa: E402

# 重構 build_lm.build 之前，用舊程式碼對同一組玩具計數建出的模型的 SHA-256；之後必須相同。
GOLDEN_TINY_MODEL_SHA = "47ff6b1a9485a77cd3c3f07d7e42db0cb1dd4c5d49ea351a952df2546c53bec3"


class KnCont(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        for name, c in (("counts-200000.pkl", WIKI), ("counts-colloquial3.pkl", COLL)):
            pickle.dump(c, open(os.path.join(cls.tmp.name, name), "wb"))
        cls.old_work, build_lm.WORK = build_lm.WORK, cls.tmp.name
        cls.cls = build_lm.variant_classes(BY_READING)
        data = build_lm.build(cls.cls)[0]
        cls.data = data
        cls.path = os.path.join(cls.tmp.name, "tiny.sjlm")
        open(cls.path, "wb").write(data)
        cls.bi = build_lm.merged_counts(cls.cls)[1]

    @classmethod
    def tearDownClass(cls):
        build_lm.WORK = cls.old_work
        cls.tmp.cleanup()

    def side(self, theta=1, bi=None, name="side.sjkn"):
        vocab = L.BigramLM(self.path, classes=False).vocab
        n = kn_cont.continuation(self.bi if bi is None else bi, self.cls, theta, vocab)
        p = os.path.join(self.tmp.name, name)
        open(p, "wb").write(kn_cont.side_bytes(self.path, n, self.cls, theta))
        return p

    def load(self, beta, theta=1):
        return L.BigramLM(self.path, classes=False, kn=self.side(theta), kn_beta=beta, kn_classes=self.cls)

    def test_build_bytes_unchanged(self):
        self.assertEqual(hashlib.sha256(self.data).hexdigest(), GOLDEN_TINY_MODEL_SHA)

    def test_side_file_layout(self):
        b = open(self.side(2), "rb").read()
        V = len(L.BigramLM(self.path, classes=False).vocab)
        self.assertEqual(b[:8], b"SJKN0001")
        self.assertEqual(struct.unpack_from("<I", b, 8)[0], V)
        self.assertEqual(b[12:44], hashlib.sha256(self.data).digest())
        self.assertEqual(struct.unpack_from("<I", b, 44)[0], 2)
        self.assertEqual(len(b), 48 + 4 * V)
        self.assertEqual(struct.unpack_from("<II", b, 48), (0, 0))

    def test_counts_by_hand(self):
        """合併後二元組：(他,起床)4 (他,佔)2 (他,占)5 (<s>,起床)8 (<s>,他)15 (起床,</s>)14 (起牀,</s>)14。前文 <s> 不算、</s> 不進側檔，
        所以 N(起床)=1（前文只有 他）、起牀 取代表的 N、N(他)=0、N(佔)=N(占)=1；ΣN′ 每類只算代表：1+2+2+2 = 7。
        once(v) 這裡用不到（起床類只出現在 </s> 前面），由 test_predecessor_filters 測。"""
        lm = self.load(1.0)
        _, np1, total = lm.kn
        g = lambda w: np1[lm.ids[w]]
        self.assertEqual((g("他"), g("佔"), g("占"), g("起床"), g("起牀")), (1, 2, 2, 2, 2))
        self.assertEqual(total, 7)
        self.assertEqual((np1[0], np1[1]), (1, 1))   # id 0、1 的 N 是 0，但不進 ΣN′

    def test_beta_zero_bit_identical(self):
        plain, kn = L.BigramLM(self.path, classes=False), self.load(0.0)
        for v in ("<s>", "他", "占", "起床", "起牀"):
            for w in ("他", "佔", "占", "起床", "起牀", "沒見過"):
                for lp in (-1.5, -4.9, -7.25):
                    for lam in (0.5, 0.7):
                        self.assertEqual(plain.word(lam, v, w, lp), kn.word(lam, v, w, lp))

    def test_beta_one_three_branches(self):
        lm = self.load(1.0)
        D, lp = lm.D, -3.0       # lam = 1：score = log10 prob
        w = lambda v, x: lm.word(1.0, v, x, lp)
        back_ta = 1 - ((4 - D) + (2 - D) + (5 - D)) / 11     # 他 的 back
        # 保留條目 (他, 占)：(5−D)/11 + back·pb，pb(占) = 2/7
        self.assertAlmostEqual(w("他", "占"), math.log10((5 - D) / 11 + back_ta * 2 / 7), places=12)
        # 一般回退 (他, 起牀)：他 有前文但沒有 起牀 條目（只放在代表成員上）→ back·pb，pb(起牀) = 2/7
        self.assertAlmostEqual(w("他", "起牀"), math.log10(back_ta * 2 / 7), places=12)
        # 詞類項 (佔, 起床)：佔 沒有前文 → back = 1；(1−μ)·pb + μ·Pc·emit
        lm.cls, lm.mu, lm.K3 = [0xFFFF] * len(lm.vocab), 0.5, 4
        lm.emit, lm.Pc = [0.0] * len(lm.vocab), [0.0] * 16
        lm.cls[lm.ids["佔"]], lm.cls[lm.ids["起床"]] = 1, 2
        lm.emit[lm.ids["起床"]], lm.Pc[1 * 4 + 2] = 0.5, 0.2
        self.assertAlmostEqual(w("佔", "起床"), math.log10(0.5 * (2 / 7) + 0.5 * 0.2 * 0.5), places=12)
        # 沒有類（回退到 back·pb，back = 1）：占 沒有前文
        lm.cls = None
        self.assertAlmostEqual(w("占", "他"), math.log10(1 / 7), places=12)

    def test_unknown_and_eos_unchanged(self):
        plain, kn = L.BigramLM(self.path, classes=False), self.load(1.0)
        self.assertEqual(plain.word(0.5, "他", "沒見過", -4.0), kn.word(0.5, "他", "沒見過", -4.0))
        self.assertEqual(plain.eos(0.5, "他"), kn.eos(0.5, "他"))

    def test_monotone_in_N(self):
        for beta in (0.5, 1.0):
            lm = self.load(beta)
            # 他 N=0（N′=1）、佔 N=1（N′=2），同一個 lp、同一個前文（占 無前文無類）
            self.assertLessEqual(lm.word(1.0, "占", "他", -3.0), lm.word(1.0, "占", "佔", -3.0))

    def test_mismatch_raises(self):
        side = open(self.side(), "rb").read()
        bad = {"hash": side[:12] + bytes([side[12] ^ 1]) + side[13:],
               "V": side[:8] + struct.pack("<I", 99) + side[12:],
               "len": side[:-4]}
        for k, b in bad.items():
            p = os.path.join(self.tmp.name, f"bad-{k}.sjkn")
            open(p, "wb").write(b)
            with self.assertRaises(ValueError, msg=k):
                L.BigramLM(self.path, classes=False, kn=p, kn_beta=1.0, kn_classes=self.cls)
        good = self.side()
        for kw in (dict(kn_beta=1.0), dict(kn_classes=self.cls), dict(kn_beta=1.5, kn_classes=self.cls)):
            with self.assertRaises(ValueError, msg=str(kw)):
                L.BigramLM(self.path, classes=False, kn=good, **kw)
        with self.assertRaises(ValueError, msg="beta without kn"):
            L.BigramLM(self.path, classes=False, kn_beta=1.0, kn_classes=self.cls)

    def test_predecessor_filters(self):
        """起床、起牀 同一類，都接 他：N(他) 只算一次（once）；詞彙外的前文不算（build 也丟掉那些二元組）。"""
        vocab = L.BigramLM(self.path, classes=False).vocab
        bi = {("起床", "他"): 1, ("起牀", "他"): 1, ("沒見過", "佔"): 3}
        self.assertEqual(kn_cont.continuation(bi, self.cls, 1, vocab), {"他": 1})

    def test_thetas_differ(self):
        bi = {("他", "起床"): 1, ("佔", "起床"): 2, ("占", "起床"): 3}   # N(起床) = 3、2、1
        sides = [open(self.side(t, bi, f"t{t}.sjkn"), "rb").read() for t in (1, 2, 3)]
        self.assertEqual(len(set(sides)), 3)
        vi = L.BigramLM(self.path, classes=False).ids["起床"]
        self.assertEqual([struct.unpack_from("<I", s, 48 + 4 * vi)[0] for s in sides], [3, 2, 1])


if __name__ == "__main__":
    unittest.main()
