"""Digit-or-zhuyin scoring and key simulation: the Python baseline of docs/contracts/digit-context.md C.4 (rules of docs/contracts/digits.md
A.3.1-A.3.5; decisions where that draft's A.8 lists gaps are in experiments/digits/README.md, "Decisions").

Standard layout only. A key is classified as
  alphabet symbol key  1 2 5 8 9 0 - . /   (digits, and the zhuyin ㄅ ㄉ ㄓ ㄚ ㄞ ㄢ ㄦ ㄡ ㄥ)
  tone key             3 4 6 7             (digits, and the tones ˇ ˋ ˊ ˙); the space bar is the first tone
  other symbol key     every other key that types a zhuyin symbol (u j m i k , o l p ; and letters)
  ENTER                the boundary key of C.4.2 (every boundary key flushes the same way)
A run starts at an alphabet symbol key or a tone key with nothing pending. Its buffer holds at most MAX_BUF keys (older keys are flushed to
digit tokens first). For every key the buffer is split into (m digits, t tail keys), the tail being a legal zhuyin tail
(`Legal.tail_units`); `Scorer.decide` scores the splits and picks one.

theta (digits.md A.3.3, digit-context.md C.6 item 3): when the tail closes a syllable, a zhuyin split that holds no digit (emitted + m = 0) is
scored S + theta; one that holds a digit is scored S; the all-digit split wins only when its S is strictly greater than the best of those.

Scores (log10): S = lpD + lpZ.
  lpD = 0 when d = 0, else log P(D|C) + log P_len(d | ended) + sum of log P_ch over the first d keys of the run (emitted keys included, added in key order);
        d = emitted + m; ended = (t > 0 or the key is a boundary).
  lpZ = 0 when t = 0, else log of the sum over the words w compatible with the tail (predict.compat_prefix, best compatible reading's lp, the
        candidate order of predict3.reference) of P(w | C'); C' = the word context C when d = 0, else the digit context (C, D).
  P(w | C) = lm.prob(C, w, pb(w)); P(w | C, D) = Table.p_w_given_vD(w, C, pb(w), gamma); pb(w) = lm.word's backoff term (`base_pb`).
  The sum is a plain left-to-right float sum in candidate order: the Rust port must add in the same order to match bit for bit.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in ("reference/proto", "experiments/sp", "tools"):
    sys.path.insert(0, os.path.join(ROOT, *_p.split("/")))

import lm as L  # noqa: E402
import predict as P1  # noqa: E402
import predict3 as P3  # noqa: E402

# Copied from core/src/engine.rs (SYMBOLS, KEYS_STANDARD, TONE_KEYS_STANDARD); test_digits.py checks them against that source.
SYMBOLS = "ㄅㄆㄇㄈㄉㄊㄋㄌㄍㄎㄏㄐㄑㄒㄓㄔㄕㄖㄗㄘㄙㄧㄨㄩㄚㄛㄜㄝㄞㄟㄠㄡㄢㄣㄤㄥㄦ"
KEYS_STANDARD = "1qaz2wsxedcrfv5tgbyhnujm8ik,9ol.0p;/-"
KEY_SYMBOL = dict(zip(KEYS_STANDARD, SYMBOLS))
TONE_KEYS = {"6": "ˊ", "3": "ˇ", "4": "ˋ", "7": "˙"}   # tone 2, 3, 4, 5; tone 1 is the space bar
ALPHABET = frozenset("0123456789./-")
SPACE, ENTER = " ", "\n"
MAX_BUF = 4
NEG = -math.inf


def keys_of_syllable(syl):
    """A reading -> key presses (engine_replay.rs keys_of): its symbols' keys, then the tone key (the space bar for the first tone)."""
    keys, toned = [], False
    for ch in syl:
        tone = next((k for k, v in TONE_KEYS.items() if v == ch), None)
        if tone:
            keys.append(tone)
            toned = True
        else:
            keys.append(next(k for k, v in KEY_SYMBOL.items() if v == ch))
    return keys if toned else keys + [SPACE]


def is_han(c):
    """core/src/learn.rs is_han."""
    return any(a <= ord(c) <= b for a, b in L._HAN)


def base_pb(lm, w, lp):
    """The backoff term of lm.word (the word's own probability, blended with the KN side file when the model has one): one function in lm.py."""
    return L.unigram_back(lm, w, lp)


