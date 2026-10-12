"""S7f source 2: Cofacts (HF Cofacts/line-msg-fact-check-tw, CC BY-SA 4.0) -> cleaned sentences + manifest. Mac only (contract 2.1).

  python cofacts.py --articles articles.csv.zip --requests reply_requests.csv.zip --revision HF_REVISION --tune TUNEDIR --out DIR
      -> DIR/cofacts.txt, DIR/cofacts.manifest.json

Only two columns are read: `articles.text` (articleType TEXT, status NORMAL) and `reply_requests.reason` (status NORMAL). No user
column is read or written; no other Cofacts file is opened. URLs, emails and phone-like strings are scrubbed (clean.scrub).
"""
import argparse
import collections
import csv
import io
import os
import sys
import zipfile

import clean

csv.field_size_limit(sys.maxsize)


def rows(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            sys.exit(f"cofacts: expected one .csv inside {zip_path}, found {names}")
        with z.open(names[0]) as raw:
            yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))


def texts(articles, requests, counts):
    for r in rows(articles):
        if r["articleType"] == "TEXT" and r["status"] == "NORMAL":
            counts["article_used"] += 1
            yield r["text"]
        else:
            counts["article_skipped"] += 1
    for r in rows(requests):
        if r["status"] == "NORMAL" and r["reason"]:
            counts["reason_used"] += 1
            yield r["reason"]
        else:
            counts["reason_skipped"] += 1


def run(articles, requests, flt, counts):
    kept = []
    for t in texts(articles, requests, counts):
        kept += clean.clean_text(t, flt, counts)
    return kept


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--articles", required=True); ap.add_argument("--requests", required=True)
    ap.add_argument("--revision", required=True, help="the HF dataset revision the two files were downloaded at")
    ap.add_argument("--out", required=True)
    clean.add_eval_args(ap)
    a = ap.parse_args(argv)
    for p, want in ((a.articles, "articles.csv.zip"), (a.requests, "reply_requests.csv.zip")):
        if os.path.basename(p) != want:
            sys.exit(f"cofacts: {p}: only {want} is used here")
    ev, info = clean.eval_sentences(a.tune, a.eval)
    flt, counts = clean.Filter(clean.load_simp_only(), ev, convert=clean.converter()), collections.Counter()
    kept = run(a.articles, a.requests, flt, counts)
    os.makedirs(a.out, exist_ok=True)
    clean.write_lines(os.path.join(a.out, "cofacts.txt"), kept)
    inputs = {"dataset": "https://huggingface.co/datasets/Cofacts/line-msg-fact-check-tw", "revision": a.revision,
              "sha256": {os.path.basename(p): clean.sha256_file(p) for p in (a.articles, a.requests)}}
    m = clean.source_manifest("cofacts", inputs, kept, flt, counts, info)
    clean.write_manifest(os.path.join(a.out, "cofacts.manifest.json"), m)
    print(f"cofacts: {m['rows']} rows, {m['chars']} chars, removed {m['removed']}")


if __name__ == "__main__":
    main()
