"""S2 大調整集：從不參與計數的真人句子建調整列（`前文|句子|讀音`），寫到 ~/.cache/shanjie/work/s2/tune/。

- cvtune：Common Voice zh-TW 與 Tatoeba cmn，句子雜湊（md5）餘 5 等於 0 的那 20% 切出來，不進計數。
  計數用的另外 80% 寫成 colloquial-train.txt，給 build_counts_text.py。
- wikitune：維基第 300,000 篇以後的條目（計數只用到前 200,000 篇）。
都在標點、空白與英數處切成 4–30 字的子句，讀音用 tools/readings.py；含 CHECK 或讀不出的列丟掉。
用法：python3 experiments/s2/build_tune.py [--cv-rows 4000] [--wiki-rows 3000]
"""
import argparse
import bz2
import hashlib
import os
import random
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import build_counts as bc  # noqa: E402

SRC = os.path.expanduser("~/.cache/shanjie/sources/colloquial")
OUT = os.path.expanduser("~/.cache/shanjie/work/s2/tune")
HAN = re.compile(r"[一-鿿]{4,30}")


def is_tune(s):
    return int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16) % 5 == 0


def readings(rows, name):
    src, dst = os.path.join(OUT, f"{name}.in"), os.path.join(OUT, f"{name}.all")
    open(src, "w", encoding="utf-8").write("".join(f"|{s}\n" for s in rows))
    r = subprocess.run(["python3", os.path.join(ROOT, "tools", "readings.py"), src, dst], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"readings.py failed on {name}")
    check = {l.split("\t")[1] for l in r.stderr.splitlines() if l.startswith("CHECK\t")}
    kept = [l for l in open(dst, encoding="utf-8") if l.split("|")[1] not in check]
    open(os.path.join(OUT, f"{name}.txt"), "w", encoding="utf-8").write("".join(kept))
    print(f"{name}: {len(rows)} clauses → {len(kept)} rows (dropped {len(rows) - len(kept)} CHECK/unreadable)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cv-rows", type=int, default=4000)
    ap.add_argument("--wiki-rows", type=int, default=3000)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    phrase, char, maxp = bc.load_conv()
    train, tune = [], set()
    files = [os.path.join(SRC, f) for f in sorted(os.listdir(SRC)) if f.startswith("cv-")] + [os.path.join(SRC, "cmn_sentences.tsv.bz2")]
    for p in files:
        f = bz2.open(p, "rt", encoding="utf-8") if p.endswith(".bz2") else open(p, encoding="utf-8")
        for line in f:
            s = bc.convert(line.rstrip("\n").split("\t")[-1], phrase, char, maxp)
            if is_tune(s):
                tune.update(HAN.findall(s))
            else:
                train.append(s)
    open(os.path.join(OUT, "colloquial-train.txt"), "w", encoding="utf-8").write("\n".join(train) + "\n")
    rnd = random.Random(20261003)
    readings(rnd.sample(sorted(tune), min(a.cv_rows, len(tune))), "cvtune")
    wiki = []
    for i, raw in enumerate(bc.articles(305_000)):
        if i < 300_000:
            continue
        t = raw
        for _ in range(3):
            t = bc.TEMPLATE.sub("", t)
        for pat, rep in bc.MARKUP:
            t = pat.sub(rep, t)
        for s in bc.SENT.findall(t):
            wiki.extend(HAN.findall(bc.convert(s, phrase, char, maxp)))
    readings(rnd.sample(sorted(set(wiki)), min(a.wiki_rows, len(set(wiki)))), "wikitune")
    print(f"colloquial-train: {len(train)} sentences")


if __name__ == "__main__":
    main()
