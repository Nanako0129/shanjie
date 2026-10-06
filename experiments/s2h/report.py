"""S2h 報告（契約 §4、§5.2–§5.5）：不加前文 vs 加前文（history 規則）的第一名，配對比較。

  python3 experiments/s2h/report.py sets      切尾集（關卡）與 dev302／typing76／user-reported（守門）：n、前後 top-1、修好／弄壞、McNemar
  python3 experiments/s2h/report.py probe F   探針檔 F（前文|句子|讀音）每列前後第一名與分數
  python3 experiments/s2h/report.py empty     前文為空的列，Rust cli 有無 --context 的第一名逐列相同（§5.2）
Python 參考實作計算；Rust 與它的一致性由 eval/golden/s2h-lm-context.txt 與切尾集的 sha 比對另外證明。
"""
import glob
import math
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402
import lm as L  # noqa: E402
from eval import lenient  # noqa: E402

WORK = os.path.expanduser("~/.cache/shanjie/work/s2h")
LMF = os.path.join(ROOT, "data", "lm", "bigram.sjlm")
CLI = os.path.join(ROOT, "target", "release", "shanjie-eval")


def rows_of(paths, limit=None):
    out = []
    for f in paths:
        for line in open(f, encoding="utf-8"):
            p = line.rstrip("\n").split("|")
            if len(p) == 3:
                out.append((p[0], p[1], p[2].split()))
    return out[:limit] if limit else out


def mcnemar(a, b):
    fixed = sum(x == 0 and y == 1 for x, y in zip(a, b))
    broken = sum(x == 1 and y == 0 for x, y in zip(a, b))
    n = fixed + broken
    if n == 0:
        return fixed, broken, 1.0
    k = min(fixed, broken)
    return fixed, broken, min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def load():
    lm = L.BigramLM(LMF)
    base = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.OVERLAYS)
    ov = {l.split("\t")[1] for l in open(os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"), encoding="utf-8")}
    return lm, L.cap_overlay(base, ov, lm)


def top(lex, lm, syls, profile, start):
    sc, ws = L.decode(lex, syls, lm, profile, start=start)[0]
    return "".join(ws), sc


def sets():
    dev = sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt")))
    d = lambda n: os.path.join(ROOT, "eval", "dev", n)  # noqa: E731
    return [
        ("gate", "cvtail", rows_of([os.path.join(WORK, "cvtail.txt")])),
        ("gate", "wikitail", rows_of([os.path.join(WORK, "wikitail.txt")])),
        ("guard", "dev302", rows_of(dev, 302)),
        ("guard", "typing76", rows_of([d("user-typing.txt")])),
        ("guard", "user-reported", rows_of([d("user-reported.txt")])),
    ]


def cmd_sets():
    lm, lex = load()
    print("kind set profile n rows_with_history base_top1 ctx_top1 fixed broken p")
    for kind, name, rows in sets():
        for prof in ("chat", "formal"):
            a, b, hist = [], [], 0
            for ctx, truth, syls in rows:
                h = L.history(L.context_key(ctx), lm)
                s0 = top(lex, lm, syls, prof, "<s>")[0]
                s1 = s0 if h == "<s>" else top(lex, lm, syls, prof, h)[0]
                hist += h != "<s>"
                a.append(int(lenient(s0) == lenient(truth)))
                b.append(int(lenient(s1) == lenient(truth)))
            f, br, p = mcnemar(a, b)
            print(f"{kind} {name} {prof} n={len(rows)} history={hist} base={sum(a)} ctx={sum(b)} fixed={f} broken={br} p={p:.3g}", flush=True)


def cmd_probe(path):
    lm, lex = load()
    for prof in ("chat", "formal"):
        for ctx, truth, syls in rows_of([path]):
            h = L.history(L.context_key(ctx), lm)
            s0, c0 = top(lex, lm, syls, prof, "<s>")
            s1, c1 = top(lex, lm, syls, prof, h)
            print(f"{prof}\t{ctx}|{truth}\thist={h}\tbefore {s0} {c0:.4f}\tafter {s1} {c1:.4f}")


def cmd_empty():
    """Rust cli: rows whose context cuts to nothing keep the same first name with and without --context."""
    lm, _ = load()
    for name, args in (("dev302", ["--dev", "302"]),
                       ("typing76", ["--rows", os.path.join(ROOT, "eval/dev/user-typing.txt")]),
                       ("user-reported", ["--rows", os.path.join(ROOT, "eval/dev/user-reported.txt")])):
        rows = rows_of(glob.glob(os.path.join(ROOT, "eval/dev/*.txt")) and sorted(glob.glob(os.path.join(ROOT, "eval/dev/*.txt"))), 302) if name == "dev302" else rows_of([args[1]])
        for prof in ("chat", "formal"):
            tops = []
            for extra in ([], ["--context"]):
                dump = os.path.join(WORK, f"empty-{name}-{prof}{'-ctx' if extra else ''}.tsv")
                if os.path.exists(dump):
                    os.remove(dump)
                subprocess.run([CLI, "--lm", LMF, "--profile", prof, *args, *extra, "--dump", dump], check=True, stdout=subprocess.DEVNULL)
                tops.append({int(l.split("\t")[0]): l.split("\t")[2] for l in open(dump, encoding="utf-8") if l.split("\t")[1] == "1"})
            empty = [i for i, (ctx, _, _) in enumerate(rows, 1) if L.context_key(ctx) == ""]
            same = sum(tops[0][i] == tops[1][i] for i in empty)
            hist_rows = [i for i, (ctx, _, _) in enumerate(rows, 1) if L.history(L.context_key(ctx), lm) == "<s>"]
            same_h = sum(tops[0][i] == tops[1][i] for i in hist_rows)
            print(f"{name} {prof}: empty-context rows {len(empty)}, same top-1 {same}; history=<s> rows {len(hist_rows)}, same {same_h}")


if __name__ == "__main__":
    {"sets": cmd_sets, "probe": lambda: cmd_probe(sys.argv[2]), "empty": cmd_empty}[sys.argv[1]]()
