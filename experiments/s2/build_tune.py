"""S2 大調整集：從不參與計數的真人句子建調整列（`前文|句子|讀音`），寫到 ~/.cache/shanjie/work/s2/tune/。

- cvtune：Common Voice zh-TW 與 Tatoeba cmn，句子雜湊（md5）餘 5 等於 0 的那 20% 切出來，不進計數。
  計數用的另外 80% 寫成 colloquial-train.txt，給 build_counts_text.py。
- wikitune：維基第 300,000 篇以後的條目（計數只用到前 200,000 篇）。
都在標點、空白與英數處切成 4–30 字的子句，讀音用 tools/readings.py；含 CHECK 或讀不出的列丟掉。
用法：python3 experiments/s2/build_tune.py [--cv-rows 4000] [--wiki-rows 3000]
docs/contracts/tw-conv.md §2.1：Common Voice zh-TW 是 native（一個字都不改）；Tatoeba、維基是 mixed（逐句判斷簡繁，簡體句走 tw-conv.tsv）。
寫出的 colloquial-train.txt 已經轉過，之後的計數與訓練一律當 native 讀。--cv-name 改 cv 這半的輸出檔名。
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


def cv_files():
    """The Common Voice and Tatoeba sources as (path, kind), in the order the split reads them: Common Voice zh-TW is native (Taiwan's own text,
    never converted), Tatoeba cmn is mixed (docs/contracts/tw-conv.md section 2.1)."""
    return [(os.path.join(SRC, f), "native") for f in sorted(os.listdir(SRC)) if f.startswith("cv-")] + [(os.path.join(SRC, "cmn_sentences.tsv.bz2"), "mixed")]


def tune_sentences(files):
    """(raw, is_tune, kind) for every source line: raw = the last tab field. The split hashes the raw sentence, so a change of conversion never moves it."""
    for p, kind in files:
        f = bz2.open(p, "rt", encoding="utf-8") if p.endswith(".bz2") else open(p, encoding="utf-8")
        for line in f:
            raw = line.rstrip("\n").split("\t")[-1]
            yield raw, is_tune(raw), kind


def split_colloquial(files, conv):
    """(train, tune): train = the sentences whose raw text hashes outside the tune 20%; tune = the Han clauses of the other 20%. The split hashes
    the raw sentence, so it does not depend on the conversion; a native sentence is used as it is, a mixed one is converted (build_counts.convert)."""
    train, tune = [], set()
    for raw, tuned, kind in tune_sentences(files):
        s = raw if bc.check_kind(kind) == "native" else bc.convert(raw, *conv)
        if tuned:
            tune.update(HAN.findall(s))
        else:
            train.append(s)
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
    ap.add_argument("--mw", action="store_true", help="S2w：wikitune 的參考句改用 MediaWiki 的 zh-tw 轉換（只影響 --part wiki；cvtune 與 colloquial-train 不變）")
    ap.add_argument("--wiki-range", type=int, nargs=2, default=[300_000, 305_000], metavar=("LO", "HI"),
                    help="維基第 LO..HI-1 篇（預設是 wikitune 的 300,000–304,999；rerank-formal 的 wikijudge 用 310000 315000）")
    ap.add_argument("--wiki-name", default="wikitune", help="輸出檔名（不含 .txt）")
    ap.add_argument("--progress-every", type=int, default=0, metavar="N", help="維基那半每讀 N 篇（含跳過的）在 stderr 印一行進度；預設 0＝不印，輸出不變")
    ap.add_argument("--cv-name", default="cvtune", help="cv 這半的輸出檔名（不含 .txt）")
    return ap


def main():
    ap = parser()
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    phrase, char, maxp = bc.load_conv()
    train, tune = split_colloquial(cv_files(), (phrase, char, maxp))
    rnd = random.Random(20261003)
    cv = rnd.sample(sorted(tune), min(a.cv_rows, len(tune)))
    if a.part != "wiki":
        open(os.path.join(OUT, "colloquial-train.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(train) + "\n")
        readings(cv, a.cv_name)
        print(f"colloquial-train: {len(train)} sentences")
    if a.part == "cv":
        return
    wiki = []
    if a.mw:
        sys.path.insert(0, os.path.join(ROOT, "experiments", "s2w"))
        import mwconv
        mw = mwconv.load()
    for raw in articles_in(*a.wiki_range, progress=a.progress_every):
        t = wiki_markup(mwconv.convert(mwconv.unescape(raw), mw) if a.mw else raw)
        wiki.extend(wiki_clauses(t, (phrase, char, maxp), a.mw))
    readings(rnd.sample(sorted(set(wiki)), min(a.wiki_rows, len(set(wiki)))), a.wiki_name)


def wiki_markup(t):
    """An article's wikitext after template and markup removal (what main hands to wiki_clauses)."""
    for _ in range(3):
        t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP:
        t = pat.sub(rep, t)
    return t


def wiki_clauses(text, conv, mw=False):
    """The Han clauses of one article after markup removal: each sentence converted as a mixed source (unless mw, which converted the whole
    article already), then split on non-Han."""
    out = []
    for s in bc.SENT.findall(text):
        out.extend(HAN.findall(s if mw else bc.convert(s, *conv)))
    return out


def articles_in(lo, hi, progress=0):
    """Wiki articles lo..hi-1 (0-based, in dump order). progress > 0: a line on stderr every that many articles read (the skipped ones too), for a remote heartbeat."""
    for i, raw in enumerate(bc.articles(hi)):
        if progress and i and i % progress == 0:
            print(f"wiki articles read: {i}", file=sys.stderr, flush=True)
        if i >= lo:
            yield raw

if __name__ == "__main__":
    main()
