"""S7a parity gate (contract section 3 item 4): max |delta LL| between two score files of the same candidates, scored by different
backends (188 torch vs Mac numpy, real weights, cvtune chat, all candidates). Exit 1 when the largest difference is >= 1e-4.

  python parity.py scores-torch/S.chat.cvtune.jsonl scores-numpy/S.chat.cvtune.jsonl
"""
import json
import sys

LIMIT = 1e-4
COLS = ("ll", "ll0", "ll16")


def max_delta(rows_a, rows_b):
    """{column: (max |delta|, number of candidates compared)}; the files must hold the same rows and the same n-gram scores."""
    if len(rows_a) != len(rows_b):
        raise ValueError(f"row counts differ: {len(rows_a)} vs {len(rows_b)}")
    out = {c: [0.0, 0] for c in COLS}
    for a, b in zip(rows_a, rows_b):
        if a["i"] != b["i"] or a["n"] != b["n"] or a["ng"] != b["ng"]:
            raise ValueError(f"row {a['i']}: the files do not hold the same candidates")
        for c in COLS:
            for x, y in zip(a.get(c, []), b.get(c, [])):
                out[c][0] = max(out[c][0], abs(x - y)); out[c][1] += 1
    return {c: tuple(v) for c, v in out.items()}


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    if len(a) != 2:
        sys.exit("usage: parity.py FILE_A FILE_B")
    d = max_delta(load(a[0]), load(a[1]))
    for c, (m, n) in d.items():
        print(f"{c}: max |dLL| {m:.3e} over {n} candidates")
    if d["ll"][1] == 0 or max(m for m, _ in d.values()) >= LIMIT:
        sys.exit(f"parity: STOP, max |dLL| >= {LIMIT} (or nothing compared)")
    print("parity: OK")


if __name__ == "__main__":
    main()
