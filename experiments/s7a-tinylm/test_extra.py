"""S7f changes to S7a, numpy only (no torch, no OpenCC files): train.py's epoch and batch plan (batching.build_epoch / plan) without
--extra is exactly S7a's; with --extra every piece of the extra ids is in the epoch once; prep.py text encodes with the existing vocab.
Run: python3 -W ignore -m unittest experiments/s7a-tinylm/test_extra.py -v   (from the repo root)
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import batching  # noqa: E402
import prep  # noqa: E402
from tinylm import BOS, UNK  # noqa: E402


def fake_ids(rng, n_runs, vocab=40):
    """uint16 ids, BOS before every run; some runs are longer than one piece (65 tokens)."""
    parts = []
    for _ in range(n_runs):
        n = int(rng.choice([2, 5, 9, 64, 65, 130, 200]))
        parts += [BOS] + list(rng.integers(3, vocab, n))
    return np.array(parts, dtype=np.uint16)


def s7a_epoch(work):
    """train.build_epoch + the batch lines of train.main as they were at S7a (cdaddf2), the reference for 'unchanged'."""
    wiki, coll = np.load(os.path.join(work, "ids-wiki.npy")), np.load(os.path.join(work, "ids-coll.npy"))
    ids = np.concatenate([wiki, coll])
    ws, wl = batching.pieces(wiki)
    cs, cl = batching.pieces(coll)
    starts = np.concatenate([ws] + [cs + len(wiki)] * prep.COLL_WEIGHT)
    lengths = np.concatenate([wl] + [cl] * prep.COLL_WEIGHT)
    return ids, starts, lengths


class TestEpoch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = self.tmp.name
        rng = np.random.default_rng(3)
        np.save(os.path.join(self.work, "ids-wiki.npy"), fake_ids(rng, 300))
        np.save(os.path.join(self.work, "ids-coll.npy"), fake_ids(rng, 120))
        self.extra = os.path.join(self.work, "ids-chat.npy")
        np.save(self.extra, fake_ids(rng, 90))

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_extra_is_s7a(self):
        ids, starts, lengths = s7a_epoch(self.work)
        rng = np.random.default_rng(20261010)
        want = batching.make_batches(lengths, 512, rng)[:7]
        got = batching.plan(self.work, None, 512, 20261010, 7)
        for a, b in zip(got[:3], (ids, starts, lengths)):
            self.assertEqual(a.dtype, b.dtype)
            self.assertEqual(a.tobytes(), b.tobytes())
        self.assertEqual(len(got[3]), 7)
        self.assertEqual([x.tobytes() for x in got[3]], [x.tobytes() for x in want])
        full = batching.plan(self.work, None, 512, 20261010)[3]
        self.assertEqual([x.tobytes() for x in full], [x.tobytes() for x in batching.make_batches(lengths, 512, np.random.default_rng(20261010))])

    def test_extra_pieces_are_in_the_epoch_once(self):
        ids0, starts0, lengths0 = s7a_epoch(self.work)
        ids, starts, lengths, batches = batching.plan(self.work, self.extra, 512, 20261012)
        ex = np.load(self.extra)
        self.assertEqual(ids[:len(ids0)].tobytes(), ids0.tobytes())
        self.assertEqual(ids[len(ids0):].tobytes(), ex.tobytes())
        es, el = batching.pieces(ex)
        self.assertEqual(starts[:len(starts0)].tobytes(), starts0.tobytes())
        self.assertEqual(starts[len(starts0):].tolist(), (es + len(ids0)).tolist())
        self.assertEqual(lengths[len(lengths0):].tolist(), el.tolist())
        used = np.sort(np.concatenate(batches))
        self.assertEqual(used.tolist(), list(range(len(lengths))))           # every piece, extra included, exactly once
        for k in range(len(starts0), len(starts)):                            # the extra pieces read the extra tokens
            self.assertEqual(ids[starts[k]:starts[k] + lengths[k]].tobytes(), ex[es[k - len(starts0)]:es[k - len(starts0)] + el[k - len(starts0)]].tobytes())

    def test_extra_must_be_ids(self):
        bad = os.path.join(self.work, "bad.npy")
        np.save(bad, np.array([5, 6, BOS, 7], dtype=np.uint16))
        with self.assertRaises(ValueError):
            batching.build_epoch(self.work, bad)


class TestPrepText(unittest.TestCase):
    def test_encode_with_existing_vocab(self):
        with tempfile.TemporaryDirectory() as work:
            with open(os.path.join(work, "vocab.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write("我\n們\n去\n吃\n飯\n好\n")
            src = os.path.join(work, "chat.txt")
            with open(src, "w", encoding="utf-8", newline="\n") as f:
                f.write("我們去吃飯，好嗎\n好\nok 我們\n")
            with mock.patch.object(prep.bc, "convert", lambda text, *conv: text):     # no OpenCC tables here
                rep = prep.encode_extra(src, work, "chat", ())
            runs = open(os.path.join(work, "runs-chat.txt"), encoding="utf-8").read()
            self.assertEqual(runs, "我們去吃飯\n好嗎\n我們\n")                       # Han runs of length >= 2, as the colloquial text
            ids = np.load(os.path.join(work, "ids-chat.npy"))
            self.assertEqual(ids.tolist(), [BOS, 3, 4, 5, 6, 7, BOS, 8, UNK, BOS, 3, 4])
            self.assertEqual((rep["tokens"], rep["runs"], rep["lines"]), (12, 3, 3))
            self.assertEqual(json.load(open(os.path.join(work, "text-chat.json")))["ids_sha256"], prep.sha256_file(os.path.join(work, "ids-chat.npy")))
            self.assertEqual(open(os.path.join(work, "vocab.txt"), encoding="utf-8").read(), "我\n們\n去\n吃\n飯\n好\n")
            with self.assertRaises(SystemExit):
                prep.encode_extra(src, work, "coll", ())


if __name__ == "__main__":
    unittest.main()
