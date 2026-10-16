"""The section-2 anchor: an extension's summary line must equal reference/proto/lm_eval.py's, every field, top1_sha256 included.

  python3 anchor.py EXT_OUTPUT REF_OUTPUT

Each file is the stdout of one run (or several, e.g. chat then formal): only lines that start with "## " are compared, in order.
Exit 0 when they are identical; otherwise print the first difference and exit 1 (a stop condition of the contract, section 5).
A summary line is `## <name>  <profile tags>  {'n': ..., 'top1': ..., 'oracle@64': ..., 'top1_sha256': '...'}`.
"""
import ast
import sys


def summary_lines(text):
    return [l for l in text.splitlines() if l.startswith("## ")]


def _split(line):
    """(name, profile, dict) of a summary line; None when it does not have the lm_eval shape."""
    try:
        head, rest = line.split("  {", 1)
        name, prof = head[3:].split("  ", 1)
        return name, prof, ast.literal_eval("{" + rest)
    except (ValueError, SyntaxError):
        return None


def first_diff(ext, ref):
    """None when the summary lines are identical, else a one-line description of the first difference."""
    a, b = summary_lines(ext), summary_lines(ref)
    for k, (x, y) in enumerate(zip(a, b), 1):
        if x == y:
            continue
        sx, sy = _split(x), _split(y)
        if sx and sy:
            for what, u, v in zip(("name", "profile tags"), sx[:2], sy[:2]):
                if u != v:
                    return f"line {k}: {what}: ext {u!r} != ref {v!r}"
            for key in sorted(set(sx[2]) | set(sy[2])):
                if sx[2].get(key) != sy[2].get(key):
                    return f"line {k}: {key}: ext {sx[2].get(key)!r} != ref {sy[2].get(key)!r}"
        pos = next((i for i, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
        return f"line {k}: first differing character at {pos}: ext {x[pos:pos + 20]!r} != ref {y[pos:pos + 20]!r}"
    if len(a) != len(b):
        return f"summary line count differs: ext {len(a)} != ref {len(b)}"
    if not a:
        return "no summary line in either file"
    return None


def main(argv):
    if len(argv) != 2:
        sys.exit(__doc__)
    d = first_diff(open(argv[0], encoding="utf-8").read(), open(argv[1], encoding="utf-8").read())
    if d:
        print(f"ANCHOR MISMATCH: {d}", file=sys.stderr)
        sys.exit(1)
    print("anchor ok")


if __name__ == "__main__":
    main(sys.argv[1:])
