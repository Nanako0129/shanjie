"""Set A of the long-context evaluation sets (README.md): the private chat sets with a 64-character context column.

  chatctx.py superset --discord DIR --meta DIR --self-name NAME --out WORKDIR
  chatctx.py rebuild --frozen-work DIR --superset WORKDIR --period tune|chatjudge --corrections FILE --out FILE
  chatctx.py discordtune --msgs FILE --rows FILE --out FILE

Reads the frozen work directory, never writes in it. Prints counts only.
"""
import argparse
import json
import os
import random
import re
import statistics
import sys

import ctx64
from ctx64 import e2common as c, normalize_ctx, segments_with_ctx, check_rows
import build_chatclause as bc


# ------------------------------------------------------------------ superset
def superset_rows(discord_root, meta_root, self_name):
    """[(month, source, old context, sentence, new context)]: build_chatclause.prepare's extraction with no exclusion list."""
    sources = []
    if discord_root:
        sources.append(("discord", bc.discord_messages(discord_root), True))
    if meta_root:
        for src, (msgs, dropped) in bc.meta_messages(meta_root, self_name).items():
            if msgs or dropped:
                sources.append((src, msgs, False))
    rows = []
    for rank, (src, msgs, cross) in enumerate(sources):
        clean = []
        for ch, mid, ts, text in msgs:
            if ts is None or bc.URL.search(text):
                continue
            if src == "discord":
                text = bc.DISCORD_MARKUP.sub(" ", text)
            if not bc.private_rule(text):
                clean.append((ch, mid, ts, text))
        texts = [m[3] for m in sorted(clean, key=lambda m: (m[0], m[2], m[1]))]   # clauses() numbers messages in this order
        by_msg = {}
        for ts, ch, k, ctx, s, flags in bc.clauses(clean, cross):
            by_msg.setdefault((ch, k), []).append((ts, ctx, s, flags, k))
        for cl in by_msg.values():
            ts, _ctx, first, flags, k = cl[0]
            if "url" in flags or "code" in flags or first is None:
                continue
            new = segments_with_ctx(texts[k])
            if [s for s, _ in new] != [x[2] for x in cl]:
                sys.exit("chatctx superset: STOP, segments_with_ctx disagrees with build_chatclause.clauses about a message")
            for (ts, ctx, s, _f, _k), (_s, nctx) in zip(cl, new):
                rows.append((ts, rank, bc.month(ts), src, ctx, s, nctx))
    rows.sort(key=lambda r: (r[0], r[1]))
    seen, kept = set(), []
    for _ts, _rank, mon, src, ctx, s, nctx in rows:
        if (mon, s) not in seen:
            seen.add((mon, s))
            kept.append((mon, src, ctx, s, nctx))
    return kept


def cmd_superset(a):
    if os.path.exists(os.path.join(a.out, "clauses.tsv")):
        sys.exit("chatctx superset: STOP, --out holds a clauses.tsv: that is a frozen work dir, which is never written")
    kept = superset_rows(a.discord, a.meta, a.self_name)
    os.makedirs(a.out, exist_ok=True)
    c.write_lf(os.path.join(a.out, "superset.tsv"), "".join("\t".join(r) + "\n" for r in kept))
    print(f"superset rows {len(kept)}")


# ------------------------------------------------------------------ rebuild
def replay(clause_rows, months, period):
    """The clauses.tsv rows period_rows picks (before corrections), as the rows themselves. random.sample picks by position,
    so sampling a same-length list of rows gives period_rows's selection."""
    rows = [r for r in clause_rows if r[0] in months]
    tune_idx = random.Random(c.SEED).sample(range(len(rows)), min(bc.TUNE_MAX, len(rows)))
    if period == "tune":
        return [rows[i] for i in tune_idx]
    tuned = {rows[i][3] for i in tune_idx}
    rest = [r for r in rows if r[3] not in tuned]
    return random.Random(bc.CHATJUDGE_SEED).sample(rest, min(bc.CHATJUDGE_MAX, len(rest)))


def rebuild_core(picked, corrections, readings):
    """[(clauses row, final sentence, reading)] of the rows that survive build_chatclause.build_period itself (corrections, CHECK and
    unreadable drops); the index of the clauses row travels in build_period's context slot."""
    frozen, _draft, _st = bc.build_period([(str(i), r[3]) for i, r in enumerate(picked)], corrections, readings)
    return [(picked[int(i)], s, rd) for i, s, rd in frozen]


def dist(ctxs, old):
    ln = sorted(len(x) for x in ctxs)
    q = statistics.quantiles(ln, n=4) if len(ln) > 1 else [0, 0, 0]
    return (f"n {len(ln)} mean {sum(ln) / max(1, len(ln)):.1f} quartiles {q[0]:.0f}/{q[1]:.0f}/{q[2]:.0f} "
            f"empty {sum(x == 0 for x in ln) / max(1, len(ln)):.3f} same-as-old {sum(a == b for a, b in zip(ctxs, old)) / max(1, len(ln)):.3f}")


def inside(path, folder):
    p, f = os.path.realpath(path), os.path.realpath(folder)
    return os.path.commonpath([p, f]) == f


