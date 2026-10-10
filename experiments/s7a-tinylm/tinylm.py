"""S7a core, numpy only (no torch import): vocabulary, context key, the .npz format, the forward pass, and LL.

Contract: docs/contracts/s7a-tinylm.md section 2.3. Everything that scores a candidate or computes a perplexity goes through
`ll_many` (one function), with the exact input sequence [BOS] + k + c.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in ("reference/proto", "tools", "experiments/s2"):
    _p = os.path.join(ROOT, *_p.split("/"))
    if _p not in sys.path:
        sys.path.insert(0, _p)
from lm import context_key, _HAN  # noqa: E402  reference/proto/lm.py: the engine's context_key (Python mirror of core/src/learn.rs)

PAD, UNK, BOS = 0, 1, 2
SPECIALS = ["<pad>", "<unk>", "<bos>"]
CTX = 64
LN10 = float(np.log(10.0))
LAYER_KEYS = ["ln1_w", "ln1_b", "qkv_w", "qkv_b", "proj_w", "proj_b", "ln2_w", "ln2_b", "fc1_w", "fc1_b", "fc2_w", "fc2_b"]


# ------------------------------------------------------------------ context
def han_tail(prefix, n):
    """The last <= n consecutive Han characters of prefix (same Han ranges as context_key); "" when none."""
    tail = []
    for c in reversed(prefix):
        if len(tail) == n or not any(a <= ord(c) <= b for a, b in _HAN):
            break
        tail.append(c)
    return "".join(reversed(tail))


def row_key(prefix, n=2):
    """k for a row. n = 2 is the engine's context_key (its sentinel "^" is the empty string here); n = 16 is the record-only variant."""
    return context_key(prefix) if n == 2 else han_tail(prefix, n)


# ------------------------------------------------------------------ vocabulary
class Vocab:
    """ids 0 / 1 / 2 are PAD / UNK / BOS; characters follow from id 3. Characters outside the table map to UNK."""

    def __init__(self, chars):
        self.chars = list(chars)
        assert len(set(self.chars)) == len(self.chars) and all(len(c) == 1 for c in self.chars)
        self.ids = {c: i + 3 for i, c in enumerate(self.chars)}
        self.size = len(self.chars) + 3

    def enc(self, s):
        return [self.ids.get(c, UNK) for c in s]


# ------------------------------------------------------------------ the .npz format (the only definition of it)
def save_params(path, params, chars, n_layer, n_head, ctx=CTX):
    """params: dict of float arrays with keys emb, pos, lnf_w, lnf_b and l{i}.{LAYER_KEYS}; weights are (in, out) so y = x @ W + b."""
    d = {k: np.asarray(v, dtype=np.float32) for k, v in params.items()}
    d["cfg"] = np.array([n_layer, n_head, ctx], dtype=np.int64)
    d["chars"] = np.array(list(chars), dtype="<U1")
    np.savez(path, **d)


def random_params(chars, n_layer, d, n_head, seed=0, ctx=CTX):
    """Random (non-trivial LayerNorm / bias) weights, for tests and for exercising the numpy forward without torch."""
    rng = np.random.default_rng(seed)
    V = len(chars) + 3

    def w(*shape, s=0.2):
        return rng.normal(0, s, shape).astype(np.float32)

    p = {"emb": w(V, d), "pos": w(ctx, d), "lnf_w": 1 + w(d, s=0.1), "lnf_b": w(d, s=0.1)}
    for i in range(n_layer):
        for k, shape in (("ln1_w", (d,)), ("ln1_b", (d,)), ("qkv_w", (d, 3 * d)), ("qkv_b", (3 * d,)), ("proj_w", (d, d)), ("proj_b", (d,)),
                         ("ln2_w", (d,)), ("ln2_b", (d,)), ("fc1_w", (d, 4 * d)), ("fc1_b", (4 * d,)), ("fc2_w", (4 * d, d)), ("fc2_b", (d,))):
            p[f"l{i}.{k}"] = (1 + w(*shape, s=0.1)) if k.endswith(("ln1_w", "ln2_w")) else w(*shape)
    return p


# ------------------------------------------------------------------ numpy forward
def _ln(x, w, b):
    mu = x.mean(-1, keepdims=True)
    var = ((x - mu) ** 2).mean(-1, keepdims=True)
    return (x - mu) / np.sqrt(var + 1e-5) * w + b


