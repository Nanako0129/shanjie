"""S2w 契約 §3.3：單元檢查。不需要 dump；需要有 zhconv-rs 的 venv：
  ~/.cache/shanjie/venv-s2w/bin/python experiments/s2w/test_mw.py

突變檢查（契約要求：拿掉某一步，對應的斷言必須失敗）。2026-10-06 的做法：把 mwconv.py 複製到暫存目錄、
把 prefix() 裡的 `if groups:` 改成 `if False:`（突變 A）／把 `out = [mw["_site"]] if site else []` 改成 `out = []`（突變 B），
再跑那份複本的 test_mw.py（PYTHONPATH 補上 experiments/s2）；突變 A 的 test_noteta_group（連同 test_alias、test_count_batch_mw、test_precedence_one_way_rule）失敗，突變 B 只有 test_site_table 失敗（兩次 exit 1）。
下面的 mutation_* 另外在同一份程式碼裡用 groups=False／site=False 重現同一件事：正常斷言在突變下不成立。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
import mwconv  # noqa: E402
import mwdata  # noqa: E402

MW = {
    "groups": {"T": ["zh-cn:内存; zh-tw:記憶體;"], "G": ["zh-cn:通用电气; zh-tw:通用電氣;"]},
    "noteta": ["NoteTA", "Noteta", "NoteTA/Test"],
    "site": [["通用电气", "奇異"]],
}


def tw(raw, groups=True, site=True):
    """和 build_counts 同一條路：還原實體、MW 轉換、刪模板（3 次）、刪標記。"""
    import build_counts as bc
    t = mwconv.convert(mwconv.unescape(raw), mw(), groups, site)
    for _ in range(3):
        t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP:
        t = pat.sub(rep, t)
    return t.strip()


_MW = []


def mw():
    if not _MW:
        p = os.path.join(tempfile.mkdtemp(), "mwdata.json")
        json.dump(MW, open(p, "w", encoding="utf-8"), ensure_ascii=False)
        _MW.append(mwconv.load(p))
    return _MW[0]


def test_inline_rules():
    assert tw("-{zh-cn:内存; zh-tw:記憶體;}-") == "記憶體"
    assert tw("-{H|zh-cn:内存; zh-tw:記憶體;}-甲内存乙内存") == "甲記憶體乙記憶體"
    assert tw("-{R|王后}-") == "王后"


def test_noteta_group():
    assert "記憶體" in tw("{{NoteTA|G1=T}}甲内存")
    assert "記憶體" not in tw("{{NoteTA|G1=T}}甲内存", groups=False)   # 突變 A：不加群組規則，必須不成立


def test_alias():
    assert tw("{{Noteta|G1=T}}甲内存") == tw("{{NoteTA|G1=T}}甲内存") == "甲記憶體"
    assert tw("{{noteTA|G1=T}}甲内存") == "甲記憶體"            # 首字母不分大小寫
    assert tw("{{NoteTA/Test|G1=T}}甲内存") == "甲記憶體"


def test_numbered_param():
    assert "滑鼠" in tw("{{NoteTA|1=zh-cn:鼠标; zh-tw:滑鼠;}}甲鼠标")
    assert "滑鼠" in tw("{{NoteTA|zh-cn:鼠标; zh-tw:滑鼠;}}甲鼠标")   # 位置參數＝參數 1


def test_site_table():
    assert "奇異" in tw("甲通用电气")
    assert "奇異" not in tw("甲通用电气", site=False)               # 突變 B：不加站上轉換表，必須不成立


def test_precedence_one_way_rule():
    """單向規則（-{H|來源=>zh-tw:目標}-）與優先順序（同來源時文中較後面的勝出）；不過就停，不改用別的寫法。"""
    assert tw("-{H|通用电气=>zh-tw:奇異}-甲通用电气") == "甲奇異"
    assert tw("{{NoteTA|G1=G}}甲通用电气") == "甲通用電氣"           # 群組蓋過站上轉換表
    assert tw("{{NoteTA|G1=T}}-{H|zh-cn:内存; zh-tw:內存;}-甲内存") == "甲內存"   # 本文規則蓋過群組


def test_count_batch_mw():
    """build_counts 的 --mw 路徑：worker 載 $S2_WORK/mwdata.json，整篇轉完再計數。"""
    import build_counts as bc
    d = tempfile.mkdtemp()
    json.dump(MW, open(os.path.join(d, "mwdata.json"), "w", encoding="utf-8"), ensure_ascii=False)
    os.environ["S2_WORK"] = d
    bc._init(False, False, True)
    uni = bc.count_batch(["{{NoteTA|G1=T}}'''電腦'''的内存很大。"])[0]
    assert uni["記憶體"] == 1, dict(uni)
    bc._init(False, False, False)
    assert bc._W["mw"] is None


def test_tw_forms():
    """契約 §7：MW 轉完再換台灣字形；臺 保留。突變：convert() 不套 tw_forms()，第一條失敗（main 2026-10-06 跑過）。"""
    assert mwconv.convert("这是爲了説明裏面", mw()) == "這是為了說明裡面"
    assert mwconv.convert("臺北", mw()) == "臺北"


def test_bad_rule_segments_do_not_panic():
    """2026-10-06：NoteTA 數字參數裡以 => 開頭的段落與 HTML 註解。突變：_flat() 不丟這種段落，第一條就 panic（main 跑過）。"""
    out = mwconv.convert("{{NoteTA|1=zh:珠穆朗瑪峰;=>zh-cn:珠穆朗玛峰;zh-tw:聖母峰;}}登上珠穆朗瑪峰", mw())
    assert "聖母峰" in out, out
    out = mwconv.convert("{{NoteTA|1=zh:帕爾帕廷;zh-hant:白卜庭;<!-- 註解 -->}}帕爾帕廷", mw())
    assert "白卜庭" in out and "註解" not in out.split("}}")[-1], out


def test_panic_becomes_a_normal_error():
    import zhconv_rs

    class Panic(BaseException):
        pass

    def boom(*a):
        raise Panic()
    real = zhconv_rs.zhconv
    zhconv_rs.zhconv = boom
    try:
        mwconv.convert("文字", mw())
    except RuntimeError:
        pass
    else:
        raise AssertionError("panic was not turned into RuntimeError")
    finally:
        zhconv_rs.zhconv = real


def test_mwdata_parsers():
    lua = """local Item = require('Module:CGroup/core').Item;
