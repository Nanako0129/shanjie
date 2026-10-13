"""Typo-tolerant input, offline measurement (docs/contracts/fuzzy-research.md).

Wraps reference/proto/lm.py: the exact decoder is lm.decode untouched; `decode_tol` is the same beam search with extra
lattice entries from tolerant readings (at most one tolerant syllable per word), each scored `word score - delta`.
Kinds: tone (tone mark ignored), fuzzy (paired symbols merged), adjacent (one symbol replaced by a row neighbour key).
Gates: G0 tolerant only when the exact input fails, G1 exact top 5 + up to 4 tolerant sentences + the rest, G2 tolerant always.
"""
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (os.path.join(ROOT, "reference", "proto"), os.path.join(ROOT, "tools")):
    sys.path.insert(0, _p)
import ime  # noqa: E402
import lm as L  # noqa: E402
from eval import lenient  # noqa: E402
from evalstats import format_rowstats  # noqa: E402

# Copied from core/src/engine.rs (SYMBOLS, KEYS_STANDARD); test_fuzzy.py checks them against that source.
SYMBOLS = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ"
KEYS_STANDARD = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-"
KEY_ROWS = ["1234567890-=", "qwertyuiop[]", "asdfghjkl;'", "zxcvbnm,./"]
TONES = ["", "ˊ", "ˇ", "ˋ", "˙"]  # tone 1 is unmarked
FUZZY_PAIRS = ["ㄣㄥ", "ㄓㄗ", "ㄔㄘ", "ㄕㄙ"]
KINDS = ("tone", "fuzzy", "adjacent")
GATES = ("G0", "G1", "G2")
BEAM, PER_KEY = 64, ime.PER_KEY


def _neighbours():
    """symbol -> list of symbols on the keys left/right in the same row; tone keys and non-symbol keys dropped."""
    key_sym = dict(zip(KEYS_STANDARD, SYMBOLS))
    out = {}
    for row in KEY_ROWS:
        for i, k in enumerate(row):
            if k in key_sym:
                out[key_sym[k]] = [key_sym[row[j]] for j in (i - 1, i + 1) if 0 <= j < len(row) and row[j] in key_sym]
    return out


NEIGHBOURS = _neighbours()


def _slot(sym):
    """Composer column of a symbol (docs/contracts/s3a.md section 2; core/src/engine.rs symbol_of): 0 initial, 1 medial, 2 final."""
    i = SYMBOLS.index(sym)
    return 0 if i < 21 else 1 if i < 24 else 2


def compose(keys):
    """The reading the composer shows after these symbol keys, typed in order (s3a rule 9: a key fills its own column,
    a second key in the same column replaces the first)."""
    cols = ["", "", ""]
    for c in keys:
        cols[_slot(c)] = c
    return "".join(cols)


def strip_tone(syl):
    return "".join(c for c in syl if c in SYMBOLS)


def tone_of(syl):
    return "".join(c for c in syl if c not in SYMBOLS)


def _pair_map(pairs):
    return {a: b for p in pairs for a, b in ((p[0], p[1]), (p[1], p[0]))}


def inject(syls, kind, p, rng, valid_syls, pairs=FUZZY_PAIRS):
    """Returns (new syllables, changed, invalid): invalid = some changed syllable is not a lexicon syllable.
    p is per syllable (tone), per paired symbol (fuzzy), per symbol key (adjacent)."""
    out, changed, invalid = [], False, False
    pm = _pair_map(pairs)
    for s in syls:
        new = s
        if kind == "tone":
            if rng.random() < p:
                base = strip_tone(s)
                new = base + rng.choice([t for t in TONES if t != tone_of(s)])
        else:
            chars = []
            for c in strip_tone(s) if kind == "adjacent" else s:
                alt = None
                if kind == "fuzzy" and c in pm:
                    if rng.random() < p:
                        alt = pm[c]
                elif kind == "adjacent":
                    nb = NEIGHBOURS.get(c, [])
                    if nb and rng.random() < p:
                        alt = rng.choice(nb)
                chars.append(alt or c)
            # adjacent: the neighbour key may sit in another column; what the IME shows is the slot composition of the keys
            new = compose(chars) + tone_of(s) if kind == "adjacent" else "".join(chars)
        if new != s:
            changed = True
            invalid = invalid or new not in valid_syls
        out.append(new)
    return out, changed, invalid


