#!/usr/bin/env python3
"""Sentence review of the cleaned subtitles (docs/contracts/sj-lm-7.md section 2.7; maintainer 2026-10-25, "A, subscription").

  review_subs.py prefilter SUBS_TSV OUT_DIR [--chunk 2000]   chunks of the suspect lines: OUT_DIR/chunk-NNNN.txt, `line<TAB>sentence`
  review_subs.py apply SUBS_TSV FLAGS_DIR OUT_DIR            drops the lines named in FLAGS_DIR/*.txt; writes OUT_DIR/subs.tsv, subs-half.tsv

A suspect line holds a character traditional Chinese shares with a different mainland use (SUSPECT), a Latin letter or a subtitle mark; the
rest is kept without review (900 hand-checked lines: all 41 residues were suspects, 6.9% of the lines were). Reviewers (Sonnet subagents) write
one flagged line number per line to FLAGS_DIR/flags-NNNN.txt, optionally followed by a tab and a category; `apply` reads only the numbers.
Line numbers are 1-based positions in SUBS_TSV. Prints counts only, never sentence text (subtitles are public, but the counts are the record).
"""
import argparse
import glob
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import clean_subs as C  # noqa: E402

SUSPECT = re.compile("[里么后于游制余尸伙系干万愿癥硅准征杰壹范采松斗冲着元]|[A-Za-z*#♪♫§]")


def read_tsv(path):
    with open(path, encoding="utf-8") as f:
        return [l.rstrip("\n").split("\t") for l in f]


def prefilter(subs, out, chunk):
    rows = read_tsv(subs)
    sus = [(n, r[-1]) for n, r in enumerate(rows, 1) if SUSPECT.search(r[-1])]
    os.makedirs(out, exist_ok=True)
    for i in range(0, len(sus), chunk):
        with open(os.path.join(out, f"chunk-{i // chunk:04d}.txt"), "w", encoding="utf-8") as f:
            f.writelines(f"{n}\t{s}\n" for n, s in sus[i:i + chunk])
    print(json.dumps({"lines": len(rows), "suspects": len(sus), "chunks": (len(sus) + chunk - 1) // chunk}))


def apply(subs, flags_dir, out):
    rows = read_tsv(subs)
    flagged = set()
    for p in sorted(glob.glob(os.path.join(flags_dir, "*.txt"))):
        for l in open(p, encoding="utf-8"):
            m = re.match(r"\s*(\d+)", l)
            if m:
                flagged.add(int(m.group(1)))
    bad = sorted(n for n in flagged if not 1 <= n <= len(rows))
    if bad:
        raise SystemExit(f"STOP: flagged line numbers outside 1..{len(rows)}: {bad[:5]}")
    kept = [r for n, r in enumerate(rows, 1) if n not in flagged]
    os.makedirs(out, exist_ok=True)
    halves = [r for r in kept if C.half(r[-1])]
    stats = {"lines": len(rows), "flagged": len(flagged), "kept": len(kept)}
    for name, part in (("subs.tsv", kept), ("subs-half.tsv", halves)):
        p = os.path.join(out, name)
        with open(p, "w", encoding="utf-8") as f:
            f.writelines("\t".join(r) + "\n" for r in part)
        stats[name] = {"rows": len(part), "han_chars": sum(C.P2.han_count(r[-1]) for r in part),
                       "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest()}
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(usage=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prefilter")
    p.add_argument("subs"); p.add_argument("out"); p.add_argument("--chunk", type=int, default=2000)
    q = sub.add_parser("apply")
    q.add_argument("subs"); q.add_argument("flags"); q.add_argument("out")
    a = ap.parse_args()
    prefilter(a.subs, a.out, a.chunk) if a.cmd == "prefilter" else apply(a.subs, a.flags, a.out)
