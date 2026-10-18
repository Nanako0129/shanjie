"""L0 readouts of the registration: numbers-only Markdown tables and the (A) / (B) verdict lines.

  python gate.py --tune chatclause --dev discordtune chatjudge lytune --dir SCORES [--ws 1 3] [--b-set discordtune]

File names in SCORES (the tuning set and every dev set; chat setting only):
  L0w<w>.c64.<set>.jsonl, L0w<w>.c2.<set>.jsonl   score_l0.py l0 --context 64 / 2   (model l0-w<w>.pt)
  M.<set>.jsonl                                   experiments/s7a-tinylm/score.py, shipped context (fields n, ok, ng, ll); M's shipped pick
                                                  is fusegrid.pick(r, 0.3, 2, col="ll", delta=0.25)
  M64.<tune>.jsonl                                score_l0.py m64 (tuning set only)
Every file of a set must have the same rows and the same ok flags; the run stops otherwise.
"""
import argparse
import json
import math
import os
import sys

import l0core
from l0core import CAP, pick, ub95
import evalstats
import fusegrid

SHIPPED = (0.3, 2, 0.25)       # (alpha, tau, delta): README (B), M's shipped values
A_FIX, A_RETAIN, B_NET = 0.55, 0.92, 40


def md(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"


def ratio(a, b):
    return f"{a / b:.3f}" if b else "n/a"


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def load_set(d, name, ws, with_m64=False):
    """-> {"L0": {(w, ctx): recs}, "M": records, "M64": records or None}; all aligned on the ok flags."""
    out = {"L0": {(w, c): load(os.path.join(d, f"L0w{w}.c{c}.{name}.jsonl")) for w in ws for c in (64, 2)},
           "M": load(os.path.join(d, f"M.{name}.jsonl")),
           "M64": load(os.path.join(d, f"M64.{name}.jsonl")) if with_m64 else None}
    ref = [r["ok"] for r in out["M"]]
    for label, recs in [(f"L0w{w}.c{c}", v) for (w, c), v in out["L0"].items()] + [("M64", out["M64"])]:
        if recs is not None and [r["ok"] for r in recs] != ref:
            sys.exit(f"gate: {label}.{name} and M.{name} differ in rows / ok flags")
    return out


def breaks_line(label, recs_ok, picks):
    f, b = l0core.fixes_breaks(recs_ok, picks)
    n = len(picks)
    return [label, f, b, f - b, f"{ub95(b):.1f}", f"{100 * ub95(b) / n:.2f}%", ratio(b, f)]


def passes_a(fix_hit, fixable, kept, retainable):
    return fixable > 0 and retainable > 0 and fix_hit >= A_FIX * fixable and kept >= A_RETAIN * retainable


def run(tune, devs, d, ws=(1, 3), b_set="discordtune"):
    out = []
    T = load_set(d, tune, ws, with_m64=True)
    n = len(T["M"])
    oks = [r["ok"] for r in T["M"]]

    # ---- (A) diagnostics on the tuning set
    labelled = [("M (shipped context)", [l0core.m_view(r) for r in T["M"]]), ("M@64", T["M64"])] + \
        [(f"L0 w{w} c{c}", T["L0"][(w, c)]) for c in (64, 2) for w in ws]
    rows, a_res = [], {}
    for label, recs in labelled:
        fh, fx, kp, rt = l0core.alone_retain(recs)
        a_res[label] = (fh, fx, kp, rt)
        rows.append([label, fx, fh, pct(fh, fx), rt, kp, pct(kp, rt), sum("noscore" in r for r in recs)])
    out += [f"### (A) {tune}: single-handed pick, no threshold (n = {n})", "",
            md(["model", "fixable rows", "alone-fix hits", "alone-fix", "retain rows", "kept", "retain", "noscore rows"], rows), ""]

    # ---- thresholds selected on the tuning set
    sel, grids = {}, []
    for c in (64, 2):
        for w in ws:
            t, table = l0core.select_t(T["L0"][(w, c)])
            sel[(w, c)] = t
            grids.append(f"L0 w{w} c{c}: " + ("t = " + str(t) if t is not None else "無門檻 (no t within the cap; L0 then never switches)"))
            out_rows = [[g, f, b, net, f"{100 * ub:.2f}%", "yes" if okk else "no"] for g, f, b, net, ub, okk in table]
            grids.append("\n" + md(["t", "fixes", "breaks", "net", "UB / n", f"UB / n <= {100 * CAP:.1f}%"], out_rows) + "\n")
    out += [f"### Selected t on {tune} (cap: 95% Poisson upper limit of broken / n <= {100 * CAP:.1f}%; max net; ties -> larger t)", ""] + grids

    def t_eff(w, c):
        return math.inf if sel[(w, c)] is None else sel[(w, c)]

    # ---- dev sets
    b_ok = {}
    for name in devs:
        D = load_set(d, name, ws)
        n_d = len(D["M"])
        ok_d = [r["ok"] for r in D["M"]]
        m_pick = [fusegrid.pick(r, *SHIPPED[:2], col="ll", delta=SHIPPED[2]) for r in D["M"]]
        a_rows = [breaks_line("M shipped", ok_d, m_pick)]
        b_rows = []
        for c in (64, 2):
            for w in ws:
                recs = D["L0"][(w, c)]
                t = t_eff(w, c)
                picks = [pick(r, t) for r in recs]
                a_rows.append(breaks_line(f"L0 w{w} c{c} (t = {sel[(w, c)]})", ok_d, picks))
                cc = sum(1 for ok, p, m in zip(ok_d, picks, m_pick) if ok[p] and not ok[m])    # L0 right, M wrong
                bb = sum(1 for ok, p, m in zip(ok_d, picks, m_pick) if ok[m] and not ok[p])    # L0 wrong, M right
                b_rows.append([f"L0 w{w} c{c}", cc, bb, cc - bb, f"{evalstats.mcnemar_exact(bb, cc):.4g}"])
                if name == b_set and c == 64:
                    f, b = l0core.fixes_breaks(ok_d, picks)
                    b_ok[w] = (cc - bb, ub95(b) / n_d, sel[(w, c)] is not None)
        out += [f"### {name} (n = {n_d}): vs the n-gram first candidate", "",
                md(["picks", "fixes", "breaks", "net", "breaks UB95", "UB / n", "error ratio (breaks / fixes)"], a_rows), "",
                f"### {name}: L0 vs M shipped", "", md(["L0", "L0 right, M wrong", "L0 wrong, M right", "net", "McNemar p"], b_rows), ""]

    # ---- verdicts (contexts 64; the context-2 rows above are reported only)
    out.append("### Verdicts (context 64)")
    a_pass = {}
    for w in ws:
        fh, fx, kp, rt = a_res[f"L0 w{w} c64"]
        a_pass[w] = passes_a(fh, fx, kp, rt)
        out.append(f"- (A) w{w}: {'PASS' if a_pass[w] else 'FAIL'} (alone-fix {pct(fh, fx)} vs >= {100 * A_FIX:.0f}%, retain {pct(kp, rt)} vs >= {100 * A_RETAIN:.0f}%)")
    b_pass = {}
    for w in ws:
        if w not in b_ok:
            out.append(f"- (B) w{w}: n/a ({b_set} not among --dev)")
            continue
        net, ubn, has_t = b_ok[w]
        b_pass[w] = has_t and net >= B_NET and ubn <= CAP
        out.append(f"- (B) w{w}: {'PASS' if b_pass[w] else 'FAIL'} (net vs M shipped {net:+d} vs >= +{B_NET}, UB / n vs n-gram {100 * ubn:.2f}% vs <= {100 * CAP:.1f}%"
                   + ("" if has_t else ", 無門檻") + ")")
    out.append(f"- (A) overall: {'PASS' if any(a_pass.values()) else 'FAIL'}")
    out.append(f"- (B) overall: {'PASS' if any(b_pass.values()) else 'FAIL'}" + ("" if b_pass else " (n/a)"))
    out.append(f"- registered reading (any w, any of A / B): {'PASS' if any(a_pass.values()) or any(b_pass.values()) else 'FAIL'}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", required=True); ap.add_argument("--dev", nargs="+", default=[])
    ap.add_argument("--dir", required=True); ap.add_argument("--ws", nargs="+", type=int, default=[1, 3])
    ap.add_argument("--b-set", default="discordtune")
    a = ap.parse_args(argv)
    print(run(a.tune, a.dev, a.dir, tuple(a.ws), a.b_set))


if __name__ == "__main__":
    main()
