"""Evaluation wrapper of the reading-aware experiment (contract docs/contracts/reading-aware-0.md 3.1, 3.2, 3.3, 3.4).

Runs reference/proto/lm_eval.py unchanged (derisk-1015/common/patched_eval.py: row selection, KN/classes/trigram loading, scoring,
--rowstats, --dump and the summary line are lm_eval's own code) with these replaced for the duration of the run:
  - lm.cap_overlay: lm_eval calls it with its DEFAULT arguments, so changing lm.DEMOTE_TSV would do nothing (default arguments are bound
    when the function is defined); the wrapper calls the original and then merges the extra demotion tables into the returned lexicon's
    `demote`. lm.decode and trigram.decode3 (the formal profile with a trigram table) both read `lex.demote`, so both see the table.
  - --units only: lm.decode (decode_units.decode), lm.history (decode_units.history) and the model that cap_overlay counts with
    (decode_units.CountView). Needed for a model with reading units; harmless on a model without (the anchor).

Options of this wrapper (everything else goes to lm_eval.py as it is):
  --extra-demote FILE   a demotion table (a1_table.py's) merged with data/lexicon/demote.tsv; repeatable. A key present twice, in the shipped
                        table or between the tables or the tone rows, stops the run (exit message, status 1). Each file must also load against
                        the lexicon (lm.load_demote: every row's word exists under that reading).
  --delta-tone D        D > 0: also demote the level-1 rows (level1.tone_rows: ㄅㄚ 吧, ...) by D (A2). 0 (default) = none.
  --units               the model holds reading units (decode_units.py).
Summary line: with no extra table, no D and no units model it is lm_eval.py's, character for character (the section 3.1 anchor). Otherwise
`+dm:<first 8 hex of the SHA-256 of the extra table files>`, `:t<D>` and `+units` are appended to the profile field.

  python3 ra_eval.py [wrapper options] --lm MODEL --profile chat|formal --rows FILE|--dev N --context ... (lm_eval.py's options)
"""
import argparse
import contextlib
import hashlib
import os
import sys
from unittest import mock

import rcommon
import decode_units
import level1
import lm as L
import patched_eval  # noqa: E402  (experiments/derisk-1015/common)


def merged_demote(lex, base, extra_paths, tone_delta):
    """`base` (lex.demote after cap_overlay) plus the tables and tone rows. SystemExit on a key that appears twice."""
    table, src = dict(base or {}), {k: "data/lexicon/demote.tsv" for k in (base or {})}

    def put(key, delta, where):
        if key in table:
            raise SystemExit(f"ra_eval: demotion key {key} is in both {src[key]} and {where}")
        table[key], src[key] = delta, where

    for path in extra_paths:
        for key, delta in L.load_demote(path, lex).items():
            put(key, delta, path)
    if tone_delta:
        for reading, word in level1.tone_rows(lex):
            put((reading, word), tone_delta, "the --delta-tone rows")
    return table


def make_cap_overlay(orig, extra_paths, tone_delta, count_view=None):
    def cap_overlay(lex, overlay_words, lm, *rest, **kw):
        out = orig(lex, overlay_words, count_view(lm) if count_view else lm, *rest, **kw)
        if extra_paths or tone_delta:
            out.demote = merged_demote(out, getattr(out, "demote", {}), extra_paths, tone_delta)
        return out
    return cap_overlay


def model_has_units(path):
    with open(path, "rb") as f:
        return any(rcommon.U in w for w in L.read_header(f.read())[4])


def tag_of(extra_paths, tone_delta, units_model):
    tag = ""
    if extra_paths:
        h = hashlib.sha256()
        for p in extra_paths:
            h.update(open(p, "rb").read())
        tag += "+dm:" + h.hexdigest()[:8]
    if tone_delta:
        tag += f":t{tone_delta:g}"
    return tag + ("+units" if units_model else "")


def run(argv):
    ap = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    ap.add_argument("--extra-demote", action="append", default=[])
    ap.add_argument("--delta-tone", type=float, default=0.0)
    ap.add_argument("--units", action="store_true")
    ap.add_argument("--lm", default=os.path.join(rcommon.ROOT, "data", "lm", "bigram.sjlm"))
    a, rest = ap.parse_known_args(argv)
    rest = ["--lm", a.lm, *rest]
    if a.delta_tone < 0:
        raise SystemExit("ra_eval: --delta-tone must not be negative")
    units_model = model_has_units(a.lm)
    if units_model and not a.units:
        raise SystemExit("ra_eval: the model holds reading units, run with --units")
    if a.units:
        tp = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        tp.add_argument("--profile")
        tp.add_argument("--tri")
        tp.add_argument("--no-tri", action="store_true")
        t = tp.parse_known_args(rest)[0]
        if t.profile == "formal" and not t.no_tri and (t.tri or os.path.exists(os.path.join(os.path.dirname(os.path.abspath(a.lm)), "trigram.sjt3"))):
            raise SystemExit("ra_eval: --units does not extend trigram.decode3, run the formal profile with --no-tri")
    with contextlib.ExitStack() as st:
        st.enter_context(mock.patch.object(L, "cap_overlay", make_cap_overlay(
            L.cap_overlay, a.extra_demote, a.delta_tone, decode_units.CountView if a.units else None)))
        if a.units:
            st.enter_context(mock.patch.object(L, "history", decode_units.history))
        return patched_eval.run_lm_eval(rest, decode=decode_units.decode if a.units else None, tag=tag_of(a.extra_demote, a.delta_tone, units_model))


if __name__ == "__main__":
    run(sys.argv[1:])
