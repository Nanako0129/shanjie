"""P2B unit tests (synthetic data only). Vocabulary / alignment / filtering run without torch; the torch half is skipped without it
(main runs it on Colab before training):  python3 -m unittest discover -s experiments/p2b-reading -p 'test_*.py' -v"""
import json
import math
import os
import random
import tempfile
import unittest

import numpy as np

import train_b as tb               # first: puts p2-distill and s7a-tinylm on sys.path
import test_p2 as t2               # P2's helpers: CHARS, tiny_npz, write_files (imported as a module, so its tests are not collected here)
import tinylm
from tinylm import NumpyLM, ll_many

tp, CHARS, LN10 = tb.tp, t2.CHARS, math.log(10.0)
SYLS = ["ㄋㄧˇ", "ㄏㄠˇ", "ㄊㄧㄢ", "ㄑㄧˋ", "ㄕㄨㄛ", "ㄨㄛˇ", "ㄕˋ", "ㄅㄨˋ"]


def make_data_b(n_rows=40, seed=1):
    """-> rows [(gold, syl, prefix)], dump cands, teacher records. Every candidate of a row has the row's length, except: row 3 (n = 1),
    row 5 (one candidate longer: len_mismatch), row 7 (skip), row 9 (reading one syllable too long: len_mismatch), row 11 (a 64-character
    candidate, with 64 syllables: too_long)."""
    rnd = random.Random(seed)
    rows, cands, teacher = [], [], []
    prefixes = ["", "你好", "他說。我", "今天天氣", "是"]
    for i in range(1, n_rows + 1):
        n, L = (1 if i == 3 else rnd.randint(2, 8)), rnd.randint(2, 4)
        cs = [("".join(rnd.choice(CHARS) for _ in range(L)), -float(j)) for j in range(n)]
        if i == 5:
            cs[1] = (cs[1][0] + "好", -1.0)
        if i == 11:
            cs[1] = ("好" * 64, -1.0)
        syl = [rnd.choice(SYLS) for _ in range(64 if i == 11 else L + (i == 9))]
        rows.append((cs[0][0], syl, rnd.choice(prefixes)))
        cands.append(cs)
        t = {"i": i, "n": n, "skip": int(i == 7)}
        if n >= 2:
            t["ll"] = [0.0] * n if i == 7 else [-rnd.uniform(0.5, 6.0) for _ in range(n)]
        teacher.append(t)
    return rows, cands, teacher