class Tolerance:
    """Per-kind map from an observed syllable to the other lexicon syllables it may mean."""

    def __init__(self, lex, kind, pairs=FUZZY_PAIRS):
        self.kind = kind
        self.canon = {p[1]: p[0] for p in pairs}  # second of a pair -> first
        self.valid = {s for key in lex.by_reading for s in key}
        self.index = {}
        for s in self.valid:
            if kind != "adjacent":
                self.index.setdefault(self.normal(s), set()).add(s)
                continue
            # reverse of the injection: every reading the composer can show when one key of s was a row neighbour
            keys = strip_tone(s)
            for i, c in enumerate(keys):
                for n in NEIGHBOURS.get(c, []):
                    shown = compose(keys[:i] + n + keys[i + 1:]) + tone_of(s)
                    if shown != s:
                        self.index.setdefault(shown, set()).add(s)
        self._cache = {}

    def normal(self, syl):
        """The normalised reading: tone dropped (tone) or paired symbols merged to the first of the pair (fuzzy)."""
        if self.kind == "tone":
            return strip_tone(syl)
        return "".join(self.canon.get(c, c) for c in syl)

    def variants(self, syl):
        v = self._cache.get(syl)
        if v is None:
            key = syl if self.kind == "adjacent" else self.normal(syl)
            v = sorted(self.index.get(key, set()) - {syl})
            self._cache[syl] = v
        return v


def tolerant_entries(lex, tol, key, exact_words):
    """Words whose reading differs from `key` in exactly one syllable by tolerance: [(word, lp, lexicon key)]."""
    best = {}
    for j, s in enumerate(key):
        for v in tol.variants(s):
            k2 = key[:j] + (v,) + key[j + 1:]
            for w, lp in lex.by_reading.get(k2, ()):
                if w not in exact_words and (w not in best or lp > best[w][0]):
                    best[w] = (lp, k2)
    return sorted(((w, lp, k) for w, (lp, k) in best.items()), key=lambda x: -x[1])[:PER_KEY]


def decode_tol(lex, tol, syls, lm, profile, delta, beam=BEAM, start="<s>"):
    """lm.decode plus tolerant entries. Returns [(score, words, used_tolerance)] sorted by score (stable)."""
    table = getattr(lex, "demote", None)
    lam = L.PROFILES[profile]
    syls = tuple(syls); n = len(syls)
    hyps = [[] for _ in range(n + 1)]; hyps[0] = [(0.0, (), False)]
    for i in range(1, n + 1):
        cand = {}
        for ln in range(1, min(lex.max_len, i) + 1):
            if not hyps[i - ln]:
                continue
            key = syls[i - ln:i]
            exact = lex.by_reading.get(key) or []
            items = [(w, lp, key, 0.0, False) for w, lp in exact[:PER_KEY]]
            items += [(w, lp, k, delta, True) for w, lp, k in tolerant_entries(lex, tol, key, {w for w, _ in exact})]
            for word, lp, k, pen, flag in items:
                d = L.demote_delta(table, "-".join(k), word) if table else 0.0
                for s, ws, f in hyps[i - ln]:
                    sc = s + (lm.word(lam, ws[-1] if ws else start, word, lp) - d - pen)
                    surface = "".join(ws) + word
                    if surface not in cand or sc > cand[surface][0]:
                        cand[surface] = (sc, ws + (word,), f or flag)
        hyps[i] = sorted(cand.values(), key=lambda x: -x[0])[:beam]
    out = [(s + lm.eos(lam, ws[-1] if ws else start), ws, f) for s, ws, f in hyps[n]]
    return sorted(out, key=lambda x: -x[0])


