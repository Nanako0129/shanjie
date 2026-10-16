"""The measurements of docs/contracts/digit-context.md C.4: the digit-run set (C.4.1), the prediction rows and the digit-or-zhuyin split
(C.4.2), and the R1/R2 verdicts (C.4.3). Only numbers are printed; the digit-run sets are public text (wiki, Common Voice, Tatoeba) and go to a
directory outside the repo. Commands and runtimes: experiments/digits/README.md.

  measure.py build-set --out-dir DIR [--src SOURCES_DIR] [--seed N] [--cap N] [--range LO HI --single NAME]
      --range LO HI --single NAME: the judging set of digit-context.md C.6 item 4: wiki articles LO..HI-1 only (no colloquial), every row to
      the one file NAME (no halves), STOP below SINGLE_MIN rows
  measure.py tune  --table digits.tsv --digit-tune F --cvtune F --wikitune F --out REPORT [--procs N]
  measure.py judge --table digits.tsv --theta X --digit-judge F --dev302 F --typing76 F --out REPORT [--procs N]
Rows: zh rows `前文|句子|讀音`, digit rows `前文|數字串|後接詞|讀音` (reading: syllables separated by spaces).
"""
import argparse
import collections
import hashlib
import json
import multiprocessing as mp
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import digits_score as DS  # noqa: E402
import build_digits as bd  # noqa: E402

P1, P3, L, bc = DS.P1, DS.P3, DS.L, bd.bc
THETAS = (0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0)  # the one place the grid is written; 3-8 added after the first run (digit-context.md C.4.3)
THETA_SET = "{" + ", ".join(f"{t:g}" for t in THETAS) + "}"
SEED = 20261017          # the fixed seed of the digit-run set and of its split into halves
CAP = 3000               # C.4.1: at most 3,000 rows
MIN_ROWS = 1000          # C.4.3 stop condition: fewer rows than this
SINGLE_MIN = 500         # C.6 item 4: the single judging set stops below this
TUNE_ARTICLES = (300_000, 305_000)   # wikitune's articles: the tuning half of the default set
LAMBDAS = (("chat", L.PROFILES["chat"]), ("formal", L.PROFILES["formal"]))
CTX_MAX = 8              # characters of the Han run before the digits kept in a row (context_key uses the last two)
A_MIN = 0.90             # R2: (a) at least this on the judging half


def load_lexicon():
    """The segmentation lexicon of build_counts (mcbpmf-data + overlay-add)."""
    return bc.ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=[os.path.join(ROOT, "data", "lexicon", "overlay-add.tsv")])


# ---------- rows ----------

def parse_syls(text):
    return text.split()


def typable(syl):
    """A syllable the standard layout can type: only zhuyin symbols and tone marks, at least one symbol."""
    return P1.parse_syl(syl) is not None and all(c in DS.SYMBOLS or c in P1.TONES for c in syl)


def load_zh_rows(path, lex):
    """`前文|句子|讀音` -> [(left, sentence, [syllables])]; an empty reading comes from the lexicon (engine_replay.rs). Rows whose syllable
    count differs from the sentence length, or that cannot be spelled, are dropped; -> (rows, dropped)."""
    rows, dropped = [], 0
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("|")
        if len(f) != 3:
            dropped += 1
            continue
        syls = parse_syls(f[2]) or lex.to_syllables(f[1])
        if not syls or len(syls) != len(f[1]) or not all(typable(s) for s in syls):
            dropped += 1
            continue
        rows.append((f[0], f[1], syls))
    return rows, dropped