def _gelu(x):
    """GELU, tanh form (torch: F.gelu(approximate="tanh")); chosen because numpy has no erf."""
    return 0.5 * x * (1.0 + np.tanh(0.7978845608028654 * (x + 0.044715 * x ** 3)))


class NumpyLM:
    """Decoder-only pre-LN Transformer, tied embedding, learned positions, causal attention. Reads the .npz of `save_params`."""

    def __init__(self, path, dtype=np.float64):
        # float64 by default: the weights are float32 in the file, so this adds no information but removes the numpy side's rounding
        # from the parity gate (torch's own float32 rounding is what remains); latency.py also measures float32.
        z = np.load(path, allow_pickle=False)
        self.n_layer, self.n_head, self.ctx = (int(x) for x in z["cfg"])
        self.vocab = Vocab([str(c) for c in z["chars"]])
        self.dtype = dtype
        self.p = {k: z[k].astype(dtype) for k in z.files if k not in ("cfg", "chars")}
        assert self.p["emb"].shape[0] == self.vocab.size, "emb rows != vocabulary size"
        self.d = self.p["emb"].shape[1]

    def logits(self, ids):
        ids = np.asarray(ids)
        B, T = ids.shape
        assert T <= self.ctx
        p, H = self.p, self.n_head
        dh = self.d // H
        x = p["emb"][ids] + p["pos"][:T]
        mask = np.triu(np.ones((T, T), dtype=bool), 1)
        for i in range(self.n_layer):
            g = lambda k: p[f"l{i}.{k}"]  # noqa: E731
            q, k_, v = np.split(_ln(x, g("ln1_w"), g("ln1_b")) @ g("qkv_w") + g("qkv_b"), 3, axis=-1)
            q, k_, v = (t.reshape(B, T, H, dh).transpose(0, 2, 1, 3) for t in (q, k_, v))
            s = (q @ k_.transpose(0, 1, 3, 2)) / np.sqrt(dh).astype(self.dtype)
            s = np.where(mask, -np.inf, s)
            s = np.exp(s - s.max(-1, keepdims=True))
            a = (s / s.sum(-1, keepdims=True)) @ v
            x = x + a.transpose(0, 2, 1, 3).reshape(B, T, self.d) @ g("proj_w") + g("proj_b")
            x = x + _gelu(_ln(x, g("ln2_w"), g("ln2_b")) @ g("fc1_w") + g("fc1_b")) @ g("fc2_w") + g("fc2_b")
        return _ln(x, p["lnf_w"], p["lnf_b"]) @ p["emb"].T

    def logp10(self, ids):
        """(B, T) ids -> (B, T, V) float64 log10 P(next token | tokens up to and including position t)."""
        z = self.logits(ids).astype(np.float64)
        z = z - z.max(-1, keepdims=True)
        return (z - np.log(np.exp(z).sum(-1, keepdims=True))) / LN10


# ------------------------------------------------------------------ LL: the one scoring function
def ll_many(model, items, bs=64):
    """items: [(k, c)]. LL = sum over the positions of c of log10 P(c_i | [BOS] + k + c_<i), UNK for characters outside the table.
    model: .vocab (Vocab) and .logp10(ids (B, T) int64, right-padded with PAD) -> (B, T, V). Used for candidates AND perplexity."""
    voc = model.vocab
    out = np.zeros(len(items))
    for s in range(0, len(items), bs):
        seqs = []
        for k, c in items[s:s + bs]:
            if len(c) > CTX - 1:
                raise ValueError(f"candidate of {len(c)} characters does not fit the context {CTX}")
            k = k[max(0, len(k) - (CTX - 1 - len(c))):]   # only when [BOS] + k + c would exceed the context: drop the oldest context characters
            seqs.append(([BOS] + voc.enc(k) + voc.enc(c), 1 + len(k)))
        T = max(len(q) for q, _ in seqs)
        ids = np.full((len(seqs), T), PAD, dtype=np.int64)
        for b, (q, _) in enumerate(seqs):
            ids[b, :len(q)] = q
        lp = model.logp10(ids)
        for b, (q, st) in enumerate(seqs):
            if len(q) > st:
                out[s + b] = lp[b, np.arange(st - 1, len(q) - 1), q[st:]].sum()
    return out


def ll(model, k, c):
    return float(ll_many(model, [(k, c)])[0])
