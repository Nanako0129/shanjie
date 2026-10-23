#!/usr/bin/env python3
"""The calibration of step 2 (README.md in this directory): can the labeller be used, per character class? Counts only (readings, characters, numbers).

  calib.py agree HUMAN.tsv LABELS.tsv [--t0 T0.tsv] [--manual JUDGED.tsv] [--chars step2-chars.txt]
  calib.py manual-sample POSITIONS.tsv LABELS.tsv [--n 150] [--seed 20261023] --out judge.tsv [--chars step2-chars.txt]
  calib.py manual-rate judge.tsv

HUMAN.tsv = sample_single.py `human` (`id, source, sentence, pos, char, human, tool`); LABELS.tsv = label_llm.py `collect` (`id, char, reading`).
`agree`: per class (`tone` / `seg`, sample_single.classify) the positions the labeller answered, the labeller's and tools/readings.py's agreement with the
human reading on those same positions, and the verdict: usable = positions >= 20 and labeller agreement >= tool agreement (a tie counts). Then the
confusion on the positions whose human reading is not the character's primary reading r* (T0's t = 0 reading, given by --t0; without --t0 the
tools/readings.py primary): per (class, character, human reading, labelled reading) the count. Prints `BOTH UNUSABLE: stop` when neither class is
usable. With --manual JUDGED.tsv (the manual-rate input) seg is usable only if its agreement passes AND the manual error rate is <= 10%; the last
line is then `usable: <classes>`, the --usable argument of build_t2.py. Without --manual the last line is `usable by agreement only (seg still needs
manual-rate): <classes>` and must not be passed on. Exit 0 for any judged result.
`manual-sample`: seg-class positions with a valid label, drawn with sample_labels.reservoir (n, seed), written as sample_labels.py's eight columns
(`id, source, sentence, pos, char, label, context, judgment` with the judgment empty) for main to fill with `ok` or `x`.
`manual-rate`: sample_labels.rate on the judged file; prints the error rate; exit 3 when it is above 10% (seg is then unusable), exit 1 on a blank or
other judgment.
"""
import argparse, collections, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sample_single as S  # noqa: E402

SL = S.SL
MIN_POSITIONS = 20


def rate_of(path):
    with open(path, encoding="utf-8") as f:
        return SL.rate(f)


def agree(rows, labels, classes, primary):
    """-> ({class: stats}, confusion). rows = human.tsv rows (id, source, sentence, pos, char, human, tool); only positions with a valid label count.
    stats = positions, labeller == human, tool == human."""
    stats = {c: {"n": 0, "lab": 0, "tool": 0} for c in ("tone", "seg")}
    conf = collections.Counter()
    for id_, _, _, _, char, human, tool in rows:
        lab = labels.get(id_, (char, ""))[1]
        if not lab:
            continue
        k = classes[char]
        s = stats[k]
        s["n"] += 1
        s["lab"] += lab == human
        s["tool"] += tool == human
        if human != primary[char]:
            conf[k, char, human, lab] += 1
    for s in stats.values():
        s["usable"] = s["n"] >= MIN_POSITIONS and s["lab"] >= s["tool"]
    return stats, conf


def main_agree(a):
    chars = S.load_chars(a.chars)
    readings, _ = S.load_merged(chars)
    classes = {c: S.classify(readings[c]) for c in chars}
    t0 = {(c, r): t for c, r, t in S.B1.read_table(S.B1.read_text(a.t0))} if a.t0 else None
    primary = S.primary_readings(readings, t0)
    rows = [r for r in S.read_positions(a.human)]
    if any(len(r) != 7 for r in rows):
        S.B1.stop("HUMAN.tsv must have seven columns (sample_single.py human)")
    stats, conf = agree(rows, S.read_labels(a.labels), classes, primary)
    print("primary from", "T0 (t = 0, ties: tools/readings.py)" if a.t0 else "tools/readings.py (no --t0)")
    for k, s in stats.items():
        n = max(s["n"], 1)
        print(f"{k}\tpositions {s['n']}\tlabeller {s['lab']}/{s['n']} = {s['lab'] / n:.4f}\ttool {s['tool']}/{s['n']} = {s['tool'] / n:.4f}"
              f"\t{'USABLE' if s['usable'] else 'UNUSABLE'}")
    print("confusion (human reading is not r*): class, character, human, labelled, count")
    for (k, c, h, lab), n in sorted(conf.items()):
        print(f"{k}\t{c}\t{h}\t{lab}\t{n}")
    if a.manual:
        try:
            bad, total, _ = rate_of(a.manual)
        except ValueError as e:   # a blank or other judgment: stop, as manual-rate
            S.B1.stop(str(e))
        print(f"manual check (seg): {bad}/{total} wrong = {bad / max(total, 1):.4f}")
        stats["seg"]["usable"] = stats["seg"]["usable"] and total > 0 and bad / total <= SL.STOP_RATE
    ok = [k for k, s in stats.items() if s["usable"]]
    if not ok:
        print("BOTH UNUSABLE: stop")
    print("usable:" if a.manual else "usable by agreement only (seg still needs manual-rate):", ",".join(ok))
    return 0


def main_sample(a):
    chars = S.load_chars(a.chars)
    readings, _ = S.load_merged(chars)
    labels = S.read_labels(a.labels)
    seg = [p for p in S.read_positions(a.positions) if S.classify(readings[p[4]]) == "seg" and labels.get(p[0], ("", ""))[1]]
    picked = SL.reservoir(seg, a.n, a.seed)
    with open(a.out, "x", encoding="utf-8", newline="\n") as f:
        for id_, src, s, pos, c, *_ in picked:
            ctx = s[max(0, pos - 3):pos] + "[" + c + "]" + s[pos + 1:pos + 4]
            f.write(f"{id_}\t{src}\t{s}\t{pos}\t{c}\t{labels[id_][1]}\t{ctx}\t\n")
    print(f"calib: {len(picked)} of {len(seg)} seg positions -> {a.out}")
    return 0


def main_rate(a):
    try:
        bad, total, per = rate_of(a.judged)
    except ValueError as e:
        print(f"calib: {e}", file=sys.stderr)
        return 1
    print(f"overall\t{bad}\t{total}\t{bad / total:.4f}" if total else "overall\t0\t0\tnan")
    for c, (e, t) in sorted(per.items()):
        print(f"{c}\t{e}\t{t}\t{e / t:.4f}")
    if total and bad / total > SL.STOP_RATE:
        print("STOP: the seg class label error rate is above 10%: seg is unusable", file=sys.stderr)
        return 3
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("agree")
    g.add_argument("human")
    g.add_argument("labels")
    g.add_argument("--t0")
    g.add_argument("--manual")
    g.add_argument("--chars", default=S.DEFAULT_CHARS)
    m = sub.add_parser("manual-sample")
    m.add_argument("positions")
    m.add_argument("labels")
    m.add_argument("--n", type=int, default=150)
    m.add_argument("--seed", type=int, default=S.SEED)
    m.add_argument("--out", required=True)
    m.add_argument("--chars", default=S.DEFAULT_CHARS)
    sub.add_parser("manual-rate").add_argument("judged")
    a = ap.parse_args(argv)
    if a.cmd == "manual-sample" and os.path.exists(a.out):
        S.B1.stop("--out exists")
    return {"agree": main_agree, "manual-sample": main_sample, "manual-rate": main_rate}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