def load_digit_rows(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line.strip() or line.startswith("#"):
            continue
        f = line.split("|")
        rows.append((f[0], f[1], f[2], parse_syls(f[3])))
    return rows


def keys_of(syls):
    """-> (keys, syllable index of each key)."""
    keys, owner = [], []
    for i, s in enumerate(syls):
        ks = DS.keys_of_syllable(s)
        keys += ks
        owner += [i] * len(ks)
    return keys, owner


# ---------- one row, one summary (all summaries are additive) ----------

class Env:
    """Everything one process needs; built once per process."""

    def __init__(self, table_path):
        self.lm, self.lex = P1.build_lm_lex()
        self.idx = P1.Index(self.lex)
        self.legal = DS.Legal(self.lex)
        self.table = bd.Table.load(table_path)
        self.cache = DS.Cache()
        self.scorers = {}

    def scorer(self, theta, mode):
        key = (theta, mode)
        if key not in self.scorers:
            self.scorers[key] = DS.Scorer(self.table, self.lm, self.idx, self.legal, theta=theta, mode=mode, cache=self.cache)
        return self.scorers[key]

    def history(self, shown):
        return L.history(L.context_key(shown), self.lm)


def merge(a, b):
    for k, v in b.items():
        if isinstance(v, collections.Counter):
            a.setdefault(k, collections.Counter()).update(v)
        else:
            a[k] = a.get(k, 0) + v
    return a


def timing(sim, out):
    for ev in sim.log:
        if ev["splits"]:
            out.setdefault("nsplit", collections.Counter())[len(ev["splits"])] += 1
            out.setdefault("nscan", collections.Counter())[sum(1 for c in ev["splits"] if c[1] > 0)] += 1


def zh_row(env, scorer, row):
    """A pure-Chinese row typed right: (c) syllables that end up with a digit key, (d) key presses that show a digit."""
    left, sent, syls = row
    keys, owner = keys_of(syls)
    sim = DS.simulate(scorer, env.history, keys, left, list(sent))
    bad = {owner[p] for p in range(len(keys)) if sim.cls[p] == "D"}
    out = {"rows": 1, "syl": len(syls), "keys": len(keys), "c": len(bad), "c_rows": int(bool(bad)),
           "d": sum(ev["shows_digit"] for ev in sim.log[:-1])}
    timing(sim, out)
    return out


def digit_row(env, scorer, row):
    """A digit run followed by a word typed in zhuyin: (a) the final split is digits then zhuyin, (b) display flips of the run's own keys."""
    left, digits, word, syls = row
    zkeys, _ = keys_of(syls)
    keys = list(digits) + zkeys
    sim = DS.simulate(scorer, env.history, keys, left, list(word))
    want = "D" * len(digits) + "Z" * len(zkeys)
    own = sim.rid_of[0]   # (b) counts the digit run's own keys only: a run started inside the following word is not this row's display
    flips = sum(1 for ev in sim.log[:-1] for p in ev["flipped"] if sim.rid_of[p] == own)
    out = {"rows": 1, "a_ok": int("".join(sim.cls[:len(keys)]) == want), "flips": collections.Counter([min(flips, 3)])}
    timing(sim, out)
    return out


def pred_row(env, row, lams=LAMBDAS):
    """C.4.2 item 1: (i) the digit-context row before any zhuyin key (once per row, it has no profile); (ii) per profile, hit@9 of the word
    after its first zhuyin key, digit context vs today's `<s>`: keys `<profile>.base|new|new_only|base_only`."""
    left, digits, word, syls = row
    syls = tuple(syls)
    if not any(w == word for w, _ in env.lex.by_reading.get(syls, ())):
        return {"dropped": 1}
    v = env.history(left)
    r_i = DS.digit_rank_empty(env.table, env.lm, env.lex, v, word)
    units = P1.position_units(tuple(P1.parse_syl(s) for s in syls))["P1"]
    cands = P3.reference(env.idx, units, "P")
    out = {"n": 1, "i_hit": int(r_i is not None and r_i <= 9), "i_in_layer": int(r_i is not None)}
    for prof, lam in lams:
        base = P3.rank_s(P3.order_s(cands, P1.scores(cands, env.lm, lam, "<s>"), P1.succ_words(env.lm, "<s>")), word)
        new = P3.rank_s(DS.digit_tiers(env.table, env.lm, env.idx, units, v, lam), word)
        h0, h1 = base is not None and base <= 9, new is not None and new <= 9
        out.update({f"{prof}.base": int(h0), f"{prof}.new": int(h1), f"{prof}.new_only": int(h1 and not h0), f"{prof}.base_only": int(h0 and not h1)})
    return out


# ---------- processes ----------

_ENV = None


def _init(table_path):
    global _ENV
    _ENV = Env(table_path)


def _task(spec):
    kind, rows, theta, mode = spec
    env = _ENV
    out = {}
    for row in rows:
        if kind == "zh":
            r = zh_row(env, env.scorer(theta, mode), row)
        elif kind == "digit":
            r = digit_row(env, env.scorer(theta, mode), row)
        else:
            r = pred_row(env, row)
        merge(out, r)
    return out


class Runner:
    def __init__(self, table_path, procs):
        self.pool = mp.Pool(procs, initializer=_init, initargs=(table_path,)) if procs > 1 else None
        if not self.pool:
            _init(table_path)

    def run(self, kind, rows, theta=0.0, mode="score", chunk=40):
        specs = [(kind, rows[i:i + chunk], theta, mode) for i in range(0, len(rows), chunk)]
        out = {}
        for r in (self.pool.imap_unordered(_task, specs) if self.pool else map(_task, specs)):
            merge(out, r)
        return out


# ---------- verdicts (C.4.3), kept as plain functions so the tests can check the logic without a model ----------

def choose_theta(c_by_theta):
    """R2: the smallest theta whose (c) is 0 on the cvtune and wikitune keys together; None when no theta does.
    c_by_theta: {theta: total count of syllables that ended as digits on both tuning sets}."""
    for th in sorted(c_by_theta):
        if c_by_theta[th] == 0:
            return th
    return None


def r1_verdict(by_setting):
    """R1 on the judging half. by_setting: {name: (new_only, base_only)} (McNemar pair counts: right now / wrong now vs today's `<s>`).
    A setting is worse when the exact McNemar p < 0.05 and base_only > new_only. -> ("digit-context" | "<s>", {name: p})."""
    ps = {k: P1.mcnemar(b, c) for k, (b, c) in by_setting.items()}
    worse = [k for k, (b, c) in by_setting.items() if ps[k] < 0.05 and c > b]
    return ("<s>" if worse else "digit-context"), ps


def r2_verdict(theta, a_ok, a_n, c_dev, c_typing, c_struct):
    """R2 on the judging half, dev302 and typing76. theta None: no theta passed the tuning keys, so the structural rule, without
    looking at the judging data. -> (design, stop) design "score" (digits.md A.3.3) or "structural"; stop True when the structural rule itself
    has (c) > 0 (it cannot with legal readings: the tool or the key set is wrong)."""
    stop = c_struct > 0
    if theta is None:
        return "structural", stop
    ok = a_n > 0 and a_ok / a_n >= A_MIN and c_dev == 0 and c_typing == 0
    return ("score" if ok else "structural"), stop


# ---------- the digit-run set (C.4.1) ----------

def digit_cuts(sentence, lex):
    """[(context, digits, word)]: every digit run of one converted sentence that is directly followed by a Han run that segments; the
    context is the Han run directly before it (up to CTX_MAX characters, possibly empty); the word is the first word of the following Han run."""
    s = sentence.translate(bd.FULL)
    out = []
    for m in bd.TOKEN.finditer(s):
        if not m.group(1):
            continue
        i, j = m.span()
        nxt = bc.HAN.match(s, j)
        words = bc.segment(lex, nxt.group()) if nxt else None
        if not words:
            continue
        k = i
        while k > 0 and bc.HAN.fullmatch(s[k - 1]):
            k -= 1
        out.append((s[max(k, i - CTX_MAX):i], m.group(1), words[0]))
    return out


def pick(candidates, seed, cap):
    """candidates [(sentence id, row)] in input order -> one random candidate per sentence, duplicates removed, at most `cap` sampled
    (the result keeps the input order)."""
    rnd = random.Random(f"{seed}|digitruns")
    by_sid = {}
    for sid, row in candidates:
        by_sid.setdefault(sid, []).append(row)
    rows = list(dict.fromkeys(rnd.choice(g) for g in by_sid.values()))
    if len(rows) > cap:
        keep = set(rnd.sample(range(len(rows)), cap))
        rows = [r for i, r in enumerate(rows) if i in keep]
    return rows


def split_halves(rows, seed):
    """Seeded split into (tuning, judging), each in input order."""
    order = list(range(len(rows)))
    random.Random(f"{seed}|halves").shuffle(order)
    tune = set(order[:len(rows) // 2])
    return [r for i, r in enumerate(rows) if i in tune], [r for i, r in enumerate(rows) if i not in tune]


def readings_by_word(words, workdir):
    """tools/readings.py over `|word` lines -> {word: [syllables]} without CHECK and unreadable words (build_tune's rule)."""
    src, dst = os.path.join(workdir, "digitwords.in"), os.path.join(workdir, "digitwords.all")
    open(src, "w", encoding="utf-8", newline="\n").write("".join(f"|{w}\n" for w in words))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "readings.py"), src, dst], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        sys.exit(f"readings.py failed: {r.stderr[-300:]}")
    check = {l.split("\t")[1] for l in r.stderr.splitlines() if l.startswith("CHECK\t")}
    out = {}
    for l in open(dst, encoding="utf-8"):
        f = l.rstrip("\n").split("|")
        if len(f) == 3 and f[1] not in check and f[2].split():
            out[f[1]] = f[2]
    return out


def make_rows(candidates, seed, cap, reading_fn):
    """-> (rows `前文|數字串|詞|讀音` as tuples, (tuning, judging), counts)."""
    rows = pick(candidates, seed, cap)
    rd = reading_fn(sorted({r[2] for r in rows}))
    kept = [(c, d, w, rd[w]) for c, d, w in rows if w in rd]
    tune, judge = split_halves(kept, seed)
    return kept, (tune, judge), {"picked": len(rows), "with_reading": len(kept), "tuning": len(tune), "judging": len(judge)}


def build_set(a):
    sys.path.insert(0, os.path.join(ROOT, "experiments", "derisk-1015", "e4"))
    import build_e4ctx as e4   # cv_lines and the is_tune cut of build_tune; importing it only defines
    bt = e4.bt
    single = a.range is not None
    if single and not a.single:
        sys.exit("--range needs --single NAME")
    lo, hi = a.range or TUNE_ARTICLES
    bc.SRC = a.src   # load_conv reads opencc/ under it (build_tune's SRC is only its own main's colloquial directory: cv_lines is given the path)
    bc.DUMP = os.path.join(a.src, os.path.basename(bc.DUMP))
    os.makedirs(a.out_dir, exist_ok=True)
    conv = bc.load_conv()
    lex = load_lexicon()
    cands = {"cv": [], "wiki": []}
    if not single:   # the default set: the colloquial tuning lines and wikitune's articles; the single judging set is wiki only
        for n, (_, raw) in enumerate(e4.cv_lines(os.path.join(a.src, "colloquial"))):
            if bt.is_tune(raw):   # on the unconverted line, as build_tune
                cands["cv"] += [(("cv", n), r) for r in digit_cuts(bc.convert(raw, *conv), lex)]
    sid = 0
    for i, raw in enumerate(bc.articles(hi)):
        if i < lo:
            continue
        for s in bc.article_sentences(raw, conv):
            cands["wiki"] += [(("wiki", sid), r) for r in digit_cuts(s, lex)]
            sid += 1
    kept, (tune, judge), counts = make_rows(cands["cv"] + cands["wiki"], a.seed, a.cap, lambda ws: readings_by_word(ws, a.out_dir))
    outs = {a.single: kept} if single else {"digitruns-tune.txt": tune, "digitruns-judge.txt": judge}
    for name, rows in outs.items():
        with open(os.path.join(a.out_dir, name), "w", encoding="utf-8", newline="\n") as f:
            f.writelines(f"{c}|{d}|{w}|{rd}\n" for c, d, w, rd in rows)
    man = {"seed": a.seed, "cap": a.cap, "articles": [lo, hi], "candidates": {k: len(v) for k, v in cands.items()}, "counts": counts,
           "outputs": {n: hashlib.sha256(open(os.path.join(a.out_dir, n), "rb").read()).hexdigest() for n in outs}}
    json.dump(man, open(os.path.join(a.out_dir, a.single.rsplit(".", 1)[0] + ".manifest.json" if single else "digitruns.manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(json.dumps(man, ensure_ascii=False, indent=1))
    need = SINGLE_MIN if single else MIN_ROWS
    if len(kept) < need:
        sys.exit(f"STOP: {len(kept)} digit-run rows, fewer than {need} (digit-context.md C.5, C.6 item 4)")


# ---------- reports ----------

def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "-"


def per_k(x, n):
    return f"{1000 * x / n:.2f}" if n else "-"


def q(counter, p):
    items = sorted(counter.items())
    total, run = sum(counter.values()), 0
    for v, c in items:
        run += c
        if run >= p * total:
            return v
    return float("nan")


def zh_line(name, r):
    return (f"{name}: rows {r['rows']}, syllables {r['syl']}, (c) {r['c']} syllables ({per_k(r['c'], r['syl'])} per 1000; rows with one: {r['c_rows']}), "
            f"(d) {r['d']} key presses ({per_k(r['d'], r['syl'])} per 1000)")


def time_line(r):
    ns, nc = r.get("nsplit", collections.Counter()), r.get("nscan", collections.Counter())
    return (f"splits per scored key p50={q(ns, .5)} p95={q(ns, .95)} max={max(ns) if ns else '-'}; "
            f"compat scans per scored key p50={q(nc, .5)} p95={q(nc, .95)} max={max(nc) if nc else '-'}")


def digit_line(name, r):
    fl = r["flips"]
    return (f"{name}: n {r['rows']}, (a) {r['a_ok']}/{r['rows']} = {pct(r['a_ok'], r['rows'])}; (b) flips per run 0:{fl[0]} 1:{fl[1]} 2:{fl[2]} 3+:{fl[3]}")


def pred_line(name, prof, r):
    new, base, b, c = (r[f"{prof}.{k}"] for k in ("new", "base", "new_only", "base_only"))
    p = P1.mcnemar(b, c)
    return (f"{name} {prof}: n {r['n']} (dropped {r.get('dropped', 0)}); (i) hit@9 {r['i_hit']}/{r['n']} = {pct(r['i_hit'], r['n'])} "
            f"(word in the successor layer {pct(r['i_in_layer'], r['n'])}); (ii) hit@9 digit-context {new} = {pct(new, r['n'])}, `<s>` {base} = {pct(base, r['n'])}, "
            f"right now {b}, wrong now {c}, McNemar p={p:.4g}")


class Report:
    def __init__(self, path):
        self.f = open(path, "x", encoding="utf-8")   # refuses to overwrite: the judging half is looked at once

    def __call__(self, line=""):
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def tune(a):
    rep = Report(a.out)
    rep(f"# tune: table sha256 {sha(a.table)}; digit tuning half {a.digit_tune} sha256 {sha(a.digit_tune)}")
    run = Runner(a.table, a.procs)
    lex = load_lexicon()
    zh = {n: load_zh_rows(p, lex) for n, p in (("cvtune", a.cvtune), ("wikitune", a.wikitune))}
    digit = load_digit_rows(a.digit_tune)
    rep(f"digit tuning rows {len(digit)}; zh rows " + ", ".join(f"{n} {len(r)} (dropped {d})" for n, (r, d) in zh.items()))
    c_by_theta, digit_by_theta = {}, {}
    for th in THETAS:
        tot = 0
        for n, (rows, _) in zh.items():
            r = run.run("zh", rows, th)
            tot += r["c"]
            rep(f"theta {th}  " + zh_line(n, r))
        c_by_theta[th] = tot
        digit_by_theta[th] = run.run("digit", digit, th)
        rep(f"theta {th}  " + digit_line("digit-tune", digit_by_theta[th]))
    c_struct = 0
    for n, (rows, _) in zh.items():
        r = run.run("zh", rows, mode="structural")
        c_struct += r["c"]
        rep("structural  " + zh_line(n, r))
    rep("structural  " + digit_line("digit-tune", run.run("digit", digit, mode="structural")))
    if c_struct > 0:
        rep("STOP: the structural rule has (c) > 0 on cvtune/wikitune, so the key set or the tool is wrong (digit-context.md C.5)")
        sys.exit(1)
    rep(f"time (theta {THETAS[0]}, digit tuning half)  " + time_line(digit_by_theta[THETAS[0]]))
    r = run.run("pred", digit)
    for prof, _ in LAMBDAS:
        rep("tuning half, not for the verdict  " + pred_line("digit-tune", prof, r))
    th = choose_theta(c_by_theta)
    rep(f"R2 theta: {th}" + ("" if th is not None else f"  (no theta in {THETA_SET} gives (c) = 0 on cvtune + wikitune: R2 fails, A uses the structural rule)"))


def judge(a):
    rep = Report(a.out)
    rep(f"# judge: table sha256 {sha(a.table)}; theta {a.theta}; judging half {a.digit_judge} sha256 {sha(a.digit_judge)}; "
        f"dev302 sha256 {sha(a.dev302)}; typing76 sha256 {sha(a.typing76)}")
    run = Runner(a.table, a.procs)
    lex = load_lexicon()
    digit = load_digit_rows(a.digit_judge)
    sets = {n: load_zh_rows(p, lex)[0] for n, p in (("dev302", a.dev302), ("typing76", a.typing76))}
    rep(f"judging rows {len(digit)}; " + ", ".join(f"{n} {len(r)}" for n, r in sets.items()))
    by, r = {}, run.run("pred", digit)
    for prof, _ in LAMBDAS:
        by[prof] = (r[f"{prof}.new_only"], r[f"{prof}.base_only"])
        rep(pred_line("digit-judge", prof, r))
    v1, ps = r1_verdict(by)
    rep(f"R1: the prediction row uses {v1} (McNemar p " + ", ".join(f"{k} {p:.4g}" for k, p in ps.items()) + ")")
    th = None if a.theta == "none" else float(a.theta)
    res = {}
    for mode in ("score", "structural"):
        if mode == "score" and th is None:
            continue
        res[mode] = {}
        if th is not None:   # theta none: only R1 and the structural rule's (c) on dev302/typing76 (the stop check), nothing else on the judging set
            res[mode]["a"] = run.run("digit", digit, th, mode)
            rep(f"{mode}  " + digit_line("digit-judge", res[mode]["a"]))
        for n, rows in sets.items():
            res[mode][n] = run.run("zh", rows, th or 0.0, mode)
            rep(f"{mode}  " + zh_line(n, res[mode][n]))
    sc, st = res.get("score"), res["structural"]
    if sc:
        rep("time (score, judging half)  " + time_line(sc["a"]))
    design, stop = r2_verdict(th, sc["a"]["a_ok"] if sc else 0, sc["a"]["rows"] if sc else 0, sc["dev302"]["c"] if sc else 0,
                              sc["typing76"]["c"] if sc else 0, st["dev302"]["c"] + st["typing76"]["c"])
    rep(f"R2: A uses the {design} design" + ("   STOP: the structural rule has (c) > 0, so the key set or the tool is wrong" if stop else ""))
    if design == "score":
        rep("(d) is report-only: the maintainer decides whether to tighten theta (digits.md A.3.3)")
    if stop:
        sys.exit(1)


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build-set")
    b.add_argument("--out-dir", required=True)
    b.add_argument("--src", default=bc.SRC)
    b.add_argument("--seed", type=int, default=SEED)
    b.add_argument("--cap", type=int, default=CAP)
    b.add_argument("--range", type=int, nargs=2, metavar=("LO", "HI"), help="wiki articles LO..HI-1 only (no colloquial); needs --single")
    b.add_argument("--single", metavar="NAME", help="write every row to out-dir/NAME (no tuning/judging halves)")
    for name in ("tune", "judge"):
        t = sub.add_parser(name)
        t.add_argument("--table", required=True)
        t.add_argument("--out", required=True)
        t.add_argument("--procs", type=int, default=1)
    t = sub.choices["tune"]
    for f in ("digit-tune", "cvtune", "wikitune"):
        t.add_argument("--" + f, required=True)
    j = sub.choices["judge"]
    j.add_argument("--theta", required=True, help="the tune report's R2 theta, or `none`")
    for f in ("digit-judge", "dev302", "typing76"):
        j.add_argument("--" + f, required=True)
    a = ap.parse_args(argv)
    {"build-set": build_set, "tune": tune, "judge": judge}[a.cmd](a)


if __name__ == "__main__":
    main()
