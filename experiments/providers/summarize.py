"""把 N0（本機）、Jev（J1）、N1（雲端供應商）在開發集上的結果排成一張表。

只讀各實驗已寫好的 results，不呼叫任何 API。
雲端的列從 jsonl 逐列重算，只取共同的前 COMMON 列：開發集在批次之間從 302 長到 307，
新句子都附加在 user-reported 尾端，所以前 302 列在每個批次都一樣。
用法：python3 experiments/providers/summarize.py
"""
import ast
import glob
import json
import os
import re

COMMON = 302
EXP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(EXP, "providers", "results")
LINE = re.compile(r"^## (\S+)\s+(\S+)\s+(\{.*\})")
LENIENT = str.maketrans("她妳它牠嘗周臺裏", "他你他他嚐週台裡")   # 與 n1_providers.py 相同


def parse(path):
    out = {}
    for raw in open(path, encoding="utf-8"):
        m = LINE.match(raw)
        if m:
            out.setdefault(m.group(1), {})[m.group(2)] = ast.literal_eval(m.group(3))
    return out


def pct(v, q):
    v = sorted(v)
    return round(v[len(v) // 2] if q == .5 else v[min(len(v) - 1, int(len(v) * q))])


rows, partial = [], []   # (來源, 模型, effort, n, 整句, 寬鬆, p50, p95, 不合法, US$/千句)
for f in sorted(glob.glob(os.path.join(EXP, "n0", "results", "*-pin.txt"))):
    for name, d in parse(f).get("dev", {}).items():
        rows.append(("本機 188", name, "—", d["n"], d["sent_acc"], d["lenient_acc"], d.get("p50_ms"), d.get("p95_ms"), None, None))
j1 = os.path.join(EXP, "jev", "results", "j1.txt")
if os.path.exists(j1):
    p = parse(j1)
    d = p.get("ALL", {}).get("jev-1.13.0-choice8")
    warm = p.get("latency", {}).get("warm", {})
    if d:
        rows.append(("TypeSafe（候選 8）", "jev-1.13.0", "—", d["n"], d["sent_acc"], d["lenient_acc"], warm.get("p50_ms"), warm.get("p95_ms"), None, None))
oracle = None
for jl in sorted(glob.glob(os.path.join(RES, "*-k16-*.jsonl"))):
    tag = os.path.basename(jl)[:-6]
    prov, rest = tag.split("-", 1)
    model, effort = rest.rsplit("-k16-r", 1)
    allrows = [json.loads(l) for l in open(jl, encoding="utf-8")]
    rs = allrows[:COMMON]
    if not rs:   # 第一列就被拒（例如不支援 effort=none）
        partial.append((prov, model, effort, 0)); continue
    use = parse(jl[:-6] + ".txt").get("usage", {}).get(tag, {}) if os.path.exists(jl[:-6] + ".txt") else {}
    per_k = use.get("usd", 0) / len(allrows) * 1000 if allrows and "usd" in use else None
    picks = [r["cands"][r["index"] - 1] for r in rs]
    bad = sum(not (m := re.search(r'"index"\s*:\s*(\d+)', r["content"]) or re.fullmatch(r"\s*(\d+)\s*", r["content"]))
              or not 1 <= int(m.group(1)) <= len(r["cands"]) for r in rs)
    rec = (prov, model, effort, len(rs),
           sum(o == r["truth"] for o, r in zip(picks, rs)) / len(rs),
           sum(o.translate(LENIENT) == r["truth"].translate(LENIENT) for o, r in zip(picks, rs)) / len(rs),
           pct([r["ms"] for r in rs], .5), pct([r["ms"] for r in rs], .95), bad, per_k)
    if len(rs) == COMMON:
        rows.append(rec)
        oracle = sum(r["truth"] in r["cands"] for r in rs) / COMMON
    else:
        partial.append(rec)

rows.sort(key=lambda r: (-r[4], r[7] or 0))
f = lambda v, fmt="{}": "—" if v is None else fmt.format(v)
head = "| 來源 | 模型 | effort | n | 整句 | 寬鬆 | p50 ms | p95 ms | 不合法 | US$/千句 |\n|---|---|---|---|---|---|---|---|---|---|"
print(head)
for r in rows:
    print(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]:.1%} | {r[5]:.1%} | {f(r[6])} | {f(r[7])} | {f(r[8])} | {f(r[9], '{:.3f}')} |")
if oracle is not None:
    print(f"\n雲端 k=16 候選在前 {COMMON} 列的 oracle（正解在候選內的比例）：{oracle:.1%}")
if partial:
    print(f"\n未跑滿 {COMMON} 列（不列入比較）：")
    for r in partial:
        print(f"- {r[0]} {r[1]} {r[2]}：{r[3]} 列")
total = sum(json.load(open(s))["usd"] for s in glob.glob(os.path.join(RES, "spend*.json")))
print(f"\n全部帳本合計：US${total:.2f}")
