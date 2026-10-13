"""X0c (docs/contracts/x0c-compare.md 2.4, 2.5): 0c against tinylm-v3 row by row, and the question 3 counts. Numbers only.

  python3 experiments/x0c/versus.py --scores DIR --setting chat|formal --sets lytune dev302 ... --out OUT \
      --a ALPHA TAU DELTA --b ALPHA TAU DELTA [--rows NAME=FILE|dev:N]... [--no-counts]

A = x0c (DIR/x0c.<setting>.<set>.jsonl, score_0c.py), B = v3 (DIR/v3.<setting>.<set>.jsonl, the CLI's --tinylm-scores) with the given values.
Per set: two tools/evalstats.py compare --net rows with B as the base and A as the new (so fixed = 0c right where tinylm is wrong): all rows,
and covered rows only (rows with skip = 1 take the n-gram first on both sides). Rowstats go to OUT/rowstats/ (only [0-9\\t\\n]).
Then the 2.5 counts for A (see counts()); they need the prefixes, so the rows file of every set (public sets are built in).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "experiments", "s7a-tinylm"))
import fusegrid as fg  # noqa: E402  (also puts tools/ on sys.path)
import evalstats  # noqa: E402
import score_0c as S  # noqa: E402  (numpy only; onnxruntime is imported lazily by load_model)
from tinylm import row_key  # noqa: E402

PUBLIC = {"cvtune": "~/.cache/shanjie/s7c2c-public/tune/cvtune.txt", "wikitune": "~/.cache/shanjie/s7c2c-public/tune/wikitune.txt",
          "dev302": "dev:302", "typing76": "eval/dev/user-typing.txt", "user-reported": "eval/dev/user-reported.txt",
          "fw": "eval/rerank/s7e-function-words.txt", "lytune": "~/.cache/shanjie/e2-public/lytune.txt",
          "wikitypo": "~/.cache/shanjie/e2-public/wikitypo.txt"}


def picks(rows, params, col="ll", skip=None):
    """fusegrid.pick per row; with `skip` (0/1 per row, the 0c scores' skip flags) those rows keep the n-gram first."""
    a, t, d = params
    return [0 if skip and skip[k] else fg.pick(r, a, t, col, d) for k, r in enumerate(rows)]


def counts(rows_a, rows_b, prefixes, pa, pb):
    """The 2.5 counts for A. Rows are the x0c / v3 score records of one set (same candidates), prefixes the rows file's prefix column.
    ll_ne_ll0: rows with some candidate's ll != ll0 (> 1e-6, log10); ctx_pick: pick(ll) != pick(ll0); ctx_longer: of those, 0c's context
    (last 64 characters) is longer than tinylm's row_key; tone_pick: pick(ll) != pick(llr); a_fix_b_not: A right where the n-gram first
    is wrong and B is not right; of those, ctx / tone: A's pick changes with the context / with the tone marks."""
    c = dict(rows=len(rows_a), skip=0, ll_ne_ll0=0, ctx_pick=0, ctx_longer=0, tone_pick=0, a_fix_b_not=0, fix_ctx=0, fix_tone=0, fix_both=0)
    for a, b, pre in zip(rows_a, rows_b, prefixes):
        c["skip"] += bool(a.get("skip"))
        if a["n"] < 2:
            continue
        pl = fg.pick(a, *pa[:2], "ll", pa[2])
        p0 = fg.pick(a, *pa[:2], "ll0", pa[2])
        pr = fg.pick(a, *pa[:2], "llr", pa[2])
        c["ll_ne_ll0"] += max(abs(x - y) for x, y in zip(a["ll"], a["ll0"])) > fg.LL_CHANGED
        ctx, tone = pl != p0, pl != pr
        c["ctx_pick"] += ctx
        c["ctx_longer"] += ctx and len(pre[-S.CONTEXT:]) > len(row_key(pre, 2))
        c["tone_pick"] += tone
        if a["ok"][pl] and not a["ok"][0] and not b["ok"][fg.pick(b, *pb[:2], "ll", pb[2])]:
            c["a_fix_b_not"] += 1
            c["fix_ctx"] += ctx
            c["fix_tone"] += tone
            c["fix_both"] += ctx and tone
    return c


def prefixes_of(spec):
    if spec.startswith("dev:"):
        return [p for _, _, p in S.read_rows(dev=int(spec[4:]))]
    path = os.path.expanduser(spec)
    return [p for _, _, p in S.read_rows(rows=path if os.path.isabs(path) else os.path.join(ROOT, path))]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", required=True); ap.add_argument("--setting", choices=fg.SETTINGS, required=True)
    ap.add_argument("--sets", nargs="+", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--a", nargs=3, type=float, required=True, metavar=("ALPHA", "TAU", "DELTA"))
    ap.add_argument("--b", nargs=3, type=float, required=True, metavar=("ALPHA", "TAU", "DELTA"))
    ap.add_argument("--rows", action="append", default=[], metavar="NAME=FILE|dev:N"); ap.add_argument("--no-counts", action="store_true")
    ap.add_argument("--label", default="x0c vs v3")
    a = ap.parse_args(argv)
    src = dict(PUBLIC, **dict(x.split("=", 1) for x in a.rows))
    os.makedirs(os.path.join(a.out, "rowstats"), exist_ok=True)
    first = True
    allc = {}
    for s in a.sets:
        ra = fg.load_scores(os.path.join(a.scores, f"x0c.{a.setting}.{s}.jsonl"))
        rb = fg.load_scores(os.path.join(a.scores, f"v3.{a.setting}.{s}.jsonl"))
        if len(ra) != len(rb) or any(x[k] != y[k] for x, y in zip(ra, rb) for k in ("n", "ng", "ok", "ed", "glen")):
            sys.exit(f"versus: STOP, {s}: the 0c and tinylm score files do not describe the same candidates")
        skip = [r["skip"] for r in ra]
        for cov in (False, True):
            tag = f"{a.setting}.{s}.{'cov' if cov else 'all'}"
            bp, ap_ = (os.path.join(a.out, "rowstats", f"{tag}.{x}.txt") for x in ("v3", "x0c"))
            for p, rows, params in ((bp, rb, a.b), (ap_, ra, a.a)):
                with open(p, "w", newline="") as f:
                    f.write(fg.rowstats(rows, picks(rows, params, skip=skip if cov else None)))
            t = evalstats.compare(bp, ap_, f"{a.label} {a.setting} {s} {'covered only' if cov else 'all rows'}", net=True).split("\n")
            print("\n".join(t if first else t[2:]))
            first = False
        if not a.no_counts:
            allc[s] = counts(ra, rb, prefixes_of(src[s]), a.a, a.b)
    for s, c in allc.items():
        print(f"counts {a.setting} {s}: " + ", ".join(f"{k} {v}" for k, v in c.items()))


if __name__ == "__main__":
    main()
