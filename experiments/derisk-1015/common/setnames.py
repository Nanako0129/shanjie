"""Shared set names, order and locations for experiments/derisk-1015 (contract docs/contracts/derisk-1015.md section 2).

ORDER is the concatenation order of rowstats files: the two tuning sets first, then the five judging sets. Both sides of every
comparison use this one order (concat_rowstats.py).  discordtune is private: main runs it and concatenates by hand.

Locations are looked up in tools/bench.py (SETS2, suite v2), so a moved or re-hashed set is changed in one place. Exceptions:
  - user-reported is the WHOLE eval/dev/user-reported.txt (as tools/triage.py does; bench pins only the first 80 rows). Its hash is not
    pinned: the file grows by appending, record the hash at run time.
  - e4ctx-punct / e4ctx-alnum are looked up in $E4CTX_DIR (default ~/.cache/shanjie/derisk-1015/e4ctx); only E4 uses them (E4_ORDER).
  - dev302 / typing76 are the frozen suite-v1 files (same as bench SETS2).

  python3 setnames.py list        # name, rows, sha256, whether the hash matches bench's pin, path (tab separated)
  python3 setnames.py path NAME   # just the path, for shell loops
"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bench  # noqa: E402  (defines constants only; nothing runs on import)

TUNE = ["cvtune", "wikitune"]
JUDGE = ["dev302", "typing76", "user-reported", "lytune", "wikitypo"]
ORDER = TUNE + JUDGE
# E4 revision 1: the public derived set e4ctx (e4/build_e4ctx.py) follows the five judging sets in the E4 concatenation.
E4CTX = ["e4ctx-punct", "e4ctx-alnum"]
E4_ORDER = ORDER + E4CTX
E4CTX_DIR = os.environ.get("E4CTX_DIR") or os.path.expanduser("~/.cache/shanjie/derisk-1015/e4ctx")   # where build_e4ctx.py wrote them


def location(name):
    """(path, pinned sha256 or None) of a set."""
    if name in E4CTX:
        return os.path.join(E4CTX_DIR, name + ".txt"), None   # derived, not pinned: the build_e4ctx manifest records the hash
    if name == "user-reported":
        return os.path.join(ROOT, "eval/dev/user-reported.txt"), None
    path, sha, _private = bench.SETS2[name]
    return path, sha


def three_field_rows(path):
    """Rows lm_eval.rows_of keeps: lines with exactly 3 `|` fields. Returns the raw lines (no newline)."""
    return [l.rstrip("\n") for l in open(path, encoding="utf-8") if len(l.rstrip("\n").split("|")) == 3]


def main():
    if sys.argv[1:2] == ["path"] and len(sys.argv) == 3 and sys.argv[2] in E4_ORDER:
        print(location(sys.argv[2])[0])
        return
    if sys.argv[1:] != ["list"]:
        sys.exit(__doc__)
    for name in E4_ORDER:
        path, pin = location(name)
        if not os.path.exists(path):
            print(f"{name}\tMISSING\t{path}")
            continue
        sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
        state = "unpinned" if pin is None else ("pin-ok" if sha == pin else "PIN-MISMATCH")
        print(f"{name}\t{len(three_field_rows(path))}\t{sha}\t{state}\t{path}")


if __name__ == "__main__":
    main()
