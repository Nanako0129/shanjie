"""moore_lewis.py on synthetic sentences: normalised KN probabilities, the cross-entropy difference ordering, the 50% rule, determinism given the
seed, and the reserve bucket (first byte of SHA-256(message id) < 32)."""
import contextlib
import hashlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moore_lewis as M  # noqa: E402

CHAT = ["我們一起去吃飯吧", "你今天好嗎真的是這樣", "晚上要不要一起看電影", "我也想去吃飯啊", "你好嗎我們去看電影吧", "今天一起去吃飯好不好"] * 4
TECH = ["資料庫的連線逾時需要重新設定", "編譯器會最佳化迴圈的結構", "網路協定定義了封包的格式", "記憶體配置必須對齊位元組", "處理器快取影響程式效能", "演算法的時間複雜度很重要"] * 4
CANDIDATES = ["我們今天一起去看電影吧", "編譯器的記憶體配置很重要", "你好嗎晚上一起吃飯", "資料庫的封包格式需要設定", "我也想去看電影啊", "處理器的演算法效能"]


class KN(unittest.TestCase):
    def test_every_context_is_a_distribution(self):
        m = M.CharKN(CHAT, 4)
        vocab = set("".join(CHAT)) | {M.EOS, "龘"}                # the characters seen, EOS and one unseen character = V items
        self.assertEqual(len(vocab), m.V)
        for ctx in ("\x02\x02\x02", "\x02\x02我", "我們一", "們一起", "zzz", "晚上要"):
            self.assertAlmostEqual(sum(m.prob(ctx, w) for w in vocab), 1.0, places=9, msg=repr(ctx))

    def test_in_domain_text_is_cheaper_than_out_of_domain_text(self):
        m = M.CharKN(CHAT, 4)
        self.assertLess(m.entropy("我們一起去看電影吧"), m.entropy("記憶體配置必須對齊"))

    def test_entropy_counts_eos_and_is_per_character(self):
        m = M.CharKN(["甲乙"], 2)
        # bigram model on one sentence: P(甲|BOS) = P(乙|甲) = P(EOS|乙) are the same by construction; 3 predictions averaged
        self.assertAlmostEqual(m.entropy("甲乙"), -sum(__import__("math").log2(m.prob(c, w)) for c, w in (("\x02", "甲"), ("甲", "乙"), ("乙", M.EOS))) / 3)


class Select(unittest.TestCase):
    ROWS = {"src": [(f"{i}\t{s}\n", s) for i, s in enumerate(CANDIDATES)], "other": [(s + "\n", s) for s in TECH[:6]]}

    def test_best_half_per_source_in_file_order(self):
        kept, cut = M.select(self.ROWS, CHAT, seed=1)
        chat_like = {"我們今天一起去看電影吧", "你好嗎晚上一起吃飯", "我也想去看電影啊"}
        self.assertEqual({s for _, s in kept["src"]}, chat_like)                     # the 3 of 6 closest to the chat file
        self.assertEqual([l for l, _ in kept["src"]], [self.ROWS["src"][i][0] for i in (0, 2, 4)])      # lines unchanged, original order
        self.assertEqual(len(kept["other"]), 3)                                      # 50% of 6 in every source
        self.assertEqual(set(cut), {"src", "other"})

    def test_odd_counts_round_up_and_ties_keep_the_earlier_line(self):
        rows = {"s": [(f"{s}\n", s) for s in ["一起去吃飯", "一起去吃飯", "一起去吃飯"]]}
        kept, _ = M.select(rows, CHAT, seed=1)
        self.assertEqual(len(kept["s"]), 2)                                          # ceil(3 * 0.5)

    def test_same_seed_same_selection_and_the_sample_matches_the_in_domain_size(self):
        a, _ = M.select(self.ROWS, CHAT, seed=7)
        b, _ = M.select(self.ROWS, CHAT, seed=7)
        self.assertEqual(a, b)
        pool = [s for rows in self.ROWS.values() for _, s in rows]
        got = M.sample_chars(pool, 30, seed=7)
        self.assertGreaterEqual(sum(map(len, got)), 30)
        self.assertLess(sum(map(len, got)) - len(got[-1]), 30)                       # stops at the first sentence that reaches it
        self.assertEqual(got, M.sample_chars(pool, 30, seed=7))
        self.assertNotEqual(got, M.sample_chars(pool, 30, seed=8))
        self.assertEqual(M.sample_chars(pool, 10 ** 9, seed=1), pool)                # a pool not larger than the target is used whole


