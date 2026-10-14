#!/usr/bin/env python3
"""Error-bucket triage (docs/contracts/triage.md): where does the shipping setting go wrong, and how many rows is each cause?

  tools/triage.py run --label L [--set NAME ...] [--private-root DIR] [--cli FILE] [--rows-out DIR]
  tools/triage.py table [JSON ...] [--out docs/triage.md]

`run` decodes each set in both profiles with the CLI (shipping setting S: --context --packs acg --tinylm, plus --dump --dump-words
--rowstats --tinylm-scores; and once more without --context for the E flags), puts every wrong row (rowstats ok=0) into one
main bucket (section 3: A1, B1, B2, D1, C; F is the user-reported rows that are right offline) and writes
eval/bench/triage/<label>.json (counts only). Public sets also get a per-row numbers file under --rows-out (default
~/.cache/shanjie/triage), outside the repo; private sets only ever get counts. `table` renders docs/triage.md from the JSON files.
The holdout is never a set here. Standard library only (plus tools/bench.py's set table and reference/proto/eval.py's `lenient`).
"""
import argparse, collections, importlib.util, json, os, struct, subprocess, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402  (set table, CLI helpers, DEFAULT_PRIVATE)

OUT_DIR = os.path.join(ROOT, "eval/bench/triage")
DOC = os.path.join(ROOT, "docs/triage.md")
PROFILES = ["chat", "formal"]
TOPK = 8       # core/src/tinylm.rs TOPK: the candidates the small model reranks
BEAM = 64      # core BEAM_S1: the n-best the dump holds; "in the list" means the first 64
# The five small sets (about 750 rows, contract section 2): the default of `run`. Heavy evaluation runs off the Mac (another machine or
# Colab); these may also run on the Mac as a quick check. The bigger and the private sets are always run elsewhere with --set.
SMALL_SETS = ["dev302", "typing76", "probe", "user-reported", "fw"]
# Not in tools/bench.py's SETS2 as the contract wants them: the whole user-reported file, every 3-field row (bench pins the first 80).
OWN_SETS = {"user-reported": os.path.join(ROOT, "eval/dev/user-reported.txt")}

# Subtag "function word" of C1 (a single homophone character that is the whole difference): the pairs of the S7b function-word analysis,
# docs/research-log.md "S7b：L 的結果（2026-10-11）", the bullet on how much the rerank repairs function-word errors
# (再/在, 做/作, 的/得, 錢/前, 事/是, 哪/那). 他/她 is left out on purpose: the same entry says the reading alone cannot decide it.
FUNCTION_CHARS = frozenset("再在做作的得錢前事是哪那")
RANK_BANDS = ["2", "3-8", "9-64"]
MARGIN_BANDS = ["<0.25", "0.25-0.5", "0.5-1", "1-2", ">=2"]   # log10 n-gram score of rank 1 minus the gold rank; lower edge included
MAIN = ["A1", "B1", "B2", "D1", "C"]
C_SUBS = ["C1", "C2", "C3", "C4", "C5"]
D2S = ["D2a", "D2b", "D2c", "D2-none"]


# ---------------------------------------------------------------- inputs

