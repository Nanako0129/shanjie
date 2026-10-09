"""Tests for tools/build_acg_pack.py (docs/contracts/acg-pack.md A.5). Run: python3 -m unittest tools.test_build_acg_pack   (from the repo root).
No network: the build runs against a fake API. It needs the repo's data (data/lexicon, data/lm/bigram.sjlm) and the evaluation CLI
(built on demand with cargo) for the collision detection; it fails loudly without them, never skips."""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_acg_pack as B  # noqa: E402


def second_column(path):
    return {l.split("\t")[1] for l in open(path, encoding="utf-8") if l.strip()}


def lexicon_words():
    words = set()
    for l in open(B.bo.BASE, encoding="utf-8"):
        f = l.split()
        if len(f) == 3 and l[0] not in "#_":
            words.add(f[1])
    for f in ("overlay-add.tsv", "sandhi-add.tsv"):
        words |= second_column(os.path.join(B.LEX, f))
    return words


class Rules(unittest.TestCase):
    def test_zh_tw_values(self):
        self.assertEqual(B.tw_values("zh-cn:路飞; zh-tw:魯夫/路飛; zh-hk:路飛"), [("魯夫", "zh-tw"), ("路飛", "zh-tw")])
        self.assertEqual(B.tw_values("魯夫=>zh-tw:路飛"), [("路飛", "zh-tw")])           # 單向規則
        self.assertEqual(B.tw_values("zh-hant:《進擊的巨人》; zh-cn:进击的巨人"), [("進擊的巨人", "zh-hant")])   # 沒有 zh-tw 才退回 zh-hant
        self.assertEqual(B.tw_values("zh-tw:A; zh-hant:鋼彈"), [])                       # 有 zh-tw 就不退回；非漢字與單字不收
        self.assertEqual(B.tw_values("zh-tw:魯"), [])

    def test_named_template_parameters_do_not_stick_to_the_language_values(self):
        self.assertEqual(B.tw_values("zh-cn:; zh-hk:冰雪仙姬; zh-tw:冰雪竹姬|original="), [("冰雪竹姬", "zh-tw")])
        self.assertEqual(B.tw_values("zh-tw:冰雪竹姬; zh-hk:冰雪仙姬|desc=轉換說明|original=冰雪仙子"), [("冰雪竹姬", "zh-tw")])
        self.assertEqual(B.tw_values("zh-tw:甲乙{{x|a}}丙|original=甲"), [])               # {{ }} 裡的 | 不拆；含非漢字就不收
        self.assertEqual(B.tw_values("zh-hant:鋼彈戰士|desc=zh-tw:不要這個"), [("鋼彈戰士", "zh-hant")])   # 具名參數整個丟掉

    def test_group_rules_read_template_items_and_lua_items_from_the_same_page(self):
        page = "{{CItem|zh-tw:甲甲甲}}\n{ type = 'item', original = '', rule = 'zh-tw:乙乙乙;zh-hk:丙丙丙;', description = 'x' },\nItem('x', 'zh-tw:丁丁丁')\n"
        got = sorted(v for r in B.group_rules(page) for v, _ in B.tw_values(r))
        self.assertEqual(got, ["丁丁丁", "乙乙乙", "甲甲甲"])

    def test_an_unterminated_long_bracket_ends_the_scan(self):
        got = []
        t = threading.Thread(target=lambda: got.extend(B.lua_items("Item('x', 'zh-tw:甲甲甲')\nItem([[壞掉")), daemon=True)
        t.start()
        t.join(2)
        self.assertFalse(t.is_alive(), "lua_items loops on an unterminated [[")
        self.assertEqual(got, [["x", "zh-tw:甲甲甲"]])    # 完整的 Item 照常產出，壞掉的那個停在這裡

    def test_wiki_title_keeps_slashes_and_decodes(self):
        self.assertEqual(B.wiki_title("https://zh.wikipedia.org/wiki/.hack//SIGN"), ".hack//SIGN")
        self.assertEqual(B.wiki_title("https://zh.wikipedia.org/wiki/%E9%A2%A8%E4%B9%8B%E8%B0%B7"), "風之谷")

    def test_strict_name_filter(self):
        base = {"小明"}
        ok = lambda n, s, kind="li": B.strict_ok(n, kind, s, base)
        self.assertTrue(ok("阿庫雷特", "阿庫雷特（アクレット）"))
        self.assertFalse(ok("小明", "小明（シャオミン）"))                    # 在基底
        self.assertFalse(ok("阿庫雷特", "阿庫雷特（アクレット）", "heading"))  # 小標題
        self.assertFalse(ok("阿庫雷特的母親", "阿庫雷特的母親（ママ）"))      # 關係詞組
        self.assertFalse(ok("阿庫雷特社團", "阿庫雷特社團（クラブ）"))        # 泛稱
        self.assertFalse(ok("阿庫雷特", "阿庫雷特，主角"))                    # 旁邊沒有原名
        self.assertFalse(ok("阿庫雷特", "主角阿庫雷特（アクレット）"))        # 摘要不是以名字開頭


