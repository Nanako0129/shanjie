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

    def test_prefilter_chunks_only_suspects_with_line_numbers(self):
        out = os.path.join(self.d, "chunks")
        st = self.run_quiet(R.prefilter, self.subs, out, 2)
        self.assertEqual(st, {"lines": 5, "suspects": 3, "chunks": 2})
        self.assertEqual(open(os.path.join(out, "chunk-0000.txt"), encoding="utf-8").read(), "2\t那里有魚\n4\t拼寫n\n")
        self.assertEqual(open(os.path.join(out, "chunk-0001.txt"), encoding="utf-8").read(), "5\t第四步游戲\n")

    def test_apply_drops_the_flagged_lines_and_keeps_the_order(self):
        flags = os.path.join(self.d, "flags")
        os.makedirs(flags)
        open(os.path.join(flags, "flags-0000.txt"), "w").write("2\t1\n4\n")
        open(os.path.join(flags, "flags-0001.txt"), "w").write("無\n")              # a reviewer's "none" line is not a number
        st = self.run_quiet(R.apply, self.subs, flags, os.path.join(self.d, "out"))
        self.assertEqual((st["lines"], st["flagged"], st["kept"]), (5, 2, 3))
        kept = open(os.path.join(self.d, "out", "subs.tsv"), encoding="utf-8").read().splitlines()
        self.assertEqual(kept, [self.ROWS[0], self.ROWS[2], self.ROWS[4]])

    def test_a_line_number_outside_the_file_stops(self):
        flags = os.path.join(self.d, "flags")
        os.makedirs(flags)
        open(os.path.join(flags, "flags-0000.txt"), "w").write("9\n")
        with self.assertRaises(SystemExit):
            self.run_quiet(R.apply, self.subs, flags, os.path.join(self.d, "out"))


if __name__ == "__main__":
    unittest.main()
