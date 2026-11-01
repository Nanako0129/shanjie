#!/usr/bin/env python3
"""DECIDE-0 (research log 2026-10-31): a copy of a .sjtl with only the formal standard alpha and delta changed.

  python3 experiments/decide0/patch_sjtl.py IN OUT formal ALPHA DELTA

The standard (alpha, tau) of each profile sit in the main header and the standard deltas right after it
(experiments/s7a-tinylm/export_sjtl.py HEADER and DELTAS); tau, the other profile, the conservative and aggressive levels, the
weights and the hashes are copied byte for byte. The copy is read back with export_sjtl.read_sjtl and must differ from the input
only in the two values asked for.

Formal only. The chat fusion with the colloquial table does not read these header values (core/src/colloquial.rs uses its own
registered cells), and the core turns the colloquial term on only for the shipping file's SHA-256, so any patched copy would run
chat with the colloquial and reading-channel terms off (DECIDE-0 amendment 1).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "s7a-tinylm"))
import export_sjtl as X  # noqa: E402


def patch(src, dst, prof, alpha, delta):
    b = bytearray(open(src, "rb").read())
    f = list(X.HEADER.unpack_from(b, 0))
    if not X.VERSION_V2 <= f[1] <= X.VERSION_V4:
        raise SystemExit(f"STOP: .sjtl version {f[1]}; this tool knows the header of versions 2 to 4 only")
    i = {"chat": 8, "formal": 10}[prof]           # ca, ct, fa, ft follow the 7 u32 after the magic
    f[i] = alpha
    X.HEADER.pack_into(b, 0, *f)
    d = list(X.DELTAS.unpack_from(b, X.HEADER.size))
    d[{"chat": 0, "formal": 1}[prof]] = delta
    X.DELTAS.pack_into(b, X.HEADER.size, *d)
    tmp = dst + ".tmp"
    open(tmp, "wb").write(bytes(b))
    try:   # the copy only takes its name once it reads back as the input with exactly the two values changed
        a, _, pa = X.read_sjtl(src)
        c, _, pc = X.read_sjtl(tmp)
        want = dict(a)
        want[prof] = (alpha, a[prof][1])
        want["delta"] = tuple(delta if k == {"chat": 0, "formal": 1}[prof] else a["delta"][k] for k in range(2))
        if c != want or pa.keys() != pc.keys() or any((pa[k] != pc[k]).any() for k in pa):
            raise SystemExit("STOP: the copy differs from the input in more than the two values")
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


if __name__ == "__main__":
    if len(sys.argv) != 6 or sys.argv[3] != "formal":
        raise SystemExit(__doc__)
    patch(sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5]))
