"""E1c: model compression, written and read back in Python.

  (i)  lossless: successor ids sorted inside a context are stored as varint deltas; counts, context totals, unigram counts and the context-id
       list are varints too. decode(encode(x)) is byte-identical to the SJLM / SJT30001 files it was made from.
  (ii) lossy: on top of (i), the successor COUNTS of each table (bigram table, trigram table) are replaced by an index into that table's own
       level list (2**bits levels, log-spaced, exact for small integers), 8 or 4 bits per entry. Decoding writes each count as its level's
       representative integer. Context totals, unigram counts and successor ids stay exact.
       Levels (make_levels): if the table has <= 2**bits distinct counts they are all kept (no loss). Otherwise the integers cmin..m are exact
       levels and m..cmax is a geometric ladder with ratio >= 1 + 1/m (so rounded rungs are distinct integers), m the smallest for which the
       ladder fits. A count goes to the nearest level in log scale: boundaries are the geometric means of neighbouring levels, so the level
       index is monotone in the count and a level's representative lies inside its own interval (test_e1 asserts both).
       Fix-up at decode: a context's kept counts after quantization can add up to more than its stored total (the total is exact); the decoded
       file then gets total' = max(total, sum of decoded counts), so back-off weights stay positive. It touches only contexts where the rounding
       overshot.

Encoded container "SJZ00001": magic, mode byte (0 lossless, 8, 4), has-trigram byte, then length-prefixed blocks (see _bigram_blocks / _tri_blocks).
The decoded files are ordinary SJLM / SJT30001 files, so harness.py loads them unchanged. A lossy bigram file has another SHA-256 than the
original: the kn.sjkn / classes.sjc / trigram table bound to the original are re-bound (hash field rewritten) by `decode`.

  python3 compress.py encode --lm B0/bigram.sjlm [--tri T/tri-t3.sjt3] --bits 0|8|4 --out FILE.sjz
  python3 compress.py decode --in FILE.sjz --out-dir DIR [--kn B0/kn.sjkn] [--classes B0/classes.sjc]
  python3 compress.py report --lm B0/bigram.sjlm [--tri ...] --out report.json    (raw / zip / encoded / zip(encoded) bytes for all three modes)
"""
import argparse
import hashlib
import io
import os
import struct
import zipfile

import numpy as np

import common
import sjlm_io

MAGIC = b"SJZ00001"
MODES = (0, 8, 4)


# ---------- varint (LEB128), vectorised ----------
def varint_encode(a):
    a = np.asarray(a, dtype=np.uint64)
    if a.size == 0:
        return b""
    nb = np.ones(a.size, np.int64)
    for k in range(1, 10):
        nb += a >= (np.uint64(1) << np.uint64(7 * k))
    ends = np.cumsum(nb)
    starts = ends - nb
    out = np.zeros(int(ends[-1]), np.uint8)
    for j in range(10):
        m = nb > j
        if not m.any():
            break
        v = ((a[m] >> np.uint64(7 * j)) & np.uint64(0x7F)).astype(np.uint8)
        out[starts[m] + j] = v | ((nb[m] > j + 1).astype(np.uint8) << 7)
    return out.tobytes()


def varint_decode(buf, n):
    """First n varints of buf -> uint64 array. Raises if buf holds fewer, or if bytes are left over (a block holds exactly its values)."""
    if n == 0:
        if len(buf):
            raise ValueError("unexpected bytes in an empty varint block")
        return np.zeros(0, np.uint64)
    b = np.frombuffer(buf, np.uint8)
    term = np.flatnonzero(b < 128)
    if len(term) != n or term[-1] != len(b) - 1:
        raise ValueError(f"varint block holds {len(term)} values, expected {n}")
    starts = np.concatenate(([0], term[:-1] + 1))
    lens = term - starts + 1
    within = np.arange(len(b)) - np.repeat(starts, lens)
    vals = (b.astype(np.uint64) & np.uint64(0x7F)) << (within * 7).astype(np.uint64)
    return np.add.reduceat(vals, starts)


# ---------- log-count levels ----------
def make_levels(counts, bits):
    """Sorted unique uint32 representative values (<= 2**bits) for a table's counts; see the module docstring."""
    L = 1 << bits
    uniq = np.unique(np.asarray(counts, dtype=np.int64))
    if len(uniq) <= L:
        return uniq.astype(np.uint32)
    cmin, cmax = int(uniq[0]), int(uniq[-1])
    m = cmin
    while True:
        k = L - (m - cmin + 1)
        if k < 1:
            raise AssertionError("no ladder fits")   # cannot happen: at m = cmin + L - 2 the ratio is cmax / m > 1 + 1 / m
        ratio = (cmax / m) ** (1.0 / k)
        if ratio >= 1 + 1.0 / m:
            break
        m += 1
    reps = list(range(cmin, m + 1)) + [int(np.floor(m * ratio ** j + 0.5)) for j in range(1, k + 1)]
    reps = np.unique(np.minimum(np.array(reps, dtype=np.int64), cmax))
    if reps[-1] != cmax:
        reps = np.append(reps, cmax)
    return reps.astype(np.uint32)


