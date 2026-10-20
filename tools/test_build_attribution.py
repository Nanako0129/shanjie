"""tools/build_attribution.py on synthetic pools (docs/contracts/colloquial-term.md section 3.6): the sums, the stop conditions, and that no
sentence text reaches the output."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_attribution as ba  # noqa: E402

SMALL = {"wildchat": 2, "yodas": 3, "tatoeba": 2, "massive": 1}
POOL = [
    ("wildchat", "aaa:0:1", "祕密句子一"), ("wildchat", "bbb:2", "祕密句子二"),
    ("yodas", "vid1:0", "祕密句子三"), ("yodas", "vid1:4", "祕密句子四"), ("yodas", "vid2:1:0", "祕密句子五"),
    ("tatoeba", "10", "祕密句子六"), ("tatoeba", "11:1", "祕密句子七"),
    ("massive", "test-0", "祕密句子八"),
]
ATTR = [
    ("yodas", "vid1:0", "https://www.youtube.com/watch?v=vid1"), ("yodas", "vid1:4", "https://www.youtube.com/watch?v=vid1"),
    ("yodas", "vid2:1:0", "https://www.youtube.com/watch?v=vid2"),
    ("tatoeba", "10", "alice"), ("tatoeba", "11:1", "bob"),
]


def tsv(rows):
    return "".join("\t".join(r) + "\n" for r in rows)


class Run(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.p = lambda n: os.path.join(self.d.name, n)

    def go(self, pool=POOL, attr=ATTR, expected=SMALL, sha=False):
        open(self.p("pool.tsv"), "w", encoding="utf-8").write(tsv(pool))
        open(self.p("attr.tsv"), "w", encoding="utf-8").write(tsv(attr))
        args = ["--pool", self.p("pool.tsv"), "--attribution", self.p("attr.tsv"), "--out", self.p("out.tsv")]
        if sha:
            args += ["--sha-out", self.p("out.sha256")]
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(ba, "EXPECTED", expected), contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            rc = ba.main(args)
        return rc, err.getvalue()

    def rows(self):
        return [l.split("\t") for l in open(self.p("out.tsv"), encoding="utf-8").read().splitlines()]

    def test_one_row_per_unit_with_counts_and_authors(self):
        rc, _ = self.go()
        self.assertEqual(rc, 0)
        r = self.rows()
        self.assertEqual(r[0], ba.HEADER.split("\t"))
        got = {(x[0], x[1]): x for x in r[1:]}
        self.assertEqual(got[("yodas", "vid1")][3:], ["https://www.youtube.com/watch?v=vid1", ba.LICENCES["yodas"], "2"])
        self.assertEqual(got[("yodas", "vid2")][5], "1")
        self.assertEqual(got[("tatoeba", "11")][2:], ["bob", "-", ba.LICENCES["tatoeba"], "1"])
        self.assertEqual(got[("wildchat", "allenai/WildChat-1M")][5], "2")
        self.assertEqual(got[("massive", "AmazonScience/massive")][5], "1")
        self.assertEqual(len(r) - 1, 6)

    def test_per_source_sums_equal_the_expected_counts(self):
        self.assertEqual(self.go()[0], 0)
        for src, n in SMALL.items():
            self.assertEqual(sum(int(x[5]) for x in self.rows()[1:] if x[0] == src), n)
        rc, err = self.go(expected={**SMALL, "yodas": 4})
        self.assertEqual(rc, 1)
        self.assertIn("yodas: 3 sentences, expected 4", err)

    def test_no_sentence_text_in_the_output(self):
        self.go()
        self.assertNotIn("祕密", open(self.p("out.tsv"), encoding="utf-8").read())

    def test_a_pool_row_without_a_unit_id_stops(self):
        for bad in ("", ":3"):
            rc, err = self.go(pool=POOL + [("tatoeba", bad, "x")], expected={**SMALL, "tatoeba": 3})
            self.assertEqual(rc, 1, bad)
            self.assertIn("no unit id", err)
            self.assertNotIn("x\n", err)

    def test_a_tatoeba_unit_without_an_author_stops(self):
        attr = [a for a in ATTR if a[1] != "11:1"] + [("tatoeba", "11:1", "")]
        rc, err = self.go(attr=attr)
        self.assertEqual(rc, 1)
        self.assertIn("tatoeba unit has no author", err)
        rc, err = self.go(attr=[a for a in ATTR if a[1] != "11:1"])   # no attribution row at all
        self.assertEqual(rc, 1)

    def test_a_yodas_unit_without_a_url_stops(self):
        rc, err = self.go(attr=[a for a in ATTR if a[1] != "vid2:1:0"])
        self.assertEqual(rc, 1)
        self.assertIn("yodas unit has no URL", err)

    def test_two_references_for_one_unit_stop(self):
        attr = [a if a[1] != "vid1:4" else ("yodas", "vid1:4", "https://www.youtube.com/watch?v=other") for a in ATTR]
        rc, err = self.go(attr=attr)
        self.assertEqual(rc, 1)
        self.assertIn("two different URLs", err)

    def test_unknown_source_and_wrong_field_count_stop(self):
        self.assertEqual(self.go(pool=POOL + [("reddit", "1", "x")])[0], 1)
        self.assertEqual(self.go(pool=POOL + [("tatoeba", "12")])[0], 1)

    def test_deterministic_and_the_sha_line_matches_the_file(self):
        self.go(sha=True)
        first = open(self.p("out.tsv"), "rb").read()
        self.go(pool=list(reversed(POOL)), sha=True)
        self.assertEqual(first, open(self.p("out.tsv"), "rb").read())
        import hashlib
        line = open(self.p("out.sha256"), encoding="utf-8").read().split()
        self.assertEqual(line[0], hashlib.sha256(first).hexdigest())

    def test_the_expected_counts_are_the_ones_the_research_log_records(self):
        log = open(os.path.join(HERE, "..", "docs", "research-log.md"), encoding="utf-8").read()
        for needle in ("WildChat 3,491", "YODAS 90,080", "Tatoeba 36,839", "MASSIVE 13,485"):
            self.assertIn(needle, log)
        self.assertEqual(ba.EXPECTED, {"wildchat": 3491, "yodas": 90080, "tatoeba": 36839, "massive": 13485})


if __name__ == "__main__":
    unittest.main()
