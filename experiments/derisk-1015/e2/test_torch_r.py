"""E2 torch tests (skipped without torch; written on a Mac that has none, so FIRST RUN is on Colab/Windows, before any training).
Run:  python -W ignore -m unittest discover -s experiments/derisk-1015/e2 -p 'test_torch_r.py' -v
"""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tinylm_r as tr  # noqa: E402

try:
    import torch
except ImportError:      # pragma: no cover
    torch = None

if torch is not None:
    import torch_r
    import train_r
    from torch_model import GPT

CHARS = list("長好不一了嗎行你我他是的在再")
SYLS = ["ㄓㄤˇ", "ㄔㄤˊ", "ㄏㄠˇ", "ㄅㄨˋ", "ㄅㄨˊ", "ㄧ", "ㄧˊ", "ㄌㄜ˙", "ㄌㄧㄠˇ", "ㄇㄚ˙", "ㄒㄧㄥˊ", "ㄏㄤˊ", "ㄋㄧˇ", "ㄨㄛˇ"]


@unittest.skipIf(torch is None, "torch not installed")
class TestTorchR(unittest.TestCase):
    def model(self):
        torch.manual_seed(0)
        m = torch_r.GPTR(len(CHARS) + 3, 2, 16, 2, len(SYLS) + 1)
        with torch.no_grad():                    # non-trivial values everywhere (the init is tiny)
            for p in m.parameters():
                p.add_(torch.randn_like(p) * 0.1)
            m.rd.weight[0].zero_()
        return m

    def test_no_reading_is_plain_gpt(self):
        m = self.model().eval()
        g = GPT(m.cfg["V"], 2, 16, 2)
        g.load_state_dict({k: v for k, v in m.state_dict().items() if k != "rd.weight"})
        ids = torch.randint(3, len(CHARS) + 3, (3, 7))
        self.assertTrue(torch.equal(m(ids), g.eval()(ids)))
        self.assertTrue(torch.equal(m(ids, torch.zeros_like(ids)), m(ids)))

    def test_noread_row_stays_zero_after_a_step(self):
        m = self.model()
        opt = torch.optim.AdamW(m.parameters(), lr=1e-2, weight_decay=0.1)
        ids = torch.randint(3, len(CHARS) + 3, (4, 8))
        rd = torch.randint(0, len(SYLS) + 1, (4, 8))
        m(ids, rd).sum().backward()
        opt.step()
        self.assertEqual(float(m.rd.weight[0].abs().max()), 0.0)
        self.assertGreater(float(m.rd.weight[1:].abs().max()), 0.0)

    def test_numpy_and_torch_agree_through_the_exporter(self):
        m = self.model().eval()
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "m.npz")
            torch_r.export_npz_r(m, CHARS, SYLS, p)
            items = [("我他", "你好是", [3, 4, 5]), ("", "你好", [3, 4]), ("你", "他的在再", None)]
            a = tr.ll_many_r(tr.NumpyLMR(p), items)
            b = tr.ll_many_r(torch_r.TorchLMR(p), items)
            self.assertLess(float(np.abs(a - b).max()), 1e-4)
            self.assertEqual(float(np.abs(tr.ll_many_r(tr.NumpyLMR(p), items) - a).max()), 0.0)

    def test_train_smoke_on_synthetic_work_dir(self):
        rng = np.random.default_rng(0)
        with tempfile.TemporaryDirectory() as w, tempfile.TemporaryDirectory() as out:
            open(os.path.join(w, "vocab.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(CHARS) + "\n")
            open(os.path.join(w, "rdvocab.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(SYLS) + "\n")
            for name in ("wiki", "coll"):
                ids, rd = [], []
                for n in (5, 9, 30, 64, 70, 130, 7, 12) * 6:
                    ids += [2] + list(rng.integers(3, len(CHARS) + 3, n))
                    rd += [0] + list(rng.integers(1, len(SYLS) + 1, n))
                np.save(os.path.join(w, f"ids-{name}.npy"), np.array(ids, dtype=np.uint16))
                np.save(os.path.join(w, f"rd-{name}.npy"), np.array(rd, dtype=np.uint16))
            train_r.main(["--size", "T", "--work", w, "--out", out, "--max-steps", "6", "--tokens-per-batch", "256", "--warmup", "2",
                          "--split-probs", "0.5", "0.2", "0.3", "--device", "cpu"])
            m = tr.NumpyLMR(os.path.join(out, "tinylm-Tr.npz"))
            self.assertEqual(m.rvocab.syls, SYLS)
            info = json.load(open(os.path.join(out, "train-Tr.json")))
            self.assertEqual(info["steps"], 6); self.assertEqual(info["split_probs"], [0.5, 0.2, 0.3])


if __name__ == "__main__":
    unittest.main()
