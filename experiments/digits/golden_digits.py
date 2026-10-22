"""Generates eval/golden/digits-score.txt: the Python baseline's per-key splits and scores for 31 synthetic key sequences
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
    # digits.md A.3.3 revision three: dev302 row 196 as the engine shows it (嗎, before 表 turns it into 碼); u decides on the spot
    ("比賽用嗎", "1ul3", "表"),
]
assert len(ROWS) == 31


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


# digits.md A.3.5 prediction rows: (left context, digit keys, unfinished-syllable symbols; the symbols are keys outside the digit alphabet, so
# the Rust engine can be put in exactly this state: digits, then the key of ㄩ). Every digit string is one the baseline shows as digits
# (a string like 20 at the sentence start shows as ㄉㄢ, which is the unfinished-syllable row of today, not a digit row). The context is `lm.history(context_key(left))`; with
# symbols the row is the tiers of `digit_row`, without them the successor layer.
PRED_ROWS = [
    ("", "3", ""), ("", "202", ""), ("", "101", ""), ("", "34", ""), ("", "10", "ㄩ"),
    ("下午", "3", ""), ("下午", "2", ""), ("下午", "20", ""), ("下午", "101", ""),
    ("價格", "3", ""), ("價格", "202", ""), ("價格", "300", "ㄩ"),
]
assert len(PRED_ROWS) == 12
PRED_PROFILE, PRED_LIMIT = "chat", 9


def generate_pred(env, table_path):
    """The text of eval/golden/digits-pred.txt: per row the first 9 items of `digit_row` (word and Python's repr of the score)."""
    lam = DS.L.PROFILES[PRED_PROFILE]
    header = [
        "digits-pred golden (experiments/digits/golden_digits.py --pred; digits.md A.3.5). Python is the baseline; Rust must match words and score bits.",
        f"digits.tsv sha256={sha(table_path)}",
        "models: " + " ".join(f"{n} sha256={sha(os.path.join(ROOT, 'data', 'lm', n))}" for n in ("bigram.sjlm", "classes.sjc", "kn.sjkn")),
        "lexicon: " + " ".join(f"{n} sha256={sha(os.path.join(ROOT, 'data', 'lexicon', n))}" for n in LEXICON_FILES),
        f"profile={PRED_PROFILE} lambda={lam!r} limit={PRED_LIMIT} gamma=1.0",
        f"generated on {platform.system()} {platform.machine()}, python {platform.python_version()}",
        "format: `## <n>\\tleft=\\tdigits=\\tsyms=\\tv=`, then `<rank>\\t<word>\\t<score repr>` per item; syms are the unfinished symbols (none: the row of a finished digit run)",
    ]
    out = ["# " + l for l in header]
    for n, (left, digits, syms) in enumerate(PRED_ROWS, 1):
        v = env.history(left)
        units = DS.P1.units_of(list(syms)) if syms else []
        out.append(f"## {n}\tleft={left}\tdigits={digits}\tsyms={syms}\tv={v}")
        for r, (w, s) in enumerate(DS.digit_row(env.table, env.lm, env.lex, env.idx, v, units, lam, PRED_LIMIT), 1):
            out.append(f"{r}\t{w}\t{s!r}")
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
    ap.add_argument("--pred", action="store_true", help="write the prediction-row golden (digits.md A.3.5, eval/golden/digits-pred.txt) instead")
    a = ap.parse_args(argv)
    import measure
    env = measure.Env(a.table)
    if a.pred:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(generate_pred(env, a.table))
        return
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
