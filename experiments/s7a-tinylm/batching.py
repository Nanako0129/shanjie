"""S7a training batches, numpy only. One sequence = BOS + one Han run (contract section 2.2); sequences are never packed together, so
the model never sees another run as context (at scoring time it sees only [BOS] + k + c).

A sequence longer than CTX + 1 tokens is cut into pieces of CTX + 1 tokens that overlap by one token, so every character is a target
exactly once; only the first piece starts with BOS. Batches hold sequences of exactly one length (no padding).
The epoch and its batch order (build_epoch, plan) live here, not in train.py, so they are tested without torch.
"""
import os

import numpy as np

import prep
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


def parse_extra(arg):
    """'PATH' -> (PATH, 1); 'PATH:5' -> (PATH, 5). A last ':' followed by anything but digits belongs to the path."""
    path, sep, w = arg.rpartition(":")
    return (path, int(w)) if sep and w.isdigit() else (arg, 1)


def build_epoch(work, extra=()):
    """ids-wiki.npy once, ids-coll.npy prep.COLL_WEIGHT times, then each (path, weight) of `extra` (ids files from prep.py text,
    weight = integer copies of every piece per epoch, the way the colloquial x5 is done) appended after them in the order given.
    Without `extra` the arrays are exactly the S7a epoch. Returns (ids, starts, lengths)."""
    wiki, coll = np.load(os.path.join(work, "ids-wiki.npy")), np.load(os.path.join(work, "ids-coll.npy"))
    parts = [wiki, coll]
    ws, wl = pieces(wiki)
    cs, cl = pieces(coll)
    starts = [ws] + [cs + len(wiki)] * prep.COLL_WEIGHT
    lengths = [wl] + [cl] * prep.COLL_WEIGHT
    base = len(wiki) + len(coll)
    for path, weight in extra:
        if not isinstance(weight, int) or weight < 1:
            raise ValueError(f"{path}: weight must be an integer >= 1, got {weight!r}")
        ex = np.load(path)
        if ex.dtype != wiki.dtype or not len(ex) or ex[0] != BOS:
            raise ValueError(f"{path}: expected {wiki.dtype} ids starting with BOS (prep.py text)")
        es, el = pieces(ex)
        parts.append(ex); starts += [es + base] * weight; lengths += [el] * weight
        base += len(ex)
    return np.concatenate(parts), np.concatenate(starts), np.concatenate(lengths)


def plan(work, extra, tokens_per_batch, seed, max_steps=0):
    """train.py's data: the epoch, its shuffled batches (rng = default_rng(seed), as S7a), cut to max_steps when given.
    The number of steps is len(batches): one epoch, so it follows from the total (weighted) token count (see train.py). Returns (ids, starts, lengths, batches)."""
    rng = np.random.default_rng(seed)
    ids, starts, lengths = build_epoch(work, extra)
    batches = make_batches(lengths, tokens_per_batch, rng)
    return ids, starts, lengths, batches[:max_steps] if max_steps else batches
