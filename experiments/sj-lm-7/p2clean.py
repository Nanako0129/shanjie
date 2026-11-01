"""Copied from experiments/p2-data/clean.py of the local, unmerged branch exp/p2-data at commit 4f7504bf, with the P2 pool builder removed
(the source readers, run() and main()); what is left is what ptt_clean.py and clean_subs.py import: the sentence split, the filters (reject),
the dedupe key and the evaluation Guard (docs/contracts/sj-lm-7.md sections 2.4 and 2.7).

Filters, in this order: split (sentence-final punctuation 。！？!? and line breaks), han_len (4..40 Han characters), han_ratio (Han >= 70% of the
non-space characters), url (URL, bare domain, e-mail, @handle), digits (a run of >= 7 digits, or a phone-like string with >= 8), code (braces,
backslash, backtick, pipe, arrows, ==, call syntax), simplified (a character in build_counts.TRAD_SIMP_ONLY). Dedupe is NFKC + whitespace removed +
case folded. The Guard drops a sentence that contains an evaluation sentence (or, for word lists, equals one).
"""
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
MIN_HAN, MAX_HAN, MIN_RATIO = 4, 40, 0.7
CONTAIN_MIN = 8      # guard: an evaluation entry of >= this many Han characters removes every pool sentence that contains it; shorter ones only
                     # remove a pool sentence they equal (see Guard). The holdout always uses holdout_guard's containment from 4 characters.

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
