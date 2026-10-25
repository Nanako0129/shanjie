#!/usr/bin/env python3
"""OPUS OpenSubtitles zh_tw lines -> sentences for the colloquial counts and the tinylm corpus (docs/contracts/sj-lm-7.md section 2.7).
Brought from the SUB-0 branch (origin/exp/sub0-subtitles, experiments/sub0-subtitles/clean_subs.py); the additions are the raw zip reader and the film count (no cap) below.

  clean_subs.py --raw raw/zh_tw.zip --out DIR --lexicon data/lexicon/mcbpmf-data.txt --invade INVADE_CLONE
                --invade-sha256 HEX [--repo DIR] [--cache DIR] [--extra-rows LABEL=FILE ...] [--min-han 4000000] [--sample N --seed S]

Order, after p2clean.py's split / filters / Guard (copied from exp/p2-data 4f7504bf):
  split, cue    p2clean.split_sentences (。！？!? and line breaks, not commas), then a leading '-' / opening quote and the subtitle marks
                * # ♪ ♫ § are stripped
  han_len, han_ratio, url, digits, code   p2clean.reject (4..40 Han characters, >= 70% Han, URL / e-mail / handle, digit runs and phones, code)
  kana          a kana character (hiragana, katakana, phonetic extensions, half-width katakana)
  lexicon       >= 30% of the Han characters are not a character of --lexicon's words
  english       >= 2 English words, or a single lowercase Latin letter standing alone (法式拼寫n); "OK" and T恤, X光, B型 stay
  simplified    p2clean's TRAD_SIMP_ONLY character
  mainland      a mainland phrase written with a character traditional Chinese also has (什么, 這里, 最后, 對于, 愿意: MAINLAND), found on
                the NFKC text with whitespace removed; the simplified step lets these characters through on purpose (公里, 皇后). A phrase
                that is a word of --lexicon is not used (MAINLAND leaves out the ones the shipped lexicon has: 村里, 水里, 聯系, 市里, 伙計); a match is not a hit when a longer lexicon word holds it (二里頭文化)
                or a lexicon word with a shared character and no phrase in it crosses it (皇后來了, 汽車里程, 若干活動, 海關系統)
  residue       a bracket ( ()（）[]【】 ), a "︰" speaker label, a Cantonese-only character (喺搵咗嘅冇佢啲嚟咁乜嘢哋嗰) or the OCR split 黑占
  invade        a term (two characters or more) of the invade list, the S7f generated-sentence rule (invade.load + Matcher.any), plus the
                terms of --mainland-terms (mainland wording in traditional characters, e.g. OpenCC TWPhrases keys PTT seldom uses)
  dedupe        NFKC + whitespace removed + case folded
  guard         p2clean.Guard over the evaluation / tuning / judging sets and the holdout (4-character containment for the holdout)
Films are counted, not capped (contract 2.7, maintainer 2026-10-25: no per-film cap). A film is a run of non-empty lines between blank lines; the
input is OPUS raw/zh_tw.zip (one XML file per subtitle, OpenSubtitles/raw/zh_tw/<year>/<film id>/<file>.xml), read as the text of every <s> with its
<time> tags removed, grouped by film id (films in the order of their first path, a film's files in path order), one blank line after each film. The mono file (mono/zh_tw.txt.gz) has no blank line at all (measured
2026-10-25: 1,456,479 lines), so it reads as one film: the run STOPs when the input shows fewer than two films. Plain-text input with blank lines
between films is still accepted (tests).
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
STEPS = ["han_len", "han_ratio", "url", "digits", "code", "kana", "lexicon", "english", "simplified", "mainland", "residue", "invade", "dedupe", "guard"]
# Contract 2.7 (maintainer 2026-10-25): the first 300-sentence proofread (seed 20261025) had 20 residues (6.7%): 18 machine-converted lines
# with these shared characters (么 里 后 于 愿) in their mainland use, a lyric marker and an English word. Phrases, not characters (except 万 and a lone 么, added after the second proofread), so 公里, 皇后,
# 姓于 stay; the lexicon decides word boundaries (mainland_hit), and 能干 and 下里 are left out (不能干涉, 下里巴人). Known limit (code review
# of #299): a guard word that starts or ends with the shared character also covers a real mainland use (家里人 through 里人, 最后座 through
# 后座); structurally it is the same as 皇后來了, so the lexicon cannot tell them apart. The review step (review_subs.py) sees these lines.
MAINLAND = tuple("""什么 怎么 那么 這么 多么 要么 甚么 這里 那里 哪里 屋里 家里 心里 手里 眼里 夜里 城里 店里 房里 包里 車里 袋里 子里
    室里 院里 樓里 獄里 營里 洞里 宮里 廳里 間里 私下里 里面 里頭 里邊 最后 然后 以后 之后 后來 后面 后悔 隨后 背后 前后 后天 后果 后退 落后
    稍后 今后 此后 事后 后頭 后邊 對于 由于 關于 于是 終于 等于 至于 屬于 位于 善于 在于 過于 敢于 基于 用于 處于 向于 便于 樂于 歸于 急于
    忙于 免于 出于 鑒于 限于 勇于 愿意 心愿 愿望 自愿 情愿 寧愿 但愿 志愿 祝愿 如愿 干嘛 干活 干淨 干杯 干脆 干掉 實干 准備 標准 准確 准時
    關系 范圍 戰斗 奮斗 斗爭 放松 輕松 采取 采訪 干了 万 么""".split())   # 干了, 万, 么: the second proofread (seed 20261026)
SHARED = frozenset("么里后于愿干准系范斗松采伙万")   # the shared characters of MAINLAND
CUE_LEAD = re.compile(r"^(?:[-–—－\s]|[「『“\"])+")
MARKS = re.compile("[*#♪♫§]")                  # subtitle marks (lyrics, notes) seen in the second proofread; never part of the text
LONE_LETTER = re.compile(r"(?<![A-Za-z])[a-z](?![A-Za-z])")   # lowercase: OCR noise (拼寫n); uppercase stays (T恤, X光, B型, 維他命C)
# Contract 2.7 (maintainer 2026-10-25): from the residues of the fifth proofread (seeds 20261030, 20261031): a bracket anywhere (sound notes,
# translator notes), a "︰" speaker label, Cantonese-only characters, and the OCR split 黑占 (點). No quote-pairing rule: strip_cue drops a
# leading 「 and split_sentences cuts inside quotes, so a count of 「 and 」 here flags ordinary dialogue (code review of #304). 唔 is not in
# the Cantonese set: Mandarin subtitles use it as an interjection (唔，好吧).
RESIDUE = (("bracket", re.compile(r"[()（）\[\]【】]")), ("speaker label", re.compile("︰")),
           ("cantonese", re.compile("[喺搵咗嘅冇佢啲嚟咁乜嘢哋嗰]")), ("ocr 黑占", re.compile("黑占")))
QUOTE_PAIRS = (("「", "」"), ("『", "』"), ("“", "”"), ('"', '"'))


def sentences(line):
    """p2clean.split_sentences of each speaker's part of one subtitle line (two-speaker lines are cut at the inner dash before tidy() removes the
    spaces that show it); the ids' running index k counts across the parts."""
    return [x for part in CUE_SPLIT.split(line) for x in P2.split_sentences(part)]


