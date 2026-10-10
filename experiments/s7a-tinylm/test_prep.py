"""S7a unit tests, numpy only: training-text equivalence with build_counts, vocabulary / ids, batching, construction check.
Needs the OpenCC files in ~/.cache/shanjie/sources/opencc (the same ones build_counts needs; hash-checked by build_counts)."""
import json
import os
import tempfile
import unittest

import numpy as np

import batching
import buildcheck
import prep
import tinylm
from tinylm import BOS, UNK

import build_counts as bc
import build_counts_text as bct
import build_tune

WIKI_SNIPPET = ("'''台湾'''是{{lang|en|Taiwan}}位于东亚的岛屿&lt;ref&gt;来源&lt;/ref&gt;。[[中华民国|中華民國]]政府，位在台北！<!--注-->"
                "这里很好玩；a{{x{{y}}}}后面的人们说：「我们去吃饭吧」，好吗？\n== 歷史 ==\n* 他說&amp;她說\n")
ORIG_SEGMENT = bc.segment
COLLOQUIAL = ["我们明天去哪里吃饭，好吗", "今天天氣真好", "ok", "a"]


class TestPrepEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bc._init(False, False)          # lexicon + conversion tables, exactly as count_batch runs
        cls.conv = bc._W["conv"]

    def record(self, fn):
        """Runs handed to bc.segment by `fn` (count_batch / build_counts_text call it once per run of >= 2 Han characters)."""
        seen = []
        bc.segment = lambda lex, text: (seen.append(text), ORIG_SEGMENT(lex, text))[1]
        try:
            fn()
        finally:
            bc.segment = ORIG_SEGMENT
        return seen

    def test_wiki_runs_equal_count_batch_runs(self):
        theirs = self.record(lambda: bc.count_batch([WIKI_SNIPPET]))
        ours = list(prep.wiki_runs(WIKI_SNIPPET, self.conv))
        self.assertGreater(len(ours), 4)
        self.assertEqual(ours, theirs)
        self.assertTrue(any("台灣" in r for r in ours) and all(len(r) >= 2 for r in ours))     # converted, not raw

    def test_colloquial_runs_equal_build_counts_text_runs(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "c.txt")
            with open(src, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(COLLOQUIAL) + "\n")
            theirs = self.record(lambda: bct.main([os.path.join(td, "o.pkl"), src]))
            ours = [r for line in bct.lines(src) for r in prep.text_runs(line, self.conv)]
        self.assertEqual(ours, theirs)
        self.assertIn("我們明天去哪裡吃飯", ours)

    def test_input_hash_check_stops_on_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "synth.txt")
            open(p, "w").close()
            with self.assertRaises(SystemExit):
                prep.check_inputs({"synth.txt": p})
            with self.assertRaises(SystemExit):
                prep.check_inputs({"synth.txt": os.path.join(td, "missing")})
        self.assertTrue(prep.match_hash("synth.txt", "bec7a6a2" + "0" * 52 + "99f1"))
        self.assertFalse(prep.match_hash("synth.txt", "bec7a6a3" + "0" * 52 + "99f1"))


class TestVocabAndIds(unittest.TestCase):
    def test_make_vocab_threshold_and_order(self):
        cc = {"甲": 9, "乙": 5, "丙": 4, "丁": 9}
        self.assertEqual(prep.make_vocab(cc, 5), ["丁", "甲", "乙"] if ord("丁") < ord("甲") else ["甲", "丁", "乙"])
        self.assertNotIn("丙", prep.make_vocab(cc, 5))          # 4 < 5 -> UNK

    def test_encode_text_bos_and_unk(self):
        lut = prep.make_lut(["你", "好"])
        ids = prep.encode_text("你好\n好丙\n𠮷你", lut)
        self.assertEqual(ids.tolist(), [BOS, 3, 4, BOS, 4, UNK, BOS, UNK, 3])      # 丙 not in table, 𠮷 is above the BMP
        self.assertEqual(ids.dtype, np.uint16)

    def test_encode_roundtrip_through_files(self):
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "runs-wiki.txt"), "w", encoding="utf-8", newline="\n").write("你好\n好你好\n")
            open(os.path.join(td, "runs-coll.txt"), "w", encoding="utf-8", newline="\n").write("你丙\n")
            json.dump({"charcount": {"你": 5, "好": 5, "丙": 1}}, open(os.path.join(td, "charcount.json"), "w", encoding="utf-8"))
            prep.cmd_encode(type("A", (), {"work": td, "min_count": 5})())
            self.assertEqual(np.load(os.path.join(td, "ids-wiki.npy")).tolist(), [BOS, 3, 4, BOS, 4, 3, 4])
            self.assertEqual(np.load(os.path.join(td, "ids-coll.npy")).tolist(), [BOS, 3, UNK])
            self.assertEqual(prep.load_vocab(td), ["你", "好"])


