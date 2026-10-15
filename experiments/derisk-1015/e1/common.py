"""Shared paths and provenance helpers for the E1/E4(b) tools. Standard library only."""
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
for _sub in ("reference/proto", "tools", "experiments/s2"):
    _p = os.path.join(ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# bytes-per-record of the on-disk size measure (README "Size measure"); the code that writes the files uses the same numbers
SJLM_BYTES_PER_ENTRY = 8       # next_id u32 + count u32
TRI_BYTES_PER_ENTRY = 8        # next_id u32 + count u32
TRI_BYTES_PER_CONTEXT = 20     # ctx_u u32 + ctx_v u32 + ctx_total u64 + ctx_off u32


def sha256_file(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def git_state():
    """(short commit, dirty flag) of the checkout this file lives in."""
    def run(*a):
        return subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True).stdout.strip()
    return run("rev-parse", "--short", "HEAD"), bool(run("status", "--porcelain", "--", "experiments/derisk-1015", "reference", "tools", "data"))


def provenance(inputs=(), extra=None):
    """Dict recorded next to every output: commit, argv, SHA-256 of each input file."""
    commit, dirty = git_state()
    d = {"commit": commit + ("+dirty" if dirty else ""), "argv": sys.argv, "python": sys.version.split()[0],
         "inputs": {p: sha256_file(p) for p in inputs}}
    d.update(extra or {})
    return d


def write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
