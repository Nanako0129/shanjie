"""pack-oov (docs/contracts/pack-oov.md): the committed cross-language golden is the Python decoder's current output, and the Python
decoder scores an optional-pack word as unknown to the model and without the cap. core/tests/pack_oov.rs compares the file to Rust.
Run: python3 -B -m unittest tools/test_pack_oov.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "reference", "proto"))
import gen_pack_oov  # noqa: E402


class PackOov(unittest.TestCase):
    # Same rule as core/tests/pack_oov.rs: the golden's scores are byte-compared only on macOS, where they were generated;
    # Linux's libm differs in the last digits (seen in CI 2026-10-24). The other tests here compare first places and run everywhere.
    @unittest.skipIf(sys.platform.startswith("linux"), "scores are byte-compared on macOS only (libm last digits)")
    def test_golden_is_current(self):
        for name, b in gen_pack_oov.generate().items():
            self.assertEqual(open(os.path.join(gen_pack_oov.OUT, name), "rb").read(), b, name)

    def test_pack_changes_the_python_scores(self):
        """The golden is not vacuous: a pack set moves the scores of the same first name, and the pack word's lp is not capped."""
        rows = [l.split("\t") for l in gen_pack_oov.generate()["decode.tsv"].decode().splitlines()]
        top = {(r[0], r[1], r[2], r[3], r[4]): (r[6], r[7]) for r in rows if r[5] == "1"}
        none, a = top[("plain", "none", "<s>", "chat", "2")], top[("plain", "A", "<s>", "chat", "2")]
        self.assertEqual((none[0], a[0]), ("他占", "他占"))
        self.assertNotEqual(none[1], a[1])
        self.assertNotEqual(top[("plain", "none", "<s>", "chat", "2")], top[("plain", "B", "<s>", "chat", "2")])
        self.assertNotEqual(top[("plain", "B", "<s>", "chat", "2")], top[("plain", "B", "他", "chat", "2")])   # the start word 他 is a pack word: unknown


    def test_unforced_unseen_pack_words_skip_the_unseen_penalty(self):
        """Section 1b: an unforced pack word the model never saw keeps its score; other overlay words still lose the penalty."""
        import ime, lm as L
        lm = L.BigramLM(os.path.join(gen_pack_oov.KN, "bigram.sjlm"))
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "lexicon.txt")
            open(p, "w", encoding="utf-8").write(gen_pack_oov.lexicon_text() + "ㄒㄧㄣ 鑫 -2.0\n")
            self.assertEqual(lm.count("鑫"), 0)
            base = ime.Lexicon(p)
            plain = L.cap_overlay(base, {"鑫"}, lm, demote_path=None)
            exempt = L.cap_overlay(base, {"鑫"}, lm, demote_path=None, unseen_pack_words={"鑫"})
        self.assertEqual(plain.by_word["鑫"][1], -3.0)
        self.assertEqual(exempt.by_word["鑫"][1], -2.0)
        self.assertNotIn("鑫", exempt.pack, "unforced: not unknown-by-force, the model just never saw it")

if __name__ == "__main__":
    unittest.main()
