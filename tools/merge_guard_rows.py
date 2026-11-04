#!/usr/bin/env python3
"""Union of several `holdout_guard.py report` outputs, then the rows files without the reported rows (docs/contracts/tune-no-twphrases.md section 1.2).

  merge_guard_rows.py --clean ROWS_IN ROWS_OUT [--clean ...] REPORT...

REPORT is a saved stdout of `holdout_guard.py report` (lines `patterns<TAB>loaded<TAB>skipped`, `source<TAB>units<TAB>hit units`, `rows<TAB>path:line`).
Each ROWS_IN must be spelled exactly as the reports spelled it in --rows (every report was run with the same --rows paths). The union of the `rows` lines of all
reports is removed from the matching ROWS_IN and the rest is written to ROWS_OUT in the original order and bytes (lines count '\\n' only, as holdout_guard does).
It stops, writing nothing, when a report has no `patterns` line or no source line (the run did not finish: holdout_guard prints
`patterns` before it scans and every source line only after the last scan), when the `patterns` lines differ (not the same rows files), when a reported
row names a file that is not a --clean input or a line that is not in it. Prints per file: lines in, removed, kept, SHA-256 of the output. Never prints row text.
"""
import argparse
import hashlib
import os
import sys


def read_report(path):
    """-> (patterns line, [(file, line_no)], [(source, units, hit)])."""
    pat, rows, srcs = None, [], []
    for line in open(path, encoding="utf-8", errors="replace"):
        f = line.rstrip("\n").split("\t")
        if f[0] == "patterns" and len(f) == 3:
            if pat is not None:
                raise SystemExit(f"{path}: two patterns lines")
            pat = tuple(f[1:])
        elif f[0] == "rows" and len(f) == 2:
            name, _, no = f[1].rpartition(":")
            if not name or not no.isdigit():
                raise SystemExit(f"{path}: bad rows entry")
            rows.append((name, int(no)))
        elif len(f) == 3 and f[1].isdigit() and f[2].isdigit():
            srcs.append((f[0], int(f[1]), int(f[2])))
    if pat is None:
        raise SystemExit(f"{path}: no patterns line (the guard run did not finish)")
    if not srcs:
        raise SystemExit(f"{path}: no source line (the guard run did not finish)")
    return pat, rows, srcs


def merge(reports, cleans):
    """cleans: [(in, out)]. -> {in: sorted removed line numbers}; writes the outputs. Raises SystemExit before writing anything on a bad input."""
    pats = {read_report(r)[0] for r in reports}
    if len(pats) != 1:
        raise SystemExit(f"the reports disagree on the patterns line: {sorted(pats)}")
    removed = {i: set() for i, _ in cleans}
    for r in reports:
        for name, no in read_report(r)[1]:
            if name not in removed:
                raise SystemExit(f"{r}: a reported row names {name}, which is not a --clean input")
            removed[name].add(no)
    plan = []
    for src, dst in cleans:
        with open(src, "rb") as f:
            lines = f.readlines()
        bad = [n for n in removed[src] if not 1 <= n <= len(lines)]
        if bad:
            raise SystemExit(f"{src}: reported line {min(bad)}.. is outside the file ({len(lines)} lines)")
        if os.path.exists(dst):
            raise SystemExit(f"{dst} exists (never overwritten)")
        plan.append((src, dst, lines))
    out = {}
    for src, dst, lines in plan:
        kept = [l for n, l in enumerate(lines, 1) if n not in removed[src]]
        with open(dst, "xb") as f:
            f.writelines(kept)
        out[src] = (len(lines), len(removed[src]), len(kept), hashlib.sha256(b"".join(kept)).hexdigest())
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clean", nargs=2, action="append", metavar=("IN", "OUT"), required=True)
    ap.add_argument("reports", nargs="+")
    a = ap.parse_args(argv)
    if len({i for i, _ in a.clean}) != len(a.clean):
        ap.error("the same --clean input twice")
    if len({o for _, o in a.clean}) != len(a.clean):
        ap.error("the same --clean output twice")
    for src, (n, rm, kept, sha) in merge(a.reports, a.clean).items():
        print(f"{src}\tlines {n}\tremoved {rm}\tkept {kept}\tsha256 {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
