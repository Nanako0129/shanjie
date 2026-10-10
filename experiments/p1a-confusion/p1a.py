"""P1a (docs/contracts/p1a-confusion.md): the pure pieces. Everything here is deterministic and runs without the real corpus.

Data flow (README.md has the commands):
  runs      wiki_runs / colloquial_runs   the HAN runs build_counts / build_counts_text count (same functions)
  classes   target_positions              per-position reading = the containing word's by_word reading (section 2.1)
  samples   mask_features / sample_features   features of left part + right part, the position itself masked (section 2.2)
  training  train_lr / save_weights       multinomial logistic regression, fixed hyper-parameters, numpy, deterministic
  scoring   candidate_f / char_term       f(c) of section 2.3 over every character position of a candidate
  fusion    pick / select / run_stage     s' = s_ngram + mu * f, tau gate, frozen selection, staged activation (section 2.4)
"""
import copy
import hashlib
import json
import math
import os
import struct
import sys
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _d in ("experiments/s2", "reference/proto", "tools"):
    sys.path.insert(0, os.path.join(ROOT, _d))
import build_counts as bc  # noqa: E402
import build_counts_text as bct  # noqa: E402
import ime  # noqa: E402
import lm as L  # noqa: E402
from eval import lenient  # noqa: E402

# ---- section 2.1 / 2.2 / 2.3: everything the contract fixes in advance ----
READINGS = ("ㄗㄞˋ", "ㄗㄨㄛˋ", "ㄕˋ", "ㄚ", "ㄒㄧㄤˋ", "ㄉㄠˋ")
STAGES = (("ㄗㄞˋ",), ("ㄗㄨㄛˋ",), ("ㄕˋ", "ㄚ", "ㄒㄧㄤˋ", "ㄉㄠˋ"))   # readings added at stage 1, 2, 3
OTHER = "其他"
MIN_CLASS_COUNT, MAX_CLASSES = 1000, 6
CAP = 300_000                                  # per class; half for wiki, half for colloquial
SRC_WEIGHT = {"w": 1.0, "c": 5.0}
HASH_BITS, MIN_FEATURE_COUNT = 20, 3
LR, BATCH, EPOCHS, WEIGHT_DECAY, SEED = 0.01, 4096, 3, 1e-6, 20261010
MUS = (0.1, 0.2, 0.3, 0.5, 0.7, 1, 1.5, 2, 3)
TAUS = (0.5, 1, 2, 3, 5, math.inf)
TUNING = ("cvtune", "wikitune")                # the only sets selection and the disable rule may read
PROFILES = ("chat", "formal")
EQUALIZE = {"ㄗㄞˋ": ("再", "在"), "ㄗㄨㄛˋ": ("做", "作")}   # (rare, common): lexicon score of rare := common's
Q_SMOOTH = 0.5
TOP_K = 8


# ---------------------------------------------------------------- data preparation

def training_lexicon():
    """Base + overlay-add.tsv, the lexicon build_counts._init segments with (not the capped decode lexicon)."""
    d = os.path.join(ROOT, "data", "lexicon")
    return ime.Lexicon(os.path.join(d, "mcbpmf-data.txt"), overlay=[os.path.join(d, "overlay-add.tsv")])


def wiki_runs(lex, texts, conv):
    """HAN runs of raw wiki page texts, in the order and with the filters of build_counts.count_batch
    (entities, TEMPLATE x3, MARKUP, SENT, convert, HAN; length >= 2; the run must segment)."""
    phrase, char, maxp = conv
    for raw in texts:
        t = raw.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
        for _ in range(3):
            t = bc.TEMPLATE.sub("", t)
        for pat, rep in bc.MARKUP:
            t = pat.sub(rep, t)
        for s in bc.SENT.findall(t):
            for run in bc.HAN.findall(bc.convert(s, phrase, char, maxp)):
                if len(run) >= 2 and bc.segment(lex, run):
                    yield run


def colloquial_runs(lex, paths, conv):
    """The same for sentence files, as build_counts_text.main reads them (bct.lines, then convert, HAN, length >= 2)."""
    phrase, char, maxp = conv
    for p in paths:
        for s in bct.lines(p):
            for run in bc.HAN.findall(bc.convert(s, phrase, char, maxp)):
                if len(run) >= 2 and bc.segment(lex, run):
                    yield run


