"""S7f: how many evaluation rows appear whole inside a corpus (contract 2.1, discordtune; Mac only). Prints counts only, never text.

  python discord_overlap.py --rows DISCORD_ROWS --corpus A0_runs-wiki.txt A0_runs-coll.txt chat-real.txt generated.txt [--rows-out FILE]

Rows are `前文|句子|讀音` (lm_eval.rows_of). A row overlaps a corpus file when its sentence occurs whole inside some line of it.
Output: `rows<TAB>n`, one `<corpus basename><TAB>count` per file, and `any<TAB>count`. --rows-out writes the 1-based numbers of the
overlapping rows (digits and newlines only), so the judgement can use the rows that overlap nothing. --min-len N skips rows shorter
than N characters (reference only; the contract's count uses every row).

  python discord_overlap.py --net BASE.txt NEW.txt --exclude OVERLAP_ROWS [--weight 10]

Contract section 3's net: over the rows of two rowstats files (fusegrid's .base / .new) NOT listed in OVERLAP_ROWS (the --rows-out
file), prints `rows`, `fixed`, `broken` and `net` (fixed - weight x broken). Numbers only.
"""
import argparse
import os
import sys

import clean

sys.path.insert(0, os.path.join(clean.ROOT, "tools"))
import evalstats  # noqa: E402  read_rowstats: aligned, digits-only rowstats lines


def overlap(sentences, corpus_paths, min_len=1):
    """Returns ({path: set of row indices}, set of row indices overlapping any). Rows shorter than `min_len` never count (the contract
    counts every row; --min-len 4 is the corpus-removal rule of section 2.1, reported for reference)."""
    by_sent = {}
    for i, s in enumerate(sentences):
        if s and len(s) >= min_len:
            by_sent.setdefault(s, []).append(i)
    m = clean.Matcher(by_sent)
    per = {}
    for p in corpus_paths:
        hit = set()
        with open(p, encoding="utf-8") as f:
            for line in f:
                for s in m.find(line):
                    hit.update(by_sent[s])
        per[p] = hit
    return per, set().union(*per.values()) if per else set()


def net(base, new, exclude, weight):
    """(rows, fixed, broken, fixed - weight x broken) over the rows not in `exclude` (1-based row numbers)."""
    b, c = evalstats.read_rowstats(base), evalstats.read_rowstats(new)
    if len(b) != len(c) or any(x[2] != y[2] for x, y in zip(b, c)):
        raise SystemExit("discord_overlap: the two rowstats files are not the same rows (count or gold lengths differ)")
    rows = [r for r in range(1, len(b) + 1) if r not in exclude]
    fixed = sum(1 for r in rows if not b[r - 1][0] and c[r - 1][0])
    broken = sum(1 for r in rows if b[r - 1][0] and not c[r - 1][0])
    return len(rows), fixed, broken, fixed - weight * broken


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows"); ap.add_argument("--corpus", nargs="+")
    ap.add_argument("--rows-out"); ap.add_argument("--min-len", type=int, default=1)
    ap.add_argument("--net", nargs=2, metavar=("BASE", "NEW")); ap.add_argument("--exclude"); ap.add_argument("--weight", type=float, default=10)
    a = ap.parse_args(argv)
    if a.net:
        if not a.exclude:
            ap.error("--net needs --exclude")
        exclude = {int(x) for x in open(a.exclude).read().split()}
        n, f, b, v = net(*a.net, exclude, a.weight)
        print(f"rows\t{n}\nfixed\t{f}\nbroken\t{b}\nnet\t{v:g}")
        return
    if not (a.rows and a.corpus):
        ap.error("--rows and --corpus are required (or --net)")
    sentences = [t for t, _, _ in clean.rows_of([a.rows])]
    per, any_ = overlap(sentences, a.corpus, a.min_len)
    print(f"rows\t{len(sentences)}")
    for p in a.corpus:
        print(f"{os.path.basename(p)}\t{len(per[p])}")
    print(f"any\t{len(any_)}")
    if a.rows_out:
        with open(a.rows_out, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(f"{i + 1}\n" for i in sorted(any_))


if __name__ == "__main__":
    main()
