"""Tests for tools/build_meme_pack.py (docs/contracts/meme-pack.md section 4). Run: python3 -m unittest tools/test_build_meme_pack.py   (from the repo root).
No network, no evaluation CLI, no real corpus: candidates and counts are small fixtures, the decoder and the reading tool are fakes.
count_usage.py (names-creators) is not in this tree; its output is imitated (three header lines, then string, articles, boards)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_meme_pack as T  # noqa: E402

B, IT = T.B, T.IT
SC = {n: -7.0 for n in range(2, 11)}
TREE = "t" * 64


def put(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def get(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def fake_readings(words):
    """same reading for the two 同音 words, otherwise one made-up syllable per character."""
    return {w: (["a", "b", "c"] if w.startswith("同音") else [f"s{ord(c)}" for c in w], False) for w in words}


def fake_decoder(chat=None, formal=None, seen=None):
    """decode(pairs, profile, meme=None): without meme the first place is chat/formal.get(word, '？'+word) (never the word itself, so nothing is 'already right');
    with meme (the pack under test) every word is its own first place."""
    chat, formal = chat or {}, formal or {}

    def decode(pairs, prof, meme=None):
        if seen is not None:
            seen.append((prof, meme is not None))
        if meme:
            return [w for w, _ in pairs]
        return [(chat if prof == "chat" else formal).get(w, "？" + w) for w, _ in pairs]
    return decode


def counts_text(rows, input_sha, tree=TREE, commit="abc123"):
    return f"# ptt_tree_sha256 {tree}\n# input_sha256 {input_sha}\n# tool_commit {commit}\n" + "".join(f"{s}\t{a}\t{b}\n" for s, (a, b) in rows.items())


class Candidates(unittest.TestCase):
    def read(self, text, name="ptt.tsv"):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, name)
            put(p, text)
            return T.read_candidates([p])

    def test_length_and_ascii_filters_and_the_default_source(self):
        cands, st, _ = self.read("# note\n\n一二\n一二三四五六七八九十\n一二三四五六七八九十一\n單\nJoJo立\n三小4\n有人氣\tptt\n")
        self.assertEqual(cands, {"一二": {"ptt"}, "一二三四五六七八九十": {"ptt"}, "有人氣": {"ptt"}})   # source defaults to the file stem
        self.assertEqual((st["dropped_ascii"], st["dropped_length"], st["candidates"]), (2, 2, 3))

    def test_quote_lines_split_at_punctuation_and_each_piece_is_a_candidate(self):
        cands, st, _ = self.read("我就爛，你能拿我怎樣！\t統神\n好、啦 嗎\t統神\n一二三四五六七八九十一，不錯喔\t統神\n")
        self.assertEqual(cands, {"我就爛": {"統神"}, "你能拿我怎樣": {"統神"}, "不錯喔": {"統神"}})
        self.assertEqual(st["dropped_length"], 4)       # 好、啦、嗎 and the 11-character piece

    def test_full_width_letters_and_digits_count_as_english_and_digits(self):
        cands, st, _ = self.read("三小４\n\uff2a\uff4f\uff2a\uff4f立\n三小4\n正常詞\n")
        self.assertEqual((cands, st["dropped_ascii"]), ({"正常詞": {"ptt"}}, 3))

    def test_provenance_is_keyed_by_the_path_given_so_same_named_files_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for sub, text in (("a", "有人氣\n"), ("b", "好笑啦\n")):
                os.makedirs(os.path.join(d, sub))
                paths.append(os.path.join(d, sub, "cand.tsv"))
                put(paths[-1], text)
            cands, _, files = T.read_candidates(paths)
        self.assertEqual(files, {paths[0]: IT.sha256_text("有人氣\n"), paths[1]: IT.sha256_text("好笑啦\n")})
        self.assertEqual(cands, {"有人氣": {"cand"}, "好笑啦": {"cand"}})

    def test_a_string_with_two_sources_keeps_both(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.tsv"), os.path.join(d, "b.tsv")
            put(a, "有人氣\n")
            put(b, "有人氣\tx\n")
            self.assertEqual(T.read_candidates([a, b])[0], {"有人氣": {"a", "x"}})

    def test_the_candidate_file_is_deduped_and_byte_sorted(self):
        self.assertEqual(T.lines_text(["乙乙", "甲甲", "乙乙"]), "乙乙\n甲甲\n" if "乙".encode() < "甲".encode() else "甲甲\n乙乙\n")


class Counts(unittest.TestCase):
    def test_header_fields_and_rows(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.tsv")
            put(p, counts_text({"甲甲": (12, 4)}, "i" * 64))
            self.assertEqual(T.read_counts(p), ({"ptt_tree_sha256": TREE, "input_sha256": "i" * 64, "tool_commit": "abc123"}, {"甲甲": (12, 4)}))
            put(p, "# ptt_tree_sha256 x\n# input_sha256 y\n甲甲\t1\t1\n")
            with self.assertRaises(SystemExit):
                T.read_counts(p)


class Prepare(unittest.TestCase):
    def test_thresholds_boards_3_articles_10(self):
        cands = {w: {"s"} for w in ("看板二文章十", "看板三文章十", "看板三文章九", "看板五文章五十", "街頭字典詞")}
        counts = {"看板二文章十": (10, 2), "看板三文章十": (10, 3), "看板三文章九": (9, 3), "看板五文章五十": (50, 5)}
        admitted, reading, check, st, unread = T.prepare(cands, counts, frozenset(), frozenset(), fake_readings)
        self.assertEqual(sorted(admitted), ["看板三文章十", "看板五文章五十"])
        self.assertEqual((st["below_threshold"], st["no_count"]), (2, 1))      # 街頭字典詞 has no PTT count: not admitted
        self.assertEqual(admitted["看板三文章十"], (10, 3))

    def test_dedupe_against_the_lexicon_layers_and_the_other_packs(self):
        cands = {w: {"s"} for w in ("詞庫詞", "動漫詞", "新詞新")}
        counts = {w: (99, 9) for w in cands}
        admitted, *_, st, _ = T.prepare(cands, counts, frozenset({"詞庫詞"}), frozenset({"動漫詞"}), fake_readings)
        self.assertEqual(sorted(admitted), ["新詞新"])
        self.assertEqual((st["dedupe_lexicon"], st["dedupe_packs"]), (1, 1))

    def test_a_net_slang_word_is_deduped_through_the_overlay_layer(self):
        slang = next(r[0] for r in B.read_tsv(os.path.join(B.LEX, "slang-add.tsv")))      # its rows are in overlay-add.tsv (tag slang)
        self.assertIn(slang, T.have_words())

    def test_pack_words_reads_every_pack_file_but_its_own(self):
        with tempfile.TemporaryDirectory() as d:
            put(os.path.join(d, "acg-add.tsv"), "a\t動漫詞\t-1\tacg\n")
            put(os.path.join(d, "quotes-add.tsv"), "a\t台詞詞\t-1\tquotes\n")
            put(os.path.join(d, "meme-add.tsv"), "a\t自己詞\t-1\tmeme\n")
            self.assertEqual(T.pack_words(d), {"動漫詞", "台詞詞"})

    def test_fixed_readings_replace_the_tool_reading(self):
        with tempfile.TemporaryDirectory() as d:
            fixed = os.path.join(d, "meme-readings.tsv")
            put(fixed, "# 註解\n蝦咪\tㄒㄧㄚ ㄇㄧ\n")
            _, reading, check, *_ = T.prepare({"蝦咪": {"s"}}, {"蝦咪": (20, 5)}, frozenset(), frozenset(),
                                              lambda ws: {w: (["x", "y"], True) for w in ws}, fixed)
        self.assertEqual((reading["蝦咪"], check["蝦咪"]), (["ㄒㄧㄚ", "ㄇㄧ"], False))


class Judge(unittest.TestCase):
    def run_judge(self, admitted, xs, counts2, reading=None, fs=None, packwords=frozenset()):
        reading = reading or {w: [f"s{ord(c)}" for c in w] for w in admitted}
        return T.judge(admitted, reading, xs, fs or {w: "書面" + w for w in admitted}, counts2, packwords)

    def test_x_from_another_pack_wins_whatever_the_counts(self):
        kept, dropped = self.run_judge({"資料圓": (50, 5)}, {"資料圓": "資料源"}, {"資料源": (0, 0)}, packwords=frozenset({"資料源"}))
        self.assertEqual((kept, [d[1] for d in dropped]), ([], ["x_other_pack"]))

    def test_nfkc_before_splitting(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.tsv")
            with open(p, "w", encoding="utf-8") as f:
                f.write("\u2f00切都是\tx\n我就爛\uf900\tx\n")      # Kangxi radical one; a compatibility ideograph
            cands, _, _ = T.read_candidates([p])
        self.assertIn("一切都是", cands)
        self.assertIn("我就爛" + __import__("unicodedata").normalize("NFKC", "\uf900"), cands)

    def test_missing_x_manifest_is_a_clear_stop(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit) as e:
            T.read_x(d)
        self.assertIn("run --emit-x first", str(e.exception))

    def test_fewer_than_x_is_dropped_more_than_x_is_kept(self):
        kept, dropped = self.run_judge({"少於者": (20, 5), "多於者": (60, 5), "同數者": (30, 5)}, {"少於者": "它拼的詞甲", "多於者": "它拼的詞乙", "同數者": "它拼的詞丙"},
                                       {"它拼的詞甲": (40, 5), "它拼的詞乙": (40, 5), "它拼的詞丙": (30, 5)})
        self.assertEqual(kept, ["多於者"])
        self.assertEqual({d[0]: (d[1], d[2], d[3], d[4]) for d in dropped},
                         {"少於者": ("x_not_fewer", "它拼的詞甲", 20, 40), "同數者": ("x_not_fewer", "它拼的詞丙", 30, 30)})

    def test_two_candidates_with_the_same_reading_only_the_higher_is_judged(self):
        adm = {"同音多": (50, 5), "同音少": (20, 5)}
        rd = {"同音多": ["a"], "同音少": ["a"]}
        kept, dropped = self.run_judge(adm, {"同音多": "拼詞", "同音少": "別的拼詞"}, {"拼詞": (10, 5)}, rd)
        self.assertEqual(kept, ["同音多"])
        self.assertEqual(dropped, [("同音少", "same_reading", "同音多", 20, 50)])
        # the higher one is the one judged: if it loses to X the pair leaves nothing, the lower one is not promoted
        kept, dropped = self.run_judge(adm, {"同音多": "拼詞", "同音少": "別的拼詞"}, {"拼詞": (90, 5)}, rd)
        self.assertEqual((kept, sorted((d[0], d[1]) for d in dropped)), ([], [("同音多", "x_not_fewer"), ("同音少", "same_reading")]))

    def test_first_in_both_profiles_is_already_right(self):
        kept, dropped = self.run_judge({"打對詞": (50, 5)}, {"打對詞": "打對詞"}, {}, fs={"打對詞": "打對詞"})
        self.assertEqual((kept, [d[1] for d in dropped]), ([], ["already_right"]))

    def test_first_in_chat_but_not_in_formal_is_not_dropped_as_already_right(self):
        kept, dropped = self.run_judge({"只聊天對": (50, 5)}, {"只聊天對": "只聊天對"}, {}, fs={"只聊天對": "書面的別字"})
        self.assertEqual((kept, dropped), (["只聊天對"], []))        # no X to give way (counts2 is not even consulted), the pack helps the formal profile

    def test_x_missing_from_the_second_counts_stops_with_a_mismatch_message(self):
        with self.assertRaises(SystemExit) as e:
            self.run_judge({"詞詞": (50, 5)}, {"詞詞": "沒算到"}, {})
        self.assertIn("does not match meme-x-strings.txt", str(e.exception))


def run_main(*argv):
    with mock.patch.object(sys, "argv", ["build_meme_pack.py", *argv]), contextlib.redirect_stdout(io.StringIO()):
        T.main()


class Pipeline(unittest.TestCase):
    """The three stages through main(): candidates → counts → --emit-x → counts → final."""

    CAND = ("有人氣\t統神\n甘我屁事\t統神\n我就爛，你能拿我怎樣\t統神\n街頭字典詞\t街頭\n看板二詞\tptt\nJoJo立\t迷因Wiki\n已有詞\tptt\n同音多\tptt\n同音少\tptt\n"
            "已打對\tptt\n只聊天對\tptt\n")
    C1 = {"有人氣": (30, 5), "甘我屁事": (12, 3), "我就爛": (40, 6), "你能拿我怎樣": (15, 4), "看板二詞": (99, 2), "已有詞": (99, 9), "同音多": (50, 5),
          "同音少": (20, 5), "已打對": (60, 5), "只聊天對": (45, 5)}
    X = {"有人氣": "有人氣", "甘我屁事": "拼出詞乙", "我就爛": "拼出詞甲", "你能拿我怎樣": "拼出詞丙", "同音多": "拼出詞丁", "同音少": "拼出詞戊", "已打對": "已打對", "只聊天對": "只聊天對"}
    FORMAL = {"有人氣": "有人氣", "已打對": "已打對"}      # first place in the formal profile: only these two are right in both profiles
    # X articles: 甲 < 40 keeps 我就爛; 乙 = 12 ties 甘我屁事 (12) and drops it; 丙 > 15 drops 你能拿我怎樣; 丁 < 50 keeps 同音多
    C2 = {"拼出詞甲": (5, 3), "拼出詞乙": (12, 3), "拼出詞丙": (80, 6), "拼出詞丁": (7, 3), "拼出詞戊": (1, 1), "有人氣": (30, 5), "已打對": (60, 5)}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.work, self.out = os.path.join(t, "work"), os.path.join(t, "out")
        self.cand, self.c1, self.c2 = (os.path.join(t, n) for n in ("cand.tsv", "c1.tsv", "c2.tsv"))
        put(self.cand, self.CAND)
        for patch in (mock.patch.object(IT, "check_shipping_lm"), mock.patch.object(T, "have_words", lambda: frozenset({"已有詞"})),
                      mock.patch.object(T, "pack_words", lambda: frozenset()), mock.patch.object(B, "make_readings", fake_readings),
                      mock.patch.object(B, "scores", lambda base: dict(SC)), mock.patch.object(B, "base_lexicon", lambda: None),
                      mock.patch.object(T, "decoder", lambda: fake_decoder(chat=self.X, formal=self.FORMAL)),
                      mock.patch.object(T, "MIN_WORDS", 1)):
            patch.start()
            self.addCleanup(patch.stop)

    def args(self, *more):
        return ["--cand", self.cand, "--work", self.work, "--out", self.out, "--collisions", os.path.join(self.tmp.name, "none.tsv"),
                "--readings", os.path.join(self.tmp.name, "none-r.tsv"), *more]

    def stage1(self):
        run_main(*self.args("--emit-candidates"))
        self.cands_sha = IT.sha256_text(get(os.path.join(self.work, "meme-candidates.txt")))
        put(self.c1, counts_text(self.C1, self.cands_sha))

    def stage2(self):
        run_main(*self.args("--emit-x", "--counts", self.c1))
        sha = IT.sha256_text(get(os.path.join(self.work, "meme-x-strings.txt")))
        put(self.c2, counts_text(self.C2, sha))

    def final(self):
        run_main(*self.args("--counts", self.c1, self.c2))

    def test_candidate_file_has_the_split_pieces_and_no_ascii_strings(self):
        self.stage1()
        lines = get(os.path.join(self.work, "meme-candidates.txt")).splitlines()
        self.assertIn("我就爛", lines)
        self.assertIn("你能拿我怎樣", lines)
        self.assertNotIn("JoJo立", lines)
        self.assertEqual(lines, T.bsorted(lines))

    def test_emit_x_writes_chat_x_hashed_and_the_strings_file_one_x_per_line(self):
        self.stage1()
        self.stage2()
        rows = [l.split("\t") for l in get(os.path.join(self.work, "meme-x.tsv")).splitlines()]
        self.assertEqual({r[0]: r[2] for r in rows}, self.X)                    # chat X of every admitted candidate
        self.assertEqual({r[0]: r[3] for r in rows}, {w: self.FORMAL.get(w, "？" + w) for w in self.X})
        self.assertNotIn("看板二詞", {r[0] for r in rows})                      # below the threshold: not decoded
        self.assertNotIn("已有詞", {r[0] for r in rows})                        # in the lexicon
        self.assertNotIn("街頭字典詞", {r[0] for r in rows})                    # no PTT count
        strings = get(os.path.join(self.work, "meme-x-strings.txt")).splitlines()
        self.assertEqual(strings, T.bsorted(set(self.X[r[0]] for r in rows)))
        self.assertEqual(len(strings), len(set(strings)))
        meta = json.loads(get(os.path.join(self.work, "meme-x.json")))
        self.assertEqual(meta["meme-x.tsv"], IT.sha256_text(get(os.path.join(self.work, "meme-x.tsv"))))

    def test_x_is_the_chat_first_place_and_the_formal_one_is_only_a_fourth_column(self):
        seen = []
        self.stage1()
        with mock.patch.object(T, "decoder", lambda: fake_decoder(chat=self.X, formal={w: "書面不同" for w in self.X}, seen=seen)):
            run_main(*self.args("--emit-x", "--counts", self.c1))
        self.assertEqual(set(seen), {("chat", False), ("formal", False)})
        rows = [l.split("\t") for l in get(os.path.join(self.work, "meme-x.tsv")).splitlines()]
        self.assertEqual({r[0]: (r[2], r[3]) for r in rows}, {w: (x, "書面不同") for w, x in self.X.items()})
        self.assertNotIn("書面不同", get(os.path.join(self.work, "meme-x-strings.txt")))        # the second count is of the chat X strings only

    def test_final_stage_applies_the_first_place_rule(self):
        self.stage1()
        self.stage2()
        self.final()
        words = {l.split("\t")[1] for l in get(os.path.join(self.out, "meme-add.tsv")).splitlines()}
        self.assertEqual(words, {"我就爛", "同音多", "只聊天對"})
        self.assertTrue(all(l.endswith("\tmeme") for l in get(os.path.join(self.out, "meme-add.tsv")).splitlines()))
        self.assertEqual(float(get(os.path.join(self.out, "meme-add.tsv")).splitlines()[0].split("\t")[2]), -4.0)       # the base score -7 plus BONUS 3
        dropped = {l.split("\t")[0]: l.split("\t")[1] for l in get(os.path.join(self.work, "dropped.tsv")).splitlines()[1:]}
        self.assertEqual(dropped, {"甘我屁事": "x_not_fewer", "你能拿我怎樣": "x_not_fewer", "有人氣": "already_right", "已打對": "already_right", "同音少": "same_reading"})
        src = get(os.path.join(self.out, "meme-sources.tsv")).splitlines()
        self.assertIn("我就爛\t統神\t40\t6\t", src)

    def test_meme_json_records_the_header_fields_of_both_counts_files_and_the_x_hashes(self):
        self.stage1()
        self.stage2()
        self.final()
        m = json.loads(get(os.path.join(self.out, "meme.json")))
        for key, path in (("counts_candidates", self.c1), ("counts_x", self.c2)):
            head, _ = T.read_counts(path)
            self.assertEqual({k: m[key][k] for k in T.HEAD_FIELDS}, head)
            self.assertEqual(m[key]["sha256"], B.sha256_file(path))
        self.assertEqual(m["meme_x"], json.loads(get(os.path.join(self.work, "meme-x.json"))))
        self.assertEqual(m["candidate_stats"]["dropped_ascii"], 1)
        self.assertEqual(m["files"]["meme-add.tsv"]["sha256"], B.sha256_file(os.path.join(self.out, "meme-add.tsv")))
        self.assertEqual(m["sources"], {"統神": 1, "ptt": 2})
        self.assertEqual(m["candidate_files"], {self.cand: B.sha256_file(self.cand)})

    def test_the_x_article_count_is_that_of_the_x_string_not_the_row(self):
        # 拼出詞甲 is counted as a string in the second file: 39 articles (< 40) keeps 我就爛, 40 drops it. The row 我就爛 itself has 40 in the first file.
        self.stage1()
        self.stage2()
        for n, kept in ((39, True), (40, False)):
            put(self.c2, counts_text(dict(self.C2, 拼出詞甲=(n, 5)), IT.sha256_text(get(os.path.join(self.work, "meme-x-strings.txt")))))
            self.final()
            self.assertEqual("我就爛" in get(os.path.join(self.out, "meme-add.tsv")), kept)

    def test_a_changed_meme_x_tsv_stops_the_final_stage(self):
        self.stage1()
        self.stage2()
        p = os.path.join(self.work, "meme-x.tsv")
        put(p, get(p).replace("拼出詞甲", "拼出詞丙"))
        with self.assertRaises(SystemExit) as e:
            self.final()
        self.assertIn("meme-x.tsv", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(self.out, "meme-add.tsv")))

    def test_counts_files_for_the_wrong_input_stop(self):
        self.stage1()
        put(self.c1, counts_text(self.C1, "0" * 64))
        with self.assertRaises(SystemExit):
            run_main(*self.args("--emit-x", "--counts", self.c1))
        put(self.c1, counts_text(self.C1, self.cands_sha))
        self.stage2()
        put(self.c2, counts_text(self.C2, "0" * 64))
        with self.assertRaises(SystemExit):
            self.final()
        put(self.c2, counts_text(self.C2, IT.sha256_text(get(os.path.join(self.work, "meme-x-strings.txt"))), tree="u" * 64))
        with self.assertRaises(SystemExit):
            self.final()

    def test_fewer_than_30_words_stops_before_anything_is_written_to_the_pack_dir(self):
        self.stage1()
        self.stage2()
        with mock.patch.object(T, "MIN_WORDS", 30), self.assertRaises(SystemExit) as e:
            self.final()
        self.assertIsInstance(e.exception.code, str)                                            # a message, so the exit status is non-zero
        self.assertIn("STOP", e.exception.code)
        self.assertFalse(os.path.exists(self.out))
        self.assertTrue(os.path.exists(os.path.join(self.work, "report.txt")))                 # the per-source numbers are still reported

    def test_rebuild_from_the_same_inputs_is_byte_identical(self):
        self.stage1()
        self.stage2()
        self.final()
        first = {n: get(os.path.join(self.out, n)) for n in ("meme-add.tsv", "meme-sources.tsv", "meme.json")}
        self.final()
        self.assertEqual(first, {n: get(os.path.join(self.out, n)) for n in first})
        x = get(os.path.join(self.work, "meme-x.tsv"))
        self.stage2()
        self.assertEqual(x, get(os.path.join(self.work, "meme-x.tsv")))


class Collisions(unittest.TestCase):
    """The (c) rule: a meme row that pushes an existing word (lexicon layer, anime or it pack) off first place on any emitted reading, in either profile, is dropped."""

    def run_build(self, decode, packwords=frozenset(), have=frozenset(), words=("擠人詞", "安分詞"), x=None, readings=fake_readings, collisions=(), counts=None):
        """x: {word: (chat X, formal first place)}, default ('？'+word, '？'+word); every X is counted once in the second file."""
        cands = {w: {"s"} for w in words}
        c1 = counts or {w: (30, 5) for w in words}
        _, reading, _, _, _ = T.prepare(cands, c1, have, packwords, readings)
        x = {w: (x or {}).get(w, ("？" + w, "？" + w)) for w in reading}
        table = [(w, " ".join(r), *x[w]) for w, r in reading.items()]
        c2 = {xc: (1, 1) for xc, _ in x.values()}
        with tempfile.TemporaryDirectory() as d:
            coll = os.path.join(d, "meme-collisions.tsv")
            put(coll, "".join("\t".join(r) + "\n" for r in collisions))
            return T.build(cands, c1, table, c2, decode, readings, have, packwords, coll, None, SC)

    @staticmethod
    def pack_words(files):
        return {l.split("\t")[1] for l in files["meme-add.tsv"].splitlines()}

    def test_a_meme_word_that_displaces_an_anime_or_it_pack_word_is_dropped(self):
        def decode(pairs, prof, meme=None):
            return [w for w, _ in pairs] if meme else ["動漫詞" if w == "擠人詞" and prof == "chat" else "？" + w for w, _ in pairs]
        files, m, col, dropped, unread, ldrop = self.run_build(decode, packwords=frozenset({"動漫詞"}), x={"擠人詞": ("動漫詞", "？擠人詞")})
        self.assertEqual(self.pack_words(files), {"安分詞"})
        # X is another pack's word: the earlier pack wins at the first-place rule already, before the (c) check
        self.assertEqual([(d[0], d[1]) for d in dropped], [("擠人詞", "x_other_pack")])
        self.assertEqual((m["stages"]["dropped_x_other_pack"], ldrop), (1, []))

    def test_displacing_a_lexicon_word_that_is_x_in_the_chat_main_reading_is_the_first_place_rules_call(self):
        decode = lambda pairs, prof, meme=None: [w for w, _ in pairs] if meme else ["舊詞庫詞" if w == "擠人詞" and prof == "chat" else "？" + w for w, _ in pairs]
        files, m, col, dropped, unread, ldrop = self.run_build(decode, have=frozenset({"舊詞庫詞"}), x={"擠人詞": ("舊詞庫詞", "？擠人詞")})
        self.assertEqual(self.pack_words(files), {"擠人詞", "安分詞"})
        self.assertEqual(ldrop, [])

    def test_displacing_a_lexicon_word_in_the_formal_profile_is_dropped(self):
        decode = lambda pairs, prof, meme=None: [w for w, _ in pairs] if meme else ["舊詞庫詞" if w == "擠人詞" and prof == "formal" else "？" + w for w, _ in pairs]
        files, m, col, dropped, unread, ldrop = self.run_build(decode, have=frozenset({"舊詞庫詞"}), x={"擠人詞": ("？擠人詞", "舊詞庫詞")})
        self.assertEqual(self.pack_words(files), {"安分詞"})
        self.assertEqual([(w, o, p) for _, w, o, p in ldrop], [("擠人詞", "舊詞庫詞", "formal")])

    def test_displacing_a_lexicon_word_that_is_not_the_chat_x_is_dropped(self):
        # the existing word is first place in chat too, but it is not the X the first-place rule judged (X is a decoder-made string): not exempt
        decode = lambda pairs, prof, meme=None: [w for w, _ in pairs] if meme else ["舊詞庫詞" if w == "擠人詞" and prof == "chat" else "？" + w for w, _ in pairs]
        files, *_ = self.run_build(decode, have=frozenset({"舊詞庫詞"}), x={"擠人詞": ("？擠人詞", "？擠人詞")})
        self.assertEqual(self.pack_words(files), {"安分詞"})

    def test_a_sandhi_variant_row_that_displaces_a_lexicon_word_drops_the_word(self):
        rd = lambda ws: {w: (["ㄅㄨˋ", "ㄉㄢˋ"], False) for w in ws}

        def decode(pairs, prof, meme=None):
            return [w for w, _ in pairs] if meme else ["舊詞庫詞" if syls[0] == "ㄅㄨˊ" else "？" + w for w, syls in pairs]
        files, m, col, dropped, unread, ldrop = self.run_build(decode, have=frozenset({"舊詞庫詞"}), words=("不但",), readings=rd)
        self.assertEqual(files["meme-add.tsv"], "")
        self.assertEqual([(r, w, o) for r, w, o, _ in ldrop], [("ㄅㄨˊ ㄉㄢˋ", "不但", "舊詞庫詞")])

    def test_sandhi_rows_and_the_bonus_scores_come_from_the_shared_pack_rows(self):
        rd = lambda ws: {"不但": (["ㄅㄨˋ", "ㄉㄢˋ"], False)}
        files, *_ = self.run_build(fake_decoder(), words=("不但",), readings=rd)
        rows = [l.split("\t") for l in files["meme-add.tsv"].splitlines()]
        self.assertEqual([r[0] for r in rows], ["ㄅㄨˋ-ㄉㄢˋ", "ㄅㄨˊ-ㄉㄢˋ"])
        self.assertEqual(float(rows[1][2]), -7.5)

    def test_collisions_tsv_exclusions_are_applied_before_the_same_reading_winner_is_chosen(self):
        rd = lambda ws: {w: (["a", "b", "c"], False) for w in ws}
        files, m, col, dropped, *_ = self.run_build(fake_decoder(), words=("同音多", "同音少"), readings=rd, counts={"同音多": (50, 5), "同音少": (20, 5)},
                                                    collisions=[("a b c", "同音少", "同音多", "理由")])
        self.assertEqual(self.pack_words(files), {"同音少"})          # the excluded higher word does not block the lower one
        self.assertEqual((dropped, m["stages"]["excluded_by_meme_collisions_tsv"]), ([], 1))

    def test_check_set_is_seeded_capped_and_skips_check_readings(self):
        info = {"order": [f"{c}字詞" for c in "甲乙丙丁戊己庚辛壬癸"], "reading": {}, "check": {}}
        info["reading"] = {w: ["a", "b", "c"] for w in info["order"]}
        info["check"] = {w: w == "甲字詞" for w in info["order"]}
        rows = T.check_words(info, seed=1, n=4)
        self.assertEqual((len(rows), rows), (4, T.check_words(info, seed=1, n=4)))
        self.assertNotIn("|甲字詞|", "".join(T.check_words(info, seed=1, n=99)))
        self.assertEqual(len(T.check_words(info, n=99)), 9)         # fewer than n: all of them


class DecodeCommand(unittest.TestCase):
    def test_the_shipping_decode_runs_acg_and_it_with_the_meme_rows_appended_to_it(self):
        with tempfile.TemporaryDirectory() as packs, tempfile.TemporaryDirectory() as meme:
            put(os.path.join(packs, "acg-add.tsv"), "k\t動漫詞\t-1\tacg\n")
            put(os.path.join(packs, "it-add.tsv"), "k\t資訊詞\t-1\tit\n")
            put(os.path.join(meme, "acg-add.tsv"), "k\t迷因詞\t-1\tmeme\n")
            seen = []

            def run(cmd, **_):
                d = cmd[cmd.index("--packs-dir") + 1]
                seen.append((cmd, get(os.path.join(d, "it-add.tsv")), get(os.path.join(d, "acg-add.tsv"))))
                put(cmd[cmd.index("--dump") + 1], "1\t1\t答案\t0\n")

            with mock.patch.object(T.subprocess, "run", run), mock.patch.object(B, "eval_bin", lambda: "shanjie-eval"), mock.patch.object(IT, "SHIP_ARGS", ["--ship-flag"]):
                self.assertEqual(T.decoder(packs)([("詞", ["a"])], "formal", meme), ["答案"])
                T.decoder(packs)([("詞", ["a"])], "chat")
            cmd, it, acg = seen[0]
            self.assertEqual((cmd[cmd.index("--packs") + 1], cmd[cmd.index("--profile") + 1], it, acg), ("acg,it", "formal", "k\t資訊詞\t-1\tit\nk\t迷因詞\t-1\tmeme\n", "k\t動漫詞\t-1\tacg\n"))
            self.assertIn("--ship-flag", cmd)                                                    # the same shipping flags as the it pack's decoder
            self.assertEqual(cmd[cmd.index("--threads") + 1], str(B.THREADS))
            self.assertEqual(seen[1][1], "k\t資訊詞\t-1\tit\n")                                 # no meme rows when decoding the baseline

    def test_a_missing_pack_file_stops_when_the_decoder_is_made(self):
        with tempfile.TemporaryDirectory() as packs:
            put(os.path.join(packs, "acg-add.tsv"), "")
            with self.assertRaises(SystemExit):
                T.decoder(packs)


if __name__ == "__main__":
    unittest.main()
