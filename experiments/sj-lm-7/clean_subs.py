#!/usr/bin/env python3
"""OPUS OpenSubtitles zh_tw lines -> sentences for the colloquial counts and the tinylm corpus (docs/contracts/sj-lm-7.md section 2.7).
Brought from the SUB-0 branch (origin/exp/sub0-subtitles, experiments/sub0-subtitles/clean_subs.py); the one addition is the film cap below.

  clean_subs.py --raw raw/zh_tw.zip --out DIR --lexicon data/lexicon/mcbpmf-data.txt --invade INVADE_CLONE
                --invade-sha256 HEX [--repo DIR] [--cache DIR] [--extra-rows LABEL=FILE ...] [--min-han 4000000] [--sample N --seed S]

Order, after p2clean.py's split / filters / Guard (copied from exp/p2-data 4f7504bf):
  split, cue    p2clean.split_sentences (。！？!? and line breaks, not commas), then a leading '-' / opening quote is stripped
  han_len, han_ratio, url, digits, code   p2clean.reject (4..40 Han characters, >= 70% Han, URL / e-mail / handle, digit runs and phones, code)
  kana          a kana character (hiragana, katakana, phonetic extensions, half-width katakana)
  lexicon       >= 30% of the Han characters are not a character of --lexicon's words
  english       >= 2 English words
  simplified    p2clean's TRAD_SIMP_ONLY character
  invade        a term (two characters or more) of the invade list, the S7f generated-sentence rule (invade.load + Matcher.any)
  dedupe        NFKC + whitespace removed + case folded
  guard         p2clean.Guard over the evaluation / tuning / judging sets and the holdout (4-character containment for the holdout)
Films are counted, not capped (contract 2.7, maintainer 2026-10-25: no per-film cap). A film is a run of non-empty lines between blank lines; the
input is OPUS raw/zh_tw.zip (one XML file per subtitle, OpenSubtitles/raw/zh_tw/<year>/<film id>/<file>.xml), read as the text of every <s> with its
<time> tags removed, files in path order, one blank line after each film id. The mono file (mono/zh_tw.txt.gz) has no blank line at all (measured
2026-10-25: 1,456,479 lines), so it reads as one film; plain-text input is still accepted (tests).
Writes OUT/subs.tsv (all kept) and OUT/subs-half.tsv (the kept sentences whose SHA-256(UTF-8) first byte is < 128), both `source<TAB>id<TAB>
sentence` like p2-public/pool.tsv, plus OUT/manifest.json. Prints counts only: per step the sentences dropped and the 20 most frequent triggers,
where a trigger is a member of a closed set (a rule, a bucket, a kana / simplified-only / non-lexicon character, a list term, a label), never
a piece of a sentence. The only mode that prints sentences is --sample N --seed S, for proofreading the PUBLIC subtitle file.
--extra-rows adds row files (the private tuning / judging sets on the machine that has them) to the guard, and the run STOPs unless the guard
covers holdout, chatclause, discordtune, cvtune-clean and chatjudge2 (label them so); every label uses 4-character containment; --cache replaces the
cache directory p2clean.guard_files reads the tuning sets from. Exit 1 with a STOP line when the kept Han characters are below --min-han.
"""
import argparse
import collections
import glob
import gzip
import hashlib
import json
import os
import random
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "experiments", "s7f-corpus"))
import p2clean as P2  # noqa: E402  (puts tools, experiments/s2, reference/proto on sys.path)
import invade  # noqa: E402

bc = P2.bc
SOURCE = "opus-subs"
KANA = re.compile("[぀-ゟ゠-ヿㇰ-ㇿｦ-ﾟ]")
ENGLISH_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*")
MIN_ENGLISH_WORDS = 2
MAX_NON_LEXICON = 0.30     # a sentence with this share or more of its Han characters outside the lexicon is dropped
MIN_KEPT_HAN = 4_000_000   # contract 2.7: subtitles >= 4,000,000 Han characters
TOP = 20
REQUIRED_LABELS = ("holdout", "chatclause", "discordtune", "cvtune-clean", "chatjudge2")   # the sets the guard must cover
CUE_SPLIT = re.compile(r"\s+[-–—－]+\s*")      # a second speaker's dash inside a line ("- A - B"); a hyphen with no space before it is not one
STEPS = ["han_len", "han_ratio", "url", "digits", "code", "kana", "lexicon", "english", "simplified", "invade", "dedupe", "guard"]
CUE_LEAD = re.compile(r"^(?:[-–—－\s]|[「『“\"])+")
QUOTE_PAIRS = (("「", "」"), ("『", "』"), ("“", "”"), ('"', '"'))


