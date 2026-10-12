"""S7f shared cleaning (docs/contracts/s7f-colloquial.md section 2.1). Pure functions plus one small stateful filter; no I/O except
the evaluation-set loader and the manifest writer.

Order inside a source: scrub (URL, email, phone-like -> a sentence break) -> split into sentences -> Filter.keep:
simplified (any build_counts.TRAD_SIMP_ONLY character) -> no_han -> length 4..60 -> duplicate -> eval_overlap (on the sentence and,
when a converter is given, on the sentence as prep.py text converts it for training).
`sample_to_cap` is the seeded down-sampling used by build_chat.py (per-source and total caps) and generate.py (exact quota).
"""
import collections
import glob
import hashlib
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in ("experiments/s2", "reference/proto"):
    _p = os.path.join(ROOT, *_p.split("/"))
    if _p not in sys.path:
        sys.path.insert(0, _p)
import build_counts as bc  # noqa: E402  TRAD_SIMP_ONLY (filled by load_conv), HAN, TEMPLATE, MARKUP, convert
from lm_eval import rows_of  # noqa: E402  the evaluation rows exactly as lm_eval / the CLI read them

MIN_LEN, MAX_LEN = 4, 60
SEED = 20261012
DEV_ROWS = 302
# The public evaluation sets besides dev302 and the tune sets (run_sets.rows_args names them the same way). user-reported is taken
# whole (run_sets may limit it to 66 rows; the whole file is a superset, so more is removed, never less).
EVAL_FILES = ["eval/dev/user-typing.txt", "eval/dev/user-reported.txt", "eval/rerank/s7e-function-words.txt"]
TUNE_FILES = ["cvtune.txt", "wikitune.txt"]

