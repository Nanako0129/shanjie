"""forms-v1: 由教育部《重編國語辭典修訂本》產生「合法寫法」分數用的表 eval/forms.tsv。

契約見 docs/contracts/forms-score.md；這支腳本就是契約的參考實作。來源 CC BY-ND 3.0 TW，只用於評測（LICENSES/data.md）。
兩種規則，全寫在這裡，不手寫任何一對：
  詞對：詞條 T（>= 2 字、無 {）的 def 寫「也作／亦作」Y，Y 與 T 等長、不同、無 {、也是詞條、讀音集合有交集。
  單字：單字詞條 X 的第一個 heteronym 只有一條釋義，且是「「Y」的異體字。」或（def 空、link 剛好是「同「Y」。」）。
讀音一律先以（讀音）（語音）（又音）切開再比對；單字對帶讀音條件，只在該位置的讀音符合時才換。
用法：python3 tools/build_forms.py                 產生並寫入
      python3 tools/build_forms.py --check         重建並和 repo 裡的檔案逐位元組比對，並檢查說明行計數與不變量
      python3 tools/build_forms.py --explain 寫法   印出這個寫法由哪條規則、哪個詞條的哪一句原文產生
"""
import argparse
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import eval as E  # noqa: E402
from build_variants import MOEDICT, MOEDICT_COMMIT, MOEDICT_SHA256  # noqa: E402

OUT = os.path.join(ROOT, "eval", "forms.tsv")
VERSION = "forms-v1"
ALSO = re.compile(r"(?:也作|亦作)「([^」]+)」")
VARIANT_OF = re.compile(r"「(.)」的異體字。")
SAME_AS = re.compile(r"同「(.)」。")
SPLIT = re.compile(r"（(?:讀音|語音|又音)）")
CHARMAP = E.CHARMAP


def readings_of(h):
    """一個 heteronym 的讀音：以（讀音）（語音）（又音）切開，空白（含全形）收成一個半形空格，丟掉空段。"""
    segs = (" ".join(s.split()) for s in SPLIT.split(h.get("bopomofo") or ""))
    return {s for s in segs if s}


def to_row_form(reading):
    """辭典的輕聲 ˙ㄗ 寫在音節前，列檔寫在後（ㄗ˙）。"""
    return " ".join(s[1:] + "˙" if s.startswith("˙") else s for s in reading.split(" "))


def load_entries():
    raw = open(MOEDICT, "rb").read()
    if hashlib.sha256(raw).hexdigest() != MOEDICT_SHA256:
        sys.exit("moedict dump SHA-256 mismatch")
    return json.loads(raw)


def derive(entries):
    """回傳 (詞對來源, 單字來源)：詞對 {(T, Y): def}，單字 {X: (Y, rule, def, 條件)}，都還沒正規化。"""
    readings = {}
    for e in entries:
        s = readings.setdefault(e.get("title", ""), set())
        for h in e.get("heteronyms", []):
            s |= readings_of(h)
    words = {}
    for e in entries:
        t = e.get("title", "")
        if len(t) < 2 or "{" in t:
            continue
        for h in e.get("heteronyms", []):
            for d in h.get("definitions", []):
                for m in ALSO.finditer(d.get("def", "")):
                    for y in m.group(1).split("」、「"):
                        if len(y) == len(t) and y != t and "{" not in y and y in readings and readings[t] & readings[y]:
                            words.setdefault((t, y), d["def"])
    chars = {}
    for e in entries:
        x, hs = e.get("title", ""), e.get("heteronyms", [])
        if len(x) != 1 or not hs or len(hs[0].get("definitions", [])) != 1:
            continue
        d = hs[0]["definitions"][0]
        m, rule = VARIANT_OF.fullmatch(d.get("def", "")), "a"
        if not m and d.get("def") == "" and len(d.get("link", [])) == 1:
            m, rule = SAME_AS.fullmatch(d["link"][0]), "b"
        if not m or x in chars:
            continue
        y = m.group(1)
        rd = readings_of(hs[0]) or readings.get(y)
        if y in readings and rd:
            chars[x] = (y, rule, d.get("def") or d["link"][0], rd)
    return words, chars