def level_index(counts, reps):
    """Nearest level in log scale; monotone in the count. Boundaries are the geometric means of neighbouring levels."""
    r = reps.astype(np.float64)
    return np.searchsorted(np.sqrt(r[:-1] * r[1:]), np.asarray(counts, dtype=np.float64), side="right")


def pack_idx(idx, bits):
    idx = idx.astype(np.uint8)
    if bits == 8:
        return idx.tobytes()
    if len(idx) % 2:
        idx = np.append(idx, np.uint8(0))
    return (idx[0::2] | (idx[1::2] << 4)).tobytes()


def unpack_idx(buf, n, bits):
    b = np.frombuffer(buf, np.uint8)
    if bits == 8:
        return b[:n]
    return np.stack([b & 15, b >> 4], axis=1).reshape(-1)[:n]


# ---------- container ----------
def blk(b):
    return struct.pack("<Q", len(b)) + b


def read_blocks(buf, pos):
    out = []
    while pos < len(buf):
        (n,) = struct.unpack_from("<Q", buf, pos)
        out.append(bytes(buf[pos + 8:pos + 8 + n])); pos += 8 + n
    return out


def _counts_block(cnt, bits):
    if bits == 0:
        return blk(varint_encode(cnt))
    reps = make_levels(cnt, bits)
    return blk(struct.pack("<I", len(reps)) + reps.astype("<u4").tobytes() + pack_idx(level_index(cnt, reps), bits))


def _counts_unblock(b, n, bits):
    if bits == 0:
        return varint_decode(b, n).astype(np.uint32)
    (k,) = struct.unpack_from("<I", b, 0)
    reps = np.frombuffer(b, "<u4", k, 4)
    return reps[unpack_idx(b[4 + 4 * k:], n, bits)].astype(np.uint32)


def _next_deltas(nxt, off):
    nxt = nxt.astype(np.int64)
    prev = np.empty_like(nxt)
    if len(nxt):
        prev[1:] = nxt[:-1]
        prev[off[:-1].astype(np.int64)] = -1
    return nxt - prev


def _next_undelta(d, off):
    cs = np.cumsum(d.astype(np.int64))
    base = np.concatenate(([0], cs))[off[:-1].astype(np.int64)]
    return (cs - np.repeat(base, np.diff(off.astype(np.int64))) - 1).astype(np.uint32)


def _structure_blocks(first, tot, off, nxt, cnt, bits):
    """context keys (delta), totals, per-context entry counts, successor deltas, counts."""
    return [blk(varint_encode(np.diff(first.astype(np.int64), prepend=0))), blk(varint_encode(tot)),
            blk(varint_encode(np.diff(off.astype(np.int64)))), blk(varint_encode(_next_deltas(nxt, off))), _counts_block(cnt, bits)]


def _structure_unblock(bl, C, E, bits):
    first = np.cumsum(varint_decode(bl[0], C).astype(np.int64))
    tot = varint_decode(bl[1], C)
    off = np.concatenate(([0], np.cumsum(varint_decode(bl[2], C).astype(np.int64)))).astype(np.uint32)
    nxt = _next_undelta(varint_decode(bl[3], E), off)
    cnt = _counts_unblock(bl[4], E, bits)
    return first, tot, off, nxt, cnt


def fix_totals(tot, off, cnt):
    """total' = max(total, sum of the decoded counts) per context (lossy decode only)."""
    if len(tot) == 0:
        return tot
    s = np.add.reduceat(cnt.astype(np.uint64), off[:-1].astype(np.int64))
    return np.maximum(tot, s)


def encode(model, tri=None, bits=0):
    """model: bytes of an .sjlm; tri: bytes of an .sjt3 or None; bits: 0 (lossless), 8 or 4."""
    if bits not in MODES:
        raise ValueError("bits must be 0, 8 or 4")
    d = sjlm_io.read_sjlm(model)
    C, E = len(d["ctx"]), len(d["nxt"])
    parts = [MAGIC, struct.pack("<BB", bits, tri is not None)]
    parts += [blk(struct.pack("<IQQdII", d["V"], d["N"], d["eos_total"], d["D"], C, E)), blk(d["vocab_raw"]), blk(varint_encode(d["uni"])),
              *_structure_blocks(d["ctx"], d["tot"], d["off"], d["nxt"], d["cnt"], bits)]
    if tri is not None:
        t = sjlm_io.read_tri(tri)
        Ct, Et = len(t["key"]), len(t["nxt"])
        parts += [blk(struct.pack("<Id32sII", t["V"], t["D"], t["model_sha"], Ct, Et)),
                  *_structure_blocks(t["key"], t["tot"], t["off"], t["nxt"], t["cnt"], bits)]
    return b"".join(parts)


