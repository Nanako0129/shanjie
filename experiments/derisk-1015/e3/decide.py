"""E3 decision rule (docs/contracts/derisk-1015.md section 3, E3): at p = 0.2 the dev302 top1 must be at least 90% of the same
setting's full-input top1, in BOTH settings (chat and formal).

  python3 decide.py FULL_CHAT P02_CHAT FULL_FORMAL P02_FORMAL

Each argument is the stdout file of one e3_eval.py run (its `## ` summary line is read). FULL_* are the --abbrev off runs on
the unabbreviated dev302 rows, P02_* the --abbrev on runs on dev302.p0.2.txt. Prints the two ratios and PASS or FAIL.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))
import anchor  # noqa: E402

THRESHOLD = 0.9


def top1(path):
    """(n, top1) of the single summary line in `path`."""
    lines = anchor.summary_lines(open(path, encoding="utf-8").read())
    if len(lines) != 1:
        raise ValueError(f"{path}: expected one summary line, found {len(lines)}")
    d = anchor._split(lines[0])[2]
    return d["n"], d["top1"]


def decide(pairs):
    """pairs: {setting: (full_path, p02_path)} -> (passed, [message per setting])."""
    ok, msgs = True, []
    for setting, (full, p02) in pairs.items():
        (n0, t0), (n1, t1) = top1(full), top1(p02)
        if n0 != n1 or t0 == 0:
            raise ValueError(f"{setting}: the two runs are not the same set (n {n0} vs {n1}) or the full input scores 0")
        r = t1 / t0
        ok &= r >= THRESHOLD
        msgs.append(f"{setting}: p=0.2 top1 {t1} / full top1 {t0} = {r:.4f} ({'>=' if r >= THRESHOLD else '<'} {THRESHOLD})")
    return ok, msgs


def main(argv):
    if len(argv) != 4:
        sys.exit(__doc__)
    try:
        ok, msgs = decide({"chat": argv[0:2], "formal": argv[2:4]})
    except (ValueError, KeyError, OSError) as e:
        sys.exit(f"decide: {e}")
    print("\n".join(msgs) + f"\nE3 {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main(sys.argv[1:])