-- Item('註解', 'zh-cn:不要; zh-tw:不要;'),
return {
	name = 'IT',
	{ type = 'text', text = 'zh-cn:說明; rule = nope' },
	Item('账号', 'zh-cn:账号; zh-tw:帳號;'),
	{ type = 'item', rule = "zh-cn:内存; zh-tw:記憶體;" },
	Item('it\\'s', 'zh-cn:a\\'b; zh-tw:c;'),
}"""
    assert mwdata.module_rules(lua) == ["zh-cn:账号; zh-tw:帳號;", "zh-cn:内存; zh-tw:記憶體;", "zh-cn:a'b; zh-tw:c;"]
    assert mwdata.template_rules("{{CGroup/header}}\n{{CItem|zh-cn:甲; zh-tw:乙;|desc}}\n{{CItem|zh-cn:丙;\nzh-tw:丁;}}") == ["zh-cn:甲; zh-tw:乙;", "zh-cn:丙; zh-tw:丁;"]
    assert mwdata.module_rules("Item(nil, 'zh-cn:朝鲜;zh-tw:北韓;'),\nItem( nil ,\"zh-cn:韩国;zh-tw:南韓;\")") == ["zh-cn:朝鲜;zh-tw:北韓;", "zh-cn:韩国;zh-tw:南韓;"]
    assert mwdata.template_rules("{{CItemLan|zh:指環王;zh-hant:魔戒;|The Lord of the Rings}}") == ["zh:指環王;zh-hant:魔戒;"]
    for src in ("return require('Module:CGroup/Hayate the Combat Butler')", "return require [[Module:CGroup/地名]]",
                "return require( '模块:CGroup/IT' );", 'return require("模組:CGroup/IT")'):
        a = mwdata.LUA_ALIAS.match(src)
        assert a and a.group(1) in ("Hayate the Combat Butler", "地名", "IT"), src
    assert mwdata.site_rules("說明\n*通用电气=>奇異;\n* 软件 => 軟體 ;\n*壞{}=>x;\n")[0] == [("通用电气", "奇異"), ("软件", "軟體")]
    pages = {"M": {"IT": ("rules", ["r1"]), "Alias": ("alias", "IT"), "Both": ("rules", ["m"])}, "T": {"Both": ("rules", ["t"]), "OnlyT": ("rules", ["t2"])}}
    redir = {"Template:Noteta": "Template:NoteTA", "Template:N2": "Template:Noteta", "Template:Other": "Template:Foo"}
    refs = {("Noteta", "Alias"): 3, ("Foo", "Nope"): 9, ("NoteTA", "Missing"): 2}
    data, unres = mwdata.build(pages, redir, {"MediaWiki:Conversiontable/zh-tw": "*甲=>乙;", "MediaWiki:Conversiontable/zh-hant": ("redirect", "MediaWiki:X"),
                                              "MediaWiki:X": "*甲=>丙;\n*丁=>戊;"}, refs, refs, 10)
    assert data["groups"] == {"IT": ["r1"], "Alias": ["r1"], "Both": ["m"], "OnlyT": ["t2"]}
    assert data["noteta"] == ["N2", "NoteTA", "Noteta"], data["noteta"]
    assert data["site"] == [("丁", "戊"), ("甲", "乙")]                  # zh-tw 優先，zh-hant 補沒有的
    assert dict(unres) == {"Missing": 2}


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("ok  ", name)
            except Exception as e:
                fails += 1; print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
