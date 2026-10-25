import contextlib, io, json, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review_subs as R  # noqa: E402


class Review(unittest.TestCase):
    ROWS = ["opus-subs\t1\t我們今天去吃飯", "opus-subs\t2\t那里有魚", "opus-subs\t3\t這裡很好", "opus-subs\t4\t拼寫n", "opus-subs\t5\t第四步游戲"]

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.subs = os.path.join(self.d, "subs.tsv")
        open(self.subs, "w", encoding="utf-8").write("\n".join(self.ROWS) + "\n")

    def run_quiet(self, f, *a):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            f(*a)
        return json.loads(buf.getvalue())

    def test_prefilter_chunks_only_suspect_sentences(self):
        out = os.path.join(self.d, "chunks")
        st = self.run_quiet(R.prefilter, self.subs, out, 2)
        self.assertEqual(st, {"lines": 5, "suspects": 3, "chunks": 2})
        self.assertEqual(open(os.path.join(out, "chunk-0000.txt"), encoding="utf-8").read(), "那里有魚\n拼寫n\n")
        self.assertEqual(open(os.path.join(out, "chunk-0001.txt"), encoding="utf-8").read(), "第四步游戲\n")

    def test_apply_drops_the_flagged_sentences_and_keeps_the_order(self):
        flags = os.path.join(self.d, "flags")
        os.makedirs(flags)
        open(os.path.join(flags, "flags-0000.txt"), "w", encoding="utf-8").write("1\t1\t那里 有魚\n2\t4\t拼寫n\n9\t1\t抄錯的句子\n")   # an extra space still matches
        open(os.path.join(flags, "flags-0001.txt"), "w", encoding="utf-8").write("無\n")   # a reviewer's "none" line is not a flag
        st = self.run_quiet(R.apply, self.subs, flags, os.path.join(self.d, "out"))
        self.assertEqual((st["lines"], st["flagged_distinct"], st["unmatched_distinct"], st["kept"]), (5, 2, 1, 3))
        self.assertEqual(len(json.load(open(os.path.join(self.d, "out", "manifest.json")))["flags"]), 2)
        kept = open(os.path.join(self.d, "out", "subs.tsv"), encoding="utf-8").read().splitlines()
        self.assertEqual(kept, [self.ROWS[0], self.ROWS[2], self.ROWS[4]])



if __name__ == "__main__":
    unittest.main()