def strip_cue(s):
    """A subtitle cue starts with '-' (dialogue dash) and may open a quote; a closing quote is dropped only when its opener is not in the sentence."""
    s = CUE_LEAD.sub("", MARKS.sub("", s))
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


def lexicon_words(path):
    """The all-Han words of two characters or more of a McBopomofo-format lexicon file (same lines as lexicon_chars)."""
    out = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.split()
            if line[:1] not in ("#", "_") and len(p) == 3 and len(p[1]) >= 2 and len(P2.HANC.findall(p[1])) == len(p[1]):
                out.add(p[1])
    return out


def mainland_rules(words):
    """-> (regex of the MAINLAND phrases that are not lexicon words, guard words, longest word length). Guard words are lexicon words with a
    shared character and no MAINLAND phrase inside (皇后, 里程, 若干, 系統): where one crosses a match, the match is the traditional reading."""
    rx = re.compile("|".join(map(re.escape, sorted((m for m in MAINLAND if m not in words), key=len, reverse=True))))
    guard = frozenset(w for w in words if not SHARED.isdisjoint(w) and not any(m in w for m in MAINLAND))
    return rx, guard, max(map(len, words), default=2)


def mainland_hit(s, ctx):
    """-> the first MAINLAND phrase used the mainland way in s, or None (rules in the docstring at the top)."""
    t = re.sub(r"\s", "", unicodedata.normalize("NFKC", s))
    for m in ctx.mainland_rx.finditer(t):
        i, j = m.span()
        spans = ((a, b) for a in range(max(0, j - ctx.longest), j) for b in range(max(a + 2, i + 1), min(len(t), a + ctx.longest) + 1))
        if not any((a <= i and b >= j and b - a > j - i and t[a:b] in ctx.words) or ((a < i or b > j) and t[a:b] in ctx.mainland_guard)
                   for a, b in spans):
            return m.group(0)
    return None