class CrossFit(unittest.TestCase):
    def test_no_sentence_is_scored_by_a_model_trained_on_it(self):
        pool = [s + c for s in TECH + CHAT + CANDIDATES for c in "甲乙丙丁"]
        trained = []
        real = M.CharKN

        class Spy(real):
            def __init__(self, sentences, order=4, discount=0.75):
                trained.append(set(sentences))
                super().__init__(sentences, order, discount)
                self.id = len(trained) - 1
        scored_by = {}
        real_entropy = Spy.entropy
        Spy.entropy = lambda self, s: scored_by.setdefault(s, set()).add(self.id) or real_entropy(self, s)
        M.CharKN = Spy
        try:
            ent = M.cross_fit(pool, 400, 3)
            for s in pool:
                ent(s)
        finally:
            M.CharKN = real
        self.assertEqual(len(trained), 2)
        self.assertTrue(all(len(ids) == 1 for ids in scored_by.values()))
        for s, ids in scored_by.items():
            self.assertNotIn(s, trained[next(iter(ids))], s)                          # the model that scored s did not train on s
        self.assertTrue(trained[0] and trained[1] and not trained[0] & trained[1])    # the halves are disjoint

    def test_a_sentence_score_does_not_depend_on_whether_it_was_sampled(self):
        pool = list(dict.fromkeys(s + c for s in TECH + CHAT + CANDIDATES for c in "甲乙丙丁戊己庚辛"))
        in_a = [s for s in pool if M.in_half_a(s)]
        extra = [s for s in (f"資料庫第{i}個封包" for i in range(400)) if M.in_half_a(s)][:40]     # more half-A sentences: half A's sample changes
        base, more = M.cross_fit(pool, 300, 5), M.cross_fit(pool + extra, 300, 5)
        self.assertTrue(len(in_a) > 20)
        for s in in_a:
            self.assertEqual(base(s), more(s), s)                                          # A sentences are scored by the model trained on half B only
        in_b = [s for s in pool if not M.in_half_a(s)]
        self.assertTrue(any(base(s) != more(s) for s in in_b))                             # the other half is scored by the model that did change


class Cli(unittest.TestCase):
    def test_score_prints_counts_and_hashes_only_and_is_reproducible(self):
        with tempfile.TemporaryDirectory() as d:
            ind = os.path.join(d, "in.txt")
            open(ind, "w", encoding="utf-8").write("".join(s + "\n" for s in CHAT))
            src = os.path.join(d, "s.tsv")
            open(src, "w", encoding="utf-8").write("".join(f"u{i}\t{s}\n" for i, s in enumerate(CANDIDATES)))
            outs = []
            for n in (1, 2):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    M.main(["score", "--in-domain", ind, "--source", f"s.tsv={src}", "--out", os.path.join(d, f"o{n}"), "--seed", "5"])
                outs.append((buf.getvalue(), open(os.path.join(d, f"o{n}", "s.tsv"), "rb").read()))
            self.assertEqual(outs[0], outs[1])
            text = outs[0][0]
            self.assertIn("kept\t3\t", text)
            self.assertIn(hashlib.sha256(outs[0][1]).hexdigest(), text)
            for s in CHAT + CANDIDATES:
                self.assertNotIn(s, text)
            self.assertEqual(outs[0][1].decode().count("\n"), 3)


class Jobs(unittest.TestCase):
    def test_jobs_3_is_byte_identical_to_jobs_1(self):
        pool = [f"{a}{b}{c}" for a in ("我們", "資料", "你好") for b in ("一起", "庫的", "嗎晚") for c in ("去吃飯", "封包", "看電影", "設定")] * 2   # duplicates: ties
        old, M.CHUNK = M.CHUNK, 5                                  # many chunks, so every worker gets some
        try:
            with tempfile.TemporaryDirectory() as d:
                ind = os.path.join(d, "in.txt")
                open(ind, "w", encoding="utf-8").write("".join(s + "\n" for s in CHAT))
                a, b = os.path.join(d, "a.tsv"), os.path.join(d, "b.txt")
                open(a, "w", encoding="utf-8").write("".join(f"u{i}\t{s}\n" for i, s in enumerate(pool)))
                open(b, "w", encoding="utf-8").write("".join(s + "\n" for s in pool[::2] + TECH))
                res = []
                for jobs in (1, 3):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        M.main(["score", "--in-domain", ind, "--source", f"a.tsv={a}", "--source", f"b.txt={b}", "--out", os.path.join(d, f"o{jobs}"),
                                "--seed", "5", "--jobs", str(jobs)])
                    res.append((buf.getvalue(), [open(os.path.join(d, f"o{jobs}", n), "rb").read() for n in ("a.tsv", "b.txt")]))
                self.assertEqual(res[0], res[1])
                self.assertIn("cut\t", res[0][0])
                self.assertTrue(0 < res[0][1][0].count(b"\n") < len(pool))
        finally:
            M.CHUNK = old


