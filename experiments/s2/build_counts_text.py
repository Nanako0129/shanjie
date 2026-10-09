"""S2 實驗：從純文字句子檔統計 unigram／bigram（口語語料：Common Voice zh-TW、Tatoeba cmn）。

和 build_counts.py 用同一套轉換、切句與斷詞。
用法：python3 experiments/s2/build_counts_text.py <out.pkl> <句子檔>...（.tsv 取最後一欄；.bz2 自動解壓）
"""
import argparse
import bz2
import collections
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_counts as bc  # noqa: E402


def lines(path):
    f = bz2.open(path, "rt", encoding="utf-8") if path.endswith(".bz2") else open(path, encoding="utf-8")
    for line in f:
        line = line.rstrip("\n")
        yield line.split("\t")[-1] if ".tsv" in path else line


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--expected", action="store_true", help="詞圖上的期望次數（S2n 契約 §6.2）；不算 trigram")
    ap.add_argument("--extra-lexicon", action="append", default=[], metavar="FILE", help="model-v5：疊加層格式的檔加進斷詞詞庫，可給多次")
    ap.add_argument("out")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args(argv)
    expected, out, paths = a.expected, a.out, a.paths
    bc._init(False, expected, extra_lexicons=tuple(os.path.abspath(f) for f in a.extra_lexicon))
    lex, (phrase, char, maxp) = bc._W["lex"], bc._W["conv"]
    uni, bi, tri, runs = collections.Counter(), collections.Counter(), collections.Counter(), 0
    for p in paths:
        for s in lines(p):
            for run in bc.HAN.findall(bc.convert(s, phrase, char, maxp)):
                if len(run) < 2:
                    continue
                if expected:
                    r = bc.expected_counts(lex, run)
                    if r is None:
                        continue
                    runs += 1
                    for w, e in r[0].items():
                        uni[w] += e
                    for k, e in r[1].items():
                        bi[k] += e
                    continue
                ws = bc.segment(lex, run)
                if not ws:
                    continue
                runs += 1
                prev = "<s>"
                for w in ws:
                    uni[w] += 1; bi[(prev, w)] += 1; prev = w
                bi[(prev, "</s>")] += 1
                seq = ["<s>", "<s>"] + ws + ["</s>"]
                for i in range(2, len(seq)):
                    tri[(seq[i - 2], seq[i - 1], seq[i])] += 1
    pickle.dump({"uni": uni, "bi": bi, "tri": tri, "articles": 0, "runs": runs}, open(out, "wb"))
    print(f"{runs} runs, {sum(uni.values())} tokens, {len(uni)} types, {len(bi)} bigram types")


if __name__ == "__main__":
    main()
