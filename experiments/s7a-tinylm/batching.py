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
    """'PATH' -> (PATH, 1); 'PATH:5' -> (PATH, 5). The weight is ASCII digits and >= 1; anything else after the last ':' is a ValueError
    (a path that itself ends in ':something' is not supported)."""
    path, sep, w = arg.rpartition(":")
    if not sep:
        return arg, 1
    if not (path and w.isascii() and w.isdigit() and int(w) >= 1):
        raise ValueError(f"--extra {arg!r}: expected PATH or PATH:WEIGHT with WEIGHT an integer >= 1 (a path ending in ':something' is not supported)")
    return path, int(w)


def build_epoch(work, extra=None):
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
    for path, weight in extra or ():
        if not isinstance(weight, int) or weight < 1:
            raise ValueError(f"{path}: weight must be an integer >= 1, got {weight!r}")
        ex = np.load(path)
        if ex.dtype != wiki.dtype or not len(ex) or ex[0] != BOS:
            raise ValueError(f"{path}: expected {wiki.dtype} ids starting with BOS (prep.py text)")
        es, el = pieces(ex)
        parts.append(ex); starts += [es + base] * weight; lengths += [el] * weight
        base += len(ex)
    return np.concatenate(parts), np.concatenate(starts), np.concatenate(lengths)


def flag_path(ids_path):
    """ids-<name>.npy -> flags-<name>.u8 in the same directory (prep.py --changed-flags)."""
    d, base = os.path.split(ids_path)
    return os.path.join(d, "flags-" + base[len("ids-"):-len(".npy")] + ".u8")


def _sha256(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _flags_sha_from_report(ids_path):
    """The flags_sha256 prep recorded for this source: charcount.json's report[wiki|coll] for ids-wiki/ids-coll, text-<name>.json for an extra;
    None when the report or the key is absent (then only the run count is checked)."""
    import json
    d, base = os.path.split(ids_path)
    name = base[len("ids-"):-len(".npy")]
    try:
        if name in ("wiki", "coll"):
            rep = json.load(open(os.path.join(d, "charcount.json"), encoding="utf-8"))["report"]
            return (rep.get(name) or {}).get("flags_sha256")
        return json.load(open(os.path.join(d, f"text-{name}.json"), encoding="utf-8")).get("flags_sha256")
    except (OSError, ValueError, KeyError):
        return None


def token_flags(work, extra=None):
    """The --changed-flags bits as a per-token uint8 array aligned with build_epoch's concatenated ids (wiki, coll, then each extra, once each):
    every token of a run (its BOS included) carries that run's flag. A source without its flags file, or whose file does not have one byte per
    run, is an error: a run that was never flagged must not silently count as unchanged."""
    paths = [os.path.join(work, "ids-wiki.npy"), os.path.join(work, "ids-coll.npy")] + [p for p, _ in extra or ()]
    out = []
    for ip in paths:
        fp = flag_path(ip)
        if not os.path.isfile(fp):
            raise ValueError(f"{fp} is missing (prep.py {'runs' if ip.endswith(('ids-wiki.npy', 'ids-coll.npy')) else 'text'} --changed-flags)")
        want = _flags_sha_from_report(ip)
        if want is not None and _sha256(fp) != want:   # review P3-d: a stale flags file with the same run count must not pass
            raise ValueError(f"{fp}: sha256 differs from flags_sha256 in its prep report (re-run prep.py with --changed-flags)")
        ids, fl = np.load(ip, mmap_mode="r"), np.fromfile(fp, dtype=np.uint8)
        st = np.flatnonzero(ids == BOS)
        if len(fl) != len(st) or fl.max(initial=0) > 1:
            raise ValueError(f"{fp}: {len(fl)} bytes, but {ip} has {len(st)} runs (or a byte other than 0/1)")
        out.append(np.repeat(fl, np.diff(np.append(st, len(ids)))))
    return np.concatenate(out)


def reweight_changed(starts, lengths, changed, ratio, rng):
    """SR-TW-V V3. starts/lengths: the epoch's pieces; changed: bool per piece (its run's flag). Keeps every unchanged piece exactly as it is
    (so the source weights, wiki x1 and colloquial x5, are untouched) and repeats the changed ones as a group, floor(m) times plus a random
    share of the rest, so that changed pieces hold `ratio` of all target tokens (length - 1 per piece). m = ratio / (1 - ratio) * (unchanged
    targets) / (changed targets) can be below 1 (the changed ones are then thinned out). Returns (starts, lengths); the order is irrelevant
    (make_batches shuffles)."""
    if not 0 < ratio < 1:
        raise ValueError(f"changed-weight ratio must be in (0, 1), got {ratio}")
    w = lengths - 1
    changed = np.asarray(changed, dtype=bool)
    if not changed.any() or changed.all():
        raise ValueError("changed-weight needs both changed and unchanged pieces")
    m = ratio / (1 - ratio) * w[~changed].sum() / w[changed].sum()
    ci = np.flatnonzero(changed)
    k = int(m)
    idx = np.concatenate([np.flatnonzero(~changed)] + [ci] * k + [ci[rng.random(len(ci)) < m - k]])
    return starts[idx], lengths[idx]


def plan(work, extra, tokens_per_batch, seed, max_steps=0, changed_weight=None, tokflag=None):
    """train.py's data: the epoch, its shuffled batches (rng = default_rng(seed), as S7a), cut to max_steps when given.
    The number of steps is len(batches) (see train.py). Returns (ids, starts, lengths, batches).
    changed_weight (SR-TW-V V3): reweight_changed with tokflag = token_flags(work, extra), before the batches are made; None = S7a's epoch."""
    rng = np.random.default_rng(seed)
    ids, starts, lengths = build_epoch(work, extra)   # extra: None/empty = none
    if changed_weight is not None:
        starts, lengths = reweight_changed(starts, lengths, tokflag[starts] == 1, changed_weight, rng)
    batches = make_batches(lengths, tokens_per_batch, rng)
    return ids, starts, lengths, batches[:max_steps] if max_steps else batches