class Inset(unittest.TestCase):
    CONV = ({}, {"网": "網", "络": "路"}, 1)           # stand-in for the OpenCC tables, as in test_dedupe_eval
    MSGS = [("1", "我們一起去吃飯吧"), ("2", "今天天氣真的很好啊"), ("3", "前面說了\t上網路看影片喔"), ("4", "吃飯喔"), ("5", "完全不相干的一句話"), ("6", "太短了")]

    def run_inset(self, d, min_len, rows):
        msgs, out = os.path.join(d, "m.tsv"), os.path.join(d, "o.txt")
        open(msgs, "w", encoding="utf-8").write("".join(f"{i}\t{t}\n" for i, t in self.MSGS))
        args = ["inset", "--messages", msgs, "--min-len", str(min_len), "--out", out]
        for label, text in rows.items():
            p = os.path.join(d, label + ".txt")
            open(p, "w", encoding="utf-8").write(text)
            args += ["--exclude", f"{label}={p}"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            M.main(args, conv=self.CONV)
        return buf.getvalue(), open(out, "rb").read()

    def test_long_eval_sentences_drop_short_ones_do_not_order_kept_nothing_leaks(self):
        rows = {"disc": "前文|我們一起去吃飯|ㄅ\n|吃飯|ㄅ\n|上網路看影片|ㄅ\n", "res": "|今天天氣真的很好|ㄅ\n|太短了|ㄅ\n"}
        with tempfile.TemporaryDirectory() as d:
            text, out = self.run_inset(d, 6, rows)
            self.assertEqual(out.decode(), "吃飯喔\n完全不相干的一句話\n太短了\n")       # 1 (long hit), 2, 3 (hit across a tab) dropped; 4: "吃飯" is 2 characters
            self.assertEqual(text, f"messages\tin\t6\ndropped\tdisc\t2\ndropped\tres\t1\nkept\t3\tdropped\t3\tsha256\t{hashlib.sha256(out).hexdigest()}\n")
            for _, t in self.MSGS:
                self.assertNotIn(t, text)
            for s in ("我們一起去吃飯", "今天天氣真的很好", "吃飯"):
                self.assertNotIn(s, text)
            text2, out2 = self.run_inset(d, 2, {"disc": rows["disc"]})        # MIN_LEN of holdout_guard (4) still floors it: "吃飯" stays no pattern
            self.assertEqual(out2.decode(), "今天天氣真的很好啊\n吃飯喔\n完全不相干的一句話\n太短了\n")


class Reserve(unittest.TestCase):
    def test_first_byte_below_32(self):
        for mid in ("1", "123456789012345678", "abc", "x" * 30):
            self.assertEqual(M.in_reserve(mid), hashlib.sha256(mid.encode()).digest()[0] < 32)
        self.assertEqual(M.RESERVE_BELOW, 32)
        ids = [str(10 ** 17 + 7919 * i) for i in range(8000)]
        share = sum(M.in_reserve(i) for i in ids) / len(ids)
        self.assertTrue(0.11 < share < 0.14, share)                                  # about 1/8

    def test_split_writes_two_disjoint_files(self):
        with tempfile.TemporaryDirectory() as d:
            msgs = os.path.join(d, "m.tsv")
            ids = [str(10 ** 17 + 7919 * i) for i in range(400)]
            open(msgs, "w", encoding="utf-8").write("".join(f"{i}\t訊息{i}\n" for i in ids))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                M.main(["split", "--messages", msgs, "--reserve", os.path.join(d, "r"), "--rest", os.path.join(d, "x")])
            r = open(os.path.join(d, "r"), encoding="utf-8").read().splitlines()
            x = open(os.path.join(d, "x"), encoding="utf-8").read().splitlines()
            self.assertEqual(len(r) + len(x), 400)
            self.assertTrue(all(M.in_reserve(l.split("\t")[0]) for l in r) and not any(M.in_reserve(l.split("\t")[0]) for l in x))
            self.assertEqual(buf.getvalue().strip(), f"reserve\t{len(r)}\trest\t{len(x)}")


if __name__ == "__main__":
    unittest.main()
