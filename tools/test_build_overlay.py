"""Tests for tools/build_overlay.py manual words (data/lexicon/overlay-manual.tsv) and their tail of overlay-add.tsv.
Synthetic files except the last test, which reads the committed lexicon files; no dumps, no network.
Run: python3 -m unittest tools/test_build_overlay.py   (from the repo root)."""
import contextlib, gzip, io, itertools, os, sys, tempfile, unittest, zipfile
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
        cls.words = picked[3:]                      # more candidates for the 2.3 revision-4 cases (each has two different syllables, none is in a layer)
        cls.chars = chars
        # a word with 不 that gets an automatic sandhi row (不 before a fourth tone), a word ending in 罷 (several non-neutral readings, one neutral)
        cls.bu = next(w for w in ("不" + c for c in chars) if w not in cls.base.by_word and w not in taken and cls.base.to_syllables(w)
                      and B.sandhi_variant(w, B.normalize(w, cls.base.to_syllables(w))))
        cls.ba = next(w for w in (c + "罷" for c in chars) if w not in cls.base.by_word and w not in taken and cls.base.to_syllables(w))

    check, gap = (), ()

    def build(self, *args, extra_moe=None, extra_corpus=(), simp=(), titles=(), concised=None, jp=(), tones=None, **kw):
        """concised: {word: B.Concised} for the mocked load_concised; jp: the Japanese-form drop set; tones: a replacement for B.base_tones."""
        d = tempfile.mkdtemp(); self.addCleanup(__import__("shutil").rmtree, d)
        wikt = os.path.join(d, "t.gz")
        with gzip.open(wikt, "wt", encoding="utf-8") as f:
            f.write(f"{self.title}\n{self.other}\n" + "".join(f"{t}\n" for t in titles))
        empty = os.path.join(d, "e.gz")
        gzip.open(empty, "wt").close()
        corpus = os.path.join(d, "c.txt")
        open(corpus, "w", encoding="utf-8").write(f"{self.sup}和{self.sup}\n{self.other}\n" + "".join(f"{w}和{w}\n" for w in extra_corpus))
        moe = {self.title: tuple(reversed(self.base.to_syllables(self.title))), self.sup: tuple(self.base.to_syllables(self.sup)), **(extra_moe or {})}
        unsure = lambda w: w in self.check or w in self.gap                       # an unsure word gets a different tentative reading (reversed)
        stub = mock.Mock(); stub.read = lambda w: ((list(reversed(self.base.to_syllables(w))) if unsure(w) else list(self.base.to_syllables(w))),
                                                   ["unsure"] if w in self.check else [], ["gap"] if w in self.gap else [])
        patches = [mock.patch.object(B, "fetch", lambda name: wikt if name == "enwiktionary" else empty), mock.patch.object(bc, "load_conv", lambda: None),
                   mock.patch.object(B, "moe_titles", lambda: set()), mock.patch.object(readings, "moe_primary", lambda: moe),
                   mock.patch.object(readings, "make_reader", lambda lex, **kw: stub), mock.patch.object(bc, "TRAD_SIMP_ONLY", set(simp)),
                   mock.patch.object(B, "load_concised", lambda: concised or {}), mock.patch.object(B, "jp_drop_set", lambda chars, path=None: set(jp))]
        if tones is not None:
            patches.append(mock.patch.object(B, "base_tones", lambda *a: tones))
        with contextlib.ExitStack() as st, contextlib.redirect_stdout(io.StringIO()):
            for p in patches:
                st.enter_context(p)
            return B.build(*args, supplement=[corpus] if kw.get("sup") else None, reading_report=kw.get("report") and os.path.join(d, "r.tsv")), d

    def row(self, rows, w):
        return [r for r in rows if r.split("\t")[1] == w]

    def test_default_is_the_old_method(self):
        (rows, _, _, jp_removed), _ = self.build(jp={self.title[0]})        # the drop set is not even read without --moedict-readings
        self.assertEqual(jp_removed, [])
        want = B.normalize(self.title, self.base.to_syllables(self.title))
        self.assertEqual(self.row(rows, self.title), [f"{'-'.join(want)}\t{self.title}\t{B.SCORE[2]!r}\twikt\n"])
        self.assertFalse([r for r in rows if r.endswith("萌典補詞\n")])
        self.assertTrue(self.row(rows, self.other))                              # a word outside Moedict is written too
        tags = [r.rstrip("\n").split("\t")[3] for r in rows]
        tail = tags[tags.index("manual"):]
        self.assertEqual(tail, sorted(tail, key=("manual", "slang").index))   # the tail is the manual rows, then the slang rows (docs/contracts/net-slang.md)

    def test_moedict_readings_and_report(self):
        (rows, _, _, _), d = self.build(True, report=True)
        old = B.normalize(self.title, self.base.to_syllables(self.title))
        new = B.normalize(self.title, tuple(reversed(self.base.to_syllables(self.title))))
        self.assertEqual(self.row(rows, self.title)[0].split("\t")[0], "-".join(new))
        self.assertEqual(self.row(rows, self.other)[0].split("\t")[0], "-".join(B.normalize(self.other, self.base.to_syllables(self.other))))   # not in Moedict: readings.py
        report = open(os.path.join(d, "r.tsv"), encoding="utf-8").read().splitlines()
        self.assertEqual(report[1:], [f"{self.title}\t{' '.join(old)}\t{' '.join(new)}\tmoedict"])

    def test_check_and_gap_words_from_readings_py_keep_their_reading_and_are_listed(self):
        old = "-".join(B.normalize(self.other, self.base.to_syllables(self.other)))
        for kind in ("CHECK", "GAP"):
            self.check, self.gap = ({self.other}, ()) if kind == "CHECK" else ((), {self.other})
            (rows, _, _, _), d = self.build(True, report=True)
            self.assertEqual([r.split("\t")[0] for r in self.row(rows, self.other)], [old])   # still written, with the reading it has today
            self.assertIn(self.title, {r.split("\t")[1] for r in rows})              # a Moedict word never goes through readings.py
            report = open(os.path.join(d, "r.tsv"), encoding="utf-8").read().splitlines()
            self.assertIn(f"{kind} (kept)", {l.split("\t")[3] for l in report[1:] if l.split("\t")[0] == self.other})
        self.check = self.gap = ()

    def test_a_variant_is_removed_by_its_old_reading_even_when_moedict_gives_another(self):
        base = self.base
        tai = next(w for w in sorted(base.by_word) if len(w) == 2 and "台" in w and B.MANUAL_WORD.match(w) and w.replace("台", "臺") not in base.by_word
                   and len(set(base.to_syllables(w))) == 2)
        variant = tai.replace("台", "臺")
        self.title, saved = variant, self.title                                 # the wiktionary title is the variant; Moedict reverses its reading
        try:
            (rows, _, vrem, _), _ = self.build(True)
        finally:
            self.title = saved
        self.assertNotIn(variant, {r.split("\t")[1] for r in rows})
        self.assertIn(variant, {l.split("\t")[0] for l in vrem})

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
        (rows, _removed, variant_removed, _), _ = self.build(sup=True, extra_moe=extra, extra_corpus=corpus)
        words = {r.split("\t")[1] for r in rows}
        self.assertIn(self.sup, words)                                           # the control: an ordinary supplement word is written
        self.assertNotIn(variant, words)
        self.assertEqual([l.split("\t")[:2] for l in variant_removed if l.startswith(variant)], [[variant, tai]])    # recorded like any removed variant
        self.assertNotIn(offbase, words)
        (rows, _, _, _), _ = self.build(sup=True, simp={bad})
        self.assertNotIn(self.sup, {r.split("\t")[1] for r in rows})            # a simplified-only character keeps a Moedict word out too

    def test_supplement_skips_a_net_slang_word(self):
        d = tempfile.mkdtemp(); self.addCleanup(__import__("shutil").rmtree, d)
        slang = os.path.join(d, "slang-add.tsv")
        open(slang, "w", encoding="utf-8").write(f"{self.sup}\t{' '.join(B.normalize(self.sup, self.base.to_syllables(self.sup)))}\ttest\n")
        with mock.patch.object(B, "SLANG", slang):
            (rows, _, _, _), _ = self.build(sup=True)
        self.assertEqual(self.row(rows, self.sup), [])   # not a supplement word (the slang tail itself reads the committed file); before, it was, and the slang tail then aborted

    def test_supplement_rows_sit_between_the_generated_rows_and_the_manual_tail(self):
        (rows, _, _, _), _ = self.build(sup=True)
        sup = [i for i, r in enumerate(rows) if r.endswith("萌典補詞\n")]
        self.assertEqual([rows[i].split("\t")[1] for i in sup], [self.sup])
        tags = [r.rstrip("\n").split("\t")[3] for r in rows]
        self.assertTrue(tags.index("萌典補詞") > max(i for i, t in enumerate(tags) if t == "wikt") and tags.index("萌典補詞") < tags.index("manual"))

    # --- SJ-LM-7 contract 2.3 revision 4: concised dictionary first, neutral tone kept twice, Japanese forms out ---
    NEUTRAL = "ㄇㄚ˙"

    def syls(self, w):
        return list(B.normalize(w, self.base.to_syllables(w)))

    def written(self, rows, w):
        """[(reading, score)] of a word's rows, in file order."""
        return [(r.split("\t")[0], float(r.split("\t")[2])) for r in rows if r.split("\t")[1] == w]

    def report(self, d):
        return open(os.path.join(d, "r.tsv"), encoding="utf-8").read().splitlines()[1:]

    def test_the_concised_reading_beats_moedict_and_the_report_names_the_source(self):
        w = self.words[0]
        old = self.syls(w); new = old[::-1]
        (rows, _, _, _), d = self.build(True, report=True, titles=[w], extra_moe={w: tuple(old)}, concised={w: B.Concised(new, [])})
        self.assertEqual(self.written(rows, w), [("-".join(new), B.SCORE[2])])
        self.assertIn(f"{w}\t{' '.join(old)}\t{' '.join(new)}\tconcised", self.report(d))

    def test_a_concised_variant_that_only_differs_by_the_sandhi_of_one_or_bu_is_not_written(self):
        w = self.bu
        main = self.syls(w)
        var = B.sandhi_variant(w, main)
        (rows, _, _, _), d = self.build(True, report=True, titles=[w], concised={w: B.Concised(main, [("變", var)])})
        want = B.overlay_rows(w, main, B.SCORE[2], "wikt")
        self.assertEqual(len(want), 2)                                          # the automatic sandhi row is the only second row
        self.assertEqual([r for r in rows if r.split("\t")[1] == w], want)
        self.assertFalse([l for l in self.report(d) if l.startswith(w + "\t")])  # a sandhi-only variant is not even listed

    def test_a_neutral_concised_variant_is_a_second_row_with_the_same_score_after_the_base_tone(self):
        w = self.words[1]
        main = self.syls(w); neu = [main[0], self.NEUTRAL]
        self.assertIn(self.NEUTRAL, B.base_syllables(self.base))
        (rows, _, _, _), d = self.build(True, report=True, titles=[w], concised={w: B.Concised(main, [("變", neu)])})
        self.assertEqual(self.written(rows, w), [("-".join(main), B.SCORE[2]), ("-".join(neu), B.SCORE[2])])
        self.assertIn(f"{w}\t{' '.join(main)}\t{' '.join(neu)}\t第二列（簡編本變／語音）", self.report(d))

    def test_a_spoken_variant_is_a_second_row_only_when_it_is_valid_and_the_other_kinds_are_listed(self):
        ok, bad, also, tone = self.words[2:6]
        c = {}
        for w, kind, var in ((ok, "語音", self.syls(ok)[::-1]), (bad, "語音", ["jia3", self.syls(bad)[1]]), (also, "又音", self.syls(also)[::-1]),
                             (tone, "變", self.syls(tone)[::-1])):          # a 變 that is neither sandhi nor neutral
            c[w] = B.Concised(self.syls(w), [(kind, var)])
        (rows, _, _, _), d = self.build(True, report=True, titles=[ok, bad, also, tone], concised=c)
        self.assertEqual(len(self.written(rows, tone)), 1)
        self.assertEqual(self.written(rows, ok), [("-".join(self.syls(ok)), B.SCORE[2]), ("-".join(self.syls(ok)[::-1]), B.SCORE[2])])
        self.assertEqual(len(self.written(rows, bad)), 1)                       # a syllable the base does not have: not written
        self.assertEqual(len(self.written(rows, also)), 1)                      # 又音 is never written, only listed
        rep = self.report(d)
        self.assertIn(f"{also}\t{' '.join(self.syls(also))}\t{' '.join(self.syls(also)[::-1])}\t簡編本又音（不寫）", rep)
        self.assertTrue(any(l.startswith(bad + "\t") and "有基底沒有的音節，不寫" in l for l in rep))

    def test_a_concised_reading_the_base_cannot_spell_falls_back_to_moedict_and_the_report_says_why(self):
        miss, short = self.words[6:8]
        c = {miss: B.Concised(["jia3", self.syls(miss)[1]], []), short: B.Concised([self.syls(short)[0]], [])}
        moe = {w: tuple(self.syls(w)[::-1]) for w in (miss, short)}
        (rows, _, _, _), d = self.build(True, report=True, titles=[miss, short], extra_moe=moe, concised=c)
        for w, why in ((miss, "有基底沒有的音節"), (short, "音節數不等於字數")):
            self.assertEqual(self.written(rows, w), [("-".join(self.syls(w)[::-1]), B.SCORE[2])])
            self.assertIn(f"{w}\t{' '.join(self.syls(w))}\t{' '.join(self.syls(w)[::-1])}\tmoedict (簡編本不合：{why})", self.report(d))

    def test_the_real_base_gives_the_same_rime_base_tone_and_a_neutral_without_one_stays_neutral(self):
        tones = B.base_tones()
        row = lambda w, s: B.tone_row(w, s, tones)[0]
        self.assertEqual(row("桌子", ["ㄓㄨㄛ", "ㄗ˙"]), ["ㄓㄨㄛ", "ㄗˇ"])                  # neutral-tolerance.md section 1 examples
        self.assertEqual(row("石頭", ["ㄕˊ", "ㄊㄡ˙"]), ["ㄕˊ", "ㄊㄡˊ"])
        self.assertEqual(row("我們", ["ㄨㄛˇ", "ㄇㄣ˙"]), ["ㄨㄛˇ", "ㄇㄣˊ"])
        self.assertEqual(row("你和", ["ㄋㄧˇ", "ㄏㄜ˙"]), ["ㄋㄧˇ", "ㄏㄜˊ"])                # 和 has ㄏㄢˋ (-3.0) too, but that is another rime
        self.assertEqual(row("丁罷", ["ㄉㄧㄥ", "ㄅㄚ˙"]), ["ㄉㄧㄥ", "ㄅㄚˋ"])
        self.assertEqual(row("桌子的", ["ㄓㄨㄛ", "ㄗ˙", "ㄉㄜ˙"]), ["ㄓㄨㄛ", "ㄗˇ", "ㄉㄜ˙"])      # mixed: 子 changes, 的 (only ㄉㄧˊ ㄉㄧˋ, another rime) keeps its neutral syllable
        for w, s, why in (("一定的", ["ㄧ", "ㄉㄧㄥˋ", "ㄉㄜ˙"], "沒有同韻的非輕聲讀音"), ("好了", ["ㄏㄠˇ", "ㄌㄜ˙"], "沒有同韻的非輕聲讀音"),
                          ("對著", ["ㄉㄨㄟˋ", "ㄓㄜ˙"], "沒有同韻的非輕聲讀音"), ("怎麼樣", ["ㄗㄣˇ", "ㄇㄜ˙", "ㄧㄤˋ"], "沒有同韻的非輕聲讀音")):
            self.assertEqual(B.tone_row(w, s, tones), (None, why), w)                         # nothing to change: no base-tone row, and no toneless ㄉㄜ ㄌㄜ ㄓㄜ ㄇㄜ

    def test_a_neutral_reading_also_gets_its_base_tone_row_first_whatever_the_source(self):
        w = self.ba
        a = self.syls(w)[0]
        neutral = [a, "ㄅㄚ˙"]
        for kw in ({"extra_moe": {w: tuple(neutral)}}, {"concised": {w: B.Concised(neutral, [])}}):
            (rows, _, _, _), d = self.build(True, report=True, titles=[w], **kw)
            self.assertEqual(self.written(rows, w), [(f"{a}-ㄅㄚˋ", B.SCORE[2]), (f"{a}-ㄅㄚ˙", B.SCORE[2])])
            self.assertIn(f"{w}\t{a} ㄅㄚ˙\t{a} ㄅㄚˋ\t輕聲本調列（排在前；同韻最高分）", self.report(d))

    def test_a_neutral_character_without_a_same_rime_reading_gets_no_second_row_and_the_report_says_so(self):
        w = self.ba
        a = self.syls(w)[0]
        (rows, _, _, _), d = self.build(True, report=True, titles=[w], extra_moe={w: (a, "ㄅㄚ˙")}, tones={})
        self.assertEqual(self.written(rows, w), [(f"{a}-ㄅㄚ˙", B.SCORE[2])])
        self.assertIn(f"{w}\t{a} ㄅㄚ˙\t\t輕聲無本調（沒有同韻的非輕聲讀音，不寫第二列）", self.report(d))

    def test_the_concised_variant_that_is_the_base_tone_of_a_neutral_main_reading_is_the_base_tone_row(self):
        w = self.ba
        a = self.syls(w)[0]
        c = {w: B.Concised([a, "ㄅㄚ˙"], [("變", [a, "ㄅㄚˇ"])])}                  # the dictionary says ㄅㄚˇ although the guess would be ㄅㄚˋ
        (rows, _, _, _), d = self.build(True, report=True, titles=[w], concised=c)
        self.assertEqual(self.written(rows, w), [(f"{a}-ㄅㄚˇ", B.SCORE[2]), (f"{a}-ㄅㄚ˙", B.SCORE[2])])
        rep = self.report(d)
        self.assertIn(f"{w}\t{a} ㄅㄚ˙\t{a} ㄅㄚˇ\t輕聲本調列（排在前；簡編本變）", rep)
        self.assertFalse([l for l in rep if l.startswith(w + "\t") and "不寫" in l])   # it is the base-tone row, not a variant that is left out
        c = {w: B.Concised([a, "ㄅㄚ˙"], [("變", ["ㄅㄚˇ", "ㄅㄚˇ"])])}              # it also changes a syllable that is not neutral: not the pair, the guess stays
        (rows, _, _, _), d = self.build(True, report=True, titles=[w], concised=c)
        self.assertEqual(self.written(rows, w), [(f"{a}-ㄅㄚˋ", B.SCORE[2]), (f"{a}-ㄅㄚ˙", B.SCORE[2])])
        self.assertTrue([l for l in self.report(d) if l.startswith(w + "\t") and "不寫" in l])

    def test_a_neutral_variant_must_keep_every_non_neutral_syllable_of_the_main_reading(self):
        ok, bad = self.words[8:10]
        main = {w: self.syls(w) for w in (ok, bad)}
        c = {ok: B.Concised(main[ok], [("變", [main[ok][0], self.NEUTRAL])]),
             bad: B.Concised(main[bad], [("變", [main[bad][1], self.NEUTRAL])])}    # the first syllable differs as well
        (rows, _, _, _), d = self.build(True, report=True, titles=[ok, bad], concised=c)
        self.assertEqual(len(self.written(rows, ok)), 2)
        self.assertEqual(len(self.written(rows, bad)), 1)
        self.assertTrue([l for l in self.report(d) if l.startswith(bad + "\t") and "簡編本變（不是輕聲也不是" in l])

    def test_a_word_with_a_dropped_japanese_form_is_not_written_and_is_recorded(self):
        w = self.other
        (rows, _, _, jp_removed), _ = self.build(True, jp={w[1]})
        self.assertFalse(self.row(rows, w))
        self.assertEqual(jp_removed, [f"{w}\t{'-'.join(self.syls(w))}\t{w[1]}\n"])
        self.assertTrue(self.row(rows, self.title))                              # the others are untouched

    def test_the_dictionary_lookup_uses_the_standard_form_of_a_variant_character_but_the_word_written_stays(self):
        base = self.base
        for v, std in bc.MERGE.items():
            pair = [(v + c, std + c) for c in self.chars if len(v) == len(std) == 1 and v in base.by_word and std in base.by_word
                    and v + c not in base.by_word and std + c not in base.by_word and base.to_syllables(v + c) and len(set(base.to_syllables(v + c))) == 2]
            if pair:
                break
        wv, ws = pair[0]
        new = self.syls(wv)[::-1]
        (rows, _, _, _), _ = self.build(True, titles=[wv], concised={ws: B.Concised(new, [])})
        self.assertEqual(self.written(rows, wv), [("-".join(new), B.SCORE[2])])
        self.assertFalse(self.row(rows, ws))

    def test_supplement_words_take_the_concised_reading_and_the_same_neutral_rows(self):
        a = self.syls(self.ba)[0]
        c = {self.sup: B.Concised(self.syls(self.sup)[::-1], [])}
        (rows, _, _, _), _ = self.build(sup=True, extra_moe={self.ba: (a, "ㄅㄚ˙")}, extra_corpus=[self.ba], concised=c)
        self.assertEqual(self.written(rows, self.sup), [("-".join(self.syls(self.sup)[::-1]), self.score(2))])      # Moedict says the base reading; concised wins
        self.assertEqual(self.written(rows, self.ba), [(f"{a}-ㄅㄚˋ", self.score(2)), (f"{a}-ㄅㄚ˙", self.score(2))])
        self.assertEqual(self.written(rows, self.title), [("-".join(self.syls(self.title)), B.SCORE[2])])           # without --moedict-readings the wiktionary titles keep today's reading

    def score(self, n):
        return B.length_scores(self.base)[n]


