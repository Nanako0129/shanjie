#!/usr/bin/env python3
"""Builds colloquial-attribution.tsv (docs/contracts/colloquial-term.md sections 2.1 and 3.6): one row per source unit of the public
colloquial pool, so the licences of the pool's four sources (ODC-BY, CC BY 3.0, CC BY 2.0 FR, CC BY 4.0) can be honoured. No corpus text
goes into the output, only unit ids, names or URLs and sentence counts.

  python3 tools/build_attribution.py --pool pool.tsv --attribution attribution.tsv --out data/lm/colloquial-attribution.tsv \
      [--sha-out data/colloquial-attribution.tsv.sha256]

--pool is the pool's source<TAB>unit-id<TAB>sentence file (only the first two columns are read: the sentence is never copied);
--attribution is its source<TAB>unit-id<TAB>reference file: for yodas the video URL, for tatoeba the author. YODAS is attributed as the
dataset supplies it (dataset name, licence, video id, URL; the channel is not in the dataset); Tatoeba per sentence id with its author;
WildChat and MASSIVE are one dataset-level row each. The per-source sums must equal EXPECTED (the counts of the research log's
"Phase 2 public sentence pool" entry, which live in the repository, not computed here); a pool row without a unit id, a YODAS unit without
a URL or a Tatoeba unit without an author, or the same (source, unit id) twice in --attribution, is an error; lines end in LF or CRLF. Exit codes: 0 ok, 1 an input is incomplete or the sums differ, 2 bad usage.

A unit is the id before the first colon of the pool's unit id: the YODAS video id, the Tatoeba sentence id (the colon parts number the pieces
one sentence was cut into). Output columns: source, unit_id, author, url, licence, sentences ("-" where the source supplies nothing). Rows:
yodas by video id, tatoeba by sentence id (numeric order), then wildchat and massive; the file is deterministic.
"""
import argparse
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPECTED = {"wildchat": 3491, "yodas": 90080, "tatoeba": 36839, "massive": 13485}
# Dataset-level rows: (unit_id = the dataset's name, url, licence). WildChat and MASSIVE carry no per-unit author in the pool.
DATASETS = {
    "wildchat": ("allenai/WildChat-1M", "https://huggingface.co/datasets/allenai/WildChat-1M", "ODC-BY 1.0"),
    "massive": ("AmazonScience/massive", "https://huggingface.co/datasets/AmazonScience/massive", "CC BY 4.0"),
}
LICENCES = {"yodas": "CC BY 3.0 (espnet/yodas)", "tatoeba": "CC BY 2.0 FR (Tatoeba)"}
ORDER = ["yodas", "tatoeba", "wildchat", "massive"]
HEADER = "source\tunit_id\tauthor\turl\tlicence\tsentences"


class Incomplete(Exception):
    pass


def read_tsv(path, what, fields):
    """Rows of `path` split on tabs, each with exactly `fields` fields; the message names the line, never its text. A line ends in LF or CRLF."""
    with open(path, encoding="utf-8", newline="") as f:
        for n, line in enumerate(f, 1):
            row = line.rstrip("\r\n").split("\t")
            if len(row) != fields:
                raise Incomplete(f"{what} line {n}: {len(row)} fields, expected {fields}")
            yield n, row


def source_id(unit):
    """The attributed unit of a pool unit id: the pool writes `<id>[:<segment>[:<piece>]]`, with the YODAS video id or the Tatoeba sentence id
    first (the colon parts only number the pieces that one cleaned sentence was cut into). "" when the id itself is empty."""
    return unit.split(":", 1)[0]


def build(pool_path, attribution_path):
    """-> list of output rows (lists of strings). Raises Incomplete with a text-free message."""
    refs = {}
    for n, (src, unit, ref) in read_tsv(attribution_path, "attribution", 3):
        if not source_id(unit):
            raise Incomplete(f"attribution line {n}: no unit id")
        if (src, unit) in refs:
            raise Incomplete(f"attribution line {n}: the same source and unit id as an earlier line")
        refs[(src, unit)] = ref
    counts = {s: {} for s in ORDER}
    for n, row in read_tsv(pool_path, "pool", 3):
        src, unit = row[0], row[1]
        if src not in counts:
            raise Incomplete(f"pool line {n}: unknown source")
        if not source_id(unit):
            raise Incomplete(f"pool line {n}: no unit id")
        key = source_id(unit) if src in ("yodas", "tatoeba") else DATASETS[src][0]
        if src in ("yodas", "tatoeba"):
            ref = refs.get((src, unit), "")
            what = "URL" if src == "yodas" else "author"
            if not ref:
                raise Incomplete(f"pool line {n}: {src} unit has no {what}")
            prev = counts[src].get(key)
            if prev is not None and prev[0] != ref:
                raise Incomplete(f"pool line {n}: {src} unit {key!r} has two different {what}s")
            counts[src][key] = [ref, (prev[1] if prev else 0) + 1]
        else:
            prev = counts[src].get(key)
            counts[src][key] = ["", (prev[1] if prev else 0) + 1]
    for src in ORDER:
        total = sum(c for _, c in counts[src].values())
        if total != EXPECTED[src]:
            raise Incomplete(f"{src}: {total} sentences, expected {EXPECTED[src]}")
    rows = []
    for key, (ref, n) in sorted(counts["yodas"].items()):
        rows.append(["yodas", key, "-", ref, LICENCES["yodas"], str(n)])
    for key, (ref, n) in sorted(counts["tatoeba"].items(), key=lambda kv: (len(kv[0]), kv[0])):
        rows.append(["tatoeba", key, ref, "-", LICENCES["tatoeba"], str(n)])
    for src in ("wildchat", "massive"):
        for key, (_, n) in counts[src].items():
            rows.append([src, key, "-", DATASETS[src][1], DATASETS[src][2], str(n)])
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for k in ("pool", "attribution", "out"):
        ap.add_argument("--" + k, required=True)
    ap.add_argument("--sha-out", help="also write the `<sha256>  <path of --out>` line (data/*.sha256 format; the path relative to the repository root, "
                    "so `shasum -a 256 -c` works from there; --out as given when it is outside the repository)")
    a = ap.parse_args(argv)
    try:
        rows = build(a.pool, a.attribution)
    except Incomplete as e:
        print(f"STOP: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"STOP: cannot read an input ({e.__class__.__name__})", file=sys.stderr)
        return 1
    text = HEADER + "\n" + "".join("\t".join(r) + "\n" for r in rows)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if a.sha_out:
        with open(a.sha_out, "w", encoding="utf-8") as f:
            rel = os.path.relpath(os.path.abspath(a.out), ROOT)
            f.write(f"{hashlib.sha256(text.encode('utf-8')).hexdigest()}  {a.out if rel.startswith(os.pardir) else rel}\n")
    sums = {s: sum(int(r[5]) for r in rows if r[0] == s) for s in ORDER}
    print(f"{len(rows)} rows; sentences " + " ".join(f"{s} {sums[s]}" for s in ORDER))
    return 0


if __name__ == "__main__":
    sys.exit(main())
