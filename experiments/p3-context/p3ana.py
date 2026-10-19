"""P3 readout (README registration): S64 against the shipped M, with S2 (P2's student) as the control and M@64 as a reference.
Numbers only.

  python3 experiments/p3-context/p3ana.py SCORE_DIR SJTL
    SCORE_DIR/<arm>.<profile>.<set>.jsonl from score_p3.py; profile chat for every set, formal for the public sets (dev302, typing, ur).
    SJTL = the shipped tinylm.sjtl; its hash must equal data/tinylm.sjtl.sha256 and the shipped values come from its header.
  Arms (ARMS): M8 = the shipped int8 file as an .npz (sjtl2npz.py), the verdict baseline; M2 = the f32 M, P2's baseline, so S2 against it
  reproduces P2; M64 = the shipped M at the 64-character context; S2 = P2's student; S64 = the new student.
"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "experiments", "s7a-tinylm"), os.path.join(ROOT, "tools"), os.path.join(ROOT, "reference", "proto"),
                os.path.join(ROOT, "experiments", "p2-distill")]
import fusegrid as fg  # noqa: E402
import evalstats as ev  # noqa: E402
from export_sjtl import read_sjtl  # noqa: E402
from p2ana import alone, fb  # noqa: E402

TUNE = "chatclause"
PRIVATE = ["discordtune", "chatjudge", "lytune"]
PUBLIC = ["dev302", "typing", "ur"]          # ur = eval/dev/user-reported.txt, the one-sided typo set
CAP_TUNE = 0.003                             # registered: P2's tuning rule
P2_NGRAM_TOP1 = {"chatclause": 4802, "discordtune": 4223, "chatjudge": 2498, "lytune": 4049}   # registered stop: the chat dumps of P2
# arm -> (context, first 16 hex of the model's sha256, tuned on chatclause?)
ARMS = {"M8": (2, "8d4e23f88a1c6ab6", False), "M2": (2, "32308122782d3c5a", False), "M64": (64, "8d4e23f88a1c6ab6", False),
        "S2": (2, "06a25d07d65ecaa1", True), "S64": (64, "f320234764111b22", True)}
SHOWN = ["S64", "S2", "M64", "M2"]           # table rows, each against M8


def load(d, arm, profile, s):
    rows = fg.load_scores(os.path.join(d, f"{arm}.{profile}.{s}.jsonl"))
    got = {(r.get("ctx"), r.get("npz")) for r in rows}
    if got != {ARMS[arm][:2]}:
        sys.exit(f"STOP: {arm}.{profile}.{s} has (context, model) {sorted(got)}, expected {ARMS[arm][:2]}")
    return rows


def shipped_values(sjtl):
    pinned = open(os.path.join(ROOT, "data", "tinylm.sjtl.sha256")).read().split()[0]
    if hashlib.sha256(open(sjtl, "rb").read()).hexdigest() != pinned:
        sys.exit(f"STOP: {sjtl} is not the pinned shipped file {pinned[:8]}")
    head = read_sjtl(sjtl)[0]
    return {"chat": (*head["chat"], head["delta"][0]), "formal": (*head["formal"], head["delta"][1])}


def picks(rows, cell):
    return [fg.pick(r, cell[0], cell[1], "ll", cell[2]) for r in rows]


def regression_ok(fixed, broken):
    """The regression-layer rule: rows identical, or broken <= 2 and net >= 0."""
    return broken <= 2 and fixed >= broken


def verdict(v):
    """v[(profile, set)] = (fixed, broken) of S64 against the shipped M, plus for (chat, discordtune) also (p, S64's broken against the
    n-gram, the shipped M's broken against the n-gram). Returns [(line, ok)] in the registered order."""
    c1, c2, p, b, b_ship = v[("chat", "discordtune")]
    out = [(f"1. discordtune: net {c1 - c2:+d}, p {p:.3g}; broken vs n-gram {b} (shipped {b_ship})",
            (p >= 0.05 or c1 >= c2) and b <= b_ship)]
    for profile in ("chat", "formal"):
        for s in ("dev302", "typing"):
            f, b = v[(profile, s)][:2]
            out.append((f"2. {profile} {s}: +{f} / -{b}", regression_ok(f, b)))
    net = sum(v[(profile, "ur")][0] - v[(profile, "ur")][1] for profile in ("chat", "formal"))
    out.append((f"3. typo set, chat + formal: net {net:+d}", net > 0))
    return out


def main(d, sjtl):
    shipped = shipped_values(sjtl)
    data = {}
    for arm in ARMS:
        for s in [TUNE] + PRIVATE + PUBLIC:
            data[(arm, "chat", s)] = load(d, arm, "chat", s)
        for s in PUBLIC:
            data[(arm, "formal", s)] = load(d, arm, "formal", s)
    for (arm, profile, s), rows in data.items():   # same dump: same candidates, n-gram scores and gold flags in every arm
        if [(r["n"], r["ng"], r["ok"], r["oks"]) for r in rows] != [(r["n"], r["ng"], r["ok"], r["oks"]) for r in data[("M8", profile, s)]]:
            sys.exit(f"STOP: candidates differ between {arm} and M8 on {profile} {s}")
    for s, want in P2_NGRAM_TOP1.items():
        got = sum(r["ok"][0] for r in data[("M8", "chat", s)])
        if got != want:
            sys.exit(f"STOP: chat {s} n-gram top-1 {got}, P2's dump had {want}")
    print(f"n-gram top-1 of the chat dumps equals P2's: {P2_NGRAM_TOP1}")
    print(f"shipped values from {os.path.basename(sjtl)} (hash checked): chat {shipped['chat']}, formal {shipped['formal']}\n")

    cells = {(arm, "formal"): shipped["formal"] for arm in ARMS}
    tuned_ok = {}
    n_t = len(data[("M8", "chat", TUNE)])
    print(f"## Chat values chosen on {TUNE} (n = {n_t}), cap_ub {CAP_TUNE}\n")
    for arm, (_ctx, _sha, tuned) in ARMS.items():
        cells[(arm, "chat")] = shipped["chat"]
        tuned_ok[arm] = True
        if not tuned:
            continue
        try:
            cell = fg.select_weighted({TUNE: data[(arm, "chat", TUNE)]}, 1, col="ll", names=(TUNE,), cap_ub=CAP_TUNE)
        except ValueError:   # no cell within the cap: keep the n-gram first (tau 0 never reranks), and say so in the verdict
            print(f"- {arm}: NO CELL within the cap; keeps the n-gram first")
            cells[(arm, "chat")], tuned_ok[arm] = (0.0, 0, 0), False
            continue
        cells[(arm, "chat")] = cell[:3]
        print(f"- {arm}: alpha {cell[0]}, tau {cell[1]}, delta {cell[2]}; +{cell[3]} / -{cell[4]}, net {cell[3] - cell[4]:+d}, "
              f"UB / n {ev.poisson_upper95(cell[4]) / n_t:.2%}")
    print("- M8, M2, M64: the shipped values\n")

    v = {}
    for profile, sets in (("chat", PRIVATE + PUBLIC), ("formal", PUBLIC)):
        for s in sets:
            ref = data[("M8", profile, s)]
            n = len(ref)
            ng = [bool(r["ok"][0]) for r in ref]
            right, strict = {}, {}
            for arm in ARMS:
                rows, pk = data[(arm, profile, s)], picks(data[(arm, profile, s)], cells[(arm, profile)])
                right[arm] = [bool(r["ok"][j]) for r, j in zip(rows, pk)]
                strict[arm] = sum(r["oks"][j] for r, j in zip(rows, pk))
            ship = right["M8"]
            f0, b0 = fb(ship, ng)
            print(f"## {profile} {s} (n = {n}); n-gram top-1 {sum(ng)}\n")
            print("| arm | top-1 | strict | vs n-gram +/- | UB / n | vs M8 shipped: new right / old right | net | McNemar p |")
            print("|---|---|---|---|---|---|---|---|")
            print(f"| M8 shipped | {sum(ship)} | {strict['M8']} | +{f0}/-{b0} | {ev.poisson_upper95(b0) / n:.2%} | | | |")
            for arm in SHOWN:
                f, b = fb(right[arm], ng)
                c1, c2 = fb(right[arm], ship)
                p = ev.mcnemar_exact(c1, c2)
                print(f"| {arm} | {sum(right[arm])} | {strict[arm]} | +{f}/-{b} | {ev.poisson_upper95(b) / n:.2%} | {c1} / {c2} | "
                      f"{c1 - c2:+d} | {p:.3g} |")
                if arm == "S64":
                    v[(profile, s)] = (c1, c2, p, b, b0)
            f64, b64 = fb(right["S64"], right["S2"])
            print(f"\nS64 vs S2 (the context's own effect): +{f64} / -{b64}, net {f64 - b64:+d}, p {ev.mcnemar_exact(f64, b64):.3g}")
            print("alone: " + "; ".join(f"{arm} {alone(data[(arm, profile, s)])}" for arm in ("S64", "S2", "M64", "M8")) + "\n")

    print("## Verdict (registered, S64 vs the shipped M8)\n")
    lines = verdict(v)
    for line, ok in lines:
        print(f"{line} -> {'pass' if ok else 'FAIL'}")
    if not tuned_ok["S64"]:
        print("S64 had no cell within the tuning cap, so the lines above score the n-gram, not S64")
    passed = tuned_ok["S64"] and all(ok for _, ok in lines)
    print(f"\nregistered reading: {'PASS (worth building the context return)' if passed else 'FAIL'}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
