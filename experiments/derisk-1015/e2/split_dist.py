"""E2: the left-context length distribution of the evaluation rows, for train_r.py --split-probs.

  python split_dist.py SCORES/M.chat.cvtune.jsonl SCORES/M.chat.wikitune.jsonl ...      # score.py output, chat setting
  -> P(s=0) P(s=1) P(s=2) on one line (rows pooled), counts on stderr

`key` in a score record is len(context_key(prefix)): the engine keeps at most the last 2 Han characters, so the context a scored
sequence [BOS] + k + c carries is 0, 1 or 2 characters. The record holds a number only (no text); the prefixes are identical in the
chat and formal setting, so one setting is enough.
"""
import collections
import json
import sys


def split_probs(paths, maxlen=2):
    c = collections.Counter()
    for p in paths:
        for line in open(p, encoding="utf-8"):
            if line.strip():
                c[min(json.loads(line)["key"], maxlen)] += 1
    n = sum(c.values())
    if not n:
        raise ValueError("no rows")
    return [c[s] / n for s in range(maxlen + 1)], [c[s] for s in range(maxlen + 1)]


if __name__ == "__main__":
    probs, counts = split_probs(sys.argv[1:])
    print(f"counts {counts}", file=sys.stderr)
    print(" ".join(f"{p:.4f}" for p in probs))
