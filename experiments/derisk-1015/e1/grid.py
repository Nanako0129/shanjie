"""E1b: the lambda x beta grid, the frozen selection, and the judge-set rowstats at a fixed point. Parallel over processes.

  select  one model, one profile: the 15 grid points of contract §3 E1b on the tuning sets (concatenated), pick the best top1, write the
          frozen selection JSON. lambda = shipped + {-0.10, -0.05, 0, +0.05, +0.10} (shipped: chat 0.5, formal 0.7, core/src/lm.rs),
          beta in {0.5, 0.75, 1}. Ties go to the point nearest the shipped one (lambda shipped, beta = 1): `distance` below.
  judge   one model, one profile, one point (from --selection FILE, or --lam / --beta, e.g. the shipped point): decodes the given sets and writes
          OUT/<label>/<profile>/<set>.rowstats (+ <set>.summary, the lm_eval.py-format line) and OUT/<label>/<profile>/judge.json.

Sets are `--set NAME=PATH` (rows file `context|sentence|reading`) or `--set dev302=dev:302` (the first 302 rows of eval/dev/*.txt sorted,
lm_eval.py --dev). Every set's SHA-256 and row count go into the JSON. The Python reference is always run with --context (contract §2).
Rows are cut into chunks and spread over --procs processes (each loads the model once); the result does not depend on --procs.
"""
import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys

import common
import harness

import lm as L  # noqa: E402
from evalstats import format_rowstats  # noqa: E402

LAM_STEPS = (-0.10, -0.05, 0.0, 0.05, 0.10)
BETAS = (0.5, 0.75, 1.0)
LAM_STEP, BETA_STEP = 0.05, 0.25     # one grid step on each axis, for `distance`


def grid_points(profile):
    """The 15 (lambda, beta) points. The shipped lambda is the profile's own float, the others are rounded to 2 decimals."""
    lam0 = L.PROFILES[profile]
    return [(lam0 if d == 0.0 else round(lam0 + d, 2), b) for d in LAM_STEPS for b in BETAS]


def distance(point, profile):
    """Distance of a grid point from the shipped point (lambda shipped, beta = 1), in grid steps (L1). The contract says "nearest the shipped
    value" without a metric; this is the one used. Remaining ties: smaller |lambda steps|, then larger beta, then smaller lambda (the last
    one only separates the mirror pair lambda shipped +- k steps at the same beta, which the contract's rule leaves open).
    Computed in integer steps: raw float differences of the rounded lambdas (0.45 - 0.5 vs 0.55 - 0.5) are not symmetric."""
    ls = round((point[0] - L.PROFILES[profile]) / LAM_STEP)
    bs = round((1.0 - point[1]) / BETA_STEP)
    return (abs(ls) + abs(bs), abs(ls), -point[1], point[0])


def choose(scores, profile):
    """scores {(lambda, beta): top1}: the highest top1; ties -> smallest `distance`."""
    best = max(scores.values())
    return min((p for p, s in scores.items() if s == best), key=lambda p: distance(p, profile))


def beta_of(beta, kn_path):
    """The beta to run and record. With a KN side file, as given. Without one beta has no effect, so only the default 1.0 (or none) is
    accepted, and it is recorded as None, not as a 1.0 that nothing used."""
    if kn_path:
        return beta
    if beta not in (None, 1.0):
        sys.exit(f"--beta {beta:g} needs a KN side file (none next to the model, none given)")
    return None


def load_set(spec):
    """'NAME=PATH' or 'NAME=dev:N' -> (name, rows, info) with rows [(truth, syls, ctx)]."""
    name, _, src = spec.partition("=")
    if not name or not src:
        raise ValueError(f"bad --set {spec!r}: want NAME=PATH or NAME=dev:N")
    if src.startswith("dev:"):
        rows, _ = harness.load_rows(dev=int(src[4:]))
        digest = hashlib.sha256("\n".join(f"{c}|{t}|{' '.join(s)}" for t, s, c in rows).encode("utf-8")).hexdigest()
    else:
        rows, _ = harness.load_rows(rows=src)
        digest = common.sha256_file(src)
    return name, rows, {"spec": src, "sha256": digest, "n": len(rows)}


# ---------- parallel decoding ----------
_EV = None


def _init(lm_path, kwargs):
    global _EV
    _EV = harness.Evaluator(lm_path, **kwargs)


def _chunk_task(task):
    key, lam, beta, i0, rows = task
    _EV.set_beta(beta)
    out = []
    for t, syls, ctx in rows:
        ok, first, surf, _ = _EV.judge(t, syls, ctx, lam)
        out.append((bool(ok), first, t, harness.lenient(t) in {harness.lenient(s) for s in surf}))
    return key, i0, out


def decode_points(lm_path, kwargs, jobs, procs, chunk=40):
    """jobs: {key: (lam, beta, rows)}. Returns {key: [(ok, first_surface, truth, in_64) per row, in row order]}."""
    tasks = [(key, lam, beta, i, rows[i:i + chunk]) for key, (lam, beta, rows) in jobs.items() for i in range(0, len(rows), chunk)]
    parts = {k: [] for k in jobs}
    if procs <= 1:
        _init(lm_path, kwargs)
        results = map(_chunk_task, tasks)
    else:
        pool = mp.Pool(procs, initializer=_init, initargs=(lm_path, kwargs))
        results = pool.imap_unordered(_chunk_task, tasks)
    try:
        for key, i0, out in results:
            parts[key].append((i0, out))
    except BaseException:
        if procs > 1:
            pool.terminate()   # a worker failed: do not wait for the remaining chunks
        raise
    if procs > 1:
        pool.close(); pool.join()
    return {k: [r for _, out in sorted(v) for r in out] for k, v in parts.items()}


