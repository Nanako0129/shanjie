"""E2 wikitypo (docs/contracts/e2-chat-eval.md section 2.3): Wikipedia typo pairs whose wrong and right words read the same, a guard set only.

  python build_wikitypo.py [--src ~/.cache/shanjie/e2-public/wikitypos] [--out ~/.cache/shanjie/e2-public/wikitypo.txt]

Reads SRC/pairs.tsv (wrong, right, source; SHA-256 pinned below) and SRC/AutoWikiBrowser_Typos.rev90288329.wikitext (SHA-256 checked
against SRC/manifest.json). Drops the rows of the AutoWikiBrowser rules that turn 藉 into 籍 (context rules: their lookarounds, which
the pair expansion dropped, are what excluded the exceptions): the rules are found in the raw wikitext by their find/replace and a
pair is dropped when the rule's regex matches its wrong word, never by position. Keeps the distinct pairs whose wrong and right words
have identical readings (tools/readings.py; CHECK and unreadable words drop the pair). Then rows whose gold has a TRAD_SIMP_ONLY character and repeated golds (first kept) are
dropped (main, 2026-10-12). One row per pair: no context, gold = right
word. Writes OUT (`|right|reading`) and OUT.manifest.json (counts per step, SHA-256). The row file is not for the repo.
"""
import argparse
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2common as c  # noqa: E402

PAIRS_SHA = "fd5c558962b7563c3e6170c2a6c0a4b8d93657ef7a96d194f5de5a00a8af29d6"
AWB = "Wikipedia:AutoWikiBrowser/Typos"
CACHE = os.path.expanduser("~/.cache/shanjie/e2-public")
TYPO = re.compile(r'<Typo word="[^"]*" find="([^"]*)" replace="([^"]*)"\s*/>')


def awb_jie_rules(wikitext):
    """Compiled `find` regexes of the AWB rules that rewrite 藉 as 籍 and carry a lookaround (a context rule)."""
    out = []
    for find, repl in TYPO.findall(wikitext):
        find, repl = html.unescape(find), html.unescape(repl)
        if "藉" in find and "籍" in repl and ("(?!" in find or "(?<" in find):
            out.append(re.compile(find))
    return out


def dropped_by_rule(pair, rules):
    wrong, right, source = pair
    return source == AWB and "藉" in wrong and right == wrong.replace("藉", "籍") and any(r.fullmatch(wrong) for r in rules)


def read_pairs(path):
    lines = open(path, encoding="utf-8").read().split("\n")
    assert lines[0] == "wrong\tright\tsource", "pairs.tsv header changed"
    return [tuple(l.split("\t")) for l in lines[1:] if l]


def trad_simp_only():
    """The project's simplified-only characters for a traditional sentence (experiments/s2/build_counts.TRAD_SIMP_ONLY), not the full
    SIMP_ONLY, which holds common Taiwan characters."""
    sys.path[:0] = [os.path.join(c.ROOT, "experiments", "s2"), os.path.join(c.ROOT, "reference", "proto")]
    import build_counts
    build_counts.load_conv()
    return set(build_counts.TRAD_SIMP_ONLY)


def run(src, out, readings=c.readings, check_hash=True, simp=trad_simp_only):
    pf = os.path.join(src, "pairs.tsv")
    if check_hash and c.sha256_file(pf) != PAIRS_SHA:
        sys.exit(f"build_wikitypo: pairs.tsv sha256 {c.sha256_file(pf)[:16]}…, expected {PAIRS_SHA[:16]}…")
    man = json.load(open(os.path.join(src, "manifest.json"), encoding="utf-8"))
    awb = next(p for p in man["pages"] if p["title"] == AWB)
    wf = os.path.join(src, awb["file"])
    if check_hash and c.sha256_file(wf) != awb["sha256"]:
        sys.exit(f"build_wikitypo: {awb['file']} does not match manifest.json")
    pairs = read_pairs(pf)
    n = {"pairs": len(pairs)}
    pairs = [p for p in pairs if p[0] and p[1] and p[0] != p[1]]
    n["after_empty_or_same"] = len(pairs)
    rules = awb_jie_rules(open(wf, encoding="utf-8").read())
    n["jie_context_rules"] = len(rules)
    kept = [p for p in pairs if not dropped_by_rule(p, rules)]
    n["dropped_jie_rule_rows"] = len(pairs) - len(kept)
    seen, uniq = set(), []
    for w, r, _ in kept:
        if (w, r) not in seen:
            seen.add((w, r))
            uniq.append((w, r))
    n["distinct_pairs"] = len(uniq)
    rd = readings([x for p in uniq for x in p])
    ok = lambda w: rd[w][0] == "ok"
    both = [(w, r) for w, r in uniq if ok(w) and ok(r)]
    n["dropped_check_or_unreadable"] = len(uniq) - len(both)
    same = [(w, r) for w, r in both if rd[w][1] == rd[r][1]]
    n["dropped_different_reading"] = len(both) - len(same)
    n["same_reading_pairs"] = len(same)
    so = simp()
    same = [(w, r) for w, r in same if not any(ch in so for ch in r)]
    n["dropped_simplified_gold"] = n["same_reading_pairs"] - len(same)
    seen, uniq = set(), []
    for w, r in same:
        if r not in seen:
            seen.add(r)
            uniq.append((w, r))
    n["dropped_duplicate_gold"] = len(same) - len(uniq)
    same = uniq
    n["rows"] = len(same)
    text = "".join(f"|{r}|{rd[r][1]}\n" for _, r in same)
    c.write_lf(out, text)
    n["sha256"] = c.sha256_bytes(text.encode("utf-8"))
    n["pairs_sha256"] = PAIRS_SHA
    c.write_lf(out + ".manifest.json", json.dumps(n, ensure_ascii=False, indent=1) + "\n")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(CACHE, "wikitypos")); ap.add_argument("--out", default=os.path.join(CACHE, "wikitypo.txt"))
    a = ap.parse_args(argv)
    print(json.dumps(run(a.src, a.out), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
