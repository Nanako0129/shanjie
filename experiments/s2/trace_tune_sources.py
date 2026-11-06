"""Where do the TWPhrases-looking words in a built tune file come from? (docs/contracts/tune-no-twphrases.md section 2, first bullet)

For the rows of a tune file whose sentence contains 區域性／分割槽／擴充套件／指令碼／許可權 (override with --words), re-read the same sources with the same
split and range as build_tune.py and print which raw source sentence each such clause came from, and whether convert() itself treats that raw sentence
as Simplified (build_counts.is_simplified, the test convert() applies). Only a Simplified source sentence (or a marker-only one, which is_simplified
also calls Simplified) gets the mainland-term TWPhrases subset of tw-conv.tsv, so a hit that traces to a Traditional sentence is a failure (contract section 4).

  python3 experiments/s2/trace_tune_sources.py --part cv   --tune cvtune-tw0.txt
  python3 experiments/s2/trace_tune_sources.py --part wiki --tune wikitune-tw0.txt [--wiki-range 300000 305000]

The wiki pass reads the dump again (single process, as long as the build's read). That pass does not need the tune
file, so it can run next to the build: `--collect HITS` scans and writes every source clause that contains one of the words (no --tune), and later
`--tune FILE --hits HITS` joins that file to the rows without reading the sources. Output, tab separated, to
stdout: `hit<TAB>word<TAB>tune line<TAB>source<TAB>simplified|traditional<TAB>raw sentence`, then `count<TAB>word<TAB>rows<TAB>from simplified<TAB>from traditional`,
`no-source<TAB>rows` for rows no source produced (the build parameters do not match), and `traditional-sources<TAB>N`. The rows are public text (Common Voice,
Tatoeba, Wikipedia) but the file is a private tune file: keep the output with it. Exit status 0: every occurrence traces to a Simplified sentence,
1: some trace to a Traditional one, 2: some row has no source.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_counts as bc  # noqa: E402
import build_tune as bt  # noqa: E402

WORDS = ("區域性", "分割槽", "擴充套件", "指令碼", "許可權")


def tune_rows(path, words):
    """{sentence: [1-based line numbers]} for the rows whose sentence contains one of the words (line numbers count '\\n' only)."""
    out = {}
    with open(path, encoding="utf-8", newline="") as f:
        for n, line in enumerate(f, 1):
            parts = line.rstrip("\n").split("|")
            if len(parts) >= 2 and any(w in parts[1] for w in words):
                out.setdefault(parts[1], []).append(n)
    return out


def cv_sources(conv):
    """(source label, raw sentence, clauses) for every tune-side source line, converted as build_tune does (native sources are not converted)."""
    for n, (raw, tuned, kind) in enumerate(bt.tune_sentences(bt.cv_files()), 1):
        if tuned:
            yield f"cv:{n}", raw, bt.HAN.findall(raw if bc.check_kind(kind) == "native" else bc.convert(raw, *conv))


def wiki_sources(conv, lo, hi, progress=0):
    for i, raw in enumerate(bt.articles_in(lo, hi, progress), lo):
        for s in bc.SENT.findall(bt.wiki_markup(raw)):
            yield f"wiki:{i}", s, bt.wiki_clauses(s, conv, False)


def trace(part, want, conv, lo=300_000, hi=305_000, progress=0):
    """-> [(clause, source label, raw sentence, simplified?)] for every source clause for which want(clause) is true."""
    src = cv_sources(conv) if part == "cv" else wiki_sources(conv, lo, hi, progress)
    return [(c, label, raw, bc.is_simplified(raw)) for label, raw, clauses in src for c in clauses if want(c)]


def clean(s):
    return s.replace("\t", " ").replace("\n", " ")


def load_hits(path):
    """The --collect file: label, 1 if the raw sentence is Simplified, clause, raw sentence."""
    out = []
    for line in open(path, encoding="utf-8"):
        label, simp, c, raw = line.rstrip("\n").split("\t", 3)
        out.append((c, label, raw, simp == "1"))
    return out


def main(argv=None, conv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--part", choices=["cv", "wiki"], required=True)
    ap.add_argument("--tune", help="the built tune file (before or after the guard); not needed with --collect")
    ap.add_argument("--collect", metavar="HITS", help="scan the sources only: write every source clause that contains one of the words to HITS (never overwrites)")
    ap.add_argument("--hits", metavar="HITS", help="with --tune: take the sources' hits from a --collect file instead of scanning")
    ap.add_argument("--wiki-range", type=int, nargs=2, default=[300_000, 305_000], metavar=("LO", "HI"))
    ap.add_argument("--progress-every", type=int, default=0, metavar="N", help="a line on stderr every N articles read (build_tune's option)")
    ap.add_argument("--words", nargs="+", default=list(WORDS))
    a = ap.parse_args(argv)
    if bool(a.tune) == bool(a.collect) or (a.hits and not a.tune):
        ap.error("give --tune (optionally with --hits), or --collect alone")
    scan = lambda want: trace(a.part, want, conv or bc.load_conv(), *a.wiki_range, a.progress_every)
    if a.collect:
        hits = scan(lambda c: any(w in c for w in a.words))
        with open(a.collect, "x", encoding="utf-8", newline="\n") as f:
            f.write("".join(f"{label}\t{int(simp)}\t{c}\t{clean(raw)}\n" for c, label, raw, simp in hits))
        print(f"collected\t{len(hits)}")
        return 0
    rows = tune_rows(a.tune, a.words)
    hits = [h for h in load_hits(a.hits) if h[0] in rows] if a.hits else scan(lambda c: c in rows)
    seen = {c for c, *_ in hits}
    for c, label, raw, simp in sorted(hits, key=lambda h: (rows[h[0]][0], h[1])):
        for w in a.words:
            if w in c:
                print("hit", w, ",".join(map(str, rows[c])), label, "simplified" if simp else "traditional", clean(raw), sep="\t")
    for w in a.words:
        ws = [c for c in rows if w in c]
        bad = {c for c, _, _, simp in hits if w in c and not simp}
        good = {c for c, _, _, simp in hits if w in c and simp} - bad
        print("count", w, sum(len(rows[c]) for c in ws), sum(len(rows[c]) for c in good), sum(len(rows[c]) for c in bad), sep="\t")
    missing = sum(len(v) for c, v in rows.items() if c not in seen)
    n_trad = len({(c, label) for c, label, _, simp in hits if not simp})
    print("no-source", missing, sep="\t")
    print("traditional-sources", n_trad, sep="\t")
    return 1 if n_trad else 2 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
