#!/usr/bin/env python3
"""Row file for the long-context rerank latency of .github/workflows/latency-gate.yml.

The latency of one rerank depends on how long the left context is, hardly on what it says, so the rows are put together
from public rows that are already tracked: each row is `context|sentence|readings`, the sentence and its readings taken
from a source row unchanged, the context the last CTX Han characters of other source sentences joined end to end (no
punctuation, no other characters: a 64-character context is 64 tokens for the small model). Nothing is random: row i
takes source row i mod N and starts its context at a fixed stride, so the same sources give the same bytes.

  python3 scripts/ci/longctx_rows.py --out FILE [--root REPO] [--rows 3000] [--ctx 64]

Prints `rows=<n> min_context=<han characters> sha256=<of the file>` and exits 1 when a row would not meet --ctx.
"""
import argparse, hashlib, os, re, sys

# Public rows with typed readings (eval/README.md); eval/bench/suite-v1/typing76.txt is the same file as user-typing.txt.
SOURCES = ["eval/bench/suite-v1/dev302.txt", "eval/dev/user-typing.txt", "eval/dev/user-reported.txt", "eval/bench/suite-v2/fw.txt"]
HAN = re.compile(r"[㐀-䶿一-鿿]")


def han(s):
    return "".join(HAN.findall(s))


def stats(path):
    """(rows, fewest Han characters in a context, SHA-256) of a row file."""
    data = open(path, "rb").read()
    lines = data.decode("utf-8").splitlines()
    return len(lines), min((len(han(l.split("|")[0])) for l in lines), default=0), hashlib.sha256(data).hexdigest()


def build(root, rows, ctx):
    src, seen = [], set()
    for rel in SOURCES:
        for line in open(os.path.join(root, rel), encoding="utf-8").read().splitlines():
            f = line.split("|")
            if len(f) != 3 or not f[1] or not f[2] or (f[1], f[2]) in seen:
                continue
            seen.add((f[1], f[2]))
            src.append((f[1], f[2]))
    n = len(src)
    if sum(len(han(s)) for s, _ in src) < 4 * ctx:
        sys.exit("error: the source rows hold too few Han characters for a %d-character context" % ctx)
    out = []
    for i in range(rows):
        sent, reading = src[i % n]
        j = (i * 17 + 5 + (i // n) * 101) % n   # where this row's context starts; the i // n term moves it on every pass over the sources
        left = ""
        while len(left) < ctx:
            if j != i % n:
                left += han(src[j][0])
            j = (j + 1) % n
        out.append("%s|%s|%s\n" % (left[-ctx:], sent, reading))
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    ap.add_argument("--out", required=True)
    ap.add_argument("--rows", type=int, default=3000)
    ap.add_argument("--ctx", type=int, default=64)
    a = ap.parse_args()
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        f.write(build(a.root, a.rows, a.ctx))
    n, shortest, sha = stats(a.out)
    print("rows=%d min_context=%d sha256=%s" % (n, shortest, sha))
    if n != a.rows or shortest < a.ctx:
        sys.exit("error: %d rows (wanted %d), shortest context %d (wanted at least %d)" % (n, a.rows, shortest, a.ctx))


if __name__ == "__main__":
    main()