class Legal:
    """The legal-tail test (digits.md A.3.1 "切分"), built from the lexicon's readings."""

    def __init__(self, lex):
        self.prefixes, self.han = set(), set()
        for syls, entries in lex.by_reading.items():
            for s in syls:
                bare = "".join(c for c in s if c not in P1.TONES)
                self.prefixes.update(bare[:i] for i in range(1, len(bare) + 1))
            if any(is_han(c) for w, _ in entries for c in w):
                self.han.update(syls)   # a complete syllable must occur in an entry with a Han character (not just the letter word "ㄅ")

    def tail_units(self, keys):
        """Keys -> predict.py units when they form a legal tail, else None. Last key a tone key or the space bar: a complete syllable
        that exists in the lexicon with a Han entry; otherwise symbols only, a prefix of some toneless syllable (this also fixes the
        initial-medial-final order)."""
        last = keys[-1]
        if last == SPACE or last in TONE_KEYS:
            body = keys[:-1]
            if not body or any(k not in KEY_SYMBOL for k in body):
                return None
            if "".join(KEY_SYMBOL[k] for k in body) + TONE_KEYS.get(last, "") not in self.han:
                return None
            return P1.units_of([KEY_SYMBOL[k] for k in body] + [TONE_KEYS.get(last, SPACE)])
        if any(k not in KEY_SYMBOL for k in keys) or "".join(KEY_SYMBOL[k] for k in keys) not in self.prefixes:
            return None
        return P1.units_of([KEY_SYMBOL[k] for k in keys])


class Cache:
    """Candidate lists per tail and word-probability sums per (context, tail); independent of theta, so a theta sweep shares one."""

    def __init__(self):
        self.cands, self.sums = {}, {}


class Scorer:
    def __init__(self, table, lm, idx, legal, theta=0.0, beta=1.0, gamma=1.0, mode="score", cache=None):
        self.tb, self.lm, self.idx, self.legal = table, lm, idx, legal
        self.theta, self.beta, self.gamma, self.mode = theta, beta, gamma, mode
        self.cache = cache or Cache()

    def candidates(self, units):
        key = tuple(units)
        got = self.cache.cands.get(key)
        if got is None:
            got = self.cache.cands[key] = [(e[0], e[1], base_pb(self.lm, e[0], e[1])) for e in P3.reference(self.idx, units, "P")]
        return got

    def lp_z(self, C, digit_ctx, units):
        key = (digit_ctx, C, tuple(units))
        got = self.cache.sums.get(key)
        if got is None:
            tot = 0.0
            if digit_ctx:
                f, tb, g = self.tb.p_w_given_vD, self.tb, self.gamma
                for w, _, pb in self.candidates(units):
                    tot += f(w, C, pb, g)
            else:
                prob = self.lm.prob
                for w, _, pb in self.candidates(units):
                    tot += prob(C, w, pb)
            got = self.cache.sums[key] = math.log10(tot) if tot > 0 else NEG
        return got

    def lp_d(self, C, dkeys, ended):
        if not dkeys:
            return 0.0
        terms = [self.tb.p_D_given(C, self.beta), self.tb.p_len(len(dkeys), ended)] + [self.tb.p_ch(k) for k in dkeys]
        s = 0.0
        for i, x in enumerate(terms):
            if x <= 0:
                return NEG
            s = math.log10(x) if i == 0 else s + math.log10(x)
        return s

    def decide(self, C, emitted, buf, extra, prev_d):
        """Splits of `buf` (the keys in the buffer, the key just pressed included when it is an alphabet or tone key) and the pick.
        extra: None, SPACE, or a non-alphabet symbol key (it always belongs to the tail). emitted: keys already flushed (they are digits).
        prev_d: digits displayed before this key (None at a run's first key). -> ([(m, t, score)], index of the pick or None).
        m counts digits within buf; a candidate with t = 0 is "all digits" (with extra SPACE: digits, then the space as a boundary)."""
        n, cands = len(buf), []
        boundary = extra == SPACE
        for m in range(n, -1, -1):
            tail = buf[m:] + ([extra] if extra is not None else [])
            if not tail:
                cands.append([m, 0, None, None])
            elif len(tail) <= MAX_BUF:
                units = self.legal.tail_units(tail)
                if units is not None:
                    cands.append([m, len(tail), units, None])
        if boundary:
            cands.append([n, 0, None, None])   # all digits, the space is a boundary (the t = 0 split of buf + space)
        if not cands:
            return [], None
        if self.mode == "structural":   # A.3.3 end: the largest legal tail wins
            out = [(m, t, None) for m, t, _, _ in cands]
            return out, max(range(len(out)), key=lambda i: (out[i][1], -i))
        for c in cands:
            m, t, units, _ = c
            digits = emitted + buf[:m]
            lpd = self.lp_d(C, digits, ended=(t > 0 or boundary))
            lpz = self.lp_z(C, bool(digits), units) if t else 0.0
            c[3] = lpd + lpz
        out = [(m, t, s) for m, t, _, s in cands]
        closing = boundary or (extra is None and buf and buf[-1] in TONE_KEYS)
        zh = [i for i, c in enumerate(out) if c[1] > 0]
        dg = [i for i, c in enumerate(out) if c[1] == 0]
        if closing and zh and dg:
            # theta guards only the splits with no digit at all (pure zhuyin); once the run holds a digit the question is where the run ends
            eff = {i: out[i][2] + (self.theta if len(emitted) + out[i][0] == 0 else 0.0) for i in zh}
            best = max(zh, key=lambda i: (eff[i], out[i][1]))
            return out, dg[0] if out[dg[0]][2] > eff[best] else best
        top = max(c[2] for c in out)
        tied = [i for i, c in enumerate(out) if c[2] == top]
        keep = [i for i in tied if prev_d is not None and len(emitted) + out[i][0] == prev_d]
        return out, keep[0] if keep else max(tied, key=lambda i: (out[i][1], -i))


