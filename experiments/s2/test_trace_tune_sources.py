"""trace_tune_sources.py on the synthetic sources of test_build_tune (docs/contracts/tune-no-twphrases.md section 2, first bullet)."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_tune as bt  # noqa: E402
import test_build_tune as tbt  # noqa: E402
import test_trad_phrases as ttp  # noqa: E402
import trace_tune_sources as tr  # noqa: E402

WIKI = ["管理員權限加好了。我们的权限很好用。", "這個插件我们天天用。", "他的權限很好。"]   # article 0: Traditional 權限, then a Simplified one; 2: Traditional


class Trace(tbt.SynthTables):
    def run_trace(self, part, tune_rows, *flags):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as d:
            self.sources(src)
            tune = os.path.join(d, "t.txt")
            open(tune, "w", encoding="utf-8").write("".join(f"|{r}|ㄅ\n" for r in tune_rows))
            out = io.StringIO()
            with mock.patch.object(bt, "SRC", src), mock.patch.object(bt, "articles_in", lambda lo, hi, progress=0: iter(WIKI)), contextlib.redirect_stdout(out):
                rc = tr.main(["--part", part, "--tune", tune, "--words", "許可權", *flags], conv=ttp.CONV)
            return rc, [l.split("\t") for l in out.getvalue().splitlines()]

    def test_cv_default_build_traces_a_traditional_source(self):
        """Built the old way, the Traditional 權限 sentence became 許可權 in the tune file: the trace names its raw source and calls it traditional."""
        rc, lines = self.run_trace("cv", ["我們的許可權很好用戊", "我們的許可權很好用丙"])
        hits = {l[2]: l[4] for l in lines if l[0] == "hit"}
        self.assertEqual(hits, {"1": "traditional", "2": "simplified"})
        self.assertEqual([l for l in lines if l[0] == "hit" and l[2] == "1"][0][5], tbt.CV_TRAD)   # the raw sentence is named
        self.assertIn(["count", "許可權", "2", "1", "1"], lines)
        self.assertEqual(rc, 1)

    def test_cv_flag_build_traces_only_simplified_sources(self):
        rc, lines = self.run_trace("cv", ["我們的許可權很好用丙", "我們的權限很好用戊"], "--cv-no-trad-tw-phrases")
        self.assertEqual([l[4] for l in lines if l[0] == "hit"], ["simplified"])
        self.assertIn(["count", "許可權", "1", "1", "0"], lines)
        self.assertIn(["traditional-sources", "0"], lines)
        self.assertEqual(rc, 0)

    def test_wiki_trace_by_article_and_sentence(self):
        rc, lines = self.run_trace("wiki", ["我們的許可權很好用", "管理員許可權加好了"], "--tune-no-trad-tw-phrases", "--wiki-range", "7", "10")
        hits = {(l[3], l[4]) for l in lines if l[0] == "hit"}
        self.assertEqual(hits, {("wiki:7", "simplified")})                       # the Simplified sentence; the Traditional 權限 keeps its wording
        self.assertEqual(rc, 2)                                                    # 管理員許可權加好了 is in the file but no source gives it: parameters do not match
        self.assertIn(["no-source", "1"], lines)
        rc, lines = self.run_trace("wiki", ["我們的許可權很好用", "管理員許可權加好了", "他的許可權很好"], "--wiki-range", "7", "10")   # built the old way
        self.assertEqual({(l[3], l[4]) for l in lines if l[0] == "hit"}, {("wiki:7", "simplified"), ("wiki:7", "traditional"), ("wiki:9", "traditional")})
        self.assertEqual(rc, 1)

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
            self.assertEqual(run("--collect", hits)[1], "collected\t3\n")                     # no tune file needed; three source clauses become 許可權 (the flag is off, as in an old-style build)
            self.assertEqual(run("--tune", tune, "--hits", hits), run("--tune", tune))
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(FileExistsError):
                run("--collect", hits)


if __name__ == "__main__":
    unittest.main()
