"""彙整 188 上的 out-*.txt／daily-*.txt：陷阱集＋日常集合併成一組（109 句），附 Wilson 95% 信賴區間。

用法：ssh 188 'findstr /B "## " out-*.txt daily-*.txt' | python3 summarize.py
"""
import ast
import math
import re
import sys
from collections import defaultdict

LINE = re.compile(r"## (\S+)\s+(\S+)\s+(\{.*\})")


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


rows = defaultdict(dict)   # model -> set -> metrics
for raw in sys.stdin:
    m = LINE.search(raw.replace("\\", "/"))
    if not m:
        continue
    name, model, d = m.group(1), m.group(2), ast.literal_eval(m.group(3))
    model = model.split("/")[-1].replace(":", "@")   # 早期一輪用 :4bit
    rows[model][name] = d

print(f"{'模型':32s} {'口語109 整句':>14s} {'95% CI':>13s} {'寬鬆':>7s} {'萌典300 整句':>12s} {'寬鬆':>6s} {'p50 ms':>7s}")
out = []
for model, sets in rows.items():
    a, b = sets.get("同音陷阱集"), sets.get("日常驗證集")
    if not (a and b):
        continue
    n = a["n"] + b["n"]
    k = round(a["sent_acc"] * a["n"]) + round(b["sent_acc"] * b["n"])
    kl = round(a["lenient_acc"] * a["n"]) + round(b["lenient_acc"] * b["n"])
    lo, hi = wilson(k, n)
    md = sets.get("萌典例句", {})
    out.append((k / n, model, n, k, kl, lo, hi, md, a.get("p50_ms", 0)))
for acc, model, n, k, kl, lo, hi, md, p50 in sorted(out, reverse=True):
    print(f"{model:32s} {k:>4d}/{n} {acc:6.1%} [{lo:.0%}, {hi:.0%}] {kl / n:7.1%} "
          f"{md.get('sent_acc', float('nan')):12.1%} {md.get('lenient_acc', float('nan')):6.1%} {p50:7}")
