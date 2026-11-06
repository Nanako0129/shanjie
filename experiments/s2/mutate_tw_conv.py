"""The mutations M1-M12 of docs/contracts/tw-conv.md section 3: each one breaks the conversion (or the table builder) in one way, and test_tw_conv.py must
fail on the test the contract names. Single process, about a minute (M6 and M8 rebuild the table from the PTT snapshot, 20 seconds each).

  python3 experiments/s2/mutate_tw_conv.py [--simp-rows FILE] [--only M3 ...]

Every mutation edits a file in place and puts it back in a `finally` (bytes saved before, written back after), runs the test in a fresh
interpreter with a throw-away PYTHONPYCACHEPREFIX (a same-length mutation restored within the second must not leave a stale pyc), and checks
that the named test is among the failures that the unmutated tree does not have. The unmutated run is the baseline: tests that already fail
there (the ambiguous-pair list and the coverage check until tw-conv-simp.tsv and tw-conv-ambiguous.tsv are filled) are left out of the verdict.
--simp-rows FILE runs everything with that file in place of tw-conv-simp.tsv (restored afterwards). Exit status 0 only when every mutation is killed.
The table is rebuilt for M6 and M8 into a temporary file and swapped in for the run; the real table is put back.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BC = os.path.join(HERE, "build_counts.py")
BCT = os.path.join(HERE, "build_counts_text.py")
E1 = os.path.join(ROOT, "experiments", "derisk-1015", "e1", "count.py")
BTC = os.path.join(HERE, "build_tw_conv.py")
TABLE = os.path.join(HERE, "tw-conv.tsv")
SIMP = os.path.join(HERE, "tw-conv-simp.tsv")
TEST = os.path.join(HERE, "test_tw_conv.py")

A1, A3_BCT, A3_E1, A4_ROWS = "test_a1_unchanged", "test_a3_build_counts_text_native_as_written", "test_a3_count_py_native_as_written", "test_a4_simplified_rows"
A1_FOLD = "test_a1_a_mixed_example_gets_the_fold_rows"
A2 = "test_a2_roundtrip_pairs_are_listed"
A4_COVER = "test_a4_coverage_of_the_changed_rows"

OLD_RULES = '''

# ---- M2: the previous detector (SIMP_ONLY, TRAD_ONLY = first values of the markers) and the whole TWPhrases table on the Simplified path
_OLD = {}


def sentence_kind(text):
    if not _OLD:
        st = read_opencc()["st"]
        markers = {k for k, v in st.items() if v[0] != k}
        _OLD.update(S={k for k, v in st.items() if k not in v}, M=markers, T={st[k][0] for k in markers}, TW=read_opencc()["twp"])
    m, t = sum(c in _OLD["M"] for c in text), sum(c in _OLD["T"] for c in text)
    return "simp" if (m > 0 and t == 0) or sum(c in _OLD["S"] for c in text) > t else "trad"


def convert(text, phrase, char, maxp):
    if sentence_kind(text) == "trad":
        return "".join(char.get(c, c) if c in TRAD_SIMP_ONLY else c for c in text).translate(FOLD_TRANS)
    text = _longest(text, phrase, maxp, char)
    return _longest(text, _OLD["TW"], max(map(len, _OLD["TW"]))).translate(FOLD_TRANS)
'''

# (name, what, [(file, old text, new text or ("append", text))], tests that must fail, rebuild the table first)
MUTATIONS = [
    ("M1", "a Traditional sentence also gets the whole TWPhrases.txt",
     [(BC, '''        return text.translate(FOLD_TRANS)''',
       '''        full = convert.__dict__.get("full") or convert.__dict__.setdefault("full", read_opencc()["twp"])
        return _longest(text, full, max(map(len, full))).translate(FOLD_TRANS)''')], [A1], False),
    ("M2", "is_simplified is the previous version and the Simplified path gets the whole TWPhrases.txt",
     [(BC, None, ("append", OLD_RULES))], [A1], False),
    ("M3", "the single-character and phrase rows go back to OpenCC's values",
     [(BC, '''    phrase = {**o["stp"], **tw["phrase"]}''', '''    phrase = dict(o["stp"])'''),
      (BC, '''    char = {**{k: v[0] for k, v in st.items()}, **tw["char"]}''', '''    char = {k: v[0] for k, v in st.items()}''')], [A4_ROWS], False),
    ("M4a", "build_counts_text.py --native goes through the mixed path",
     [(BCT, '''type=lambda p: ("native", p)''', '''type=lambda p: ("mixed", p)''')], [A3_BCT], False),
    ("M4b", "count.py text --native goes through the mixed path",
     [(E1, '''type=lambda p: ("native", p)''', '''type=lambda p: ("mixed", p)''')], [A3_E1], False),
    ("M5", "the fold rows are not applied",
     [(BC, '''    FOLD.clear(); FOLD.update({x: y for x, y in tw["fold"].items() if x != y})''', '''    FOLD.clear()''')], [A1_FOLD], False),
    ("M6", "the candidates of the single-character and phrase rows are counted without folding first",
     [(BTC, '''            cands = list(dict.fromkeys(fold(c) for c in cand))''', '''            cands = list(dict.fromkeys(cand))'''),
      (BTC, '''        chunk = chunk.translate(trans)\n''', '''        pass\n''')], [A4_COVER], True),   # revision 7: counts compare folded strings, so the table's changed rows move
    ("M7", "a sentence with marker characters only also gets the single-character table",
     [(BC, '''char if kind == "simp" else _only(char, "marker")''', '''char''')], [A1], False),
    ("M8", "a phrase `hold` always takes the single-character row's value",
     [(BTC, '''                if fv[i] == row_cands[k[i]][1] and char_rows[k[i]][1] in ("evidence", "mediawiki"):''', '''                if True:''')], [A4_ROWS], True),
    ("M9", "T contains the marker characters again (么 counts as Traditional evidence)",
     [(BC, '''set(o["twv"].values())) - MARKERS)''', '''set(o["twv"].values())))''')], [A4_ROWS], False),
    ("M10", "a Traditional sentence gets no phrase at all (only the single characters of S and N)",
     [(BC, '''        text = _longest(text, s_phrase, max(map(len, s_phrase), default=1), _only(char, "trad"))''',
       '''        text = "".join(_only(char, "trad").get(c, c) for c in text)''')], [A4_ROWS], False),
    ("M11", "the set N is empty",
     [(BC, '''    RARE.clear(); RARE.update(tw["n"])''', '''    RARE.clear()''')], [A4_ROWS], False),
    ("M12", "char rows go back to single-character counts and the 2x rule over every candidate (revision 4)",
     [(BTC, '''            alts = [c for c in cands if c != first and (frozenset((first, c)) in variant or (k in MEDIAWIKI and c == fold(MEDIAWIKI[k])))]''',
       '''            alts = [c for c in cands if c != first]'''),
      (BTC, '''            if wc[best] >= PICK_MIN and wc[best] >= WORD_RATIO * wc[first]:''',
       '''            best = max(alts, key=lambda c: fc[c])
            if fc[best] >= PICK_MIN and fc[best] >= PICK_RATIO * fc[first]:''')], [A2], True),
]


def run_tests():
    """-> (set of failing test names, raw output)."""
    with tempfile.TemporaryDirectory() as cache:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": cache}
        r = subprocess.run([sys.executable, "-B", "-W", "ignore", "-m", "unittest", TEST], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    failed = set(re.findall(r"^(?:FAIL|ERROR): (\w+) ", r.stderr, re.M))
    if not failed and r.returncode != 0:
        failed = {"<the test file did not run>"}
    return failed, r.stderr


class Patched:
    """Put a list of (path, old, new) edits in place; give every file back its bytes on exit."""
    def __init__(self, edits):
        self.edits, self.saved = edits, {}

    def __enter__(self):
        for path, old, new in self.edits:
            if path not in self.saved:
                self.saved[path] = open(path, "rb").read()
            text = open(path, encoding="utf-8").read()
            if isinstance(new, tuple):
                text += new[1]
            else:
                if text.count(old) != 1:
                    raise SystemExit(f"mutation does not apply: {os.path.basename(path)} has {text.count(old)} copies of the text to replace")
                text = text.replace(old, new)
            open(path, "w", encoding="utf-8", newline="\n").write(text)
        return self

    def __exit__(self, *exc):
        for path, data in self.saved.items():
            open(path, "wb").write(data)
        return False


def rebuilt_table(edits):
    """Rebuild tw-conv.tsv with the builder edits into a temporary file; -> its path."""
    out = tempfile.mkdtemp()
    tmp = os.path.join(out, "tw-conv.tsv")
    with Patched([e for e in edits if e[0] == BTC]):
        r = subprocess.run([sys.executable, "-B", BTC, "--out", tmp], cwd=ROOT, capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise SystemExit("the mutated builder failed: " + r.stderr[-400:])
    return tmp


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--simp-rows", help="use this file as tw-conv-simp.tsv for the run (the real one is put back)")
    ap.add_argument("--only", nargs="*", default=[], help="run only these mutations (M1 ... M12, M4a, M4b)")
    a = ap.parse_args(argv)
    saved = {p: open(p, "rb").read() for p in (SIMP, TABLE)}
    results = []
    try:
        if a.simp_rows:
            open(SIMP, "wb").write(open(a.simp_rows, "rb").read())
        base, _ = run_tests()
        print(f"baseline: {len(base)} failing test(s): {sorted(base)}", flush=True)
        for name, what, edits, must, rebuild in MUTATIONS:
            if a.only and name not in a.only and name[:2] not in a.only:
                continue
            table = rebuilt_table(edits) if rebuild else None
            try:
                if table:
                    open(TABLE, "wb").write(open(table, "rb").read())
                with Patched([e for e in edits if e[0] != BTC]):
                    failed, _ = run_tests()
            finally:
                open(TABLE, "wb").write(saved[TABLE])
            new = failed - base
            killed = all(m in new for m in must)
            results.append(killed)
            print(f"{name}\t{'KILLED' if killed else 'SURVIVED'}\tmust fail {must}\tnew failures {sorted(new)}\t{what}", flush=True)
    finally:
        for p, data in saved.items():
            open(p, "wb").write(data)
    print(f"{sum(results)} of {len(results)} mutations killed")
    return 0 if all(results) and results else 1


if __name__ == "__main__":
    sys.exit(main())
