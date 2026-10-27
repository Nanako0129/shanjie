#!/usr/bin/env python3
"""Sentence review of the cleaned subtitles (docs/contracts/sj-lm-7.md section 2.7; maintainer 2026-10-25, "A, subscription").

  review_subs.py prefilter SUBS_TSV OUT_DIR [--chunk 1000]   chunks of the suspect sentences: OUT_DIR/chunk-NNNN.txt, one sentence a line
  review_subs.py apply SUBS_TSV FLAGS_DIR OUT_DIR            drops the sentences named in FLAGS_DIR/*.txt; writes OUT_DIR/subs.tsv, subs-half.tsv

A suspect line holds a character traditional Chinese shares with a different mainland use (SUSPECT), a Latin letter or a subtitle mark; the
rest is kept without review (900 hand-checked lines: all 41 residues were suspects, 6.9% of the lines were). A chunk holds sentences only:
in the pilot, reviewers asked for an ID column wrote the Read tool's line positions instead, so the chunk carries no IDs. Reviewers (Sonnet
subagents) write `position<TAB>category<TAB>sentence` per flagged sentence to FLAGS_DIR/flags-*.txt; `apply` drops by the sentence text (the
third field, compared on clean_subs' dedupe key), so the flags still apply after a re-clean renumbers the lines; distinct flagged texts that
are not in SUBS_TSV are counted as `unmatched_distinct` (a miscopy, or a line a re-clean already dropped). Prints counts only.
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import clean_subs as C  # noqa: E402

# clean_subs.SHARED plus characters the proofreads found outside it (游 制 余 尸 癥 硅 征 杰 壹 冲 着 元), Latin letters and subtitle marks
SUSPECT = re.compile("[" + "".join(sorted(C.SHARED | set("游制余尸癥硅征杰壹冲着元"))) + "]|[A-Za-z*#♪♫§]")


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
    """Flags are matched on clean_subs' dedupe key (NFKC, whitespace removed, case folded): subs.tsv is unique under it, and a reviewer's copy
    with an extra space or a full-width letter still finds its sentence. Writes OUT/manifest.json with the input and every flags file."""
    rows = read_tsv(subs)
    flagged, files = set(), []
    for p in sorted(glob.glob(os.path.join(flags_dir, "*.txt"))):
        files.append({"name": os.path.basename(p), "sha256": C.sha256_file(p)})
        for l in open(p, encoding="utf-8"):
            f = l.rstrip("\r\n").split("\t")
            if len(f) >= 3 and f[0].strip().isdigit():
                flagged.add(C.P2.dedupe_key(f[2]))
    present = {C.P2.dedupe_key(r[-1]) for r in rows}
    kept = [r for r in rows if C.P2.dedupe_key(r[-1]) not in flagged]
    os.makedirs(out, exist_ok=True)
    halves = [r for r in kept if C.half(r[-1])]
    stats = {"lines": len(rows), "flagged_distinct": len(flagged & present), "unmatched_distinct": len(flagged - present), "kept": len(kept)}
    for name, part in (("subs.tsv", kept), ("subs-half.tsv", halves)):
        p = os.path.join(out, name)
        C.write_tsv(p, [(r[1], r[-1]) for r in part])
        stats[name] = {"rows": len(part), "han_chars": sum(C.P2.han_count(r[-1]) for r in part), "sha256": C.sha256_file(p)}
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"input": {"basename": os.path.basename(subs), "sha256": C.sha256_file(subs)}, "flags": files, "stats": stats}, f,
                  ensure_ascii=False, indent=1)
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