def sentences(line):
    """p2clean.split_sentences of each speaker's part of one subtitle line (two-speaker lines are cut at the inner dash before tidy() removes the
    spaces that show it); the ids' running index k counts across the parts."""
    return [x for part in CUE_SPLIT.split(line) for x in P2.split_sentences(part)]


def strip_cue(s):
    """A subtitle cue starts with '-' (dialogue dash) and may open a quote; a closing quote is dropped only when its opener is not in the sentence."""
    s = CUE_LEAD.sub("", s)
    for o, c in QUOTE_PAIRS:
        if s.endswith(c) and (o not in s[:-1] or o == c and s.count(c) % 2 == 1):
            s = s[:-1].rstrip()
    return s.strip()


def half(s):
    """The fixed, reproducible half: SHA-256 of the sentence's UTF-8, first byte < 128."""
    return hashlib.sha256(s.encode("utf-8")).digest()[0] < 128


def lexicon_chars(path):
    """The Han characters of the words of a McBopomofo-format lexicon file (comment and '_' lines, exactly three fields)."""
    out = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.split()
            if line[:1] in ("#", "_") or len(p) != 3:
                continue
            out.update(P2.HANC.findall(p[1]))
    return out


def decile(x):
    return f"{int(x * 10) / 10:.1f}"


def trigger(step, s, ctx):
    """-> a closed-set label for why `step` dropped `s` (never text of `s`), or a list of such labels (counted once each)."""
    if step == "han_len":
        return "fewer than 4 Han" if P2.han_count(s) < P2.MIN_HAN else "more than 40 Han"
    if step == "han_ratio":
        return "han share " + decile(P2.han_count(s) / len(re.sub(r"\s", "", s)))
    if step == "url":
        if P2.EMAIL.search(s):
            return "e-mail"
        if P2.HANDLE.search(s):
            return "@handle"
        m = P2.URL.search(s).group(0).lower()
        return "scheme" if "://" in m else "www" if m.startswith("www.") else "domain ." + m.rsplit(".", 1)[-1]
    if step == "digits":
        return "digit run >= 7" if P2.DIGITS.search(unicodedata.normalize("NFKC", s)) else "phone-like"
    if step == "code":
        m = P2.CODE.search(s).group(0)
        return m if len(m) <= 2 and not m.isalnum() else "call syntax" if m.endswith("(") else "semicolon at the end"
    if step == "kana":
        return "hiragana" if "぀" <= KANA.search(s).group(0) <= "ゟ" else "katakana"
    if step == "lexicon":
        han = P2.HANC.findall(s)
        return "outside the lexicon " + decile(sum(c not in ctx.lex for c in han) / len(han))
    if step == "english":
        return f"{min(len(ENGLISH_WORD.findall(s)), 5)}{'+' if len(ENGLISH_WORD.findall(s)) >= 5 else ''} English words"
    if step == "simplified":
        return next(c for c in s if c in ctx.simp)
    if step == "invade":
        return next(ctx.invade_matcher.find(s))
    return step


def reject(s, ctx):
    """-> the name of the first step that drops `s`, or None. p2clean.reject with an empty simplified set covers han_len .. code."""
    r = P2.reject(s, frozenset())
    if r:
        return r
    if KANA.search(s):
        return "kana"
    han = P2.HANC.findall(s)
    if sum(c not in ctx.lex for c in han) >= MAX_NON_LEXICON * len(han):
        return "lexicon"
    if len(ENGLISH_WORD.findall(s)) >= MIN_ENGLISH_WORDS:
        return "english"
    if not ctx.simp.isdisjoint(s):
        return "simplified"
    if ctx.invade_matcher.any(s):
        return "invade"
    return None


