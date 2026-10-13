"""X0c acceptance 5: score_0c.py and versus.py with a fake model (no onnxruntime, no weights). Run: python3 -m unittest experiments/x0c/test_x0c.py"""
import math
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "experiments", "s7a-tinylm"))
import numpy as np  # noqa: E402
import score_0c as S  # noqa: E402
import versus as V  # noqa: E402
import fusegrid as fg  # noqa: E402

assert "onnxruntime" not in sys.modules

CHARS = list("今天氣很好金器我們去公元園下週就是期其中考" + "甲乙丙丁戊己庚辛壬癸")
BASES = ["ㄐㄧㄣ", "ㄊㄧㄢ", "ㄑㄧ", "ㄏㄣ", "ㄏㄠ", "ㄍㄨㄥ", "ㄩㄢ", "ㄇㄚ", "ㄅㄚ"]
ID = {c: i + 8 for i, c in enumerate(CHARS)}


class FakeSession:
    """Records every feed; the score of a candidate is a fixed function of its target ids, the context it saw, the reading and the tones."""
    def __init__(self):
        self.feeds = []

    def run(self, names, feeds):
        self.feeds.append(feeds)
        ids, bases, tones = feeds["ids"][0], feeds["bases"][0], feeds["tones"][0]
        seen = float(ids[ids > 7].sum() % 7) * 0.01 + float(bases.sum() % 5) * 0.1 + float(tones.sum() % 3) * 0.3
        assert names == ["scores"] and feeds["mask"].dtype == bool
        return [np.array([-0.001 * float(r.sum()) - seen for r in feeds["target_ids"]], dtype=np.float32)]


def model(session=None):
    return S.Zuin(session or FakeSession(), {"characters": CHARS, "bases": BASES}, {"neutral_tolerant_last": ["ㄇㄚ"]})


def trow(i=1, n=2, ok=(1, 0)):
    return {"i": i, "n": n, "ng": [-1.0 - k for k in range(n)], "ok": list(ok), "ed": [0, 1][:n], "glen": 5}


