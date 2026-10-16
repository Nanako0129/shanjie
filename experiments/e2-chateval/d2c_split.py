"""Rerank-strength amendment one, section 11.5 (docs/contracts/rerank-strength.md): split the rows whose answer is in the
top n but not first by n-gram, as the triage contract's D2 does, and split D2c by what the small model alone picks. Record only.

  python d2c_split.py --scores DIR [DIR ...] --setting chat|formal --sets S [S ...] [--model v3] [--params ALPHA TAU DELTA]

Reads judge.py's inputs (DIR/<model>.<setting>.<set>.jsonl, the CLI's --tinylm-scores) and never decodes. Default params: the
shipped standard of the setting. One line per set, then the total: rows in range, rescued, D2a, D2b, D2c-(i), D2c-(ii), D2-none,
and (i) / D2c. Numbers only.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "s7a-tinylm"))
import fusegrid as fg  # noqa: E402
from judge import find_scores  # noqa: E402

STANDARD = {"chat": (0.3, 2.0, 0.25), "formal": (0.7, 1.0, 2.0)}
KEYS = ("rescued", "D2a", "D2b", "D2c_i", "D2c_ii", "D2_none")


def classify(r, alpha, tau, delta):
    """One of KEYS, or None when the row is outside the range (n-gram first already right, or no right answer in the top n).
    The answer g is the first ok candidate. j and the fused scores follow fusegrid.pick, ties to the better n-gram rank."""
    ok = r["ok"][: r["n"]]
    if not ok or ok[0] or 1 not in ok:
        return None
    if r["n"] < 2 or r.get("ll") is None:
        return "D2_none"
    if r["ng"][0] - r["ng"][1] >= tau:
        return "D2a"
    s = [g + alpha * l for g, l in zip(r["ng"], r["ll"])]
    j = max(range(len(s)), key=lambda i: (s[i], -i))
    if ok[j]:
        return "rescued" if s[j] - s[0] >= delta else "D2b"
    return "D2c_i" if ok[fg.lm_alone(r)] else "D2c_ii"


def count(rows, params):
    c = dict.fromkeys(KEYS, 0)
    c["in_range"] = 0
    for r in rows:
        k = classify(r, *params)
        if k:
            c[k] += 1
            c["in_range"] += 1
    return c


def line(name, c):
    d2c = c["D2c_i"] + c["D2c_ii"]
    share = f"{c['D2c_i'] / d2c:.3f}" if d2c else "-"
    return f"| {name} | {c['in_range']} | " + " | ".join(str(c[k]) for k in KEYS) + f" | {share} |"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scores", nargs="+", required=True)
    ap.add_argument("--setting", choices=fg.SETTINGS, required=True)
    ap.add_argument("--sets", nargs="+", required=True)
    ap.add_argument("--model", default="v3")
    ap.add_argument("--params", nargs=3, type=float, metavar=("ALPHA", "TAU", "DELTA"))
    a = ap.parse_args(argv)
    params = tuple(a.params) if a.params else STANDARD[a.setting]
    total = dict.fromkeys(KEYS + ("in_range",), 0)
    print(f"{a.setting}: alpha {params[0]:g}, tau {params[1]:g}, delta {params[2]:g}")
    print("| set | in range | " + " | ".join(KEYS) + " | (i) / D2c |")
    print("|---|" + "---|" * (len(KEYS) + 2))
    for name in a.sets:
        c = count(fg.load_scores(find_scores(a.scores, a.model, a.setting, name)), params)
        print(line(name, c))
        for k in total:
            total[k] += c[k]
    print(line("total", total))
    return total


if __name__ == "__main__":
    main()