class TestBatching(unittest.TestCase):
    def test_pieces_cover_every_target_once(self):
        run = list(range(3, 3 + 130))                     # BOS + 130 characters = 131 tokens > 65
        ids = np.array([BOS] + run + [BOS, 5, 6], dtype=np.uint16)
        st, ln = batching.pieces(ids)
        o = np.argsort(st); st, ln = st[o], ln[o]
        self.assertEqual(len(st), 4)                      # 131 tokens -> 65 + 65 + 3 ; plus the short run
        self.assertTrue((ln <= batching.PIECE).all())
        targets = np.concatenate([ids[s + 1:s + l] for s, l in zip(st, ln)])
        expect = np.concatenate([ids[1:1 + 130], [5, 6]])
        self.assertEqual(sorted(targets.tolist()), sorted(expect.tolist()))      # every character is a target exactly once
        self.assertEqual(ids[st[0]], BOS)
        self.assertNotEqual(ids[st[1]], BOS)              # only the first piece of a run starts with BOS

    def test_batches_one_length_each_and_cover_everything(self):
        rng = np.random.default_rng(0)
        lengths = rng.integers(3, 20, size=500)
        b = batching.make_batches(lengths, 64, np.random.default_rng(1))
        self.assertEqual(sorted(np.concatenate(b).tolist()), list(range(500)))
        for x in b:
            self.assertEqual(len(set(lengths[x].tolist())), 1)
            self.assertLessEqual(len(x) * int(lengths[x[0]]), 64 + int(lengths[x[0]]))
        again = batching.make_batches(lengths, 64, np.random.default_rng(1))
        self.assertTrue(all((x == y).all() for x, y in zip(b, again)))        # fixed seed -> same epoch


class TestConstructionCheck(unittest.TestCase):
    @staticmethod
    def toy_case():
        """A raw line whose is_tune(raw) is false while is_tune(converted) is true (conversion changes the characters, hence the hash)."""
        conv = bc.load_conv()
        for i in range(2000):
            raw = f"后面{i}号我们说"
            if not build_tune.is_tune(raw) and build_tune.is_tune(bc.convert(raw, *conv)):
                return raw, conv
        raise AssertionError("no toy line found")

    def test_check_uses_the_raw_line(self):
        raw, conv = self.toy_case()
        raws = [raw] + [f"line {i}" for i in range(60)]
        # build_tune.py's procedure: converted lines of the is_tune(raw)-false sentences go to colloquial-train.txt
        train = [bc.convert(r, *conv) for r in raws if not build_tune.is_tune(r)]
        self.assertIn(bc.convert(raw, *conv), train)
        rep = buildcheck.construction_check(raws, len(train))
        self.assertTrue(rep["ok"], rep)
        self.assertEqual(rep["is_tune_true"] + rep["is_tune_false"], rep["raw_total"])
        # a converted-line check would have counted this line as tune and disagreed with the file
        wrong = sum(not build_tune.is_tune(bc.convert(r, *conv)) for r in raws)
        self.assertNotEqual(wrong, len(train))

    def test_check_fails_on_a_row_count_mismatch(self):
        raws = [f"line {i}" for i in range(50)]
        n_false = sum(not build_tune.is_tune(r) for r in raws)
        self.assertFalse(buildcheck.construction_check(raws, n_false + 1)["ok"])
        self.assertFalse(buildcheck.construction_check(raws, n_false - 1)["ok"])
        self.assertTrue(buildcheck.construction_check(raws, n_false)["ok"])
        self.assertIn("NOT CHECKED", buildcheck.construction_check(raws, n_false)["wiki_articles_used"])    # not recorded: said, not claimed

    def test_check_fails_when_prep_used_too_many_articles(self):
        raws = [f"line {i}" for i in range(50)]
        n_false = sum(not build_tune.is_tune(r) for r in raws)
        self.assertTrue(buildcheck.construction_check(raws, n_false, articles=buildcheck.WIKI_TUNE_FIRST_INDEX)["ok"])
        rep = buildcheck.construction_check(raws, n_false, articles=buildcheck.WIKI_TUNE_FIRST_INDEX + 1)
        self.assertFalse(rep["ok"])
        self.assertIn("wikitune starts", rep["reasons"][0])

    def test_substring_ratio(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "r.txt")
            open(p, "w", encoding="utf-8", newline="\n").write("今天天氣真好\n我們去吃飯\n")
            self.assertEqual(buildcheck.substring_ratio(["天氣真", "吃飯吧", "今天", "好我"], [p]), (2, 4))      # the last one spans a line break
            self.assertEqual(buildcheck.substring_ratio(["天氣真", "吃飯吧", "今天", "好我"], [p], procs=2), (2, 4))


if __name__ == "__main__":
    unittest.main()
