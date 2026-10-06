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
    ("我吃了饭", True, "我吃了飯"), ("我们都喫了饭", True, "我們都吃了飯"),   # S2f §2.3：TWVariants 的 喫→吃，S2n 的「輸入有喫就保留」拿掉
    
    ("這個國家的經濟發展关系着我們", False, "這個國家的經濟發展關係著我們"), ("這些東西我們吃不出来", False, "這些東西我們吃不出來"),
    ("這些國家都认为這樣不對", False, "這些國家都認為這樣不對"), ("隨着時間他説", False, "隨著時間他說"),
    ("臺北市隨着發展", False, "臺北市隨著發展"),
]


# S2f（契約 §3.1）：台灣字形。每列 (句子, 是否簡體句, 預期)，先斷言 is_simplified 再斷言輸出。
# 簡體句的 床、灶、粽 在 SIMP_CHAR 先被換成 牀、竈、糉，由 §2.3 的整句字表換回；繁體句的 庄、秘 靠 §2.2 的縮小集合不被轉。
S2F = [
    ("南庄鄉", False, "南庄鄉"), ("秘書說了", False, "秘書說了"), ("他說什么", False, "他說什么"),   # 什么 不轉：§2.2 (b) 的已知代價
    ("貪污的國家", False, "貪污的國家"),                                                       # 污→汙 在排除名單
    ("台湾的", True, "台灣的"), ("他在床上", True, "他在床上"), ("秘书", True, "秘書"), ("什么", True, "什麼"),
    ("我们吃饭", True, "我們吃飯"), ("我们喫饭", True, "我們吃飯"), ("病灶", True, "病灶"), ("肉粽", True, "肉粽"),
]


def check(fn, rows):
    return [(src, fn(src), want) for src, simp, want in rows if bc.is_simplified(src) != simp or fn(src) != want]


for src, want in SIMPLIFIED + TRADITIONAL + MIXED + KEEP:
    assert c(src) == want, (src, c(src), want)
assert bc.is_simplified("之后") and not bc.is_simplified("台灣") and not bc.is_simplified("皇后說")
assert not check(c, S2F), check(c, S2F)
assert not check(c, THIRD), check(c, THIRD)
assert all(bc.TW_VARIANTS[k] == v for k, v in bc.VARIANTS.items() if k in bc.TW_VARIANTS)   # 兩表重疊的 9 條方向相同
assert not set(bc.TW_VARIANTS) & set("污癡樑蔘")                                              # §2.1 的四條排除
# S2f 突變（2026-10-07 手動：改 build_counts.py、跑本檔、還原；記 exit code 與失敗的斷言，不是建置錯誤）：
#  (i) 拿掉 §2.3 這一層（convert 的 table = {}）→ exit 1，`assert not check(c, S2F)` 失敗：
#      [他在床上→他在牀上, 我们喫饭→我們喫飯, 病灶→病竈, 肉粽→肉糉]（秘书 不變：秘 由 TAIWAN_KEEP 保留）。
#  (ii) 拿掉 §2.2 (a)(b) 的扣除（trad_simp_only = set(simp_only)）→ exit 1，同一條斷言失敗：
#      [南庄鄉→南莊鄉, 秘書說了→祕書說了, 他說什么→他說什麼]（目標的兩條 南庄鄉、秘書說了 都在內）。
print(f"ok: {len(SIMPLIFIED) + len(TRADITIONAL) + len(MIXED) + len(KEEP) + len(THIRD) + len(S2F)} conversion checks")
