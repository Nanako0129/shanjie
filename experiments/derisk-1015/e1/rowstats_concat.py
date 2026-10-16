"""Concatenate rowstats files of several sets into one (contract §2 "比較"): row numbers restart from 1, the order is the contract's,
and both arms of a comparison use the same order because the order is a constant here, not an argument.

  python3 rowstats_concat.py --dir ARM_DIR --group judge5 --out ARM_DIR/judge5.rowstats
  python3 rowstats_concat.py --dir ARM_DIR --sets cvtune wikitune dev302 ... --out F      (explicit list; the order given is used)

ARM_DIR/<set>.rowstats is what grid.py judge writes. `tools/evalstats.py compare` then checks the gold lengths line up row by row.
"""
import argparse
import os

import common  # noqa: F401
from evalstats import read_rowstats  # noqa: E402

TUNE = ("cvtune", "wikitune")
JUDGE5 = ("dev302", "typing76", "user-reported", "lytune", "wikitypo")   # contract §2: dev302, typing test, typo reports, lytune, wikitypo
JUDGE4 = tuple(n for n in JUDGE5 if n != "lytune")   # E1d, report only: lytune is 88% of judge5
GROUPS = {"tune": TUNE, "judge5": JUDGE5, "judge4": JUDGE4, "all": TUNE + JUDGE5}


def concat_rows(row_lists):
    """[[(ok, errors, gold_len)...], ...] -> one rowstats text, rows renumbered from 1."""
    out, i = [], 0
    for rows in row_lists:
        for ok, e, n in rows:
            i += 1
            out.append(f"{i}\t{ok}\t{e}\t{n}\n")
    return "".join(out)


def concat_files(paths):
    """An empty input file is an error (a failed run, not a set with no rows), like common/concat_rowstats.py."""
    parts = []
    for p in paths:
        r = read_rowstats(p)
        if not r:
            raise ValueError(f"{p}: no rows")
        parts.append(r)
    return concat_rows(parts)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--group", choices=sorted(GROUPS))
    g.add_argument("--sets", nargs="+")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    names = GROUPS[a.group] if a.group else a.sets
    text = concat_files([os.path.join(a.dir, f"{n}.rowstats") for n in names])
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    print(f"{a.out}: {text.count(chr(10))} rows from {' '.join(names)}")


if __name__ == "__main__":
    main()
