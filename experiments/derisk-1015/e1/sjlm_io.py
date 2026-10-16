"""numpy reader / writer for the SJLM bigram file (layout: tools/build_lm.py docstring) and for the trigram table SJT30001.

SJT30001 (little-endian), the trigram table T_k adds to a bigram model B0:
  magic        8 bytes  b"SJT30001"
  V            u32      vocabulary size of the bigram model it is bound to
  D            f64      absolute discount of the trigram level
  model_sha    32 bytes SHA-256 of the bigram .sjlm file (the table is meaningless for another model, like kn.sjkn)
  C            u32      number of (u, v) contexts that keep at least one entry
  E            u32      number of entries
  ctx_key      u64[C]   u * V + v for the two history word ids (u first), sorted ascending: one binary search finds a context
  ctx_total    u64[C]   the context's total count BEFORE pruning (the pruned mass goes to the back-off, like SJLM)
  ctx_off      u32[C+1] entry intervals
  next_id      u32[E]   ascending inside a context
  count        u32[E]   weighted count (>= the prune threshold)
Size = 64 + 20 * C + 8 * E bytes (common.TRI_BYTES_PER_*): the on-disk measure E1 reports.
"""
import struct

import numpy as np

SJLM_MAGIC = b"SJLM0001"
TRI_MAGIC = b"SJT30001"
TRI_HEAD = struct.calcsize("<8sId32sII")   # 60
TRI_FIXED = TRI_HEAD + 4                    # + the closing ctx_off entry: size = TRI_FIXED + 20 * contexts + 8 * entries


def read_sjlm(b):
    """b: bytes of an .sjlm file. Returns a dict of numpy arrays (views of b) and header values."""
    if b[:8] != SJLM_MAGIC:
        raise ValueError("bad magic")
    V, N, eos_total, D = struct.unpack_from("<IQQd", b, 8)
    p = 8 + struct.calcsize("<IQQd")
    v0 = p
    for _ in range(V):
        (n,) = struct.unpack_from("<H", b, p)
        p += 2 + n
    d = {"V": V, "N": N, "eos_total": eos_total, "D": D, "vocab_raw": bytes(b[v0:p])}
    d["uni"] = np.frombuffer(b, "<u8", V, p); p += 8 * V
    (C,) = struct.unpack_from("<I", b, p); p += 4
    d["ctx"] = np.frombuffer(b, "<u4", C, p); p += 4 * C
    d["tot"] = np.frombuffer(b, "<u8", C, p); p += 8 * C
    d["off"] = np.frombuffer(b, "<u4", C + 1, p); p += 4 * (C + 1)
    (E,) = struct.unpack_from("<I", b, p); p += 4
    d["nxt"] = np.frombuffer(b, "<u4", E, p); p += 4 * E
    d["cnt"] = np.frombuffer(b, "<u4", E, p); p += 4 * E
    if p != len(b):
        raise ValueError("trailing bytes")
    return d


def write_sjlm(d):
    C, E = len(d["ctx"]), len(d["nxt"])
    return b"".join([
        SJLM_MAGIC, struct.pack("<IQQd", d["V"], d["N"], d["eos_total"], d["D"]), d["vocab_raw"],
        np.asarray(d["uni"], "<u8").tobytes(), struct.pack("<I", C), np.asarray(d["ctx"], "<u4").tobytes(),
        np.asarray(d["tot"], "<u8").tobytes(), np.asarray(d["off"], "<u4").tobytes(), struct.pack("<I", E),
        np.asarray(d["nxt"], "<u4").tobytes(), np.asarray(d["cnt"], "<u4").tobytes()])


def read_tri(b):
    magic, V, D, sha, C, E = struct.unpack_from("<8sId32sII", b, 0)
    if magic != TRI_MAGIC:
        raise ValueError("bad trigram magic")
    p = TRI_HEAD
    d = {"V": V, "D": D, "model_sha": sha}
    d["key"] = np.frombuffer(b, "<u8", C, p); p += 8 * C
    d["tot"] = np.frombuffer(b, "<u8", C, p); p += 8 * C
    d["off"] = np.frombuffer(b, "<u4", C + 1, p); p += 4 * (C + 1)
    d["nxt"] = np.frombuffer(b, "<u4", E, p); p += 4 * E
    d["cnt"] = np.frombuffer(b, "<u4", E, p); p += 4 * E
    if p != len(b):
        raise ValueError("trailing bytes in trigram table")
    return d


def write_tri(d):
    C, E = len(d["key"]), len(d["nxt"])
    return b"".join([
        struct.pack("<8sId32sII", TRI_MAGIC, d["V"], d["D"], d["model_sha"], C, E),
        np.asarray(d["key"], "<u8").tobytes(), np.asarray(d["tot"], "<u8").tobytes(),
        np.asarray(d["off"], "<u4").tobytes(), np.asarray(d["nxt"], "<u4").tobytes(), np.asarray(d["cnt"], "<u4").tobytes()])
