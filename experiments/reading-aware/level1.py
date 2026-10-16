"""Level-1 rows and the fixed-direction probe (contract docs/contracts/reading-aware-0.md 3.3).

Level-1 character: a sentence-final particle of the whitelist (rcommon.TAIL_WHITELIST) that has a ˙ reading and the one-tone reading of the
same syllable (吧 嗎 呢 啦 啊 呀 嘞 哩 in the lexicon). Level-1 row: (that one-tone reading, the character), e.g. (ㄅㄚ, 吧); ㄇㄚˇ of 嗎 is not one.
tone_rows() is what ra_eval.py demotes by delta_tone.

Probe (direction fixed, ˙ -> one tone only): in a gold sentence, every STANDALONE level-1 character whose reading in the row is ˙ gets
the one-tone reading of the same syllable; positions already one-tone stay; a row with nothing to change is skipped. Standalone = a
single-character word in the lexicon-score cut of the gold sentence under the row's own readings (rcommon.segment).
Public probe rows are not committed: `probe` writes them to the files you name, outside the repo.

  python3 level1.py list
  python3 level1.py probe --rows FILE|dev:N --out PROBE_ROWS --idx SOURCE_ROW_NUMBERS
  python3 level1.py onetone --rows FILE|dev:N --idx ROW_NUMBERS
prints one TSV line: rows spec, rows, rows already typed one-tone, probe rows.  --idx lists the 1-based source rows of the probe rows
(concat_rowstats.py select turns the BASE rowstats of the source set into the "before" side of the S2r premise line).
"""
import argparse

import rcommon


def level1(lex):
    """{(one-tone reading, character): ˙ reading}."""
    tab, out = rcommon.char_readings(lex), {}
    for c in rcommon.TAIL_WHITELIST:
        rs = {r for r, _ in tab.get(c, ())}
        for r in sorted(rs):
            if rcommon.is_dot(r) and r[:-1] in rs:
                out[(r[:-1], c)] = r
    return out


def _order(l1):
    return sorted(l1, key=lambda k: (rcommon.TAIL_WHITELIST.index(k[1]), k[0]))


def tone_rows(lex):
    """[(reading, character)] to demote by delta_tone, in whitelist order."""
    return _order(level1(lex))


def analyse(sent, syls, lex, l1=None):
    """(dot positions, one-tone positions) of standalone level-1 characters in the row."""
    l1 = level1(lex) if l1 is None else l1
    dots, ones = [], []
    for i in rcommon.standalone(sent, syls, lex):
        if (syls[i], sent[i]) in l1:
            ones.append(i)
        elif l1.get((syls[i][:-1], sent[i])) == syls[i]:
            dots.append(i)
    return dots, ones


def flip(sent, syls, lex, l1=None):
    """The syllables with every standalone level-1 ˙ replaced by the one-tone reading; None when no position can change."""
    dots, _ = analyse(sent, syls, lex, l1)
    if not dots:
        return None
    new = list(syls)
    for i in dots:
        new[i] = syls[i][:-1]
    return new


def probe(rows, lex):
    """rows: [(sentence, syllables, context)]. Returns (probe lines, 1-based source numbers, rows already one-tone, rows)."""
    l1 = level1(lex)
    lines, idx, already = [], [], 0
    for n, (sent, syls, ctx) in enumerate(rows, 1):
        new = flip(sent, syls, lex, l1)
        already += bool(analyse(sent, syls, lex, l1)[1])
        if new:
            lines.append(f"{ctx}|{sent}|{' '.join(new)}\n")
            idx.append(n)
    return lines, idx, already, len(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("probe")
    p.add_argument("--rows", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--idx", required=True)
    o = sub.add_parser("onetone")
    o.add_argument("--rows", required=True)
    o.add_argument("--idx", required=True)
    a = ap.parse_args(argv)
    lex = rcommon.load_lexicon()
    if a.cmd == "list":
        l1 = level1(lex)
        for k in _order(l1):
            print(f"{k[1]}\t{k[0]}\t{l1[k]}")
        return
    if a.cmd == "onetone":
        l1, rows = level1(lex), rcommon.load_rows(a.rows)
        idx = [n for n, (sent, syls, _) in enumerate(rows, 1) if analyse(sent, syls, lex, l1)[1]]
        open(a.idx, "w", encoding="utf-8", newline="\n").write("".join(f"{i}\n" for i in idx))
        print(f"{a.rows}\t{len(rows)}\t{len(idx)}")
        return
    lines, idx, already, n = probe(rcommon.load_rows(a.rows), lex)
    open(a.out, "w", encoding="utf-8", newline="\n").writelines(lines)
    open(a.idx, "w", encoding="utf-8", newline="\n").write("".join(f"{i}\n" for i in idx))
    print(f"{a.rows}\t{n}\t{already}\t{len(lines)}")


if __name__ == "__main__":
    main()