class ConcisedSources(unittest.TestCase):
    """The two pinned sources of contract 2.3 revision 4: the concised dictionary (an xlsx in a zip) and OpenCC JPShinjitaiCharacters.txt."""
    NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

    def zip_with(self, d, rows):
        """A zip holding one xlsx; rows are lists of str (shared string), int (number) or None (empty cell), columns A, B, ..."""
        strings = []
        def cell(i, j, v):
            ref = f"{'ABCDEFG'[j]}{i}"
            if v is None:
                return ""
            if isinstance(v, int):
                return f'<c r="{ref}"><v>{v}</v></c>'
            strings.append(v)
            return f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>'
        sheet = "".join(f'<row r="{i}">{"".join(cell(i, j, v) for j, v in enumerate(r))}</row>' for i, r in enumerate(rows, 1))
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as x:
            x.writestr("xl/sharedStrings.xml", f'<sst xmlns="{self.NS}">' + "".join(f'<si><t xml:space="preserve">{t}</t></si>' for t in strings) + "</sst>")
            x.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{self.NS}"><sheetData>{sheet}</sheetData></worksheet>')
        path = os.path.join(d, "c.zip")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("c.xlsx", inner.getvalue())
        return path

    def test_load_concised_finds_the_columns_by_header_takes_the_smallest_order_and_converts_the_neutral_mark(self):
        header = ["字詞號", "注音一式", "字詞名", "變體注音", "多音排序", "變體類型 1:變 2:又音 3:語音 4:讀音"]
        rows = [header,
                ["1", "ㄐㄧㄚˇ\u3000ㄧˇ", "甲乙", None, 2, None],                    # first in the file, second reading
                ["2", "ㄅㄧㄥˇ ˙ㄉㄧㄥ", "甲乙", "ㄅㄧㄥˇ ㄉㄧㄥ", 1, "語音 "],            # the smallest 多音排序: this row, with its own variant
                ["3", "ㄨˋ\u3000ㄍㄜ", "戊己", "ㄨˋ\u3000ㄍㄜ˙", 0, "又音"],
                ["4", "ㄨˋ ㄍㄜˇ", "戊己", None, 0, None]]                             # same order: the first in the file stays
        with tempfile.TemporaryDirectory() as d:
            path = self.zip_with(d, rows)
            with mock.patch.object(B, "CONCISED_SHA256", B.sha256_file(path)):
                got = B.load_concised(path)
        self.assertEqual(got, {"甲乙": B.Concised(["ㄅㄧㄥˇ", "ㄉㄧㄥ˙"], [("語音", ["ㄅㄧㄥˇ", "ㄉㄧㄥ"])]),
                               "戊己": B.Concised(["ㄨˋ", "ㄍㄜ"], [("又音", ["ㄨˋ", "ㄍㄜ˙"])])})

    def load(self, rows):
        with tempfile.TemporaryDirectory() as d:
            path = self.zip_with(d, rows)
            with mock.patch.object(B, "CONCISED_SHA256", B.sha256_file(path)):
                return B.load_concised(path)

    def test_a_blank_order_sorts_after_every_number_and_a_float_order_is_read(self):
        header = ["字詞名", "多音排序", "注音一式", "變體類型", "變體注音"]
        got = self.load([header,
                         ["甲乙", None, "ㄅㄧㄥˇ ㄉㄧㄥ", None, None],              # blank: last, although first in the file
                         ["甲乙", 7, "ㄐㄧㄚˇ ㄧˇ", None, None],
                         ["丙丁", None, "ㄨˋ ㄍㄜ", None, None],                    # a blank order alone still gives the word
                         ["戊己", "2.0", "ㄒㄧㄡ ㄍㄜ", None, None],                # "2.0" as text: float() reads it
                         ["戊己", "10", "ㄨˋ ㄧ", None, None]])                      # 10 > 2.0 numerically (not as strings)
        self.assertEqual(got["甲乙"].syls, ["ㄐㄧㄚˇ", "ㄧˇ"])
        self.assertEqual(got["丙丁"].syls, ["ㄨˋ", "ㄍㄜ"])
        self.assertEqual(got["戊己"].syls, ["ㄒㄧㄡ", "ㄍㄜ"])

    def test_a_missing_header_column_stops_the_build_and_names_it(self):
        with self.assertRaises(SystemExit) as cm:
            self.load([["字詞名", "多音排序", "注音一式", "變體類型"], ["甲乙", 1, "ㄐㄧㄚˇ ㄧˇ", None]])
        self.assertIn("變體注音", str(cm.exception))

    def test_a_file_with_the_wrong_hash_stops_the_build(self):
        with tempfile.TemporaryDirectory() as d:
            path = self.zip_with(d, [["字詞名", "多音排序", "注音一式", "變體類型", "變體注音"]])
            with self.assertRaises(SystemExit):
                B.load_concised(path)                                              # the real CONCISED_SHA256 is not this file's
            open(os.path.join(d, "jp.txt"), "w", encoding="utf-8").write("両\t兩\n")
            with self.assertRaises(SystemExit):
                B.jp_drop_set(set(), os.path.join(d, "jp.txt"))

    def test_jp_drop_set_keeps_self_mapped_characters_and_characters_the_dictionaries_use(self):
        text = "# comment\n\n両\t兩\n万\t萬\n台\t臺 台\n体\t體\n"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "jp.txt")
            open(path, "w", encoding="utf-8").write(text)
            with mock.patch.object(B, "JP_SHA256", B.sha256_file(path)):
                self.assertEqual(B.jp_drop_set({"体"}, path), {"両", "万"})        # 台 maps to itself too; 体 is in a dictionary headword


