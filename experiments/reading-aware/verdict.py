"""The contract's two significance words for one BASE/CAND pair of rowstats files (docs/contracts/derisk-1015.md section 2, reused by
docs/contracts/reading-aware-0.md sections 4 and 5), from the same counts `tools/evalstats.py compare` prints.

  python3 verdict.py BASE CAND [--alpha 0.05] [--stop-on-negative]
prints `fixed<TAB>broken<TAB>p<TAB>positive|negative|neither`:
  positive  fixed > broken and the exact McNemar p < alpha (alpha 0.05 here, the contract's a)
  negative  broken > fixed and p < 0.05
--stop-on-negative: exit 5 on `negative` (the A1 stop condition of section 5; "A1 is significantly negative on a set").
"""
import argparse
import sys

import rcommon  # noqa: F401  (sys.path)
from evalstats import mcnemar_exact, read_rowstats


def verdict(base, cand, alpha=0.05):
    b, c = read_rowstats(base), read_rowstats(cand)
    if len(b) != len(c) or [r[2] for r in b] != [r[2] for r in c]:
        raise ValueError("the files are not the same evaluation set")
    fixed = sum(1 for x, y in zip(b, c) if not x[0] and y[0])
    broken = sum(1 for x, y in zip(b, c) if x[0] and not y[0])
    p = mcnemar_exact(fixed, broken)
    word = "positive" if fixed > broken and p < alpha else "negative" if broken > fixed and p < 0.05 else "neither"
    return fixed, broken, p, word


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("base")
    ap.add_argument("cand")
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--stop-on-negative", action="store_true")
    a = ap.parse_args(argv)
    try:
        fixed, broken, p, word = verdict(a.base, a.cand, a.alpha)
    except (ValueError, OSError) as e:
        sys.exit(f"verdict: {e}")
    print(f"{fixed}\t{broken}\t{p:.4f}\t{word}")
    if a.stop_on_negative and word == "negative":
        print("STOP: significantly negative (contract section 5)", file=sys.stderr)
        sys.exit(5)


if __name__ == "__main__":
    main()
