#!/usr/bin/env python3
"""T1 (mc-split) of README.md in this directory: the CLI's built-in reading table T0 (`shanjie-eval --reading-decode --dump-reading-table T0.tsv`,
rows `character TAB reading TAB t`, t = log10 P(reading | character) relative to the character's best reading, before the lambda * max(t, -c)
step) with exactly the README's three changes, in this order:

  1. equal-score characters: a character with at least two readings in the merged lexicon, all at the same score, gets t(r) = log10((m(r) + EPS) /
     (max m + EPS)) with m(r) the mass of the multi-character words of mcbpmf-data.txt that read the character r (p5.py `mc_logp`: sum of 10^score,
     one row per (reading, word) at its largest score, once per position); a character no such word reads keeps t = 0.
  2. the exemption list: t = 0 for the final particles 嗎 啦 嘛 吧 啊 呀 read in the first tone or neutral, for (波 ㄅㄛ) (癌 ㄞˊ) (和 ㄏㄜˊ)
     (麼 ㄇㄛ˙), and for B-f's own sandhi readings (一 ㄧˊ, 一 ㄧˋ, 不 ㄅㄨˊ).
  3. the 44 characters of docs/contracts/reading-aware-0.md section 3.2 ("降"): the neutral-tone reading's t = min(t, -1.0).

  build_t1.py --t0 T0.tsv --out T1.tsv [--lexicon data/lexicon] [--pack data/packs/acg-add.tsv]

The merged lexicon is what the CLI loads with `--packs acg` (mcbpmf-data.txt, then overlay-add, sandhi-add, names-add and the pack); the pairs of T0
and the lexicon's single-character pairs must be the same set or the build stops. The output is a pure function of the two inputs (T0's row order,
Python's shortest float text); the counts printed are numbers only. Exit 0 built, 1 stop.
"""
import argparse, hashlib, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
EPS = 1e-9
NEUTRAL_CAP = -1.0
TAIL_PARTICLES = "嗎啦嘛吧啊呀"
EXEMPT_PAIRS = {("波", "ㄅㄛ"), ("癌", "ㄞˊ"), ("和", "ㄏㄜˊ"), ("麼", "ㄇㄛ˙"), ("一", "ㄧˊ"), ("一", "ㄧˋ"), ("不", "ㄅㄨˊ")}
# reading-aware-0.md section 3.2 "降": characters that are never read neutral on their own (neutral only at the end of a word)
A1_CHARS = set("西公巴水太謝意思和阿夫寶弟妹爸姊嫂舅叔嬸姑婆娘奶姥娃鼓係價遮匙裳碌簍蟆蟇掇蔔罷嗦遢晢琶杷")
assert len(A1_CHARS) == 44 and not (A1_CHARS & set(TAIL_PARTICLES))


def stop(msg):
    print("STOP:", msg, file=sys.stderr)
    sys.exit(1)


def neutral(syl):
    return "˙" in syl


def first_tone(syl):
    return "˙" not in syl and syl[-1:] not in "ˊˇˋ"


