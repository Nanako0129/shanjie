"""Tests for tools/build_meme_pack.py . Run: python3 -m unittest tools/test_build_meme_pack.py   (from the repo root).
No network, no evaluation CLI, no real corpus: candidates and counts are small fixtures, the decoder and the reading tool are fakes.
The counting tool is not in this tree; its output is imitated (three header lines, then string, articles, boards)."""
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
    """same reading for the two 同音 words, otherwise one made-up syllable per character; words starting 無讀 have no reading."""
    return {w: (["a", "b", "c"] if w.startswith("同音") else [f"s{ord(c)}" for c in w], False) for w in words if not w.startswith("無讀")}


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
    return f"# tree_sha256 {tree}\n# input_sha256 {input_sha}\n# tool_commit {commit}\n" + "".join(f"{s}\t{a}\t{b}\n" for s, (a, b) in rows.items())


class Candidates(unittest.TestCase):
    def read(self, text, name="forum.tsv"):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, name)
            put(p, text)
            return T.read_candidates([p])

    def test_length_and_ascii_filters_and_the_default_source(self):
        cands, st, _ = self.read("# note\n\n一二\n一二三四五六七八九十\n一二三四五六七八九十一\n單\nJoJo立\n三小4\n有人氣\tforum\n")
        self.assertEqual(cands, {"一二": {"forum"}, "一二三四五六七八九十": {"forum"}, "有人氣": {"forum"}})   # source defaults to the file stem
        self.assertEqual((st["dropped_ascii"], st["dropped_length"], st["candidates"]), (2, 2, 3))

    def test_quote_lines_split_at_punctuation_and_each_piece_is_a_candidate(self):
        cands, st, _ = self.read("我就爛，你能拿我怎樣！\t統神\n好、啦 嗎\t統神\n一二三四五六七八九十一，不錯喔\t統神\n")
        self.assertEqual(cands, {"我就爛": {"統神"}, "你能拿我怎樣": {"統神"}, "不錯喔": {"統神"}})
        self.assertEqual(st["dropped_length"], 4)       # 好、啦、嗎 and the 11-character piece

    def test_full_width_letters_and_digits_count_as_english_and_digits(self):
        cands, st, _ = self.read("三小４\n\uff2a\uff4f\uff2a\uff4f立\n三小4\n正常詞\n")
        self.assertEqual((cands, st["dropped_ascii"]), ({"正常詞": {"forum"}}, 3))

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
            self.assertEqual(T.read_counts(p), ({"tree_sha256": TREE, "input_sha256": "i" * 64, "tool_commit": "abc123"}, {"甲甲": (12, 4)}))
            put(p, "# tree_sha256 x\n# input_sha256 y\n甲甲\t1\t1\n")
            with self.assertRaises(SystemExit):
                T.read_counts(p)

    def test_a_prefixed_tree_header_reads_the_same_as_the_plain_one(self):
        with tempfile.TemporaryDirectory() as d:
            new, old = os.path.join(d, "new.tsv"), os.path.join(d, "old.tsv")
            text = counts_text({"甲甲": (12, 4)}, "i" * 64)
            put(new, text)
            put(old, text.replace("# tree_sha256", "# old_tree_sha256"))
            self.assertEqual(T.read_counts(old), T.read_counts(new))


