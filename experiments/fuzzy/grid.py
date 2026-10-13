"""Whole grid -> one TSV (docs/contracts/fuzzy-research.md section 2). Runs on any machine with Python 3.
  python3 experiments/fuzzy/grid.py --cvtune CV.txt --wikitune WIKI.txt --out OUT.tsv [--seed 1] [--jobs 10] [--tune-rows N]
        [--ps 0.05,0.1,0.2] [--deltas 0.5,1,2,3,4] [--limit N]
Stages (each a multiprocessing Pool map, results identical for any --jobs):
  1. exact decodes, once per (set, kind, p) (clean input once per set, shared by the kinds), never recomputed per delta or gate;
  2. tune: per kind and delta, G2 on a seeded subsample (--tune-rows per set, default all) of cvtune and wikitune pooled;
     delta = most G2 rescue@1 at p=0.1 among deltas whose clean G2 break rate <= 0.5%, a tie goes to the larger delta (fewer
     clean rows touched); none qualifies -> the largest delta, marked fallback;
  3. report: dev299 (the dev302 rows without the 3 user-reported ones, see fuzzy.load_set) and typing76, every kind x gate x p
     with the chosen delta, plus clean (p=0) fixed/broken/pushed9/time ratio.
     Rescue counts only changed rows the exact decoder gets right on the clean input (the others are `excluded`, not rescuable).
     time_ratio = gate time / exact decode time of the clean input (also at p > 0).
Column meanings (measure.py has the full list): rows = rows of the set (tuning lines: the pooled tuning rows); n = rows counted by
the other columns (clean lines: all rows; p > 0 and tuning lines: rows the injection changed). On tuning lines `broken` counts
over `rows` (clean input), the rescue columns over `n`.
TSV: first line is a '#' header with the tuning record (seed, tune rows, sizes, deltas); elapsed_s = wall seconds of the
delta-dependent decode job that produced the cell (the shared exact decode is not included).
Also writes OUT.evalstats.md: the tools/evalstats.py compare --net table row of each clean report cell."""
import argparse
import os
import random
import tempfile
import time
from multiprocessing import Pool

import fuzzy as F
import measure as M
from evalstats import compare

TUNE_P, MAX_BREAK = 0.1, 0.005
_ctx, _sets, _seed = None, {}, 1


def _init(profile, sets, seed):
    global _ctx, _sets, _seed
    _ctx, _sets, _seed = F.Ctx(profile), sets, seed


def _exact(key):
    name, kind, p = key
    return key, F.exact_of(_ctx, _sets[name], kind or "tone", p, _seed)  # p = 0: kind does not matter


def _tune(job):
    kind, p, delta, ex_p, ex_0 = job
    t0 = time.perf_counter()
    rows = _sets["tune"]
    n, excl, wrong, r1, r9, empty, _ = F.rescue(F.process(_ctx, rows, kind, p, _seed, delta, exact=ex_p, clean=ex_0), "G2")
    broken = F.clean_stats(F.process(_ctx, rows, kind, 0, _seed, delta, exact=ex_0), "G2")[3]
    return dict(kind=kind, delta=delta, n=n, excluded=excl, wrong=wrong, empty=empty, rescue1=r1, rescue9=r9, broken=broken,
                rows=len(rows), el=time.perf_counter() - t0)


def _report(job):
    name, kind, p, delta, ex, ex_0 = job
    t0 = time.perf_counter()
    recs = F.process(_ctx, _sets[name], kind, p, _seed, delta, exact=ex, clean=ex_0)
    rows, md = [], []
    for gate in F.GATES:
        rows += M.cell_rows(name, kind, gate, p, delta, recs)
        if p == 0:
            _, _, _, _, _, base_rs, gate_rs = F.clean_stats(recs, gate)
            with tempfile.TemporaryDirectory() as td:
                b, g = os.path.join(td, "b"), os.path.join(td, "g")
                open(b, "w", newline="").write(base_rs); open(g, "w", newline="").write(gate_rs)
                md.append(compare(b, g, f"{name} {kind} {gate}", net=True).split("\n")[-1])
    el = time.perf_counter() - t0
    for r in rows:
        r["elapsed_s"] = f"{el:.1f}"
    return rows, md


