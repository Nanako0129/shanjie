#!/usr/bin/env python3
"""Drop training-text lines that contain a held-out sentence (docs/contracts/holdout-refresh.md section 8).

Hash definition: SHA-256 (lowercase hex) of the sentence field only, UTF-8, only the trailing newline
removed, no other normalisation. The hash file has one hash per line and no text.

  holdout_guard.py --hashes HASHFILE INPUT [--out OUTPUT] [--report-matches FILE]
      Streams INPUT line by line. A line is split into clauses at the five separators, each clause is
      whitespace-stripped and hashed; if any hash is in HASHFILE the whole line is dropped. Other lines
      are written byte for byte (to OUTPUT, or stdout). The dropped-line count goes to stderr.
      --report-matches FILE also writes the distinct matched hashes, sorted, one per line.
  holdout_guard.py --hash-sentences ROWS
      Prints the hash of the sentence field of each `context|sentence|reading` row, one per line.

Standard library only; memory is the hash set plus one line.
"""
import argparse
import hashlib
import re
import sys

SEPARATORS = re.compile("[。！？；，]")  # same split rule as contract section 3


def h(sentence: str) -> str:
    return hashlib.sha256(sentence.encode("utf-8")).hexdigest()


def line_hashes(raw: bytes):
    text = raw.decode("utf-8", errors="surrogateescape")
    return [h(c.strip()) for c in SEPARATORS.split(text)]  # empty clauses hash too; harmless unless listed


def guard(src, dst, hashes, matched):
    """Copy src to dst (binary), dropping matching lines. Returns the number dropped."""
    dropped = 0
    for raw in src:
        hit = [x for x in line_hashes(raw) if x in hashes]
        if hit:
            dropped += 1
            matched.update(hit)
        elif dst is not None:
            dst.write(raw)
    return dropped


def hash_sentences(src):
    for raw in src:
        s = raw.decode("utf-8").rstrip("\n").split("|")[1]
        yield h(s)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hashes")
    ap.add_argument("--out")
    ap.add_argument("--report-matches", metavar="FILE")
    ap.add_argument("--hash-sentences", metavar="ROWS")
    ap.add_argument("input", nargs="?")
    a = ap.parse_args(argv)
    if a.hash_sentences:
        with open(a.hash_sentences, "rb") as f:
            for x in hash_sentences(f):
                print(x)
        return 0
    if not (a.hashes and a.input):
        ap.error("--hashes and INPUT are required")
    with open(a.hashes, encoding="ascii") as f:
        hashes = {l.strip() for l in f if l.strip()}
    matched = set()
    dst = open(a.out, "wb") if a.out else sys.stdout.buffer
    with open(a.input, "rb") as src:
        dropped = guard(src, dst, hashes, matched)
    if a.out:
        dst.close()
    else:
        dst.flush()
    if a.report_matches:
        with open(a.report_matches, "w", encoding="ascii") as f:
            f.writelines(x + "\n" for x in sorted(matched))
    print(f"dropped {dropped} line(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
