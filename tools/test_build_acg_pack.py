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

    def test_numbered_parameter_is_positional(self):
        self.assertEqual(B.tw_values("1=zh-tw:魯夫;zh-cn:路飞"), [("魯夫", "zh-tw")])
        self.assertEqual(B.tw_values("zh-tw:甲甲|2=zh-hk:乙乙"), [("甲甲", "zh-tw")])
        self.assertEqual(B.tw_values("1=zh-tw:甲甲|original=乙"), [("甲甲", "zh-tw")])

    def test_group_rules_read_template_items_and_lua_items_from_the_same_page(self):
        page = "{{CItem|zh-tw:甲甲甲}}\n{ type = 'item', original = '', rule = 'zh-tw:乙乙乙;zh-hk:丙丙丙;', description = 'x' },\nItem('x', 'zh-tw:丁丁丁')\n"
        got = sorted(v for r in B.group_rules(page) for v, _ in B.tw_values(r))
        self.assertEqual(got, ["丁丁丁", "乙乙乙", "甲甲甲"])

    def test_commented_out_rules_are_not_collected(self):
        wiki = "{{CItem|zh-tw:甲甲甲}}\n<!-- {{CItem|zh-tw:乙乙乙}} -->\n<!--\n{{CItem|zh-tw:丙丙丙}}\n-->{{CItem|zh-tw:丁丁丁}}"
        self.assertEqual(sorted(v for r in B.group_rules(wiki) for v, _ in B.tw_values(r)), ["丁丁丁", "甲甲甲"])
        lua = ("Item('x', 'zh-tw:甲甲甲')\n-- Item('x', 'zh-tw:乙乙乙')\n--[[ Item('x', 'zh-tw:丙丙丙')\nItem('x', 'zh-tw:戊戊戊') ]] Item('x', 'zh-tw:丁丁丁')\n"
               "--[==[ Item('x', 'zh-tw:己己己') ]==]\nItem('x', 'zh-tw:庚--庚庚') -- Item('x', 'zh-tw:辛辛辛')\n")
        got = sorted(v for r in B.group_rules(lua, lua=True) for v, _ in B.tw_values(r))
        self.assertEqual(got, ["丁丁丁", "甲甲甲"])                # 字串裡的 -- 不算註解（庚--庚庚 含非漢字，tw_values 不收，但後面的 辛辛辛 照樣被註解吃掉）

    def test_pages_follows_continue_when_the_response_is_truncated(self):
        class Truncating:
            calls = 0

            def wiki(self, **p):
                self.calls += 1
                ts = p["titles"].split("|")
                i = int(p.get("rvcontinue", 0))
                rev = lambda t: {"title": t, "revisions": [{"revid": 10 + ts.index(t), "timestamp": "2026-10-01T00:00:00Z", "slots": {"main": {"content": t + "內文"}}}]}
                out = [rev(ts[i])] + [{"title": t} for t in ts[i + 1:]]      # 一次只給一頁的內容，其餘沒有 revisions
                r = {"query": {"pages": out}}
                if i + 1 < len(ts):
                    r["continue"] = {"rvcontinue": str(i + 1), "continue": "||"}
                return r
        api = Truncating()
        got = B.pages(api, ["甲", "乙", "丙"])
        self.assertEqual({t: v["text"] for t, v in got.items()}, {"甲": "甲內文", "乙": "乙內文", "丙": "丙內文"})
        self.assertEqual(api.calls, 3)

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
        # 契約 A2.2：原名要在名字緊接的括號裡，不是摘要前 120 字的任何地方
        self.assertFalse(ok("阿庫雷特", "阿庫雷特，主角（アクレット）"))
        self.assertFalse(ok("阿庫雷特", "阿庫雷特（主角）；原名 Akuret"))
        self.assertTrue(ok("阿庫雷特", "阿庫雷特 (Akuret)"))                  # 半形括號、拉丁字母
        self.assertTrue(ok("阿庫雷特", "阿庫雷特 （アクレット，主角）"))        # 括號前可有空白
        self.assertFalse(ok("阿庫雷特", "阿庫雷特（A）"))                     # 單一拉丁字母不算原名
        for credit in ("阿庫雷特（CV：Akari）", "阿庫雷特（CV：ひかさ）", "阿庫雷特（OVA）", "阿庫雷特（TV）", "阿庫雷特（聲優：Akari）", "阿庫雷特（由 Akari 配音）", "阿庫雷特（聲：Akari, Mv版）"):
            self.assertFalse(ok("阿庫雷特", credit), credit)               # 聲優、播出形式不是原名
        # 第一個聲優標記之後的文字不是原名的證據；ver.、第N話、XY／IT／TVB 這類標籤與縮寫也不是
        self.assertFalse(ok("勅使河原鏡花", "勅使河原鏡花（勅使河原 鏡花，聲：長月アキ）"))
        self.assertTrue(ok("勅使河原鏡花", "勅使河原鏡花（てしがわら きょうか，聲：長月アキ）"))
        for tag in ("天音姐妹（泳裝ver.）", "天音姐妹（偶像Ver. 2）", "天音姐妹（XY第129話）", "天音姐妹（IT，聲：山田）", "天音姐妹（TVB）", "天音姐妹（第5集）"):
            self.assertFalse(ok("天音姐妹", tag), tag)
        self.assertTrue(ok("天音姐妹", "天音姐妹（Amane Shimai, ver. 2）"))
        self.assertTrue(ok("由井薰", "由井薰（ゆい かおる）"))                   # 姓以「由」開頭的名字不是聲優標註
        self.assertTrue(ok("阿庫雷特", "阿庫雷特（Akuret, CV Akari）"))        # 有原名，後面才有聲優

    def test_year_list_takes_only_the_links_in_the_title_column(self):
        got = B.year_works(YEAR_LIST)
        self.assertEqual(got, ["新作動畫 (動畫)", "風之谷", "紅連結作品", "傳統作品"])      # 錨點拿掉、去重；rowspan 的列欄位對得上；紅連結模板、製作公司欄的連結、<ref> 裡的連結都不取
        with self.assertRaises(SystemExit):                    # 有 wikitable 卻沒有作品名欄：頁面結構變了，停
            B.year_works('{| class="wikitable"\n|-\n!日期!!名稱\n|-\n|1月||[[甲]]\n|}')
        with self.assertRaises(SystemExit):
            B.year_works("沒有表格")

    def test_year_list_parser_survives_rows_that_end_early_colspan_and_markup(self):
        table = lambda *rows: '{| class="wikitable"\n!日期!!作品名!!話數\n' + "".join("|-\n" + r + "\n" for r in rows) + "|}"
        # 一列比標題列早結束，而且最後一欄被上一列的 rowspan 佔著：佔位要補上並扣掉，下一列才不會多出一格
        t = table("|1月||[[甲]]||rowspan=2|12話", "|2月||[[乙]]", "|3月||[[丙]]||12話")
        self.assertEqual(B.table_rows(t)[1], [["1月", "[[甲]]", "12話"], ["2月", "[[乙]]", ""], ["3月", "[[丙]]", "12話"]])
        self.assertEqual(B.year_works(t), ["甲", "乙", "丙"])
        # colspan：佔好幾欄，文字放第一欄
        self.assertEqual(B.table_rows(table("|1月||colspan=2|[[甲]]", "|2月||[[乙]]||z"))[1], [["1月", "[[甲]]", ""], ["2月", "[[乙]]", "z"]])
        # `!!` 只在標題行拆；資料行裡的 !! 是內容
        self.assertEqual(B.table_cells("a!!b||c"), ["a!!b", "c"])
        self.assertEqual(B.table_cells("a!!b||c", header=True), ["a", "b", "c"])
        # 自閉合的 <ref .../>，屬性裡有 /：不能吃到後面的文字，也不能把 ref 內容當連結
        t = table('|1月||[[甲]]<ref name="a/b" />||1', '|2月||[[乙]]<ref>[[不要]]</ref>||2', '|3月||[[丙]]||3')
        self.assertEqual(B.year_works(t), ["甲", "乙", "丙"])
        # 語言轉換只取 zh-tw 的分支
        t = table("|1月||[[-{zh-tw:台灣作品;zh-cn:大陸作品}-]]||1", "|2月||-{zh-tw:[[乙]];zh-cn:[[丁]]}-||2", "|3月||[[-{丙}-]]||3")
        self.assertEqual(B.year_works(t), ["台灣作品", "乙", "丙"])

    def test_year_list_rowspan_gap_header_colspan_zh_tw_and_footnotes(self):
        table = lambda head, *rows: '{| class="wikitable"\n' + head + "\n" + "".join("|-\n" + r + "\n" for r in rows) + "|}"
        # 一列在沒被佔用的欄位結束，後面還有被 rowspan 佔著的欄位：每一個被佔的欄位都補上並扣掉一列
        t = table("!日期!!作品名!!話數!!備註", "|1月||[[甲]]||12||rowspan=2|n", "|2月||[[乙]]", "|3月||[[丙]]||12||n3")
        self.assertEqual(B.table_rows(t)[1], [["1月", "[[甲]]", "12", "n"], ["2月", "[[乙]]", "", ""], ["3月", "[[丙]]", "12", "n3"]])
        self.assertEqual(B.year_works(t), ["甲", "乙", "丙"])
        # 標題列的 colspan 展開，作品名欄的索引才對得上資料列
        self.assertEqual(B.year_works(table("!日期!!colspan=2|作品名!!話數", "|1月||[[甲]]||甲原名||12")), ["甲"])
        # colspan=0 的資料格：欄位對不上，建置中止，不能靜靜地位移
        with self.assertRaises(SystemExit):
            B.year_works(table("!日期!!作品名!!話數", "|1月||colspan=0|[[甲]]||12"))
        # 沒有語言變體的 -{ }-：整段保留（冒號、分號不是語言代碼）；&amp; 還原
        self.assertEqual(B.zh_tw_branch("[[-{Re:從零開始的異世界生活}-]]"), "[[Re:從零開始的異世界生活]]")
        self.assertEqual(B.year_works(table("!日期!!作品名", "|1月||[[-{Re:從零開始的異世界生活}-]]", "|2月||[[-{A&amp;B}-]]")), ["Re:從零開始的異世界生活", "A&B"])
        self.assertEqual(B.zh_tw_branch("-{zh-cn:甲;zh-hk:乙}-"), "甲")                # 只有別的語言：取第一個
        # 作品名在第一欄的列，其他欄是空的：不是註腳列；作品名欄也是空的整列才是
        self.assertEqual(B.year_works(table("!作品名!!日期", "|[[甲]]||", "|colspan=2|註腳")), ["甲"])

    def test_year_list_stops_on_a_misaligned_row_or_too_few_works(self):
        ok = '{| class="wikitable"\n!日期!!作品名\n|-\n|1月||[[甲]]\n|-\n|colspan=2|註腳 <references/>\n|}'
        self.assertEqual(B.year_works(ok), ["甲"])               # 橫跨整列的註腳列不算
        with self.assertRaises(SystemExit):                    # 比標題列多出一格
            B.year_works('{| class="wikitable"\n!日期!!作品名\n|-\n|1月||[[甲]]||多出來\n|}')
        with self.assertRaises(SystemExit):                    # 短到沒有作品名欄
            B.year_works('{| class="wikitable"\n!日期!!話數!!作品名\n|-\n|1月||12\n|}')
        with self.assertRaises(SystemExit):                    # 取到的作品太少
            B.year_works(ok, minimum=2)

    def test_groups_column_four_must_be_include_or_exclude(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        good = os.path.join(d, "g.tsv")
        open(good, "w", encoding="utf-8").write("# c\n作品\t碇系\tEVA\tinclude\t動畫\n作品\t某電影\tMovie\texclude\t電影\n")
        self.assertEqual(len(B.read_groups(good)), 2)
        bad = os.path.join(d, "b.tsv")
        open(bad, "w", encoding="utf-8").write("作品\t碇系\tEVA\tincluded\t動畫\n")
        with self.assertRaises(SystemExit):
            B.read_groups(bad)


class Dedupe(unittest.TestCase):
    def test_words_already_in_the_lexicon_are_dropped(self):
        self.assertEqual(B.dedupe(["悲慘世界", "碇源堂", "碇源堂"], {"悲慘世界"}), ["碇源堂"])

    def test_the_committed_pack_shares_no_string_with_base_overlay_or_sandhi(self):
        pack = second_column(os.path.join(B.PACKS, "acg-add.tsv"))
        self.assertGreater(len(pack), 10000)
        self.assertEqual(pack & lexicon_words(), set())


class Ordering(unittest.TestCase):
    def test_a_decision_rows_keep_word_ranks_before_more_sources(self):
        n = {"乙乙": 5, "甲甲": 1}
        reading = {"乙乙": ["ㄅ"], "甲甲": ["ㄅ"]}
        self.assertEqual(B.ordered(["乙乙", "甲甲"], n.get, {("ㄅ", "甲甲"): 0, ("ㄅ", "乙乙"): 1}, reading), ["甲甲", "乙乙"])    # 處置列的順序先於來源數
        self.assertEqual(B.ordered(["乙乙", "甲甲"], n.get, {}, reading), ["乙乙", "甲甲"])

    def test_more_sources_first_then_by_string(self):
        n = {"乙乙": 1, "甲甲": 1, "丙丙": 3}
        self.assertEqual(B.ordered(["乙乙", "甲甲", "丙丙"], n.get), ["丙丙"] + sorted(["乙乙", "甲甲"]))
        self.assertEqual(B.ordered(["丙丙", "甲甲", "乙乙"], n.get), B.ordered(["乙乙", "丙丙", "甲甲"], n.get))


# ---- a fake API serving a small, fixed corner of Wikipedia

YEAR_LIST = """{{noteTA|G1=Anime}}
== 電視動畫 ==
{| class="wikitable sortable" style="width:100%"
!style="width:12%"|開始日－結束日!!作品名!!原名!!製作公司!!話數
|-
|1月3日<!--22:30--><ref group="冬">[[不要這個]]</ref>－3月26日||[[新作動畫 (動畫)|新作動畫]]||{{lang|ja|しんさく}}||[[某公司]]||12話
|-
|rowspan=2|4月4日||{{link-ja|紅連結|あか}}||{{lang|ja|x}}||[[某公司]]||12話
|-
|[[風之谷#第2季|風之谷]]（第2期）||{{lang|ja|y}}||z||12話
|-
|7月||[[新作動畫 (動畫)#第2季|新作動畫 第2期]]||{{lang|ja|z}}||w||12話
|-
|10月||[[紅連結作品]]||{{lang|ja|r}}||w||12話
|-
|10月||[[傳統作品]]||{{lang|ja|t}}||w||12話
|}
== 劇場版 ==
{| class="wikitable"
|-
!上映日!!作品名!!原名
|-
|8月1日
|[[File:x.jpg]][[:ja:外語連結]]
|{{lang|ja|a}}
|}
"""
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
    "2026年日本動畫列表": (8, "2026-10-09T00:00:00Z", YEAR_LIST),
    "風之谷角色列表": (6, "2026-10-03T00:00:00Z", ""),
}
ARTICLE = ("<h2>登場人物</h2><ul><li>阿庫雷特（アクレット）</li><li>朋友（ともだち）</li><li>米卡莎的母親（ママ）</li></ul>")
REVID = {"風之谷": 5, "風之谷角色列表": 6, "新作動畫 (動畫)": 7, "传统作品": 9}
CONVERTED = {"傳統作品": "传统作品"}      # 簡繁不同的標題：wikitext 的 [[傳統作品]] 找得到，action=parse 要用實際標題
PARSES = {
    "風之谷": {"title": "風之谷 (電影)", "displaytitle": "<span>風之谷 (電影)</span>", "text": ARTICLE, "links": [
        {"ns": 0, "title": "風之谷角色列表", "exists": True}, {"ns": 0, "title": "不存在角色列表", "exists": False}]},
    "風之谷角色列表": {"title": "風之谷角色列表", "displaytitle": "風之谷角色列表", "text": "<h2>人物</h2><ul><li>碇真次郎（シンジロウ）</li></ul>", "links": []},
    # 年度清單上的作品：艾蓮娜 第一次出現沒有原名、第二次有（A2.2 所有出現都判斷）；卡羅爾 的原名不在緊接的括號裡（A2.2 相鄰檢查）
    "新作動畫 (動畫)": {"title": "新作動畫 (動畫)", "displaytitle": "<span>新作動畫 (動畫)</span>", "links": [],
                        "text": "<h2>登場人物</h2><ul><li>艾蓮娜</li><li>卡羅爾，主角（キャロル）</li></ul><h2>角色介紹</h2><dl><dt>艾蓮娜（エレナ）</dt></dl>"},
    "传统作品": {"title": "传统作品", "displaytitle": "傳統作品", "text": "", "links": []},
}


