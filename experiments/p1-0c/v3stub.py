"""P1: the fields score_0c.py copies from a tinylm score file (i, n, ng, ok, ed, glen), computed from the rows and the dump alone, so the
L0 training rows can get 0c teacher scores without running M. Same definitions as experiments/s7a-tinylm/score.py build_records.

  python3 experiments/p1-0c/v3stub.py --rows wiki.txt --dump wiki.dump8 --expect-top1 82966 --out wiki.v3.jsonl
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s7a-tinylm"))
import score  # noqa: E402  (puts reference/proto and tools on sys.path; lenient and levenshtein come with it)


def records(rows, cands):
    for i, ((gold, _syl, _prefix), cs) in enumerate(zip(rows, cands), 1):
        g = score.lenient(gold)
        ok = [int(score.lenient(s) == g) for s, _ in cs]
        yield {"i": i, "n": len(cs), "ng": [sc for _, sc in cs], "ok": ok,
               "ed": [0 if o else score.levenshtein(s, gold) for (s, _), o in zip(cs, ok)], "glen": len(gold)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True); ap.add_argument("--dump", required=True)
    ap.add_argument("--expect-top1", type=int, required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rows = score.load_rows(a.rows)
    cands = score.load_dump(open(a.dump, encoding="utf-8"), len(rows))
    top1 = score.top1_from_dump(rows, cands)
    if top1 != a.expect_top1:
        sys.exit(f"v3stub: STOP, top1 from the dump {top1} != expected {a.expect_top1}")
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        for r in records(rows, cands):
            f.write(json.dumps(r) + "\n")
    print(f"v3stub: rows {len(rows)}, top1 {top1}, wrote {a.out}")


if __name__ == "__main__":
    main()