class TestVocabAndJoin(unittest.TestCase):
    def test_table(self):
        table = tb.build_syl_table([["ㄕˋ", "ㄅㄨˋ"], ["ㄕˋ", "ㄋㄧˇ"]])
        self.assertEqual(table[:2], ["<none>", "<unk>"])
        self.assertEqual(table[2:], sorted({"ㄕˋ", "ㄅㄨˋ", "ㄋㄧˇ"}))
        sid = tb.syl_ids(table)
        self.assertEqual(sorted(sid.values()), [2, 3, 4])
        self.assertEqual(sid.get("ㄑㄧˋ", tb.UNK_SYL), 1)             # outside the table -> unknown
        self.assertNotIn("<none>", sid)

    def test_len_mismatch_filter_and_counts(self):
        rows, cands, teacher = make_data_b()
        items, c = tb.join_rows_b(rows, cands, teacher)
        self.assertEqual(c["unusable"], {"n<2": 1, "skip": 1, "too_long": 1, "len_mismatch": 2})   # P2's reasons first, then ours
        self.assertEqual(c["usable"], len(items))
        self.assertEqual(len(items), len(rows) - 5)
        self.assertEqual(c["rows_read"], len(rows))
        for prefix, surfs, ll, syls in items:
            self.assertTrue(all(len(s) == len(syls) for s in surfs))
        prefix, surfs, ll, syls = items[0]                           # row 1: its own prefix, candidates, teacher and reading
        self.assertEqual((prefix, syls), (rows[0][2], rows[0][1]))
        self.assertEqual((surfs, ll), ([s for s, _ in cands[0]], teacher[0]["ll"]))
        # the row number carried through tp.join_rows is not mistaken for another row: item k is the k-th kept row
        kept = [i for i in range(len(rows)) if i + 1 not in (3, 5, 7, 9, 11)]
        self.assertEqual([it[3] for it in items], [rows[i][1] for i in kept])

    def test_mismatch_still_stops(self):
        rows, cands, teacher = make_data_b()
        with self.assertRaises(tb.Mismatch):
            tb.join_rows_b(rows, cands, teacher[:-1])

    def test_rd_placement(self):
        """rd is non-zero exactly at the positions that predict a candidate character, and holds that character's syllable."""
        table = ["<none>", "<unk>", "a", "b", "c", "d"]
        sid = tb.syl_ids(table)
        with tempfile.TemporaryDirectory() as td:
            vocab = NumpyLM(t2.tiny_npz(td)).vocab
        items = [("你好", ["今天氣"], [0.0], ["a", "b", "c"]),                  # [BOS 你 好 今 天 氣]: scored columns 2, 3, 4
                 ("", ["天氣", "說說"], [0.0, 0.0], ["c", "zzz"]),               # empty k: c_1's syllable sits on BOS (column 0); zzz -> unknown
                 ("你好", ["好" * 62], [0.0], ["d"] * 62)]                      # k cut to 1 character to fit the context
        P = tb.PackedB(items, vocab, sid)
        b = tb.collate_b(P, np.array([0, 1, 2]))
        rd = b["rd"]                                                           # sequences: row 0, row 1 (two candidates), row 2
        self.assertEqual(rd.shape, b["ids"].shape)
        self.assertEqual(rd[0][:6].tolist(), [0, 0, 2, 3, 4, 0])
        for s in (1, 2):
            self.assertEqual(rd[s][:3].tolist(), [4, tb.UNK_SYL, 0])
        self.assertEqual(rd[3].tolist(), [0] + [5] * 62 + [0])                  # [BOS 好 + 62 characters]: columns 1..62
        self.assertEqual(int((rd != 0).sum()), 3 + 2 + 2 + 62)
        mask = np.zeros_like(rd, dtype=bool)                                    # every non-zero position is a scored one, and only those
        mask[b["pos_seq"], b["pos_col"]] = True
        np.testing.assert_array_equal(rd != 0, mask)

    def test_empty_packed(self):
        with tempfile.TemporaryDirectory() as td:
            vocab = NumpyLM(t2.tiny_npz(td)).vocab
        self.assertEqual(len(tb.PackedB([], vocab, {})), 0)


try:
    import torch
except ImportError:
    torch = None


def b_model(td, seed=0, table=None, rd_scale=0.0):
    """Tiny T model (2 layers, d 32) wrapped as ReadingGPT; R zeros, or random N(0, rd_scale) outside row 0."""
    gpt, vocab = tb.torch_model.from_npz(t2.tiny_npz(td, seed=seed))
    m = tb.ReadingGPT(gpt, table or tb.build_syl_table([SYLS])).eval()
    if rd_scale:
        g = torch.Generator().manual_seed(5)
        with torch.no_grad():
            m.rd.weight[2:] = torch.randn(m.rd.weight[2:].shape, generator=g) * rd_scale
    return m, vocab


