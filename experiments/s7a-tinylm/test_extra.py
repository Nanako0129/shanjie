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

    def test_none_means_no_extras(self):                  # experiments/derisk-1015/e2/train_r.py passes None
        none, empty = batching.plan(self.work, None, 512, 3), batching.plan(self.work, [], 512, 3)
        for x, y in zip(none[:3], empty[:3]):
            self.assertEqual(x.tobytes(), y.tobytes())
        self.assertEqual([x.tobytes() for x in none[3]], [x.tobytes() for x in empty[3]])

    def test_parse_extra_accepts_only_ascii_digit_weights_of_one_or_more(self):
        for bad in ("p.npy:", "p.npy:0", "p.npy:00", "p.npy:x", "p.npy:-1", "p.npy:1.5", "p.npy:\u0665", "p.npy:\u00b2", ":5", "p.npy: 5"):
            with self.assertRaises(ValueError, msg=bad) as cm:
                batching.parse_extra(bad)
            self.assertIn("--extra", str(cm.exception))
        self.assertEqual(batching.parse_extra("p.npy:007"), ("p.npy", 7))


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
            prep.cmd_encode(type("A", (), {"work": work, "min_count": 2, "extra": [], "force": False})())
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
            prep.cmd_encode(type("A", (), {"work": work, "min_count": 5, "extra": ["a", "b"], "force": False})())
            self.assertEqual(prep.load_vocab(work), ["你", "我", "去"])
            ids = np.load(os.path.join(work, "ids-a.npy")).tolist()
            self.assertEqual(ids, [BOS, UNK, UNK, UNK, UNK, BOS, 5, 5])
            self.assertEqual(np.load(os.path.join(work, "ids-b.npy")).tolist(), [BOS, 5, 5, 5, UNK])
            rep = json.load(open(os.path.join(work, "encode-report.json")))
            self.assertEqual(rep["extra"]["a"]["tokens"], 8)
            self.assertEqual(rep["vocab_size"], 6)

    def test_half_keeps_whole_input_lines_by_the_hash_of_the_line(self):
        # SHA-256 first byte of the whole input line (hashlib, checked once). keep: 吃飯了沒 92, 好嗎，你好 52, 我是你 78;
        # drop: 天氣好嗎 209, 你好嗎，我是你 186, 我們去吃飯，好嗎 218.
        # 好嗎，你好 is kept although its first run 好嗎 alone would hash to 130 (drop); 你好嗎，我是你 is dropped although both of its
        # runs alone (你好嗎 125, 我是你 78) would be kept: the line is judged, not its runs.
        lines = ["天氣好嗎", "吃飯了沒", "好嗎，你好", "你好嗎，我是你", "我們去吃飯，好嗎", "我是你"]
        self.assertEqual([prep.half_keep(x) for x in lines], [False, True, True, False, False, True])
        with tempfile.TemporaryDirectory() as work:
            self.write(work, "in.txt", "\n".join(lines) + "\n")
            with mock.patch.object(prep.bc, "convert", lambda text, *conv, **kw: text):
                rep = prep.write_runs(os.path.join(work, "in.txt"), work, "g", (), half=True)
                prep.write_runs(os.path.join(work, "in.txt"), work, "all", ())
            self.assertEqual(open(os.path.join(work, "runs-g.txt"), encoding="utf-8").read(), "吃飯了沒\n好嗎\n你好\n我是你\n")   # input order kept
            self.assertEqual(len(open(os.path.join(work, "runs-all.txt"), encoding="utf-8").read().split("\n")) - 1, 9)           # no --half: all
            self.assertEqual((rep["lines"], rep["half_dropped_runs"]), (6, 5))                                                      # 1 + 2 + 2 runs

    def test_half_on_tsv_hashes_the_sentence_field_without_the_unit_column(self):
        # whole line a1\t吃飯了沒 hashes to 212 (drop) but the sentence 吃飯了沒 to 92 (keep); 7\t天氣好嗎 to 42 (keep) but 天氣好嗎 to 209 (drop)
        with tempfile.TemporaryDirectory() as work:
            self.write(work, "in.tsv", "a1\t吃飯了沒\n7\t天氣好嗎\n")
            with mock.patch.object(prep.bc, "convert", lambda text, *conv, **kw: text):
                prep.write_runs(os.path.join(work, "in.tsv"), work, "g", (), half=True)
            self.assertEqual(open(os.path.join(work, "runs-g.txt"), encoding="utf-8").read(), "吃飯了沒\n")

    def test_procs_gives_the_same_files_as_one_process(self):
        # the Pool path loads the real conversion tables in each worker, so this needs the OpenCC files (skipped without them)
        if not os.path.isdir(os.path.join(prep.bc.SRC, "opencc")):
            self.skipTest("no OpenCC files")
        # 3 batches of 2,000; the simplified 这/话 are there on purpose: they must come out as 這/話, so the workers really converted
        lines = [f"{i}\t这是第{i}句话，我們去吃飯好嗎" if i % 3 else f"{i}\tok 天氣真好" for i in range(4500)]
        conv = prep.bc.load_conv()
        with tempfile.TemporaryDirectory() as work:
            self.write(work, "in.tsv", "\n".join(lines) + "\n")
            for half in (False, True):
                reps = [prep.write_runs(os.path.join(work, "in.tsv"), work, f"p{p}", conv, half=half, procs=p) for p in (1, 2)]
                self.assertEqual(open(os.path.join(work, "runs-p1.txt"), "rb").read(), open(os.path.join(work, "runs-p2.txt"), "rb").read())
                self.assertEqual(reps[0], reps[1])
                self.assertEqual(reps[0]["lines"], 4500)
                out = open(os.path.join(work, "runs-p2.txt"), encoding="utf-8").read()
                self.assertIn("這是第", out)
                self.assertNotIn("这", out)

    def test_text_json_and_report_keep_the_old_bytes_on_the_default_path(self):
        # sha256 of text-chat.json written by the code before SJ-SR-7 (092042ee) for this input; run in a temp cwd so "input" is the relative path
        with tempfile.TemporaryDirectory() as work:
            self.write(work, "vocab.txt", "我\n們\n去\n吃\n飯\n好\n")
            self.write(work, "chat.txt", "我們去吃飯，好嗎\n好\nok 我們\n")
            cwd = os.getcwd()
            os.chdir(work)
            try:
                with mock.patch.object(prep.bc, "convert", lambda text, *conv, **kw: text):
                    rep = prep.encode_extra("chat.txt", ".", "chat", ())
            finally:
                os.chdir(cwd)
            raw = open(os.path.join(work, "text-chat.json"), "rb").read()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), "497524447723d0dc5f3f668084bf33f8af64cf79837f0ec1a6646e2922374748")
            self.assertEqual(json.dumps(rep, indent=1), raw.decode())                  # what `prep.py text` prints is the file
            self.assertEqual(list(rep)[-1], "ids_sha256")
            with mock.patch.object(prep.bc, "convert", lambda text, *conv, **kw: text):    # guard / half keys come after the old ones
                class G:
                    def any(self, run):
                        return False
                os.chdir(work)
                try:
                    rep = prep.encode_extra("chat.txt", ".", "chat", (), guard=G(), half=True)
                finally:
                    os.chdir(cwd)
            self.assertEqual(list(rep)[-2:], ["holdout_guard_skipped_runs", "half_dropped_runs"])
            self.assertEqual(list(rep).index("ids_sha256"), list(rep).index("holdout_guard_skipped_runs") - 1)


class TestEncodeGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = self.tmp.name
        for n, t in (("wiki", "我我我我我我\n"), ("coll", "你你你你你你\n"), ("a", "好好好好好\n"), ("b", "去去去去去\n")):
            with open(os.path.join(self.work, f"runs-{n}.txt"), "w", encoding="utf-8", newline="\n") as f:
                f.write(t)
        json.dump({"charcount": {"我": 6, "你": 6}}, open(os.path.join(self.work, "charcount.json"), "w"))

    def tearDown(self):
        self.tmp.cleanup()

    def encode(self, *extra, force=False):
        with mock.patch("sys.stdout", new=open(os.devnull, "w")):
            prep.cmd_encode(type("A", (), {"work": self.work, "min_count": 5, "extra": list(extra), "force": force})())

    def test_rejects_reserved_duplicate_and_missing_names(self):
        for bad in (("wiki",), ("coll",), ("a", "a"), ("nope",)):
            with self.assertRaises(SystemExit):
                self.encode(*bad)
        self.assertFalse(os.path.exists(os.path.join(self.work, "vocab.txt")))                  # refused before writing anything

    def test_refuses_a_vocab_from_another_extra_set_unless_forced(self):
        self.encode()                                                                              # R0-like: no extras
        keep = open(os.path.join(self.work, "vocab.txt"), "rb").read()
        with self.assertRaises(SystemExit):
            self.encode("a")
        self.assertEqual(open(os.path.join(self.work, "vocab.txt"), "rb").read(), keep)            # untouched
        self.encode()                                                                              # same set again is fine
        self.encode("a", force=True)
        self.assertEqual(json.load(open(os.path.join(self.work, "ids-vocab.json")))["extra"], ["a"])
        self.encode("a")                                                                           # now "a" is the recorded set
        with self.assertRaises(SystemExit):
            self.encode("a", "b")
        self.assertEqual(list(json.load(open(os.path.join(self.work, "encode-report.json")))["extra"]), ["a"])    # the set is in the report

    def test_a_work_dir_from_before_the_record_counts_as_no_extras(self):
        self.encode()
        os.remove(os.path.join(self.work, "ids-vocab.json"))
        self.encode()
        os.remove(os.path.join(self.work, "ids-vocab.json"))
        with self.assertRaises(SystemExit):
            self.encode("a")

    def test_check_vocab(self):
        self.encode("a", "b")
        w = self.work
        ids_a = os.path.join(w, "ids-a.npy")
        prep.check_vocab(w, [ids_a, os.path.join(w, "ids-b.npy")])                                # consistent
        prep.check_vocab(w, [])                                                                    # extras are optional at train time
        with open(os.path.join(w, "ids-a.npy"), "ab") as f:                                       # tampered extra ids
            f.write(b"\0\0")
        with self.assertRaises(SystemExit):
            prep.check_vocab(w, [ids_a])
        self.encode("a", "b")
        tj = os.path.join(w, "text-a.json")
        good = open(tj).read()
        open(tj, "w").write(good.replace('"vocab_sha256": "', '"vocab_sha256": "0'))              # encoded with another vocab
        with self.assertRaises(SystemExit):
            prep.check_vocab(w, [ids_a])
        os.remove(tj)                                                                              # no json
        with self.assertRaises(SystemExit):
            prep.check_vocab(w, [ids_a])
        open(tj, "w").write(good)
        with open(os.path.join(w, "vocab.txt"), "a", encoding="utf-8") as f:                       # vocab.txt changed after encode
            f.write("x\n")
        with self.assertRaises(SystemExit):
            prep.check_vocab(w, [])
        self.encode("a", "b")
        with open(os.path.join(w, "ids-wiki.npy"), "ab") as f:                                    # ids-wiki replaced
            f.write(b"\0\0")
        with self.assertRaises(SystemExit):
            prep.check_vocab(w, [])
        self.encode("a", "b")
        os.remove(os.path.join(w, "ids-vocab.json"))                                              # old work dir: fine alone, not with extras
        prep.check_vocab(w, [])
        with self.assertRaises(SystemExit):
            prep.check_vocab(w, [ids_a])


if __name__ == "__main__":
    unittest.main()
