"""S7a torch model (training and the torch scoring backend). The numpy twin is tinylm.NumpyLM; the .npz format lives in tinylm.py.

Only this file and train.py import torch. Same architecture as NumpyLM: pre-LN decoder blocks, tied embedding, learned positions,
GELU (tanh form), causal attention, LayerNorm eps 1e-5.
"""
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

import tinylm
from tinylm import CTX, LAYER_KEYS, Vocab

# contract section 2.2; "T" is a tiny config for smoke tests only
CONFIGS = {"S": dict(n_layer=4, d=256, n_head=4), "M": dict(n_layer=6, d=384, n_head=6), "T": dict(n_layer=2, d=32, n_head=2)}


class Block(nn.Module):
    def __init__(self, d, n_head):
        super().__init__()
        self.n_head = n_head
        self.ln1, self.ln2 = nn.LayerNorm(d, eps=1e-5), nn.LayerNorm(d, eps=1e-5)
        self.qkv, self.proj = nn.Linear(d, 3 * d), nn.Linear(d, d)
        self.fc1, self.fc2 = nn.Linear(d, 4 * d), nn.Linear(4 * d, d)

    def forward(self, x):
        B, T, d = x.shape
        q, k, v = self.qkv(self.ln1(x)).split(d, dim=-1)
        q, k, v = (t.reshape(B, T, self.n_head, d // self.n_head).transpose(1, 2) for t in (q, k, v))
        a = F.scaled_dot_product_attention(q, k, v, is_causal=True)
        x = x + self.proj(a.transpose(1, 2).reshape(B, T, d))
        return x + self.fc2(F.gelu(self.fc1(self.ln2(x)), approximate="tanh"))


class GPT(nn.Module):
    def __init__(self, V, n_layer, d, n_head, ctx=CTX):
        super().__init__()
        self.cfg = dict(V=V, n_layer=n_layer, d=d, n_head=n_head, ctx=ctx)
        self.emb, self.pos = nn.Embedding(V, d), nn.Embedding(ctx, d)
        self.blocks = nn.ModuleList(Block(d, n_head) for _ in range(n_layer))
        self.lnf = nn.LayerNorm(d, eps=1e-5)
        self.apply(self._init)
        for b in self.blocks:   # GPT-2 style: scale the residual-branch outputs
            nn.init.normal_(b.proj.weight, 0.0, 0.02 / (2 * n_layer) ** 0.5)
            nn.init.normal_(b.fc2.weight, 0.0, 0.02 / (2 * n_layer) ** 0.5)

    @staticmethod
    def _init(m):
        if isinstance(m, nn.Linear):
            nn.init.normal_(m.weight, 0.0, 0.02)
            nn.init.zeros_(m.bias)
        elif isinstance(m, nn.Embedding):
            nn.init.normal_(m.weight, 0.0, 0.02)

    def forward(self, ids):
        T = ids.shape[1]
        x = self.emb(ids) + self.pos(torch.arange(T, device=ids.device))
        for b in self.blocks:
            x = b(x)
        return self.lnf(x) @ self.emb.weight.t()   # tied embedding


def export_npz(model, chars, path):
    """The one exporter. Linear weights are transposed to (in, out); the result is read by tinylm.NumpyLM."""
    def n(t):
        return t.detach().cpu().float().numpy()
    p = {"emb": n(model.emb.weight), "pos": n(model.pos.weight), "lnf_w": n(model.lnf.weight), "lnf_b": n(model.lnf.bias)}
    for i, b in enumerate(model.blocks):
        for k, t in (("ln1_w", b.ln1.weight), ("ln1_b", b.ln1.bias), ("qkv_w", b.qkv.weight.t()), ("qkv_b", b.qkv.bias),
                     ("proj_w", b.proj.weight.t()), ("proj_b", b.proj.bias), ("ln2_w", b.ln2.weight), ("ln2_b", b.ln2.bias),
                     ("fc1_w", b.fc1.weight.t()), ("fc1_b", b.fc1.bias), ("fc2_w", b.fc2.weight.t()), ("fc2_b", b.fc2.bias)):
            p[f"l{i}.{k}"] = n(t)
    assert len(chars) + 3 == model.cfg["V"]
    tinylm.save_params(path, p, chars, model.cfg["n_layer"], model.cfg["n_head"], model.cfg["ctx"])


def from_npz(path, device="cpu"):
    """The exported weights loaded back into a torch GPT (so the torch scoring backend scores exactly what the .npz holds)."""
    z = np.load(path, allow_pickle=False)
    n_layer, n_head, ctx = (int(x) for x in z["cfg"])
    chars = [str(c) for c in z["chars"]]
    d = z["emb"].shape[1]
    m = GPT(len(chars) + 3, n_layer, d, n_head, ctx)

    def put(param, a):
        with torch.no_grad():
            param.copy_(torch.from_numpy(np.ascontiguousarray(a)))

    put(m.emb.weight, z["emb"]); put(m.pos.weight, z["pos"]); put(m.lnf.weight, z["lnf_w"]); put(m.lnf.bias, z["lnf_b"])
    for i, b in enumerate(m.blocks):
        for k in LAYER_KEYS:
            a = z[f"l{i}.{k}"]
            mod, kind = k.split("_")
            mod = getattr(b, mod)
            put(mod.weight, a.T if a.ndim == 2 else a) if kind == "w" else put(mod.bias, a)
    return m.to(device), Vocab(chars)


class TorchLM:
    """Scoring backend with the same interface as tinylm.NumpyLM (.vocab, .logp10); float32, no autocast, no TF32."""

    def __init__(self, path, device="cpu"):
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        self.model, self.vocab = from_npz(path, device)
        self.model.eval()
        self.device = device

    @torch.no_grad()
    def logp10(self, ids):
        z = self.model(torch.from_numpy(np.asarray(ids)).to(self.device)).float()
        return (F.log_softmax(z, dim=-1) / tinylm.LN10).double().cpu().numpy()
