#!/usr/bin/env python3
"""Build a standalone VanguardFactoryDict4Typing.txtMap (TextMap v2.0) directly from Shanjie data.

Zero lexicon entries are copied from VanguardLexicon. Unigrams, contextual bigrams,
optional trigrams, and optional debugger-mined overrides are scored to match Shanjie's
model definitions and Homa's PathFinder / weightedScore semantics, then serialized and
strictly self-validated against VanguardTrie.TrieIO rules.

Overrides:
  * --extra-ngrams rows with an EMPTY context are unigram overrides: the word's unigram
    probability at that reading is replaced if the word exists there, otherwise the word is added
    (Debugger actions fillMissingCount/adjustCount vs addUnigram). Counts go to build_stats.json.
  * --base-txtmap PATH: skip the Shanjie build; read an existing TextMap 2.0 file (e.g. the
    Vanguard factory dict), apply only the --extra-ngrams overrides (unigram and contextual) and
    write it back. Untouched lines are preserved verbatim.

Simplified column (--simplified, off by default): vChewing reads the Simplified candidates of a unigram line from its second
column (EntryType 5) and the Traditional ones from the third (6), and a contextual line's typeID 5 only in Simplified mode, 6
only in Traditional mode. With the flag, every word and every contextual entry also gets its Simplified form, converted word by
word with OpenCC's TSPhrases.txt (longest match) and TSCharacters.txt (Apache-2.0; hash-checked, read from --opencc-dir; the
tables are not in the repo). Without it the output is byte-for-byte what it was. --vendor TEXT writes one meta unigram
(key _VENDOR, value TEXT, score 0, typeID 2).

--compare-factory PATH (the user's own vChewing factory dictionary; it is only read) counts line categories in that file and in the
output and exits 2 when a category the factory file has is absent from the output and not in FACTORY_ABSENT_OK; see
docs/spec/vanguard-textmap.md section 8.2.

Purpose: tools/vanguard_textmap.py generates the Shanjie phonetic dictionary in the TextMap 2.0 layout
that vChewing's Vanguard typing engine reads (VanguardFactoryDict4Typing.txtMap). The spec page is
docs/spec/vanguard-textmap.md.

Licence: the whole output is distributed under CC BY-SA 4.0. With --simplified the Simplified forms are produced with OpenCC's
TSPhrases.txt and TSCharacters.txt (Apache-2.0). It also carries MIT parts (McBopomofo's
mcbpmf-data.txt and sandhi-add.tsv; full text LICENSES/McBopomofo-MIT.txt) and BSD parts (libtabe's phrases);
their notices must be kept. A build that includes the name layer and the IT pack also carries
name-suggestion-index parts (BSD-3-Clause; full text LICENSES/name-suggestion-index-BSD-3-Clause.txt; keep the
copyright notice, do not use its authors' names to endorse the output) and parts under the Open Government Data
License, version 1.0 (政府資料開放授權條款第 1 版; name layer, IT pack; name the providing agency). This list is not complete: the
notices and credits to keep are the ones in each row of LICENSES/data.md. The companion files that --out-dict
copies belong to vChewing, not to Shanjie, and are copied only from the directory the user passes.

Reproduce the released file (output sha256 7f5d0a92...0d130, 620,219 entries, 478,784 keys): take this file from
a checkout that has it, open a second worktree of data commit 1a9899ac (git worktree add --detach <path> 1a9899ac)
whose data/lm/ matches that commit's data/*.sha256, and run, with <out> outside every repo work tree:

  python3 tools/vanguard_textmap.py --shanjie-root <worktree of 1a9899ac> \
    --out-txtmap <out>/VanguardFactoryDict4Typing.txtMap \
    --packs acg,it,meme,beauty --kn-beta 0.75 --uni-lam 0.7 --bi-mode blend --bi-lam 0.7 --bi-budget 400000 \
    --bi-margin 0.05 --validate

build_stats.json (next to the output as <output file name>.build_stats.json, or inside --out-dict) holds no absolute
path: paths are relative to --shanjie-root, or just the file name when outside it. Its "inputs" records every data
file read (path, sha256) and the git commit and dirtiness of --shanjie-root.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import functools
import glob
import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import unicodedata

import numpy as np

VARIANT_GAP = 0.01
UNSEEN_OVERLAY_PENALTY = 1.0
D_TRI = 0.75
TRI_FLOOR = 0.5
SCORE_CEIL = -0.001
VALID_PACKS = ("acg", "it", "meme", "beauty", "quotes")
_EDGE = (" ", "\t", "\r", "\x0b", "\x0c", "\u00a0")
_DECIMAL_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$", re.ASCII)
_NEWLINE_CHARS = frozenset(("\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x1f", "\x85", "\u2028", "\u2029"))
_TONE_MARKS = frozenset(("ˊ", "ˇ", "ˋ", "˙"))
# OpenCC commit 3ac34aa (Apache-2.0): the only Traditional-to-Simplified tables --simplified reads. Nothing on the command line
# or in the environment can skip or replace this check.
OPENCC_SHA256 = {
    "TSPhrases.txt": "9a23666e95c97dbf8668b5d71ca19f09f33b4f3a3aa9e05677dab4b608cef102",
    "TSCharacters.txt": "9ff46a7d30e5765375eb13d33f2b03a34d298913caf2b120380679f33ae1642d",
}
VENDOR_KEY = "_VENDOR"
META_TYPE_ID = 2
# --compare-factory: categories (labels as count_categories writes them) the output may lack although the factory file has them. One line
# per entry with the reason in docs/spec/vanguard-textmap.md section 8.3; a test keeps the two lists equal.
FACTORY_ABSENT_OK = frozenset({
    "A typeID 7", "A typeID 9", "A typeID 10", "A typeID 11",
    "C typeID 8",
    "meta key _BUILD_TIMESTAMP", "meta key _NORM",
    "header key DEFAULT_PROB_7", "header key DEFAULT_PROB_9", "header key DEFAULT_PROB_10", "header key DEFAULT_PROB_11",
})
_SECTION_HEADER = "#PRAGMA:VANGUARD_HOMA_LEXICON_HEADER"
_SECTION_VALUES = "#PRAGMA:VANGUARD_HOMA_LEXICON_VALUES"
_SECTION_KEY_MAP = "#PRAGMA:VANGUARD_HOMA_LEXICON_KEY_LINE_MAP"


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def default_opencc_dir() -> str:
    return os.path.join(os.path.expanduser(os.environ.get("SHANJIE_CACHE", "~/.cache/shanjie")), "sources", "opencc")


def load_opencc(opencc_dir: str) -> tuple[dict[str, str], dict[str, str]]:
    """(TSPhrases, TSCharacters) as {key: first value} after checking both files against OPENCC_SHA256; exit 2 when one is missing or differs."""
    tables = []
    for name, want in OPENCC_SHA256.items():
        path = os.path.join(opencc_dir, name)
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            die(f"OpenCC table not found: {path} (expected sha256 {want}); give the folder with --opencc-dir")
        got = hashlib.sha256(raw).hexdigest()
        if got != want:
            die(f"OpenCC table {path} has sha256 {got}, expected {want}")
        table = {}
        for line in raw.decode("utf-8").split("\n"):
            p = line.split("\t")
            if len(p) == 2 and not line.startswith("#"):
                table[p[0]] = p[1].split(" ")[0]
        tables.append(table)
    return tables[0], tables[1]


def make_t2s(phrases: dict[str, str], chars: dict[str, str]):
    """Word-level Traditional-to-Simplified: left to right, the longest TSPhrases key at each position, else the character's TSCharacters value, else the character as is."""
    longest = max(map(len, phrases), default=0)

    @functools.lru_cache(maxsize=None)
    def t2s(word: str) -> str:
        out, i = [], 0
        while i < len(word):
            for n in range(min(longest, len(word) - i), 0, -1):
                hit = phrases.get(word[i : i + n])
                if hit is not None:
                    out.append(hit)
                    i += n
                    break
            else:
                out.append(chars.get(word[i], word[i]))
                i += 1
        return "".join(out)

    return t2s


def dedupe(items):
    return list(dict.fromkeys(items))


def is_bopomofo_symbol(word: str) -> bool:
    if len(word) != 1:
        return False
    cp = ord(word)
    return (0x3105 <= cp <= 0x312F) or (word in _TONE_MARKS)


def clamp_round(score: float, decimals: int) -> float:
    if not math.isfinite(score):
        raise ValueError(f"non-finite score: {score!r}")
    s = min(score, SCORE_CEIL)
    s = round(s, decimals)
    if s == 0.0 or s > SCORE_CEIL:
        s = SCORE_CEIL
    return s


def format_prob(value: float, decimals: int) -> str:
    s = clamp_round(value, decimals)
    txt = f"{s:.{decimals}f}"
    if "." in txt:
        txt = txt.rstrip("0").rstrip(".")
    if txt in ("", "-0", "0"):
        return str(SCORE_CEIL)
    return txt


def is_structural_control(ch: str) -> bool:
    if ch in _NEWLINE_CHARS:
        return True
    return unicodedata.category(ch) == "Cc"


def validate_bare_value(value: str) -> None:
    if not value:
        raise ValueError("Entry value must not be empty.")
    for ch in value:
        if is_structural_control(ch):
            raise ValueError(f"Entry value {value!r} contains control character U+{ord(ch):04X}.")


def validate_grouped_token(token: str) -> None:
    if not token:
        raise ValueError("Grouped cells must not contain empty tokens.")
    for ch in token:
        if ord(ch) != 0x07 and is_structural_control(ch):
            raise ValueError(f"Grouped token {token!r} contains control character U+{ord(ch):04X}.")


def validate_context_tokens(ctx: tuple[str, ...]) -> None:
    if not (1 <= len(ctx) <= 2):
        raise ValueError(f"TextMap v2.0 supports 1..2 context tokens, got {len(ctx)}.")
    for tok in ctx:
        validate_grouped_token(tok)
    if "*" in ctx:
        if not (len(ctx) == 2 and ctx[1] == "*"):
            raise ValueError(f"Wild card '*' only allowed as second token of 2-token context, got {ctx!r}.")


def escape_grouped_value(value: str) -> str:
    validate_grouped_token(value)
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "|":
            out.append("\\|")
        elif ch == " ":
            out.append("\\s")
        elif ord(ch) == 0x07:
            out.append("\\a")
        else:
            out.append(ch)
    return "".join(out)


def encode_grouped_values(values: list[str] | tuple[str, ...]) -> str:
    if not values:
        return "\x07"
    return "|".join(escape_grouped_value(v) for v in values)


