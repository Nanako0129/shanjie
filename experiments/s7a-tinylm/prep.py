"""S7a data preparation (contract section 2.2): the training text, produced with the SAME functions as the model-v5 n-gram counts.

  python prep.py runs   --work DIR --coll colloquial-train.txt --synth synth.txt [--articles 200000] [--procs N]
  python prep.py encode --work DIR [--min-count 5]

`runs`  : checks the input SHA-256 values, then writes DIR/runs-wiki.txt and DIR/runs-coll.txt (one Han run per line) and DIR/charcount.json.
          Wiki: build_counts.articles(N), the count_batch order (entity unescape, TEMPLATE x3, MARKUP, SENT, convert, HAN, length >= 2).
          Colloquial: build_counts_text.lines() of colloquial-train.txt then synth.txt, convert, HAN, length >= 2.
`encode`: vocabulary (characters seen >= min-count times in the training text; other characters are UNK), DIR/vocab.txt, and
          DIR/ids-{wiki,coll}.npy: uint16, every run preceded by BOS.
`text`  : (S7f) python prep.py text --work DIR --in FILE --name chat [--half] [--runs-only]
          one more text file through the colloquial path (text_runs: convert, HAN, length >= 2) into DIR/runs-<name>.txt and
          DIR/ids-<name>.npy with the EXISTING DIR/vocab.txt (no vocabulary rebuild); DIR/text-<name>.json has the counts and hashes.
          --half (SJ-SR-7 arm R-half, contract sj-lm-7 section 2.7): keep only the units whose SHA-256(UTF-8) first byte is < 128. The unit is
          what this command writes per line of runs-<name>.txt: one Han run (text_runs output, after convert), NOT the input line; when an
          input line holds several Han runs they are judged one by one. Deterministic, no seed.
          --runs-only: write runs-<name>.txt and text-<name>.json only (no vocab.txt needed), for the new-corpus vocabulary below.
`encode --extra NAME ...` (SJ-SR-7, section 2.7): the vocabulary is counted over runs-wiki + runs-coll + each DIR/runs-<NAME>.txt (from
          `text --runs-only`), every file ONCE (never multiplied by its epoch weight, as the x5 colloquial pool is not), then each
          DIR/ids-<NAME>.npy is encoded with that vocabulary. Without --extra the output is byte-identical to before.
"""
import argparse
import collections
import hashlib
import json
import multiprocessing as mp
import os
import sys

import numpy as np

import tinylm
from tinylm import BOS, UNK

import build_counts as bc  # noqa: E402  experiments/s2 (on sys.path via tinylm)
import build_counts_text as bct  # noqa: E402

# experiments/model-v5/README.md table: (first 8 hex, last 4 hex). OpenCC files are additionally pinned in full by build_counts._checked.
EXPECTED = {"wiki": ("5db9052e", "7e2f"), "colloquial-train.txt": ("3e833d06", "b4db"), "synth.txt": ("bec7a6a2", "99f1"),
            "TWVariants.txt": ("245b94eb", "cb86"), "STCharacters.txt": ("a0ca1601", "582b")}
MIN_COUNT = 5
WIKI_WEIGHT, COLL_WEIGHT = 1, 5    # per epoch, same as the n-gram counts (wiki x1, colloquial x5)


def die(msg):
    sys.exit(f"prep: {msg}")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    return h.hexdigest()


def match_hash(name, hexd):
    head, tail = EXPECTED[name]
    return hexd.startswith(head) and hexd.endswith(tail)


def check_inputs(paths):
    """paths: {expected-name: file}. Returns {name: sha256}; stops (SystemExit) on the first mismatch."""
    got = {}
    for name, p in paths.items():
        if not os.path.isfile(p):
            die(f"INPUT MISSING {name}: {p}")
        got[name] = sha256_file(p)
        if not match_hash(name, got[name]):
            die(f"INPUT HASH MISMATCH {name}: {got[name]} (expected {EXPECTED[name][0]}...{EXPECTED[name][1]})")
    return got


# ------------------------------------------------------------------ the text functions (tests compare them with build_counts)
def wiki_runs(raw, conv):
    """One wiki article's Han runs, in count_batch's order (the --mw branch is not used: model-v5 counts were made without it).
    The pipeline is build_counts.article_runs, the one copy the counting scripts and holdout_guard share."""
    for run in bc.article_runs(raw, conv):
        if len(run) >= 2:
            yield run


def text_runs(line, conv):
    """One colloquial line's Han runs (build_counts_text.main's loop)."""
    for run in bc.text_runs(line, conv):
        if len(run) >= 2:
            yield run


_CONV = _GUARD = None


def _init(guard=None):
    global _CONV, _GUARD
    _CONV, _GUARD = bc.load_conv(), guard


def _work(args):
    kind, items = args
    f = wiki_runs if kind == "wiki" else text_runs
    runs, skipped = [], 0
    for x in items:
        for r in f(x, _CONV):
            if _GUARD is not None and _GUARD.any(bc.fold(r)):   # --holdout-guard: held-out sentence inside the run, not part of the training text
                skipped += 1
                continue
            runs.append(r)
    text = "\n".join(runs)
    return text, collections.Counter(text.replace("\n", "")), len(runs), skipped


