"""fuzzy_ref.py against core/src/fuzzy.rs and core/src/engine.rs: the constants are read from the Rust source text and compared item by item,
the tables are checked with the examples of the Rust unit tests. Run: python3 -W ignore -m unittest reference/proto/test_fuzzy_ref.py"""
import hashlib
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
        self.assertEqual(int(re.search(r"pub const FUZZY_ADJACENT: u32 = 1 << (\d+);", src).group(1)), F.FUZZY_ADJACENT.bit_length() - 1)
        self.assertEqual(int(re.search(r"pub const FUZZY_ONE_SYM: u32 = 1 << (\d+);", src).group(1)), F.FUZZY_ONE_SYM.bit_length() - 1)
        self.assertIn("pub const FUZZY_ALL: u32 = FUZZY_SOUND | FUZZY_ADJACENT | FUZZY_ONE_SYM;", src)
        self.assertEqual(F.FUZZY_ALL, 0b11_1111)
        self.assertEqual(float(re.search(r"pub const DELTA_ONE_SYM: f64 = ([0-9.]+);", src).group(1)), F.DELTA_ONE_SYM)
        self.assertEqual(int(re.search(r"pub const ONE_SYM_SINGLE_TOTAL: usize = (\d+);", src).group(1)), 24)

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

    def test_one_sym_replaces_a_symbol_within_its_column(self):
        """The examples of `one_symbol_replaces_within_the_column` in core/src/fuzzy.rs."""
        syls = ["ㄒㄧㄚˇ", "ㄒㄧㄤˇ", "ㄒㄧㄝˇ", "ㄒㄧㄠˇ", "ㄒㄧㄡˇ", "ㄒㄧㄢˇ", "ㄒㄧㄣˇ", "ㄒㄧㄥˇ", "ㄅㄧ", "ㄅㄨ", "ㄅㄚ", "ㄉㄚ"]
        t = F.Tolerance(syls, syls)
        self.assertEqual(t.one_sym("ㄒㄧㄟˇ"), sorted(["ㄒㄧㄚˇ", "ㄒㄧㄤˇ", "ㄒㄧㄝˇ", "ㄒㄧㄠˇ", "ㄒㄧㄡˇ", "ㄒㄧㄢˇ", "ㄒㄧㄣˇ", "ㄒㄧㄥˇ"]))
        self.assertEqual(t.one_sym("ㄅㄩ"), ["ㄅㄧ", "ㄅㄨ"], "the medial column only; no final is added")
        self.assertEqual(t.one_sym("ㄒㄧㄤˇ"), [], "a syllable the lexicon has gets no entry")
        self.assertEqual(t.one_sym("ㄅㄧㄚ"), [], "no column is added")
        self.assertEqual(t.one_sym("ㄆㄚ"), ["ㄅㄚ", "ㄉㄚ"], "any other initial, not just a neighbour")
        t = F.Tolerance(["ㄅㄚ", "ㄉㄚ"], ["ㄅㄚ"])
        self.assertEqual(t.one_sym("ㄆㄚ"), ["ㄅㄚ"], "only syllables with a one-syllable entry are alternatives")

def shipped_lexicon_syllables():
    """(all syllables, syllables with a one-syllable entry) of the lexicon `load_lexicon` builds from data/lexicon (base, overlay, sandhi and names rows,
    no packs), read with the row filters of `Lexicon::parse_with` in core/src/lib.rs."""
    allsyl, single = set(), set()

    def add(reading):
        syls = reading.split("-")
        allsyl.update(syls)
        if len(syls) == 1:
            single.add(syls[0])

    d = os.path.join(ROOT, "data", "lexicon")
    with open(os.path.join(d, "mcbpmf-data.txt"), encoding="utf-8") as f:
        for line in f:
            if line[:1] in ("#", "_"):
                continue
            t = line.split()
            if len(t) == 3 and len(t[1]) == len(t[0].split("-")):
                add(t[0])
    for name in ("overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv"):
        with open(os.path.join(d, name), encoding="utf-8") as f:
            for line in f.read().splitlines():
                row = line.split("\t")
                if row[3] != "colloq":  # colloquial rows (tone3::COLLOQ_TAG) are kept apart and intern no syllable of the lexicon
                    add(row[0])
    return allsyl, single


def digest_one_sym(t):
    """(entries, SHA-256) of `unknown<TAB>alt1,alt2,...` lines sorted by UTF-8 bytes: the text `one_sym_digest` in core/src/fuzzy.rs hashes."""
    lines = sorted(f"{k}\t{','.join(v)}\n".encode("utf-8") for k, v in t.one_sym_table.items())
    return len(lines), hashlib.sha256(b"".join(lines)).hexdigest()


class ShippedLexicon(unittest.TestCase):
    def test_one_sym_table_of_the_shipped_lexicon_matches_the_rust_digest(self):
        """`one_symbol_table_of_the_shipped_lexicon_has_the_pinned_digest` in core/src/fuzzy.rs asserts the same constant (docs/spec/decoder.md §6.2)."""
        allsyl, single = shipped_lexicon_syllables()
        self.assertEqual(digest_one_sym(F.Tolerance(allsyl, single)), ONE_SYM_DIGEST)


ONE_SYM_DIGEST = (3413, "082a805f3e2011d948d2109b4260ec99fca2fe42acd1d8915f40518a4ae119ef")


if __name__ == "__main__":
    unittest.main()
