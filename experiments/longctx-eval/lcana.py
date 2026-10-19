"""Long-context evaluation sets readout (README "只報"): numbers only, no verdict.

  python3 experiments/longctx-eval/lcana.py SCORE_DIR SJTL
    SCORE_DIR/<arm>.<profile>.<set>.jsonl from experiments/p3-context/score_p3.py for P3's arms (M8, M2, M64, S2, S64);
    SJTL = the shipped tinylm.sjtl (hash checked against data/tinylm.sjtl.sha256; the shipped values come from its header).
  Chat values: M8, M2 and M64 use the shipped header; S2 and S64 use the values P3 chose on chatclause (README table). Formal: the
  shipped header for every arm. Every (profile, set) found in SCORE_DIR is reported.
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "p3-context"))
import p3ana as pa  # noqa: E402  (puts s7a-tinylm, tools, reference/proto and p2-distill on sys.path)

TUNED_CHAT = {"S2": (0.5, 2, 0.5), "S64": (2, 2, 4)}   # P3's selections on chatclause (research log 2026-10-19, P3)
SHOWN = ["S64", "S2", "M64", "M2"]


def cells(shipped):
    out = {}
    for arm in pa.ARMS:
        out[(arm, "chat")] = TUNED_CHAT.get(arm, shipped["chat"])
        out[(arm, "formal")] = shipped["formal"]
    return out


def main(d, sjtl):
    shipped = pa.shipped_values(sjtl)
    cell = cells(shipped)
    pairs = sorted({tuple(os.path.basename(p).split(".")[1:3]) for p in glob.glob(os.path.join(d, "M8.*.*.jsonl"))})
    print(f"shipped values from {os.path.basename(sjtl)} (hash checked): chat {shipped['chat']}, formal {shipped['formal']}; "
          f"tuned chat {TUNED_CHAT}\n")
    for profile, s in pairs:
        data = {arm: pa.load(d, arm, profile, s) for arm in pa.ARMS}
        ref = data["M8"]
        for arm, rows in data.items():
            if [(r["n"], r["ng"], r["ok"], r["oks"]) for r in rows] != [(r["n"], r["ng"], r["ok"], r["oks"]) for r in ref]:
                sys.exit(f"STOP: candidates differ between {arm} and M8 on {profile} {s}")
        n = len(ref)
        ng = [bool(r["ok"][0]) for r in ref]
        right = {}
        strict = {}
        for arm, rows in data.items():
            pk = pa.picks(rows, cell[(arm, profile)])
            right[arm] = [bool(r["ok"][j]) for r, j in zip(rows, pk)]
            strict[arm] = sum(r["oks"][j] for r, j in zip(rows, pk))
        ship = right["M8"]
        f0, b0 = pa.fb(ship, ng)
        print(f"## {profile} {s} (n = {n}); n-gram top-1 {sum(ng)}\n")
        print("| arm | top-1 | strict | vs n-gram +/- | UB / n | vs M8 shipped: new right / old right | net | McNemar p |")
        print("|---|---|---|---|---|---|---|---|")
        print(f"| M8 shipped | {sum(ship)} | {strict['M8']} | +{f0}/-{b0} | {pa.ev.poisson_upper95(b0) / n:.2%} | | | |")
        for arm in SHOWN:
            f, b = pa.fb(right[arm], ng)
            c1, c2 = pa.fb(right[arm], ship)
            print(f"| {arm} | {sum(right[arm])} | {strict[arm]} | +{f}/-{b} | {pa.ev.poisson_upper95(b) / n:.2%} | {c1} / {c2} | "
                  f"{c1 - c2:+d} | {pa.ev.mcnemar_exact(c1, c2):.3g} |")
        f64, b64 = pa.fb(right["S64"], right["S2"])
        print(f"\nS64 vs S2: +{f64} / -{b64}, net {f64 - b64:+d}, p {pa.ev.mcnemar_exact(f64, b64):.3g}\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
