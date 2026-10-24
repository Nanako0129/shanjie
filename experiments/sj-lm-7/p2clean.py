#!/usr/bin/env python3
"""Copied from experiments/p2-data/clean.py of the local, unmerged branch exp/p2-data at commit 4f7504bf; only this note differs.
SJ-LM-7 (docs/contracts/sj-lm-7.md sections 2.4 and 2.7) reuses its sentence split, filters, dedupe key and Guard: ptt_clean.py and
clean_subs.py import this file. The readers and run() below are the original P2 pool builder and are not used here.

P2 candidate pool, step 2: raw downloads (fetch.py) -> ~/.cache/shanjie/p2-public/pool.tsv (`source<TAB>source_id<TAB>sentence`).

Per source: units -> sentences -> filters, in this order (every step is counted in manifest.json):
  split      sentence-final punctuation (。！？!?) and line breaks; whitespace stripped; empty pieces dropped
  han_len    4..40 Han characters
  han_ratio  Han characters >= 70% of the non-space characters
  url        URL, bare domain, e-mail address or @handle
  digits     a run of >= 7 digits (full-width folded), or a phone-number-like string with >= 8 digits
  code       code-like text (braces, backslash, backtick, pipe, arrows, ==, call syntax)
  simplified a character the project treats as Simplified-only in Traditional text (build_counts.TRAD_SIMP_ONLY, from STCharacters.txt)
  dedupe     exact duplicate after NFKC + whitespace removal + case folding, across all sources (first source in the run order wins)
  guard      the sentence contains (or, for word lists, equals) an evaluation sentence
Sentences containing a TWPhrases key whose Taiwan form differs are only flagged (flags.tsv), never dropped.

  clean.py [--raw DIR] [--out DIR] [--repo DIR] [--contain-min N]
"""
import argparse
import collections
import glob
import hashlib
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in ("tools", "experiments/s2", "reference/proto"):
    sys.path.insert(0, os.path.join(ROOT, *_p.split("/")))
import build_counts as bc  # noqa: E402  HAN, fold, convert, load_conv, TRAD_SIMP_ONLY, SIMP_ONLY, TW_PHRASE
import holdout_guard as hg  # noqa: E402  tools/holdout_guard.py; it puts experiments/s7f-corpus first on sys.path and imports ITS `clean`
s7f = hg.clean   # experiments/s7f-corpus/clean.py (Matcher). This file has the same name: never `import clean` here, load it by path (test_p2.py does)

CACHE = os.path.expanduser("~/.cache/shanjie")
RAW = os.path.join(CACHE, "p2-public", "raw")
OUT = os.path.join(CACHE, "p2-public")
MIN_HAN, MAX_HAN, MIN_RATIO = 4, 40, 0.7
CONTAIN_MIN = 8      # guard: an evaluation entry of >= this many Han characters removes every pool sentence that contains it; shorter ones only
                     # remove a pool sentence they equal (see Guard). The holdout always uses holdout_guard's containment from 4 characters.
YODAS_GAP = 50       # 10 ms units: a pause of >= 0.5 s between two subtitle fragments ends the sentence (about the 80th percentile of gaps)
YODAS_VID = 11       # a YouTube id is 11 characters and may contain '-', so the utterance id is cut by position
ORDER = ["wildchat", "yodas", "tatoeba", "massive"]
LICENCE = {"wildchat": "ODC-BY 1.0 (allenai/WildChat-1M)", "yodas": "CC BY 3.0 (espnet/yodas, CC-BY YouTube videos)",
           "tatoeba": "CC BY 2.0 FR (Tatoeba)", "massive": "CC BY 4.0 (AmazonScience/massive)"}

