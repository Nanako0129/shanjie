"""Tests for `tools/build_meme_pack.py --pack beauty` (docs/spec/lexicon-and-packs.md §8.2, §8.4). Run: python3 -m unittest tools/test_build_beauty_pack.py   (from the repo root).
No network, no evaluation CLI, no real corpus: candidates and counts are small fixtures (shared with test_build_meme_pack.py), the decoder and the reading tool are fakes.
`--pack meme` is covered by test_build_meme_pack.py, which is not changed."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_meme_pack as T  # noqa: E402
import test_build_meme_pack as M  # noqa: E402

B, IT = T.B, T.IT
put, get = M.put, M.get
P = M.Pipeline


class BeautyPipeline(unittest.TestCase):
    """The three stages through main(--pack beauty): the same fixtures as the meme pipeline, the beauty names, seed and manifest."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.work, self.out, self.packs = (os.path.join(t, n) for n in ("work", "out", "packs"))
        os.makedirs(self.packs)
        self.cand, self.c1, self.c2 = (os.path.join(t, n) for n in ("cand.tsv", "c1.tsv", "c2.tsv"))
        put(self.cand, P.CAND)
        for patch in (mock.patch.object(IT, "check_shipping_lm"), mock.patch.object(T, "have_words", lambda: frozenset({"已有詞"})),
                      mock.patch.object(T, "pack_words", lambda *a, **k: frozenset()), mock.patch.object(B, "make_readings", M.fake_readings),
                      mock.patch.object(B, "scores", lambda base: dict(M.SC)), mock.patch.object(B, "base_lexicon", lambda: None),
                      mock.patch.object(T, "decoder", lambda *a, **k: M.fake_decoder(chat=P.X, formal=P.FORMAL)), mock.patch.object(T, "MIN_WORDS_BEAUTY", 1),
                      mock.patch.object(T, "PACKS", self.packs)):        # the default beauty-exclude.tsv is looked up here: none unless a test puts one
            patch.start()
            self.addCleanup(patch.stop)

    def args(self, *more, cand=None):
        return ["--pack", "beauty", "--cand", cand or self.cand, "--work", self.work, "--out", self.out, "--collisions", os.path.join(self.tmp.name, "none.tsv"),
                "--readings", os.path.join(self.tmp.name, "none-r.tsv"), *more]

    def run_main(self, *argv):
        M.run_main(*argv)

    def stages(self, *more, cand=None):
        self.run_main(*self.args("--emit-candidates", cand=cand))
        put(self.c1, M.counts_text(P.C1, IT.sha256_text(get(os.path.join(self.work, "beauty-candidates.txt")))))
        self.run_main(*self.args("--emit-x", "--counts", self.c1, cand=cand))
        put(self.c2, M.counts_text(P.C2, IT.sha256_text(get(os.path.join(self.work, "beauty-x-strings.txt")))))
        self.run_main(*self.args("--counts", self.c1, self.c2, *more, cand=cand))

    def words(self):
        return {l.split("\t")[1] for l in get(os.path.join(self.out, "beauty-add.tsv")).splitlines()}

    def test_file_names_tag_and_the_seed(self):
        seen = []
        real = T.check_words
        with mock.patch.object(T, "check_words", lambda info, seed=T.SEED, n=T.CHECK_N: (seen.append(seed), real(info, seed, n))[1]):
            self.stages()
        self.assertEqual(seen, [20261108])
        self.assertEqual(T.SEEDS, {"meme": 20261025, "beauty": 20261108})
        for d in (self.work, self.out):
            self.assertFalse([n for n in os.listdir(d) if "meme" in n], os.listdir(d))
        self.assertEqual(sorted(os.listdir(self.out)), ["beauty-add.tsv", "beauty-sources.tsv", "beauty.json"])
        self.assertEqual({"beauty-candidates.txt", "beauty-x.tsv", "beauty-x-strings.txt", "beauty-x.json", "report.txt", "dropped.tsv"} - set(os.listdir(self.work)), set())
        self.assertEqual(self.words(), {"我就爛", "同音多", "只聊天對"})         # the same first-place rule as the meme pack
        self.assertTrue(all(l.endswith("\tbeauty") for l in get(os.path.join(self.out, "beauty-add.tsv")).splitlines()))
        m = json.loads(get(os.path.join(self.out, "beauty.json")))
        self.assertEqual(sorted(m["files"]), ["beauty-add.tsv", "beauty-sources.tsv"])
        self.assertEqual(m["beauty_x"], json.loads(get(os.path.join(self.work, "beauty-x.json"))))

    def test_candidate_files_records_names_not_paths(self):
        self.stages()
        text = get(os.path.join(self.out, "beauty.json"))
        self.assertEqual(json.loads(text)["candidate_files"], {"cand.tsv": B.sha256_file(self.cand)})        # self.cand is an absolute path in a temp dir
        self.assertNotIn(self.tmp.name, text)
        self.assertNotIn(os.path.basename(self.tmp.name), get(os.path.join(self.work, "report.txt")))

    def test_candidate_files_with_the_same_name_in_two_folders_stop(self):
        other = os.path.join(self.tmp.name, "sub", "cand.tsv")
        os.makedirs(os.path.dirname(other))
        put(other, "別的詞\n")
        with self.assertRaises(SystemExit) as e:
            self.run_main("--pack", "beauty", "--cand", self.cand, other, "--work", self.work, "--emit-candidates")
        self.assertIn("different names", str(e.exception))

    def test_exclude_reads_the_first_column_of_the_tsv_only(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ex.tsv")
            put(p, "# 註解\n\n字串甲\t類別乙\n字串丙\n字串丁\t\n")
            strings, sha = T.read_exclude(p)
            self.assertEqual((set(strings), sha), ({"字串甲", "字串丙", "字串丁"}, B.sha256_file(p)))
            self.assertEqual(T.read_exclude(os.path.join(d, "missing.tsv"), must_exist=False), (frozenset(), None))
            self.assertEqual(T.read_exclude(None), (frozenset(), None))
        # end to end: the category column names a kept word, which must stay in the pack
        put(os.path.join(self.tmp.name, "ex.tsv"), "我就爛\t同音多\n")
        self.stages("--exclude", os.path.join(self.tmp.name, "ex.tsv"))
        self.assertEqual(self.words(), {"同音多", "只聊天對"})
        m = json.loads(get(os.path.join(self.out, "beauty.json")))
        self.assertEqual(m["exclude"], {"sha256": B.sha256_file(os.path.join(self.tmp.name, "ex.tsv")), "strings": 1})
        self.assertEqual(m["stages"]["excluded_by_exclude"], 1)
        self.assertNotIn("exclude_private", m)

    def test_beauty_reads_data_packs_beauty_exclude_tsv_by_default(self):
        put(os.path.join(self.packs, "beauty-exclude.tsv"), "同音多\t樣板\n")
        self.stages()
        self.assertEqual(self.words(), {"我就爛", "只聊天對", "同音少"})           # the winner is out, so 同音少 is judged on its own: X 拼出詞戊 has 1 article < 20
        self.assertEqual(json.loads(get(os.path.join(self.out, "beauty.json")))["exclude"]["strings"], 1)

    def test_exclude_private_leaves_only_a_hash_and_a_count(self):
        secret_dir = os.path.join(self.tmp.name, "private-cache-dir")
        os.makedirs(secret_dir)
        priv = os.path.join(secret_dir, "ex-private.tsv")
        put(priv, "只聊天對\t某網站導購\n完全無關的私人字串\t某網站導購\n")
        self.stages("--exclude-private", priv)
        self.assertEqual(self.words(), {"我就爛", "同音多"})
        text = get(os.path.join(self.out, "beauty.json"))
        m = json.loads(text)
        self.assertEqual(m["exclude_private"], {"sha256": B.sha256_file(priv), "strings": 2})
        for needle in ("private-cache-dir", "ex-private", "完全無關的私人字串", "某網站導購", "只聊天對", self.tmp.name):
            self.assertNotIn(needle, text)
        for n in ("beauty-add.tsv", "beauty-sources.tsv"):
            self.assertNotIn("完全無關的私人字串", get(os.path.join(self.out, n)))


class PackWords(unittest.TestCase):
    def test_its_own_add_file_is_not_a_pack_to_dedupe_against(self):
        with tempfile.TemporaryDirectory() as d:
            put(os.path.join(d, "acg-add.tsv"), "a\t動漫詞\t-1\tacg\n")
            put(os.path.join(d, "meme-add.tsv"), "a\t迷因詞\t-1\tmeme\n")
            put(os.path.join(d, "beauty-add.tsv"), "a\t自己詞\t-1\tbeauty\n")
            self.assertEqual(T.pack_words(d, tag="beauty"), {"動漫詞", "迷因詞"})
            self.assertEqual(T.pack_words(d), {"動漫詞", "自己詞"})                      # meme (the default) skips only its own file


def run_build(decode, counts, x=None, c2=None, exclude=None, tag="beauty", collisions=(), readings=M.fake_readings, have=frozenset()):
    """T.build on small fixtures. counts: {candidate: (articles, boards)}; x: {word: (chat X, formal first place)}, default ('？'+word, '？'+word); c2: second counts."""
    cands = {w: {"s"} for w in counts}
    _, reading, _, _, _ = T.prepare(cands, counts, frozenset(), frozenset(), readings)
    x = {w: (x or {}).get(w, ("？" + w, "？" + w)) for w in reading}
    table = [(w, " ".join(r), *x[w]) for w, r in reading.items()]
    c2 = dict({xc: (1, 1) for xc, _ in x.values()}, **(c2 or {}))
    with tempfile.TemporaryDirectory() as d:
        coll = os.path.join(d, f"{tag}-collisions.tsv")
        put(coll, "".join("\t".join(r) + "\n" for r in collisions))
        return T.build(cands, counts, table, c2, decode, readings, have, frozenset(), coll, None, M.SC, tag=tag, exclude=exclude)


def decode_with(off):
    """off: {(profile, word): first place with the pack off}; everything else is '？'+word. With the pack on every word is its own first place."""
    def decode(pairs, prof, meme=None):
        return [w if meme else off.get((prof, w), "？" + w) for w, _ in pairs]
    return decode


def pack_words(files, tag="beauty"):
    return {l.split("\t")[1] for l in files[f"{tag}-add.tsv"].splitlines()}


SAME = lambda ws: {w: (["a", "b", "c"], False) if w.startswith("同音") else ([f"s{ord(c)}" for c in w], False) for w in ws}       # 同音X share one reading


def hits(col):
    return sorted((e[0], e[1], e[2], e[3]) for v in col.values() for e in v)


class Exclude(unittest.TestCase):
    def test_an_excluded_same_reading_winner_does_not_block_the_loser(self):
        counts = {"同音多": (50, 5), "同音少": (20, 5)}
        files, m, col, dropped, *_ = run_build(decode_with({}), counts, exclude=frozenset({"同音多"}), readings=SAME)
        self.assertEqual((pack_words(files), dropped), ({"同音少"}, []))        # judged on its own (X has 1 article < 20), not dropped as same_reading
        self.assertEqual(m["stages"]["excluded_by_exclude"], 1)
        files, m, col, dropped, *_ = run_build(decode_with({}), counts, exclude=frozenset(), readings=SAME)      # control: nothing excluded
        self.assertEqual((pack_words(files), [d[:2] for d in dropped]), ({"同音多"}, [("同音少", "same_reading")]))

    def test_an_excluded_string_is_not_in_the_b_reference_list(self):
        counts = {"收錄詞": (40, 5), "被剔除": (30, 5)}
        off = decode_with({("formal", "收錄詞"): "被剔除"})             # in the formal profile the pack row pushes 被剔除 off first place
        files, m, col, *_ = run_build(off, counts, exclude=frozenset({"被剔除"}))
        self.assertEqual((pack_words(files), col), ({"收錄詞"}, {}))
        files, m, col, *_ = run_build(off, counts, exclude=frozenset())     # control: kept as a candidate, it is in the list and the displacement is listed
        self.assertEqual(hits(col), [("收錄詞", "b", "formal", "被剔除")])

    def test_the_exclude_stage_key_exists_only_when_an_exclude_set_is_given(self):
        counts = {"收錄詞": (40, 5)}
        self.assertNotIn("excluded_by_exclude", run_build(decode_with({}), counts, tag="meme")[1]["stages"])
        self.assertIn("excluded_by_beauty_collisions_tsv", run_build(decode_with({}), counts, exclude=frozenset())[1]["stages"])


class ReferenceList(unittest.TestCase):
    """(b) for beauty (docs/spec/lexicon-and-packs.md §8.2): the reference list is the candidates that passed the thresholds, and the chat main-reading displacement of the X the first-place rule judged is exempt."""

    def test_the_chat_x_that_lost_the_count_is_not_a_b_collision(self):
        counts = {"同音多": (50, 5), "同音少": (20, 5)}      # 同音少 passed the thresholds and lost the same-reading pick: it is in the list, and it is the chat X of 同音多
        files, m, col, dropped, *_ = run_build(decode_with({("chat", "同音多"): "同音少"}), counts, x={"同音多": ("同音少", "？同音多")}, c2={"同音少": (20, 5)}, readings=SAME)
        self.assertEqual((pack_words(files), col), ({"同音多"}, {}))
        # the same displacement for the meme pack is a (b) collision: its list is every candidate and it has no exemption
        files, m, col, *_ = run_build(decode_with({("chat", "同音多"): "同音少"}), counts, x={"同音多": ("同音少", "？同音多")}, c2={"同音少": (20, 5)}, readings=SAME, tag="meme")
        self.assertEqual(hits(col), [("同音多", "b", "chat", "同音少")])

    def test_another_passing_candidate_that_is_taken_over_is_still_listed(self):
        counts = {"同音多": (50, 5), "同音少": (20, 5)}
        # not the X (the chat X is another string), the formal first place, and a sandhi-free second reading would be the same: all listed
        off = decode_with({("chat", "同音多"): "同音少", ("formal", "同音多"): "同音少"})
        files, m, col, *_ = run_build(off, counts, x={"同音多": ("別的第一名", "？同音多")}, readings=SAME)
        self.assertEqual(pack_words(files), {"同音多"})
        self.assertEqual(hits(col), [("同音多", "b", "chat", "同音少"), ("同音多", "b", "formal", "同音少")])
        # the X is 同音少 in chat (exempt there) but the formal profile still lists it
        files, m, col, *_ = run_build(off, counts, x={"同音多": ("同音少", "？同音多")}, c2={"同音少": (20, 5)}, readings=SAME)
        self.assertEqual(hits(col), [("同音多", "b", "formal", "同音少")])

    def test_a_judged_c_displacement_is_not_an_open_collision(self):
        counts = {"收錄詞": (40, 5)}
        off = decode_with({("chat", "收錄詞"): "既有詞"})                  # the pack row pushes the lexicon word 既有詞 off the chat first place
        x = {"收錄詞": ("既有詞", "？收錄詞")}                                 # ...and 既有詞 is the X the judge weighed (1 article < 40)
        files, m, col, *_ = run_build(off, counts, x=x, have=frozenset({"既有詞"}))
        self.assertEqual((pack_words(files), col, m["unresolved_collision_readings"]), ({"收錄詞"}, {}, 0))
        files, m, col, *_ = run_build(off, counts, x=x, have=frozenset({"既有詞"}), tag="meme")      # meme keeps listing it (its output is unchanged)
        self.assertEqual(hits(col), [("收錄詞", "c", "chat", "既有詞")])
        files, m, col, *_ = run_build(off, counts, have=frozenset({"既有詞"}))      # control: not the judged X, so the (c) loop drops the word
        self.assertEqual(pack_words(files), set())

    def test_a_candidate_below_the_thresholds_is_not_in_the_list(self):
        counts = {"收錄詞": (40, 5), "沒過門檻": (9, 5), "看板太少": (50, 2)}
        off = decode_with({("chat", "收錄詞"): "沒過門檻", ("formal", "收錄詞"): "看板太少"})
        files, m, col, *_ = run_build(off, counts)
        self.assertEqual((pack_words(files), col), ({"收錄詞"}, {}))
        files, m, col, *_ = run_build(off, counts, tag="meme")             # control: the meme list holds them
        self.assertEqual(hits(col), [("收錄詞", "b", "chat", "沒過門檻"), ("收錄詞", "b", "formal", "看板太少")])


if __name__ == "__main__":
    unittest.main()
