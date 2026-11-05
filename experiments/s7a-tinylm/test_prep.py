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

    def test_holdout_guard_skips_matching_runs_and_off_is_unchanged(self):
        """--holdout-guard (docs/contracts/holdout-refresh.md section 8): off, _work is what it was; on, runs holding a held-out sentence are dropped and counted."""
        import holdout_guard
        with tempfile.TemporaryDirectory() as td:
            rows = os.path.join(td, "rows.txt")
            open(rows, "w", encoding="utf-8").write("a|今天天氣真好|x\n")
            matcher = holdout_guard.build_matcher([rows], self.conv)[0]
            lines = ["今天天氣真好", "我们明天去哪里吃饭"]
            prep._init()
            off = prep._work(("text", lines))
            self.assertEqual(off[0].split("\n"), [r for l in lines for r in prep.text_runs(l, self.conv)])
            self.assertEqual(off[3], 0)
            prep._init(matcher)
            on = prep._work(("text", lines))
            prep._init()
        self.assertEqual((on[2], on[3]), (off[2] - 1, 1))
        self.assertNotIn("今天天氣真好", on[0])

    def test_colloquial_runs_equal_build_counts_text_runs(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "c.txt")
            with open(src, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(COLLOQUIAL) + "\n")
            theirs = self.record(lambda: bct.main([os.path.join(td, "o.pkl"), src]))
            ours = [r for line in bct.lines(src) for r in prep.text_runs(line, self.conv)]
        self.assertEqual(ours, theirs)
        self.assertIn("我們明天去哪裡吃飯", ours)

    def test_no_trad_tw_phrases_keeps_traditional_wording(self):
        """docs/contracts/sr-no-twphrases.md section 2.1: with tw=False a Traditional sentence keeps 權限 (TWPhrases has 權限 -> 許可權);
        a Simplified one is converted as before; tw=True (the default) is the old output."""
        trad, simp = "管理員權限加好了", "我们的插件"
        self.assertEqual(list(prep.text_runs(trad, self.conv)), [r for r in bc.text_runs(trad, self.conv) if len(r) >= 2])
        self.assertNotIn("管理員權限加好了", list(prep.text_runs(trad, self.conv)))           # the default still rewrites it
        self.assertEqual(list(prep.text_runs(trad, self.conv, False)), ["管理員權限加好了"])
        self.assertEqual(list(prep.text_runs(simp, self.conv, False)), list(prep.text_runs(simp, self.conv)))
        art = trad + "。"
        self.assertEqual(list(prep.wiki_runs(art, self.conv, False)), ["管理員權限加好了"])
        self.assertEqual(list(prep.wiki_runs(art, self.conv)), [r for r in bc.article_runs(art, self.conv) if len(r) >= 2])
        prep._init(None, False)
        try:
            self.assertEqual(prep._work(("text", [trad]))[0], "管理員權限加好了")
            self.assertEqual(prep._text_work(([trad], False, False))[0], "管理員權限加好了\n")
        finally:
            prep._init()
        self.assertEqual(prep._work(("text", [trad]))[0], "\n".join(list(prep.text_runs(trad, self.conv))))

    def test_simp_tw_keys_and_changed_flags(self):
        """SR-TW-V: --simp-tw-keys restricts TWPhrases on Simplified sentences; --changed-flags marks the runs that differ from the default
        conversion (A) in the one written (B), one byte per kept run; off (the default) nothing changes."""
        trad, simp, same = "管理員權限加好了", "管理员权限加好了", "今天天氣真好"
        lines = [trad, same, simp]
        listed, other = frozenset({"權限"}), frozenset({"軟件"})
        # B = --no-trad-tw-phrases: the Traditional 權限 run differs from A (許可權); the plain run and (keys unset) the Simplified run do not
        self.assertEqual(list(prep.text_runs_flagged(trad, self.conv, False)), [("管理員權限加好了", True)])
        self.assertEqual(list(prep.text_runs_flagged(same, self.conv, False)), [(same, False)])
        self.assertEqual(list(prep.text_runs_flagged(simp, self.conv, False)), [("管理員許可權加好了", False)])
        # B = --simp-tw-keys: the Simplified run stays 權限 unless 權限 is listed; Traditional runs follow tw
        self.assertEqual(list(prep.text_runs(simp, self.conv, True, other)), ["管理員權限加好了"])
        self.assertEqual(list(prep.text_runs(simp, self.conv, True, listed)), ["管理員許可權加好了"])
        self.assertEqual(list(prep.text_runs_flagged(simp, self.conv, True, other)), [("管理員權限加好了", True)])
        self.assertEqual(list(prep.text_runs_flagged(simp, self.conv, True, listed)), [("管理員許可權加好了", False)])
        # the flagged wiki path yields the same runs as wiki_runs
        art = "管理员权限加好了。今天天氣真好！" + trad + "。"
        for tw, keys in ((True, None), (False, None), (True, other), (False, other)):
            self.assertEqual([r for r, _ in prep.wiki_runs_flagged(art, self.conv, tw, keys)], list(prep.wiki_runs(art, self.conv, tw, keys)))
        self.assertEqual([c for _, c in prep.wiki_runs_flagged(art, self.conv, False, other)], [True, False, True])
        # the workers: flags are bytes aligned with the runs, 4-tuple without the option
        prep._init(None, False, other, True)
        try:
            text, _, n, _, fl = prep._work(("text", lines))
            self.assertEqual((text.split("\n"), n, fl), (["管理員權限加好了", same, "管理員權限加好了"], 3, b"\x01\x00\x01"))
            out = prep._text_work((lines, False, False))
            self.assertEqual((out[0], out[4]), ("管理員權限加好了\n" + same + "\n管理員權限加好了\n", b"\x01\x00\x01"))
        finally:
            prep._init()
        self.assertEqual(len(prep._work(("text", lines))), 4)
        self.assertEqual(len(prep._text_work((lines, False, False))), 4)
        # the guard drops a run from both the text and the flags
        import holdout_guard
        with tempfile.TemporaryDirectory() as td:
            rows = os.path.join(td, "rows.txt")
            open(rows, "w", encoding="utf-8").write("a|" + same + "|x\n")
            prep._init(holdout_guard.build_matcher([rows], self.conv, False)[0], False, None, True)
            try:
                text, _, n, skipped, fl = prep._work(("text", lines))
            finally:
                prep._init()
        self.assertEqual((n, skipped, fl), (2, 1, b"\x01\x00"))

    def test_write_runs_changed_flags_file_and_report(self):
        lines = ["管理員權限加好了", "今天天氣真好", "管理员权限加好了"]
        keys_file = None
        with tempfile.TemporaryDirectory() as td:
            src, keys_file = os.path.join(td, "in.txt"), os.path.join(td, "keys.txt")
            open(src, "w", encoding="utf-8").write("\n".join(lines) + "\n")
            open(keys_file, "w", encoding="utf-8").write("# comment\n軟件\n\n")
            keys, info = prep.load_simp_keys(keys_file)
            self.assertEqual((keys, info["keys"], info["basename"], info["sha256"]), (frozenset({"軟件"}), 1, "keys.txt", prep.sha256_file(keys_file)))
            self.assertEqual(info["keys_in_twphrases"], 1)
            bad = os.path.join(td, "bad.txt")   # Taiwan wording instead of TWPhrases keys: refused, not a silent empty table
            open(bad, "w", encoding="utf-8").write("軟體\n滑鼠\n")
            with self.assertRaises(SystemExit):
                prep.load_simp_keys(bad)
            plain = prep.write_runs(src, td, "plain", self.conv, tw=False)
            self.assertFalse(os.path.exists(os.path.join(td, "flags-plain.u8")))
            self.assertNotIn("flags_sha256", plain)
            for procs in (1, 2):
                rep = prep.write_runs(src, td, f"f{procs}", self.conv, procs=procs, tw=False, keys=keys, keys_info=info, flags=True)
                self.assertEqual(open(os.path.join(td, f"flags-f{procs}.u8"), "rb").read(), b"\x01\x00\x01")
                self.assertEqual((rep["changed_runs"], rep["simp_tw_keys"]), (2, info))
                self.assertEqual(rep["flags_sha256"], prep.sha256_file(os.path.join(td, f"flags-f{procs}.u8")))
            self.assertEqual(open(os.path.join(td, "runs-f1.txt"), "rb").read(), open(os.path.join(td, "runs-f2.txt"), "rb").read())
            self.assertEqual(open(os.path.join(td, "runs-f1.txt"), encoding="utf-8").read().split("\n")[:-1], ["管理員權限加好了", "今天天氣真好", "管理員權限加好了"])
            # the same keys and no flags: identical runs, no flags file
            prep.write_runs(src, td, "nf", self.conv, tw=False, keys=keys, keys_info=info)
            self.assertEqual(open(os.path.join(td, "runs-nf.txt"), "rb").read(), open(os.path.join(td, "runs-f1.txt"), "rb").read())
            self.assertFalse(os.path.exists(os.path.join(td, "flags-nf.u8")))
            open(keys_file, "w", encoding="utf-8").write("軟\n")
            with self.assertRaises(SystemExit):
                prep.load_simp_keys(keys_file)

    def test_no_trad_tw_phrases_guard_matches_the_written_text(self):
        """The guard gets the same setting, so a held-out 權限 sentence is found in the text that is actually written."""
        import holdout_guard
        with tempfile.TemporaryDirectory() as td:
            rows = os.path.join(td, "rows.txt")
            open(rows, "w", encoding="utf-8").write("a|管理員權限加好了|x\n")
            matcher = holdout_guard.build_matcher([rows], self.conv, False)[0]
            prep._init(matcher, False)
            try:
                text, _, n, skipped = prep._work(("text", ["管理員權限加好了"]))
            finally:
                prep._init()
        self.assertEqual((text, n, skipped), ("", 0, 1))

    def test_no_trad_tw_phrases_expects_the_regenerated_pool(self):
        self.assertTrue(prep.match_hash("colloquial-train.txt", "eb905936" + "0" * 52 + "57a0", prep.EXPECTED_NO_TW))
        self.assertFalse(prep.match_hash("colloquial-train.txt", "3e833d06" + "0" * 52 + "b4db", prep.EXPECTED_NO_TW))
        self.assertTrue(prep.match_hash("colloquial-train.txt", "3e833d06" + "0" * 52 + "b4db"))
        self.assertEqual({k: v for k, v in prep.EXPECTED_NO_TW.items() if k != "colloquial-train.txt"},
                         {k: v for k, v in prep.EXPECTED.items() if k != "colloquial-train.txt"})

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
            prep.cmd_encode(type("A", (), {"work": td, "min_count": 5, "extra": [], "force": False})())
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
            # S7d flags: one per sentence, in order, with the pool too; an empty sentence is never a hit
            for procs in (1, 2):
                self.assertEqual(buildcheck.substring_hits(["天氣真", "", "吃飯吧", "今天", "好我"], [p], procs=procs), [True, False, False, True, False])


if __name__ == "__main__":
    unittest.main()
