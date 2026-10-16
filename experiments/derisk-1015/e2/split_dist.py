"""E2: the left-context length distribution of the evaluation rows, for train_r.py --split-probs.

  python split_dist.py SCORES/M.chat.cvtune.jsonl SCORES/M.chat.wikitune.jsonl ...      # score.py output, chat setting
  -> P(s=0) P(s=1) P(s=2) on one line (rows pooled), counts on stderr
  python split_dist.py --field key16 --maxlen 16 SCORES/*.jsonl      # revision 7: 17 values, s = 0..16 (`key16` = last <= 16 chars)

`key` in a score record is len(context_key(prefix)): the engine keeps at most the last 2 Han characters, so the context a scored
sequence [BOS] + k + c carries is 0, 1 or 2 characters (`key16`, the last <= 16 characters: 0..16, revision 7). The record holds a number only (no text); the prefixes are identical in the
chat and formal setting, so one setting is enough.
"""
import argparse
import collections
import json
import sys


def split_probs(paths, maxlen=2, field="key"):
    c = collections.Counter()
    for p in paths:
        for line in open(p, encoding="utf-8"):
            if line.strip():
                c[min(json.loads(line)[field], maxlen)] += 1
    n = sum(c.values())
    if not n:
        raise ValueError("no rows")
    return [c[s] / n for s in range(maxlen + 1)], [c[s] for s in range(maxlen + 1)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--field", default="key", help="score-record field: key (last <= 2 chars) or key16 (last <= 16 chars)")
    ap.add_argument("--maxlen", type=int, default=2, help="values above this are pooled into it; prints maxlen+1 probabilities")
    a = ap.parse_args()
    probs, counts = split_probs(a.paths, a.maxlen, a.field)
    print(f"counts {counts}", file=sys.stderr)
    print(" ".join(f"{p:.4f}" for p in probs))
