"""S2r-2 §4.3：給 main 人工抽查的樣本（公開資料；種子固定）。比較 main 的疊加層（c6abd58）和工作樹的疊加層。

寫出兩個 TSV：changed（主要讀音改變的列＋新增的變調列，50 列）和 he（讀音有「和」且主要讀音改變的列，50 列）。
用法：python3 experiments/s2/s2r2_samples.py OUTDIR
"""
import os
import random
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SEED = 20261004
OLD_REV = "c6abd58"


def read(lines):
    prim, var = {}, {}
    for l in lines:
        r, w, _, _ = l.rstrip("\n").split("\t")
        (var if w in prim else prim)[w] = r
    return prim, var


def main(out):
    old, _ = read(subprocess.check_output(["git", "-C", ROOT, "show", f"{OLD_REV}:data/lexicon/overlay-add.tsv"], text=True).splitlines())
    new, var = read(open(os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"), encoding="utf-8"))
    changed = [(w, "primary", old[w], new[w]) for w in new if new[w] != old[w]]
    added = [(w, "variant", new[w], var[w]) for w in var]
    he = [c for c in changed if "和" in c[0]]
    rng = random.Random(SEED)
    for name, pop in (("changed", changed + added), ("he", he)):
        with open(os.path.join(out, f"s2r2-sample-{name}.tsv"), "w", encoding="utf-8") as f:
            f.write("word\tkind\tbefore(main primary | new primary)\tafter(new primary | new variant)\n")
            for row in rng.sample(pop, 50):
                f.write("\t".join(row) + "\n")
        print(name, len(pop))


if __name__ == "__main__":
    main(sys.argv[1])
