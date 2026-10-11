"""S7f source 1: archive.tw transcripts (https://github.com/audreyt/transcript, CC0) -> cleaned sentences + manifest.

  python archive_tw.py --repo CLONE --tune TUNEDIR --out DIR      # writes DIR/archive_tw.txt and DIR/archive_tw.manifest.json

Files: root-level `YYYY-MM-DD-*.md` whose Han characters outnumber Latin letters (the Chinese ones). Every Chinese file named in
`.alternates` is dropped: a pair is a translation, and the file does not say which side is the original, so both directions go.
Lines: `#` headings (title, `### 唐鳳：` speaker labels) and `>` blockquotes (stage directions, section markers) are dropped; a leading
time stamp and a leading short label (`答：`, `sli.do：`) are removed; Markdown links keep their text; then clean.py's rules.
"""
import argparse
import collections
import glob
import os
import re
import subprocess

import clean

FILE = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.md$")
LATIN = re.compile(r"[A-Za-zÀ-ɏ]")
TIMESTAMP = re.compile(r"^[\[(（]?\d{1,2}:\d{2}(?::\d{2})?[\])）]?\s*")
LABEL = re.compile(r"^[^\s：，。「」]{1,8}：\s*")
MD = [(re.compile(r"!\[[^\]]*\]\([^)]*\)"), ""),                  # images
      (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),              # links keep their text
      (re.compile(r"<[^>]+>"), ""), (re.compile(r"\*{1,3}|`"), "")]


def is_chinese(text):
    return sum(map(len, clean.bc.HAN.findall(text))) > len(LATIN.findall(text))


def alternates(repo):
    out = set()
    with open(os.path.join(repo, ".alternates"), encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or "\t" not in line:
                continue
            out.update(x.strip() for x in line.split("\t"))
    return out


def body_lines(text, counts):
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith("#"):
            counts["line_heading"] += 1
            continue
        if s.startswith(">"):
            counts["line_blockquote"] += 1
            continue
        s = LABEL.sub("", TIMESTAMP.sub("", s))
        for pat, rep in MD:
            s = pat.sub(rep, s)
        yield s


def select_files(repo, counts):
    alt = alternates(repo)
    for p in sorted(glob.glob(os.path.join(repo, "*.md"))):
        name = os.path.basename(p)
        if not FILE.match(name):
            continue
        text = open(p, encoding="utf-8").read()
        if not is_chinese(text):
            counts["file_not_chinese"] += 1
            continue
        if name in alt:
            counts["file_alternates"] += 1
            continue
        counts["file_used"] += 1
        yield text


def run(repo, flt, counts):
    kept = []
    for text in select_files(repo, counts):
        kept += clean.clean_text("\n".join(body_lines(text, counts)), flt, counts)
    return kept


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True); ap.add_argument("--out", required=True)
    clean.add_eval_args(ap)
    a = ap.parse_args(argv)
    commit = subprocess.run(["git", "-C", a.repo, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    ev, info = clean.eval_sentences(a.tune, a.eval)
    flt, counts = clean.Filter(clean.load_simp_only(), ev), collections.Counter()
    kept = run(a.repo, flt, counts)
    os.makedirs(a.out, exist_ok=True)
    clean.write_lines(os.path.join(a.out, "archive_tw.txt"), kept)
    m = clean.source_manifest("archive.tw", {"repo": "https://github.com/audreyt/transcript", "commit": commit}, kept, flt, counts, info)
    clean.write_manifest(os.path.join(a.out, "archive_tw.manifest.json"), m)
    print(f"archive_tw: {m['rows']} rows, {m['chars']} chars, removed {m['removed']}")


if __name__ == "__main__":
    main()