def residue_hit(s):
    """-> the RESIDUE rule that drops s, or None."""
    return next((name for name, rx in RESIDUE if rx.search(s)), None)


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
    if step == "english" and len(ENGLISH_WORD.findall(s)) < MIN_ENGLISH_WORDS:
        return "a lone lowercase letter"
    if step == "english":
        return f"{min(len(ENGLISH_WORD.findall(s)), 5)}{'+' if len(ENGLISH_WORD.findall(s)) >= 5 else ''} English words"
    if step == "simplified":
        return next(c for c in s if c in ctx.simp)
    if step == "mainland":
        return mainland_hit(s, ctx)
    if step == "residue":
        return residue_hit(s)
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
    if len(ENGLISH_WORD.findall(s)) >= MIN_ENGLISH_WORDS or LONE_LETTER.search(s):
        return "english"
    if not ctx.simp.isdisjoint(s):
        return "simplified"
    if mainland_hit(s, ctx):
        return "mainland"
    if residue_hit(s):
        return "residue"
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
        for names_of_film in films.values():              # names are sorted, so films come in the order of their first path
            for name in names_of_film:
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
    if a.mainland_terms:          # one term per line, two characters or more; '#' lines are comments
        extra = {l.strip() for l in open(a.mainland_terms, encoding="utf-8") if l.strip() and not l.startswith("#")}
        short = sorted(t for t in extra if len(t) < 2)
        if short:
            raise SystemExit(f"STOP: {a.mainland_terms}: terms shorter than two characters: {short[:5]}")
        info = dict(info, mainland_terms={"basename": os.path.basename(a.mainland_terms), "sha256": sha256_file(a.mainland_terms),
                                          "terms": len(extra - set(terms))})
        terms = set(terms) | extra
    words = lexicon_words(a.lexicon)
    rx, guard, longest = mainland_rules(words)
    ctx = argparse.Namespace(lex=lexicon_chars(a.lexicon), simp=bc.TRAD_SIMP_ONLY, invade_terms=terms, invade_info=info,
                             invade_matcher=P2.s7f.Matcher(terms), words=words, mainland_rx=rx, mainland_guard=guard, longest=longest)
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
    ap.add_argument("--mainland-terms", help="extra invade terms, one per line (mainland wording in traditional characters)")
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
                "invade": {k: ctx.invade_info[k] for k in ("commit", "sha256", "terms", "mainland_terms") if k in ctx.invade_info},
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
    if man["films"]["count"] < 2:
        print("STOP: the input shows fewer than two films (no blank line between films: the mono file? use raw/zh_tw.zip)")
        return 1
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
