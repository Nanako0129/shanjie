"""E2 reading labeler for the training text: tools/readings.py rules 0, 1, 3, 4 (NOT rule 2, the MoeDict rule: its licence allows it
only for deciding evaluation-input readings). Rule 5 (CHECK) words, words that do not fit a lexicon reading (GAP) and characters
outside the lexicon get NOREAD.

  python label_readings.py --work DIR [--procs N]      # DIR: prep.py output of experiments/s7a-tinylm (runs-*.txt, ids-*.npy)
      -> DIR/rd-wiki.npy, DIR/rd-coll.npy (uint16, same length as ids-*.npy: 0 at every BOS and for NOREAD), DIR/rdvocab.txt,
         DIR/rd-report.json (counts per rule and the NOREAD fraction, input hashes)
  python label_readings.py --sample FILE                # one sentence per line (e.g. a public sample): print the report only

The per-word rules are a copy of the branches of tools/readings.py main() (they live inside it and cannot be imported); the data
(lexicon, overrides file, heterophony list) and the word segmentation (ime.segment_words) are the ones that file uses, and
test_e2.py::TestReadingsParity runs both on the same sentences.
"""
import argparse
import collections
import hashlib
import json
import multiprocessing as mp
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tinylm_r  # noqa: E402,F401  (puts experiments/s7a-tinylm, reference/proto and tools on sys.path)
from tinylm_r import ROOT, RVocab  # noqa: E402

import ime  # noqa: E402
import readings  # noqa: E402  tools/readings.py: HETERO, OVERRIDES (paths, SHANJIE_HETERO overrides the default)

LEXICON = os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt")
CITATION = {"一": "ㄧ", "不": "ㄅㄨˋ"}
REASONS = ("r0", "r1", "r3", "r4", "check", "gap", "oov")      # r2 does not exist here on purpose


def load_hetero(path):
    return dict(l.split() for l in open(path, encoding="utf-8") if len(l.split()) == 2)


def load_overrides(path):
    return {w: tuple(r.split()) for w, r in
            (l.rstrip("\n").split("\t") for l in open(path, encoding="utf-8") if not l.startswith("#"))}


class Labeler:
    def __init__(self, lex, hetero, overrides):
        self.lex, self.hetero, self.overrides = lex, hetero, overrides
        self.alts = {}
        for syls, entries in lex.by_reading.items():
            for w, s in entries:
                self.alts.setdefault(w, []).append((s, syls))
        for v in self.alts.values():
            v.sort(key=lambda x: -x[0])
        self.stats = collections.Counter()

    def pick(self, w):
        """(syllable tuple or None, reason) for one word, tools/readings.py rules 0, 1, 3, 4 in that order; None = NOREAD."""
        cands = [syls for _, syls in self.alts.get(w, [])]
        if w in self.overrides:                                                    # rule 0
            return (self.overrides[w], "r0") if self.overrides[w] in cands else (None, "gap")
        citation = [c for c in cands if all(r == CITATION.get(ch, r) for ch, r in zip(w, c))]
        if len(citation) == 1 and len(citation) < len(cands) and any(ch in "一不" for ch in w):    # rule 1
            return citation[0], "r1"
        if len(w) == 1 and w in self.hetero and (self.hetero[w],) in cands:        # rule 3 (rule 2, MoeDict, is skipped)
            return (self.hetero[w],), "r3"
        if len(cands) == 1 or self.alts[w][0][0] - self.alts[w][1][0] >= 0.5:      # rule 4
            return cands[0], "r4"
        return None, "check"                                                       # rule 5: tools/readings.py writes the top reading + CHECK

    def label(self, text):
        """One syllable string (or None) per character of `text`. Characters outside the lexicon split the text; each piece of
        in-lexicon characters is segmented by ime.segment_words (the segmentation tools/readings.py uses)."""
        out = [None] * len(text)
        i, n = 0, len(text)
        while i < n:
            if text[i] not in self.lex.by_word:
                self.stats["oov"] += 1
                i += 1
                continue
            j = i
            while j < n and text[j] in self.lex.by_word:
                j += 1
            words = ime.segment_words(self.lex, text[i:j])
            if words is None:                      # cannot happen for in-lexicon characters; counted as OOV, never guessed
                self.stats["oov"] += j - i
                i = j
                continue
            pos = i
            for w, _auto in words:
                syls, why = self.pick(w)
                self.stats[why] += len(w)
                if syls is not None:
                    out[pos:pos + len(w)] = syls
                pos += len(w)
            i = j
        return out


