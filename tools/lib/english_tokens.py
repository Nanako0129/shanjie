"""English tokens beside Chinese, and the split of the corpus into table / tuning probe / confirmation probe documents (docs/spec/english-mixed.md §16.5).
The one copy of both rules: tools/build_english.py (the table) and tools/build_english_probe.py (the probes) call these functions, so the table and the
probes agree on what a token is and never share a document.

Token: a maximal run of ASCII letters and digits that starts with a letter and is at most 32 characters, lower-cased ([a-z][a-z0-9]{0,31}), in a
sentence that holds a Han character, with a Han character or the sentence's start or end on at least one side (blanks skipped).
Not a token: a run right next to one of @ . / : # (addresses, paths, URLs, file names, tags; `://` is covered by `:` and `/`), and, in a forum push
unit, a run inside the head of the line (`推`, `噓` or `→`, then an account, then an optional colon).

About that head rule. In the corpus the model is counted from (docs/spec/models.md §4.1), a push unit does NOT carry the pusher's account: the cleaner
that makes the units (the research repository's forum cleaner) reads the account and the content of a push as separate fields of the page and
writes only the content (the account is used to join adjacent pushes of one person, then dropped). So on those files the rule finds nothing. It is kept, and locked by a test on synthetic input, for push
text that still has the head (a raw export): there the account sits right behind a Han character and would count as English beside Chinese.

Split: bucket = first byte of sha256(source + TAB + document id) mod 10; 0-7 the table, 8 the tuning probe, 9 the confirmation probe. A document
is a wiki article (id: its title), a forum article with its pushes (id: board/article, the same for the body file and the push file), a g0v message
(id: the first column), a sentence of the colloquial pool (id: the sentence).
"""
import hashlib
import re

SOURCES = ("wiki", "pool", "g0v", "forum")
MAX_LEN = 32
RUN = re.compile(r"[A-Za-z0-9]+")
ADJACENT = frozenset("@./:#")
PUSH_HEAD = re.compile(r"\s*[推噓→]\s*[A-Za-z0-9_]+\s*:?")
HAN_LO, HAN_HI = "一", "鿿"   # tools/lib/build_counts.py HAN
LEFT_MAX = 16


def is_han(c):
    return HAN_LO <= c <= HAN_HI


def doc_digest(source, doc_id):
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    return hashlib.sha256(f"{source}\t{doc_id}".encode("utf-8")).digest()


def bucket(source, doc_id):
    return doc_digest(source, doc_id)[0] % 10


def split_of(b):
    return "table" if b <= 7 else "tune" if b == 8 else "confirm"


def _before(s, at):
    """Index of the last non-blank character before `at`, or -1."""
    i = at - 1
    while i >= 0 and s[i].isspace():
        i -= 1
    return i


def _after(s, at):
    """Index of the first non-blank character from `at`, or len(s)."""
    i = at
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def tokens(sentence, push=False, push_head=True):
    """-> [(start, end, token)] of `sentence` (see the module docstring). push: the sentence is a line of a forum push unit.
    push_head=False switches the head rule off (the test that shows what the rule is for)."""
    if not any(is_han(c) for c in sentence):
        return []
    head = PUSH_HEAD.match(sentence) if push and push_head else None
    head_end = head.end() if head else 0
    out = []
    for m in RUN.finditer(sentence):
        a, b = m.span()
        run = m.group()
        if not ("a" <= run[0].lower() <= "z") or len(run) > MAX_LEN or a < head_end:
            continue
        if (a > 0 and sentence[a - 1] in ADJACENT) or (b < len(sentence) and sentence[b] in ADJACENT):
            continue
        i, j = _before(sentence, a), _after(sentence, b)
        if (i < 0 or is_han(sentence[i])) or (j >= len(sentence) or is_han(sentence[j])):
            out.append((a, b, run.lower()))
    return out


def text_end_before(sentence, at):
    """Where the text in front of a token ends once the blanks before it are skipped: the context of the token is read up to here, since a
    person typing does not type that blank before the English."""
    return _before(sentence, at) + 1


def left_text(sentence, at):
    """The probe's left text: the Han characters right before the token (blanks before it skipped), LEFT_MAX at most, stopping at anything else."""
    end = text_end_before(sentence, at)
    i = end
    while i > 0 and end - i < LEFT_MAX and is_han(sentence[i - 1]):
        i -= 1
    return sentence[i:end]
