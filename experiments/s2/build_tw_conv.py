"""Build experiments/s2/tw-conv.tsv, the Simplified-to-Taiwan conversion table (docs/contracts/tw-conv.md section 2.3).

  python3 experiments/s2/build_tw_conv.py [--body FILE --push FILE] [--out FILE]   build the table from the PTT snapshot, single process
  python3 experiments/s2/build_tw_conv.py --review [--table FILE]                    print the rows the maintainer looks at, from the table alone

The evidence is the PTT snapshot only (Taiwan's own spelling): the sentence is the last tab field of every line of body.tsv and push.tsv. The
file holds characters, words and counts, never a sentence. The rules are fixed in the contract before any count exists:
  fold   a pair X->Y (VARIANT_PAIRS and the first value of TWVariants.txt) is folded when count(Y) >= 20 * count(X) and count(Y) >= 30
  char   candidates are folded first, then counted in the folded text; the first >= 2 * the second and >= 30 -> `evidence`, else
         the first OpenCC value (folded) -> `hold`
  phrase STPhrases keys that contain a char row whose chosen value is not OpenCC's first value (folded) or that is one of the 31 MediaWiki keys;
         candidates = OpenCC's value with each such position varied over its row's candidates; same decision, a `hold` follows the char row
         only where OpenCC's phrase value uses the key's first value
  term   the keys of mainland-terms.txt with their TWPhrases value
  N      (contract 7) the char rows whose chosen value is not the key, whose key has fewer than 30 PTT occurrences and fewer than 1/20 of the chosen
         value's (folded) occurrences: the keys Taiwan almost never writes (装 痒 苹 价). Marked `N` in column 7 of the char row; --review lists them
Columns: section, key, value, status, manual, counts, and `N` for the rows of the set N. `manual` ("value@date", the date of the decisions.md entry) beats the rule and is kept on rebuild.
"""
import argparse
import collections
import hashlib
import itertools
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_counts as bc  # noqa: E402

TABLE = os.path.join(HERE, "tw-conv.tsv")
TERMS = os.path.join(HERE, "mainland-terms.txt")
PTT = os.path.expanduser("~/.cache/shanjie/sj7/ptt-clean")
SECTIONS = ("fold", "char", "phrase", "term")
RARE_MAX = 30                    # contract 7: a key under this many PTT occurrences (and under 1/20 of the chosen value's) is in N; same bar as the fold rows
FOLD_RATIO, FOLD_MIN = 20, 30    # contract 2.3(3): folding ignores context, so the bar is 20x (not 2x): 泄 vs 洩 is 14x and 排泄 needs 泄
PICK_RATIO, PICK_MIN = 2, 30     # contract 2.3(1)
# The previous VARIANTS table of build_counts (it stays there only as part of MERGE); the other fold candidates are TWVariants.txt's first values.
VARIANT_PAIRS = {"爲": "為", "衆": "眾", "綫": "線", "麪": "麵", "僞": "偽", "裏": "裡", "峯": "峰", "羣": "群", "啓": "啟", "敎": "教", "着": "著", "説": "說"}
# OpenCC (STCharacters) and MediaWiki zh-tw single-character conversion differ in these 31 keys; the MediaWiki value is a candidate (contract 2.3(1)).
MEDIAWIKI = dict(zip("冲凄勋卤吣哗喂埙棂湿牦瘘硷签艳袅账鉴钚钫锎锝锨锫锿镅镎镥鹇鹚𩙧", "沖淒勛鹵唚嘩餵塤櫺濕氂瘺鹼簽艷裊帳鑒鈽鍅鉲鎝杴鉳鑀鋂錼鎦鷴鶿𩗡"))
assert len(MEDIAWIKI) == 31


def die(msg):
    sys.exit(f"build_tw_conv: {msg}")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def sentences(files, chunk_chars=1 << 24):
    """The sentence (last tab field) of every line, joined in chunks of about chunk_chars characters, one sentence per line of the chunk."""
    for path in files:
        buf, size = [], 0
        with open(path, encoding="utf-8", newline="\n") as f:
            for line in f:
                s = line[line.rindex("\t") + 1:]
                buf.append(s if s.endswith("\n") else s + "\n")
                size += len(s)
                if size >= chunk_chars:
                    yield "".join(buf)
                    buf, size = [], 0
        if buf:
            yield "".join(buf)


def read_table(path=TABLE):
    """{section: {key: (value, status, manual, counts, rare)}}; rare is `N` or empty (column 7). The comment lines are skipped. Missing file -> empty."""
    t = {s: {} for s in SECTIONS}
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            if line.startswith("#") or not line.strip():
                continue
            sec, key, value, status, manual, counts, rare = (line.rstrip("\n").split("\t") + [""] * 7)[:7]
            t[sec][key] = (value, status, manual, counts, rare)
    return t


def fold_of(table):
    """The fold map {X: Y} of a table: a fold row whose status is `fold`, or whose manual value differs from X."""
    out = {}
    for x, (y, status, manual, *_) in table["fold"].items():
        y = manual.split("@")[0] if manual else (y if status == "fold" else x)
        if y != x:
            out[x] = y
    return out


