#!/usr/bin/env python3
"""Builds data/lexicon/english-next.tsv, the Chinese words that follow an English word (docs/contracts/english-next.md section 1, revision 3).

Sources (each an optional path; the raw input, read BEFORE the cleaning that drops English), weight, spread unit:
  --wiki DUMP.bz2        first --wiki-limit (200,000) articles, x1, article. build_counts.article_sentences (entity unescape, templates, markup,
                         sentence split, convert without TWPhrases as SJ-LM-7 counts), so the Han-only step of article_runs is skipped.
  --colloquial FILE      colloquial-train.txt (build_tune.py), one sentence per line, x5, sentence.
  --g0v FILE             the g0v #rand0m source file as UTF-8 text, x5, message. Either one message per line, or (when the first non-empty line
                         has a tab) g0v_clean.py's `ts<TAB>sentence` lines, where consecutive lines with the same ts are one message (one
                         unit, one spread count) and the ts column is not text.
  --ptt DIR              <board>/<article>.html as ptt_clean parses it (body, merged pushes; no p2clean). Pushes x5, bodies x1, unit = board.
                         Only articles with sha256(id)[0] % 8 != 0 (7/8) count; the rest is the measurement bucket of section 3.
  --subs FILE           OPUS raw/zh_tw.zip (read by clean_subs.read_lines: one blank line after each film id; plain text with blank lines
                         between films also works), x1, film = blank-line segment. Only clean_subs.sentences, strip_cue, URL removal, and
                         dropping sentences with >= MIN_ENGLISH_WORDS English words (a parallel line is a translation). Fewer than two films
                         is a STOP (the mono file has no blank line).
Quoted PTT lines and signatures go in ptt_clean.body; URLs are cut out of every source and act as a boundary.

An English word e: 2-32 ASCII letters, not touching a letter or digit, lower-cased. A Han run after it (at most one half-width space) is one
"e before Chinese" event; w is the first word of the run by longest match in the lexicon files. All thresholds count UNWEIGHTED events.

Rows (tab separated, an e with no w row left gets no E row either; byte-sorted under `#` header lines with every input's SHA-256, the lexicon files' and the tool version):
  E  e  N(e)          weighted events
  W  e  w  N(e->w)    weighted events
Admission: events(e) >= 20, and (PTT boards with an event >= 5, or units of the other sources with an event >= 20). An e that passes through
PTT boards alone, is the account of any push in the snapshot (lower-cased) and has events < 100 is dropped. w needs unweighted events >= 3, the
top 20 per e by weighted count then bytes. Over 2,000,000 bytes the w cut goes 5, 10, 20 and the first size that fits is written.

  python3 tools/build_english_next.py --lexicon FILE... --out FILE [--wiki DUMP] [--colloquial FILE] [--g0v FILE] [--ptt DIR] [--subs FILE] [--jobs N]
                                      [--report-e WORD[:W]]...
--report-e WORD (repeatable) prints after counting, whether or not WORD is in the table: its unweighted events, weighted N(e), PTT boards,
units per other source, blocked by the account rule or not, in the table or not, and its top w by weighted count with unweighted and weighted
counts (WORD:W adds W to the list). Subtitle zips are parsed in the workers (each opens the zip and reads its films' members), not by the feeder.
"""
import argparse
import collections
import glob
import gzip
import hashlib
import itertools
import multiprocessing as mp
import os
import html
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("experiments/sj-lm-7", "experiments/s2", "tools", "reference/proto"):
    sys.path.insert(0, os.path.join(ROOT, *_p.split("/")))
import ptt_clean as PC  # noqa: E402
import clean_subs as CS  # noqa: E402

VERSION = "2"
MAX_BYTES = 2_000_000            # read as 10^6-based, like build_digits.py
MIN_EVENTS = 20                  # events of e, all sources
MIN_BOARDS, MIN_UNITS = 5, 20    # spread: PTT boards, or units of the other sources
MIN_W, MAX_W = 3, 20             # unweighted e->w events and w per e
W_STEPS = (MIN_W, 5, 10, 20)     # the size fallback
ACCOUNT_N = 100                  # an account-named e that passes through PTT boards alone needs this many events
WIKI_LIMIT = 200_000
WEIGHTS = {"wiki": 1, "colloquial": 5, "g0v": 5, "ptt-push": 5, "ptt-body": 1, "subs": 1}
BATCH = 256
HAN = "㐀-䶿一-鿿"
PAIR = re.compile(rf"(?<![A-Za-z0-9])([A-Za-z]{{2,32}})(?![A-Za-z0-9]) ?([{HAN}]+)")
URL = re.compile(r"https?://\S+|www\.\S+")