class Dedupe(unittest.TestCase):
    def test_words_already_in_the_lexicon_are_dropped(self):
        self.assertEqual(B.dedupe(["悲慘世界", "碇源堂", "碇源堂"], {"悲慘世界"}), ["碇源堂"])

    def test_the_committed_pack_shares_no_string_with_base_overlay_or_sandhi(self):
        pack = second_column(os.path.join(B.PACKS, "acg-add.tsv"))
        self.assertGreater(len(pack), 10000)
        self.assertEqual(pack & lexicon_words(), set())


class Ordering(unittest.TestCase):
    def test_more_sources_first_then_by_string(self):
        n = {"乙乙": 1, "甲甲": 1, "丙丙": 3}
        self.assertEqual(B.ordered(["乙乙", "甲甲", "丙丙"], n.get), ["丙丙"] + sorted(["乙乙", "甲甲"]))
        self.assertEqual(B.ordered(["丙丙", "甲甲", "乙乙"], n.get), B.ordered(["乙乙", "丙丙", "甲甲"], n.get))


# ---- a fake API serving a small, fixed corner of Wikipedia

GROUPS = """# 區段\t組名\t模組\t收錄\t類別或理由
作品\t碇系\tEVA\tinclude\t動畫
作品\t遊戲系\tGames\tinclude\t遊戲
作品\t某電影\tMovie\texclude\t電影
"""
LIST = """{| class="wikitable"
| colspan=4 div style="text-align: center;" | 艺术、影视与ACG（三）：作品
{{CGroup/list/item|碇系|EVA|--|x}}
{{CGroup/list/item|遊戲系|Games|--|x}}
{{CGroup/list/item|某電影|Movie|--|x}}
{{CGroup/list/item|新作|NewWork|--|x}}
"""
TS = {"list": "2026-10-01T00:00:00Z", "eva": "2026-10-05T00:00:00Z", "games": "2026-10-08T12:00:00Z", "movie": "2026-09-01T00:00:00Z"}
PAGES = {
    "Template:CGroup/list": (1, TS["list"], LIST),
    "Template:CGroup/EVA": (2, TS["eva"], "{{CItem|zh-tw:碇源堂; zh-cn:碇源堂}}\n{{CItem|zh-tw:朋友}}\n{{CItem|zh-hant:螢火蟲之墓}}"),
    "Template:CGroup/Games": (3, TS["games"], "{{CItem|zh-tw:楓之谷}}\n{{CItem|zh-tw:阿庫雷特}}"),
    "Template:CGroup/Movie": (4, TS["movie"], "{{CItem|zh-tw:怪獸電力公司}}"),
    "風之谷 (電影)": (5, "2026-10-02T00:00:00Z", ""),
    "風之谷角色列表": (6, "2026-10-03T00:00:00Z", ""),
}
ARTICLE = ("<h2>登場人物</h2><ul><li>阿庫雷特（アクレット）</li><li>朋友（ともだち）</li><li>米卡莎的母親（ママ）</li></ul>")
REVID = {"風之谷": 5, "風之谷角色列表": 6}
PARSES = {
    "風之谷": {"title": "風之谷 (電影)", "displaytitle": "<span>風之谷 (電影)</span>", "text": ARTICLE, "links": [
        {"ns": 0, "title": "風之谷角色列表", "exists": True}, {"ns": 0, "title": "不存在角色列表", "exists": False}]},
    "風之谷角色列表": {"title": "風之谷角色列表", "displaytitle": "風之谷角色列表", "text": "<h2>人物</h2><ul><li>碇真次郎（シンジロウ）</li></ul>", "links": []},
}


