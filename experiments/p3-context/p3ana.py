"""P3 readout (README registration): S64 against the shipped M, with S2 (P2's student) as the control and M@64 as a reference.
Numbers only.

  python3 experiments/p3-context/p3ana.py SCORE_DIR
    SCORE_DIR/<arm>.<profile>.<set>.jsonl from score_p3.py, arm in M2 M64 S2 S64 (M2 = shipped M with the 2-character key),
    profile chat for every set, and formal for the public sets (dev302, typing, ur).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "experiments", "s7a-tinylm"), os.path.join(ROOT, "tools"), os.path.join(ROOT, "reference", "proto")]
import fusegrid as fg  # noqa: E402
import evalstats as ev  # noqa: E402

TUNE = "chatclause"
PRIVATE = ["discordtune", "chatjudge", "lytune"]
PUBLIC = ["dev302", "typing", "ur"]          # ur = eval/dev/user-reported.txt, the one-sided typo set
CAP_TUNE = 0.003                             # registered: P2's tuning rule
SHIPPED = {"chat": (0.3, 2, 0.25), "formal": (0.7, 2, 1)}   # the shipped tinylm.sjtl e7c179d8 header
ARMS = {"M2": 2, "M64": 64, "S2": 2, "S64": 64}
STUDENTS = ["S2", "S64", "M64"]              # arms whose chat values are tuned on chatclause


def load(d, arm, profile, s):
    rows = fg.load_scores(os.path.join(d, f"{arm}.{profile}.{s}.jsonl"))
    ctx = {r.get("ctx") for r in rows}
    if ctx != {ARMS[arm]}:
        sys.exit(f"STOP: {arm}.{profile}.{s} has context {sorted(ctx)}, expected {ARMS[arm]}")
    return rows


def picks(rows, cell):
    return [fg.pick(r, cell[0], cell[1], "ll", cell[2]) if r["n"] >= 2 else 0 for r in rows]


def right(rows, cell, strict=False):
    key = "oks" if strict else "ok"
    return [bool(r[key][j]) for r, j in zip(rows, picks(rows, cell))]


def fb(new, base):
    return sum(a and not b for a, b in zip(new, base)), sum(b and not a for a, b in zip(new, base))


def regression_ok(fixed, broken):
    """The regression-layer rule: rows identical, or broken <= 2 and net >= 0."""
    return (fixed == 0 and broken == 0) or (broken <= 2 and fixed - broken >= 0)


def main(d):
    data = {}
    for arm in ARMS:
        for s in [TUNE] + PRIVATE + PUBLIC:
            data[(arm, "chat", s)] = load(d, arm, "chat", s)
        for s in PUBLIC:
            data[(arm, "formal", s)] = load(d, arm, "formal", s)
    for (arm, profile, s), rows in data.items():   # same dump, same gold flags, or the comparison is meaningless
        if [tuple(r["ok"]) for r in rows] != [tuple(r["ok"]) for r in data[("M2", profile, s)]]:
            sys.exit(f"STOP: ok columns differ between {arm} and M2 on {profile} {s}")
    cells = {"M2": SHIPPED["chat"]}
    print(f"## Chat values chosen on {TUNE} (n = {len(data[('M2', 'chat', TUNE)])}), cap_ub {CAP_TUNE}\n")
    for arm in STUDENTS:
        try:
            cell = fg.select_weighted({TUNE: data[(arm, "chat", TUNE)]}, 1, col="ll", names=(TUNE,), cap_ub=CAP_TUNE)
        except ValueError:
            cell = (0.0, 0, 0, 0, 0)
        cells[arm] = cell[:3]
        print(f"- {arm}: alpha {cell[0]}, tau {cell[1]}, delta {cell[2]}; +{cell[3]} / -{cell[4]} on the tuning set")
    print(f"- M2 shipped chat {SHIPPED['chat']}, formal {SHIPPED['formal']} (formal is used for every arm)\n")

    verdict = {}
    for profile, sets in (("chat", PRIVATE + PUBLIC), ("formal", PUBLIC)):
        for s in sets:
            m = data[("M2", profile, s)]
            n = len(m)
            ng = [bool(r["ok"][0]) for r in m]
            ship = right(m, SHIPPED[profile])
            print(f"## {profile} {s} (n = {n}); M shipped top-1 {sum(ship)} (strict {sum(right(m, SHIPPED[profile], True))})\n")
            print("| arm | top-1 | strict | vs n-gram +/- | vs M shipped: new right / old right | net | McNemar p | kept |")
            print("|---|---|---|---|---|---|---|---|")
            for arm in ["S64", "S2", "M64"]:
                cell = cells[arm] if profile == "chat" else SHIPPED["formal"]
                p = right(data[(arm, profile, s)], cell)
                ps = right(data[(arm, profile, s)], cell, True)
                f, b = fb(p, ng)
                c1, c2 = fb(p, ship)
                kept = sum(a and b_ for a, b_ in zip(p, ship))
                print(f"| {arm} | {sum(p)} | {sum(ps)} | +{f}/-{b} | {c1} / {c2} | {c1 - c2:+d} | {ev.mcnemar_exact(c1, c2):.3g} | "
                      f"{kept}/{sum(ship)} |")
                if arm == "S64":
                    verdict[(profile, s)] = (c1, c2, ev.mcnemar_exact(c1, c2), b, fb(ship, ng)[1])
            f64, b64 = fb(right(data[("S64", profile, s)], cells["S64"] if profile == "chat" else SHIPPED["formal"]),
                          right(data[("S2", profile, s)], cells["S2"] if profile == "chat" else SHIPPED["formal"]))
            print(f"\nS64 vs S2 (the context's own effect): +{f64} / -{b64}, net {f64 - b64:+d}, p {ev.mcnemar_exact(f64, b64):.3g}\n")

    print("## Verdict (registered, S64 vs the shipped M)\n")
    c1, c2, p, b, b_ship = verdict[("chat", "discordtune")]
    v1 = (p >= 0.05 or c1 - c2 >= 0) and b <= b_ship
    print(f"1. discordtune: net {c1 - c2:+d}, p {p:.3g}; broken vs n-gram {b} (shipped {b_ship}) -> {'pass' if v1 else 'FAIL'}")
    v2 = True
    for profile in ("chat", "formal"):
        for s in ("dev302", "typing"):
            c1, c2 = verdict[(profile, s)][:2]
            ok = regression_ok(c1, c2)
            v2 = v2 and ok
            print(f"2. {profile} {s}: +{c1} / -{c2} -> {'pass' if ok else 'FAIL'}")
    net = sum(verdict[(profile, "ur")][0] - verdict[(profile, "ur")][1] for profile in ("chat", "formal"))
    v3 = net > 0
    print(f"3. typo set, chat + formal: net {net:+d} -> {'pass' if v3 else 'FAIL'}")
    print(f"\nregistered reading: {'PASS (worth building the context return)' if v1 and v2 and v3 else 'FAIL'}")


if __name__ == "__main__":
    main(sys.argv[1])
