"""S2r-2 §4.4：重產 golden 前的預期——公開集合裡含有「主要讀音改變或新增變調列的疊加層詞」的列（上界：列裡有這個詞才可能變）。
用法：python3 experiments/s2/s2r2_predict.py   （需要 s2r2_samples.py 同樣的 OLD_REV）
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from s2r2_samples import OLD_REV, read  # noqa: E402


def words():
    old, _ = read(subprocess.check_output(["git", "-C", ROOT, "show", f"{OLD_REV}:data/lexicon/overlay-add.tsv"], text=True).splitlines())
    new, var = read(open(os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"), encoding="utf-8"))
    return {w for w in new if new[w] != old[w]} | set(var)


def sents(paths):
    out = []
    for p in paths:
        for l in open(p, encoding="utf-8"):
            f = l.rstrip("\n").split("|")
            if len(f) >= 2 and not l.startswith("#"):
                out.append(f[1])
    return out


def main():
    ws = words()
    sets = {
        "dev (first 302 of eval/dev, upper bound = all dev rows)": sents(sorted(glob.glob(os.path.join(ROOT, "eval/dev/*.txt")))),
        "probe": sents([os.path.join(ROOT, "eval/probe/s2r-probe.txt")]),
    }
    for t in ("trap", "daily", "moedict"):
        for p in glob.glob(os.path.join(ROOT, "eval", "**", f"*{t}*"), recursive=True):
            if os.path.isfile(p) and "golden" not in p:
                sets[f"{t} ({os.path.relpath(p, ROOT)})"] = sents([p])
    for name, ss in sets.items():
        hit = [s for s in ss if any(s[i:j] in ws for i in range(len(s)) for j in range(i + 2, min(len(s), i + 4) + 1))]
        print(f"{name}: {len(hit)}/{len(ss)} rows contain a changed/added overlay word")
        for s in hit[:40]:
            print("   ", s)


if __name__ == "__main__":
    main()