class RuleKeepChars(unittest.TestCase):
    """契約 §2.3 修訂四驗收：規則（readings.py）改音時，為、町、蛤、吧、萎、凊 這幾個字保留原讀音，其他字照規則。"""
    def test_a_kept_character_stays_and_the_others_change(self):
        base = ime.Lexicon(B.BASE)
        w = next(x for x in ("頗為", "因為", "作為") if base.to_syllables(x) is not None)
        today = B.normalize(w, base.to_syllables(w))
        other = "ㄅㄚ" if today[0] != "ㄅㄚ" else "ㄅㄚˇ"           # the rule's reading differs at both positions
        wei = "ㄨㄟˋ" if today[1] != "ㄨㄟˋ" else "ㄨㄟˊ"
        stub = mock.Mock(); stub.read = lambda x: ([other, wei], [], [])
        with mock.patch.object(readings, "make_reader", lambda lex, **kw: stub):
            pick = B.reading_picker(base, B.base_syllables(base), {}, {}, lambda t: t)
            got = pick(w)
        self.assertEqual((got.syls, got.how), ([other, today[1]], "readings.py"))


class ToneRow(unittest.TestCase):
    """契約 §2.3 修訂四「輕聲」（和 neutral-tolerance.md §1 同一條規則）：本調＝同韻（去掉聲調後相同）的非輕聲單字讀音裡分數最高的；
    平手取這個字自己的列在檔案裡最前面的；沒有同韻的非輕聲讀音時那個位置保留輕聲，整個詞沒有任何位置能換就不寫（回 None）。合成的基底檔。"""
    BASE_TEXT = """# format
_ctrl_x ! 0.0
ㄗ˙ 子 -5
ㄗ 子 -3
ㄗˇ 子 -3
ㄗㄜˇ 子 -1
ㄊㄡˊ 頭 -3
ㄊㄡ 頭 -3
ㄊㄡ˙ 頭 -2
ㄉㄜ˙ 的 -1
ㄉㄧˊ 的 -3
ㄉㄧˋ 的 -4
ㄅㄨ˙ 不 -1
ㄅㄨˊ 不 -2
ㄅㄨˋ 不 -3
ㄗˋ 子子 -1
ㄗˋ-ㄗˋ 子 -1
"""

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.txt")
            open(p, "w", encoding="utf-8").write(cls.BASE_TEXT)
            cls.tones = B.base_tones(p)

    def test_base_tones_keeps_the_non_neutral_single_character_readings_in_the_characters_own_file_order(self):
        self.assertEqual(self.tones["子"], [("ㄗ", -3.0), ("ㄗˇ", -3.0), ("ㄗㄜˇ", -1.0)])    # the neutral row, the multi-character word and the dash reading are out
        self.assertEqual(self.tones["頭"], [("ㄊㄡˊ", -3.0), ("ㄊㄡ", -3.0)])

    def test_the_highest_scoring_same_rime_reading_wins_and_a_tie_goes_to_the_earlier_row_of_that_character(self):
        self.assertEqual(B.tone_row("桌子", ["ㄓㄨㄛ", "ㄗ˙"], self.tones), (["ㄓㄨㄛ", "ㄗ"], "同韻最高分"))     # ㄗ and ㄗˇ tie at -3; ㄗ is the earlier row; ㄗㄜˇ (-1) is another rime
        self.assertEqual(B.tone_row("石頭", ["ㄕˊ", "ㄊㄡ˙"], self.tones)[0], ["ㄕˊ", "ㄊㄡˊ"])                  # the other character: ㄊㄡˊ is earlier than ㄊㄡ
        flipped = {"子": [("ㄗˇ", -3.0), ("ㄗ", -3.0)]}
        self.assertEqual(B.tone_row("桌子", ["ㄓㄨㄛ", "ㄗ˙"], flipped)[0], ["ㄓㄨㄛ", "ㄗˇ"])                  # same scores, the order of the character's rows decides, not the dict
        better = {"子": [("ㄗ", -4.0), ("ㄗˇ", -3.0)]}
        self.assertEqual(B.tone_row("桌子", ["ㄓㄨㄛ", "ㄗ˙"], better)[0], ["ㄓㄨㄛ", "ㄗˇ"])                   # a higher score beats an earlier row

    def test_a_position_without_a_same_rime_reading_keeps_its_neutral_syllable_and_a_word_with_nothing_to_change_gets_no_row(self):
        self.assertEqual(B.tone_row("的的", ["ㄉㄜ˙", "ㄉㄜ˙"], self.tones), (None, "沒有同韻的非輕聲讀音"))   # 的 has ㄉㄧˊ ㄉㄧˋ only: not the same rime
        self.assertEqual(B.tone_row("了了", ["ㄌㄜ˙", "ㄌㄜ˙"], self.tones), (None, "沒有同韻的非輕聲讀音"))   # 了 is not in the table at all
        self.assertEqual(B.tone_row("了子", ["ㄌㄜ˙", "ㄗ˙"], self.tones), (["ㄌㄜ˙", "ㄗ"], "同韻最高分，其他輕聲位置保留輕聲"))   # mixed: only 子 changes
        self.assertEqual(B.tone_row("桌子的", ["ㄓㄨㄛ", "ㄗ˙", "ㄉㄜ˙"], self.tones)[0], ["ㄓㄨㄛ", "ㄗ", "ㄉㄜ˙"])
        self.assertEqual(B.tone_row("子頭", ["ㄗˇ", "ㄊㄡ˙"], self.tones)[0], ["ㄗˇ", "ㄊㄡˊ"])                  # only the neutral position changes

    def test_the_result_goes_through_normalize_and_a_given_base_tone_is_used_as_it_is(self):
        self.assertEqual(B.tone_row("對不", ["ㄉㄨㄟˋ", "ㄅㄨ˙"], self.tones)[0], ["ㄉㄨㄟˋ", "ㄅㄨˋ"])           # 不: the highest same-rime row is ㄅㄨˊ (-2), normalize makes it ㄅㄨˋ
        self.assertEqual(B.tone_row("桌子", ["ㄓㄨㄛ", "ㄗ˙"], self.tones, ["ㄓㄨㄛ", "ㄗˇ"]), (["ㄓㄨㄛ", "ㄗˇ"], "簡編本變"))
        self.assertEqual(B.tone_row("對不", ["ㄉㄨㄟˋ", "ㄅㄨ˙"], self.tones, ["ㄉㄨㄟˋ", "ㄅㄨˊ"])[0], ["ㄉㄨㄟˋ", "ㄅㄨˋ"])   # a given row is normalized too
        self.assertEqual(B.tone_row("甲乙", ["ㄐㄧㄚˇ", "ㄧˇ"], self.tones), (None, None))                          # no neutral syllable: no base-tone row


