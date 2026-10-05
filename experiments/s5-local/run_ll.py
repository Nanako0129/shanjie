"""B1-ll, B4-ll, Q-ll (contract §3): log P(prefix+candidate) - log P(prefix), no length normalisation.

  run_ll.py --model Q-ll --set dev302 --limit 20 --ctx synth [--idle]
Q-ll runs under venv A; B1-ll/B4-ll need the PrismML fork (venv B, contract §9). Never prints sentence text.
"""
import argparse
import copy
import json
import math
import sys
import time

import s5k
from s5k import die

MODEL_DIR = {"B1-ll": "bonsai-1.7b-mlx-1bit", "B4-ll": "bonsai-4b-mlx-1bit", "Q-ll": "qwen3-1.7b-4bit"}
HEAD = "以下是台灣使用者用注音輸入法打的一句繁體中文。"


def prefix_text(ctx):
    return HEAD + "\n" + (f"前文：{ctx}\n" if ctx else "") + "句子："


def cand_scores(model, prefix_ids, cand_id_lists):
    """Prefix KV computed once; each candidate continues from a copy. -> [sum of candidate token logprobs]."""
    import mlx.core as mx
    from mlx_lm.models.cache import make_prompt_cache
    cache = make_prompt_cache(model)
    last = model(mx.array([prefix_ids]), cache=cache)[0, -1].astype(mx.float32)
    first_lp = last - mx.logsumexp(last)
    out = []
    for ids in cand_id_lists:
        c = copy.deepcopy(cache)
        lg = model(mx.array([ids]), cache=c)[0].astype(mx.float32)
        lp = lg - mx.logsumexp(lg, axis=-1, keepdims=True)
        tot = first_lp[ids[0]] + sum(lp[i, ids[i + 1]] for i in range(len(ids) - 1))
        out.append(tot)
    mx.eval(out)
    return [float(x) for x in out]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=list(MODEL_DIR))
    ap.add_argument("--set", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ctx", choices=["none", "real", "synth"], required=True)
    ap.add_argument("--idle", action="store_true")
    a = ap.parse_args()
    rows = s5k.load_rows(a.set, a.limit)  # hash check before any model is touched
    import mlx.core as mx
    from mlx_lm import load
    d = s5k.out_dir(a.set, a.limit)
    t = time.perf_counter()
    model, tok = load(s5k.MODELS + "/" + MODEL_DIR[a.model])
    load_s = time.perf_counter() - t
    todo = s5k.jobs(rows, a.ctx, a.set)
    if a.ctx == "synth":
        print(f"ctx_chars={len(s5k.SYNTH_CTX)}")
    path = s5k.result_path(d, a.model, a.ctx)
    done = s5k.done_keys(path)
    bad = 0
    for k, ctx in todo:
        if k in done:
            continue
        pre = prefix_text(ctx)
        pids = tok.encode(pre)
        cids = []
        for c in rows[k]["cands"]:
            full = tok.encode(pre + c)
            cids.append(full[len(pids):] if full[:len(pids)] == pids else None)
        rec = {"k": k}
        with s5k.Timer() as tm:
            if None in cids:  # prefix tokens differ: row not scored, counted (contract §3)
                rec["scores"], bad = None, bad + 1
            else:
                rec["scores"] = cand_scores(model, pids, cids)
                if not all(math.isfinite(x) for x in rec["scores"]):
                    die("STOP non-finite scores")
        rec["ms"] = tm.ms
        s5k.append(path, rec)
    allrecs = [json.loads(l) for l in open(path, encoding="utf-8")]
    nb = sum(1 for r in allrecs if r["scores"] is None)
    print(f"{a.model} ctx={a.ctx} rows={len(allrecs)} boundary_mismatch={nb}")
    if nb > 0.01 * max(1, len(allrecs)):
        die("STOP token boundary mismatch above 1%")
    s5k.write_meta(d, runner="run_ll", model=a.model, ctx=a.ctx, load_s=round(load_s, 3),
                   peak_mb=round(mx.get_peak_memory() / 2**20, 1), load_note="idle window" if a.idle else "load unknown")
    if a.limit and s5k.degeneracy([r["scores"] for r in allrecs if r["scores"]], 1e-6, a.model, a.ctx):
        sys.exit(3)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # never print e: messages can carry row text
        sys.exit(f"s5k: unexpected {type(e).__name__}")
