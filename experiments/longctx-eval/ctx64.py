"""Shared pieces of the long-context evaluation sets (README.md in this folder, section 共同規則)."""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(HERE)
for _d in (os.path.join(EXP, "e2-chateval"), HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)
import e2common  # noqa: E402

CTX_MAX = 64
NON_HAN_TAIL = re.compile(r"[^一-鿿]+\Z")      # same Han range as e2common.HAN
SEP = str.maketrans({c: " " for c in "\r\n\t|#"})


def normalize_ctx(before):
    """The three steps in the README's order: separators -> space, strip trailing non-Han, keep the last 64 characters."""
    return NON_HAN_TAIL.sub("", before.translate(SEP))[-CTX_MAX:]


def segments_with_ctx(text):
    """[(sentence, context)] for every e2common.HAN match; the context never reaches into another message."""
    return [(m.group(), normalize_ctx(text[:m.start()])) for m in e2common.HAN.finditer(text)]


def check_rows(path, expect_n):
    """The per-file format check; exits non-zero with a message on the first failure."""
    with open(path, encoding="utf-8", newline="") as f:
        lines = f.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != expect_n:
        sys.exit(f"check_rows: STOP, {path} has {len(lines)} lines, expected {expect_n}")
    for i, line in enumerate(lines, 1):
        parts = line.split("|")
        if len(parts) != 3:
            sys.exit(f"check_rows: STOP, {path} line {i} has {len(parts)} fields")
        if line.startswith("#") or any(ch in parts[0] for ch in "\r\t#"):
            sys.exit(f"check_rows: STOP, {path} line {i} breaks the context rules")


def sha256_stream(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