class FakeApi:
    def wiki(self, **p):
        if p["action"] == "parse":
            if p["page"] not in PARSES:
                raise RuntimeError({"code": "missingtitle"})
            assert "revid" in p["prop"]
            return {"parse": dict(PARSES[p["page"]], revid=REVID[p["page"]])}
        out = []
        for t in p["titles"].split("|"):
            if t not in PAGES:
                out.append({"title": t, "missing": True})
                continue
            rid, ts, text = PAGES[t]
            rev = {"revid": rid, "timestamp": ts}
            if "content" in p["rvprop"]:
                rev["slots"] = {"main": {"content": text}}
            out.append({"title": t, "revisions": [rev]})
        return {"query": {"pages": out}}

    def sparql(self, query):
        return {"results": {"bindings": [{"w": {"value": "http://www.wikidata.org/entity/Q1"}, "sl": {"value": "50"},
                                          "art": {"value": "https://zh.wikipedia.org/wiki/%E9%A2%A8%E4%B9%8B%E8%B0%B7"}}]}}


READINGS = {  # tools/readings.py needs the MOE dictionary; the fixture fixes the readings instead
    "碇源堂": "ㄉㄧㄥˋ ㄩㄢˊ ㄊㄤˊ", "螢火蟲之墓": "ㄧㄥˊ ㄏㄨㄛˇ ㄔㄨㄥˊ ㄓ ㄇㄨˋ", "風之谷": "ㄈㄥ ㄓ ㄍㄨˇ",
    "楓之谷": "ㄈㄥ ㄓ ㄍㄨˇ", "阿庫雷特": "ㄚ ㄎㄨˋ ㄌㄟˊ ㄊㄜˋ", "奇希莉卡": "ㄑㄧˊ ㄒㄧ ㄌㄧˋ ㄎㄚˇ",
}