# ---------------------------------------------------------------------------------------------------------------- building
def pick(cands, counts, first):
    """cands: unique candidates (folded), counts: {cand: n}. -> (value, status)."""
    order = sorted(range(len(cands)), key=lambda i: (-counts[cands[i]], i))
    top = counts[cands[order[0]]]
    second = counts[cands[order[1]]] if len(order) > 1 else 0
    if top >= PICK_RATIO * second and top >= PICK_MIN:
        return cands[order[0]], "evidence"
    return first, "hold"


def fmt_counts(cands, counts):
    return " ".join(f"{c}:{counts[c]}" for c in cands)


def build_rows(files, old):
    t0 = time.time()
    o = bc.read_opencc()
    st, stp, twv, twp = o["st"], o["stp"], o["twv"], o["twp"]
    word_chars = bc.lexicon_word_chars()
    S = bc.true_simplified(st, word_chars)
    # pass 1: raw character counts
    cc = collections.Counter()
    for chunk in sentences(files):
        cc.update(chunk)
    log(f"pass 1: {sum(cc.values())} characters, {len(cc)} types", t0)
    # fold
    pairs = dict(twv)
    for x, y in VARIANT_PAIRS.items():
        if pairs.setdefault(x, y) != y:
            die(f"VARIANT_PAIRS and TWVariants.txt disagree on {x}")
    pairs = {x: y for x, y in pairs.items() if x != y}
    fold_rows = {}
    for x in sorted(pairs):
        y = pairs[x]
        yes = cc[y] >= FOLD_MIN and cc[y] >= FOLD_RATIO * cc[x]
        fold_rows[x] = (y, "fold" if yes else "nofold", f"{x}:{cc[x]} {y}:{cc[y]}")
    F = {x: r[0] for x, r in fold_rows.items() if r[1] == "fold"}
    if any(y in F for y in F.values()):
        die("a fold target is itself folded: " + " ".join(y for y in F.values() if y in F))
    man = lambda sec, key: old[sec].get(key, ("",) * 5)[2]
    for x, (y, status, c) in fold_rows.items():     # a manual fold decision changes the fold map the counting uses
        m = man("fold", x)
        if m:
            if m.split("@")[0] == x:
                F.pop(x, None)
            else:
                F[x] = m.split("@")[0]
    fold = lambda s: "".join(F.get(c, c) for c in s)
    fc = collections.Counter()
    for c, n in cc.items():
        fc[F.get(c, c)] += n
    # single characters
    char_rows, row_cands = {}, {}
    for k, v in st.items():
        cand = list(v)
        if k not in S and k not in v:
            cand.append(k)
        if k in MEDIAWIKI and MEDIAWIKI[k] not in cand:
            cand.append(MEDIAWIKI[k])
        if len(v) > 1 or k in MEDIAWIKI or (k not in S and k not in v):
            cands = list(dict.fromkeys(fold(c) for c in cand))
            first = fold(v[0])
            value, status = pick(cands, fc, first)
            char_rows[k] = (value, status, fmt_counts(cands, fc))
            row_cands[k] = (cands, first)
    chosen = lambda k: (man("char", k).split("@")[0] if man("char", k) else char_rows[k][0])
    rare = {k for k in char_rows if chosen(k) != k and cc[k] < RARE_MAX and FOLD_RATIO * cc[k] < fc[chosen(k)]}
    char_rows = {k: (*r, "N" if k in rare else "") for k, r in char_rows.items()}
    log(f"char rows: {len(char_rows)}, N: {len(rare)}", t0)
    trig = {k for k in char_rows if k in MEDIAWIKI or chosen(k) != row_cands[k][1]}
    # phrases: candidates, then one pass over the folded text
    phrase_cands = {}
    for k, v in stp.items():
        pos = [i for i, c in enumerate(k) if c in trig]
        if not pos:
            continue
        if len(v) != len(k):
            die(f"STPhrases {k}: value length differs, no position to vary")
        fv = fold(v)
        opts = [list(dict.fromkeys([fv[i]] + row_cands[k[i]][0])) for i in pos]
        cands = []
        for combo in itertools.product(*opts):
            s = list(fv)
            for i, c in zip(pos, combo):
                s[i] = c
            cands.append("".join(s))
        cands = list(dict.fromkeys(cands))
        phrase_cands[k] = (pos, fv, cands)
    strings = {s for _, _, cands in phrase_cands.values() for s in cands}
    anchor = {c for k, (pos, _, cands) in phrase_cands.items() for s in cands for c in (s[i] for i in pos)}
    shapes = collections.defaultdict(set)
    for s in strings:
        j = next(i for i, c in enumerate(s) if c in anchor)
        shapes[s[j]].add((j, len(s)))
    log(f"phrase keys: {len(phrase_cands)}, candidate strings: {len(strings)}, anchor chars: {len(anchor)}, "
        f"anchor occurrences: {sum(cc[c] for c in anchor)}", t0)
    pc = dict.fromkeys(strings, 0)
    anchor_re = re.compile("[" + "".join(re.escape(c) for c in sorted(anchor)) + "]")
    trans = str.maketrans(F)
    for chunk in sentences(files):
        chunk = chunk.translate(trans)
        for m in anchor_re.finditer(chunk):
            i = m.start()
            for j, n in shapes[chunk[i]]:
                if i >= j:
                    s = chunk[i - j:i - j + n]
                    if s in pc:
                        pc[s] += 1
    log("pass 2 done", t0)
    phrase_rows = {}
    for k, (pos, fv, cands) in phrase_cands.items():
        value, status = pick(cands, pc, fv)
        if status == "hold":    # follow the char row only where OpenCC's phrase value uses the key's own first value
            s = list(fv)
            for i in pos:
                if fv[i] == row_cands[k[i]][1]:
                    s[i] = chosen(k[i])
            value = "".join(s)
        phrase_rows[k] = (value, status, fmt_counts(cands, pc))
    # mainland terms
    terms = [l.rstrip("\n") for l in open(TERMS, encoding="utf-8") if l.strip() and not l.startswith("#")]
    missing = [k for k in terms if k not in twp]
    if missing:
        die(f"mainland-terms.txt keys that are not TWPhrases.txt keys: {missing[:5]}")
    return {"fold": fold_rows, "char": char_rows, "phrase": phrase_rows, "term": {k: (twp[k], "term", "") for k in terms}}, cc


