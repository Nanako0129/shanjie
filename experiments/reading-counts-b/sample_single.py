#!/usr/bin/env python3
"""Step 2 of README.md in this directory: the positions where a character of step2-chars.txt stands alone, and the character classes.

  sample_single.py sample --chars step2-chars.txt (--wiki [--articles 200000] | --text FILE) --expect-sha256 PREFIX [--per 50] [--seed 20261023] --out positions.tsv
  sample_single.py human  --chars step2-chars.txt --rows ROWS --out human.tsv

`sample`: one fixed-seed reservoir (Algorithm R, the draw of experiments/reading-aware/sample_labels.py `reservoir`) per character over the stream of
that character's standing-alone positions: the sentence stream, the conversion and the lexicon-score cut (build_counts.segment over a Han run; a run of
one character is that character standing alone) are sample_labels.py's. `--expect-sha256` (required) is a prefix of the SHA-256 of the input file (the
wiki dump, or the --text file): the run stops unless it matches, and prints the full hash (registered: zhwiki 5db9052e, colloquial-train.txt 3e833d06). Writes `id, source, sentence, pos, char` (pos = index of the character in the sentence; id = <source>-<hex of the
character>-<k>, unique across a wiki run and a text run because the source differs: `wiki`, or `text-<file name without extension>`). Run it once for
the wiki and once for the colloquial text and `cat` the two. Sentences holding a tab are skipped (and counted).
`human`: ROWS in the repo's eval format (`context|sentence|syllables`); for every standing-alone position of a set character in the row's sentence
(rows whose syllable count differs from the sentence length are skipped and counted) writes `id, source, sentence, pos, char, human, tool`: human =
the row's syllable at that position, tool = the reading tools/readings.py gives that sentence at that position (its own program, run on the sentences;
its CHECK lines are not shown). The first five columns are what label_llm.py `requests` reads, so human.tsv is also its input. Rows are public files.
The class of a character (`classify`): `tone` when all its single-character readings in the merged lexicon share initial and final (only the tone or
neutral differ), else `seg`. Also here: the merged lexicon loader and the labels reader (`read_labels`, a repeated id stops), shared with label_llm.py, calib.py and build_t2.py.
Output is positions and counts. Exit 0 done, 1 stop.
"""
import argparse, collections, functools, hashlib, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import build_t1 as B1  # noqa: E402  (puts the p5 / d23 / tools paths on sys.path; stop, read_table, write_table, read_text)
sys.path.insert(0, os.path.join(ROOT, "experiments", "reading-aware"))
import rcommon  # noqa: E402  (puts reference/proto, tools and experiments/s2 and derisk-1015 on sys.path)
import build_counts as bc  # noqa: E402
import mapping  # noqa: E402
import sample_labels as SL  # noqa: E402

DEFAULT_CHARS = os.path.join(HERE, "step2-chars.txt")
SEED = 20261023
MAX_EXAMPLES = 3
CORPUS_PREFIXES = ("wiki-", "text-")   # the ids `sample` writes; the ids of `human` are calibration positions


def load_chars(path):
    with open(path, encoding="utf-8") as f:
        chars = [l.rstrip("\n") for l in f if l.strip()]
    if any(len(c) != 1 for c in chars) or len(set(chars)) != len(chars):
        B1.stop("the character list has a line that is not one character, or a repeat")
    return chars


@functools.lru_cache(maxsize=None)
def merged_lexicon():
    """{(reading tuple, word): score}: the lexicon the CLI loads with `--packs acg` (d23.load_lex_scores), loaded once."""
    import d23 as D
    return D.load_lex_scores()[0]


def single_pairs():
    """{(syllable, char)} of the merged lexicon's single-character entries (what a T0 must hold, build_t1.build's check)."""
    return {(r[0], w) for (r, w) in merged_lexicon() if len(w) == 1 and len(r) == 1}


def load_merged(chars):
    """-> ({char: [reading, ...]} in lexicon-score order (stable: ties keep the lexicon's order), {(char, reading): [(score, word)]}) from the merged
    lexicon the CLI loads with `--packs acg` (d23.load_lex_scores). The readings are every single-character reading; the examples are the words of >= 2
    characters in which the character reads that way, at that position, for the characters in `chars` only. Insertion order of d23's dict = load order."""
    lex = merged_lexicon()
    chars = set(chars)
    singles, examples = collections.defaultdict(list), collections.defaultdict(list)
    for (r, w), sc in lex.items():
        if len(w) == 1 and len(r) == 1 and w in chars:
            singles[w].append((sc, r[0]))
        elif len(w) >= 2 and len(r) == len(w):
            for c, rd in zip(w, r):
                if c in chars:
                    examples[c, rd].append((sc, w))
    readings = {c: [r for _, r in sorted(v, key=lambda x: -x[0])] for c, v in singles.items()}   # sorted() is stable
    return readings, examples


