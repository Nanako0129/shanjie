"""S2n 契約 §2.1 的固定字串單元檢查。用法：python3 experiments/s2/test_convert.py（失敗會 AssertionError）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_counts as bc  # noqa: E402

conv = bc.load_conv()


def c(s):
    return bc.convert(s, *conv)


SIMPLIFIED = [   # 簡體句：之后 之後、由于 由於 …
    ("之后他去了", "之後他去了"), ("由于下雨", "由於下雨"), ("你住哪里", "你住哪裡"), ("最后一个", "最後一個"),
    ("于是我们走了", "於是我們走了"), ("台湾很大", "台灣很大"),
    ("皇后来了", "皇后來了"), ("干涉别人", "干涉別人"), ("只有这里", "只有這裡"), ("系统发生错误", "系統發生錯誤"),
    ("头发和公里", "頭髮和公里"),
]
TRADITIONAL = [   # 繁體句不變；夾一個簡體專用字的繁體句只改那個字
    ("皇后說他在里長辦公室", "皇后說他在里長辦公室"), ("大概十分鐘後到", "大概十分鐘後到"),
    ("我們今天在后面等书", "我們今天在后面等書"),
]
KEEP = [   # 台灣用字例外：單字不換成 喫／巖；詞組照詞組
    ("我们早饭吃什么", "我們早飯吃什麼"), ("这块岩石很大", "這塊岩石很大"), ("嘴唇很红", "嘴唇很紅"), ("她的嘴唇", "她的嘴唇"), ("范围很广", "範圍很廣"),
]
MIXED = [   # 簡體專用字比繁體專用字多：整句當簡體句；像、待這類簡繁都用的字不擋
    ("她是最后一个我在聚会上期待看到的女人", "她是最後一個我在聚會上期待看到的女人"), ("源于希腊语rhodon的合成词", "源於希臘語rhodon的合成詞"),
]
# 第三次重建（契約 §2.1）：喫→吃、VARIANTS 加 着→著 説→說、繁體句完整輸出再套 VARIANTS。每組 (句子, 是否簡體句, 預期)。
# 「關係著」「吃不出來」走詞組表（STPhrases 有 关系着→關係着、吃不出来→喫不出來）；「認為」走字表（认为 不在 STPhrases，为→爲 再由 VARIANTS 換成 為）。
THIRD = [
    ("我吃了饭", True, "我吃了飯"), ("我们都喫了饭", True, "我們都喫了飯"),
    ("這個國家的經濟發展关系着我們", False, "這個國家的經濟發展關係著我們"), ("這些東西我們吃不出来", False, "這些東西我們吃不出來"),
    ("這些國家都认为這樣不對", False, "這些國家都認為這樣不對"), ("隨着時間他説", False, "隨著時間他說"),
    ("臺北市隨着發展", False, "臺北市隨著發展"),
]


def check(fn, rows):
    return [(src, fn(src), want) for src, simp, want in rows if bc.is_simplified(src) != simp or fn(src) != want]


def mutant_d(text):   # 只在 VARIANTS 加字，繁體句不做完整輸出那一遍
    if bc.is_simplified(text):
        return c(text)
    t = bc._longest(text, conv[0], conv[2], conv[1])
    t = bc._longest(t, bc.TW_PHRASE, bc.TW_MAXP[0])
    return t if "喫" in text else t.replace("喫", "吃")


def mutant_e(text):   # 喫 的規則只用在簡體句
    if not bc.is_simplified(text):
        return _trad_no_kiss(text)
    return c(text)


def _trad_no_kiss(text):
    t = "".join(bc.VARIANTS.get(x, x) for x in bc._longest(text, conv[0], conv[2], conv[1]))
    return bc._longest(t, bc.TW_PHRASE, bc.TW_MAXP[0])


for src, want in SIMPLIFIED + TRADITIONAL + MIXED + KEEP:
    assert c(src) == want, (src, c(src), want)
assert bc.is_simplified("之后") and not bc.is_simplified("台灣") and not bc.is_simplified("皇后說")
assert not check(c, THIRD), check(c, THIRD)
bad_d = {r[0] for r in check(mutant_d, THIRD)}
bad_e = {r[0] for r in check(mutant_e, THIRD)}
assert {"這個國家的經濟發展关系着我們", "這些國家都认为這樣不對"} <= bad_d, bad_d   # 突變 (d)
assert "這些東西我們吃不出来" in bad_e, bad_e                                          # 突變 (e)
print(f"mutation (d) fails {sorted(bad_d)}; mutation (e) fails {sorted(bad_e)}")
print(f"ok: {len(SIMPLIFIED) + len(TRADITIONAL) + len(MIXED) + len(KEEP) + len(THIRD)} conversion checks")
