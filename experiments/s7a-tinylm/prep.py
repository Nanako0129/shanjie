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
          --half (SJ-SR-7 arm R-half, contract sj-lm-7 section 2.7): keep only the sentences whose SHA-256(UTF-8) first byte is < 128. The
          sentence is the INPUT LINE as read: without its trailing newline, before any conversion, and for a path containing ".tsv" only its
          last tab-separated field (the unit column is dropped, the same rule as build_counts_text.lines). All Han runs of a kept line
          are written, all runs of a dropped line are dropped, so one line is never split between the halves. Deterministic, no seed.
          (The Han runs themselves are still taken from the whole line, as without --half.)
          --runs-only: write runs-<name>.txt and text-<name>.json only (no vocab.txt needed), for the new-corpus vocabulary below.
          --raw (CTX64): one training line per input line = the OpenCC-converted ORIGINAL text (punctuation, ASCII, digits, full-width forms
          kept; only leading / trailing whitespace stripped; empty lines dropped; a ".tsv" path uses its last field). The holdout guard drops
          the whole line when ANY of its Han runs (length >= 2, as in the default path) hits it. `encode --extra` counts every character
          of such a file into the vocabulary (only characters above U+FFFF are never in the vocabulary: they stay UNK). `--half` applies
          to the line as before. `runs --raw` does the same for the wiki part (build_counts.article_sentences, i.e. sentences split
          at 。！？； with their in-sentence punctuation). Both default off: every default output is byte-identical to before.
`encode --extra NAME ...` (SJ-SR-7, section 2.7): the vocabulary is counted over runs-wiki + runs-coll + each DIR/runs-<NAME>.txt (from
          `text --runs-only`), every file ONCE (never multiplied by its epoch weight, as the x5 colloquial pool is not), then each
          DIR/ids-<NAME>.npy is encoded with that vocabulary. Without --extra the vocab.txt, ids-wiki/coll.npy and encode-report.json are
          byte-identical to before. encode also writes DIR/ids-vocab.json (vocab and ids hashes, the extra set); it refuses to run over a
          vocab.txt made from a different extra set (a work dir without that file counts as "no extras") unless --force, and rejects the
          names wiki, coll and duplicates. train.py checks these hashes (check_vocab) before it trains.
