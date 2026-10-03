"""兩次 iter2 run 的配對比較：修好幾句、弄壞幾句、McNemar 精確檢定、正確率差與對數損失差的 bootstrap 95% 區間。

用法：python3 experiments/s2/compare.py <基準 run> <候選 run>
私有集合只印數字。
"""
import math
import os
import random
import sys

WORK = os.path.expanduser("~/.cache/shanjie/work/s2/runs")
PRIVATE = os.path.expanduser("~/side-project/shanjie-private/s2-runs")


def load(run):
    out = {}
    for base in (WORK, PRIVATE):
        d = os.path.join(base, run)
        if os.path.isdir(d):
            for f in os.listdir(d):
                rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(d, f), encoding="utf-8")]
                out[f[:-4]] = [(int(r[1]), float(r[4])) for r in rows]
    return out


def mcnemar(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def boot(diffs, reps=2000, seed=0):
    rnd = random.Random(seed); n = len(diffs)
    ms = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    return ms[int(0.025 * reps)], ms[int(0.975 * reps)]


def main():
    a, b = load(sys.argv[1]), load(sys.argv[2])
    for name in a:
        if name not in b:
            continue
        x, y = a[name], b[name]
        fixed = sum(1 for (p, _), (q, _) in zip(x, y) if not p and q)
        broke = sum(1 for (p, _), (q, _) in zip(x, y) if p and not q)
        d_acc = [q - p for (p, _), (q, _) in zip(x, y)]
        d_ll = [lq - lp for (_, lp), (_, lq) in zip(x, y)]
        lo, hi = boot(d_acc); llo, lhi = boot(d_ll)
        n = len(x)
        print(f"{name:9s} n={n:5d}  修好 {fixed:4d}  弄壞 {broke:4d}  淨 {fixed - broke:+4d} ({(fixed - broke) / n:+.2%}, 95% [{lo:+.2%}, {hi:+.2%}])"
              f"  McNemar p={mcnemar(fixed, broke):.3g}  對數損失差 {sum(d_ll) / n:+.4f} [{llo:+.4f}, {lhi:+.4f}]")


if __name__ == "__main__":
    main()
