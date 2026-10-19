#!/usr/bin/env python3
"""D1 fusion upper bound (README): cross counts of the n-gram-wrong rows with the gold in ranks 2-8, and the 24-cell (alpha, delta)
frontier at tau 2, from P3's M8 score files (numbers only).

  python3 experiments/diag-near-miss/d1.py SCORE_DIR
    SCORE_DIR/M8.chat.<set>.jsonl for discordtune (decides) and chatclause (reported).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s7a-tinylm"))
import fusegrid as fg  # noqa: E402

SHIP = (0.3, 2.0, 0.25)                     # alpha, tau, delta of the shipped chat profile
P3_SHIP = {"discordtune": (107, 26)}        # research log 2026-10-19 "P3": M8 against the n-gram first choice
ALPHAS = (0.2, 0.3, 0.4, 0.5, 0.7, 1.0)
DELTAS = (0, 0.25, 0.5, 1.0)
BANDS = ((0, 0.25), (0.25, 0.5), (0.5, 1), (1, 2), (2, float("inf")))
MARGIN = 10                                  # a cell must beat the shipping cell's fixes by this much (same as E-C's chat bar)


def fixes_breaks(rows, alpha, tau, delta):
    f = b = 0
    for r in rows:
        j = fg.pick(r, alpha, tau, "ll", delta)
        f += r["ok"][0] == 0 and r["ok"][j] == 1
        b += r["ok"][0] == 1 and r["ok"][j] == 0
    return f, b


def band(m):
    """(index, label) of the margin band."""
    return next((i, f"{lo}–{hi}" if hi != float("inf") else f"≥ {lo}") for i, (lo, hi) in enumerate(BANDS) if lo <= m < hi)


def cross(rows):
    """{(band, alone right, fused argmax right, blocked by): rows} over n-gram-wrong rows with the gold in ranks 2-8."""
    out = {}
    alpha, tau, delta = SHIP
    for r in rows:
        n = min(r["n"], 8)
        golds = [j for j in range(1, n) if r["ok"][j]]
        if r["ok"][0] or not golds:
            continue
        g = golds[0]
        alone = r["ok"][max(range(n), key=lambda i: (r["ll"][i], -i))] == 1
        s = [r["ng"][i] + alpha * r["ll"][i] for i in range(n)]
        arg = max(range(n), key=lambda i: (s[i], -i))
        fused = r["ok"][arg] == 1
        picked = fg.pick(r, alpha, tau, "ll", delta)
        if r["ok"][picked]:
            blocked = "fixed"
        elif r["ng"][0] - r["ng"][1] >= tau:
            blocked = "tau"
        elif fused:
            blocked = "delta"
        else:
            blocked = "-"
        key = (band(r["ng"][0] - r["ng"][g]), alone, fused, blocked)
        out[key] = out.get(key, 0) + 1
    return out


def main(d):
    for s in ("discordtune", "chatclause"):
        p = os.path.join(d, f"M8.chat.{s}.jsonl")
        if not os.path.exists(p):
            print(f"{s}: no score file, skipped\n")
            continue
        rows = fg.load_scores(p)
        ship = fixes_breaks(rows, *SHIP)
        print(f"## {s} (n = {len(rows)}): shipping cell alpha {SHIP[0]}, tau {SHIP[1]}, delta {SHIP[2]}: +{ship[0]} / -{ship[1]}")
        if s in P3_SHIP and ship != P3_SHIP[s]:
            sys.exit(f"STOP: the shipping cell gives {ship}, P3 recorded {P3_SHIP[s]}")
        print("\n| n-gram margin | M alone right | fused argmax right | outcome | rows |\n|---|---|---|---|---|")
        for k, v in sorted(cross(rows).items()):
            print(f"| {k[0][1]} | {'yes' if k[1] else 'no'} | {'yes' if k[2] else 'no'} | {k[3]} | {v} |")
        print("\n| alpha \\ delta | " + " | ".join(str(x) for x in DELTAS) + " |\n|---|" + "---|" * len(DELTAS))
        best = []
        for a in ALPHAS:
            cells = []
            for dl in DELTAS:
                f, b = fixes_breaks(rows, a, SHIP[1], dl)
                cells.append(f"+{f}/-{b}")
                if b <= ship[1] and f >= ship[0] + MARGIN:
                    best.append((a, dl, f, b))
            print(f"| {a} | " + " | ".join(cells) + " |")
        if s == "discordtune":
            verdict = "a cell passes: report it (optimistic upper bound on the same set)" if best else "no cell passes: the fusion layer is closed"
            print(f"\nD1 threshold (breaks <= {ship[1]} and fixes >= {ship[0] + MARGIN}): {verdict}; passing cells {best}")
        print()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
