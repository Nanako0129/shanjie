"""E2 revision 7: score file(s) with every record's `ll` replaced by its `ll16` (the 16-char-context score), all other fields kept.

  python use_ll16.py IN OUT          # IN, OUT: score files (score.py / score_r.py JSONL), or directories (every *.jsonl, same file names)

The 16-char arms (C16, N16) are judged with the unchanged judge.py on the converted files: write them to their own directory
(`--scores DIR16`), the file names `MODEL.setting.set.jsonl` stay. Records without `ll`/`ll16` (n < 2) pass through unchanged.
Prints to stderr: records whose `ll` changed, and records with key16 > key.
"""
import glob
import json
import os
import sys


def convert(src, dst):
    changed = longer = 0
    with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8") as g:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if "ll" in r and "ll16" in r:
                changed += r["ll"] != r["ll16"]
                r["ll"] = r["ll16"]
            longer += r.get("key16", 0) > r.get("key", 0)
            g.write(json.dumps(r) + "\n")
    print(f"{src}: {changed} records changed, {longer} with key16 > key", file=sys.stderr)


if __name__ == "__main__":
    src, dst = sys.argv[1:]
    if os.path.isdir(src):
        os.makedirs(dst, exist_ok=True)
        for p in sorted(glob.glob(os.path.join(src, "*.jsonl"))):
            convert(p, os.path.join(dst, os.path.basename(p)))
    else:
        convert(src, dst)