def build(entries):
    words, chars = derive(entries)
    # 詞對：先套現行寬鬆對照，套完相同的丟掉，聯集分組，標準形取 min()。
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for t, y in words:
        a, b = E.lenient(t), E.lenient(y)
        if a != b:
            parent[find(a)] = find(b)
    groups = {}
    for w in parent:
        groups.setdefault(find(w), []).append(w)
    wcanon = {}
    for ws in groups.values():
        c = min(ws)
        wcanon.update({w: c for w in ws if w != c})
    # 單字：X 被現行單字對照換掉的不收；Y 先套現行單字對照；套完相同的丟掉；鏈解到底，條件用第一段的。
    step = {}
    for x, (y, rule, src, rd) in chars.items():
        y = y.translate(CHARMAP)
        if x.translate(CHARMAP) == x and y != x:
            step[x] = (y, sorted(to_row_form(r) for r in rd))
    ccanon = {}
    for x, (y, rd) in step.items():
        seen = [x]
        while y in step:
            if y in seen:
                sys.exit(f"char pair cycle at {x}")
            seen.append(y)
            y = step[y][0]
        ccanon[x] = (y, rd)
    wl = [f"word\t{w}\t{wcanon[w]}" for w in sorted(wcanon)]
    cl = [f"char\t{x}\t{ccanon[x][0]}\t{','.join(ccanon[x][1])}" for x in sorted(ccanon)]
    head = [
        f"# 合法寫法分數用的表（docs/contracts/forms-score.md，規則版本 {VERSION}）。由 tools/build_forms.py 產生，請勿手改。",
        f"# 來源：教育部《重編國語辭典修訂本》，經 g0v/moedict-data dict-revised_bkup.json（commit {MOEDICT_COMMIT}，SHA-256 {MOEDICT_SHA256}）。CC BY-ND 3.0 TW，只用於評測。",
        "# 格式：word<TAB>寫法<TAB>標準形；char<TAB>X<TAB>Y<TAB>讀音1,讀音2…（只在該位置的讀音符合時才換）。先套現行寬鬆對照，再套本表。",
        f"# 詞對 {len(wl)} 個、單字對 {len(cl)} 個",
    ]
    return "\n".join(head + wl + cl) + "\n", (words, chars, wcanon, ccanon)


def check_invariants(text):
    lines = text.splitlines()
    data = [l.split("\t") for l in lines if not l.startswith("#")]
    nw, nc = (sum(1 for r in data if r[0] == k) for k in ("word", "char"))
    m = re.fullmatch(r"# 詞對 (\d+) 個、單字對 (\d+) 個", lines[3])
    if not m or (int(m[1]), int(m[2])) != (nw, nc):
        return f"header counts differ from lines ({nw}, {nc})"
    xs = {r[1] for r in data if r[0] == "char"}
    for r in data:
        if r[0] == "char":
            if r[2] in xs:
                return f"char {r[1]}->{r[2]} is not resolved to the end"
            if any(s.startswith("˙") for c in r[3].split(",") for s in c.split(" ")):
                return f"leading ˙ in the reading condition of {r[1]}"
    return None


def explain(w, info):
    words, chars, wcanon, ccanon = info
    hit = False
    for (t, y), d in sorted(words.items()):
        if w in (t, y, E.lenient(t), E.lenient(y)):
            hit = True
            print(f"word rule: {t} <- also {y}: {d}")
    if w in wcanon:
        print(f"table line: word\t{w}\t{wcanon[w]}")
    if w in ccanon or any(w == x for x in chars):
        x = w
        y, rule, src, rd = chars[x]
        hit = True
        print(f"char rule ({rule}): {x} -> {y}: {src}; readings {sorted(rd)}")
        if x in ccanon:
            print(f"table line: char\t{x}\t{ccanon[x][0]}\t{','.join(ccanon[x][1])}")
    if not hit:
        print("no rule produced this form")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--explain", metavar="寫法")
    a = ap.parse_args()
    text, info = build(load_entries())
    if a.explain:
        explain(a.explain, info)
        return
    if a.check:
        bad = check_invariants(text)
        same = os.path.exists(OUT) and open(OUT, encoding="utf-8").read() == text
        print("forms.tsv " + ("matches" if same else "DIFFERS") + (f"; {bad}" if bad else ""))
        sys.exit(0 if same and not bad else 1)
    open(OUT, "w", encoding="utf-8").write(text)
    print(text.splitlines()[3])


if __name__ == "__main__":
    main()
