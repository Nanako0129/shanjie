"""SJ-SR-7 additions (contract sj-lm-7 section 2.7): weighted --extra PATH[:WEIGHT], the vocabulary counted over the extra runs files (once each),
prep.py text --half; the unchanged parts are pinned by hash (encode without extras) and by the S7a comparison below.
S7f changes to S7a, numpy only (no torch, no OpenCC files): train.py's epoch and batch plan (batching.build_epoch / plan) without
--extra is exactly S7a's; with --extra every piece of the extra ids is in the epoch once; prep.py text encodes with the existing vocab.
Run: python3 -W ignore -m unittest experiments/s7a-tinylm/test_extra.py -v   (from the repo root)
"""
import collections
import hashlib
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
    """train.build_epoch + the batch lines of train.main as they were at S7a (568297d), the reference for 'unchanged'."""
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
        got = batching.plan(self.work, [], 512, 20261010, 7)
        for a, b in zip(got[:3], (ids, starts, lengths)):
            self.assertEqual(a.dtype, b.dtype)
            self.assertEqual(a.tobytes(), b.tobytes())
        self.assertEqual(len(got[3]), 7)
        self.assertEqual([x.tobytes() for x in got[3]], [x.tobytes() for x in want])
        full = batching.plan(self.work, [], 512, 20261010)[3]
        self.assertEqual([x.tobytes() for x in full], [x.tobytes() for x in batching.make_batches(lengths, 512, np.random.default_rng(20261010))])

    def test_extra_pieces_are_in_the_epoch_once(self):
        ids0, starts0, lengths0 = s7a_epoch(self.work)
        ids, starts, lengths, batches = batching.plan(self.work, [(self.extra, 1)], 512, 20261012)
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
            batching.build_epoch(self.work, [(bad, 1)])


