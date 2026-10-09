# Audit of why pack words lose to lexicon words (docs/research-log.md, 2026-10-10).
# KEPT AS A RECORD, NOT AS A RERUNNABLE SCRIPT: it ran on files in the author's session scratch directory (S and ROOT below), which are
# not in the repo and are not kept. The inputs were the dumps of acg_audit.rs and the pack of c84e1ac.
import collections, math, os, re, sys
S = "/private/tmp/claude-501/-Users-nanako-side-project-shanjie/9cffaeb7-0bdc-48b8-8156-79a5228cbc30/scratchpad"
A = S + "/acg-audit"
ROOT = "/Users/nanako/side-project/shanjie-acg2"
LAM = {"chat": 0.5, "formal": 0.7}

# ---- origins of lexicon entries: (reading with spaces, word) -> tags
origin = collections.defaultdict(set)
for l in open(ROOT + "/data/lexicon/mcbpmf-data.txt", encoding="utf-8"):
    if l[0] in "#_":
        continue
    f = l.split()
    if len(f) == 3:
        origin[(f[0].replace("-", " "), f[1])].add("base")
for fn, tagcol in (("overlay-add.tsv", True), ("sandhi-add.tsv", False)):
    for l in open(ROOT + "/data/lexicon/" + fn, encoding="utf-8"):
        f = l.rstrip("\n").split("\t")
        origin[(f[0].replace("-", " "), f[1])].add(("overlay:" + f[3]) if fn.startswith("overlay") else "sandhi")
for l in open(S + "/r2/packs/acg-add.tsv", encoding="utf-8"):
    f = l.rstrip("\n").split("\t")
    origin[(f[0].replace("-", " "), f[1])].add("pack")

src = collections.defaultdict(lambda: [set(), 0])
for l in list(open(S + "/r2/packs/acg-sources.tsv", encoding="utf-8"))[1:]:
    f = l.rstrip("\n").split("\t")
    src[f[0]][0].add(f[1])
    src[f[0]][1] += len([x for x in f[2].split("; ") if x])


def band(c):
    return "0" if c == 0 else "1-9" if c < 10 else "10-99" if c < 100 else "100-999" if c < 1000 else ">=1000"


def parse(path):
    rows = []
    for l in open(path, encoding="utf-8"):
        f = l.rstrip("\n").split("\t")
        r, w, prof, top, tot, comps, rank, wt, wlp, wc, wv = f
        cs = []
        pos = 0
        for c in comps.split("|"):
            cw, lp, cnt, voc = c.rsplit(":", 3)
            n = len(cw)
            cs.append(dict(w=cw, lp=float(lp) if lp != "NA" else None, cnt=int(cnt), voc=int(voc), reading=" ".join(r.split(" ")[pos:pos + n])))
            pos += n
        rows.append(dict(r=r, w=w, prof=prof, top=top, tot=float(tot), comps=cs, rank=int(rank), wt=None if wt == "NA" else float(wt),
                         wlp=None if wlp == "NA" else float(wlp), wc=int(wc), wv=int(wv)))
    return rows


K = {}


