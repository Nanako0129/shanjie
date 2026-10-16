"""P1a selection, staged decisions and reports (Mac or anywhere with numpy; reads score.py / candidates.py output only).

Layout under --work: cands/<set>.<profile>.jsonl (candidates.py output), scored/<set>.<profile>.jsonl (score.py output), eq/<set>.<profile>.jsonl (candidates.py --equalize output),
weights/<reading>.sjw. Sets: cvtune wikitune dev302 typing reported discordtune.

  fuse.py fable    --work W                     the section 0 numbers on cvtune chat (step 0, with the pinned cvtune)
  fuse.py accuracy --work W [--sets ...]        section 3 item 2: classifier vs most common vs n-gram per reading, and the disable rule
  fuse.py stage N  --work W --out stageN.json   section 2.4: active set, per-profile frozen (mu, tau) from cvtune + wikitune only, stop rules
  fuse.py report   --work W --stage stageN.json [--sets ...]   section 3 items 3-4 and the section 4 verdict, with the frozen values
"""
import argparse
import glob
import json
import math
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import p1a  # noqa: E402
from p1a import lenient, surface, is_ok, pick, TUNING, PROFILES  # noqa: E402
import evalstats as E  # noqa: E402

SETS = ("cvtune", "wikitune", "dev302", "typing", "reported", "discordtune")
PUBLIC = SETS[:5]
# the function characters the section 0 numbers count (the readings of p1a plus 的/得 and 坐/座), as in the outside analysis' probe
CONF = set("在再做作坐座的得是事試市式室視世啊阿像向項象到道")


