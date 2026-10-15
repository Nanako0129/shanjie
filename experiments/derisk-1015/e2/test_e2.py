"""E2 unit tests, numpy only, synthetic data (a 2-layer d=16 random model, a toy lexicon, 6-row toy sets).
Run from the repo root:  python3 -W ignore -m unittest discover -s experiments/derisk-1015/e2 -p 'test_e2.py' -v
The tools/readings.py parity test needs the real lexicon and the McBopomofo heterophony list (SHANJIE_HETERO); it is skipped without them.
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tinylm_r as tr  # noqa: E402
import label_readings as lr  # noqa: E402
import score_r  # noqa: E402
import anchor  # noqa: E402
import judge_e2  # noqa: E402
import split_dist  # noqa: E402
import tinylm  # noqa: E402
import score  # noqa: E402
import parity  # noqa: E402
import ime  # noqa: E402

CHARS = list("長好不一了嗎行你我他是的在再")
SYLS = ["ㄓㄤˇ", "ㄔㄤˊ", "ㄏㄠˇ", "ㄅㄨˋ", "ㄅㄨˊ", "ㄧ", "ㄧˊ", "ㄌㄜ˙", "ㄌㄧㄠˇ", "ㄇㄚ˙", "ㄒㄧㄥˊ", "ㄏㄤˊ", "ㄋㄧˇ", "ㄨㄛˇ", "ㄊㄚ",
        "ㄕˋ", "ㄉㄜ˙", "ㄗㄞˋ"]
LEXICON = """# toy
ㄓㄤˇ 長 -3.0
ㄔㄤˊ 長 -2.9
ㄏㄠˇ 好 -1.0
ㄅㄨˋ 不 -1.0
ㄅㄨˊ 不 -1.1
ㄧ 一 -1.0
ㄧˊ 一 -1.1
ㄌㄜ˙ 了 -1.0
ㄌㄧㄠˇ 了 -1.1
ㄇㄚ˙ 嗎 -1.0
ㄒㄧㄥˊ 行 -2.0
ㄏㄤˊ 行 -2.2
ㄋㄧˇ 你 -1.0
ㄨㄛˇ 我 -1.0
ㄊㄚ 他 -1.0
ㄕˋ 是 -1.0
ㄉㄜ˙ 的 -1.0
ㄗㄞˋ 在 -1.0
ㄗㄞˋ 再 -1.5
"""


def toy_lexicon(tmp):
    p = os.path.join(tmp, "lex.txt")
    open(p, "w", encoding="utf-8").write(LEXICON)
    return ime.Lexicon(p)


def toy_labeler(tmp):
    return lr.Labeler(toy_lexicon(tmp), {"長": "ㄓㄤˇ"}, {"了": ("ㄌㄜ˙",), "嗎": ("ㄇㄚ",)})


def make_models(tmp, zero_rd=False, n_layer=2, d=16, n_head=2):
    """(control npz without a reading table, new npz with one) holding the same character weights."""
    p = tr.random_params_r(CHARS, SYLS, n_layer, d, n_head, seed=3)
    if zero_rd:
        p["rd"][:] = 0
    plain = os.path.join(tmp, "plain.npz")
    tinylm.save_params(plain, {k: v for k, v in p.items() if k != "rd"}, CHARS, n_layer, n_head)
    new = os.path.join(tmp, "new.npz")
    tr.save_params_r(new, p, CHARS, SYLS, n_layer, n_head)
    return plain, new


class TestLabeler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = toy_labeler(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_rules_and_no_reading_cases(self):
        s = "我不長好行了嗎龘一"
        out = self.lab.label(s)
        self.assertEqual(len(out), len(s))                       # one entry per character: reading and character align
        got = dict(zip(s, out))
        self.assertEqual(got["不"], "ㄅㄨˋ")                      # rule 1 (citation tone)
        self.assertEqual(got["長"], "ㄓㄤˇ")                      # rule 3 (heterophony list)
        self.assertEqual(got["好"], "ㄏㄠˇ")                      # rule 4 (single reading)
        self.assertEqual(got["了"], "ㄌㄜ˙")                      # rule 0 (override)
        self.assertIsNone(got["行"])                             # rule 5: CHECK -> no reading
        self.assertIsNone(got["嗎"])                             # override not in the lexicon readings (GAP) -> no reading
        self.assertIsNone(got["龘"])                             # not in the lexicon -> no reading
        c = self.lab.stats
        self.assertEqual((c["r0"], c["r1"], c["r3"], c["check"], c["gap"], c["oov"]), (1, 2, 1, 1, 1, 1))

    def test_labels_are_in_the_reading_vocabulary(self):
        rv = lr.reading_vocab(self.lab.lex)
        arr = lr.label_runs(self.lab, rv, ["我不長好", "行了"])
        self.assertEqual(arr.dtype, np.uint16)
        self.assertEqual(len(arr), 2 + 4 + 2)                    # a BOS slot (0) per run, then one id per character
        self.assertEqual(arr[0], 0); self.assertEqual(arr[5], 0)
        self.assertEqual(arr[6], 0)                              # 行: CHECK -> NOREAD
        self.assertEqual(rv.syls[arr[1] - 1], "ㄨㄛˇ")
        self.assertEqual(lr.report(self.lab.stats, 8)["noread"], 1)


class TestReadingsParity(unittest.TestCase):
    """Where the labeler gives a reading it is the one tools/readings.py writes, with rule 2 disabled (an empty MoeDict)."""

    def test_same_readings_as_tools_readings(self):
        if not os.path.isfile(lr.readings.HETERO):
            self.skipTest("heterophony list not found (SHANJIE_HETERO)")
        rows = [l.split("|")[1] for l in open(os.path.join(tr.ROOT, "eval", "dev", "user-typing.txt"), encoding="utf-8")][:30]
        with tempfile.TemporaryDirectory() as tmp:
            empty = os.path.join(tmp, "moe.json"); open(empty, "w").write("[]")
            old, lr.readings.MOEDICT = lr.readings.MOEDICT, empty
            try:
                src, dst = os.path.join(tmp, "in.txt"), os.path.join(tmp, "out.txt")
                open(src, "w", encoding="utf-8").write("".join(f"|{s}\n" for s in rows))
                import contextlib
                with contextlib.redirect_stderr(io.StringIO()):
                    lr.readings.main(src, dst)
            finally:
                lr.readings.MOEDICT = old
            ref = {l.split("|")[1]: l.rstrip("\n").split("|")[2].split() for l in open(dst, encoding="utf-8")}
            lab, _ = lr.make_labeler()
            checked = nonnull = total = 0
            for s, syl in ref.items():
                out = lab.label(s)
                self.assertEqual(len(syl), len(s))
                for a, b in zip(out, syl):
                    total += 1
                    if a is not None:
                        nonnull += 1
                        self.assertEqual(a, b, s)
                checked += 1
            self.assertGreater(checked, 10)
            self.assertGreater(nonnull / total, 0.8)


class TestTrainingSamples(unittest.TestCase):
    def test_target_aligned(self):
        r = np.array([[0, 5, 6, 7]])
        self.assertEqual(tr.target_aligned(r).tolist(), [[5, 6, 7, 0]])

    def test_context_positions_are_no_reading_in_training(self):
        rd = np.array([[0, 5, 6, 7, 8], [0, 5, 6, 7, 8], [0, 5, 6, 7, 8]])
        out = tr.mask_context(rd, np.array([0, 1, 2]))
        self.assertEqual(out.tolist(), [[0, 5, 6, 7, 8], [0, 0, 6, 7, 8], [0, 0, 0, 7, 8]])
        self.assertEqual(rd[2].tolist(), [0, 5, 6, 7, 8])         # input untouched
        # a context longer than the sample: everything is NOREAD
        self.assertEqual(tr.mask_context(rd[:1], np.array([9])).tolist(), [[0] * 5])

    def test_split_draw(self):
        rng = np.random.default_rng(0)
        self.assertEqual(set(tr.sample_splits(rng, 50, [1, 0, 0]).tolist()), {0})
        s = tr.sample_splits(rng, 20000, [0.7, 0.0, 0.3])
        self.assertAlmostEqual(float((s == 2).mean()), 0.3, delta=0.02)
        self.assertNotIn(1, set(s.tolist()))

    def test_split_dist_from_score_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "a.jsonl")
            open(p, "w").write("\n".join(json.dumps({"key": k}) for k in (0, 0, 2, 1, 2, 0, 0, 0)) + "\n")
            probs, counts = split_dist.split_probs([p])
            self.assertEqual(counts, [5, 1, 2]); self.assertAlmostEqual(sum(probs), 1)


class TestScoringItems(unittest.TestCase):
    def setUp(self):
        self.voc = tinylm.Vocab(CHARS)
        self.rv = tr.RVocab(SYLS)

    def test_context_positions_are_no_reading_in_scoring(self):
        rd = self.rv.enc(["ㄋㄧˇ", "ㄏㄠˇ"])
        q, st, rt = tr.build_item(self.voc, "我他", "你好", rd)
        self.assertEqual(len(q), len(rt))                         # token-aligned
        self.assertEqual(q[0], tinylm.BOS)
        self.assertEqual(rt, [0, 0, 0] + rd)                      # BOS + the two context characters: NOREAD; candidate: its readings
        self.assertEqual(st, 3)
        _, _, rt0 = tr.build_item(self.voc, "", "你好", rd)
        self.assertEqual(rt0, [0] + rd)
        self.assertEqual(tr.build_item(self.voc, "我他", "你好", None)[2], [0] * 5)

    def test_row_readings_share_one_reading_and_never_mix(self):
        rd, why = tr.row_readings(self.rv, ["ㄋㄧˇ", "ㄏㄠˇ"], ["你好", "你是"])
        self.assertIsNone(why); self.assertEqual(rd[0], rd[1])
        rd, why = tr.row_readings(self.rv, ["ㄋㄧˇ", "ㄏㄠˇ"], ["你好", "你"])
        self.assertEqual((rd, why), ([None, None], "length differs"))
        rd, why = tr.row_readings(self.rv, ["ㄋㄧˇ", "XX"], ["你好"])
        self.assertEqual((rd, why), ([None], "unknown syllable"))

    def test_trimming_keeps_alignment(self):
        c = "你" * 62                                             # CTX - 1 - 62 = 1: only the last context character fits
        q, st, rt = tr.build_item(self.voc, "我他", c, [1] * 62)
        self.assertEqual(len(q), tinylm.CTX); self.assertEqual(len(q), len(rt))
        self.assertEqual(rt[:st], [0] * st)


class TestModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.plain, self.new = make_models(self.tmp.name)
        self.items = [("我他", "你好是", [3, 4, 5]), ("", "你好", [3, 4]), ("你", "他的在再", None), ("", "我", [2])]

    def tearDown(self):
        self.tmp.cleanup()

    def test_control_is_exactly_tinylm_ll_many(self):
        m = tinylm.NumpyLM(self.plain)
        want = tinylm.ll_many(m, [(k, c) for k, c, _ in self.items])
        self.assertTrue(np.array_equal(tr.ll_many_r(m, self.items), want))

    def test_reading_input_off_is_the_plain_model(self):
        # all-NOREAD input (row 0 of the table is zero): NumpyLMR gives exactly NumpyLM's numbers
        m, r = tinylm.NumpyLM(self.plain), tr.NumpyLMR(self.new)
        off = [(k, c, None) for k, c, _ in self.items]
        self.assertTrue(np.array_equal(tr.ll_many_r(r, off), tinylm.ll_many(m, [(k, c) for k, c, _ in self.items])))
        self.assertFalse(np.array_equal(tr.ll_many_r(r, self.items), tr.ll_many_r(r, off)))   # and readings are used when given

    def test_readings_are_causally_aligned(self):
        """The reading of character j changes the prediction of j (the position before it) and later ones, never earlier positions."""
        r = tr.NumpyLMR(self.new)
        q, _, rt = tr.build_item(r.vocab, "我", "你好是的", [3, 4, 5, 6])
        ids = np.array([q])
        base = r.logp10(ids, tr.target_aligned(np.array([rt])))
        j = 3                                                      # token index of 好 (the position before it, 2, predicts it)
        rt2 = list(rt); rt2[j] = 9
        alt = r.logp10(ids, tr.target_aligned(np.array([rt2])))
        self.assertTrue(np.array_equal(base[0, :j - 1], alt[0, :j - 1]))      # positions before the one predicting 是
        self.assertFalse(np.allclose(base[0, j - 1], alt[0, j - 1]))          # the position that predicts 是 sees its reading

    def test_scoring_passes_target_aligned_readings(self):
        seen = []

        class Spy(tr.NumpyLMR):
            def logp10(self, ids, rd=None):
                seen.append(np.array(rd))
                return super().logp10(ids, rd)

        r = Spy(self.new)
        tr.ll_many_r(r, [("我他", "你好", r.rvocab.enc(["ㄋㄧˇ", "ㄏㄠˇ"]))])
        rd = seen[0][0]
        self.assertEqual(rd.tolist()[:3], [0, 0, r.rvocab.ids["ㄋㄧˇ"]])      # BOS->我, 我->他: NOREAD; 他 predicts 你: your reading
        self.assertEqual(rd[3], r.rvocab.ids["ㄏㄠˇ"])


def write_set(tmp, name, rows):
    """rows: [(prefix, gold, [cands])] -> (rows file, dump file, top1). The reading is the toy reading of the gold's characters."""
    rf, df = os.path.join(tmp, f"{name}.txt"), os.path.join(tmp, f"{name}.dump")
    sy = dict(zip("你好是的我他在再", ["ㄋㄧˇ", "ㄏㄠˇ", "ㄕˋ", "ㄉㄜ˙", "ㄨㄛˇ", "ㄊㄚ", "ㄗㄞˋ", "ㄗㄞˋ"]))
    top1 = 0
    with open(rf, "w", encoding="utf-8") as f, open(df, "w", encoding="utf-8") as g:
        for i, (prefix, gold, cands) in enumerate(rows, 1):
            f.write(f"{prefix}|{gold}|{' '.join(sy[c] for c in gold)}\n")
            top1 += score.lenient(cands[0]) == score.lenient(gold)
            for rank, c in enumerate(cands, 1):
                g.write(f"{i}\t{rank}\t{c}\t{-1.0 - 6.0 * rank}\n")
    return rf, df, top1


