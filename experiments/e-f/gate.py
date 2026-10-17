"""E-F learned gate (experiments/e-f/README.md). Reads M's chat score files, prints numbers only.

  python gate.py CODE_DIR SCORE_DIR

CODE_DIR: a checkout of main (for experiments/s7a-tinylm/fusegrid.py and tools/evalstats.py); SCORE_DIR: M.chat.<set>.jsonl."""
import os, sys
import numpy as np

CODE, SDIR = sys.argv[1], sys.argv[2]
sys.path[:0] = [os.path.join(CODE, "experiments", "s7a-tinylm"), os.path.join(CODE, "tools"), os.path.join(CODE, "reference", "proto")]
import fusegrid as fg, evalstats as ev  # noqa: E402

TUNE, DEV = "chatclause", ["chatjudge", "discordtune", "lytune"]
SHIP, CAP, FOLDS = (0.3, 2.0, 0.25), 0.005, 5
data = {s: fg.load_scores(os.path.join(SDIR, f"M.chat.{s}.jsonl")) for s in [TUNE] + DEV}


def feats(r):
    """[(i, features)] for candidates 1..n-1 of a row that has small-model scores."""
    n, ng, ll = r["n"], r["ng"], r.get("ll")
    if n < 2 or not ll:
        return []
    p = np.exp(np.array(ll) - max(ll)); p /= p.sum()
    llrank = np.argsort(np.argsort(-np.array(ll)))
    return [(i, [ng[i] - ng[0], ll[i] - ll[0], i, 1 / i, ng[0] - ng[1], p[i], p[0], llrank[i], llrank[0], n]) for i in range(1, n)]


def pairs(rows):
    X, y = [], []
    for r in rows:
        for i, f in feats(r):
            a, b = r["ok"][i], r["ok"][0]
            if a != b:
                X.append(f); y.append(1.0 if a else 0.0)
    return np.array(X), np.array(y)


def fit(X, y, l2=1.0, iters=50):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.hstack([(X - mu) / sd, np.ones((len(X), 1))])
    w = np.zeros(Z.shape[1])
    for _ in range(iters):   # Newton steps
        p = 1 / (1 + np.exp(-Z @ w))
        g = Z.T @ (p - y) + l2 * np.r_[w[:-1], 0]
        H = (Z * (p * (1 - p))[:, None]).T @ Z + l2 * np.diag(np.r_[np.ones(len(w) - 1), 0])
        w -= np.linalg.solve(H, g)
    return lambda F: np.hstack([(np.array(F) - mu) / sd, np.ones((len(F), 1))]) @ w


def best_scores(model, rows):
    """Per row: (best candidate index, its score), or (0, -inf) when the row cannot be reranked."""
    out = []
    for r in rows:
        f = feats(r)
        if not f:
            out.append((0, -np.inf)); continue
        s = model([x for _, x in f])
        j = int(np.argmax(s))
        out.append((f[j][0], float(s[j])))
    return out


def fb(rows, picks, base):
    fixed = sum(1 for r, j, k in zip(rows, picks, base) if r["ok"][j] and not r["ok"][k])
    broken = sum(1 for r, j, k in zip(rows, picks, base) if r["ok"][k] and not r["ok"][j])
    return fixed, broken


tune = data[TUNE]
n = len(tune)
# Out-of-fold scores on the tuning rows (contiguous blocks), then the threshold.
oof = [None] * n
for k in range(FOLDS):
    lo, hi = k * n // FOLDS, (k + 1) * n // FOLDS
    X, y = pairs(tune[:lo] + tune[hi:])
    m = fit(X, y)
    oof[lo:hi] = best_scores(m, tune[lo:hi])
zero = [0] * n
cands = sorted({s for _, s in oof if np.isfinite(s)})
best = None
for t in cands:
    f, b = fb(tune, [i if s >= t else 0 for i, s in oof], zero)
    if ev.poisson_upper95(b) / n <= CAP and (best is None or f - b > best[1] - best[2]):
        best = (t, f, b)
t, f, b = best
print(f"tune {TUNE} n {n}: learned gate (out-of-fold) +{f}/-{b} net {f - b:+d} ub {ev.poisson_upper95(b) / n:.3%} at t {t:.3f}")
ship_t = [fg.pick(r, *SHIP[:2], "ll", SHIP[2]) for r in tune]
f2, b2 = fb(tune, ship_t, zero)
print(f"tune M shipped {SHIP}: +{f2}/-{b2} net {f2 - b2:+d} ub {ev.poisson_upper95(b2) / n:.3%}")
same = fg.select_weighted({TUNE: tune}, 1, names=(TUNE,), cap_ub=CAP)
print(f"tune M same rule cell {same[:3]}: +{same[3]}/-{same[4]} net {same[3] - same[4]:+d}")
model = fit(*pairs(tune))
for s in DEV:
    rows = data[s]
    zero = [0] * len(rows)
    g = [i if sc >= t else 0 for i, sc in best_scores(model, rows)]
    sh = [fg.pick(r, *SHIP[:2], "ll", SHIP[2]) for r in rows]
    sr = [fg.pick(r, same[0], same[1], "ll", same[2]) for r in rows]
    fg_, bg = fb(rows, g, zero); fs, bs = fb(rows, sh, zero); fr, br = fb(rows, sr, zero)
    a1, b1 = fb(rows, g, sh); a2, b2_ = fb(rows, g, sr)
    er = lambda f_, b_: f"{b_ / f_:.2f}" if f_ else "-"
    print(f"dev {s} n {len(rows)}: gate vs ng +{fg_}/-{bg} net {fg_ - bg:+d} ub {ev.poisson_upper95(bg) / len(rows):.2%} er {er(fg_, bg)}"
          f" | shipped +{fs}/-{bs} net {fs - bs:+d} er {er(fs, bs)} | same-rule +{fr}/-{br} net {fr - br:+d}"
          f" | gate vs shipped +{a1}/-{b1} p {ev.mcnemar_exact(a1, b1):.2g} | gate vs same-rule +{a2}/-{b2_} p {ev.mcnemar_exact(a2, b2_):.2g}")
