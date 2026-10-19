"""Sets B (HPLT 3.0 Traditional Chinese) and C (Wikisource prose) of the long-context evaluation sets (README.md).

  longtext.py hplt --shards F.jsonl.zst... --out ROWS [--overlap-drop FILE...] [--overlap-report FILE...] [--procs N] [--doc-fraction F | --target-docs N]
  longtext.py wikisource-fetch --titles FILE --out DIR
  longtext.py wikisource --fetched DIR --titles FILE --out ROWS [--overlap-drop FILE...] [--overlap-report FILE...] [--procs N]

--out ROWS also writes ROWS.manifest.json (every count, input and output sha256) and ROWS.proofread.txt (the 10% sample).
"""
import argparse
import hashlib
import html.parser
import glob
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import ctx64
from ctx64 import e2common as c, segments_with_ctx, check_rows, sha256_stream

sys.path.insert(0, os.path.join(ctx64.EXP, "s7a-tinylm"))      # buildcheck (substring_hits) and its imports

SEED = 20261019
MAX_ROWS = 3000
PER_DOC = 3
MIN_CTX_HAN = 16     # README "前文 ≥ 16 字": read as 16 Han characters of the context
KEY_HAN = 16
HAN_CH = re.compile(r"[一-鿿]")
# hplt: about this many documents survive the seeded document selection. 2026-10-20: the first real build (30,000 documents) gave 3,000
# rows of which about 40% were loan/credit SEO keyword spam, so the content filters below now remove documents and rows after the
# selection; 60,000 documents (at most 3 candidate rows each) keep the final 3,000-row sample reachable. Not measured: raise it if
# a build stops under 1,000 rows or ends under 3,000.
TARGET_DOCS = 60000
# 2026-10-20 finding: most filter-passing HPLT documents are pixnet.net blogs (192,003 of 203,405), and many are loan/credit SEO
# keyword spam. A document with at least SPAM_MIN non-overlapping matches of SPAM_RE in its text is dropped (counted as `spam`).
SPAM_RE = re.compile("貸款|借錢|借款|信貸|當舖|代書|小額|債務|二胎|免留車|地下錢莊|票貼|融資|週轉|周轉")
SPAM_MIN = 5
GIBBERISH_PERCENTILE = 0.01      # rows scoring below this percentile of the reference sets' gold sentences are dropped
GIBBERISH_PROFILE = "chat"
REPO_URL = "https://github.com/Nanako0129/shanjie"
MIN_ROWS = 1000


def doc_hash(doc_id, salt=""):
    """A seeded hash in [0, 1) of (SEED, document id[, salt])."""
    return int(hashlib.sha256(f"{SEED}:{doc_id}:{salt}".encode("utf-8")).hexdigest()[:12], 16) / 16 ** 12


def rows_from_docs(docs, per_doc=None):
    """docs: (doc_id, [paragraph]) -> [(doc_id, context, sentence)] with at least MIN_CTX_HAN Han characters of context
    (the README's 「前文 ≥ 16 字」 read as 16 Han characters). per_doc: keep at most that many rows of a document, picked by a
    seeded hash order and returned in document order."""
    out = []
    for d, paras in docs:
        rows = [(d, x, s) for p in paras for s, x in segments_with_ctx(p) if len(HAN_CH.findall(x)) >= MIN_CTX_HAN]
        if per_doc is not None and len(rows) > per_doc:
            top = sorted(range(len(rows)), key=lambda i: doc_hash(d, i))[:per_doc]
            rows = [rows[i] for i in sorted(top)]
        out += rows
    return out


def overlap_key(ctx, sentence):
    """Last 16 Han characters of the context (non-Han characters between them dropped) + the sentence."""
    return "".join(HAN_CH.findall(ctx))[-KEY_HAN:] + sentence


