#!/usr/bin/env python3
"""R1g: the (syllable, character) pairs the reading term must not charge (experiments/r1-reading-decode/README.md, section R1g).

  std_exempt.py [OUT]     -> OUT (default: std-exempt.tsv next to this script), two columns: syllable, character.

Rule: the single-character rows of data/lexicon/mcbpmf-data.txt (one syllable, `_` rows skipped, the largest score of a repeated
(syllable, character)); for each character with two or more readings, the concised MOE dictionary's entry (tools/build_overlay.load_concised)
when it has exactly one syllable; that pair is exempt when the lexicon has it, scores it below the character's best reading, and not at
the floor LEX_FLOOR (-6.8, core/src/reading.rs: a floor pair is estimated from the multi-character words instead). Stops unless the
result is the 12 pairs the README registers.
"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_overlay as bo  # noqa: E402

LEXICON = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
LEX_FLOOR = -6.8
EXPECTED = {("ㄆㄛˋ", "朴"), ("ㄇㄚ˙", "嗎"), ("ㄕㄨㄞˋ", "率"), ("ㄌㄧ", "哩"), ("ㄏㄨㄢˊ", "還"), ("ㄏㄜˊ", "和"),
            ("ㄞˊ", "癌"), ("ㄌㄧㄣˋ", "賃"), ("ㄅㄛ", "波"), ("ㄒㄧㄤ", "相"), ("ㄏㄜˋ", "嚇"), ("ㄐㄧㄠˋ", "教")}


def pairs():
    scores = {}   # character -> {syllable: largest score}
    with open(LEXICON, encoding="utf-8") as f:
        for line in f:
            p = line.split()
            if line.startswith(("#", "_")) or len(p) != 3 or len(p[1]) != 1 or "-" in p[0]:
                continue
            d = scores.setdefault(p[1], {})
            d[p[0]] = max(float(p[2]), d.get(p[0], -1e9))
    concised = bo.load_concised()
    out = []
    for c, d in scores.items():
        if len(d) < 2 or c not in concised or len(concised[c].syls) != 1:
            continue
        syl = concised[c].syls[0]
        if syl in d and d[syl] < max(d.values()) and abs(d[syl] - LEX_FLOOR) >= 1e-9:
            out.append((syl, c))
    return out


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "std-exempt.tsv")
    got = pairs()
    if set(got) != EXPECTED or len(got) != len(EXPECTED):
        sys.exit(f"not the 12 registered pairs: extra {sorted(set(got) - EXPECTED)}, missing {sorted(EXPECTED - set(got))}")
    sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# python3 experiments/r1-reading-decode/std_exempt.py\n# data/lexicon/mcbpmf-data.txt sha256 {sha(LEXICON)}\n"
                f"# concised dictionary zip (dict_concised_2014_20260929.zip) sha256 {bo.CONCISED_SHA256}\n")
        for syl, c in got:
            f.write(f"{syl}\t{c}\n")
    print(f"{len(got)} pairs -> {out_path}")


if __name__ == "__main__":
    main()
