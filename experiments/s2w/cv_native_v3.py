"""S2w 契約 §8.2：W2 對 model-v3 的 cvtune-native。

S2f 的定義（experiments/s2f/cv_native.py：修改前、修改後兩種 OpenCC 轉換都不改的原句切出的子句）再加一個條件：
MW 轉換（zhconv-rs ＋ §8.1 的台灣字形層，站上轉換表照用；CV／Tatoeba 句子沒有 NoteTA）也不改。
一個子句只要出現在任何「被任一轉換改變」的句子（原文與三種輸出各自切出的子句）裡就不算。
印 S2f 的列數、加了條件後的列數與輸出檔的 SHA-256；少於 1,000 列是停止條件（exit 1）。
用法：~/.cache/shanjie/venv-s2w/bin/python experiments/s2w/cv_native_v3.py [--out FILE]
"""
import argparse
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [HERE, os.path.join(ROOT, "experiments", "s2")]
import importlib.util  # noqa: E402
import mwconv  # noqa: E402

# experiments/s2w/cv_native.py 同名，S2f 的那支用路徑載入
_spec = importlib.util.spec_from_file_location("s2f_cv_native", os.path.join(ROOT, "experiments", "s2f", "cv_native.py"))
s2f = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(s2f)


def native_clauses(old, mw):
    new, bt = s2f.new, s2f.bt
    cn, co = new.load_conv(), old.load_conv()
    good = {"s2f": set(), "mw": set()}
    bad = {"s2f": set(), "mw": set()}
    for raw in s2f.source_lines():
        s_old, s_new = old.convert(raw, *co), new.convert(raw, *cn)
        s_mw = mwconv.convert(raw, mw)
        same = raw == s_old == s_new
        for key, ok, outs in (("s2f", same, (raw, s_old, s_new)), ("mw", same and raw == s_mw, (raw, s_old, s_new, s_mw))):
            if ok:
                if bt.is_tune(raw):
                    good[key].update(bt.HAN.findall(raw))
            else:
                for s in outs:
                    bad[key].update(bt.HAN.findall(s))
    return good["s2f"] - bad["s2f"], good["mw"] - bad["mw"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/.cache/shanjie/work/s2w2/cvtune-native-v3.txt"))
    a = ap.parse_args()
    got = hashlib.sha256(open(s2f.CVTUNE, "rb").read()).hexdigest()
    if got != s2f.CVTUNE_SHA:
        sys.exit(f"cvtune SHA-256 mismatch: {got}")
    n_s2f, n_mw = native_clauses(s2f.load_old(), mwconv.load())
    lines = open(s2f.CVTUNE, encoding="utf-8").readlines()
    rows_s2f = [l for l in lines if l.split("|")[1] in n_s2f]
    rows = [l for l in lines if l.split("|")[1] in n_mw]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w", encoding="utf-8", newline="\n").write("".join(rows))
    sha = hashlib.sha256(open(a.out, "rb").read()).hexdigest()
    print(f"cvtune rows {len(lines)}; S2f native rows {len(rows_s2f)}; with the MW condition {len(rows)}; sha256 {sha} -> {a.out}")
    sys.exit(1 if len(rows) < 1000 else 0)


if __name__ == "__main__":
    main()