RAW_S = re.compile(r'<s id="[^"]*">(.*?)</s>', re.S)
RAW_TIME = re.compile(r"<time[^>]*/>")


def read_lines(path):
    if path.endswith(".zip"):
        yield from read_raw_zip(path)
        return
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace", newline="") as f:
        for line in f:
            yield line.rstrip("\n")


def read_raw_zip(path):
    """OPUS raw/zh_tw.zip -> the text of each <s> (time tags removed, stripped, XML entities unescaped), one blank line after each film id."""
    import html
    import zipfile
    with zipfile.ZipFile(path) as z:
        films = collections.defaultdict(list)
        for name in sorted(n for n in z.namelist() if n.endswith(".xml")):
            films[name.split("/")[-2]].append(name)
        for film in sorted(films, key=lambda f: films[f][0]):
            for name in films[film]:
                for s in RAW_S.findall(z.read(name).decode("utf-8", errors="replace")):
                    s = html.unescape(RAW_TIME.sub("", s)).strip()
                    if s:
                        yield s
            yield ""


def sha256_file(path):
    return P2.sha256_file(path)


def clean(lines, ctx, guard):
    """-> (kept [(id, sentence)], manifest part). `lines` is an iterable of subtitle lines."""
    funnel = collections.OrderedDict([("split", 0)] + [(s, 0) for s in STEPS])   # sentences that passed each step
    dropped = {s: collections.Counter() for s in STEPS}
    missing = collections.Counter()                        # Han characters outside the lexicon in the lexicon-dropped sentences
    guard_sets, guard_patterns, guard_rows = collections.Counter(), collections.defaultdict(set), collections.defaultdict(set)
    seen, kept = set(), []
    films, in_film = 0, False
    for n, line in enumerate(lines, 1):
        if not line.strip():
            in_film = False
            continue
        if not in_film:
            films, in_film = films + 1, True
        for k, s in enumerate(sentences(line)):
            s = strip_cue(s)
            funnel["split"] += 1
            why = reject(s, ctx)
            if why:
                dropped[why][trigger(why, s, ctx)] += 1
                if why == "lexicon":
                    missing.update(c for c in P2.HANC.findall(s) if c not in ctx.lex)
                continue
            key = P2.dedupe_key(s)
            if key in seen:
                dropped["dedupe"]["duplicate"] += 1
                continue
            labels, short = guard.hits(s)
            labels = labels | short               # short entries drop the line too (none are left at 4-character containment)
            if labels:
                seen.add(key)                     # a guard hit stays a guard hit wherever it recurs
                dropped["guard"][",".join(sorted(labels))] += 1
                for lab in labels:
                    guard_sets[lab] += 1
                for r in P2.bc.HAN.findall(s) + P2.bc.HAN.findall(P2.bc.convert(s, *guard.conv)):
                    for p in guard.matcher.find(bc.fold(r)):
                        for lab in guard.contain[p]:
                            guard_patterns[lab].add(p)
                for lab, row in guard.hit_rows(s):
                    guard_rows[lab].add(row)
                continue
            seen.add(key)
            kept.append((f"{n}" if k == 0 else f"{n}:{k}", s))
    left = funnel["split"]
    for step in STEPS:
        left -= sum(dropped[step].values())
        funnel[step] = left
    man = {"step_order": ["split"] + STEPS, "remaining_after_step": dict(funnel), "films": {"count": films},
           "dropped": {s: {"sentences": sum(c.values()), "top_triggers": c.most_common(TOP)} for s, c in dropped.items()},
           "lexicon_missing_characters_top": missing.most_common(TOP),
           "guard": {"dropped_sentences_by_label_nonexclusive": dict(sorted(guard_sets.items())),
                     "rows_hit_by_label": {l: len(r) for l, r in sorted(guard_rows.items())},
                     "distinct_patterns_hit_by_label": {l: len(p) for l, p in sorted(guard_patterns.items())},
                     "note": "rows: distinct evaluation rows (exact and containment together; a JSON word-list file counts as one row) owning a pattern "
                             "a dropped sentence contains; patterns: distinct Han runs, an extra count"},
           "kept": {"sentences": len(kept), "han_chars": sum(P2.han_count(s) for _, s in kept)}}
    return kept, man


