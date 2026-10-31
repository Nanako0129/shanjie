#!/usr/bin/env python3
"""DECIDE-0 (research log 2026-10-31, registration 08b645f2, amendment 1 d9ea5d13): the tables from the Windows machine's outputs.

  python3 experiments/decide0/analyze.py [DIR]      DIR defaults to ~/.cache/shanjie/decide0

Chat: the core's three registered levels, each against standard, from the CLI's rowstats.
Formal: the 9 registered cells re-picked with tools/triage.py pick on the shipping run's --tinylm-scores records (rows without a rerank
keep the CLI's verdict). Two checks stop the run: the shipping cell must reproduce the CLI's shipping rowstats row for row, and the
cross-check cell (alpha 1.0, delta 0) must reproduce the CLI run with the header-patched copy row for row.
Direction rule (registration): a cell passes when every set of its profile has net >= 0 and the total broken <= half the total fixed.
Homophone rows (triage bucket C) and 在/再 rows are counted for cvtune and wikitune from the triage and LOSS-0 files; both are wrong
in the shipping run by construction, so only their "fixed" count carries information.
"""
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))
import triage  # noqa: E402

D = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.cache/shanjie/decide0")
CACHE = os.path.expanduser("~/.cache/shanjie")
CHAT_SETS, FORMAL_SETS = ["cvtune", "wikitune", "discordtune"], ["cvtune", "wikitune", "newstune"]
SMALL = ["dev302", "typing76", "probe", "fw", "user-reported"]
FORMAL_CELLS = [(a, d) for a in (0.5, 0.75, 1.0) for d in (0.5, 0.25, 0.0)]   # first is shipping
TAU = 2.0


def rowstats(path):
    v = [l.split("\t") for l in open(path) if l.strip()]
    if [int(t[0]) for t in v] != list(range(1, len(v) + 1)):
        sys.exit(f"STOP: {path}: rows are not numbered 1..{len(v)} in order")
    return [t[1] == "1" for t in v]


def compare(base, cand):
    if len(base) != len(cand):
        sys.exit(f"STOP: row counts differ ({len(base)} vs {len(cand)})")
    fixed = sum(1 for b, c in zip(base, cand) if c and not b)
    broken = sum(1 for b, c in zip(base, cand) if b and not c)
    return fixed, broken


def homophone_rows(s, p):
    """Row numbers (1-based) of triage bucket C and of the 在/再 rows (LOSS-0's rows file), cvtune and wikitune only."""
    f = (f"{CACHE}/triage/ruhi0/ruhi0-ba8d3860-{s}-{p}.tsv" if s in SMALL
         else f"{CACHE}/triage/ruhi0-big/rows-a/ruhi0-big-a-{s}-{p}.tsv")
    if not os.path.exists(f):
        return None, None
    c = {int(t.split("\t")[0]) for t in open(f, encoding="utf-8") if t.split("\t")[1].startswith("C")}
    zai = set()
    lr = f"{CACHE}/triage/homo0/loss0-rows.tsv"
    if not os.path.exists(lr):
        sys.exit(f"STOP: {lr} is missing; write it first with experiments/loss0/loss0.py --rows {lr}")
    # column 3 is the row's number in the original set file (loss0.py writes the .map's first field)
    for l in open(lr, encoding="utf-8"):
        t = l.rstrip("\n").split("\t")
        if t[0] == s and t[1] == p and t[-1] != "?" and set(t[-1].replace("→", "")) <= {"在", "再"}:
            zai.add(int(t[2]))
    return c, zai


def subset(base, cand, rows):
    if rows is None:
        return ""
    f = sum(1 for i in rows if 0 < i <= len(base) and cand[i - 1] and not base[i - 1])
    b = sum(1 for i in rows if 0 < i <= len(base) and base[i - 1] and not cand[i - 1])
    return f"{f}/{b}"


def formal_pick(sc_path, cli, alpha, delta):
    out = []
    lines = open(sc_path).read().splitlines()
    if len(lines) != len(cli):
        sys.exit(f"STOP: {sc_path}: {len(lines)} score records for {len(cli)} rows")
    for k, (l, v) in enumerate(zip(lines, cli), 1):
        r = json.loads(l)
        if r["i"] != k:
            sys.exit(f"STOP: {sc_path}: record {k} is row {r['i']}")
        if "ll" not in r or len(r["ng"]) < 2:
            out.append(v)
            continue
        out.append(r["ok"][triage.pick(r["ng"], r["ll"], alpha, TAU, delta, r.get("masked", ()))] == 1)
    return out


