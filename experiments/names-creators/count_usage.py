"""PTT usage of candidate creator names (docs/contracts/names-creators.md section 2: boards >= 3 and articles >= 10 on the 110-board crawl).
Usage: python3 experiments/names-creators/count_usage.py CANDIDATES LEXICON_ROOT PTT_DIR OUT
  CANDIDATES    one name per line (blank lines and # lines skipped)
  LEXICON_ROOT  a checkout root: data/lexicon/mcbpmf-data.txt plus the second column of data/lexicon/*-add.tsv and data/packs/*-add.tsv
                (overlay, slang, sandhi, names, both packs); a name already there is not counted
  PTT_DIR       PTT_DIR/<board>/<article>.html
  OUT           name<TAB>articles<TAB>boards, most articles first (articles = pages whose text holds the name, boards = distinct boards of those pages)
One process, one regex pass per page. A name that is a prefix of a longer candidate is still counted: the longer match credits the shorter names it contains.
Tests: test_count_usage.py."""
import collections
import glob
import html
import os
import re
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


def count(names, ptt_dir):
    """{name: (articles, boards)} for every name."""
    # (?=...) so overlapping names (a name inside another) are found at their own start; the alternation tries longer names first,
    # so a name that is a prefix of a longer one at the same start is credited through `inside`.
    pattern = re.compile("(?=(" + "|".join(re.escape(n) for n in sorted(names, key=lambda n: -len(n))) + "))") if names else None
    inside = {n: [m for m in names if m != n and m in n] for n in names}
    articles, boards = collections.Counter(), collections.defaultdict(set)
    for path in glob.glob(os.path.join(ptt_dir, "*", "*.html")):
        if pattern is None:
            break
        with open(path, encoding="utf-8", errors="replace") as f:
            text = html.unescape(re.sub(r"<[^>]+>", "", f.read()))
        hit = {m.group(1) for m in pattern.finditer(text)}
        for n in list(hit):
            hit.update(inside[n])
        board = os.path.basename(os.path.dirname(path))
        for n in hit:
            articles[n] += 1
            boards[n].add(board)
    return {n: (articles[n], len(boards[n])) for n in names}


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    cands, root, ptt, out = sys.argv[1:]
    have = lexicon_words(root)
    names = [n for n in read_names(cands) if n not in have]
    got = count(names, ptt)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(f"{n}\t{a}\t{b}\n" for n, (a, b) in sorted(got.items(), key=lambda kv: (-kv[1][0], kv[0])))
    print(f"{len(names)} names counted, {len(read_names(cands)) - len(names)} already in the lexicon")


if __name__ == "__main__":
    main()
