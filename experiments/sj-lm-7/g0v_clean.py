"""g0v Slack #rand0m archive -> cleaned sentences for the SJ-LM-7 colloquial counts (docs/contracts/sj-lm-7.md section 2.4; weight x5,
unit = one message). Same rules as ptt_clean.py: p2clean's split and filters, UNIT_CAP sentences per message, dedupe on p2clean's key.
The channel topic releases messages under CC0 unless the message marks itself "(no CC0)"; those are skipped. Only the increment is kept:
a sentence whose key is in an --exclude file (cc0-sentences and the colloquial pool, which already carry part of this channel) is dropped.
Usage: python3 experiments/sj-lm-7/g0v_clean.py ARCHIVE_DIR OUT_TSV [--exclude FILE]...   (ARCHIVE_DIR/<YYYY-MM>.html, the archive's month
pages; writes unit<TAB>sentence, unit = the message's Slack ts; prints counts only). The test is test_g0v_clean.py."""
import argparse
import collections
import glob
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ptt_clean  # noqa: E402

META = re.compile(r'<span class="message-time small" title="(.*?)">', re.S)   # the message's Slack JSON
NO_CC0 = re.compile(r"[(（]\s*no\s*cc0\s*[)）]", re.I)
MENTION = re.compile(r"<[@#!][^>]*>")
LINK = re.compile(r"<([^>|]+)\|([^>]*)>")    # <url|label>
BARE = re.compile(r"<([^>]+)>")             # <url>
STYLE = re.compile(r"[*~_]")   # bold, strike, italic marks (駕駛動力交通工具*肇*事); only Han runs are counted, so dropping every one is safe
EMOJI = re.compile(r"(?<![0-9]):[a-z0-9_+\-']+:")   # not after a digit: 7:11-7:25 and 16:15:30 are times


KEEP_SUBTYPES = (None, "thread_broadcast")   # a thread reply also sent to the channel is a person's message too


def messages(page, seen):
    """[(ts, text)] of the people's messages in page order, each ts once (the archive shows a thread's replies twice; `seen` carries
    across pages): joins, topic changes and other subtypes, and bots, are skipped."""
    out = []
    for raw in META.findall(page):
        m = json.loads(html.unescape(raw))
        if m.get("type") == "message" and m.get("subtype") in KEEP_SUBTYPES and not m.get("bot_id") and m.get("text") and m["ts"] not in seen:
            seen.add(m["ts"])
            out.append((m["ts"], m["text"]))
    return out


def plain(text):
    """Slack markup -> text: quoted lines dropped (someone else's words, not released by this author), mentions and emoji codes removed,
    bold/strike/italic marks removed, a link keeps its label (a bare URL stays, for the url filter)."""
    text = "\n".join(line for line in text.split("\n") if not line.lstrip().startswith(("&gt;", ">")))   # Slack escapes > in most messages, not all
    text = LINK.sub(r"\2", MENTION.sub("", text))
    return html.unescape(STYLE.sub("", EMOJI.sub("", BARE.sub(r"\1", text))))


def clean(P, msgs, exclude=frozenset()):
    """[(unit, sentence)], deduplicated in message order, and the rejection counts."""
    why, seen, rows = collections.Counter(), set(), []
    for ts, text in msgs:
        if NO_CC0.search(text):
            why["no_cc0_message"] += 1
            continue
        for s in ptt_clean.unit_sentences(P, [plain(text)], why):
            k = P.dedupe_key(s)
            if k in exclude:
                why["already_in_pool"] += 1
            elif k in seen:
                why["duplicate"] += 1
            else:
                seen.add(k)
                rows.append((ts, s))
    return rows, why


def main(src, out, excludes):
    P = ptt_clean.load_p2clean()
    msgs, seen = [], set()
    for f in sorted(glob.glob(os.path.join(src, "[0-9][0-9][0-9][0-9]-[0-9][0-9].html"))):
        msgs += messages(open(f, encoding="utf-8").read(), seen)
    exclude = set()
    for f in excludes:
        exclude.update(P.dedupe_key(line.strip()) for line in open(f, encoding="utf-8") if line.strip())
    rows, why = clean(P, msgs, frozenset(exclude))
    with open(out, "w", encoding="utf-8") as fh:
        fh.writelines(f"{u}\t{s}\n" for u, s in rows)
    print(json.dumps({"messages": len(msgs), "units_with_sentences": len({u for u, _ in rows}), "sentences": len(rows),
                      "han": sum(P.han_count(s) for _, s in rows), "dropped_by": dict(why.most_common())}, ensure_ascii=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(usage=__doc__)
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--exclude", action="append", default=[])
    a = ap.parse_args()
    main(a.src, a.out, a.exclude)
