"""e4ctx: a public derived evaluation set for E4 whose left contexts cross a comma or an alphanumeric run
(docs/contracts/derisk-1015.md, section 3 E4, "revision 1").

Rows `context|sentence|reading`, cut from the same texts as experiments/s2/build_tune.py's cvtune and wikitune:
  - cv: the Common Voice zh-TW / Tatoeba cmn lines with is_tune(raw) true (hash of the UNCONVERTED line);
  - wiki: articles 300,000-304,999 of bc.articles(305_000).
Text processing is build_tune's up to (not including) its HAN step: cv is convert(raw); wiki is TEMPLATE x3, MARKUP, bc.SENT split,
convert. Punctuation, digits and English are kept. One cv line, or one SENT match of an article, is one "original sentence".

A row is cut at each maximal Han run (2-25 characters) that is directly preceded by
  - a MID character (punct_ctx.MID)         -> subset e4ctx-punct, or
  - a WORDLIKE character (subset.WORDLIKE: a digit or letter, ASCII or full-width)  -> subset e4ctx-alnum.
The context is everything before the run in that original sentence (so it ends with that character); the sentence is the run.
Per subset and source: at most one row per original sentence (a random one of its candidates), at most --cap rows (random sample),
seeded by (seed, source, subset). Readings: tools/readings.py (needs SHANJIE_MOEDICT / SHANJIE_HETERO on a machine where the
data is not in the default place); CHECK rows and rows that cannot be spelled are dropped, as build_tune does.

  python3 build_e4ctx.py --out-dir DIR [--src SOURCES_DIR] [--part all|cv|wiki] [--seed 20261015] [--cap 1500]

SOURCES_DIR (default ~/.cache/shanjie/sources, as experiments/s2/build_counts.py) holds colloquial/cv-* and
colloquial/cmn_sentences.tsv.bz2, opencc/ (the conversion tables) and zhwiki-20261001-pages-articles.xml.bz2. Reading the 305,000
articles takes as long as build_tune's wikitune part (about 20 minutes): run it on the Windows machine or Colab, not on the Mac.
Outputs (DIR, outside the repo): e4ctx-punct.txt, e4ctx-alnum.txt, e4ctx.manifest.json (generator commit, seed, counts, input file
sizes, output SHA-256).
"""
import argparse
import bz2
import hashlib
import json
import os
import random
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "experiments", "s2"))
import build_tune as bt  # noqa: E402  (module level only defines; also brings build_counts as bt.bc)
import punct_ctx  # noqa: E402
import subset  # noqa: E402

bc = bt.bc
SEED = 20261015
CAP = 1500
SENT_LEN = (2, 25)
HANRUN = re.compile(r"[一-鿿]+")     # the Han range of build_tune's HAN / bc.HAN
SUBSETS = ("e4ctx-punct", "e4ctx-alnum")


def wiki_sentences(raw, conv):
    """The converted original sentences of one article, exactly build_tune's steps before its HAN split."""
    t = raw
    for _ in range(3):
        t = bc.TEMPLATE.sub("", t)
    for pat, rep in bc.MARKUP:
        t = pat.sub(rep, t)
    return [conv(s) for s in bc.SENT.findall(t)]


def cuts(s):
    """[(subset, context, sentence)] for one original sentence `s` (see the module docstring)."""
    out = []
    for m in HANRUN.finditer(s):
        i, j = m.span()
        if i == 0 or not SENT_LEN[0] <= j - i <= SENT_LEN[1]:
            continue
        prev = s[i - 1]
        kind = "e4ctx-punct" if prev in punct_ctx.MID else "e4ctx-alnum" if subset.WORDLIKE.match(prev) else None
        ctx = s[:i]
        if kind and not any(c == "|" or c < " " for c in ctx):   # would break the `ctx|sentence` row format
            out.append((kind, ctx, m.group()))
    return out


def pick(candidates, source, kind, seed, cap):
    """candidates: [(sentence id, context, sentence)] in input order. One random candidate per original sentence, duplicates
    removed, then at most `cap` rows sampled; the result keeps the input order."""
    rnd = random.Random(f"{seed}|{source}|{kind}")
    by_sid = {}
    for sid, ctx, sent in candidates:
        by_sid.setdefault(sid, []).append((ctx, sent))
    rows = list(dict.fromkeys(rnd.choice(group) for group in by_sid.values()))
    if len(rows) > cap:
        keep = set(rnd.sample(range(len(rows)), cap))
        rows = [r for i, r in enumerate(rows) if i in keep]
    return rows


