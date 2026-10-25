"""Tests for tools/build_overlay.py manual words (data/lexicon/overlay-manual.tsv) and their tail of overlay-add.tsv.
Synthetic files except the last test, which reads the committed lexicon files; no dumps, no network.
Run: python3 -m unittest tools/test_build_overlay.py   (from the repo root)."""
import contextlib, gzip, io, itertools, os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_overlay as B  # noqa: E402
import ime  # noqa: E402
import readings  # noqa: E402
import build_counts as bc  # noqa: E402

SCORES = {2: -7.0, 3: -7.04116568, 4: -6.6, 5: -6.17843815}
SYLLABLES = {"ㄐㄧㄚˇ", "ㄧˇ", "ㄅㄧㄥˇ", "ㄉㄧㄥ", "ㄨˋ", "ㄧ", "ㄧˋ", "ㄒㄧㄡ", "ㄍㄜ"}


def manual(text, taken=frozenset(), encoding="utf-8", tag="manual"):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "m.tsv")
        with open(p, "w", encoding=encoding) as f:
            f.write(text)
        return B.manual_rows(set(taken), SCORES, SYLLABLES, p, tag)


class ManualRows(unittest.TestCase):
    def test_rows_in_file_order_with_the_bonus_and_the_manual_tag(self):
        rows = manual("# comment\n\n甲乙丙\tㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ\tnote\n甲乙丙丁戊\tㄐㄧㄚˇ ㄧˇ ㄅㄧㄥˇ ㄉㄧㄥ ㄨˋ\tfive characters\n")
        self.assertEqual(rows, [f"ㄐㄧㄚˇ-ㄧˇ-ㄅㄧㄥˇ\t甲乙丙\t{round(SCORES[3] + B.MANUAL_BONUS, 8)!r}\tmanual\n",
                                f"ㄐㄧㄚˇ-ㄧˇ-ㄅㄧㄥˇ-ㄉㄧㄥ-ㄨˋ\t甲乙丙丁戊\t{round(SCORES[5] + B.MANUAL_BONUS, 8)!r}\tmanual\n"])

    def test_a_fourth_column_adds_to_the_score_of_slang_rows_only(self):
        self.assertEqual(manual("甲乙\tㄐㄧㄚˇ ㄧˇ\tx\t1.5\n", tag="slang"), [f"ㄐㄧㄚˇ-ㄧˇ\t甲乙\t{round(SCORES[2] + B.MANUAL_BONUS + 1.5, 8)!r}\tslang\n"])
        for bad in ("more", "nan", "inf", "-0.5", "0", "0.3", "3.5", "15"):        # 0.5 steps, 0 < boost <= 3
            with self.assertRaises(SystemExit, msg=bad):
                manual(f"甲乙\tㄐㄧㄚˇ ㄧˇ\tx\t{bad}\n", tag="slang")
        with self.assertRaises(SystemExit):                                   # manual words take no boost
            manual("甲乙\tㄐㄧㄚˇ ㄧˇ\tx\t1.5\n")

    def test_a_boost_only_goes_on_the_first_slang_word_of_a_reading(self):
        rows = manual("甲乙\tㄐㄧㄚˇ ㄧˇ\tx\n假以\tㄐㄧㄚˇ ㄧˇ\ty\t1.0\n", tag="slang")
        with self.assertRaises(SystemExit):
            B.slang_behind(rows, {"假以"})
        first = B.slang_behind(manual("甲乙\tㄐㄧㄚˇ ㄧˇ\tx\t1.0\n假以\tㄐㄧㄚˇ ㄧˇ\ty\n", tag="slang"), {"甲乙"})
        self.assertGreater(float(first[0].split("\t")[2]), float(first[1].split("\t")[2]))   # the first stays first

    def test_a_bom_is_not_part_of_the_first_line(self):
        self.assertEqual(len(manual("# comment\n甲乙\tㄐㄧㄚˇ ㄧˇ\tx\n", encoding="utf-8-sig")), 1)

    def test_bad_rows_stop_the_build(self):
        bad = {
            "a word already in the lexicon": ("甲乙\tㄐㄧㄚˇ ㄧˇ\tx\n", {"甲乙"}),
            "syllable count": ("甲乙\tㄐㄧㄚˇ\tx\n", ()),
            "unknown syllable": ("甲乙\tjia3 ㄧˇ\tx\n", ()),
            "not Han": ("ab\tㄐㄧㄚˇ ㄧˇ\tx\n", ()),
            "one character": ("甲\tㄐㄧㄚˇ\tx\n", ()),
            "two fields": ("甲乙\tㄐㄧㄚˇ ㄧˇ\n", ()),
            "the same row twice": ("甲乙\tㄐㄧㄚˇ ㄧˇ\tx\n甲乙\tㄐㄧㄚˇ ㄧˇ\ty\n", ()),
            # 一 before a first tone gets a sandhi row ㄧˋ automatically, so writing ㄧˋ by hand repeats it
            "a reading the sandhi row already gives": ("一休哥\tㄧ ㄒㄧㄡ ㄍㄜ\tx\n一休哥\tㄧˋ ㄒㄧㄡ ㄍㄜ\ty\n", ()),
        }
        for why, (text, taken) in bad.items():
            with self.assertRaises(SystemExit, msg=why):
                manual(text, taken)


