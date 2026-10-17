#!/usr/bin/env python3
"""New-word B revision one (docs/contracts/newword-b.md §3.1): classify the rows the baseline got right and new-word B
got wrong, printing counts only.

Inputs are the `NEWWORD_DUMP` files of the two runs of `measure_newword` (`row\\tok\\tshown\\tgold`, same replay set),
the learned words that `NEWWORD_SHOW=1` printed (lines `newword\\t<reading>\\t<word>`, reading joined by `-`; other lines
are ignored, so the test's whole stdout can be passed), and the lexicon the engine loaded (contract §2.2 item 3: base,
overlay-add.tsv, sandhi-add.tsv, names-add.tsv, plus any pack files given), at the file scores.

For a broken row, every place where a learned word stands in the new output and the gold text there differs is a
cover. Classes, the first that applies:
  (i)   a cover of a lexicon word scoring above the shadow (default -6.5, NEWWORD_SHADOW);
  (ii)  a cover of a lexicon word scoring at or below it;
  (iii) a cover of a string the lexicon lacks under that reading (e.g. two homophones taught, 王斌 / 王彬);
  (iv)  no learned word covers anything in the row (an indirect effect).
The outputs hold sentence text: they stay on the machine that made them (contract §5). This prints no text.

ponytail: the gold span's reading is taken to be the learned word's reading (both stand on the same syllables when
the decoder put the word there); a learned word that appears in the output by coincidence of another reading would be
misread, upgrade by passing the rows file if that ever matters.
"""
import argparse
import os
import sys

SHADOW = -6.5


def load_lexicon(lexicon_dir, packs=()):
    """{(reading, word): best file score} over the engine's lexicon files."""
    lex = {}

    def put(r, w, s):
        if len(w) == r.count("-") + 1:
            lex[(r, w)] = max(s, lex.get((r, w), s))

    with open(os.path.join(lexicon_dir, "mcbpmf-data.txt"), encoding="utf-8") as f:
        for line in f:
            if line[:1] in ("#", "_"):
                continue
            p = line.split()
            if len(p) == 3:
                put(p[0], p[1], float(p[2]))
    files = [os.path.join(lexicon_dir, n) for n in ("overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv")] + list(packs)
    for path in files:
        with open(path, encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) >= 3:
                    put(p[0], p[1], float(p[2]))
    return lex


def read_dump(path):
    rows = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            row, ok, shown, gold = line.rstrip("\n").split("\t")
            rows[int(row)] = (ok == "1", shown, gold)
    return rows


def read_words(path):
    with open(path, encoding="utf-8") as f:
        return [tuple(l.rstrip("\n").split("\t")[1:3]) for l in f if l.startswith("newword\t")]


def classify(shown, gold, words, lex, shadow=SHADOW):
    """The class of one broken row: 'i', 'ii', 'iii' or 'iv'."""
    best = "iv"
    for reading, word in words:
        n = len(word)
        for p in range(len(shown) - n + 1):
            if shown[p:p + n] != word or gold[p:p + n] == word:
                continue
            score = lex.get((reading, gold[p:p + n]))
            c = "iii" if score is None else ("i" if score > shadow else "ii")
            best = min(best, c, key=["i", "ii", "iii", "iv"].index)
    return best


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("base", help="NEWWORD_DUMP of the baseline run")
    ap.add_argument("new", help="NEWWORD_DUMP of the new-word B run")
    ap.add_argument("words", help="NEWWORD_SHOW output of the new-word B run")
    ap.add_argument("--lexicon-dir", default=os.path.join(os.path.dirname(__file__), "..", "..", "data", "lexicon"))
    ap.add_argument("--pack", action="append", default=[], help="a pack file the engine loaded (repeatable)")
    ap.add_argument("--shadow", type=float, default=SHADOW)
    a = ap.parse_args(argv)
    base, new = read_dump(a.base), read_dump(a.new)
    if base.keys() != new.keys():
        sys.exit("the two dumps cover different rows")
    words = read_words(a.words)
    lex = load_lexicon(a.lexicon_dir, a.pack)
    counts = {c: 0 for c in ("i", "ii", "iii", "iv")}
    for row, (ok, _, _) in base.items():
        nok, shown, gold = new[row]
        if ok and not nok:
            counts[classify(shown, gold, words, lex, a.shadow)] += 1
    print("broken\t%d" % sum(counts.values()))
    for c, n in counts.items():
        print("(%s)\t%d" % (c, n))


if __name__ == "__main__":
    main()
