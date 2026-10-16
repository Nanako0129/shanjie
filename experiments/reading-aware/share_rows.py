"""Of the rows B breaks, how many have the change at a "share" character (contract docs/contracts/reading-aware-0.md 3.4, "另外單獨報").

  python3 share_rows.py --lm B0u.sjlm --set NAME ROWS BASE.rowstats CAND.rowstats BASE.dump CAND.dump [--set ...]
prints `set<TAB>broken<TAB>share_rows`: broken = rows BASE gets right and CAND gets wrong; share_rows = of those, rows where the first
candidates differ at a position whose character stands alone in the BASE or the CAND first candidate (lexicon-score cut under the row's
readings) and has reading units in the model but not the unit of this row's reading, so decode_units scored it with the share factor.
(The factor lowers a score, so the affected character is often the BASE one, the gold that lost.)
"""
import argparse
import sys

import rcommon
import decode_units
import lm as L
from evalstats import read_rowstats


def first_of(dump):
    out = {}
    with open(dump, encoding="utf-8") as f:
        for line in f:
            r, rank, surface, _ = line.rstrip("\n").split("\t")
            if rank == "1":
                out[int(r)] = surface
    return out


def share_positions(top, syls, idx, lex):
    """Positions of `top` scored with the share factor."""
    return [i for i in rcommon.standalone(top, syls, lex)
            if top[i] in idx.units and top[i] + rcommon.U + syls[i] not in idx.lm.ids]


def report(rows, base_rs, cand_rs, base_first, cand_first, idx, lex):
    broken = share = 0
    for k, (_, syls, _) in enumerate(rows, 1):
        if base_rs[k - 1][0] and not cand_rs[k - 1][0]:
            broken += 1
            b, c = base_first[k], cand_first[k]
            if len(b) == len(c) == len(syls) and set(i for i in range(len(c)) if b[i] != c[i]) & (set(share_positions(c, syls, idx, lex)) | set(share_positions(b, syls, idx, lex))):
                share += 1
    return broken, share


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lm", required=True)
    ap.add_argument("--set", action="append", nargs=6, required=True, metavar=("NAME", "ROWS", "BASE_RS", "CAND_RS", "BASE_DUMP", "CAND_DUMP"))
    a = ap.parse_args(argv)
    lm = L.BigramLM(a.lm, classes=False)
    idx, lex = decode_units.index_of(lm), rcommon.load_lexicon()
    print("set\tbroken\tshare_rows")
    for name, rows, brs, crs, bd, cd in a.set:
        r = rcommon.load_rows(rows)
        base_rs, cand_rs = read_rowstats(brs), read_rowstats(crs)
        if not len(r) == len(base_rs) == len(cand_rs):
            sys.exit(f"share_rows: {name}: rows {len(r)}, rowstats {len(base_rs)} / {len(cand_rs)}")
        print(f"{name}\t" + "\t".join(map(str, report(r, base_rs, cand_rs, first_of(bd), first_of(cd), idx, lex))))


if __name__ == "__main__":
    main()
