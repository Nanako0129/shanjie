"""Shared bits of the youtube-events tools (docs/contracts/youtube-events.md part A): the only place that touches the network.
Tests replace http_get; nothing else here opens a socket."""
import csv
import time
import urllib.request

UA = "shanjie-youtube-events/1.0 (https://github.com/Nanako0129/shanjie; research tool for word candidates; contact via the repository)"
_last = 0.0


def http_get(url, interval=1.0):
    """GET url -> bytes, at least `interval` seconds after the previous call of this process."""
    global _last
    wait = interval - (time.time() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.time()
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60).read()


def read_tsv(path):
    """Tab-separated file with a header line -> list of dicts (comment lines starting with # are skipped)."""
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader((l for l in f if not l.startswith("#")), delimiter="\t", quoting=csv.QUOTE_NONE))   # write_tsv never quotes


def write_tsv(path, fields, rows):
    """rows are dicts; tabs and newlines inside values are flattened to spaces."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(fields) + "\n")
        for r in rows:
            f.write("\t".join(" ".join(str(r.get(k, "")).split()) for k in fields) + "\n")