def in_build_bucket(article_id):
    return hashlib.sha256(article_id.encode("utf-8")).digest()[0] % 8 != 0


def load_words(files):
    """The words of lexicon files: `reading word score` (space) or `reading<TAB>word<TAB>score...`; `#` and `_` (control) lines skipped."""
    words = set()
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(("#", "_")) or not line.strip():
                    continue
                p = line.rstrip("\n").split("\t") if "\t" in line else line.split()
                if len(p) >= 2:
                    words.add(p[1])
    return words


def first_word(run, words, maxlen):
    for n in range(min(len(run), maxlen), 0, -1):
        if run[:n] in words:
            return run[:n]
    return None


def pairs(text, words, maxlen):
    """[(e, w or None)] of one text."""
    out = []
    for chunk in URL.sub("\n", text).split("\n"):
        for m in PAIR.finditer(chunk):
            out.append((m.group(1).lower(), first_word(m.group(2), words, maxlen)))
    return out


def sub_sentences(line):
    """The subtitle steps of section 1: split, cue, URL, parallel line (>= MIN_ENGLISH_WORDS English words) dropped. Nothing else."""
    out = []
    for s in CS.sentences(line):
        s = CS.strip_cue(s)
        if len(CS.ENGLISH_WORD.findall(URL.sub(" ", s))) < CS.MIN_ENGLISH_WORDS:
            out.append(s)
    return out


class Acc:
    """Counts of a stretch of units: events and weighted counts per e and (e, w); per e the PTT boards and the units of the other sources."""

    def __init__(self):
        self.ev, self.wev = collections.Counter(), collections.Counter()
        self.wt_e, self.wt_w = collections.Counter(), collections.Counter()
        self.boards = collections.defaultdict(set)
        self.units = collections.defaultdict(collections.Counter)   # source -> e -> units with an event
        self.accounts, self.snap = set(), []
        self.by_source = collections.Counter()                      # events per source (report)
        self.films = 0

    def unit(self, source, texts, words, maxlen, board=None, weight=None):
        """One unit (all its texts); `board` for PTT (the spread unit), else the unit counts once per e it has."""
        wgt = WEIGHTS[source] if weight is None else weight
        seen = set()
        for t in texts:
            for e, w in pairs(t, words, maxlen):
                self.ev[e] += 1
                self.wt_e[e] += wgt
                self.by_source[source] += 1
                seen.add(e)
                if w:
                    self.wev[(e, w)] += 1
                    self.wt_w[(e, w)] += wgt
        for e in seen:
            if board is not None:
                self.boards[e].add(board)
            else:
                self.units[source][e] += 1

    def merge(self, o):
        for name in ("ev", "wev", "wt_e", "wt_w", "by_source"):
            getattr(self, name).update(getattr(o, name))
        for e, b in o.boards.items():
            self.boards[e] |= b
        for s, c in o.units.items():
            self.units[s].update(c)
        self.accounts |= o.accounts
        self.snap += o.snap
        self.films += o.films


_G = {}


def _init(lexicon):
    _G["words"] = load_words(lexicon)
    _G["maxlen"] = max(map(len, _G["words"]))


def wiki_sentences(raw):
    """A wiki article as converted sentences (the conversion tables load once per process, on first use)."""
    import build_counts as bc
    if "conv" not in _G:
        _G["conv"] = bc.load_conv()
    return list(bc.article_sentences(raw, _G["conv"], trad_tw_phrases=False))


