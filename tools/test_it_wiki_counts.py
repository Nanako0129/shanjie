"""Tests for tools/it_wiki_counts.py. A tiny bz2 dump built here; no network, no zhconv-rs (mw_mode "none")."""
import bz2
import collections
import hashlib
import os
import random
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import it_wiki_counts as C  # noqa: E402

PAGES = [("甲", 0, "演算法很重要。演算法演算法！資料庫與資料庫。"), ("乙", 0, "aaa。人工智慧；機器學習和人工智慧\n人工智慧慧"),
         ("丙", 1, "演算法"), ("丁", 0, "#REDIRECT [[甲]]")] + [(f"頁{i}", 0, "資料庫" * (i % 3) + "。機器學習") for i in range(40)]
CANDS = ["演算法", "資料庫", "人工智慧", "機器學習", "機器", "沒有"]


def write_dump():
    d = tempfile.mkdtemp()
    xml = "<mediawiki>\n" + "".join(
        f"<page>\n<title>{t}</title>\n<ns>{ns}</ns>\n<revision>\n<text xml:space=\"preserve\">{x}</text>\n</revision>\n</page>\n" for t, ns, x in PAGES) + "</mediawiki>\n"
    p = os.path.join(d, "d.xml.bz2")
    with bz2.open(p, "wt", encoding="utf-8") as f:
        f.write(xml)
    c = os.path.join(d, "cands.txt")
    open(c, "w", encoding="utf-8").write("".join(x + "\n" for x in CANDS))
    return d, p, c


class Counts(unittest.TestCase):
    def test_non_overlapping_matches_str_count(self):
        out = collections.Counter()
        C.count_sentence("aaa", {"aa"}, {"a"}, [2], out)
        self.assertEqual(out["aa"], "aaa".count("aa"))
        rnd = random.Random(1)
        cands = ["ab", "aba", "bab", "abab", "b"]
        for _ in range(300):
            s = "".join(rnd.choice("ab") for _ in range(rnd.randint(0, 25)))
            o = collections.Counter()
            C.count_sentence(s, set(cands), {c[0] for c in cands}, sorted({len(c) for c in cands}), o)
            self.assertEqual({c: o[c] for c in cands}, {c: s.count(c) for c in cands}, s)

    def test_procs_independent_and_values(self):
        d, dump, cands = write_dump()
        a = C.count_dump(dump, CANDS, 1, "none", batch=3)
        b = C.count_dump(dump, CANDS, 2, "none", batch=3)
        self.assertEqual(a, b)
        self.assertEqual(a["演算法"], 3)        # ns1 與 redirect 不算
        self.assertEqual(a["人工智慧"], 3)
        self.assertEqual(a["機器學習"], 1 + sum(1 for i in range(40)))
        self.assertEqual(a["沒有"], 0)

    def run_cli(self, dump, cands, out, procs, sha=C.DUMP_SHA):
        return subprocess.run([sys.executable, C.__file__, "--dump", dump, "--candidates", cands, "--out", out, "--procs", str(procs),
                               "--expect-sha256", sha, "--no-mw"], capture_output=True, text=True)

    def test_cli_header_and_procs(self):
        d, dump, cands = write_dump()
        sha = C.sha256(dump)
        outs = []
        for n in (1, 2):
            o = os.path.join(d, f"o{n}.tsv")
            r = self.run_cli(dump, cands, o, n, sha)
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(open(o, encoding="utf-8").read())
        self.assertEqual(outs[0].split("\n", 1)[1], outs[1].split("\n", 1)[1])
        head, *rows = outs[0].splitlines()
        self.assertEqual(head.split()[1], f"dump_sha256={sha}")
        self.assertEqual(head.split()[2], "candidates_sha256=" + hashlib.sha256(open(cands, "rb").read()).hexdigest())
        self.assertRegex(head.split()[3], r"^tool_commit=[0-9a-f]{40}(-dirty)?$")
        self.assertEqual([r.split("\t")[0] for r in rows], sorted(CANDS))

    def test_dump_hash_mismatch_stops(self):
        d, dump, cands = write_dump()
        o = os.path.join(d, "o.tsv")
        r = self.run_cli(dump, cands, o, 1)        # 預設是契約的雜湊，夾具不符
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("mismatch", r.stderr)
        self.assertFalse(os.path.exists(o))


if __name__ == "__main__":
    unittest.main()
