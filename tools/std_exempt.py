#!/usr/bin/env python3
"""R1g: the (syllable, character) pairs the reading term must not charge (section R1g of the reading-decode work; shipped data since
docs/spec/decoder.md §5). tools/test_std_exempt.py checks that this rule still gives data/lexicon/std-exempt.tsv.

  std_exempt.py [OUT]     -> OUT (default: data/lexicon/std-exempt.tsv), two columns: syllable, character.

Rule: the single-character rows of data/lexicon/mcbpmf-data.txt (one syllable, `_` rows skipped, the largest score of a repeated
(syllable, character)); for each character with two or more readings, the concised MOE dictionary's entry (tools/build_overlay.load_concised)
when it has exactly one syllable; that pair is exempt when the lexicon has it, scores it below the character's best reading, and not at
the floor LEX_FLOOR (-6.8, core/src/reading.rs: a floor pair is estimated from the multi-character words instead).

Second rule (the only reading of the dictionary): a character the revised MOE dictionary (Moedict's dict-revised, SHANJIE_MOEDICT, the file
tools/readings.py reads) gives exactly one reading, when the lexicon has that reading AT the floor and scores another reading of the character
above it. The lexicon then has no count for the one reading the dictionary knows, and the multi-character estimate would charge everyone who
types it (ㄏㄞ 嗨: the lexicon counts ㄏㄞˋ; row 188 of eval/dev/user-reported.txt). These pairs follow the first rule's in the file.

Stops unless the result is the 12 + 4 pairs registered below.
"""
import json
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_overlay as bo  # noqa: E402
import readings  # noqa: E402  SHANJIE_MOEDICT

LEXICON = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
LEX_FLOOR = -6.8
EXPECTED = {("ㄆㄛˋ", "朴"), ("ㄇㄚ˙", "嗎"), ("ㄕㄨㄞˋ", "率"), ("ㄌㄧ", "哩"), ("ㄏㄨㄢˊ", "還"), ("ㄏㄜˊ", "和"),
            ("ㄞˊ", "癌"), ("ㄌㄧㄣˋ", "賃"), ("ㄅㄛ", "波"), ("ㄒㄧㄤ", "相"), ("ㄏㄜˋ", "嚇"), ("ㄐㄧㄠˋ", "教")}
EXPECTED_SOLE = {("ㄅㄛˋ", "播"), ("ㄏㄞ", "嗨"), ("ㄐㄧㄣˇ", "儘"), ("ㄐㄩ", "据")}   # the second rule's pairs


def sole_readings(path=None):
    """Character -> its reading, for the single-character entries of the revised dictionary whose heteronyms all give one and the same syllable
    (a label in full-width parentheses, as （語音）, is dropped; the neutral tone is written after the syllable as the lexicon does)."""
    out = {}
    with open(path or readings.need(readings.MOEDICT, "SHANJIE_MOEDICT"), encoding="utf-8") as f:
        for e in json.load(f):
            t = e.get("title") or ""
            if len(t) != 1:
                continue
            syls = {tuple(x[1:] + "˙" if x.startswith("˙") else x for x in re.sub(r"（[^）]*）", "", h.get("bopomofo") or "").replace("　", " ").split())
                    for h in e.get("heteronyms") or []}
            out[t] = out.get(t, set()) | (syls - {()})   # a heteronym without a reading is not a second reading
    return {c: next(iter(s))[0] for c, s in out.items() if len(s) == 1 and len(next(iter(s))) == 1}


def lexicon_scores():
    scores = {}   # character -> {syllable: largest score}
    with open(LEXICON, encoding="utf-8") as f:
        for line in f:
            p = line.split()
            if line.startswith(("#", "_")) or len(p) != 3 or len(p[1]) != 1 or "-" in p[0]:
                continue
            d = scores.setdefault(p[1], {})
            d[p[0]] = max(float(p[2]), d.get(p[0], -1e9))
    return scores


def pairs():
    scores = lexicon_scores()
    concised = bo.load_concised()
    out = []
    for c, d in scores.items():
        if len(d) < 2 or c not in concised or len(concised[c].syls) != 1:
            continue
        syl = concised[c].syls[0]
        if syl in d and d[syl] < max(d.values()) and abs(d[syl] - LEX_FLOOR) >= 1e-9:
            out.append((syl, c))
    return out


def sole_pairs(sole=None):
    """The second rule: the dictionary's only reading of the character is in the lexicon at the floor, under another reading of the character."""
    sole = sole_readings() if sole is None else sole
    return [(sole[c], c) for c, d in lexicon_scores().items()
            if len(d) >= 2 and sole.get(c) in d and abs(d[sole[c]] - LEX_FLOOR) < 1e-9 and max(d.values()) > LEX_FLOOR]


HEADER = "# python3 experiments/r1-reading-decode/std_exempt.py"   # the first line of the shipped file, kept as it was written (the script moved to tools/)


def render(got):
    """The file's text for the pairs `got` (the header lines name the two sources by their hashes)."""
    sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
    return (f"{HEADER}\n# data/lexicon/mcbpmf-data.txt sha256 {sha(LEXICON)}\n"
            f"# concised dictionary zip (dict_concised_2014_20260929.zip) sha256 {bo.CONCISED_SHA256}\n"
            f"# the last {len(EXPECTED_SOLE)} rows: the only reading of the revised dictionary (dict-revised, g0v/moedict-data), at the lexicon's floor\n"
            + "".join(f"{syl}\t{c}\n" for syl, c in got))


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "lexicon", "std-exempt.tsv")
    got, sole = pairs(), sole_pairs()
    for name, g, want in (("12", got, EXPECTED), ("4 only-reading", sole, EXPECTED_SOLE)):
        if set(g) != want or len(g) != len(want):
            sys.exit(f"not the {name} registered pairs: extra {sorted(set(g) - want)}, missing {sorted(want - set(g))}")
    got += sole
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(render(got))
    print(f"{len(got)} pairs -> {out_path}")


if __name__ == "__main__":
    main()
