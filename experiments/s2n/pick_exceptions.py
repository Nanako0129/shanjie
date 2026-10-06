"""S2n 契約 §2.1「台灣用字例外」的挑法：只看維基抽樣裡被判為繁體句（is_simplified 為否）的句子，
兼用字（STCharacters 的對照含自己、第一個對照不是自己）的原字與第一個對照各出現幾次；原字 >= 3 倍才列入。
契約表已明列結果的 后 于 里 台 干 余 不參與（它們要轉）。原字至少出現 MIN_SELF 次才算（兩邊都接近 0 的不算）。
用法：python3 experiments/s2n/pick_exceptions.py [篇數，預設 25000] → 印每個兼用字的比例與入選結果
"""
import collections
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "s2"))
import build_counts as bc  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 25000
MIN_SELF = 30
CONTRACT_TABLE = set("后于里台干余")

bc.load_conv()
dual = {}
for line in open(os.path.join(bc.SRC, "opencc", "STCharacters.txt"), encoding="utf-8"):
    p = line.rstrip("\n").split("\t")
    if len(p) == 2 and not line.startswith("#"):
        v = p[1].split(" ")
        if p[0] in v and v[0] != p[0]:
            dual[p[0]] = v[0]
watch = set(dual) | set(dual.values())
cnt = collections.Counter()
sents = trad = 0
for raw in bc.articles(N):
    t = raw.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    for _ in range(3):
        t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP:
        t = pat.sub(rep, t)
    for s in bc.SENT.findall(t):
        sents += 1
        if bc.is_simplified(s):
            continue
        trad += 1
        for c in s:
            if c in watch:
                cnt[c] += 1
print(f"articles={N} sentences={sents} traditional-classified={trad}")
picked = []
print("char first self first_form ratio picked")
for c, f in sorted(dual.items(), key=lambda kv: -cnt[kv[0]]):
    if cnt[c] == 0 and cnt[f] == 0:
        continue
    ok = c not in CONTRACT_TABLE and cnt[c] >= MIN_SELF and cnt[c] >= 3 * cnt[f]
    if ok:
        picked.append(c)
    r = cnt[c] / cnt[f] if cnt[f] else float("inf")
    print(f"{c} {f} {cnt[c]} {cnt[f]} {r:.1f} {'PICK' if ok else ''}")
print("PICKED:", "".join(picked))
