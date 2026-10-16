"""Generates eval/golden/digits-score.txt: the Python baseline's per-key splits and scores for 30 synthetic key sequences
(docs/contracts/digit-context.md C.5; the Rust port in docs/contracts/digits.md A.4.1 #49 compares against it, scores as f64 bits).

  python3 experiments/digits/golden_digits.py --table data/lexicon/digits.tsv --theta 0 [--mode structural] --out eval/golden/digits-score.txt

Depends on data/lm/ (bigram.sjlm, classes.sjc, kn.sjkn), the lexicon and the table (its SHA-256 is in the header). Generate on macOS: Linux's
libm differs from macOS's in the last bit of log10, and the sums are order-sensitive (candidate order of predict3.reference, plain left-to-right).

Row line:  `## <n>\\tleft=<left>\\tkeys=<keys>\\tchars=<characters the finished syllables show>` (the space bar is `␣`, Enter is `⏎`; Enter is appended).
Key line:  `<index>\\t<key>\\tflush=<keys flushed to digit tokens before scoring>\\tctx=<run context C or ->\\tbuf=<buffer keys the splits refer to: after the key joined it, before the run ended>\\t
            splits=<m>,<t>,<score>;...\\tchoice=<m>,<t> or -\\tcls=<D digit, Z zhuyin, S separator, - not yet decided, one char per key so far>`
  splits are in the order Scorer.decide builds them (m from the buffer's length down to 0, then the boundary split); the score is Python's repr.
"""
import argparse
import hashlib
import os
import platform
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import digits_score as DS  # noqa: E402

SP, EN = "␣", "⏎"
# (left context, keys, characters shown by the syllables that finish). Cases: digits at once, tone keys, space, a split forced by a key that
# is not in the digit alphabet (u m , j k o l), a fifth key that flushes the oldest (1258901258: no tone key, so no syllable finishes mid-run;
# 2026/10/15), contexts that need a word before. A syllable that finishes in the middle of a row and is followed by more keys must have its
# character in `chars` (the next run's context reads it): `generate(strict=True)` refuses a row that leaves a "?" in the middle of the shown text.
ROWS = [
    ("", "1", ""), ("", "2026su06", "年"), ("下午", "3", ""), ("下午", "32u03", "點"), ("", "84", ""),
    ("", "84" + SP, ""), ("", "504", ""), ("", "1984", ""), ("", "20u", ""), ("", "101o4", "倍"),
    ("", "100m06", "元"), ("", "1m,4", "月"), ("", "3.14", ""), ("", "25" + SP, ""), ("", "2." + SP, ""),
    ("", "1258901258", ""), ("", "2026/10/15", ""), ("", "1-2", ""), ("價格", "300m06", "元"), ("", "hk4g4123", "測試"),
    ("你", "5j03", "轉"), ("", "1" + SP, ""), ("", "3", ""), ("", "6.5", ""), ("", "0.5", ""),
    ("我們", "2026su062k7", "年的"), ("", "10", ""), ("下午", "2", ""), ("下午", "20", ""), ("", "2020" + SP, ""),
]
assert len(ROWS) == 30


def visible(k):
    return SP if k == DS.SPACE else EN if k == DS.ENTER else k


def generate(scorer, history_fn, rows, header_lines, strict=False):
    out = ["# " + l for l in header_lines]
    for n, (left, keys, chars) in enumerate(rows, 1):
        sim = DS.simulate(scorer, history_fn, [DS.SPACE if k == SP else k for k in keys], left, list(chars))
        if strict and "?" in sim.shown[:-1]:   # a "?" is a syllable without its character; one at the very end is read by nobody
            raise SystemExit(f"row {n} ({keys!r}): a syllable finished mid-row without a character in chars (shown {sim.shown!r})")
        out.append(f"## {n}\tleft={left}\tkeys={keys}{EN}\tchars={chars}")
        for ev in sim.log:
            splits = ";".join(f"{m},{t},{s!r}" for m, t, s in ev["splits"])
            choice = "-" if ev["choice"] is None else f"{ev['choice'][0]},{ev['choice'][1]}"
            out.append(f"{ev['idx']}\t{visible(ev['key'])}\tflush={ev['flush']}\tctx={ev['ctx'] or '-'}\tbuf={ev['buf']}\t"
                       f"splits={splits}\tchoice={choice}\tcls={ev['cls']}")
    return "\n".join(out) + "\n"


LEXICON_FILES = ("mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv")


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--theta", type=float, default=0.0)
    ap.add_argument("--mode", choices=("score", "structural"), default="score", help="structural: C's R2 verdict was `structural` (scores are None)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    import measure
    env = measure.Env(a.table)
    scorer = env.scorer(a.theta, a.mode)
    lm_dir = os.path.join(ROOT, "data", "lm")
    header = [
        "digits-score golden (experiments/digits/golden_digits.py; digit-context.md C.5). Python is the baseline; Rust must match the scores as f64 bits.",
        f"digits.tsv sha256={sha(a.table)}",
        f"models: " + " ".join(f"{n} sha256={sha(os.path.join(lm_dir, n))}" for n in ("bigram.sjlm", "classes.sjc", "kn.sjkn")),
        "lexicon: " + " ".join(f"{n} sha256={sha(os.path.join(ROOT, 'data', 'lexicon', n))}" for n in LEXICON_FILES),
        f"theta={scorer.theta} beta={scorer.beta} gamma={scorer.gamma} mode={scorer.mode} max_buffer={DS.MAX_BUF}",
        f"generated on {platform.system()} {platform.machine()}, python {platform.python_version()}",
        "format: see the module docstring of experiments/digits/golden_digits.py",
    ]
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(generate(scorer, env.history, ROWS, header, strict=True))


if __name__ == "__main__":
    main()
