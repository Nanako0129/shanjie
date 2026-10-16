"""Helpers for the unit tests (synthetic lexicons and models). Not a test module."""
import os
import pickle
from unittest import mock

import rcommon
import build_lm  # noqa: E402
import ime  # noqa: E402
import lm as L  # noqa: E402

# A small lexicon in the data file format (`reading word score`). 吧/巴 copy each other's scores like the shipped file; 好吧 is a word.
LEXICON = """# format org.openvanilla.mcbopomofo.sorted
ㄅㄚ˙ 吧 -2.0
ㄅㄚ 吧 -2.0
ㄅㄚ˙ 巴 -2.1
ㄅㄚ 巴 -2.1
ㄏㄠˇ 好 -1.5
ㄏㄠˇ-ㄅㄚ˙ 好吧 -5.0
ㄏㄠˇ-ㄅㄚ 好吧 -5.0
ㄋㄧˇ 你 -1.5
ㄌㄜ˙ 了 -1.0
ㄌㄧㄠˇ 了 -2.5
ㄇㄚ˙ 嗎 -2.0
ㄇㄚ 嗎 -2.2
ㄇㄚˇ 嗎 -3.0
ㄇㄚ˙ 嘛 -2.0
ㄇㄚˊ 嘛 -2.5
ㄛ 喔 -3.0
ㄛˊ 喔 -3.5
ㄋㄚ˙ 哪 -4.0
ㄋㄚˇ 哪 -2.0
ㄋㄚ 哪 -4.5
ㄈㄥ 風 -2.0
"""


def write(dirpath, name, text):
    p = os.path.join(dirpath, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def lexicon(dirpath, text=LEXICON, name="lex.txt"):
    return ime.Lexicon(write(dirpath, name, text))


def sjlm(dirpath, uni, bi, name="m.sjlm"):
    """Write a bigram model through tools/build_lm.py's build() (PRUNE 2: a bigram needs a count >= 2 to be kept) and load it without classes."""
    work = os.path.join(dirpath, "work-" + name)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "counts-a.pkl"), "wb") as f:
        pickle.dump({"uni": dict(uni), "bi": dict(bi)}, f)
    with mock.patch.object(build_lm, "WORK", work), mock.patch.object(build_lm, "CORPORA", [("counts-a.pkl", 1)]):
        data, *_ = build_lm.build(cls={})
    path = os.path.join(dirpath, name)
    with open(path, "wb") as f:
        f.write(data)
    return path, L.BigramLM(path, classes=False)


U = rcommon.U
