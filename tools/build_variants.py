"""S2v：由教育部《重編國語辭典修訂本》產生寬鬆對照用的異體詞表 eval/variants.tsv。

契約見 docs/PLAN.md §S2v；這支腳本就是契約的參考實作。來源 CC BY-ND 3.0 TW，只用於評測（LICENSES/data.md）。
標準：詞條標題 T（≥ 2 字、無 { 缺字碼）的釋義 def 含「也作「Y」」或「亦作「Y」」，Y 與 T 等長、不同、無 {，
而且兩者是詞條、至少有一個注音讀音相同。正規化全部在這裡做完：先套單字對照，丟掉套完相同的詞對，聯集分組，
標準形取 min()，每個異體寫一行「異體\\t標準形」。載入端不做任何轉換。
用法：python3 tools/build_variants.py          產生並寫入
      python3 tools/build_variants.py --check  重新產生並和 repo 裡的檔案比對，不同就 exit 1
      python3 tools/build_variants.py --probe  印出 Rust／Python 對照用的探測字串（一律讀 repo 的表）
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
from eval import CHARMAP  # noqa: E402

OUT = os.path.join(ROOT, "eval", "variants.tsv")
MOEDICT = os.path.expanduser("~/side-project/ime-research/repos/moedict-data/dict-revised_bkup.json")
MOEDICT_COMMIT = "a6dc997417507eb510fc29822bc514de2c92728c"
MOEDICT_SHA256 = "ea767109dfb04f838900b7462ebf6977f344f592e1dd745a204cbe3327ef8fe3"
ALSO = re.compile(r"(?:也作|亦作)「([^」]+)」")
GOLDEN = ["unigram.txt", "s1-dev302.txt", "s1-dev302-nooverlay.txt", "s1-overlay-sets.txt"]


def pairs_from_dict():
    raw = open(MOEDICT, "rb").read()
    if hashlib.sha256(raw).hexdigest() != MOEDICT_SHA256:
        sys.exit("moedict dump SHA-256 mismatch")
    entries = json.loads(raw)
    readings = {}
    for e in entries:
        readings.setdefault(e.get("title", ""), set()).update(
            h["bopomofo"] for h in e.get("heteronyms", []) if h.get("bopomofo"))
    pairs = set()
    for e in entries:
        t = e.get("title", "")
        if len(t) < 2 or "{" in t:
            continue
        for h in e.get("heteronyms", []):
            for d in h.get("definitions", []):
                for m in ALSO.finditer(d.get("def", "")):
                    for y in m.group(1).split("」、「"):
                        if len(y) == len(t) and y != t and "{" not in y and readings.get(t, set()) & readings.get(y, set()):
                            pairs.add(tuple(sorted((t, y))))
    return pairs


def build():
    pairs = pairs_from_dict()
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    kept = 0
    for a, b in pairs:
        a, b = a.translate(CHARMAP), b.translate(CHARMAP)
        if a != b:
            kept += 1
            parent[find(a)] = find(b)
    groups = {}
    for w in parent:
        groups.setdefault(find(w), []).append(w)
    canon = {}
    for ws in groups.values():
        c = min(ws)
        for w in ws:
            if w != c:
                if canon.get(w, c) != c:
                    sys.exit("variant maps to two canonical forms")
                canon[w] = c
    head = [
        "# 寬鬆對照用的異體詞表（docs/PLAN.md §S2v）。由 tools/build_variants.py 產生，請勿手改。",
        f"# 來源：教育部《重編國語辭典修訂本》，經 g0v/moedict-data dict-revised_bkup.json（commit {MOEDICT_COMMIT}）。CC BY-ND 3.0 TW，只用於評測。",
        "# 標準：釋義寫「也作／亦作」、字數相同、至少一個注音讀音相同；已套單字對照（她妳它牠嘗周臺裏 → 他你他他嚐週台裡）。",
        f"# 詞對 {len(pairs)} 組（套單字對照後不同的 {kept} 組），分成 {len(groups)} 組，異體 {len(canon)} 個。格式：異體<TAB>標準形。",
    ]
    return "\n".join(head + [f"{v}\t{canon[v]}" for v in sorted(canon)]) + "\n"


def probe():
    rows = [l.rstrip("\n").split("\t") for l in open(OUT, encoding="utf-8") if not l.startswith("#")]
    vs = [r[0] for r in rows]
    out = list(vs)
    out += [a + b for a, b in zip(vs, vs[1:])]
    out += [f"臺{v}臺" for v in vs]
    for g in GOLDEN:
        for l in open(os.path.join(ROOT, "eval", "golden", g), encoding="utf-8"):
            m = re.match(r"^   ✗ (.*) → (.*)$", l.rstrip("\n"))
            if m:
                out += [m.group(1), m.group(2)]
    for f in sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt"))):
        for l in open(f, encoding="utf-8"):
            p = l.rstrip("\n").split("|")
            if len(p) >= 2 and not l.startswith("#"):
                out.append(p[1])
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    if a.probe:
        sys.stdout.write(probe())
        return
    text = build()
    if a.check:
        same = os.path.exists(OUT) and open(OUT, encoding="utf-8").read() == text
        print("variants.tsv " + ("matches" if same else "DIFFERS"))
        sys.exit(0 if same else 1)
    open(OUT, "w", encoding="utf-8").write(text)
    print(text.splitlines()[3])


if __name__ == "__main__":
    main()
