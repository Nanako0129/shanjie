"""Review table for main: candidate strings + news check + PTT long-term -> a proposed route per string (docs/contracts/youtube-events.md part A).
Strings already in a lexicon layer (base mcbpmf-data.txt, overlay-add.tsv, names-add.tsv, slang-add.tsv, packs/acg-add.tsv, packs/it-add.tsv) are left out.
Route: names (string is in the names hint list) > net-slang (long_term = 1) > hot-word (news pass, not long-term) > drop.
With --readings (string<TAB>reading, syllables separated by spaces) and --counts (word<TAB>PTT article count) the same-reading lexicon words and
their counts go into `same_reading`; the net-slang same-reading rule itself is applied in main's review, not here.
Usage: python3 route.py CANDIDATES.tsv NEWS.tsv PTT.tsv OUT.tsv [--names-hint FILE] [--readings FILE] [--counts FILE] [--lexicon-dir DIR] [--packs-dir DIR]"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIELDS = ("string", "route", "events", "dates", "news_headlines", "news_outlets", "ptt_articles", "ptt_boards", "ptt_span_days", "same_reading")
MAX_EVENTS = 3


def reading_key(r):
    return "-".join(r.replace("-", " ").split())


def lexicon(lex_dir, packs_dir):
    """-> (set of words, {reading: [words]}). Column layouts: base `reading word score`, overlay/names/pack `reading word score tag`, slang `word reading source`."""
    words, by_reading = set(), collections.defaultdict(list)

    def add(w, r):
        words.add(w)
        by_reading[reading_key(r)].append(w)

    for path, sep, word_col in ((os.path.join(lex_dir, "mcbpmf-data.txt"), " ", 1), (os.path.join(lex_dir, "overlay-add.tsv"), "\t", 1),
                                (os.path.join(lex_dir, "names-add.tsv"), "\t", 1), (os.path.join(packs_dir, "acg-add.tsv"), "\t", 1),
                                (os.path.join(packs_dir, "it-add.tsv"), "\t", 1), (os.path.join(lex_dir, "slang-add.tsv"), "\t", 0)):
        with open(path, encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split(sep)
                if line.startswith("#") or len(p) < 2:
                    continue
                add(p[word_col], p[1 - word_col])
    return words, by_reading


def column(path, col=0):
    if not path:
        return []
    with open(path, encoding="utf-8") as f:
        return [l.rstrip("\n").split("\t")[col] for l in f if l.strip() and not l.startswith("#")]


def route_of(in_names, news_pass, long_term):
    return "names" if in_names else "net-slang" if long_term else "hot-word" if news_pass else "drop"


def build(cands, news, ptt, lex_words, by_reading=None, names=(), readings=None, counts=None):
    """cands/news/ptt: lists of dicts as read from the tools' TSVs. -> list of output dicts, one per string not in the lexicon."""
    events = collections.defaultdict(list)
    for r in cands:
        if (r["event"], r["date"]) not in events[r["string"]]:
            events[r["string"]].append((r["event"], r["date"]))
    n = collections.defaultdict(lambda: (0, 0, False))      # best headlines, outlets over the dates, any pass
    for r in news:
        h, o = int(r["headlines"] or 0), int(r["outlets"] or 0)
        b = n[r["string"]]
        n[r["string"]] = (max(b[0], h), max(b[1], o), b[2] or r["pass"] == "1")
    p = {r["string"]: r for r in ptt}
    names, readings, counts = set(names), readings or {}, counts or {}
    out = []
    for s in sorted(events):
        if s in lex_words:
            continue
        pr = p.get(s, {})
        same = ""
        if by_reading is not None and counts and s in readings:
            same = ";".join(f"{w}:{counts.get(w, 0)}" for w in by_reading.get(reading_key(readings[s]), ()) if w != s)
        out.append({"string": s, "route": route_of(s in names, n[s][2], pr.get("long_term") == "1"),
                    "events": " | ".join(e for e, _ in events[s][:MAX_EVENTS]), "dates": ";".join(sorted({d for _, d in events[s]})),
                    "news_headlines": n[s][0], "news_outlets": n[s][1], "ptt_articles": pr.get("articles", ""), "ptt_boards": pr.get("boards", ""),
                    "ptt_span_days": pr.get("span_days", ""), "same_reading": same})
    return out


def main(argv):
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument("candidates"), a.add_argument("news"), a.add_argument("ptt"), a.add_argument("out")
    a.add_argument("--names-hint"), a.add_argument("--readings"), a.add_argument("--counts")
    a.add_argument("--lexicon-dir", default=os.path.join(ROOT, "data", "lexicon")), a.add_argument("--packs-dir", default=os.path.join(ROOT, "data", "packs"))
    x = a.parse_args(argv)
    words, by_reading = lexicon(x.lexicon_dir, x.packs_dir)
    rows = build(common.read_tsv(x.candidates), common.read_tsv(x.news), common.read_tsv(x.ptt), words, by_reading, column(x.names_hint),
                 dict(zip(column(x.readings), column(x.readings, 1))) if x.readings else None,
                 {w: int(c) for w, c in zip(column(x.counts), column(x.counts, 1))} if x.counts else None)
    common.write_tsv(x.out, FIELDS, rows)
    print(dict(collections.Counter(r["route"] for r in rows)), "(strings already in the lexicon are left out)")


if __name__ == "__main__":
    main(sys.argv[1:])
