"""PTT usage of candidate creator names (docs/contracts/names-creators.md section 2: boards >= 3 and articles >= 10 on the 110-board crawl).
Usage: python3 experiments/names-creators/count_usage.py [--include-known] [--jobs N] CANDIDATES LEXICON_ROOT PTT_DIR OUT
  CANDIDATES    one name per line (blank lines and # lines skipped)
  LEXICON_ROOT  a checkout root: data/lexicon/mcbpmf-data.txt plus the second column of data/lexicon/*-add.tsv and data/packs/*-add.tsv
                (overlay, slang, sandhi, names, both packs); a name already there is not counted
  PTT_DIR       PTT_DIR/<board>/<article>.html
  OUT           three header lines (# ptt_tree_sha256, # input_sha256, # tool_commit), then name<TAB>articles<TAB>boards, most articles first
                (articles = pages whose text holds the name, boards = distinct boards of those pages)
  --include-known  count names already in the lexicon too (docs/contracts/meme-pack.md section 2: the second count, of the current first places)
  --jobs N      split the pages over N processes; the output is byte-identical to one process
The tree hash is the english-next section 1 method: SHA-256 over the sorted lines "<board>/<file>\0<sha256 of the file>\n" of every .html page.
One regex pass per page. A name that is a prefix of a longer candidate is still counted: the longer match credits the shorter names it contains.
Tests: test_count_usage.py."""
import argparse
import collections
import glob
import hashlib
import html
import multiprocessing as mp
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402


def lexicon_words(root):
    words = set(ime.Lexicon(os.path.join(root, "data", "lexicon", "mcbpmf-data.txt")).by_word)
    for pat in ("data/lexicon/*-add.tsv", "data/packs/*-add.tsv"):
        for path in glob.glob(os.path.join(root, pat)):
            with open(path, encoding="utf-8") as f:
                words |= {l.split("\t")[1] for l in f if "\t" in l}
    return words


def read_names(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(dict.fromkeys(l.strip() for l in f if l.strip() and not l.startswith("#")))


_W = {}


def _init(names):
    # (?=...) so overlapping names (a name inside another) are found at their own start; the alternation tries longer names first,
    # so a name that is a prefix of a longer one at the same start is credited through `inside`.
    _W["pattern"] = re.compile("(?=(" + "|".join(re.escape(n) for n in sorted(names, key=lambda n: -len(n))) + "))") if names else None
    _W["inside"] = {n: [m for m in names if m != n and m in n] for n in names}


def _pages(paths):
    """-> (articles Counter, boards {name: set}, [(relative path, file sha256)]) over `paths`."""
    articles, boards, hashes = collections.Counter(), collections.defaultdict(set), []
    for path in paths:
        with open(path, "rb") as f:
            raw = f.read()
        board = os.path.basename(os.path.dirname(path))
        hashes.append((board + "/" + os.path.basename(path), hashlib.sha256(raw).hexdigest()))
        if _W["pattern"] is None:
            continue
        text = html.unescape(re.sub(r"<[^>]+>", "", raw.decode("utf-8", errors="replace")))
        hit = {m.group(1) for m in _W["pattern"].finditer(text)}
        for n in list(hit):
            hit.update(_W["inside"][n])
        for n in hit:
            articles[n] += 1
            boards[n].add(board)
    return articles, boards, hashes


def count(names, ptt_dir, jobs=1, with_tree=False):
    """{name: (articles, boards)} for every name; with `with_tree`, also the tree hash of the pages."""
    paths = sorted(glob.glob(os.path.join(ptt_dir, "*", "*.html")))
    if not paths:
        raise SystemExit(f"no {os.path.join(ptt_dir, '*', '*.html')} pages: wrong PTT_DIR?")
    chunks = [paths[i:i + 500] for i in range(0, len(paths), 500)]
    if jobs > 1:
        with mp.Pool(jobs, _init, (names,)) as pool:
            parts = pool.map(_pages, chunks)
    else:
        _init(names)
        parts = [_pages(c) for c in chunks]
    articles, boards, hashes = collections.Counter(), collections.defaultdict(set), []
    for a, b, h in parts:
        articles.update(a)
        for n, s in b.items():
            boards[n] |= s
        hashes.extend(h)
    got = {n: (articles[n], len(boards[n])) for n in names}
    if not with_tree:
        return got
    tree = hashlib.sha256("".join(f"{p}\0{h}\n" for p, h in sorted(hashes)).encode()).hexdigest()
    return got, tree


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tool_commit(head, porcelain):
    """HEAD，工作樹有未提交改動（git status --porcelain 非空）時加 -dirty。"""
    return head + ("-dirty" if porcelain.strip() else "")


def commit():
    git = lambda *a: subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True, check=True).stdout
    try:
        return tool_commit(git("rev-parse", "HEAD").strip(), git("status", "--porcelain"))
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--include-known", action="store_true")
    ap.add_argument("--jobs", type=int, default=1)
    for name in ("candidates", "root", "ptt", "out"):
        ap.add_argument(name)
    a = ap.parse_args(argv)
    every = read_names(a.candidates)
    have = set() if a.include_known else lexicon_words(a.root)
    names = [n for n in every if n not in have]
    got, tree = count(names, a.ptt, a.jobs, with_tree=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# ptt_tree_sha256 {tree}\n# input_sha256 {sha256_file(a.candidates)}\n# tool_commit {commit()}\n")
        f.writelines(f"{n}\t{x}\t{b}\n" for n, (x, b) in sorted(got.items(), key=lambda kv: (-kv[1][0], kv[0])))
    print(f"{len(names)} names counted, {len(every) - len(names)} already in the lexicon")


if __name__ == "__main__":
    main()
