"""docs/contracts/news-corpus.md section 3.2 / 3.6: split zhwikinews by article title, export one side as plain text.

is_validation(title): first byte of SHA-256(title as UTF-8) < 32 (about 1 in 8). The validation side feeds the private news eval sets;
the train side feeds counting. Decided before any data was fetched; both sides call this one function.

  python3 experiments/sj-lm-7/wikinews.py export --dump DUMP.xml.bz2 --split train|val --out FILE [--no-trad-tw-phrases]

FILE: one Han run (2+ characters) per line, in dump order, from build_counts.article_runs (entities, TEMPLATE x3, MARKUP, SENT, conversion,
HAN: the one copy of the pipeline). build_counts_text.py and holdout_guard.py report --text read it as is. The text is already converted, so
those readers convert it a second time; the conversion is idempotent on converted text apart from OpenCC phrase chains (not measured).
Pass --no-trad-tw-phrases here when the counting run will.
"""
import argparse
import bz2
import hashlib
import html
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "s2"))
import build_counts as bc  # noqa: E402

THRESHOLD = 32
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_TEXT = re.compile(r"<text[^>]*>(.*?)</text>", re.S)


def is_validation(title):
    return hashlib.sha256(title.encode("utf-8")).digest()[0] < THRESHOLD


def pages(dump):
    """(title, raw wikitext) of every main-namespace non-redirect page, in dump order: build_counts.articles' filter, plus the title it drops."""
    buf = ""
    with bz2.open(dump, "rt", encoding="utf-8") as f:
        for line in f:
            buf += line
            if "</page>" in line:
                if "<ns>0</ns>" in buf and "#REDIRECT" not in buf.upper() and "#重定向" not in buf:
                    t, m = _TITLE.search(buf), _TEXT.search(buf)
                    if t and m:
                        yield html.unescape(t.group(1)).strip(), m.group(1)
                buf = ""


def export(dump, split, out, trad_tw_phrases=True):
    """Returns (articles written, runs written)."""
    conv = bc.load_conv()
    want_val = split == "val"
    arts = runs = 0
    with open(out + ".tmp", "w", encoding="utf-8") as f:
        for title, raw in pages(dump):
            if is_validation(title) != want_val:
                continue
            arts += 1
            for r in bc.article_runs(raw, conv, trad_tw_phrases=trad_tw_phrases):
                if len(r) >= 2:
                    f.write(r + "\n")
                    runs += 1
    os.replace(out + ".tmp", out)
    return arts, runs


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--dump", required=True)
    e.add_argument("--split", required=True, choices=["train", "val"])
    e.add_argument("--out", required=True)
    e.add_argument("--no-trad-tw-phrases", action="store_true", help="same flag as build_counts.py")
    a = ap.parse_args(argv)
    arts, runs = export(a.dump, a.split, a.out, not a.no_trad_tw_phrases)
    print(f"{a.split}: {arts} articles, {runs} runs")   # numbers only


if __name__ == "__main__":
    main()
