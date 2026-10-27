"""Shared helpers of the E2 builders (docs/contracts/e2-chat-eval.md): Han segmentation, SHA-256, readings through tools/readings.py."""
import collections
import hashlib
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SEED = 20261012
# The segmentation of experiments/s2/build_tune.py (runs of Han characters, at most 30; a longer run is cut every 30 and a remainder
# shorter than the minimum is dropped), with the E2 minimum of 2 characters instead of build_tune's 4.
HAN = re.compile(r"[一-鿿]{2,30}")


def segments(text):
    return HAN.findall(text)


# CE3 (docs/contracts/chat-eval-3.md section 4): a message goes to a set by the first byte of sha256(source + message id), so all clauses
# of one message share a bucket and the bucket does not depend on where the message sits in an export.
BUCKETS = ((96, "ctune"), (160, "cdev"), (256, "cjudge"))    # first byte < bound -> set
CE3_SEED = 20261027
WINDOW = ("2015-07", "2026-08")      # contract section 2: the months any source may be read from
CJUDGE_LAST = "2026-07"              # the judge pool stops one month earlier (maintainer's judge-set rule)


def bucket_set(source, mid):
    first = hashlib.sha256(f"{source}:{mid}".encode("utf-8")).digest()[0]
    return next(name for bound, name in BUCKETS if first < bound)


def draw_messages(items, cap, rng, limit=None):
    """Whole messages, never single clauses (a message's clauses chain their contexts, so they stay in one set). items: [(key, rows,
    account)] in a fixed order. They are shuffled with rng and taken while the rows still fit `cap`; a message that does not fit is
    skipped, and so is one that would take its account past `limit` rows. -> the chosen items in the order drawn."""
    order, total, per, out = list(items), 0, collections.Counter(), []
    rng.shuffle(order)
    for key, rows, acct in order:
        if total + len(rows) > cap or (limit and per[acct] + len(rows) > limit):
            continue
        total += len(rows)
        per[acct] += len(rows)
        out.append((key, rows, acct))
    return out


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    with open(path, "rb") as f:
        return sha256_bytes(f.read())


def write_lf(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def readings(sentences):
    """{sentence: (status, reading)} through tools/readings.py; status is 'ok', 'check' (ambiguous, reading is the tool's first
    choice) or 'unreadable' (the lexicon cannot spell it, reading ''). One tool run over the unique sentences."""
    uniq = sorted(set(sentences))
    with tempfile.TemporaryDirectory() as td:
        src, dst = os.path.join(td, "in.txt"), os.path.join(td, "out.txt")
        write_lf(src, "".join(f"|{s}\n" for s in uniq))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "readings.py"), src, dst], capture_output=True, text=True, encoding="utf-8")
        if r.returncode:
            sys.exit(f"readings.py failed:\n{r.stderr[-2000:]}")
        check = {l.split("\t")[1] for l in r.stderr.splitlines() if l.startswith("CHECK\t")}
        got = {}
        for line in open(dst, encoding="utf-8"):
            _ctx, s, rd = line.rstrip("\n").split("|")
            got[s] = rd
    return {s: (("check" if s in check else "ok"), got[s]) if s in got else ("unreadable", "") for s in uniq}
