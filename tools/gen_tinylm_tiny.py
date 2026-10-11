"""小型字元模型的跨語言 golden（docs/contracts/s7c-core-tinylm.md §3 第 3 項）：Python 產生，core/tests/tinylm_tiny.rs 的 Rust 比對。

輸出到 eval/golden/tinylm-tiny/：
  tiny-f32.sjtl、tiny-int8.sjtl：`tinylm.random_params` 的隨機小模型（種子固定）經 experiments/s7a-tinylm/export_sjtl.py 匯出；
    記的 n-gram 模型雜湊是 eval/golden/kn-tiny/bigram.sjlm 的（隨便一個固定檔），α／τ 聊天 0.7／2、書面 0.5／1（手填，只給 CLI 測試看）。
  items.tsv：`k<TAB>c<TAB>LL(f32 檔)<TAB>LL(int8 檔)`，LL 是 numpy（float64）在**同一份還原後權重**上算的（int8 檔是 q * scale 還原後的值）。
    項目含字表外的字、空的 k、需要丟前文的長 k、c 剛好 63 字（k 全丟）、單字候選、空的 c。
權重事後放大（emb、qkv、fc1）讓 attention 和 GELU 的非線性真的被用到；不放大時突變（拿掉遮罩、GELU 常數）不一定改得動 LL。
重產：python3 -B tools/gen_tinylm_tiny.py
"""
import hashlib
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "experiments", "s7a-tinylm"))
import export_sjtl  # noqa: E402
import tinylm  # noqa: E402

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
    files["items.tsv"] = "".join(f"{k}\t{c}\t{float(a)!r}\t{float(b)!r}\n" for (k, c), a, b in zip(ITEMS, *lls)).encode()
    return files


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, b in build().items():
        open(os.path.join(OUT, name), "wb").write(b)
        print(name, len(b))