def classify(rs):
    """`tone` when the readings `rs` differ only in tone or neutral, else `seg`."""
    return "tone" if len({rcommon.strip_tone(r) for r in rs}) == 1 else "seg"


def primary_readings(readings, t0=None, tiebreak=None):
    """-> {char: r*}. r* = the reading with T0's t = 0 ({(char, reading): t}; every reading when t0 is None); when no reading has t = 0 (乜 掇 溴 鉈 in
    T0), the reading with the largest t; more than one such reading: tiebreak(char) (default: tools/readings.py's own primary, units.Labeller.primary)
    when it is one of them, else the first in lexicon order."""
    out, tb = {}, tiebreak
    for c, rs in readings.items():
        ts = [0.0 if t0 is None else t0.get((c, r), 0.0) for r in rs]
        zero = [r for r, t in zip(rs, ts) if t == max(ts)]
        if len(zero) > 1:
            if tb is None:
                tb = readings_primary()
            p = tb(c)
            zero = [p] if p in zero else zero[:1]
        out[c] = zero[0]
    return out


def readings_primary():
    """c -> the primary reading of tools/readings.py rules 0, 1, 3, 4 (units.Labeller.primary over the lexicon of reading-aware)."""
    import readings as RT
    import units
    lab = units.Labeller(rcommon.char_readings(rcommon.load_lexicon()), units.load_overrides(RT.OVERRIDES), units.load_hetero(RT.HETERO))
    return lab.primary


