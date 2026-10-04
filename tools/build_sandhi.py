"""S2r：依教育部規範補齊「一」「不」的本調／變調讀音與「法」的 ㄈㄚˇ（docs/contracts/s2r-sandhi-variants.md §1）。

輸入 data/lexicon/mcbpmf-data.txt（小麥基底，MIT）；讀 data/lexicon/overlay-add.tsv 只為了去重。
輸出 data/lexicon/sandhi-add.tsv，每列「讀音\\t詞\\t分數\\tsandhi」，依（讀音, 詞）的 UTF-8 位元組序排序。

規則依據：《國語辭典簡編本》〈單一音讀〉（https://dict.concised.moe.edu.tw/page.jsp?ID=55&la=0&powerMode=0 ）、
88 年《國語一字多音審訂表》。規則本身是事實，這個檔案不含任何教育部資料。

用法：python3 tools/build_sandhi.py [--check]
"""
import argparse
import collections
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
OVERLAY = os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv")
OUT = os.path.join(ROOT, "data", "lexicon", "sandhi-add.tsv")

# 「一」只讀本調的上下文（§1 排除）：前一字是「第」或數字字、後一字是數字字或「月」、詞尾、疊字動詞中間、
# 詞中的詞尾（final_words）、序詞與專名（LISTED）。
NUMERALS = set("零〇一二三四五六七八九十百千萬億兆兩廿卅佰仟")
YI, BU = "一", "不"
YI_BASE, YI_RISE, YI_FALL = "ㄧ", "ㄧˊ", "ㄧˋ"
BU_BASE, BU_RISE = "ㄅㄨˋ", "ㄅㄨˊ"

# 序詞（簡編本：一律讀本調）與專名（教育部沒有規則，保守不變調）裡的「一」，結構規則判斷不了。
# 2026-10-04 main 逐列看過 R1 的全部 358 列後列出；比對子字串，「一」落在其中就不變調。
LISTED = (
    # 序詞：校名、宿舍、年級、編號、路線、審級、世代
    "一女中", "男一舍", "女一舍", "女一分舍", "高一上", "高一下", "一丙", "一級主管", "一級棒", "一哥", "一信", "一巷",
    "保一總隊", "工一連", "偵一組長", "核一廠", "更一審", "台一線", "研究院一路", "文武一街", "一丁目", "查理一世",
    "清冠一號", "中華衛星一號", "中新一號", "深太空一號", "一號木桿", "一點五", "一之七",
    # 詞中的詞尾，基底另有變調用法所以 final_words 判斷不了：同一（同一個）、正一、弘一
    "同一性", "正一教", "弘一大師",
    # 專名
    "豐田章一郎", "鈴木一朗", "三宅一生", "一青窈", "一色紗英", "一蘭拉麵", "一休和尚", "洪一中", "羅一鈞",
)
_final = None


def final_words():
    """詞中的詞尾（統一發票、唯一正解、國一生）：基底裡以「一」結尾的詞，每個讀音都讀 ㄧ，而且基底沒有任何一列
    在它後面接字時把這個「一」變調（每一、另一、不一 都有變調的例子，所以不算）。"""
    global _final
    if _final is None:
        last, sandhied = collections.defaultdict(set), set()
        for word, syls, _, _ in base_rows():
            last[word].add(syls[-1])
            for i in range(1, len(word) - 1):
                if word[i] == YI and syls[i] in (YI_RISE, YI_FALL):
                    sandhied.update(word[j:i + 1] for j in range(i))
        _final = {w for w, r in last.items() if len(w) > 1 and w[-1] == YI and r == {YI_BASE} and w not in sandhied}
    return _final


def tone(syl):
    """1–4 聲或 5（輕聲），看讀音最後的聲調符號。"""
    return {"ˊ": 2, "ˇ": 3, "ˋ": 4, "˙": 5}.get(syl[-1], 1)


def next_tone(word, syls, i):
    """下一個字的聲調（給「一」「不」判斷變調），依本調：「不」是去聲、「一」是陰平（不管它在這一列讀不讀變調）；
    「個」讀輕聲時視為去聲（審訂表「個」不取輕聲）。詞尾回 None。"""
    if i + 1 >= len(word):
        return None
    nxt = word[i + 1]
    if nxt == BU:
        return 4
    if nxt == YI:
        return 1
    t = tone(syls[i + 1])
    if t == 5 and nxt == "個":
        return 4
    return t


def yi_keeps_base(word, i):
    """「一」在這個位置是否只讀本調（數詞序詞、詞尾、疊字動詞中間、詞中的詞尾、序詞與專名）。"""
    if i + 1 >= len(word):
        return True
    prev = word[i - 1] if i > 0 else None
    nxt = word[i + 1]
    if prev is not None and (prev == "第" or prev in NUMERALS):
        return True
    if nxt in NUMERALS or nxt == "月":
        return True
    if prev is not None and prev == nxt:
        return True
    if any(word[j:i + 1] in final_words() for j in range(i)):
        return True
    for s in LISTED:
        k = word.find(s, max(0, i - len(s) + 1))
        if k != -1 and k <= i:
            return True
    return False


