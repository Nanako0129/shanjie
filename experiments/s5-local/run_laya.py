"""L-noul, L-choice-fwd, L-choice-rev, L-pos (contract §3). Run under venv A.

  run_laya.py --set dev302 --limit 20 --ctx synth [--conds L-noul,...] [--idle]
Resumable per row. stdout: row counts, key names, fixed strings. Never sentence text.
"""
import argparse
import json
import math
import sys
import time

import s5k
from s5k import die

NOUL_Q = ("A user in Taiwan typed a Zhuyin (Bopomofo) phonetic input. The text the user had already written just "
          "before it is `context` (it may be empty). Is the following sentence exactly what the user meant: correct "
          "Traditional Chinese characters as used in Taiwan, grammatical, and sensible in context? Sentence: 「{c}」")
NOUL_KEYS = {"type", "confidence", "answer_confidence", "action", "noul"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ctx", choices=["none", "real", "synth"], required=True)
    ap.add_argument("--conds", default="L-noul,L-choice-fwd,L-choice-rev,L-pos")
    ap.add_argument("--idle", action="store_true")
    a = ap.parse_args()
    rows = s5k.load_rows(a.set, a.limit)  # hash check happens here, before any model is touched
    from s5 import INSTR, POS_INSTR, pos_decision, positions  # S5j pure functions only
    import mlx.core as mx
    import laya_mlx
    d = s5k.out_dir(a.set, a.limit)
    t = time.perf_counter()
    agent = laya_mlx.load(s5k.MODELS + "/laya-multilingual-mlx-fp16", device="gpu", dtype="float16")
    load_s = time.perf_counter() - t
    todo = s5k.jobs(rows, a.ctx, a.set)
    if a.ctx == "synth":
        print(f"ctx_chars={len(s5k.SYNTH_CTX)}")
    choice_ins = INSTR.replace("`rows[{i}].context`", "`context`")
    assert choice_ins != INSTR
    stop = False
    for cond in a.conds.split(","):
        path = s5k.result_path(d, cond, a.ctx)
        done = s5k.done_keys(path)
        for k, ctx in todo:
            if k in done:
                continue
            cands = rows[k]["cands"]
            state = {"context": ctx}
            rec = {"k": k}
            with s5k.Timer() as tm:
                if cond == "L-noul":
                    qs = {f"q{j}": {"type": "noul", "instructions": NOUL_Q.format(c=c)} for j, c in enumerate(cands)}
                    ans = agent.system_one(state, qs)["answers"]
                    keys = {kk for v in ans.values() for kk in v}
                    if not done and k == todo[0][0]:
                        print(f"L-noul answer keys={sorted(keys)}")
                    if keys != NOUL_KEYS:
                        die("STOP L-noul answer keys differ from contract")
                    rec["scores"] = [ans[f"q{j}"]["noul"] for j in range(len(cands))]
                elif cond in ("L-choice-fwd", "L-choice-rev"):
                    rev = cond.endswith("rev")
                    cs = cands[::-1] if rev else cands
                    q = {"q": {"type": "choice", "instructions": choice_ins, "criteria": {f"c{j + 1}": c for j, c in enumerate(cs)}}}
                    r = agent.system_one(state, q)["answers"]["q"]
                    pr = [r["probabilities"][f"c{j + 1}"] for j in range(len(cs))]
                    if not all(math.isfinite(x) for x in pr) or abs(sum(pr) - 1) > 1e-3:  # rounded to 4 decimals
                        die("STOP choice probabilities invalid")
                    rec["scores"] = pr[::-1] if rev else pr  # rank order
                else:  # L-pos
                    ps = positions(rows[k])
                    rec["adopt"] = []
                    if ps:
                        qs = {f"q{n}": {"type": "choice", "instructions": POS_INSTR,
                                        "criteria": {f"c{j + 1}": s for j, s in enumerate(opts)}} for n, (_, opts) in enumerate(ps)}
                        ans = agent.system_one(state, qs)["answers"]
                        for n, (p, _) in enumerate(ps):
                            j, _m = pos_decision(ans[f"q{n}"], list(qs[f"q{n}"]["criteria"]))
                            if j:
                                rec["adopt"].append([p, j])
            if "scores" in rec and not all(math.isfinite(x) for x in rec["scores"]):
                die("STOP non-finite scores")
            rec["ms"] = tm.ms
            s5k.append(path, rec)
        print(f"{cond} ctx={a.ctx} rows={len(todo)}")
        if cond == "L-noul" and a.limit:  # smoke only; the full run gets the same numbers from score.py
            recs = [json.loads(l)["scores"] for l in open(path, encoding="utf-8")]
            stop |= s5k.degeneracy(recs, 0.01, cond, a.ctx)
    s5k.write_meta(d, runner="run_laya", ctx=a.ctx, conds=a.conds, load_s=round(load_s, 3),
                   peak_mb=round(mx.get_peak_memory() / 2**20, 1), load_note="idle window" if a.idle else "load unknown")
    if stop:
        sys.exit(3)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # never print e: messages can carry row text
        sys.exit(f"s5k: unexpected {type(e).__name__}")
