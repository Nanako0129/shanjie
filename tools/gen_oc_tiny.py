"""0c's cross-language golden (docs/contracts/oc-experimental.md section 3): Python writes, core/tests/oc_tiny.rs compares.

Writes to eval/golden/oc-tiny/:
  tiny.sjoc    a fixed-seed random model of the 0c structure (2 layers, d 16, 2 heads, ffn 12) written with tools/oc_ref.py `write`.
  scores.tsv   `context<TAB>syllables (space-separated)<TAB>candidates (|-separated)<TAB>log10 scores (comma-separated, repr) or -`:
               experiments/x0c/score_0c.py `score(packed=False)` (one sequence per candidate, the model card's layout) over oc_ref's
               float64 forward on the same float32 weights; `-` where score_0c.check_row says the row is unscorable.
The rows cover a candidate character outside the vocabulary, an empty context, a context longer than 64 characters (and exactly 64),
context characters outside the vocabulary, a single syllable, the neutral retry (it wins for some candidates and loses for others;
a last syllable that already is neutral takes no retry), 32 syllables (scored) and 33 (not), a candidate of the wrong length, an
unknown base, every tone mark, and more candidates than one batch.
The q, k and first feed-forward weights are scaled up after drawing (SHARPEN) so that the forward's parts matter: RoPE, the mask,
the type embedding, the GELU and the head's LayerNorm each change the scores by far more than the test's 1e-5.
Regenerate: python3 -B tools/gen_oc_tiny.py
"""
import hashlib
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import oc_ref  # noqa: E402

OUT = os.path.join(ROOT, "eval", "golden", "oc-tiny")
CHARS = list("天氣很好真的我你他們是不了在有人這個說都就要會對可以時候來去吃飯")
BASES = ["ㄊㄧㄢ", "ㄑㄧ", "ㄏㄣ", "ㄏㄠ", "ㄓㄣ", "ㄉㄜ", "ㄇㄚ", "ㄋㄧ", "ㄇㄣ", "ㄕ", "ㄅㄨ"]
NEUTRAL = ["ㄇㄚ", "ㄇㄣ", "ㄉㄜ"]
CFG = {"n_layer": 2, "n_head": 2, "d": 16, "ffn": 12, "n_tok": 8 + len(CHARS), "n_base": 2 + len(BASES), "n_tone": 6, "n_type": 3,
       "n_rope": 4}
CFG["n_out"] = CFG["n_tok"] - 6
OUT_IDS = [4, 6] + list(range(8, CFG["n_tok"]))   # the graph's pattern: every id but 0-3, 5, 7
# Weights N(0, STD), then q, k and the first feed-forward matrix scaled by SHARPEN (sharper attention, a wider GELU input).
# Larger values make the float32 forward drift from the float64 reference by more than the test's 1e-5: Rust's worst log10 difference
# (tests/oc_tiny.rs) was 7.7e-6 at 0.5 / 1.5, 6.4e-6 at 0.35 / 3, 2.6e-6 at 0.25 / 2 (the one in use), 3.9e-6 at 0.25 / 3.
STD = 0.25
SHARPEN = 2
ROPE_BASE = 50.0   # the real model's base is in its file; a small one here makes the rotation matter within 130 positions


def model():
    rng = np.random.default_rng(20261019)
    c = CFG
    T = {}
    for name, shape, kind in oc_ref.tensors(*(c[k] for k in ("n_layer", "d", "ffn", "n_tok", "n_base", "n_tone", "n_type", "n_rope"))):
        if name.endswith("norm"):
            T[name] = (1 + 0.3 * rng.normal(size=shape)).astype(np.float32)
        elif name == "dec_b":
            T[name] = (0.3 * rng.normal(size=shape)).astype(np.float32)
        else:
            T[name] = rng.normal(0, STD, shape).astype(np.float32)
    for k in T:
        if k.endswith(("wq", "wk", "w1")):
            T[k] = T[k] * SHARPEN
    T["inv_freq"] = (1.0 / ROPE_BASE ** (np.arange(c["n_rope"]) / c["n_rope"])).astype(np.float32)
    return T


def syl(base, tone=""):
    return BASES[base] + tone