def fake_readings(words):
    return {w: (READINGS[w].split(), False) for w in words if w in READINGS}


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.groups = os.path.join(cls.tmp, "groups.tsv")
        open(cls.groups, "w", encoding="utf-8").write(GROUPS)
        cls.manual = os.path.join(cls.tmp, "manual.tsv")
        open(cls.manual, "w", encoding="utf-8").write("# m\n奇希莉卡\t無職轉生\t角色\n朋友\t基底已有\t去重\n")
        cls.none = os.path.join(cls.tmp, "none.tsv")                       # 沒有處置檔
        cls.excl = os.path.join(cls.tmp, "collisions.tsv")
        open(cls.excl, "w", encoding="utf-8").write("# c\nㄈㄥ ㄓ ㄍㄨˇ\t風之谷\t楓之谷\t保留既有的名字\n")

    def build(self, collisions, exclude=None):
        return B.build(FakeApi(), self.groups, collisions, self.manual, readings=fake_readings, exclude_tsv=exclude or self.none)

    def test_pack_content_and_filters(self):
        files, manifest, col, unread, ref = self.build(self.excl)
        words = {l.split("\t")[1] for l in files["acg-add.tsv"].splitlines()}
        self.assertEqual(words, {"碇源堂", "螢火蟲之墓", "風之谷", "阿庫雷特", "奇希莉卡"})   # 奇希莉卡 來自手動清單；手動的 朋友 被去重丟掉
        self.assertNotIn("怪獸電力公司", words)        # 排除的組
        self.assertNotIn("朋友", words)                # 基底已有（轉換組與人物都一樣）
        self.assertNotIn("楓之谷", words)              # acg-collisions.tsv 排除
        self.assertIn("怪獸電力公司", ref)             # 但在參考名單裡
        self.assertIn("楓之谷", ref)
        self.assertIn("米卡莎的母親", ref)             # 參考名單含所有抽出來的人名，在嚴格過濾之前
        self.assertNotIn("米卡莎的母親", words)
        self.assertEqual(manifest["unclassified_groups"], ["新作 (NewWork)"])
        self.assertEqual(manifest["version"], "20261008-" + hashlib.sha256(files["acg-add.tsv"].encode()).hexdigest()[:8])   # 最新來源頁的日期（不是建置日期）加內容雜湊
        self.assertEqual(manifest["revision_ids"], {"min": 1, "max": 6, "pages": 6})            # 條目的 revid 來自 parse 同一次回應
        self.assertEqual(manifest["sources"], {"cgroup": 3, "char": 1, "title": 1, "manual": 1})
        self.assertEqual(col, {})
        self.assertEqual(unread, ["碇真次郎"])         # 角色列表條目裡的名字通過過濾，但讀音拼不出（夾具沒給）就丟掉
        for line in files["acg-add.tsv"].splitlines():
            self.assertEqual(len(line.split("\t")), 4)
            self.assertTrue(line.endswith("\tacg"))

    def test_a_changed_pack_changes_the_version(self):
        manual = os.path.join(self.tmp, "manual2.tsv")
        open(manual, "w", encoding="utf-8").write(open(self.manual, encoding="utf-8").read() + "艾倫葉卡\t某作品\t角色\n")
        more = B.build(FakeApi(), self.groups, self.excl, manual, readings=lambda w: fake_readings(w) | {"艾倫葉卡": (["ㄞˋ", "ㄌㄨㄣˊ", "ㄧㄝˋ", "ㄎㄚˇ"], False)}, exclude_tsv=self.none)[1]
        base = self.build(self.excl)[1]
        self.assertEqual(more["version"][:8], base["version"][:8])            # 來源頁沒變，日期一樣
        self.assertNotEqual(more["version"], base["version"])                 # 內容變了，版號就變

    def test_cached_missingtitle_is_replayed_offline(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        api = B.Api(d, offline=True)
        key = B.Api.key(dict(action="parse", page="已刪除", format="json", formatversion="2", maxlag="5"))
        open(os.path.join(d, key + ".json"), "w").write('{"error": {"code": "missingtitle"}}')
        with self.assertRaises(RuntimeError) as c:
            api.wiki(action="parse", page="已刪除")
        self.assertEqual(c.exception.args[0]["code"], "missingtitle")

    def test_manual_words_carry_their_own_source_tag(self):
        files, *_ = self.build(self.excl)
        self.assertIn("奇希莉卡\tmanual\t無職轉生\t", files["acg-sources.tsv"])

    def test_same_cache_twice_is_identical(self):
        a = self.build(self.excl)
        b = self.build(self.excl)
        self.assertEqual(a[0], b[0])
        self.assertEqual(a[1], b[1])
        self.assertEqual(B.collisions_text(a[2], a[1]), B.collisions_text(b[2], b[1]))
        out = [tempfile.mkdtemp(), tempfile.mkdtemp()]
        for d in out:
            self.addCleanup(shutil.rmtree, d, True)
        for d, r in zip(out, (a, b)):
            B.write_all(r[0], r[1], d)
        for name in ("acg-add.tsv", "acg-sources.tsv", "acg.json"):
            self.assertEqual(open(os.path.join(out[0], name), "rb").read(), open(os.path.join(out[1], name), "rb").read())
        m = json.load(open(os.path.join(out[0], "acg.json"), encoding="utf-8"))
        for name, meta in m["files"].items():
            self.assertEqual(hashlib.sha256(open(os.path.join(out[0], name), "rb").read()).hexdigest(), meta["sha256"])

    def test_collisions_are_listed_without_a_disposition(self):
        _, manifest, col, _, _ = self.build(self.none)
        # 夾具裡 風之谷 是作品標題、本身也在詞包，開了詞包第一名仍是 風之谷，所以 楓之谷 只多一個候選、不列
        # （第一名真的被換掉時會列出，見 test_a_collision_counts_only_when_the_pack_changes_the_top1）。
        self.assertNotIn("ㄈㄥ ㄓ ㄍㄨˇ", col)
        self.assertNotIn("ㄉㄧㄥˋ ㄩㄢˊ ㄊㄤˊ", col)                   # 碇源堂：不開是 定元堂，不在參考名單
        self.assertNotIn("ㄧㄥˊ ㄏㄨㄛˇ ㄔㄨㄥˊ ㄓ ㄇㄨˋ", col)         # 螢火蟲之墓：不開是 螢火蟲之目
        self.assertEqual(manifest["unresolved_collision_readings"], 0)

    def test_a_collision_counts_only_when_the_pack_changes_the_top1(self):
        # 使用者 2026-10-09：開了詞包第一名沒變的（只多一個候選），不列為衝突；第一名被換掉的才列。
        reading = {"芭芭": ("ㄅㄚ", "ㄅㄚ"), "楓之谷": ("ㄈㄥ", "ㄓ", "ㄍㄨˇ")}
        def decode(pairs, prof, packs=None):
            out = []
            for w, _ in pairs:
                if w == "芭芭":
                    out.append("巴巴")                         # 開不開都是 巴巴
                else:
                    out.append("楓之谷" if packs else "風之谷")  # 開了被換掉
            return out
        col = B.detect_collisions(["芭芭", "楓之谷"], reading, [], {"巴巴", "風之谷"}, decode=decode)
        self.assertNotIn("ㄅㄚ ㄅㄚ", col)
        self.assertIn("ㄈㄥ ㄓ ㄍㄨˇ", col)

    def test_a_decision_covers_only_the_words_it_names(self):
        # 夾具：開了詞包，阿庫雷特 的第一名被換成 奇希莉卡（詞包內同音），在 阿庫雷特 的讀音上有一筆衝突。
        def decode(pairs, prof, packs=None):
            return ["奇希莉卡" if packs and w == "阿庫雷特" else w for w, _ in pairs]
        def run(row):
            c = os.path.join(self.tmp, "c.tsv")
            open(c, "w", encoding="utf-8").write(row)
            return B.build(FakeApi(), self.groups, c, self.manual, decode=decode, readings=fake_readings, exclude_tsv=self.none)[2]
        r = "ㄚ ㄎㄨˋ ㄌㄟˊ ㄊㄜˋ"
        self.assertIn(r, run(f"{r}\t某個舊詞\t+另一個舊詞\t別的詞已處置\n"))   # 同讀音但沒點名 阿庫雷特：新詞照樣列出
        self.assertNotIn(r, run(f"{r}\t阿庫雷特\t+某個舊詞\t點名了\n"))
        self.assertNotIn(r, run(f"{r}\t某個舊詞\t+阿庫雷特\t兩個都留，點名在第三欄\n"))

    def test_excluded_strings_leave_the_pack_and_the_reference_list(self):
        excl = os.path.join(self.tmp, "exclude.tsv")
        open(excl, "w", encoding="utf-8").write("# e\n碇源堂\t測試排除\n不在來源裡\t沒抽到的字串不計\n")
        files, manifest, _, _, ref = self.build(self.excl, excl)
        words = {l.split("\t")[1] for l in files["acg-add.tsv"].splitlines()}
        self.assertNotIn("碇源堂", words)
        self.assertNotIn("碇源堂", ref)
        self.assertIn("螢火蟲之墓", words)
        self.assertEqual(manifest["excluded_by_exclude_tsv"], 1)
        self.assertEqual(self.build(self.excl)[1]["excluded_by_exclude_tsv"], 0)   # 沒有排除檔
        bad = os.path.join(self.tmp, "bad-exclude.tsv")
        open(bad, "w", encoding="utf-8").write("碇源堂\n")                    # 少了理由
        with self.assertRaises(AssertionError):
            self.build(self.excl, bad)

    def test_the_committed_exclusions_are_applied_to_the_committed_pack(self):
        pack = second_column(os.path.join(B.PACKS, "acg-add.tsv"))
        rows = B.read_tsv(os.path.join(B.PACKS, "acg-exclude.tsv"))
        self.assertGreater(len(rows), 0)
        for w, why in rows:
            self.assertNotIn(w, pack, w)
            self.assertTrue(why)

    def test_the_committed_dispositions_are_applied_to_the_committed_pack(self):
        pack = second_column(os.path.join(B.PACKS, "acg-add.tsv"))
        rows = B.read_tsv(os.path.join(B.PACKS, "acg-collisions.tsv"))
        self.assertGreater(len(rows), 100)
        for reading, keep, exclude, why in rows:
            if exclude.startswith("+"):                # 兩個都留：另一個詞在詞包裡
                self.assertIn(exclude[1:], pack, exclude)
            else:
                self.assertNotIn(exclude, pack, exclude)
            self.assertTrue(why)
        self.assertIn("風之谷", pack)
        self.assertNotIn("楓之谷", pack)


if __name__ == "__main__":
    unittest.main()