def sample_rows(rows, seed=SEED):
    """At most PER_DOC rows per document, then at most MAX_ROWS in all; deterministic, document order kept."""
    rng = random.Random(seed)
    by_doc = {}
    for i, r in enumerate(rows):
        by_doc.setdefault(r[0], []).append(i)
    keep = []
    for idx in by_doc.values():
        keep += idx if len(idx) <= PER_DOC else rng.sample(idx, PER_DOC)
    keep.sort()
    if len(keep) > MAX_ROWS:
        keep = sorted(rng.sample(keep, MAX_ROWS))
    return [rows[i] for i in keep]


def gibberish_threshold(ref_scores):
    """The GIBBERISH_PERCENTILE of the reference scores (the value at index floor(p * n) of the sorted list)."""
    s = sorted(ref_scores)
    return s[int(GIBBERISH_PERCENTILE * len(s))]


# ---- the gibberish score: per-character log10 probability of the gold string under the repo's Python reference decoder
# (reference/proto/lm.py decode, the profile GIBBERISH_PROFILE, the n-gram model of data/lm), as lm_eval.py loads it. There is no
# fixed-string scorer in the repo, so: decode the row's reading, take the score of the candidate whose surface is the gold string (lenient
# comparison), and when the gold is not among the 64 candidates the 64th candidate's score, which is an upper bound of the gold's.
MODEL_FILES = ("bigram.sjlm", "classes.sjc", "kn.sjkn")
_STATE = None


def check_models(lm_dir):
    """Stops unless every file of MODEL_FILES is in lm_dir and matches data/<name>.sha256. Returns {name: sha256}."""
    out = {}
    for name in MODEL_FILES:
        p, shaf = os.path.join(lm_dir, name), os.path.join(c.ROOT, "data", name + ".sha256")
        if not os.path.isfile(p):
            sys.exit(f"longtext: STOP, model file {p} is missing")
        want = open(shaf).read().split()[0]
        got = sha256_stream(p)
        if got != want:
            sys.exit(f"longtext: STOP, {p} hashes to {got[:16]}…, {shaf} says {want[:16]}…")
        out[name] = got
    return out


