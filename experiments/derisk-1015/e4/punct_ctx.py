"""E4 (a): the left context no longer resets at mid-sentence punctuation (docs/contracts/derisk-1015.md section 3, E4).

Today (lm.py context_key, the same rule as core/src/learn.rs context_key): the context key is the trailing run of Han characters,
at most 2, so ANY non-Han character before the cursor (punctuation, digit, English, space) yields "" and the first word is decoded
from "<s>" (lm.history). Here, a trailing run of MID characters is skipped first, so "我今天吃飯，" keys on "吃飯" (history() then
takes "吃飯" or "飯", whichever has bigram history), the last word before the comma. Everything else is unchanged: sentence-final
punctuation (FINAL), quotes, brackets, digits, English and spaces still end the run, so the key is "" and the first word starts at "<s>".

MID: the clause-level separators, each in its ASCII, full-width, and small/half-width forms.
  ,  U+002C   ;  U+003B   :  U+003A        ASCII
  ，U+FF0C   ；U+FF1B   ：U+FF1A           full-width
  、U+3001   ､ U+FF64                      ideographic comma, half-width ideographic comma
  ﹐U+FE50   ﹑U+FE51   ﹔U+FE54   ﹕U+FE55   small forms
FINAL (kept resetting; listed for the tests and the README): 。 U+3002, ｡ U+FF61, . U+002E, ． U+FF0E, ？ U+FF1F, ? U+003F,
！ U+FF01, ! U+0021, … U+2026, ⋯ U+22EF.
Not in either list: dashes, quotes, brackets, middle dots, spaces: they keep resetting (nothing here changes them).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "reference/proto"))
import lm as L  # noqa: E402

MID = frozenset(",;:，；：、､﹐﹑﹔﹕")
BASE_KEY = L.context_key   # kept here: the E4 run replaces lm.context_key by context_key_mid, which must not call itself
FINAL = frozenset("。｡.．？?！!…⋯")


def context_key_mid(prefix):
    """lm.context_key after dropping the trailing run of MID characters."""
    end = len(prefix)
    while end and prefix[end - 1] in MID:
        end -= 1
    return BASE_KEY(prefix[:end])