class Run:
    def __init__(self, rid, ctx):
        self.rid, self.ctx = rid, ctx
        self.emitted, self.buf, self.m = [], [], 0   # flushed digit keys, buffered keys, how many buffered keys are shown as digits


class Sim:
    """One composition: feed key presses, read per-key logs and the class of every key ('D' digit, 'Z' zhuyin, 'S' space or other
    separator). `chars` are the characters a finished syllable shows (for the context of later runs); missing ones show as '?'."""

    def __init__(self, scorer, history_fn, left="", chars=()):
        self.sc, self.hist, self.shown, self.chars = scorer, history_fn, left, list(chars)
        self.keys, self.cls, self.rid_of, self.log = [], [], [], []
        self.run, self.pend, self.next_rid = None, [], 0

    # -- helpers
    def _k(self, positions):
        return [self.keys[p] for p in positions]

    def _digits(self, positions):
        for p in positions:
            self.cls[p] = "D"
        return "".join(self._k(positions))

    def _end_run(self, digit_positions):
        """The run is over: its digit keys join the shown text."""
        self.shown += self._digits(digit_positions)
        self.run = None

    def _z_token(self):
        self.shown += self.chars.pop(0) if self.chars else "?"

    def _set_buf_cls(self, run, m):
        run.m = m
        for i, p in enumerate(run.buf):
            self.cls[p] = "D" if i < m else "Z"

    def _start_run(self):
        run = Run(self.next_rid, self.hist(self.shown))
        self.next_rid += 1
        self.run = run
        return run

    # -- one key
    def feed(self, key):
        idx = len(self.keys)
        self.keys.append(key)
        self.cls.append("-")
        self.rid_of.append(None)
        before = list(self.cls)
        ev = {"idx": idx, "key": key, "flush": 0, "ctx": None, "buf": "", "splits": [], "choice": None}
        if key == ENTER:
            if self.run is not None:
                run = self.run
                ev["flush"] = len(run.buf)
                self._end_run(run.emitted + run.buf)
            self.cls[idx] = "S"
            self.pend = []
        elif self.run is not None:
            self._in_run(idx, key, ev)
        elif self.pend:
            self.cls[idx] = "Z"
            if key == SPACE or key in TONE_KEYS:
                self.pend = []
                self._z_token()
            else:
                self.pend.append(idx)
        else:
            self._idle(idx, key, ev)
        ev["cls"] = "".join(self.cls)
        ev["flipped"] = [p for p, b in enumerate(before[:idx]) if b != "-" and self.cls[p] != b]   # earlier keys whose class changed
        ev["flips"] = len(ev["flipped"])
        rid = self.rid_of[idx]
        ev["shows_digit"] = rid is not None and any(self.cls[p] == "D" for p in range(len(self.keys)) if self.rid_of[p] == rid)
        self.log.append(ev)
        return ev

    def _decide(self, run, extra, ev, prev_d):
        ev["ctx"], ev["buf"] = run.ctx, "".join(self._k(run.buf))   # what the splits refer to (the run may end with this key)
        cands, pick = self.sc.decide(run.ctx, self._k(run.emitted), self._k(run.buf), extra, prev_d)
        ev["splits"], ev["choice"] = cands, (cands[pick][0], cands[pick][1]) if pick is not None else None
        return cands, pick

    def _idle(self, idx, key, ev):
        if key in KEY_SYMBOL and key in ALPHABET:
            run = self._start_run()
            run.buf.append(idx)
            self.rid_of[idx] = run.rid
            _, pick = self._decide(run, None, ev, None)   # nothing displayed before: a tie goes to zhuyin
            self._set_buf_cls(run, ev["splits"][pick][0])
        elif key in TONE_KEYS:   # a tone key cannot be a tail alone: a digit token that starts a run
            run = self._start_run()
            run.emitted.append(idx)
            self.rid_of[idx] = run.rid
            self.cls[idx] = "D"
            ev["ctx"] = run.ctx
        elif key in KEY_SYMBOL:
            self.pend = [idx]
            self.cls[idx] = "Z"
        else:
            self.cls[idx] = "S"

    def _in_run(self, idx, key, ev):
        run = self.run
        prev_d = len(run.emitted) + run.m   # digits on display before this key
        self.rid_of[idx] = run.rid
        if key in KEY_SYMBOL and key not in ALPHABET:   # a key that can only be in the tail
            cands, pick = self._decide(run, key, ev, prev_d)
            if pick is None:   # no legal tail: the buffer is digits, the key opens a new syllable
                self._end_run(run.emitted + run.buf)
                self.pend, self.cls[idx] = [idx], "Z"
                return
            m = cands[pick][0]
            self._end_run(run.emitted + run.buf[:m])
            self.pend = run.buf[m:] + [idx]
            for p in self.pend:
                self.cls[p] = "Z"
        elif key in ALPHABET:
            run.buf.append(idx)
            while len(run.buf) > MAX_BUF:
                run.emitted.append(run.buf.pop(0))
                self.cls[run.emitted[-1]] = "D"
                ev["flush"] += 1
            cands, pick = self._decide(run, None, ev, prev_d)
            m, t = cands[pick][0], cands[pick][1]
            if key in TONE_KEYS and t > 0:   # the tail is a complete syllable: the run ends
                self._end_run(run.emitted + run.buf[:m])
                for p in run.buf[m:]:
                    self.cls[p] = "Z"
                self._z_token()
            else:
                self._set_buf_cls(run, m)
        elif key == SPACE:
            cands, pick = self._decide(run, SPACE, ev, prev_d)
            m, t = cands[pick][0], cands[pick][1]
            self._end_run(run.emitted + (run.buf[:m] if t else run.buf))
            self.cls[idx] = "Z" if t else "S"
            for p in (run.buf[m:] if t else ()):
                self.cls[p] = "Z"
            if t:
                self._z_token()
        else:   # some other key (not a zhuyin key): a boundary, nothing is judged
            self._end_run(run.emitted + run.buf)
            self.cls[idx] = "S"


