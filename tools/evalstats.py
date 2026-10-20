"""Paired statistics over two rowstats files (docs/contracts/eval-stats.md).

A rowstats file has one line per evaluated row: `row\tok\terrors\tgold_len` (row from 1; ok is 0 or 1;
errors = Levenshtein distance of the first candidate to the gold sentence when the row is wrong, else 0;
gold_len = Unicode scalar count of the gold sentence). Digits, tabs and newlines only: no text.

  python3 tools/evalstats.py compare BASE CAND [--label NAME] [--seed S] [--resamples N] [--net] [--cluster FILE]
  python3 tools/evalstats.py mde (--discordant D | --base BASE --cand CAND) --alpha A [--power 0.8]

prints a Markdown table row: n, top1 base/new, fixed/broken, exact McNemar p, CER base/new, and the paired
bootstrap 95% intervals of the top1 and CER differences (new - base). With --net three more columns: net (fixed - broken),
break rate (broken / n) and the one-sided exact 95% Poisson upper limit of the broken count. With --cluster FILE (one cluster id
per line, same order and count as the rows: rows of one message or article) the bootstrap resamples clusters instead of rows and the
label gains "(clustered, M clusters)"; McNemar is unchanged. Without it the output is exactly as before.

`mde` prints the minimum detectable effect: the smallest net (cand right - base right, among D discordant rows) whose exact two-sided
McNemar test (mcnemar_exact, reject when p <= A) rejects with probability >= power, the D rows splitting binomially with
p(cand right) = (D + net) / (2 D). Run it before a confirmation: a net below it cannot be told from zero at this n.
Standard library only.
"""
import argparse
import math
import random
import re
import sys

DIGITS = re.compile(r"[0-9]+\Z")  # ASCII digits only: str.isdigit() also takes '３' and '٣'


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p over the discordant counts (same value as tools/bench.py's mcnemar)."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def poisson_upper95(k):
    """One-sided exact (Garwood) 95% upper confidence limit of a Poisson mean given k observed events: the rate L with
    P(X <= k | L) = 0.05 (k = 0 gives -ln 0.05 = 2.9957). Bisection on the exact cdf, so no scipy."""
    def cdf(lam):
        ll = math.log(lam)  # log space: exp(-lam) alone underflows to 0 for lam > ~745
        return sum(math.exp(i * ll - lam - math.lgamma(i + 1)) for i in range(k + 1))
    lo, hi = 0.0, k + 10 + 10 * math.sqrt(k + 1)  # the limit is below k + 10 sqrt(k + 1) + a few for every k
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) > 0.05 else (lo, mid)
    return (lo + hi) / 2


def mde(d, alpha, power=0.8):
    """Smallest net = c - b (same parity as d, since b + c = d) whose exact two-sided McNemar test rejects (p <= alpha) with
    probability >= power when c ~ Binomial(d, (d + net) / (2 d)). None when even net = d does not reach the power (or d = 0)."""
    if d == 0:
        return None
    reject = [mcnemar_exact(d - c, c) <= alpha for c in range(d + 1)]
    for net in range(d % 2, d + 1, 2):
        p = (d + net) / (2 * d)
        if p >= 1:
            pw = float(reject[d])
        else:
            lp, lq = math.log(p), math.log1p(-p)
            pw = sum(math.exp(math.lgamma(d + 1) - math.lgamma(c + 1) - math.lgamma(d - c + 1) + c * lp + (d - c) * lq)
                     for c in range(d + 1) if reject[c])
        if pw >= power - 1e-12:
            return net
    return None


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
            if len(p) != 4 or not all(DIGITS.match(x) for x in p) or int(p[0]) != i or p[1] not in ("0", "1"):
                raise ValueError(f"{path}: line {i} is not a rowstats line aligned to row {i}")
            rows.append((int(p[1]), int(p[2]), int(p[3])))
    return rows


def paired_bootstrap(base, cand, seed=20261008, resamples=10000, clusters=None, level=0.95):
    """`level` (default 95%) intervals of (cand - base): (top1 count diff, CER diff), resampling rows with replacement. With `clusters` (one id per
    row) whole clusters are resampled instead, so rows of one message / article stay together (clusters are numbered in order of
    first appearance: with every row its own cluster the draws equal the row bootstrap's)."""
    rng = random.Random(seed)
    # Per-row differences once; each resample is then three sums over the drawn indexes.
    dok = [c[0] - b[0] for b, c in zip(base, cand)]
    derr = [c[1] - b[1] for b, c in zip(base, cand)]
    glen = [b[2] for b in base]
    if clusters is not None:
        ids = {}
        for k, cid in enumerate(clusters):
            ids.setdefault(cid, len(ids))
        dok_c, derr_c, glen_c = ([0] * len(ids) for _ in range(3))
        for k, cid in enumerate(clusters):
            j = ids[cid]
            dok_c[j] += dok[k]; derr_c[j] += derr[k]; glen_c[j] += glen[k]
        dok, derr, glen = dok_c, derr_c, glen_c
    n = len(dok)
    d_ok, d_err = [], []
    for _ in range(resamples):
        idx = rng.choices(range(n), k=n)
        d_ok.append(sum(dok[i] for i in idx))
        g = sum(glen[i] for i in idx)
        d_err.append((sum(derr[i] for i in idx) / g) if g else 0.0)
    q = 0.025 if level == 0.95 else round((1 - level) / 2, 10)   # 0.95 keeps the literal quantiles, so the default output is unchanged
    def ci(v):
        v.sort()
        return v[int(q * resamples)], v[int((1 - q) * resamples)]
    return ci(d_ok), ci(d_err)