HANC = re.compile(r"[一-鿿]")
CJK = "⺀-鿿　-〿＀-￯"
SPLIT = re.compile(r"[。！？!?\n\r  ]+")
LEAD = re.compile(r"^(?:[\-*•#>]+\s*|\(?\d{1,2}[.)、)](?!\d)\s*|[」』）)】》\s]+)+")
URL = re.compile(r"https?://|ftp://|www\.|(?<![A-Za-z0-9-])[A-Za-z0-9-]+\.(?:com|net|org|edu|gov|io|tw|cn|co|app|info|me|tv)(?![A-Za-z0-9])", re.I)   # \\b is no boundary next to Han
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
HANDLE = re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_.]{2,}")
DIGITS = re.compile(r"\d{7,}")
PHONE = re.compile(r"(?:\+\d|(?<![\d.])[(]?0)[\d\s\-()]{6,}\d[)]?")   # same shape as experiments/s7f-corpus/clean.py PHONE_CAND, on NFKC text
PHONE_MIN_DIGITS = 8
CODE = re.compile(r"[{}<>\\`|$^]|==|!=|=>|->|::|\(\)|\w\.\w+\(|;\s*$")
STEPS = ["split", "han_len", "han_ratio", "url", "digits", "code", "simplified", "dedupe", "guard"]


# ------------------------------------------------------------------ text helpers (pure)
def tidy(s):
    """Whitespace: collapsed, and removed next to a CJK character (Chinese is not written with spaces; YODAS and chat have stray ones)."""
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(rf"(?<=[{CJK}]) ", "", s)
    return re.sub(rf" (?=[{CJK}])", "", s)


def split_sentences(text):
    """Pieces of `text` between sentence-final punctuation / line breaks; list-bullet and stray closing-quote leaders removed; empties dropped."""
    out = []
    for p in SPLIT.split(text):
        p = tidy(LEAD.sub("", p.strip()))
        if p:
            out.append(p)
    return out


def han_count(s):
    return len(HANC.findall(s))


def reject(s, simp):
    """-> the name of the first filter step that drops `s` (STEPS order, split..simplified), or None."""
    h = han_count(s)
    if not MIN_HAN <= h <= MAX_HAN:
        return "han_len"
    if h < MIN_RATIO * len(re.sub(r"\s", "", s)):
        return "han_ratio"
    if URL.search(s) or EMAIL.search(s) or HANDLE.search(s):
        return "url"
    n = unicodedata.normalize("NFKC", s)
    if DIGITS.search(n) or any(sum(c.isdigit() for c in m.group(0)) >= PHONE_MIN_DIGITS for m in PHONE.finditer(n)):
        return "digits"
    if CODE.search(s):
        return "code"
    if not simp.isdisjoint(s):
        return "simplified"
    return None