def make_labeler():
    lex = ime.Lexicon(LEXICON)
    return Labeler(lex, load_hetero(readings.HETERO), load_overrides(readings.OVERRIDES)), lex


def reading_vocab(lex):
    """Every syllable of the lexicon's readings, sorted: ids 1.. (0 is NOREAD). Deterministic for a given lexicon."""
    return RVocab(sorted({s for syls in lex.by_reading for s in syls}))


def label_runs(labeler, rvocab, runs):
    """runs: list of Han runs -> flat uint16 array in the layout of ids-*.npy (a 0 for the BOS, then one id per character)."""
    out = []
    for run in runs:
        out.append(0)
        out.extend(0 if s is None else rvocab.ids[s] for s in labeler.label(run))
    return np.array(out, dtype=np.uint16)


_W = None


def _init():
    global _W
    lab, lex = make_labeler()
    _W = (lab, reading_vocab(lex))


def _work(runs):
    lab, rv = _W
    lab.stats.clear()
    return label_runs(lab, rv, runs), dict(lab.stats)


def report(stats, chars):
    noread = sum(stats.get(r, 0) for r in ("check", "gap", "oov"))
    return {"chars": chars, "per_reason": {r: stats.get(r, 0) for r in REASONS},
            "noread": noread, "noread_fraction": noread / max(1, chars)}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def chunks(path, size=2000):
    buf = []
    for line in open(path, encoding="utf-8", newline="\n"):
        buf.append(line.rstrip("\n"))
        if len(buf) == size:
            yield buf
            buf = []
    if buf:
        yield buf


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--work"); ap.add_argument("--sample")
    ap.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    a = ap.parse_args(argv)
    if bool(a.work) == bool(a.sample):
        ap.error("exactly one of --work / --sample")
    if a.sample:
        _init()
        lines = [l.rstrip("\n") for l in open(a.sample, encoding="utf-8")]
        arr, st = _work(lines)
        print(json.dumps(report(st, sum(map(len, lines))), ensure_ascii=False, indent=1))
        return
    lab, lex = make_labeler()
    rv = reading_vocab(lex)
    open(os.path.join(a.work, "rdvocab.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(rv.syls) + "\n")
    rep = {"inputs": {"lexicon_sha256": sha256_file(LEXICON), "overrides_sha256": sha256_file(readings.OVERRIDES),
                      "heterophony_sha256": sha256_file(readings.HETERO)}, "readings": rv.size, "rules": "0,1,3,4 (no rule 2)"}
    with mp.Pool(a.procs, initializer=_init) as pool:
        for name in ("wiki", "coll"):
            parts, tot = [], collections.Counter()
            for arr, st in pool.imap(_work, chunks(os.path.join(a.work, f"runs-{name}.txt"))):
                parts.append(arr)
                tot.update(st)
            rd = np.concatenate(parts)
            ids = np.load(os.path.join(a.work, f"ids-{name}.npy"), mmap_mode="r")
            if len(rd) != len(ids) or ((rd == 0) & (np.asarray(ids) == 2)).sum() != (np.asarray(ids) == 2).sum():
                sys.exit(f"label_readings: STOP, rd-{name} is not aligned with ids-{name}.npy ({len(rd)} vs {len(ids)})")
            np.save(os.path.join(a.work, f"rd-{name}.npy"), rd)
            chars = int(len(ids) - (np.asarray(ids) == 2).sum())
            rep[name] = dict(report(tot, chars), rd_sha256=sha256_file(os.path.join(a.work, f"rd-{name}.npy")))
    rep["rdvocab_sha256"] = sha256_file(os.path.join(a.work, "rdvocab.txt"))
    json.dump(rep, open(os.path.join(a.work, "rd-report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
