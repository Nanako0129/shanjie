"""Injected rows for the Rust check (docs/contracts/fuzzy-input.md section 3.1). Public sets only.
  python3 experiments/fuzzy/export_rows.py --set dev299 --kind fuzzy --p 0.1 [--seed 1] --out ROWS
Writes ROWS in the CLI's --rows format (`|sentence|syllables`: sentence start, as the research's Ctx) with fuzzy.inject and the
per-row seed of fuzzy.exact_of, so row i gets the same injected input as in grid.py; p = 0 writes the clean rows. Also writes
ROWS.flags, one `row\tchanged\tinvalid` line per row (digits only).
  python3 experiments/fuzzy/export_rows.py --rows FILE --kind fuzzy --p 0.1 [--seed 1] --out ROWS
--rows takes the place of --set: FILE is in the CLI's `前文|句子|讀音` format (eval/dev/*.txt); each row's context field is kept as it is
and only the reading is injected, by the same rule and per-row seed; ROWS and ROWS.flags are written as with --set.
  python3 experiments/fuzzy/export_rows.py --score ROWS.flags CLEAN EXACT TOL
CLEAN, EXACT, TOL: shanjie-eval rowstats of the same set on the clean rows, on ROWS with `--fuzzy 0` (exact, a row without a path
counts as wrong) and on ROWS with `--fuzzy FLAGS`. Prints per class what fuzzy.rescue counts at k = 1: changed rows, excluded
(wrong on the clean input), wrong (exact wrong on the injected input) and rescued (of the wrong ones, right with tolerance).
An invalid row (a syllable the lexicon lacks) is always wrong for the exact decoder, as in the research: the CLI decodes such a
syllable through its adjacent readings whatever the flags (the switch only gates the engine's finish_syllable), so EXACT is
used for the valid rows only."""
import argparse
import random

import fuzzy as F
from evalstats import read_rowstats


def export(rows, kind, p, seed, valid, ctxs=None):
    """(rows file text, flags file text). `ctxs`: the context field of each row (default empty: sentence start)."""
    out, flags = [], []
    for i, (gold, syls) in enumerate(rows):
        rng = random.Random(f"{seed}|{kind}|{p}|{i}")  # the same string as fuzzy.exact_of
        inp, changed, invalid = F.inject(syls, kind, p, rng, valid) if p > 0 else (syls, False, False)
        out.append(f"{ctxs[i] if ctxs else ''}|{gold}|{' '.join(inp)}\n")
        flags.append(f"{i + 1}\t{int(changed)}\t{int(invalid)}\n")
    return "".join(out), "".join(flags)


def score(flags, clean, exact, tol):
    """{class: (changed, excluded, wrong, rescued)} for class valid / invalid."""
    n = len(flags)
    if not len(clean) == len(exact) == len(tol) == n:
        raise SystemExit(f"row counts differ: flags {n}, rowstats {len(clean)}/{len(exact)}/{len(tol)}")
    out = {}
    for name, inv in (("valid", 0), ("invalid", 1)):
        ch = [i for i, (c, v) in enumerate(flags) if c and v == inv]
        kept = [i for i in ch if clean[i][0]]
        wrong = [i for i in kept if inv or not exact[i][0]]
        out[name] = (len(ch), len(ch) - len(kept), len(wrong), sum(tol[i][0] for i in wrong))
    return out


def read_rows3(path):
    r"""(contexts, rows) of a `前文|句子|讀音` file, with the rows kept as the CLI's three_field_rows does: lines split on "\n" only
    (one trailing "\r" dropped, so a lone "\r" does not split a row), exactly three fields. A local reader on purpose: lm_eval.rows_of
    and fuzzy.rows_of open the file in text mode, where a lone "\r" ends a line, which the CLI does not do."""
    with open(path, encoding="utf-8", newline="") as f:
        lines = f.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # str::lines: a final "\n" ends the last line, it does not start another
    parts = [l.removesuffix("\r").split("|") for l in lines]
    parts = [p for p in parts if len(p) == 3]
    return [p[0] for p in parts], [(p[1], p[2].split()) for p in parts]


def read_flags(path):
    with open(path, encoding="utf-8") as f:
        return [tuple(int(x) for x in l.split("\t")[1:]) for l in f]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=("dev299", "typing76")); ap.add_argument("--rows"); ap.add_argument("--kind", choices=F.KINDS)
    ap.add_argument("--p", type=float); ap.add_argument("--seed", default="1"); ap.add_argument("--out")
    ap.add_argument("--score", nargs=4, metavar=("FLAGS", "CLEAN", "EXACT", "TOL"))
    a = ap.parse_args()
    if a.score:
        f, *rs = a.score
        for name, (ch, ex, wr, r1) in score(read_flags(f), *map(read_rowstats, rs)).items():
            print(f"{name}\tchanged {ch}\texcluded {ex}\twrong {wr}\trescued@1 {r1}")
        return
    if not ((a.set is None) != (a.rows is None) and a.kind and a.p is not None and a.out):
        ap.error("one of --set and --rows, and --kind, --p and --out are required without --score")
    valid = F.Ctx().tol(a.kind).valid
    ctxs, rows = read_rows3(a.rows) if a.rows else (None, F.load_set(a.set))
    text, flags = export(rows, a.kind, a.p, a.seed, valid, ctxs)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text)
    with open(a.out + ".flags", "w", encoding="utf-8") as f:
        f.write(flags)


if __name__ == "__main__":
    main()
