#!/usr/bin/env python3
"""The cell choice of "English or Zhuyin at the closing key" (docs/spec/english-mixed.md §16.4), and the rate it is chosen by.

  python3 tools/select_english_cell.py rate KEYS OUT
      KEYS: a probe's decision-point rows (`english-sim --keys` input: id, left, prefix, keys); OUT: what `english-sim --keys KEYS --out OUT`
      wrote for one cell. Prints `rows<TAB>english_first<TAB>rate`: the rows whose keys added exactly one unit, an English token whose
      lower-cased text is the row's token (its keys without the closing `_`).
  python3 tools/select_english_cell.py pick GRID.tsv
      GRID.tsv: one line a cell, `theta kappa kappa_dup rate neg1 neg2 neg3 neg4` (tab separated; `#` lines skipped): the rate on the tuning
      probe and the `auto_english_max` of the four negative runs. A cell is feasible when all four are at most 0.5; the feasible cell with the
      highest rate is chosen, ties going to the larger theta, then the larger kappa, then the larger kappa_dup. Prints the cell; exit 3 when no
      cell is feasible.
"""
import sys

NEG_MAX = 0.5
PUNCT = "_punct_"


def rate(keys_text, out_text):
    """-> (rows, english_first)."""
    ins, outs = keys_text.splitlines(), out_text.splitlines()
    if len(ins) != len(outs):
        raise ValueError("the probe and the output differ in length")
    hits = 0
    for line, o in zip(ins, outs):
        f, g = line.split("\t"), o.split("\t")
        if f[0] != g[0]:
            raise ValueError("the output is not this probe's")
        units = g[2].split()
        hits += len(units) == 1 and units[0].startswith(PUNCT) and units[0][len(PUNCT):].lower() == f[3].rstrip("_")
    return len(ins), hits


def parse_grid(text):
    cells = []
    for line in text.splitlines():
        if line and not line.startswith("#"):
            f = [float(x) for x in line.split("\t")]
            if len(f) != 8:
                raise ValueError("a grid line has eight numbers")
            cells.append(tuple(f))
    return cells


def feasible(cells):
    return [c for c in cells if all(x <= NEG_MAX for x in c[4:])]


def pick(cells):
    """The chosen cell, or None when none is feasible."""
    ok = feasible(cells)
    return max(ok, key=lambda c: (c[3], c[0], c[1], c[2])) if ok else None


def main(argv):
    if len(argv) == 3 and argv[0] == "rate":
        with open(argv[1], encoding="utf-8") as k, open(argv[2], encoding="utf-8") as o:
            rows, hits = rate(k.read(), o.read())
        print(f"{rows}\t{hits}\t{hits / rows if rows else 0.0:.6f}")
        return 0
    if len(argv) == 2 and argv[0] == "pick":
        with open(argv[1], encoding="utf-8") as f:
            cells = parse_grid(f.read())
        c = pick(cells)
        if c is None:
            print(f"no feasible cell among {len(cells)}")
            return 3
        print(f"theta={c[0]:g} kappa={c[1]:g} kappa_dup={c[2]:g} rate={c[3]:.6f} feasible={len(feasible(cells))}/{len(cells)}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
