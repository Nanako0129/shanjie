"""0c slice A (docs/contracts/oc-experimental.md section 2, A1): Funmula/0c-sequence-d512 model.onnx -> SJOC0001 (tools/oc_ref.py).

  python3 tools/convert_oc.py --model-dir M --out oc.sjoc      (needs the onnx package: Colab or GCP, not the Mac)

Read from the graph (2026-10-19 full dump, commit 66eb4a81). The pinned SHA-256 already stops any other export; the checks below
confirm that this graph is the structure the converter assumes (the output bias val_779 and the RoPE table `expand` are taken by name
and only the A2 comparison with ONNX Runtime checks their roles):
- named initializers: the embeddings, every LayerNorm weight (layers.N.attn_norm only for N >= 1; layer 0's attention has none), the
  head (lm_head.dense, lm_head.norm), the tied decoder.weight; `out_ids` = every token id but 0-3, 5 and 7 (so 4, 6, 8, 9, ...: each
  character id c at position c - 6, score_0c.py's target_ids); the output bias is `val_779`, gathered by out_ids; RoPE's inverse
  frequencies are `expand` (1, n_rope, 1).
- the unnamed val_* matrices are the right operands of the MatMul nodes, in node order six per layer: q, k, v, o (d x d), w1 (d x 2 ffn),
  w2 (ffn x d); w1's output is Split in two halves, GELU on the first, times the second.
- the head is Gemm(x, lm_head.dense, transB = 1, no bias) -> GELU -> LayerNorm, then Gemm with decoder.weight[out_ids] (transB) plus
  val_779[out_ids] -> LogSoftmax over those n_tok - 6 columns; target_ids index those columns.
- every LayerNormalization has epsilon 1e-5 and no bias; the attention scale constant is 1 / sqrt(head width).
"""
import argparse
import hashlib
import json
import os

import numpy as np

import oc_ref

PINNED = {"model.onnx": "71af1f0e77b2752cc71cbc04bf19c8e8f2e765662246f766e65a2822bab4e66e",
          "vocab.json": "556dffaffbf4c05211a846dcee55ef735cea68c5754f9bffcc6f9588124172b3",
          "manifest.json": "f8d737cf44e9ff8f90e7108f1e9df4fece0cdcae254e050968fc6ef723ee1976"}
P = "m.network.model."


def convert(model_dir, out):
    import onnx
    from onnx import numpy_helper
    sha = {}
    for f, h in PINNED.items():
        sha[f] = hashlib.sha256(open(os.path.join(model_dir, f), "rb").read()).digest()
        if sha[f].hex() != h:
            raise SystemExit(f"convert_oc: STOP, sha256 of {f} is not the pinned one")
    g = onnx.load(os.path.join(model_dir, "model.onnx")).graph
    init = {t.name: numpy_helper.to_array(t) for t in g.initializer}
    vocab = json.load(open(os.path.join(model_dir, "vocab.json"), encoding="utf-8"))
    manifest = json.load(open(os.path.join(model_dir, "manifest.json"), encoding="utf-8"))
    attr = lambda n: {a.name: onnx.helper.get_attribute_value(a) for a in n.attribute}

    dec = init["m.network.decoder.weight"]
    n_tok, d = dec.shape
    inv = init["expand"].reshape(-1)
    spec = manifest["spec"]
    n_layer, H, ffn = spec["layers"], spec["heads"], spec["intermediate"]
    if len(vocab["characters"]) != n_tok - 8 or init["m.base_embedding.weight"].shape[0] != len(vocab["bases"]) + 2:
        raise SystemExit("convert_oc: STOP, vocab.json does not match the embedding tables")
    if float(init["val_103"]) != 1 / np.sqrt(d // H) or 2 * len(inv) != d // H:
        raise SystemExit("convert_oc: STOP, attention scale or RoPE width differs from the head width")
    for n in g.node:
        if n.op_type == "LayerNormalization" and (len(n.input) != 2 or abs(attr(n)["epsilon"] - oc_ref.EPS) > 1e-9):
            raise SystemExit(f"convert_oc: STOP, LayerNorm {n.output[0]} has a bias or another epsilon")
    mats = [n.input[1] for n in g.node if n.op_type == "MatMul" and n.input[1] in init]
    shapes = [(d, d)] * 4 + [(d, 2 * ffn), (ffn, d)]
    if [init[m].shape for m in mats] != shapes * n_layer:
        raise SystemExit(f"convert_oc: STOP, MatMul weights are not six per layer in the order q k v o w1 w2: {[init[m].shape for m in mats]}")
    gemm = [n for n in g.node if n.op_type == "Gemm"]
    if (len(gemm) != 2 or list(gemm[0].input) != ["val_762", "m.network.lm_head.dense.weight"] or attr(gemm[0]).get("transB") != 1
            or len(gemm[1].input) != 3 or attr(gemm[1]).get("transB") != 1):
        raise SystemExit("convert_oc: STOP, the head is not the two Gemms read from the graph")
    split = [n for n in g.node if n.op_type == "Split"]
    gelu_in = [n.input[0] for n in g.node if n.op_type == "Div" and n.input[1] == "val_123"]
    if [s.output[0] for s in split] != gelu_in[:n_layer]:
        raise SystemExit("convert_oc: STOP, GELU is not on the first half of every Split")

    T = {"dec": dec, "dec_b": init["val_779"], "base": init["m.base_embedding.weight"], "tone": init["m.tone_embedding.weight"],
         "type": init["m.type_embedding.weight"], "emb_norm": init[P + "embeddings.norm.weight"], "inv_freq": inv,
         "final_norm": init[P + "final_norm.weight"], "head_w": init["m.network.lm_head.dense.weight"].T,
         "head_norm": init["m.network.lm_head.norm.weight"]}
    for i in range(n_layer):
        if i:
            T[f"l{i}.attn_norm"] = init[P + f"layers.{i}.attn_norm.weight"]
        T[f"l{i}.mlp_norm"] = init[P + f"layers.{i}.mlp_norm.weight"]
        for k, m in zip(("wq", "wk", "wv", "wo", "w1", "w2"), mats[6 * i:6 * i + 6]):
            T[f"l{i}.{k}"] = init[m]
    if P + "layers.0.attn_norm.weight" in init:
        raise SystemExit("convert_oc: STOP, layer 0 has an attention LayerNorm")
    cfg = {"n_layer": n_layer, "n_head": H, "d": d, "ffn": ffn, "n_tok": n_tok, "n_out": len(init["out_ids"]),
           "n_base": T["base"].shape[0], "n_tone": T["tone"].shape[0], "n_type": T["type"].shape[0], "n_rope": len(inv)}
    oc_ref.write(out, cfg, T, vocab["characters"], vocab["bases"], manifest["neutral_tolerant_last"], init["out_ids"],
                 (sha["model.onnx"], sha["vocab.json"]))
    print(f"wrote {out} ({os.path.getsize(out)} bytes, sha256 {hashlib.sha256(open(out, 'rb').read()).hexdigest()})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    convert(a.model_dir, a.out)