class RowGuard(P2.Guard):
    """p2clean.Guard that also remembers which evaluation rows own each pattern, so the rows hit can be counted (`owner`: ("c" | "e", folded
    pattern) -> {(label, row id)}). A JSON word-list file counts as one row per file. Row ids are never printed."""

    def __init__(self, conv, contain_min):
        super().__init__(conv, contain_min)
        self.owner = collections.defaultdict(set)
        self.cur = None

    def _add_contain(self, label, text):
        super()._add_contain(label, text)
        for run in bc.HAN.findall(text):
            if len(run) >= P2.MIN_HAN:
                for v in {run, bc.convert(run, *self.conv)}:
                    self.owner["c", bc.fold(v)].add((label, self.cur))

    def _add_exact(self, label, text):
        super()._add_exact(label, text)
        h = "".join(bc.HAN.findall(text))
        if len(h) >= P2.MIN_HAN:
            for v in {h, "".join(bc.HAN.findall(bc.convert(h, *self.conv)))}:
                self.owner["e", bc.fold(v)].add((label, self.cur))

    def add_rows_file(self, label, path, shown):
        n = 0
        for i, line in enumerate(P2.hg.read_lines(path), 1):
            line = line.rstrip("\r")
            if not line.strip() or line.startswith("#"):
                continue
            n += 1
            self.cur = f"{shown}:{i}"
            if "|" in line:
                for fld in line.split("|"):
                    self._add_contain(label, fld)
            else:
                for fld in line.split("\t"):
                    self._add_exact(label, fld)
        self.files.append({"label": label, "path": shown, "sha256": sha256_file(path), "entries": n})

    def add_json_file(self, label, path, shown):
        self.cur = f"{shown}:json"
        super().add_json_file(label, path, shown)

    def add_holdout(self, repo):
        rows = sorted(glob.glob(os.path.join(repo, "eval", "holdout", "**", "*.txt"), recursive=True))
        _, owners, loaded, skipped = P2.hg.build_matcher(rows, self.conv)
        for pat, ids in owners.items():
            self.contain[pat].add("holdout")
            self.owner["c", pat].update(("holdout", i) for i in ids)
        for p in rows:
            for n, line in enumerate(P2.hg.read_lines(p), 1):
                f = line.split("|")
                if len(f) == 3:
                    self.cur = f"{p}:{n}"
                    self._add_contain("holdout", f[0])
            self.files.append({"label": "holdout", "path": os.path.relpath(p, repo), "sha256": sha256_file(p), "entries": None})
        self.files.append({"label": "holdout", "path": "(holdout_guard.build_matcher)", "patterns_loaded": loaded, "skipped_short": skipped})

    def hit_rows(self, s):
        """{(label, row id)} of the evaluation rows whose pattern (containment or exact) this sentence contains."""
        c = bc.convert(s, *self.conv)
        out = set()
        for r in bc.HAN.findall(s) + bc.HAN.findall(c):
            for p in self.matcher.find(bc.fold(r)):
                out |= self.owner.get(("c", p), set())
        h, hc = "".join(bc.HAN.findall(s)), "".join(bc.HAN.findall(c))
        for k in {bc.fold(h), bc.fold(hc)}:
            out |= self.owner.get(("e", k), set())
        return out


def check_labels(guard):
    """STOP unless the guard covers every set in REQUIRED_LABELS; -> the sorted label list."""
    labels = sorted({f["label"] for f in guard.files})
    print("guard labels:", " ".join(labels))
    missing = [l for l in REQUIRED_LABELS if l not in labels]
    if missing:
        raise SystemExit(f"STOP: the guard has no {', '.join(missing)} (give the row files with --extra-rows LABEL=FILE)")
    return labels


