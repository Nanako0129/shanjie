"""The one context rule of P3, shared by the trainer (p2-distill/train_p2.py) and the scorer (score_p3.py).

key_for(prefix, cand, context): the k of the sequence `[BOS] k c`.
  2  -> tinylm.row_key(prefix, 2) (the engine's context_key: <= 2 Han characters of the last Han run), cut from the left to fit CTX - 1 - |c|.
  64 -> the last CTX - 1 - |c| Han characters of the prefix, every non-Han character (punctuation, ASCII, digits, spaces) skipped.
Any other context is a ValueError. For 64 a caller scoring many candidates of one row filters once, han = han_only(prefix), and passes it in.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "s7a-tinylm"))
import tinylm  # noqa: E402
from tinylm import CTX  # noqa: E402


def is_han(c):
    return any(a <= ord(c) <= b for a, b in tinylm._HAN)


def han_only(prefix):
    return "".join(c for c in prefix if is_han(c))


def k_cross(prefix, n):
    """The last n Han characters of prefix, punctuation and other characters skipped over; "" when n <= 0 (as l0core.k_cross, exp/l0-listwise)."""
    return han_only(prefix)[-n:] if n > 0 else ""


def key_for(prefix, cand, context, han=None):
    room = CTX - 1 - len(cand)
    if context == 64:
        h = han_only(prefix) if han is None else han
        return h[-room:] if room > 0 else ""
    if context == 2:
        k = tinylm.row_key(prefix, 2)
        return k[len(k) - room:] if room < len(k) else k
    raise ValueError(f"context must be 2 or 64, got {context!r}")