def add_readings(rows, workdir, name):
    """tools/readings.py over `ctx|sentence` rows; returns the `ctx|sentence|reading` lines without CHECK rows (build_tune's rule)."""
    src, dst = os.path.join(workdir, f"{name}.in"), os.path.join(workdir, f"{name}.all")
    open(src, "w", encoding="utf-8", newline="\n").write("".join(f"{c}|{s}\n" for c, s in rows))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "readings.py"), src, dst], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"readings.py failed on {name}: {r.stderr[-300:]}")
    check = {l.split("\t")[1] for l in r.stderr.splitlines() if l.startswith("CHECK\t")}
    return [l for l in open(dst, encoding="utf-8") if l.split("|")[1] not in check]


def cv_lines(cv_dir):
    files = [os.path.join(cv_dir, f) for f in sorted(os.listdir(cv_dir)) if f.startswith("cv-")] + [os.path.join(cv_dir, "cmn_sentences.tsv.bz2")]
    for p in files:
        f = bz2.open(p, "rt", encoding="utf-8") if p.endswith(".bz2") else open(p, encoding="utf-8")
        for line in f:
            yield p, line.rstrip("\n").split("\t")[-1]


def collect(conv, a):
    """{source: {subset: [(sid, ctx, sent)]}} and the input files read."""
    got = {"cv": {k: [] for k in SUBSETS}, "wiki": {k: [] for k in SUBSETS}}
    files = set()
    if a.part in ("all", "cv"):
        for n, (p, raw) in enumerate(cv_lines(os.path.join(a.src, "colloquial"))):
            files.add(p)
            if bt.is_tune(raw):   # on the unconverted line, as build_tune
                for kind, ctx, sent in cuts(conv(raw)):
                    got["cv"][kind].append((n, ctx, sent))
    if a.part in ("all", "wiki"):
        files.add(bc.DUMP)
        sid = 0
        for i, raw in enumerate(bc.articles(305_000)):
            if i < 300_000:
                continue
            for s in wiki_sentences(raw, conv):
                for kind, ctx, sent in cuts(s):
                    got["wiki"][kind].append((sid, ctx, sent))
                sid += 1
    return got, sorted(files)


def git_head():
    try:
        return subprocess.check_output(["git", "-C", ROOT, "rev-parse", "HEAD"], text=True).strip(), bool(
            subprocess.check_output(["git", "-C", ROOT, "status", "--porcelain", "--", "experiments/derisk-1015"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        return "unknown", None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--src", default=bc.SRC)
    ap.add_argument("--part", choices=["all", "cv", "wiki"], default="all")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--cap", type=int, default=CAP)
    a = ap.parse_args(argv)
    bc.SRC = bt.SRC = a.src
    bc.DUMP = os.path.join(a.src, os.path.basename(bc.DUMP))
    os.makedirs(a.out_dir, exist_ok=True)
    phrase, char, maxp = bc.load_conv()
    got, files = collect(lambda t: bc.convert(t, phrase, char, maxp), a)
    commit, dirty = git_head()
    man = {"generator_commit": commit, "generator_dirty": dirty, "seed": a.seed, "cap": a.cap, "part": a.part,
           "input_files": {os.path.relpath(p, a.src): os.path.getsize(p) for p in files}, "counts": {}, "outputs": {}}
    for kind in SUBSETS:
        rows = []
        for source in ("cv", "wiki"):
            cand = got[source][kind]
            chosen = pick(cand, source, kind, a.seed, a.cap)
            man["counts"][f"{kind}.{source}"] = {"candidates": len(cand), "sampled": len(chosen)}
            rows += chosen
        lines = add_readings(rows, a.out_dir, kind)
        text = "".join(lines)
        with open(os.path.join(a.out_dir, f"{kind}.txt"), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        man["counts"][kind] = {"sampled": len(rows), "rows": len(lines), "dropped_check_or_unreadable": len(rows) - len(lines)}
        man["outputs"][f"{kind}.txt"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with open(os.path.join(a.out_dir, "e4ctx.manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(json.dumps(man, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
