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
MIXED = [   # 簡體專用字比繁體專用字多：整句當簡體句；像、待這類簡繁都用的字不擋
    ("她是最后一个我在聚会上期待看到的女人", "她是最後一個我在聚會上期待看到的女人"), ("源于希腊语rhodon的合成词", "源於希臘語rhodon的合成詞"),
]
for src, want in SIMPLIFIED + TRADITIONAL + MIXED:
    assert c(src) == want, (src, c(src), want)
assert bc.is_simplified("之后") and not bc.is_simplified("台灣") and not bc.is_simplified("皇后說")
print(f"ok: {len(SIMPLIFIED) + len(TRADITIONAL) + len(MIXED)} conversion checks")
