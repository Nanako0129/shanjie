"""IT+AI 詞包的維基詞頻（契約 docs/contracts/it-pack.md §2「頻率門檻」）：候選字串在 zhwiki 全部 ns0 條目的不重疊出現次數。

用法：python3 tools/it_wiki_counts.py --dump zhwiki-20261001-pages-articles.xml.bz2 --candidates FILE --out wiki-counts.tsv [--procs N]

正文＝build_counts.article_sentences，MediaWiki zh-tw 轉換（需要 zhconv-rs 與 mwdata.json，見 s2w-mediawiki-zhtw.md），conv 給恆等表。
計數＝每個句子裡每個候選字串的不重疊出現次數（等於 str.count），全部句子加總；結果與 --procs 無關。
輸出 `字串\\t次數`（依字串排序，含 0 次），第一行 `# dump_sha256=… candidates_sha256=… tool_commit=…`。dump 的 SHA-256 不符就停。
"""
import argparse
import collections
import hashlib
import multiprocessing
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
import build_counts as bc  # noqa: E402

DUMP_SHA = "5db9052efe9ca97aa3da05ffe5b2f12c8ffe5e040f0a9d1ced82ec2fda5d7e2f"
IDENTITY = ({}, {}, 0)   # build_counts.convert 的 (詞組表, 字表, 最長詞組)：空表＝不轉
_W = {}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            h.update(b)
    return h.hexdigest()


def tool_commit():
    run = lambda *a: subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True).stdout.strip()
    return run("rev-parse", "HEAD") + ("-dirty" if run("status", "--porcelain", "--", "tools/it_wiki_counts.py", "tools/it_sources.py") else "")


def count_sentence(s, cands, first, lens, out):
    """s 裡每個候選字串的不重疊出現次數（leftmost，等於 str.count）加進 out。掃每個位置、每個長度查集合，比每個候選各掃一次快得多。"""
    end = {}
    n = len(s)
    for i in range(n):
        if s[i] not in first:
            continue
        for L in lens:
            if i + L > n:
                break
            w = s[i:i + L]
            if w in cands and end.get(w, 0) <= i:
                end[w] = i + L
                out[w] += 1


def init_worker(cands, mw_mode):
    _W["cands"] = frozenset(cands)
    _W["first"] = {c[0] for c in cands}
    _W["lens"] = sorted({len(c) for c in cands})
    _W["mw"] = _W["mwconv"] = None
    if mw_mode == "mediawiki":
        sys.path.insert(0, os.path.join(ROOT, "experiments", "s2w"))
        import mwconv
        _W["mw"], _W["mwconv"] = mwconv.load(), mwconv


def count_batch(raws):
    out = collections.Counter()
    for raw in raws:
        for s in bc.article_sentences(raw, IDENTITY, _W["mw"], _W["mwconv"]):
            count_sentence(s, _W["cands"], _W["first"], _W["lens"], out)
    return out


def count_dump(dump, cands, procs=1, mw_mode="mediawiki", batch=100):
    total = collections.Counter()
    batches = bc.batches(bc.articles(10**9, dump=dump), batch)
    if procs <= 1:
        init_worker(cands, mw_mode)
        for b in batches:
            total.update(count_batch(b))
    else:
        with multiprocessing.Pool(procs, init_worker, (cands, mw_mode)) as pool:
            for c in pool.imap_unordered(count_batch, batches, chunksize=1):
                total.update(c)
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--procs", type=int, default=1)
    ap.add_argument("--expect-sha256", default=DUMP_SHA, help="dump 的 SHA-256（預設是契約固定的那份）")
    ap.add_argument("--no-mw", action="store_true", help="只給測試：不做 zh-tw 轉換")
    a = ap.parse_args()
    dsha = sha256(a.dump)
    if dsha != a.expect_sha256:
        sys.exit(f"dump SHA-256 mismatch: {dsha}")
    cands = [l.rstrip("\n") for l in open(a.candidates, encoding="utf-8") if l.strip()]
    counts = count_dump(a.dump, cands, a.procs, "none" if a.no_mw else "mediawiki")
    with open(a.out, "w", encoding="utf-8") as o:
        o.write(f"# dump_sha256={dsha} candidates_sha256={sha256(a.candidates)} tool_commit={tool_commit()}\n")
        for c in sorted(set(cands)):
            o.write(f"{c}\t{counts[c]}\n")


if __name__ == "__main__":
    main()