def decode(blob):
    """-> (model bytes, tri bytes or None). A lossy bigram file has a new SHA-256; the returned trigram table is bound to it."""
    if blob[:8] != MAGIC:
        raise ValueError("bad magic")
    bits, has_tri = struct.unpack_from("<BB", blob, 8)
    bl = read_blocks(blob, 10)
    V, N, eos, D, C, E = struct.unpack_from("<IQQdII", bl[0])
    first, tot, off, nxt, cnt = _structure_unblock(bl[3:8], C, E, bits)
    uni = varint_decode(bl[2], V)
    if bits:
        tot = fix_totals(tot, off, cnt)
    model = sjlm_io.write_sjlm({"V": V, "N": N, "eos_total": eos, "D": D, "vocab_raw": bl[1], "uni": uni, "ctx": first, "tot": tot, "off": off,
                                "nxt": nxt, "cnt": cnt})
    if not has_tri:
        return model, None
    Vt, Dt, sha, Ct, Et = struct.unpack_from("<Id32sII", bl[8])
    key, ttot, toff, tnxt, tcnt = _structure_unblock(bl[9:14], Ct, Et, bits)
    if bits:
        ttot = fix_totals(ttot, toff, tcnt)
        sha = hashlib.sha256(model).digest()
    tri = sjlm_io.write_tri({"V": Vt, "D": Dt, "model_sha": sha, "key": key.astype(np.uint64), "tot": ttot, "off": toff, "nxt": tnxt, "cnt": tcnt})
    return model, tri


def rebind(side, new_model, sha_at):
    """Rewrite the model-hash field of a kn.sjkn (offset 12) or classes.sjc (offset 8) for another bigram file."""
    return side[:sha_at] + hashlib.sha256(new_model).digest() + side[sha_at + 32:]


def zip_size(data, name="x"):
    """Size of a zip archive (deflate, level 9) holding the bytes as one member."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(name, data)
    return buf.tell()


def report(model, tri=None):
    """{mode: sizes} for the three encodings; raw = the SJLM (+ SJT3) files as they are."""
    raw = model + (tri or b"")
    out = {"raw": {"bytes": len(raw), "zip": zip_size(raw)}}
    for bits in MODES:
        blob = encode(model, tri, bits)
        out["lossless" if bits == 0 else f"q{bits}"] = {"bytes": len(blob), "zip": zip_size(blob)}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("encode", "report"):
        p = sub.add_parser(n)
        p.add_argument("--lm", required=True); p.add_argument("--tri"); p.add_argument("--out", required=True)
    sub.choices["encode"].add_argument("--bits", type=int, choices=MODES, default=0)
    d = sub.add_parser("decode")
    d.add_argument("--in", dest="inp", required=True); d.add_argument("--out-dir", required=True)
    d.add_argument("--kn"); d.add_argument("--classes")
    a = ap.parse_args(argv)
    rd = lambda p: open(p, "rb").read() if p else None
    if a.cmd == "encode":
        blob = encode(rd(a.lm), rd(a.tri), a.bits)
        open(a.out, "wb").write(blob)
        print(f"{a.out}: {len(blob)} bytes (bits={a.bits})")
    elif a.cmd == "report":
        r = report(rd(a.lm), rd(a.tri))
        r["inputs"] = common.provenance([p for p in (a.lm, a.tri) if p])
        common.write_json(a.out, r)
        for k, v in r.items():
            if k != "inputs":
                print(f"{k:9s} bytes={v['bytes']:>12,} zip={v['zip']:>12,}")
    else:
        blob = open(a.inp, "rb").read()
        model, tri = decode(blob)
        os.makedirs(a.out_dir, exist_ok=True)
        w = lambda name, b: open(os.path.join(a.out_dir, name), "wb").write(b)
        w("bigram.sjlm", model)
        if tri is not None:
            w("tri.sjt3", tri)
        if a.kn:
            w("kn.sjkn", rebind(rd(a.kn), model, 12))
        if a.classes:
            w("classes.sjc", rebind(rd(a.classes), model, 8))
        print(f"decoded bigram.sjlm sha256={hashlib.sha256(model).hexdigest()}" + (f" tri.sjt3 sha256={hashlib.sha256(tri).hexdigest()}" if tri else ""))


if __name__ == "__main__":
    main()
