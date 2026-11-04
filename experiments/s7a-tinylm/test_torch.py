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
            prep.cmd_encode(type("A", (), {"work": td, "min_count": 5, "extra": [], "force": False})())
            out = os.path.join(td, "out")
            train.main(["--size", "T", "--work", td, "--out", out, "--tokens-per-batch", "64", "--max-steps", "150", "--warmup", "10", "--device", "cpu"])
            info = json.load(open(os.path.join(out, "train-T.json")))
            self.assertEqual(info["steps"], 150)
            self.assertIsNone(info["extra"])                                                      # no --extra: null, as before SJ-SR-7
            self.assertLess(info["final_loss_nats_per_token"], np.log(info["vocab"]) - 0.5)       # it learned something
            m = NumpyLM(os.path.join(out, "tinylm-T.npz"))                                         # numpy reads what train.py exported
            good, bad = tinylm.ll(m, "", "今天天氣真好"), tinylm.ll(m, "", "好真氣天天今")
            self.assertGreater(good, bad)
            self.assertEqual(m.vocab.chars, prep.load_vocab(td))

    def test_warm_start_from_npz(self):
        """SR-TW-W: prep.py encode --vocab-from takes the vocabulary of an npz, train.py --init starts from its weights with --peak-lr; a work dir
        with another vocabulary, or another size, is refused."""
        import prep
        import train
        with tempfile.TemporaryDirectory() as td:
            runs = ["你好嗎", "今天天氣真好", "我是你", "他不是我", "天氣好嗎"]
            def work(d, lines, extra_char=""):
                os.makedirs(d)
                for name, k in (("wiki", 400), ("coll", 20)):
                    with open(os.path.join(d, f"runs-{name}.txt"), "w", encoding="utf-8", newline="\n") as f:
                        f.write("\n".join(lines * k) + "\n")
                cc = {}
                for r in lines:
                    for c in r:
                        cc[c] = cc.get(c, 0) + 100
                with open(os.path.join(d, "charcount.json"), "w", encoding="utf-8") as f:
                    json.dump({"charcount": cc}, f)
            a, b = os.path.join(td, "a"), os.path.join(td, "b")
            work(a, runs)
            prep.cmd_encode(type("A", (), {"work": a, "min_count": 5, "extra": [], "force": False})())
            train.main(["--size", "T", "--work", a, "--out", os.path.join(td, "oa"), "--tokens-per-batch", "64", "--max-steps", "150", "--warmup", "10", "--device", "cpu"])
            npz = os.path.join(td, "oa", "tinylm-T.npz")
            work(b, runs + ["新字在這裡"])                                       # characters the npz does not have: UNK under its vocabulary
            prep.main(["encode", "--work", b, "--vocab-from", npz])
            self.assertEqual(prep.load_vocab(b), NumpyLM(npz).vocab.chars)
            rep = json.load(open(os.path.join(b, "encode-report.json")))
            self.assertEqual(rep["vocab_from_sha256"], prep.sha256_file(npz))
            self.assertGreater(rep["wiki"]["unk_fraction"], 0)
            before = tinylm.ll(NumpyLM(npz), "", "今天天氣真好")
            train.main(["--size", "T", "--work", b, "--out", os.path.join(td, "ob"), "--tokens-per-batch", "64", "--max-steps", "20", "--warmup", "5",
                        "--init", npz, "--peak-lr", "1e-4", "--device", "cpu"])
            info = json.load(open(os.path.join(td, "ob", "train-T.json")))
            self.assertEqual((info["peak_lr"], info["init_sha256"], info["steps"]), (1e-4, prep.sha256_file(npz), 20))
            after = tinylm.ll(NumpyLM(os.path.join(td, "ob", "tinylm-T.npz")), "", "今天天氣真好")
            self.assertLess(abs(after - before), 1.0)                                # it started from the npz (a fresh model scores far lower)
            with self.assertRaises(SystemExit):                                       # a vocabulary counted from b's text, not the npz's
                prep.cmd_encode(type("A", (), {"work": b, "min_count": 5, "extra": [], "force": False})())
                train.main(["--size", "T", "--work", b, "--out", os.path.join(td, "oc"), "--max-steps", "2", "--init", npz, "--device", "cpu"])
            prep.main(["encode", "--work", b, "--vocab-from", npz])
            with self.assertRaises(SystemExit):                                       # size S is not the npz's T
                train.main(["--size", "S", "--work", b, "--out", os.path.join(td, "od"), "--max-steps", "2", "--init", npz, "--device", "cpu"])

    def tiny_work(self, td, flags=True):
        """A work dir like test_tiny_training_run's, encoded, with flags-wiki.u8 / flags-coll.u8 (every run's flag cycles 0,1,0,0,1 with the run text)."""
        runs = ["你好嗎", "今天天氣真好", "我是你", "他不是我", "天氣好嗎"]
        for name, k in (("wiki", 400), ("coll", 20)):
            with open(os.path.join(td, f"runs-{name}.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(runs * k) + "\n")
            if flags:
                np.tile(np.array([0, 1, 0, 0, 1], dtype=np.uint8), k).tofile(os.path.join(td, f"flags-{name}.u8"))
        cc = {c: 100 for r in runs for c in r}
        with open(os.path.join(td, "charcount.json"), "w", encoding="utf-8") as f:
            json.dump({"charcount": cc}, f)
        import prep
        prep.cmd_encode(type("A", (), {"work": td, "min_count": 5, "extra": [], "force": False})())

    def train(self, td, out, *args, steps=40, seed=20261010):
        import train
        train.main(["--size", "T", "--work", td, "--out", out, "--tokens-per-batch", "64", "--max-steps", str(steps), "--warmup", "5",
                    "--seed", str(seed), "--device", "cpu", *args])
        return json.load(open(os.path.join(out, "train-T.json"))), np.load(os.path.join(out, "tinylm-T.npz"))

    def test_kl_term_math(self):
        import train
        torch.manual_seed(0)
        a, b = torch.randn(3, 5, 11), torch.randn(3, 5, 11)
        self.assertLess(float(train.kl_student_teacher(a, a).abs().max()), 1e-6)         # student == teacher: zero
        kl = train.kl_student_teacher(a, b)
        self.assertTrue(bool((kl > 0).all()))
        pa, pb = torch.softmax(a, -1).double(), torch.softmax(b, -1).double()
        np.testing.assert_allclose(kl.numpy(), (pa * (pa.log() - pb.log())).sum(-1).numpy(), atol=1e-5)    # KL(student || teacher)
        tgt = torch.randint(0, 11, (3, 5))
        ce = train.step_loss(a, tgt)
        keep = (torch.rand(3, 5) < 0.5).float()
        self.assertAlmostEqual(float(train.step_loss(a, tgt, a, keep, 2.0)), float(ce), places=5)                  # student == teacher: CE only
        want = float(ce) + 2.0 * float((train.kl_student_teacher(a, b) * keep).sum()) / keep.numel()
        self.assertAlmostEqual(float(train.step_loss(a, tgt, b, keep, 2.0)), want, places=5)                         # CE + lambda * masked mean KL
        self.assertAlmostEqual(float(train.step_loss(a, tgt, b, keep, 0.0)), float(ce), places=6)                  # lambda 0: the plain loss
        self.assertAlmostEqual(float(train.step_loss(a, tgt, b, torch.zeros(3, 5), 5.0)), float(ce), places=6)     # nothing unchanged: CE only
        self.assertGreater(float(train.step_loss(a, tgt, b, torch.ones(3, 5), 1.0)), float(ce))

    def test_default_run_is_unchanged_and_kl_lambda_zero_equals_it(self):
        """The new options at their defaults leave the run as it was; a teacher with --kl-lambda 0 gives the same weights (CPU, deterministic)."""
        with tempfile.TemporaryDirectory() as td:
            self.tiny_work(td)
            teacher_info, _ = self.train(td, os.path.join(td, "t"), steps=15, seed=1)
            teacher = os.path.join(td, "t", "tinylm-T.npz")
            plain, pz = self.train(td, os.path.join(td, "plain"))
            self.assertEqual((plain["weight_decay"], plain["kl_teacher"], plain["kl_lambda"], plain["changed_weight"]), (0.1, None, 0.0, None))
            same, sz = self.train(td, os.path.join(td, "same"), "--weight-decay", "0.1", "--kl-teacher", teacher, "--kl-lambda", "0")
            import prep
            self.assertEqual(same["kl_teacher"]["sha256"], prep.sha256_file(teacher))
            self.assertEqual(plain["curve"], same["curve"])
            for k in pz.files:
                np.testing.assert_allclose(pz[k], sz[k], atol=1e-6, err_msg=k)

    def test_kl_anchor_changed_weight_and_weight_decay_options_run_and_are_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            self.tiny_work(td)
            self.train(td, os.path.join(td, "t"), steps=15, seed=1)
            self.train(td, os.path.join(td, "t2"), steps=15, seed=2)
            start, teacher = os.path.join(td, "t", "tinylm-T.npz"), os.path.join(td, "t2", "tinylm-T.npz")     # the teacher is a different model
            plain, pz = self.train(td, os.path.join(td, "plain"), "--init", start, "--peak-lr", "1e-4")
            kl, kz = self.train(td, os.path.join(td, "kl"), "--init", start, "--peak-lr", "1e-4", "--kl-teacher", teacher, "--kl-lambda", "50")
            self.assertEqual(kl["kl_lambda"], 50.0)
            self.assertTrue(any(not np.allclose(pz[k], kz[k], atol=1e-7) for k in pz.files))          # the anchor changed the update
            cw, _ = self.train(td, os.path.join(td, "cw"), "--changed-weight", "0.1", "--weight-decay", "0")
            self.assertEqual((cw["changed_weight"], cw["weight_decay"]), (0.1, 0.0))
            os.remove(os.path.join(td, "flags-coll.u8"))                                              # a source without flags is refused
            with self.assertRaises(SystemExit):
                self.train(td, os.path.join(td, "bad"), "--changed-weight", "0.1")
            with self.assertRaises(SystemExit):
                self.train(td, os.path.join(td, "bad2"), "--kl-lambda", "1")                           # lambda without a teacher

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
        # S7b §2.1: about 30M and 65M parameters at the real vocabulary (V 9,717 = 9,714 characters + PAD, UNK, BOS)
        for name, lo, hi in (("L", 29.5e6, 31e6), ("XL", 64e6, 66.5e6)):
            n = sum(p.numel() for p in tm.GPT(9717, **tm.CONFIGS[name]).parameters())
            self.assertTrue(lo <= n <= hi, (name, n))


if __name__ == "__main__":
    unittest.main()
