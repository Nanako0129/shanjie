"""Q8-ll (contract §11): Qwen3-8B 4-bit (bitsandbytes nf4) on CUDA. Same scoring as run_ll.py's Q-ll:
score = log P(prefix+candidate) - log P(prefix), no length normalisation, ties to the earlier rank.

  run_ll_cuda.py --set dev302 --limit 20 --ctx synth [--idle]
Output: Q8-ll.<noctx|ctx>.jsonl (same record schema as run_ll.py) and meta-188.jsonl. Never prints sentence text.
The pure functions import without torch; torch/transformers load inside load_model().
No KV reuse: one full forward per candidate (recorded as kv_reuse=false in meta).
"""
import argparse
import json
import math
import os
import sys

import s5k
from s5k import die

COND = "Q8-ll"
MODEL_PATH = os.path.join(s5k.HOME, "models", "Qwen3-8B")  # Windows: %USERPROFILE%\models\Qwen3-8B
HEAD = "以下是台灣使用者用注音輸入法打的一句繁體中文。"  # same as run_ll.py


def prefix_text(ctx):
    return HEAD + "\n" + (f"前文：{ctx}\n" if ctx else "") + "句子："


# ---- pure functions (no torch) ----

def logprobs_from_logits(logits, ids):
    """logits[i] (a vocab-sized list) predicts ids[i+1]. -> [log P(ids[i+1] | ids[:i+1])], len(ids)-1 values."""
    out = []
    for i in range(len(ids) - 1):
        m = max(logits[i])
        lse = m + math.log(sum(math.exp(x - m) for x in logits[i]))
        out.append(logits[i][ids[i + 1]] - lse)
    return out


def split_candidate(prefix_ids, full_ids):
    """Candidate token count check (contract §3): full must start with exactly the prefix tokens. -> n_prefix or None."""
    n = len(prefix_ids)
    return n if 0 < n < len(full_ids) and list(full_ids[:n]) == list(prefix_ids) else None


def candidate_score(token_lps, n_prefix):
    """token_lps over the full ids; the candidate starts at token n_prefix, predicted by token_lps[n_prefix-1]."""
    return sum(token_lps[n_prefix - 1:])


def score_row(prefix_ids, full_id_lists, token_lps_fn):
    """-> [score per candidate], or None when any candidate fails the boundary check (row not scored, counted)."""
    ns = [split_candidate(prefix_ids, f) for f in full_id_lists]
    if None in ns:
        return None
    return [candidate_score(token_lps_fn(f), n) for f, n in zip(full_id_lists, ns)]


def run_loop(rows, todo, path, encode, token_lps_fn, score=None):
    """Score every todo row (resumable), appending {"k","scores","ms"} records. -> number of boundary-mismatch rows."""
    score, done, bad = score or score_row, s5k.done_keys(path), 0
    for k, ctx in todo:
        if k in done:
            continue
        pre = prefix_text(ctx)
        pids = encode(pre)
        fulls = [encode(pre + c) for c in rows[k]["cands"]]
        rec = {"k": k}
        with s5k.Timer() as tm:
            rec["scores"] = score(pids, fulls, token_lps_fn)
            if rec["scores"] is None:
                bad += 1
            elif not all(math.isfinite(x) for x in rec["scores"]):
                die("STOP non-finite scores")
        rec["ms"] = tm.ms
        s5k.append(path, rec)
    return bad


def finish(path, limit):
    """Boundary-rate stop (>1%). -> (n_rows, records)."""
    recs = [json.loads(l) for l in open(path, encoding="utf-8")]
    nb = sum(1 for r in recs if r["scores"] is None)
    print(f"{COND} rows={len(recs)} boundary_mismatch={nb}")
    if nb > 0.01 * max(1, len(recs)):
        die("STOP token boundary mismatch above 1%")
    return len(recs), recs


# ---- model (torch imported here only) ----

def load_model():
    """-> (encode, token_lps_fn, info, peak_mb_fn)."""
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    import bitsandbytes
    if not torch.cuda.is_available():
        die("STOP no CUDA")
    cfg = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
    tok = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(MODEL_PATH, quantization_config=cfg, device_map={"": 0}, local_files_only=True).eval()
    info = {"torch": torch.__version__, "transformers": transformers.__version__, "bitsandbytes": bitsandbytes.__version__,
            "gpu": torch.cuda.get_device_name(0), "quant": "nf4", "compute_dtype": "bfloat16", "kv_reuse": False,
            "bos_token_id": tok.bos_token_id}

    def encode(text):
        return tok(text)["input_ids"]  # tokenizer default special tokens only; no chat template

    @torch.no_grad()
    def token_lps(ids):
        x = torch.tensor([ids], device="cuda")
        lg = model(x).logits[0, :-1].float()
        lp = torch.log_softmax(lg, dim=-1).gather(1, x[0, 1:, None])[:, 0]
        return lp.tolist()

    return encode, token_lps, info, lambda: torch.cuda.max_memory_allocated() / 2**20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ctx", choices=["none", "real", "synth"], required=True)
    ap.add_argument("--idle", action="store_true")
    a = ap.parse_args()
    rows = s5k.load_rows(a.set, a.limit)  # hash check before any model is touched
    d = s5k.out_dir(a.set, a.limit)
    with s5k.Timer() as lt:
        encode, token_lps, info, peak = load_model()
    todo = s5k.jobs(rows, a.ctx, a.set)
    if a.ctx == "synth":
        print(f"ctx_chars={len(s5k.SYNTH_CTX)}")
    path = s5k.result_path(d, COND, a.ctx)
    run_loop(rows, todo, path, encode, token_lps)
    n, recs = finish(path, a.limit)
    s5k.append(os.path.join(d, "meta-188.jsonl"), dict(
        runner="run_ll", model=COND, ctx=a.ctx, load_s=round(lt.ms / 1000, 3), peak_mb=round(peak(), 1),
        load_note="idle window" if a.idle else "load unknown", **info))
    if a.limit and s5k.degeneracy([r["scores"] for r in recs if r["scores"]], 1e-6, COND, a.ctx):
        sys.exit(3)


def cli():
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # never print e: messages can carry row text
        sys.exit(f"s5k: unexpected {type(e).__name__}")


if __name__ == "__main__":
    cli()
