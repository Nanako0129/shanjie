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


if __name__ == "__main__":
    unittest.main()
