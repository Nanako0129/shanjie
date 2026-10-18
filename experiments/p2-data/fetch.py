#!/usr/bin/env python3
"""P2 candidate pool, step 1: download the four public sources into ~/.cache/shanjie/p2-public/raw/ (stdlib only).

  fetch.py wildchat [--budget SEC]   Taiwan rows of allenai/WildChat-1M via the datasets-server filter API; user turns only
  fetch.py yodas                     the 33 TEXT files of espnet/yodas data/zh000/text (never audio)
  fetch.py tatoeba                   cmn_sentences_detailed + cmn_transcriptions (per-language exports)
  fetch.py massive                   zh-TW of MASSIVE 1.1 (the S3 tarball; the datasets-server rows API does not serve it, see README)

Sequential requests, a pause between requests, retries with exponential backoff, and resume: wildchat writes one file per page and skips
the pages that exist (--budget stops cleanly after SEC seconds so a run fits a tool timeout; run it again to continue). Every URL that
was fetched is appended to raw/fetched.jsonl with the file's SHA-256 (clean.py copies these into manifest.json).
"""
import argparse
import bz2
import hashlib
import io
import json
import os
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

RAW = os.path.expanduser("~/.cache/shanjie/p2-public/raw")
UA = {"User-Agent": "shanjie-p2-data/1 (research; polite sequential fetch)"}
PAUSE = 1.0          # seconds between requests
TRIES = 30           # per request
DS = "https://datasets-server.huggingface.co"
WC_WHERE = '"country"=\'Taiwan\''   # country is a top-level column (conversation[].country exists too, but a nested column is not filterable)
WC_PAGE = 100        # the API's maximum length
YODAS_TREE = "https://huggingface.co/api/datasets/espnet/yodas/tree/main/data/zh000/text"
YODAS_RAW = "https://huggingface.co/datasets/espnet/yodas/resolve/main/data/zh000/text/"
TATOEBA = "https://downloads.tatoeba.org/exports/per_language/cmn/"
MASSIVE_TAR = "https://amazon-massive-nlu-dataset.s3.amazonaws.com/amazon-massive-dataset-1.1.tar.gz"


