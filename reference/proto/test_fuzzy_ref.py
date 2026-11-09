"""fuzzy_ref.py against core/src/fuzzy.rs and core/src/engine.rs: the constants are read from the Rust source text and compared item by item,
the tables are checked with the examples of the Rust unit tests. Run: python3 -W ignore -m unittest reference/proto/test_fuzzy_ref.py"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import fuzzy_ref as F  # noqa: E402


def rust(name):
    with open(os.path.join(ROOT, "core", "src", name), encoding="utf-8") as f:
        return f.read()


def const_str(src, name):
    m = re.search(r'const %s: &str = "([^"]*)"' % name, src)
    assert m, f"no `const {name}: &str` in the Rust source: update this test and fuzzy_ref.py"
    return m.group(1)


class Constants(unittest.TestCase):
    def test_keys_and_symbols(self):
        eng = rust("engine.rs")
        self.assertEqual(const_str(eng, "SYMBOLS"), F.SYMBOLS)
        self.assertEqual(const_str(eng, "KEYS_STANDARD"), F.KEYS_STANDARD)

    def test_pairs_rows_and_bits(self):
        src = rust("fuzzy.rs")
        m = re.search(r"pub const FUZZY_PAIRS: \[\(char, char\); 4\] = \[(.*?)\];", src)
        self.assertEqual(re.findall(r"\('(.)', '(.)'\)", m.group(1)), F.FUZZY_PAIRS)
        m = re.search(r"const ROWS: \[&str; 4\] = \[(.*?)\];", src)
        self.assertEqual(re.findall(r'"([^"]*)"', m.group(1)), F.ROWS)
        self.assertEqual(int(re.search(r"pub const FUZZY_SOUND: u32 = (0b[01]+)", src).group(1), 2), F.FUZZY_SOUND)

    def test_neighbours_table(self):
        """Every symbol key's neighbours, computed again from the Rust ROWS and the Rust key-to-symbol map."""
        src = rust("fuzzy.rs")
        rows = re.findall(r'"([^"]*)"', re.search(r"const ROWS: \[&str; 4\] = \[(.*?)\];", src).group(1))
        sym = dict(zip(const_str(rust("engine.rs"), "KEYS_STANDARD"), const_str(rust("engine.rs"), "SYMBOLS")))
        want = {}
        for row in rows:
            for i, k in enumerate(row):
                if k in sym:
                    want[sym[k]] = [sym[row[j]] for j in (i - 1, i + 1) if 0 <= j < len(row) and row[j] in sym]
        self.assertEqual(F.NEIGHBOURS, want)
        self.assertEqual(len(want), 37)
        self.assertEqual(F.NEIGHBOURS["ㄉ"], ["ㄅ"], "the tone key 3 is dropped")
        self.assertEqual(F.NEIGHBOURS["ㄊ"], ["ㄆ", "ㄍ"])
        self.assertEqual(F.NEIGHBOURS["ㄦ"], ["ㄢ"])

    def test_compose_columns(self):
        self.assertEqual(F.compose("ㄇㄥ"), "ㄇㄥ")
        self.assertEqual(F.compose("ㄇㄛㄥ"), "ㄇㄥ", "the final column takes ㄥ over ㄛ")
        self.assertEqual(F.compose("ㄗㄧㄥ"), "ㄗㄧㄥ")
        self.assertEqual(F.compose("ㄗㄇㄥ"), "ㄇㄥ", "a second initial replaces the first")


class Tables(unittest.TestCase):
    """The examples of the unit tests in core/src/fuzzy.rs."""

    def test_sound_pairs_by_bit(self):
        syls = ["ㄇㄣ", "ㄇㄥ", "ㄓ", "ㄗ", "ㄔ", "ㄘ", "ㄕ", "ㄙ", "ㄓㄣˊ", "ㄗㄥˊ", "ㄓㄥˊ", "ㄗㄣˊ", "ㄇㄚ"]
        t = F.Tolerance(syls, syls)
        for bit, (a, b) in enumerate([("ㄇㄣ", "ㄇㄥ"), ("ㄓ", "ㄗ"), ("ㄔ", "ㄘ"), ("ㄕ", "ㄙ")]):
            self.assertEqual(t.sound(a, 1 << bit), [b])
            self.assertEqual(t.sound(b, 1 << bit), [a])
            for other in (o for o in range(4) if o != bit):
                self.assertEqual(t.sound(a, 1 << other), [])
        self.assertEqual(t.sound("ㄇㄣ", 0), [])
        self.assertEqual(t.sound("ㄇㄚ", 0b1111), [])
        self.assertEqual(t.sound("ㄅㄩ", 0b1111), [])
        self.assertEqual(t.sound("ㄓㄣˊ", 0b01), ["ㄓㄥˊ"])
        self.assertEqual(t.sound("ㄓㄣˊ", 0b10), ["ㄗㄣˊ"])
        self.assertEqual(sorted(t.sound("ㄓㄣˊ", 0b11)), sorted(["ㄓㄥˊ", "ㄗㄣˊ", "ㄗㄥˊ"]))
        self.assertEqual(t.sound("ㄓㄣˊ", (1 << 4) | 0b01), ["ㄓㄥˊ"], "bits above the sound pairs are not part of the lookup")

    def test_adjacent_follows_the_composer_columns(self):
        syls = ["ㄇㄧㄥˊ", "ㄇㄥˊ", "ㄗㄥˊ", "ㄅㄚ", "ㄉㄚ", "ㄊㄚ", "ㄆㄚ", "ㄍㄚ"]
        t = F.Tolerance(syls, syls)
        self.assertEqual(t.adjacent("ㄇㄥˊ"), ["ㄇㄧㄥˊ"])
        self.assertEqual(t.adjacent("ㄗㄥˊ"), ["ㄇㄧㄥˊ"])
        self.assertEqual(t.adjacent("ㄇㄥ"), [])
        self.assertEqual(t.adjacent("ㄉㄚ"), ["ㄅㄚ"])
        self.assertEqual(t.adjacent("ㄅㄚ"), ["ㄉㄚ"])
        self.assertEqual(t.adjacent("ㄆㄚ"), ["ㄊㄚ"])
        self.assertEqual(t.adjacent("ㄍㄚ"), ["ㄊㄚ"])
        self.assertEqual(t.adjacent("ㄏㄚ"), [])

    def test_only_single_syllable_entries_are_adjacent_alternatives(self):
        t = F.Tolerance(["ㄅㄚ", "ㄉㄚ"], ["ㄅㄚ"])
        self.assertEqual(t.adjacent("ㄉㄚ"), ["ㄅㄚ"])
        self.assertEqual(t.adjacent("ㄅㄚ"), [])


if __name__ == "__main__":
    unittest.main()
