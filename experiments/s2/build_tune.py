"""S2 大調整集：從不參與計數的真人句子建調整列（`前文|句子|讀音`），寫到 ~/.cache/shanjie/work/s2/tune/。

- cvtune：Common Voice zh-TW 與 Tatoeba cmn，句子雜湊（md5）餘 5 等於 0 的那 20% 切出來，不進計數。
  計數用的另外 80% 寫成 colloquial-train.txt，給 build_counts_text.py。
- wikitune：維基第 300,000 篇以後的條目（計數只用到前 200,000 篇）。
都在標點、空白與英數處切成 4–30 字的子句，讀音用 tools/readings.py；含 CHECK 或讀不出的列丟掉。
用法：python3 experiments/s2/build_tune.py [--cv-rows 4000] [--wiki-rows 3000]
SJ-LM-7（契約 §2.2）：--train-only-new-conv 只重寫 colloquial-train.txt，train 那一側的繁體句不套 TWPhrases（插件不換成外掛）；
切分與 tune 那一側仍用現行轉換算，所以 cvtune 的抽樣與參考句不變（那個選項不寫 cvtune、wikitune）。
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

SRC = os.path.join(bc.SRC, "colloquial")
OUT = os.path.join(bc.OUT, "tune")   # 跟著 build_counts 的 S2_WORK
HAN = re.compile(r"[一-鿿]{4,30}")


def is_tune(s):
    return int(hashlib.md5(s.encode("utf-8")).hexdigest(), 16) % 5 == 0


def split_colloquial(files, conv, new_train=False):
    """(train, tune): train = the sentences whose raw text hashes outside the tune 20%; tune = the Han clauses of the other 20%. The split hashes
    the raw sentence and tune is always converted with the current convert(); new_train only changes how the train sentences are converted
    (Traditional ones keep their wording, SJ-LM-7 section 2.2), so the split and the tune side are the same with or without it."""
    train, tune = [], set()
    for p in files:
        f = bz2.open(p, "rt", encoding="utf-8") if p.endswith(".bz2") else open(p, encoding="utf-8")
        for line in f:
            raw = line.rstrip("\n").split("\t")[-1]
            s = bc.convert(raw, *conv)
            if is_tune(raw):   # 用原始句子算雜湊：轉換方式改了，切分也不會變
                tune.update(HAN.findall(s))
            else:
                train.append(bc.convert(raw, *conv, trad_tw_phrases=False) if new_train else s)
    return train, tune


def readings(rows, name):
    src, dst = os.path.join(OUT, f"{name}.in"), os.path.join(OUT, f"{name}.all")
    open(src, "w", encoding="utf-8", newline="\n").write("".join(f"|{s}\n" for s in rows))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "readings.py"), src, dst], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"readings.py failed on {name}")
    check = {l.split("\t")[1] for l in r.stderr.splitlines() if l.startswith("CHECK\t")}
    kept = [l for l in open(dst, encoding="utf-8") if l.split("|")[1] not in check]
    open(os.path.join(OUT, f"{name}.txt"), "w", encoding="utf-8", newline="\n").write("".join(kept))
    print(f"{name}: {len(rows)} clauses → {len(kept)} rows (dropped {len(rows) - len(kept)} CHECK/unreadable)")


def parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cv-rows", type=int, default=4000)
    ap.add_argument("--wiki-rows", type=int, default=3000)
    ap.add_argument("--part", choices=["all", "cv", "wiki"], default="all",
                    help="cv：只做 Common Voice／Tatoeba（colloquial-train、cvtune，幾分鐘）；wiki：只做 wikitune（要讀 30 萬篇，約 20 分鐘）。"
                         "wiki 會重算 cv 的取樣來接上同一個亂數狀態，結果和 all 逐位元組相同")
    ap.add_argument("--train-only-new-conv", action="store_true",
                    help="SJ-LM-7 契約 §2.2：只寫 colloquial-train.txt，train 側的繁體句不套 OpenCC TWPhrases；切分與 tune 側照現行轉換，不寫 cvtune、wikitune；印出輸出的 SHA-256")
    ap.add_argument("--mw", action="store_true", help="S2w：wikitune 的參考句改用 MediaWiki 的 zh-tw 轉換（只影響 --part wiki；cvtune 與 colloquial-train 不變）")
    ap.add_argument("--wiki-range", type=int, nargs=2, default=[300_000, 305_000], metavar=("LO", "HI"),
                    help="維基第 LO..HI-1 篇（預設是 wikitune 的 300,000–304,999；rerank-formal 的 wikijudge 用 310000 315000）")
    ap.add_argument("--wiki-name", default="wikitune", help="輸出檔名（不含 .txt）")
    return ap


def main():
    a = parser().parse_args()
    os.makedirs(OUT, exist_ok=True)
    phrase, char, maxp = bc.load_conv()
    files = [os.path.join(SRC, f) for f in sorted(os.listdir(SRC)) if f.startswith("cv-")] + [os.path.join(SRC, "cmn_sentences.tsv.bz2")]
    train, tune = split_colloquial(files, (phrase, char, maxp), a.train_only_new_conv)
    if a.train_only_new_conv:
        path = os.path.join(OUT, "colloquial-train.txt")
        open(path, "w", encoding="utf-8", newline="\n").write("\n".join(train) + "\n")
        print(f"colloquial-train (new conversion): {len(train)} sentences, {sum('插件' in t for t in train)} with 插件, SHA-256 {hashlib.sha256(open(path, 'rb').read()).hexdigest()}")
        return
    rnd = random.Random(20261003)
    cv = rnd.sample(sorted(tune), min(a.cv_rows, len(tune)))
    if a.part != "wiki":
        open(os.path.join(OUT, "colloquial-train.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(train) + "\n")
        readings(cv, "cvtune")
        print(f"colloquial-train: {len(train)} sentences")
    if a.part == "cv":
        return
    wiki = []
    if a.mw:
        sys.path.insert(0, os.path.join(ROOT, "experiments", "s2w"))
        import mwconv
        mw = mwconv.load()
    for raw in articles_in(*a.wiki_range):
        t = raw
        if a.mw:
            t = mwconv.convert(mwconv.unescape(t), mw)
        for _ in range(3):
            t = bc.TEMPLATE.sub("", t)
        for pat, rep in bc.MARKUP:
            t = pat.sub(rep, t)
        for s in bc.SENT.findall(t):
            wiki.extend(HAN.findall(s if a.mw else bc.convert(s, phrase, char, maxp)))
    readings(rnd.sample(sorted(set(wiki)), min(a.wiki_rows, len(set(wiki)))), a.wiki_name)


def articles_in(lo, hi):
    """Wiki articles lo..hi-1 (0-based, in dump order)."""
    for i, raw in enumerate(bc.articles(hi)):
        if i >= lo:
            yield raw

if __name__ == "__main__":
    main()