def simulate(scorer, history_fn, keys, left="", chars=()):
    """Feed `keys` then ENTER (every run is settled); -> Sim."""
    sim = Sim(scorer, history_fn, left, chars)
    for k in keys:
        sim.feed(k)
    sim.feed(ENTER)
    return sim


# ---------- digits.md A.3.5: the prediction row after a digit run ----------

def digit_scores(table, lm, idx_cands, v, lam, gamma=1.0):
    """The score of each candidate (w, lp, ...) in a digit context: lam log10 P(w | v, D) + (1 - lam) lp, with lm.word's backoff term as P(w)."""
    out = []
    for e in idx_cands:
        p = table.p_w_given_vD(e[0], v, base_pb(lm, e[0], e[1]), gamma)
        out.append(lam * math.log10(p) + (1 - lam) * e[1] if p > 0 else NEG)
    return out


def digit_tiers(table, lm, idx, units, v, lam, gamma=1.0):
    """predict3.order_s over the same candidates with the digit-context scores and the successor layer N(v,D->w) > 0 or N(D->w) > 0."""
    cands = P3.reference(idx, units, "P")
    return P3.order_s(cands, digit_scores(table, lm, cands, v, lam, gamma), table.successors(v))


def digit_rank_empty(table, lm, lex, v, W, gamma=1.0):
    """No unfinished syllable yet (digits.md A.3.5): the successor layer ordered by P(w | v, D) alone (no tail, so no lexicon blend).
    -> W's pessimistic rank (opponents with an equal P count as ahead), or None when W is not in the layer or not in the lexicon."""
    if W not in table.successors(v) or W not in lex.by_word:
        return None
    ps = {w: table.p_w_given_vD(w, v, base_pb(lm, w, lex.by_word[w][1]), gamma) for w in table.successors(v) if w in lex.by_word}
    return sum(1 for x in ps.values() if x >= ps[W])
