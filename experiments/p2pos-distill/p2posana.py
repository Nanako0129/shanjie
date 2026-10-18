"""Position-level distillation readout (experiments/p2pos-distill/README.md registration). Numbers only.

  python3 experiments/p2pos-distill/p2posana.py --scores DIR --dumps DIR --train wiki.dump8 pool.dump8 [--students T R]
         [--formal-m M.formal.jsonl --formal-t T.formal.jsonl [--formal-values 0.7 1 2]]

Per student (default T, then R): p2bana's readout (file prefix = the student; M, S = P2 and X0C = 0c from the same DIR) and p2cdiag's agreement
with 0c on chatclause and discordtune (DIR/STUDENT.SET.jsonl, DIR/X0C.SET.jsonl, DUMPS/SET.dump, TRAIN = the training rows' dumps).
Only T is judged by the registration; R is reported.

Formal non-inferiority (wikitune-clean, a formal-profile dump scored by score.py for M and for T): both are picked with the SAME values, M's
formal shipped (alpha, tau, delta), then compared with the n-gram first (fixed / broken, net, UB / n) and T against M (McNemar).
The shipped values are NOT read from the file: --formal-values defaults to 0.7 1 2, the formal set of tinylm-M.npz's export in
the export command in docs/contracts/rerank-strength.md (`--formal 0.7 1 2`); pass the header's values (export_sjtl.read_sjtl(path)[0]["formal"]
and ["delta"][1]) if the shipped file ever changes.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(EXP, "p2b-reading"), os.path.join(EXP, "p2c-data")]
import p2bana as bana   # noqa: E402  (puts s7a-tinylm, tools and reference/proto on sys.path: fusegrid, evalstats)
import p2cdiag          # noqa: E402

fg, ev = bana.fg, bana.ev
FORMAL_SHIPPED = (0.7, 1, 2)      # M's formal (alpha, tau, delta)


def formal_noninferiority(m_rows, t_rows, values=FORMAL_SHIPPED):
    """Score records of M and T on the same rows -> dict: per model fixed / broken against the n-gram first, T against M."""
    if [tuple(r["ok"]) for r in m_rows] != [tuple(r["ok"]) for r in t_rows]:
        raise SystemExit("p2posana: STOP, the ok columns of the M and T formal files differ")
    ng = [bool(r["ok"][0]) for r in m_rows]
    pm, pt = bana.right(m_rows, values), bana.right(t_rows, values)
    out = {"n": len(ng)}
    for name, p in (("M", pm), ("T", pt)):
        f, b = bana.fb(p, ng)
        out[name] = {"fixed": f, "broken": b, "net": f - b, "ub_over_n": ev.poisson_upper95(b) / len(ng)}
    c1, c2 = bana.fb(pt, pm)
    out["T_vs_M"] = {"new_right": c1, "old_right": c2, "net": c1 - c2, "p": ev.mcnemar_exact(c1, c2)}
    return out


def print_formal(r, values):
    print(f"\n## formal profile, wikitune-clean (n = {r['n']}), M's formal shipped values {values} for both\n")
    print("| picks | vs n-gram +/- | net | UB / n |\n|---|---|---|---|")
    for name in ("M", "T"):
        x = r[name]
        print(f"| {name} | +{x['fixed']}/-{x['broken']} | {x['net']:+d} | {x['ub_over_n']:.2%} |")
    d = r["T_vs_M"]
    print(f"\nT vs M: new right / old right {d['new_right']} / {d['old_right']}, net {d['net']:+d}, McNemar p {d['p']:.3g}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", required=True); ap.add_argument("--dumps", required=True)
    ap.add_argument("--train", nargs="+", required=True)
    ap.add_argument("--students", nargs="+", default=["T", "R"])
    ap.add_argument("--formal-m"); ap.add_argument("--formal-t")
    ap.add_argument("--formal-values", type=float, nargs=3, default=list(FORMAL_SHIPPED), metavar=("ALPHA", "TAU", "DELTA"))
    a = ap.parse_args(argv)
    for s in a.students:
        print(f"\n# student {s}\n")
        bana.main(a.scores, s)
        print()
        p2cdiag.main(["--scores", a.scores, "--dumps", a.dumps, "--train", *a.train, "--student", s])
    if a.formal_m or a.formal_t:
        if not (a.formal_m and a.formal_t):
            ap.error("--formal-m and --formal-t go together")
        values = tuple(a.formal_values)
        print_formal(formal_noninferiority(fg.load_scores(a.formal_m), fg.load_scores(a.formal_t), values), values)


if __name__ == "__main__":
    main()