# ---------- commands ----------
def model_kwargs(a):
    return dict(tri_path=a.tri, classes=not a.no_classes, kn=a.kn, no_kn=False, extra_overlay=a.extra_overlay, context=True,
                x_context=getattr(a, "x_context", False))


def model_info(a):
    kn = harness.LE.kn_side(a.lm, a.kn)
    classes = os.path.join(os.path.dirname(os.path.abspath(a.lm)), "classes.sjc")
    return {"lm": a.lm, "lm_sha256": common.sha256_file(a.lm), "tri": a.tri, "tri_sha256": common.sha256_file(a.tri) if a.tri else None,
            "kn": kn, "kn_sha256": common.sha256_file(kn) if kn else None, "no_classes": a.no_classes,
            "classes_sha256": common.sha256_file(classes) if not a.no_classes and os.path.exists(classes) else None,
            "x_context": getattr(a, "x_context", False), "extra_overlay": a.extra_overlay}


def cmd_select(a):
    sets = [load_set(s) for s in a.set]
    rows = [r for _, rs, _ in sets for r in rs]
    if a.limit:
        rows = rows[:a.limit]
    pts = grid_points(a.profile)
    res = decode_points(a.lm, model_kwargs(a), {p: (p[0], p[1], rows) for p in pts}, a.procs)
    scores = {p: sum(r[0] for r in res[p]) for p in pts}
    pick = choose(scores, a.profile)
    beta = beta_of(pick[1], harness.LE.kn_side(a.lm, a.kn))
    out = {"label": a.label, "profile": a.profile, "shipped_lambda": L.PROFILES[a.profile], "lambda": pick[0], "beta": beta,
           "top1": scores[pick], "n": len(rows), "limit": a.limit,
           "scores": [{"lambda": p[0], "beta": p[1], "top1": scores[p]} for p in pts],
           "tuning_sets": {n: i for n, _, i in sets}, "model": model_info(a),
           "provenance": common.provenance(extra={"distance_metric": "L1 in grid steps (0.05 lambda, 0.25 beta), then |dlambda|, then larger beta, then smaller lambda"})}
    path = os.path.join(a.out, f"{a.label}-{a.profile}-selection.json")
    common.write_json(path, out)
    for p in pts:
        print(f"lambda={p[0]:<5g} beta={p[1]:<5g} top1={scores[p]}{'  <- selected' if p == pick else ''}")
    print(f"selected lambda={pick[0]:g} beta={pick[1]:g} top1={scores[pick]}/{len(rows)} -> {path}")


def cmd_judge(a):
    if a.selection:
        sel = json.load(open(a.selection, encoding="utf-8"))
        if sel["profile"] != a.profile:
            sys.exit(f"selection is for {sel['profile']}, not {a.profile}")
        lam, beta = sel["lambda"], sel["beta"]
    elif a.lam is not None and a.beta is not None:
        lam, beta = a.lam, a.beta
    else:
        sys.exit("give --selection FILE, or both --lam and --beta (the shipped point is --lam <shipped> --beta 1)")
    kn_path = harness.LE.kn_side(a.lm, a.kn)
    beta = beta_of(beta, kn_path)
    sets = [load_set(s) for s in a.set]
    res = decode_points(a.lm, model_kwargs(a), {n: (lam, beta, rows) for n, rows, _ in sets}, a.procs)
    d = os.path.join(a.out, a.label, a.profile)
    os.makedirs(d, exist_ok=True)
    info = {}
    for n, rows, meta in sets:
        r = res[n]
        with open(os.path.join(d, f"{n}.rowstats"), "w", encoding="utf-8", newline="") as f:
            f.write(format_rowstats([(ok, first, t) for ok, first, t, _ in r]))
        top1, o64 = sum(x[0] for x in r), sum(x[3] for x in r)
        sha = hashlib.sha256("\n".join(x[1] for x in r).encode("utf-8")).hexdigest()
        line = harness.summary_line(n, a.profile, len(r), top1, o64, sha, kn_path=kn_path, classes=not a.no_classes,
                                    extra_overlay=a.extra_overlay, tri_path=a.tri, lam=lam, beta=beta, x_context=getattr(a, "x_context", False))
        open(os.path.join(d, f"{n}.summary"), "w", encoding="utf-8").write(line + "\n")
        info[n] = {**meta, "top1": top1, "oracle64": o64, "top1_sha256": sha}
        print(line, flush=True)
    common.write_json(os.path.join(d, "judge.json"), {
        "label": a.label, "profile": a.profile, "lambda": lam, "beta": beta, "selection": a.selection, "sets": info, "model": model_info(a),
        "provenance": common.provenance()})


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("select", "judge"):
        p = sub.add_parser(name)
        p.add_argument("--label", required=True, help="model name in the output paths, e.g. B0, T200, B0m")
        p.add_argument("--lm", required=True)
        p.add_argument("--tri"); p.add_argument("--kn"); p.add_argument("--no-classes", action="store_true"); p.add_argument("--extra-overlay")
        p.add_argument("--profile", choices=sorted(L.PROFILES), required=True)
        p.add_argument("--set", action="append", required=True, metavar="NAME=PATH|NAME=dev:N")
        p.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
        p.add_argument("--out", required=True)
    sub.choices["select"].add_argument("--limit", type=int, help="smoke test: first N rows of the concatenated tuning sets")
    j = sub.choices["judge"]
    j.add_argument("--selection"); j.add_argument("--lam", type=float); j.add_argument("--beta", type=float)
    j.add_argument("--x-context", action="store_true", help="E4 (b): digit / English in the left context starts from <X> (mapped model only)")
    a = ap.parse_args(argv)
    (cmd_select if a.cmd == "select" else cmd_judge)(a)


if __name__ == "__main__":
    main()
