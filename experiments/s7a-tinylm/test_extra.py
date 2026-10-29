"""SJ-SR-7 additions (contract sj-lm-7 section 2.7): weighted --extra PATH[:WEIGHT], the vocabulary counted over the extra runs files (once each),
prep.py text --half; the unchanged parts are pinned by hash (encode without extras) and by the S7a comparison below.
S7f changes to S7a, numpy only (no torch, no OpenCC files): train.py's epoch and batch plan (batching.build_epoch / plan) without
--extra is exactly S7a's; with --extra every piece of the extra ids is in the epoch once; prep.py text encodes with the existing vocab.
Run: python3 -W ignore -m unittest experiments/s7a-tinylm/test_extra.py -v   (from the repo root)
"""
import collections
import contextlib
import hashlib
import io
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
        # 3 batches of 2,000; the simplified 这/话 are there on purpose: they must come out as 這/話, so the workers really converted.
        # Every 7th line has a run the holdout guard (a real Matcher, passed to the workers through the Pool initializer) must skip.
        lines = [f"{i}\t这是第{i}句话，今天天氣真好我們去散步吧" if i % 7 == 0 else f"{i}\t这是第{i}句话，我們去吃飯好嗎" if i % 3 else f"{i}\tok 天氣真好"
                 for i in range(4500)]
        conv = prep.bc.load_conv()
        with tempfile.TemporaryDirectory() as work:
            self.write(work, "in.tsv", "\n".join(lines) + "\n")
            self.write(work, "rows.txt", "|今天天氣真好我們去散步吧|ㄐㄧㄣ\n")
            guard = prep.bc.guard_matcher([os.path.join(work, "rows.txt")])
            for half in (False, True):
                for g in (None, guard):
                    reps = [prep.write_runs(os.path.join(work, "in.tsv"), work, f"p{p}", conv, guard=g, half=half, procs=p) for p in (1, 2)]
                    self.assertEqual(open(os.path.join(work, "runs-p1.txt"), "rb").read(), open(os.path.join(work, "runs-p2.txt"), "rb").read())
                    self.assertEqual(reps[0], reps[1])
                    self.assertEqual(reps[0]["lines"], 4500)
                    out = open(os.path.join(work, "runs-p2.txt"), encoding="utf-8").read()
                    self.assertIn("這是第", out)
                    self.assertNotIn("这", out)
                    self.assertEqual("今天天氣真好我們去散步吧" in out, g is None)
                    if g is not None:
                        self.assertGreater(reps[0]["holdout_guard_skipped_runs"], 0)

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