def cmd_runs(a):
    paths = {"wiki": bc.DUMP, "colloquial-train.txt": a.coll, "synth.txt": a.synth,
             "TWVariants.txt": os.path.join(bc.SRC, "opencc", "TWVariants.txt"), "STCharacters.txt": os.path.join(bc.SRC, "opencc", "STCharacters.txt")}
    hashes = check_inputs(paths)
    guard = bc.guard_matcher(a.holdout_guard)
    os.makedirs(a.work, exist_ok=True)
    skipped_runs = 0
    total, report = collections.Counter(), {"input_sha256": hashes, "articles": a.articles}
    with mp.Pool(a.procs, initializer=_init, initargs=(guard,)) as pool:
        # wiki: imap (ordered), so the training text is the same on every run
        n_runs = n_chars = 0
        with open(os.path.join(a.work, "runs-wiki.txt"), "w", encoding="utf-8", newline="\n") as out:
            for i, (text, cnt, n, sk) in enumerate(pool.imap(_work, (("wiki", b) for b in bc.batches(bc.articles(a.articles)))), 1):
                if n:
                    out.write(text + "\n")
                total.update(cnt); n_runs += n; n_chars += sum(cnt.values()); skipped_runs += sk
                if i % 50 == 0:
                    print(f"~{i * 200} articles, {n_runs} runs, {n_chars} chars", flush=True)
        report["wiki"] = {"runs": n_runs, "chars": n_chars}
        n_runs = n_chars = 0
        with open(os.path.join(a.work, "runs-coll.txt"), "w", encoding="utf-8", newline="\n") as out:
            for name, p in (("colloquial-train.txt", a.coll), ("synth.txt", a.synth)):
                r = c = 0
                for text, cnt, n, sk in pool.imap(_work, (("text", b) for b in bc.batches(bct.lines(p), 2000))):
                    if n:
                        out.write(text + "\n")
                    total.update(cnt); r += n; c += sum(cnt.values()); skipped_runs += sk
                report[name] = {"runs": r, "chars": c}
                n_runs += r; n_chars += c
        report["coll"] = {"runs": n_runs, "chars": n_chars}
    if guard is not None:
        report["holdout_guard_skipped_runs"] = skipped_runs
    report["train_chars_once"] = report["wiki"]["chars"] + report["coll"]["chars"]
    report["train_chars_weighted"] = WIKI_WEIGHT * report["wiki"]["chars"] + COLL_WEIGHT * report["coll"]["chars"]
    json.dump({"report": report, "charcount": dict(total)}, open(os.path.join(a.work, "charcount.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(report, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ vocabulary and ids
def make_vocab(charcount, min_count=MIN_COUNT):
    """Characters seen >= min_count times (counted once over the training text, before the x5), most frequent first, ties by code point."""
    return [c for c, n in sorted(charcount.items(), key=lambda x: (-x[1], x[0])) if n >= min_count]


def encode_text(text, lut):
    """text: runs joined by "\\n", NO trailing newline. Returns uint16 ids with a BOS in front of every run (the newline becomes BOS)."""
    cp = np.frombuffer(("\n" + text).encode("utf-32-le"), dtype="<u4")
    return lut[np.minimum(cp, len(lut) - 1)]


def make_lut(vocab_chars):
    assert all(ord(c) < 0xFFFF for c in vocab_chars) and len(vocab_chars) + 3 < 65535
    lut = np.full(0x10000, UNK, dtype=np.uint16)       # index 0xFFFF stands for "everything above" and stays UNK
    lut[10] = BOS
    for i, c in enumerate(vocab_chars):
        lut[ord(c)] = i + 3
    return lut


def encode_file(path, lut, block=1 << 26):
    parts = []
    with open(path, encoding="utf-8", newline="\n") as f:
        while True:
            lines = f.readlines(block)
            if not lines:
                break
            parts.append(encode_text("".join(lines)[:-1], lut))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.uint16)


def cmd_encode(a):
    cc = json.load(open(os.path.join(a.work, "charcount.json"), encoding="utf-8"))
    count = collections.Counter(cc["charcount"])
    for name in a.extra:                                   # each extra file once, not by its epoch weight
        with open(os.path.join(a.work, f"runs-{name}.txt"), encoding="utf-8", newline="\n") as f:
            for line in f:
                count.update(line.rstrip("\n"))
    chars = make_vocab(count, a.min_count)
    open(os.path.join(a.work, "vocab.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(chars) + "\n")
    lut = make_lut(chars)
    rep = {"vocab_size": len(chars) + 3, "min_count": a.min_count}
    for name in ("wiki", "coll"):
        ids = encode_file(os.path.join(a.work, f"runs-{name}.txt"), lut)
        np.save(os.path.join(a.work, f"ids-{name}.npy"), ids)
        rep[name] = {"tokens": int(len(ids)), "runs": int((ids == BOS).sum()), "unk_fraction": float((ids == UNK).sum() / max(1, len(ids) - (ids == BOS).sum()))}
    if a.extra:
        rep["extra"] = {}
        for name in a.extra:
            r = write_ids(a.work, name, lut)
            rep["extra"][name] = {k: r[k] for k in ("tokens", "runs", "unk_fraction")}
    json.dump(rep, open(os.path.join(a.work, "encode-report.json"), "w"), indent=1)
    print(json.dumps(rep, indent=1))


def half_keep(unit):
    """The R-half rule: SHA-256 of the UTF-8 unit, first byte < 128 (about half of all units)."""
    return hashlib.sha256(unit.encode("utf-8")).digest()[0] < 128


def write_runs(src, work, name, conv, guard=None, half=False):
    """src -> DIR/runs-<name>.txt (one Han run per line; --half keeps the half_keep ones) and DIR/text-<name>.json. Returns the report dict."""
    if name in ("wiki", "coll"):
        die(f"--name {name} would overwrite the S7a training ids")
    runs_path = os.path.join(work, f"runs-{name}.txt")
    n_lines = skipped = dropped = 0
    with open(src, encoding="utf-8", newline="\n") as f, open(runs_path, "w", encoding="utf-8", newline="\n") as out:
        for line in f:
            n_lines += 1
            for run in text_runs(line.rstrip("\n"), conv):
                if guard is not None and guard.any(bc.fold(run)):
                    skipped += 1
                    continue
                if half and not half_keep(run):
                    dropped += 1
                    continue
                out.write(run + "\n")
    rep = {"input": src, "input_sha256": sha256_file(src), "lines": n_lines, "runs_sha256": sha256_file(runs_path)}
    if guard is not None:
        rep["holdout_guard_skipped_runs"] = skipped
    if half:
        rep["half_dropped_runs"] = dropped
    json.dump(rep, open(os.path.join(work, f"text-{name}.json"), "w"), indent=1)
    return rep


def write_ids(work, name, lut):
    """DIR/runs-<name>.txt -> DIR/ids-<name>.npy with DIR/vocab.txt; adds the token counts and hashes to DIR/text-<name>.json. Returns that report."""
    runs_path, ids_path = os.path.join(work, f"runs-{name}.txt"), os.path.join(work, f"ids-{name}.npy")
    ids = encode_file(runs_path, lut)
    np.save(ids_path, ids)
    rj = os.path.join(work, f"text-{name}.json")
    rep = json.load(open(rj)) if os.path.isfile(rj) else {}
    rep.update({"tokens": int(len(ids)), "runs": int((ids == BOS).sum()),
                "unk_fraction": float((ids == UNK).sum() / max(1, len(ids) - (ids == BOS).sum())),
                "vocab_sha256": sha256_file(os.path.join(work, "vocab.txt")), "runs_sha256": sha256_file(runs_path), "ids_sha256": sha256_file(ids_path)})
    json.dump(rep, open(rj, "w"), indent=1)
    return rep


def encode_extra(src, work, name, conv, guard=None, half=False):
    """`text`: src -> DIR/runs-<name>.txt -> DIR/ids-<name>.npy with the existing DIR/vocab.txt. Returns the report dict."""
    write_runs(src, work, name, conv, guard, half)
    return write_ids(work, name, make_lut(load_vocab(work)))


def cmd_text(a):
    conv, guard = bc.load_conv(), bc.guard_matcher(a.holdout_guard)
    print(json.dumps(write_runs(a.input, a.work, a.name, conv, guard, a.half) if a.runs_only
                     else encode_extra(a.input, a.work, a.name, conv, guard, a.half), indent=1))


def load_vocab(work):
    return open(os.path.join(work, "vocab.txt"), encoding="utf-8", newline="\n").read().split("\n")[:-1]


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("runs")
    r.add_argument("--work", required=True); r.add_argument("--coll", required=True); r.add_argument("--synth", required=True)
    r.add_argument("--holdout-guard", nargs="*", default=None, metavar="ROWS"); r.add_argument("--articles", type=int, default=200_000); r.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    e = sub.add_parser("encode")
    e.add_argument("--work", required=True); e.add_argument("--min-count", type=int, default=MIN_COUNT)
    e.add_argument("--extra", nargs="*", default=[], metavar="NAME", help="NAMEs of DIR/runs-<NAME>.txt (prep.py text --runs-only) counted into the vocabulary once each and encoded")
    t = sub.add_parser("text")
    t.add_argument("--work", required=True); t.add_argument("--in", dest="input", required=True); t.add_argument("--name", required=True)
    t.add_argument("--half", action="store_true", help="keep only runs whose SHA-256 first byte is < 128 (SJ-SR-7 arm R-half)")
    t.add_argument("--runs-only", action="store_true", help="write runs-<name>.txt only; encode later with `encode --extra`")
    t.add_argument("--holdout-guard", nargs="*", default=None, metavar="ROWS", help="default off: runs containing a held-out sentence are not written (docs/contracts/holdout-refresh.md section 8)")
    a = ap.parse_args(argv)
    {"runs": cmd_runs, "encode": cmd_encode, "text": cmd_text}[a.cmd](a)


if __name__ == "__main__":
    main()