@dataclass
class SJLMModel:
    vocab: list[str]
    word_to_id: dict[str, int]
    N: int
    eos_total: int
    D: float
    uni: np.ndarray
    ctx: np.ndarray
    tot: np.ndarray
    off: np.ndarray
    nxt: np.ndarray
    cnt: np.ndarray
    back: np.ndarray
    ctx_pos: dict[int, int]
    sha256: bytes
    _buf: bytes

    @classmethod
    def load(cls, path: str, build_ctx_pos: bool = True) -> "SJLMModel":
        with open(path, "rb") as f:
            b = f.read()
        if b[:8] != b"SJLM0001":
            raise ValueError(f"{path}: bad SJLM magic")
        sha = hashlib.sha256(b).digest()
        V, N, eos_total, D = struct.unpack_from("<IQQd", b, 8)
        p = 8 + struct.calcsize("<IQQd")
        vocab = [None] * V
        for i in range(V):
            n = b[p] | (b[p + 1] << 8)
            p += 2
            vocab[i] = b[p : p + n].decode("utf-8")
            p += n
        uni = np.frombuffer(b, "<u8", V, p)
        p += 8 * V
        (C,) = struct.unpack_from("<I", b, p)
        p += 4
        ctx = np.frombuffer(b, "<u4", C, p)
        p += 4 * C
        tot = np.frombuffer(b, "<u8", C, p)
        p += 8 * C
        off = np.frombuffer(b, "<u4", C + 1, p)
        p += 4 * (C + 1)
        (E,) = struct.unpack_from("<I", b, p)
        p += 4
        nxt = np.frombuffer(b, "<u4", E, p)
        p += 4 * E
        cnt = np.frombuffer(b, "<u4", E, p)
        p += 4 * E
        if p != len(b):
            raise ValueError(f"{path}: trailing bytes in SJLM file")
        if C > 0:
            if np.all(off[:-1] < off[1:]):
                kept_sum = np.add.reduceat(cnt.astype(np.float64) - D, off[:-1])
            else:
                kept_sum = np.zeros(C, dtype=np.float64)
                for i in range(C):
                    lo, hi = int(off[i]), int(off[i + 1])
                    if hi > lo:
                        kept_sum[i] = float((cnt[lo:hi].astype(np.float64) - D).sum())
            back = 1.0 - kept_sum / tot.astype(np.float64)
        else:
            back = np.zeros(0, dtype=np.float64)
        word_to_id = {w: i for i, w in enumerate(vocab)}
        ctx_pos = {int(ctx[i]): i for i in range(C)} if build_ctx_pos else {}
        return cls(
            vocab=vocab,
            word_to_id=word_to_id,
            N=N,
            eos_total=eos_total,
            D=D,
            uni=uni,
            ctx=ctx,
            tot=tot,
            off=off,
            nxt=nxt,
            cnt=cnt,
            back=back,
            ctx_pos=ctx_pos,
            sha256=sha,
            _buf=b,
        )

    def count(self, word: str) -> int:
        wid = self.word_to_id.get(word)
        return int(self.uni[wid]) if wid is not None else 0

    def cond_prob_ids(self, vi: int, wi: int, pb: float) -> tuple[float, int]:
        """Exact SJLM0001 P(w | v) using back-off pb, plus raw bigram count."""
        cidx = self.ctx_pos.get(vi)
        if cidx is None:
            return pb, 0
        lo = int(self.off[cidx])
        hi = int(self.off[cidx + 1])
        b_v = float(self.back[cidx])
        if wi >= 0 and hi > lo:
            sub = self.nxt[lo:hi]
            j = int(np.searchsorted(sub, wi))
            if j < (hi - lo) and int(sub[j]) == wi:
                k = int(self.cnt[lo + j])
                return (k - self.D) / float(self.tot[cidx]) + b_v * pb, k
        return b_v * pb, 0


def load_kn(path: str, expected_v: int, expected_sha: bytes) -> tuple[np.ndarray, int, float]:
    with open(path, "rb") as f:
        b = f.read()
    if b[:8] != b"SJKN0002" or len(b) != 64 + 4 * expected_v:
        raise ValueError(f"{path}: bad SJKN0002 side file")
    (v,) = struct.unpack_from("<I", b, 8)
    if v != expected_v:
        raise ValueError(f"{path}: kn vocab size {v} != {expected_v}")
    if b[12:44] != expected_sha:
        raise ValueError(f"{path}: kn side file was built for another bigram model")
    file_beta, total = struct.unpack_from("<dQ", b, 48)
    if total == 0:
        raise ValueError(f"{path}: kn total is zero")
    np1 = np.frombuffer(b, "<u4", expected_v, 64).astype(np.uint64) + 1
    return np1, int(total), float(file_beta)


@dataclass
class TriTableFast:
    V: int
    D: float
    key: np.ndarray
    tot: np.ndarray
    off: np.ndarray
    nxt: np.ndarray
    cnt: np.ndarray
    back: np.ndarray
    _buf: bytes

    @classmethod
    def load(cls, path: str, expected_v: int, expected_sha: bytes) -> "TriTableFast":
        with open(path, "rb") as f:
            b = f.read()
        magic, V, D, sha, C, E = struct.unpack_from("<8sId32sII", b, 0)
        if magic != b"SJT30001":
            raise ValueError(f"{path}: bad SJT30001 magic")
        if D != D_TRI:
            raise ValueError(f"{path}: unexpected trigram discount {D}")
        if V != expected_v or sha != expected_sha:
            raise ValueError(f"{path}: trigram table was built for another bigram model")
        p = 60
        key = np.frombuffer(b, "<u8", C, p)
        p += 8 * C
        tot = np.frombuffer(b, "<u8", C, p)
        p += 8 * C
        off = np.frombuffer(b, "<u4", C + 1, p)
        p += 4 * (C + 1)
        nxt = np.frombuffer(b, "<u4", E, p)
        p += 4 * E
        cnt = np.frombuffer(b, "<u4", E, p)
        p += 4 * E
        if p != len(b):
            raise ValueError(f"{path}: trailing bytes in SJT30001")
        if C > 0:
            if np.all(off[:-1] < off[1:]):
                kept_sum = np.add.reduceat(cnt.astype(np.float64) - D, off[:-1])
            else:
                kept_sum = np.zeros(C, dtype=np.float64)
                for i in range(C):
                    lo, hi = int(off[i]), int(off[i + 1])
                    if hi > lo:
                        kept_sum[i] = float((cnt[lo:hi].astype(np.float64) - D).sum())
            back = 1.0 - kept_sum / tot.astype(np.float64)
        else:
            back = np.zeros(0, dtype=np.float64)
        return cls(V=V, D=D, key=key, tot=tot, off=off, nxt=nxt, cnt=cnt, back=back, _buf=b)


