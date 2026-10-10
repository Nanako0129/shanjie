"""S7a unit tests that need torch (skipped cleanly when it is not importable; main runs them on 188 on CPU, before training):
numpy vs torch through the real export function, npz -> torch roundtrip, and a tiny end-to-end run of prep.encode + train.py."""
import json
import os
import tempfile
import unittest

import numpy as np

import tinylm
from tinylm import BOS, NumpyLM, ll_many

try:
    import torch
    HAVE_TORCH = True
except ImportError:
    HAVE_TORCH = False


@unittest.skipUnless(HAVE_TORCH, "torch is not installed")
class TestTorch(unittest.TestCase):
    CHARS = list("你我他好嗎的是不了天氣")

    def make(self, td, n_layer=3, d=48, n_head=3):
        import torch_model as tm
        torch.manual_seed(0)
        model = tm.GPT(len(self.CHARS) + 3, n_layer=n_layer, d=d, n_head=n_head)
        with torch.no_grad():
            for p in model.parameters():      # default init has identity LayerNorms and zero biases: make every code path matter
                p.add_(torch.randn_like(p) * 0.15)
        path = os.path.join(td, "m.npz")
        tm.export_npz(model, self.CHARS, path)
        return tm, model.eval(), path

    def test_numpy_equals_torch_through_export(self):
        with tempfile.TemporaryDirectory() as td:
            tm, model, path = self.make(td)
            ids = torch.randint(0, len(self.CHARS) + 3, (5, 20))
            with torch.no_grad():
                want = model(ids).numpy()
            got = NumpyLM(path).logits(ids.numpy())
            np.testing.assert_allclose(got, want, atol=1e-4)
            items = [("你好", "我他不了"), ("", "天氣好嗎的是不了你我他好嗎"), ("嗎", "是")]
            tl = tm.TorchLM(path)
            d = np.abs(ll_many(NumpyLM(path), items) - ll_many(tl, items)).max()
            self.assertLess(d, 1e-4)                         # the contract's parity bound, on the exported weights

    def test_npz_roundtrip_into_torch(self):
        with tempfile.TemporaryDirectory() as td:
            tm, model, path = self.make(td)
            back, vocab = tm.from_npz(path)
            ids = torch.randint(0, len(self.CHARS) + 3, (4, 12))
            with torch.no_grad():
                np.testing.assert_allclose(back.eval()(ids).numpy(), model(ids).numpy(), atol=1e-5)
            self.assertEqual(vocab.chars, self.CHARS)

    def test_causal_in_torch_and_numpy(self):
        with tempfile.TemporaryDirectory() as td:
            tm, model, path = self.make(td)
            a = torch.randint(3, len(self.CHARS) + 3, (1, 10))
            b = a.clone(); b[0, 6:] = 3
            with torch.no_grad():
                np.testing.assert_allclose(model(a)[:, :6].numpy(), model(b)[:, :6].numpy(), atol=1e-6)

    def test_tiny_training_run(self):
        import prep
        import train
        with tempfile.TemporaryDirectory() as td:
            runs = ["你好嗎", "今天天氣真好", "我是你", "他不是我", "天氣好嗎"]
            with open(os.path.join(td, "runs-wiki.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(runs * 400) + "\n")
            with open(os.path.join(td, "runs-coll.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(runs * 20) + "\n")
            cc = {}
            for r in runs:
                for c in r:
                    cc[c] = cc.get(c, 0) + 100
            with open(os.path.join(td, "charcount.json"), "w", encoding="utf-8") as f:
                json.dump({"charcount": cc}, f)
            prep.cmd_encode(type("A", (), {"work": td, "min_count": 5})())
            out = os.path.join(td, "out")
            train.main(["--size", "T", "--work", td, "--out", out, "--tokens-per-batch", "64", "--max-steps", "150", "--warmup", "10", "--device", "cpu"])
            info = json.load(open(os.path.join(out, "train-T.json")))
            self.assertEqual(info["steps"], 150)
            self.assertLess(info["final_loss_nats_per_token"], np.log(info["vocab"]) - 0.5)       # it learned something
            m = NumpyLM(os.path.join(out, "tinylm-T.npz"))                                         # numpy reads what train.py exported
            good, bad = tinylm.ll(m, "", "今天天氣真好"), tinylm.ll(m, "", "好真氣天天今")
            self.assertGreater(good, bad)
            self.assertEqual(m.vocab.chars, prep.load_vocab(td))

    def test_lr_schedule(self):
        import train
        self.assertAlmostEqual(train.lr_at(0, 1000), 1e-3 / train.WARMUP)
        self.assertAlmostEqual(train.lr_at(train.WARMUP, 1000), 1e-3)
        self.assertAlmostEqual(train.lr_at(999, 1000), 0.0, delta=1e-8)

    def test_model_sizes(self):
        import torch_model as tm
        for name, V in (("S", 6000), ("M", 6000)):
            m = tm.GPT(V, **tm.CONFIGS[name])
            print(name, sum(p.numel() for p in m.parameters()), "parameters at V =", V)
        self.assertEqual(tm.CONFIGS["S"], dict(n_layer=4, d=256, n_head=4))
        self.assertEqual(tm.CONFIGS["M"], dict(n_layer=6, d=384, n_head=6))


if __name__ == "__main__":
    unittest.main()
