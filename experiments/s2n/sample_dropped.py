"""S2n 契約 §2.1 驗收追加：被重挑拿掉的候選字，在簡體句的轉換輸出裡各抽樣 20 處，印出前後文，看用的是不是台灣寫法。
用法：python3 experiments/s2n/sample_dropped.py [篇數，預設 8000]"""
import os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "s2"))
import build_counts as bc
conv = bc.load_conv()
D = {"夸": "誇", "涌": "湧", "朴": "樸", "伙": "夥", "郁": "鬱", "淀": "澱", "咨": "諮", "范": "範"}
hits = {c: [] for c in D}
for raw in bc.articles(int(sys.argv[1]) if len(sys.argv) > 1 else 8000):
    t = raw.replace("&lt;", "<")
    for _ in range(3): t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP: t = pat.sub(rep, t)
    for s in bc.SENT.findall(t):
        if not bc.is_simplified(s): continue
        out = bc.convert(s, *conv)
        for i, ch in enumerate(s):
            if ch in D and len(out) == len(s):
                hits[ch].append((s[max(0, i - 4):i + 5], out[max(0, i - 4):i + 5]))
random.seed(7)
for c, v in hits.items():
    pick = random.sample(v, min(20, len(v)))
    print(f"== {c}->{D[c]} (simplified-sentence occurrences {len(v)})")
    for a, b in pick: print(f"  {a} -> {b}")
