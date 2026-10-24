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
