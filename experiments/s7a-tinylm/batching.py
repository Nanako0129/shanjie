"""S7a training batches, numpy only. One sequence = BOS + one Han run (contract section 2.2); sequences are never packed together, so
the model never sees another run as context (at scoring time it sees only [BOS] + k + c).

A sequence longer than CTX + 1 tokens is cut into pieces of CTX + 1 tokens that overlap by one token, so every character is a target
exactly once; only the first piece starts with BOS. Batches hold sequences of exactly one length (no padding).
"""
import numpy as np

from tinylm import BOS, CTX

PIECE = CTX + 1   # CTX inputs + the one-step-shifted targets


def pieces(ids):
    """ids: flat token array, every run preceded by BOS. Returns (starts, lengths) of the pieces."""
    st = np.flatnonzero(ids == BOS)
    ln = np.diff(np.append(st, len(ids)))
    short = ln <= PIECE
    S, L = [st[short]], [ln[short]]
    for s, l in zip(st[~short].tolist(), ln[~short].tolist()):
        off, ps, pl = 0, [], []
        while True:
            n = min(PIECE, l - off)
            ps.append(s + off); pl.append(n)
            if off + n >= l:
                break
            off += PIECE - 1
        S.append(np.array(ps, dtype=st.dtype)); L.append(np.array(pl, dtype=ln.dtype))
    return np.concatenate(S), np.concatenate(L)


def make_batches(lengths, tokens_per_batch, rng):
    """Returns [(index array into the piece list)] covering every piece once: random order inside each exact length,
    batches of max(1, tokens_per_batch // length) pieces, the batch list shuffled."""
    order = rng.permutation(len(lengths))
    order = order[np.argsort(lengths[order], kind="stable")]
    ls = lengths[order]
    out = []
    for l in np.unique(ls):
        lo, hi = np.searchsorted(ls, l, "left"), np.searchsorted(ls, l, "right")
        b = max(1, tokens_per_batch // int(l))
        out += [order[i:min(i + b, hi)] for i in range(lo, hi, b)]
    return [out[i] for i in rng.permutation(len(out))]