class TestRawMode(unittest.TestCase):
    """CTX64: `text --raw`, `runs --raw`, the vocabulary over every character, and BOS at every piece."""

    class Guard:
        """Stands in for holdout_guard's Matcher: .any(folded run) is true for a run containing a guarded sentence."""
        def any(self, run):
            return "天氣真好" in run

    def write(self, work, name, text):
        with open(os.path.join(work, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def runs(self, work, text, **kw):
        self.write(work, "in.txt", text)
        with mock.patch.object(prep.bc, "convert", lambda t, *conv, **k: t):
            rep = prep.write_runs(os.path.join(work, "in.txt"), work, "g", (), **kw)
        return open(os.path.join(work, "runs-g.txt"), encoding="utf-8").read(), rep

    def test_raw_keeps_punctuation_ascii_digits_and_strips_the_edges(self):
        with tempfile.TemporaryDirectory() as work:
            out, rep = self.runs(work, "  我們去吃飯，OK 123！ \n\n   \nfull ＡＢ，好嗎？\n", raw=True)
            self.assertEqual(out, "我們去吃飯，OK 123！\nfull ＡＢ，好嗎？\n")      # empty lines dropped, no Han split
            self.assertEqual((rep["lines"], rep["raw"]), (4, True))

    def test_default_mode_is_unchanged_on_the_same_fixture(self):
        with tempfile.TemporaryDirectory() as work:
            out, rep = self.runs(work, "  我們去吃飯，OK 123！ \n\n好\nfull ＡＢ，好嗎？\n")
            self.assertEqual(out, "我們去吃飯\n好嗎\n")                          # Han runs of length >= 2 only, as before
            self.assertNotIn("raw", rep)
            out2, rep2 = self.runs(work, "  我們去吃飯，OK 123！ \n\n好\nfull ＡＢ，好嗎？\n", raw=False)
            self.assertEqual((out2, rep2), (out, rep))

    def test_raw_guard_drops_the_whole_line_when_any_han_run_is_guarded(self):
        with tempfile.TemporaryDirectory() as work:
            text = "你好嗎，今天天氣真好，我們去吃飯\n你好嗎，我們去吃飯\n"
            out, rep = self.runs(work, text, raw=True, guard=self.Guard())
            self.assertEqual(out, "你好嗎，我們去吃飯\n")
            self.assertEqual(rep["holdout_guard_skipped_runs"], 1)
            out, _ = self.runs(work, text, guard=self.Guard())                      # the default path drops only the run
            self.assertEqual(out, "你好嗎\n我們去吃飯\n你好嗎\n我們去吃飯\n")

    def test_raw_keeps_only_lines_with_a_han_run_of_two_or_more(self):
        with tempfile.TemporaryDirectory() as work:
            out, rep = self.runs(work, "ok 123!\n天，氣\n好 ok\n我們，ok\n  \n", raw=True)
            self.assertEqual(out, "我們，ok\n")                                   # ASCII debris and one-character fragments are out
            self.assertEqual((rep["lines"], rep["raw_no_han_lines"]), (5, 3))        # the blank line is neither kept nor counted
        raw = "ASCII 12。天，氣。第二句天氣真好！"
        with mock.patch.object(prep.bc, "convert", lambda t, *conv, **k: t):
            self.assertEqual(list(prep.raw_wiki_lines(raw, ())), [(None, prep.NO_HAN), (None, prep.NO_HAN), ("第二句天氣真好", None)])

    def test_runs_raw_applies_to_wiki_and_colloquial_items_with_the_same_filters(self):
        # `runs --raw` goes through _work: kind "wiki" = an article, kind "text" = one sentence as bct.lines yields it (synthetic fixture only)
        art = "第一句，有逗號and 12。ok。第二句天氣真好！"
        sents = ["我們去吃飯，OK！", "ok 123", "天氣真好，哈哈", "  "]
        with mock.patch.object(prep.bc, "convert", lambda t, *conv, **k: t):
            try:
                prep._init(self.Guard())
                w, c = prep._work(("wiki", [art], True)), prep._work(("text", sents, True))
                prep._init(None)
                dw, dc = prep._work(("wiki", [art])), prep._work(("text", sents))
            finally:
                prep._init()
        self.assertEqual((w[0], w[2], w[3], w[4]), ("第一句，有逗號and 12", 1, 1, 1))        # kept / guarded / no Han run (ok)
        self.assertEqual((c[0], c[2], c[3], c[4]), ("我們去吃飯，OK！", 1, 1, 1))             # blank: counted nowhere
        self.assertEqual(dw[0], "第一句\n有逗號\n第二句天氣真好")                           # default: Han runs, unchanged
        self.assertEqual((dc[0], dc[3], dc[4]), ("我們去吃飯\n天氣真好\n哈哈", 0, 0))

    def test_raw_wiki_drops_namespace_prefixes_after_the_guard(self):
        class G:
            def any(self, s):
                return "秘密句子" in s
        raw = "Category:臺灣城市。File:地圖圖片。分類:台灣。秘密句子|表格。正常的一句。"
        with mock.patch.object(prep.bc, "convert", lambda t, *conv, **k: t):
            got = list(prep.raw_wiki_lines(raw, (), G()))
        self.assertEqual(got, [(None, prep.MARKUP_JUNK)] * 3 + [(None, prep.GUARDED), ("正常的一句", None)])   # guarded counts as guard, not markup

    def test_raw_wiki_drops_markup_residue_and_list_marks(self):
        raw = "* 項目一二三。:: 縮排的句子。有 == 標題 ==。表格|欄位。留下[[連結。結束}}了。{{模板。一般的句子。"
        with mock.patch.object(prep.bc, "convert", lambda t, *conv, **k: t):
            got = list(prep.raw_wiki_lines(raw, ()))
            self.assertEqual(got, [("項目一二三", None), ("縮排的句子", None)] + [(None, prep.MARKUP_JUNK)] * 5 + [("一般的句子", None)])
            try:
                prep._init()
                w = prep._work(("wiki", [raw], True))
                d = prep._work(("wiki", [raw]))
            finally:
                prep._init()
        self.assertEqual((w[2], w[5]), (3, 5))                                         # kept lines, markup lines
        self.assertEqual(w[0].split("\n"), ["項目一二三", "縮排的句子", "一般的句子"])
        self.assertEqual(d[5], 0)                                                       # the default path is untouched
        self.assertIn("標題", d[0].split("\n"))                                           # default: Han runs of the same sentences

    def test_text_raw_needs_runs_only(self):
        with tempfile.TemporaryDirectory() as work:
            self.write(work, "in.txt", "我們去吃飯，OK\n")
            args = ["text", "--work", work, "--in", os.path.join(work, "in.txt"), "--name", "g"]
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
                prep.main(args + ["--raw"])
            self.assertIn("--runs-only", str(cm.exception))
            self.assertFalse(os.path.exists(os.path.join(work, "runs-g.txt")))

    def test_raw_half_skips_blank_lines_before_the_half_judgement(self):
        with tempfile.TemporaryDirectory() as work:
            _, rep = self.runs(work, "\n   \n\n", raw=True, half=True)
            self.assertEqual((rep["lines"], rep["half_dropped_runs"]), (3, 0))

    def test_raw_half_and_tsv(self):
        lines = ["天氣好嗎", "吃飯了沒，OK", "好嗎，你好", "我們去吃飯，好嗎"]           # the line is judged whole, as in the default path
        keep = [x for x in lines if prep.half_keep(x)]
        self.assertTrue(keep and len(keep) < len(lines))
        with tempfile.TemporaryDirectory() as work:
            out, rep = self.runs(work, "\n".join(lines) + "\n", raw=True, half=True)
            self.assertEqual((out, rep["half_dropped_runs"]), ("".join(x + "\n" for x in keep), len(lines) - len(keep)))
            self.write(work, "in.tsv", "a1\t吃飯了沒，OK\n")
            with mock.patch.object(prep.bc, "convert", lambda t, *conv, **k: t):
                prep.write_runs(os.path.join(work, "in.tsv"), work, "t", (), raw=True)
            self.assertEqual(open(os.path.join(work, "runs-t.txt"), encoding="utf-8").read(), "吃飯了沒，OK\n")   # unit column dropped

    def test_vocabulary_counts_every_character_of_a_raw_file(self):
        with tempfile.TemporaryDirectory() as work:
            self.write(work, "runs-wiki.txt", "我我我我我\n")
            self.write(work, "runs-coll.txt", "你你你你你\n")
            json.dump({"charcount": {"我": 5, "你": 5}}, open(os.path.join(work, "charcount.json"), "w"))
            self.write(work, "runs-r.txt", "好，好，好，好，好，a1\nA😀😀😀😀😀\n")
            prep.cmd_encode(type("A", (), {"work": work, "min_count": 5, "extra": ["r"], "force": False})())
            self.assertEqual(prep.load_vocab(work), ["你", "好", "我", "，"])       # 5 x ， and 好 (ties by code point); a, 1, A: too rare; 😀 (> U+FFFF) never
            self.assertEqual(np.load(os.path.join(work, "ids-r.npy")).tolist(),
                             [BOS, 4, 6, 4, 6, 4, 6, 4, 6, 4, 6, UNK, UNK, BOS, UNK, UNK, UNK, UNK, UNK, UNK])

    def test_raw_wiki_lines_are_sentences_with_their_punctuation(self):
        raw = "第一句，有逗號、and ASCII 12。第二句天氣真好！\n\n第三句？"
        with mock.patch.object(prep.bc, "convert", lambda t, *conv, **k: t):
            self.assertEqual(list(prep.raw_wiki_lines(raw, ())), [("第一句，有逗號、and ASCII 12", None), ("第二句天氣真好", None), ("第三句", None)])
            self.assertEqual(list(prep.raw_wiki_lines(raw, (), self.Guard()))[1], (None, prep.GUARDED))
            self.assertEqual([r for r in prep.wiki_runs(raw, ())], ["第一句", "有逗號", "第二句天氣真好", "第三句"])    # the default: Han runs

    def test_rebos_puts_bos_at_every_piece_and_covers_every_target_once(self):
        run = list(range(3, 3 + 130))
        ids = np.array([BOS] + run + [BOS, 5, 6], dtype=np.uint16)
        new = batching.rebos(ids)
        st, ln = batching.pieces(new)
        self.assertTrue((new[st] == BOS).all())
        self.assertTrue((ln <= batching.PIECE).all())
        self.assertEqual(len(st), 4)                                                  # 64 + 64 + 2 characters, plus the short run
        targets = np.concatenate([new[s + 1:s + l] for s, l in zip(np.sort(st), ln[np.argsort(st)])])
        self.assertEqual(sorted(targets.tolist()), sorted(run + [5, 6]))
        old_st, _ = batching.pieces(ids)
        self.assertNotEqual(int(ids[np.sort(old_st)[1]]), BOS)                        # the default still has none there

    def test_rebos_equals_the_loop_it_replaced_on_random_data(self):
        def loop(ids):                                   # the first version, one Python step per 64 characters
            st = np.flatnonzero(ids == BOS)
            ln = np.diff(np.append(st, len(ids)))
            parts, at = [], 0
            for s, l in zip(st[ln > batching.PIECE].tolist(), ln[ln > batching.PIECE].tolist()):
                parts.append(ids[at:s])
                chars = ids[s + 1:s + l]
                for i in range(0, len(chars), batching.PIECE - 1):
                    parts += [ids[s:s + 1], chars[i:i + batching.PIECE - 1]]
                at = s + l
            parts.append(ids[at:])
            return np.concatenate(parts)
        rng = np.random.default_rng(11)
        for _ in range(20):
            parts = []
            for _ in range(60):
                n = int(rng.choice([1, 2, 63, 64, 65, 66, 127, 128, 129, 130, 193, 500, 1000]))
                parts += [BOS] + list(rng.integers(3, 50, n))
            ids = np.array(parts, dtype=np.uint16)
            got = batching.rebos(ids)
            self.assertEqual((got.dtype, got.tobytes()), (ids.dtype, loop(ids).tobytes()))

    def test_rebos_without_a_long_run_and_the_default_plan_are_byte_identical(self):
        short = np.array([BOS, 3, 4, BOS] + [3] * 64, dtype=np.uint16)                # 65 tokens: exactly one piece
        self.assertEqual(batching.rebos(short).tobytes(), short.tobytes())
        rng = np.random.default_rng(5)
        with tempfile.TemporaryDirectory() as work:
            for n in ("wiki", "coll"):
                np.save(os.path.join(work, f"ids-{n}.npy"), fake_ids(rng, 40))
            off = batching.plan(work, [], 512, 1)
            self.assertEqual([x.tobytes() for x in off[:3]], [x.tobytes() for x in batching.plan(work, [], 512, 1, 0, False)[:3]])
            on = batching.plan(work, [], 512, 1, 0, True)
            self.assertTrue((on[0][on[1]] == BOS).all())
            self.assertFalse((off[0][off[1]] == BOS).all())


class TestBaseVocab(unittest.TestCase):
    COUNT = {"我": 5, "好": 5, "你": 6, "\x00": 5, "稀": 1}

    def encode(self, work, base):
        for n, s in (("wiki", "我我我我我好好好好好\n"), ("coll", "你你你你你你\x00\x00\x00\x00\x00稀\n")):
            open(os.path.join(work, f"runs-{n}.txt"), "w", encoding="utf-8", newline="\n").write(s)
        json.dump({"charcount": self.COUNT}, open(os.path.join(work, "charcount.json"), "w"))
        prep.cmd_encode(type("A", (), {"work": work, "min_count": 5, "extra": [], "force": False, "base_vocab": base})())

    def test_merge_keeps_base_order_and_appends_in_normal_order(self):
        self.assertEqual(prep.merge_vocab(["我", "好", "你"], ["稀", "好"]), (["稀", "好", "我", "你"], 2))
        with self.assertRaises(ValueError):
            prep.merge_vocab(["我"], ["好", "好"])

    def test_encode_base_vocab_records_and_default_is_unchanged(self):
        with tempfile.TemporaryDirectory() as work, tempfile.TemporaryDirectory() as w2:
            base = os.path.join(work, "base.txt")
            open(base, "w", encoding="utf-8", newline="\n").write("稀\n好\n")
            self.encode(work, base)
            normal = prep.make_vocab(collections.Counter(self.COUNT), 5)          # 你 (6) first, then ties by code point
            self.assertEqual(normal, ["你", "\x00", "好", "我"])
            self.assertEqual(prep.load_vocab(work), ["稀", "好", "你", "\x00", "我"])    # 稀 stays though rare
            rec = json.load(open(os.path.join(work, "ids-vocab.json")))["base_vocab"]
            self.assertEqual((rec["size"], rec["appended"], rec["sha256"]), (2, 3, prep.sha256_file(base)))
            self.encode(w2, None)
            self.assertNotIn("base_vocab", json.load(open(os.path.join(w2, "ids-vocab.json"))))
            self.assertEqual(prep.load_vocab(w2), normal)

    def test_extend_emb_copies_prefix_and_fills_new_rows(self):
        emb = np.random.default_rng(1).normal(size=(7, 4)).astype(np.float32)
        out = prep.extend_emb(emb, 3, 5)
        self.assertEqual(out.shape, (10, 4))
        np.testing.assert_array_equal(out[:7], emb)
        self.assertTrue((np.abs(out[7:] - emb[3:].mean(axis=0)) < 0.2 * emb.std()).all())
        self.assertFalse((out[7] == out[8]).all())
        np.testing.assert_array_equal(out, prep.extend_emb(emb, 3, 5))      # same seed, same rows

    def test_non_prefix_vocab_is_refused(self):
        prep.check_prefix(["a", "b"], ["a", "b", "c"])
        for old in (["b", "a"], ["a", "c"], ["a", "b", "c", "d"]):
            with self.assertRaises(ValueError):
                prep.check_prefix(old, ["a", "b", "c"])


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
