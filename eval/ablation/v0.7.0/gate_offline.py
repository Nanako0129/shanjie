#!/usr/bin/env python3
"""Gate row of the v0.7.0 ablation table, recomputed offline from a `shanjie-eval --tinylm-scores` file.

  python3 eval/ablation/v0.7.0/gate_offline.py chat   SCORES ROWSTATS [--off gate|soft] [--out DIR]
  python3 eval/ablation/v0.7.0/gate_offline.py formal SCORES ROWSTATS [--out DIR]
  python3 eval/ablation/v0.7.0/gate_offline.py --self-test

How to produce SCORES and ROWSTATS is in eval/ablation/v0.7.0/README.md.

SCORES is the per-candidate record of the full-system run (v0.7.0, standard strength), ROWSTATS the rowstats
file of the same run. Step 1 recomputes the shipped pick of every row from the scores and stops (exit 1) unless
it equals the record's `c` and the rowstats line it implies equals ROWSTATS byte for byte. Step 2 repeats the
pick with the gate off (tau = inf, delta = 0; alpha, the colloquial fusion and the mask unchanged) and prints
the correct rows and fixed / broken against the full system. `--off soft` (chat only) instead fixes the soft gate
of the colloquial term, sigmoid((ll_i - ll_0) / T), at 1 and leaves tau and delta as shipped. With --out the two
rowstats files are written there (digits, tabs and newlines only) for `tools/evalstats.py compare --net`.

The rules are those of the core at tag v0.7.0 (56979b2e): `tinylm::pick_masked` (no learning term: the
evaluation CLI has none) and `colloquial::fuse` / `guarded_pick`. Standard library only.
"""
import argparse
import json
import math
import os
import sys

MU, GATE_T = 0.5, 1.0                 # core/src/colloquial.rs MU, GATE_T
CHAT_CELL = (1.1, 2.0, 0.0)           # colloquial.rs STANDARD: the chat fusion cell with the colloquial term
CHAT_HEADER = (0.3, 2.0, 0.25)        # the .sjtl header's chat standard triple: only for a col that is not finite
FORMAL_HEADER = (0.8, 2.0, 0.0)       # the .sjtl header's written standard triple (docs/releases/sj-sr-8-15m.md)


def sigmoid(x):
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def fuse(ll, col, alpha, soft=True):
    """colloquial.rs `fuse`: ll_i + sigmoid((ll_i - ll_0) / GATE_T) * (MU / alpha) * (col_i - col_0).
    soft=False fixes the sigmoid at 1."""
    w = lambda i: sigmoid((ll[i] - ll[0]) / GATE_T) if soft else 1.0
    return [ll[i] + w(i) * (MU / alpha) * (col[i] - col[0]) for i in range(len(ll))]


def pick_masked(ng, ll, alpha, tau, delta, masked=()):
    """tinylm.rs `pick_masked` with an empty learning term. The margin test uses the whole list; a masked
    candidate cannot be chosen; ties go to the better n-gram rank; the winner must beat the first by delta."""
    if len(ng) < 2 or ng[0] - ng[1] >= tau:
        return 0
    s0 = ng[0] + alpha * ll[0]
    best, best_s = 0, s0
    for i in range(1, len(ng)):
        if i in masked:
            continue
        s = ng[i] + alpha * ll[i]
        if s > best_s:
            best, best_s = i, s
    return best if best_s - s0 >= delta else 0


def row_pick(r, profile, gate=True, soft=True):
    """The index the rerank chooses for one record; gate=False sets tau = inf and delta = 0; soft=False fixes the
    colloquial term's soft gate at 1 (chat only)."""
    if "ll" not in r:                  # one candidate, or the rerank did not run: the first stands
        return 0
    n, masked = r["n"], set(r["masked"])
    ng, ll = r["ng"][:n], r["ll"][:n]
    off = lambda cell: cell if gate else (cell[0], math.inf, 0.0)
    if profile == "formal":
        return pick_masked(ng, ll, *off(FORMAL_HEADER), masked)
    col = r["col"]                     # KeyError here means the scores are not a full-system chat run
    if all(c is not None for c in col):
        alpha, tau, delta = off(CHAT_CELL)
        return pick_masked(ng, fuse(ll, col, alpha, soft), alpha, tau, delta, masked)
    return pick_masked(ng, ll, *off(CHAT_HEADER), masked)   # guarded_pick's fallback


