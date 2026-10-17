"""hijack.py on a small made-up example: one broken row per class, plus rows that must not count."""
import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import hijack  # noqa: E402


def write(d, name, text):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


class HijackTest(unittest.TestCase):
    def test_four_classes(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "mcbpmf-data.txt", "# format x\n_punct_x ， 0.0\nㄨㄟˊ-ㄑㄧˊ 為期 -4.85\nㄍㄣ-ㄧㄣ 根音 -7.47\n")
            write(d, "overlay-add.tsv", "")
            write(d, "sandhi-add.tsv", "")
            # the names layer counts as lexicon (revision two): 湯臣 above the shadow
            write(d, "names-add.tsv", "ㄊㄤ-ㄔㄣˊ\t湯臣\t-5.17\tnames\n")
            base = write(d, "base.tsv", "1\t1\t為期很長\t為期很長\n2\t1\t根音在下\t根音在下\n3\t1\t王斌來了\t王斌來了\n"
                         "4\t1\t他來了\t他來了\n5\t1\t湯臣大樓\t湯臣大樓\n6\t0\t為祁\t為期\n7\t1\t好\t好\n")
            new = write(d, "new.tsv", "1\t0\t為祁很長\t為期很長\n2\t0\t根因在下\t根音在下\n3\t0\t王彬來了\t王斌來了\n"
                        "4\t0\t她來了\t他來了\n5\t0\t湯陳大樓\t湯臣大樓\n6\t0\t為祁\t為期\n7\t1\t好\t好\n")
            words = write(d, "words.txt", "running 1 test\nnewword\tㄨㄟˊ-ㄑㄧˊ\t為祁\nnewword\tㄍㄣ-ㄧㄣ\t根因\n"
                          "newword\tㄨㄤˊ-ㄅㄧㄣ\t王彬\nnewword\tㄊㄤ-ㄔㄣˊ\t湯陳\nmeasure_newword new: ...\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                hijack.main([base, new, words, "--lexicon-dir", d])
            self.assertEqual(out.getvalue(), "broken\t5\n(i)\t2\n(ii)\t1\n(iii)\t1\n(iv)\t1\n")
            # the shadow decides (i) against (ii)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                hijack.main([base, new, words, "--lexicon-dir", d, "--shadow", "-4.0"])
            self.assertEqual(out.getvalue(), "broken\t5\n(i)\t0\n(ii)\t3\n(iii)\t1\n(iv)\t1\n")
            # the names layer is read; control rows (`_` prefix) are skipped
            lex = hijack.load_lexicon(d)
            self.assertEqual(lex[("ㄊㄤ-ㄔㄣˊ", "湯臣")], -5.17)
            self.assertNotIn(("_punct_x", "，"), lex)


if __name__ == "__main__":
    unittest.main()
