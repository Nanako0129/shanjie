#!/usr/bin/env python3
"""The three diagnostics of README.md in this directory (report only). Output is numbers and (character, reading) pairs, never a sentence, a
context or a word of a row; row numbers are allowed.

  diag.py ignition --rows R --dump D --words W --table TABLE.tsv [--top 100]
      One CLI run with `--reading-decode [--reading-table TABLE]` and `--dump D --dump-words W` over rows R. For every (character, reading) pair
      with t != 0 in TABLE that a single-character word of the first 64 candidates uses (the reading is the row's syllable at that position):
      the number of correct and of wrong candidates it is in, and the mean score gap to the row's first candidate in each. Top `--top` by count.
  diag.py exposure --rows R --words W --table TABLE.tsv
      The gold split of each row (`--dump-words` row k = 0): every single-character word whose pair has t < 0, as `row, character, reading, t`.
  diag.py delta --t0 T0.tsv --t1 T1.tsv [--min 0.5]
      The pairs with |t1 - t0| >= --min (a pair in one table only counts as 0 in the other), their number first.

A candidate is correct when its surface equals the row's sentence under the CLI's own comparison (`--lenient` is the default: eval/variants.tsv).
"""
import argparse, collections, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def read_text(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def lines_of(text):
    out = [l[:-1] if l.endswith("\r") else l for l in text.split("\n")]
    return out[:-1] if out and out[-1] == "" else out


def parse_rows(text):
    """-> [(truth, syllables)] of the lines with exactly three `|` fields (the CLI's rows)."""
    rows = []
    for l in lines_of(text):
        p = l.split("|")
        if len(p) == 3:
            rows.append((p[1], p[2].split()))
    return rows


def read_table(text):
    """-> {(character, reading): t}."""
    out = {}
    for l in lines_of(text):
        c, r, t = l.split("\t")
        out[c, r] = float(t)
    return out


def read_dump(text, n):
    """-> per row [(surface, score)] in rank order."""
    cands = [[] for _ in range(n)]
    for l in lines_of(text):
        r, _, s, sc = l.split("\t")
        cands[int(r) - 1].append((s, float(sc)))
    return cands


def read_words(text, n):
    """-> (gold words or None per row, per row [words of candidate k])."""
    gold, cands = [None] * n, [[] for _ in range(n)]
    for l in lines_of(text):
        r, k, w = l.split("\t", 2)
        if int(k) == 0:
            gold[int(r) - 1] = None if w == "-" else w.split(" ")
        else:
            cands[int(r) - 1].append(w.split(" "))
    return gold, cands


def singles(words, syls):
    """[(character, syllable)] of the one-character words of a split, or None when the split does not cover the syllables one to one."""
    if sum(len(w) for w in words) != len(syls):
        return None
    out, pos = [], 0
    for w in words:
        if len(w) == 1:
            out.append((w, syls[pos]))
        pos += len(w)
    return out


def ignition(rows, dump, words, table, same, top=100):
    """-> (rows of (pair, right, wrong, mean gap right, mean gap wrong, t) best first, skipped candidates)."""
    cands = read_dump(dump, len(rows))
    _, cw = read_words(words, len(rows))
    acc, skipped = collections.defaultdict(lambda: [[], []]), 0
    for (truth, syls), cs, ws in zip(rows, cands, cw):
        for (surface, score), w in zip(cs, ws):
            if "".join(w) != surface:
                raise ValueError("dump-words does not match dump")
            s = singles(w, syls)
            if s is None:
                skipped += 1
                continue
            gap = cs[0][1] - score
            for pair in {p for p in s if table.get(p, 0.0) != 0.0}:
                acc[pair][0 if same(surface, truth) else 1].append(gap)
    mean = lambda v: sum(v) / len(v) if v else None
    out = [(p, len(r), len(w), mean(r), mean(w), table[p]) for p, (r, w) in acc.items()]
    out.sort(key=lambda x: (-(x[1] + x[2]), x[0]))
    return out[:top], skipped


def exposure(rows, words, table):
    """-> [(row number, character, reading, t)] of the gold split's single-character words with t < 0."""
    gold, _ = read_words(words, len(rows))
    out = []
    for i, ((_, syls), g) in enumerate(zip(rows, gold)):
        for p in (singles(g, syls) or []) if g else []:
            if table.get(p, 0.0) < 0:
                out.append((i + 1, p[0], p[1], table[p]))
    return out


def delta(t0, t1, lo=0.5):
    """-> [(character, reading, t0, t1)] with |t1 - t0| >= lo, sorted by the pair."""
    return sorted((c, r, t0.get((c, r), 0.0), t1.get((c, r), 0.0)) for c, r in set(t0) | set(t1)
                  if abs(t1.get((c, r), 0.0) - t0.get((c, r), 0.0)) >= lo)


def fmt(x):
    return "-" if x is None else f"{x:.3f}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ignition", "exposure"):
        p = sub.add_parser(name)
        p.add_argument("--rows", required=True)
        p.add_argument("--words", required=True)
        p.add_argument("--table", required=True)
        if name == "ignition":
            p.add_argument("--dump", required=True)
            p.add_argument("--top", type=int, default=100)
            p.add_argument("--exact", action="store_true", help="correct = identical surface (tests); default: the CLI's variant-tolerant comparison")
    d = sub.add_parser("delta")
    d.add_argument("--t0", required=True)
    d.add_argument("--t1", required=True)
    d.add_argument("--min", type=float, default=0.5)
    a = ap.parse_args(argv)
    if a.cmd == "delta":
        rows = delta(read_table(read_text(a.t0)), read_table(read_text(a.t1)), a.min)
        print("pairs", len(rows))
        for c, r, x, y in rows:
            print(f"{c}\t{r}\t{x!r}\t{y!r}\t{y - x:+.3f}")
        return 0
    rows, table = parse_rows(read_text(a.rows)), read_table(read_text(a.table))
    if a.cmd == "exposure":
        out = exposure(rows, read_text(a.words), table)
        print("positions", len(out), "rows", len({o[0] for o in out}))
        for i, c, r, t in out:
            print(f"{i}\t{c}\t{r}\t{t!r}")
        return 0
    if a.exact:
        same = lambda x, y: x == y
    else:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import triage
        lenient = triage.load_lenient()
        same = lambda x, y: lenient(x) == lenient(y)
    out, skipped = ignition(rows, read_text(a.dump), read_text(a.words), table, same, a.top)
    print("pairs", len(out), "skipped candidates", skipped)
    print("char\treading\tt\tin_correct\tin_wrong\tmean_gap_correct\tmean_gap_wrong")
    for (c, r), nr, nw, gr, gw, t in out:
        print(f"{c}\t{r}\t{t!r}\t{nr}\t{nw}\t{fmt(gr)}\t{fmt(gw)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
