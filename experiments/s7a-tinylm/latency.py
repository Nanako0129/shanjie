"""S7a latency and size (contract section 3 item 5), numpy inference on the Mac.

  python latency.py --npz tinylm-S.npz [--runs 200]

One run = ll_many over 8 candidates of 17 characters ([BOS] + 17 characters each, k empty), the same function that scores candidates.
Characters are drawn from the model's own vocabulary with a fixed seed. Prints p50 / p95 in ms, the parameter count, the file size
and the size of an int8-quantised copy (per-row symmetric int8 for matrices, float32 scales / vectors; measured by writing it to a
temporary file, never shipped).
"""
import argparse
import os
import tempfile
import time

import numpy as np

import tinylm
from tinylm import NumpyLM, ll_many


def quantize_int8(path, out):
    z = np.load(path, allow_pickle=False)
    d = {}
    for k in z.files:
        a = z[k]
        if a.dtype.kind == "f" and a.ndim == 2:
            scale = np.maximum(np.abs(a).max(axis=1, keepdims=True), 1e-12) / 127.0
            d[k] = np.round(a / scale).astype(np.int8)
            d[k + ".scale"] = scale.astype(np.float32)
        else:
            d[k] = a
    np.savez(out, **d)


def measure(model, runs=200, warmup=10, seed=0, n_cand=8, n_char=17):
    rng = np.random.default_rng(seed)
    chars = model.vocab.chars
    items = [("", "".join(chars[i] for i in rng.integers(0, len(chars), n_char))) for _ in range(n_cand)]
    for _ in range(warmup):
        ll_many(model, items)
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        ll_many(model, items)
        ts.append((time.perf_counter() - t) * 1000)
    return float(np.percentile(ts, 50)), float(np.percentile(ts, 95))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True); ap.add_argument("--runs", type=int, default=200)
    a = ap.parse_args(argv)
    model = NumpyLM(a.npz, dtype=np.float32)
    p50, p95 = measure(model, a.runs)
    m64 = NumpyLM(a.npz, dtype=np.float64)
    q50, q95 = measure(m64, a.runs)
    params = sum(v.size for v in model.p.values())
    with tempfile.TemporaryDirectory() as td:
        q = os.path.join(td, "int8.npz")
        quantize_int8(a.npz, q)
        q_size = os.path.getsize(q)
    print(f"{os.path.basename(a.npz)}: 8 candidates x 17 characters, {a.runs} runs, float32: p50 {p50:.2f} ms, p95 {p95:.2f} ms")
    print(f"{os.path.basename(a.npz)}: same, float64 (what score.py uses): p50 {q50:.2f} ms, p95 {q95:.2f} ms")
    print(f"parameters {params}, npz {os.path.getsize(a.npz) / 1e6:.2f} MB (float32), int8 copy {q_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