def calibrate(rows):
    """For an OOV single word the decode total is lp + K(profile): K = lam*log10(back(<s>)) + lam*log10(p_eos). Learn K from rows whose w is inside the beam."""
    for prof in LAM:
        ks = [x["wt"] - x["wlp"] for x in rows if x["prof"] == prof and x["wt"] is not None and x["wlp"] is not None and x["wv"] == 0]
        K[prof] = sorted(ks)[len(ks) // 2]
        print("K", prof, K[prof], "min", min(ks), "max", max(ks), "n", len(ks), file=sys.stderr)


def analyse(x):
    lam = LAM[x["prof"]]
    x["wt_est"] = False
    if x["wt"] is None and x["wv"] == 0 and x["wlp"] is not None and K:
        x["wt"] = x["wlp"] + K[x["prof"]]
        x["wt_est"] = True
    x["win"] = x["top"] == x["w"]
    comp = x["comps"]
    x["o_single"] = len(comp) == 1
    x["o_entry"] = (1 - lam) * sum(c["lp"] for c in comp if c["lp"] is not None)
    x["o_lm"] = x["tot"] - x["o_entry"]
    x["o_voc_all"] = all(c["voc"] for c in comp)
    x["o_mincnt"] = min(c["cnt"] for c in comp)
    if x["wt"] is not None and x["wlp"] is not None:
        x["w_entry"] = (1 - lam) * x["wlp"]
        x["w_lm"] = x["wt"] - x["w_entry"]
        x["gap"] = x["wt"] - x["tot"]
        x["d_entry"] = x["w_entry"] - x["o_entry"]
        x["d_lm"] = x["w_lm"] - x["o_lm"]
    else:
        x["w_entry"] = x["w_lm"] = x["gap"] = x["d_entry"] = x["d_lm"] = None
    # mechanism (exclusive)
    if x["o_single"]:
        if x["wv"] == 0 and x["o_voc_all"]:
            m = "M2 w OOV, o a single in-vocab entry"
        elif x["wv"] == 0:
            m = "M3 both OOV, entry score decides"
        elif x["o_voc_all"]:
            m = "M4 both in vocab (context / unigram)"
        else:
            m = "M5 other (w in vocab, o OOV)"
    else:
        if x["wv"] == 0 and x["o_voc_all"]:
            m = "M1 w OOV, o = several in-vocab words"
        elif x["wv"] == 0:
            m = "M6 w OOV, o = several words, some OOV"
        else:
            m = "M7 w in vocab, o = several words"
    x["mech"] = m
    oo = []
    for c in comp:
        tg = sorted(origin.get((c["reading"], c["w"]), {"?"}))
        oo.append("/".join(tg))
    x["o_origin"] = "|".join(oo)
    return x


def pct(a, b):
    return f"{a} / {b} = {100 * a / b:.1f}%" if b else f"{a} / 0"


def summarize(rows, name, lines):
    n = len(rows)
    lose = [x for x in rows if not x["win"]]
    lines.append(f"### {name}\n")
    lines.append(f"- rows (word x profile): {n}; w decodes to itself: {n - len(lose)}; loses: {pct(len(lose), n)}")
    for v, lab in ((0, "w OOV in the bigram model"), (1, "w in the bigram vocabulary")):
        sub = [x for x in rows if x["wv"] == v]
        if sub:
            lines.append(f"- {lab}: {len(sub)} rows, lose {pct(sum(1 for x in sub if not x['win']), len(sub))}")
    c = collections.Counter(x["mech"] for x in lose)
    bl = collections.defaultdict(lambda: [0, 0])
    for x in rows:
        bl[min(len(x["w"]), 5)][0] += 1
        bl[min(len(x["w"]), 5)][1] += (not x["win"])
    lines.append("- loss rate by length: " + ", ".join(f"{k}{'+' if k == 5 else ''} chars: {pct(v[1], v[0])}" for k, v in sorted(bl.items())))
    lines.append("- losers by mechanism:")
    for k, v in sorted(c.items()):
        ex = [f"{x['w']}→{x['top']}" for x in lose if x["mech"] == k][:4]
        lines.append(f"  - {k}: {pct(v, len(lose))}; e.g. {', '.join(ex)}")
    known = [x for x in lose if x["gap"] is not None]
    lines.append(f"- losers whose w score is inside the beam (200): {len(known)}")
    if known:
        e_pos = sum(1 for x in known if x["d_entry"] > 0)
        lm_neg = sum(1 for x in known if x["d_lm"] < 0)
        both = sum(1 for x in known if x["d_entry"] <= 0 and x["d_lm"] < 0)
        lines.append(f"  - entry term of w beats o's: {pct(e_pos, len(known))} (so w loses only through the LM term)")
        lines.append(f"  - LM term of w below o's: {pct(lm_neg, len(known))}; both terms against w: {pct(both, len(known))}")
        gaps = sorted(x["gap"] for x in known)
        lines.append(f"  - score gap (w total - top total): median {gaps[len(gaps) // 2]:.2f}, p10 {gaps[len(gaps) // 10]:.2f}, p90 {gaps[9 * len(gaps) // 10]:.2f}")
        dl = sorted(x["d_lm"] for x in known)
        de = sorted(x["d_entry"] for x in known)
        lines.append(f"  - median LM-term deficit {dl[len(dl) // 2]:.2f}, median entry-term difference {de[len(de) // 2]:.2f}")
    b = collections.Counter(band(x["o_mincnt"]) for x in lose)
    lines.append("  - frequency band of o (min unigram count of its words): " + ", ".join(f"{k}: {b[k]}" for k in ("0", "1-9", "10-99", "100-999", ">=1000")))
    og = collections.Counter(("composed" if not x["o_single"] else "single:" + x["o_origin"]) for x in lose)
    lines.append("  - o origin: " + ", ".join(f"{k}: {v}" for k, v in og.most_common(8)))
    lines.append("")


def tsv_row(x, extra=""):
    f = lambda v: "" if v is None else f"{v:.3f}"
    k = src.get(x["w"])
    return "\t".join([x["prof"], x["r"], x["w"], "+".join(sorted(k[0])) if k else "", str(k[1]) if k else "", str(x["wv"]), str(x["wc"]), f(x["wlp"]),
                      f(x["w_entry"]), f(x["w_lm"]), f(x["wt"]), x["top"], "single" if x["o_single"] else "composed", x["o_origin"],
                      "|".join(str(c["cnt"]) for c in comp_(x)), "|".join(str(c["voc"]) for c in comp_(x)), f(x["o_entry"]), f(x["o_lm"]), f(x["tot"]),
                      f(x["gap"]), f(x["d_entry"]), f(x["d_lm"]), x["mech"]])


def comp_(x):
    return x["comps"]


HEAD = "\t".join(["profile", "reading", "w", "w_source_kinds", "w_source_pages", "w_in_lm_vocab", "w_count", "w_capped_entry_lp", "w_entry_term", "w_lm_term", "w_total",
                  "o", "o_kind", "o_origin", "o_counts", "o_in_vocab", "o_entry_term", "o_lm_term", "o_total", "gap", "d_entry", "d_lm", "mechanism"])

pack = parse(A + "/pack-raw.tsv")
calibrate(pack)
pack = [analyse(x) for x in pack]
lex = [analyse(x) for x in parse(A + "/lex-raw.tsv")]
meta = [l.rstrip("\n").split("\t") for l in open(A + "/lex-meta.tsv", encoding="utf-8")]
for x, m in zip(lex[0::2], meta):
    pass
lexkind = []
for i, m in enumerate(meta):
    lexkind += [m[0], m[0]]

with open(A + "/losers-pack.tsv", "w", encoding="utf-8") as f:
    f.write(HEAD + "\n")
    for x in sorted((x for x in pack if not x["win"]), key=lambda x: (x["gap"] is None, x["gap"] or 0)):
        f.write(tsv_row(x) + "\n")

lines = ["# ACG pack audit: why pack words lose to lexicon words\n"]
lines.append("(machine-generated by analyze.py; terms: entry term = (1-lambda) * sum of the capped entry lp of the words; LM term = decode total - entry term; "
             "lambda 0.5 chat, 0.7 formal. 'in vocab' = `lm.word_id` known in data/lm/bigram.sjlm. Frequency = `lm.count` unigram count.)\n")
summarize(pack, "(i) all pack rows (30,399 rows x 2 profiles)", lines)
summarize([x for x in pack if x["prof"] == "chat"], "(i) chat only", lines)
summarize([x for x in pack if x["prof"] == "formal"], "(i) formal only", lines)
# w words, either profile
lw = {}
for x in pack:
    if not x["win"]:
        lw.setdefault((x["r"], x["w"]), []).append(x["prof"])
lines.append(f"- distinct (reading, word) that lose in at least one profile: {len(lw)} of {len(set((x['r'], x['w']) for x in pack))}; in both: {sum(1 for v in lw.values() if len(v) == 2)}\n")

by_len = collections.defaultdict(lambda: [0, 0])
for x in pack:
    by_len[min(len(x["w"]), 5)][0] += 1
    by_len[min(len(x["w"]), 5)][1] += (not x["win"])
lines.append("- pack loss rate by word length: " + ", ".join(f"{k}{'+' if k == 5 else ''} chars: {pct(v[1], v[0])}" for k, v in sorted(by_len.items())) + "\n")
ls = [x for x in pack if not x["win"] and x["gap"] is not None]
lines.append(f"- all {len(ls)} pack losers with an estimated/actual w total (w total = capped lp + K, K chat {K['chat']:.3f}, formal {K['formal']:.3f}; checked on in-beam rows):")
for thr, lab in ((1.0, "removing the UNSEEN_OVERLAY_PENALTY (+1.0)"), (2.0, "+2.0"), (3.0, "+3.0")):
    lines.append(f"  - w would win with a score {lab}: {pct(sum(1 for x in ls if x['gap'] + thr > 0), len(ls))}")
g = sorted(x["gap"] for x in ls)
lines.append(f"  - gap percentiles p10/p25/p50/p75/p90: " + " / ".join(f"{g[int(q * (len(g) - 1))]:.2f}" for q in (.1, .25, .5, .75, .9)))
comp_l = [x for x in ls if not x["o_single"]]
lines.append(f"  - of those, o is a composition of several words: {pct(len(comp_l), len(ls))}; o consists only of single characters: {pct(sum(1 for x in comp_l if all(len(c['w']) == 1 for c in x['comps'])), len(ls))}")
lines.append("")
# lexicon-wide
for kind in ("overlay-wikt", "overlay-zhwiki", "base"):
    rows = [x for x, k in zip(lex, lexkind) if k == kind]
    summarize(rows, f"lexicon check, pack OFF: random sample of {len(rows)//2} {kind} entries (2-10 Han chars)", lines)

# (ii)
drop_with = [analyse(x) for x in parse(A + "/dropped-with.tsv")]
drop_wo = [analyse(x) for x in parse(A + "/dropped-without.tsv")]
with open(A + "/dropped-c.tsv", "w", encoding="utf-8") as f:
    f.write("\t".join(["profile_dropped_for", "reading", "w", "w_count", "w_in_vocab", "w_capped_lp_if_in_pack", "top_with_w_in_pack", "total_with_w", "o_with_pack_without_w", "o_origin", "o_counts", "o_in_vocab", "o_total", "gap_w_minus_o", "w_lm_term_with", "o_lm_term"]) + "\n")
    dr = [l.rstrip("\n").split("\t") for l in open(S + "/r2/report/collisions.txt", encoding="utf-8") if l.count("\t") == 3 and not l.startswith("#")]
    for r, w, o, prof in dr:
        a = next(x for x in drop_with if x["w"] == w and x["prof"] == prof)
        b = next(x for x in drop_wo if x["w"] == w and x["prof"] == prof)
        wlm = a["tot"] - (1 - LAM[prof]) * sum(c["lp"] for c in a["comps"])
        f.write("\t".join([prof, r, w, str(a["wc"]), str(a["wv"]), f"{a['wlp']:.3f}", a["top"], f"{a['tot']:.3f}", b["top"], b["o_origin"],
                           "|".join(str(c["cnt"]) for c in b["comps"]), "|".join(str(c["voc"]) for c in b["comps"]), f"{b['tot']:.3f}", f"{a['tot'] - b['tot']:.3f}", f"{wlm:.3f}", f"{b['o_lm']:.3f}"]) + "\n")
lines.append("### (ii) the 9 words dropped by the (c) rule (w put into the pack for the measurement; see dropped-c.tsv)\n")
for l in list(open(A + "/dropped-c.tsv", encoding="utf-8"))[1:]:
    f = l.rstrip("\n").split("\t")
    lines.append(f"- {f[0]} {f[1]}: {f[2]} (count {f[3]}, in vocab {f[4]}) would win over {f[8]} (origin {f[9]}, counts {f[10]}, in vocab {f[11]}); gap {f[13]}")
lines.append("")

def pages(x):
    k = src.get(x["w"]); return k[1] if k else 0
def table(title, rows):
    lines.append(title + "\n")
    lines.append("| w | pages | profile | o | o origin | o counts | gap | w entry lp |\n|---|---|---|---|---|---|---|---|")
    for x in rows:
        lines.append(f"| {x['w']} | {pages(x)} | {x['prof']} | {x['top']} | {x['o_origin']} | {'/'.join(str(c['cnt']) for c in x['comps'])} | {format(x['gap'], '.2f')} | {x['wlp']:.2f} |")
    lines.append("")
lose = [x for x in pack if not x["win"]]
table("### 20 notable rows: obscure o (a single lexicon entry seen at most 10 times in the corpus) blocking the best-sourced pack words", sorted([x for x in lose if x["o_single"] and x["o_mincnt"] <= 10], key=lambda x: (-pages(x), x["prof"]))[:20])
table("### 10 best-sourced pack words that lose in chat (o is usually two common characters)", sorted([x for x in lose if x["prof"] == "chat"], key=lambda x: -pages(x))[:10])
open(A + "/summary-raw.md", "w", encoding="utf-8").write("\n".join(lines))
print("\n".join(lines))