def load_lenient():
    """reference/proto/eval.py's `lenient` over eval/variants.tsv: the CLI's `ok` and `oracle@64` use the same table."""
    os.environ["SHANJIE_VARIANTS"] = os.path.join(ROOT, "eval/variants.tsv")
    proto = os.path.join(ROOT, "reference/proto")
    if proto not in sys.path:
        sys.path.append(proto)
    spec = importlib.util.spec_from_file_location("proto_eval", os.path.join(proto, "eval.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.lenient


def is_han(ch):
    """core/src/learn.rs is_han: what the engine's context key counts as a Han character."""
    c = ord(ch)
    return (0x3400 <= c <= 0x4DBF or 0x4E00 <= c <= 0x9FFF or 0xF900 <= c <= 0xFAFF or 0x20000 <= c <= 0x2A6DF or 0x2A700 <= c <= 0x2EBEF
            or 0x30000 <= c <= 0x3134F or 0x2F800 <= c <= 0x2FA1F or c == 0x3007)


def read_text(path):
    """The file's text with newlines untouched (newline=""): Rust sees the bytes, and a lone \\r inside a field is not a line break there."""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def lines_of(text):
    """Rust str::lines: split on \\n, drop one trailing \\r per line and the empty piece after a final newline."""
    out = [l[:-1] if l.endswith("\r") else l for l in text.split("\n")]
    return out[:-1] if out and out[-1] == "" else out


def parse_rows(text):
    """cli/src/main.rs three_field_rows: only lines with exactly three `|` fields; -> [(context, truth, syllables)]."""
    rows = []
    for l in lines_of(text):
        p = l.split("|")
        if len(p) == 3:
            rows.append((p[0], p[1], p[2].split()))
    return rows


class Lex:
    """The lexicon the engine and the CLI load (core/src/engine.rs load_lexicon_packs_detailed): mcbpmf-data.txt, then the overlay rows of
    overlay-add.tsv, sandhi-add.tsv and the acg pack. Every (reading, word) entry; `source` = words of overlay-add.tsv or the pack
    (sandhi-add.tsv's changed-tone readings are not a source of words), `base` = words of mcbpmf-data.txt."""

    def __init__(self, base, overlay, sandhi, pack):
        self.by_reading = collections.defaultdict(set)
        self.readings_of = collections.defaultdict(set)
        self.base, self.source = set(), set()
        for l in lines_of(base):   # core Lexicon::parse_with: comment and control lines skipped, exactly three fields, one syllable per character
            f = l.split()
            if l[:1] in ("#", "_") or len(f) != 3 or len(f[1]) != len(f[0].split("-")):
                continue
            float(f[2])
            self._add(tuple(f[0].split("-")), f[1])
            self.base.add(f[1])
        for text, is_source in ((overlay, True), (sandhi, False), (pack, True)):
            for l in lines_of(text):   # overlay_row: exactly four tab fields and a numeric score, else the engine refuses the file
                f = l.split("\t")
                if len(f) != 4:
                    raise ValueError("bad overlay row")
                float(f[2])
                self._add(tuple(f[0].split("-")), f[1])
                if is_source:
                    self.source.add(f[1])

    def _add(self, reading, word):
        self.by_reading[reading].add(word)
        self.readings_of[word].add(reading)

    @classmethod
    def load(cls):
        r = lambda *p: read_text(os.path.join(ROOT, *p))
        return cls(r("data/lexicon/mcbpmf-data.txt"), r("data/lexicon/overlay-add.tsv"), r("data/lexicon/sandhi-add.tsv"), r("data/packs/acg-add.tsv"))

    def has(self, word):
        return word in self.readings_of


def tinylm_params(path):
    """(alpha, tau, delta) per profile from the .sjtl header (core/src/tinylm.rs parse): alpha, tau at 36/44 (chat) and 52/60 (formal), delta at
    100/108 in a version 2 file and 0 in version 1."""
    with open(path, "rb") as f:
        b = f.read(116)
    magic, version = b[:8], struct.unpack_from("<I", b, 8)[0]
    if (magic, version) not in ((b"SJTL0001", 1), (b"SJTL0002", 2)):
        raise ValueError("not a .sjtl file")
    d = struct.unpack_from("<dd", b, 100) if version == 2 else (0.0, 0.0)
    return {"chat": (*struct.unpack_from("<dd", b, 36), d[0]), "formal": (*struct.unpack_from("<dd", b, 52), d[1])}


def pick(ng, ll, alpha, tau, delta):
    """core/src/tinylm.rs pick, line for line."""
    if len(ng) < 2 or ng[0] - ng[1] >= tau:
        return 0
    s0 = ng[0] + alpha * ll[0]
    best, best_s = 0, s0
    for i in range(1, len(ng)):
        s = ng[i] + alpha * ll[i]
        if s > best_s:
            best, best_s = i, s
    return best if best_s - s0 >= delta else 0


# ---------------------------------------------------------------- one row

def bounds(words):
    out, p = {0}, 0
    for w in words:
        p += len(w)
        out.add(p)
    return out


def diff_segments(sel, gold_words, lenient):
    """Section 3 'difference segments': the positions where the chosen sentence differs from the gold path (both leniently normalised, compared
    per character; the original strings when the lengths differ), each widened to the gold path's word boundaries, touching or overlapping
    ones merged. Returns [(start, end)]."""
    gold = "".join(gold_words)
    a, b = lenient(sel), lenient(gold)
    # Normalised positions only mean something when normalising kept every length; otherwise the original strings.
    x, y = (a, b) if len(a) == len(b) == len(sel) == len(gold) else (sel, gold)
    pos = [i for i in range(len(gold))] if len(x) != len(y) else [i for i in range(len(x)) if x[i] != y[i]]
    spans, p = [], 0
    for w in gold_words:
        spans.append((p, p + len(w)))
        p += len(w)
    hit = sorted({s for i in pos for s in spans if s[0] <= i < s[1]})
    out = []
    for s, e in hit:
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def margin_band(m):
    return MARGIN_BANDS[0 if m < 0.25 else 1 if m < 0.5 else 2 if m < 1 else 3 if m < 2 else 4]


def rank_band(r):
    return RANK_BANDS[0 if r == 2 else 1 if r <= 8 else 2]


def classify(row, lex, par, lenient):
    """One wrong row -> {"bucket", "sub", "seg", "func", "source", "rank", "margin", "d2"} (contract section 3).
    row: truth, syls, cands [(surface, score, words)] for ranks 1.., gold0 (rank-0 words or None), c (chosen index), scores (the row's
    --tinylm-scores record or None)."""
    truth, cands, c = row["truth"], row["cands"][:BEAM], row["c"]
    t = lenient(truth)
    gold_rank = next((r for r, (s, _, _) in enumerate(cands, 1) if lenient(s) == t), None)
    if gold_rank:
        gold_words, seg = cands[gold_rank - 1][2], "rank"
    elif row["gold0"] is not None:
        gold_words, seg = row["gold0"], "lex"
    else:
        gold_words, seg = list(truth), "none"   # no path: the difference positions are the segments
    gold = "".join(gold_words)
    segs = diff_segments(cands[c][0], gold_words, lenient)
    res = {"seg": seg, "func": False, "func_any": False, "source": False, "rank": gold_rank, "margin": None, "d2": None, "sub": None}
    # 2 A1: any gold word in a segment that has no entry under the row's reading while the lexicon has it under another
    if seg != "none":
        p = 0
        for w in gold_words:
            if any(s <= p and p + len(w) <= e for s, e in segs):
                if w not in lex.by_reading.get(tuple(row["syls"][p:p + len(w)]), ()) and lex.has(w):
                    return {**res, "bucket": "A1"}
            p += len(w)
    # 3, 4: the gold sentence is not in the first 64
    if gold_rank is None:
        # B1 = a differing segment of >= 2 characters that is no entry. The rank-0 split only holds lexicon words (core segment_spans),
        # so this is "a differing multi-character segment", not proof that a word is missing (contract section 3).
        wide = any(e - s >= 2 and not lex.has(gold[s:e]) for s, e in segs)
        return {**res, "bucket": "B1" if wide else "B2"}
    # 5 D1: the n-gram first was right and the rerank moved off it
    if row["scores"] is not None and lenient(cands[0][0]) == t and c != 0:
        return {**res, "bucket": "D1"}
    res["bucket"] = "C"
    # Function characters in any C sub-type: every differing character pair is two function characters. The C1 flag alone misses the rows
    # where the wrong side glues 在 into a word (你在, 不會在, 我再 -> C2/C3): 6 of 16 such rows in the blind label (research log 2026-10-14).
    x, y = lenient(cands[c][0]), lenient(gold)
    res["func_any"] = len(x) == len(y) and all(p in FUNCTION_CHARS and q in FUNCTION_CHARS for p, q in zip(x, y) if p != q)
    m = cands[0][1] - cands[gold_rank - 1][1]
    res["margin"] = margin_band(m)
    # C sub-types, in order C4, C3, C1, C2, C5 (a wrong row always has a difference segment)
    if len(segs) >= 2:
        res["sub"] = "C4"
    else:
        (s, e), sel_words = segs[0], cands[c][2]
        inside = lambda bs: {b for b in bs if s <= b <= e}
        if inside(bounds(sel_words)) != inside(bounds(gold_words)):
            res["sub"] = "C3"
        elif e - s == 1:
            res["sub"], res["func"] = "C1", gold[s] in FUNCTION_CHARS
        elif not bounds(gold_words) & set(range(s + 1, e)):   # same boundaries, so no word boundary inside on either side: one multi-character word each
            res["sub"] = "C2"
            res["source"] = gold[s:e] in lex.source and gold[s:e] not in lex.base
        else:
            res["sub"] = "C5"
    # D2: the first 8 and the rerank did not save it, following core pick
    sc = row["scores"]
    if gold_rank <= TOPK and sc is not None:
        alpha, tau, delta = par
        if "ll" not in sc:
            res["d2"] = "D2-none"
        elif sc["ng"][0] - sc["ng"][1] >= tau:
            res["d2"] = "D2a"
        else:
            # core pick with delta 0 is the plain argmax of ng + alpha * ll, ties to the better n-gram rank; here the margin is below tau already
            res["d2"] = "D2b" if sc["ok"][pick(sc["ng"], sc["ll"], alpha, tau, 0.0)] else "D2c"
    return res


def empty_counts():
    return {"n": 0, "top1": 0, "errors": 0, "oracle64": 0, "top1_sha256": None, "buckets": dict.fromkeys(MAIN, 0), "F": 0,
            "seg_lex": 0, "seg_none": 0, "c_sub": dict.fromkeys(C_SUBS, 0), "c1_func": 0, "c_func": 0, "c2_source": 0,
            "c_rank": dict.fromkeys(RANK_BANDS, 0), "c_margin": dict.fromkeys(MARGIN_BANDS, 0), "d2": dict.fromkeys(D2S, 0), "E1": 0, "E2": 0}


def triage_rows(rows, ok, ok_nc, lex, par, lenient, report=False):
    """rows: dicts as in `classify` plus "ctx"; ok / ok_nc: rowstats ok lists with and without --context (ok_nc may be None).
    Returns (counts, per-row [(code, rank, margin, E)])."""
    cn, per = empty_counts(), []
    cn["n"], cn["top1"], cn["errors"] = len(rows), sum(ok), len(rows) - sum(ok)
    for i, row in enumerate(rows):
        e = "-"
        if ok_nc is not None and row["ctx"] and is_han(row["ctx"][-1]) and ok[i] != ok_nc[i]:
            e = "E1" if ok[i] else "E2"
            cn[e] += 1
        if ok[i]:
            f = report
            cn["F"] += f
            per.append(("F" if f else "ok", "-", "-", e))
            continue
        r = classify(row, lex, par, lenient)
        cn["buckets"][r["bucket"]] += 1
        cn["seg_lex"] += r["seg"] == "lex"
        cn["seg_none"] += r["seg"] == "none"
        if r["bucket"] == "C":
            cn["c_sub"][r["sub"]] += 1
            cn["c1_func"] += r["func"]
            cn["c_func"] += r["func_any"]
            cn["c2_source"] += r["source"]
            cn["c_rank"][rank_band(r["rank"])] += 1
            cn["c_margin"][r["margin"]] += 1
            if r["d2"]:
                cn["d2"][r["d2"]] += 1
        code = r["sub"] or r["bucket"]
        per.append((code, r["rank"] or "-", r["margin"] or "-", e))
    return cn, per


# ---------------------------------------------------------------- the CLI

def cli_cmd(cli, lm, tl, prof, rows, context, extra):
    return [cli, "--lm", lm, "--profile", prof, "--rows", rows] + (["--context"] if context else []) + ["--packs", "acg", "--tinylm", tl] + extra


def read_cli_files(dump, words, scores, n):
    """-> per row (cands [(surface, score, words)], gold0 words or None, scores record)."""
    cands = [[] for _ in range(n)]
    for l in lines_of(read_text(dump)):
        r, k, s, sc = l.split("\t")
        cands[int(r) - 1].append((s, float(sc), None))
    gold0 = [None] * n
    for l in lines_of(read_text(words)):
        r, k, w = l.split("\t", 2)
        r, k = int(r) - 1, int(k)
        if k == 0:
            gold0[r] = None if w == "-" else w.split(" ")
        else:
            s, sc, _ = cands[r][k - 1]
            if "".join(w.split(" ")) != s:
                raise RuntimeError("dump-words does not match dump")
            cands[r][k - 1] = (s, sc, w.split(" "))
    recs = [None] * n
    for l in lines_of(read_text(scores)):
        j = json.loads(l)
        recs[j["i"] - 1] = j
    return cands, gold0, recs


def run_cell(cli, lm, tl, prof, rows_file, lex, par, lenient, report, td):
    rows = parse_rows(read_text(rows_file))
    f = lambda n: os.path.join(td, n)
    env = dict(os.environ, SHANJIE_VARIANTS=os.path.join(ROOT, "eval/variants.tsv"))
    tl8 = bench.sha_file(tl)[:8]
    # bench.sh raises with the tail of the CLI's stderr; bench.parse_summary checks there is exactly one summary line.
    m = bench.parse_summary(bench.sh(cli_cmd(cli, lm, tl, prof, rows_file, True, ["--dump", f("d"), "--dump-words", f("w"), "--rowstats", f("r"), "--tinylm-scores", f("s")]), env=env))
    m0 = bench.parse_summary(bench.sh(cli_cmd(cli, lm, tl, prof, rows_file, False, ["--rowstats", f("r0")]), env=env))
    # The setting tag the CLI prints: a dropped flag (no context, no pack, no small model) must fail the cell, as in bench.run_cell2.
    for got, ctx_tag in ((m["tag"], "+ctx"), (m0["tag"], "")):
        if got != f"lm-{prof}{ctx_tag}+tl:{tl8}+acg":
            raise RuntimeError(f"{prof}: CLI setting tag {got} is not the shipping setting")
    n, top1, o64, sha = m["n"], m["top1"], m["oracle64"], m["top1_sha256"]
    if not (len(rows) == n == m0["n"]):
        raise RuntimeError(f"the CLI scored {n} rows, the rows file has {len(rows)}")
    ok, ok_nc = bench.parse_rowstats(f("r"), n), bench.parse_rowstats(f("r0"), n)   # raises when a line is malformed or the row count differs
    if top1 != sum(ok):
        raise RuntimeError(f"rowstats top1 {sum(ok)} != CLI summary top1 {top1}")
    cands, gold0, recs = read_cli_files(f("d"), f("w"), f("s"), n)
    recs_rows = []
    for i, (ctx, truth, syls) in enumerate(rows):
        sc = recs[i]
        c = sc["c"]
        sel_ok = int(lenient(cands[i][c][0]) == lenient(truth))
        if sel_ok != ok[i] or (sc["n"] > 1 and "ll" in sc and c != pick(sc["ng"], sc["ll"], *par)):
            raise RuntimeError(f"row {i + 1}: the chosen candidate disagrees with rowstats or with pick")
        recs_rows.append({"ctx": ctx, "truth": truth, "syls": syls, "cands": cands[i], "gold0": gold0[i], "c": c, "scores": sc})
    cn, per = triage_rows(recs_rows, [int(x) for x in ok], [int(x) for x in ok_nc], lex, par, lenient, report)
    cn["oracle64"], cn["top1_sha256"] = o64, sha
    return cn, per


def set_file(name, private_root):
    if name in OWN_SETS:
        return OWN_SETS[name], False
    f, want, private = bench.SETS2[name]
    if private:
        f = os.path.join(os.path.abspath(os.path.expanduser(private_root)), f)
    if not os.path.isfile(f):
        raise SystemExit(f"triage: {name}: no such file {f}")
    if want and bench.sha_file(f) != want:
        raise SystemExit(f"triage: {name}: SHA-256 of {f} does not match the pinned value")
    return f, private


def git_head():
    """HEAD, plus `-dirty` when a tracked file differs from it: the numbers then do not belong to that commit."""
    git = lambda *a: subprocess.run(["git", "-C", ROOT, *a], capture_output=True, text=True, check=True).stdout.strip()
    return git("rev-parse", "HEAD") + ("-dirty" if git("status", "--porcelain", "--untracked-files=no") else "")


def check_same_run(old, new):
    """Sets of one JSON file must come from one build: refuse to add to a file whose commit, models, thresholds or CLI binary differ."""
    bad = [k for k in new if k in old and old[k] != new[k]]
    if bad:
        raise SystemExit(f"triage: the existing file was made with a different {', '.join(bad)}; use another --label or delete it")


def cmd_run(a):
    cli = a.cli or os.path.join(ROOT, "target/release/shanjie-eval")
    lm, tl = os.path.join(ROOT, "data/lm/bigram.sjlm"), os.path.join(ROOT, "data/lm/tinylm.sjtl")
    lenient, lex, par = load_lenient(), Lex.load(), tinylm_params(tl)
    path = os.path.join(OUT_DIR, a.label + ".json")
    doc = json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else {"label": a.label, "sets": {}}
    meta = {"commit": git_head(), "setting": "--context --packs acg --tinylm", "cli_sha256": bench.sha_file(cli),
            "models": {n: bench.sha_file(os.path.join(ROOT, "data/lm", n)) for n in ("bigram.sjlm", "classes.sjc", "tinylm.sjtl")},
            "alpha_tau_delta": {p: list(v) for p, v in par.items()}}
    check_same_run(doc, meta)
    doc.update(meta)
    for name in a.set or SMALL_SETS:
        f, private = set_file(name, a.private_root)
        doc["sets"][name] = {"private": private, "file_sha256": bench.sha_file(f), "profiles": {}}
        for prof in PROFILES:
            with tempfile.TemporaryDirectory() as td:   # the dump and the word dump hold sentences: they are deleted here
                cn, per = run_cell(cli, lm, tl, prof, f, lex, par[prof], lenient, name == "user-reported", td)
            doc["sets"][name]["profiles"][prof] = cn
            if not private:
                os.makedirs(a.rows_out, exist_ok=True)
                with open(os.path.join(a.rows_out, f"{a.label}-{name}-{prof}.tsv"), "w", encoding="utf-8") as o:
                    o.write("".join(f"{i}\t{code}\t{r}\t{m}\t{e}\n" for i, (code, r, m, e) in enumerate(per, 1)))
            print(f"triage: {name} {prof}: n={cn['n']} errors={cn['errors']} {cn['buckets']}", file=sys.stderr)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as o:
        json.dump(doc, o, ensure_ascii=False, indent=1)
        o.write("\n")


# ---------------------------------------------------------------- the tables

# Fix families (section 4): (name, count keys the family can rescue, rescue ratio, ratio source, cost in slices). The ratio is the share of
# those rows a fix is expected to rescue; the sort value is count x ratio / cost, all three printed. 0.3 is a placeholder with no
# measurement behind it (〔推論〕). S7b's 51 of 128 function-word errors (research log 2026-10-11) is the share the rerank fixed, i.e. before the
# rerank; the rows counted here are what is left after it, so that ratio does not apply and no ratio here is measured.
UNMEASURED = "〔推論〕未量，暫定 0.3"
# Contract section 5.3, research log 2026-10-14 (seed 20261014, 60 rows): which buckets the hand labels validated (>= 5 hand-labelled rows each).
BLIND_NOTE = ("盲標（契約 §5.3，60 列，研究紀錄 2026-10-14）：主桶 60／60、C 子類 58／58 和手標一致；驗證過的是 C 與 C1、C2、C3（手標各 ≥ 5 列）。"
              "A1、B1、B2、D1、C4、C5 手標不到 5 列，**未驗證**。B1 量的是「差異段 ≥ 2 字」，不等於缺詞（契約 §3）。")
FAMILIES = [
    ("補讀音（A1）", ["A1"], 0.3, UNMEASURED, 1),
    ("補詞（B1，差異段 ≥ 2 字，不一定缺詞）", ["B1"], 0.3, UNMEASURED, 2),
    ("放大候選名單或 beam（B2）", ["B2"], 0.3, UNMEASURED, 2),
    ("重排與融合的取捨（D1、D2b）", ["D1", "D2b"], 0.3, UNMEASURED, 1),
    ("更強的小模型（D2c、D2-none）", ["D2c", "D2-none"], 0.3, UNMEASURED, 3),
    ("同音功能字（C 裡差異字都是功能字）", ["c_func"], 0.3, UNMEASURED, 2),
    ("斷詞（C3）", ["C3"], 0.3, UNMEASURED, 2),
    ("來源詞的權重（C2 的來源詞）", ["c2_source"], 0.3, UNMEASURED, 1),
]


def key_count(cn, k):
    return cn["buckets"].get(k) if k in cn["buckets"] else cn["c_sub"].get(k) if k in cn["c_sub"] else cn["d2"].get(k) if k in cn["d2"] else cn[k]


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "-"


def render(doc):
    out = [f"## {doc['label']}（commit `{doc['commit'][:8]}{'-dirty' if doc['commit'].endswith('-dirty') else ''}`，出貨設定 `{doc['setting']}`）", ""]
    out.append("模型：" + "、".join(f"{n} `{h[:8]}`" for n, h in doc["models"].items()) + ("、CLI `" + doc["cli_sha256"][:8] + "`" if "cli_sha256" in doc else "") +
               "。α、τ、δ（依設定）：" + "、".join(f"{p} {tuple(v)}" for p, v in doc["alpha_tau_delta"].items()) + "。", )
    out.append("")
    for prof in PROFILES:
        sets = {n: s["profiles"][prof] for n, s in doc["sets"].items() if prof in s["profiles"]}
        if not sets:
            continue
        out += [f"### {prof}：主桶", "", "| 集合 | n | 錯 | 錯率 | F 離線對 | A1 | B1（差異段 ≥ 2 字） | B2 | D1 | C | E1 | E2 |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for n, c in sets.items():
            b = c["buckets"]
            out.append(f"| {n} | {c['n']} | {c['errors']} | {pct(c['errors'], c['n'])} | {c['F'] if n == 'user-reported' else '-'} | {b['A1']} | {b['B1']} | "
                       f"{b['B2']} | {b['D1']} | {b['C']} | {c['E1']} | {c['E2']} |")
        out += ["", f"### {prof}：C 的細分", "", "| 集合 | C1（正解是功能字） | C2（來源詞） | C3 | C4 | C5 | 名次 2 | 3–8 | 9–64 | 分差 <0.25 | 0.25–0.5 | 0.5–1 | 1–2 | ≥2 | D2a | D2b | D2c | D2-none |",
                "|---|" + "---|" * 17]
        for n, c in sets.items():
            s, r, m, d = c["c_sub"], c["c_rank"], c["c_margin"], c["d2"]
            out.append(f"| {n} | {s['C1']}（{c['c1_func']}） | {s['C2']}（{c['c2_source']}） | {s['C3']} | {s['C4']} | {s['C5']} | " + " | ".join(str(r[k]) for k in RANK_BANDS) +
                       " | " + " | ".join(str(m[k]) for k in MARGIN_BANDS) + " | " + " | ".join(str(d[k]) for k in D2S) + " |")
        out += ["", f"### {prof}：修法家族排序（全部集合的計數相加）", "",
                "家族的列數**會重疊，不能相加、也不能當互斥的桶比**：同一個 `c_func` 列也在 C1、C2 或 C3 裡，也可能在 D2b 或 D2c 裡；C3 的列也在 D2c 裡。", "",
                "| 家族 | 可救的列 | 可救比例 | 比例的出處 | 成本（片） | 排序值 |", "|---|---|---|---|---|---|"]
        rank = []
        for name, keys, ratio, src, cost in FAMILIES:
            rows = sum(key_count(c, k) for c in sets.values() for k in keys)
            rank.append((rows * ratio / cost, f"| {name} | {rows} | {ratio} | {src} | {cost} | {rows} × {ratio} ÷ {cost} = {rows * ratio / cost:.1f} |"))
        out += [l for _, l in sorted(rank, key=lambda x: -x[0])] + [""]
    return "\n".join(out)


def cmd_table(a):
    files = a.json or sorted(os.path.join(OUT_DIR, f) for f in os.listdir(OUT_DIR) if f.endswith(".json"))
    docs = {}
    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        for n, st in d["sets"].items():
            for prof, cn in st["profiles"].items():
                if set(cn) != set(empty_counts()):   # a file from an older triage.py: fail here, the numbers would be mislabelled
                    raise SystemExit(f"triage: {f}: {n}/{prof} has the fields {sorted(set(cn) ^ set(empty_counts()))} different from this tool; re-run `run`")
        if d["label"] in docs:
            m = docs[d["label"]]
            if (m["models"], m["commit"], m.get("cli_sha256")) != (d["models"], d["commit"], d.get("cli_sha256")):
                raise SystemExit("triage: two JSON files of one label differ in commit, models or CLI")
            m["sets"].update(d["sets"])
        else:
            docs[d["label"]] = d
    text = ["# 錯誤桶（`tools/triage.py` 產生，不手改）", "",
            "規格：`docs/contracts/triage.md`。每個錯列歸一個主桶（A1 讀音不在詞庫、B1 差異段 ≥ 2 字（不一定缺詞）、B2 不在名單、D1 重排改壞、C 排序錯）；F 是錯字回報裡離線就對的列（不是錯列）；"
            "E1 有前文才對、E2 有前文反而錯（另計）。不含保留集。重跑：`tools/triage.py run --label L`，再 `tools/triage.py table`。", "", BLIND_NOTE, ""]
    text += [render(d) for d in docs.values()]
    with open(a.out, "w", encoding="utf-8") as o:
        o.write("\n".join(text) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", required=True)
    r.add_argument("--set", action="append")
    r.add_argument("--private-root", default=bench.DEFAULT_PRIVATE)
    r.add_argument("--cli")
    r.add_argument("--rows-out", default=os.path.expanduser("~/.cache/shanjie/triage"))
    t = sub.add_parser("table")
    t.add_argument("json", nargs="*")
    t.add_argument("--out", default=DOC)
    a = p.parse_args()
    {"run": cmd_run, "table": cmd_table}[a.cmd](a)


if __name__ == "__main__":
    main()