class MainFiles(unittest.TestCase):
    """main() with build() replaced: --check treats a missing file as different, and the default write removes a stale overlay-jp-removed.tsv."""
    def run_main(self, d, *argv):
        paths = {n: os.path.join(d, n) for n in ("OUT", "REMOVED", "VARIANT_REMOVED", "JP_REMOVED")}
        built = (["a\n"], ["b\n"], ["c\n"], ["d\n"])
        with contextlib.ExitStack() as st, contextlib.redirect_stdout(io.StringIO()):
            for n, p in paths.items():
                st.enter_context(mock.patch.object(B, n, p))
            st.enter_context(mock.patch.object(B, "build", lambda *a, **k: built))
            st.enter_context(mock.patch.object(sys, "argv", ["build_overlay.py", *argv]))
            try:
                B.main()
            except SystemExit as e:
                return e.code, paths
        return 0, paths

    def test_check_with_moedict_readings_and_no_jp_file_says_differs_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as d:
            for n, text in (("OUT", "a\n"), ("REMOVED", "b\n"), ("VARIANT_REMOVED", "c\n")):
                open(os.path.join(d, n), "w", encoding="utf-8").write(text)
            code, paths = self.run_main(d, "--check", "--moedict-readings")
            self.assertEqual(code, 1)
            self.assertFalse(os.path.exists(paths["JP_REMOVED"]))
            open(paths["JP_REMOVED"], "w", encoding="utf-8").write("d\n")
            self.assertEqual(self.run_main(d, "--check", "--moedict-readings")[0], 0)
            self.assertEqual(self.run_main(d, "--check")[0], 0)                     # the default does not look at that file

    def test_a_default_write_removes_a_stale_jp_file_and_the_flag_writes_it(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "JP_REMOVED"), "w", encoding="utf-8").write("stale\n")
            _, paths = self.run_main(d)
            self.assertFalse(os.path.exists(paths["JP_REMOVED"]))
            self.assertEqual(open(paths["OUT"], encoding="utf-8").read(), "a\n")
            self.run_main(d, "--moedict-readings")
            self.assertEqual(open(paths["JP_REMOVED"], encoding="utf-8").read(), "d\n")


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
                         {"付畀", "凝睇", "咿唔", "唔篤", "噏呷", "投畀豺虎", "曼睩", "睇眄", "迎睇", "從諗"})   # 從諗: a Moedict headword the SJ-LM-7 supplement brings in


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
