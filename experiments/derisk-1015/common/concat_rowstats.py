"""Concatenate or subset rowstats files (docs/contracts/derisk-1015.md section 2, "Compare").

  python3 concat_rowstats.py concat OUT A B C ...     rows of A, then B, then C; row numbers renumbered from 1
  python3 concat_rowstats.py select OUT IN IDXFILE    keep the 1-based row numbers listed in IDXFILE (one per line); renumbered from 1

Every input goes through tools/evalstats.py read_rowstats, so a malformed file (wrong columns, non-digits, rows not 1..n) is
rejected, and the output is what `tools/evalstats.py compare` accepts. Give BASE and CAND the same file order. Use the order of
setnames.ORDER (the contract's section 2 order); the tool does not know set names, it only keeps the order it is given.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from evalstats import read_rowstats  # noqa: E402


def _text(rows):
    return "".join(f"{i}\t{ok}\t{e}\t{g}\n" for i, (ok, e, g) in enumerate(rows, 1))


def concat(paths):
    """Text of the concatenation. An empty input file is an error (it is a failed run, not a set with no rows)."""
    rows = []
    for p in paths:
        r = read_rowstats(p)
        if not r:
            raise ValueError(f"{p}: no rows")
        rows += r
    if not paths:
        raise ValueError("nothing to concatenate")
    return _text(rows)


def select(path, idx):
    """Text of the rows of `path` whose 1-based numbers are in idx (ascending, no duplicates, in range)."""
    rows = read_rowstats(path)
    idx = list(idx)
    if idx != sorted(set(idx)) or any(not 1 <= i <= len(rows) for i in idx):
        raise ValueError(f"{path}: row selection is not ascending, unique and within 1..{len(rows)}")
    return _text(rows[i - 1] for i in idx)


def read_idx(path):
    return [int(l) for l in open(path, encoding="utf-8").read().split()]


def main(argv):
    try:
        if argv[:1] == ["concat"] and len(argv) >= 3:
            text = concat(argv[2:])
        elif argv[:1] == ["select"] and len(argv) == 4:
            text = select(argv[2], read_idx(argv[3]))
        else:
            sys.exit(__doc__)
    except (ValueError, OSError) as e:
        print(f"concat_rowstats: {e}", file=sys.stderr)
        sys.exit(1)
    with open(argv[1], "w", encoding="utf-8", newline="") as f:
        f.write(text)


if __name__ == "__main__":
    main(sys.argv[1:])
