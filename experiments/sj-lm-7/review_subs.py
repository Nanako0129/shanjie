#!/usr/bin/env python3
"""Sentence review of the cleaned subtitles (docs/contracts/sj-lm-7.md section 2.7; maintainer 2026-10-25, "A, subscription").

  review_subs.py prefilter SUBS_TSV OUT_DIR [--chunk 1000]   chunks of the suspect sentences: OUT_DIR/chunk-NNNN.txt, one sentence a line
  review_subs.py apply SUBS_TSV FLAGS_DIR OUT_DIR            drops the sentences named in FLAGS_DIR/*.txt; writes OUT_DIR/subs.tsv, subs-half.tsv

A suspect line holds a character traditional Chinese shares with a different mainland use (SUSPECT), a Latin letter or a subtitle mark; the
rest is kept without review (900 hand-checked lines: all 41 residues were suspects, 6.9% of the lines were). A chunk holds sentences only:
in the pilot, reviewers asked for an ID column wrote the Read tool's line positions instead, so the chunk carries no IDs. Reviewers (Sonnet
subagents) write `position<TAB>category<TAB>sentence` per flagged sentence to FLAGS_DIR/flags-*.txt; `apply` drops by the sentence text (the
third field), so the flags still apply after a re-clean renumbers the lines, and counts flag lines whose text is not a chunk sentence of
SUBS_TSV as `unmatched` (a miscopy). Prints counts only, never sentence text.
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
    sus = [r[-1] for r in rows if SUSPECT.search(r[-1])]
    os.makedirs(out, exist_ok=True)
    for i in range(0, len(sus), chunk):
        with open(os.path.join(out, f"chunk-{i // chunk:04d}.txt"), "w", encoding="utf-8") as f:
            f.writelines(s + "\n" for s in sus[i:i + chunk])
    print(json.dumps({"lines": len(rows), "suspects": len(sus), "chunks": (len(sus) + chunk - 1) // chunk}))


def apply(subs, flags_dir, out):
    rows = read_tsv(subs)
    flagged = set()
    for p in sorted(glob.glob(os.path.join(flags_dir, "*.txt"))):
        for l in open(p, encoding="utf-8"):
            f = l.rstrip("\n").split("\t")
            if len(f) >= 3 and f[0].strip().isdigit():
                flagged.add(f[2])
    present = {r[-1] for r in rows}
    unmatched = len(flagged - present)
    kept = [r for r in rows if r[-1] not in flagged]
    os.makedirs(out, exist_ok=True)
    halves = [r for r in kept if C.half(r[-1])]
    stats = {"lines": len(rows), "flagged": len(flagged & present), "unmatched": unmatched, "kept": len(kept)}
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
    p.add_argument("subs"); p.add_argument("out"); p.add_argument("--chunk", type=int, default=1000)
    q = sub.add_parser("apply")
    q.add_argument("subs"); q.add_argument("flags"); q.add_argument("out")
    a = ap.parse_args()
    prefilter(a.subs, a.out, a.chunk) if a.cmd == "prefilter" else apply(a.subs, a.flags, a.out)
