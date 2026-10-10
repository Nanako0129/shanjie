"""P1a candidates (section 2.3): the shipped decode (model-v5 + classes-v3, --context, no word pack) through the Python reference
implementation, top 8 with word boundaries and n-gram scores, one JSON line per row.

  python3 candidates.py --lm data/lm/bigram.sjlm --profile chat (--rows FILE | --dev 302) --name cvtune --out cvtune.chat.jsonl [--equalize ㄗㄞˋ,ㄗㄨㄛˋ]

Prints the summary line of reference/proto/lm_eval.py --context; its top1_sha256 must equal the CLI's (shanjie-eval --lm ... --context) for the
same set and profile, row for row. --equalize decodes with the lexicon-score-equalization control instead (再 := 在, 做 := 作 for the given readings; p1a.EQUALIZE).
"""
import argparse
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p1a  # noqa: E402
from p1a import ROOT, L, ime, lenient  # noqa: E402
from lm_eval import rows_of  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lm", required=True); ap.add_argument("--profile", choices=p1a.PROFILES, required=True)
    ap.add_argument("--rows"); ap.add_argument("--dev", type=int); ap.add_argument("--name", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--equalize", help="comma-separated readings (ㄗㄞˋ,ㄗㄨㄛˋ)")
    a = ap.parse_args()
    rows = rows_of(sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt"))))[:a.dev] if a.dev else rows_of([a.rows])
    lm = L.BigramLM(a.lm)
    base = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.OVERLAYS)
    ov = {l.split("\t")[1] for l in open(os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"), encoding="utf-8")}
    lex = L.cap_overlay(base, ov, lm)
    if a.equalize:
        lex = p1a.equalize(lex, tuple(a.equalize.split(",")))
    top1 = o64 = 0
    firsts = []
    with open(a.out, "w", encoding="utf-8") as out:
        for i, (gold, syls, ctx) in enumerate(rows, 1):
            ck = L.context_key(ctx)
            nb = L.decode(lex, syls, lm, a.profile, start=L.history(ck, lm))
            surf = ["".join(ws) for _, ws in nb]
            firsts.append(surf[0])
            top1 += lenient(surf[0]) == lenient(gold)
            o64 += lenient(gold) in {lenient(s) for s in surf}
            out.write(json.dumps({"i": i, "gold": gold, "syls": syls, "ctxk": ck,
                                  "cands": [{"s": sc, "w": list(ws)} for sc, ws in nb[:p1a.TOP_K]]}, ensure_ascii=False) + "\n")
    sha = hashlib.sha256("\n".join(firsts).encode("utf-8")).hexdigest()
    print(f"## {a.name}  lm-{a.profile}+ctx{'  EQUALIZED ' + a.equalize if a.equalize else ''}  {{'n': {len(rows)}, 'top1': {top1}, 'oracle@64': {o64}, 'top1_sha256': '{sha}'}}")


if __name__ == "__main__":
    main()