class FakeApi:
    parsed = []

    def wiki(self, **p):
        if p.get("converttitles"):                  # resolve_titles：存在的標題、簡繁轉換、紅連結
            ts = p["titles"].split("|")
            gone = lambda t: CONVERTED.get(t, t) not in PARSES and t not in PAGES
            return {"query": {"converted": [{"from": t, "to": CONVERTED[t]} for t in ts if t in CONVERTED],
                              "pages": [{"title": CONVERTED.get(t, t), **({"missing": True} if gone(t) else {"pageid": 1})} for t in ts]}}
        if p["action"] == "parse":
            self.parsed.append(p["page"])
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
    "新作動畫": "ㄒㄧㄣ ㄗㄨㄛˋ ㄉㄨㄥˋ ㄏㄨㄚˋ", "傳統作品": "ㄔㄨㄢˊ ㄊㄨㄥˇ ㄗㄨㄛˋ ㄆㄧㄣˇ", "艾蓮娜": "ㄞˋ ㄌㄧㄢˊ ㄋㄚˋ", "卡羅爾": "ㄎㄚˇ ㄌㄨㄛˊ ㄦˇ",
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

    def build(self, collisions, exclude=None, real_decoder=False, years=()):
        # 真的解碼器每次建置要跑 4 個 CLI 程序；只有斷言需要真實解碼結果的測試才開（real_decoder=True）。
        return B.build(FakeApi(), self.groups, collisions, self.manual, readings=fake_readings, exclude_tsv=exclude or self.none, years=years, min_year_works=1,
                       **({} if real_decoder else {"decode": lambda pairs, prof, packs=None: [w for w, _ in pairs]}))

    def test_pack_content_and_filters(self):
        files, manifest, col, unread, ref, _ = self.build(self.excl)
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

    def test_year_list_works_are_built_and_every_name_occurrence_is_judged(self):
        files, manifest, _, _, ref, _ = self.build(self.excl, years=[2026])
        words = {l.split("\t")[1] for l in files["acg-add.tsv"].splitlines()}
        self.assertIn("新作動畫", words)                # 年度清單的作品：顯示標題進詞包
        self.assertIn("艾蓮娜", words)                  # 第一次出現沒有原名，第二次有：收
        self.assertIn("卡羅爾", ref)
        self.assertNotIn("卡羅爾", words)               # 原名不在緊接的括號裡：不收，但仍在參考名單
        self.assertIn("艾蓮娜\tchar\t新作動畫 (動畫)@7\t", files["acg-sources.tsv"])
        self.assertIn("傳統作品", words)                # 標題簡繁不同的條目（實際標題 传统作品）也取得到，顯示標題是 zh-tw
        self.assertEqual(manifest["articles_missing"], ["紅連結作品"])      # 紅連結記下來、不去 parse；製作公司欄的連結沒有被當成條目
        self.assertNotIn("紅連結作品", FakeApi.parsed)
        self.assertNotIn("某公司", FakeApi.parsed)
        self.assertEqual(manifest["yearly_lists"], {"2026": {"title": "2026年日本動畫列表", "revid": 8, "ts": "2026-10-09T00:00:00Z", "links": 4}})
        self.assertEqual(manifest["latest_source_revision"], "2026-10-09T00:00:00Z")      # 清單頁的時間算進版號的日期
        self.assertTrue(manifest["version"].startswith("20261009-"))

    def test_a_changed_pack_changes_the_version(self):
        manual = os.path.join(self.tmp, "manual2.tsv")
        open(manual, "w", encoding="utf-8").write(open(self.manual, encoding="utf-8").read() + "艾倫葉卡\t某作品\t角色\n")
        more = B.build(FakeApi(), self.groups, self.excl, manual, readings=lambda w: fake_readings(w) | {"艾倫葉卡": (["ㄞˋ", "ㄌㄨㄣˊ", "ㄧㄝˋ", "ㄎㄚˇ"], False)}, exclude_tsv=self.none, years=(),
                        decode=lambda pairs, prof, packs=None: [w for w, _ in pairs])[1]
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

    def test_a_manual_words_fourth_column_adds_a_reading(self):
        manual = os.path.join(self.tmp, "manual3.tsv")
        open(manual, "w", encoding="utf-8").write("奇希莉卡\t無職轉生\t角色\tㄑㄧˊ ㄒㄧ ㄌㄧˋ ㄍㄚˇ\n")
        files = B.build(FakeApi(), self.groups, self.excl, manual, readings=fake_readings, exclude_tsv=self.none, years=(),
                        decode=lambda pairs, prof, packs=None: [w for w, _ in pairs])[0]
        self.assertIn("ㄑㄧˊ-ㄒㄧ-ㄌㄧˋ-ㄍㄚˇ\t奇希莉卡\t", files["acg-add.tsv"])         # 另外的讀音
        self.assertIn("ㄑㄧˊ-ㄒㄧ-ㄌㄧˋ-ㄎㄚˇ\t奇希莉卡\t", files["acg-add.tsv"])         # 讀音工具的讀音也還在
        bad = os.path.join(self.tmp, "manual4.tsv")
        open(bad, "w", encoding="utf-8").write("奇希莉卡\t無職轉生\t角色\tㄑㄧˊ ㄒㄧ\n")
        with self.assertRaises(SystemExit):
            B.build(FakeApi(), self.groups, self.excl, bad, readings=fake_readings, exclude_tsv=self.none, years=(), decode=lambda pairs, prof, packs=None: [w for w, _ in pairs])

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
        _, manifest, col, _, _, _ = self.build(self.none, real_decoder=True)
        # 夾具裡 風之谷（作品標題）與 楓之谷（轉換組）都在詞包、同讀音、來源數相同；同分時依字串排序，楓之谷排在前（pack_rows 的 TIE），
        # 開了詞包第一名是 楓之谷，所以風之谷被換掉，列為未處置（正式資料由 acg-collisions.tsv 處置）。
        self.assertIn("ㄈㄥ ㄓ ㄍㄨˇ", col)
        self.assertNotIn("ㄉㄧㄥˋ ㄩㄢˊ ㄊㄤˊ", col)                   # 碇源堂：不開是 定元堂，不在參考名單
        self.assertNotIn("ㄧㄥˊ ㄏㄨㄛˇ ㄔㄨㄥˊ ㄓ ㄇㄨˋ", col)         # 螢火蟲之墓：不開是 螢火蟲之目
        self.assertEqual(manifest["unresolved_collision_readings"], 1)      # 楓之谷／風之谷 那一個，沒有處置檔

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

    def test_a_pack_word_that_displaces_an_existing_lexicon_word_is_a_type_c_collision(self):
        reading = {"歐兜邁": ("ㄡ", "ㄉㄡ", "ㄇㄞˋ"), "甲甲甲": ("ㄅㄚ", "ㄅㄚ", "ㄅㄚ"), "乙乙乙": ("ㄆㄚ", "ㄆㄚ", "ㄆㄚ")}
        def decode(pairs, prof, packs=None):
            if not packs:
                return ["歐兜賣" if w == "歐兜邁" else ("丙丙丙" if w == "甲甲甲" else w) for w, _ in pairs]
            return [w for w, _ in pairs]                       # 開了之後第一名都是 w 自己
        # 歐兜賣 是既有詞庫的詞 → c；丙丙丙 不在既有詞庫也不在參考名單 → 不列；乙乙乙 開不開都一樣 → 不列
        col = B.detect_collisions(["歐兜邁", "甲甲甲", "乙乙乙"], reading, [], set(), decode=decode, existing={"歐兜賣"})
        self.assertEqual(col, {"ㄡ ㄉㄡ ㄇㄞˋ": [("歐兜邁", "c", "chat", "歐兜賣", "歐兜邁"), ("歐兜邁", "c", "formal", "歐兜賣", "歐兜邁")]})

    def test_type_c_words_are_dropped_by_rule_and_the_rest_is_rerun(self):
        # 使用者 2026-10-10：詞包詞擠掉既有詞（朋友 在基底）→ 丟掉詞包詞，不需要 acg-collisions.tsv 的列。
        def decode(pairs, prof, packs=None):       # 阿庫雷特：不開是 朋友；開了而且詞包裡有它就是它自己
            pack = open(os.path.join(packs, "acg-add.tsv"), encoding="utf-8").read() if packs else ""
            return [w if (w != "阿庫雷特" or w in pack) else "朋友" for w, _ in pairs] if packs else ["朋友" if w == "阿庫雷特" else w for w, _ in pairs]
        files, manifest, col, _, _, dropped = B.build(FakeApi(), self.groups, self.none, self.manual, decode=decode, readings=fake_readings, exclude_tsv=self.none, years=())
        self.assertNotIn("阿庫雷特", {l.split("\t")[1] for l in files["acg-add.tsv"].splitlines()})
        self.assertEqual([(w, o) for _, w, o, _ in dropped], [("阿庫雷特", "朋友")])
        self.assertEqual((manifest["dropped_by_lexicon_rule"], manifest["unresolved_collision_readings"]), (1, 0))

    def test_a_decision_covers_only_the_words_it_names(self):
        # 夾具：開了詞包，阿庫雷特 的第一名被換成 奇希莉卡（詞包內同音），在 阿庫雷特 的讀音上有一筆衝突。
        def decode(pairs, prof, packs=None):
            return ["奇希莉卡" if packs and w == "阿庫雷特" else w for w, _ in pairs]
        def run(row):
            c = os.path.join(self.tmp, "c.tsv")
            open(c, "w", encoding="utf-8").write(row)
            return B.build(FakeApi(), self.groups, c, self.manual, decode=decode, readings=fake_readings, exclude_tsv=self.none, years=())[2]
        r = "ㄚ ㄎㄨˋ ㄌㄟˊ ㄊㄜˋ"
        self.assertIn(r, run(f"{r}\t某個舊詞\t+另一個舊詞\t別的詞已處置\n"))   # 同讀音但沒點名 阿庫雷特：新詞照樣列出
        self.assertIn(r, run(f"{r}\t阿庫雷特\t+某個舊詞\t點名了\n"))           # 點名了阿庫雷特，但開了之後的第一名奇希莉卡沒被點名：新詞搶走第一名，照樣列出
        self.assertIn(r, run(f"{r}\t阿庫雷特\t+奇希莉卡\t兩個都點名，但保留的詞阿庫雷特不是第一名\n"))      # 保留的詞必須是第一名，點名第二個不夠
        self.assertNotIn(r, run(f"{r}\t奇希莉卡\t+阿庫雷特\t保留的詞奇希莉卡是第一名\n"))

    def test_the_keep_word_is_the_first_row_whose_keep_word_is_in_the_pack(self):
        # 同一個讀音兩列：第一列的保留的詞不在詞包，第二列的在 → 以第二列的為準（開詞包後的第一名是它就算已處置）
        def decode(pairs, prof, packs=None):
            return ["奇希莉卡" if packs and w == "阿庫雷特" else w for w, _ in pairs]
        r = "ㄚ ㄎㄨˋ ㄌㄟˊ ㄊㄜˋ"
        c = os.path.join(self.tmp, "c2.tsv")
        def run(rows):
            open(c, "w", encoding="utf-8").write(rows)
            return B.build(FakeApi(), self.groups, c, self.manual, decode=decode, readings=fake_readings, exclude_tsv=self.none, years=())[2]
        self.assertNotIn(r, run(f"{r}\t某個舊詞\t+另一個舊詞\t第一列的保留的詞不在詞包\n{r}\t奇希莉卡\t+阿庫雷特\t第二列\n"))
        self.assertIn(r, run(f"{r}\t某個舊詞\t+另一個舊詞\t第一列都不在詞包\n{r}\t阿庫雷特\t+某個舊詞\t第二列保留阿庫雷特但第一名是奇希莉卡\n"))
        # 第一列的保留的詞不在詞包、但 + 後面的詞在：排序上它排在第二列之前，所以它才是該有的第一名
        self.assertNotIn(r, run(f"{r}\t某個舊詞\t+奇希莉卡\t第一列的保留的詞不在詞包\n{r}\t阿庫雷特\t+某個舊詞\t第二列\n"))

    def test_the_required_first_word_is_the_first_named_word_in_ordering(self):
        # 兩列的保留的詞都在詞包：第一列的保留的詞必須是第一名（next → reversed 就會讓這個測試失敗）
        def decode(pairs, prof, packs=None):
            return ["奇希莉卡" if packs and w == "阿庫雷特" else w for w, _ in pairs]
        r = "ㄚ ㄎㄨˋ ㄌㄟˊ ㄊㄜˋ"
        c = os.path.join(self.tmp, "c3.tsv")
        def run(rows):
            open(c, "w", encoding="utf-8").write(rows)
            return B.build(FakeApi(), self.groups, c, self.manual, decode=decode, readings=fake_readings, exclude_tsv=self.none, years=())[2]
        both = f"{r}\t阿庫雷特\t+某個舊詞\t第一列\n{r}\t奇希莉卡\t+阿庫雷特\t第二列\n"
        self.assertIn(r, run(both))                    # 第一名是奇希莉卡，但第一列的保留的詞阿庫雷特才該是第一名
        self.assertNotIn(r, run(f"{r}\t奇希莉卡\t+阿庫雷特\t第一列\n{r}\t阿庫雷特\t+某個舊詞\t第二列\n"))    # 順序反過來就對了
        rank = B.collision_rank([[r, "阿庫雷特", "+某個舊詞", ""], [r, "奇希莉卡", "+阿庫雷特", ""]])
        self.assertEqual(B.first_named(rank, {"阿庫雷特", "奇希莉卡"}), {r: "阿庫雷特"})
        self.assertEqual(B.first_named(rank, {"奇希莉卡"}), {r: "奇希莉卡"})              # 第一個不在詞包就看後面的

    def test_a_row_may_not_keep_a_word_and_name_it_as_the_other_word(self):
        bad = os.path.join(self.tmp, "self.tsv")
        open(bad, "w", encoding="utf-8").write("ㄅㄚ\t甲甲\t+甲甲\t自己點名自己\n")
        with self.assertRaises(SystemExit):
            B.read_collisions(bad)

    def test_excluded_strings_leave_the_pack_and_the_reference_list(self):
        excl = os.path.join(self.tmp, "exclude.tsv")
        open(excl, "w", encoding="utf-8").write("# e\n碇源堂\t測試排除\n不在來源裡\t沒抽到的字串不計\n")
        files, manifest, _, _, ref, _ = self.build(self.excl, excl)
        words = {l.split("\t")[1] for l in files["acg-add.tsv"].splitlines()}
        self.assertNotIn("碇源堂", words)
        self.assertNotIn("碇源堂", ref)
        self.assertIn("螢火蟲之墓", words)
        self.assertEqual(manifest["excluded_by_exclude_tsv"], 1)
        self.assertEqual(self.build(self.excl)[1]["excluded_by_exclude_tsv"], 0)   # 沒有排除檔
        bad = os.path.join(self.tmp, "bad-exclude.tsv")
        open(bad, "w", encoding="utf-8").write("碇源堂\n")                    # 少了理由
        with self.assertRaises(SystemExit):
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
            if exclude.startswith("+"):                # 兩個都留：兩個詞都在詞包裡
                self.assertIn(keep, pack, keep)
                self.assertIn(exclude[1:], pack, exclude)
            else:
                self.assertNotIn(exclude, pack, exclude)
            self.assertTrue(why)
        self.assertIn("風之谷", pack)
        self.assertNotIn("楓之谷", pack)

    def test_every_decided_readings_keep_word_is_first_with_the_committed_pack(self):
        # 處置列同一個讀音的第一列，保留的詞：只要詞包改變了第一名，新的第一名就必須是它（聊天與書面；出貨的詞包與真的解碼器）。
        # 詞包沒有改變第一名（詞庫的詞或解碼器拼出的字串原本就贏）的讀音不算：保留的詞贏不了語言模型是已知的限制（見研究紀錄）。
        rank = B.collision_rank(B.read_tsv(os.path.join(B.PACKS, "acg-collisions.tsv")))
        first = B.first_named(rank, second_column(os.path.join(B.PACKS, "acg-add.tsv")))         # 和建置工具同一個定義
        self.assertGreater(len(first), 100)
        pairs = [(k, r.split()) for r, k in first.items()]
        for prof in ("chat", "formal"):
            on, off = B.top1(pairs, prof, B.PACKS), B.top1(pairs, prof)
            bad = [(k, g) for (k, _), g, o in zip(pairs, on, off) if g != k and g != o]
            self.assertEqual(bad, [], prof)


if __name__ == "__main__":
    unittest.main()