def read_text(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def read_table(text):
    """-> [(char, reading, t)] in file order."""
    rows = []
    for l in text.split("\n")[:-1] if text.endswith("\n") else text.split("\n"):
        c, r, t = l.split("\t")
        if len(c) != 1:
            stop("a table row with a character that is not one")
        rows.append((c, r, float(t)))
    return rows


def write_table(rows):
    return "".join(f"{c}\t{r}\t{t!r}\n" for c, r, t in rows)


def parse_lexicon(base, overlays):
    """-> ({(syllable, char): largest score}, {(syllable, char): mc mass}) of mcbpmf-data.txt `base` plus the overlay texts (four tab fields).
    Mass as core/src/reading.rs `build_floor_mc` counts it: only `base`, words of two or more characters with one syllable per character, one row
    per (reading, word) at its largest score, summed in first-occurrence order."""
    scores, rows, at = {}, [], {}
    for l in base.split("\n"):
        if l.startswith(("#", "_")):
            continue
        f = l.split()
        if len(f) != 3 or len(f[1]) != len(f[0].split("-")):
            continue
        s = float(f[2])
        if len(f[1]) == 1:
            scores[f[0], f[1]] = max(scores.get((f[0], f[1]), -math.inf), s)
        else:
            k = (f[0], f[1])
            if k in at:
                rows[at[k]][2] = max(rows[at[k]][2], s)
            else:
                at[k] = len(rows)
                rows.append([f[0], f[1], s])
    for text in overlays:
        for l in text.split("\n"):
            f = l.split("\t")
            if len(f) == 4 and len(f[1]) == 1 and "-" not in f[0]:
                s = float(f[2])
                scores[f[0], f[1]] = max(scores.get((f[0], f[1]), -math.inf), s)
    mass = {}
    for reading, word, s in rows:
        for c, syl in zip(word, reading.split("-")):
            mass[syl, c] = mass.get((syl, c), 0.0) + 10.0 ** s
    return scores, mass


def build(t0_rows, scores, mass):
    """-> (T1 rows, counts). Rule 1 then 2 then 3, each on the previous rule's output."""
    if {(r, c) for c, r, _ in t0_rows} != set(scores):
        stop(f"T0 has {len(t0_rows)} pairs and the lexicon {len(scores)}: not the same set (T0 must come from a run with the same packs)")
    by_char = {}
    for c, r, _ in t0_rows:
        by_char.setdefault(c, []).append(r)
    equal = {c for c, rs in by_char.items() if len(rs) >= 2 and len({scores[r, c] for r in rs}) == 1}
    t = {(c, r): v for c, r, v in t0_rows}
    stages = [dict(t)]
    for c in equal:  # rule 1
        top = max(mass.get((r, c), 0.0) for r in by_char[c])
        for r in by_char[c]:
            t[c, r] = math.log10((mass.get((r, c), 0.0) + EPS) / (top + EPS)) if top > 0 else 0.0
    stages.append(dict(t))
    for c, r in t:  # rule 2
        if (c, r) in EXEMPT_PAIRS or (c in TAIL_PARTICLES and (first_tone(r) or neutral(r))):
            t[c, r] = 0.0
    stages.append(dict(t))
    for c, r in t:  # rule 3
        if c in A1_CHARS and neutral(r):
            t[c, r] = min(t[c, r], NEUTRAL_CAP)
    stages.append(dict(t))
    counts = {"pairs": len(t0_rows), "equal_score_chars": len(equal)}
    for name, a, b in zip(("rule1", "rule2", "rule3"), stages, stages[1:]):
        ch = [k for k in a if a[k] != b[k]]
        counts[name] = {"chars": len({c for c, _ in ch}), "pairs": len(ch)}
    ch = [k for k in t if t[k] != stages[0][k]]
    counts["total"] = {"chars": len({c for c, _ in ch}), "pairs": len(ch)}
    counts["abs_dt_ge_0.5"] = sum(abs(t[k] - stages[0][k]) >= 0.5 for k in t)
    counts["a1_chars_without_neutral_pair"] = sum(not any(neutral(r) for r in by_char.get(c, ())) for c in A1_CHARS)
    return [(c, r, t[c, r]) for c, r, _ in t0_rows], counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--t0", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lexicon", default=os.path.join(ROOT, "data/lexicon"))
    ap.add_argument("--pack", default=os.path.join(ROOT, "data/packs/acg-add.tsv"))
    a = ap.parse_args(argv)
    if os.path.exists(a.out):
        stop("--out exists")
    d = lambda n: read_text(os.path.join(a.lexicon, n))
    scores, mass = parse_lexicon(d("mcbpmf-data.txt"), [d("overlay-add.tsv"), d("sandhi-add.tsv"), d("names-add.tsv"), read_text(a.pack)])
    t0_text = read_text(a.t0)
    rows, counts = build(read_table(t0_text), scores, mass)
    out = write_table(rows)
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    for k, v in counts.items():
        print(k, v)
    print("T0 sha256", hashlib.sha256(t0_text.encode()).hexdigest())
    print("T1 sha256", hashlib.sha256(out.encode()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