def other_reading(word, syls, i):
    """位置 i 的「一」「不」的另一種合規讀音（變調→本調、可變調的本調→變調）；沒有就回 None。生成器與探針共用。"""
    ch, s = word[i], syls[i]
    if ch == YI:
        if s in (YI_RISE, YI_FALL):
            return YI_BASE
        if s == YI_BASE and not yi_keeps_base(word, i):
            t = next_tone(word, syls, i)
            return YI_RISE if t == 4 else YI_FALL if t in (1, 2, 3) else None
    elif ch == BU:
        if s == BU_RISE:
            return BU_BASE
        if s == BU_BASE and next_tone(word, syls, i) == 4:
            return BU_RISE
    return None


def _apply(word, syls, want):
    """把 want(字, 讀音) 為真的位置換成 other_reading；有改到才回新讀音。"""
    out = list(syls)
    for i, ch in enumerate(word):
        if want(ch, syls[i]):
            out[i] = other_reading(word, syls, i) or out[i]
    return out if out != list(syls) else None


def r1_yi_sandhi(word, syls):
    """R1：「一」讀本調、不在排除範圍 → 去聲前 ㄧˊ、陰平／陽平／上聲前 ㄧˋ（其他輕聲前不改）。"""
    return _apply(word, syls, lambda ch, s: ch == YI and s == YI_BASE)


def r2_base_tone(word, syls):
    """R2：這一列所有讀變調的「一」改 ㄧ、所有讀 ㄅㄨˊ 的「不」改 ㄅㄨˋ（兩者都在時產生全部本調的那一列）。"""
    return _apply(word, syls, lambda ch, s: (ch == YI and s in (YI_RISE, YI_FALL)) or (ch == BU and s == BU_RISE))


def r3_bu_sandhi(word, syls):
    """R3：「不」讀 ㄅㄨˋ 且下一音節是去聲 → ㄅㄨˊ。"""
    return _apply(word, syls, lambda ch, s: ch == BU and s == BU_BASE)


def r5_fa(word, syls):
    """R5：「法」讀 ㄈㄚˋ（審訂表沒有這個音）→ ㄈㄚˇ。"""
    out, changed = list(syls), False
    for i, ch in enumerate(word):
        if ch == "法" and syls[i] == "ㄈㄚˋ":
            out[i], changed = "ㄈㄚˇ", True
    return out if changed else None


RULES = [("R1", r1_yi_sandhi), ("R2", r2_base_tone), ("R3", r3_bu_sandhi), ("R5", r5_fa)]


def base_rows(path=BASE):
    """基底的有效列，規則同 Lexicon::parse_with：略過 # 與 _ 開頭、三欄、字數等於音節數。"""
    for line in open(path, encoding="utf-8"):
        if line[:1] in ("#", "_"):
            continue
        parts = line.split()
        if len(parts) != 3:
            continue
        reading, word, score = parts
        syls = reading.split("-")
        if len(word) == len(syls):
            yield word, syls, float(score), parts[2]


def build():
    existing = set()
    rows = list(base_rows())
    for word, syls, _, _ in rows:
        existing.add(("-".join(syls), word))
    for line in open(OVERLAY, encoding="utf-8"):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 2:
            existing.add((p[0], p[1]))
    best = {}          # (reading, word) -> (score float, score text)
    per_rule = collections.Counter()
    for word, syls, score, text in rows:
        if len(word) < 2:
            continue   # 單字列不動
        for name, rule in RULES:
            new = rule(word, syls)
            if new is None:
                continue
            key = ("-".join(new), word)
            if key in existing:
                continue
            if key not in best or score > best[key][0]:
                if key not in best:
                    per_rule[name] += 1
                best[key] = (score, text)
    lines = [f"{r}\t{w}\t{t}\tsandhi\n" for (r, w), (_, t) in sorted(best.items(), key=lambda kv: (kv[0][0].encode(), kv[0][1].encode()))]
    return "".join(lines), per_rule


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="重產並和現有檔逐位元組比對，不寫檔")
    a = ap.parse_args()
    text, per_rule = build()
    print("rows per rule (first rule to produce each row):", dict(sorted(per_rule.items())), "total", text.count("\n"), file=sys.stderr)
    if a.check:
        cur = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else None
        if cur != text:
            print("error: data/lexicon/sandhi-add.tsv differs from a fresh build", file=sys.stderr)
            sys.exit(1)
        print("sandhi-add.tsv: up to date", file=sys.stderr)
        return
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    main()
