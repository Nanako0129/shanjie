#!/usr/bin/env python3
"""T2 of README.md in this directory ("表 T2"): T0 with the labeller's counts for the characters of the usable classes.

  build_t2.py --t0 T0.tsv --labels LABELS.tsv --usable tone,seg --out T2.tsv [--chars step2-chars.txt]

T0 = `shanjie-eval --reading-decode --dump-reading-table T0.tsv`; LABELS.tsv = label_llm.py `collect` (`id, char, reading`; an empty reading is not
counted). A character changes only when its class (sample_single.classify) is in --usable and it has N >= 10 labelled positions. Then, over its readings
in T0: n_r = positions labelled r, P0(r) = 10^t0(r) / sum 10^t0, P(r) = (n_r + K P0(r)) / (N + K) with K = 5; r* = the reading with T0's t = 0 (ties:
the tools/readings.py primary); t(r*) = 0 and t(r) = min(0, log10(P(r) / P(r*))). The sandhi pairs (一 ㄧˊ) (一 ㄧˋ) (不 ㄅㄨˊ) keep T0's value. Every
other row is T0's, byte for byte, in T0's row order (T0 must be in Python's shortest float text, as build_t1.py writes it). The (character, reading)
pairs of T0 must be the merged lexicon's single-character pairs, as in build_t1.py. Prints the number of changed characters and pairs, the characters
whose largest P is not r* (the flips: standard and habit disagree) and the sha256 of T2. Exit 0 built, 1 stop.
"""
import argparse, collections, hashlib, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sample_single as S  # noqa: E402

B1 = S.B1
K = 5
MIN_N = 10
SANDHI = B1.SANDHI_PAIRS


def build(t0_rows, counts, classes, usable, primary):
    """-> (T2 rows, changed characters, flips [(char, r*, argmax reading)]). counts = {char: Counter(reading)}; classes = {char: class}; primary = {char: r*}."""
    by_char = collections.defaultdict(list)
    for c, r, t in t0_rows:
        by_char[c].append((r, t))
    new, changed, flips = {}, [], []
    for c, rts in by_char.items():
        n = counts.get(c, {})
        total = sum(n.values())
        if classes.get(c) not in usable or total < MIN_N:
            continue
        z = sum(10 ** t for _, t in rts)
        p = {r: (n.get(r, 0) + K * 10 ** t / z) / (total + K) for r, t in rts}
        star = primary[c]
        for r, _ in rts:
            if (c, r) not in SANDHI:
                new[c, r] = min(0.0, math.log10(p[r] / p[star]))   # r == star gives log10(1) = 0
        changed.append(c)
        top = max(rts, key=lambda x: p[x[0]])[0]
        if p[top] > p[star]:
            flips.append((c, star, top))
    return [(c, r, new.get((c, r), t)) for c, r, t in t0_rows], changed, flips


def read_counts(path, readings):
    """-> {char: Counter(reading)} of the valid labels; STOP on a repeated id (S.read_labels), on an id that is not a corpus id (`wiki-` / `text-`:
    calibration labels must never feed n_r) and on a reading that is not one of the character's."""
    out = collections.defaultdict(collections.Counter)
    for i, (c, r) in S.read_labels(path).items():
        if not i.startswith(S.CORPUS_PREFIXES):
            B1.stop("a label id that is not a corpus position (wiki-... or text-...)")
        if r:
            if r not in readings.get(c, ()):
                B1.stop("a label that is not a single-character reading of its character")
            out[c][r] += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--t0", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--usable", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--chars", default=S.DEFAULT_CHARS)
    a = ap.parse_args(argv)
    usable = set(a.usable.split(","))
    if not usable <= {"tone", "seg"}:
        B1.stop("--usable is a subset of tone,seg")
    if os.path.exists(a.out):
        B1.stop("--out exists")
    chars = S.load_chars(a.chars)
    readings, _ = S.load_merged(chars)
    t0_text = B1.read_text(a.t0)
    t0_rows = B1.read_table(t0_text)
    if B1.write_table(t0_rows) != t0_text:
        B1.stop("T0 is not in the shortest float text of build_t1.py write_table (its unchanged rows would not stay byte for byte)")
    pairs = S.single_pairs()
    if {(r, c) for c, r, _ in t0_rows} != pairs:
        B1.stop(f"T0 has {len(t0_rows)} pairs and the lexicon {len(pairs)}: not the same set")
    t0 = {(c, r): t for c, r, t in t0_rows}
    rows, changed, flips = build(t0_rows, read_counts(a.labels, readings), {c: S.classify(readings[c]) for c in chars}, usable,
                                 S.primary_readings(readings, t0))
    out = B1.write_table(rows)
    with open(a.out, "x", encoding="utf-8", newline="") as f:
        f.write(out)
    print("changed characters", len(changed), "changed pairs", sum(x != y for x, y in zip(rows, t0_rows)))
    print("flips (largest P is not r*):", len(flips))
    for c, star, top in flips:
        print("flip", c, "r*", star, "largest", top)
    print("T2 sha256", hashlib.sha256(out.encode()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