def write_table(rows, files, path):
    old = read_table(path)
    try:
        dirty = subprocess.run(["git", "status", "--porcelain", "--", os.path.join(HERE, "build_tw_conv.py"), os.path.join(HERE, "build_counts.py")],
                               capture_output=True, text=True, cwd=HERE).stdout.strip()
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip() + (" (tool files modified)" if dirty else "")
    except OSError:
        commit = "unknown"
    out = ["# tw-conv.tsv: docs/contracts/tw-conv.md section 2.3. Built by experiments/s2/build_tw_conv.py from the PTT snapshot (characters, words and counts only).",
           *(f"# input {os.path.basename(f)} sha256 {sha256_file(f)}" for f in files),
           f"# tool commit {commit}",
           "# columns: section, key, value, status, manual, counts[, N]. status: evidence | hold | fold | nofold | term. manual: value@date beats the rule. N (char rows): the set N of contract 7."]
    for sec in SECTIONS:
        for k in sorted(rows[sec]):
            r = rows[sec][k]
            value, status, counts, rare = r[0], r[1], (r[2] if len(r) > 2 else ""), (r[3] if len(r) > 3 else "")
            out.append("\t".join((sec, k, value, status, old[sec].get(k, ("",) * 5)[2], counts) + ((rare,) if rare else ())))
    tmp = path + ".tmp"
    open(tmp, "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
    os.replace(tmp, path)


def log(msg, t0):
    print(f"[{time.time() - t0:7.1f}s] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------------------------------------------- review
def review(table_path):
    """The rows for the maintainer (contract 2.3, end): chosen value not OpenCC's first (folded), every char `hold`, phrase `hold` with any count,
    every `nofold`. Printed as key, rule result, manual, candidates with counts."""
    t = read_table(table_path)
    st = bc.read_opencc()["st"]
    F = fold_of(t)
    fold = lambda s: "".join(F.get(c, c) for c in s)
    stp = bc.read_opencc()["stp"]
    lines = []
    for k, (value, status, manual, counts, rare) in sorted(t["char"].items()):
        if status == "hold" or value != fold(st[k][0]):
            lines.append(("char", k, value, status, manual, counts))
    for k, (value, status, manual, counts, rare) in sorted(t["char"].items()):
        if rare:
            lines.append(("N", k, value, status, manual, counts))
    for k, (value, status, manual, counts, rare) in sorted(t["phrase"].items()):
        if value != fold(stp[k]) or (status == "hold" and any(int(c.rsplit(":", 1)[1]) for c in counts.split())):
            lines.append(("phrase", k, value, status, manual, counts))
    for k, (value, status, manual, counts, rare) in sorted(t["fold"].items()):
        if status == "nofold":
            lines.append(("fold", k, value, status, manual, counts))
    n = collections.Counter(l[0] for l in lines)
    print(f"# review rows: {dict(n)}; columns: section, key, rule value, status, manual, candidates with counts")
    for l in lines:
        print("\t".join(l))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true", help="print the rows for the maintainer from the table (needs no corpus)")
    ap.add_argument("--table", default=TABLE)
    ap.add_argument("--body", default=os.path.join(PTT, "body.tsv"))
    ap.add_argument("--push", default=os.path.join(PTT, "push.tsv"))
    ap.add_argument("--out", default=TABLE)
    a = ap.parse_args(argv)
    if a.review:
        return review(a.table)
    t0 = time.time()
    files = [a.body, a.push]
    rows, _ = build_rows(files, read_table(a.out))
    write_table(rows, files, a.out)
    print(f"wrote {a.out}: " + ", ".join(f"{s} {len(rows[s])}" for s in SECTIONS) + f"; {time.time() - t0:.1f} s")


if __name__ == "__main__":
    main()
