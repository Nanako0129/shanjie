"""P1a step 0 and data preparation (188). Subcommands:

  hashes        SHA-256 of every input against the values of experiments/model-v5/README.md and the contract; exit 1 on any mismatch
  construction  the non-overlap check on the raw colloquial sources (section 3 item 1); exit 1 if it fails
  runs          the HAN runs build_counts / build_counts_text count -> runs-wiki.txt.gz, runs-colloq.txt.gz
  classes       per-reading character counts over those runs -> classes.json (the class table, section 2.1)

See README.md for the commands. Needs the corpus; nothing here runs in the unit tests except the pure functions of p1a.py.
"""
import argparse
import bz2
import collections
import gzip
import hashlib
import json
import multiprocessing as mp
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p1a  # noqa: E402
from p1a import bc, ROOT  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
import build_tune  # noqa: E402

WIKI_ARTICLES = 200_000
WIKITUNE_FIRST_ARTICLE = 300_000   # build_tune.main: `if i < 300_000: continue` (the counts use the first 200,000 only)
# prefixes of the hashes in experiments/model-v5/README.md (inputs) and docs/contracts/p1a-confusion.md section 2.2 (the rest)
EXPECTED = {"dump": "5db9052e", "train": "3e833d06", "synth": "bec7a6a2", "lexicon": "0deae7b7", "overlay": "348979c8",
            "twvariants": "245b94eb", "stcharacters": "a0ca1601", "model": "f81a021e", "classes": "75a5efb3",
            "cvtune": "31de456d", "wikitune": "8dcfe40c"}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 24), b""):
            h.update(blk)
    return h.hexdigest()


def cmd_hashes(a):
    lexdir = os.path.join(ROOT, "data", "lexicon")
    paths = {"dump": bc.DUMP, "train": a.train, "synth": a.synth, "lexicon": os.path.join(lexdir, "mcbpmf-data.txt"),
             "overlay": os.path.join(lexdir, "overlay-add.tsv"), "twvariants": os.path.join(bc.SRC, "opencc", "TWVariants.txt"),
             "stcharacters": os.path.join(bc.SRC, "opencc", "STCharacters.txt"), "model": a.model,
             "classes": os.path.join(os.path.dirname(os.path.abspath(a.model)), "classes.sjc"),
             "cvtune": a.cvtune, "wikitune": a.wikitune}
    bad = 0
    for k, p in paths.items():
        h = sha256_file(p)
        ok = h.startswith(EXPECTED[k])
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'} {k:13s} {h}  {p}")
    sys.exit(1 if bad else 0)


def raw_sources():
    d = os.path.join(bc.SRC, "colloquial")
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.startswith("cv-")] + [os.path.join(d, "cmn_sentences.tsv.bz2")]


def cmd_construction(a):
    """colloquial-train.txt = the raw lines with is_tune(raw) false (build_tune.main): count them from the raw sources and compare with the file.
    is_tune is applied to the raw line (last tab field), never to converted text. Wiki: the counts read the first 200,000 articles,
    wikitune starts at article 300,000 (build_tune.py)."""
    false_n = true_n = total = 0
    for p in raw_sources():
        print(f"raw {sha256_file(p)}  {p}")
        f = bz2.open(p, "rt", encoding="utf-8") if p.endswith(".bz2") else open(p, encoding="utf-8")
        for line in f:
            total += 1
            if build_tune.is_tune(line.rstrip("\n").split("\t")[-1]):
                true_n += 1
            else:
                false_n += 1
    train_n = open(a.train, encoding="utf-8", newline="\n").read().count("\n")
    ok = false_n == train_n and false_n + true_n == total > 0 and WIKI_ARTICLES <= WIKITUNE_FIRST_ARTICLE
    print(f"raw lines {total}: is_tune true {true_n}, false {false_n}; colloquial-train.txt {train_n} lines")
    print(f"wiki: counts use articles 0..{WIKI_ARTICLES - 1}, wikitune uses articles >= {WIKITUNE_FIRST_ARTICLE}")
    print("construction", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


_G = {}


def _init_runs():
    bc._init(False, False, False, ())
    _G["lex"], _G["conv"] = bc._W["lex"], bc._W["conv"]


def _wiki_batch(texts):
    return list(p1a.wiki_runs(_G["lex"], texts, _G["conv"]))


def cmd_runs(a):
    os.makedirs(a.out, exist_ok=True)
    bc._init(False, False, False, ())
    lex, conv = bc._W["lex"], bc._W["conv"]
    n = 0
    with gzip.open(os.path.join(a.out, "runs-colloq.txt.gz"), "wt", encoding="utf-8", newline="\n") as f:
        for run in p1a.colloquial_runs(lex, [a.train, a.synth], conv):
            f.write(run + "\n"); n += 1
    print(f"colloquial runs {n}", flush=True)
    n = 0
    with mp.Pool(a.procs, initializer=_init_runs) as pool, gzip.open(os.path.join(a.out, "runs-wiki.txt.gz"), "wt", encoding="utf-8", newline="\n") as f:
        for runs in pool.imap(_wiki_batch, bc.batches(bc.articles(a.articles))):   # imap keeps the stream order
            for run in runs:
                f.write(run + "\n"); n += 1
    print(f"wiki runs {n} from {a.articles} articles")


def iter_runs(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="\n") as f:
        for line in f:
            yield line.rstrip("\n")


def chunks(it, n):
    b = []
    for x in it:
        b.append(x)
        if len(b) == n:
            yield b; b = []
    if b:
        yield b


def _init_count():
    _G["lex"] = p1a.training_lexicon()


def _count(runs):
    c, lex = collections.Counter(), _G["lex"]
    for run in runs:
        for _, ch, r in p1a.target_positions(lex, bc.segment(lex, run)):
            if r in p1a.READINGS:
                c[(r, ch)] += 1
    return c


def cmd_classes(a):
    tot = collections.Counter()
    with mp.Pool(a.procs, initializer=_init_count) as pool:
        for src in ("runs-wiki.txt.gz", "runs-colloq.txt.gz"):
            for c in pool.imap_unordered(_count, chunks(iter_runs(os.path.join(a.runs, src)), 2000)):
                tot.update(c)
    out = {}
    for r in p1a.READINGS:
        counts = {ch: n for (rr, ch), n in tot.items() if rr == r}
        classes = p1a.class_table(counts)
        out[r] = {"classes": classes, "counts": dict(sorted(counts.items(), key=lambda x: -x[1])), "skip": len(classes) < 2}
        top = ", ".join(f"{c}:{counts[c]}" for c in classes)
        print(f"{r}: positions {sum(counts.values())}, classes [{top}], other {sum(counts.values()) - sum(counts[c] for c in classes)}"
              f"{'  SKIPPED (< 2 classes)' if out[r]['skip'] else ''}")
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("hashes")
    for k in ("train", "synth", "model", "cvtune", "wikitune"):
        h.add_argument("--" + k, required=True)
    c = sub.add_parser("construction")
    c.add_argument("--train", required=True)
    r = sub.add_parser("runs")
    r.add_argument("--train", required=True); r.add_argument("--synth", required=True); r.add_argument("--out", required=True)
    r.add_argument("--articles", type=int, default=WIKI_ARTICLES); r.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    k = sub.add_parser("classes")
    k.add_argument("--runs", required=True); k.add_argument("--out", required=True)
    k.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    a = ap.parse_args()
    {"hashes": cmd_hashes, "construction": cmd_construction, "runs": cmd_runs, "classes": cmd_classes}[a.cmd](a)


if __name__ == "__main__":
    main()
