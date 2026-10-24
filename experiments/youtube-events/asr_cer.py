"""Contract docs/contracts/youtube-events.md section 3 (C): CER of each ASR system against the creator subtitles. Runs on the Mac.

Usage:  python3 asr_cer.py PICKS.json SUB_DIR ASR_DIR [--sources DIR]
  PICKS.json              list of {"id", "lang", "start" (seconds), ...}; the segment is [start, start+600)
  SUB_DIR/<id>.<lang>.vtt reference; a cue counts when its start time is inside the segment
  ASR_DIR/<model>/<id>.json   from asr_run.py (text, wall_s, audio_s)
  --sources DIR           folder holding opencc/ (default: build_counts.SRC), the tables experiments/s2/build_counts.py uses
Both sides: OpenCC conversion (build_counts.convert: STPhrases/STCharacters/TWPhrases/TWVariants, the repo's s2twp equivalent,
sentence by sentence), then Han characters only. Output is TSV on stdout (no transcript text):
  id model cer edits ref_han hyp_han trad_share rtf       one row per video and model, then a TOTAL row per model
trad_share = share of the raw ASR output's Han characters that the conversion leaves unchanged (the contract does not define it).
"""
import argparse
import collections
import json
import os
import re
import sys

SEGMENT = 600
HAN = re.compile(r"[一-鿿㐀-䶿]")
CHUNK = re.compile(r"[^。！？；，、,.!?;\n]+")   # conversion decides simplified/traditional per sentence, so feed it pieces, not 10 minutes at once
TIME = re.compile(r"(?:(\d+):)?(\d+):(\d+)[.,](\d+)\s*-->")
TAG = re.compile(r"<[^>]*>")


def cues(vtt):
    """VTT text -> [(start_seconds, text)]."""
    out, start, lines = [], None, []
    for line in vtt.splitlines() + [""]:
        m = TIME.search(line)
        if m:
            h, mi, s, frac = m.groups()
            start, lines = int(h or 0) * 3600 + int(mi) * 60 + int(s) + int(frac) / 10 ** len(frac), []
        elif not line.strip():
            if start is not None and lines:
                out.append((start, " ".join(lines)))
            start, lines = None, []
        elif start is not None:
            lines.append(TAG.sub("", line).strip())
    return out


def reference(vtt, start):
    return "\n".join(t for s, t in cues(vtt) if start <= s < start + SEGMENT)


def han(text):
    return "".join(HAN.findall(text))


def normalise(text, conv):
    return han("".join(conv(c) for c in CHUNK.findall(text)))


def trad_counts(text, conv):
    """(Han characters, Han characters unchanged by conv) over the raw text."""
    n = same = 0
    for c in CHUNK.findall(text):
        a, b = han(c), han(conv(c))
        n += len(a)
        # ponytail: when conversion changes a chunk's length, count by multiset overlap; position-exact only for equal length
        same += sum(x == y for x, y in zip(a, b)) if len(a) == len(b) else sum((collections.Counter(a) & collections.Counter(b)).values())
    return n, same


def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def score(ref_raw, asr, conv):
    """One video and model -> dict of the counts the TSV and the totals need."""
    ref, hyp = normalise(ref_raw, conv), normalise(asr["text"], conv)
    n, same = trad_counts(asr["text"], conv)
    return {"edits": edit_distance(ref, hyp), "ref": len(ref), "hyp": len(hyp), "han": n, "same": same,
            "wall": asr["wall_s"], "audio": asr["audio_s"]}


def ratio(a, b):
    return a / b if b else float("nan")


def row(name, model, r):
    return [name, model, f"{ratio(r['edits'], r['ref']):.4f}", r["edits"], r["ref"], r["hyp"],
            f"{ratio(r['same'], r['han']):.4f}", f"{ratio(r['wall'], r['audio']):.3f}"]


def report(picks, sub_dir, asr_dir, conv):
    rows, total = [], collections.defaultdict(collections.Counter)
    for model in sorted(d for d in os.listdir(asr_dir) if os.path.isdir(os.path.join(asr_dir, d))):
        for p in picks:
            f = os.path.join(asr_dir, model, p["id"] + ".json")
            if not os.path.exists(f):
                print(f"missing {model}/{p['id']}", file=sys.stderr)
                continue
            vtt = open(os.path.join(sub_dir, f"{p['id']}.{p['lang']}.vtt"), encoding="utf-8").read()
            r = score(reference(vtt, p["start"]), json.load(open(f, encoding="utf-8")), conv)
            rows.append(row(p["id"], model, r))
            total[model].update(r)
        if model in total:
            rows.append(row("TOTAL", model, total[model]))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("picks")
    ap.add_argument("sub_dir")
    ap.add_argument("asr_dir")
    ap.add_argument("--sources")
    a = ap.parse_args()
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, os.path.join(root, "experiments", "s2"))
    import build_counts as bc
    if a.sources:
        bc.SRC = os.path.expanduser(a.sources)
    phrase, char, maxp = bc.load_conv()
    conv = lambda s: bc.convert(s, phrase, char, maxp)   # noqa: E731
    print("id\tmodel\tcer\tedits\tref_han\thyp_han\ttrad_share\trtf")
    for r in report(json.load(open(a.picks, encoding="utf-8")), a.sub_dir, a.asr_dir, conv):
        print("\t".join(map(str, r)))


if __name__ == "__main__":
    main()