def lines_of(path):
    with open(path, encoding="utf-8", newline="\n") as f:      # only \n ends a line
        return [l.rstrip("\n") for l in f]


def rebuild(frozen_work, superset_dir, period, corrections_file, out, readings=c.readings):
    if inside(out, frozen_work):
        sys.exit("chatctx rebuild: STOP, --out is inside --frozen-work, which is never written")
    frozen_file = os.path.join(frozen_work, f"{period}.rows.txt")
    for p in (os.path.join(frozen_work, "clauses.tsv"), os.path.join(frozen_work, "split.json"), frozen_file, corrections_file):
        print(f"input {os.path.basename(p)} sha256 {c.sha256_file(p)}")
    sup = {}
    for line in lines_of(os.path.join(superset_dir, "superset.tsv")):
        mon, _src, old, s, new = line.split("\t")
        sup[(mon, s)] = (old, new)
    clause_rows = bc.read_clauses(frozen_work)
    for mon, _src, old, s in clause_rows:
        if (mon, s) not in sup or sup[(mon, s)][0] != old:
            sys.exit("chatctx rebuild: STOP, a frozen clauses.tsv row is not in the superset with the same old context")
    split = json.load(open(os.path.join(frozen_work, "split.json"), encoding="utf-8"))
    frozen = [l.split("|") for l in lines_of(frozen_file) if l.strip()]
    got = rebuild_core(replay(clause_rows, set(split["pool_months"]), period), bc.load_corrections(corrections_file), readings)
    if [(r[2], s) for r, s, _rd in got] != [(f[0], f[1]) for f in frozen]:
        sys.exit("chatctx rebuild: STOP, the replayed (old context, sentence) sequence differs from the frozen file")
    new = [sup[(r[0], r[3])][1] for r, _s, _rd in got]
    c.write_lf(out, "".join(f"{n}|{f[1]}|{f[2]}\n" for n, f in zip(new, frozen)))
    check_rows(out, len(frozen))
    print(f"{period} rows {len(frozen)} sha256 {c.sha256_file(out)} {dist(new, [f[0] for f in frozen])}")


# ------------------------------------------------------------------ discordtune
RUN = re.compile(r"[一-鿿]+")


def discordtune(msgs, rows):
    """-> [new context] per row. msgs: message texts; rows: [(old context, sentence)]. Stops on a failed check."""
    runs = []      # (message index, run, old context = previous run of the same message or '', new context)
    for mi, text in enumerate(msgs):
        prev = ""
        # positions come from the raw text; the context is cut from a copy whose Discord markup is blanked (one space per markup, positions kept aligned)
        # each markup becomes ONE space, as in `prepare`; the NULs keep the positions aligned until the context is cut
        masked = bc.DISCORD_MARKUP.sub(lambda m: " " + "\0" * (len(m.group()) - 1), text)
        for m in RUN.finditer(text):
            runs.append((mi, m.group(), prev, normalize_ctx(masked[:m.start()].replace("\0", ""))))
            prev = m.group()
    pos, placed = 0, []
    for old, s in rows:
        j = next((j for j in range(pos, len(runs)) if runs[j][1] == s), None)
        if j is None:
            sys.exit("discordtune: STOP, check (a) failed: a row is not found after the previous row")
        placed.append(j)
        pos = j + 1      # ponytail: "from the previous row's position on" read as the next run (a run holds one row)
    only = {}
    for j, r in enumerate(runs):
        only.setdefault((r[2], r[1]), []).append(j)
    bad = unique = 0
    for j, (old, s) in zip(placed, rows):
        occ = only.get((old, s), [])
        if len(occ) == 1:
            unique += 1
            bad += occ[0] != j
    if bad:
        sys.exit(f"discordtune: STOP, check (b) failed on {bad} rows")
    print(f"discordtune rows {len(rows)} placed {len(placed)} unique-source rows {unique} all at the same position")
    return [runs[j][3] for j in placed]


def cmd_discordtune(a):
    msgs = lines_of(a.msgs)
    print(f"messages {len(msgs)}")
    frozen = [l.split("|") for l in lines_of(a.rows) if l.strip()]
    new = discordtune(msgs, [(f[0], f[1]) for f in frozen])
    c.write_lf(a.out, "".join(f"{n}|{f[1]}|{f[2]}\n" for n, f in zip(new, frozen)))
    check_rows(a.out, len(frozen))
    print(f"sha256 {c.sha256_file(a.out)} {dist(new, [f[0] for f in frozen])}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("superset")
    s.add_argument("--discord"); s.add_argument("--meta"); s.add_argument("--self-name"); s.add_argument("--out", required=True)
    r = sub.add_parser("rebuild")
    r.add_argument("--frozen-work", required=True); r.add_argument("--superset", required=True)
    r.add_argument("--period", required=True, choices=["tune", "chatjudge"])
    r.add_argument("--corrections", required=True); r.add_argument("--out", required=True)
    d = sub.add_parser("discordtune")
    d.add_argument("--msgs", required=True); d.add_argument("--rows", required=True); d.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "superset":
        cmd_superset(a)
    elif a.cmd == "rebuild":
        rebuild(a.frozen_work, a.superset, a.period, a.corrections, a.out)
    else:
        cmd_discordtune(a)


if __name__ == "__main__":
    main()
