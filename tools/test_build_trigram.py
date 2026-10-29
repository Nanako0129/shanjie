"""tools/build_trigram.py and reference/proto/trigram.py (docs/contracts/trigram-core.md). Run: python3 -B -m unittest tools/test_build_trigram.py

The remap tests use two tiny bigram models holding the same words under permuted ids; the scoring tests use the kn-tiny toy model and the committed
tri-tiny table with hand values (the numbers core/tests/tri_tiny.rs and the lm.rs unit tests check on the Rust side); the golden test checks that
eval/golden/tri-tiny/ is what tools/gen_tri_tiny.py writes now."""
import hashlib
import math
import os
import struct
import sys
import tempfile
import unittest
import unittest.mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for sub in ("tools", "reference/proto"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import build_trigram  # noqa: E402
import gen_kn_tiny  # noqa: E402
import gen_tri_tiny  # noqa: E402
import ime  # noqa: E402
import lm as L  # noqa: E402
import trigram  # noqa: E402

WORDS = ["<s>", "</s>", "我", "去", "學校", "吃", "飯", "他", "說"]
TRI = {("<s>", "<s>", "我"): 5, ("<s>", "我", "去"): 4, ("我", "去", "學校"): 3, ("我", "去", "吃"): 2, ("去", "吃", "飯"): 2,
       ("<s>", "<s>", "他"): 3, ("<s>", "他", "說"): 3, ("他", "說", "我"): 2, ("說", "我", "去"): 2}


def mk_sjlm(vocab):
    """A minimal SJLM holding no bigrams (only the vocabulary matters to the remap)."""
    raw = b"".join(struct.pack("<H", len(w.encode())) + w.encode() for w in vocab)
    return b"SJLM0001" + struct.pack("<IQQd", len(vocab), 1, 1, 0.75) + raw + struct.pack(f"<{len(vocab)}Q", *([0] * len(vocab))) \
        + struct.pack("<I", 0) + struct.pack("<I", 0) + struct.pack("<I", 0)


def totals_of(entries):
    t = {}
    for (u, v, _), c in entries.items():
        t[(u, v)] = t.get((u, v), 0) + c + 1   # one pruned count per context
    return t


def triples(tri, vocab):
    """{(u, v, w) strings: count} and {(u, v): pre-prune total}."""
    V, c3, t3 = len(vocab), {}, {}
    for i, k in enumerate(tri["key"]):
        u, v = divmod(int(k), V)
        t3[(vocab[u], vocab[v])] = int(tri["tot"][i])
        for j in range(tri["off"][i], tri["off"][i + 1]):
            c3[(vocab[u], vocab[v], vocab[int(tri["nxt"][j])])] = int(tri["cnt"][j])
    return c3, t3


class Remap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sv = WORDS
        cls.dv = WORDS[:2] + ["說", "他", "飯", "吃", "學校", "去", "我"]   # same words, other ids
        cls.src, cls.dst = mk_sjlm(cls.sv), mk_sjlm(cls.dv)
        cls.tri = build_trigram.from_counts(TRI, totals_of(TRI), cls.src)
        cls.out, _, _ = build_trigram.remap(cls.src, cls.tri, cls.dst)

    def test_counts_and_totals_preserved(self):
        a, b = trigram.read_tri(self.tri), trigram.read_tri(self.out)
        self.assertEqual(triples(a, self.sv), triples(b, self.dv))
        self.assertEqual(triples(b, self.dv), (TRI, totals_of(TRI)))
        self.assertEqual((len(a["key"]), len(a["nxt"])), (len(b["key"]), len(b["nxt"])))

    def test_sorted_and_bound_to_the_destination(self):
        b = trigram.read_tri(self.out)
        self.assertTrue((np.diff(b["key"].astype(np.int64)) > 0).all())
        for i in range(len(b["key"])):
            self.assertTrue((np.diff(b["nxt"][b["off"][i]:b["off"][i + 1]].astype(np.int64)) > 0).all())
        self.assertEqual((b["V"], b["model_sha"]), (len(self.dv), hashlib.sha256(self.dst).digest()))
        self.assertNotEqual(trigram.read_tri(self.tri)["model_sha"], b["model_sha"])

    def test_refusals(self):
        with self.assertRaises(ValueError):   # the table is bound to SRC, not to DST
            build_trigram.remap(self.dst, self.tri, self.src)
        with self.assertRaises(ValueError):   # a SRC word missing from DST
            build_trigram.remap(self.src, self.tri, mk_sjlm([w for w in self.dv if w != "吃"]))
        with self.assertRaises(ValueError):   # a total below the kept counts
            build_trigram.from_counts(TRI, {k: 1 for k in totals_of(TRI)}, self.src)
        with tempfile.TemporaryDirectory() as tmp:   # the command line stops with a message and writes nothing
            for name, b in (("src", self.src), ("tri", self.tri), ("dst", mk_sjlm([w for w in self.dv if w != "吃"]))):
                open(os.path.join(tmp, name), "wb").write(b)
            args = [f"--{k}-lm" if k != "tri" else "--tri" for k in ("src", "tri", "dst")]
            with self.assertRaises(SystemExit):
                build_trigram.main(sum(([a, os.path.join(tmp, n)] for a, n in zip(args, ("src", "tri", "dst"))), []) + ["--out", os.path.join(tmp, "out")])
            self.assertFalse(os.path.exists(os.path.join(tmp, "out")))


class DropMissing(unittest.TestCase):
    def test_words_missing_from_dst_are_dropped_with_their_contexts_and_entries(self):
        src = mk_sjlm(WORDS)
        tri = build_trigram.from_counts(TRI, totals_of(TRI), src)
        dv = [w for w in WORDS if w != "吃"]
        out, _, _ = build_trigram.remap(src, tri, mk_sjlm(dv), drop_missing=True)
        got = triples(trigram.read_tri(out), dv)
        want = {k: c for k, c in TRI.items() if "吃" not in k}        # 去吃飯 (context with 吃) and 我去→吃 (next word 吃) go
        tot = {k: t for k, t in totals_of(TRI).items() if k in {(u, v) for u, v, _ in want}}   # totals unchanged; 去吃 is gone
        self.assertEqual(got, (want, tot))
        self.assertEqual(tot[("我", "去")], totals_of(TRI)[("我", "去")])   # the total still counts the dropped 我去吃


class Scoring(unittest.TestCase):
    """Hand values on the kn-tiny model (no classes) and the committed table."""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(gen_tri_tiny.KN_TINY, "bigram.sjlm")
        cls.lm = L.BigramLM(path, classes=False)
        cls.tri = trigram.TriLM(cls.lm, trigram.TriTable.from_path(os.path.join(gen_tri_tiny.OUT, "trigram.sjt3"),
                                                                   hashlib.sha256(open(path, "rb").read()).digest()))

    def test_four_situations(self):
        lm, tri, lp, D = self.lm, self.tri, -3.0, 0.75
        pb = 10 ** lp
        # kept entry: (<s>, 他) -> 占 3 of 8, kept 3 + 1 + 2
        back3 = 1.0 - ((3 - D) + (1 - D) + (2 - D)) / 8
        self.assertEqual(tri.word(0.7, "<s>", "他", "占", lp), 0.7 * math.log10((3 - D) / 8 + back3 * lm.prob("他", "占", pb)) + (1 - 0.7) * lp)
        # the context is stored, the entry is not: (<s>, 他) -> 起牀
        self.assertEqual(tri.word(0.7, "<s>", "他", "起牀", lp), 0.7 * math.log10(back3 * lm.prob("他", "起牀", pb)) + (1 - 0.7) * lp)
        # the context is not stored: (占, 起床) -> 占, and u unknown: (?, 他) -> 占; both are the bigram term
        self.assertEqual(tri.word(0.7, "占", "起床", "占", lp), lm.word(0.7, "起床", "占", lp))
        self.assertEqual(tri.word(0.7, None, "他", "占", lp), lm.word(0.7, "他", "占", lp))
        # the closing term: (他, 占) -> </s> 2 of 9
        back3 = 1.0 - (2 - D) / 9
        self.assertEqual(tri.eos(0.7, "他", "占"), 0.7 * math.log10((2 - D) / 9 + back3 * lm.prob("占", "</s>", lm.p_eos)))
        self.assertEqual(tri.eos(0.7, None, "占"), lm.eos(0.7, "占"))

    def test_start_state(self):
        self.assertEqual(trigram.start_state("<s>"), ("<s>", "<s>"))
        self.assertEqual(trigram.start_state("他"), (None, "他"))


class FloorAndClosing(unittest.TestCase):
    """Slice A2 (docs/contracts/trigram-core.md): the floor of P3, and the closing term of a one-word sentence that starts from a left context."""

    @classmethod
    def setUpClass(cls):
        model = open(os.path.join(gen_tri_tiny.KN_TINY, "bigram.sjlm"), "rb").read()
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = cls._tmp.name
        for name, b in (("bigram.sjlm", model), ("classes.sjc", open(os.path.join(gen_tri_tiny.KN_TINY, "classes.sjc"), "rb").read()),
                        ("lexicon.txt", open(os.path.join(gen_tri_tiny.OUT, "lexicon.txt"), "rb").read()),
                        # (<s>, <s>) keeps 他 6 of 6: back3 = 1 - 5.25 / 6 = 0.125, far below the floor for every other word
                        ("floor.sjt3", build_trigram.from_counts({("<s>", "<s>", "他"): 6}, {("<s>", "<s>"): 6}, model))):
            open(os.path.join(tmp, name), "wb").write(b)
        sha = hashlib.sha256(model).digest()
        cls.lm = L.BigramLM(os.path.join(tmp, "bigram.sjlm"))
        cls.floor_lm = trigram.TriLM(cls.lm, trigram.TriTable.from_path(os.path.join(tmp, "floor.sjt3"), sha))
        cls.tri_lm = trigram.TriLM(cls.lm, trigram.TriTable.from_path(os.path.join(gen_tri_tiny.OUT, "trigram.sjt3"), sha))
        cls.lex = L.cap_overlay(ime.Lexicon(os.path.join(tmp, "lexicon.txt")), set(), cls.lm, demote_path=None)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_floor_active_inactive_and_context_not_stored(self):
        lm, tri, pb = self.lm, self.floor_lm, 10 ** -3.0
        # active: the context is stored and keeps only 他, so 占 would get 0.125 * P2; the floor lifts it to 0.5 * P2
        p2 = lm.prob("<s>", "占", pb)
        self.assertLess(0.125 * p2, 0.5 * p2)
        self.assertEqual(tri.prob("<s>", "<s>", "占", pb), 0.5 * p2)
        # inactive: the kept word is (6 - 0.75) / 6 + 0.125 * P2, above the floor, unchanged
        p2 = lm.prob("<s>", "他", pb)
        self.assertEqual(tri.prob("<s>", "<s>", "他", pb), (6 - 0.75) / 6 + (1.0 - (6 - 0.75) / 6) * p2)
        # the context is not stored (or u is unknown): P2, no floor
        self.assertEqual(tri.prob("占", "<s>", "占", pb), lm.prob("<s>", "占", pb))
        self.assertEqual(tri.prob(None, "<s>", "占", pb), lm.prob("<s>", "占", pb))
        # the closing term goes through the same function
        self.assertEqual(tri.eos(0.7, "<s>", "<s>"), 0.7 * math.log10(0.5 * lm.prob("<s>", "</s>", lm.p_eos)))

    def lp(self, syls, word):
        return dict(self.lex.by_reading[tuple(syls)])[word]

    def test_one_word_sentence_from_a_left_context_closes_with_the_bigram(self):
        t, lam = self.tri_lm, L.PROFILES["formal"]
        best = trigram.decode3(self.lex, ["ㄑㄧˇ", "ㄔㄨㄤˊ"], t, "formal", start="他")
        sc, ws = best[0]
        self.assertEqual(ws, ("起床",))
        lp = self.lp(["ㄑㄧˇ", "ㄔㄨㄤˊ"], "起床")
        want = t.word(lam, None, "他", "起床", lp) + t.eos(lam, None, "起床")
        self.assertEqual(sc, want)
        # premise: the table has (他, 起床) -> </s>, so the old closing term was a different number
        self.assertNotEqual(t.eos(lam, "他", "起床"), t.eos(lam, None, "起床"))
        # from the sentence start (<s>, <s>) the one-word sentence is unchanged: the table's closing term
        sc, ws = trigram.decode3(self.lex, ["ㄑㄧˇ", "ㄔㄨㄤˊ"], t, "formal", start="<s>")[0]
        self.assertEqual(sc, t.word(lam, "<s>", "<s>", "起床", lp) + t.eos(lam, "<s>", "起床"))

    def test_two_word_sentence_from_a_left_context_keeps_the_table_closing_term(self):
        t, lam = self.tri_lm, L.PROFILES["formal"]
        sc, ws = trigram.decode3(self.lex, ["ㄊㄚ", "ㄑㄧˇ", "ㄔㄨㄤˊ"], t, "formal", start="他")[0]
        self.assertEqual(ws, ("他", "起床"))
        lp_ta, lp_qc = self.lp(["ㄊㄚ"], "他"), self.lp(["ㄑㄧˇ", "ㄔㄨㄤˊ"], "起床")
        want = t.word(lam, None, "他", "他", lp_ta) + t.word(lam, "他", "他", "起床", lp_qc) + t.eos(lam, "他", "起床")
        self.assertEqual(sc, want)
        self.assertNotEqual(t.eos(lam, "他", "起床"), t.eos(lam, None, "起床"))


class ProfileLm(unittest.TestCase):
    """lm.profile_lm is core `Lm::scoring`'s beta rule: formal takes FORMAL_KN_BETA 0.75, chat the side file's, a model without a side file is untouched."""

    def test_formal_gets_beta_075_in_a_copy_and_chat_keeps_the_files(self):
        path = os.path.join(gen_tri_tiny.KN_TINY, "bigram.sjlm")
        lm = L.BigramLM(path, kn=os.path.join(gen_tri_tiny.KN_TINY, "kn.sjkn"))
        self.assertEqual(lm.kn[0], 1.0)
        formal = L.profile_lm(lm, "formal")
        self.assertEqual((formal.kn[0], L.FORMAL_KN_BETA, formal.kn[1:]), (0.75, 0.75, lm.kn[1:]))
        self.assertEqual(lm.kn[0], 1.0, "the loaded model is not changed: one model serves both profiles")
        self.assertIs(L.profile_lm(lm, "chat"), lm)
        plain = L.BigramLM(path)
        self.assertIs(L.profile_lm(plain, "formal"), plain)


class Table(unittest.TestCase):
    def test_python_reader_accepts_only_the_contracts_discount(self):
        good = open(os.path.join(gen_tri_tiny.OUT, "trigram.sjt3"), "rb").read()
        trigram.read_tri(good)
        for d in (0.5, 0.76, 1.5, -0.1, float("nan")):
            with self.assertRaises(ValueError, msg=d):
                trigram.read_tri(good[:12] + struct.pack("<d", d) + good[20:])


class LmEvalCli(unittest.TestCase):
    """reference/proto/lm_eval.py mirrors the Rust CLI's loading rules (cli/src/main.rs `run_lm`, docs/contracts/sj-lm-7-pr2.md): each profile reads only its own
    table (formal trigram.sjt3, chat trigram-chat.sjt3), kn.sjkn is read and checked either way, and both profiles score with Kneser-Ney and their table,
    formal at beta 0.75 (lm.FORMAL_KN_BETA) and chat at the file's."""

    def setUp(self):
        import lm_eval
        self.lm_eval = lm_eval
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        for src, name in (("kn-tiny", "bigram.sjlm"), ("kn-tiny", "classes.sjc"), ("kn-tiny", "kn.sjkn"), ("tri-tiny", "trigram.sjt3"), ("tri-tiny", "trigram-chat.sjt3")):
            open(os.path.join(self.dir, name), "wb").write(open(os.path.join(ROOT, "eval", "golden", src, name), "rb").read())
        # The toy side file's ΣN' was counted with the toy lexicon's variant classes, not the real lexicon's that lm_eval derives.
        import build_lm
        classes = build_lm.variant_classes
        patch = unittest.mock.patch.object(build_lm, "variant_classes", lambda _: classes(gen_kn_tiny.BY_READING))
        patch.start()
        self.addCleanup(patch.stop)
        self.rows = os.path.join(self.dir, "rows.txt")
        open(self.rows, "w", encoding="utf-8").write("|他占|ㄊㄚ ㄓㄢˋ\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *argv):
        """(exit code or exception, stdout)."""
        import contextlib
        import io
        out = io.StringIO()
        with unittest.mock.patch.object(sys, "argv", ["lm_eval", "--lm", os.path.join(self.dir, "bigram.sjlm"), "--rows", self.rows, *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                self.lm_eval.main()
                return 0, out.getvalue()
            except SystemExit as e:
                return e.code, out.getvalue()
            except Exception as e:  # noqa: BLE001
                return type(e), out.getvalue()

    def dump(self, name, *argv):
        path = os.path.join(self.dir, name)
        self.assertEqual(self.run_main("--dump", path, *argv)[0], 0)
        return open(path, encoding="utf-8").read()

    def sha8(self, name):
        return hashlib.sha256(open(os.path.join(self.dir, name), "rb").read()).hexdigest()[:8]

    def test_each_profile_reads_its_own_table_and_keeps_kn(self):
        kn = self.sha8("kn.sjkn")
        code, out = self.run_main("--profile", "formal")
        self.assertEqual(code, 0)
        self.assertIn(f"lm-formal+kn:{kn}:θ1:β0.75+tri:{self.sha8('trigram.sjt3')}  ", out)   # formal: beta 0.75 although the file records 1
        code, out = self.run_main("--profile", "chat")
        self.assertEqual(code, 0)
        self.assertIn(f"lm-chat+kn:{kn}:θ1:β1+tri:{self.sha8('trigram-chat.sjt3')}  ", out)
        for prof in ("chat", "formal"):
            code, out = self.run_main("--profile", prof, "--no-tri")
            self.assertTrue(code == 0 and "+tri:" not in out and "+kn:" in out, out)
            code, out = self.run_main("--profile", prof, "--no-kn")
            self.assertTrue(code == 0 and "+tri:" in out and "+kn:" not in out, out)

    def test_an_explicit_table_is_allowed_for_either_profile(self):
        for prof, name in (("chat", "trigram-chat.sjt3"), ("formal", "trigram.sjt3")):
            self.assertEqual(self.run_main("--profile", prof, "--tri", os.path.join(self.dir, name))[0], 0)
        # the chat profile given the formal table scores differently from its own (the toy tables differ)
        other = os.path.join(self.dir, "trigram.sjt3")
        self.assertNotEqual(self.dump("own.tsv", "--profile", "chat"), self.dump("other.tsv", "--profile", "chat", "--tri", other))

    def test_a_profile_does_not_read_the_other_profiles_table(self):
        open(os.path.join(self.dir, "trigram.sjt3"), "wb").write(b"junk")
        self.assertEqual(self.run_main("--profile", "chat")[0], 0)
        self.assertIsNot(self.run_main("--profile", "formal")[0], 0)           # a bad table is never ignored
        self.assertEqual(self.run_main("--profile", "formal", "--no-tri")[0], 0)
        open(os.path.join(self.dir, "trigram.sjt3"), "wb").write(open(os.path.join(ROOT, "eval", "golden", "tri-tiny", "trigram.sjt3"), "rb").read())
        open(os.path.join(self.dir, "trigram-chat.sjt3"), "wb").write(b"junk")
        self.assertEqual(self.run_main("--profile", "formal")[0], 0)
        self.assertIsNot(self.run_main("--profile", "chat")[0], 0)
        self.assertEqual(self.run_main("--profile", "chat", "--no-tri")[0], 0)

    def test_both_profiles_score_with_the_side_file_and_formal_at_beta_075(self):
        # the toy KN moves the scores of both profiles' table decodes
        for prof in ("chat", "formal"):
            self.assertNotEqual(self.dump(f"{prof}-kn.tsv", "--profile", prof), self.dump(f"{prof}-nokn.tsv", "--profile", prof, "--no-kn"), prof)
        # formal ignores the file's beta: the beta-1 file and the beta-0.75 file give the same formal scores, and different chat scores
        b075 = os.path.join(ROOT, "eval", "golden", "kn-tiny", "kn-beta075.sjkn")
        self.assertEqual(self.dump("f1.tsv", "--profile", "formal"), self.dump("f075.tsv", "--profile", "formal", "--kn", b075))
        self.assertNotEqual(self.dump("c1.tsv", "--profile", "chat"), self.dump("c075.tsv", "--profile", "chat", "--kn", b075))

    def test_a_bad_side_file_fails_either_profile(self):
        open(os.path.join(self.dir, "kn.sjkn"), "wb").write(b"junk")
        for prof in ("formal", "chat"):
            self.assertIsNot(self.run_main("--profile", prof)[0], 0)           # the CLI fails on a bad kn.sjkn here too
            self.assertEqual(self.run_main("--profile", prof, "--no-kn")[0], 0)


class Golden(unittest.TestCase):
    def test_committed_files_are_what_the_generator_writes(self):
        for name, b in gen_tri_tiny.generate().items():
            self.assertEqual(open(os.path.join(gen_tri_tiny.OUT, name), "rb").read(), b, name)


if __name__ == "__main__":
    unittest.main()
