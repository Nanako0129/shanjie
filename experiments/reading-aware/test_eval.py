"""Tests of ra_eval.py (contract docs/contracts/reading-aware-0.md 4.1: the evaluation anchor in its unit-test form, the merged demotion
table on both decoding paths, the duplicate-key stop). The anchor tests run reference/proto/lm_eval.py itself on a small model with the
repo's real lexicon, once directly and once through the wrapper."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

import a1_table
import level1
import ra_eval
import rcommon
import testkit
from testkit import U
import lm as L
import lm_eval  # noqa: E402
from trigram import TriLM, decode3  # noqa: E402

BARE_UNI = {"今天": 9, "天氣": 9, "很": 9, "好": 9, "吧": 9, "巴": 40, "我": 9, "去": 9, "學校": 9, "嗎": 9, "你": 9}
BARE_BI = {("<s>", "今天"): 6, ("今天", "天氣"): 6, ("天氣", "很"): 6, ("很", "好"): 6, ("好", "</s>"): 6, ("<s>", "巴"): 30, ("巴", "</s>"): 30,
           ("好", "吧"): 3, ("吧", "</s>"): 3, ("<s>", "我"): 6, ("我", "去"): 6, ("去", "學校"): 6, ("學校", "</s>"): 6, ("<s>", "你"): 6, ("你", "嗎"): 6, ("嗎", "</s>"): 6}
ROWS = """|今天天氣很好|ㄐㄧㄣ ㄊㄧㄢ ㄊㄧㄢ ㄑㄧˋ ㄏㄣˇ ㄏㄠˇ
|吧|ㄅㄚ˙
好|吧|ㄅㄚ˙
好|吧|ㄅㄚ
|我去學校|ㄨㄛˇ ㄑㄩˋ ㄒㄩㄝˊ ㄒㄧㄠˋ
|你嗎|ㄋㄧˇ ㄇㄚ
"""
FLAGS = ["--no-classes", "--context", "--dump"]


def reference(argv):
    buf = io.StringIO()
    with mock.patch.object(sys, "argv", ["lm_eval", *argv]), contextlib.redirect_stdout(buf):
        lm_eval.main()
    return buf.getvalue()


def wrapped(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ra_eval.run(argv)
    return buf.getvalue()


def firsts(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        r, rank, surface, _ = line.rstrip("\n").split("\t")
        if rank == "1":
            out[int(r)] = surface
    return out


class Wrapper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.d = cls.td.name
        cls.rows = testkit.write(cls.d, "rows.txt", ROWS)
        cls.model, _ = testkit.sjlm(cls.d, BARE_UNI, BARE_BI)
        cls.real = rcommon.load_lexicon()
        cls.a1 = os.path.join(cls.d, "a1.tsv")
        a1_table.write_checked(cls.a1, cls.real, 9.0)           # huge delta: the table decides the winner whatever the model says

    @classmethod
    def tearDownClass(cls):
        cls.td.cleanup()

    def argv(self, profile, tag, *extra):
        return ["--lm", self.model, "--profile", profile, "--rows", self.rows, "--no-classes", "--context", "--rowstats", os.path.join(self.d, tag + ".rs"),
                "--dump", os.path.join(self.d, tag + ".dump"), *extra]

    def rs(self, tag):
        return open(os.path.join(self.d, tag + ".rs"), "rb").read()

    def test_anchor_no_table_no_tone_is_lm_evals_own_output(self):
        for profile in ("chat", "formal"):
            with self.subTest(profile):
                ref = reference(self.argv(profile, "ref-" + profile))
                got = wrapped(self.argv(profile, "got-" + profile))
                self.assertEqual(got, ref)
                self.assertIn("top1_sha256", got)
                self.assertEqual(self.rs("got-" + profile), self.rs("ref-" + profile))
                self.assertEqual(open(os.path.join(self.d, "got-" + profile + ".dump")).read(), open(os.path.join(self.d, "ref-" + profile + ".dump")).read())

    def test_anchor_with_the_extended_decoder_on_a_model_without_units(self):
        for profile in ("chat", "formal"):
            with self.subTest(profile):
                ref = reference(self.argv(profile, "uref-" + profile))
                got = wrapped(self.argv(profile, "uget-" + profile, "--units"))
                self.assertEqual(got, ref)
                self.assertEqual(self.rs("uget-" + profile), self.rs("uref-" + profile))
                self.assertEqual(open(os.path.join(self.d, "uget-" + profile + ".dump")).read(), open(os.path.join(self.d, "uref-" + profile + ".dump")).read())

    def test_a_demotion_table_changes_the_first_candidate_and_the_summary_tag(self):
        ref = reference(self.argv("chat", "t-ref"))
        got = wrapped(self.argv("chat", "t-got", "--extra-demote", self.a1))
        base, cand = firsts(os.path.join(self.d, "t-ref.dump")), firsts(os.path.join(self.d, "t-got.dump"))
        self.assertEqual(base[2], "巴")                               # BASE: the model's 巴 beats 吧 for ㄅㄚ˙ after nothing (the A1 situation)
        self.assertEqual(cand[2], "吧")                               # with the table: 吧
        self.assertEqual((base[3], base[4]), ("吧", "吧"))            # after 好 the model likes 吧; the table only helps, ...
        self.assertEqual((cand[3], cand[4]), ("吧", "吧"))            # ... and ㄅㄚ (one tone) is not a ˙ row
        self.assertEqual((base[1], base[5], base[6]), (cand[1], cand[5], cand[6]))
        tag = got.split("  ")[1]
        self.assertTrue(tag.startswith("lm-chat+ctx-noclasses+dm:") and len(tag.split("+dm:")[1]) == 8, tag)
        self.assertNotEqual(got, ref)
        self.assertEqual(got.split("'n': ")[1].split(",")[0], ref.split("'n': ")[1].split(",")[0])
        # a table does not change a row it has no entry for: byte-identical dump lines
        grab = lambda tag_, row: [l for l in open(os.path.join(self.d, tag_ + ".dump"), encoding="utf-8") if l.startswith(f"{row}\t")]
        self.assertEqual(grab("t-ref", 1), grab("t-got", 1))

    def test_no_demote_switch_still_means_no_table(self):
        got = wrapped(self.argv("chat", "nd-got", "--extra-demote", self.a1, "--no-demote"))
        ref = reference(self.argv("chat", "nd-ref", "--no-demote"))
        self.assertEqual(firsts(os.path.join(self.d, "nd-got.dump")), firsts(os.path.join(self.d, "nd-ref.dump")))
        self.assertIn("-nodemote", got)
        self.assertEqual(self.rs("nd-got"), self.rs("nd-ref"))

    def test_delta_tone_demotes_the_level_one_rows_only(self):
        base = wrapped(self.argv("chat", "n-base", "--extra-demote", self.a1))
        tone = wrapped(self.argv("chat", "n-tone", "--extra-demote", self.a1, "--delta-tone", "9"))
        b, t = firsts(os.path.join(self.d, "n-base.dump")), firsts(os.path.join(self.d, "n-tone.dump"))
        self.assertEqual(b[4], "吧")                                  # ㄅㄚ (one tone) after 好: 吧 wins at first (巴 ㄅㄚ is not in the A1 table)...
        self.assertNotEqual(t[4], "吧")                               # ... and loses to a homophone (八 in the real lexicon) once the level-1 row (ㄅㄚ, 吧) is demoted
        self.assertEqual((b[2], b[3]), (t[2], t[3]))                  # the ˙ rows are A1's, unchanged by the tone rows
        self.assertTrue(tone.split("  ")[1].endswith(":t9"), tone)
        self.assertNotEqual(base, tone)

    def test_a_duplicate_key_stops_the_run(self):
        dup = testkit.write(self.d, "dup.tsv", "ㄅㄚ˙\t巴\t0.5\treading-neutral\ttest\n")
        with self.assertRaisesRegex(SystemExit, "demotion key"):
            wrapped(self.argv("chat", "dup", "--extra-demote", self.a1, "--extra-demote", dup))
        tone = testkit.write(self.d, "tone.tsv", "ㄅㄚ\t吧\t0.5\treading-neutral\ttest\n")
        with self.assertRaisesRegex(SystemExit, "demotion key"):
            wrapped(self.argv("chat", "dup2", "--extra-demote", tone, "--delta-tone", "0.5"))

    def test_a_unit_model_needs_the_flag(self):
        path, _ = testkit.sjlm(self.d, {"好": 9, "吧" + U + "ㄅㄚ": 9}, {("<s>", "好"): 5, ("好", "</s>"): 5}, "units.sjlm")
        with self.assertRaisesRegex(SystemExit, "--units"):
            wrapped(["--lm", path, "--profile", "chat", "--rows", self.rows, "--no-classes"])
        out = wrapped(["--lm", path, "--profile", "chat", "--rows", self.rows, "--no-classes", "--units"])
        self.assertIn("+units", out.split("  ")[1])


class MergedTable(unittest.TestCase):
    """The merged table is on the lexicon, so lm.decode and the formal profile's trigram.decode3 both see it (the reviewer's warning)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        d = self.td.name
        _, self.lm = testkit.sjlm(d, {"吧": 40, "巴": 40}, {("<s>", "吧"): 20, ("<s>", "巴"): 30, ("吧", "</s>"): 20, ("巴", "</s>"): 30})
        self.lex = testkit.lexicon(d)
        self.table = testkit.write(d, "x.tsv", "ㄅㄚ˙\t巴\t9.0\treading-neutral\ttest\n")

    def capped(self, extra, tone=0.0):
        return ra_eval.make_cap_overlay(L.cap_overlay, extra, tone)(self.lex, set(), self.lm, demote_path=None)

    def test_both_decoders_see_the_table(self):
        plain, merged = self.capped([]), self.capped([self.table])
        syls = ["ㄅㄚ˙"]
        tri = TriLM(self.lm)
        self.assertEqual(L.decode(plain, syls, self.lm, "formal")[0][1], ("巴",))
        self.assertEqual(L.decode(merged, syls, self.lm, "formal")[0][1], ("吧",))
        self.assertEqual(decode3(plain, syls, tri, "formal")[0][1], ("巴",))
        self.assertEqual(decode3(merged, syls, tri, "formal")[0][1], ("吧",))

    def test_the_shipped_table_is_kept_and_a_clash_with_it_stops(self):
        shipped = testkit.write(self.td.name, "shipped.tsv", "ㄅㄚ˙\t吧\t0.1\treading\ttest\n")
        wrap = ra_eval.make_cap_overlay(L.cap_overlay, [self.table], 0.0)
        out = wrap(self.lex, set(), self.lm, demote_path=shipped)
        self.assertEqual(out.demote, {("ㄅㄚ˙", "吧"): 0.1, ("ㄅㄚ˙", "巴"): 9.0})
        clash = testkit.write(self.td.name, "clash.tsv", "ㄅㄚ˙\t吧\t2.0\treading-neutral\ttest\n")
        with self.assertRaisesRegex(SystemExit, "demotion key"):
            ra_eval.make_cap_overlay(L.cap_overlay, [clash], 0.0)(self.lex, set(), self.lm, demote_path=shipped)

    def test_a_table_row_the_lexicon_lacks_is_an_error(self):
        bad = testkit.write(self.td.name, "bad.tsv", "ㄅㄚ˙\t無\t1.0\treading-neutral\ttest\n")
        with self.assertRaisesRegex(ValueError, "no lexicon entry"):
            self.capped([bad])

    def test_tone_rows_are_the_level_one_rows(self):
        out = self.capped([], 0.25)
        self.assertEqual(out.demote, {("ㄅㄚ", "吧"): 0.25, ("ㄇㄚ", "嗎"): 0.25})
        self.assertEqual(sorted(out.demote), sorted((r, w) for r, w in level1.tone_rows(self.lex)))
        self.assertEqual(self.capped([], 0.0).demote, {})            # no table, no tone: the lexicon is cap_overlay's own

    def test_tag(self):
        self.assertEqual(ra_eval.tag_of([], 0.0, False), "")
        self.assertEqual(ra_eval.tag_of([], 0.5, True), ":t0.5+units")
        self.assertRegex(ra_eval.tag_of([self.table], 0.25, False), r"^\+dm:[0-9a-f]{8}:t0\.25$")


if __name__ == "__main__":
    unittest.main()
