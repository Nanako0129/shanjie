"""lytune 歸因（docs/contracts/lytune-attrib.md §2）：複製 classes.sjc、只改檔頭的詞類權重 μ。

格式見 tools/build_classes.py：magic 8 bytes、model_sha 32、K u32，接著 μ f64（位移 44，little-endian）。
用法：python3 set_mu.py IN.sjc OUT.sjc MU   → 印出 OUT 的 SHA-256 與讀回的 μ
"""
import hashlib
import struct
import sys

MAGIC, MU_AT = b"SJCL0001", 44


def set_mu(data: bytes, mu: float) -> bytes:
    if data[:8] != MAGIC:
        raise ValueError("not a classes.sjc (magic)")
    if not 0.0 <= mu <= 1.0:
        raise ValueError("mu must be in [0, 1] (core/src/lm.rs refuses anything else)")
    return data[:MU_AT] + struct.pack("<d", mu) + data[MU_AT + 8:]


def get_mu(data: bytes) -> float:
    return struct.unpack_from("<d", data, MU_AT)[0]


def demo():
    head = MAGIC + bytes(32) + struct.pack("<IdI", 3, 0.8, 2) + b"rest"
    out = set_mu(head, 0.4)
    assert get_mu(head) == 0.8 and get_mu(out) == 0.4
    assert len(out) == len(head) and out[:MU_AT] == head[:MU_AT] and out[MU_AT + 8:] == head[MU_AT + 8:]


if __name__ == "__main__":
    if len(sys.argv) == 1:
        demo()
        print("ok")
        sys.exit(0)
    src, dst, mu = sys.argv[1], sys.argv[2], float(sys.argv[3])
    with open(src, "rb") as f:
        out = set_mu(f.read(), mu)
    with open(dst, "xb") as f:
        f.write(out)
    print(hashlib.sha256(out).hexdigest(), get_mu(out))
