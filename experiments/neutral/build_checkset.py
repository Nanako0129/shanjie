"""experiments/neutral/build_checkset.py: builds eval/dev/neutral/words.txt (docs/contracts/neutral-tolerance.md §3). Run from the repo root.
Input: ~/.cache/shanjie/neutral/nt-counts.tsv (count_usage.py --include-known on the 2026-10-25 PTT snapshot),
data/lexicon/sandhi-add.tsv (neutral rows), data/lexicon/mcbpmf-data.txt, ~/.cache/shanjie/sources/concised/concised-readings.tsv.
Top 300 by articles (ties: code point). When a word has several neutral rows, the reading is the one closest to how Taiwan types it:
(1) most syllables whose rime matches the concised dictionary's reading of the word; (2) most syllables whose rime matches each
character's majority rime across concised headwords; (3) highest sum of the base's single-character scores for those rimes; (4) code point."""
import collections, os
rime = lambda s: s.rstrip("ˊˇˋ˙")
con = {}
char_rimes = collections.defaultdict(collections.Counter)
for l in open(os.path.expanduser("~/.cache/shanjie/sources/concised/concised-readings.tsv"), encoding="utf-8"):
    if l.startswith("#"):
        continue
    w, z = l.rstrip("\n").split("\t")[:2]
    syl = [(s[1:] + "˙") if s.startswith("˙") else s for s in z.split()]
    if w not in con:
        con[w] = syl
        if len(syl) == len(w) and len(w) > 1:
            for c, s in zip(w, syl):
                char_rimes[c][rime(s)] += 1
single = collections.defaultdict(dict)
for l in open("data/lexicon/mcbpmf-data.txt", encoding="utf-8"):
    p = l.split()
    if l.startswith("#") or len(p) != 3 or len(p[1]) != 1:
        continue
    single[p[1]][p[0]] = max(float(p[2]), single[p[1]].get(p[0], -99.0))


def key(w, r):
    syl = r.split("-")
    c = con.get(w)
    m1 = sum(rime(a) == rime(b) for a, b in zip(syl, c)) if c and len(c) == len(syl) else 0
    m2 = sum(bool(char_rimes[ch]) and char_rimes[ch].most_common(1)[0][0] == rime(s) for ch, s in zip(w, syl))
    tot = sum(max([v for k, v in single[ch].items() if rime(k) == rime(s)] or [-99.0]) for ch, s in zip(w, syl))
    return (-m1, -m2, -tot, r)


cnt = []
for l in open(os.path.expanduser("~/.cache/shanjie/neutral/nt-counts.tsv"), encoding="utf-8"):
    if not l.startswith("#"):
        w, a, _ = l.rstrip("\n").split("\t")
        cnt.append((w, int(a)))
neu = collections.defaultdict(list)
for l in open("data/lexicon/sandhi-add.tsv", encoding="utf-8"):
    r, w, _, t = l.rstrip("\n").split("\t")
    if t == "neutral":
        neu[w].append(r)
cnt.sort(key=lambda t: (-t[1], t[0]))
rows = [(w, sorted(neu[w], key=lambda r: key(w, r))[0]) for w, _ in cnt[:300]]
os.makedirs("eval/dev/neutral", exist_ok=True)
with open("eval/dev/neutral/words.txt", "w", encoding="utf-8", newline="\n") as f:
    f.writelines(f"|{w}|{r.replace('-', ' ')}\n" for w, r in rows)
print([(w, r) for w, r in rows if len(neu[w]) > 1])
