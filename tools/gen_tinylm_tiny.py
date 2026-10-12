"""小型字元模型的跨語言 golden（docs/contracts/s7c-core-tinylm.md §3 第 3 項）：Python 產生，core/tests/tinylm_tiny.rs 的 Rust 比對。

輸出到 eval/golden/tinylm-tiny/：
  tiny-f32.sjtl、tiny-int8.sjtl：`tinylm.random_params` 的隨機小模型（種子固定）經 experiments/s7a-tinylm/export_sjtl.py 匯出；
    記的 n-gram 模型雜湊是 eval/golden/kn-tiny/bigram.sjlm 的（隨便一個固定檔），α／τ 聊天 0.7／2、書面 0.5／1（手填，只給 CLI 測試看）。
  items.tsv：`k<TAB>c<TAB>LL(f32 檔)<TAB>LL(int8 檔)`，LL 是 numpy（float64）在**同一份還原後權重**上算的（int8 檔是 q * scale 還原後的值）。
    項目含字表外的字、空的 k、需要丟前文的長 k、c 剛好 63 字（k 全丟）、單字候選、空的 c。
  tiny-int8-v2.sjtl（S7c-2c，docs/contracts/s7c2c-delta.md §2.4）：同一份權重的 SJTL0002，α／τ 同上，δ 聊天 0.25、書面 2。
  pick.tsv（S7c-2c §3 第 1 項）：`fusegrid.pick` 在固定種子的隨機 ng／ll 與幾組融合差剛好是 0.25、2 的手寫例子上的選擇。
    前三列是格點（`alpha`／`tau`／`delta<TAB>逗號分隔`），之後每列 `ng<TAB>ll<TAB>選擇`，選擇依 alpha、tau、delta 的巢狀順序；
    數值都用 repr 寫出，Rust 讀回來是同一個 f64。
權重事後放大（emb、qkv、fc1）讓 attention 和 GELU 的非線性真的被用到；不放大時突變（拿掉遮罩、GELU 常數）不一定改得動 LL。
重產：python3 -B tools/gen_tinylm_tiny.py
"""
import hashlib
import os
import random
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "experiments", "s7a-tinylm"))
import export_sjtl  # noqa: E402
import tinylm  # noqa: E402
import fusegrid  # noqa: E402

OUT = os.path.join(ROOT, "eval", "golden", "tinylm-tiny")
CHARS = "我你他好嗎的是不了在有人這個們說都就要會對可以時候來去"
LONG_K = "".join(CHARS[(i * 7 + i // 5) % len(CHARS)] for i in range(70))   # 70 characters: dropping the oldest 7 or more is needed to fit
ITEMS = [
    ("", "你好"),                           # empty k
    ("我們", "你好嗎"),
    ("你好", "龘"),                          # the candidate is one character outside the table (UNK)
    ("好", "我"),                            # one-character candidate
    ("A我，", "你好"),                       # characters outside the table in k
    ("我們", "你好ab嗎"),                    # ... and inside c
    ("他", ""),                              # empty c: LL 0
    (LONG_K, "我們都是好人"),                # k must be trimmed to 57 characters
    (LONG_K, CHARS[:8] * 7 + CHARS[:7]),     # c of 63 characters: k is dropped entirely
    ("們", "你們都" * 3),
    ("", "不"),
    ("我們都", "要去"),
    ("我們都", "要來"),                      # more than 8 items: two batches, padded
]


def build():
    """filename -> bytes."""
    params = tinylm.random_params(list(CHARS), n_layer=2, d=16, n_head=2, seed=7)
    for k in params:
        if k == "emb" or k.endswith(("qkv_w", "fc1_w")):
            params[k] = params[k] * 3
    files = {}
    with tempfile.TemporaryDirectory() as tmp:
        npz = os.path.join(tmp, "tiny.npz")
        tinylm.save_params(npz, params, CHARS, n_layer=2, n_head=2)
        with open(os.path.join(ROOT, "eval", "golden", "kn-tiny", "bigram.sjlm"), "rb") as f:
            sha = hashlib.sha256(f.read()).digest()
        lls = []
        for wtype in ("f32", "int8"):
            path = os.path.join(tmp, f"tiny-{wtype}.sjtl")
            export_sjtl.export(npz, path, wtype, (0.7, 2.0), (0.5, 1.0), sha)
            files[f"tiny-{wtype}.sjtl"] = open(path, "rb").read()
            _, chars, restored = export_sjtl.read_sjtl(path)
            assert "".join(chars) == CHARS
            rpath = os.path.join(tmp, f"restored-{wtype}.npz")
            tinylm.save_params(rpath, restored, chars, n_layer=2, n_head=2)
            lls.append(tinylm.ll_many(tinylm.NumpyLM(rpath), ITEMS))     # float64 on the restored float32 weights
        path = os.path.join(tmp, "tiny-int8-v2.sjtl")
        export_sjtl.export(npz, path, "int8", (0.7, 2.0, 0.25), (0.5, 1.0, 2.0), sha)
        files["tiny-int8-v2.sjtl"] = open(path, "rb").read()
    files["items.tsv"] = "".join(f"{k}\t{c}\t{float(a)!r}\t{float(b)!r}\n" for (k, c), a, b in zip(ITEMS, *lls)).encode()
    files["pick.tsv"] = pick_table().encode()
    return files


# The pick grid: 0.5 and 1 keep the hand lists' sums exact; 0.25 and 2 are the S7e deltas (chat, formal).
PICK_ALPHAS = [0.2, 0.5, 0.7, 1.0, 1.5]
PICK_TAUS = [0.5, 1.0, 2.0, float("inf")]
PICK_DELTAS = [0.0, 0.25, 0.5, 1.0, 2.0, 4.0]
# (ng, ll) whose fused gap s[1] - s[0] lands exactly on a grid delta or on 0 (multiples of 1/8): 0.25, 2, 0.5, 2 at alpha 1;
# 0, 0.5, 0, 0.5 at alpha 0.5
PICK_HAND = [
    ([-1.0, -1.25], [-1.0, -0.5]),
    ([-1.0, -2.0], [-4.0, -1.0]),
    ([-2.0, -2.5, -3.0], [-3.0, -2.0, -2.25]),
    ([-0.5, -1.5, -0.75], [-6.0, -3.0, -5.5]),
]


def pick_table():
    rnd = random.Random(20261012)
    lists = list(PICK_HAND)
    for _ in range(40):
        n = rnd.randint(2, 8)
        lists.append(([-rnd.uniform(0, 6) for _ in range(n)], [-rnd.uniform(0, 30) for _ in range(n)]))
    out = [f"{name}\t{','.join(repr(float(x)) for x in xs)}\n"
           for name, xs in (("alpha", PICK_ALPHAS), ("tau", PICK_TAUS), ("delta", PICK_DELTAS))]
    for ng, ll in lists:
        r = {"n": len(ng), "ng": ng, "ll": ll}
        picks = [fusegrid.pick(r, a, t, delta=d) for a in PICK_ALPHAS for t in PICK_TAUS for d in PICK_DELTAS]
        out.append(f"{','.join(map(repr, ng))}\t{','.join(map(repr, ll))}\t{','.join(map(str, picks))}\n")
    return "".join(out)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, b in build().items():
        open(os.path.join(OUT, name), "wb").write(b)
        print(name, len(b))
