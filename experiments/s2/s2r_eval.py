"""S2r 評測（docs/contracts/s2r-sandhi-variants.md §4）：用出貨的 Rust 引擎（評測 CLI 的 --lm 模式）量探針。

兩種比較：
- base：同一個 CLI，原讀音 vs 換過讀音（§4.0 的基準：前提是換過讀音的比較容易錯）。
- ab：同一批列，CLI A（補齊前，main）vs CLI B（補齊後，這個分支），--rows 給 probe 或 orig。

每列對錯用 reference/proto/eval.py 的 lenient（S2v 已驗證和 Rust 一致）比對第一名。McNemar 精確檢定（雙尾）。
私有集合的逐列結果寫到 ~/side-project/shanjie-private/s2-probe/，終端只印數字。

用法：
  python3 experiments/s2/s2r_eval.py base <cli> [--sets public,cvtune,discordtune]
  python3 experiments/s2/s2r_eval.py ab <cliA> <cliB> --which probe|orig [--sets …]
"""
import argparse
import math
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
from build_probe import flip  # noqa: E402
from eval import lenient  # noqa: E402
from iter2 import all_sets, PRIVATE  # noqa: E402

LM = os.path.join(ROOT, "data", "lm", "bigram.sjlm")
OUTDIR = os.path.join(PRIVATE, "s2-probe")


def pairs(name, every=False):
    """(sentence, original syllables, flipped syllables) per row; with every=True all rows of the set
    (flipped = original where nothing flips), for the no-regression check on the original readings."""
    sets = all_sets()
    rows, private = (sets["dev302"][0] + sets["typing76"][0], False) if name == "public" else sets[name]
    out = []
    for sent, syls in rows:
        new = flip(sent, syls)
        if new is not None or every:
            out.append((sent, syls, new or syls))
    return out, private


def run_cli(cli, rows, private, tag):
    """rows: [(truth, syls)] -> [ok] by the top-1 of the CLI's dump."""
    d = OUTDIR if private else tempfile.gettempdir()
    os.makedirs(d, exist_ok=True)
    rows_path = os.path.join(d, f"s2r-{tag}.rows")
    dump_path = os.path.join(d, f"s2r-{tag}.dump")
    with open(rows_path, "w", encoding="utf-8") as f:
        for truth, syls in rows:
            f.write(f"|{truth}|{' '.join(syls)}\n")
    r = subprocess.run([cli, "--lm", LM, "--profile", "chat", "--rows", rows_path, "--dump", dump_path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"cli failed ({r.returncode}): {r.stderr.strip()[:200]}")
    top = {}
    for line in open(dump_path, encoding="utf-8"):
        i, rank, surface, _ = line.rstrip("\n").split("\t")
        if rank == "1":
            top[int(i)] = surface
    return [int(lenient(top[i + 1]) == lenient(truth)) for i, (truth, _) in enumerate(rows)]


def mcnemar(a, b):
    """a, b: paired 0/1 lists. Returns (fixed, broken, p two-sided exact)."""
    fixed = sum(1 for x, y in zip(a, b) if x == 0 and y == 1)
    broken = sum(1 for x, y in zip(a, b) if x == 1 and y == 0)
    n = fixed + broken
    if n == 0:
        return fixed, broken, 1.0
    k = min(fixed, broken)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return fixed, broken, min(1.0, 2 * p)


def se_diff(a, b):
    d = [y - x for x, y in zip(a, b)]
    m = sum(d) / len(d)
    var = sum((v - m) ** 2 for v in d) / max(1, len(d) - 1)
    return math.sqrt(var / len(d))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["base", "ab"])
    ap.add_argument("cli", nargs="+")
    ap.add_argument("--which", choices=["probe", "orig"], default="probe")
    ap.add_argument("--sets", default="public,cvtune,discordtune")
    ap.add_argument("--all", action="store_true", help="ab --which orig on every row of the set, not only rows with 一/不 to flip")
    a = ap.parse_args()
    for name in a.sets.split(","):
        ps, private = pairs(name, every=a.all)
        if a.mode == "base":
            orig = run_cli(a.cli[0], [(s, o) for s, o, _ in ps], private, f"{name}-orig")
            prob = run_cli(a.cli[0], [(s, p) for s, _, p in ps], private, f"{name}-probe")
            x, y, label = orig, prob, "orig -> probe"
        else:
            rows = [(s, p if a.which == "probe" else o) for s, o, p in ps]
            x = run_cli(a.cli[0], rows, private, f"{name}-{a.which}-A")
            y = run_cli(a.cli[1], rows, private, f"{name}-{a.which}-B")
            label = f"A -> B ({a.which})"
        fixed, broken, p = mcnemar(x, y)
        n = len(ps)
        print(f"{name}: n={n} {label}: {sum(x)}/{n} -> {sum(y)}/{n} (diff {sum(y) - sum(x):+d}, "
              f"se {se_diff(x, y) * n:.1f} rows) fixed={fixed} broken={broken} p={p:.3g}")


if __name__ == "__main__":
    main()