def get(url, timeout=120):
    """GET with retries and exponential backoff (2 s .. 20 s; the cap is low because the datasets-server answers "index is loading" in ~20 s anyway). Returns bytes. 4xx other than 429 is not retried."""
    delay = 2
    for k in range(TRIES):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read()[:200]
            if 400 <= e.code < 500 and e.code != 429:
                raise RuntimeError(f"HTTP {e.code} for {url}: {body!r}")
            why = f"HTTP {e.code} {body!r}"      # 5xx incl. "the dataset index is loading" and 429
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            why = repr(e)
        print(f"  retry {k + 1}/{TRIES} in {delay}s: {why[:120]}", file=sys.stderr, flush=True)
        time.sleep(delay)
        delay = min(delay * 2, 20)
    raise RuntimeError(f"giving up on {url}")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def log_fetch(source, url, path):
    os.makedirs(RAW, exist_ok=True)
    with open(os.path.join(RAW, "fetched.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"source": source, "url": url, "file": os.path.relpath(path, RAW), "sha256": sha256_file(path),
                            "bytes": os.path.getsize(path), "time": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, ensure_ascii=False) + "\n")


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# ------------------------------------------------------------------ WildChat
def wildchat(budget):
    d = os.path.join(RAW, "wildchat")
    os.makedirs(d, exist_ok=True)
    meta_path = os.path.join(d, "total.json")
    t0, total = time.time(), None
    if os.path.exists(meta_path):
        total = json.load(open(meta_path))["num_rows_total"]
    off = 0
    while total is None or off < total:
        page = os.path.join(d, f"page-{off:07d}.jsonl")
        if os.path.exists(page):
            off += WC_PAGE
            continue
        if budget and time.time() - t0 > budget:
            print(f"budget reached at offset {off}/{total}; run again to continue")
            return 1
        q = urllib.parse.urlencode({"dataset": "allenai/WildChat-1M", "config": "default", "split": "train", "where": WC_WHERE,
                                    "offset": off, "length": WC_PAGE})
        url = f"{DS}/filter?{q}"
        resp = json.loads(get(url, timeout=180))
        if total is None:
            total = resp["num_rows_total"]
            json.dump({"num_rows_total": total, "where": WC_WHERE, "url_template": f"{DS}/filter?dataset=allenai/WildChat-1M&config=default&split=train&where=<where>&offset=<o>&length={WC_PAGE}"}, open(meta_path, "w"))
            print(f"Taiwan rows: {total}", flush=True)
        lines = []
        for r in resp["rows"]:
            row = r["row"]
            turns = [{"i": i, "text": t["content"], "toxic": bool(t.get("toxic")), "redacted": bool(t.get("redacted")), "lang": t.get("language"),
                      "country": t.get("country")}
                     for i, t in enumerate(row["conversation"]) if t.get("role") == "user"]
            lines.append(json.dumps({"hash": row["conversation_hash"], "country": row.get("country"), "language": row.get("language"),
                                     "turns": turns}, ensure_ascii=False))
        tmp = page + ".tmp"
        save(tmp, ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8"))
        os.replace(tmp, page)
        log_fetch("wildchat", url, page)
        print(f"offset {off}: {len(lines)} rows", flush=True)
        if not resp["rows"]:
            break
        off += WC_PAGE
        time.sleep(PAUSE)
    print("wildchat done")
    return 0


# ------------------------------------------------------------------ YODAS
def yodas():
    tree = json.loads(get(YODAS_TREE))
    names = sorted(os.path.basename(x["path"]) for x in tree if x["path"].endswith(".txt"))
    json.dump(names, open(os.path.join(RAW, "yodas-listing.json"), "w"))
    for n in names:
        p = os.path.join(RAW, "yodas", n)
        if os.path.exists(p):
            continue
        save(p, get(YODAS_RAW + n))
        log_fetch("yodas", YODAS_RAW + n, p)
        print(n, os.path.getsize(p), flush=True)
        time.sleep(PAUSE)
    print(f"yodas done: {len(names)} text files")


# ------------------------------------------------------------------ Tatoeba
def tatoeba():
    for n in ("cmn_sentences_detailed", "cmn_transcriptions"):
        p = os.path.join(RAW, "tatoeba", n + ".tsv")
        if os.path.exists(p):
            continue
        save(p + ".bz2", get(TATOEBA + n + ".tsv.bz2"))
        log_fetch("tatoeba", TATOEBA + n + ".tsv.bz2", p + ".bz2")
        save(p, bz2.decompress(open(p + ".bz2", "rb").read()))
        print(n, os.path.getsize(p), flush=True)
        time.sleep(PAUSE)


# ------------------------------------------------------------------ MASSIVE
def massive():
    d = os.path.join(RAW, "massive")
    p = os.path.join(d, "zh-TW.jsonl")
    if os.path.exists(p):
        return
    tgz = os.path.join(d, "amazon-massive-dataset-1.1.tar.gz")
    save(tgz, get(MASSIVE_TAR, timeout=600))
    log_fetch("massive", MASSIVE_TAR, tgz)
    with tarfile.open(tgz) as t:
        m = [x for x in t.getmembers() if x.name.endswith("zh-TW.jsonl")]
        assert len(m) == 1, [x.name for x in m]
        save(p, t.extractfile(m[0]).read())
    os.remove(tgz)   # the tarball holds all 52 locales; only zh-TW is kept (its hash stays in fetched.jsonl)
    print("massive zh-TW", os.path.getsize(p))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", choices=["wildchat", "yodas", "tatoeba", "massive"])
    ap.add_argument("--budget", type=int, default=0, help="wildchat: stop cleanly after this many seconds")
    a = ap.parse_args()
    if a.source == "wildchat":
        return wildchat(a.budget)
    {"yodas": yodas, "tatoeba": tatoeba, "massive": massive}[a.source]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
