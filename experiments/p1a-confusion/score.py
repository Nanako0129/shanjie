"""P1a classifier scoring (section 2.3): adds f(c) to every candidate of a candidates.py file, once with the row's context and once
without (the "did the context reach the classifier" count), and the classifier's prediction at each gold position (section 3 item 2).

  python3 score.py --lm data/lm/bigram.sjlm --weights DIR --in cvtune.chat.jsonl --out cvtune.chat.scored.jsonl

f is stored per reading ({"ㄗㄞˋ": [with context, without]}) for every reading that has a weights file; the stage decides which are summed (fuse.py).
"""
import argparse
import functools
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p1a  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lm", required=True); ap.add_argument("--weights", required=True)
    ap.add_argument("--in", dest="inp", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    models = [p1a.load_weights(p) for p in sorted(glob.glob(os.path.join(a.weights, "*.sjw")))]
    lex, cls_of = p1a.training_lexicon(), p1a.load_cls_of(a.lm)
    featurize = functools.lru_cache(maxsize=200_000)(lambda left, right: p1a.hash_ids(p1a.mask_features(lex, cls_of, left, right)))
    with open(a.inp, encoding="utf-8") as f, open(a.out, "w", encoding="utf-8") as out:
        for line in f:
            row = json.loads(line)
            ck, syls = row["ctxk"], row["syls"]
            for c in row["cands"]:
                c["f"] = {m.reading: [p1a.candidate_f(m, featurize, ck, c["w"], syls), p1a.candidate_f(m, featurize, "", c["w"], syls)] for m in models}
            row["gp"] = {m.reading: p1a.gold_predictions(m, featurize, ck, row["gold"], syls) for m in models}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