LONG = "".join(CHARS[(i * 7 + i // 5) % len(CHARS)] for i in range(70))   # 70 characters: the oldest 6 must be dropped
ALL = CHARS[:16]
CANDS = ["".join(ALL[(i * 5 + j * 3) % len(ALL)] for j in range(4)) for i in range(10)]   # 10 candidates of 4 characters
# (context, syllables, candidates)
ROWS = [
    ("", [syl(0), syl(1, "ˋ"), syl(2, "ˇ")], ["天氣好", "天氣很", "真的好", "你他們"]),                       # empty context
    ("我", [syl(0, "ˊ"), syl(3, "ˇ")], ["天氣", "很好", "真的", "我你", "他們", "是不", "了在", "有人"]),      # 8 candidates
    ("你好嗎", [syl(0), syl(1, "ˋ"), syl(4, "˙")], ["天氣好", "龘氣好", "天氣很"]),                         # a candidate character outside the vocabulary
    ("A我，B好。", [syl(4), syl(5, "ˇ")], ["真的", "你他", "我們"]),                                        # context characters outside the vocabulary
    (LONG, [syl(0), syl(1), syl(2, "ˋ"), syl(3, "ˇ")], ["天氣很好", "真的我你", "他們是不"]),               # context longer than 64
    (LONG[-64:], [syl(0), syl(1), syl(2, "ˋ"), syl(3, "ˇ")], ["天氣很好", "真的我你", "他們是不"]),         # exactly 64
    ("我們", [syl(0)], ["天", "氣"]),                                                                   # a single syllable: not scored
    ("我們", [syl(0), syl(6)], ["天氣", "真的", "我你", "他們", "很好"]),                                  # neutral retry: last base neutral, no mark
    ("你好", [syl(7), syl(8, "ˊ")], ["天氣", "真的", "我你", "他們", "很好", "是不"]),                       # ... with a tone mark: retried too
    ("你好", [syl(7), syl(8, "˙")], ["天氣", "真的", "我你", "他們"]),                                      # already neutral: no retry
    ("他們", [syl(9, "ˊ"), syl(10, "ˋ"), syl(0), syl(1, "ˇ")], ["我們是不", "天氣很好"]),                   # tones 2, 4, 1, 3
    ("", [syl(i % 4) for i in range(32)], ["".join(CHARS[(i * 3 + j) % len(CHARS)] for i in range(32)) for j in range(2)]),   # 32: scored
    ("我", [syl(i % 4) for i in range(33)], ["".join(CHARS[(i * 3 + j) % len(CHARS)] for i in range(33)) for j in range(2)]),   # 33: not
    ("你", [syl(0), syl(1), syl(2)], ["天氣好", "天氣"]),                                                  # a candidate of the wrong length
    ("你", [syl(0), "ㄅㄆㄇ"], ["天氣", "真的"]),                                                          # a base the model does not know
    ("我們都是", [syl(0), syl(1, "ˋ"), syl(2), syl(3)], CANDS),                                           # 10 candidates: two batches
]


def main():
    os.makedirs(OUT, exist_ok=True)
    T = model()
    path = os.path.join(OUT, "tiny.sjoc")
    src = tuple(hashlib.sha256(s).digest() for s in (b"oc-tiny model.onnx", b"oc-tiny vocab.json"))
    oc_ref.write(path, CFG, T, CHARS, BASES, NEUTRAL, OUT_IDS, src)
    s0, z = oc_ref.zuin(path, dtype=np.float64)
    lines, kinds, retry_wins, retry_loses = [], {}, 0, 0
    for ctx, reading, cands in ROWS:
        try:
            s0.check_row(z, reading, cands)
        except s0.Unscorable as e:
            kinds[e.args[0]] = kinds.get(e.args[0], 0) + 1
            col = "-"
        else:
            col = ",".join(repr(x / math.log(10)) for x in s0.score(z, ctx, reading, cands, packed=False))
            last = reading[-1]
            base = last[:-1] if last[-1] in s0.TONES else last
            if base in z.neutral and last[-1] != "˙":
                a = np.array(s0.log_prob(z, ctx, reading, cands, False))
                b = np.array(s0.log_prob(z, ctx, reading[:-1] + [base + "˙"], cands, False))
                retry_wins += int((b > a).sum())
                retry_loses += int((b < a).sum())
        lines.append(f"{ctx}\t{' '.join(reading)}\t{'|'.join(cands)}\t{col}\n")
    # The retry must matter both ways, or "never retry" and "always the neutral tone" would pass.
    assert retry_wins >= 3 and retry_loses >= 3, (retry_wins, retry_loses)
    assert kinds == {"oov": 1, "syllables": 2, "length": 1, "base": 1}, kinds
    with open(os.path.join(OUT, "scores.tsv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(lines))
    print(f"{len(lines)} rows; unscorable {kinds}; the neutral retry wins {retry_wins}, loses {retry_loses} candidate scores")


if __name__ == "__main__":
    main()