"""
import argparse
import collections
import contextlib
import hashlib
import json
import multiprocessing as mp
import os
import re
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


GUARDED, NO_HAN, MARKUP_JUNK = "guard", "no_han", "markup"
WIKI_JUNK = re.compile(r"==|\||\[\[|\]\]|\{\{|\}\}|\b(?:Category|File|Image|Template|Wikipedia|Portal|Help|分類|分类|檔案|文件|圖像|模板):", re.I)
# wiki markup that article_text left in a sentence (raw path only); the namespace prefixes are what MARKUP's link rule leaves of [[Category:X]] or [[File:X]]
LIST_LEAD = re.compile(r"^[*#:;]+\s*")


def raw_line(text, guard=None):
    """CTX64: (the line to train on, why not). `text` is already OpenCC-converted. Stripped. Not kept: an empty line (None, None); a line with
    no Han run of length >= 2 (None, NO_HAN: ASCII debris and one-character fragments, the default path keeps only such runs too); a line
    where ANY Han run of length >= 2 hits the guard (None, GUARDED; the same test, on the same runs, as the default path applies per run)."""
    t = text.strip()
    if not t:
        return None, None
    han = [r for r in bc.HAN.findall(t) if len(r) >= 2]
    if not han:
        return None, NO_HAN
    if guard is not None and any(guard.any(bc.fold(r)) for r in han):
        return None, GUARDED
    return t, None


def raw_wiki_lines(raw, conv, guard=None):
    """One wiki article's raw lines (`runs --raw`): (line or None, why not) per bc.article_sentences sentence. A sentence holding markup
    residue (== | [[ ]] {{ }} or a namespace prefix such as Category:) that the guard let through is dropped (MARKUP_JUNK); a leading list mark (* # : ;) and the blanks after it are removed."""
    for sent in bc.article_sentences(raw, conv):
        t, why = raw_line(LIST_LEAD.sub("", sent.strip()), guard)   # the guard first, so its count does not depend on the markup filter
        yield (None, MARKUP_JUNK) if t is not None and WIKI_JUNK.search(t) else (t, why)


_CONV = _GUARD = None


def _init(guard=None):
    global _CONV, _GUARD
    _CONV, _GUARD = bc.load_conv(), guard


def _work(args):
    """args = (kind, items[, raw]). Returns (the kept lines joined by "\\n", their character counts, how many, guard-skipped, raw lines
    without a Han run of length >= 2, raw wiki sentences with markup residue)."""
    kind, items, *flag = args
    runs, skipped, no_han, markup = [], 0, 0, 0
    if flag and flag[0]:    # `runs --raw`: wiki sentences, and the colloquial / synth lines as bct.lines gave them (the default path's unit), whole
        for x in items:
            for line, why in (raw_wiki_lines(x, _CONV, _GUARD) if kind == "wiki" else [raw_line(bc.convert(x, *_CONV), _GUARD)]):
                skipped += why == GUARDED
                no_han += why == NO_HAN
                markup += why == MARKUP_JUNK
                if line is not None:
                    runs.append(line)
    else:
        f = wiki_runs if kind == "wiki" else text_runs
        for x in items:
            for r in f(x, _CONV):
                if _GUARD is not None and _GUARD.any(bc.fold(r)):   # --holdout-guard: held-out sentence inside the run, not part of the training text
                    skipped += 1
                    continue
                runs.append(r)
    text = "\n".join(runs)
    return text, collections.Counter(text.replace("\n", "")), len(runs), skipped, no_han, markup


def cmd_runs(a):
    paths = {"wiki": bc.DUMP, "colloquial-train.txt": a.coll, "synth.txt": a.synth,
             "TWVariants.txt": os.path.join(bc.SRC, "opencc", "TWVariants.txt"), "STCharacters.txt": os.path.join(bc.SRC, "opencc", "STCharacters.txt")}
    hashes = check_inputs(paths)
    guard = bc.guard_matcher(a.holdout_guard)
    os.makedirs(a.work, exist_ok=True)
    skipped_runs = 0
    total, report = collections.Counter(), {"input_sha256": hashes, "articles": a.articles}
    if a.raw:
        report["raw"] = True
    with mp.Pool(a.procs, initializer=_init, initargs=(guard,)) as pool:
        # wiki: imap (ordered), so the training text is the same on every run
        n_runs = n_chars = skip_src = nh_src = mk_src = 0
        with open(os.path.join(a.work, "runs-wiki.txt"), "w", encoding="utf-8", newline="\n") as out:
            for i, (text, cnt, n, sk, nh, mk) in enumerate(pool.imap(_work, (("wiki", b, a.raw) for b in bc.batches(bc.articles(a.articles)))), 1):
                if n:
                    out.write(text + "\n")
                total.update(cnt); n_runs += n; n_chars += sum(cnt.values()); skipped_runs += sk; skip_src += sk; nh_src += nh; mk_src += mk
                if i % 50 == 0:
                    print(f"~{i * 200} articles, {n_runs} runs, {n_chars} chars", flush=True)
        report["wiki"] = {"runs": n_runs, "chars": n_chars}
        if a.raw:     # raw: "runs" are lines; the counts of each source apart (the Han-run counts of the default path stay as they were)
            report["wiki"].update(guard_skipped_lines=skip_src, no_han_lines=nh_src, markup_lines=mk_src)
        n_runs = n_chars = 0
        with open(os.path.join(a.work, "runs-coll.txt"), "w", encoding="utf-8", newline="\n") as out:
            for name, p in (("colloquial-train.txt", a.coll), ("synth.txt", a.synth)):
                r = c = skip_src = nh_src = 0
                for text, cnt, n, sk, nh, _mk in pool.imap(_work, (("text", b, a.raw) for b in bc.batches(bct.lines(p), 2000))):
                    if n:
                        out.write(text + "\n")
                    total.update(cnt); r += n; c += sum(cnt.values()); skipped_runs += sk; skip_src += sk; nh_src += nh
                report[name] = {"runs": r, "chars": c}
                if a.raw:
                    report[name].update(guard_skipped_lines=skip_src, no_han_lines=nh_src)
                n_runs += r; n_chars += c
        report["coll"] = {"runs": n_runs, "chars": n_chars}
    if guard is not None:
        report["holdout_guard_skipped_runs" if not a.raw else "holdout_guard_skipped_lines"] = skipped_runs
    report["train_chars_once"] = report["wiki"]["chars"] + report["coll"]["chars"]
    report["train_chars_weighted"] = WIKI_WEIGHT * report["wiki"]["chars"] + COLL_WEIGHT * report["coll"]["chars"]
    json.dump({"report": report, "charcount": dict(total)}, open(os.path.join(a.work, "charcount.json"), "w", encoding="utf-8"), ensure_ascii=False)
    print(json.dumps(report, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ vocabulary and ids
def make_vocab(charcount, min_count=MIN_COUNT):
    """Characters seen >= min_count times (counted once over the training text, before the x5), most frequent first, ties by code point.
    Characters at or above U+FFFF never enter (make_lut is a BMP table and they encode as UNK); Han runs have none, raw text can."""
    return [c for c, n in sorted(charcount.items(), key=lambda x: (-x[1], x[0])) if n >= min_count and ord(c) < 0xFFFF]


def merge_vocab(normal, base):
    """CTX64-B `--base-vocab`: the base file's characters in its order (kept even if rare), then the characters of the normal vocabulary
    that are not in the base, in the normal order. Returns (merged, number appended)."""
    if len(set(base)) != len(base) or any(len(c) != 1 or ord(c) >= 0xFFFF for c in base):
        raise ValueError("the base vocabulary has a duplicate, a non-single character or one at or above U+FFFF")
    have = set(base)
    new = [c for c in normal if c not in have]
    return list(base) + new, len(new)


def extend_emb(emb, n_new, seed):
    """CTX64-B `train.py --init`: n_new rows for appended characters = mean of the existing character rows (ids >= 3) + Gaussian noise,
    std 0.02 x the std of the whole matrix, from default_rng(seed). Returns the (V + n_new, d) matrix; the first V rows are `emb` itself."""
    rng = np.random.default_rng(seed)
    rows = emb[3:].mean(axis=0) + rng.normal(0.0, 0.02 * emb.std(), (n_new, emb.shape[1])).astype(emb.dtype)
    return np.concatenate([emb, rows.astype(emb.dtype)], axis=0)


def check_prefix(old_chars, chars):
    """The vocabulary of an --init npz must be a prefix of the work dir's vocab.txt; raises ValueError otherwise."""
    if list(chars[:len(old_chars)]) != list(old_chars):
        raise ValueError(f"the --init vocabulary ({len(old_chars)} characters) is not a prefix of vocab.txt ({len(chars)} characters)")


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


VOCAB_RECORD = "ids-vocab.json"


def recorded_extra(work):
    """The extra set the vocab.txt in `work` was made from: the list in ids-vocab.json; [] when only vocab.txt exists (made before the record
    existed, so without extras); None when there is no vocab.txt at all."""
    rec = os.path.join(work, VOCAB_RECORD)
    if os.path.isfile(rec):
        return json.load(open(rec, encoding="utf-8"))["extra"]
    return [] if os.path.isfile(os.path.join(work, "vocab.txt")) else None


def check_vocab(work, extra_paths):
    """train.py's guard: every ids file it will read was encoded with the vocab.txt in `work`. Stops (SystemExit) otherwise.
    ids-wiki/coll: the hashes ids-vocab.json recorded at encode time (a work dir made before that file existed is accepted only
    without extras). Each extra ids-<name>.npy: the vocab_sha256 and ids_sha256 in its text-<name>.json (prep.py encode --extra / text)."""
    vh = sha256_file(os.path.join(work, "vocab.txt"))
    rec = os.path.join(work, VOCAB_RECORD)
    if os.path.isfile(rec):
        r = json.load(open(rec, encoding="utf-8"))
        if r["vocab_sha256"] != vh:
            die(f"{work}/vocab.txt changed after prep.py encode (hash {vh}, recorded {r['vocab_sha256']}); run encode again")
        for n in ("wiki", "coll"):
            if sha256_file(os.path.join(work, f"ids-{n}.npy")) != r["ids_sha256"][n]:
                die(f"{work}/ids-{n}.npy is not the file prep.py encode wrote with this vocab.txt; run encode again")
    elif extra_paths:
        die(f"{rec} is missing: ids-wiki/coll cannot be tied to vocab.txt; run prep.py encode --extra ... in this work dir")
    for p in extra_paths:
        d, base = os.path.split(p)
        if not (base.startswith("ids-") and base.endswith(".npy")):
            die(f"--extra {p}: expected a file named ids-<name>.npy")
        tj = os.path.join(d, f"text-{base[4:-4]}.json")
        if not os.path.isfile(tj):
            die(f"--extra {p}: {tj} is missing (made by prep.py text / encode --extra), cannot check its vocabulary")
        t = json.load(open(tj, encoding="utf-8"))
        if t.get("vocab_sha256") != vh:
            die(f"--extra {p} was encoded with a different vocab.txt (recorded {t.get('vocab_sha256')}, {work}/vocab.txt is {vh})")
        if t.get("ids_sha256") != sha256_file(p):
            die(f"--extra {p} does not match the ids_sha256 in {tj}")


def cmd_encode(a):
    names = list(a.extra)
    for n in names:
        if n in ("wiki", "coll"):
            die(f"--extra {n}: would overwrite the S7a training ids")
        if not os.path.isfile(os.path.join(a.work, f"runs-{n}.txt")):
            die(f"--extra {n}: {a.work}/runs-{n}.txt is missing (prep.py text --runs-only --name {n})")
    if len(set(names)) != len(names):
        die(f"--extra has a duplicate name: {names}")
    prev = recorded_extra(a.work)
    if prev is not None and sorted(prev) != sorted(names) and not a.force:
        die(f"{a.work}/vocab.txt was made with extras {sorted(prev)}, this run has {sorted(names)}: it would overwrite that vocabulary and "
            f"ids (another arm's files); use a separate work dir, or --force")
    if os.path.isfile(os.path.join(a.work, VOCAB_RECORD)):
        os.remove(os.path.join(a.work, VOCAB_RECORD))     # re-written last: a crash in between leaves no record that looks valid
    cc = json.load(open(os.path.join(a.work, "charcount.json"), encoding="utf-8"))
    count = collections.Counter(cc["charcount"])
    for name in a.extra:                                   # each extra file once, not by its epoch weight
        with open(os.path.join(a.work, f"runs-{name}.txt"), encoding="utf-8", newline="\n") as f:
            for block in iter(lambda: f.read(1 << 24), ""):    # C-level counts over 16M-char blocks, the same counts as line by line
                count.update(block.replace("\n", ""))
    chars = make_vocab(count, a.min_count)
    base_rec = None
    if getattr(a, "base_vocab", None):
        try:
            chars, n_new = merge_vocab(chars, open(a.base_vocab, encoding="utf-8", newline="\n").read().split("\n")[:-1])
        except ValueError as e:
            die(f"--base-vocab {a.base_vocab}: {e}")
        base_rec = {"sha256": sha256_file(a.base_vocab), "size": len(chars) - n_new, "appended": n_new}
    open(os.path.join(a.work, "vocab.txt"), "w", encoding="utf-8", newline="\n").write("\n".join(chars) + "\n")
    lut = make_lut(chars)
    rep = {"vocab_size": len(chars) + 3, "min_count": a.min_count, **({"base_vocab": base_rec} if base_rec else {})}
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
    json.dump({"vocab_sha256": sha256_file(os.path.join(a.work, "vocab.txt")), "extra": sorted(names), **({"base_vocab": base_rec} if base_rec else {}),
               "ids_sha256": {n: sha256_file(os.path.join(a.work, f"ids-{n}.npy")) for n in ("wiki", "coll")}},
              open(os.path.join(a.work, VOCAB_RECORD), "w"), indent=1)
    print(json.dumps(rep, indent=1))


def half_keep(sentence):
    """The R-half rule: SHA-256 of the sentence's UTF-8, first byte < 128 (about half of all sentences)."""
    return hashlib.sha256(sentence.encode("utf-8")).digest()[0] < 128


TEXT_KEYS = ["input", "input_sha256", "lines", "tokens", "runs", "unk_fraction", "vocab_sha256", "runs_sha256", "ids_sha256",
             "holdout_guard_skipped_runs", "half_dropped_runs", "raw", "raw_no_han_lines"]   # text-<name>.json key order; the first ten are the pre-SJ-SR-7 order


def dump_text_report(path, rep):
    rep = {k: rep[k] for k in TEXT_KEYS if k in rep}
    json.dump(rep, open(path, "w"), indent=1)
    return rep


def _text_batch(lines, conv, guard, half, tsv, raw=False):
    """write_runs on one batch of input lines (trailing newline removed): (the kept runs, each + "\\n"; lines; guard-skipped runs; half-dropped runs;
    raw lines without a Han run of length >= 2). raw: the kept "runs" are the converted lines (raw_line); the skipped / dropped counts
    are then lines, and a blank line is skipped before the half judgement (it counts in neither)."""
    out, skipped, dropped, no_han = [], 0, 0, 0
    for line in lines:
        sent = line.split("\t")[-1] if tsv else line     # the sentence: the last field of a .tsv line
        if raw and not sent.strip():
            continue
        if half and not half_keep(sent):
            dropped += 1 if raw else sum(1 for _ in text_runs(line, conv))
            continue
        if raw:
            t, why = raw_line(bc.convert(sent, *conv), guard)
            skipped += why == GUARDED
            no_han += why == NO_HAN
            if t is not None:
                out.append(t + "\n")
            continue
        for run in text_runs(line, conv):
            if guard is not None and guard.any(bc.fold(run)):
                skipped += 1
                continue
            out.append(run + "\n")
    return "".join(out), len(lines), skipped, dropped, no_han


def _text_work(args):
    return _text_batch(args[0], _CONV, _GUARD, *args[1:])


def write_runs(src, work, name, conv, guard=None, half=False, procs=1, raw=False):
    """src -> DIR/runs-<name>.txt (one Han run per line) and DIR/text-<name>.json. Returns the report dict.
    half: keep a whole input line (all its runs) iff half_keep(its sentence); the sentence is the line without the trailing newline,
    before conversion, and for a ".tsv" path only the last tab-separated field. One line is never split.
    procs > 1: batches of 2,000 lines in a Pool whose workers load bc.load_conv() (as `runs` does; `conv` is then unused); imap keeps the
    order, so the files are the same as with procs=1."""
    if name in ("wiki", "coll"):
        die(f"--name {name} would overwrite the S7a training ids")
    runs_path = os.path.join(work, f"runs-{name}.txt")
    tsv = ".tsv" in src
    n_lines = skipped = dropped = no_han = 0
    with open(src, encoding="utf-8", newline="\n") as f, open(runs_path, "w", encoding="utf-8", newline="\n") as out, \
            (mp.Pool(procs, initializer=_init, initargs=(guard,)) if procs > 1 else contextlib.nullcontext()) as pool:
        jobs = ((b, half, tsv, raw) for b in bc.batches((line.rstrip("\n") for line in f), 2000))
        for text, n, sk, dr, nh in (pool.imap(_text_work, jobs) if pool else (_text_batch(b, conv, guard, h, t, r) for b, h, t, r in jobs)):
            out.write(text)
            n_lines += n; skipped += sk; dropped += dr; no_han += nh
    rep = {"input": src, "input_sha256": sha256_file(src), "lines": n_lines, "runs_sha256": sha256_file(runs_path)}
    if guard is not None:
        rep["holdout_guard_skipped_runs"] = skipped
    if half:
        rep["half_dropped_runs"] = dropped    # Han runs of the dropped lines (raw: the dropped lines)
    if raw:
        rep["raw"] = True
        rep["raw_no_han_lines"] = no_han
    return dump_text_report(os.path.join(work, f"text-{name}.json"), rep)


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
    return dump_text_report(rj, rep)


def encode_extra(src, work, name, conv, guard=None, half=False, procs=1):
    """`text`: src -> DIR/runs-<name>.txt -> DIR/ids-<name>.npy with the existing DIR/vocab.txt. Returns the report dict. Never raw: raw text
    needs its own vocabulary (`text --raw --runs-only`, then `encode --extra`), the existing one would turn its punctuation into UNK."""
    write_runs(src, work, name, conv, guard, half, procs)
    return write_ids(work, name, make_lut(load_vocab(work)))


def cmd_text(a):
    if a.raw and not a.runs_only:
        die("--raw needs --runs-only: without it the lines would be encoded with the existing vocab.txt (Han only), so punctuation and ASCII "
            "would silently become UNK; write runs-<name>.txt with --runs-only and let `encode --extra <name>` build the vocabulary and ids")
    conv, guard = bc.load_conv(), bc.guard_matcher(a.holdout_guard)
    print(json.dumps(write_runs(a.input, a.work, a.name, conv, guard, a.half, a.procs, a.raw) if a.runs_only
                     else encode_extra(a.input, a.work, a.name, conv, guard, a.half, a.procs), indent=1))


def load_vocab(work):
    return open(os.path.join(work, "vocab.txt"), encoding="utf-8", newline="\n").read().split("\n")[:-1]


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("runs")
    r.add_argument("--work", required=True); r.add_argument("--coll", required=True); r.add_argument("--synth", required=True)
    r.add_argument("--holdout-guard", nargs="*", default=None, metavar="ROWS"); r.add_argument("--raw", action="store_true", help="CTX64: wiki (bc.article_sentences), colloquial and synth lines are kept whole (punctuation, ASCII, digits) instead of as Han runs"); r.add_argument("--articles", type=int, default=200_000); r.add_argument("--procs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    e = sub.add_parser("encode")
    e.add_argument("--work", required=True); e.add_argument("--min-count", type=int, default=MIN_COUNT)
    e.add_argument("--base-vocab", metavar="FILE", help="CTX64-B: a vocab.txt whose characters come first in its order (kept even if rare); the characters the normal rule admits and FILE lacks follow")
    e.add_argument("--force", action="store_true", help="encode over a vocab.txt made from a different --extra set (overwrites that arm's vocabulary and ids)")
    e.add_argument("--extra", nargs="*", default=[], metavar="NAME", help="NAMEs of DIR/runs-<NAME>.txt (prep.py text --runs-only) counted into the vocabulary once each and encoded")
    t = sub.add_parser("text")
    t.add_argument("--work", required=True); t.add_argument("--in", dest="input", required=True); t.add_argument("--name", required=True)
    t.add_argument("--half", action="store_true", help="keep only runs whose SHA-256 first byte is < 128 (SJ-SR-7 arm R-half)")
    t.add_argument("--runs-only", action="store_true", help="write runs-<name>.txt only; encode later with `encode --extra`")
    t.add_argument("--raw", action="store_true", help="CTX64: one training line per input line = its converted original text (punctuation, ASCII, digits kept), not its Han runs")
    t.add_argument("--procs", type=int, default=1, help="worker processes for the conversion (the output is the same for any value)")
    t.add_argument("--holdout-guard", nargs="*", default=None, metavar="ROWS", help="default off: runs containing a held-out sentence are not written (docs/contracts/holdout-refresh.md section 8)")
    a = ap.parse_args(argv)
    {"runs": cmd_runs, "encode": cmd_encode, "text": cmd_text}[a.cmd](a)


if __name__ == "__main__":
    main()
