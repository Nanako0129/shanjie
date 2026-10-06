"""S2n 報告用量測（不是門檻）：語言模型檔的 unigram 與 bigram 表面字串裡，含簡體專用字、含 twfilter 異體字表的條目數。
用法：python3 experiments/s2n/model_surfaces.py <model.sjlm>…   （格式見 tools/build_lm.py 檔頭）"""
import array
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "s2"))
import build_counts as bc  # noqa: E402

bc.load_conv()
SIMP = set(bc.SIMP_ONLY)
ORTHO = set("裏着衞爲説麽綫墻眞啓衆産囯麪")   # twfilter converted_orthography；説 是 U+8AAC


def load(path):
    b = open(path, "rb").read()
    assert b[:8] == b"SJLM0001"
    V, N, eos, D = struct.unpack_from("<IQQd", b, 8)
    o = 8 + 28
    vocab = []
    for _ in range(V):
        (n,) = struct.unpack_from("<H", b, o); o += 2
        vocab.append(b[o:o + n].decode("utf-8")); o += n
    uni = array.array("Q"); uni.frombytes(b[o:o + 8 * V]); o += 8 * V
    (C,) = struct.unpack_from("<I", b, o); o += 4
    ctx = array.array("I"); ctx.frombytes(b[o:o + 4 * C]); o += 4 * C
    o += 8 * C
    offs = array.array("I"); offs.frombytes(b[o:o + 4 * (C + 1)]); o += 4 * (C + 1)
    (E,) = struct.unpack_from("<I", b, o); o += 4
    nxt = array.array("I"); nxt.frombytes(b[o:o + 4 * E]); o += 4 * E
    cnt = array.array("I"); cnt.frombytes(b[o:o + 4 * E])
    return vocab, uni, ctx, offs, nxt, cnt


for path in sys.argv[1:]:
    vocab, uni, ctx, offs, nxt, cnt = load(path)
    has = {name: [any(c in cs for c in w) for w in vocab] for name, cs in (("simplified-only", SIMP), ("orthography", ORTHO))}
    print(f"== {os.path.basename(path)}: {len(vocab) - 2} unigram surfaces, {len(nxt)} bigram entries")
    for name, flags in has.items():
        u = [i for i in range(2, len(vocab)) if flags[i]]
        ub = [(ctx[k], nxt[j]) for k in range(len(ctx)) for j in range(offs[k], offs[k + 1]) if flags[ctx[k]] or flags[nxt[j]]]
        print(f"{name}: unigram surfaces {len(u)} (token mass {sum(uni[i] for i in u):,}), bigram entries {len(ub)}")
        print("  unigram examples:", " ".join(vocab[i] for i in sorted(u, key=lambda i: -uni[i])[:10]))
        top = sorted(range(len(ub)), key=lambda t: 0)[:0]
        ex = []
        for k in range(len(ctx)):
            for j in range(offs[k], offs[k + 1]):
                if (flags[ctx[k]] or flags[nxt[j]]) and cnt[j] >= 200 and len(ex) < 10:
                    ex.append(f"{vocab[ctx[k]]}+{vocab[nxt[j]]}")
        print("  bigram examples:", " ".join(ex))
