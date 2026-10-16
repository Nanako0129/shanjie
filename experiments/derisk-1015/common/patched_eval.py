"""Run reference/proto/lm_eval.py unchanged, with one decoder-side function replaced (docs/contracts/derisk-1015.md section 2).

lm_eval.main reads `lm.decode` and `lm.context_key` at call time, so an extension replaces just that function for the duration
of the run; row selection, KN/classes/demotion loading, scoring, --rowstats and the summary line are lm_eval's own code. The
section-2 anchor is then a real test: the extension's "off" configuration goes through the same code as the reference.
"""
import contextlib
import io
import os
import re
import sys
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
for sub in ("tools", "reference/proto"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import lm as L  # noqa: E402
import lm_eval  # noqa: E402

_REAL_ROWS_OF = lm_eval.rows_of


def run_lm_eval(argv, decode=None, context_key=None, tag=""):
    """lm_eval.main with `argv` (lm_eval's own options). `decode` / `context_key` replace lm.decode / lm.context_key.
    `tag` is appended to the profile field of the summary line (e.g. "+abbr") so an extension's line cannot be mistaken for
    the reference's. Prints lm_eval's output; returns the rows lm_eval evaluated: [(gold, syllables, context)]."""
    seen = {}

    def rows_of(paths):
        seen["rows"] = _REAL_ROWS_OF(paths)
        return seen["rows"]

    buf = io.StringIO()
    with contextlib.ExitStack() as st:
        st.enter_context(mock.patch.object(sys, "argv", ["lm_eval", *argv]))
        st.enter_context(mock.patch.object(lm_eval, "rows_of", rows_of))
        if decode:
            st.enter_context(mock.patch.object(L, "decode", decode))
        if context_key:
            st.enter_context(mock.patch.object(L, "context_key", context_key))
        with contextlib.redirect_stdout(buf):
            lm_eval.main()
    for line in buf.getvalue().splitlines():
        if tag and line.startswith("## "):
            head, rest = line.split("  {", 1)
            line = f"{head}{tag}  {{{rest}"
        print(line)
    # rows_of returns the whole file; --dev / --limit cut it afterwards, from the front, and the summary line's n is what was evaluated
    n = int(re.search(r"'n': (\d+)", buf.getvalue()).group(1))
    return seen["rows"][:n]
