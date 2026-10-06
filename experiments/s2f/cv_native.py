"""S2f 契約 §3.4：cvtune-native 子集。

cvtune 的來源句（Common Voice、Tatoeba；讀檔與 is_tune 照 experiments/s2/build_tune.py）裡，修改前與修改後的轉換都不改變的句子
（raw == convert_舊(raw) 而且 raw == convert_新(raw)）所切出的子句；一個子句只要也出現在任何「被任一轉換改變」的句子（原文、舊輸出、
新輸出各自切出的子句）裡就不算。模型 E 那版的 cvtune（SHA-256 核對）列的參考句屬於這些子句的才留下，寫到 --out，印列數。

舊的轉換只在這裡用：從基準提交 OLD_REF（S2f 分支的起點，等於當時 origin/main 的 build_counts.py）用 `git show` 讀出來，
載入成一個暫時模組，不另存在 repo 裡。重建完成、驗收完之後這支程式就沒有用了。
用法：python3 experiments/s2f/cv_native.py [--cvtune FILE] [--out FILE]
"""
import argparse
import bz2
import hashlib
import os
import subprocess
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
import build_counts as new  # noqa: E402
import build_tune as bt  # noqa: E402

OLD_REF = "dd7d2bf"
CVTUNE = os.path.expanduser("~/.cache/shanjie/work/s2n/tune/cvtune.txt")
CVTUNE_SHA = "7f9b35e6deba78c84c9c9c4786d217c054f45718cb8b43c902faf4062c46bbb4"


def load_old():
    src = subprocess.run(["git", "show", f"{OLD_REF}:experiments/s2/build_counts.py"], cwd=ROOT, capture_output=True, text=True, check=True, encoding="utf-8").stdout
    mod = types.ModuleType("build_counts_old")
    mod.__file__ = os.path.join(ROOT, "experiments", "s2", "build_counts.py")   # 它的 ROOT、SRC 從 __file__ 推
    exec(compile(src, f"{OLD_REF}:build_counts.py", "exec"), mod.__dict__)
    return mod


def source_lines():
    src = os.path.join(new.SRC, "colloquial")
    files = [os.path.join(src, f) for f in sorted(os.listdir(src)) if f.startswith("cv-")] + [os.path.join(src, "cmn_sentences.tsv.bz2")]
    for p in files:
        f = bz2.open(p, "rt", encoding="utf-8") if p.endswith(".bz2") else open(p, encoding="utf-8")
        for line in f:
            yield line.rstrip("\n").split("\t")[-1]


def native_clauses(old):
    cn, co = new.load_conv(), old.load_conv()
    good, bad = set(), set()
    for raw in source_lines():
        s_old, s_new = old.convert(raw, *co), new.convert(raw, *cn)
        if raw == s_old == s_new:
            if bt.is_tune(raw):
                good.update(bt.HAN.findall(raw))
        else:
            for s in (raw, s_old, s_new):
                bad.update(bt.HAN.findall(s))
    return good - bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cvtune", default=CVTUNE)
    ap.add_argument("--out", default=os.path.expanduser("~/.cache/shanjie/work/s2f/cvtune-native.txt"))
    a = ap.parse_args()
    got = hashlib.sha256(open(a.cvtune, "rb").read()).hexdigest()
    if got != CVTUNE_SHA:
        sys.exit(f"cvtune SHA-256 mismatch: {got}")
    native = native_clauses(load_old())
    rows = [l for l in open(a.cvtune, encoding="utf-8") if l.split("|")[1] in native]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w", encoding="utf-8", newline="\n").write("".join(rows))
    print(f"cvtune rows {sum(1 for _ in open(a.cvtune, encoding='utf-8'))}; native clauses {len(native)}; cvtune-native rows {len(rows)} -> {a.out}")


if __name__ == "__main__":
    main()
