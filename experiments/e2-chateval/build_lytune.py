"""E2 lytune (docs/contracts/e2-chat-eval.md section 2.2): colloquial clauses from the legislature transcripts, a guard set only.

  python build_lytune.py [--src ~/.cache/shanjie/e2-public/ly] [--out ~/.cache/shanjie/e2-public/lytune.txt]

Checks every CSV of --src/manifest.json against its SHA-256, strips speaker labels and timestamps, cuts each transcript (= one
speech) into 2-30 character Han segments with the previous segment of the same speech as context, keeps the segments holding one of
the colloquial words, drops clauses that are only a colloquial word and exact duplicate sentences (first kept), draws 5,000 with seed 20261012, and spells them with tools/readings.py (CHECK and unreadable rows dropped).
Writes OUT (`context|sentence|reading`) and OUT with .manifest.json (row counts per step, SHA-256). The row file is not for the repo.
"""
import argparse
import csv
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2common as c  # noqa: E402

COLLOQUIAL = ("嘛", "啦", "喔", "欸", "吧", "好不好", "對不對", "是不是")
ROWS = 5000
CACHE = os.path.expanduser("~/.cache/shanjie/e2-public")
# A speaker label is a short run without punctuation ending in a full-width colon, at the start of a line or after a sentence end
# (`陳委員玉珍：`, `主席：`, `王部長國材：`). ponytail: a sentence-initial `我跟你講：` is stripped too; the loss is one clause start.
LABEL = re.compile(r"(?:^|(?<=[。！？」）…]))[^。！？，、；：「」（）\s0-9]{1,12}：", re.M)
STAMP = re.compile(r"（\d+時(?:\d+分)?）")


def clean(transcript):
    return LABEL.sub("", STAMP.sub("", transcript))


def clauses(transcript):
    """[(context, segment)] of one speech: the context is the previous segment of the same speech, '' for the first."""
    segs = c.segments(clean(transcript))
    return [("" if i == 0 else segs[i - 1], s) for i, s in enumerate(segs)]


def check_sources(src):
    m = json.load(open(os.path.join(src, "manifest.json"), encoding="utf-8"))
    files = [f for f in m["files"] if f["path"].startswith("data/csv/")]
    for f in files:
        got = c.sha256_file(os.path.join(src, f["path"]))
        if got != f["sha256"]:
            sys.exit(f"build_lytune: {f['path']} sha256 {got[:16]}…, manifest says {f['sha256'][:16]}…")
    return sorted(files, key=lambda f: f["path"])


def run(src, out, readings=c.readings):
    files = check_sources(src)
    counts = {"csv_files": len(files)}
    csv.field_size_limit(10 ** 9)
    speeches, rows = 0, []
    for f in files:
        with open(os.path.join(src, f["path"]), encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                speeches += 1
                rows += clauses(row["transcript"])
    counts["speeches"] = speeches
    counts["clauses_2_30_han"] = len(rows)
    rows = [r for r in rows if any(w in r[1] for w in COLLOQUIAL)]
    counts["clauses_colloquial"] = len(rows)
    # main, 2026-10-12 (before any scoring): a clause that is only a filter word (對不對, 好不好, 是不是, 嘛, 吧 ...) is a bare tag
    # whose row differs only in context; drop them, then exact duplicate sentences (first in file order kept), then sample.
    rows = [r for r in rows if r[1] not in COLLOQUIAL]
    counts["after_dropping_bare_filter_words"] = len(rows)
    seen, uniq = set(), []
    for r in rows:
        if r[1] not in seen:
            seen.add(r[1])
            uniq.append(r)
    rows = uniq
    counts["after_dropping_duplicate_sentences"] = len(rows)
    pick = random.Random(c.SEED).sample(rows, min(ROWS, len(rows)))
    counts["sampled"] = len(pick)
    rd = readings([s for _, s in pick])
    counts["dropped_check"] = sum(rd[s][0] == "check" for _, s in pick)
    counts["dropped_unreadable"] = sum(rd[s][0] == "unreadable" for _, s in pick)
    kept = [(x, s, rd[s][1]) for x, s in pick if rd[s][0] == "ok"]
    counts["rows"] = len(kept)
    text = "".join(f"{x}|{s}|{r}\n" for x, s, r in kept)
    c.write_lf(out, text)
    counts["sha256"] = c.sha256_bytes(text.encode("utf-8"))
    counts["colloquial_words"] = list(COLLOQUIAL)
    counts["seed"] = c.SEED
    counts["source_revision"] = json.load(open(os.path.join(src, "manifest.json"), encoding="utf-8"))["revision"]
    c.write_lf(out + ".manifest.json", json.dumps(counts, ensure_ascii=False, indent=1) + "\n")
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(CACHE, "ly")); ap.add_argument("--out", default=os.path.join(CACHE, "lytune.txt"))
    a = ap.parse_args(argv)
    print(json.dumps(run(a.src, a.out), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
