"""Step-0 bucket table (contract docs/contracts/reading-aware-0.md 3.0), from lm_eval.py / ra_eval.py `--dump` output of BASE.

  python3 buckets.py --label chat --set dev302 dev:302 dev302.chat.dump --set typing76 typing76.txt typing76.chat.dump ...
        [--hetero heterophony1.list] [--overrides tools/reading_overrides.tsv]
One TSV line per set (stdout, numbers only; header first). Run it once per setting (the dump differs). A row is wrong when the first
candidate differs from the gold sentence under eval.lenient, as lm_eval counts it. The dump is rank 1 per row of `row<TAB>rank<TAB>surface<TAB>score`.

  i       wrong rows whose first candidate has a "level-2 reading": a character of the 44-character A1 list (a1_table.DEMOTE_44) standing
          alone with a ˙ reading
  i_err   of those, rows where the gold character at that position differs (the error is at that position): the upper bound A1 can fix
  ii      wrong rows whose first candidate has a "level-3 reading": a standalone character with two or more lexicon readings, read with
          neither the primary reading nor a reading of the same sound with another tone (the consonant/vowel differ, e.g. 長 ㄓㄤˇ)
  ii_err  likewise; i + ii (as sets) bounds what B can fix
  iii     ALL rows whose gold sentence has a "level-1 reading": a standalone whitelist particle typed one-tone (ㄅㄚ for 吧; level1.level1)
  iii_wrong  of those rows, the ones BASE already gets wrong (A2 can only hurt the other ones)
"Standalone" = a single-character word in the lexicon-score cut of that sentence under its readings (rcommon.segment); the dump does not
carry BASE's own cut, so a character the LM actually cut out alone but a lexicon word covers is not counted (stated limit). Primary reading
= units.Labeller.primary (tools/readings.py rules 0, 1, 3, 4; --hetero needed for rule 3, without it the lexicon's first reading).
"""
import argparse
import os
import sys

import rcommon
import a1_table
import level1
import units
import lm_eval  # noqa: E402


def read_first(dump):
    """{row number: first candidate} from a --dump file."""
    out = {}
    with open(dump, encoding="utf-8") as f:
        for line in f:
            r, rank, surface, _ = line.rstrip("\n").split("\t")
            if rank == "1":
                out[int(r)] = surface
    return out


def classify(rows, firsts, lex, labeller, l1):
    """Counts dict for one set. rows = lm_eval rows [(gold, syllables, context)]; firsts = read_first()."""
    lenient = lm_eval.lenient
    tab = labeller.table
    c = dict(n=len(rows), wrong=0, i=0, i_err=0, ii=0, ii_err=0, iii=0, iii_wrong=0)
    for k, (gold, syls, _) in enumerate(rows, 1):
        top = firsts[k]
        wrong = lenient(top) != lenient(gold)
        c["wrong"] += wrong
        if wrong and len(top) == len(syls) == len(gold):
            t2 = [i for i in rcommon.standalone(top, syls, lex) if top[i] in a1_table.DEMOTE_44 and rcommon.is_dot(syls[i])]
            t3 = []
            for i in rcommon.standalone(top, syls, lex):
                ch = top[i]
                if labeller.multi(ch):
                    p = labeller.primary(ch)
                    if syls[i] != p and rcommon.strip_tone(syls[i]) != rcommon.strip_tone(p):
                        t3.append(i)
            c["i"] += bool(t2)
            c["i_err"] += any(lenient(top[i]) != lenient(gold[i]) for i in t2)
            c["ii"] += bool(t3)
            c["ii_err"] += any(lenient(top[i]) != lenient(gold[i]) for i in t3)
        if len(gold) == len(syls) and level1.analyse(gold, syls, lex, l1)[1]:
            c["iii"] += 1
            c["iii_wrong"] += wrong
    return c


COLS = ("n", "wrong", "i", "i_err", "ii", "ii_err", "iii", "iii_wrong")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--label", required=True, help="chat or formal (printed, not used)")
    ap.add_argument("--set", action="append", nargs=3, metavar=("NAME", "ROWS", "DUMP"), required=True, help="ROWS: a file or dev:N")
    ap.add_argument("--hetero", help="heterophony1.list (primary readings)")
    ap.add_argument("--overrides", default=os.path.join(rcommon.ROOT, "tools", "reading_overrides.tsv"))
    a = ap.parse_args(argv)
    if not a.hetero:
        print("buckets: no --hetero: the primary reading is the lexicon's first", file=sys.stderr)
    lex = rcommon.load_lexicon()
    labeller = units.Labeller(rcommon.char_readings(lex), units.load_overrides(a.overrides) if os.path.exists(a.overrides) else None,
                              units.load_hetero(a.hetero) if a.hetero else None)
    l1 = level1.level1(lex)
    print("label\tset\t" + "\t".join(COLS))
    for name, spec, dump in a.set:
        rows = rcommon.load_rows(spec)
        firsts = read_first(dump)
        if sorted(firsts) != list(range(1, len(rows) + 1)):
            sys.exit(f"buckets: {name}: the dump has rows {min(firsts, default=0)}..{max(firsts, default=0)} ({len(firsts)}), the rows file {len(rows)}")
        c = classify(rows, firsts, lex, labeller, l1)
        print(f"{a.label}\t{name}\t" + "\t".join(str(c[x]) for x in COLS))


if __name__ == "__main__":
    main()
