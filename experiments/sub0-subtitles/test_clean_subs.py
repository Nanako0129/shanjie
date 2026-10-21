import collections, contextlib, hashlib, io, json, os, sys, tempfile, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import clean_subs as C  # noqa: E402

# characters of the synthetic lexicon; 这 is the "simplified only" character, 視頻 the invade term
LEXICON = "我們今天一起去吃飯喔你好嗎真的是這樣啊太棒了看電影很好玩視頻"
KEEP = "我們今天一起去吃飯喔"           # 10 Han characters
SIMP = "这"


def write(d, name, text):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


class FakeGuard:
    """Stands in for p2clean.Guard: one pattern, labelled 'dev' and 'holdout'."""
    conv = ({}, {}, 1)
    contain = {"一起去吃": {"dev", "holdout"}}
    files = []

    def __init__(self):
        self.matcher = C.P2.s7f.Matcher(self.contain)

    def hits(self, s):
        return ({"dev", "holdout"} if "一起去吃" in s else set()), set()


def make_ctx(d, simp=SIMP):
    lex = write(d, "lex.txt", "# format\n_ctrl_x ！ 0.0\n" + "".join(f"ㄅ {c} -1.0\n" for c in LEXICON) + "bad line\n")
    repo = os.path.join(d, "invade", "database", "vocabs")
    os.makedirs(repo, exist_ok=True)
    write(repo, "a.yml", "word: 視頻\n")
    write(repo, "b.yml", "word: 好\n")                              # single character: dropped by invade.load
    with mock.patch.object(C.bc, "TRAD_SIMP_ONLY", {simp}):
        ns = C.argparse.Namespace(invade=os.path.join(d, "invade"), invade_sha256="", lexicon=lex)
        with mock.patch.object(C.bc, "load_conv", return_value=None):
            return C.build_ctx(ns), lex


class Pieces(unittest.TestCase):
    def test_strip_cue(self):
        self.assertEqual(C.strip_cue("- 你好嗎"), "你好嗎")
        self.assertEqual(C.strip_cue("-「你好嗎"), "你好嗎")
        self.assertEqual(C.strip_cue("好的啊」"), "好的啊")                  # opener missing: the closer goes
        self.assertEqual(C.strip_cue("他說「好」"), "他說「好」")            # a balanced quote stays
        self.assertEqual(C.strip_cue('好的啊"'), "好的啊")

    def test_half_is_the_first_byte_of_the_sha256_below_128(self):
        for s in ("我們去吃飯", "你好嗎", "太棒了啊", "看電影吧", "真的是這樣"):
            self.assertEqual(C.half(s), hashlib.sha256(s.encode()).digest()[0] < 128)
        self.assertEqual(len({C.half(f"句子{i}") for i in range(40)}), 2)   # both values occur

    def test_lexicon_chars_skip_comments_underscore_and_short_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = write(d, "l.txt", "# c\n_x ！ 1\nㄅ-ㄆ 你好 -1.0\nonly two\nㄅ 好 -1.0\n")
            self.assertEqual(C.lexicon_chars(p), {"你", "好"})

    def test_kana_and_english_patterns(self):
        for s in ("あ", "ア", "ｱ", "ㇰ"):
            self.assertTrue(C.KANA.search(s), s)
        self.assertIsNone(C.KANA.search("漢字"))
        self.assertEqual(len(C.ENGLISH_WORD.findall("don't stop me now")), 4)