def parse_variant_order(path: str) -> list[tuple[str, str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    rows: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        flds = line.split("\t")
        if len(flds) != 4 or any(not x or x.startswith(_EDGE) or x.endswith(_EDGE) for x in flds) or flds[1] == flds[2]:
            raise ValueError(f"bad variant-order row: {line!r}")
        key = (flds[0], flds[1])
        if key in seen:
            raise ValueError(f"duplicate variant-order row: {key!r}")
        seen.add(key)
        rows.append((flds[0], flds[1], flds[2]))
    lesses = {l for _, l, _ in rows}
    if any(m in lesses for _, _, m in rows):
        raise ValueError("variant-order chain: 'more' word appears as 'less' in another row")
    return rows


def apply_variants(by_reading: dict[str, list[list]], variant_rows: list[tuple[str, str, str]]) -> None:
    for reading, less, more in variant_rows:
        entries = by_reading.get(reading)
        if not entries:
            raise ValueError(f"variant-order reading missing: {reading}")
        top = max((e[1] for e in entries if e[0] == more), default=None)
        if top is None or not any(e[0] == less for e in entries):
            raise ValueError(f"variant-order row names missing spelling: {reading} {less}->{more}")
        limit = top - VARIANT_GAP
        for e in entries:
            if e[0] == less and e[1] > limit:
                e[1] = limit
        entries.sort(key=lambda e: -e[1])


def parse_demote(path: str, by_reading: dict[str, list[list]], all_words: set[str]) -> dict[tuple[str, str], float]:
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    lines = text.split("\n")
    rows: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    for i, line in enumerate(lines):
        if i < len(lines) - 1 and line.endswith("\r"):
            line = line[:-1]
        if not line or line[0] == "#":
            continue
        flds = line.split("\t")
        if len(flds) != 5 or any(not x or x.startswith(_EDGE) or x.endswith(_EDGE) for x in flds):
            raise ValueError(f"bad demote row: {line!r}")
        reading, word, delta = flds[0], flds[1], flds[2]
        if not _DECIMAL_RE.match(delta):
            raise ValueError(f"bad demote delta: {delta!r}")
        d = float(delta)
        if not (math.isfinite(d) and d > 0.0) or (reading, word) in seen:
            raise ValueError(f"invalid or duplicate demote row: {line!r}")
        seen.add((reading, word))
        rows.append((reading, word, d))
    for reading, word, _ in rows:
        if reading == "*":
            ok = word in all_words
        else:
            ok = any(e[0] == word for e in by_reading.get(reading, ()))
        if not ok:
            raise ValueError(f"demote row matches no lexicon entry: {reading}\t{word}")
    return {(r, w): d for r, w, d in rows}


def load_lexicon(
    shanjie_root: str,
    include_colloq_readings: bool,
    use_names: bool,
    packs: list[str],
) -> tuple[dict[str, list[list]], set[str], set[str], set[str]]:
    """Load Shanjie base lexicon, overlays, names, and optional packs.

    Returns:
      by_reading: reading -> [[word, score], ...]
      capping_words: words subject to cap_overlay (overlay-add + names + unforced packs)
      forced_pack_words: pack words tagged 'acg-oov' (never capped, no unseen penalty)
      unseen_pack_words: non-forced pack words (capped if seen in corpus, no unseen penalty)
    """
    lex_dir = os.path.join(shanjie_root, "data", "lexicon")
    base_path = os.path.join(lex_dir, "mcbpmf-data.txt")
    by_reading: dict[str, list[list]] = defaultdict(list)
    seen_pairs: dict[tuple[str, str], int] = {}

    with open(base_path, encoding="utf-8") as f:
        for line in f:
            if not line or line[0] in "#_\n\r":
                continue
            parts = line.split()
            if len(parts) != 3:
                continue
            reading, word, score = parts[0], parts[1], float(parts[2])
            syl_count = reading.count("-") + 1
            if len(word) != syl_count and not is_bopomofo_symbol(word):
                continue
            key = (reading, word)
            idx = seen_pairs.get(key)
            if idx is not None:
                if score > by_reading[reading][idx][1]:
                    by_reading[reading][idx][1] = score
            else:
                seen_pairs[key] = len(by_reading[reading])
                by_reading[reading].append([word, score])

    overlay_files: list[tuple[str, str]] = [
        (os.path.join(lex_dir, "overlay-add.tsv"), "overlay"),
        (os.path.join(lex_dir, "sandhi-add.tsv"), "sandhi"),
    ]
    if use_names:
        overlay_files.append((os.path.join(lex_dir, "names-add.tsv"), "names"))
    for p in packs:
        overlay_files.append((os.path.join(shanjie_root, "data", "packs", f"{p}-add.tsv"), "pack"))

    capping_words: set[str] = set()
    forced_pack_words: set[str] = set()
    unseen_pack_words: set[str] = set()

    for path, kind in overlay_files:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"missing lexicon file: {path}")
        with open(path, encoding="utf-8") as f:
            for line in f:
                raw = line.rstrip("\n")
                if not raw:
                    continue
                parts = raw.split("\t")
                if len(parts) != 4:
                    raise ValueError(f"{path}: expected 4 tab-separated columns, got {len(parts)}: {raw!r}")
                reading, word, score_s, tag = parts
                if tag == "colloq" and not include_colloq_readings:
                    continue
                score = float(score_s)
                key = (reading, word)
                if key in seen_pairs:
                    raise ValueError(f"{path}: duplicates an existing entry {key!r}")
                seen_pairs[key] = len(by_reading[reading])
                by_reading[reading].append([word, score])
                if kind in ("overlay", "names"):
                    capping_words.add(word)
                elif kind == "pack":
                    if tag == "acg-oov":
                        forced_pack_words.add(word)
                    else:
                        capping_words.add(word)
                        unseen_pack_words.add(word)

    for entries in by_reading.values():
        entries.sort(key=lambda e: -e[1])

    return by_reading, capping_words, forced_pack_words, unseen_pack_words


def parse_extra_ngrams(path: str, decimals: int) -> dict[tuple[str, str, tuple[str, ...]], float]:
    out: dict[tuple[str, str, tuple[str, ...]], float] = {}
    with open(path, encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            line = raw.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                raise ValueError(f"{path}:{line_no}: expected at least 4 tab-separated fields")
            if parts[:4] == ["reading", "value", "context", "prob"]:
                continue
            reading, value, ctx_str, prob_str = parts[0], parts[1], parts[2], parts[3]
            if not reading or any(ch.isspace() or is_structural_control(ch) for ch in reading) or any(not tok for tok in reading.split("-")):
                raise ValueError(f"{path}:{line_no}: invalid reading {reading!r}")
            validate_bare_value(value)
            ctx = tuple(ctx_str.split("|")) if ctx_str else ()
            if ctx:  # v2: empty context = unigram override
                validate_context_tokens(ctx)
            prob = clamp_round(float(prob_str), decimals)
            key = (reading, value, ctx)
            if key not in out or prob > out[key]:
                out[key] = prob
    return out


# ---------------------------------------------------------------------------
# Strict TextMap v2.0 validator (mirrors VanguardTrieIO.swift + TextMapTrie)
# ---------------------------------------------------------------------------

def _decode_grouped_values_strict(raw_cell: str, allows_empty_cell: bool = False) -> list[str]:
    if raw_cell == "":
        if allows_empty_cell:
            return []
        raise ValueError("Empty grouped cell.")
    if raw_cell == "\x07":
        return []
    raw_bytes = raw_cell.encode("utf-8")
    result: list[str] = []
    cur = bytearray()
    is_escaping = False
    for b in raw_bytes:
        if is_escaping:
            if b == 0x5C:  # \
                cur.append(0x5C)
            elif b == 0x7C:  # |
                cur.append(0x7C)
            elif b == 0x73:  # s
                cur.append(0x20)
            elif b == 0x61:  # a
                cur.append(0x07)
            else:
                raise ValueError(f"Unknown grouped-cell escape sequence \\{chr(b)}.")
            is_escaping = False
            continue
        if b == 0x5C:
            is_escaping = True
            continue
        if b == 0x7C:
            if not cur:
                raise ValueError("Grouped cells must not contain empty tokens.")
            tok = cur.decode("utf-8")
            for ch in tok:
                if ord(ch) >= 0x80 and is_structural_control(ch):
                    raise ValueError("Grouped cells contain raw control characters.")
            result.append(tok)
            cur.clear()
            continue
        if b <= 0x20 or b == 0x7F:
            raise ValueError("Grouped cells require escaped spaces and no raw control characters.")
        cur.append(b)
    if is_escaping:
        raise ValueError("Dangling grouped-cell escape.")
    if not cur:
        raise ValueError("Grouped cells must not contain empty tokens.")
    tok = cur.decode("utf-8")
    for ch in tok:
        if ord(ch) >= 0x80 and is_structural_control(ch):
            raise ValueError("Grouped cells contain raw control characters.")
    result.append(tok)
    return result


def _parse_finite_prob(raw: str) -> float:
    val = float(raw)
    if not math.isfinite(val):
        raise ValueError(f"Non-finite probability: {raw!r}")
    if val > SCORE_CEIL:
        raise ValueError(f"Probability {val} exceeds SCORE_CEIL ({SCORE_CEIL})")
    return val


def _parse_int32(raw: str) -> int | None:
    if not raw:
        return None
    if raw[0] in "+-":
        if len(raw) == 1 or not raw[1:].isdigit():
            return None
    elif not raw.isdigit():
        return None
    v = int(raw)
    if -2147483648 <= v <= 2147483647:
        return v
    return None


def validate_value_line_strict(line: str, is_typing: bool, default_probs: dict[int, float]) -> int:
    if not line:
        raise ValueError("Empty VALUES line.")
    parts = line.split("\t")
    first = parts[0]

    # Type A: >typeID\tencodedCell
    if first.startswith(">") and len(parts) == 2:
        tid = _parse_int32(first[1:])
        if tid is None or tid not in default_probs or not math.isfinite(default_probs[tid]):
            raise ValueError("Malformed type-A VALUES line.")
        vals = _decode_grouped_values_strict(parts[1])
        return len(vals)

    # Type B: @probability\tchsCell\tchtCell
    if is_typing and len(parts) == 3 and first.startswith("@"):
        try:
            prob = _parse_finite_prob(first[1:])
        except ValueError:
            prob = None
        if prob is not None:
            v5 = _decode_grouped_values_strict(parts[1], allows_empty_cell=True)
            v6 = _decode_grouped_values_strict(parts[2], allows_empty_cell=True)
            return len(v5) + len(v6)

    # Type D: !value\ttypeID\tprob1\tctx1...
    if first.startswith("!") and len(parts) > 4:
        if len(parts) % 2 != 0:
            raise ValueError("Type-D line requires probability/context pairs.")
        pair_count = (len(parts) - 2) // 2
        if not (2 <= pair_count <= 16):
            raise ValueError(f"Type-D line must contain 2..16 entries, got {pair_count}.")
        val = first[1:]
        validate_bare_value(val)
        tid = _parse_int32(parts[1])
        if tid is None:
            raise ValueError("Invalid type-D typeID.")
        for p_idx in range(pair_count):
            _parse_finite_prob(parts[2 + p_idx * 2])
            ctx = _decode_grouped_values_strict(parts[3 + p_idx * 2])
            validate_context_tokens(tuple(ctx))
        return pair_count

    # Type C: value\tprobability\ttypeID[\tcontextCell]
    if len(parts) in (3, 4):
        tid = _parse_int32(parts[2])
        if tid is not None:
            if tid == 2:  # v2: meta lines (_NORM/_BUILD_TIMESTAMP in factory dicts) carry non-score numbers
                if not math.isfinite(float(parts[1])):
                    raise ValueError(f"Non-finite meta value: {parts[1]!r}")
            else:
                _parse_finite_prob(parts[1])
            validate_bare_value(parts[0])
            if len(parts) == 4:
                ctx = _decode_grouped_values_strict(parts[3])
                validate_context_tokens(tuple(ctx))
            return 1

    raise ValueError(f"Malformed VALUES line: {line!r}")


def contextual_items(line: str) -> list[tuple[int | None, str, tuple[str, ...]]]:
    """(typeID, value, context) of each entry of a type-C or type-D line; [] for any other line (A, B, meta)."""
    p = line.split("\t")
    if line.startswith("!") and len(p) > 4:
        return [(_parse_int32(p[1]), p[0][1:], tuple(_decode_grouped_values_strict(p[3 + 2 * i]))) for i in range((len(p) - 2) // 2)]
    if line[:1] not in ("@", ">", "!") and len(p) == 4:
        return [(_parse_int32(p[2]), p[0], tuple(_decode_grouped_values_strict(p[3])))]
    return []


def check_simplified_mirror(key: str, block: list[str], t2s) -> None:
    """--simplified rule for one key: each B line's Simplified cell is its Traditional cell converted and deduplicated (so never empty),
    and the (value, context) set of typeID 5 is the typeID 6 set converted."""
    chs_set: set = set()
    cht_set: set = set()
    for ln in block:
        p = ln.split("\t")
        if ln.startswith("@") and len(p) == 3:
            v5 = _decode_grouped_values_strict(p[1], allows_empty_cell=True)
            v6 = _decode_grouped_values_strict(p[2], allows_empty_cell=True)
            if not v5 or v5 != dedupe(t2s(w) for w in v6):
                raise ValueError(f"key {key!r}: Simplified cell {p[1]!r} does not mirror Traditional cell {p[2]!r}")
        for tid, val, ctx in contextual_items(ln):
            if tid == 5:
                chs_set.add((val, ctx))
            elif tid == 6:
                cht_set.add((t2s(val), tuple(t2s(c) for c in ctx)))
    if chs_set != cht_set:
        raise ValueError(f"key {key!r}: typeID 5 entries are not the converted typeID 6 entries")


def validate_textmap_file(path: str, check_sorted: bool = True, t2s=None) -> tuple[int, int]:
    """t2s (from make_t2s) additionally checks the --simplified mirror rules (check_simplified_mirror) on every key."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"TextMap is not valid UTF-8: {e}") from e
    if "\r" in text:
        raise ValueError("TextMap contains CR characters; expected LF line endings.")

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    if not lines or lines[0] != "#PRAGMA:VANGUARD_HOMA_LEXICON_HEADER":
        raise ValueError("Missing #PRAGMA:VANGUARD_HOMA_LEXICON_HEADER")
    if len(lines) < 2 or lines[1] != "VERSION\t2.0":
        raise ValueError("Missing VERSION\\t2.0")

    idx = 1
    header_kv: dict[str, str] = {}
    default_probs: dict[int, float] = {}
    while idx < len(lines) and not lines[idx].startswith("#PRAGMA:"):
        ln = lines[idx]
        idx += 1
        if not ln:
            continue
        kv = ln.split("\t", 1)
        if len(kv) != 2:
            raise ValueError(f"Malformed header line: {ln!r}")
        k, v = kv
        header_kv[k] = v
        if k.startswith("DEFAULT_PROB_"):
            tid = _parse_int32(k[len("DEFAULT_PROB_") :])
            if tid is None:
                raise ValueError(f"Bad DEFAULT_PROB key: {k!r}")
            default_probs[tid] = float(v)

    if header_kv.get("TYPE") != "TYPING":
        raise ValueError(f"Expected TYPE=TYPING, got {header_kv.get('TYPE')!r}")
    if header_kv.get("READING_SEPARATOR") != "-":
        raise ValueError(f"Expected READING_SEPARATOR=-, got {header_kv.get('READING_SEPARATOR')!r}")
    entry_count = int(header_kv["ENTRY_COUNT"])
    key_count = int(header_kv["KEY_COUNT"])

    if idx >= len(lines) or lines[idx] != "#PRAGMA:VANGUARD_HOMA_LEXICON_VALUES":
        raise ValueError("Missing #PRAGMA:VANGUARD_HOMA_LEXICON_VALUES")
    idx += 1

    val_start = idx
    while idx < len(lines) and not lines[idx].startswith("#PRAGMA:"):
        idx += 1
    val_lines = lines[val_start:idx]
    if len(val_lines) != entry_count:
        raise ValueError(f"ENTRY_COUNT={entry_count} != actual VALUES lines {len(val_lines)}")

    for li, ln in enumerate(val_lines):
        try:
            validate_value_line_strict(ln, is_typing=True, default_probs=default_probs)
        except ValueError as e:
            raise ValueError(f"Invalid VALUES line {li}: {e}") from e

    if idx >= len(lines) or lines[idx] != "#PRAGMA:VANGUARD_HOMA_LEXICON_KEY_LINE_MAP":
        raise ValueError("Missing #PRAGMA:VANGUARD_HOMA_LEXICON_KEY_LINE_MAP")
    idx += 1

    km_lines = [ln for ln in lines[idx:] if ln != ""]
    if len(km_lines) != key_count:
        raise ValueError(f"KEY_COUNT={key_count} != actual KEY_LINE_MAP lines {len(km_lines)}")

    prev_bytes = b""
    expected_start = 0
    for ki, ln in enumerate(km_lines):
        parts = ln.split("\t")
        if len(parts) != 3:
            raise ValueError(f"KEY_LINE_MAP line {ki} does not have 3 fields: {ln!r}")
        key_s, s_str, c_str = parts
        if not key_s or not s_str.isdigit() or not c_str.isdigit():
            raise ValueError(f"Malformed KEY_LINE_MAP line {ki}: {ln!r}")
        kb = key_s.encode("utf-8")
        if check_sorted and ki > 0 and not (prev_bytes < kb):
            raise ValueError(f"KEY_LINE_MAP not strictly UTF-8 sorted at {ki}: {prev_bytes!r} >= {kb!r}")
        prev_bytes = kb
        st = int(s_str)
        cnt = int(c_str)
        if cnt <= 0 or st < 0 or st + cnt > entry_count:
            raise ValueError(f"KEY_LINE_MAP out of bounds at {ki}: start={st}, count={cnt}, ENTRY_COUNT={entry_count}")
        if st != expected_start:
            raise ValueError(f"KEY_LINE_MAP gap/overlap at {ki}: expected start {expected_start}, got {st}")
        expected_start = st + cnt

    if expected_start != entry_count:
        raise ValueError(f"KEY_LINE_MAP covers {expected_start} lines != ENTRY_COUNT {entry_count}")

    if t2s is not None:
        for ln in km_lines:
            key_s, s_str, c_str = ln.split("\t")
            check_simplified_mirror(key_s, val_lines[int(s_str) : int(s_str) + int(c_str)], t2s)

    return entry_count, key_count


def count_categories(path: str, what: str) -> dict[str, int]:
    """{category label: count} for a TextMap 2.0 file, for --compare-factory. Categories: B lines with a non-empty Simplified / Traditional
    cell, type-A / C / D lines per typeID, KEY_LINE_MAP keys starting with "_" (meta keys), header keys other than ENTRY_COUNT and KEY_COUNT.
    A header, VALUES or KEY_LINE_MAP line that fits none of them exits 2 with its line number and first 40 characters."""
    try:
        with open(path, "rb") as f:
            lines = f.read().decode("utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as e:
        die(f"{what}: cannot read {os.path.basename(path)}: {e}")
    if lines and lines[-1] == "":
        lines.pop()
    counts: dict[str, int] = defaultdict(int)
    section = None
    for i, ln in enumerate(lines):
        def unknown() -> None:
            die(f"{what}: line {i + 1} fits no category: {ln[:40]}")
        if ln.startswith("#PRAGMA:"):
            if ln not in (_SECTION_HEADER, _SECTION_VALUES, _SECTION_KEY_MAP):
                unknown()
            section = ln
            continue
        if section is None:
            unknown()
        p = ln.split("\t")
        if section == _SECTION_HEADER:
            if ln and len(ln.split("\t", 1)) != 2:
                unknown()
            if ln and p[0] not in ("ENTRY_COUNT", "KEY_COUNT"):
                counts[f"header key {p[0]}"] += 1
        elif section == _SECTION_KEY_MAP:
            if ln and len(p) != 3:
                unknown()
            if ln and p[0].startswith("_"):
                counts[f"meta key {p[0]}"] += 1
        elif ln.startswith(">") and len(p) == 2 and _parse_int32(p[0][1:]) is not None:
            counts[f"A typeID {_parse_int32(p[0][1:])}"] += 1
        elif ln.startswith("@") and len(p) == 3:
            counts["B Simplified cell non-empty"] += p[1] not in ("", "\x07")
            counts["B Traditional cell non-empty"] += p[2] not in ("", "\x07")
        elif ln.startswith("!") and len(p) > 4 and len(p) % 2 == 0 and _parse_int32(p[1]) is not None:
            counts[f"D typeID {_parse_int32(p[1])}"] += 1
        elif len(p) in (3, 4) and _parse_int32(p[2]) is not None:
            counts[f"C typeID {_parse_int32(p[2])}"] += 1
        else:
            unknown()
    return dict(counts)


def _category_order(label: str) -> tuple:
    group = label.split(" ", 1)[0]
    tid = int(label.rsplit(" ", 1)[1]) if group in ("A", "C", "D") else 0
    return ({"B": 0, "A": 1, "C": 2, "D": 3, "meta": 4, "header": 5}[group], tid, label)


def compare_factory(factory_path: str, ours_path: str) -> tuple[str, list[str]]:
    """(table, missing): both files' category counts side by side, and the labels the factory file has (> 0), the output lacks (= 0)
    and FACTORY_ABSENT_OK does not list."""
    fac = count_categories(factory_path, "factory file")
    ours = count_categories(ours_path, "output")
    labels = sorted(set(fac) | set(ours), key=_category_order)
    rows, missing = [f"{'category':<34}{'factory':>10}{'ours':>10}  status"], []
    for lab in labels:
        f, o = fac.get(lab, 0), ours.get(lab, 0)
        if f > 0 and o == 0:
            status = "absent, allowed" if lab in FACTORY_ABSENT_OK else "MISSING"
            if lab not in FACTORY_ABSENT_OK:
                missing.append(lab)
        else:
            status = "ok"
        rows.append(f"{lab:<34}{f:>10}{o:>10}  {status}")
    return "\n".join(rows), missing


def assemble_companion_files(companion_dir: str, out_dict: str) -> list[str]:
    if not os.path.isdir(companion_dir):
        raise FileNotFoundError(f"companion dir not found: {companion_dir}")
    required = [
        "phonic_table_Z.txt",
        "data-punctuations.txt",
        "data-paired-punctuations.txt",
    ]
    templates = sorted(glob.glob(os.path.join(companion_dir, "template-*.txt")))
    if not templates:
        raise FileNotFoundError(f"no template-*.txt files found in {companion_dir}")
    sources: list[str] = []
    for name in required:
        p = os.path.join(companion_dir, name)
        if not os.path.isfile(p):
            raise FileNotFoundError(f"missing companion file: {p}")
        sources.append(p)
    sources.extend(templates)

    copied = []
    for src in sources:
        base = os.path.basename(src)
        if base == "VanguardFactoryDict4Typing.txtMap":
            continue
        dst = os.path.join(out_dict, base)
        shutil.copy2(src, dst)
        copied.append(base)
    return copied


def parse_packs(packs_arg: str) -> list[str]:
    s = packs_arg.strip()
    if not s or s.lower() == "none":
        return []
    out = []
    seen = set()
    for part in s.split(","):
        p = part.strip()
        if not p:
            continue
        if p not in VALID_PACKS:
            raise ValueError(f"unknown pack {p!r}; valid: {', '.join(VALID_PACKS)} or none")
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def emit_contextual_lines(val: str, items: list[tuple[tuple[str, ...], float]], decimals: int, type_id: int = 6) -> list[str]:
    """VALUES lines for one (reading, value) contextual group: Format C for 1 entry, Format D chunks (2..16) otherwise.
    type_id is 6 (Traditional) or, for the --simplified mirror, 5."""
    validate_bare_value(val)
    items = sorted(items, key=lambda it: (-it[1], it[0]))
    if len(items) == 1:
        ctx_t, sc = items[0]
        validate_context_tokens(ctx_t)
        return [f"{val}\t{format_prob(sc, decimals)}\t{type_id}\t{encode_grouped_values(ctx_t)}"]
    out: list[str] = []
    pos = 0
    total_items = len(items)
    while pos < total_items:
        rem = total_items - pos
        chunk_cnt = 15 if rem == 17 else min(rem, 16)
        chunk = items[pos : pos + chunk_cnt]
        cells = [f"!{val}", str(type_id)]
        for ctx_t, sc in chunk:
            validate_context_tokens(ctx_t)
            cells.append(format_prob(sc, decimals))
            cells.append(encode_grouped_values(ctx_t))
        out.append("\t".join(cells))
        pos += chunk_cnt
    return out


# ---------------------------------------------------------------------------
# --base-txtmap: apply --extra-ngrams overrides to an existing TextMap 2.0 file
# ---------------------------------------------------------------------------

def _b_parts(line: str) -> list[str] | None:
    """Split a type-B unigram line '@prob\tchsCell\tchtCell'; None for any other line."""
    if line.startswith("@"):
        parts = line.split("\t")
        if len(parts) == 3:
            return parts
    return None


def _line_value(line: str) -> str:
    return line.split("\t", 1)[0][1:] if line.startswith("!") else line.split("\t", 1)[0]


def override_unigram_lines(lines: list[str], value: str, prob: float, decimals: int) -> tuple[list[str], bool]:
    """Set the cht-cell unigram probability of `value` in one key block. Returns (lines, existed)."""
    ptxt = format_prob(prob, decimals)
    existed = False
    out: list[str] = []
    for ln in lines:
        parts = _b_parts(ln)
        if parts is not None:
            cht = _decode_grouped_values_strict(parts[2], allows_empty_cell=True)
            if value in cht:
                existed = True
                if float(parts[0][1:]) == float(ptxt):
                    return lines, True  # already at the requested probability
                rest = [t for t in cht if t != value]
                chs = _decode_grouped_values_strict(parts[1], allows_empty_cell=True)
                if rest or chs:
                    out.append("\t".join([parts[0], parts[1], encode_grouped_values(rest) if rest else ""]))
                continue
        out.append(ln)
    ev = escape_grouped_value(value)
    insert_at = None
    last_b = -1
    for i, ln in enumerate(out):
        parts = _b_parts(ln)
        if parts is None:
            continue
        last_b = i
        if parts[0] == "@" + ptxt:
            cell = parts[2]
            parts[2] = ev if cell in ("", "\x07") else cell + "|" + ev
            out[i] = "\t".join(parts)
            return out, existed
        if insert_at is None and float(parts[0][1:]) < float(ptxt):
            insert_at = i
    if insert_at is None:
        insert_at = last_b + 1
    out.insert(insert_at, f"@{ptxt}\t\t{ev}")
    return out, existed


def override_contextual_lines(
    lines: list[str], value: str, ctx_probs: dict[tuple[str, ...], float], decimals: int
) -> tuple[list[str], int, int]:
    """Merge contextual (typeID 6) overrides for `value` into one key block. Returns (lines, overridden, added)."""
    existing: dict[tuple[str, ...], float] = {}
    kept: list[str] = []
    first_removed: int | None = None
    for ln in lines:
        parts = ln.split("\t")
        hit = False
        if ln.startswith("!") and len(parts) > 4 and parts[0][1:] == value and _parse_int32(parts[1]) == 6:
            for i in range((len(parts) - 2) // 2):
                existing[tuple(_decode_grouped_values_strict(parts[3 + 2 * i]))] = float(parts[2 + 2 * i])
            hit = True
        elif (
            ln[:1] not in ("@", ">", "!")
            and len(parts) == 4
            and parts[0] == value
            and _parse_int32(parts[2]) == 6
        ):
            existing[tuple(_decode_grouped_values_strict(parts[3]))] = float(parts[1])
            hit = True
        if hit:
            if first_removed is None:
                first_removed = len(kept)
        else:
            kept.append(ln)
    n_over = n_add = 0
    for ctx, prob in ctx_probs.items():
        if ctx in existing:
            n_over += 1
        else:
            n_add += 1
        existing[ctx] = prob
    new_lines = emit_contextual_lines(value, list(existing.items()), decimals)
    if first_removed is None:
        vb = value.encode("utf-8")
        first_removed = len(kept)
        for i, ln in enumerate(kept):
            if ln[:1] not in ("@", ">") and _line_value(ln).encode("utf-8") > vb:
                first_removed = i
                break
    return kept[:first_removed] + new_lines + kept[first_removed:], n_over, n_add


def _rel(path: str, root: str) -> str:
    """Path relative to the real --shanjie-root, or the bare file name when it lies outside it."""
    p = os.path.realpath(path)
    r = os.path.realpath(root)
    return os.path.relpath(p, r) if os.path.commonpath([p, r]) == r else os.path.basename(p)


def _git(root: str, *cmd: str) -> str | None:
    """Output of `git -C root cmd`, or None when root is not itself the top of a git work tree."""
    try:
        top = subprocess.run(["git", "-C", root, "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
        if os.path.realpath(top) != os.path.realpath(root):
            return None
        return subprocess.run(["git", "-C", root, *cmd], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _input_files(args: argparse.Namespace, packs: list[str]) -> list[str]:
    """The data files this run reads (the same paths build_textmap and load_lexicon open)."""
    if args.base_txtmap:
        return [args.base_txtmap] + ([args.extra_ngrams] if args.extra_ngrams else [])
    root = args.shanjie_root
    lex, lm_dir = os.path.join(root, "data", "lexicon"), os.path.join(root, "data", "lm")
    files = [os.path.join(lex, n) for n in ("mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv")]
    files += [] if args.no_names else [os.path.join(lex, "names-add.tsv")]
    files += [os.path.join(root, "data", "packs", f"{p}-add.tsv") for p in packs]
    files += [os.path.join(lex, "variant-order.tsv"), os.path.join(lex, "demote.tsv"), os.path.join(lm_dir, "bigram.sjlm")]
    if args.kn_beta > 0.0:
        files.append(args.kn_path or os.path.join(lm_dir, "kn.sjkn"))
    if args.colloq_uni_w > 0.0 or args.colloq_bi_w > 0.0:
        files.append(args.colloq_path or os.path.join(lm_dir, "colloquial.sjlm"))
    files += [f for f in (args.trigram_path, args.extra_ngrams) if f]
    if args.simplified:
        files += [os.path.join(args.opencc_dir, n) for n in OPENCC_SHA256]
    return [f for f in files if os.path.isfile(f)]  # demote.tsv is the only optional one


def finish_stats(stats: dict, args: argparse.Namespace, packs: list[str], txtmap_path: str, out_dict: str | None) -> dict:
    """Make the stats path-free, add "inputs", and write build_stats.json (inside --out-dict, else next to the txtMap)."""
    root = args.shanjie_root
    flags = dict(vars(args))
    for k in ("shanjie_root", "out_dict", "out_txtmap", "companion_dir", "kn_path", "colloq_path", "trigram_path", "extra_ngrams", "base_txtmap", "opencc_dir", "compare_factory"):
        if flags.get(k):
            flags[k] = _rel(flags[k], root)
    stats["flags"] = flags
    stats["txtmap_path"] = _rel(txtmap_path, root)
    if "base_txtmap" in stats:
        stats["base_txtmap"] = _rel(stats["base_txtmap"], root)
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no")
    stats["inputs"] = {
        "git_commit": _git(root, "rev-parse", "HEAD"),
        "git_dirty": None if dirty is None else bool(dirty),
        "files": [{"path": _rel(f, root), "sha256": hashlib.sha256(open(f, "rb").read()).hexdigest()} for f in _input_files(args, packs)],
    }
    stats_path = os.path.join(out_dict, "build_stats.json") if out_dict is not None else txtmap_path + ".build_stats.json"
    with open(stats_path, "w", encoding="utf-8") as sf:
        json.dump(stats, sf, ensure_ascii=False, indent=2)
    return stats


def build_from_base(args: argparse.Namespace, out_dict: str | None, txtmap_path: str, t_start: float) -> dict:
    base = os.path.realpath(args.base_txtmap)
    if not os.path.isfile(base):
        die(f"--base-txtmap not found: {base}")
    decimals = args.decimals
    t0 = time.perf_counter()
    if args.validate:
        try:
            validate_textmap_file(base, check_sorted=False)  # factory dicts are not UTF-8 byte-sorted
        except Exception as e:
            die(f"--base-txtmap failed validation: {e}")
    with open(base, "rb") as f:
        base_bytes = f.read()
    text = base_bytes.decode("utf-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    vi = lines.index("#PRAGMA:VANGUARD_HOMA_LEXICON_VALUES")
    ki = lines.index("#PRAGMA:VANGUARD_HOMA_LEXICON_KEY_LINE_MAP")
    header = lines[: vi + 1]
    values = lines[vi + 1 : ki]
    order: list[str] = []
    blk: dict[str, list[str]] = {}
    for ln in lines[ki + 1 :]:
        if not ln:
            continue
        key, st, cnt = ln.split("\t")
        order.append(key)
        blk[key] = values[int(st) : int(st) + int(cnt)]
    t_load = time.perf_counter() - t0

    t0 = time.perf_counter()
    extra_map = parse_extra_ngrams(args.extra_ngrams, decimals) if args.extra_ngrams else {}
    uni: dict[str, dict[str, float]] = defaultdict(dict)
    ctxd: dict[str, dict[str, dict[tuple[str, ...], float]]] = defaultdict(lambda: defaultdict(dict))
    for (reading, value, ctx), prob in extra_map.items():
        if ctx:
            ctxd[reading][value][ctx] = prob
        else:
            uni[reading][value] = prob
    uni_over = uni_add = ctx_over = ctx_add = 0
    new_keys: list[str] = []
    for reading in sorted(set(uni) | set(ctxd), key=lambda k: k.encode("utf-8")):
        cur = blk.get(reading)
        if cur is None:
            cur = []
            new_keys.append(reading)
        for value, prob in uni.get(reading, {}).items():
            cur, existed = override_unigram_lines(cur, value, prob, decimals)
            if existed:
                uni_over += 1
            else:
                uni_add += 1
        for value, cp in ctxd.get(reading, {}).items():
            cur, n_o, n_a = override_contextual_lines(cur, value, cp, decimals)
            ctx_over += n_o
            ctx_add += n_a
        blk[reading] = cur
    # ponytail: factory key order is not UTF-8 byte order and its exact collation is unknown, so
    # new keys are appended (byte-sorted among themselves); the Swift TextMapTrie accepts any key order.
    order = order + new_keys
    t_apply = time.perf_counter() - t0

    t0 = time.perf_counter()
    value_lines: list[str] = []
    key_map_lines: list[str] = []
    for key in order:
        b = blk[key]
        if b:
            key_map_lines.append(f"{key}\t{len(value_lines)}\t{len(b)}")
            value_lines.extend(b)
    if out_dict is not None:
        os.makedirs(out_dict, exist_ok=True)
        copied_companions = assemble_companion_files(args.companion_dir, out_dict)
    else:
        os.makedirs(os.path.dirname(txtmap_path), exist_ok=True)
        copied_companions = []
    with open(txtmap_path, "w", encoding="utf-8", newline="\n") as f:
        for ln in header:
            if ln.startswith("ENTRY_COUNT\t"):
                ln = f"ENTRY_COUNT\t{len(value_lines)}"
            elif ln.startswith("KEY_COUNT\t"):
                ln = f"KEY_COUNT\t{len(key_map_lines)}"
            f.write(ln + "\n")
        for ln in value_lines:
            f.write(ln + "\n")
        f.write("#PRAGMA:VANGUARD_HOMA_LEXICON_KEY_LINE_MAP\n")
        for ln in key_map_lines:
            f.write(ln + "\n")
    t_ser = time.perf_counter() - t0

    t0 = time.perf_counter()
    if args.validate:
        try:
            v_entries, v_keys = validate_textmap_file(txtmap_path, check_sorted=False)
        except Exception as e:
            die(f"self-validation failed for {txtmap_path}: {e}", code=2)
    else:
        v_entries, v_keys = len(value_lines), len(key_map_lines)
    t_val = time.perf_counter() - t0

    with open(txtmap_path, "rb") as f:
        out_bytes = f.read()
    stats = {
        "txtmap_path": txtmap_path,
        "txtmap_bytes": len(out_bytes),
        "txtmap_sha256": hashlib.sha256(out_bytes).hexdigest(),
        "entry_count": v_entries,
        "key_count": v_keys,
        "base_txtmap": base,
        "base_txtmap_sha256": hashlib.sha256(base_bytes).hexdigest(),
        "extra_unigram_overridden": uni_over,
        "extra_unigram_added": uni_add,
        "extra_ngram_overridden": ctx_over,
        "extra_ngram_added": ctx_add,
        "extra_ngram_entries": ctx_over + ctx_add,
        "copied_companions": copied_companions,
        "validated": args.validate,
        "timing_sec": {
            "load": round(t_load, 3),
            "apply": round(t_apply, 3),
            "serialize": round(t_ser, 3),
            "validate": round(t_val, 3),
            "total": round(time.perf_counter() - t_start, 3),
        },
    }
    return finish_stats(stats, args, [], txtmap_path, out_dict)


def build_textmap(args: argparse.Namespace) -> dict:
    t_start = time.perf_counter()
    packs = parse_packs(args.packs)
    # Base mode copies an existing file's lines and has no Simplified or meta step: refuse the flags rather than ignore them.
    if args.base_txtmap and (args.simplified or args.vendor is not None):
        die("--simplified and --vendor are not supported together with --base-txtmap")
    if args.vendor is not None:
        try:
            validate_bare_value(args.vendor)
        except ValueError as e:
            die(f"--vendor: {e}")
    t2s = make_t2s(*load_opencc(args.opencc_dir)) if args.simplified else None
    if args.compare_factory and not os.path.isfile(args.compare_factory):
        die(f"--compare-factory file not found: {os.path.basename(args.compare_factory)}")

    # A 33 MB output inside the repo work tree would be committed by mistake: refuse it.
    root_real = os.path.realpath(args.shanjie_root)

    def check_outside_root(p: str, flag: str) -> None:
        # Every existing ancestor is compared by identity (samefile), not by spelling: on a case-insensitive file system
        # realpath keeps the typed case, so <root>/../REPO/x would otherwise pass for <root>/../repo.
        q = p
        while True:
            if os.path.exists(q) and os.path.samefile(q, root_real):
                die(f"{flag} must lie outside the --shanjie-root work tree: got {p}")
            parent = os.path.dirname(q)
            if parent == q:
                return
            q = parent

    if args.out_dict:
        out_dict = os.path.realpath(args.out_dict)
        check_outside_root(out_dict, "--out-dict")
        if os.path.exists(out_dict) and not args.force:
            die(f"--out-dict already exists (pass --force to overwrite): {out_dict}")
        txtmap_path = os.path.join(out_dict, "VanguardFactoryDict4Typing.txtMap")
    else:
        out_dict = None
        txtmap_path = os.path.realpath(args.out_txtmap)
        check_outside_root(txtmap_path, "--out-txtmap")
        if os.path.exists(txtmap_path) and not args.force:
            die(f"--out-txtmap already exists (pass --force to overwrite): {txtmap_path}")

    if args.base_txtmap:
        return build_from_base(args, out_dict, txtmap_path, t_start)

    # 1. Load Shanjie lexicon + overlays + names + packs
    t0 = time.perf_counter()
    by_reading, capping_words, forced_pack_words, unseen_pack_words = load_lexicon(
        shanjie_root=args.shanjie_root,
        include_colloq_readings=args.include_colloq_readings,
        use_names=not args.no_names,
        packs=packs,
    )
    variant_path = os.path.join(args.shanjie_root, "data", "lexicon", "variant-order.tsv")
    variant_rows = parse_variant_order(variant_path)
    apply_variants(by_reading, variant_rows)
    t_lex = time.perf_counter() - t0

    # 2. Load LM(s)
    t0 = time.perf_counter()
    bigram_path = os.path.join(args.shanjie_root, "data", "lm", "bigram.sjlm")
    lm = SJLMModel.load(bigram_path, build_ctx_pos=(args.trigram_path is not None or args.colloq_bi_w > 0.0))

    kn_data = None
    if args.kn_beta > 0.0:
        kn_path = args.kn_path or os.path.join(args.shanjie_root, "data", "lm", "kn.sjkn")
        kn_np1, kn_total, _ = load_kn(kn_path, len(lm.vocab), lm.sha256)
        kn_data = (args.kn_beta, kn_np1, kn_total)

    colloq_lm = None
    if args.colloq_uni_w > 0.0 or args.colloq_bi_w > 0.0:
        colloq_path = args.colloq_path or os.path.join(args.shanjie_root, "data", "lm", "colloquial.sjlm")
        colloq_lm = SJLMModel.load(colloq_path, build_ctx_pos=(args.colloq_bi_w > 0.0))
    t_lm = time.perf_counter() - t0

    # 3. Apply cap_overlay + variant-order + unigram tuning + variant-order + demote
    t0 = time.perf_counter()
    if not args.no_cap_overlay:
        log_n = math.log10(lm.N)
        for entries in by_reading.values():
            for e in entries:
                w = e[0]
                if w in capping_words and w not in forced_pack_words:
                    c = lm.count(w)
                    if c > 0:
                        e[1] = min(e[1], math.log10(c) - log_n)
                    elif w not in unseen_pack_words:
                        e[1] -= UNSEEN_OVERLAY_PENALTY
            entries.sort(key=lambda e: -e[1])
        apply_variants(by_reading, variant_rows)

    # Save post-cap/post-variant lp_lex per (reading, word) before uni_lam/colloq_uni_w/len_bonus/demote
    lp_lex_map: dict[tuple[str, str], float] = {
        (reading, e[0]): e[1]
        for reading, entries in by_reading.items()
        for e in entries
    }

    colloq_denom = (colloq_lm.N + len(colloq_lm.vocab) + 1.0) if colloq_lm is not None else 0.0
    if args.uni_lam > 0.0 or args.colloq_uni_w > 0.0:
        for entries in by_reading.values():
            for e in entries:
                w, lp_lex = e[0], e[1]
                if w in forced_pack_words:
                    continue
                if args.uni_lam > 0.0:
                    wi = lm.word_to_id.get(w, -1)
                    c = int(lm.uni[wi]) if wi >= 0 else 0
                    if c > 0:
                        p1 = c / lm.N
                        if kn_data is not None and wi >= 2:
                            beta, np1, kn_total = kn_data
                            p1 = beta * (float(np1[wi]) / kn_total) + (1.0 - beta) * p1
                        if args.colloq_uni_w > 0.0 and colloq_lm is not None:
                            c_c = colloq_lm.count(w)
                            p_c = (c_c + 1.0) / colloq_denom
                            p1 = (1.0 - args.colloq_uni_w) * p1 + args.colloq_uni_w * p_c
                        e[1] = args.uni_lam * math.log10(max(p1, 1e-15)) + (1.0 - args.uni_lam) * lp_lex
                    elif args.colloq_uni_w > 0.0 and colloq_lm is not None:
                        c_c = colloq_lm.count(w)
                        if c_c > 0:
                            p_lex = 10.0 ** min(lp_lex, SCORE_CEIL)
                            p_c = (c_c + 1.0) / colloq_denom
                            p1 = (1.0 - args.colloq_uni_w) * p_lex + args.colloq_uni_w * p_c
                            e[1] = args.uni_lam * math.log10(max(p1, 1e-15)) + (1.0 - args.uni_lam) * lp_lex
                elif args.colloq_uni_w > 0.0 and colloq_lm is not None:
                    p_lex = 10.0 ** min(lp_lex, SCORE_CEIL)
                    c_c = colloq_lm.count(w)
                    p_c = (c_c + 1.0) / colloq_denom
                    p1 = (1.0 - args.colloq_uni_w) * p_lex + args.colloq_uni_w * p_c
                    e[1] = math.log10(max(p1, 1e-15))

    if args.len_bonus != 0.0:
        for entries in by_reading.values():
            for e in entries:
                e[1] += args.len_bonus * (len(e[0]) - 1)

    if args.uni_lam > 0.0 or args.colloq_uni_w > 0.0 or args.len_bonus != 0.0:
        apply_variants(by_reading, variant_rows)

    all_words = {e[0] for entries in by_reading.values() for e in entries}
    demote_path = os.path.join(args.shanjie_root, "data", "lexicon", "demote.tsv")
    demote_map = parse_demote(demote_path, by_reading, all_words) if os.path.isfile(demote_path) else {}

    # Finalize unigram scores (demote, clamp, round) and compute B(R) & top1 status
    decimals = args.decimals
    uni_final: dict[str, list[tuple[str, float, float, float]]] = {}  # reading -> [(word, rounded_score, sc_raw, demote_d)]
    b_max: dict[str, float] = {}
    unique_top1: dict[str, str | None] = {}
    best_lp_by_word: dict[str, float] = {}

    for reading, entries in by_reading.items():
        finalized = []
        for w, lp_base in entries:
            d = demote_map.get((reading, w))
            if d is None:
                d = demote_map.get(("*", w), 0.0)
            sc_raw = lp_base - d
            sc = clamp_round(sc_raw, decimals)
            finalized.append((w, sc, sc_raw, d))
            prev_best = best_lp_by_word.get(w)
            if prev_best is None or sc_raw > prev_best:
                best_lp_by_word[w] = sc_raw
        # Stable sort by rounded score descending so emission and B(R) match exact order
        finalized.sort(key=lambda item: -item[1])
        uni_final[reading] = finalized
        top_score = finalized[0][1]
        b_max[reading] = top_score
        if len(finalized) == 1 or finalized[1][1] < top_score:
            unique_top1[reading] = finalized[0][0]
        else:
            unique_top1[reading] = None

    # Build word -> attached readings list filtered by --max-reading-gap
    # Each tuple: (reading, b_r, is_unique_top1, uni_score, lp_lex, len_adj, demote_d, p1_backoff, log10_p1)
    word_attachments: dict[str, list[tuple[str, float, bool, float, float, float, float, float, float]]] = defaultdict(list)
    for reading, finalized in uni_final.items():
        b_r = b_max[reading]
        u_top = unique_top1[reading]
        for w, sc, sc_raw, d in finalized:
            if w in forced_pack_words:
                continue
            if sc_raw < best_lp_by_word[w] - args.max_reading_gap:
                continue
            len_adj = args.len_bonus * (len(w) - 1)
            lp_lex = min(lp_lex_map[(reading, w)], SCORE_CEIL)
            pb = 10.0 ** lp_lex
            if kn_data is not None:
                wi = lm.word_to_id.get(w, -1)
                if wi >= 2:
                    beta, np1, kn_total = kn_data
                    pb = beta * (float(np1[wi]) / kn_total) + (1.0 - beta) * pb
            pb = max(pb, 1e-15)
            word_attachments[w].append((reading, b_r, (u_top == w), sc, lp_lex, len_adj, d, pb, math.log10(pb)))

    t_uni = time.perf_counter() - t0

    # Helper to compute contextual score from probability p_cond
    bi_mode = args.bi_mode
    bi_lam = args.bi_lam
    bi_offset = args.bi_offset
    bi_gamma = args.bi_gamma

    def score_from_cond(p_cond: float, uni_sc: float, lp_lex: float, len_adj: float, demote_d: float, log10_p1: float) -> float:
        lp_cond = math.log10(p_cond)
        if bi_mode == "blend":
            return bi_lam * lp_cond + (1.0 - bi_lam) * lp_lex + len_adj + bi_offset - demote_d
        return uni_sc + bi_gamma * (lp_cond - log10_p1)

    # 4. Contextual Bigrams
    t0 = time.perf_counter()
    # contextual_entries[(reading, value, ctx_tuple)] = score
    contextual_entries: dict[tuple[str, str, tuple[str, ...]], float] = {}
    bigram_count_emitted = 0

    if not args.no_bigrams:
        V = len(lm.vocab)
        in_lex_ctx = np.zeros(V, dtype=np.bool_)
        in_lex_val = np.zeros(V, dtype=np.bool_)
        word_att_by_vid: list[list | None] = [None] * V

        for w, atts in word_attachments.items():
            wid = lm.word_to_id.get(w)
            if wid is None:
                continue
            if len(w) <= args.max_ctx_len:
                in_lex_ctx[wid] = True
            if len(w) <= args.max_val_len and atts:
                in_lex_val[wid] = True
                word_att_by_vid[wid] = atts

        C = len(lm.ctx)
        edge_ctx_idx = np.repeat(np.arange(C, dtype=np.int32), np.diff(lm.off))
        valid_c = (lm.tot >= args.bi_min_ctx) & in_lex_ctx[lm.ctx]
        mask = (lm.cnt >= args.bi_min_count) & valid_c[edge_ctx_idx] & in_lex_val[lm.nxt]

        sel_c = edge_ctx_idx[mask]
        sel_v = lm.ctx[sel_c]
        sel_w = lm.nxt[mask]
        sel_k = lm.cnt[mask]
        sel_term1 = (sel_k.astype(np.float64) - lm.D) / lm.tot[sel_c].astype(np.float64)
        sel_back = lm.back[sel_c]

        # Track eligible bigrams: (rank_val, reading, w, v, s_bi)
        eligible_bi: list[tuple[float, str, str, str, float]] = []
        colloq_w = args.colloq_bi_w
        seen_colloq_pairs: set[tuple[str, str]] | None = set() if (colloq_w > 0.0 and colloq_lm is not None) else None

        bi_margin = args.bi_margin
        bi_non_top1 = args.bi_non_top1_only
        bi_rank = args.bi_rank

        for vi, wi, term1, b_v, k in zip(
            sel_v.tolist(), sel_w.tolist(), sel_term1.tolist(), sel_back.tolist(), sel_k.tolist()
        ):
            v = lm.vocab[vi]
            w = lm.vocab[wi]
            atts = word_att_by_vid[wi]
            if not atts:
                continue
            if seen_colloq_pairs is not None:
                seen_colloq_pairs.add((v, w))
                c_vi = colloq_lm.word_to_id.get(v, -1)
                c_wi = colloq_lm.word_to_id.get(w, -1)
            else:
                c_vi = c_wi = -1

            for reading, b_r, is_top1, uni_sc, lp_lex, len_adj, demote_d, pb, log10_pb in atts:
                if bi_non_top1 and is_top1:
                    continue
                p2_main = term1 + b_v * pb
                eff_k = k
                if colloq_w > 0.0 and colloq_lm is not None:
                    p2_col, k_col = colloq_lm.cond_prob_ids(c_vi, c_wi, pb) if c_vi >= 0 else (pb, 0)
                    p2 = (1.0 - colloq_w) * p2_main + colloq_w * p2_col
                    eff_k = k + k_col
                else:
                    p2 = p2_main
                if p2 <= 1e-15:
                    continue
                s_raw = score_from_cond(p2, uni_sc, lp_lex, len_adj, demote_d, log10_pb)
                s_bi = clamp_round(s_raw, decimals)
                if s_bi <= b_r or s_bi <= b_r + bi_margin:
                    continue
                lift = s_bi - b_r
                if bi_rank == "impact":
                    rk = eff_k * lift
                elif bi_rank == "lift":
                    rk = lift
                else:
                    rk = float(eff_k)
                eligible_bi.append((rk, reading, w, v, s_bi))

        # Include colloquial-only bigrams if colloq_bi_w > 0
        if colloq_w > 0.0 and colloq_lm is not None and seen_colloq_pairs is not None:
            Vc = len(colloq_lm.vocab)
            c_in_ctx = np.zeros(Vc, dtype=np.bool_)
            c_in_val = np.zeros(Vc, dtype=np.bool_)
            c_att_by_id: list[list | None] = [None] * Vc
            for w, atts in word_attachments.items():
                wid = colloq_lm.word_to_id.get(w)
                if wid is None:
                    continue
                if len(w) <= args.max_ctx_len:
                    c_in_ctx[wid] = True
                if len(w) <= args.max_val_len and atts:
                    c_in_val[wid] = True
                    c_att_by_id[wid] = atts
            Cc = len(colloq_lm.ctx)
            c_edge_ctx = np.repeat(np.arange(Cc, dtype=np.int32), np.diff(colloq_lm.off))
            c_valid = (colloq_lm.tot >= args.bi_min_ctx) & c_in_ctx[colloq_lm.ctx]
            c_mask = (colloq_lm.cnt >= args.bi_min_count) & c_valid[c_edge_ctx] & c_in_val[colloq_lm.nxt]
            cs_c = c_edge_ctx[c_mask]
            cs_v = colloq_lm.ctx[cs_c]
            cs_w = colloq_lm.nxt[c_mask]
            cs_k = colloq_lm.cnt[c_mask]
            cs_t1 = (cs_k.astype(np.float64) - colloq_lm.D) / colloq_lm.tot[cs_c].astype(np.float64)
            cs_bk = colloq_lm.back[cs_c]
            for c_vi, c_wi, term1_c, b_vc, k_c in zip(
                cs_v.tolist(), cs_w.tolist(), cs_t1.tolist(), cs_bk.tolist(), cs_k.tolist()
            ):
                v = colloq_lm.vocab[c_vi]
                w = colloq_lm.vocab[c_wi]
                if (v, w) in seen_colloq_pairs:
                    continue
                atts = c_att_by_id[c_wi]
                if not atts:
                    continue
                m_vi = lm.word_to_id.get(v, -1)
                m_wi = lm.word_to_id.get(w, -1)
                for reading, b_r, is_top1, uni_sc, lp_lex, len_adj, demote_d, pb, log10_pb in atts:
                    if bi_non_top1 and is_top1:
                        continue
                    p2_main, k_m = lm.cond_prob_ids(m_vi, m_wi, pb) if m_vi >= 0 else (pb, 0)
                    p2_col = term1_c + b_vc * pb
                    p2 = (1.0 - colloq_w) * p2_main + colloq_w * p2_col
                    if p2 <= 1e-15:
                        continue
                    s_raw = score_from_cond(p2, uni_sc, lp_lex, len_adj, demote_d, log10_pb)
                    s_bi = clamp_round(s_raw, decimals)
                    if s_bi <= b_r or s_bi <= b_r + bi_margin:
                        continue
                    eff_k = k_m + k_c
                    lift = s_bi - b_r
                    rk = (eff_k * lift) if bi_rank == "impact" else (lift if bi_rank == "lift" else float(eff_k))
                    eligible_bi.append((rk, reading, w, v, s_bi))

        eligible_bi.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
        per_key_cnt: dict[str, int] = defaultdict(int)
        bi_budget = args.bi_budget
        bi_max_per_key = args.bi_max_per_key

        for _, reading, w, v, s_bi in eligible_bi:
            if bi_max_per_key > 0 and per_key_cnt[reading] >= bi_max_per_key:
                continue
            per_key_cnt[reading] += 1
            ckey = (reading, w, (v,))
            prev_sc = contextual_entries.get(ckey)
            if prev_sc is None or s_bi > prev_sc:
                contextual_entries[ckey] = s_bi
            bigram_count_emitted += 1
            if bi_budget > 0 and bigram_count_emitted >= bi_budget:
                break

    t_bi = time.perf_counter() - t0

    # 5. Optional Trigrams
    t0 = time.perf_counter()
    trigram_count_emitted = 0
    if args.trigram_path:
        tri = TriTableFast.load(args.trigram_path, len(lm.vocab), lm.sha256)
        V = tri.V
        in_lex_ctx = np.zeros(V, dtype=np.bool_)
        in_lex_val = np.zeros(V, dtype=np.bool_)
        word_att_by_vid = [None] * V
        for w, atts in word_attachments.items():
            wid = lm.word_to_id.get(w)
            if wid is None:
                continue
            if len(w) <= args.max_ctx_len:
                in_lex_ctx[wid] = True
            if len(w) <= args.max_val_len and atts:
                in_lex_val[wid] = True
                word_att_by_vid[wid] = atts

        ui_arr = (tri.key // V).astype(np.int64)
        vi_arr = (tri.key % V).astype(np.int64)
        valid_c3 = (tri.tot >= args.tri_min_ctx) & in_lex_ctx[ui_arr] & in_lex_ctx[vi_arr]
        C3 = len(tri.key)
        edge_ctx3 = np.repeat(np.arange(C3, dtype=np.int32), np.diff(tri.off))
        mask3 = (tri.cnt >= args.tri_min_count) & valid_c3[edge_ctx3] & in_lex_val[tri.nxt]

        sel_c3 = edge_ctx3[mask3]
        sel_u3 = ui_arr[sel_c3]
        sel_v3 = vi_arr[sel_c3]
        sel_w3 = tri.nxt[mask3]
        sel_k3 = tri.cnt[mask3]
        sel_t3 = tri.tot[sel_c3].astype(np.float64)
        sel_bk3 = tri.back[sel_c3]
        sel_t1_3 = (sel_k3.astype(np.float64) - tri.D) / sel_t3

        eligible_tri: list[tuple[float, str, str, str, str, float]] = []
        colloq_w = args.colloq_bi_w
        tri_margin = args.tri_margin
        bi_rank = args.bi_rank

        for ui, vi, wi, t1_3, bk3, k3 in zip(
            sel_u3.tolist(), sel_v3.tolist(), sel_w3.tolist(), sel_t1_3.tolist(), sel_bk3.tolist(), sel_k3.tolist()
        ):
            u = lm.vocab[ui]
            v = lm.vocab[vi]
            w = lm.vocab[wi]
            atts = word_att_by_vid[wi]
            if not atts:
                continue
            c_vi = colloq_lm.word_to_id.get(v, -1) if (colloq_w > 0.0 and colloq_lm is not None) else -1
            c_wi = colloq_lm.word_to_id.get(w, -1) if (colloq_w > 0.0 and colloq_lm is not None) else -1

            for reading, b_r, _, uni_sc, lp_lex, len_adj, demote_d, pb, log10_pb in atts:
                p2_main, _ = lm.cond_prob_ids(vi, wi, pb)
                if colloq_w > 0.0 and colloq_lm is not None:
                    p2_col, _ = colloq_lm.cond_prob_ids(c_vi, c_wi, pb) if c_vi >= 0 else (pb, 0)
                    p2 = (1.0 - colloq_w) * p2_main + colloq_w * p2_col
                else:
                    p2 = p2_main
                p3 = max(t1_3 + bk3 * p2, TRI_FLOOR * p2)
                if p3 <= 1e-15 or p2 <= 1e-15:
                    continue
                s_eff = score_from_cond(p3, uni_sc, lp_lex, len_adj, demote_d, log10_pb)
                s_bi_ref = score_from_cond(p2, uni_sc, lp_lex, len_adj, demote_d, log10_pb)
                if s_eff <= b_r + tri_margin or s_eff <= s_bi_ref + 0.05:
                    continue
                prob_stored = clamp_round(b_r + 0.5 * (s_eff - b_r), decimals)
                if prob_stored <= b_r:
                    continue
                lift = s_eff - b_r
                rk = (k3 * lift) if bi_rank == "impact" else (lift if bi_rank == "lift" else float(k3))
                eligible_tri.append((rk, reading, w, u, v, prob_stored))

        eligible_tri.sort(key=lambda item: (-item[0], item[1], item[2], item[3], item[4]))
        tri_budget = args.tri_budget
        for _, reading, w, u, v, prob_stored in eligible_tri:
            ckey = (reading, w, (u, v))
            prev_sc = contextual_entries.get(ckey)
            if prev_sc is None or prob_stored > prev_sc:
                contextual_entries[ckey] = prob_stored
            trigram_count_emitted += 1
            if tri_budget > 0 and trigram_count_emitted >= tri_budget:
                break

    t_tri = time.perf_counter() - t0

    # 6. Optional --extra-ngrams override
    extra_ngram_count = 0
    uni_overridden = 0
    uni_added = 0
    if args.extra_ngrams:
        extra_map = parse_extra_ngrams(args.extra_ngrams, decimals)
        for ckey, prob in extra_map.items():
            reading, value, ctx = ckey
            if not ctx:  # v2: empty context = unigram override / addUnigram
                entries = uni_final.setdefault(reading, [])
                for i, (w, _, _, d) in enumerate(entries):
                    if w == value:
                        entries[i] = (w, prob, prob, d)
                        uni_overridden += 1
                        break
                else:
                    entries.append((value, prob, prob, 0.0))
                    uni_added += 1
                entries.sort(key=lambda item: -item[1])
                continue
            contextual_entries[ckey] = prob
            extra_ngram_count += 1

    # 7. Group contextual entries by reading -> value -> [(ctx_tuple, prob)]
    t0 = time.perf_counter()
    ctx_by_reading: dict[str, dict[str, list[tuple[tuple[str, ...], float]]]] = defaultdict(lambda: defaultdict(list))
    for (reading, value, ctx_tuple), prob in contextual_entries.items():
        ctx_by_reading[reading][value].append((ctx_tuple, prob))

    # --simplified: every typeID 6 entry again as typeID 5 with value and context converted; entries that collapse keep the highest score.
    chs_entries: dict[tuple[str, str, tuple[str, ...]], float] = {}
    if t2s is not None:
        for (reading, value, ctx_tuple), prob in contextual_entries.items():
            ckey = (reading, t2s(value), tuple(t2s(c) for c in ctx_tuple))
            if prob > chs_entries.get(ckey, -math.inf):
                chs_entries[ckey] = prob
    chs_by_reading: dict[str, dict[str, list[tuple[tuple[str, ...], float]]]] = defaultdict(lambda: defaultdict(list))
    for (reading, value, ctx_tuple), prob in chs_entries.items():
        chs_by_reading[reading][value].append((ctx_tuple, prob))

    key_set = set(uni_final.keys()) | set(ctx_by_reading.keys())
    if args.vendor is not None:
        key_set.add(VENDOR_KEY)
    all_keys = sorted(key_set, key=lambda k: k.encode("utf-8"))

    value_lines: list[str] = []
    key_map_lines: list[str] = []
    unigram_entry_count = 0
    unigram_line_count = 0
    contextual_line_count = 0
    chs_contextual_line_count = 0

    for reading in all_keys:
        start_line = len(value_lines)
        if reading == VENDOR_KEY:
            value_lines.append(f"{args.vendor}\t0\t{META_TYPE_ID}")

        # Unigrams: Format B grouped by descending rounded score
        u_entries = uni_final.get(reading)
        if u_entries:
            prob_groups: dict[float, list[str]] = defaultdict(list)
            for w, sc, _, _ in u_entries:
                validate_bare_value(w)
                prob_groups[sc].append(w)
                unigram_entry_count += 1
            for sc in sorted(prob_groups.keys(), reverse=True):
                words_cell = encode_grouped_values(prob_groups[sc])
                # Simplified cell (EntryType 5): the same words converted, deduplicated in order; \a (no words) without --simplified.
                chs_cell = encode_grouped_values(dedupe(t2s(w) for w in prob_groups[sc])) if t2s is not None else "\x07"
                value_lines.append(f"@{format_prob(sc, decimals)}\t{chs_cell}\t{words_cell}")
                unigram_line_count += 1

        # Contextuals: grouped by (value, typeID), Format C for 1 entry, Format D (chunk 2..16) for >= 2; typeID 5 lines (--simplified) first
        chs_by_val = chs_by_reading.get(reading)
        if chs_by_val:
            for val in sorted(chs_by_val.keys(), key=lambda x: x.encode("utf-8")):
                new_lines = emit_contextual_lines(val, chs_by_val[val], decimals, 5)
                value_lines.extend(new_lines)
                chs_contextual_line_count += len(new_lines)
        c_by_val = ctx_by_reading.get(reading)
        if c_by_val:
            for val in sorted(c_by_val.keys(), key=lambda x: x.encode("utf-8")):
                new_lines = emit_contextual_lines(val, c_by_val[val], decimals)
                value_lines.extend(new_lines)
                contextual_line_count += len(new_lines)

        line_cnt = len(value_lines) - start_line
        if line_cnt > 0:
            key_map_lines.append(f"{reading}\t{start_line}\t{line_cnt}")

    if out_dict is not None:
        os.makedirs(out_dict, exist_ok=True)
        copied_companions = assemble_companion_files(args.companion_dir, out_dict)
    else:
        os.makedirs(os.path.dirname(txtmap_path), exist_ok=True)
        copied_companions = []

    header_lines = [
        "#PRAGMA:VANGUARD_HOMA_LEXICON_HEADER",
        "VERSION\t2.0",
        "TYPE\tTYPING",
        "READING_SEPARATOR\t-",
        f"ENTRY_COUNT\t{len(value_lines)}",
        f"KEY_COUNT\t{len(key_map_lines)}",
        "#PRAGMA:VANGUARD_HOMA_LEXICON_VALUES",
    ]
    with open(txtmap_path, "w", encoding="utf-8", newline="\n") as f:
        for ln in header_lines:
            f.write(ln)
            f.write("\n")
        for ln in value_lines:
            f.write(ln)
            f.write("\n")
        f.write("#PRAGMA:VANGUARD_HOMA_LEXICON_KEY_LINE_MAP\n")
        for ln in key_map_lines:
            f.write(ln)
            f.write("\n")
    t_ser = time.perf_counter() - t0

    # 8. Strict self-validation
    t0 = time.perf_counter()
    if args.validate:
        try:
            v_entries, v_keys = validate_textmap_file(txtmap_path, t2s=t2s)
        except Exception as e:
            die(f"self-validation failed for {txtmap_path}: {e}", code=2)
    else:
        v_entries, v_keys = len(value_lines), len(key_map_lines)
    t_val = time.perf_counter() - t0

    with open(txtmap_path, "rb") as f:
        txtmap_bytes = f.read()
    txtmap_sha = hashlib.sha256(txtmap_bytes).hexdigest()
    total_sec = time.perf_counter() - t_start

    stats = {
        "txtmap_path": txtmap_path,
        "txtmap_bytes": len(txtmap_bytes),
        "txtmap_sha256": txtmap_sha,
        "entry_count": v_entries,
        "key_count": v_keys,
        "unigram_entries": unigram_entry_count,
        "unigram_lines": unigram_line_count,
        "bigram_entries": bigram_count_emitted,
        "trigram_entries": trigram_count_emitted,
        "extra_ngram_entries": extra_ngram_count,
        "extra_unigram_overridden": uni_overridden,
        "extra_unigram_added": uni_added,
        "contextual_total_entries": len(contextual_entries),
        "contextual_lines": contextual_line_count,
        "chs_contextual_entries": len(chs_entries),
        "chs_contextual_lines": chs_contextual_line_count,
        "copied_companions": copied_companions,
        "validated": args.validate,
        "timing_sec": {
            "lexicon": round(t_lex, 3),
            "lm_load": round(t_lm, 3),
            "unigrams": round(t_uni, 3),
            "bigrams": round(t_bi, 3),
            "trigrams": round(t_tri, 3),
            "serialize": round(t_ser, 3),
            "validate": round(t_val, 3),
            "total": round(total_sec, 3),
        },
    }

    return finish_stats(stats, args, packs, txtmap_path, out_dict)


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Build standalone VanguardFactoryDict4Typing.txtMap (TextMap v2.0) from Shanjie data."
    )
    ap.add_argument("--shanjie-root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), help="Repo root holding data/ (default: the repo this file is in)")
    ap.add_argument("--out-dict", default=None, help="Output dict directory (assembles companion files + txtMap + build_stats.json)")
    ap.add_argument("--out-txtmap", default=None, help="Output .txtMap path (when not building full companion dir)")
    ap.add_argument("--companion-dir", default=None, help="vChewing dictionary folder whose companion files --out-dict copies (required with --out-dict)")
    ap.add_argument("--force", action="store_true", help="Allow overwriting existing --out-dict or --out-txtmap")

    # Lexicon options
    ap.add_argument("--include-colloq-readings", action="store_true", help="Include tag=='colloq' overlay/sandhi readings")
    ap.add_argument("--no-names", action="store_true", help="Exclude data/lexicon/names-add.tsv")
    ap.add_argument("--packs", default="none", help="Comma-separated subset of acg,it,meme,beauty,quotes or none")
    ap.add_argument("--no-cap-overlay", action="store_true", help="Disable cap_overlay corpus capping")

    # LM files & blends
    ap.add_argument("--kn-path", default=None, help="Path to kn.sjkn (default <shanjie-root>/data/lm/kn.sjkn)")
    ap.add_argument("--kn-beta", type=float, default=0.0, help="Kneser-Ney continuation blend weight beta (default 0.0)")
    ap.add_argument("--colloq-path", default=None, help="Path to colloquial.sjlm (default <shanjie-root>/data/lm/colloquial.sjlm)")
    ap.add_argument("--colloq-uni-w", type=float, default=0.0, help="Colloquial unigram blend weight (default 0.0)")
    ap.add_argument("--colloq-bi-w", type=float, default=0.0, help="Colloquial bigram blend weight (default 0.0)")
    ap.add_argument("--trigram-path", default=None, help="Optional SJT30001 trigram table path")
    ap.add_argument("--extra-ngrams", default=None, help="Optional TSV (reading\\tvalue\\tcontext\\tprob[\\tsource]) overrides; empty context = unigram override/add")
    ap.add_argument("--simplified", action="store_true", help="Also write the Simplified column (EntryType 5) and typeID 5 contextual lines, converted with OpenCC TSPhrases/TSCharacters from --opencc-dir")
    ap.add_argument("--opencc-dir", default=default_opencc_dir(), help="Folder with OpenCC TSPhrases.txt and TSCharacters.txt, sha256-checked (default $SHANJIE_CACHE/sources/opencc)")
    ap.add_argument("--vendor", default=None, help="Write one meta unigram: key _VENDOR, value TEXT, score 0, typeID 2")
    ap.add_argument("--compare-factory", default=None, help="The user's own vChewing VanguardFactoryDict4Typing.txtMap: after writing, compare line categories with the output and exit 2 when the output lacks one the factory file has (except FACTORY_ABSENT_OK)")
    ap.add_argument("--base-txtmap", default=None, help="Existing TextMap 2.0 file: skip the Shanjie build, apply only --extra-ngrams overrides (other build flags ignored)")

    # Unigram tuning
    ap.add_argument("--uni-lam", type=float, default=0.0, help="Unigram corpus blend lambda (default 0.0)")
    ap.add_argument("--len-bonus", type=float, default=0.0, help="Per-extra-char score adjustment (default 0.0)")
    ap.add_argument("--decimals", type=int, default=3, help="Decimal places for rounding probabilities (default 3)")

    # Bigram / contextual emission
    ap.add_argument("--no-bigrams", action="store_true", help="Disable bigram emission (unigram-only + optional extra/trigrams)")
    ap.add_argument("--bi-mode", choices=("blend", "lift"), default="blend", help="Contextual score formula (default blend)")
    ap.add_argument("--bi-lam", type=float, default=0.7, help="Bigram/trigram blend lambda (default 0.7)")
    ap.add_argument("--bi-offset", type=float, default=0.0, help="Constant offset added in blend mode (default 0.0)")
    ap.add_argument("--bi-gamma", type=float, default=1.0, help="PMI lift multiplier in lift mode (default 1.0)")
    ap.add_argument("--bi-min-count", type=int, default=3, help="Minimum bigram count (default 3)")
    ap.add_argument("--bi-min-ctx", type=int, default=5, help="Minimum bigram context total count (default 5)")
    ap.add_argument("--max-ctx-len", type=int, default=4, help="Maximum context word length in chars (default 4)")
    ap.add_argument("--max-val-len", type=int, default=4, help="Maximum target value length in chars (default 4)")
    ap.add_argument("--max-reading-gap", type=float, default=1.5, help="Max score gap from word's best reading (default 1.5)")
    ap.add_argument("--bi-margin", type=float, default=0.02, help="Minimum s_bi - B(R) margin (default 0.02)")
    ap.add_argument("--bi-non-top1-only", action="store_true", help="Skip bigrams where w is already unique top-1 at R")
    ap.add_argument("--bi-budget", type=int, default=0, help="Max emitted bigrams (0 = unlimited)")
    ap.add_argument("--bi-rank", choices=("impact", "lift", "count"), default="impact", help="Ranking metric for budget selection")
    ap.add_argument("--bi-max-per-key", type=int, default=0, help="Max emitted bigrams per reading key (0 = unlimited)")

    # Trigram tuning
    ap.add_argument("--tri-budget", type=int, default=0, help="Max emitted trigrams (0 = unlimited)")
    ap.add_argument("--tri-min-count", type=int, default=3, help="Minimum trigram count (default 3)")
    ap.add_argument("--tri-min-ctx", type=int, default=5, help="Minimum trigram (u, v) total count (default 5)")
    ap.add_argument("--tri-margin", type=float, default=0.02, help="Minimum S_eff - B(R) margin (default 0.02)")

    # Validation
    ap.add_argument("--validate", action=argparse.BooleanOptionalAction, default=True, help="Strictly self-validate output .txtMap")
    return ap


def main() -> int:
    ap = make_parser()
    args = ap.parse_args()
    if not args.out_dict and not args.out_txtmap:
        ap.error("must specify either --out-dict or --out-txtmap")
    if args.out_dict and args.out_txtmap:
        ap.error("specify only one of --out-dict or --out-txtmap")
    if args.out_dict and not args.companion_dir:
        ap.error("--out-dict needs --companion-dir")
    if not (0.0 <= args.kn_beta <= 1.0):
        ap.error("--kn-beta must be in [0, 1]")
    if not (0.0 <= args.colloq_uni_w <= 1.0) or not (0.0 <= args.colloq_bi_w <= 1.0):
        ap.error("--colloq-uni-w and --colloq-bi-w must be in [0, 1]")
    if not (0.0 <= args.uni_lam <= 1.0):
        ap.error("--uni-lam must be in [0, 1]")
    if args.bi_margin < 0.0 or args.tri_margin < 0.0:
        ap.error("--bi-margin and --tri-margin must be >= 0")

    stats = build_textmap(args)
    print(json.dumps({k: v for k, v in stats.items() if k != "flags"}, ensure_ascii=False, indent=2))
    if args.compare_factory:
        out = os.path.join(os.path.realpath(args.out_dict), "VanguardFactoryDict4Typing.txtMap") if args.out_dict else os.path.realpath(args.out_txtmap)
        table, missing = compare_factory(args.compare_factory, out)
        print(table)
        if missing:
            die("the factory file has categories the output lacks and FACTORY_ABSENT_OK does not allow: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
