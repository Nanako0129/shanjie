"""S2r 跨語言檢查（docs/contracts/s2r-sandhi-variants.md §3）：Python ime.Lexicon 載入兩份疊加層
（overlay-add.tsv、sandhi-add.tsv），以 unigram 解碼開發集前 302 列、S1 的疊加層集合（trap、daily、moedict）
與 S2r 探針集，印出的「## 名稱  unigram  指標」與「   ✗ 正解 → 第一名」必須和 Rust CLI 產生的 golden 逐行一致。

讀列規則照 Rust core/src/eval.rs：eval/dev/*.txt 依檔名排序後全部讀入（含 user-typing.txt）再取前 302 列；
有第三欄讀音就用它，否則 to_syllables；兩者都沒有的列丟掉。

用法：python3 reference/proto/check_unigram_overlay.py   （不一致時印出第一處差異並以 1 結束）
"""
import glob
import os
import sys

from eval import BEAM, lenient
from ime import OVERLAYS, Lexicon, decode

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
LEXDIR = os.path.join(ROOT, "data", "lexicon")
GOLDEN = os.path.join(ROOT, "eval", "golden")


def parse_rows(text):
    rows = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        ctx, rest = line.split("|", 1)
        sent, _, reading = rest.partition("|")
        rows.append((sent, reading.split() if "|" in rest else None))
    return rows


def read(path):
    return open(path, encoding="utf-8").read()


def report(lex, name, rows):
    rows = [(s, r) for s, r in rows if r is not None or lex.to_syllables(s)]
    sent_ok = len_ok = char_ok = chars = oracle = 0
    lines = []
    for truth, reading in rows:
        nbest = decode(lex, reading or lex.to_syllables(truth))
        texts = ["".join(ws) for _, ws in nbest]
        out = texts[0]
        oracle += truth in texts
        sent_ok += out == truth
        len_ok += lenient(out) == lenient(truth)
        char_ok += sum(a == b for a, b in zip(out, truth))
        chars += len(truth)
        if out != truth and name != "萌典例句":
            lines.append(f"   ✗ {truth} → {out}")
    n = len(rows)
    res = {"n": n, "sent_acc": round(sent_ok / n, 3), "lenient_acc": round(len_ok / n, 3),
           "char_acc": round(char_ok / chars, 4), f"oracle@{BEAM}": round(oracle / n, 3)}
    return [f"## {name}  unigram  {res}"] + lines


def golden_lines(name):
    """golden 裡的 unigram 標題與 ✗ 行（略過 extra、oracle@64、學習模擬等其他輸出）。"""
    keep, out = False, []
    for line in read(os.path.join(GOLDEN, name)).splitlines():
        if line.startswith("## "):
            keep = "  unigram  " in line
            if keep:
                out.append(line)
        elif keep and line.startswith("   ✗ "):
            out.append(line)
    return out


def main():
    lex = Lexicon(os.path.join(LEXDIR, "mcbpmf-data.txt"),
                  overlay=OVERLAYS)
    dev = []
    for f in sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt"))):
        dev += parse_rows(read(f))
    dev = [(s, r) for s, r in dev if r is not None or lex.to_syllables(s)][:302]
    sets = os.path.join(ROOT, "eval", "sets")
    checks = {
        "s1-dev302.txt": report(lex, "開發集", dev),
        "s1-overlay-sets.txt": report(lex, "同音陷阱集", parse_rows(read(os.path.join(sets, "trap.txt"))))
        + report(lex, "日常驗證集", parse_rows(read(os.path.join(sets, "daily.txt"))))
        + report(lex, "萌典例句", [(s, None) for s in read(os.path.join(sets, "moedict.txt")).split()]),
        # 上面三份的讀音都是基底已有的，拿掉 sandhi-add.tsv 結果不變；探針集換過讀音，才量得到變體列。
        "s2r-probe-unigram.txt": report(lex, "探針集", parse_rows(read(os.path.join(ROOT, "eval", "probe", "s2r-probe.txt")))),
    }
    bad = False
    for golden, mine in checks.items():
        want = golden_lines(golden)
        if mine == want:
            print(f"{golden}: {len(want)} lines match")
            continue
        bad = True
        i = next((i for i, (a, b) in enumerate(zip(mine, want)) if a != b), min(len(mine), len(want)))
        print(f"{golden}: differs at line {i + 1} (python {len(mine)} lines, rust {len(want)} lines)")
        print(f"  python: {mine[i] if i < len(mine) else '<end>'}")
        print(f"  rust:   {want[i] if i < len(want) else '<end>'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
