"""Tests of the stop-condition helpers: build_model.py options, sample_labels.py, verdict.py (contract section 5)."""
import collections
import contextlib
import hashlib
import io
import os
import pickle
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import build_model
import decode_units
import rcommon
import share_rows
import sample_labels as S
import testkit
import units
import verdict
import build_counts as bc  # noqa: E402
import build_lm  # noqa: E402

RULES = """ㄉㄜ˙ 得 -2.0
ㄉㄟˇ 得 -3.0
ㄉㄧˋ 地 -2.0
ㄉㄜ˙ 地 -3.0
ㄒㄧㄥˊ 行 -2.0
ㄏㄤˊ 行 -2.5
ㄈㄥ 風 -2.0
"""
LAT = SimpleNamespace(by_word={w: (None, s) for w, s in {"不得": -2.5, "不": -1.0, "得": -2.0, "慢慢": -3.0, "慢": -2.0, "地": -2.5, "三": -1.5, "行": -2.0,
                                                           "他": -1.0, "跑": -1.0, "很": -1.0, "快": -1.0, "風": -1.0, "走": -1.0}.items()}, max_len=2)


def quiet():
    return contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO())


def run_main(fn, argv):
    """(exit code, stdout) of fn(argv); 0 when it returns."""
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            fn(argv)
        return 0, out.getvalue()
    except SystemExit as e:
        return (e.code if isinstance(e.code, int) else 1), out.getvalue()