def _load_scorer(lm_dir):
    sys.path.insert(0, os.path.join(c.ROOT, "reference", "proto"))
    import lm_eval
    base = lm_eval.ime.Lexicon(os.path.join(c.ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=lm_eval.ime.overlays(None))
    path = os.path.join(lm_dir, "bigram.sjlm")
    lm = lm_eval.load_lm(path, base, True, lm_eval.kn_side(path), None)
    lex = lm_eval.L.cap_overlay(base, lm_eval.ime.capping_words(None), lm)
    return lm_eval, lex, lm


def _init_worker(lm_dir):
    global _STATE
    _STATE = _load_scorer(lm_dir)


def _char_logprob(pair):
    lm_eval, lex, lm = _STATE
    gold, syls = pair
    nb = lm_eval.L.decode(lex, syls, lm, GIBBERISH_PROFILE)
    if not nb:
        return -99.0
    t = lm_eval.lenient(gold)
    score = next((sc for sc, ws in nb if lm_eval.lenient("".join(ws)) == t), nb[-1][0])
    return score / len(gold)


def load_gibberish(procs, lm_dir=None):
    """The `gibberish` argument of build(): the scorer (a process pool of procs), the reference scores of dev302 and user-typing.txt
    gold sentences, their counts, the model hashes."""
    lm_dir = lm_dir or os.path.join(c.ROOT, "data", "lm")
    shas = check_models(lm_dir)
    _init_worker(lm_dir)
    import lm_eval
    dev = lm_eval.rows_of(sorted(glob.glob(os.path.join(c.ROOT, "eval", "dev", "*.txt"))))[:302]
    typing = lm_eval.rows_of([os.path.join(c.ROOT, "eval", "dev", "user-typing.txt")])
    pool = None
    if procs > 1:
        import multiprocessing as mp
        pool = mp.Pool(procs, _init_worker, (lm_dir,))

    def score(pairs):
        work = [(s, rdg.split()) for s, rdg in pairs]
        return pool.map(_char_logprob, work, chunksize=64) if pool else [_char_logprob(w) for w in work]
    ref = [(t, syls) for t, syls, _ctx in dev + typing]
    return {"score": score, "ref_scores": [_char_logprob(r) for r in ref],
            "ref_counts": {"dev302": len(dev), "user-typing": len(typing)}, "model_sha256": shas}


def han_only_copy(path, dst):
    """Streams a training file into dst, one Han-only line per input line. A line with `|` is L0's `context|clause|reading` row
    (context with punctuation): fields 0 and 1 are joined; a line without `|` is a Han run (M's runs-*.txt). Returns line counts."""
    n = {"lines_with_pipe": 0, "lines_without_pipe": 0}
    with open(path, encoding="utf-8", newline="\n") as f, open(dst, "w", encoding="utf-8", newline="\n") as g:
        for line in f:
            line = line.rstrip("\n")
            if "|" in line:      # an L0 row `context|clause|reading` (the reading is Zhuyin, no Han): one output line per row
                n["lines_with_pipe"] += 1
                g.write("".join(HAN_CH.findall(line)) + "\n")
            else:
                # M's runs files hold one Han run per line, and a key crosses the non-Han boundary that a newline stands for, so the
                # runs are written as one continuous Han stream, with no newline. ponytail: a key can also match across two documents'
                # runs; the false-positive risk is negligible and the drop is conservative.
                n["lines_without_pipe"] += 1
                g.write("".join(HAN_CH.findall(line)))
    return n


def overlap_hits(keys, path, procs, tmpdir, conv):
    import buildcheck      # heavy import (numpy); only the builders need it
    dst = os.path.join(tmpdir, os.path.basename(path) + ".han")
    conv[os.path.basename(path)] = {"conversion": "Han characters only; `|` lines one output line each, other lines joined without newline", **han_only_copy(path, dst)}
    return buildcheck.substring_hits(keys, [dst], procs)


SAMPLING = ("documents: seeded hash of (20261019, document id) selects them while streaming; at most 3 candidate rows per kept document by "
            "a seeded hash order; readings and overlap run on those; then rows are picked at random (seed 20261019) to at most 3 per "
            "document and at most 3,000 rows, kept in document order")


def build(docs, out, inputs, drop_files=(), report_files=(), procs=None, readings=c.readings, counts=None, gibberish=None):
    n = {} if counts is None else counts
    procs = procs or os.cpu_count() or 1
    n["sampling"] = SAMPLING
    rows = rows_from_docs(docs, PER_DOC)
    n["candidate_rows_ge16_han_context_max3_per_doc"] = len(rows)
    rd = readings([s for _, _, s in rows])
    n["dropped_check"] = sum(rd[s][0] == "check" for _, _, s in rows)
    n["dropped_unreadable"] = sum(rd[s][0] == "unreadable" for _, _, s in rows)
    rows = [r for r in rows if rd[r[2]][0] == "ok"]
    n["after_readings"] = len(rows)
    n["overlap_report"], n["overlap_dropped"], n["overlap_conversion"] = {}, {}, {}
    with tempfile.TemporaryDirectory() as td:
        for p in report_files:
            n["overlap_report"][os.path.basename(p)] = sum(overlap_hits([overlap_key(x, s) for _, x, s in rows], p, procs, td, n["overlap_conversion"]))
        for p in drop_files:
            hits = overlap_hits([overlap_key(x, s) for _, x, s in rows], p, procs, td, n["overlap_conversion"])
            n["overlap_dropped"][os.path.basename(p)] = sum(hits)
            rows = [r for r, h in zip(rows, hits) if not h]
    n["after_overlap"] = len(rows)
    if gibberish:      # row level, after readings and overlap: drop the rows whose gold sentence the n-gram model finds far too unlikely
        thr = gibberish_threshold(gibberish["ref_scores"])
        sc = gibberish["score"]([(s, rd[s][1]) for _, _, s in rows])
        n["gibberish_threshold"] = thr
        n["gibberish_reference"] = {"counts": gibberish["ref_counts"], "percentile": GIBBERISH_PERCENTILE, "model_sha256": gibberish.get("model_sha256")}
        n["gibberish"] = sum(v < thr for v in sc)
        rows = [r for r, v in zip(rows, sc) if v >= thr]
        n["after_gibberish"] = len(rows)
    rows = sample_rows(rows)
    n["final_rows"] = len(rows)
    lines = [f"{x}|{s}|{rd[s][1]}\n" for _, x, s in rows]       # never split the written text: a context may hold U+2028 and friends
    ln = sorted(len(x) for _, x, _ in rows)
    n["context_length"] = {"mean": sum(ln) / max(1, len(ln)), "quartiles": [ln[len(ln) * q // 4] for q in (1, 2, 3)] if ln else []}
    manifest = {"counts": n, "input_sha256": {p: sha256_stream(p) for p in inputs}}
    if len(rows) < MIN_ROWS:      # stop without writing the rows or the proofreading draft: only the manifest, marked as stopped
        manifest["stopped"] = f"{len(rows)} rows < {MIN_ROWS}"
        c.write_lf(out + ".manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
        print(json.dumps(manifest, ensure_ascii=False, indent=1))
        sys.exit(f"longtext: STOP, {len(rows)} rows < {MIN_ROWS}")
    c.write_lf(out, "".join(lines))
    check_rows(out, len(rows))
    pick = sorted(random.Random(SEED).sample(range(len(rows)), math.ceil(len(rows) * 0.1)))
    c.write_lf(out + ".proofread.txt", "".join(lines[i] for i in pick))
    n["proofread_rows"] = len(pick)
    manifest["output_sha256"] = sha256_stream(out)
    c.write_lf(out + ".manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, indent=1))
    return manifest


# ------------------------------------------------------------------ HPLT
KEEP_REGISTERS = ("ID", "NA", "OP")
TW_HOSTS = ("ptt.cc", "mobile01.com", "pixnet.net")


REQUIRED = ("u", "text", "pii", "web-register")


def top_register(doc):
    """The argmax label among the upper-case top-level labels of `web-register` (a dict of label -> score)."""
    wr = doc["web-register"]
    if not isinstance(wr, dict):
        sys.exit(f"longtext: STOP, web-register is a {type(wr).__name__}, expected a dict")
    scores = {k: v for k, v in wr.items() if k.isupper()}
    return max(scores, key=scores.get) if scores else None


def tw_host(url):
    """True for a .tw host or one of TW_HOSTS and their subdomains; None when the URL cannot be parsed."""
    try:
        h = (urllib.parse.urlparse(url or "").hostname or "").lower()
    except ValueError:
        return None
    return h.endswith(".tw") or any(h == d or h.endswith("." + d) for d in TW_HOSTS)


def hplt_docs(shards, counts, keep=None):
    """Yields (doc_id, [paragraph]) for the documents that pass every filter (and `keep(doc_id)`, when given); counts gets one
    counter per filter. A record without every key of REQUIRED stops the build."""
    for k in ("docs", "bad_json", "not_object", "null_text", "register", "host", "bad_url", "pii", "spam", "passing", "kept"):
        counts.setdefault(k, 0)
    for shard in shards:
        p = subprocess.Popen(["zstd", "-dc", shard], stdout=subprocess.PIPE)
        for i, line in enumerate(p.stdout):
            counts["docs"] += 1
            try:
                d = json.loads(line)
            except ValueError:
                counts["bad_json"] += 1
                continue
            if not isinstance(d, dict):
                counts["not_object"] += 1
                continue
            missing = [k for k in REQUIRED if k not in d]
            if missing:
                sys.exit(f"longtext: STOP, {shard} line {i} lacks {missing}")
            if d["text"] is None:
                counts["null_text"] += 1
                continue
            host = tw_host(d["u"])
            if top_register(d) not in KEEP_REGISTERS:
                counts["register"] += 1
            elif host is None:
                counts["bad_url"] += 1
            elif not host:
                counts["host"] += 1
            elif d["pii"]:
                counts["pii"] += 1
            elif len(SPAM_RE.findall(d["text"])) >= SPAM_MIN:
                counts["spam"] += 1
            else:
                counts["passing"] += 1
                doc_id = f"{os.path.basename(shard)}:{i}"
                if keep is None or keep(doc_id):
                    counts["kept"] += 1
                    yield doc_id, d["text"].split("\n")
        if p.wait():
            sys.exit(f"longtext: STOP, zstd failed on {shard}")


def cmd_hplt(a):
    counts = {}
    g = load_gibberish(a.procs) if a.gibberish_ref else None      # stops early when the model files are missing or differ
    before = {s: sha256_stream(s) for s in a.shards}
    if a.doc_fraction is None:
        # two passes: the first only collects the hash of every document that passes the filters; the document selection keeps the
        # a.target_docs smallest hashes, so about that many documents survive, and it is the same on every run
        hs = sorted(doc_hash(d) for d, _ in hplt_docs(a.shards, {}))
        limit = hs[a.target_docs - 1] if len(hs) >= a.target_docs else 1.0
        counts["selection"] = f"{min(len(hs), a.target_docs)} smallest of {len(hs)} document hashes, hash <= {limit}"
        keep = lambda d: doc_hash(d) <= limit   # noqa: E731
    else:
        counts["selection"] = f"document hash < {a.doc_fraction}"
        keep = lambda d: doc_hash(d) < a.doc_fraction   # noqa: E731
    stop = None
    try:
        build(hplt_docs(a.shards, counts, keep), a.out, a.shards, a.overlap_drop, a.overlap_report, a.procs, counts=counts, gibberish=g)
    except SystemExit as e:      # any stop: the shard check still runs first
        stop = e
    print(f"documents kept {counts['kept']} of {counts['passing']} passing the filters")
    if before != {s: sha256_stream(s) for s in a.shards}:
        sys.exit("longtext: STOP, a shard changed during the build")
    if stop:
        raise stop


# ------------------------------------------------------------------ Wikisource
VOID = {"br", "hr", "img", "meta", "link", "input", "wbr"}
NOISE_CLASS = ("header", "license", "noprint", "ws-noexport")     # navigation and license templates of Wikisource pages


class Paragraphs(html.parser.HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "dd", "dt", "dl", "blockquote", "tr", "td", "th", "caption", "table",
             "h1", "h2", "h3", "h4", "h5", "h6", "pre"}

    def __init__(self):
        super().__init__()
        self.paras, self.cur = [], []
        self.skip_tag, self.skip_depth = None, 0      # the element being skipped and its nesting depth of the same tag

    def flush(self):
        t = "".join(self.cur).strip()
        if t:
            self.paras.append(t)
        self.cur = []

    def noisy(self, tag, attrs):
        tokens = (dict(attrs).get("class") or "").lower().split()      # whole class tokens, so `subheader-text` stays
        return tag in ("script", "style", "table") or any(t.startswith(k) for t in tokens for k in NOISE_CLASS)

    def handle_starttag(self, tag, attrs):
        if self.skip_tag:
            self.skip_depth += tag == self.skip_tag
            return
        if tag not in VOID and self.noisy(tag, attrs):
            if tag in self.BLOCK:      # inline noise leaves the paragraph whole
                self.flush()
            self.skip_tag, self.skip_depth = tag, 1
            return
        if tag in self.BLOCK:
            self.flush()

    def handle_endtag(self, tag):
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth -= 1
                if not self.skip_depth:
                    self.skip_tag = None
                    if tag in self.BLOCK:
                        self.flush()
            return
        if tag in self.BLOCK:      # a stray </style> or </script> outside a skipped element changes nothing
            self.flush()

    def handle_data(self, data):
        if not self.skip_tag:
            self.cur.append(data)


def html_paragraphs(h):
    p = Paragraphs()
    p.feed(h)
    p.flush()
    return p.paras


API = "https://zh.wikisource.org/w/api.php"
UA = f"shanjie-longctx-eval/1.0 ({REPO_URL})"


def http_get(url):
    time.sleep(1)       # 1 request per second
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
        return json.load(r)


def fetch_title(title, get=http_get):
    out = {}
    for name, variant in (("tw", "&variant=zh-tw"), ("plain", "")):
        q = f"{API}?action=parse&page={urllib.parse.quote(title)}&redirects=1&prop=text|revid&format=json&formatversion=2{variant}"
        j = get(q)
        if "parse" not in j:
            sys.exit(f"wikisource-fetch: STOP, no parse result for {title}: {str(j)[:200]}")
        out[name] = html_paragraphs(j["parse"]["text"])
        out["revid"] = j["parse"]["revid"]
    return {"title": title, "revid": out["revid"], "paragraphs_tw": out["tw"], "paragraphs_plain": out["plain"],
            "conversion_differs": out["tw"] != out["plain"]}


def read_titles(path):
    return [l.strip() for l in open(path, encoding="utf-8", newline="\n") if l.strip() and not l.startswith("#")]


def cache_name(title):
    """The cache file of a title: named by a hash of the title, so editing the list never shifts the files."""
    return hashlib.sha256(title.encode("utf-8")).hexdigest()[:16] + ".json"


def cmd_fetch(a):
    os.makedirs(a.out, exist_ok=True)
    titles = read_titles(a.titles)
    for t in titles:
        path = os.path.join(a.out, cache_name(t))
        if not os.path.exists(path):
            c.write_lf(path, json.dumps(fetch_title(t, http_get), ensure_ascii=False, indent=1) + "\n")
    print(f"fetched {len(titles)} titles into {a.out}")


def cmd_wikisource(a):
    files = []
    for t in read_titles(a.titles):      # only the titles currently in the list
        p = os.path.join(a.fetched, cache_name(t))
        if not os.path.exists(p):
            sys.exit(f"wikisource: STOP, {t} is not fetched yet")
        files.append(p)
    docs = ((os.path.basename(p), json.load(open(p, encoding="utf-8"))["paragraphs_tw"]) for p in files)
    build(docs, a.out, files, a.overlap_drop, a.overlap_report, a.procs)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--out", required=True)
        p.add_argument("--overlap-drop", nargs="+", default=[]); p.add_argument("--overlap-report", nargs="+", default=[])
        p.add_argument("--procs", type=int, default=os.cpu_count())
    h = sub.add_parser("hplt"); h.add_argument("--shards", nargs="+", required=True); common(h)
    h.add_argument("--doc-fraction", type=float, help="keep a passing document when its seeded hash is below this; default: the --target-docs smallest hashes")
    h.add_argument("--target-docs", type=int, default=TARGET_DOCS)
    h.add_argument("--gibberish-ref", action=argparse.BooleanOptionalAction, default=True,
                   help="row-level gibberish filter against the dev302 + user-typing percentile (default on; needs data/lm)")
    f = sub.add_parser("wikisource-fetch"); f.add_argument("--titles", required=True); f.add_argument("--out", required=True)
    w = sub.add_parser("wikisource"); w.add_argument("--fetched", required=True); w.add_argument("--titles", required=True); common(w)
    a = ap.parse_args(argv)
    {"hplt": cmd_hplt, "wikisource-fetch": cmd_fetch, "wikisource": cmd_wikisource}[a.cmd](a)


if __name__ == "__main__":
    main()