class Reject(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.ctx, _ = make_ctx(self.d.name)
        self.ctx.simp = {SIMP}

    def why(self, s):
        return C.reject(s, self.ctx)

    def test_each_step(self):
        self.assertIsNone(self.why(KEEP))
        self.assertEqual(self.why("好的"), "han_len")
        self.assertEqual(self.why(KEEP + "abcdefg"), "han_ratio")
        self.assertEqual(self.why(KEEP + "喔喔喔喔 a.com"), "url")
        self.assertEqual(self.why(KEEP + "喔喔喔喔 {x}"), "code")
        self.assertEqual(self.why(KEEP + "あ"), "kana")
        self.assertEqual(self.why(KEEP + "ア"), "kana")
        self.assertEqual(self.why("龘龘龘龘龘龘龘" + KEEP), "lexicon")               # 7 of 17 Han outside the lexicon: 41%
        self.assertIsNone(self.why("龘龘龘" + KEEP))                                  # 3 of 13: 23% stays
        self.assertEqual(self.why(KEEP + " ok go"), "english")
        self.assertIsNone(self.why(KEEP + " ok"))                                      # one English word stays
        self.assertEqual(self.why(KEEP + SIMP), "simplified")
        self.assertEqual(self.why(KEEP + "視頻"), "invade")
        self.assertIsNone(self.why(KEEP + "好"))                                       # a single-character invade term is not a term

    def test_order_is_the_registered_one(self):
        # kana before lexicon before english before simplified before invade
        self.assertEqual(self.why("龘龘龘龘龘龘龘" + KEEP + "あ"), "kana")
        self.assertEqual(self.why("龘龘龘龘龘龘龘" + KEEP + " ok go"), "lexicon")
        self.assertEqual(self.why(KEEP + " ok go" + SIMP), "english")
        self.assertEqual(self.why(KEEP + SIMP + "視頻"), "simplified")

    def test_triggers_are_closed_set_labels_not_text(self):
        t = lambda step, s: C.trigger(step, s, self.ctx)
        self.assertEqual(t("han_len", "好的"), "fewer than 4 Han")
        self.assertEqual(t("han_ratio", KEEP + "abcdefg"), "han share 0.5")
        self.assertEqual(t("url", KEEP + "喔喔喔喔 a.com"), "domain .com")
        self.assertEqual(t("url", KEEP + "x@y.com"), "e-mail")
        self.assertEqual(t("code", KEEP + "喔喔喔喔 {x}"), "{")
        self.assertEqual(t("kana", KEEP + "ア"), "katakana")
        self.assertEqual(t("lexicon", "龘龘龘龘龘龘龘" + KEEP), "outside the lexicon 0.4")
        self.assertEqual(t("english", KEEP + " ok go"), "2 English words")
        self.assertEqual(t("simplified", KEEP + SIMP), SIMP)
        self.assertEqual(t("invade", KEEP + "視頻"), "視頻")


class EndToEnd(unittest.TestCase):
    LINES = ["- " + KEEP, "「" + KEEP + "」", KEEP + "。" + "你好嗎真的是這樣啊太棒了", "看電影很好玩啊真的是這樣", KEEP + "あ", "好的", "龘龘龘龘龘龘龘" + KEEP,
             KEEP + " ok go", KEEP + SIMP, KEEP + "視頻", "看電影很好玩啊真的是這樣", "我們一起去吃飯喔太棒了"]

    def run_main(self, d, extra=(), min_han="1"):
        raw = write(d, "zh_tw.txt", "\n".join(self.LINES) + "\n")
        make_ctx(d)
        out = os.path.join(d, "out")
        argv = ["--raw", raw, "--out", out, "--lexicon", os.path.join(d, "lex.txt"), "--invade", os.path.join(d, "invade"), "--repo", d,
                "--min-han", min_han, *extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch.object(C.bc, "TRAD_SIMP_ONLY", {SIMP}), mock.patch.object(C.bc, "load_conv", return_value=None), \
                mock.patch.object(C, "build_guard", return_value=(FakeGuard(), [])):
            rc = C.main(argv)
        return rc, buf.getvalue(), out

    def test_counts_files_and_no_sentence_text_on_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            rc, text, out = self.run_main(d)
            self.assertEqual(rc, 0, text)
            man = json.load(open(os.path.join(out, "manifest.json")))
            dr = {k: v["sentences"] for k, v in man["dropped"].items()}
            self.assertEqual(dr["han_len"], 1)                       # "好的"
            self.assertEqual({k: dr[k] for k in ("kana", "lexicon", "english", "simplified", "invade", "dedupe", "guard")},
                             {"kana": 1, "lexicon": 1, "english": 1, "simplified": 1, "invade": 1, "dedupe": 3, "guard": 2})
            # the fake guard drops KEEP (first seen on the cue line; the later copies are duplicates first) and "我們一起去吃飯喔太棒了"
            kept = [l.split("\t")[2] for l in open(os.path.join(out, "subs.tsv"), encoding="utf-8").read().splitlines()]
            self.assertEqual(kept, ["你好嗎真的是這樣啊太棒了", "看電影很好玩啊真的是這樣"])
            self.assertEqual(man["kept"]["sentences"], 2)
            self.assertEqual(man["guard"]["dropped_sentences_by_label_nonexclusive"], {"dev": 2, "holdout": 2})
            self.assertEqual(man["guard"]["distinct_patterns_hit_by_label"], {"dev": 1, "holdout": 1})
            self.assertEqual(man["remaining_after_step"]["split"], len(self.LINES) + 1)    # one line splits in two
            self.assertEqual(man["remaining_after_step"]["guard"], 2)
            half = [l.split("\t")[2] for l in open(os.path.join(out, "subs-half.tsv"), encoding="utf-8").read().splitlines()]
            self.assertEqual(half, [s for s in kept if C.half(s)])
            self.assertEqual(man["outputs"]["subs-half.tsv"]["rows"], len(half))
            # stdout: counts and closed-set triggers; no kept or dropped sentence appears in it
            for line in self.LINES + kept:
                self.assertNotIn(line, text)
            for s in kept:
                self.assertNotIn(s, text)
            self.assertNotIn("--- sample", text)
            self.assertEqual(man["lexicon_missing_characters_top"], [["龘", 7]])

    def test_sample_prints_n_sentences_deterministically_and_needs_a_seed(self):
        with tempfile.TemporaryDirectory() as d:
            a = self.run_main(d, ["--sample", "1", "--seed", "7"])
            b = self.run_main(d, ["--sample", "1", "--seed", "7"])
            self.assertEqual(a[1], b[1])
            tail = a[1].split("--- sample")[1].splitlines()[1:]
            self.assertEqual(len(tail), 1)
            self.assertIn(tail[0].split("\t")[1], ["你好嗎真的是這樣啊太棒了", "看電影很好玩啊真的是這樣"])
            with self.assertRaises(SystemExit):
                self.run_main(d, ["--sample", "1"])

    def test_too_few_han_characters_is_a_stop(self):
        with tempfile.TemporaryDirectory() as d:
            rc, text, _ = self.run_main(d, min_han="100")
            self.assertEqual(rc, 1)
            self.assertIn("STOP:", text)

    def test_an_empty_simplified_set_is_a_stop(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                self.run_main_empty(d)

    def run_main_empty(self, d):
        make_ctx(d)
        ns = C.argparse.Namespace(invade=os.path.join(d, "invade"), invade_sha256="", lexicon=os.path.join(d, "lex.txt"))
        with mock.patch.object(C.bc, "TRAD_SIMP_ONLY", set()), mock.patch.object(C.bc, "load_conv", return_value=None):
            C.build_ctx(ns)

    def test_a_wrong_invade_hash_is_a_stop(self):
        with tempfile.TemporaryDirectory() as d:
            make_ctx(d)
            ns = C.argparse.Namespace(invade=os.path.join(d, "invade"), invade_sha256="0" * 64, lexicon=os.path.join(d, "lex.txt"))
            with mock.patch.object(C.bc, "TRAD_SIMP_ONLY", {SIMP}), mock.patch.object(C.bc, "load_conv", return_value=None):
                with self.assertRaises(SystemExit):
                    C.build_ctx(ns)


if __name__ == "__main__":
    unittest.main()