ROWS = [("", "你好", ["你好", "你再", "他好"]), ("他在", "你是", ["他是", "你是", "你在"]), ("好", "我的", ["我的", "我在"]),
        ("，", "他在", ["他再", "他在", "我在"]), ("你好", "我是", ["我是", "我再", "你是"]), ("", "是的", ["在的", "是的", "再的"])]


class TestScorerAndAnchor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = self.tmp.name
        self.plain, self.new = make_models(self.t)
        self.rows_f, self.dump_f, self.top1 = write_set(self.t, "toy", ROWS)

    def tearDown(self):
        self.tmp.cleanup()

    def records(self, backend_cls, use_ext):
        rows = score.load_rows(rows=self.rows_f)
        cands = score.load_dump(open(self.dump_f, encoding="utf-8"), len(rows))
        m = backend_cls
        return score_r.build_records(m, rows, cands)[0] if use_ext else score.build_records(m, rows, cands)

    def test_control_records_equal_score_py(self):
        m = tinylm.NumpyLM(self.plain)
        self.assertEqual(json.dumps(self.records(m, True)), json.dumps(self.records(m, False)))

    def test_anchor_passes_and_catches_a_one_character_difference(self):
        m = tinylm.NumpyLM(self.plain)
        ref, ctl = self.records(m, False), self.records(m, True)
        ok, d = anchor.check(ref, ctl)
        self.assertTrue(ok); self.assertEqual(d["ll"][0], 0.0); self.assertEqual(d["ll"][1], sum(r["n"] for r in ref))
        # one candidate with one different character: the same n-gram scores, a different sentence -> LL differs by far more than 1e-4
        rows = score.load_rows(rows=self.rows_f)
        cands = score.load_dump(open(self.dump_f, encoding="utf-8"), len(rows))
        cands[1][0] = ("你是"[:1] + "在", cands[1][0][1])
        bad, _ = score_r.build_records(m, rows, cands)
        ok, d = anchor.check(ref, bad)
        self.assertFalse(ok); self.assertGreater(d["ll"][0], 1e-4)
        # and a perturbation of 2e-4 in one LL
        pert = json.loads(json.dumps(ctl)); pert[0]["ll"][0] += 2e-4
        self.assertFalse(anchor.check(ref, pert)[0])
        # nothing compared is not a pass
        self.assertFalse(anchor.check([{"i": 1, "n": 1, "ng": [0.0]}], [{"i": 1, "n": 1, "ng": [0.0]}])[0])

    def test_anchor_script_end_to_end(self):
        out = os.path.join(self.t, "anchor")
        buf = io.StringIO()
        with redirect_stdout(buf):
            anchor.main(["--npz", self.plain, "--dump", self.dump_f, "--rows", self.rows_f, "--name", "toy",
                         "--expect-top1", str(self.top1), "--out", out])
        self.assertIn("anchor: OK", buf.getvalue())
        with self.assertRaises(SystemExit):           # a wrong summary-line top1 stops, as score.py does
            with redirect_stdout(io.StringIO()):
                anchor.main(["--npz", self.plain, "--dump", self.dump_f, "--rows", self.rows_f, "--name", "toy",
                             "--expect-top1", str(self.top1 + 1), "--out", out])

    def test_new_arm_scores_use_the_row_reading_and_the_same_candidates(self):
        r = tr.NumpyLMR(self.new)
        new = self.records(r, True)
        ctl = self.records(tinylm.NumpyLM(self.plain), True)
        for a, b in zip(new, ctl):
            self.assertEqual((a["i"], a["n"], a["ng"], a["ok"], a["key"]), (b["i"], b["n"], b["ng"], b["ok"], b["key"]))
        self.assertNotEqual([a["ll"] for a in new if "ll" in a], [b["ll"] for b in ctl if "ll" in b])
        # the same model with every reading removed gives the control numbers
        rows = score.load_rows(rows=self.rows_f)
        cands = score.load_dump(open(self.dump_f, encoding="utf-8"), len(rows))
        for row in rows:                               # a row whose reading is unusable is scored without readings
            row[1][:] = ["XX"] * len(row[1])
        off, bad = score_r.build_records(r, rows, cands)
        self.assertEqual(bad, sum(len(c) >= 2 for c in cands))
        self.assertEqual([a.get("ll") for a in off], [b.get("ll") for b in ctl])

    def test_scorer_cli_writes_the_score_format(self):
        out = os.path.join(self.t, "E2N.chat.toy.jsonl")
        with redirect_stdout(io.StringIO()):
            score_r.main(["one", "--arm", "new", "--npz", self.new, "--dump", self.dump_f, "--rows", self.rows_f, "--name", "toy",
                          "--expect-top1", str(self.top1), "--out", out])
        recs = [json.loads(l) for l in open(out, encoding="utf-8")]
        self.assertEqual([r["i"] for r in recs], list(range(1, len(ROWS) + 1)))
        self.assertTrue(all(set(("ll", "ll0", "ll16")) <= set(r) for r in recs if r["n"] >= 2))


