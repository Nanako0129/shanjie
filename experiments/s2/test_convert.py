"""S2n 契約 §2.1 的固定字串單元檢查，轉換規則照 docs/contracts/tw-conv.md。用法：python3 experiments/s2/test_convert.py（失敗會 AssertionError）。
tw-conv 之後預期值有變的列在註解裡寫出是哪一條規則：§2.2（判斷簡體句，T 含 TWVariants 的目標字但扣掉 M、平手算簡體）、§2.4（繁體句只套鍵含真簡體字的詞組、簡體句只套大陸用詞子集）、修訂五（N）。"""
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
    ("我們今天在後面等书", "我們今天在後面等書"),   # tw-conv §2.2：原本的 后 一列，句子本身有 們（T）和 书（R）平手＝簡體句，后 會被轉，所以改成 後 的輸入
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
    ("這個國家的經濟發展关系着我們", False, "這個國家的經濟發展關係著我們"), ("這些東西我們吃不出来", False, "這些東西我們吃不出來"),   # §2.4（修訂五）：繁體句套鍵含 S 字的詞組，关系着→關係着→關係著

    ("這些國家都认为這樣不對", False, "這些國家都認為這樣不對"), ("隨着時間他説", False, "隨著時間他說"),
    ("臺北市隨着發展", False, "臺北市隨著發展"),
]


# S2f（契約 §3.1）：台灣字形。每列 (句子, 是否簡體句, 預期)，先斷言 is_simplified 再斷言輸出。
S2F = [
    ("南庄鄉", False, "南庄鄉"), ("秘書說了", False, "秘書說了"), ("他說什么", False, "他說什么"),   # 說 是 T、么 不在 S 也不在 N，沒有詞組蓋到：不轉
    ("貪污的國家", False, "貪污的國家"),
    ("台湾的", True, "台灣的"), ("他在床上", True, "他在床上"), ("秘书", True, "秘書"), ("什么", True, "什麼"),   # §2.2（修訂五）：T 扣掉 M，床、么 不算繁體證據，單獨的 他在床上、什么 是只有標記字的簡體句；什么 靠詞組轉，床 不在 N
    ("我们吃饭", True, "我們吃飯"), ("我们喫饭", True, "我們吃飯"), ("病灶", True, "病灶"), ("肉粽", True, "肉粽"),   # 灶、粽 同上
]
# S2f 修訂一 6.3 的保護詞與 TWPhrases 順序：保護詞表和字表分層已拿掉（tw-conv §2.5），繁體句只換 S 的字再摺疊。
PROTECT_ROWS = [("排泄物很臭", False, "排泄物很臭"), ("嘴脣很紅", False, "嘴唇很紅")]
ORDER_SIMP = [("大众汽车很好", True, "福斯汽車很好"), ("台式机坏了", True, "台式機壞了")]   # §2.4：只套大陸用詞子集，台式機 不在清單
ORDER_TRAD = [("這個集羣很大", False, "這個集群很大"), ("他寫的是皮裏陽秋的筆法", False, "他寫的是皮裡陽秋的筆法")]   # §2.4：繁體句不套詞組表（集羣→叢集 沒了），摺疊 羣→群
PROTECT_SCOPE = [("他在痛苦里挣扎", True, "他在痛苦裡掙扎"), ("我们在灶门口", True, "我們在灶門口"), ("在困苦裏生活", False, "在困苦裡生活")]
PROTECT_TRAD_ROWS = [("竈門炭治郎", False, "竈門炭治郎"), ("他敲了敲竈門", False, "他敲了敲竈門")]   # 竈→灶 是 nofold（PTT 灶／竈 只有 4.3 倍）


def check(fn, rows):
    return [(src, fn(src), want) for src, simp, want in rows if bc.is_simplified(src) != simp or fn(src) != want]


for src, want in SIMPLIFIED + TRADITIONAL + MIXED + KEEP:
    assert c(src) == want, (src, c(src), want)
# tw-conv §2.4：簡體句只套大陸用詞子集，權限 不在清單；繁體句不套詞組表
assert c("管理员权限加好了") == "管理員權限加好了" and c("管理員權限加好了") == "管理員權限加好了"
assert c("我们的软件和插件") == "我們的軟體和插件" and c("這個插件很好用") == "這個插件很好用"
assert bc.is_simplified("之后") and not bc.is_simplified("台灣") and not bc.is_simplified("皇后說")
assert not check(c, S2F), check(c, S2F)
assert not check(c, THIRD), check(c, THIRD)
for rows in (PROTECT_ROWS, ORDER_SIMP, ORDER_TRAD, PROTECT_SCOPE, PROTECT_TRAD_ROWS):
    assert not check(c, rows), check(c, rows)
assert bc.fold_tw("爲裏羣") == "為裡群" and bc.fold_tw("泄") == "泄"   # 摺疊列：爲裏羣 摺疊；泄→洩 只有 14 倍，nofold


# 突變（每個都要讓對應的列失敗；改的是函式的副本，不動模組本身）。tw-conv 之後的完整突變在 mutate_tw_conv.py。
def no_fold(text):                   # 繁體句不轉也不摺疊
    return c(text) if bc.is_simplified(text) else text


MUTANTS = [  # (突變, 列, 必須失敗的來源句)
    (no_fold, ORDER_TRAD, {"他寫的是皮裏陽秋的筆法"}),
    (no_fold, PROTECT_ROWS, {"嘴脣很紅"}),
]
for fn, rows, must in MUTANTS:
    bad = {r[0] for r in check(fn, rows)}
    assert must <= bad, (fn.__name__, sorted(bad))
print(f"mutations: {len(MUTANTS)} killed")
print(f"ok: {len(SIMPLIFIED) + len(TRADITIONAL) + len(MIXED) + len(KEEP) + len(THIRD) + len(S2F) + len(PROTECT_ROWS + ORDER_SIMP + ORDER_TRAD + PROTECT_SCOPE + PROTECT_TRAD_ROWS)} conversion checks")
