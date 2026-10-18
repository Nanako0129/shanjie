"""L0 training rows (`prefix|clause|reading`) from text outside the n-gram counts and M's training text (README, registration).

  python3 experiments/l0/rows.py wiki --lo 200000 --hi 300000 --n 120000 --out DIR/wiki.txt [--dump PATH] [--procs 32]
  python3 experiments/l0/rows.py pool --pool pool.tsv --out DIR/pool.txt [--procs 32]
  python3 experiments/l0/rows.py dedup --rows DIR/wiki.txt DIR/pool.txt --against A.txt B.txt ... --out-dir DIR/clean

Clauses are build_tune's: runs of 4-30 Han characters (CLAUSE) in each converted sentence (build_counts.article_sentences for wiki;
pool sentences as they are). The prefix is the text before the clause: earlier sentences of the same article (wiki), or the
previous pool line of the same document plus the start of the same sentence (pool), cut to PREFIX_KEEP characters, `|` and
line breaks replaced by a space. Readings: tools/readings.py, rows with CHECK or no reading dropped (build_tune.readings' rule).
A JSON manifest (counts, SHA-256) is written next to every output. dedup drops rows whose clause equals a clause of any --against
file and prints counts only (the --against files may be private).
"""
import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))

CLAUSE = re.compile(r"[一-鿿]{4,30}")   # build_tune.HAN
PREFIX_KEEP = 200                       # characters; L0 uses at most 62 Han characters of it
SEED = 20261018


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def clean_prefix(text):
    return re.sub(r"[|\r\n]", " ", text)[-PREFIX_KEEP:]


def clauses_with_prefix(sentences):
    """[(prefix, clause)] for one document given as a list of sentences; the prefix is everything before the clause."""
    out, before = [], ""
    for s in sentences:
        for m in CLAUSE.finditer(s):
            out.append((clean_prefix(before + s[:m.start()]), m.group(0)))
        before = (before + s)[-PREFIX_KEEP:]
    return out


def doc_key(source, source_id):
    """Pool lines that continue one another: a YODAS video, a WildChat user turn. Tatoeba and MASSIVE lines stand alone."""
    p = source_id.split(":")
    if source == "yodas":
        return ("yodas", p[0])
    if source == "wildchat":
        return ("wildchat", p[0], p[1] if len(p) > 1 else "")
    return None


def pool_pairs(lines):
    """pool.tsv lines -> [(prefix, clause)]; the previous line counts as context only within the same document."""
    out, prev_key, prev = [], None, ""
    for line in lines:
        source, source_id, sent = line.rstrip("\n").split("\t")
        key = doc_key(source, source_id)
        before = prev if key is not None and key == prev_key else ""
        for m in CLAUSE.finditer(sent):
            out.append((clean_prefix(before + sent[:m.start()]), m.group(0)))
        prev_key, prev = key, (before + sent)[-PREFIX_KEEP:]
    return out


def wiki_pairs(lo, hi, dump=None):
    import build_counts as bc
    conv = bc.load_conv()
    out = []
    for i, raw in enumerate(bc.articles(hi, dump)):
        if i >= lo:
            out.extend(clauses_with_prefix(list(bc.article_sentences(raw, conv))))
    return out


def sample_unique(pairs, n, seed=SEED):
    """One row per distinct clause (its first occurrence), then n of them at random (sorted first, as build_tune does)."""
    first = {}
    for p, c in pairs:
        first.setdefault(c, p)
    keys = sorted(first)
    pick = keys if n is None or n >= len(keys) else random.Random(seed).sample(keys, n)
    return [(first[c], c) for c in pick]


def read_with_readings(pairs, out, procs):
    """Shards -> tools/readings.py in parallel -> rows without CHECK/unreadable lines, in input order."""
    tmp = tempfile.mkdtemp(prefix="l0rows-")
    k = max(1, min(procs, len(pairs)))
    shards = [pairs[i::k] for i in range(k)]

    def run(i):
        src, dst = os.path.join(tmp, f"{i}.in"), os.path.join(tmp, f"{i}.out")
        with open(src, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(f"{p}|{c}\n" for p, c in shards[i]))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "readings.py"), src, dst], capture_output=True, text=True,
                           encoding="utf-8")
        if r.returncode != 0:
            raise SystemExit(f"readings.py failed on shard {i}: {r.stderr[-500:]}")
        check = {(l.split("\t")[1]) for l in r.stderr.splitlines() if l.startswith("CHECK\t")}
        rows = [l for l in open(dst, encoding="utf-8") if l.split("|")[1] not in check]
        return rows, len(shards[i]) - len(rows)

    with ThreadPoolExecutor(k) as ex:
        res = list(ex.map(run, range(k)))
    kept = [r for rows, _ in res for r in rows]
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(kept))
    return len(kept), sum(d for _, d in res)


def write_manifest(out, info):
    info.update(rows_file=os.path.basename(out), rows_sha256=sha256(out))
    json.dump(info, open(out + ".json", "w"), indent=1, ensure_ascii=False)
    print(json.dumps(info, ensure_ascii=False))


def dedup(rows_files, against, out_dir):
    seen = set()
    for a in against:
        for line in open(a, encoding="utf-8"):
            p = line.rstrip("\n").split("|")
            if len(p) >= 2:
                seen.add(p[1])
    os.makedirs(out_dir, exist_ok=True)
    for path in rows_files:
        kept, dropped = [], 0
        for line in open(path, encoding="utf-8"):
            if line.split("|")[1] in seen:
                dropped += 1
            else:
                kept.append(line)
        out = os.path.join(out_dir, os.path.basename(path))
        open(out, "w", encoding="utf-8", newline="\n").write("".join(kept))
        print(f"dedup {os.path.basename(path)}: kept {len(kept)} dropped {dropped} sha256 {sha256(out)}")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wiki")
    w.add_argument("--lo", type=int, default=200_000); w.add_argument("--hi", type=int, default=300_000)
    w.add_argument("--n", type=int, default=120_000); w.add_argument("--dump"); w.add_argument("--out", required=True)
    w.add_argument("--procs", type=int, default=os.cpu_count())
    p = sub.add_parser("pool")
    p.add_argument("--pool", required=True); p.add_argument("--out", required=True)
    p.add_argument("--procs", type=int, default=os.cpu_count())
    d = sub.add_parser("dedup")
    d.add_argument("--rows", nargs="+", required=True); d.add_argument("--against", nargs="+", required=True)
    d.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "dedup":
        return dedup(a.rows, a.against, a.out_dir)
    if a.cmd == "wiki":
        pairs = wiki_pairs(a.lo, a.hi, a.dump)
        info = {"source": "wiki", "articles": [a.lo, a.hi], "clauses": len(pairs)}
        n = a.n
    else:
        pairs = pool_pairs(open(a.pool, encoding="utf-8"))
        info = {"source": "pool", "pool_sha256": sha256(a.pool), "clauses": len(pairs)}
        n = None
    picked = sample_unique(pairs, n)
    kept, dropped = read_with_readings(picked, a.out, a.procs)
    info.update(distinct=len({c for _, c in pairs}), sampled=len(picked), kept=kept, dropped_check_or_unreadable=dropped, seed=SEED)
    write_manifest(a.out, info)


if __name__ == "__main__":
    main()
