"""N0 補量：M5 Mac 上的 M1 重排延遲（MLX 4-bit），和 188 的 M1 同一個方法。

使用者 2026-10-03 同意在 Mac 上只量延遲：取開發集前 302 列中每 5 列一列（61 列），
準確度只用來檢查 4-bit 沒有壞掉（188 上 E4B 4-bit 曾經壞掉），不當 N0 成績。
用法（ime-research/proto/.venv 的 python）：
  python experiments/n0/mac_latency.py <MLX 模型路徑或 HF id> [--chunk 8] [--out FILE]
"""
import argparse
import json
import os
import sys
import time

import mlx.core as mx
from mlx_lm import load

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from n0 import LENIENT, PREAMBLE, ROOT, Lexicon, decode, load_dev, pct  # noqa: E402


def score(model, tok, pre_len, ctx, cands, chunk):
    """每個候選在句尾（加「。」）的對數機率總和；右側補零不影響因果注意力下前面的位置。"""
    out = []
    for i in range(0, len(cands), chunk):
        seqs = [tok.encode(PREAMBLE + ctx + c + "。") for c in cands[i:i + chunk]]
        T = max(map(len, seqs))
        x = mx.array([s + [0] * (T - len(s)) for s in seqs])
        lg = model(x[:, :-1]).astype(mx.float32)
        lp = mx.take_along_axis(lg, x[:, 1:, None], -1)[..., 0] - mx.logsumexp(lg, -1)
        idx = mx.arange(T - 1)[None, :]
        lens = mx.array([len(s) for s in seqs])[:, None]
        keep = (idx >= pre_len - 1) & (idx < lens - 1)
        out += mx.where(keep, lp, 0.0).sum(1).tolist()   # tolist 會強制求值，計時才準
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model"); ap.add_argument("--chunk", type=int, default=8); ap.add_argument("--out")
    a = ap.parse_args()
    model, tok = load(a.model)
    pre_len = len(tok.encode(PREAMBLE))
    lex = Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"))
    rows = load_dev()[:302][::5]
    score(model, tok, pre_len, "", ["暖機"], a.chunk)   # 暖機：第一次呼叫含 Metal 編譯
    mx.reset_peak_memory()
    outs, lat = [], []
    for f, ctx, truth, syls in rows:
        t = time.perf_counter()
        texts = ["".join(ws) for _, ws in decode(lex, syls)]
        s = score(model, tok, pre_len, ctx, texts, a.chunk)
        outs.append(max(zip(s, texts))[1])
        lat.append(time.perf_counter() - t)
    n = len(rows)
    name = f"{os.path.basename(a.model.rstrip('/'))}-chunk{a.chunk}"
    res = {"n": n, "sent_acc": round(sum(o == r[2] for o, r in zip(outs, rows)) / n, 3),
           "lenient_acc": round(sum(o.translate(LENIENT) == r[2].translate(LENIENT) for o, r in zip(outs, rows)) / n, 3),
           "p50_ms": pct(lat, .5), "p95_ms": pct(lat, .95), "max_ms": round(1000 * max(lat)),
           "peak_mem_MiB": round(mx.get_peak_memory() / 2**20)}
    print(f"## mac-m5  {name}  {res}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            for r, o, l in zip(rows, outs, lat):
                fh.write(json.dumps({"file": r[0], "truth": r[2], "out": o, "s": l}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