def run_grid(sets, tune_n, seed, profile, jobs, ps, deltas, out_path):
    """sets: name -> rows for 'cvtune', 'wikitune', 'dev299', 'typing76'. Writes out_path and out_path + '.evalstats.md'."""
    tune = []
    for name in ("cvtune", "wikitune"):
        rows = sets[name]
        if tune_n and tune_n < len(rows):
            rows = [rows[i] for i in sorted(random.Random(f"{seed}|tune|{name}").sample(range(len(rows)), tune_n))]
        tune += rows
    work = {"tune": tune, "dev299": sets["dev299"], "typing76": sets["typing76"]}
    pool = Pool(jobs, _init, (profile, work, seed)) if jobs > 1 else None
    if pool is None:
        _init(profile, work, seed)
    pmap = (lambda f, xs: pool.map(f, xs, chunksize=1)) if pool else (lambda f, xs: list(map(f, xs)))
    ek = lambda name, kind, p: (name, "" if p == 0 else kind, p)
    keys = []
    for name in work:
        for k in [ek(name, "", 0)] + [ek(name, kind, p) for kind in F.KINDS for p in ([TUNE_P] if name == "tune" else ps)]:
            if k not in keys:
                keys.append(k)
    exact = dict(pmap(_exact, keys))
    res = pmap(_tune, [(kind, TUNE_P, d, exact[ek("tune", kind, TUNE_P)], exact[ek("tune", kind, 0)])
                       for kind in F.KINDS for d in deltas])
    chosen, tune_lines = {}, []
    for kind in F.KINDS:
        mine = [r for r in res if r["kind"] == kind]
        ok = [r for r in mine if r["broken"] / r["rows"] <= MAX_BREAK]
        pick = max(ok, key=lambda r: (r["rescue1"], r["delta"])) if ok else max(mine, key=lambda r: r["delta"])
        chosen[kind] = pick["delta"]
        for r in mine:
            note = ("chosen" if ok else "fallback") if r["delta"] == pick["delta"] else ""
            tune_lines.append((dict(set="tune", kind=kind, gate="G2", p=TUNE_P, delta=r["delta"], cls="all",
                                    **{c: r[c] for c in ("rows", "n", "excluded", "wrong", "empty", "rescue1", "rescue9", "broken")},
                                    elapsed_s=f"{r['el']:.1f}"), note))
    rep = pmap(_report, [(name, kind, p, chosen[kind], exact[ek(name, kind, p)], exact[ek(name, kind, 0)])
                         for name in ("dev299", "typing76") for kind in F.KINDS for p in [0] + ps])
    if pool:
        pool.close(); pool.join()
    cols = M.COLS + ["elapsed_s"]
    line = lambda r, note="": "\t".join(str(r.get(c, "")) for c in cols) + f"\t{note}\n"
    with open(out_path, "w", encoding="utf-8", newline="") as out, open(out_path + ".evalstats.md", "w", encoding="utf-8") as md:
        out.write(f"# seed={seed} tune_rows_per_set={tune_n or 'all'} tune_n={len(tune)} cvtune_n={len(sets['cvtune'])} "
                  f"wikitune_n={len(sets['wikitune'])} time_ratio=gate_time/clean_exact_time deltas={','.join(map(str, deltas))} tune_p={TUNE_P} chosen="
                  + ",".join(f"{k}:{d}" for k, d in chosen.items()) + "\n")
        out.write("\t".join(cols + ["note"]) + "\n")
        for r, note in tune_lines:
            out.write(line(r, note))
        for rows, lines in rep:
            out.writelines(line(r) for r in rows)
            md.write("".join(l + "\n" for l in lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cvtune", required=True); ap.add_argument("--wikitune", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=1); ap.add_argument("--profile", default="chat")
    ap.add_argument("--ps", default="0.05,0.1,0.2"); ap.add_argument("--deltas", default="0.5,1,2,3,4")
    ap.add_argument("--jobs", type=int, default=1); ap.add_argument("--tune-rows", type=int, default=0, help="rows per tune set (seeded subsample); 0 = all")
    ap.add_argument("--limit", type=int, help="rows per set (smoke test)")
    a = ap.parse_args()
    lim = lambda r: r[:a.limit] if a.limit else r
    sets = {"cvtune": lim(F.load_set(a.cvtune)), "wikitune": lim(F.load_set(a.wikitune)),
            "dev299": lim(F.load_set("dev299")), "typing76": lim(F.load_set("typing76"))}
    run_grid(sets, a.tune_rows, a.seed, a.profile, a.jobs, [float(x) for x in a.ps.split(",")],
             [float(x) for x in a.deltas.split(",")], a.out)


if __name__ == "__main__":
    main()