def load_rows(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def load_sets(work, sub, names, profiles=PROFILES):
    """Every named set must exist for every profile: an unknown name or a missing file is an error, never skipped."""
    out = {p: {} for p in profiles}
    for n in names:
        if n not in SETS:
            sys.exit(f"unknown set {n!r} (known: {', '.join(SETS)})")
    for p in profiles:
        for n in names:
            f = os.path.join(work, sub, f"{n}.{p}.jsonl")
            if not os.path.exists(f):
                sys.exit(f"missing input file: {f}")
            out[p][n] = load_rows(f)
    return out


def load_models(work):
    ms = [p1a.load_weights(f) for f in sorted(glob.glob(os.path.join(work, "weights", "*.sjw")))]
    return {m.reading: m for m in ms}


def word_lens(words):
    return [len(w) for w in words for _ in w]


def conf_only(row, i):
    """Positions where candidate i differs from the gold sentence when every difference is between function characters; else None."""
    top, gold = surface(row, i), row["gold"]
    if lenient(top) == lenient(gold) or len(top) != len(gold):
        return None
    d = [k for k, (a, b) in enumerate(zip(gold, top)) if lenient(a) != lenient(b)]
    return d if d and all(gold[k] in CONF and top[k] in CONF for k in d) else None


def pair_count(rows, choose, a, b):
    """Positions where the gold character is a and the chosen candidate's is b (equal lengths only)."""
    return sum(g == a and o == b for r in rows for g, o in p1a.confusions(r["gold"], surface(r, choose(r))))


def cmd_fable(a):
    """Step 0: the section 0 numbers on cvtune (the pinned file) and on the user-reported rows, chat setting, from candidates.py output."""
    for name, rows in load_sets(a.work, "cands", ["cvtune", "reported"], ("chat",))["chat"].items():
        only = [r for r in rows if conf_only(r, 0) is not None]
        in8 = [r for r in only if any(lenient(surface(r, i)) == lenient(r["gold"]) for i in range(len(r["cands"])))]
        multi = [r for r in only if any(word_lens(r["cands"][0]["w"])[k] > 1 for k in conf_only(r, 0))]
        print(f"{name} chat: n={len(rows)}, wrong={sum(not is_ok(r, 0) for r in rows)}, wrong only in function characters={len(only)}, "
              f"gold within the top 8={len(in8)}, wrong character inside a multi-character word of the top-1 path={len(multi)}")


def _pos_acc(rows, reading, choose):
    ok = n = 0
    for r in rows:
        if len(r["gold"]) != len(r["syls"]):
            continue
        top = surface(r, choose(r))
        for k, s in enumerate(r["syls"]):
            if s == reading and len(top) == len(r["gold"]):
                n += 1; ok += lenient(top[k]) == lenient(r["gold"][k])
    return ok, n


def cmd_accuracy(a):
    models = load_models(a.work)
    major = {r: m.meta["majority"] for r, m in models.items()}
    sets = load_sets(a.work, "scored", a.sets.split(","), ("chat",))["chat"]
    print("| 讀音 | 集合 | 字位數 | 分類器 | 最常見字 | n-gram 第一名 |\n|---|---|---|---|---|---|")
    for r in models:
        tot = [0, 0, 0, 0]
        for name, rows in sets.items():
            c = m = n = 0
            for row in rows:
                for g, p in row["gp"].get(r, []):
                    c += lenient(p) == lenient(g); m += lenient(major[r]) == lenient(g); n += 1
            ng, _ = _pos_acc(rows, r, lambda row: 0)
            print(f"| {r} | {name} | {n} | {c} | {m} | {ng} |")
            if name in TUNING:
                tot = [tot[0] + c, tot[1] + m, tot[2] + n, tot[3] + ng]
        print(f"| {r} | cvtune+wikitune | {tot[2]} | {tot[0]} | {tot[1]} | {tot[3]} |")
    print("disabled (classifier not strictly above the most common character, pooled cvtune+wikitune):", p1a.disabled_readings(sets, major))


def cmd_stage(a):
    models = load_models(a.work)
    major = {r: m.meta["majority"] for r, m in models.items()}
    by_prof = load_sets(a.work, "scored", list(TUNING))
    res = p1a.run_stage(a.stage, by_prof, major)
    chat = by_prof["chat"]
    pooled = [r for n in TUNING for r in chat[n]]
    base = sum(is_ok(r, 0) for r in pooled)
    c = res["chat"]
    reasons = []
    print(f"classifiers not strictly above the most common character: {len(res['disabled'])} of {len(major)} trained (stop if more than half)")
    if 2 * len(res["disabled"]) > len(major):
        reasons.append(f"{len(res['disabled'])} of {len(major)} classifiers not above the most common character (more than half)")
    gain = 100 * (c["top1"] - base) / len(pooled)
    print(f"chat cvtune+wikitune: n-gram {base}, frozen {c['top1']} of {len(pooled)} ({gain:+.2f} points; stage 3 stop line: < +0.20)")
    if a.stage == 3 and gain < 0.2:
        reasons.append(f"chat cvtune+wikitune gain {gain:+.2f} points < +0.20")
    rows = chat["cvtune"]
    new = lambda r: pick(r, res["active"], c["mu"], c["tau"])
    old = lambda r: 0
    pairs = {1: [("再", "在", "在", "再")], 2: [("做", "作", "作", "做")]}.get(a.stage, [])
    for fix_a, fix_b, back_a, back_b in pairs:
        f0, f1 = pair_count(rows, old, fix_a, fix_b), pair_count(rows, new, fix_a, fix_b)
        g0, g1 = pair_count(rows, old, back_a, back_b), pair_count(rows, new, back_a, back_b)
        passed = 2 * f1 <= f0 and g1 - g0 <= 2
        print(f"cvtune chat stop rule: {fix_a}>{fix_b} {f0} -> {f1} (must at least halve), {back_a}>{back_b} {g0} -> {g1} (increase <= 2): {'PASS, continue' if passed else 'STOP, write the conclusion'}")
        if not passed:
            reasons.append(f"stage {a.stage} stop rule: {fix_a}>{fix_b} {f0} -> {f1}, {back_a}>{back_b} {g0} -> {g1}")
    res["decision"], res["reasons"] = ("STOP" if reasons else "PASS"), reasons
    json.dump(res, open(a.out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)   # json writes tau = inf as Infinity and reads it back
    print(json.dumps(res, ensure_ascii=False))
    print(f"stage {a.stage} decision: {res['decision']}" + "".join(f"\n  - {x}" for x in reasons))


def arm(label, base, cand):
    """(markdown line, numbers) of tools/evalstats.py for two lists of (ok, surface, gold) row tuples."""
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for name, rs in (("base", base), ("cand", cand)):
            p = os.path.join(d, name)
            open(p, "w", encoding="utf-8", newline="").write(E.format_rowstats(rs))
            paths.append(p)
        line = E.compare(paths[0], paths[1], label).splitlines()[-1]
        b, c = E.read_rowstats(paths[0]), E.read_rowstats(paths[1])
        fixed = sum(1 for x, y in zip(b, c) if not x[0] and y[0]); broken = sum(1 for x, y in zip(b, c) if x[0] and not y[0])
        (tlo, thi), (clo, chi) = E.paired_bootstrap(b, c)
    return line, {"n": len(b), "fixed": fixed, "broken": broken, "p": E.mcnemar_exact(fixed, broken), "d_top1": sum(y[0] - x[0] for x, y in zip(b, c)),
                  "ci_top1": (tlo, thi), "d_cer": sum(y[1] - x[1] for x, y in zip(b, c)) / sum(x[2] for x in b), "ci_cer": (clo, chi)}


def tuples(rows, choose):
    return [(is_ok(r, choose(r)), surface(r, choose(r)), r["gold"]) for r in rows]


def cmd_report(a):
    st = json.load(open(a.stage, encoding="utf-8"))
    models, active, names = load_models(a.work), st["active"], a.sets.split(",")
    scored, eq = load_sets(a.work, "scored", names), load_sets(a.work, "eq", names)
    tables, verdict_cells, disc, ctx_rows = [], [], {}, {}
    for prof in PROFILES:
        mu, tau = st[prof]["mu"], st[prof]["tau"]
        new = lambda r: pick(r, active, mu, tau)
        zero = lambda r: pick(r, active, 0, tau)   # mu = 0 is the first candidate by construction; the baseline itself is checked by top1_sha256 against the CLI (step 0)
        print(f"\n## {prof}: stage {st['stage']}, active {active}, frozen mu={mu} tau={st[prof]['tau']}")
        for name in names:
            rows = scored[prof].get(name)
            if rows is None:
                continue
            first = lambda r: 0
            base = tuples(rows, first)
            line_c, num_c = arm(f"{name} {prof} 分類器", base, tuples(rows, new))
            line_z, _ = arm(f"{name} {prof} μ=0", base, tuples(rows, zero))
            tables += [line_c, line_z]
            if name in eq[prof]:
                eqrows = eq[prof][name]
                assert [r["gold"] for r in eqrows] == [r["gold"] for r in rows], "equalized candidates are for another set"
                line_e, num_e = arm(f"{name} {prof} 詞庫分數拉平", base, tuples(eqrows, first))
                tables.append(line_e)
            else:
                num_e = None
            verdict_cells.append((name, prof, num_c, num_e))
            trig = sum(len(r["cands"]) > 1 and r["cands"][0]["s"] - r["cands"][1]["s"] < tau for r in rows)
            o8 = sum(any(lenient(surface(r, i)) == lenient(r["gold"]) for i in range(len(r["cands"]))) for r in rows)
            ctxrows = sum(any(c["f"][x][0] != c["f"][x][1] for c in r["cands"] for x in active if x in c["f"]) for r in rows)
            if name in ("reported", "discordtune"):
                ctx_rows[(name, prof)] = ctxrows
            only = [r for r in rows if conf_only(r, 0) is not None]
            multi = [r for r in only if any(word_lens(r["cands"][0]["w"])[k] > 1 for k in conf_only(r, 0))]
            multi_fixed = sum(is_ok(r, new(r)) for r in multi)
            unlisted = 0
            for r in rows:
                i, j = 0, new(r)
                tb, tn = surface(r, i), surface(r, j)
                if j != i and len(tb) == len(tn) == len(r["syls"]):
                    unlisted += any(tb[k] != tn[k] and s in active and (tb[k] not in models[s].index or tn[k] not in models[s].index)
                                    for k, s in enumerate(r["syls"]))
            print(f"- {name}: n={len(rows)}, gate trigger {trig} ({100 * trig / len(rows):.1f}%), oracle@8 {o8}, rows where f(c) changes with the context {ctxrows}, "
                  f"wrong only in function characters {len(only)} of which inside a multi-character word {len(multi)} (fixed {multi_fixed}), "
                  f"top-1 changed to or from a character outside the class table {unlisted}")
            for x in active:
                b0, nb = _pos_acc(rows, x, first); n0, _ = _pos_acc(rows, x, new)
                print(f"    {x}: position accuracy {b0}/{nb} -> {n0}/{nb}")
            if name == "discordtune":
                disc[prof] = (num_c, num_e)
    print("\n" + "\n".join(["| 集合 | n | top1 基準 | top1 新 | 改對 | 改壞 | p | CER 基準 | CER 新 | Δtop1 95%（列） | ΔCER 95%（百分點） |", "|---|---|---|---|---|---|---|---|---|---|---|"] + tables))
    print("\n## sizes (section 3 item 4)")
    for r, m in models.items():
        nz, size = p1a.int8_sparse_bytes(m.W[:-1], m.b)
        print(f"{r}: features kept {m.meta['n_keep']}, non-zero {nz}, int8 sparse {size} bytes")
    verdict(st, verdict_cells, disc, ctx_rows)


def verdict(st, cells, disc, ctx_rows):
    """Section 4, chat setting of discordtune, plus the no-significant-loss rule on the other sets (both profiles)."""
    print(f"\n## section 4 verdict (stage {st['stage']})")
    if st.get("decision") != "PASS":   # a stage file without a recorded PASS stops too
        print(f"stage {st['stage']} decision {st.get('decision')}: section 4 is not judged")
        for x in st.get("reasons", []):
            print(f"  - {x}")
        print("verdict: STOP, do not build it into the core; write the conclusion")
        return
    if "chat" not in disc:
        print("discordtune chat not given: no verdict")
        return
    c, e = disc["chat"]
    checks = [(f"discordtune chat Δtop1 {c['d_top1']} rows >= {0.005 * c['n']:.1f} (+0.5 points)", c["d_top1"] >= 0.005 * c["n"]),
              (f"exact McNemar p {c['p']:.4f} < 0.05", c["p"] < 0.05),
              (f"Δtop1 95% interval {c['ci_top1']} excludes 0", c["ci_top1"][0] > 0 or c["ci_top1"][1] < 0),
              (f"ΔCER {100 * c['d_cer']:.2f} points <= 0", c["d_cer"] <= 0),
              (f"net fixed {c['fixed'] - c['broken']} > equalization control {None if e is None else e['fixed'] - e['broken']}",
               e is not None and c["fixed"] - c["broken"] > e["fixed"] - e["broken"])]
    for (name, prof), n in sorted(ctx_rows.items()):
        checks.append((f"{name} {prof}: {n} rows where f(c) changes with the context (must be >= 1, else the context is not wired in: stop)", n >= 1))
    for name, prof, num, _ in cells:
        if name != "discordtune":
            checks.append((f"{name} {prof}: net {num['fixed'] - num['broken']}, p {num['p']:.4f}: not (net < 0 and p < 0.05)", not (num["fixed"] < num["broken"] and num["p"] < 0.05)))
    for text, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {text}")
    print("verdict:", "build it into the core (next slice)" if all(ok for _, ok in checks) else "do not build it into the core; write the conclusion")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("fable", "accuracy", "stage", "report"):
        s = sub.add_parser(n)
        s.add_argument("--work", required=True)
        s.add_argument("--sets", default=",".join(PUBLIC))
        if n == "stage":
            s.add_argument("stage", type=int, choices=(1, 2, 3)); s.add_argument("--out", required=True)
        if n == "report":
            s.add_argument("--stage", required=True)
    a = ap.parse_args(argv)
    {"fable": cmd_fable, "accuracy": cmd_accuracy, "stage": cmd_stage, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