def work(batch):
    """One batch -> Acc. batch = (kind, items)."""
    kind, items = batch
    a, words, maxlen = Acc(), _G["words"], _G["maxlen"]
    if kind == "wiki":
        for raw in items:
            a.unit("wiki", _G.get("sentences", wiki_sentences)(raw), words, maxlen)
    elif kind == "colloquial":
        for line in items:
            a.unit(kind, [line], words, maxlen)
    elif kind == "g0v":
        for msg in items:
            a.unit(kind, msg, words, maxlen)
    elif kind == "subzip":
        path, films = items
        z = _G.setdefault("zips", {}).get(path) or _G["zips"].setdefault(path, zipfile.ZipFile(path))
        for names in films:
            film = [s for s in raw_film_lines(z, names) if s.strip()]
            if film:
                a.unit("subs", [s for line in film for s in sub_sentences(line)], words, maxlen)
                a.films += 1
    elif kind == "subs":
        for film in items:
            a.unit("subs", [s for line in film for s in sub_sentences(line)], words, maxlen)
            a.films += 1
    elif kind == "ptt":
        for path in items:
            raw = open(path, "rb").read()
            page = raw.decode("utf-8", errors="replace")
            board, aid = os.path.basename(os.path.dirname(path)), os.path.basename(path)[:-5]
            a.snap.append(f"{board}/{aid}.html\0{hashlib.sha256(raw).hexdigest()}\n")
            a.accounts |= {u.strip().lower() for u, _, _ in PC.PUSH.findall(page)}
            if in_build_bucket(aid):
                a.unit("ptt-body", [PC.body(page)], words, maxlen, board=board)
                a.unit("ptt-push", PC.merged_pushes(page), words, maxlen, board=board)
    return a


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def lines_of(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace", newline="\n") as f:   # a line ends at "\n" only, a lone "\r" is not a break
        for line in f:
            yield line.rstrip("\n").removesuffix("\r")


def raw_film_lines(z, names):
    """The lines of one film of the OPUS raw zip, as clean_subs.read_raw_zip yields them (without the blank line after the film)."""
    for name in names:
        for s in CS.RAW_S.findall(z.read(name).decode("utf-8", errors="replace")):
            s = html.unescape(CS.RAW_TIME.sub("", s)).strip()
            if s:
                yield s


def raw_zip_films(path):
    """[[member names]] per film id, in the order clean_subs.read_raw_zip reads them."""
    with zipfile.ZipFile(path) as z:
        films = collections.defaultdict(list)
        for name in sorted(n for n in z.namelist() if n.endswith(".xml")):
            films[name.split("/")[-2]].append(name)
    return list(films.values())


def g0v_messages(path):
    """Messages of a g0v file as lists of texts: one per line, or by `ts<TAB>sentence` with the consecutive lines of one ts as one message."""
    lines = (l for l in lines_of(path) if l.strip())
    first = next(lines, None)
    if first is None:
        return
    lines = itertools.chain([first], lines)
    if "\t" not in first:
        for l in lines:
            yield [l]
        return
    for _, grp in itertools.groupby((l.split("\t", 1) for l in lines), key=lambda p: p[0]):
        yield [p[1] for p in grp if len(p) > 1]


def films_of(path):
    """Subtitle lines grouped into films: the runs of non-empty lines between blank lines (clean_subs.clean). clean_subs.read_lines reads the
    OPUS raw zip (one blank line after each film id) as well as plain text."""
    film = []
    for line in CS.read_lines(path):
        if line.strip():
            film.append(line)
        elif film:
            yield film
            film = []
    if film:
        yield film


def batches(src, size=BATCH):
    """The work of every given source as (kind, items) batches."""
    def chunks(kind, it, n=size):
        it = iter(it)
        while chunk := list(itertools.islice(it, n)):
            yield kind, chunk
    if src.get("wiki"):
        import build_counts as bc
        yield from chunks("wiki", bc.articles(src.get("wiki_limit", WIKI_LIMIT), src["wiki"]), 64)
    if src.get("colloquial"):
        yield from chunks("colloquial", (l for l in lines_of(src["colloquial"]) if l.strip()), 2000)
    if src.get("g0v"):
        yield from chunks("g0v", g0v_messages(src["g0v"]), 2000)
    if src.get("ptt"):
        yield from chunks("ptt", sorted(glob.glob(os.path.join(src["ptt"], "*", "*.html"))), 64)
    if src.get("subs") and src["subs"].endswith(".zip"):
        films = raw_zip_films(src["subs"])
        for i in range(0, len(films), 16):
            yield "subzip", (src["subs"], films[i:i + 16])
    elif src.get("subs"):
        yield from chunks("subs", films_of(src["subs"]), 16)


def build(lexicon, src, jobs=1, min_events=MIN_EVENTS, min_boards=MIN_BOARDS, min_units=MIN_UNITS, min_w=MIN_W, account_n=ACCOUNT_N, max_bytes=MAX_BYTES, report_e=()):
    """(text, report). The size fallback raises the e->w cut along W_STEPS from `min_w` on; None as the text when no step fits."""
    total = Acc()
    if jobs > 1:
        with mp.Pool(jobs, _init, (lexicon,)) as pool:
            for a in pool.imap_unordered(work, batches(src)):
                total.merge(a)
    else:
        _init(lexicon)
        for b in batches(src):
            total.merge(work(b))
    if src.get("subs") and total.films < 2:   # contract 1: fewer than two films means the wrong input (the mono file has no blank line)
        raise SystemExit("STOP: the subtitle input shows fewer than two films (use the OPUS raw/zh_tw.zip)")
    keep, blocked, why = [], 0, set()
    for e, n in total.ev.items():
        boards, units = len(total.boards.get(e, ())), sum(c[e] for c in total.units.values())
        if n < min_events or not (boards >= min_boards or units >= min_units):
            continue
        if units < min_units and e in total.accounts and n < account_n:
            blocked += 1
            why.add(e)
            continue
        keep.append(e)
    keep.sort()
    by_e = collections.defaultdict(list)
    for (e, w), n in total.wev.items():
        by_e[e].append((w, n, total.wt_w[(e, w)]))
    head = [f"# english-next v{VERSION}, tools/build_english_next.py"]
    for name, path in (("wiki", src.get("wiki")), ("colloquial", src.get("colloquial")), ("g0v", src.get("g0v")), ("subs", src.get("subs"))):
        if path:
            head.append(f"# source {name} {os.path.basename(path)} sha256 {sha256_file(path)}")
    if src.get("ptt"):
        snap = "".join(sorted(total.snap))
        head.append(f"# source ptt sha256 {hashlib.sha256(snap.encode()).hexdigest()} articles {len(total.snap)}")
    head += [f"# lexicon {os.path.basename(f)} sha256 {sha256_file(f)}" for f in lexicon]
    steps = [min_w] + [s for s in W_STEPS[1:] if s > min_w]
    kept, lines = set(keep), []
    for spec in report_e:
        e, _, force = spec.partition(":")
        e = e.lower()
        tops = sorted(((w, total.wev[(e, w)], total.wt_w[(e, w)]) for (ee, w) in total.wev if ee == e), key=lambda x: (-x[2], x[0].encode()))
        shown = tops[:MAX_W] + [t for t in tops[MAX_W:] if t[0] == force]
        lines.append(f"{e}\tevents {total.ev[e]}\tN {total.wt_e[e]}\tptt_boards {len(total.boards.get(e, ()))}"
                     f"\tunits {dict((k, c[e]) for k, c in total.units.items())}\tblocked_by_account {int(e in why)}\tin_table {int(e in kept)}")
        lines += [f"{e}\t{w}\tevents {n}\tN {wt}" for w, n, wt in shown]
    report = {"report_e": lines, "e": len(keep), "blocked_by_account": blocked, "events_by_source": dict(total.by_source), "films": total.films}
    for k in steps:
        rows, n_e = [], 0
        for e in keep:
            top = sorted(((w, wt) for w, n, wt in by_e[e] if n >= k), key=lambda x: (-x[1], x[0].encode()))[:MAX_W]
            if top:                                        # an e with no surviving w has nothing to predict: no E row, no bytes
                rows.append(f"E\t{e}\t{total.wt_e[e]}\n")
                rows += [f"W\t{e}\t{w}\t{wt}\n" for w, wt in top]
                n_e += 1
        text = "".join(f"{h}\n" for h in head) + f"# min_w {k}\n" + "".join(sorted(rows, key=lambda r: r.encode()))
        if len(text.encode()) <= max_bytes:
            return text, {**report, "e_written": n_e, "min_w": k, "bytes": len(text.encode()), "rows_w": sum(r.startswith("W") for r in rows)}
    return None, {**report, "min_w": steps[-1]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lexicon", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--wiki")
    ap.add_argument("--wiki-limit", type=int, default=WIKI_LIMIT)
    ap.add_argument("--colloquial")
    ap.add_argument("--g0v")
    ap.add_argument("--ptt")
    ap.add_argument("--subs")
    ap.add_argument("--report-e", action="append", default=[])
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    a = ap.parse_args()
    src = {k: getattr(a, k) for k in ("wiki", "colloquial", "g0v", "ptt", "subs")}
    if not any(src.values()):
        sys.exit("error: give at least one source")
    src["wiki_limit"] = a.wiki_limit
    text, report = build(a.lexicon, src, a.jobs, report_e=a.report_e)
    print("\n".join(report.pop("report_e")))
    print(report)
    if text is None:
        sys.exit("error: the table is over 2 MB at every e->w cut; stop and report")
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


if __name__ == "__main__":
    main()