def cut_lexicon():
    """The lexicon of the cut: what count.init3((), None) builds in bc._W["lex"] (build_counts._init: mcbpmf-data.txt plus overlay-add.tsv), built here
    without loading the conversion tables, which `human` does not need."""
    return rcommon.ime.Lexicon(rcommon.LEXICON, overlay=[os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv")])


def standalone(sentence, lex, chars):
    """[(pos, char)] of the characters of `chars` that stand alone in the lexicon-score cut of their Han run (a run of one character is that character
    alone; sample_labels.candidates skips those, step 2 does not). Runs without a set character are not segmented."""
    if not any(c in chars for c in sentence):
        return []
    out, pos = [], 0
    for kind, raw, _ in mapping.tokenize(sentence):
        if kind == "han" and any(c in chars for c in raw):
            at = 0
            for w in bc.segment(lex, raw) or ():
                if len(w) == 1 and w in chars:
                    out.append((pos + at, w))
                at += len(w)
        pos += len(raw)
    return out


class Reservoirs:
    """One Algorithm R reservoir per key, fed one item at a time, each with its own Random(seed): over the same items it keeps exactly what
    sample_labels.reservoir keeps."""

    def __init__(self, keys, n, seed):
        self.n = n
        self.rng = {k: random.Random(seed) for k in keys}
        self.seen = dict.fromkeys(keys, 0)
        self.keep = {k: [] for k in keys}

    def add(self, k, x):
        i, keep = self.seen[k], self.keep[k]
        self.seen[k] = i + 1
        if len(keep) < self.n:
            keep.append(x)
        else:
            j = self.rng[k].randrange(i + 1)
            if j < self.n:
                keep[j] = x


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def source_tag(a):
    return "wiki" if a.wiki else "text-" + os.path.splitext(os.path.basename(a.text))[0]


def sample(a):
    chars = load_chars(a.chars)
    path = bc.DUMP if a.wiki else a.text
    full = file_sha256(path)
    print(f"sample_single: input sha256 {full}")
    if not a.expect_sha256 or not full.startswith(a.expect_sha256.lower()):
        B1.stop("the input file's SHA-256 does not start with --expect-sha256")
    res, skipped, sentences = Reservoirs(chars, a.per, a.seed), 0, 0
    ns = argparse.Namespace(wiki=a.wiki, articles=a.articles, paths=[a.text] if a.text else None)
    for _, s in SL.sentences(ns):
        sentences += 1
        if "\t" in s:
            skipped += 1
            continue
        for pos, c in standalone(s, bc._W["lex"], res.keep):   # the lexicon count.init3 built (SL.sentences calls it before the first sentence)
            res.add(c, (s, pos))
    tag = source_tag(a)
    with open(a.out, "x", encoding="utf-8", newline="\n") as f:
        for c in chars:
            for k, (s, pos) in enumerate(res.keep[c]):
                f.write(f"{tag}-{ord(c):x}-{k}\t{tag}\t{s}\t{pos}\t{c}\n")
    short = sum(len(res.keep[c]) < a.per for c in chars)
    print(f"sample_single: {tag}: {sentences} sentences ({skipped} skipped for a tab), {sum(len(v) for v in res.keep.values())} positions, "
          f"{short} of {len(chars)} characters under {a.per} -> {a.out}")
    return 0


def tool_readings(sentences):
    """-> {sentence: [syllables]} that tools/readings.py gives these sentences (its stderr is swallowed: CHECK lines name sentences)."""
    import contextlib, io, tempfile
    import readings as RT
    sentences = sorted(set(sentences))
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "in.txt"), os.path.join(d, "out.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.writelines(f"|{s}\n" for s in sentences)
        with contextlib.redirect_stderr(io.StringIO()):
            RT.main(src, dst)
        out = {}
        for l in open(dst, encoding="utf-8"):
            _, s, syls = l.rstrip("\n").split("|")
            out[s] = syls.split()
    return out


def human(a):
    from triage import parse_rows, read_text
    chars = load_chars(a.chars)
    lex = cut_lexicon()
    rows = parse_rows(read_text(a.rows))
    tag = os.path.splitext(os.path.basename(a.rows))[0]
    found, skipped = [], 0
    for i, (_, truth, syls) in enumerate(rows, 1):
        if len(truth) != len(syls) or "\t" in truth:
            skipped += 1
            continue
        found.extend((f"{tag}-{i}-{pos}", truth, pos, c, syls[pos]) for pos, c in standalone(truth, lex, chars))
    tool = tool_readings(x[1] for x in found)
    missing = sum(x[1] not in tool or len(tool[x[1]]) != len(x[1]) for x in found)
    with open(a.out, "x", encoding="utf-8", newline="\n") as f:
        for id_, s, pos, c, h in found:
            if s in tool and len(tool[s]) == len(s):
                f.write(f"{id_}\t{tag}\t{s}\t{pos}\t{c}\t{h}\t{tool[s][pos]}\n")
    print(f"sample_single: {tag}: {len(rows)} rows, {skipped} skipped (syllable count differs), {len(found) - missing} positions "
          f"({missing} dropped: tools/readings.py gave no aligned reading) -> {a.out}")
    return 0


def read_labels(path):
    """-> {id: (char, reading)} of a label_llm.py `collect` file (an empty reading = an invalid answer); a repeated id stops."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for l in f:
            i, c, r = l.rstrip("\n").split("\t")
            if i in out:
                B1.stop("a repeated id in the labels")
            out[i] = (c, r)
    return out


def read_positions(path):
    """-> [(id, source, sentence, pos, char, rest...)] of a positions or human TSV (the first five columns are the contract)."""
    out = []
    with open(path, encoding="utf-8") as f:
        for l in f:
            p = l.rstrip("\n").split("\t")
            if len(p) < 5:
                B1.stop(f"{path}: a line with fewer than five columns")
            out.append((p[0], p[1], p[2], int(p[3]), p[4], *p[5:]))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sample", "human"):
        p = sub.add_parser(name)
        p.add_argument("--chars", default=DEFAULT_CHARS)
        p.add_argument("--out", required=True)
        if name == "sample":
            g = p.add_mutually_exclusive_group(required=True)
            g.add_argument("--wiki", action="store_true")
            g.add_argument("--text")
            p.add_argument("--articles", type=int, default=200000)
            p.add_argument("--expect-sha256", required=True, metavar="PREFIX")
            p.add_argument("--per", type=int, default=50)
            p.add_argument("--seed", type=int, default=SEED)
        else:
            p.add_argument("--rows", required=True)
    a = ap.parse_args(argv)
    if os.path.exists(a.out):
        B1.stop("--out exists")
    return sample(a) if a.cmd == "sample" else human(a)


if __name__ == "__main__":
    sys.exit(main())
