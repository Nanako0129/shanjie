"""E4 subset report (docs/contracts/derisk-1015.md section 3, E4, "report only"): the rows whose left context holds mid-sentence
punctuation, a digit or English (the user-reported rows 112/113 type), as 1-based row-index lists; plus a check of the sentence field.

  python3 subset.py --out-dir DIR [--sets NAME ...]        # default: every set of setnames.ORDER (all must exist)
  python3 subset.py --out-dir DIR --rows FILE --name NAME  # one file

Writes, per set, DIR/<name>.any.idx (the context contains a MID character, a digit or a letter anywhere) and DIR/<name>.changed.idx
(the context key differs between lm.context_key and context_key_mid: the rows the E4 (a) change can touch). Also
DIR/concat.any.idx and DIR/concat.changed.idx: the same rows numbered over the concatenation of the given sets in the order given
(use setnames.ORDER, as for the rowstats), for `concat_rowstats.py select`. Prints a count table (numbers only; no row text), and
for each set the number of rows whose SENTENCE field holds anything but Han characters (decoding such a row is impossible, the
reading column has syllables only; the table makes the check visible).
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import punct_ctx  # noqa: E402
import setnames  # noqa: E402

L = punct_ctx
WORDLIKE = re.compile(r"[0-9A-Za-z０-９Ａ-Ｚａ-ｚ]")   # ASCII and full-width digits and letters


def classify(lines):
    """(any, changed, sentence_non_han): 1-based row numbers and a count, over the 3-field `lines`."""
    any_, changed, bad = [], [], 0
    for i, line in enumerate(lines, 1):
        ctx, sent, _ = line.split("|")
        if WORDLIKE.search(ctx) or any(c in punct_ctx.MID for c in ctx):
            any_.append(i)
        if punct_ctx.context_key_mid(ctx) != L.BASE_KEY(ctx):
            changed.append(i)
        bad += any(not L.BASE_KEY(c) for c in sent)   # a character that is not Han (context_key is "" for it)
    return any_, changed, bad


def write_idx(path, idx):
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(f"{i}\n" for i in idx))


def report(sets, out_dir):
    """sets: [(name, path)] in concatenation order. Writes the idx files; returns the Markdown table lines."""
    os.makedirs(out_dir, exist_ok=True)
    offset, cat_any, cat_changed = 0, [], []
    out = ["| set | rows | any | changed | sentence not all Han |", "|---|---|---|---|---|"]
    for name, path in sets:
        lines = setnames.three_field_rows(path)
        any_, changed, bad = classify(lines)
        write_idx(os.path.join(out_dir, f"{name}.any.idx"), any_)
        write_idx(os.path.join(out_dir, f"{name}.changed.idx"), changed)
        cat_any += [offset + i for i in any_]
        cat_changed += [offset + i for i in changed]
        offset += len(lines)
        out.append(f"| {name} | {len(lines)} | {len(any_)} | {len(changed)} | {bad} |")
    write_idx(os.path.join(out_dir, "concat.any.idx"), cat_any)
    write_idx(os.path.join(out_dir, "concat.changed.idx"), cat_changed)
    out.append(f"| concat | {offset} | {len(cat_any)} | {len(cat_changed)} | |")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sets", nargs="+"); ap.add_argument("--rows"); ap.add_argument("--name")
    a = ap.parse_args()
    if a.rows:
        sets = [(a.name or ap.error("--rows needs --name"), a.rows)]
    else:
        sets = [(n, setnames.location(n)[0]) for n in (a.sets or setnames.ORDER)]
        missing = [n for n, p in sets if not os.path.exists(p)]
        if missing:   # a skipped set would shift every later index of the concatenation
            ap.error(f"set file missing: {' '.join(missing)}")
    print("\n".join(report(sets, a.out_dir)))


if __name__ == "__main__":
    main()
