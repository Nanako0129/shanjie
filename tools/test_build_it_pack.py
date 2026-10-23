"""Tests for tools/build_it_pack.py (docs/contracts/it-pack.md section 4). Run: python3 -m unittest tools.test_build_it_pack   (from the repo root).
No network and no evaluation CLI: the sources come from a fake web (a Fetcher with a fake opener), the decoder and the reading tool are fakes. The
only repo data it needs is the Python modules next to it."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_it_pack as T  # noqa: E402

B, S = T.B, T.S
SC = {n: -7.0 for n in range(2, 11)}


def put(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def get(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def fake_readings(words):
    return {w: ([f"s{ord(c)}" for c in w], False) for w in words}


def fake_decoder(off=None, on=None):
    """decode(pairs, profile, packs=None)：不開資訊詞包時第一名是 off.get(詞, '？'+詞)（不等於詞，所以不會被當成「已經打得對」），開了是 on.get(詞, 詞)。"""
    off, on = off or {}, on or {}
    return lambda pairs, prof, packs=None: [(on if packs else off).get(w, w if packs else "？" + w) if not isinstance(
        (on if packs else off).get(w), dict) else (on if packs else off)[w][prof] for w, _ in pairs]


def src(**kw):
    base = {"names16": set(), "names75": set(), "ai": set(), "cg_tw": {}, "cg_hant": set(), "mainland": set(), "manual": {}, "proper": set()}
    base.update(kw)
    return base


def build(s, counts, decode=None, have=frozenset(), acg=frozenset(), collisions=None, info=None):
    with tempfile.TemporaryDirectory() as d:
        coll = os.path.join(d, "it-collisions.tsv")
        if collisions:
            put(coll, "".join("\t".join(r) + "\n" for r in collisions))
        return T.build(s, counts, decode or fake_decoder(), fake_readings, have, acg, coll, SC, info)


class SourceRules(unittest.TestCase):
    def alive(self, s, counts):
        return T.filter_claims(s, counts)[0]

    def test_substring_markers_with_the_taiwan_spelling_of_algorithm_masked(self):
        s = src(names16={"排序演算法", "排序算法", "網絡卡", "軟體開發"}, ai={"信息檢索", "資訊檢索"})
        counts = {w: 50 for w in s["names16"] | s["ai"]}
        alive, stats, markers, _ = T.filter_claims(s, counts)
        self.assertEqual(sorted(alive), ["排序演算法", "資訊檢索", "軟體開發"])
        self.assertEqual({m: n for m, n in markers.items() if n}, {"網絡": 1, "算法": 1, "信息": 1})
        self.assertEqual(stats["marker"]["claims_dropped"], {"naer16": 2, "ai": 1})

    def test_markers_do_not_apply_to_the_other_sources(self):
        s = src(names75={"信息理論"}, cg_tw={"網絡位址": {"Module:CGroup/IT"}}, manual={"軟件工程": ("開發", None)})
        self.assertEqual(sorted(self.alive(s, {})), ["信息理論", "網絡位址", "軟件工程"])

    def test_frequency_floor_is_20_and_only_for_15416_and_ai_titles(self):
        s = src(names16={"十九次詞", "二十次詞"}, ai={"十九次標題", "二十次標題"}, names75={"零次詞"}, cg_tw={"零次組": {"m"}}, manual={"零次手寫": ("AI", None)})
        counts = {"十九次詞": 19, "二十次詞": 20, "十九次標題": 19, "二十次標題": 20}
        self.assertEqual(sorted(self.alive(s, counts)), ["二十次標題", "二十次詞", "零次手寫", "零次組", "零次詞"])

    def test_a_string_missing_from_the_counts_file_stops_the_build(self):
        with self.assertRaises(SystemExit):
            T.filter_claims(src(names16={"沒算過"}), {})

    def test_mainland_list_blocks_unless_a_taiwan_source_has_the_string(self):
        s = src(ai={"伺服端", "共有詞", "只在大陸"}, names16={"共有詞"}, mainland={"伺服端", "共有詞"})
        counts = {w: 50 for w in s["ai"] | s["names16"]}
        alive, stats, _, _ = T.filter_claims(s, counts)
        self.assertEqual(sorted(alive), ["共有詞", "只在大陸"])
        self.assertEqual(stats["mainland"]["claims_dropped"]["ai"], 1)      # 共有詞 的 AI 標題來源也放行（15416 有它）
        s["cg_tw"] = {"伺服端": {"Module:CGroup/IT"}}                         # CGroup 的 zh-tw 值也算台灣來源
        self.assertIn("伺服端", self.alive(s, counts))

    def test_proper_noun_filter_only_for_ai_titles(self):
        s = src(ai={"某某公司", "注意力機制"}, names16={"某某公司"}, proper={"某某公司"})
        counts = {w: 50 for w in s["ai"] | s["names16"]}
        alive, stats, _, _ = T.filter_claims(s, counts)
        self.assertEqual(alive["某某公司"], ["naer16"])                       # 15416 的同名來源不受影響，詞還在
        self.assertEqual(alive["注意力機制"], ["ai"])
        self.assertEqual(stats["proper_noun"]["claims_dropped"], {"ai": 1})
        self.assertEqual(stats["proper_noun"]["words_dropped"], 0)

    def test_mainland_forms_from_the_converted_column(self):
        self.assertEqual(T.mainland_forms("[磁]盤"), {"磁盤"})
        self.assertEqual(T.mainland_forms("事務[元]"), {"事務", "事務元"})
        self.assertEqual(T.mainland_forms("下載【動】"), {"下載"})
        self.assertEqual(T.mainland_forms("光盤（碟）"), {"光盤"})
        self.assertEqual(T.mainland_forms("QWERTY鍵盤"), set())

    def test_cn_values(self):
        self.assertEqual(T.cn_values("zh-cn:激活函数; zh-tw:啟用功能;"), ["激活函数"])
        self.assertEqual(T.cn_values("存取=>zh-cn:访问; 存取=>zh-tw:存取;"), ["访问"])
        self.assertEqual(T.cn_values("zh-hans:服务器; zh-tw:伺服器"), ["服务器"])
        self.assertEqual(T.cn_values("zh-cn:A; zh-tw:X"), [])


class Pipeline(unittest.TestCase):
    S1 = src(names16={"負載平衡", "合併請求", "已有詞", "動漫詞", "打對詞"}, manual={"提示詞": ("AI", None)})
    C1 = {w: 50 for w in S1["names16"]}

    def test_dedupe_against_the_lexicon_and_the_anime_pack(self):
        files, m, col, unread, ref, ldrop = build(self.S1, self.C1, have={"已有詞"}, acg={"動漫詞"})
        words = {l.split("\t")[1] for l in files["it-add.tsv"].splitlines()}
        self.assertEqual(words, {"負載平衡", "合併請求", "打對詞", "提示詞"})
        self.assertEqual((m["stages"]["dedupe_lexicon"], m["stages"]["dedupe_acg"]), (1, 1))
        self.assertTrue(all(l.endswith("\tit") for l in files["it-add.tsv"].splitlines()))

    def test_a_word_the_shipping_decoder_already_gets_right_in_both_profiles_is_dropped(self):
        both = lambda pairs, prof, packs=None: [w if (w == "打對詞" or (w == "只聊天對" and prof == "chat")) else "？" + w for w, _ in pairs] if not packs else [w for w, _ in pairs]
        s = src(names75={"打對詞", "只聊天對", "沒打對"})
        files, m, *_ = build(s, {}, decode=both)
        words = {l.split("\t")[1] for l in files["it-add.tsv"].splitlines()}
        self.assertEqual(words, {"只聊天對", "沒打對"})
        self.assertEqual(m["stages"]["already_right"], 1)

    def test_reference_list_is_every_source_string_before_filtering_and_b_uses_it(self):
        # 冷門詞 fails the frequency floor, so it is not in the pack, but it is a reference string: a pack word that takes its place (b) is listed.
        s = src(names16={"冷門詞", "搶走者", "無關者"}, names75={"搶走者", "無關者"})
        counts = {"冷門詞": 3, "搶走者": 3, "無關者": 3}
        off = {"搶走者": "冷門詞", "無關者": "不在名單"}
        files, m, col, unread, ref, ldrop = build(s, counts, decode=fake_decoder(off=off))
        self.assertIn("冷門詞", ref)
        self.assertNotIn("冷門詞", {l.split("\t")[1] for l in files["it-add.tsv"].splitlines()})
        self.assertEqual({r: sorted({(e[0], e[1]) for e in v}) for r, v in col.items()}, {" ".join(f"s{ord(c)}" for c in "搶走者"): [("搶走者", "b")]})
        self.assertEqual(m["unresolved_collision_readings"], 1)

    def test_a_pack_word_that_takes_first_place_from_a_lexicon_word_is_dropped_c(self):
        s = src(names75={"擠人詞", "安分詞"})
        off = {"擠人詞": "舊詞庫詞"}
        files, m, col, _, _, ldrop = build(s, {}, decode=fake_decoder(off=off), have={"舊詞庫詞"})
        self.assertEqual({l.split("\t")[1] for l in files["it-add.tsv"].splitlines()}, {"安分詞"})
        self.assertEqual([(w, o) for _, w, o, _ in ldrop], [("擠人詞", "舊詞庫詞")])
        self.assertEqual(m["stages"]["dropped_c_displacing_acg"], 0)
        self.assertEqual(col, {})

    def test_an_it_word_that_takes_first_place_from_an_anime_pack_word_is_dropped(self):
        s = src(names75={"擠動漫詞"})
        files, m, col, _, _, ldrop = build(s, {}, decode=fake_decoder(off={"擠動漫詞": "動漫詞"}), acg={"動漫詞"})
        self.assertEqual(files["it-add.tsv"], "")
        self.assertEqual(sorted({o for _, _, o, _ in ldrop}), ["動漫詞"])
        self.assertEqual(m["stages"]["dropped_c_displacing_acg"], 1)

    def test_homophones_inside_the_pack_are_a_collision_until_the_tsv_decides(self):
        # 兩個詞同讀音（假讀音依字，所以用同一個字串當兩個詞的讀音不行）：讓假解碼器在開詞包時把 甲 的第一名換成 乙，就是 (a)
        s = src(names75={"甲甲甲", "乙乙乙"})
        rd = lambda ws: {w: (["s1", "s2", "s3"], False) for w in ws}
        dec = lambda pairs, prof, packs=None: ["乙乙乙" if packs else "？" + w for w, _ in pairs]
        with tempfile.TemporaryDirectory() as d:
            coll = os.path.join(d, "c.tsv")
            _, m, col, *_ = T.build(s, {}, dec, rd, frozenset(), frozenset(), coll, SC)
            self.assertEqual(sorted(col), ["s1 s2 s3"])
            put(coll, "s1 s2 s3\t乙乙乙\t+甲甲甲\t兩個都留\n")
            _, m2, col2, *_ = T.build(s, {}, dec, rd, frozenset(), frozenset(), coll, SC)
            self.assertEqual(col2, {})
            self.assertEqual(m2["unresolved_collision_readings"], 0)

    def test_an_excluded_word_in_the_collisions_tsv_is_not_built(self):
        s = src(names75={"保留詞", "排除詞"})
        files, m, *_ = build(s, {}, collisions=[("s1 s2 s3", "保留詞", "排除詞", "理由")])
        self.assertEqual({l.split("\t")[1] for l in files["it-add.tsv"].splitlines()}, {"保留詞"})
        self.assertEqual(m["stages"]["excluded_by_it_collisions_tsv"], 1)

    def test_sandhi_rows_and_scores_come_from_the_anime_pack_rules(self):
        s = src(manual={"不但": ("AI", None)})
        with tempfile.TemporaryDirectory() as d:
            files, *_ = T.build(s, {}, fake_decoder(), lambda ws: {"不但": (["ㄅㄨˋ", "ㄉㄢˋ"], False)}, frozenset(), frozenset(), os.path.join(d, "c.tsv"), SC)
        rows = [l.split("\t") for l in files["it-add.tsv"].splitlines()]
        self.assertEqual([r[0] for r in rows], ["ㄅㄨˋ-ㄉㄢˋ", "ㄅㄨˊ-ㄉㄢˋ"])     # 主要列＋「不」的變調列
        self.assertEqual(rows[0][2], "-7.0")
        self.assertEqual(float(rows[1][2]), -7.5)

    def test_manual_alternate_reading_gets_its_own_rows(self):
        s = src(manual={"檢索詞": ("AI", ["a", "b", "c"])})
        files, *_ = build(s, {})
        self.assertEqual(sorted(l.split("\t")[0] for l in files["it-add.tsv"].splitlines()), sorted(["a-b-c", "-".join(f"s{ord(c)}" for c in "檢索詞")]))

    def test_sources_file_lists_every_kind_with_the_wiki_count(self):
        s = src(names16={"負載平衡"}, names75={"負載平衡"}, cg_tw={"負載平衡": {"Module:CGroup/IT"}}, cg_hant={"負載平衡"})
        files, *_ = build(s, {"負載平衡": 42})
        lines = files["it-sources.tsv"].splitlines()[1:]
        self.assertEqual([l.split("\t")[1] for l in lines], ["cgroup", "naer75", "naer16"])
        self.assertTrue(lines[0].endswith("zh-hant") and lines[2].endswith("wiki=42"))

    def test_check_set_is_proportional_seeded_and_skips_check_readings(self):
        words = [f"{c}字詞" for c in "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"]
        s = src(names75=set(words[:15]), cg_tw={w: {"m"} for w in words[15:]})
        info = {}
        build(s, {}, info=info)
        rows, quota = T.check_rows(info, seed=1, n=8)
        self.assertEqual((len(rows), quota), (8, {"cgroup": 2, "naer75": 6}))
        self.assertEqual(rows, T.check_rows(info, seed=1, n=8)[0])
        info["check"][words[0]] = True
        self.assertNotIn(f"|{words[0]}|", "".join(T.check_rows(info, seed=1, n=19)[0]))


class FakeWeb:
    """契約 §2a 的主機都假的：data.gov.tw 的資料集 API、國教院的兩個 CSV、維基 API（分類、轉換、模組、pageprops）與 Wikidata SPARQL。"""
    CSV16 = "序號,英文名稱,中文名稱,來源網站\n1,load balancing,負載平衡,x\n2,sort,排序算法；排序演算法（乙、丙）,x\n3,x,\"括號詞（甲、乙）、另一個詞\",x\n4,y,單,x\n"
    CSV75 = "序號,英文名稱,中文名稱,中國大陸譯名,來源網站\n1,server,伺服器,服务器【名】,x\n2,drive,磁碟機,[磁]盘,x\n"
    TW = {"机器学习": "機器學習", "某某公司": "某某公司"}
    HANT = {"服务器": "服務器", "磁盘": "磁盤", "盘": "盤", "激活函数": "激活函數", "服务器【名】": "服務器【名】", "[磁]盘": "[磁]盤"}
    MODULE = "Item('x', 'zh-cn:激活函数; zh-tw:啟用函數;'),\nItem('y', 'zh-cn:A; zh-tw:負載平衡;'),\n"

    def __init__(self):
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        u = urllib.parse.urlsplit(url)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        j = lambda o: json.dumps(o, ensure_ascii=False).encode()
        if u.hostname == "data.gov.tw":
            n = u.path.rsplit("/", 1)[1]
            return j({"result": {"distribution": [{"resourceFormat": "CSV", "resourceDownloadUrl": f"https://opendata.naer.edu.tw/x/{n}.csv"}]}})
        if u.hostname == "opendata.naer.edu.tw":
            return (self.CSV16 if "15416" in u.path else self.CSV75).encode("utf-8-sig")
        if u.hostname == "query.wikidata.org":
            assert "wd:Q42" in q["query"] and "wd:Q1" in q["query"], q["query"]
            return j({"results": {"bindings": [{"item": {"value": "http://www.wikidata.org/entity/Q42"}}]}})
        if q.get("list") == "categorymembers":
            members = ["机器学习", "某某公司"] if q["cmtitle"] == "Category:人工智能" and q["cmtype"] == "page" else []
            return j({"query": {"categorymembers": [{"title": t} for t in members]}})
        if q["action"] == "parse":
            tr = self.TW if q["variant"] == "zh-tw" else self.HANT
            return j({"parse": {"text": "".join(f"<p>{tr.get(p, p)}</p>\n" for p in q["text"].split("\n\n"))}})
        if q.get("prop") == "revisions":
            return j({"query": {"pages": [{"title": q["titles"], "revisions": [{"slots": {"main": {"content": self.MODULE}}}]}]}})
        if q.get("prop") == "pageprops":
            qid = {"机器学习": "Q1", "某某公司": "Q42"}
            return j({"query": {"pages": [{"title": t, "pageprops": {"wikibase_item": qid[t]}} for t in q["titles"].split("|")]}})
        raise AssertionError(url)


class Offline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manual = os.path.join(self.tmp.name, "manual.tsv")
        put(self.manual, "# c\n提示詞\tAI\t\n")
        self.cache = os.path.join(self.tmp.name, "cache")

    def fetcher(self, offline, web=None):
        return T.Tracking(self.cache, offline=offline, opener=web, sleep=lambda s: None)

    def counts_for(self, cands, n=50):
        path = os.path.join(self.tmp.name, "counts.tsv")
        head = f"# dump_sha256={'0' * 64} candidates_sha256={hashlib.sha256(''.join(w + chr(10) for w in cands).encode()).hexdigest()} tool_commit=x\n"
        put(path, head + "".join(f"{w}\t{n}\n" for w in cands))
        return T.read_counts(path)

    def test_candidates_use_the_same_splitting_as_the_counting_tool(self):
        web = FakeWeb()
        f = self.fetcher(False, web)
        cands = S.candidates(f)
        self.assertEqual(cands, sorted(["另一個詞", "括號詞", "排序演算法", "排序算法", "負載平衡", "機器學習", "某某公司"]))       # 括號裡的部分去掉、單字不收
        _, counts = self.counts_for(cands)
        src_ = T.gather(f, counts, self.manual)
        self.assertEqual(sorted(src_["names16"] | src_["ai"]), cands)

    def test_gather_fetches_everything_online_then_builds_offline_twice_with_identical_files(self):
        web = FakeWeb()
        cands = S.candidates(self.fetcher(False, web))
        _, counts = self.counts_for(cands)
        src_ = T.gather(self.fetcher(False, web), counts, self.manual)         # `fetch`
        self.assertEqual(src_["proper"], {"某某公司"})                           # Q42 是組織（假的 SPARQL）
        self.assertEqual(src_["cg_tw"], {"啟用函數": {"Module:CGroup/Electronics", "Module:CGroup/IT"}, "負載平衡": {"Module:CGroup/Electronics", "Module:CGroup/IT"}})
        n_calls = len(web.calls)
        outs = []
        for _ in range(2):
            f = self.fetcher(True)
            s2 = T.gather(f, counts, self.manual)
            files, m, *_ = T.build(s2, counts, fake_decoder(), fake_readings, frozenset(), frozenset(), os.path.join(self.tmp.name, "none.tsv"), SC)
            outs.append((files, json.dumps(T.source_meta(f), sort_keys=True), m))
        self.assertEqual(len(web.calls), n_calls)                              # 離線沒有再送請求
        self.assertEqual(outs[0], outs[1])
        words = {l.split("\t")[1] for l in outs[0][0]["it-add.tsv"].splitlines()}
        self.assertEqual(words, {"提示詞", "啟用函數", "負載平衡", "排序演算法", "括號詞", "另一個詞", "機器學習", "伺服器", "磁碟機"})   # 排序算法（標記）與 某某公司（專有名詞）被擋
        self.assertEqual(s2["mainland"], {"服務器", "磁盤", "激活函數"})        # 15275 的欄位值（去掉【名】、[ ]）加 CGroup 的 zh-cn 值，都轉成 zh-hant

    def test_offline_stops_on_a_cache_miss(self):
        with self.assertRaises(SystemExit) as e:
            T.gather(self.fetcher(True), {}, self.manual)
        self.assertIn("--offline: not cached", str(e.exception))

    def test_write_all_records_hashes_and_never_touches_acg_json(self):
        with tempfile.TemporaryDirectory() as d:
            put(os.path.join(d, "acg.json"), "keep")
            T.write_all({"it-add.tsv": "x\n"}, {"words": 1}, d)
            self.assertEqual(get(os.path.join(d, "acg.json")), "keep")
            m = json.loads(get(os.path.join(d, "it.json")))
            self.assertEqual(m["files"]["it-add.tsv"]["sha256"], hashlib.sha256(b"x\n").hexdigest())


if __name__ == "__main__":
    unittest.main()