def read_clusters(path, n):
    with open(path, encoding="utf-8") as f:
        ids = f.read().split("\n")
    if ids and ids[-1] == "":
        ids.pop()
    if len(ids) != n:
        raise ValueError(f"{path}: {len(ids)} cluster ids for {n} rows")
    return ids


def compare(base_path, cand_path, label="", seed=20261008, resamples=10000, net=False, cluster_path=None, level=0.95):
    base, cand = read_rowstats(base_path), read_rowstats(cand_path)
    if len(base) != len(cand):
        raise ValueError(f"row counts differ ({len(base)} vs {len(cand)})")
    if [r[2] for r in base] != [r[2] for r in cand]:
        raise ValueError("gold lengths differ: the files are not the same evaluation set")
    g = sum(r[2] for r in base)
    if g == 0:
        raise ValueError("no gold characters (empty files?): nothing to compare")
    fixed = sum(1 for b, c in zip(base, cand) if not b[0] and c[0])
    broken = sum(1 for b, c in zip(base, cand) if b[0] and not c[0])
    cer_b, cer_c = 100 * sum(r[1] for r in base) / g, 100 * sum(r[1] for r in cand) / g
    clusters = read_clusters(cluster_path, len(base)) if cluster_path else None
    (tlo, thi), (clo, chi) = paired_bootstrap(base, cand, seed, resamples, clusters, level)
    if clusters is not None:
        label += f" (clustered, {len(set(clusters))} clusters)"
    pct = f"{100 * level:g}"
    head = [f"| 集合 | n | top1 基準 | top1 新 | 改對 | 改壞 | p | CER 基準 | CER 新 | Δtop1 {pct}%（列） | ΔCER {pct}%（百分點） |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    if net:  # E2: 1:1 scoring columns appended at the end, so the default output is unchanged
        head[0] += " 淨值 | 改壞率 | 改壞數 95% 上界 |"
        head[1] += "---|---|---|"
    label = label.replace("|", "\\|")  # a '|' would shift every Markdown column
    row = (f"| {label} | {len(base)} | {sum(r[0] for r in base)} | {sum(r[0] for r in cand)} | {fixed} | {broken} "
           f"| {mcnemar_exact(fixed, broken):.4f} | {cer_b:.2f}% | {cer_c:.2f}% | [{tlo}, {thi}] "
           f"| [{100 * clo:.2f}, {100 * chi:.2f}] |")
    if net:
        row += f" {fixed - broken} | {100 * broken / len(base):.2f}% | {poisson_upper95(broken):.1f} |"
    return "\n".join(head + [row])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare")
    c.add_argument("base"); c.add_argument("cand"); c.add_argument("--label", default="")
    c.add_argument("--seed", type=int, default=20261008); c.add_argument("--resamples", type=int, default=10000)
    c.add_argument("--net", action="store_true", help="E2: add net (fixed - broken), break rate (broken / n) and the 95%% exact Poisson upper limit of broken")
    c.add_argument("--ci", type=float, default=0.95, help="level of the bootstrap intervals, within (0, 1) (default 0.95; P4b clause 2b uses 0.90)")
    c.add_argument("--cluster", help="file with one cluster id per line (rows of one message / article): the bootstrap resamples clusters")
    m = sub.add_parser("mde")
    m.add_argument("--discordant", type=int); m.add_argument("--base"); m.add_argument("--cand")
    m.add_argument("--alpha", type=float, required=True); m.add_argument("--power", type=float, default=0.8)
    a = ap.parse_args()
    try:
        if a.cmd == "mde":
            files = (a.base is not None) + (a.cand is not None)
            if (a.discordant is None) != (files == 2) or (a.discordant is not None and files):
                ap.error("give --discordant D, or --base and --cand")
            if a.discordant is not None and a.discordant < 0:
                ap.error("--discordant must be >= 0")
            if not 0 < a.alpha < 1 or not 0 < a.power < 1:
                ap.error("--alpha and --power must be within (0, 1)")
            if a.discordant is None:
                b, c = read_rowstats(a.base), read_rowstats(a.cand)
                if len(b) != len(c):
                    raise ValueError(f"row counts differ ({len(b)} vs {len(c)})")
                a.discordant = sum(x[0] != y[0] for x, y in zip(b, c))
            m_ = mde(a.discordant, a.alpha, a.power)
            print(f"D {a.discordant}, alpha {a.alpha:g}, power {a.power:g}: " +
                  (f"MDE net {m_:+d} rows" if m_ is not None else "no net reaches the power (D too small)"))
            return
        if not 0 < a.ci < 1:
            ap.error("--ci must be within (0, 1)")
        print(compare(a.base, a.cand, a.label, a.seed, a.resamples, a.net, a.cluster, a.ci))
    except (ValueError, OSError) as e:
        print(f"evalstats: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
