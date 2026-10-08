"""Paired statistics over two rowstats files (docs/contracts/eval-stats.md).

A rowstats file has one line per evaluated row: `row\tok\terrors\tgold_len` (row from 1; ok is 0 or 1;
errors = Levenshtein distance of the first candidate to the gold sentence when the row is wrong, else 0;
gold_len = Unicode scalar count of the gold sentence). Digits, tabs and newlines only: no text.

  python3 tools/evalstats.py compare BASE CAND [--label NAME] [--seed S] [--resamples N]

prints a Markdown table row: n, top1 base/new, fixed/broken, exact McNemar p, CER base/new, and the paired
bootstrap 95% intervals of the top1 and CER differences (new - base). Standard library only.
"""
import argparse
import math
import random
import sys


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p over the discordant counts (same value as tools/bench.py's mcnemar)."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def levenshtein(a, b):
    """Edit distance over Python str code points (= Unicode scalar values)."""
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def row_stats(top1, gold, ok):
    """(errors, gold_len). `ok` is the caller's lenient top-1 judgment; a lenient-equal row has 0 errors."""
    return (0 if ok else levenshtein(top1, gold), len(gold))


def format_rowstats(rows):
    """rows: iterable of (ok, top1, gold). The one writer of a rowstats file."""
    out = []
    for i, (ok, top1, gold) in enumerate(rows, 1):
        e, n = row_stats(top1, gold, ok)
        out.append(f"{i}\t{int(bool(ok))}\t{e}\t{n}\n")
    return "".join(out)


def read_rowstats(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            p = line.rstrip("\n").split("\t")
            if len(p) != 4 or not all(x.isdigit() for x in p) or int(p[0]) != i or p[1] not in ("0", "1"):
                raise ValueError(f"{path}: line {i} is not a rowstats line aligned to row {i}")
            rows.append((int(p[1]), int(p[2]), int(p[3])))
    return rows


def paired_bootstrap(base, cand, seed=20261008, resamples=10000):
    """95% intervals of (cand - base): (top1 count diff, CER diff), resampling rows with replacement."""
    n = len(base)
    rng = random.Random(seed)
    d_ok, d_err, d_len = [], [], []
    for _ in range(resamples):
        idx = rng.choices(range(n), k=n)
        d_ok.append(sum(cand[i][0] - base[i][0] for i in idx))
        g = sum(base[i][2] for i in idx)
        d_err.append((sum(cand[i][1] - base[i][1] for i in idx) / g) if g else 0.0)
    def ci(v):
        v.sort()
        return v[int(0.025 * resamples)], v[int(0.975 * resamples)]
    return ci(d_ok), ci(d_err)


def compare(base_path, cand_path, label="", seed=20261008, resamples=10000):
    base, cand = read_rowstats(base_path), read_rowstats(cand_path)
    if len(base) != len(cand):
        raise ValueError(f"row counts differ ({len(base)} vs {len(cand)})")
    if [r[2] for r in base] != [r[2] for r in cand]:
        raise ValueError("gold lengths differ: the files are not the same evaluation set")
    fixed = sum(1 for b, c in zip(base, cand) if not b[0] and c[0])
    broken = sum(1 for b, c in zip(base, cand) if b[0] and not c[0])
    g = sum(r[2] for r in base)
    cer_b, cer_c = 100 * sum(r[1] for r in base) / g, 100 * sum(r[1] for r in cand) / g
    (tlo, thi), (clo, chi) = paired_bootstrap(base, cand, seed, resamples)
    head = ["| 集合 | n | top1 基準 | top1 新 | 改對 | 改壞 | p | CER 基準 | CER 新 | Δtop1 95% | ΔCER 95% |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    row = (f"| {label} | {len(base)} | {sum(r[0] for r in base)} | {sum(r[0] for r in cand)} | {fixed} | {broken} "
           f"| {mcnemar_exact(fixed, broken):.4f} | {cer_b:.2f}% | {cer_c:.2f}% | [{tlo}, {thi}] "
           f"| [{100 * clo:.2f}, {100 * chi:.2f}] pp |")
    return "\n".join(head + [row])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare")
    c.add_argument("base"); c.add_argument("cand"); c.add_argument("--label", default="")
    c.add_argument("--seed", type=int, default=20261008); c.add_argument("--resamples", type=int, default=10000)
    a = ap.parse_args()
    try:
        print(compare(a.base, a.cand, a.label, a.seed, a.resamples))
    except (ValueError, OSError) as e:
        print(f"evalstats: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
