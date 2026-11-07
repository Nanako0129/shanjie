"""trace_tune_sources.py on synthetic sources (docs/contracts/tune-no-twphrases.md section 2, first bullet; conversion per docs/contracts/tw-conv.md:
Common Voice is native, Tatoeba and wiki are mixed). The synthetic tables have 權限 -> 許可權 as a mainland term and 权 -> 權 as a character."""
import bz2
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_tune as bt  # noqa: E402
import test_trad_phrases as ttp  # noqa: E402
import trace_tune_sources as tr  # noqa: E402

LETTERS = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥天地玄黃宇宙洪荒日月盈昃辰宿列張"
# source rows that hash into the tune 20%: a native Common Voice row that really says 許可權, and a Simplified Tatoeba row that becomes 許可權
CV_BAD = next("我們的許可權很好用" + c for c in LETTERS if bt.is_tune("我們的許可權很好用" + c))
TAT_SIMP = next("我们的权限很好用" + c for c in LETTERS if bt.is_tune("我们的权限很好用" + c) and not CV_BAD.endswith(c))
TAT_OUT = TAT_SIMP.replace("我们的权限", "我們的許可權")   # what the conversion makes of it
WIKI = ["管理員權限加好了。我们的权限很好用。", "這個插件我们天天用。", "他的權限很好。"]   # article 0: Traditional 權限, then a Simplified one


class Trace(ttp.Base):
    def setUp(self):
        super().setUp()
        ttp.TW_PHRASE["權限"] = "許可權"; self.addCleanup(ttp.TW_PHRASE.pop, "權限", None)
        ttp.CHAR["权"] = "權"; self.addCleanup(ttp.CHAR.pop, "权", None)
        ttp.TABLES["TRAD_SIMP_ONLY"].add("权"); self.addCleanup(ttp.TABLES["TRAD_SIMP_ONLY"].discard, "权")
        ttp.TABLES["MARKERS"].add("权"); self.addCleanup(ttp.TABLES["MARKERS"].discard, "权")

    def sources(self, src):
        open(os.path.join(src, "cv-a.tsv"), "w", encoding="utf-8").write(f"x\t{CV_BAD}\n")
        with bz2.open(os.path.join(src, "cmn_sentences.tsv.bz2"), "wt", encoding="utf-8") as f:
            f.write(f"1\tcmn\t{TAT_SIMP}\n")

    def run_trace(self, part, tune_rows, *flags):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as d:
            self.sources(src)
            tune = os.path.join(d, "t.txt")
            open(tune, "w", encoding="utf-8").write("".join(f"|{r}|ㄅ\n" for r in tune_rows))
            out = io.StringIO()
            with mock.patch.object(bt, "SRC", src), mock.patch.object(bt, "articles_in", lambda lo, hi, progress=0: iter(WIKI)), contextlib.redirect_stdout(out):
                rc = tr.main(["--part", part, "--tune", tune, "--words", "許可權", *flags], conv=ttp.CONV)
            return rc, [l.split("\t") for l in out.getvalue().splitlines()]

    def test_cv_traces_a_native_row_that_says_the_word_as_traditional_and_a_converted_one_as_simplified(self):
        """Common Voice is native: a 許可權 in its text is the source's own, so the trace calls it traditional (a failure of the tune file's wording)."""
        rc, lines = self.run_trace("cv", [CV_BAD, TAT_OUT])
        hits = {l[2]: l[4] for l in lines if l[0] == "hit"}
        self.assertEqual(hits, {"1": "traditional", "2": "simplified"})
        self.assertEqual([l for l in lines if l[0] == "hit" and l[2] == "1"][0][5], CV_BAD)   # the raw sentence is named
        self.assertIn(["count", "許可權", "2", "1", "1"], lines)
        self.assertEqual(rc, 1)

    def test_cv_with_only_a_simplified_source(self):
        rc, lines = self.run_trace("cv", [TAT_OUT, "我們的權限很好用戊"])
        self.assertEqual([l[4] for l in lines if l[0] == "hit"], ["simplified"])
        self.assertIn(["count", "許可權", "1", "1", "0"], lines)
        self.assertIn(["traditional-sources", "0"], lines)
        self.assertEqual(rc, 0)

    def test_wiki_trace_by_article_and_sentence(self):
        rc, lines = self.run_trace("wiki", ["我們的許可權很好用", "管理員許可權加好了"], "--wiki-range", "7", "10")
        hits = {(l[3], l[4]) for l in lines if l[0] == "hit"}
        self.assertEqual(hits, {("wiki:7", "simplified")})                       # the Simplified sentence; the Traditional 權限 keeps its wording
        self.assertEqual(rc, 2)                                                    # 管理員許可權加好了 is in the file but no source gives it: parameters do not match
        self.assertIn(["no-source", "1"], lines)

    def test_collect_then_join_gives_the_same_report_as_scanning(self):
        rows = ["我們的許可權很好用", "管理員許可權加好了", "他的許可權很好"]
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as d:
            self.sources(src)
            tune, hits = os.path.join(d, "t.txt"), os.path.join(d, "hits.tsv")
            open(tune, "w", encoding="utf-8").write("".join(f"|{r}|ㄅ\n" for r in rows))
            base = ["--part", "wiki", "--words", "許可權", "--wiki-range", "7", "10"]
            def run(*args):
                out = io.StringIO()
                with mock.patch.object(bt, "SRC", src), mock.patch.object(bt, "articles_in", lambda lo, hi, progress=0: iter(WIKI)), contextlib.redirect_stdout(out):
                    return tr.main(base + list(args), conv=ttp.CONV), out.getvalue()
            self.assertEqual(run("--collect", hits)[1], "collected\t1\n")                     # no tune file needed; one source clause becomes 許可權
            self.assertEqual(run("--tune", tune, "--hits", hits), run("--tune", tune))
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(FileExistsError):
                run("--collect", hits)

    def test_the_old_flags_are_gone(self):
        for flag in ("--cv-no-trad-tw-phrases", "--tune-no-trad-tw-phrases"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                tr.main(["--part", "cv", "--tune", "x", flag], conv=ttp.CONV)


if __name__ == "__main__":
    unittest.main()
