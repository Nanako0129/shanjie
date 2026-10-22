"""E4 (b): the ONE tokenizer that counting (count.py) and decoding (harness.py) both call.

A text is cut into maximal runs of three kinds:
  han    [一-鿿]+  (the same pattern as experiments/s2/build_counts.HAN; test_e1 asserts they are equal)
  x      a run of digits ([0-9０-９]) or a run of Latin letters ([A-Za-zＡ-Ｚａ-ｚ]); every such run is ONE token "<X>".
         "iPhone15" is two runs ("iPhone", "15"), so two <X> tokens: a digit run and a Latin run are separate runs.
  other  everything else (punctuation, spaces, kana, ...), one run per maximal stretch.
Each element is (kind, raw, token): token is "<X>" for kind x and the raw text otherwise.

Consumers must call `mapping.tokenize` through the module (not `from mapping import tokenize`), so the test that swaps the
function sees both of them change.
"""
import re

X = "<X>"
HAN = re.compile(r"[一-鿿]+")
_RUN = re.compile(r"(?P<han>[一-鿿]+)|(?P<x>[0-9０-９]+|[A-Za-zＡ-Ｚａ-ｚ]+)|(?P<other>[^一-鿿0-9０-９A-Za-zＡ-Ｚａ-ｚ]+)")


def tokenize(text):
    """[(kind, raw, token)] covering the whole text, kind in {"han", "x", "other"}."""
    out = []
    for m in _RUN.finditer(text):
        kind = m.lastgroup
        out.append((kind, m.group(), X if kind == "x" else m.group()))
    return out


def han_runs_with_left(text):
    """[(run, left_is_x)] for every maximal Han run: left_is_x is True when the token right before it is an <X>
    (no space or punctuation in between). Counting uses it to start the run from <X> instead of <s>."""
    toks = tokenize(text)
    return [(raw, i > 0 and toks[i - 1][0] == "x") for i, (kind, raw, _) in enumerate(toks) if kind == "han"]


def ends_with_x(text):
    """Decoding: True when the last token of the left context is an <X> (the text ends in a digit or Latin run)."""
    toks = tokenize(text)
    return bool(toks) and toks[-1][0] == "x"
