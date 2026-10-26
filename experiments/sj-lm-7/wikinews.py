"""docs/contracts/news-corpus.md section 3.2 / 3.6: split zhwikinews by article title, export one side.

is_validation(title): first byte of SHA-256(title as UTF-8) < 32 (about 1 in 8). The validation side feeds the private news eval sets;
the train side feeds counting. Decided before any data was fetched; both sides call this one function.

  python3 experiments/sj-lm-7/wikinews.py export --dump DUMP.xml.bz2 --split train|val --out FILE [--format text|rows]

Nothing is converted here (no OpenCC, no TWPhrases): the reader converts, exactly once and per sentence, as build_counts does.
  --format text (default; for counting and holdout_guard report --text): one SENT sentence per line, after the entity unescape, TEMPLATE x3
      and MARKUP steps of build_counts.article_text, before convert(). build_counts_text.py on this file counts what build_counts.py counts
      on the same articles (test_wikinews.py checks the counts are equal).
  --format rows (for the validation sampling of sections 2.1-2.3; use a .tsv name): `title<TAB>revid<TAB>paragraph`, one paragraph (a line of
      build_counts.article_text) per row in article and paragraph order, punctuation, digits and Latin intact. Clause splitting, filters,
      context and readings are the sampling step's job. Tabs and newlines inside a field become a space.
"""
import argparse
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "s2"))
import build_counts as bc  # noqa: E402

THRESHOLD = 32


def is_validation(title):
    return hashlib.sha256(title.encode("utf-8")).digest()[0] < THRESHOLD


def _flat(t):
    return re.sub(r"[\t\r\n]+", " ", t)


def export(dump, split, out, fmt="text"):
    """Returns (articles written, lines written)."""
    want_val = split == "val"
    arts = lines = 0
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        for title, revid, raw in bc.articles(10**12, dump=dump, meta=True):
            if is_validation(title) != want_val:
                continue
            arts += 1
            body = bc.article_text(raw)
            if fmt == "rows":
                for p in body.split("\n"):
                    if p.strip():
                        f.write(f"{_flat(title)}\t{revid}\t{_flat(p.strip())}\n")
                        lines += 1
            else:
                for s in bc.SENT.findall(body):
                    if s.strip():
                        f.write(s + "\n")
                        lines += 1
    os.replace(out + ".tmp", out)
    return arts, lines


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--dump", required=True)
    e.add_argument("--split", required=True, choices=["train", "val"])
    e.add_argument("--out", required=True)
    e.add_argument("--format", choices=["text", "rows"], default="text")
    a = ap.parse_args(argv)
    if a.format == "rows" and not a.out.endswith(".tsv"):
        ap.error("--format rows writes a TSV: use a .tsv name")
    arts, lines = export(a.dump, a.split, a.out, a.format)
    print(f"{a.split}: {arts} articles, {lines} lines")   # numbers only


if __name__ == "__main__":
    main()