class MoedictSupplement(unittest.TestCase):
    """SJ-LM-7 contract section 2.1a: the source 萌典補詞."""
    MOE = {"甲乙": ("ㄐㄧㄚˇ", "ㄧˇ"), "丙丁": ("ㄅㄧㄥˇ", "ㄉㄧㄥ"), "戊己": ("ㄨˋ", "ㄍㄜ"), "庚辛": ("jia3", "ㄧˇ"), "壬癸": ("ㄨˋ", "ㄍㄜ")}

    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); self.addCleanup(self.d.cleanup)

    def corpus(self, lines_a, lines_b=()):
        a, b = os.path.join(self.d.name, "a.txt"), os.path.join(self.d.name, "b.txt.gz")
        open(a, "w", encoding="utf-8").write("".join(l + "\n" for l in lines_a))
        with gzip.open(b, "wt", encoding="utf-8") as f:
            f.write("".join(l + "\n" for l in lines_b))
        return [a, b]

    def rows(self, corpus, taken=(), jobs=1):
        return B.supplement_rows(self.MOE, set(taken), corpus, SCORES, SYLLABLES, jobs)

    def test_two_occurrences_in_one_out_a_pack_string_out_and_the_score_is_length_scores(self):
        corpus = self.corpus(["甲乙在這裡", "丙丁只有一次", "戊己戊己", "庚辛庚辛", "壬癸壬癸"], ["另一個檔案裡的甲乙"])
        rows = self.rows(corpus, taken={"壬癸"})      # 壬癸 is a pack string
        # 甲乙: once in each file = 2 (in); 丙丁: once (out); 戊己: twice, overlapping counts are fine (in); 庚辛: unknown syllable (out)
        self.assertEqual(rows, [f"ㄨˋ-ㄍㄜ\t戊己\t{SCORES[2]!r}\t萌典補詞\n", f"ㄐㄧㄚˇ-ㄧˇ\t甲乙\t{SCORES[2]!r}\t萌典補詞\n"])
        self.assertTrue(all(r.split("\t")[2] == repr(SCORES[2]) for r in rows))    # no manual-word bonus
        self.assertNotIn(B.MANUAL_BONUS, [SCORES[2] - float(r.split("\t")[2]) for r in rows])

    def test_words_already_in_a_layer_or_too_long_for_the_scores_never_enter(self):
        corpus = self.corpus(["甲乙甲乙"])
        self.assertEqual(self.rows(corpus, taken={"甲乙"}), [])
        self.assertEqual(B.supplement_rows({"甲乙": ("ㄐㄧㄚˇ", "ㄧˇ")}, set(), corpus, {3: -7.0}, SYLLABLES), [])

    def test_the_count_is_the_same_with_several_processes(self):
        lines = ["甲乙丙丁戊己" * 3] * 30
        corpus = self.corpus(lines, lines)
        cand = {"甲乙", "丙丁", "乙丙", "己甲"}
        self.assertEqual(B.count_substrings(corpus, cand, 1), B.count_substrings(corpus, cand, 2))
        self.assertEqual(B.count_substrings(corpus, cand)["甲乙"], 180)
        self.assertEqual(B.count_substrings(corpus, cand)["己甲"], 120)      # across a repeat inside the line, not across lines

    def test_names_and_every_pack_are_taken_layers(self):
        packs = os.path.join(self.d.name, "packs"); os.makedirs(packs)
        for n, w in (("acg-add.tsv", "甲乙"), ("it-add.tsv", "丙丁"), ("notes.tsv", "戊己")):
            open(os.path.join(packs, n), "w", encoding="utf-8").write(f"ㄅ\t{w}\t-7.0\tx\n")
        names = os.path.join(self.d.name, "names-add.tsv")
        open(names, "w", encoding="utf-8").write("ㄅ\t庚辛\t-7.0\tnames\n")
        with mock.patch.object(B, "PACKS", packs), mock.patch.object(B, "NAMES", names):
            self.assertEqual(B.other_layers(), {"甲乙", "丙丁", "庚辛"})                # *-add.tsv only, as the engine loads them