def rowstats_line(r, j):
    ok = r["ok"][j]
    return f"{r['i']}\t{ok}\t{0 if ok else r['ed'][j]}\t{r['glen']}\n"


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p (tools/evalstats.py `mcnemar_exact`)."""
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def self_test():
    # core/src/tinylm.rs `a_masked_candidate_cannot_win_but_still_counts_for_the_gate`
    ng, ll = [0.0, -0.5, -3.0], [-5.0, 0.0, -1.0]
    assert pick_masked(ng, ll, 1.0, 2.0, 0.0) == 1
    assert pick_masked(ng, ll, 1.0, 2.0, 0.0, {1}) == 2
    assert pick_masked(ng, [-1.0, 0.0, -9.0], 1.0, 2.0, 0.0, {1}) == 0
    assert pick_masked([0.0, -3.0, -3.5], [-9.0, 0.0, 0.0], 1.0, 2.0, 0.0, {1}) == 0
    assert pick_masked(ng, ll, 1.0, 2.0, 1.5, {1}) == 0
    assert pick_masked([0.0, -0.5], [0.0, 0.5], 1.0, 2.0, 0.0) == 0         # a tie keeps the better n-gram rank
    assert pick_masked([0.0, -0.5], [0.0, 1.5], 1.0, 2.0, 1.0) == 1         # beating the first by exactly delta wins
    # core/src/colloquial.rs `fused_pick_uses_the_registered_cell`, `non_finite_col_falls_back_to_the_plain_pick`
    rec = lambda ng, ll, col: {"i": 1, "n": len(ng), "ng": ng, "ll": ll, "col": col, "masked": []}
    assert row_pick(rec([-10.0, -10.3], [-5.0, -4.2], [0.0, 0.0]), "chat") == 1
    assert row_pick(rec([-10.0, -10.3], [-5.0, -4.8], [0.0, 0.0]), "chat") == 0
    assert row_pick(rec([-10.0, -12.0], [-50.0, 0.0], [0.0, 0.0]), "chat") == 0
    assert row_pick(rec([-10.0, -12.0], [-50.0, 0.0], [0.0, 0.0]), "chat", gate=False) == 1   # tau off
    assert row_pick(rec([-10.0, -11.9], [-50.0, 0.0], [0.0, 0.0]), "chat") == 1
    assert row_pick(rec([-10.0, -10.3], [-5.0, -3.8], [0.0, None]), "chat") == 0
    assert row_pick(rec([-10.0, -10.3], [-5.0, -3.8], [0.0, None]), "chat", gate=False) == 1   # gate off drops delta 0.25
    assert abs(fuse([-10.0, -9.0, -12.0], [-5.0, -4.0, -3.0], 1.1)[1] - (-9.0 + sigmoid(1.0) * (0.5 / 1.1))) < 1e-12
    soft = rec([-10.0, -10.3], [-5.0, -8.0], [0.0, 10.0])        # sigmoid(-3) keeps the colloquial lift small
    assert row_pick(soft, "chat") == 0 and row_pick(soft, "chat", soft=False) == 1
    assert sigmoid(2000.0) == 1.0 and sigmoid(-2000.0) == 0.0
    assert abs(mcnemar_exact(3, 0) - 0.25) < 1e-12 and mcnemar_exact(0, 0) == 1.0
    print("self-test ok")


def main():
    if sys.argv[1:] == ["--self-test"]:
        return self_test()
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", choices=("chat", "formal"))
    ap.add_argument("scores")
    ap.add_argument("rowstats")
    ap.add_argument("--off", choices=("gate", "soft"), default="gate")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.off == "soft" and a.profile != "chat":
        ap.error("--off soft is chat only: the written profile has no colloquial term")
    with open(a.scores, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    with open(a.rowstats, encoding="utf-8") as f:
        ref = f.readlines()
    if len(rows) != len(ref):
        sys.exit(f"STOP: {len(rows)} score records, {len(ref)} rowstats lines")

    # Step 1: the shipped rule must reproduce the run.
    full = [row_pick(r, a.profile) for r in rows]
    bad_c = [r["i"] for r, j in zip(rows, full) if j != r["c"]]
    bad_r = [r["i"] for r, j, line in zip(rows, full, ref) if rowstats_line(r, j) != line]
    if bad_c or bad_r:
        sys.exit(f"STOP: recomputed pick differs from `c` on {len(bad_c)} rows {bad_c[:20]}, "
                 f"rowstats differ on {len(bad_r)} rows {bad_r[:20]}")
    ran = [r for r in rows if "ll" in r]
    fallback = sum(1 for r in ran if a.profile == "chat" and any(c is None for c in r["col"]))
    held = sum(1 for r in ran if r["ng"][0] - r["ng"][1] >= 2.0)
    print(f"{a.profile}: {len(rows)} rows; shipped pick = `c` and rowstats byte-identical on every row")
    print(f"  rerank ran on {len(ran)} rows; margin >= tau on {held}; masked candidates on "
          f"{sum(1 for r in rows if r['masked'])} rows; non-finite col (plain fallback) on {fallback} rows")

    # Step 2: tau = inf and delta = 0, or (--off soft) the colloquial soft gate fixed at 1.
    off = [row_pick(r, a.profile, gate=a.off != "gate", soft=a.off != "soft") for r in rows]
    ok_full = [r["ok"][j] for r, j in zip(rows, full)]
    ok_off = [r["ok"][j] for r, j in zip(rows, off)]
    label = "gate" if a.off == "gate" else "soft gate"
    fixed = sum(1 for x, y in zip(ok_full, ok_off) if not x and y)
    broken = sum(1 for x, y in zip(ok_full, ok_off) if x and not y)
    print(f"  full system {sum(ok_full)} correct; {label} off {sum(ok_off)} correct; "
          f"fixed {fixed}, broken {broken}, net {fixed - broken:+d}, exact McNemar p {mcnemar_exact(fixed, broken):.3g}")
    print(f"  pick differs from the full system on {sum(1 for x, y in zip(full, off) if x != y)} rows")
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        for name, picks in (("full", full), (f"{a.off}off", off)):
            with open(os.path.join(a.out, f"{a.profile}.{name}.r"), "w", encoding="utf-8") as f:
                f.writelines(rowstats_line(r, j) for r, j in zip(rows, picks))


if __name__ == "__main__":
    main()
