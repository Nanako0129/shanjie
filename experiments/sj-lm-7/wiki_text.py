"""SJ-LM-7 契約 §2.1a：萌典補詞要數子字串的「維基 20 萬篇」純文字。和 build_counts.py 數的是同一批篇、同一條管線
（article_runs：模板、標記、斷句、轉換、漢字段），一行一段漢字（2 字以上，和計數一樣），篇照 dump 順序。
用法：python3 experiments/sj-lm-7/wiki_text.py OUT [--articles 200000] [--procs N] [--no-trad-tw-phrases]"""
import argparse
import multiprocessing as mp
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "s2"))
import build_counts as bc  # noqa: E402

_CONV = {}


def _init(trad_tw):
    _CONV["conv"], _CONV["trad_tw"] = bc.load_conv(), trad_tw


def _runs(batch):
    return "".join(r + "\n" for raw in batch for r in bc.article_runs(raw, _CONV["conv"], trad_tw_phrases=_CONV["trad_tw"]) if len(r) >= 2)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--articles", type=int, default=200_000)
    ap.add_argument("--procs", type=int, default=max(1, os.cpu_count() - 2))
    ap.add_argument("--no-trad-tw-phrases", action="store_true", help="和 build_counts.py 的同名旗標相同（SJ-LM-7 §2.2）")
    a = ap.parse_args(argv)
    bc.load_conv()   # OpenCC 檔錯了在這裡就停，不在每個 worker 裡
    with mp.Pool(a.procs, initializer=_init, initargs=(not a.no_trad_tw_phrases,)) as pool, open(a.out + ".tmp", "w", encoding="utf-8") as f:
        for text in pool.imap(_runs, bc.batches(bc.articles(a.articles))):   # imap：輸出照篇的順序
            f.write(text)
    os.replace(a.out + ".tmp", a.out)


if __name__ == "__main__":
    main()
