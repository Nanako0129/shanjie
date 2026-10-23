"""One comparison for the IT+AI pack's public checks (docs/contracts/it-pack.md section 3.4): `tools/evalstats.py compare BASE CAND --net`, the
number of broken rows, and every row whose chosen sentence differs (public sets only: gold sentence, the base and the new choice, right or wrong).

  python3 experiments/it-pack/compare.py LABEL ROWS BASE_PREFIX CAND_PREFIX [--pool BASE2_PREFIX CAND2_PREFIX]

A PREFIX is the path prefix of a CLI run's files: PREFIX.rs (--rowstats), PREFIX.dump (--dump) and PREFIX.sc (--tinylm-scores). --pool joins a second
run of the same rows (the other profile) under the same label, for the typo set's two settings together. Exit 3 when a row is broken and the
label starts with `acg-` (the anime sets must have none, section 3.4); the caller decides what else is a stop.
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EVALSTATS = os.path.join(HERE, "..", "..", "tools", "evalstats.py")


def rows_of(path):
    return [l.rstrip("\n").split("|") for l in open(path, encoding="utf-8") if l.strip()]


def stats(prefix):
    return [tuple(int(x) for x in l.split("\t")) for l in open(prefix + ".rs")]


def chosen(prefix):
    """The sentence the run chose for each row: the dump keeps the decoder's order, `c` of the scores file is the pick (0 when no rerank ran)."""
    by_row = {}
    for l in open(prefix + ".dump", encoding="utf-8"):
        r, k, s, _ = l.rstrip("\n").split("\t")
        by_row.setdefault(int(r), {})[int(k)] = s
    picks = [json.loads(l)["c"] for l in open(prefix + ".sc")]
    return [by_row[i + 1][c + 1] for i, c in enumerate(picks)]


def main():
    label, rows_path, bases, cands = sys.argv[1], sys.argv[2], [sys.argv[3]], [sys.argv[4]]
    if len(sys.argv) > 5:
        assert sys.argv[5] == "--pool" and len(sys.argv) == 8, __doc__
        bases.append(sys.argv[6])
        cands.append(sys.argv[7])
    rows = rows_of(rows_path)
    names = ["" if len(bases) == 1 else f"[{i}] " for i in range(len(bases))]
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for kind, pre in (("base", bases), ("cand", cands)):
            lines = [s for p in pre for s in stats(p)]
            p = os.path.join(d, kind)
            with open(p, "w") as f:
                f.writelines(f"{i + 1}\t{s[1]}\t{s[2]}\t{s[3]}\n" for i, s in enumerate(lines))
            paths.append(p)
        out = subprocess.run([sys.executable, EVALSTATS, "compare", *paths, "--label", label, "--net"], check=True, capture_output=True, text=True).stdout
    print(out.strip().splitlines()[-1])
    broken = 0
    for tag, b, c in zip(names, bases, cands):
        sb, sc, cb, cc = stats(b), stats(c), chosen(b), chosen(c)
        assert len(sb) == len(sc) == len(rows), (len(sb), len(sc), len(rows))
        for i, (x, y) in enumerate(zip(sb, sc)):
            broken += x[1] == 1 and y[1] == 0
            if cb[i] != cc[i]:
                kind = {(1, 1): "same-right", (0, 0): "both-wrong", (0, 1): "FIXED", (1, 0): "BROKEN"}[(x[1], y[1])]
                print(f"  {tag}row {i + 1} {kind}: gold {rows[i][1]} | base {cb[i]} | new {cc[i]}")
    print(f"  {label}: broken {broken}")
    if broken and label.startswith("acg-"):
        sys.exit(3)


if __name__ == "__main__":
    main()
