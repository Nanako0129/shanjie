"""S2n 評測（docs/contracts/s2n-simplified-residue.md §3）：同一批列，改前的 CLI（模型 model-v1、舊疊加層）對改後的 CLI
（這個 worktree、model-v2），聊天與書面兩種設定，配對比較第一名（修好／弄壞／McNemar 精確檢定，雙尾）。
CLI 從自己的 repo 根目錄讀詞庫、疊加層與評測檔，所以「改前」是 `git archive` 出來的舊樹（各自 cargo build --release -p cli）。
只用公開集合：dev302、typing76、user-reported、cvtune、wikitune（舊／新參考句各一）。

用法：python3 experiments/s2n/evaluate.py <改前 CLI> <改後 CLI> --lm-a <model-v1> --lm-b <model-v2> [--tune-old DIR --tune-new DIR]
      python3 experiments/s2n/evaluate.py … --demo <rows 檔>      # 印每列兩種設定的改前／改後第一名
"""
import argparse
import math
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
from eval import lenient  # noqa: E402

DEV = os.path.join(ROOT, "eval", "dev")


def run(cli, lm, profile, args):
    """回傳 [(第一名, )] 依列序；args 是 `--dev 302` 或 `--rows 檔`。"""
    dump = tempfile.mktemp(prefix="s2n-")
    r = subprocess.run([cli, "--lm", lm, "--profile", profile, *args, "--dump", dump], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"cli failed: {r.stderr.strip()[:200]}")
    top = {}
    for line in open(dump, encoding="utf-8"):
        i, rank, surface, _ = line.rstrip("\n").split("\t")
        if rank == "1":
            top[int(i)] = surface
    os.remove(dump)
    return [top[i + 1] for i in range(len(top))]


def truths(args):
    if args[0] == "--dev":
        rows = []
        for f in sorted(f for f in os.listdir(DEV) if f.endswith(".txt")):
            rows += [l.split("|") for l in open(os.path.join(DEV, f), encoding="utf-8").read().splitlines() if l.count("|") == 2]
        return [r[1] for r in rows[:int(args[1])]]
    return [l.split("|")[1] for l in open(args[1], encoding="utf-8").read().splitlines() if l.count("|") == 2]


def mcnemar(a, b):
    fixed = sum(1 for x, y in zip(a, b) if not x and y)
    broken = sum(1 for x, y in zip(a, b) if x and not y)
    n = fixed + broken
    p = 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(fixed, broken) + 1)) / 2 ** n)
    return fixed, broken, p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cli_a"); ap.add_argument("cli_b")
    ap.add_argument("--lm-a", required=True); ap.add_argument("--lm-b", required=True)
    ap.add_argument("--tune-old"); ap.add_argument("--tune-new")
    ap.add_argument("--demo")
    ap.add_argument("--reported", help="另一份 user-reported.txt（例如更新中的分支）；給了就用它取代 eval/dev 裡的")
    a = ap.parse_args()
    if a.demo:
        rows = [l.split("|") for l in open(a.demo, encoding="utf-8").read().splitlines() if l.count("|") == 2]
        for p in ("chat", "formal"):
            x, y = run(a.cli_a, a.lm_a, p, ["--rows", a.demo]), run(a.cli_b, a.lm_b, p, ["--rows", a.demo])
            for r, u, v in zip(rows, x, y):
                print(f"{p}\t{r[1]}\t{u}\t{v}\t{'' if u == v else 'CHANGED'}")
        return
    sets = [("dev302", ["--dev", "302"]), ("typing76", ["--rows", os.path.join(DEV, "user-typing.txt")]),
            ("user-reported", ["--rows", a.reported or os.path.join(DEV, "user-reported.txt")])]
    for tag, d in (("old", a.tune_old), ("new", a.tune_new)):
        if d:
            sets += [(f"cvtune-{tag}", ["--rows", os.path.join(d, "cvtune.txt")]), (f"wikitune-{tag}", ["--rows", os.path.join(d, "wikitune.txt")])]
    for name, args in sets:
        t = truths(args)
        for p in ("chat", "formal"):
            x = [lenient(s) == lenient(g) for s, g in zip(run(a.cli_a, a.lm_a, p, args), t)]
            y = [lenient(s) == lenient(g) for s, g in zip(run(a.cli_b, a.lm_b, p, args), t)]
            f, b, pv = mcnemar(x, y)
            print(f"{name:15s} {p:6s} n={len(t):5d}  {sum(x):5d} -> {sum(y):5d}  fixed {f:3d} broken {b:3d}  McNemar p={pv:.3g}")


if __name__ == "__main__":
    main()
