"""S7f: the real-chat mix (contract 2.1) from the three cleaned sources.

  python build_chat.py --out DIR archive_tw=DIR/archive_tw.txt cofacts=DIR/cofacts.txt wikitalk=DIR/wikitalk.txt
      -> DIR/chat-real.txt, DIR/chat-real.manifest.json

Sentences repeated across sources are kept once (the first source named wins). Each source is capped at 8,000,000 characters and
the total at 20,000,000: when the capped sources sum to more, sources below the common level are taken whole and the others are cut
to the same level (clean.water_fill); nothing is back-filled from another source. Each source is sampled with clean.sample_to_cap
(fixed seed) and written in its input order, sources in the order given. Exit 3 when the total is below 5,000,000 (contract 5 stop).
"""
import argparse
import os
import sys

import clean

PER_SOURCE, TOTAL, MINIMUM = 8_000_000, 20_000_000, 5_000_000


def build(sources, seed=clean.SEED, per_source=PER_SOURCE, total=TOTAL):
    """sources: [(name, lines)]. Returns ([(name, chosen lines)], per-source report)."""
    seen, deduped, report = set(), [], {}
    for name, lines in sources:
        keep = [s for s in lines if s not in seen]
        seen.update(keep)
        deduped.append((name, keep))
        report[name] = {"rows_in": len(lines), "chars_in": sum(map(len, lines)), "cross_source_duplicates": len(lines) - len(keep)}
    sizes = [min(sum(map(len, keep)), per_source) for _, keep in deduped]
    quotas = clean.water_fill(sizes, total)
    out = []
    for (name, keep), q in zip(deduped, quotas):
        got = clean.sample_to_cap(keep, q, seed)
        out.append((name, got))
        report[name].update(quota=q, rows=len(got), chars=sum(map(len, got)), sha256=clean.sha256_lines(got))
    return out, report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True); ap.add_argument("--seed", type=int, default=clean.SEED)
    ap.add_argument("sources", nargs="+", metavar="NAME=PATH")
    a = ap.parse_args(argv)
    named = [s.split("=", 1) for s in a.sources]
    if any(len(x) != 2 for x in named) or len({n for n, _ in named}) != len(named):
        sys.exit("build_chat: give each source as a distinct NAME=PATH")
    out, report = build([(n, clean.read_lines(p)) for n, p in named], a.seed)
    for n, p in named:
        report[n]["input_sha256"] = clean.sha256_file(p)
    lines = [s for _, got in out for s in got]
    chars = sum(map(len, lines))
    if chars < MINIMUM:
        print(f"STOP (contract 5): the sources give {chars:,} characters, below {MINIMUM:,}; nothing written", file=sys.stderr)
        sys.exit(3)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "chat-real.txt")
    clean.write_lines(path, lines)
    clean.write_manifest(os.path.join(a.out, "chat-real.manifest.json"),
                         {"seed": a.seed, "per_source_cap": PER_SOURCE, "total_cap": TOTAL, "rows": len(lines), "chars": chars,
                          "sha256": clean.sha256_file(path), "sources": report})
    print(f"chat-real: {len(lines)} rows, {chars:,} chars; " + ", ".join(f"{n} {r['chars']:,}" for n, r in report.items()))


if __name__ == "__main__":
    main()