def gate_lists(exact, tol, failed):
    """exact: surfaces of lm.decode; tol: [(surface, used_tolerance)] of decode_tol; failed: the exact input fails.
    Returns {gate: surface list}."""
    top5 = exact[:5]
    extra = [s for s, f in tol if f and s not in top5][:4]
    g1 = top5 + extra
    g1 += [s for s in exact[5:] if s not in extra]
    return {"G0": [s for s, _ in tol] if failed else exact, "G1": g1, "G2": [s for s, _ in tol]}


class Ctx:
    """Loaded model and lexicon, shared by every cell."""

    def __init__(self, profile="chat", lm_path=None):
        base = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.OVERLAYS)
        self.lm = L.BigramLM(lm_path or os.path.join(ROOT, "data", "lm", "bigram.sjlm"))
        with open(os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv"), encoding="utf-8") as f:
            ov = {l.split("\t")[1] for l in f}
        self.lex = L.cap_overlay(base, ov, self.lm)
        self.profile = profile
        self.tols = {}

    def tol(self, kind, pairs=FUZZY_PAIRS):
        k = (kind, tuple(pairs))
        if k not in self.tols:
            self.tols[k] = Tolerance(self.lex, kind, pairs)
        return self.tols[k]


def rows_of(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("|")
            if len(p) == 3:
                out.append((p[1], p[2].split()))
    return out


# dev rows with real key presses. eval/dev/user-reported.txt has none (contract section 2) and is left out; the first 302 rows
# of all eval/dev/*.txt (and eval/bench/suite-v1/dev302.txt, the frozen copy) include its first 3 rows, so this set is 299.
DEV_FILES = ("existing.txt", "homophones.txt", "oov.txt")


def load_set(name):
    """(gold sentence, syllables) rows: dev299 (the dev302 rows minus the 3 user-reported ones), typing76, or a file path."""
    if name == "dev299":
        rows = []
        for f in DEV_FILES:
            rows += rows_of(os.path.join(ROOT, "eval", "dev", f))
        return rows
    if name == "typing76":
        return rows_of(os.path.join(ROOT, "eval", "dev", "user-typing.txt"))
    return rows_of(name)


def exact_of(ctx, rows, kind, p, seed, pairs=FUZZY_PAIRS):
    """The delta-independent part of a cell: per row the injected input and the exact decode (cache this per (rows, kind, p))."""
    tol = ctx.tol(kind, pairs)
    out = []
    for i, (gold, syls) in enumerate(rows):
        rng = random.Random(f"{seed}|{kind}|{p}|{i}")  # per row: independent of the other rows and of the gate
        inp, changed, invalid = inject(syls, kind, p, rng, tol.valid, pairs) if p > 0 else (syls, False, False)
        t0 = time.perf_counter()
        ex = ["".join(ws) for _, ws in L.decode(ctx.lex, inp, ctx.lm, ctx.profile)]
        out.append(dict(inp=inp, changed=changed, invalid=invalid, ex=ex, t_ex=time.perf_counter() - t0))
    return out


def process(ctx, rows, kind, p, seed, delta, pairs=FUZZY_PAIRS, exact=None, clean=None):
    """One record per row: dict(gold, ex, tol, failed, changed, invalid, clean_ok, t_ex, t_base, t_tol). p = 0 is clean input.
    exact: the result of exact_of for the same arguments, reused instead of decoding again.
    clean: exact_of at p = 0 (kind does not matter), reused too; clean_ok = the exact decoder's first answer on the clean input
    is right, t_base = its decode time (the latency baseline). Decoded here when p > 0 and not given."""
    tol = ctx.tol(kind, pairs)
    exact = exact if exact is not None else exact_of(ctx, rows, kind, p, seed, pairs)
    if clean is None:
        clean = exact if p == 0 else exact_of(ctx, rows, kind, 0, seed, pairs)
    recs = []
    for (gold, _), e, c in zip(rows, exact, clean):
        t1 = time.perf_counter()
        tl = [("".join(ws), f) for _, ws, f in decode_tol(ctx.lex, tol, e["inp"], ctx.lm, ctx.profile, delta)]
        t_tol = time.perf_counter() - t1
        failed = not e["ex"] or any(s not in tol.valid for s in e["inp"])
        recs.append(dict(gold=lenient(gold), gold_raw=gold, ex=e["ex"], tol=tl, failed=failed, changed=e["changed"],
                         invalid=e["invalid"], clean_ok=ok_at(c["ex"], lenient(gold), 1), t_ex=e["t_ex"], t_base=c["t_ex"],
                         t_tol=t_tol))
    return recs


def ok_at(surfs, gold, k):
    return any(lenient(s) == gold for s in surfs[:k])


def rescue(recs, gate, cls="all"):
    """Rows the injection changed. Those the exact decoder already gets wrong on the clean input are counted as excluded: no
    gate can rescue them. Of the rest, the ones the exact decoder gets wrong are the denominator; the gate puts how many
    first / in the top 9. A wrong row whose exact list is empty gets at most 4 sentences from G1 (the tolerant extras), so its
    top 9 is really a top 4: those rows are counted in `empty` and rescue@9 is also given without them.
    Returns (n_changed, n_excluded, n_wrong, rescue@1, rescue@9, n_empty, rescue@9 over wrong rows with a non-empty exact list)."""
    ch = [r for r in recs if r["changed"] and (cls == "all" or (cls == "invalid") == r["invalid"])]
    kept = [r for r in ch if r["clean_ok"]]
    wrong = [r for r in kept if not ok_at(r["ex"], r["gold"], 1)]
    g = [gate_lists(r["ex"], r["tol"], r["failed"])[gate] for r in wrong]
    r9 = [ok_at(s, r["gold"], 9) for s, r in zip(g, wrong)]
    return (len(ch), len(ch) - len(kept), len(wrong), sum(ok_at(s, r["gold"], 1) for s, r in zip(g, wrong)), sum(r9),
            sum(not r["ex"] for r in wrong), sum(x for x, r in zip(r9, wrong) if r["ex"]))


def time_ratio(recs, gate):
    """Time the gate spends / time of the exact decode of the clean input (not of the injected input: that one stops early at
    a syllable the lexicon lacks, so dividing by it grows with p). t_ex and t_base come from the stage-1 jobs, t_tol from the
    stage-3 job: the load differs between them, which is not measured."""
    base = sum(r["t_base"] for r in recs)
    if gate == "G2":
        t = sum(r["t_tol"] for r in recs)
    elif gate == "G1":
        t = sum(r["t_ex"] + r["t_tol"] for r in recs)
    else:
        t = sum(r["t_ex"] + (r["t_tol"] if r["failed"] else 0.0) for r in recs)
    return t / base if base else float("nan")


def clean_stats(recs, gate):
    """Clean input (p = 0): (base top1, gate top1, fixed, broken, exact-ranks-6..9 rows pushed out of the top 9).
    Plus rowstats texts (base, gate) for tools/evalstats.py."""
    base_ok = [ok_at(r["ex"], r["gold"], 1) for r in recs]
    lists = [gate_lists(r["ex"], r["tol"], r["failed"])[gate] for r in recs]
    gate_ok = [ok_at(s, r["gold"], 1) for s, r in zip(lists, recs)]
    pushed = sum(ok_at(r["ex"], r["gold"], 9) and not ok_at(r["ex"], r["gold"], 5) and not ok_at(s, r["gold"], 9)
                 for s, r in zip(lists, recs))
    rs = lambda surfs: format_rowstats((ok_at(s, r["gold"], 1), s[0] if s else "", r["gold_raw"]) for s, r in zip(surfs, recs))
    return (sum(base_ok), sum(gate_ok), sum(not b and g for b, g in zip(base_ok, gate_ok)),
            sum(b and not g for b, g in zip(base_ok, gate_ok)), pushed, rs([r["ex"] for r in recs]), rs(lists))