@unittest.skipUnless(torch, "torch is not installed")
class TestTorch(unittest.TestCase):
    def packed(self, vocab, n_rows=40):
        items, _ = tb.join_rows_b(*make_data_b(n_rows))
        items.append(("你好", ["好" * 62, "你我" * 31], [-1.0, -2.0], ["ㄋㄧˇ"] * 62))   # the k-truncation case
        return items, tb.PackedB(items, vocab, tb.syl_ids(tb.build_syl_table([SYLS])))

    def test_zero_R_equals_P2_score(self):
        with tempfile.TemporaryDirectory() as td:
            m, vocab = b_model(td)
            items, P = self.packed(vocab)
            t = tp.to_tensors(tb.collate_b(P, np.arange(len(items))), "cpu")
            self.assertGreater(int((t["rd"] != 0).sum()), 0)                     # the reading is really there, R just adds 0
            with torch.no_grad():
                sb = tb.cand_scores_b(m, t).numpy()
                s2 = tp.cand_scores(m.gpt, t).numpy()                            # P2's score of the very same weights
            np.testing.assert_allclose(sb, s2, atol=1e-6, rtol=0)
            lm = NumpyLM(os.path.join(td, "m.npz"))                              # and P2's own check: ln10 * ll_many
            for r, (prefix, surfs, _ll, _s) in enumerate(items):
                want = ll_many(lm, [(tinylm.row_key(prefix, 2), c) for c in surfs]) * LN10
                np.testing.assert_allclose(sb[r, :len(surfs)], want, atol=1e-4)

    def test_syllable_placement_in_the_network(self):
        """Only the R row at position t non-zero: the logits before t are untouched, and a non-zero row at the LAST scored position
        changes only the last character's term of the score (earlier terms cannot see it: causal)."""
        table = ["<none>", "<unk>", "a", "b", "c"]
        with tempfile.TemporaryDirectory() as td:
            m, vocab = b_model(td, table=table)
            ids = torch.tensor([[tinylm.BOS] + vocab.enc("你好今天氣")])        # scored columns 2, 3, 4
            zero = torch.zeros_like(ids)
            def terms(model, rd):
                with torch.no_grad():
                    lp = torch.log_softmax(model(ids, rd), -1)
                return lp[0, [2, 3, 4], ids[0, [3, 4, 5]]].numpy(), lp
            base_terms, base_lp = terms(m, zero)
            with torch.no_grad():
                m.rd.weight[4] = torch.randn(32) * 0.5                          # syllable "c" only
            rd = zero.clone(); rd[0, 4] = 4                                      # c_3's syllable on c_2's position (column 4)
            new_terms, new_lp = terms(m, rd)
            np.testing.assert_allclose(new_lp[0, :4].numpy(), base_lp[0, :4].numpy(), atol=1e-6)     # columns 0..3 untouched
            np.testing.assert_allclose(new_terms[:2], base_terms[:2], atol=1e-6)
            self.assertGreater(abs(float(new_terms[2] - base_terms[2])), 1e-4)
            rd = zero.clone(); rd[0, 3] = 4                                      # same row, one position earlier: terms 2 and 3 move, 1 does not
            t3, _ = terms(m, rd)
            self.assertAlmostEqual(float(t3[0]), float(base_terms[0]), places=6)
            self.assertGreater(abs(float(t3[1] - base_terms[1])), 1e-4)

    def test_row0_stays_zero_after_a_step(self):
        with tempfile.TemporaryDirectory() as td:
            m, vocab = b_model(td)
            items, P = self.packed(vocab)
            m.train()
            opt = tb.torch.optim.AdamW(m.parameters(), lr=1e-2, weight_decay=0.1)
            for _ in range(3):
                t = tp.to_tensors(tb.collate_b(P, np.arange(12)), "cpu")
                self.assertGreater(int((t["rd"] == 0).sum()), 0)                  # "none" is used a lot: an unmasked row 0 would move
                loss = tp.kl_rows(tb.cand_scores_b(m, t), t["q"], t["logq"]).mean()
                opt.zero_grad(); loss.backward(); opt.step()
            w = m.rd.weight.detach()
            self.assertEqual(float(w[tb.NONE].abs().max()), 0.0)
            self.assertGreater(float(w[2:].abs().max()), 0.0)                    # the other rows do learn

    def test_save_load_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            m, vocab = b_model(td, rd_scale=0.3)
            items, P = self.packed(vocab)
            path = os.path.join(td, "tinylm-B.npz")
            tb.save_b(m, vocab.chars, path)
            z = np.load(path)
            self.assertEqual(z["rd_emb"].shape, (len(m.table), 32))
            self.assertEqual(z["rd_syl"].tolist(), m.table)
            m2, vocab2 = tb.load_b(path)
            self.assertEqual(vocab2.chars, vocab.chars)
            t = tp.to_tensors(tb.collate_b(P, np.arange(len(items))), "cpu")
            with torch.no_grad():
                a, b = tb.cand_scores_b(m, t).numpy(), tb.cand_scores_b(m2.eval(), t).numpy()
            np.testing.assert_array_equal(a, b)
            with torch.no_grad():
                c = tb.cand_scores_b(b_model(td)[0], t).numpy()                   # R really matters: zeros score differently
            self.assertGreater(np.abs(a[a > tb.tp.NEG / 2] - c[a > tb.tp.NEG / 2]).max(), 1e-4)

    @staticmethod
    def s7_records(td, rows, cands):
        """score.py's records of the same rows with the S7a numpy model (m.npz), by row number; row 11 (a 64-character candidate) is left
        out because ll_many raises on it."""
        keep = [i for i in range(len(rows)) if i != 10]
        import score as s7
        recs = s7.build_records(NumpyLM(os.path.join(td, "m.npz")), [rows[i] for i in keep], [cands[i] for i in keep])
        for i, r in zip(keep, recs):                                             # build_records numbers its own input; restore row numbers
            r["i"] = i + 1
        return {r["i"]: r for r in recs}

    def write_eval(self, td, n_rows=40, seed=3):
        rows, cands, teacher = make_data_b(n_rows, seed)
        rp, dp, _ = t2.write_files(td, "ev", rows, cands, teacher)
        return rows, cands, rp, dp

    def test_score_b_records(self):
        import score as s7
        import fusegrid
        import score_b
        with tempfile.TemporaryDirectory() as td:
            m, vocab = b_model(td)                                               # R = 0: B must equal the S7a score of the same weights
            path = os.path.join(td, "tinylm-B.npz")
            tb.save_b(m, vocab.chars, path)
            rows, cands, rp, dp = self.write_eval(td)
            out = os.path.join(td, "B.jsonl")
            t1 = s7.top1_from_dump(rows, cands)
            score_b.main(["--npz", path, "--backend", "torch", "--dump", dp, "--rows", rp, "--name", "ev", "--expect-top1", str(t1), "--out", out])
            got = fusegrid.load_scores(out)                                      # the fusion code accepts it
            want = self.s7_records(td, rows, cands)
            self.assertEqual(len(got), len(rows))
            n_noll = 0
            for g in got:
                w = want.get(g["i"])
                if w is not None:
                    for k in ("i", "n", "ng", "ok", "ed", "glen", "key", "key16"):
                        self.assertEqual(g[k], w[k], (g["i"], k))
                self.assertEqual(g["n"] >= 2, "ll" in g)
                if g["n"] < 2:
                    continue
                if g.get("noll"):
                    n_noll += 1
                    self.assertTrue(g["i"] in (5, 9, 11), g["i"])
                    for k in ("ll", "ll0", "ll16"):
                        self.assertEqual(g[k], [0.0] * g["n"])
                    self.assertEqual(fusegrid.pick(g, 1.0, math.inf), 0)           # the fusion keeps the n-gram first
                    continue
                self.assertNotIn("noll", g)
                self.assertEqual(g["ll16"], g["ll"])
                np.testing.assert_allclose(g["ll"], w["ll"], atol=1e-4)           # R = 0: the S7a numbers
                np.testing.assert_allclose(g["ll0"], w["ll0"], atol=1e-4)
            self.assertEqual(n_noll, 3)                                          # rows 5, 9, 11 (n >= 2 and unscorable); row 3 has n = 1

    def test_score_b_uses_R_and_stops_on_top1_mismatch(self):
        import score as s7
        import score_b
        with tempfile.TemporaryDirectory() as td:
            m, vocab = b_model(td, rd_scale=0.5)
            path = os.path.join(td, "tinylm-B.npz")
            tb.save_b(m, vocab.chars, path)
            rows, cands, rp, dp = self.write_eval(td)
            out, out0 = os.path.join(td, "B.jsonl"), os.path.join(td, "B0.jsonl")
            t1 = s7.top1_from_dump(rows, cands)
            score_b.main(["--npz", path, "--dump", dp, "--rows", rp, "--name", "ev", "--expect-top1", str(t1), "--out", out])
            r = [json.loads(l) for l in open(out)]
            want = self.s7_records(td, rows, cands)
            i = next(k for k, x in enumerate(r) if x["n"] >= 2 and not x.get("noll"))
            self.assertGreater(max(abs(a - b) for a, b in zip(r[i]["ll"], want[i + 1]["ll"])), 1e-3)     # random R moved the score
            self.assertNotEqual(r[i]["ll"], r[i]["ll0"])
            with self.assertRaises(SystemExit) as cm:
                score_b.main(["--npz", path, "--dump", dp, "--rows", rp, "--name", "ev", "--expect-top1", str(t1 + 1), "--out", out0])
            self.assertIn("STOP", str(cm.exception))
            self.assertFalse(os.path.exists(out0))

    def run_main(self, td, out, extra=()):
        a = t2.write_files(td, "a", *make_data_b(40))
        b = t2.write_files(td, "b", *make_data_b(30, seed=2))
        tb.main(["--npz", os.path.join(td, "m.npz"), "--data", *a, *b, "--out", out, "--epochs", "2", "--batch", "4",
                 "--max-steps", "30", "--warmup", "5", "--lr", "1e-3", "--val-frac", "0.1", "--log-every", "1000",
                 "--val-every", "1000", "--device", "cpu", *extra])

    def test_end_to_end_tiny_run(self):
        import score as s7
        import score_b
        with tempfile.TemporaryDirectory() as td:
            t2.tiny_npz(td)
            out = os.path.join(td, "out")
            self.run_main(td, out)
            info = json.load(open(os.path.join(out, "train-B.json")))
            self.assertEqual(info["steps"], 28)                                  # 54 training rows / 4 = 14 batches x 2 epochs (< --max-steps)
            self.assertEqual(info["counts"]["unusable"], {"n<2": 2, "skip": 2, "too_long": 2, "len_mismatch": 4})
            self.assertEqual(info["counts"]["usable"], 70 - 10)
            self.assertEqual(info["n_syl"], len(SYLS) + 2)
            self.assertEqual([v[0] for v in info["val_curve"]], [0, 28])
            self.assertEqual(len(info["input_sha256"]["files"]), 6)
            z = np.load(os.path.join(out, "tinylm-B.npz"))
            self.assertEqual(z["rd_syl"].tolist(), tb.TABLE_HEAD + sorted(SYLS))
            self.assertFalse(z["rd_emb"][0].any())                                # "none" untouched
            self.assertTrue(z["rd_emb"][2:].any())                                # the syllable rows were trained
            rows, cands, rp, dp = self.write_eval(td)                             # score_b loads what train_b wrote
            sc = os.path.join(td, "B.jsonl")
            score_b.main(["--npz", os.path.join(out, "tinylm-B.npz"), "--dump", dp, "--rows", rp, "--name", "ev",
                          "--expect-top1", str(s7.top1_from_dump(rows, cands)), "--out", sc])
            self.assertEqual(len([l for l in open(sc)]), len(rows))
            out2 = os.path.join(td, "out2")                                       # same seed, same data: same order
            self.run_main(td, out2)
            self.assertEqual(info["train_order_sha256"], json.load(open(os.path.join(out2, "train-B.json")))["train_order_sha256"])

    def test_count_mismatch_stops(self):
        with tempfile.TemporaryDirectory() as td:
            rows, cands, teacher = make_data_b(10)
            a = t2.write_files(td, "a", rows, cands, teacher[:-1])
            with self.assertRaises(SystemExit) as cm:
                tb.main(["--npz", t2.tiny_npz(td), "--data", *a, "--out", os.path.join(td, "o"), "--device", "cpu"])
            self.assertIn("STOP", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