def write_tsv(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for sid, s in rows:
            f.write(f"{SOURCE}\t{sid}\t{s}\n")


def build_ctx(a):
    a.conv = bc.load_conv()               # fills bc.TRAD_SIMP_ONLY in place: it must be read after this call
    if not bc.TRAD_SIMP_ONLY:
        raise SystemExit("STOP: the simplified-only set is empty (OpenCC files not found?): the simplified filter would do nothing")
    terms, info = invade.load(a.invade)
    if a.invade_sha256 and info["sha256"] != a.invade_sha256:
        raise SystemExit(f"STOP: invade list SHA-256 {info['sha256']} is not the frozen {a.invade_sha256}")
    ctx = argparse.Namespace(lex=lexicon_chars(a.lexicon), simp=bc.TRAD_SIMP_ONLY, invade_terms=terms, invade_info=info,
                             invade_matcher=P2.s7f.Matcher(terms))
    if not ctx.lex:
        raise SystemExit(f"STOP: no characters read from the lexicon {a.lexicon}")
    return ctx


def build_guard(a):
    if a.cache:
        P2.CACHE = a.cache
    keep, P2.Guard = P2.Guard, RowGuard
    try:       # 4-character containment for every label, not p2clean's CONTAIN_MIN 8: no "short" entries remain
        guard, dups = P2.build_guard(a.repo, a.conv, P2.MIN_HAN)
    finally:
        P2.Guard = keep
    for item in a.extra_rows:
        label, _, path = item.partition("=")
        if not label or not path or not os.path.isfile(path):
            raise SystemExit("STOP: --extra-rows wants LABEL=FILE with an existing FILE")
        guard.add_rows_file(label, path, f"(extra) {label}")
    guard.build()
    check_labels(guard)
    return guard, dups


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--lexicon", default=os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"))
    ap.add_argument("--invade", required=True)
    ap.add_argument("--invade-sha256", required=True)
    ap.add_argument("--repo", default=ROOT)
    ap.add_argument("--cache")
    ap.add_argument("--extra-rows", action="append", default=[])
    ap.add_argument("--min-han", type=int, default=MIN_KEPT_HAN)
    ap.add_argument("--sample", type=int, help="print N kept sentences (with --seed): the only mode that prints text")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args(argv)
    if (a.sample is None) != (a.seed is None):
        raise SystemExit("--sample and --seed go together")
    ctx = build_ctx(a)
    guard, dups = build_guard(a)
    kept, man = clean(read_lines(a.raw), ctx, guard)
    os.makedirs(a.out, exist_ok=True)
    halves = [r for r in kept if half(r[1])]
    write_tsv(os.path.join(a.out, "subs.tsv"), kept)
    write_tsv(os.path.join(a.out, "subs-half.tsv"), halves)
    man.update({"input": {"sha256": sha256_file(a.raw), "path_basename": os.path.basename(a.raw)},
                "lexicon": {"basename": os.path.basename(a.lexicon), "sha256": sha256_file(a.lexicon), "characters": len(ctx.lex)},
                "invade": {k: ctx.invade_info[k] for k in ("commit", "sha256", "terms")},
                "outputs": {n: {"rows": len(r), "han_chars": sum(P2.han_count(s) for _, s in r), "sha256": sha256_file(os.path.join(a.out, n))}
                            for n, r in (("subs.tsv", kept), ("subs-half.tsv", halves))},
                "guard_files": guard.files, "guard_labels": sorted({f["label"] for f in guard.files}), "guard_byte_identical_copies_skipped": dups})
    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print("remaining after each step:", " ".join(f"{k} {v}" for k, v in man["remaining_after_step"].items()))
    for step, d in man["dropped"].items():
        print(f"{step}: dropped {d['sentences']} top {d['top_triggers']}")
    print("lexicon-missing characters (top):", man["lexicon_missing_characters_top"])
    print("guard:", man["guard"])
    print("kept", man["kept"], "half", man["outputs"]["subs-half.tsv"]["rows"])
    if man["kept"]["han_chars"] < a.min_han:
        print(f"STOP: {man['kept']['han_chars']} Han characters kept, fewer than {a.min_han}")
        return 1
    if a.sample is not None:
        print("--- sample (public subtitle text, for proofreading) ---")
        for sid, s in random.Random(a.seed).sample(kept, min(a.sample, len(kept))):
            print(f"{sid}\t{s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
