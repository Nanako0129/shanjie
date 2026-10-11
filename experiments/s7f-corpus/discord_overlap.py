"""S7f: how many evaluation rows appear whole inside a corpus (contract 2.1, discordtune; Mac only). Prints counts only, never text.

  python discord_overlap.py --rows DISCORD_ROWS --corpus A0_runs-wiki.txt A0_runs-coll.txt chat-real.txt generated.txt [--rows-out FILE]

Rows are `前文|句子|讀音` (lm_eval.rows_of). A row overlaps a corpus file when its sentence occurs whole inside some line of it.
Output: `rows<TAB>n`, one `<corpus basename><TAB>count` per file, and `any<TAB>count`. --rows-out writes the 1-based numbers of the
overlapping rows (digits and newlines only), so the judgement can use the rows that overlap nothing.
"""
import argparse
import os

import clean


def overlap(sentences, corpus_paths):
    """Returns ({path: set of row indices}, set of row indices overlapping any)."""
    by_sent = {}
    for i, s in enumerate(sentences):
        if s:
            by_sent.setdefault(s, []).append(i)
    m = clean.Matcher(by_sent)
    per = {}
    for p in corpus_paths:
        hit = set()
        with open(p, encoding="utf-8") as f:
            for line in f:
                for s in m.find(line):
                    hit.update(by_sent[s])
        per[p] = hit
    return per, set().union(*per.values()) if per else set()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True); ap.add_argument("--corpus", nargs="+", required=True)
    ap.add_argument("--rows-out")
    a = ap.parse_args(argv)
    sentences = [t for t, _, _ in clean.rows_of([a.rows])]
    per, any_ = overlap(sentences, a.corpus)
    print(f"rows\t{len(sentences)}")
    for p in a.corpus:
        print(f"{os.path.basename(p)}\t{len(per[p])}")
    print(f"any\t{len(any_)}")
    if a.rows_out:
        with open(a.rows_out, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(f"{i + 1}\n" for i in sorted(any_))


if __name__ == "__main__":
    main()
