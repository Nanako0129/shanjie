"""S2f 契約 §3.2：字形探針 experiments/s2f/probe.txt（`前文|期望寫法|讀音`）。

每個目標詞有兩種寫法（台灣常用寫法在前、異體在後）。期望寫法＝基底詞庫 mcbpmf-data.txt 裡，同一個讀音下兩種寫法中分數較高的那個
（只看基底，不看疊加層）。前文是 executor 挑的兩字常見前文，寫在 CONTEXT。分數對印出來給 main 核對。
用法：python3 experiments/s2f/make_probe.py        （寫 probe.txt，印分數對）
驗收：lm_eval.py --lm <模型 F> --profile chat|formal --rows experiments/s2f/probe.txt --context --dump <檔>，每列第一名等於期望寫法。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BASE = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
PAIRS = [   # (前文, 寫法 A, 寫法 B)
    ("早上", "起床", "起牀"), ("躺在", "床上", "牀上"), ("醫學", "臨床", "臨牀"), ("住在", "病床", "病牀"),
    ("公司", "秘書", "祕書"), ("保守", "秘密", "祕密"), ("非常", "神秘", "神祕"), ("發現", "病灶", "病竈"),
    ("端午", "肉粽", "肉糉"), ("端午", "粽子", "糉子"), ("軍隊", "佔領", "占領"), ("政府", "宣布", "宣佈"),
    ("全國", "分布", "分佈"), ("房間", "佈置", "布置"), ("公司", "市占率", "市佔率"), ("官員", "貪污", "貪汙"),
]


def main():
    by = {}   # 詞 → {讀音: 分數（取最高）}
    for line in open(BASE, encoding="utf-8"):
        p = line.split()
        if len(p) == 3 and line[0] not in "#_" and len(p[1]) == len(p[0].split("-")):
            d = by.setdefault(p[1], {})
            d[p[0]] = max(d.get(p[0], -1e9), float(p[2]))
    rows = []
    for ctx, a, b in PAIRS:
        shared = [r for r in by.get(a, {}) if r in by.get(b, {})]
        if not shared:   # 其中一種寫法在基底沒有：只有另一種寫法的讀音可用，期望就是有的那個
            if (a in by) == (b in by):   # 兩種都在但讀音不同，或兩種都不在：沒有「同讀音的較高分」可比
                raise SystemExit(f"{a} / {b}: both {'present with different readings' if a in by else 'absent'} in base")
            only = a if a in by else b
            r = max(by[only], key=by[only].get)
            print(f"{a} / {b}: only {only} in base ({r}, {by[only][r]})")
            rows.append((ctx, only, r))
            continue
        r = max(shared, key=lambda r: max(by[a][r], by[b][r]))
        sa, sb = by[a][r], by[b][r]
        win = a if sa >= sb else b
        print(f"{ctx}|{a} {sa} vs {b} {sb} → {win}   {r}" + ("   (tie)" if sa == sb else ""))
        rows.append((ctx, win, r.replace("-", " ")))
    open(os.path.join(HERE, "probe.txt"), "w", encoding="utf-8", newline="\n").write("".join(f"{c}|{w}|{r.replace('-', ' ')}\n" for c, w, r in rows))


if __name__ == "__main__":
    sys.exit(main())