class BuildFlags(unittest.TestCase):
    """build() on synthetic dumps over the committed base: the default output is the old method's, the new flags change only what they say."""
    @classmethod
    def setUpClass(cls):
        cls.base = ime.Lexicon(B.BASE)
        chars = sorted(w for w in cls.base.by_word if B.MANUAL_WORD.match(w) and len(w) == 1 and w not in "一不和")[:300]
        taken = B.other_layers() | B.manual_words()
        pairs = (a + b for a, b in itertools.product(chars, chars) if a + b not in cls.base.by_word and a + b not in taken)
        # two wiktionary titles (their syllables differ pairwise so a reversed reading is a different reading) and one supplement word
        picked = [w for w in itertools.islice(pairs, 200) if len(set(cls.base.to_syllables(w))) == 2]
        cls.title, cls.other, cls.sup = picked[0], picked[1], picked[2]

    check, gap = (), ()

    def build(self, *args, extra_moe=None, extra_corpus=(), simp=(), **kw):
        d = tempfile.mkdtemp(); self.addCleanup(__import__("shutil").rmtree, d)
        wikt = os.path.join(d, "t.gz")
        with gzip.open(wikt, "wt", encoding="utf-8") as f:
            f.write(f"{self.title}\n{self.other}\n")
        empty = os.path.join(d, "e.gz")
        gzip.open(empty, "wt").close()
        corpus = os.path.join(d, "c.txt")
        open(corpus, "w", encoding="utf-8").write(f"{self.sup}和{self.sup}\n{self.other}\n" + "".join(f"{w}和{w}\n" for w in extra_corpus))
        moe = {self.title: tuple(reversed(self.base.to_syllables(self.title))), self.sup: tuple(self.base.to_syllables(self.sup)), **(extra_moe or {})}
        stub = mock.Mock(); stub.read = lambda w: (list(self.base.to_syllables(w)), ["unsure"] if w in self.check else [], ["gap"] if w in self.gap else [])
        patches = [mock.patch.object(B, "fetch", lambda name: wikt if name == "enwiktionary" else empty), mock.patch.object(bc, "load_conv", lambda: None),
                   mock.patch.object(B, "moe_titles", lambda: set()), mock.patch.object(readings, "moe_primary", lambda: moe),
                   mock.patch.object(readings, "make_reader", lambda lex, **kw: stub), mock.patch.object(bc, "TRAD_SIMP_ONLY", set(simp))]
        with contextlib.ExitStack() as st, contextlib.redirect_stdout(io.StringIO()):
            for p in patches:
                st.enter_context(p)
            return B.build(*args, supplement=[corpus] if kw.get("sup") else None, reading_report=kw.get("report") and os.path.join(d, "r.tsv")), d

    def row(self, rows, w):
        return [r for r in rows if r.split("\t")[1] == w]

    def test_default_is_the_old_method(self):
        (rows, _, _), _ = self.build()
        want = B.normalize(self.title, self.base.to_syllables(self.title))
        self.assertEqual(self.row(rows, self.title), [f"{'-'.join(want)}\t{self.title}\t{B.SCORE[2]!r}\twikt\n"])
        self.assertFalse([r for r in rows if r.endswith("萌典補詞\n")])
        tags = [r.rstrip("\n").split("\t")[3] for r in rows]
        tail = tags[tags.index("manual"):]
        self.assertEqual(tail, sorted(tail, key=("manual", "slang").index))   # the tail is the manual rows, then the slang rows (docs/contracts/net-slang.md)

    def test_moedict_readings_and_report(self):
        (rows, _, _), d = self.build(True, report=True)
        old = B.normalize(self.title, self.base.to_syllables(self.title))
        new = B.normalize(self.title, tuple(reversed(self.base.to_syllables(self.title))))
        self.assertEqual(self.row(rows, self.title)[0].split("\t")[0], "-".join(new))
        self.assertEqual(self.row(rows, self.other)[0].split("\t")[0], "-".join(B.normalize(self.other, self.base.to_syllables(self.other))))   # not in Moedict: readings.py
        report = open(os.path.join(d, "r.tsv"), encoding="utf-8").read().splitlines()
        self.assertEqual(report[1:], [f"{self.title}\t{' '.join(old)}\t{' '.join(new)}\tmoedict"])

    def test_check_and_gap_words_from_readings_py_are_left_out_and_listed(self):
        for kind in ("CHECK", "GAP"):
            self.check, self.gap = ({self.other}, ()) if kind == "CHECK" else ((), {self.other})
            (rows, _, _), d = self.build(True, report=True)
            self.assertNotIn(self.other, {r.split("\t")[1] for r in rows})           # not written, whatever its reading
            self.assertIn(self.title, {r.split("\t")[1] for r in rows})              # a Moedict word never goes through readings.py
            report = open(os.path.join(d, "r.tsv"), encoding="utf-8").read().splitlines()
            self.assertIn(kind, {l.split("\t")[3] for l in report[1:] if l.split("\t")[0] == self.other})
        self.check = self.gap = ()
        (rows, _, _), _ = self.build()                                              # without the flag nothing is left out
        self.assertIn(self.other, {r.split("\t")[1] for r in rows})

    def test_supplement_words_pass_the_same_filters_as_every_other_candidate(self):
        base = self.base
        tai = next(w for w in sorted(base.by_word) if len(w) == 2 and "台" in w and B.MANUAL_WORD.match(w) and w.replace("台", "臺") not in base.by_word)
        variant = tai.replace("台", "臺")                                       # 臺/台 variant of a base word with the same reading: the twin rule drops it
        syls = base.by_word[tai][0]
        unknown = next(chr(c) for c in range(0x4E00, 0xA000) if chr(c) not in base.by_word)    # a Han character that is not a base character
        offbase = unknown + self.sup[1]
        bad = self.sup[0]                                                      # declared simplified-only below; the supplement word itself is the trap
        extra = {variant: syls, offbase: syls}
        corpus = [variant, offbase]
        (rows, _removed, variant_removed), _ = self.build(sup=True, extra_moe=extra, extra_corpus=corpus)
        words = {r.split("\t")[1] for r in rows}
        self.assertIn(self.sup, words)                                           # the control: an ordinary supplement word is written
        self.assertNotIn(variant, words)
        self.assertEqual([l.split("\t")[:2] for l in variant_removed if l.startswith(variant)], [[variant, tai]])    # recorded like any removed variant
        self.assertNotIn(offbase, words)
        (rows, _, _), _ = self.build(sup=True, simp={bad})
        self.assertNotIn(self.sup, {r.split("\t")[1] for r in rows})            # a simplified-only character keeps a Moedict word out too

    def test_supplement_rows_sit_between_the_generated_rows_and_the_manual_tail(self):
        (rows, _, _), _ = self.build(sup=True)
        sup = [i for i, r in enumerate(rows) if r.endswith("萌典補詞\n")]
        self.assertEqual([rows[i].split("\t")[1] for i in sup], [self.sup])
        tags = [r.rstrip("\n").split("\t")[3] for r in rows]
        self.assertTrue(tags.index("萌典補詞") > max(i for i, t in enumerate(tags) if t == "wikt") and tags.index("萌典補詞") < tags.index("manual"))