class Prepare(unittest.TestCase):
    def test_thresholds_boards_3_articles_10(self):
        cands = {w: {"s"} for w in ("看板二文章十", "看板三文章十", "看板三文章九", "看板五文章五十", "網站詞")}
        counts = {"看板二文章十": (10, 2), "看板三文章十": (10, 3), "看板三文章九": (9, 3), "看板五文章五十": (50, 5)}
        admitted, reading, check, st, unread = T.prepare(cands, counts, frozenset(), frozenset(), fake_readings)
        self.assertEqual(sorted(admitted), ["看板三文章十", "看板五文章五十"])
        self.assertEqual((st["below_threshold"], st["no_count"]), (2, 1))      # 網站詞 has no forum count: not admitted
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

    def test_first_in_both_profiles_is_already_right_for_beauty_and_kept_for_meme(self):
        for tag, want in (("beauty", ([], ["already_right"])), ("meme", (["打對詞"], []))):
            kept, dropped = T.judge({"打對詞": (50, 5)}, {"打對詞": ["s1"]}, {"打對詞": "打對詞"}, {"打對詞": "打對詞"}, {}, frozenset(), tag)
            self.assertEqual((kept, [d[1] for d in dropped]), want)

    def test_a_plus_pair_keeps_both_words_each_judged_on_its_own_x(self):
        adm, rd = {"同音多": (50, 5), "同音少": (20, 5)}, {"同音多": ["a"], "同音少": ["a"]}
        xs, fs = {"同音多": "拼詞", "同音少": "別的拼詞"}, {"同音多": "書", "同音少": "書"}
        plus = [("a", "同音多", "同音少")]
        self.assertEqual(T.judge(adm, rd, xs, fs, {"拼詞": (10, 5), "別的拼詞": (10, 5)}, frozenset(), "meme", plus)[0], ["同音少", "同音多"] if "同音少" < "同音多" else ["同音多", "同音少"])
        kept, dropped = T.judge(adm, rd, xs, fs, {"拼詞": (10, 5), "別的拼詞": (90, 5)}, frozenset(), "meme", plus)
        self.assertEqual((kept, [(d[0], d[1]) for d in dropped]), (["同音多"], [("同音少", "x_not_fewer")]))       # the pair is not a free pass
        self.assertEqual(T.judge(adm, rd, xs, fs, {"拼詞": (10, 5), "別的拼詞": (10, 5)}, frozenset(), "meme")[0], ["同音多"])      # no + row: only the one with more articles

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

    CAND = ("有人氣\t統神\n甘我屁事\t統神\n我就爛，你能拿我怎樣\t統神\n網站詞\tsite\n看板二詞\tforum\nJoJo立\t迷因Wiki\n已有詞\tforum\n同音多\tforum\n同音少\tforum\n"
            "已打對\tforum\n只聊天對\tforum\n")
    C1 = {"有人氣": (30, 5), "甘我屁事": (12, 3), "我就爛": (40, 6), "你能拿我怎樣": (15, 4), "看板二詞": (99, 2), "已有詞": (99, 9), "同音多": (50, 5),
          "同音少": (20, 5), "已打對": (60, 5), "只聊天對": (45, 5)}
    X = {"有人氣": "有人氣", "甘我屁事": "拼出詞乙", "我就爛": "拼出詞甲", "你能拿我怎樣": "拼出詞丙", "同音多": "拼出詞丁", "同音少": "拼出詞戊", "已打對": "已打對", "只聊天對": "只聊天對"}
    ALLOW = "# the allow list\n\n有人氣\tmeme\n甘我屁事\tmeme\n我就爛\toffensive\n同音多\tmeme\n同音少\tmeme\n已打對\tmeme\n只聊天對\tmeme\n"
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
        self.allow = os.path.join(t, "allow.tsv")
        self.extra = os.path.join(t, "extra.tsv")
        put(self.allow, self.ALLOW)
        for patch in (mock.patch.object(IT, "check_shipping_lm"), mock.patch.object(T, "have_words", lambda: frozenset({"已有詞"})),
                      mock.patch.object(T, "pack_words", lambda: frozenset()), mock.patch.object(B, "make_readings", fake_readings),
                      mock.patch.object(B, "scores", lambda base: dict(SC)), mock.patch.object(B, "base_lexicon", lambda: None),
                      mock.patch.object(T, "decoder", lambda *a, **k: fake_decoder(chat=self.X, formal=self.FORMAL)),
                      mock.patch.object(T, "MIN_WORDS", 1)):
            patch.start()
            self.addCleanup(patch.stop)

    def args(self, *more, allow=True):
        # --extra-readings points into the temp folder (absent unless a test writes it), so the real data/packs/meme-extra-readings.tsv is never read
        return ["--cand", self.cand, "--work", self.work, "--out", self.out, "--collisions", os.path.join(self.tmp.name, "none.tsv"),
                "--readings", os.path.join(self.tmp.name, "none-r.tsv"), *(["--allow", self.allow, "--extra-readings", self.extra] if allow else []), *more]

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
        self.assertNotIn("網站詞", {r[0] for r in rows})                    # no forum count
        strings = get(os.path.join(self.work, "meme-x-strings.txt")).splitlines()
        self.assertEqual(strings, T.bsorted(set(self.X[r[0]] for r in rows)))
        self.assertEqual(len(strings), len(set(strings)))
        meta = json.loads(get(os.path.join(self.work, "meme-x.json")))
        self.assertEqual(meta["meme-x.tsv"], IT.sha256_text(get(os.path.join(self.work, "meme-x.tsv"))))

    def test_x_is_the_chat_first_place_and_the_formal_one_is_only_a_fourth_column(self):
        seen = []
        self.stage1()
        with mock.patch.object(T, "decoder", lambda *a, **k: fake_decoder(chat=self.X, formal={w: "書面不同" for w in self.X}, seen=seen)):
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
        self.assertEqual(words, {"我就爛", "同音多", "只聊天對", "有人氣", "已打對"})         # the two already right in both profiles are shipped too; 你能拿我怎樣 is not on the list
        self.assertTrue(all(l.endswith("\tmeme") for l in get(os.path.join(self.out, "meme-add.tsv")).splitlines()))
        self.assertEqual(float(get(os.path.join(self.out, "meme-add.tsv")).splitlines()[0].split("\t")[2]), -4.0)       # the base score -7 plus BONUS 3
        dropped = {l.split("\t")[0]: l.split("\t")[1] for l in get(os.path.join(self.work, "dropped.tsv")).splitlines()[1:]}
        self.assertEqual(dropped, {"甘我屁事": "x_not_fewer", "同音少": "same_reading"})
        src = get(os.path.join(self.out, "meme-sources.tsv")).splitlines()
        self.assertEqual(src[0], "word\tsources\tclass\tarticles\tboards\tnote")
        self.assertIn("我就爛\t統神\toffensive\t40\t6\t", src)
        self.assertEqual(json.loads(get(os.path.join(self.out, "meme.json")))["stages"]["not_allowed"], 1)

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
        self.assertEqual(m["sources"], {"統神": 2, "forum": 3})
        self.assertEqual(m["allow"], {"file": "allow.tsv", "sha256": B.sha256_file(self.allow), "rows": 7, "shipped": {"meme": 4, "offensive": 1}})
        self.assertEqual(m["candidate_files"], {"cand.tsv": B.sha256_file(self.cand)})        # names only, no temp-dir path
        self.assertNotIn(self.tmp.name, get(os.path.join(self.out, "meme.json")))

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

    def test_two_candidate_files_with_the_same_name_stop(self):
        other = os.path.join(self.tmp.name, "sub", "cand.tsv")
        os.makedirs(os.path.dirname(other))
        put(other, "別的詞\n")
        with self.assertRaises(SystemExit) as e:
            run_main("--cand", self.cand, other, "--work", self.work, "--emit-candidates")
        self.assertIn("different names", str(e.exception))

    def test_native_pack_is_refused_for_beauty(self):
        with self.assertRaises(SystemExit) as e:
            run_main("--pack", "beauty", "--native-pack", *self.args(allow=False))
        self.assertIn("--native-pack only applies to --pack meme", str(e.exception))

    def test_native_pack_hands_the_built_rows_to_the_decoder_as_meme_add_tsv(self):
        made, dirs = [], []
        self.stage1()
        self.stage2()

        def make(*a, **k):
            made.append(k)
            inner = fake_decoder(chat=self.X, formal=self.FORMAL)

            def decode(pairs, prof, meme=None):
                if meme:
                    dirs.append(sorted(os.listdir(meme)))
                return inner(pairs, prof, meme)
            return decode
        with mock.patch.object(T, "decoder", make):
            run_main(*self.args("--native-pack", "--counts", self.c1, self.c2))
        self.assertEqual(made, [{"tag": "meme", "native": True}])
        self.assertTrue(dirs and all(d == ["meme-add.tsv"] for d in dirs))

    def kept_order(self, *more):
        self.stage1()
        self.stage2()
        run_main(*self.args(*more, "--counts", self.c1, self.c2))
        return [l.split("\t")[1] for l in get(os.path.join(self.out, "meme-add.tsv")).splitlines()]

    def test_rows_wrong_in_chat_come_before_the_ones_already_right_in_chat(self):
        self.assertEqual(self.kept_order(), ["同音多", "我就爛", "已打對", "只聊天對", "有人氣"])          # the last three are first in chat without the pack

    def test_rows_of_a_plus_pair_come_last(self):
        coll = os.path.join(self.tmp.name, "coll.tsv")
        put(coll, "a b c\t同音多\t+同音少\t兩個都留\n")
        order = self.kept_order("--collisions", coll)
        self.assertEqual(order, ["我就爛", "已打對", "只聊天對", "有人氣", "同音多", "同音少"])      # the pair keeps both, last; without the line 同音多 comes first and 同音少 is dropped

    def test_a_candidate_not_on_the_list_is_not_shipped_and_does_not_block_a_same_reading_list_word(self):
        put(self.allow, self.ALLOW.replace("同音多\tmeme\n", ""))
        order = self.kept_order()
        self.assertIn("同音少", order)                      # 同音多 has more articles but is not on the list: it does not block 同音少
        self.assertNotIn("同音多", order)
        self.assertNotIn("同音多", get(os.path.join(self.work, "dropped.tsv")))      # removed before the rules, so not a dropped row either
        self.assertEqual(json.loads(get(os.path.join(self.out, "meme.json")))["stages"]["not_allowed"], 2)      # 同音多 and 你能拿我怎樣

    def test_a_missing_allow_file_stops_both_stages(self):
        self.stage1()
        os.remove(self.allow)
        with self.assertRaises(SystemExit) as e:
            run_main(*self.args("--emit-x", "--counts", self.c1))
        self.assertIn("allow list is missing", str(e.exception))
        self.allow = os.path.join(self.tmp.name, "allow.tsv")
        put(self.allow, self.ALLOW)
        self.stage2()
        os.remove(self.allow)
        with self.assertRaises(SystemExit):
            self.final()
        self.assertFalse(os.path.exists(self.out))

    def test_a_bad_allow_file_stops_naming_the_line(self):
        for text, line in (("有人氣\tmeme\n甘我屁事\tother\n", ":2:"), ("有人氣\n", ":1:"), ("有人氣\tmeme\n\n有人氣\toffensive\n", ":3:")):
            put(self.allow, text)
            with self.assertRaises(SystemExit) as e:
                T.read_allow(self.allow)
            self.assertIn(line, str(e.exception))

    def test_allow_is_nfkc_normalised_like_candidates(self):
        put(self.allow, "\u2f00切都是\tmeme\n")
        self.assertEqual(T.read_allow(self.allow)[0], {"一切都是": "meme"})

    def test_allow_is_refused_for_beauty_and_exclude_for_meme(self):
        with self.assertRaises(SystemExit) as e:
            run_main("--pack", "beauty", *self.args())
        self.assertIn("--allow only applies to --pack meme", str(e.exception))
        for flag in ("--exclude", "--exclude-private"):
            with self.assertRaises(SystemExit) as e:
                run_main(*self.args(flag, self.allow))
            self.assertIn("does not take --exclude", str(e.exception))

    def test_emit_x_does_not_depend_on_the_allow_list(self):
        self.stage1()
        outs = []
        for text in (self.ALLOW, "有人氣\toffensive\n"):
            put(self.allow, text)
            run_main(*self.args("--emit-x", "--counts", self.c1))
            outs.append({n: get(os.path.join(self.work, n)) for n in ("meme-x.tsv", "meme-x-strings.txt", "meme-x.json")})
        self.assertEqual(outs[0], outs[1])

    def test_allow_missing_lists_every_list_word_that_is_not_shipped_with_a_reason(self):
        extra = "不是候選\tmeme\n看板二詞\tmeme\n已有詞\toffensive\n無讀音詞\tmeme\n"
        put(self.allow, self.ALLOW + extra)
        put(self.cand, self.CAND + "無讀音詞\tforum\n")
        self.C1 = dict(self.C1, 無讀音詞=(30, 5))
        coll = os.path.join(self.tmp.name, "coll.tsv")
        self.stage1()
        self.stage2()
        run_main(*self.args("--counts", self.c1, self.c2))
        rows = {r[0]: r[1:] for r in (l.split("\t") for l in get(os.path.join(self.work, "allow-missing.tsv")).splitlines())}
        self.assertEqual({w: (r[0], r[1]) for w, r in rows.items()},
                         {"不是候選": ("meme", "not_a_candidate"), "看板二詞": ("meme", "below_threshold"), "已有詞": ("offensive", "in_lexicon_or_pack"),
                          "無讀音詞": ("meme", "unreadable"), "甘我屁事": ("meme", "x_not_fewer"), "同音少": ("meme", "same_reading")})
        shipped = {l.split("\t")[1] for l in get(os.path.join(self.out, "meme-add.tsv")).splitlines()}
        self.assertEqual(len(shipped) + len(rows), len(T.read_allow(self.allow)[0]))
        # a collisions-file row without + that names a list word as its third column: excluded_by_collisions_file (and it does not block the same-reading word)
        put(coll, "a b c\t同音少\t同音多\t理由\n")
        run_main(*self.args("--collisions", coll, "--counts", self.c1, self.c2))
        rows = {r[0]: r[2] for r in (l.split("\t") for l in get(os.path.join(self.work, "allow-missing.tsv")).splitlines())}
        self.assertEqual((rows["同音多"], "同音少" in rows), ("excluded_by_collisions_file", False))

    def test_the_run_stops_when_the_list_is_not_accounted_for(self):
        self.stage1()
        self.stage2()
        with mock.patch.object(T, "allow_missing", lambda *a: []), self.assertRaises(SystemExit) as e:
            self.final()
        self.assertIn("allow-missing.tsv", e.exception.code)
        self.assertFalse(os.path.exists(self.out))

    def final_files(self):
        self.stage1()
        self.stage2()
        self.final()
        return {n: get(os.path.join(d, n)) for d, n in ((self.out, "meme-add.tsv"), (self.out, "meme-sources.tsv"), (self.out, "meme.json"),
                                                       (self.work, "words-all.txt"), (self.work, "words.txt"))}

    def test_an_extra_reading_adds_one_row_with_the_main_rows_score_and_a_words_all_row(self):
        base = self.final_files()
        put(self.extra, "# comment\n我就爛\tx y z\n")
        new = self.final_files()
        rows = [l.split("\t") for l in new["meme-add.tsv"].splitlines()]
        mine = [r for r in rows if r[1] == "我就爛"]
        self.assertEqual(sorted(r[0] for r in mine), sorted(["x-y-z", "-".join("s" + str(ord(c)) for c in "我就爛")]))
        self.assertEqual(len({r[2] for r in mine}), 1)                                      # the same score under both readings
        added = [l for l in new["meme-add.tsv"].splitlines(True) if l not in base["meme-add.tsv"].splitlines(True)]
        self.assertEqual(len(added), 1)                                                     # nothing else moved: the old file is the new one minus that row
        self.assertEqual("".join(l for l in new["meme-add.tsv"].splitlines(True) if l not in added), base["meme-add.tsv"])
        self.assertEqual(new["words-all.txt"], base["words-all.txt"] + "|我就爛|x y z\n")
        self.assertEqual((new["meme-sources.tsv"], new["words.txt"]), (base["meme-sources.tsv"], base["words.txt"]))
        self.assertEqual((json.loads(new["meme.json"])["extra_readings"], "extra_readings" in json.loads(base["meme.json"])), (1, False))

    def test_an_extra_reading_of_a_check_word_is_listed_in_words_all(self):
        put(self.extra, "我就爛\tx y z\n")
        with mock.patch.object(B, "make_readings", lambda ws: {w: (r, w == "我就爛") for w, (r, _) in fake_readings(ws).items()}):
            words_all = self.final_files()["words-all.txt"]
        self.assertEqual([l for l in words_all.splitlines() if "我就爛" in l], ["|我就爛|x y z"])      # the main reading is CHECK and left out; the confirmed extra one is in

    def test_a_comment_only_extra_file_changes_nothing(self):
        base = self.final_files()
        put(self.extra, "# nothing\n\n")
        self.assertEqual(self.final_files(), base)

    def test_a_bad_extra_readings_row_stops_before_anything_is_written(self):
        main = " ".join("s" + str(ord(c)) for c in "我就爛")
        for text, msg in (("甘我屁事\ta b c d\n", "not shipped in the pack: 甘我屁事"), ("我就爛\tx y\n", "3 characters, the reading has 2 syllables"),
                          (f"我就爛\t{main}\n", "is the word's main reading"), ("我就爛\tx y z\n我就爛\tx y z\n", "is listed twice"),
                          ("我就爛 x y z\n", "expected `word<TAB>reading`")):
            put(self.extra, text)
            self.stage1()
            self.stage2()
            with self.assertRaises(SystemExit) as e:
                self.final()
            self.assertIn(msg, str(e.exception))
            self.assertFalse(os.path.exists(self.out), text)

    def test_an_extra_readings_file_with_a_bom_reads_its_first_word(self):
        put(self.extra, "\ufeff我就爛\tx y z\n")
        self.assertEqual(T.read_extra(self.extra), [("我就爛", ["x", "y", "z"])])

    def test_extra_readings_is_refused_for_beauty(self):
        with self.assertRaises(SystemExit) as e:
            run_main("--pack", "beauty", *self.args(allow=False), "--extra-readings", self.extra)
        self.assertIn("--extra-readings only applies to --pack meme", str(e.exception))

    def test_words_all_is_every_shipped_word_without_check_readings_in_add_order(self):
        self.stage1()
        self.stage2()
        with mock.patch.object(B, "make_readings", lambda ws: {w: (r, w == "我就爛") for w, (r, _) in fake_readings(ws).items()}):
            self.stage2()
            self.final()
        order = [l.split("\t")[1] for l in get(os.path.join(self.out, "meme-add.tsv")).splitlines()]
        want = "".join(f"|{w}|{'a b c' if w.startswith('同音') else ' '.join('s' + str(ord(c)) for c in w)}\n" for w in dict.fromkeys(order) if w != "我就爛")
        self.assertEqual(get(os.path.join(self.work, "words-all.txt")), want)
        self.assertIn("我就爛", order)
        self.assertEqual(len(want.splitlines()), 4)

    def test_fewer_than_the_minimum_words_stops_before_anything_is_written_to_the_pack_dir(self):
        self.stage1()
        self.stage2()
        with mock.patch.object(T, "MIN_WORDS", 6), self.assertRaises(SystemExit) as e:
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

    def run_build(self, decode, packwords=frozenset(), have=frozenset(), words=("擠人詞", "安分詞"), x=None, readings=fake_readings, collisions=(), counts=None, extra=()):
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
            return T.build(cands, c1, table, c2, decode, readings, have, packwords, coll, None, SC, extra=extra)

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

    def test_an_extra_reading_that_displaces_a_lexicon_word_stops_naming_the_reading(self):
        # the main reading is fine; the extra reading x y z pushes 舊詞庫詞 off first place: (c) drops the word, and the stop names why
        def decode(pairs, prof, meme=None):
            return [w for w, _ in pairs] if meme else ["舊詞庫詞" if list(syls) == ["x", "y", "z"] else "？" + w for w, syls in pairs]
        with self.assertRaises(SystemExit) as e:
            self.run_build(decode, have=frozenset({"舊詞庫詞"}), extra=[("擠人詞", ["x", "y", "z"])])
        self.assertIn("擠人詞 (rule (c): it displaces 舊詞庫詞 on x y z, chat)", str(e.exception))

    def test_collisions_a_and_b_are_detected_on_an_extra_reading(self):
        # (a): on the extra reading the pack's first place is another word of the pack; (b): without the pack the first place there is a reference name
        on_a = lambda pairs, prof, meme=None: ["安分詞" if meme and list(syls) == ["x", "y", "z"] else (w if meme else "？" + w) for w, syls in pairs]
        off_b = lambda pairs, prof, meme=None: [w if meme else ("安分詞" if list(syls) == ["x", "y", "z"] else "？" + w) for w, syls in pairs]
        for decode, cond in ((on_a, "a"), (off_b, "b")):
            files, m, col, dropped, unread, ldrop = self.run_build(decode, extra=[("擠人詞", ["x", "y", "z"])])
            self.assertEqual(ldrop, [], cond)
            self.assertGreater(m["unresolved_collision_readings"], 0, cond)
            self.assertIn("x y z", str(col), cond)
            self.assertIn("x-y-z\t擠人詞", files["meme-add.tsv"], cond)

    def test_an_extra_reading_equal_to_the_main_readings_sandhi_row_stops(self):
        rd = lambda ws: {w: (["ㄅㄨˋ", "ㄉㄢˋ"], False) for w in ws}
        with self.assertRaises(SystemExit) as e:
            self.run_build(fake_decoder(), words=("不但",), readings=rd, extra=[("不但", ["ㄅㄨˊ", "ㄉㄢˋ"])])
        self.assertIn("repeats a row of the word's main reading", str(e.exception))

    def test_two_extra_readings_where_one_is_the_others_sandhi_row_stop(self):
        rd = lambda ws: {w: (["x", "y"], False) for w in ws}
        with self.assertRaises(SystemExit) as e:
            self.run_build(fake_decoder(), words=("不但",), readings=rd, extra=[("不但", ["ㄅㄨˋ", "ㄉㄢˋ"]), ("不但", ["ㄅㄨˊ", "ㄉㄢˋ"])])
        self.assertIn("repeats a row of another extra reading of the word", str(e.exception))

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