class ScoreTests(unittest.TestCase):
    reading = ["ㄐㄧㄣˇ", "ㄊㄧㄢ", "ㄑㄧˋ"]
    cands = ["今天氣", "金天器"]

    def test_context_is_the_last_64_characters_of_the_prefix(self):
        m = model()
        prefix = "甲" * 70 + "乙丙"
        S.log_prob(m, prefix, self.reading, ["今天氣"])
        ids = list(m.session.feeds[-1]["ids"][0])
        self.assertEqual(ids[:66], [1] + [ID["甲"]] * 62 + [ID["乙"], ID["丙"]] + [2])
        S.log_prob(m, "", self.reading, ["今天氣"])          # an empty prefix gives a different input: the context really is fed
        self.assertNotEqual(list(m.session.feeds[-1]["ids"][0]), ids)
        self.assertEqual(list(m.session.feeds[-1]["ids"][0])[:2], [1, 2])

    def test_reading_is_the_rows_reading(self):
        m = model()
        S.log_prob(m, "甲", self.reading, ["今天氣"])
        f = m.session.feeds[-1]
        h = 3   # [BOS] 甲 [SEP]
        self.assertEqual(list(f["bases"][0][h:h + 3]), [m.bases[b] for b in ("ㄐㄧㄣ", "ㄊㄧㄢ", "ㄑㄧ")])
        self.assertEqual(list(f["tones"][0][h:h + 3]), [3, 1, 4])
        S.log_prob(m, "甲", ["ㄏㄣˇ", "ㄏㄠˇ", "ㄏㄠˇ"], ["今天氣"])
        self.assertNotEqual(list(m.session.feeds[-1]["bases"][0]), list(f["bases"][0]))

    def test_ll_is_the_natural_log_over_ln10(self):
        m = model()
        r, kind = S.record(m, trow(), "甲乙", self.reading, self.cands)
        self.assertIsNone(kind)
        want = S.score(m, "甲乙", self.reading, self.cands)
        for x, w in zip(r["ll"], want):
            self.assertLessEqual(abs(x - w / math.log(10)), 1e-9)
        self.assertEqual({k: r[k] for k in ("i", "n", "ng", "ok", "ed", "glen")}, {k: trow()[k] for k in ("i", "n", "ng", "ok", "ed", "glen")})
        self.assertEqual(r["skip"], 0)
        self.assertNotEqual(r["ll"], r["ll0"])   # the context reaches the model
        self.assertNotEqual(r["ll"], r["llr"])   # so do the tones

    def test_packed_equals_one_by_one_structure(self):
        m = model()
        S.log_prob(m, "甲", self.reading, self.cands, packed=True)
        f = m.session.feeds[-1]
        self.assertEqual(f["target_node"].shape, (2, 3))
        self.assertEqual(list(f["target_node"][0]), [0, 1, 2])
        S.log_prob(m, "甲", self.reading, ["今天氣"], packed=False)
        g = m.session.feeds[-1]
        self.assertEqual(list(g["ids"][0]), list(f["ids"][0][:len(g["ids"][0])]))   # the first candidate's path is the card's own input

    def test_neutral_retry_only_for_listed_last_syllable(self):
        s = FakeSession()
        m = model(s)
        S.score(m, "", ["ㄅㄚ", "ㄇㄚˊ"], ["今天"])
        self.assertEqual(len(s.feeds), 2)           # ㄇㄚ is listed, not neutral yet: retried as ㄇㄚ˙
        self.assertEqual(s.feeds[1]["tones"][0][2:4].tolist(), [1, 5])
        s.feeds.clear()
        S.score(m, "", ["ㄅㄚ", "ㄅㄚˊ"], ["今天"])
        self.assertEqual(len(s.feeds), 1)

    def test_unscorable_rows_are_skipped(self):
        m = model()
        for cands, reading, kind in ((["今天氣", "今天猫"], self.reading, "oov"), (["今天氣", "今天"], self.reading, "length"),
                                     (["今"], ["ㄐㄧㄣ"], "syllables"), (["今天氣", "金天器"], ["ㄐㄧㄣ", "ㄊㄧㄢ", "ㄗㄨ"], "base")):
            r, k = S.record(m, trow(), "甲", reading, cands)
            self.assertEqual((k, r["skip"], r["n"]), (kind, 1, 2))
            self.assertEqual(r["ll"] + r["ll0"] + r["llr"], [0.0] * 6)
            self.assertEqual(fg.pick(r, 2, math.inf, "ll", 0), 0)
        self.assertEqual(len(m.session.feeds), 0)   # nothing was sent to the model

    def test_zero_ll_never_changes_a_row(self):
        rows = [dict(trow(i, ok=(i % 2, 1 - i % 2)), ll=[0.0, 0.0]) for i in range(1, 9)]
        for a in fg.ALPHAS:
            for t in fg.TAUS:
                for d in fg.DELTAS:
                    self.assertEqual(fg.fixed_broken(rows, a, t, d), (0, 0))

    def test_versus_covered_only_keeps_ngram_on_skipped_rows(self):
        # row 1 scored (0c prefers candidate 1 and is right), row 2 skipped; v3 (b) prefers candidate 1 on both
        a = [dict(trow(1, ok=(0, 1)), ll=[-3.0, 0.0], skip=0), dict(trow(2, ok=(0, 1)), ll=[0.0, 0.0], skip=1)]
        b = [dict(trow(1, ok=(0, 1)), ll=[-3.0, 0.0]), dict(trow(2, ok=(0, 1)), ll=[-3.0, 0.0])]
        p = (1.0, math.inf, 0.0)
        self.assertEqual(V.picks(a, p), [1, 0])
        skip = [r["skip"] for r in a]
        self.assertEqual(V.picks(b, p, skip=skip), [1, 0])
        self.assertEqual(V.picks(b, p), [1, 1])
        self.assertEqual(V.picks(a, p, skip=skip), [1, 0])

    def test_counts(self):
        r1 = dict(trow(1, ok=(0, 1)), ll=[-3.0, 0.0], ll0=[0.0, -3.0], llr=[-3.0, 0.0], skip=0)   # context decides it
        r2 = dict(trow(2, ok=(0, 1)), ll=[-3.0, 0.0], ll0=[-3.0, 0.0], llr=[0.0, -3.0], skip=0)   # tones decide it
        b = [dict(trow(i, ok=(0, 1)), ll=[0.0, 0.0]) for i in (1, 2)]
        c = V.counts([r1, r2], b, ["甲乙丙", "甲"], (1.0, math.inf, 0.0), (1.0, math.inf, 0.0))
        self.assertEqual((c["ll_ne_ll0"], c["ctx_pick"], c["tone_pick"], c["a_fix_b_not"], c["fix_ctx"], c["fix_tone"]), (1, 1, 1, 2, 1, 1))

    def test_end_to_end_files(self):
        with tempfile.TemporaryDirectory() as d:
            import json
            for name, ll in (("x0c", [-3.0, 0.0]), ("v3", [0.0, 0.0])):
                with open(os.path.join(d, f"{name}.chat.s.jsonl"), "w") as f:
                    for i in (1, 2):
                        r = dict(trow(i, ok=(0, 1)), ll=ll, ll0=ll, llr=ll)
                        if name == "x0c":
                            r["skip"] = i - 1
                        f.write(json.dumps(r) + "\n")
            rows = os.path.join(d, "s.txt")
            open(rows, "w").write("|甲乙|ㄐㄧㄣ ㄊㄧㄢ\n|丙|ㄐㄧㄣ\n")
            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                V.main(["--scores", d, "--setting", "chat", "--sets", "s", "--out", d, "--a", "1", "inf", "0", "--b", "1", "inf", "0",
                        "--rows", f"s={rows}"])
            text = buf.getvalue()
            self.assertIn("all rows", text)
            self.assertIn("covered only", text)
            self.assertIn("counts chat s:", text)
            for p in os.listdir(os.path.join(d, "rowstats")):
                self.assertRegex(open(os.path.join(d, "rowstats", p)).read(), r"\A[0-9\t\n]*\Z")


if __name__ == "__main__":
    unittest.main()