class Cantonese(unittest.TestCase):
    def test_rule(self):
        headwords = {"凝睇"}
        self.assertTrue(B.cantonese("冇陰公", headwords))
        self.assertTrue(B.cantonese("唔該", headwords))
        self.assertTrue(B.cantonese("買餸", headwords), "a character the review added")
        self.assertFalse(B.cantonese("凝睇", headwords), "dictionary headwords stay")
        for w in ("俾斯麥", "羊咩咩", "口眼喎斜", "好嘞", "沒問題"):
            self.assertFalse(B.cantonese(w, headwords), f"{w}: no Cantonese-only character")

    def test_the_shipped_overlay_keeps_only_these_cantonese_character_words(self):
        """The rule's observed result on the committed overlay (the build reads the MOE dictionary, which CI does not have): the
        non-manual words with a CANTO_CHARS character are exactly these dictionary headwords. Regenerating with another rule or
        dictionary changes this set and has to update it here on purpose."""
        with open(B.OUT, encoding="utf-8") as f:
            words = {l.split("\t")[1] for l in f if l.rstrip("\n").split("\t")[3] not in ("manual", "slang")}
        self.assertEqual({w for w in words if not B.CANTO_CHARS.isdisjoint(w)},
                         {"付畀", "凝睇", "咿唔", "唔篤", "噏呷", "投畀豺虎", "曼睩", "睇眄", "迎睇"})


