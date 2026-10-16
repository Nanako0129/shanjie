"""Label-accuracy sample (contract docs/contracts/reading-aware-0.md 3.4 "標注錯誤率" and the 10% stop condition of section 5).

  python3 sample_labels.py sample --out SAMPLE.tsv --seed 20261017 [--n 150] [--hetero FILE] (--wiki [--articles 200000] | --text FILE...)
  python3 sample_labels.py rate JUDGED.tsv                  exit 3 when the overall error rate is above 10% (the stop condition)

`sample` draws n sentences (a fixed-seed reservoir over the stream, so the draw does not depend on how many sentences there are) that
have at least one short-list character (得 地 還 重 空 長 行) standing alone in the lexicon-score cut of its Han run, and writes one TSV
line per such position: id, source, sentence, position, character, label, context (3 characters each side), judgment (empty).
Sentences go through the same conversion as the counting (derisk-1015/e1/count.py: OpenCC tables, markup stripping, sentence split).
Run it once per source (wiki, spoken-text files) and `cat` the two; main fills the last column with `ok` or `x` for every line.
`rate` counts every judged position (the denominator of the contract: all short-list positions standing alone in the 300 sentences) and
prints the overall error rate and each character's. A blank or other judgment is an error (exit 1).
"""
import argparse
import os
import random
import sys

import rcommon
import units
import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402
import count  # noqa: E402
import mapping  # noqa: E402

SHORT = "得地還重空長行"        # contract 3.4: the characters whose label comes from a context rule
STOP_RATE = 0.10


def candidates(sentence, labeller, lex):
    """[(position, char, label)] of the short-list characters that stand alone in the best cut of their Han run (lex = a lattice lexicon
    with by_word / max_len). Labels come from the variant-merged sentence, as in the counting."""
    if not any(c in SHORT for c in sentence):
        return []   # cheap skip before the segmentation
    folded, out, pos = bc.fold(sentence), [], 0
    for kind, raw, _ in mapping.tokenize(sentence):
        if kind == "han" and len(raw) >= 2:
            words = bc.segment(lex, raw)
            at = 0
            for w in words or ():
                if len(w) == 1 and w in SHORT:
                    out.append((pos + at, w, labeller.label(folded, pos + at, w)))
                at += len(w)
        pos += len(raw)
    return out


def reservoir(stream, n, seed):
    """Algorithm R over (item) with a fixed seed: n items, in stream order of their first selection."""
    rng, keep = random.Random(seed), []
    for i, x in enumerate(stream):
        if len(keep) < n:
            keep.append(x)
        else:
            j = rng.randrange(i + 1)
            if j < n:
                keep[j] = x
    return keep


def sentences(a):
    count.init3((), None)
    if a.wiki:
        for raw in bc.articles(a.articles):
            yield from (("wiki", s) for s in count.wiki_sentences(raw))
    else:
        for p in a.paths:
            for line in bct.lines(p):
                yield (p, bc.convert(line, *bc._W["conv"]))


def sample(a):
    ident = None if a.hetero is None else units.load_hetero(a.hetero)
    lab = units.Labeller(rcommon.char_readings(rcommon.load_lexicon()), units.load_overrides(a.overrides), ident)
    stream = ((src, s) for src, s in sentences(a) if candidates(s, lab, bc._W["lex"]))
    picked = reservoir(stream, a.n, a.seed)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        for k, (src, s) in enumerate(picked, 1):
            for pos, c, label in candidates(s, lab, bc._W["lex"]):
                ctx = s[max(0, pos - 3):pos] + "[" + c + "]" + s[pos + 1:pos + 4]
                f.write(f"{k}\t{src}\t{s}\t{pos}\t{c}\t{label}\t{ctx}\t\n")
    print(f"sample_labels: {len(picked)} sentences -> {a.out}")


def rate(lines):
    """(errors, total, {char: (errors, total)}) of judged lines; ValueError for a line whose judgment is not ok / x."""
    per, bad, total = {}, 0, 0
    for n, line in enumerate(lines, 1):
        p = line.rstrip("\n").split("\t")
        if len(p) != 8 or p[7] not in ("ok", "x"):
            raise ValueError(f"line {n}: the last column must be ok or x")
        e, t = per.get(p[4], (0, 0))
        per[p[4]] = (e + (p[7] == "x"), t + 1)
        bad += p[7] == "x"
        total += 1
    return bad, total, per


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--out", required=True)
    s.add_argument("--seed", type=int, required=True)
    s.add_argument("--n", type=int, default=150)
    s.add_argument("--hetero")
    s.add_argument("--overrides", default=os.path.join(rcommon.ROOT, "tools", "reading_overrides.tsv"))
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--wiki", action="store_true")
    g.add_argument("--text", dest="paths", nargs="+")
    s.add_argument("--articles", type=int, default=200000)
    r = sub.add_parser("rate")
    r.add_argument("judged")
    a = ap.parse_args(argv)
    if a.cmd == "sample":
        sample(a)
        return
    try:
        bad, total, per = rate(open(a.judged, encoding="utf-8"))
    except ValueError as e:
        sys.exit(f"sample_labels: {e}")
    print(f"overall\t{bad}\t{total}\t{bad / total:.4f}" if total else "overall\t0\t0\tnan")
    for c in SHORT:
        if c in per:
            print(f"{c}\t{per[c][0]}\t{per[c][1]}\t{per[c][0] / per[c][1]:.4f}")
    if total and bad / total > STOP_RATE:
        print("STOP: overall label error rate above 10% (contract section 5)", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