def dedupe_key(s):
    """NFKC (full-width -> half-width, compatibility forms), all whitespace removed, case folded."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).casefold()


class TwFlag:
    """Sentences containing a TWPhrases key (a mainland-style term) that OpenCC would change to a different Taiwan form."""

    def __init__(self, phrases):
        self.map = {k: v for k, v in phrases.items() if k != v}
        self.rx = re.compile("|".join(sorted(map(re.escape, self.map), key=len, reverse=True))) if self.map else None

    def hits(self, s):
        return sorted({m.group(0) for m in self.rx.finditer(s)}) if self.rx else []


# ------------------------------------------------------------------ YODAS fragments -> sentences
YODAS_LINE = re.compile(r"^(.{%d})-(\d{5})-(\d{8})-(\d{8})(?: (.*))?$" % YODAS_VID)


def yodas_units(lines):
    """Merges consecutive subtitle fragments of one video into units: yields (source_id, text).
    A unit ends at: another video / a skipped segment number / a pause >= YODAS_GAP between fragments / a fragment that ends with
    sentence-final punctuation / adding the next fragment would exceed MAX_HAN Han characters. Fragments are joined without a space except
    between two ASCII alphanumerics. A line that does not parse is counted by the caller (returned as source_id None)."""
    buf, vid, first, last_seg, last_end = [], None, None, None, None

    def flush():
        nonlocal buf
        if buf:
            u = (f"{vid}:{first}", "".join(buf))
            buf = []
            return u
        return None
    for line in lines:
        m = YODAS_LINE.match(line.rstrip("\n"))
        if not m:
            yield None, line
            continue
        v, seg, start, end, text = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), (m.group(5) or "").strip()
        if buf and (v != vid or seg != last_seg + 1 or start - last_end >= YODAS_GAP
                    or han_count("".join(buf)) + han_count(text) > MAX_HAN):
            u = flush()
            if u:
                yield u
        if not buf:
            vid, first = v, seg
        if buf and re.match(r"[A-Za-z0-9]", text) and re.search(r"[A-Za-z0-9]$", buf[-1]):
            buf.append(" ")
        buf.append(text)
        last_seg, last_end = seg, end
        if SPLIT.search(text[-1:]):
            u = flush()
            if u:
                yield u
    u = flush()
    if u:
        yield u


# ------------------------------------------------------------------ source readers
def read_wildchat(raw, stats):
    d = os.path.join(raw, "wildchat")
    stats.update(conversations=0, user_turns=0, dropped_toxic=0, dropped_redacted=0)
    for p in sorted(glob.glob(os.path.join(d, "page-*.jsonl"))):
        for line in open(p, encoding="utf-8"):
            r = json.loads(line)
            stats["conversations"] += 1
            for t in r["turns"]:
                stats["user_turns"] += 1
                if t["toxic"]:
                    stats["dropped_toxic"] += 1
                elif t["redacted"]:
                    stats["dropped_redacted"] += 1
                else:
                    yield f"{r['hash']}:{t['i']}", t["text"], {}


def read_yodas(raw, stats):
    stats.update(files=0, lines=0, unparsed=0, units=0)
    vids = set()
    for p in sorted(glob.glob(os.path.join(raw, "yodas", "*.txt"))):
        stats["files"] += 1
        lines = list(open(p, encoding="utf-8"))
        stats["lines"] += len(lines)
        for sid, text in yodas_units(lines):
            if sid is None:
                stats["unparsed"] += 1
                continue
            stats["units"] += 1
            vids.add(sid.split(":")[0])
            yield sid, text, {}
    stats["videos"] = len(vids)


def tatoeba_native(raw):
    """-> ({id: (text, username)}, counts). Native Traditional = the sentence has a Hans transcription (Tatoeba stores one script and
    generates the other as a transcription); native Simplified has a Hant transcription."""
    sents = {}
    for l in open(os.path.join(raw, "tatoeba", "cmn_sentences_detailed.tsv"), encoding="utf-8"):
        f = l.rstrip("\n").split("\t")
        sents[f[0]] = (f[2], "" if f[3] == "\\N" else f[3])
    scripts = collections.defaultdict(set)
    for l in open(os.path.join(raw, "tatoeba", "cmn_transcriptions.tsv"), encoding="utf-8"):
        f = l.rstrip("\n").split("\t")
        if f[1] == "cmn" and f[2] in ("Hans", "Hant"):
            scripts[f[0]].add(f[2])
    native_t = {i for i, s in scripts.items() if s == {"Hans"} and i in sents}
    c = {"sentences": len(sents), "with_hans_transcription": sum("Hans" in s for i, s in scripts.items() if i in sents),
         "with_hant_transcription": sum("Hant" in s for i, s in scripts.items() if i in sents),
         "with_both": sum(len(s) == 2 for i, s in scripts.items() if i in sents),
         "no_hans_hant_transcription": sum(1 for i in sents if i not in scripts), "native_traditional": len(native_t)}
    return {i: sents[i] for i in sorted(native_t, key=int)}, c


def read_tatoeba(raw, stats):
    native, c = tatoeba_native(raw)
    stats.update(c)
    for i, (text, user) in native.items():
        yield i, text, {"author": user}


def read_massive(raw, stats):
    stats.update(utterances=0, partitions={})
    for line in open(os.path.join(raw, "massive", "zh-TW.jsonl"), encoding="utf-8"):
        r = json.loads(line)
        stats["utterances"] += 1
        stats["partitions"][r["partition"]] = stats["partitions"].get(r["partition"], 0) + 1
        yield f"{r['partition']}-{r['id']}", r["utt"], {}


READERS = {"wildchat": read_wildchat, "yodas": read_yodas, "tatoeba": read_tatoeba, "massive": read_massive}


# ------------------------------------------------------------------ leakage guard
class Guard:
    """Evaluation text against pool sentences. Two kinds of entry, both on the counting layer (Han runs, bc.fold, plus the bc.convert form):
      contain  every Han run of >= `contain_min` characters of a row-file field (`context|sentence|reading`): a pool sentence is dropped
               when one of its Han runs contains the entry whole (tools/holdout_guard.py / experiments/s7f-corpus/clean.py Matcher). The
               holdout rows use contain_min = MIN_HAN (4), exactly as holdout_guard does;
      short    the same, for the 4..contain_min-1 character runs of every other set: NOT dropped (a 5-character eval sentence such as a
               greeting sits inside thousands of chat sentences, and dropping those removes exactly the register wanted); counted only;
      exact    every entry of >= 4 Han characters, and all of a word list / JSON string / `|`-less line: dropped when the pool sentence's Han
               characters, joined, equal the entry's. Containment is never used for word lists (a 4-character word would remove every
               sentence that contains it).
    The holdout rows go in through holdout_guard.build_matcher (sentence field) and holdout_guard.read_lines (context field); their text is
    never printed or stored, only counts."""

    def __init__(self, conv, contain_min=CONTAIN_MIN):
        self.conv = conv
        self.contain_min = contain_min
        self.contain = collections.defaultdict(set)   # pattern -> {label}
        self.short = collections.defaultdict(set)
        self.exact = collections.defaultdict(set)
        self.files = []                              # manifest rows: label, path (repo-relative or ~-relative), sha256, entries
        self.matcher = self.short_matcher = None

    def _add_contain(self, label, text):
        for run in bc.HAN.findall(text):
            if len(run) >= MIN_HAN:
                strict = label == "holdout" or len(run) >= self.contain_min
                for v in {run, bc.convert(run, *self.conv)}:
                    (self.contain if strict else self.short)[bc.fold(v)].add(label)
                self._add_exact(label, run)

    def _add_exact(self, label, text):
        h = "".join(bc.HAN.findall(text))
        if len(h) >= MIN_HAN:
            for v in {h, "".join(bc.HAN.findall(bc.convert(h, *self.conv)))}:
                self.exact[bc.fold(v)].add(label)

    def add_rows_file(self, label, path, shown):
        """A text/tsv file: lines with `|` are rows (every field is a contain entry); other non-comment lines are exact entries."""
        n = 0
        for line in hg.read_lines(path):
            line = line.rstrip("\r")
            if not line.strip() or line.startswith("#"):
                continue
            n += 1
            if "|" in line:
                for fld in line.split("|"):
                    self._add_contain(label, fld)
            else:
                for fld in line.split("\t"):
                    self._add_exact(label, fld)
        self.files.append({"label": label, "path": shown, "sha256": sha256_file(path), "entries": n})

    def add_json_file(self, label, path, shown):
        n = 0

        def walk(x):
            nonlocal n
            if isinstance(x, str):
                for s in [x] + split_sentences(x):
                    n += 1
                    self._add_exact(label, s)
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
        walk(json.load(open(path, encoding="utf-8")))
        self.files.append({"label": label, "path": shown, "sha256": sha256_file(path), "entries": n})

    def add_holdout(self, repo):
        rows = sorted(glob.glob(os.path.join(repo, "eval", "holdout", "**", "*.txt"), recursive=True))
        m, owners, loaded, skipped = hg.build_matcher(rows, self.conv)
        for pat in owners:                     # owners' values are row ids; they are dropped here and never printed
            self.contain[pat].add("holdout")
        for p in rows:                          # the context field (hg matches the sentence field only)
            for line in hg.read_lines(p):
                f = line.split("|")
                if len(f) == 3:
                    self._add_contain("holdout", f[0])
            self.files.append({"label": "holdout", "path": os.path.relpath(p, repo), "sha256": sha256_file(p), "entries": None})
        self.files.append({"label": "holdout", "path": "(holdout_guard.build_matcher)", "patterns_loaded": loaded, "skipped_short": skipped})

    def build(self):
        self.matcher = s7f.Matcher(self.contain)
        self.short_matcher = s7f.Matcher(self.short)

    def hits(self, s):
        """-> (labels whose entries this pool sentence contains / equals, labels of short entries it only contains)."""
        c = bc.convert(s, *self.conv)
        runs = bc.HAN.findall(s) + bc.HAN.findall(c)
        labels = set()
        for r in runs:
            for p in self.matcher.find(bc.fold(r)):
                labels |= self.contain[p]
        h = "".join(bc.HAN.findall(s))
        hc = "".join(bc.HAN.findall(c))
        for k in {bc.fold(h), bc.fold(hc)}:
            labels |= self.exact.get(k, set())
        short = set()
        for r in runs:
            for p in self.short_matcher.find(bc.fold(r)):
                short |= self.short[p]
        return labels, short - labels


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def guard_files(repo):
    """label -> [(path, kind)]: every evaluation file the guard reads (holdout is separate)."""
    def g(*parts):
        return sorted(glob.glob(os.path.join(*parts), recursive=True))
    out = collections.OrderedDict()
    out["dev"] = [(p, "rows") for p in g(repo, "eval", "dev", "**", "*.txt") + g(repo, "eval", "dev", "**", "*.list")]
    out["bench"] = ([(p, "rows") for p in g(repo, "eval", "bench", "**", "*.txt") + g(repo, "eval", "bench", "**", "*.tsv")]
                    + [(p, "json") for p in g(repo, "eval", "bench", "**", "*.json")])
    out["eval-other"] = [(p, "rows") for sub in ("rerank", "probe", "learn", "sets") for p in g(repo, "eval", sub, "**", "*.txt") + g(repo, "eval", sub, "**", "*.tsv")]
    out["eval-other"] += [(os.path.join(repo, "eval", "golden", "manual-words-rows.txt"), "rows")]
    tune = [p for p in g(CACHE, "s7c2c-public", "tune", "*.txt")]
    # the tuning / judge sets themselves, wherever a copy sits under ~/.cache/shanjie: cvtune.txt, wikitune.txt, wikijudge*.txt. Not the files
    # derived from them (*.rowstats.tsv, *.dump, *.rs, bench/rows/*): those hold scores and candidate lists, not evaluation sentences.
    for pat in ("cvtune.txt", "wikitune.txt", "wikijudge*.txt"):
        tune += [p for p in g(CACHE, "**", pat) if "/p2-public/" not in p]
    out["tune"] = [(p, "rows") for p in tune]
    out["e2"] = [(p, "rows") for p in g(CACHE, "e2-public", "*.txt")]
    return out


def build_guard(repo, conv, contain_min):
    gd = Guard(conv, contain_min)
    gd.add_holdout(repo)
    seen, skipped = {}, []
    for label, items in guard_files(repo).items():
        for p, kind in items:
            if not os.path.isfile(p):
                continue
            digest = sha256_file(p)
            if digest in seen:                    # byte-identical copy of a file already read (e.g. session/up3/cvtune.txt)
                skipped.append({"path": shown(p, repo), "same_as": seen[digest]})
                continue
            seen[digest] = shown(p, repo)
            (gd.add_json_file if kind == "json" else gd.add_rows_file)(label, p, shown(p, repo))
    gd.build()
    return gd, skipped


def shown(p, repo):
    return os.path.relpath(p, repo) if p.startswith(repo) else p.replace(os.path.expanduser("~"), "~")


# ------------------------------------------------------------------ main
def run(raw, out, repo, contain_min=CONTAIN_MIN):
    conv = bc.load_conv()
    simp, simp_full, tw = bc.TRAD_SIMP_ONLY, bc.SIMP_ONLY, TwFlag(bc.TW_PHRASE)
    guard, guard_dups = build_guard(repo, conv, contain_min)
    seen = set()
    pool, flags = [], []
    man = {"sources": {}, "step_order": STEPS}
    guard_order = ["holdout", "dev", "bench", "eval-other", "tune", "e2"]
    guard_hits = collections.Counter()
    guard_first = collections.Counter()
    short_hits = collections.Counter()
    for src in ORDER:
        stats, funnel, extra = {}, collections.OrderedDict((s, 0) for s in STEPS), collections.Counter()
        authors, vids, kept_n = set(), set(), 0
        for sid, text, meta in READERS[src](raw, stats):
            for k, s in enumerate(split_sentences(text)):
                funnel["split"] += 1
                why = reject(s, simp)
                for step in STEPS[1:STEPS.index(why) if why else 7]:
                    funnel[step] += 1
                if why:
                    extra["dropped_" + why] += 1
                    continue
                if not simp_full.isdisjoint(s):
                    extra["kept_but_full_simp_only_would_drop"] += 1
                key = dedupe_key(s)
                if key in seen:
                    extra["dropped_dedupe"] += 1
                    continue
                seen.add(key)
                funnel["dedupe"] += 1
                labels, short = guard.hits(s)
                for l in short:
                    short_hits[(src, l)] += 1
                if labels:
                    extra["dropped_guard"] += 1
                    for l in labels:
                        guard_hits[(src, l)] += 1
                    guard_first[(src, next(l for l in guard_order if l in labels))] += 1
                    continue
                funnel["guard"] += 1
                new_id = sid if k == 0 else f"{sid}:{k}"
                pool.append((src, new_id, s))
                if meta.get("author"):
                    authors.add(meta["author"])
                if src == "yodas":
                    vids.add(sid.split(":")[0])
                th = tw.hits(s)
                if th:
                    flags.append((src, new_id, ",".join(th)))
        man["sources"][src] = {"licence": LICENCE[src], "raw": stats, "kept_after_step": dict(funnel), "dropped": dict(extra),
                               "kept": funnel["guard"]}
        if src == "tatoeba":
            man["sources"][src]["attribution"] = {"authors_in_pool": len(authors)}
        if src == "yodas":
            man["sources"][src]["attribution"] = {"videos_in_pool": len(vids), "channel": "not in the text files (only the video id is)"}
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "pool.tsv"), "w", encoding="utf-8", newline="\n") as f:
        for r in pool:
            f.write("\t".join(r) + "\n")
    with open(os.path.join(out, "flags.tsv"), "w", encoding="utf-8", newline="\n") as f:
        for r in flags:
            f.write("\t".join(r) + "\n")
    # attribution: Tatoeba sentence id -> author (the id is the pool's source_id)
    native, _ = tatoeba_native(raw)
    with open(os.path.join(out, "attribution.tsv"), "w", encoding="utf-8", newline="\n") as f:
        for src, sid, _s in pool:
            if src == "tatoeba":
                f.write(f"tatoeba\t{sid}\t{native[sid.split(':')[0]][1]}\n")
            elif src == "yodas":
                f.write(f"yodas\t{sid}\thttps://www.youtube.com/watch?v={sid.split(':')[0]}\n")
    man["pool"] = {"path": "pool.tsv", "rows": len(pool), "sha256": sha256_file(os.path.join(out, "pool.tsv")),
                   "han_chars": sum(han_count(s) for _, _, s in pool)}
    man["flags"] = {"path": "flags.tsv", "tw_phrase_flagged": len(flags), "by_source": dict(collections.Counter(s for s, _, _ in flags))}
    man["guard"] = {"dropped_by_first_label": {f"{s}/{l}": n for (s, l), n in sorted(guard_first.items())},
                    "hits_by_label_nonexclusive": {f"{s}/{l}": n for (s, l), n in sorted(guard_hits.items())},
                    "contain_min": contain_min,
                    "kept_but_contain_a_short_eval_sentence": {f"{s}/{l}": n for (s, l), n in sorted(short_hits.items())},
                    "files": guard.files, "byte_identical_copies_skipped": guard_dups,
                    "label_order_for_first": guard_order}
    man["opencc"] = {n: sha256_file(os.path.join(CACHE, "sources", "opencc", n)) for n in ("STCharacters.txt", "TWPhrases.txt", "TWVariants.txt")}
    man["simplified_set"] = {"used": "build_counts.TRAD_SIMP_ONLY", "size": len(simp), "full_SIMP_ONLY_size": len(simp_full),
                             "carved_out": "".join(sorted(simp_full - simp))}
    fetched = []
    if os.path.exists(os.path.join(raw, "fetched.jsonl")):
        fetched = [json.loads(l) for l in open(os.path.join(raw, "fetched.jsonl"), encoding="utf-8")]
    for src in ORDER:
        man["sources"][src]["fetched"] = [{k: v for k, v in x.items() if k != "source"} for x in fetched if x["source"] == src]
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return man


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=RAW)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--repo", default=ROOT)
    ap.add_argument("--contain-min", type=int, default=CONTAIN_MIN, help="Han characters from which an evaluation entry removes sentences containing it")
    a = ap.parse_args()
    man = run(a.raw, a.out, a.repo, a.contain_min)
    for s, d in man["sources"].items():
        print(s, d["kept_after_step"])
    print("pool", man["pool"]["rows"], "flagged", man["flags"]["tw_phrase_flagged"])
    print("guard", man["guard"]["dropped_by_first_label"])


if __name__ == "__main__":
    main()
