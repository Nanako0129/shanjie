"""E4 subset report (contract §3 E4, "只報，不判"): the rows whose left context holds a mid-sentence punctuation mark, a digit run or a Latin run,
cut out of the per-set rowstats of one arm and concatenated in the order given (the same order for both arms, because it is the --set order).

  python3 subset_rowstats.py --arm ARM_DIR --set cvtune=PATH --set wikitune=PATH --set dev302=dev:302 ... --out ARM_DIR/subset.rowstats

ARM_DIR/<set>.rowstats comes from grid.py judge; the rows file gives the contexts (row i of the rowstats = row i of the file).
Mid-sentence punctuation: MID_PUNCT below (comma, enumeration comma, semicolon, colon, full- and half-width). Digit / Latin runs: mapping.tokenize
(the E4 (b) tokenizer). The condition is on the whole context, not only its tail: the tail case is the one --x-context changes, the rest is reported to
show that the other rows do not move.
"""
import argparse
import os

import common  # noqa: F401
import grid
import mapping
import rowstats_concat
from evalstats import read_rowstats  # noqa: E402

MID_PUNCT = "，、；：,;:"


def wanted(ctx):
    return any(c in MID_PUNCT for c in ctx) or any(kind == "x" for kind, _, _ in mapping.tokenize(ctx))


def subset_rows(rows):
    """1-based row numbers of rows (truth, syls, ctx) whose context qualifies."""
    return [i for i, (_, _, ctx) in enumerate(rows, 1) if wanted(ctx)]


def select(stats, numbers):
    return [stats[i - 1] for i in numbers]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True)
    ap.add_argument("--set", action="append", required=True, metavar="NAME=PATH|NAME=dev:N")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    parts, total = [], 0
    for spec in a.set:
        name, rows, _ = grid.load_set(spec)
        stats = read_rowstats(os.path.join(a.arm, f"{name}.rowstats"))
        if len(stats) != len(rows):
            raise SystemExit(f"{name}: {len(stats)} rowstats lines for {len(rows)} rows")
        nums = subset_rows(rows)
        parts.append(select(stats, nums))
        total += len(nums)
        print(f"{name}: {len(nums)} of {len(rows)} rows")
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        f.write(rowstats_concat.concat_rows(parts))
    print(f"{a.out}: {total} rows")


if __name__ == "__main__":
    main()
