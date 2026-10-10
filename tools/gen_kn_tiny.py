"""Kneser-Ney 玩具模型的跨語言 golden（docs/contracts/kn-core.md §3 第 2、4 項）：Python 產生，core/tests/kn_tiny.rs 的 Rust 逐位元組比對。

玩具語料沿用 tools/test_build_lm.py（和 test_kn_cont.py 同一組手算）。輸出到 eval/golden/kn-tiny/：
  bigram.sjlm、classes.sjc（手排的 K=3 詞類）、kn.sjkn（θ=1、β=1，出貨的設定）、kn-beta075.sjkn（β=0.75，檢查混合）、
  lexicon.txt（解碼用的玩具詞庫）、decode.tsv（Python 的解碼輸出：設定、profile、句、名次、surface、分數的 repr；
  設定 plain＝不加側檔、beta1、beta075）。
重產：python3 -B tools/gen_kn_tiny.py（test_kn_cont.py 會檢查入庫的檔案等於現在的輸出，所以改了任何一邊都會被抓到）。
"""
import hashlib
import os
import pickle
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for sub in ("tools", "reference/proto", "experiments/s2"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import build_lm  # noqa: E402
import kn_cont  # noqa: E402
import lm as L  # noqa: E402
from test_build_lm import BY_READING, COLL, WIKI  # noqa: E402

OUT = os.path.join(ROOT, "eval", "golden", "kn-tiny")
SENTENCES = ["ㄊㄚ ㄑㄧˇ ㄔㄨㄤˊ", "ㄊㄚ ㄓㄢˋ", "ㄑㄧˇ ㄔㄨㄤˊ", "ㄓㄢˋ ㄊㄚ", "ㄊㄚ ㄓㄢ", "ㄕˋ ㄐㄧㄝˋ ㄒㄧㄢˋ ㄊㄚ", "ㄊㄚ ㄓㄢˋ ㄑㄧˇ ㄔㄨㄤˊ"]
K, MU = 3, 0.5   # K3 = 6：<s> 是類 4、</s> 是類 5


def classes_bytes(model, vocab):
    """手排的詞類：他 0、佔 1、起床 2、起牀 2，其餘沒有類；test_kn_cont 的手算用 cls[佔]=1、cls[起床]=2、emit[起床]=0.5、Pc[1,2]=0.2、μ=0.5。"""
    k3 = K + 3
    cls = [L.NO_CLASS] * len(vocab)
    emit = [0.0] * len(vocab)
    for w, c, e in (("<s>", K + 1, 1.0), ("</s>", K + 2, 1.0), ("他", 0, 0.4), ("佔", 1, 0.6), ("起床", 2, 0.5), ("起牀", 2, 0.3)):
        cls[vocab.index(w)], emit[vocab.index(w)] = c, e
    pc = [0.05] * (k3 * k3)
    pc[0 * k3 + 1], pc[0 * k3 + 2], pc[1 * k3 + 2] = 0.25, 0.3, 0.2
    return L.CLASS_MAGIC + hashlib.sha256(model).digest() + struct.pack("<IdI", K, MU, len(vocab)) \
        + struct.pack(f"<{len(vocab)}H", *cls) + struct.pack(f"<{len(vocab)}d", *emit) + struct.pack(f"<{len(pc)}d", *pc)


def generate():
    """檔名 -> 位元組（不寫檔）。"""
    with tempfile.TemporaryDirectory() as tmp:
        for name, c in (("counts-200000.pkl", WIKI), ("counts-colloquial3.pkl", COLL)):
            pickle.dump(c, open(os.path.join(tmp, name), "wb"))
        old, build_lm.WORK = build_lm.WORK, tmp
        try:
            cls = build_lm.variant_classes(BY_READING)
            model = build_lm.build(cls)[0]
            bi = build_lm.merged_counts(cls)[1]
        finally:
            build_lm.WORK = old
        mpath = os.path.join(tmp, "bigram.sjlm")
        open(mpath, "wb").write(model)
        vocab = L.BigramLM(mpath, classes=False).vocab
        n = kn_cont.continuation(bi, cls, 1, vocab)
        files = {"bigram.sjlm": model, "classes.sjc": classes_bytes(model, vocab),
                 "kn.sjkn": kn_cont.side_bytes(mpath, n, cls, 1, 1.0), "kn-beta075.sjkn": kn_cont.side_bytes(mpath, n, cls, 1, 0.75),
                 "lexicon.txt": "".join(f"{'-'.join(k)} {w} {lp}\n" for k, v in BY_READING.items() for w, lp in v).encode()}
        for name, b in files.items():
            if name.endswith((".sjlm", ".sjc", ".sjkn")):
                open(os.path.join(tmp, name), "wb").write(b)
        lex_path = os.path.join(tmp, "lexicon.txt")
        open(lex_path, "wb").write(files["lexicon.txt"])
        import ime
        lines = []
        for config, side in (("plain", None), ("beta1", "kn.sjkn"), ("beta075", "kn-beta075.sjkn")):
            lm = L.BigramLM(mpath, kn=os.path.join(tmp, side) if side else None, kn_classes=cls if side else None)
            lex = L.cap_overlay(ime.Lexicon(lex_path), set(), lm, demote_path=None)
            for profile in ("chat", "formal"):
                for i, s in enumerate(SENTENCES, 1):
                    for r, (sc, ws) in enumerate(L.decode(lex, s.split(), lm, profile), 1):
                        lines.append(f"{config}\t{profile}\t{i}\t{r}\t{''.join(ws)}\t{sc!r}\n")
        files["decode.tsv"] = "".join(lines).encode()
        return files


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, b in generate().items():
        open(os.path.join(OUT, name), "wb").write(b)
        print(name, len(b))