URL = re.compile(r"(?:https?|ftp)://[!-~]+|www\.[!-~]+", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# A phone number starts with + and a digit, or with 0 (after an optional opening parenthesis): a date (2020-03-15), an amount or a
# decimal (0.12345678) is not one. A closing parenthesis right after the number goes with it, so no stray `）` is left behind.
PHONE_CAND = re.compile(r"(?:\+\d|(?<![\d.])[(（]?[0０])[\d\s\-()（）]{6,}\d[)）]?")
PHONE_MIN_DIGITS = 8
SENT = re.compile(r"[^。！？；!?…\n]+")   # build_counts.SENT plus the half-width ! ? and the ellipsis (chat text uses them)


def load_simp_only():
    """build_counts.TRAD_SIMP_ONLY, filled from the hash-checked OpenCC files (~/.cache/shanjie/sources/opencc). Main's machines only.
    It is the reduced set the project uses for traditional sentences (S2f): the full SIMP_ONLY also holds 14 characters that are
    standard in Taiwan (群 床 秘 峰 粽 ...), which dropped every sentence with 社群, 群組, 起床 or 秘密 (S7f review, 2026-10-12)."""
    bc.load_conv()
    return frozenset(bc.TRAD_SIMP_ONLY)


def converter():
    """The conversion prep.py text applies before training (build_counts.convert with the loaded tables), for Filter's eval check."""
    conv = bc.load_conv()
    return lambda s: bc.convert(s, *conv)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def sha256_lines(lines):
    """SHA-256 of the lines as written by write_lines (each followed by a newline)."""
    h = hashlib.sha256()
    for s in lines:
        h.update(s.encode("utf-8") + b"\n")
    return h.hexdigest()


# ------------------------------------------------------------------ scrub and split
def scrub(text, counts=None):
    """URLs, emails and phone-number-like strings (+ or 0 first, >= 8 digits with separators) become a newline, i.e. a sentence break, so the
    text around them is not glued together."""
    def phone(m):
        if sum(c.isdigit() for c in m.group(0)) >= PHONE_MIN_DIGITS:
            if counts is not None:
                counts["scrub_phone"] += 1
            return "\n"
        return m.group(0)
    for name, pat in (("scrub_url", URL), ("scrub_email", EMAIL)):
        text, n = pat.subn("\n", text)
        if counts is not None and n:
            counts[name] += n
    return PHONE_CAND.sub(phone, text)


def sentences(text):
    for s in SENT.findall(text):
        s = s.strip()
        if s:
            yield s


# ------------------------------------------------------------------ whole-sentence containment
class Matcher:
    """Finds which patterns occur whole inside a text. Patterns are indexed by their first two characters (single-character
    patterns separately), so a text costs one dict lookup per position."""

    def __init__(self, patterns):
        self.single, self.idx = set(), collections.defaultdict(list)
        for p in set(patterns):
            if len(p) == 1:
                self.single.add(p)
            elif p:
                self.idx[p[:2]].append(p)

    def find(self, text):
        for i in range(len(text)):
            if text[i] in self.single:
                yield text[i]
            for p in self.idx.get(text[i:i + 2], ()):
                if text.startswith(p, i):
                    yield p

    def any(self, text):
        return next(self.find(text), None) is not None


def eval_sentences(tune, extra=(), dev=DEV_ROWS):
    """The public evaluation sentences: dev302 (the first `dev` rows of eval/dev/*.txt in name order, as lm_eval --dev reads them),
    EVAL_FILES, TUNE_FILES inside `tune`, and any `extra` row files.
    Returns ({sentence: ["<set>:<row>", ...]}, {name: {paths, sha256, rows}, ...}); row numbers are 1-based within the set."""
    out, info = {}, {}

    def add(name, paths, limit=None):
        rows = rows_of(paths)[:limit] if limit else rows_of(paths)
        for n, (t, _, _) in enumerate(rows, 1):
            if t:
                out.setdefault(t, []).append(f"{name}:{n}")
        info[name] = {"paths": [os.path.relpath(p, ROOT) if p.startswith(ROOT) else p for p in paths],
                      "sha256": [sha256_file(p) for p in paths], "rows": len(rows)}
    dev_paths = sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt")))
    add(f"dev{dev}", dev_paths, dev)
    for f in EVAL_FILES:
        add(os.path.basename(f), [os.path.join(ROOT, f)])
    for f in TUNE_FILES:
        p = os.path.join(tune, f)
        if not os.path.isfile(p):
            sys.exit(f"clean: {p} not found (--tune must hold cvtune.txt and wikitune.txt)")
        add(f, [p])
    for p in extra:
        add(os.path.basename(p), [p])
    info["distinct_sentences"] = len(out)
    info["shortest"] = min(map(len, out)) if out else 0
    # main 2026-10-12: rows shorter than MIN_LEN are not used for overlap removal (Filter); counted here, hits in the manifest
    info["below_min_rows"] = sum(len(ids) for t, ids in out.items() if len(t) < MIN_LEN)
    return out, info


def add_eval_args(ap):
    ap.add_argument("--tune", required=True, help="directory with cvtune.txt and wikitune.txt")
    ap.add_argument("--eval", nargs="*", default=[], metavar="ROWS", help="more evaluation row files (前文|句子|讀音)")


# ------------------------------------------------------------------ the sentence filter
class Filter:
    """eval_rows: eval_sentences()'s {sentence: [row ids]} (a plain set also works; its sentences are their own ids). Only evaluation
    sentences of at least `lo` characters remove a corpus sentence (main, 2026-10-12): a 1-3 character row such as `吧` inside a
    sentence is not an overlap of that sentence. For each shorter row, short_report() gives how many kept sentences contain it.
    `convert` (clean.converter()): the eval check also looks at the sentence as training sees it (這裏 -> 這裡, 鼠標 -> 滑鼠)."""

    def __init__(self, simp_only, eval_rows, lo=MIN_LEN, hi=MAX_LEN, convert=None):
        rows = eval_rows if isinstance(eval_rows, dict) else {t: [t] for t in eval_rows}
        self.simp, self.lo, self.hi, self.convert = frozenset(simp_only), lo, hi, convert
        self.ev = Matcher(t for t in rows if len(t) >= lo)
        self.short_rows = {t: ids for t, ids in rows.items() if len(t) < lo}
        self.short = Matcher(self.short_rows)
        self.seen, self.removed, self.short_hits = set(), collections.Counter(), collections.Counter()

    def reason(self, s):
        if not self.simp.isdisjoint(s):
            return "simplified"
        if not bc.HAN.search(s):
            return "no_han"
        if not self.lo <= len(s) <= self.hi:
            return "length"
        if s in self.seen:
            return "duplicate"
        if self.ev.any(s) or (self.convert is not None and self.ev.any(self.convert(s))):
            return "eval_overlap"
        return None

    def keep(self, s):
        r = self.reason(s)
        if r:
            self.removed[r] += 1
            return False
        self.seen.add(s)
        self.short_hits.update(set(self.short.find(s)))
        return True

    def short_report(self):
        """{row id: kept sentences containing that row's sentence} for the rows below the minimum length. Counts only."""
        return {i: self.short_hits[t] for t, ids in sorted(self.short_rows.items()) for i in ids}


def clean_text(text, flt, counts):
    """scrub + split + filter one block of text; yields the kept sentences."""
    for s in sentences(scrub(text, counts)):
        if flt.keep(s):
            yield s


# ------------------------------------------------------------------ seeded sampling to a character cap
def sample_to_cap(lines, cap, seed=SEED):
    """Lines whose lengths sum to exactly `cap` when the total is above it: shuffled with `seed`, a prefix up to cap - 60, then the
    remaining gap (60..120 characters) filled exactly by a subset of the next SUBSET_POOL lines in shuffled order (subset_sum).
    Returned in the input order. A total <= cap comes back whole; when no exact subset exists the result is below cap (the caller
    decides what that means; generate.py collects SURPLUS characters more than its quota so the pool is never that thin)."""
    lens = [len(s) for s in lines]
    if sum(lens) <= cap:
        return list(lines)
    order = list(range(len(lines)))
    random.Random(seed).shuffle(order)
    take, n, k = [], 0, 0
    while k < len(order) and n + lens[order[k]] <= cap - MAX_LEN:
        take.append(order[k]); n += lens[order[k]]; k += 1
    pool = order[k:k + SUBSET_POOL]
    sub = subset_sum([lens[j] for j in pool], cap - n)
    if sub is not None:
        take += [pool[x] for x in sub]
    return [lines[i] for i in sorted(take)]


SUBSET_POOL = 1000


def subset_sum(lens, target):
    """Positions of a subset of `lens` summing to `target` (0/1, the first found scanning in order), or None."""
    reach = {0: None}
    for k, L in enumerate(lens):
        if target in reach:
            break
        for s in list(reach):
            if s + L <= target and s + L not in reach:
                reach[s + L] = (s, k)
    if target not in reach:
        return None
    out, s = [], target
    while s:
        s, k = reach[s]
        out.append(k)
    return out


def water_fill(sizes, total):
    """Per-source quotas: everything when the sum fits; otherwise sources below the common level are taken whole and the others
    share the rest equally (remainder one character each to the earliest of them)."""
    if sum(sizes) <= total:
        return list(sizes)
    q, left, rest = [None] * len(sizes), total, sorted(range(len(sizes)), key=lambda i: (sizes[i], i))
    while rest and sizes[rest[0]] * len(rest) <= left:
        i = rest.pop(0); q[i] = sizes[i]; left -= sizes[i]
    level, extra = divmod(left, len(rest))
    for j, i in enumerate(sorted(rest)):
        q[i] = level + (j < extra)
    return q


# ------------------------------------------------------------------ output
def write_lines(path, lines):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for s in lines:
            f.write(s + "\n")


def read_lines(path):
    with open(path, encoding="utf-8", newline="\n") as f:
        return [l.rstrip("\n") for l in f if l.strip()]


def write_manifest(path, d):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")


def source_manifest(source, inputs, kept, flt, counts, eval_info, **extra):
    return {"source": source, "inputs": inputs, "rows": len(kept), "chars": sum(map(len, kept)), "sha256": sha256_lines(kept),
            "removed": dict(sorted(flt.removed.items())), "scrubbed": dict(sorted(counts.items())),
            "length": [flt.lo, flt.hi], "eval": eval_info,
            "eval_below_min_hits": flt.short_report(), **extra}
