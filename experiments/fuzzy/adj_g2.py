"""Adjacent-key G2, research only (docs/contracts/fuzzy-input.md section 2.4): a larger delta, or a one-sided neighbour table.
  python3 experiments/fuzzy/adj_g2.py --side both|left|right --cvtune CV.txt --wikitune WIKI.txt --out OUT.tsv [grid.py options]
grid.run_grid for the adjacent kind only, same stages, sets and columns. --side left (right): the tolerance table only assumes
the key left (right) of the intended one was hit; the injection still slips to either side (the research's error model), so a
one-sided table can rescue at most the slips to its side. Workers re-import this file (Windows spawn), so the patch is applied
at import from FUZZY_SIDE, which main sets before the pool starts."""
import os

import fuzzy as F
import grid

FULL = F.NEIGHBOURS


def one_side(side):
    """symbol -> [the symbol on the key to that side in the same row], as fuzzy._neighbours but one side."""
    key_sym = dict(zip(F.KEYS_STANDARD, F.SYMBOLS))
    out = {}
    for row in F.KEY_ROWS:
        for i, k in enumerate(row):
            if k in key_sym:
                j = i - 1 if side == "left" else i + 1
                out[key_sym[k]] = [key_sym[row[j]]] if 0 <= j < len(row) and row[j] in key_sym else []
    return out


def apply(side):
    F.KINDS = ("adjacent",)
    if side == "both":
        return
    table, inject = one_side(side), F.inject

    def full_inject(*a, **k):  # injection keeps both sides; only Tolerance (built after this) sees the one-sided table
        F.NEIGHBOURS = FULL
        try:
            return inject(*a, **k)
        finally:
            F.NEIGHBOURS = table
    F.NEIGHBOURS, F.inject = table, full_inject


if os.environ.get("FUZZY_SIDE"):
    apply(os.environ["FUZZY_SIDE"])


def main():
    import sys
    i = sys.argv.index("--side")
    side = sys.argv[i + 1]
    if side not in ("both", "left", "right"):
        raise SystemExit("--side is both, left or right")
    del sys.argv[i:i + 2]
    os.environ["FUZZY_SIDE"] = side
    apply(side)
    grid.main()


if __name__ == "__main__":
    main()