def main():
    print("== chat: level vs standard (fixed/broken, net); C rows and 在/再 rows fixed/broken")
    for lvl in ["aggressive", "conservative"]:
        tot_f = tot_b = 0
        ok_all = True
        for s in CHAT_SETS + SMALL:
            base, cand = rowstats(f"{D}/chat-standard-{s}.r"), rowstats(f"{D}/chat-{lvl}-{s}.r")
            f, b = compare(base, cand)
            c, z = homophone_rows(s, "chat")
            tag = "" if s in CHAT_SETS else "  (regression)"
            if s in SMALL and z:
                tag += f" 在/再 fixed {sum(1 for i in z if cand[i - 1] and not base[i - 1])} of {len(z)}"
            print(f"  {lvl:12s} {s:14s} n {len(base):5d}  {sum(base):5d} -> {sum(cand):5d}  +{f}/-{b}  net {f - b:+d}"
                  f"  C {subset(base, cand, c)}  在再 {subset(base, cand, z)}{tag}")
            if s in CHAT_SETS:
                tot_f += f; tot_b += b; ok_all &= f - b >= 0
        if lvl == "aggressive":
            print(f"  -> aggressive: every set net >= 0: {ok_all}; total +{tot_f}/-{tot_b}; broken <= fixed/2: {2 * tot_b <= tot_f}")
    print("\n== formal: Python re-pick on the shipping scores")
    for s in FORMAL_SETS + SMALL:
        cli = rowstats(f"{D}/formal-ship-{s}.r")
        ship = formal_pick(f"{D}/formal-ship-{s}.sc", cli, 0.5, 0.5)
        if ship != cli:
            sys.exit(f"STOP: the shipping cell's Python pick differs from the CLI on {s} ({sum(a != b for a, b in zip(ship, cli))} rows)")
        x = formal_pick(f"{D}/formal-ship-{s}.sc", cli, 1.0, 0.0)
        if x != rowstats(f"{D}/formal-1.0-0-{s}.r"):
            sys.exit(f"STOP: cell (1.0, 0) Python pick differs from the CLI with the patched copy on {s}")
    print("  checks: shipping cell and cell (1.0, 0) equal the CLI row for row on every set")
    best = None
    for a, d in FORMAL_CELLS:
        tot_f = tot_b = 0
        ok_all = True
        line = []
        for s in FORMAL_SETS:
            cli = rowstats(f"{D}/formal-ship-{s}.r")
            cand = formal_pick(f"{D}/formal-ship-{s}.sc", cli, a, d)
            f, b = compare(cli, cand)
            c, z = homophone_rows(s, "formal")
            line.append(f"{s} +{f}/-{b} C {subset(cli, cand, c)} 在再 {subset(cli, cand, z)}")
            tot_f += f; tot_b += b; ok_all &= f - b >= 0
        reg = []
        zf = zb = 0
        for s in SMALL:
            cli = rowstats(f"{D}/formal-ship-{s}.r")
            cand = formal_pick(f"{D}/formal-ship-{s}.sc", cli, a, d)
            f, b = compare(cli, cand)
            reg.append(f"{s} +{f}/-{b}")
            z = homophone_rows(s, "formal")[1] or set()
            zf += sum(1 for i in z if cand[i - 1] and not cli[i - 1])
        reg.append(f"small-set 在/再 rows fixed {zf} of {sum(len(homophone_rows(s, 'formal')[1] or ()) for s in SMALL)}")
        passed = ok_all and 2 * tot_b <= tot_f and tot_f > 0   # tot_f > 0 (not in the registration) only drops 0/0 cells, the shipping cell among them
        print(f"  α {a:4} δ {d:4}: total +{tot_f}/-{tot_b} net {tot_f - tot_b:+d}  pass {passed}  |  " + "; ".join(line))
        print(f"      regression: " + "; ".join(reg))
        # registered tie-break "nearer the shipping cell": distance taken as |dα| + |dδ| (the registration names no metric); an equal
        # distance keeps the first cell in FORMAL_CELLS order
        far = abs(a - FORMAL_CELLS[0][0]) + abs(d - FORMAL_CELLS[0][1])
        if passed and (best is None or (tot_f - tot_b, -far) > (best[0], -best[3])):
            best = (tot_f - tot_b, a, d, far)
    print(f"  -> formal: best passing cell {best}")


if __name__ == "__main__":
    main()