class Limits(unittest.TestCase):
    def test_the_meme_minimum_is_10_words_and_the_beauty_one_stays_30(self):
        self.assertEqual((T.MIN_WORDS, T.MIN_WORDS_BEAUTY), (10, 30))


class DecodeCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.packs = os.path.join(self.tmp.name, "packs")
        os.makedirs(self.packs)
        for name in ("acg", "it", "beauty", "quotes"):          # quotes-add.tsv is in the folder: the meme decode must still not load it
            put(os.path.join(self.packs, name + "-add.tsv"), f"k\t{name}詞\t-1\t{name}\n")
        self.meme = os.path.join(self.tmp.name, "meme")
        os.makedirs(self.meme)

    def decode(self, tag, native=False, on=True, profile="formal"):
        """one decode call; returns (command, {file name: text} of the folder handed to the CLI)."""
        seen = []

        def run(cmd, **_):
            d = cmd[cmd.index("--packs-dir") + 1]
            seen.append((cmd, {n: get(os.path.join(d, n)) for n in sorted(os.listdir(d)) if n.endswith("-add.tsv")}))
            put(cmd[cmd.index("--dump") + 1], "1\t1\t答案\t0\n")

        if on:
            put(os.path.join(self.meme, "meme-add.tsv" if native else "acg-add.tsv"), "k\t迷因詞\t-1\tmeme\n")
        with mock.patch.object(T.subprocess, "run", run), mock.patch.object(B, "eval_bin", lambda: "shanjie-eval"), mock.patch.object(IT, "SHIP_ARGS", ["--ship-flag"]):
            self.assertEqual(T.decoder(self.packs, tag, native)([("詞", ["a"])], profile, self.meme if on else None), ["答案"])
        return seen[0]

    @staticmethod
    def packs_of(cmd):
        return cmd[cmd.index("--packs") + 1]

    def test_the_meme_decode_carries_acg_it_beauty(self):
        cmd, files = self.decode("meme", on=False, profile="chat")
        self.assertEqual((self.packs_of(cmd), cmd[cmd.index("--profile") + 1]), ("acg,it,beauty", "chat"))
        self.assertIn("--ship-flag", cmd)                                                   # the same shipping flags as the it pack's decoder
        self.assertEqual(cmd[cmd.index("--threads") + 1], str(B.THREADS))
        self.assertEqual(files["it-add.tsv"], "k\tit詞\t-1\tit\n")                         # no meme rows when decoding the baseline

    def test_the_beauty_decode_carries_acg_it(self):
        cmd, _ = self.decode("beauty", on=False)
        self.assertEqual(self.packs_of(cmd), "acg,it")

    def test_the_meme_decode_never_loads_the_quotes_pack_even_when_its_file_is_there(self):
        for on in (False, True):
            cmd, _ = self.decode("meme", on=on)
            self.assertNotIn("quotes", self.packs_of(cmd))

    def test_the_approximate_on_decode_appends_the_meme_rows_to_the_it_file(self):
        cmd, files = self.decode("meme")
        self.assertEqual((self.packs_of(cmd), files["it-add.tsv"], files["acg-add.tsv"]), ("acg,it,beauty", "k\tit詞\t-1\tit\nk\t迷因詞\t-1\tmeme\n", "k\tacg詞\t-1\tacg\n"))
        self.assertNotIn("meme-add.tsv", files)

    def test_native_pack_makes_the_on_decode_carry_acg_it_meme_beauty(self):
        cmd, files = self.decode("meme", native=True)
        self.assertEqual(self.packs_of(cmd), "acg,it,meme,beauty")
        self.assertEqual((files["meme-add.tsv"], files["it-add.tsv"]), ("k\t迷因詞\t-1\tmeme\n", "k\tit詞\t-1\tit\n"))      # the it file is left alone

    def test_native_pack_leaves_the_off_decode_without_meme(self):
        cmd, files = self.decode("meme", native=True, on=False)
        self.assertEqual(self.packs_of(cmd), "acg,it,beauty")
        self.assertNotIn("meme-add.tsv", files)

    def test_the_meme_decoder_needs_the_beauty_file_too(self):
        os.remove(os.path.join(self.packs, "beauty-add.tsv"))
        with self.assertRaises(SystemExit):
            T.decoder(self.packs, "meme")
        T.decoder(self.packs, "beauty")

    def test_a_missing_pack_file_stops_when_the_decoder_is_made(self):
        with tempfile.TemporaryDirectory() as packs:
            put(os.path.join(packs, "acg-add.tsv"), "")
            with self.assertRaises(SystemExit):
                T.decoder(packs, "beauty")


if __name__ == "__main__":
    unittest.main()