class ShippedOverlay(unittest.TestCase):
    def test_the_tails_are_what_tail_rows_makes_today(self):
        """Without the dumps: overlay-add.tsv ends with the `manual` rows, then the `slang` rows (docs/contracts/net-slang.md), and the
        tail equals tail_rows() over the committed overlay-manual.tsv and slang-add.tsv, so editing one file without regenerating fails here."""
        with open(B.OUT, encoding="utf-8") as f:
            lines = f.readlines()
        tags = [l.rstrip("\n").split("\t")[3] for l in lines]
        tail = next((i for i, t in enumerate(tags) if t in ("manual", "slang")), len(lines))
        self.assertEqual(tags[tail:], sorted(tags[tail:], key=("manual", "slang").index), "manual rows, then slang rows, are the tail")
        base = ime.Lexicon(B.BASE)
        taken = set(base.by_word) | {l.split("\t")[1] for l in lines[:tail]}
        self.assertEqual(lines[tail:], B.tail_rows(taken, B.length_scores(base), B.base_syllables(base)))
        self.assertEqual(lines, B.rebuild_tails())

    def test_no_slang_file_leaves_the_manual_tail_alone(self):
        base = ime.Lexicon(B.BASE)
        args = (set(base.by_word), B.length_scores(base), B.base_syllables(base))
        with tempfile.TemporaryDirectory() as d:
            without = B.tail_rows(*args, slang=os.path.join(d, "absent.tsv"))
        self.assertEqual(without, B.manual_rows(args[0] | B.pack_words() | B.pack_words(B.IT_PACK) | B.names_words(), *args[1:]))
        self.assertNotIn("slang", {r.rstrip("\n").split("\t")[3] for r in without})


class SlangBehind(unittest.TestCase):
    def test_later_words_on_a_reading_drop_by_their_rank(self):
        rows = ["a\t甲\t-5.0\tslang\n", "a\t乙\t-5.0\tslang\n", "b\t丙\t-5.0\tslang\n", "a\t丁\t-5.0\tslang\n"]
        got = [float(r.split("\t")[2]) for r in B.slang_behind(rows)]
        self.assertEqual(got, [-5.0, -5.0 - B.SLANG_BEHIND, -5.0, -5.0 - 2 * B.SLANG_BEHIND])

if __name__ == "__main__":
    unittest.main()
