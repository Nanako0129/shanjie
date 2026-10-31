#!/usr/bin/env python3
"""L5-0 checks 1 and 2 (research log 2026-10-31, registration "L5-0 探索片登記"): the salvageable share of LOSS-0's layer 5.

  python3 experiments/l5/l5.py ROWS      ROWS: the TSV written by experiments/loss0/loss0.py --rows (private cache)

Layer-5 rows (the reranker also prefers a wrong candidate), per profile and set group:
  check 1  the reranker margin (best gold ll minus chosen ll, log10 as the core records it, <= 0) as quartiles and the registered
           bands [-1, 0), [-2, -1), < -2 (a margin of exactly 0, a tie, is counted apart); then removed in turn
           (a) a differing span covered by eval/forms.tsv or eval/variants.tsv (both spellings legal): a char pair, a whole
               run, or a word pair sitting at the same place in both strings; the forms table's reading condition is not applied, (b) RUHI-0 label undecidable, (c) a differing span with Latin
               letters or digits; what is left is the salvageable upper bound
  check 2  the rows left, split into segmentation (HOMO-0 tags compound or segmentation) and word swap (the rest)
Prints counts only.
"""
import os
import re
import sys
from collections import defaultdict

LATIN = re.compile(r"[A-Za-z0-9０-９Ａ-Ｚａ-ｚ]")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GROUPS = ["small", "cvtune", "wikitune"]
SMALL = {"dev302", "typing76", "user-reported", "fw", "probe"}


def legal_pairs():
    """Spelling pairs either table accepts, both directions: (x, y) strings (chars, runs and words)."""
    out = set()
    for line in open(f"{REPO}/eval/forms.tsv", encoding="utf-8"):
        t = line.rstrip("\n").split("\t")
        if t[0] in ("word", "char") and len(t) >= 3:
            out |= {(t[1], t[2]), (t[2], t[1])}
    for line in open(f"{REPO}/eval/variants.tsv", encoding="utf-8"):
        if line.strip() and not line.startswith("#"):
            a, b = line.rstrip("\n").split("\t")
            out |= {(a, b), (b, a)}
    return out


def spans(cand, gold):
    """The differing runs of two equal-length strings, as (cand text, gold text)."""
    out, i = [], 0
    while i < len(gold):
        if cand[i] != gold[i]:
            j = i
            while j < len(gold) and cand[j] != gold[j]:
                j += 1
            out.append((cand[i:j], gold[i:j]))
            i = j
        else:
            i += 1
    return out


def covered(sp, legal, cand="", gold=""):
    """Every differing run is a legal pair, char by char or as a whole run, or lies inside a legal word pair found at the same place in
    both strings (cand[i:i+n], gold[i:i+n]) = (a, b)."""
    words = [(a, b) for a, b in legal if len(a) == len(b) > 1]
    def in_word(run):
        x, y = run
        for a, b in words:
            i = gold.find(b)
            while i != -1:
                if cand[i:i + len(a)] == a and x in a and y in b:
                    return True
                i = gold.find(b, i + 1)
        return False
    return all(r in legal or all((a, b) in legal for a, b in zip(*r)) or in_word(r) for r in sp)


def salvageable(t, legal):
    """A rows-file line (12 fields) of layer 5 that check 1 keeps: (margin, cand, gold, label, tags) or None."""
    if len(t) != 12:
        sys.exit(f"STOP: a rows line has {len(t)} fields, not 12; rerun loss0.py --rows with the L5-0 columns")
    s, p, _, layer, _, _, lab, tags, margin, cand, gold, _ = t
    if layer != "5":
        return None
    if not margin or len(cand) != len(gold):
        sys.exit(f"STOP: a layer-5 row of {s} {p} has no margin or unequal candidate lengths")
    sp = spans(cand, gold)
    if covered(sp, legal, cand, gold) or lab == "undecidable" or any(LATIN.search(a + b) for a, b in sp):
        return None
    return float(margin), cand, gold, lab, set(tags.split(",")) - {""}


def word_swap(x):
    return not x[4] & {"compound", "segmentation"}


def bands(v):
    v = sorted(v)
    if not v:
        return "n 0"
    q = lambda x: v[min(len(v) - 1, int(x * len(v)))]
    b = [sum(1 for x in v if x == 0), sum(1 for x in v if -1 <= x < 0), sum(1 for x in v if -2 <= x < -1), sum(1 for x in v if x < -2)]
    return f"n {len(v)}, p25 {q(0.25):.2f}, median {q(0.5):.2f}, p75 {q(0.75):.2f}; tie 0 {b[0]}, [-1, 0) {b[1]}, [-2, -1) {b[2]}, < -2 {b[3]}"


def main():
    rows = [l.rstrip("\n").split("\t") for l in open(sys.argv[1], encoding="utf-8")]
    legal = legal_pairs()
    R, ALL = defaultdict(list), defaultdict(list)
    for t in rows:
        if len(t) != 12:
            sys.exit(f"STOP: a rows line has {len(t)} fields, not 12; rerun loss0.py --rows with the L5-0 columns")
        key = (t[1], "small" if t[0] in SMALL else t[0])
        if t[3] == "5":
            ALL[key].append(float(t[8]))
        x = salvageable(t, legal)
        if x:
            R[key].append(x)
    for p in ["chat", "formal"]:
        for g in GROUPS:
            v, c = ALL[(p, g)], R[(p, g)]
            if not v:
                continue
            if any(m > 0 for m in v):
                sys.exit(f"STOP: {p} {g}: a layer-5 margin is positive")
            print(f"\n== {p} {g}: layer 5 {len(v)} rows")
            print(f"  check 1 margin, all: {bands(v)}")
            print(f"  removed: {len(v) - len(c)} (legal spelling, undecidable, Latin or digits, in that order)")
            print(f"  salvageable upper bound {len(c)}; margin: {bands([m for m, *_ in c])}")
            seg = [x for x in c if not word_swap(x)]
            swap = [x for x in c if word_swap(x)]
            print(f"  check 2 segmentation {len(seg)}: {bands([m for m, *_ in seg])}")
            print(f"  check 2 word swap {len(swap)}: {bands([m for m, *_ in swap])}")
    big = sum(len(ALL[("chat", g)]) for g in ["cvtune", "wikitune"])
    print(f"\n(chat cvtune + wikitune layer-5 rows before removal: {big})")


if __name__ == "__main__":
    main()