def seg(lex, text):
    """Highest-score segmentation (bc.segment); a part that cannot be segmented (a cut inside a word the full run used)
    falls back to single characters."""
    return bc.segment(lex, text) or list(text)


def target_positions(lex, words):
    """[(char index, char, reading)] for every character of a segmented run: the reading is the syllable the containing
    word has in lex.by_word (highest score, first in file order on ties). Section 2.1."""
    out, i = [], 0
    for w in words:
        syls = lex.by_word[w][0]
        for j, ch in enumerate(w):
            out.append((i + j, ch, syls[j]))
        i += len(w)
    return out


def load_cls_of(model_path):
    """word -> classes-v3 class id, or -1 (no class / not in the model vocabulary). Reads only the vocabulary and cls table
    (format: reference/proto/lm.py, tools/build_classes.py); refuses a classes.sjc built for another model."""
    b = open(model_path, "rb").read()
    assert b[:8] == b"SJLM0001", "bad model magic"
    (V,) = struct.unpack_from("<I", b, 8)
    p = 8 + struct.calcsize("<IQQd")
    ids = {}
    for i in range(V):
        (n,) = struct.unpack_from("<H", b, p); p += 2
        ids[b[p:p + n].decode("utf-8")] = i; p += n
    c = open(os.path.join(os.path.dirname(os.path.abspath(model_path)), "classes.sjc"), "rb").read()
    if c[:8] != L.CLASS_MAGIC or c[8:40] != hashlib.sha256(b).digest():
        raise ValueError("classes.sjc was not built for this model")
    K, _mu, V2 = struct.unpack_from("<IdI", c, 40)
    assert V2 == V
    cls = struct.unpack_from(f"<{V}H", c, 56)
    return lambda w: (-1 if ids.get(w) is None or cls[ids[w]] == L.NO_CLASS else cls[ids[w]])


def class_table(counts):
    """counts: {char: n} of the target reading over the whole (unweighted) stream. The class characters: n >= 1000, at most 6,
    most frequent first (ties by code point)."""
    return sorted((c for c, n in counts.items() if n >= MIN_CLASS_COUNT), key=lambda c: (-counts[c], c))[:MAX_CLASSES]


# ---------------------------------------------------------------- features (section 2.2)

FEATURES = ("bos", "eos", "l1", "l2", "l12", "r1", "r2", "r12", "l1r1", "lw", "rw", "lc", "rc", "lcrc")
NF = len(FEATURES)


def mask_features(lex, cls_of, left, right):
    """The feature strings of a masked position from the text left of it and the text right of it only. Both parts are
    segmented on their own; nothing here can see the character at the position, the word it sits in, or word edges."""
    lws, rws = seg(lex, left), seg(lex, right)
    lw, rw = (lws[-1] if lws else "^"), (rws[0] if rws else "^")
    l1, l2, r1, r2 = left[-1:] or "^", left[-2:-1] or "^", right[:1] or "^", right[1:2] or "^"
    lc, rc = cls_of(lw) if lws else -1, cls_of(rw) if rws else -1
    vals = (int(not left), int(not right), l1, l2, l2 + l1, r1, r2, r1 + r2, l1 + "|" + r1, lw, rw, lc, rc, f"{lc}|{rc}")
    return [f"{n}={v}" for n, v in zip(FEATURES, vals)]


def hash_ids(strs):
    """Stable (not Python's per-process hash) 2^20-bucket ids."""
    return np.array([zlib.crc32(s.encode("utf-8")) & ((1 << HASH_BITS) - 1) for s in strs], dtype=np.int32)


def sample_features(lex, cls_of, text, pos, ctx=""):
    """Training / gold-sentence form: the position `pos` of `text` is cut out; ctx (context_key chars) goes in front of the left part."""
    return hash_ids(mask_features(lex, cls_of, ctx + text[:pos], text[pos + 1:]))