class BuildModelOptions(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.counts = os.path.join(self.td.name, "counts")
        os.makedirs(self.counts)
        uni = {"今天": 9, "天氣": 9, "很": 9, "好": 9}
        bi = {("<s>", "今天"): 6, ("今天", "天氣"): 6, ("天氣", "很"): 6, ("很", "好"): 6, ("好", "</s>"): 6}
        for name, _ in build_lm.CORPORA:
            pickle.dump({"uni": uni, "bi": bi}, open(os.path.join(self.counts, name), "wb"))

    def test_expect_sha256_is_the_counting_anchor(self):
        out = os.path.join(self.td.name, "m")
        code, text = run_main(build_model.main, ["--counts", self.counts, "--out", out])
        self.assertEqual(code, 0)
        sha = hashlib.sha256(open(os.path.join(out, "bigram.sjlm"), "rb").read()).hexdigest()
        self.assertIn(sha, text)
        self.assertEqual(run_main(build_model.main, ["--counts", self.counts, "--out", out, "--expect-sha256", sha[:8]])[0], 0)
        flipped = ("0" if sha[0] != "0" else "1") + sha[1:8]
        self.assertEqual(run_main(build_model.main, ["--counts", self.counts, "--out", out, "--expect-sha256", flipped])[0], 2)

    def test_vocabulary_limit(self):
        out = os.path.join(self.td.name, "m")
        run_main(build_model.main, ["--counts", self.counts, "--out", out])
        same = os.path.join(out, "bigram.sjlm")
        self.assertEqual(run_main(build_model.main, ["--counts", self.counts, "--out", out, "--b0", same])[0], 0)
        small, _ = testkit.sjlm(self.td.name, {"今天": 9}, {("<s>", "今天"): 6, ("今天", "</s>"): 6}, "small.sjlm")   # 3 words against 6
        code, text = run_main(build_model.main, ["--counts", self.counts, "--out", out, "--b0", small])
        self.assertEqual(code, 4)
        self.assertIn("ratio 2.0000", text)


class SampleLabels(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.lab = units.Labeller(rcommon.char_readings(testkit.lexicon(td.name, RULES)))

    def test_candidates_are_short_list_characters_standing_alone(self):
        got = S.candidates("他跑得很快，慢慢地走三行。", self.lab, LAT)
        self.assertEqual(got, [(2, "得", "ㄉㄜ˙"), (8, "地", "ㄉㄜ˙"), (11, "行", "ㄏㄤˊ")])
        self.assertEqual(S.candidates("他不得很快", self.lab, LAT), [])      # 不得 is one word in the best cut: 得 is not alone
        self.assertEqual(S.candidates("風和風", self.lab, LAT), [])          # not a short-list character
        self.assertEqual(S.candidates("得", self.lab, LAT), [])               # a run of one character is not counted (the counting skips it too)

    def test_prefilter_skips_segmentation_without_changing_the_sample(self):
        corpus = ["風和風和風", "他跑得很快，慢慢地走三行。", "風風風風", "他不得很快", "走三行再跑得快"] * 20
        calls = []
        real = bc.segment
        with mock.patch.object(bc, "segment", lambda lex, raw: calls.append(raw) or real(lex, raw)):
            got = S.reservoir((s for s in corpus if S.candidates(s, self.lab, LAT)), 7, 20261017)
            self.assertNotIn("風和風和風", calls)
            self.assertNotIn("風風風風", calls)
        keep = {"他跑得很快，慢慢地走三行。"}      # the only sentence of the corpus with a candidate; the prefilter must not drop it
        self.assertEqual(got, S.reservoir((s for s in corpus if s in keep), 7, 20261017))

    def test_reservoir_is_fixed_by_the_seed(self):
        a, b = S.reservoir(iter(range(1000)), 10, 20261017), S.reservoir(iter(range(1000)), 10, 20261017)
        self.assertEqual(a, b)
        self.assertEqual(len(set(a)), 10)
        self.assertNotEqual(a, S.reservoir(iter(range(1000)), 10, 1))
        self.assertEqual(S.reservoir(iter(range(4)), 10, 5), [0, 1, 2, 3])

    def test_rate_counts_every_judged_position_and_stops_above_ten_percent(self):
        def judged(marks):
            return "".join(f"{i}\twiki\ts\t0\t{'得' if i % 2 else '行'}\tx\tc\t{m}\n" for i, m in enumerate(marks, 1))
        bad, total, per = S.rate(judged(["ok"] * 18 + ["x"] * 2).splitlines())
        self.assertEqual((bad, total, per), (2, 20, {"得": (1, 10), "行": (1, 10)}))
        f = os.path.join(tempfile.mkdtemp(), "j.tsv")
        for marks, code in ((["ok"] * 18 + ["x"] * 2, 0), (["ok"] * 17 + ["x"] * 3, 3)):      # 10% is not above 10%; 15% is
            open(f, "w", encoding="utf-8").write(judged(marks))
            self.assertEqual(run_main(S.main, ["rate", f])[0], code)
        open(f, "w", encoding="utf-8").write(judged(["ok", ""]))
        self.assertEqual(run_main(S.main, ["rate", f])[0], 1)
        with self.assertRaises(ValueError):
            S.rate(["1\twiki\ts\t0\t得\tx\tc\tmaybe\n"])

    def test_sample_end_to_end_on_a_text_file(self):
        if not os.path.isdir(os.path.join(bc.SRC, "opencc")):
            self.skipTest("needs the OpenCC tables in ~/.cache/shanjie/sources")
        td = tempfile.mkdtemp()
        src = testkit.write(td, "s.txt", "".join(f"他跑得很快{'。' if i % 2 else '，'}慢慢地走{i}行好。\n" for i in range(30)) + "沒有候選。\n")
        out = os.path.join(td, "o.tsv")
        code, text = run_main(S.main, ["sample", "--out", out, "--seed", "7", "--n", "5", "--text", src])
        self.assertEqual(code, 0, text)
        rows = [l.rstrip("\n").split("\t") for l in open(out, encoding="utf-8")]
        self.assertEqual(len({r[0] for r in rows}), 5)
        self.assertTrue(all(len(r) == 8 and r[7] == "" and r[4] in S.SHORT for r in rows))
        first = open(out, encoding="utf-8").read()
        run_main(S.main, ["sample", "--out", out, "--seed", "7", "--n", "5", "--text", src])
        self.assertEqual(open(out, encoding="utf-8").read(), first)       # the seed fixes the draw


class ShareRows(unittest.TestCase):
    def test_broken_rows_whose_change_is_at_a_share_character(self):
        with tempfile.TemporaryDirectory() as d:
            _, lm = testkit.sjlm(d, {"好": 9, "吧" + testkit.U + "ㄅㄚ": 9}, {("<s>", "好"): 5, ("好", "</s>"): 5})
            lex = testkit.lexicon(d)
            idx = decode_units.index_of(lm)
            self.assertEqual(share_rows.share_positions("吧", ["ㄅㄚ˙"], idx, lex), [0])       # unit 吧ㄅㄚ˙ is not in the model
            self.assertEqual(share_rows.share_positions("吧", ["ㄅㄚ"], idx, lex), [])         # unit 吧ㄅㄚ is
            self.assertEqual(share_rows.share_positions("好", ["ㄏㄠˇ"], idx, lex), [])        # 好 has no units
            rows = [("巴", ["ㄅㄚ˙"], ""), ("巴", ["ㄅㄚ˙"], ""), ("好", ["ㄏㄠˇ"], ""), ("巴", ["ㄅㄚ˙"], "")]
            base_rs = [(1, 0, 1), (1, 0, 1), (1, 0, 1), (0, 1, 1)]
            cand_rs = [(0, 1, 1), (1, 0, 1), (0, 1, 1), (1, 0, 1)]
            base_first = {1: "巴", 2: "巴", 3: "好", 4: "吧"}
            cand_first = {1: "吧", 2: "巴", 3: "你", 4: "巴"}
            self.assertEqual(share_rows.report(rows, base_rs, cand_rs, base_first, cand_first, idx, lex), (2, 1))   # rows 1 and 3 broke; only row 1 at a share character

    def test_share_character_on_the_base_side(self):
        with tempfile.TemporaryDirectory() as d:
            _, lm = testkit.sjlm(d, {"嘛" + testkit.U + "ㄇㄚ": 9, "麻": 9}, {("<s>", "麻"): 5, ("麻", "</s>"): 5})
            lex = testkit.lexicon(d, testkit.LEXICON + "ㄇㄚˊ 嘛 -2.0\nㄇㄚˊ 麻 -2.0\nㄇㄚ 嘛 -2.0\n")
            idx = decode_units.index_of(lm)
            self.assertEqual(share_rows.share_positions("嘛", ["ㄇㄚˊ"], idx, lex), [0])   # gold 嘛 read ㄇㄚˊ: unit 嘛ㄇㄚˊ is not in the model
            self.assertEqual(share_rows.share_positions("麻", ["ㄇㄚˊ"], idx, lex), [])
            rows = [("嘛", ["ㄇㄚˊ"], "")]
            self.assertEqual(share_rows.report(rows, [(1, 0, 1)], [(0, 1, 1)], {1: "嘛"}, {1: "麻"}, idx, lex), (1, 1))


class Verdict(unittest.TestCase):
    def write(self, rows, name):
        p = os.path.join(self.td.name, name)
        open(p, "w").write("".join(f"{i}\t{ok}\t{0 if ok else 1}\t5\n" for i, ok in enumerate(rows, 1)))
        return p

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)

    def test_words(self):
        base = self.write([0] * 12 + [1] * 12, "b")
        pos, neg, same = self.write([1] * 12 + [1] * 12, "p"), self.write([0] * 12 + [0] * 12, "n"), self.write([0] * 12 + [1] * 12, "s")
        self.assertEqual(verdict.verdict(base, pos)[:2] + verdict.verdict(base, pos)[3:], (12, 0, "positive"))
        self.assertEqual(verdict.verdict(base, neg)[:2] + verdict.verdict(base, neg)[3:], (0, 12, "negative"))
        self.assertEqual(verdict.verdict(base, same)[3], "neither")
        self.assertEqual(verdict.verdict(base, self.write([1] * 4 + [0] * 8 + [1] * 12, "w"))[3], "neither")      # 4 fixed, 0 broken: p = 0.125
        self.assertEqual(verdict.verdict(base, self.write([1] * 4 + [0] * 8 + [1] * 12, "w"), alpha=0.2)[3], "positive")

    def test_exit_code(self):
        base = self.write([1] * 24, "b")
        bad = self.write([0] * 24, "c")
        self.assertEqual(run_main(verdict.main, [base, bad])[0], 0)
        self.assertEqual(run_main(verdict.main, [base, bad, "--stop-on-negative"])[0], 5)
        self.assertEqual(run_main(verdict.main, [base, self.write([1] * 23, "short")])[0], 1)


if __name__ == "__main__":
    unittest.main()