class TestJudgeGlue(unittest.TestCase):
    def test_exact_judge_invocations(self):
        self.assertEqual(judge_e2.judge_argv("chat", ["D"], "E2C", "O")[-8:],
                         ["--select", "STD", "--tuning", "chatclause", "--weight", "1", "--cap", "0.005"])
        self.assertEqual(judge_e2.judge_argv("formal", ["D"], "E2C", "O")[-7:],
                         ["--select", "STD", "--tuning", "cvtune", "wikitune", "--weight", "10"])
        self.assertEqual(judge_e2.judge_argv("chat", ["D"], "E2C", "O")[:15],
                         ["--scores", "D", "--sets", "chatclause", "cvtune", "wikitune", "dev302", "typing76", "user-reported", "lytune",
                          "wikitypo", "discordtune", "--setting", "chat", "--model"])

    def test_concat_renumbers_and_keeps_order(self):
        with tempfile.TemporaryDirectory() as t:
            a, b, c = (os.path.join(t, x) for x in "abc")
            open(a, "w").write("1\t1\t0\t3\n2\t0\t2\t3\n"); open(b, "w").write("1\t0\t1\t4\n")
            self.assertEqual(judge_e2.concat_rowstats([a, b], c), 3)
            self.assertEqual(open(c).read(), "1\t1\t0\t3\n2\t0\t2\t3\n3\t0\t1\t4\n")
            self.assertEqual(judge_e2.concat_rowstats([b, a], c), 3)
            self.assertEqual(open(c).read(), "1\t0\t1\t4\n2\t1\t0\t3\n3\t0\t2\t3\n")

    def test_select_and_compare_run_end_to_end_on_toy_scores(self):
        with tempfile.TemporaryDirectory() as t:
            plain, new = make_models(t)
            for arm, npz, cls, name in (("control", plain, tinylm.NumpyLM, "E2C"), ("new", new, tr.NumpyLMR, "E2N")):
                d = os.path.join(t, "scores_" + name); os.makedirs(d)
                m = cls(npz)
                for setting in ("chat", "formal"):
                    for k, s in enumerate(judge_e2.CHAT_SETS):
                        rf, df, top1 = write_set(t, f"{s}{k}", ROWS)
                        rows = score.load_rows(rows=rf)
                        recs = score_r.build_records(m, rows, score.load_dump(open(df, encoding="utf-8"), len(rows)))[0]
                        open(os.path.join(d, f"{name}.{setting}.{s}.jsonl"), "w").write("".join(json.dumps(r) + "\n" for r in recs))
                with redirect_stdout(io.StringIO()):
                    cfgs = judge_e2.select([d], name, os.path.join(t, "out_" + name))
                self.assertEqual(set(cfgs), {"chat", "formal"})
            buf = io.StringIO()
            with redirect_stdout(buf):
                judge_e2.compare(os.path.join(t, "out_E2C"), os.path.join(t, "out_E2N"), os.path.join(t, "work"))
            text = buf.getvalue()
            self.assertIn("MAIN chat discordtune+five", text)
            self.assertEqual(text.count("GUARD"), 3)
            main_row = [l for l in text.split("\n") if l.startswith("| MAIN")][0]
            self.assertEqual(main_row.split("|")[2].strip(), str(6 * 6))     # five sets + discordtune, 6 rows each, concatenated


if __name__ == "__main__":
    unittest.main()