def compact(ids):
    """Drop features seen fewer than 3 times (counted over all slots of the kept samples). Returns (keep, columns):
    keep = sorted hashed ids that stay, columns = ids mapped to 0..len(keep)-1 and len(keep) for a dropped one (zero row)."""
    cnt = np.bincount(ids.ravel(), minlength=1 << HASH_BITS)
    keep = np.flatnonzero(cnt >= MIN_FEATURE_COUNT).astype(np.int32)
    lut = np.full(1 << HASH_BITS, len(keep), dtype=np.int32)
    lut[keep] = np.arange(len(keep), dtype=np.int32)
    return keep, lut[ids]


# ---------------------------------------------------------------- sample caps (section 2.2)

class Quota:
    """At most CAP/2 samples per (reading, class, source), in stream order."""

    def __init__(self, keys, cap=CAP // 2):
        self.cap, self.cnt = cap, {k: 0 for k in keys}

    def accept(self, key):
        if self.cnt[key] < self.cap:
            self.cnt[key] += 1
            return True
        return False

    def full(self):
        return {k for k, n in self.cnt.items() if n >= self.cap}

    def done(self, src):
        return all(n >= self.cap for k, n in self.cnt.items() if k[2] == src)


# ---------------------------------------------------------------- training (section 2.2, numpy)

def train_lr(cols, y, sw, C, D, seed=SEED):
    """Multinomial logistic regression on binary hashed features. cols (N, F) int32 in 0..D-1 (D-1 = the zero row of dropped
    features); y (N,) class ids; sw (N,) sample weights. Adam lr 0.01, batch 4096, 3 epochs, L2 1e-6 on W and b, zero init,
    permutations from RandomState(seed). Plain float64 numpy with no BLAS reduction: same input -> same bytes.
    Returns (W (D-1, C), b (C,))."""
    rng = np.random.RandomState(seed)
    W, b = np.zeros((D, C)), np.zeros(C)
    mW, vW, mb, vb = np.zeros_like(W), np.zeros_like(W), np.zeros_like(b), np.zeros_like(b)
    N, F = cols.shape
    t, rows = 0, np.arange(BATCH)
    for _ in range(EPOCHS):
        perm = rng.permutation(N)
        for s in range(0, N, BATCH):
            ix = perm[s:s + BATCH]
            xb, yb, w = cols[ix], y[ix], sw[ix]
            z = W[xb].sum(axis=1) + b
            z -= z.max(axis=1, keepdims=True)
            p = np.exp(z)
            p /= p.sum(axis=1, keepdims=True)
            p[rows[:len(ix)], yb] -= 1.0
            p *= (w / w.sum())[:, None]
            gW = np.zeros_like(W)
            np.add.at(gW, xb.ravel(), np.repeat(p, F, axis=0))
            gW[D - 1] = 0.0
            gW += WEIGHT_DECAY * W
            gb = p.sum(axis=0) + WEIGHT_DECAY * b
            t += 1
            for g, m, v, a in ((gW, mW, vW, W), (gb, mb, vb, b)):
                m *= 0.9; m += 0.1 * g
                v *= 0.999; v += 0.001 * g * g
                a -= LR * (m / (1 - 0.9 ** t)) / (np.sqrt(v / (1 - 0.999 ** t)) + 1e-8)
            W[D - 1] = 0.0
    return W[:D - 1], b


WMAGIC = b"P1AW0001"


def save_weights(path, meta, keep, W, b):
    """Weights file: magic, u32 meta length, meta JSON (sorted keys), int32 keep[n], float32 W[n, C], float32 b[C]; little-endian,
    nothing time-dependent, so equal training gives an equal SHA-256."""
    m = json.dumps(meta, sort_keys=True, ensure_ascii=False).encode("utf-8")
    blob = (WMAGIC + struct.pack("<I", len(m)) + m + keep.astype("<i4").tobytes()
            + np.ascontiguousarray(W, dtype="<f4").tobytes() + np.ascontiguousarray(b, dtype="<f4").tobytes())
    with open(path, "wb") as f:
        f.write(blob)
    return hashlib.sha256(blob).hexdigest()


def load_weights(path):
    blob = open(path, "rb").read()
    assert blob[:8] == WMAGIC, "bad weights magic"
    (n,) = struct.unpack_from("<I", blob, 8)
    meta = json.loads(blob[12:12 + n].decode("utf-8"))
    p, k, C = 12 + n, meta["n_keep"], len(meta["classes"]) + 1
    keep = np.frombuffer(blob, "<i4", k, p); p += 4 * k
    W = np.frombuffer(blob, "<f4", k * C, p).reshape(k, C); p += 4 * k * C
    b = np.frombuffer(blob, "<f4", C, p)
    return Model(meta, keep, W, b)


def int8_sparse_bytes(W, b):
    """Size of the (not shipped) int8 sparse form: per non-zero feature a u32 id and C int8 weights; per class an f32 scale and
    an f32 bias; 16 bytes header. Returns (non-zero features, bytes)."""
    nz = int((W != 0).any(axis=1).sum())
    return nz, 16 + nz * (4 + W.shape[1]) + 8 * W.shape[1]


class Model:
    """One reading's classifier. classes = the class characters; the last class (index len(classes)) is OTHER."""

    def __init__(self, meta, keep, W, b):
        self.meta, self.reading, self.classes = meta, meta["reading"], list(meta["classes"])
        self.other = len(self.classes)
        self.index = {c: i for i, c in enumerate(self.classes)}
        self.W = np.vstack([np.asarray(W, dtype=np.float64), np.zeros((1, self.other + 1))])
        self.b = np.asarray(b, dtype=np.float64)
        self.lut = np.full(1 << HASH_BITS, len(keep), dtype=np.int32)
        self.lut[np.asarray(keep, dtype=np.int64)] = np.arange(len(keep), dtype=np.int32)
        oc = meta["other_counts"]
        self.best_other = min(oc, key=lambda c: (-oc[c], c)) if oc else None

    def predict(self, ids):
        z = self.W[self.lut[ids]].sum(axis=0) + self.b
        p = np.exp(z - z.max())
        return np.maximum(p / p.sum(), 1e-300)

    def q(self, ch):
        """q(c | OTHER) = (n(c) + 0.5) / (N + 0.5 V)."""
        m = self.meta
        return (m["other_counts"].get(ch, 0) + Q_SMOOTH) / (m["other_total"] + Q_SMOOTH * m["v_other"])

    def predict_char(self, p):
        """The most probable character under P_char: the best class character, or OTHER's most common character."""
        best = max(range(self.other), key=lambda j: (p[j], -j))
        if self.best_other is not None and p[self.other] * self.q(self.best_other) > p[best]:
            return self.best_other
        return self.classes[best]


# ---------------------------------------------------------------- scoring (section 2.3)

def char_term(model, p, ch):
    """log10 P_char(ch | features) - max_m log P(m | features); <= 0. A character outside the class table gets P(OTHER) * q(ch | OTHER)."""
    j = model.index.get(ch)
    # log10, the base of the n-gram scores s it is added to (s + mu * f)
    lp = math.log10(p[j]) if j is not None else math.log10(p[model.other]) + math.log10(model.q(ch))
    return lp - math.log10(p.max())


def scored_positions(words, syls, reading):
    """[char index] for every character of the candidate, inside multi-character words too, whose typed syllable is `reading`."""
    out, i = [], 0
    for w in words:
        for j in range(len(w)):
            if syls[i + j] == reading:
                out.append(i + j)
        i += len(w)
    return out


def candidate_f(model, featurize, ctxk, words, syls):
    """f(c) for one reading: the sum of char_term over the candidate's positions. featurize(left, right) -> hashed ids; the left part
    is the context_key characters followed by the candidate up to the position (a sentinel context is the empty string)."""
    text, tot = "".join(words), 0.0
    for i in scored_positions(words, syls, model.reading):
        tot += char_term(model, model.predict(featurize(ctxk + text[:i], text[i + 1:])), text[i])
    return tot


def gold_predictions(model, featurize, ctxk, gold, syls):
    """[(gold char, classifier's character)] at the gold sentence's positions of this reading, with the context."""
    return [(gold[i], model.predict_char(model.predict(featurize(ctxk + gold[:i], gold[i + 1:]))))
            for i, ch in enumerate(syls) if ch == model.reading and len(gold) == len(syls)]


# ---------------------------------------------------------------- decode lexicon and the equalization control

def equalize(lex, readings=tuple(EQUALIZE)):
    """Copy of lex where the rare character's score is the common one's (再 := 在, 做 := 作) for the given readings only."""
    out = copy.copy(lex)
    out.by_reading, out.by_word = {k: list(v) for k, v in lex.by_reading.items()}, dict(lex.by_word)
    for reading in readings:
        rare, common = EQUALIZE[reading]
        key = (reading,)
        lp = dict(lex.by_reading[key])[common]
        out.by_reading[key] = sorted([(w, lp if w == rare else s) for w, s in lex.by_reading[key]], key=lambda x: -x[1])
        out.by_word[rare] = (key, lp)
    return out


# ---------------------------------------------------------------- fusion, selection, stages (sections 2.3, 2.4)
# A row: {"gold": str, "syls": [...], "cands": [{"s": n-gram score, "w": [words], "f": {reading: [f with context, f without]}}], "gp": {reading: [[gold, predicted]]}}
# cands are in decode order (best first).

def pick(row, active, mu, tau):
    """Index of the chosen candidate: the n-gram best unless its lead over the second is < tau, then the best s + mu * f
    (ties keep the earlier candidate). f sums the active readings."""
    c = row["cands"]
    if len(c) < 2 or c[0]["s"] - c[1]["s"] >= tau:
        return 0
    best, bi = None, 0
    for i, x in enumerate(c):
        v = x["s"] + mu * sum(x["f"][r][0] for r in active if r in x["f"])
        if best is None or v > best:
            best, bi = v, i
    return bi


def surface(row, i):
    return "".join(row["cands"][i]["w"])


def is_ok(row, i):
    return lenient(surface(row, i)) == lenient(row["gold"])


def select(sets, active):
    """The frozen (mu, tau) for one profile: best pooled top-1 over the tuning sets only; ties: smaller tau (inf is the largest), then smaller mu.
    sets: {set name: rows}. Returns (mu, tau, top1)."""
    rows = [r for name in TUNING for r in sets[name]]
    best = None
    for tau in TAUS:
        for mu in MUS:
            n = sum(is_ok(r, pick(r, active, mu, tau)) for r in rows)
            if best is None or (-n, tau, mu) < best[0]:
                best = ((-n, tau, mu), mu, tau, n)
    return best[1], best[2], best[3]


def pooled_accuracy(sets, majority):
    """{reading: (classifier correct, majority-character correct, positions)} over the tuning sets' gold positions together."""
    acc = {}
    for name in TUNING:
        for r in sets[name]:
            for reading, pairs in r.get("gp", {}).items():
                a = acc.setdefault(reading, [0, 0, 0])
                for g, p in pairs:
                    a[0] += lenient(p) == lenient(g)
                    a[1] += lenient(majority[reading]) == lenient(g)
                    a[2] += 1
    return {k: tuple(v) for k, v in acc.items()}


def disabled_readings(sets, majority):
    """Readings whose classifier is not strictly better than always guessing the most common character, pooled over both tuning sets."""
    acc = pooled_accuracy(sets, majority)
    return sorted(r for r in majority if not acc.get(r, (0, 0, 0))[0] > acc.get(r, (0, 0, 0))[1])


def stage_active(stage, sets, majority):
    """Active readings at stage 1..3: trained so far minus the disabled ones."""
    trained = [r for s in STAGES[:stage] for r in s if r in majority]
    off = set(disabled_readings(sets, majority))
    return [r for r in trained if r not in off]


def run_stage(stage, sets_by_profile, majority):
    """Section 2.4 (a)+(b): the active set, then a fresh selection and freeze per profile.
    sets_by_profile: {profile: {set name: rows}}. Returns {"active": [...], "disabled": [...], profile: {"mu", "tau", "top1"}}."""
    gp_sets = sets_by_profile[PROFILES[0]]   # the classifier predictions do not depend on the profile
    active = stage_active(stage, gp_sets, majority)
    out = {"stage": stage, "active": active, "disabled": disabled_readings(gp_sets, majority)}
    for prof, sets in sets_by_profile.items():
        mu, tau, n = select(sets, active)
        out[prof] = {"mu": mu, "tau": tau, "top1": n}
    return out


def confusions(gold, top1):
    """[(gold char, top-1 char)] at the positions where they differ (lenient), only when the lengths agree."""
    if len(gold) != len(top1):
        return []
    return [(a, b) for a, b in zip(gold, top1) if lenient(a) != lenient(b)]