class TestPrepText(unittest.TestCase):
    def test_encode_with_existing_vocab(self):
        with tempfile.TemporaryDirectory() as work:
            with open(os.path.join(work, "vocab.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write("我\n們\n去\n吃\n飯\n好\n")
            src = os.path.join(work, "chat.txt")
            with open(src, "w", encoding="utf-8", newline="\n") as f:
                f.write("我們去吃飯，好嗎\n好\nok 我們\n")
            with mock.patch.object(prep.bc, "convert", lambda text, *conv, **kw: text):     # no OpenCC tables here
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


class TestWeightedExtra(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = self.tmp.name
        rng = np.random.default_rng(5)
        np.save(os.path.join(self.work, "ids-wiki.npy"), fake_ids(rng, 60))
        np.save(os.path.join(self.work, "ids-coll.npy"), fake_ids(rng, 30))
        self.a, self.b = os.path.join(self.work, "ids-a.npy"), os.path.join(self.work, "ids-b.npy")
        np.save(self.a, fake_ids(rng, 40))
        np.save(self.b, fake_ids(rng, 25))

    def tearDown(self):
        self.tmp.cleanup()

    def test_weights_repeat_every_piece(self):
        ids, starts, lengths, batches = batching.plan(self.work, [(self.a, 5), (self.b, 1)], 512, 20261012)
        n_base = len(np.load(os.path.join(self.work, "ids-wiki.npy"))) + len(np.load(os.path.join(self.work, "ids-coll.npy")))
        a, b = np.load(self.a), np.load(self.b)
        sa, la = batching.pieces(a)
        sb, lb = batching.pieces(b)
        in_a = (starts >= n_base) & (starts < n_base + len(a))
        in_b = starts >= n_base + len(a)
        for want_starts, want_len, w, sel in ((sa + n_base, la, 5, in_a), (sb + n_base + len(a), lb, 1, in_b)):
            self.assertEqual(sorted(starts[sel].tolist()), sorted(want_starts.tolist() * w))      # every piece exactly w times
            self.assertEqual(sorted(lengths[sel].tolist()), sorted(want_len.tolist() * w))
        self.assertEqual(sorted(np.concatenate(batches).tolist()), list(range(len(lengths))))     # every copy trained on once per epoch
        for k in np.flatnonzero(in_a)[:20]:                                                      # a copy reads the right tokens
            j = starts[k] - n_base
            self.assertEqual(ids[starts[k]:starts[k] + lengths[k]].tobytes(), a[j:j + lengths[k]].tobytes())

    def test_bare_extra_is_weight_one(self):
        self.assertEqual(batching.parse_extra("ids-a.npy"), ("ids-a.npy", 1))
        self.assertEqual(batching.parse_extra("ids-a.npy:1"), ("ids-a.npy", 1))
        self.assertEqual(batching.parse_extra("/x:y/ids-a.npy:5"), ("/x:y/ids-a.npy", 5))
        one = batching.plan(self.work, [batching.parse_extra(self.a)], 512, 7)
        w1 = batching.plan(self.work, [batching.parse_extra(self.a + ":1")], 512, 7)
        for x, y in zip(one[:3], w1[:3]):
            self.assertEqual(x.tobytes(), y.tobytes())
        self.assertEqual([x.tobytes() for x in one[3]], [x.tobytes() for x in w1[3]])

    def test_plan_is_deterministic_per_seed_and_steps_follow_tokens(self):
        ex = [(self.a, 5), (self.b, 1)]
        p1, p2, p3 = (batching.plan(self.work, ex, 512, s) for s in (20261012, 20261012, 20261018))
        self.assertEqual([x.tobytes() for x in p1[3]], [x.tobytes() for x in p2[3]])
        self.assertNotEqual([x.tobytes() for x in p1[3]], [x.tobytes() for x in p3[3]])
        plain = batching.plan(self.work, [], 512, 20261012)
        self.assertGreater(len(p1[3]), len(plain[3]))                                             # more tokens -> more steps
        self.assertEqual(sum(int(p1[2][i].sum()) for i in p1[3]), int(p1[2].sum()))               # one epoch = every weighted piece

    def test_bad_weight(self):
        with self.assertRaises(ValueError):
            batching.build_epoch(self.work, [(self.a, 0)])


class TestPrepVocabAndHalf(unittest.TestCase):
    def write(self, work, name, text):
        with open(os.path.join(work, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def test_encode_without_extras_is_byte_identical_to_before(self):
        # hashes taken from the code before SJ-SR-7 (git 092042ee) on this fixture
        with tempfile.TemporaryDirectory() as work:
            self.write(work, "runs-wiki.txt", "我們去吃飯\n今天天氣真好\n你好嗎\n")
            self.write(work, "runs-coll.txt", "好嗎\n我們好嗎\n")
            cc = collections.Counter()
            for n in ("wiki", "coll"):
                cc.update(open(os.path.join(work, f"runs-{n}.txt"), encoding="utf-8").read().replace("\n", ""))
            json.dump({"charcount": dict(cc)}, open(os.path.join(work, "charcount.json"), "w"))
            prep.cmd_encode(type("A", (), {"work": work, "min_count": 2, "extra": []})())
            got = {f: hashlib.sha256(open(os.path.join(work, f), "rb").read()).hexdigest() for f in ("vocab.txt", "ids-wiki.npy", "ids-coll.npy", "encode-report.json")}
        self.assertEqual(got, {
            "vocab.txt": "97fe9df891e7ac0fe12f08fb9a41b04c912be588c8d75960ab736f358b2c0f4d",
            "ids-wiki.npy": "1a53db1507c87ceb941552e3e8bd207ac236927d2fabf304482f978d511e7b5f",
            "ids-coll.npy": "b5d209b96c52a46828f94a82f7edb56bd5f98e9da5cf48e76c73a8c578cee5e3",
            "encode-report.json": "90dae13175381f869b43b71265756cdc777893cbd46da217c3832aaab43f9135"})

    def test_vocab_counts_each_extra_once_not_by_weight(self):
        with tempfile.TemporaryDirectory() as work:
            self.write(work, "runs-wiki.txt", "我我我我我我\n")
            self.write(work, "runs-coll.txt", "你你你你你你\n")
            json.dump({"charcount": {"我": 6, "你": 6}}, open(os.path.join(work, "charcount.json"), "w"))
            self.write(work, "runs-a.txt", "好好好好\n去去\n")      # 好 x4 alone: < 5, would be 20 if multiplied by a weight of 5
            self.write(work, "runs-b.txt", "去去去吃\n")            # 去 2 + 3 = 5 over the two files: in; 吃 x1: out
            prep.cmd_encode(type("A", (), {"work": work, "min_count": 5, "extra": ["a", "b"]})())
            self.assertEqual(prep.load_vocab(work), ["你", "我", "去"])
            ids = np.load(os.path.join(work, "ids-a.npy")).tolist()
            self.assertEqual(ids, [BOS, UNK, UNK, UNK, UNK, BOS, 5, 5])
            self.assertEqual(np.load(os.path.join(work, "ids-b.npy")).tolist(), [BOS, 5, 5, 5, UNK])
            rep = json.load(open(os.path.join(work, "encode-report.json")))
            self.assertEqual(rep["extra"]["a"]["tokens"], 8)
            self.assertEqual(rep["vocab_size"], 6)

    def test_half_keeps_first_hash_byte_below_128(self):
        # SHA-256 first bytes by hand (python hashlib, checked once): 你好嗎 7d=125, 我是你 4e=78, 吃飯了沒 5c=92 keep;
        # 我們去吃飯 a5=165, 好嗎 82=130, 今天天氣真好 a3=163, 他不是我 f0=240, 天氣好嗎 d1=209 drop
        keep, drop = ["你好嗎", "我是你", "吃飯了沒"], ["我們去吃飯", "好嗎", "今天天氣真好", "他不是我", "天氣好嗎"]
        self.assertTrue(all(prep.half_keep(s) for s in keep))
        self.assertFalse(any(prep.half_keep(s) for s in drop))
        with tempfile.TemporaryDirectory() as work:
            src = os.path.join(work, "in.txt")
            self.write(work, "in.txt", "\n".join(drop[:2] + keep[:1] + drop[2:] + keep[1:]) + "\n")
            with mock.patch.object(prep.bc, "convert", lambda text, *conv, **kw: text):
                rep = prep.write_runs(src, work, "g", (), half=True)
                prep.write_runs(src, work, "all", ())
            self.assertEqual(open(os.path.join(work, "runs-g.txt"), encoding="utf-8").read().split("\n")[:-1], keep)       # input order kept
            self.assertEqual(len(open(os.path.join(work, "runs-all.txt"), encoding="utf-8").read().split("\n")) - 1, 8)  # no --half: all
            self.assertEqual((rep["lines"], rep["half_dropped_runs"]), (8, 5))


if __name__ == "__main__":
    unittest.main()
