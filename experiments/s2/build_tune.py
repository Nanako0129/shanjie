"""S2 大調整集：從不參與計數的真人句子建調整列（`前文|句子|讀音`），寫到 ~/.cache/shanjie/work/s2/tune/。

- cvtune：Common Voice zh-TW 與 Tatoeba cmn，句子雜湊（md5）餘 5 等於 0 的那 20% 切出來，不進計數。
  計數用的另外 80% 寫成 colloquial-train.txt，給 build_counts_text.py。
- wikitune：維基第 300,000 篇以後的條目（計數只用到前 200,000 篇）。
都在標點、空白與英數處切成 4–30 字的子句，讀音用 tools/readings.py；含 CHECK 或讀不出的列丟掉。
用法：python3 experiments/s2/build_tune.py [--cv-rows 4000] [--wiki-rows 3000]
SJ-LM-7（契約 §2.2）：--train-only-new-conv 只重寫 colloquial-train.txt，train 那一側的繁體句不套 TWPhrases（插件不換成外掛）；
切分與 tune 那一側仍用現行轉換算，所以 cvtune 的抽樣與參考句不變（那個選項不寫 cvtune、wikitune）。
docs/contracts/tune-no-twphrases.md（TW0）：--cv-no-trad-tw-phrases 讓 cv 這半的 tune 側（參考句與讀音）繁體句不套 TWPhrases，--cv-name 改輸出檔名；
維基那半接在 cv 那半的亂數狀態之後，所以 --part wiki 也要帶 --cv-no-trad-tw-phrases（再加 --tune-no-trad-tw-phrases 才是維基自己的不套）。不帶這兩個選項時所有輸出和以前逐位元組相同。
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
    """The Common Voice and Tatoeba sources, in the order the split reads them."""
    return [os.path.join(SRC, f) for f in sorted(os.listdir(SRC)) if f.startswith("cv-")] + [os.path.join(SRC, "cmn_sentences.tsv.bz2")]


def tune_sentences(files):
    """(raw, is_tune) for every source line: raw = the last tab field. The split hashes the raw sentence, so a change of conversion never moves it."""
    for p in files:
        f = bz2.open(p, "rt", encoding="utf-8") if p.endswith(".bz2") else open(p, encoding="utf-8")
        for line in f:
            raw = line.rstrip("\n").split("\t")[-1]
            yield raw, is_tune(raw)


def split_colloquial(files, conv, new_train=False, tune_tw=True):
    """(train, tune): train = the sentences whose raw text hashes outside the tune 20%; tune = the Han clauses of the other 20%. The split hashes
    the raw sentence; new_train only changes how the train sentences are converted (Traditional ones keep their wording, SJ-LM-7 section 2.2),
    so the split and the tune side are the same with or without it. tune_tw=False (--cv-no-trad-tw-phrases, TW0 contract section 1.1): the tune
    side is converted without TWPhrases for Traditional sentences (Simplified ones as before); the split is unchanged and the train side stays as
    it was, but main() does not write it."""
    train, tune = [], set()
    for raw, tuned in tune_sentences(files):
        if tuned:   # 用原始句子算雜湊：轉換方式改了，切分也不會變
            tune.update(HAN.findall(bc.convert(raw, *conv, trad_tw_phrases=tune_tw)))
        else:
            train.append(bc.convert(raw, *conv, trad_tw_phrases=not new_train))
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
    ap.add_argument("--progress-every", type=int, default=0, metavar="N", help="維基那半每讀 N 篇（含跳過的）在 stderr 印一行進度；預設 0＝不印，輸出不變")
    ap.add_argument("--cv-no-trad-tw-phrases", action="store_true",
                    help="docs/contracts/tune-no-twphrases.md §1.1：cv 這半 tune 側的參考句與讀音，繁體句不套 OpenCC TWPhrases（簡體句照舊轉換；哪些原句進 tune 不變）；"
                         "不寫 colloquial-train。維基那半接在 cv 那半的亂數狀態之後，所以 --part wiki 也要帶這個選項。與 --train-only-new-conv 互斥")
    ap.add_argument("--cv-name", default="cvtune", help="cv 這半的輸出檔名（不含 .txt）")
    ap.add_argument("--tune-no-trad-tw-phrases", action="store_true",
                    help="docs/contracts/sr-no-twphrases.md §5：wiki 參考句（與從它取的讀音）的繁體句不套 OpenCC TWPhrases（原文的「權限」不會變成「許可權」）；只影響 --part wiki，cvtune 與 colloquial-train 不變")
    return ap


def main():
    ap = parser()
    a = ap.parse_args()
    if a.cv_no_trad_tw_phrases and a.train_only_new_conv:
        ap.error("--cv-no-trad-tw-phrases does not combine with --train-only-new-conv (that one writes only colloquial-train)")
    os.makedirs(OUT, exist_ok=True)
    phrase, char, maxp = bc.load_conv()
    train, tune = split_colloquial(cv_files(), (phrase, char, maxp), a.train_only_new_conv, not a.cv_no_trad_tw_phrases)
    if a.train_only_new_conv:
        path = os.path.join(OUT, "colloquial-train.txt")
        open(path, "w", encoding="utf-8", newline="\n").write("\n".join(train) + "\n")
        print(f"colloquial-train (new conversion): {len(train)} sentences, {sum('插件' in t for t in train)} with 插件, SHA-256 {hashlib.sha256(open(path, 'rb').read()).hexdigest()}")
        return
    rnd = random.Random(20261003)
    cv = rnd.sample(sorted(tune), min(a.cv_rows, len(tune)))
    if a.part != "wiki":
        if not a.cv_no_trad_tw_phrases:   # TW0: the pool is not rebuilt, the existing colloquial-train.txt stays untouched
            open(os.path.join(OUT, "colloquial-train.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(train) + "\n")
        readings(cv, a.cv_name)
        if not a.cv_no_trad_tw_phrases:
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
        wiki.extend(wiki_clauses(t, (phrase, char, maxp), a.mw, not a.tune_no_trad_tw_phrases))
    readings(rnd.sample(sorted(set(wiki)), min(a.wiki_rows, len(set(wiki)))), a.wiki_name)


def wiki_markup(t):
    """An article's wikitext after template and markup removal (what main hands to wiki_clauses)."""
    for _ in range(3):
        t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP:
        t = pat.sub(rep, t)
    return t


def wiki_clauses(text, conv, mw=False, tw=True):
    """The Han clauses of one article after markup removal: each sentence converted (unless mw, which converted the whole article already),
    then split on non-Han. tw=False: Traditional sentences keep their wording, no TWPhrases (docs/contracts/sr-no-twphrases.md section 5)."""
    out = []
    for s in bc.SENT.findall(text):
        out.extend(HAN.findall(s if mw else bc.convert(s, *conv, trad_tw_phrases=tw)))
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
