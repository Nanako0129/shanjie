"""E2 chatclause (docs/contracts/e2-chat-eval.md section 2.1): the maintainer's own chat as clause rows, built from INPUT FILES only.

Private data: run by main on the maintainer's machine with --work in the private folder; the row files never enter the repo.

  prepare --work DIR [--discord MESSAGES_DIR --guild ID] [--meta EXPORT_DIR --self-name NAME] [--exclude ROWFILE ...]
      reads the sources, writes DIR/clauses.tsv (month, source, context, sentence, channel id, message id; every clause that
      survives the mechanical exclusions, in time order) and DIR/counts.json, prints the per-month counts table (numbers only).
      CE3 (docs/contracts/chat-eval-3.md): --guild is required with --discord and skips every channel of another server (or of none)
      without opening its messages file; a message outside 2015-07..2026-08 is dropped as soon as its timestamp is parsed, before any
      text is looked at, and only the per-month count (`_dropped_out_of_window`) is kept. Message ids do not depend on the position
      in the export (Discord: the snowflake; Meta: source, second, SHA-256 of the text). The two id columns stay in the private folder.
  freeze --work DIR --set ctune|cdev|cjudge [--k K]    (or --period ctune|cdev|cjudge-K)
      CE3 S1 side: the message's bucket is the first byte of sha256(source:id) (e2common.bucket_set: <96 ctune, <160 cdev, else the
      cjudge pool); writes DIR/<set>.rows.txt (`context|sentence|reading`), .sources.txt (one source per row), .origin.tsv (the drawn
      messages and their original sentences, private; later cjudge sets subtract them), .manifest.json, .sha256. Whole messages are
      drawn. --exclude FILE ... takes the frozen rows of the other side and of the other chat sets out (a sentence equal to one of them
      is dropped, and a correction that makes one equal stops the freeze). The PTT side is build_ptt_chat.py; main concatenates the two
      files of a set. prepare --legacy (periods frozen before CE3): no window, no --guild, duplicates per month.
  split --work DIR
      fixes the judge months from the mechanical counts (DIR/split.json; written once, a later run must give the same file).
  freeze --work DIR --period tune|judge|chatjudge|chatjudge2 [--corrections FILE] [--draft]
      tune: min(6,000, pool) clauses of the pool months, seed 20261012; judge: every clause of the judge months; chatjudge
      (docs/contracts/rerank-size.md section 3.4): min(3,000, rest) clauses of the pool months that the tuning sample neither drew
      nor repeats as a sentence, seed 20261017 (needs the tune freeze, like judge); chatjudge2 (docs/iteration-plan.md Phase 0
      item 7): min(6,000, rest) clauses of the pool months whose sentence neither the tuning sample nor the chatjudge sample holds,
      seed 20261018 (needs the tune and chatjudge freezes). Applies the optional
      corrections, spells with tools/readings.py, drops CHECK and unreadable rows, writes DIR/<period>.rows.txt (`context|sentence|reading`),
      DIR/<period>.manifest.json and DIR/<period>.sha256. With --draft only DIR/<period>.draft.txt (readings marked CHECK) for proofreading.
      A frozen period is never rewritten with other content; judge needs the tune freeze first (contract order (1) before (3)).
  emit --work DIR --period tune|judge|chatjudge|chatjudge2 --out FILE
      copies the frozen rows to FILE for run_sets.py dumps, and refuses (exit 1, nothing written) when the rows no longer hash to
      the frozen SHA-256.

Discord data package: MESSAGES_DIR is the package's messages folder (`Messages/` or `messages/`): one folder per channel
(`c<channel id>/` or just `<id>/`), each holding `messages.json` (a JSON array of objects with ID, Timestamp, Contents) or the older
`messages.csv` (header ID,Timestamp,Contents,Attachments). Both are read; index.json and every other folder are not. Each channel.json is read first and only GUILD_TEXT, GUILD_ANNOUNCEMENT,
PUBLIC_THREAD, ANNOUNCEMENT_THREAD and GUILD_VOICE channels are used: a DM, group DM, private thread, unknown type or missing
channel.json is skipped without opening its messages file. In every source, after the URL exclusion (and for Discord after removing the markup that carries snowflake ids: mentions, roles,
channels, custom emoji, timestamps, <id:...>), a message with an email address, phone-like number, a run
of 6+ digits, a Taiwan ID or a token/secret (private_rule) is dropped whole before anything else; only counts per rule are printed. Timestamps are
`2021-03-14 12:34:56.789000+00:00` (UTC) and are put in UTC+8 (Taiwan has no daylight saving) before the month is taken.
Meta official export ("Download your information", JSON; --meta EXPORT_DIR --self-name NAME): only these four exact relative paths
are opened (each optional; the rest of the export, e.g. followers.json, is never opened): threads/threads_and_replies.json,
facebook/your_posts__check_ins__photos_and_videos_N.json, facebook/posts_on_other_pages_and_profiles.json, facebook/comments.json
(the shapes are in meta_messages). Meta writes every string, keys and labels included, as UTF-8 bytes escaped as latin-1 (mojibake);
decode_tree undoes it. Comments are kept only when the author is --self-name. Each file is its own source in the tables. Context:
a post or comment's first segment is blank, later segments follow the previous one.

Corrections file (optional, UTF-8, tab separated, applied before spelling; the proofreading itself is main's job):
  OLD<TAB>NEW           the sentence OLD becomes NEW; NEW is spelled again, and a CHECK spelling drops the row (freeze warns and counts
                        it as corrected_then_check), so give READ with every changed sentence
  OLD<TAB>              drop the row
  OLD<TAB>NEW<TAB>READ  as the first, and READ (space separated syllables) is taken as the confirmed reading of NEW, so a CHECK row stays.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2common as c  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
GAP = dt.timedelta(minutes=10)         # contract 2.1: the previous message of the same channel counts as context within 10 minutes
SHANJIE_FROM = "2026-10"               # the maintainer typed with Zhuyin of Apple before this month, with shanjie from it (2026-10-12)
JUDGE_START = "2026-09"                # the judge months run back from here; never SHANJIE_FROM or later
JUDGE_MIN = 2950                       # mechanical rows (2,500 plus about 15% for proofreading and reading losses)
MIN_USABLE = 2500                      # fewer mechanical rows than this before SHANJIE_FROM: stop and ask
TUNE_MAX = 6000
CHATJUDGE_MAX = 3000       # rerank-size section 3.4: the unseen chat judge set
CHATJUDGE_SEED = 20261017
CHATJUDGE2_MAX = 6000      # maintainer 2026-10-18: a private judge set is at most 6,000 rows (one a month)
CHATJUDGE2_SEED = 20261018
PRIOR = {"judge": ("tune",), "chatjudge": ("tune",), "chatjudge2": ("tune", "chatjudge")}   # periods frozen before this one
CHECK_MAX = 0.10   # contract 2.1: more CHECK readings than this stops the freeze
URL = re.compile(r"https?://|www\.", re.I)
FENCE = "```"
STEPS = ("messages", "no_timestamp", "url", "code", "no_segment", "segments", "overlap", "duplicate", "mechanical")


# ------------------------------------------------------------------ reading the sources
def parse_ts(s):
    """A timestamp (aware: converted, naive: taken as UTC+8) in UTC+8."""
    d = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    return d.astimezone(TZ) if d.tzinfo else d.replace(tzinfo=TZ)


KEEP_CHANNELS = ("GUILD_TEXT", "GUILD_ANNOUNCEMENT", "PUBLIC_THREAD", "ANNOUNCEMENT_THREAD", "GUILD_VOICE")


def discord_messages(root, skipped=None, guild=None):
    """[(channel, id, ts, text)] of the messages folder; channels in folder-name order. ts is None when it cannot be read.
    Privacy (maintainer, 2026-10-12): each folder's channel.json is read first and only the KEEP_CHANNELS types are used; a DM, group DM,
    private thread, a missing channel.json or an unknown type is skipped WITHOUT opening its messages file. `skipped` (a dict) gets
    the number of skipped channels per type ('MISSING' for no channel.json).
    CE3 (docs/contracts/chat-eval-3.md section 2.1): with `guild` (an id string) a channel whose channel.json has another guild.id, or
    none, is skipped as well, after the type filter and before its messages file is opened ('GUILD_OTHER', 'GUILD_NONE' in `skipped`);
    no channel left that belongs to the guild stops the run."""
    out, in_guild = [], 0
    for ch in sorted(os.listdir(root)):
        d = os.path.join(root, ch)
        if not os.path.isdir(d):
            continue
        try:
            info = json.load(open(os.path.join(d, "channel.json"), encoding="utf-8"))
            kind = str(info.get("type"))
            g = info.get("guild")
            gid = str(g["id"]) if isinstance(g, dict) and g.get("id") is not None else None
        except (OSError, ValueError, AttributeError):
            kind, gid = "MISSING", None
        if kind not in KEEP_CHANNELS:
            if skipped is not None:
                skipped[kind] = skipped.get(kind, 0) + 1
            continue
        if guild is not None and gid != str(guild):
            why = "GUILD_NONE" if gid is None else "GUILD_OTHER"
            if skipped is not None:
                skipped[why] = skipped.get(why, 0) + 1
            continue
        in_guild += 1
        j, cs = os.path.join(d, "messages.json"), os.path.join(d, "messages.csv")
        if os.path.isfile(j):
            recs = json.load(open(j, encoding="utf-8"))
        elif os.path.isfile(cs):
            csv.field_size_limit(10 ** 9)
            recs = list(csv.DictReader(open(cs, encoding="utf-8", newline="")))
        else:
            continue
        for r in recs:
            try:
                ts = parse_ts(r.get("Timestamp") or "")
            except ValueError:
                ts = None
            out.append((ch, str(r.get("ID", "")), ts, r.get("Contents") or ""))
    if guild is not None and not in_guild:
        sys.exit("build_chatclause: STOP, no channel belongs to the given guild id (contract chat-eval-3 section 2.1)")
    return out


def unmojibake(x):
    """Meta's JSON holds UTF-8 bytes as latin-1 escapes (\\u00e5\\u00a5\\u00bd for the one character 好); undo that. A string that is not
    such a byte sequence (it has a character above U+00FF, or the bytes are not UTF-8) is already text and comes back unchanged."""
    try:
        return x.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return x


def decode_tree(x):
    """Every string of a Meta JSON tree, keys and labels included, through unmojibake."""
    if isinstance(x, str):
        return unmojibake(x)
    if isinstance(x, list):
        return [decode_tree(v) for v in x]
    if isinstance(x, dict):
        return {unmojibake(k): decode_tree(v) for k, v in x.items()}
    return x


def epoch(v):
    return dt.datetime.fromtimestamp(v, dt.timezone.utc).astimezone(TZ) if isinstance(v, (int, float)) and v > 0 else None


THREADS_FILE = os.path.join("threads", "threads_and_replies.json")
FB_POSTS = re.compile(r"your_posts__check_ins__photos_and_videos_(\d+)\.json\Z")
FB_OTHER = os.path.join("facebook", "posts_on_other_pages_and_profiles.json")
FB_COMMENTS = os.path.join("facebook", "comments.json")
OTHER_LABEL = "訊息"       # the label of a post's text in posts_on_other_pages_and_profiles.json


def _load(root, rel):
    p = os.path.join(root, rel)
    return decode_tree(json.load(open(p, encoding="utf-8"))) if os.path.isfile(p) else None


def meta_messages(root, self_name, window=None):
    """{source: ([(channel, id, ts, text)], {dropped counters})} from exactly these files under the export folder (each optional,
    nothing else is opened, no folder is walked or globbed beyond the one name pattern):
      threads/threads_and_replies.json  {"text_post_app_text_posts": [post]}: text = post title, else media[0] title; time =
          post creation_timestamp, else media[0] creation_timestamp (seconds)
      facebook/your_posts__check_ins__photos_and_videos_N.json  [{timestamp, data: [{post}], title}]: text = the data[].post
          strings joined with newline (the outer title is Meta's own sentence and is not read)
      facebook/posts_on_other_pages_and_profiles.json  [{timestamp, label_values: [{label, value}]}]: text = the value of the
          label 訊息
      facebook/comments.json  {"comments_v2": [{timestamp, data: [{comment: {timestamp, comment, author}}]}]}: one item per
          comment whose author is self_name (the others are counted and never read further)
    Sources: threads, facebook_posts, facebook_other_pages, facebook_comments. A post or comment is one message: its first segment
    has no context and the later ones follow the previous segment. window (first month, last month): a post outside it, or with no
    readable time, is counted (`_dropped_out_of_window` per month, `_dropped_no_timestamp`) as soon as its time is known, before its
    text is stripped or hashed."""
    out = {k: ([], {}) for k in ("threads", "facebook_posts", "facebook_other_pages", "facebook_comments")}

    def add(src, rel, k, ts, text):
        msgs, dropped = out[src]
        t = epoch(ts)
        if window:
            if t is None:
                dropped["_dropped_no_timestamp"] = dropped.get("_dropped_no_timestamp", 0) + 1
                return
            if not window[0] <= month(t) <= window[1]:
                gone = dropped.setdefault("_dropped_out_of_window", {})
                gone[month(t)] = gone.get(month(t), 0) + 1
                return
        if not (text or "").strip():
            dropped["_dropped_no_text"] = dropped.get("_dropped_no_text", 0) + 1
        else:
            # id (CE3 section 4): source, second and a hash of the text; it does not move when the export gains posts before or after
            mid = f"{src}:{int(t.timestamp()) if t else 0}:{hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]}"
            msgs.append((f"{rel}#{k}", mid, t, text))

    d = _load(root, THREADS_FILE)
    for k, post in enumerate((d or {}).get("text_post_app_text_posts", [])):
        media = (post.get("media") or [{}])[0]
        add("threads", THREADS_FILE, k, post.get("creation_timestamp") or media.get("creation_timestamp"), post.get("title") or media.get("title"))
    fb = os.path.join(root, "facebook")
    names = sorted((n for n in os.listdir(fb) if FB_POSTS.match(n)), key=lambda n: int(FB_POSTS.match(n).group(1))) if os.path.isdir(fb) else []
    for n in names:
        rel = os.path.join("facebook", n)
        for k, e in enumerate(_load(root, rel)):
            posts = [x["post"] for x in e.get("data", []) if isinstance(x, dict) and isinstance(x.get("post"), str)]
            add("facebook_posts", rel, k, e.get("timestamp"), "\n".join(posts))
    for k, e in enumerate(_load(root, FB_OTHER) or []):
        add("facebook_other_pages", FB_OTHER, k, e.get("timestamp"),
            "\n".join(v.get("value", "") for v in e.get("label_values", []) if v.get("label") == OTHER_LABEL))
    k = 0
    for e in (_load(root, FB_COMMENTS) or {}).get("comments_v2", []):
        for x in e.get("data", []):
            cm = x.get("comment")
            if not isinstance(cm, dict):
                continue
            k += 1
            if cm.get("author") != self_name:
                out["facebook_comments"][1]["_dropped_other_author"] = out["facebook_comments"][1].get("_dropped_other_author", 0) + 1
                continue
            add("facebook_comments", FB_COMMENTS, k, cm.get("timestamp") or e.get("timestamp"), cm.get("comment"))
    return out


# ------------------------------------------------------------------ personal data and secrets
# Maintainer's decision 2026-10-12: a message whose raw text has any of these is dropped whole, before segmentation, URL/code exclusion and
# the month counts, in every source. Only the counts per rule are ever printed, never the matched text.
# ponytail: the phone rule also takes an ISO date such as 2026-10-12 (8 digits with hyphens); dropping a message too many is the safe side.
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TW_ID = re.compile(r"[A-Z][12]\d{8}")
PHONE = re.compile(r"\+?\d(?:[\s\-.()]*\d){7,}")
DIGITS6 = re.compile(r"\d{6,}")
RUN20 = re.compile(r"[A-Za-z0-9_\-+/=]{20,}")
TOKEN_PREFIX = re.compile(r"\b(?:sk-|sk_|ghp_|gho_|github_pat_|xox[abpr]-|AKIA|AIza)|eyJ[A-Za-z0-9_\-]{5,}|Bearer ")
TOKEN_KV = re.compile(r"[\w.-]*(?:token|secret|password|passwd|pwd|api_key|apikey)[\w.-]*\s*=\s*\S", re.I)
# Discord markup carrying 17-20 digit snowflake ids (mentions, roles, channels, custom emoji, timestamps, guide links): not personal data,
# removed whole before the private filter (Discord only; main saw 1 in 6 server messages dropped by the phone rule because of them).
DISCORD_MARKUP = re.compile(r"<(?:@[!&]?\d+|#\d+|a?:[^:<>\s]+:\d+|t:-?\d+(?::[A-Za-z])?|id:[A-Za-z0-9_]+)>")
PRIVATE_RULES = ("email", "tw_id", "phone", "digits6", "token")


def private_rule(text):
    """The name of the first PRIVATE_RULES rule the raw text hits, else None."""
    if EMAIL.search(text):
        return "email"
    if TW_ID.search(text):
        return "tw_id"
    if PHONE.search(text):
        return "phone"
    if DIGITS6.search(text):
        return "digits6"
    if (TOKEN_PREFIX.search(text) or TOKEN_KV.search(text) or "-----BEGIN" in text
            or any(re.search("[A-Za-z]", m) and re.search("[0-9]", m) for m in RUN20.findall(text))):
        return "token"
    return None


# ------------------------------------------------------------------ clauses
def clauses(messages, cross_message):
    """[(ts, channel, seq, context, sentence, flags, id)] for every segment of every message. messages: [(channel, id, ts, text)] with
    ts not None. Context (contract 2.1): the previous segment of the same message; for the first segment, when cross_message, the
    last segment of the same channel's previous message if it is at most GAP earlier; else ''. flags: 'url' / 'code' when the
    message has one (the caller drops those messages)."""
    out, prev = [], {}
    for k, (ch, mid, ts, text) in enumerate(sorted(messages, key=lambda m: (m[0], m[2], m[1]))):
        segs = c.segments(text)
        before = prev.get(ch)
        for i, s in enumerate(segs):
            if i:
                ctx = segs[i - 1]
            elif cross_message and before and ts - before[0] <= GAP and before[1]:
                ctx = before[1]
            else:
                ctx = ""
            out.append((ts, ch, k, ctx, s, ("url" if URL.search(text) else "") + ("code" if FENCE in text else ""), mid))
        if not segs:
            out.append((ts, ch, k, "", None, ("url" if URL.search(text) else "") + ("code" if FENCE in text else ""), mid))
        prev[ch] = (ts, segs[-1] if segs else "")
    return out


def month(ts):
    return f"{ts.year:04d}-{ts.month:02d}"


def load_exclude(paths):
    """The sentences of the other evaluation sets (rows `context|sentence|reading`, or one sentence per line)."""
    s = set()
    for p in paths:
        for line in open(p, encoding="utf-8"):
            line = line.rstrip("\n")
            if line and not line.startswith("#"):
                s.add(line.split("|")[1] if "|" in line else line)
    return s


def prepare(discord_root, meta_root, self_name, exclude_files, work, guild=None, legacy=False):
    """Writes clauses.tsv (month, source, context, sentence, channel id, message id) and counts.json; returns the counts
    {source: {month: {step: n}, '_dropped_...': n}}. guild: only the Discord channels of that guild (discord_messages). Unless
    `legacy` (the periods frozen before CE3 only), a message outside e2common.WINDOW, or with no readable timestamp, is dropped right
    after its timestamp is parsed, before any function looks at its text (only the count per month is kept, `_dropped_out_of_window`),
    and a clause repeated within a source keeps its first copy (CE3 section 3); legacy keeps the old per-month duplicate rule."""
    window = None if legacy else c.WINDOW
    exclude = load_exclude(exclude_files)
    counts = {}

    def bump(src, mon, step):
        counts.setdefault(src, {}).setdefault(mon, dict.fromkeys(STEPS, 0))[step] += 1

    sources = []                                # (name, messages, cross-message context)
    if discord_root:
        skipped = {}
        sources.append(("discord", discord_messages(discord_root, skipped, guild), True))
        counts["discord"] = {"_skipped_channels_by_type": skipped}
    if meta_root:
        for src, (msgs, dropped) in meta_messages(meta_root, self_name, window).items():
            if msgs or dropped:
                counts[src] = dict(dropped)
                sources.append((src, msgs, False))
    rows = []                                   # (ts, source rank, month, source, context, sentence)
    for rank, (src, msgs, cross) in enumerate(sources):
        # Order (maintainer, 2026-10-12): URL exclusion of the whole message, then (Discord only) the platform markup is stripped, then the
        # private filter on the stripped text, then (below) code exclusion and segmentation. A message dropped here is also not offered as
        # context to the next one. ponytail: a URL message is no longer context for the next message (it was before).
        dated = [m for m in msgs if m[2] is not None]
        if len(dated) < len(msgs):
            counts.setdefault(src, {})["_dropped_no_timestamp"] = len(msgs) - len(dated)
        if window:
            outside = {}
            for m in dated:
                if not window[0] <= month(m[2]) <= window[1]:
                    outside[month(m[2])] = outside.get(month(m[2]), 0) + 1
            gone = dict(counts.get(src, {}).get("_dropped_out_of_window", {}))
            for mon, n in outside.items():
                gone[mon] = gone.get(mon, 0) + n
            counts.setdefault(src, {})["_dropped_out_of_window"] = dict(sorted(gone.items()))
            dated = [m for m in dated if window[0] <= month(m[2]) <= window[1]]
        hits, clean = {}, []
        for ch, mid, ts, text in dated:
            if URL.search(text):
                bump(src, month(ts), "messages")
                bump(src, month(ts), "url")
                continue
            if src == "discord":
                text = DISCORD_MARKUP.sub(" ", text)
            r = private_rule(text)
            if r:
                hits[r] = hits.get(r, 0) + 1
            else:
                clean.append((ch, mid, ts, text))
        dated = clean
        counts.setdefault(src, {})["_dropped_private_by_rule"] = hits
        by_msg = {}
        for ts, ch, k, ctx, s, flags, mid in clauses(dated, cross):
            by_msg.setdefault((ch, k), []).append((ts, ctx, s, flags, ch, mid))
        for cl in by_msg.values():
            ts, _ctx, first, flags, _ch, _mid = cl[0]
            mon = month(ts)
            bump(src, mon, "messages")
            if "url" in flags:
                bump(src, mon, "url")
            elif "code" in flags:
                bump(src, mon, "code")
            elif first is None:
                bump(src, mon, "no_segment")
            else:
                for ts, ctx, s, _f, ch, mid in cl:
                    bump(src, mon, "segments")
                    rows.append((ts, rank, mon, src, ctx, s, ch if src == "discord" else mid, mid))
    rows.sort(key=lambda r: (r[0], r[1]))
    seen, kept = set(), []
    for _ts, _rank, mon, src, ctx, s, ch, mid in rows:
        if s in exclude:
            bump(src, mon, "overlap")
        elif ((mon, s) if legacy else (src, s)) in seen:
            bump(src, mon, "duplicate")
        else:
            seen.add((mon, s) if legacy else (src, s))
            kept.append((mon, src, ctx, s, ch, mid))
            bump(src, mon, "mechanical")
    os.makedirs(work, exist_ok=True)
    c.write_lf(os.path.join(work, "clauses.tsv"), "".join("\t".join(r) + "\n" for r in kept))
    c.write_lf(os.path.join(work, "counts.json"), json.dumps(counts, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return counts


def mechanical_by_month(counts):
    t = {}
    for src, months in counts.items():
        for mon, st in months.items():
            if not mon.startswith("_"):
                t[mon] = t.get(mon, 0) + st["mechanical"]
    return t


def prev_month(mon):
    y, m = int(mon[:4]), int(mon[5:])
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def choose_judge_months(by_month, pool_months=(), start=JUDGE_START):
    """(judge months, pool months) from the mechanical counts alone. Judge: `start` (JUDGE_START unless the maintainer moved it) then each
    earlier month until the rows reach
    JUDGE_MIN (months without data count 0 and are passed). Never SHANJIE_FROM or later; never a month in `pool_months` (months
    already in a frozen tuning pool). Pool: every other month with data before the judge months. Stops (SystemExit) when all the
    months before SHANJIE_FROM together have fewer than MIN_USABLE rows."""
    if not (re.fullmatch(r"\d{4}-\d{2}", start) and start < SHANJIE_FROM):
        sys.exit(f"build_chatclause: STOP, judge start {start} must be a month before {SHANJIE_FROM}")
    usable = {m: n for m, n in by_month.items() if m < SHANJIE_FROM}
    if sum(usable.values()) < MIN_USABLE:
        sys.exit(f"build_chatclause: STOP, {sum(usable.values())} mechanical rows before {SHANJIE_FROM} (< {MIN_USABLE}); ask the maintainer")
    first, judge, total, mon = min(usable), [], 0, start
    while total < JUDGE_MIN:
        if mon < first:
            sys.exit(f"build_chatclause: STOP, the months back to {first} give only {total} rows (< {JUDGE_MIN}); ask the maintainer")
        if mon in pool_months:
            sys.exit(f"build_chatclause: STOP, judge month {mon} is already in the frozen tuning pool")
        judge.append(mon)
        total += usable.get(mon, 0)
        mon = prev_month(mon)
    pool = sorted(m for m in usable if m < min(judge))
    return sorted(judge), pool


# ------------------------------------------------------------------ periods
def read_clauses(work, full=False):
    """(month, source, context, sentence) per clause; full adds the channel id and message id columns (private intermediate file)."""
    rows = [tuple(l.rstrip("\n").split("\t")) for l in open(os.path.join(work, "clauses.tsv"), encoding="utf-8")]
    if full and any(len(r) != 6 for r in rows):
        sys.exit("build_chatclause: STOP, clauses.tsv has no channel id and message id columns; run prepare again")
    return rows if full else [r[:4] for r in rows]


def frozen_sentences(work, period):
    return {l.split("|")[1] for l in open(os.path.join(work, f"{period}.rows.txt"), encoding="utf-8") if l.strip()}


def period_rows(work, period):
    """[(context, sentence)] before corrections: the whole judge months, the seeded tuning sample of the pool, (chatjudge) a sample
    of the pool rows whose sentence the tuning sample does not hold, with a seed of its own, or (chatjudge2) a sample of the pool
    rows whose sentence neither the tuning sample nor the chatjudge sample holds, nor any frozen file main has read (tune,
    chatjudge and, when frozen here, judge; corrected sentences included), with a third seed. chatjudge2 stops when a replayed
    sample is not the frozen one: more frozen tune (chatjudge) sentences fall outside the replayed sample than that period's
    manifest counts corrections."""
    split = json.load(open(os.path.join(work, "split.json"), encoding="utf-8"))
    months = set(split["judge_months"] if period == "judge" else split["pool_months"])
    rows = [(x, s) for mon, _src, x, s in read_clauses(work) if mon in months]
    pool = len(rows)
    if period == "tune":
        rows = random.Random(c.SEED).sample(rows, min(TUNE_MAX, len(rows)))
    elif period in ("chatjudge", "chatjudge2"):
        # Every sentence of the tuning sample (drawn exactly as above) is left out wherever it sits, its own rows included; freeze
        # then checks the corrected rows against the frozen chatclause sentences.
        tuned = {s for _x, s in random.Random(c.SEED).sample(rows, min(TUNE_MAX, len(rows)))}
        rest = [r for r in rows if r[1] not in tuned]
        drawn = random.Random(CHATJUDGE_SEED).sample(rest, min(CHATJUDGE_MAX, len(rest)))
        if period == "chatjudge":
            rows = drawn
        else:   # the chatjudge sample's sentences go the same way; freeze checks against the frozen chatjudge rows too
            # The replays must be the samples that were frozen: a frozen sentence outside its replayed sample can only be a corrected
            # one. Checked on content, not on the recorded pool size: a reshuffled clauses.tsv keeps the size, and the work-v2 tune
            # freeze (2026-10-13, 81c035b0) wrote its sample size, 6,000, in clauses_before_sampling_or_corrections.
            for p, sample in (("tune", tuned), ("chatjudge", {s for _x, s in drawn})):
                corrected = json.load(open(os.path.join(work, f"{p}.manifest.json"), encoding="utf-8"))["corrected"]
                stray = len(frozen_sentences(work, p) - sample)
                if stray > corrected:
                    sys.exit(f"build_chatclause: STOP, {stray} frozen {p} sentences are not in its replayed sample (it has {corrected} "
                             f"corrections); clauses.tsv changed since that freeze, so its sample cannot be replayed")
            seen = {s for _x, s in drawn}.union(*(frozen_sentences(work, p) for p in ("tune", "chatjudge", "judge")
                                                  if os.path.isfile(os.path.join(work, f"{p}.rows.txt"))))
            rest = [r for r in rest if r[1] not in seen]
            rows = random.Random(CHATJUDGE2_SEED).sample(rest, min(CHATJUDGE2_MAX, len(rest)))
    return rows, pool


CE3_CAPS = {"ctune": 6000, "cdev": 5000}    # contract chat-eval-3 section 4: S1 rows per set
CJUDGE_HALF = 3000                            # a cjudge-k holds at most 6,000 rows, S1 and the PTT side each half
CE3_PERIOD = re.compile(r"(ctune|cdev|cjudge-([1-9]\d*))\Z")


def ce3_name(period):
    """('ctune' | 'cdev' | 'cjudge', k) for a CE3 set name, else None."""
    m = CE3_PERIOD.match(period)
    return (m.group(1).split("-")[0], int(m.group(2) or 0)) if m else None


def prior_cjudge(work, k):
    """The sentences of the frozen cjudge-1..k-1 of this folder, as they were drawn (<set>.origin.tsv, before corrections, drops and
    CHECK; private) and as frozen: a later judge set subtracts both, so a corrected, dropped or CHECK row never comes back, and since
    every clause of a drawn message is in that file, a drawn message has nothing left to be drawn again. Stops when one is missing."""
    sents = set()
    for j in range(1, k):
        if not (os.path.isfile(os.path.join(work, f"cjudge-{j}.rows.txt")) and os.path.isfile(os.path.join(work, f"cjudge-{j}.origin.tsv"))):
            sys.exit(f"build_chatclause: STOP, freeze cjudge-{j} before cjudge-{k}")
        sents |= {l.rstrip("\n").split("\t")[2] for l in open(os.path.join(work, f"cjudge-{j}.origin.tsv"), encoding="utf-8")}
        sents |= frozen_sentences(work, f"cjudge-{j}")
    return sents


def ce3_rows(work, name, k=1, others=frozenset()):
    """CE3 section 4, S1 side: the messages whose bucket (c.bucket_set) is `name`, from the frozen clauses.tsv, drawn WHOLE (all clauses
    of a message go to the same set). A clause whose sentence the buckets hold in more than one set, or that is in `others` (the frozen
    rows of the other side and of the other chat sets, section 3), or in an earlier frozen cjudge, is dropped. ctune / cdev: messages
    drawn with seed c.CE3_SEED while the rows fit min(cap, pool). cjudge-k: months from CJUDGE_LAST backwards, whole months until
    CJUDGE_HALF rows (the first month that does not fit entirely is drawn and ends it), minus the sentences of the frozen
    cjudge-1..k-1; the messages of the cjudge bucket after CJUDGE_LAST are only counted.
    -> ([(context, sentence, source, message id)], pool rows, stats)."""
    full, where = read_clauses(work, full=True), {}
    for _mon, src, _x, sent, _ch, mid in full:
        where.setdefault(sent, set()).add(c.bucket_set(src, mid))
    prior_sents = prior_cjudge(work, k) if name == "cjudge" else set()
    msgs, late, overlap = {}, set(), 0
    for mon, src, x, sent, _ch, mid in full:
        if c.bucket_set(src, mid) != name:
            continue
        key = (src, mid)
        if name == "cjudge" and mon > c.CJUDGE_LAST:
            late.add(key)
        elif where[sent] != {name} or sent in prior_sents or sent in others:
            overlap += 1
        else:
            msgs.setdefault(key, (mon, []))[1].append((x, sent))
    rng = random.Random(c.CE3_SEED)
    items = [(key, rows, mon) for key, (mon, rows) in msgs.items()]
    if name == "cjudge":
        take = []
        for mon in sorted({i[2] for i in items}, reverse=True):
            month_items = [i for i in items if i[2] == mon]
            need = CJUDGE_HALF - sum(len(i[1]) for i in take)
            take += c.draw_messages(month_items, need, rng)
            if sum(len(i[1]) for i in month_items) > need:
                break
    else:
        take = c.draw_messages(items, CE3_CAPS[name], rng)
    sample = [(x, sent, key[0], key[1]) for key, rows, _mon in take for x, sent in rows]
    return (sample, sum(len(i[1]) for i in items),
            {"months": sorted({i[2] for i in take}), "not_used_cjudge_bucket_after_2026_07_messages": len(late), "dropped_overlap_clauses": overlap})


def freeze_ce3(work, period, sample, pool_rows, extra, corrections_file=None, draft=False, readings=c.readings, others=frozenset()):
    """Freezes a CE3 set (either side) in `work`. sample: [(context, sentence, source, message id)], the rows as drawn.
    Writes <period>.rows.txt (`context|sentence|reading`), .sources.txt (the source of each row, line for line), .origin.tsv (the drawn
    messages and original sentences, private), .manifest.json and .sha256. Stops when more than CHECK_MAX of the rows are CHECK, when a
    corrected sentence is also in `others` or in another frozen CE3 set of the folder, or when the set is already frozen with other content."""
    frozen, drafted, st, tags = build_period([(x, s) for x, s, _src, _mid in sample], load_corrections(corrections_file), readings,
                                             [(src, mid) for _x, _s, src, mid in sample])
    check_share = st["dropped_check"] / st["rows_in"] if st["rows_in"] else 0
    if draft:
        if check_share > CHECK_MAX:
            print(f"build_chatclause: WARNING, {check_share:.1%} of the {period} rows are CHECK (> {CHECK_MAX:.0%}); a real freeze would stop", file=sys.stderr)
        c.write_lf(os.path.join(work, f"{period}.draft.txt"),
                   "".join(f"{x}|{s}|{r}{'|CHECK' if status != 'ok' else ''}\n" for x, s, r, status in drafted))
        return st
    if check_share > CHECK_MAX:
        sys.exit(f"build_chatclause: STOP, {check_share:.1%} of the {period} rows are CHECK (> {CHECK_MAX:.0%}); report to main (contract 2.1)")
    if st["corrected_then_check"]:
        print(f"build_chatclause: WARNING, {st['corrected_then_check']} corrected {period} rows spell as CHECK and are dropped; give READ", file=sys.stderr)
    held = set(others)
    for f in sorted(os.listdir(work)):
        if f.endswith(".rows.txt") and f[:-9] != period and ce3_name(f[:-9]):
            held |= frozen_sentences(work, f[:-9])
    clash = {s for _x, s, _r in frozen} & held
    if clash:
        sys.exit(f"build_chatclause: STOP, {len(clash)} {period} sentences are also in another frozen chat set (section 3); report to main")
    text = "".join(f"{x}|{s}|{r}\n" for x, s, r in frozen)
    sha = c.sha256_bytes(text.encode("utf-8"))
    sf = os.path.join(work, f"{period}.sha256")
    if os.path.isfile(sf) and open(sf).read().split()[0] != sha:
        sys.exit(f"build_chatclause: STOP, the {period} period is frozen with another content; it is never rewritten")
    c.write_lf(os.path.join(work, f"{period}.rows.txt"), text)
    c.write_lf(sf, f"{sha}  {period}.rows.txt\n")
    c.write_lf(os.path.join(work, f"{period}.sources.txt"), "".join(src + "\n" for src, _mid in tags))
    c.write_lf(os.path.join(work, f"{period}.origin.tsv"), "".join(f"{src}\t{mid}\t{s}\n" for _x, s, src, mid in sample))
    st["rows_by_source"] = {k: sum(src == k for src, _mid in tags) for k in sorted({src for src, _mid in tags})}
    st.update(extra, period=period, clauses_before_sampling_or_corrections=pool_rows, sha256=sha)
    c.write_lf(os.path.join(work, f"{period}.manifest.json"), json.dumps(st, ensure_ascii=False, indent=1) + "\n")
    return st


def load_corrections(path):
    out = {}
    for line in open(path, encoding="utf-8") if path else []:
        p = line.rstrip("\n").split("\t")
        if len(p) >= 2 and p[0]:
            out[p[0]] = (p[1], p[2] if len(p) > 2 else None)
    return out


def build_period(rows, corrections, readings=c.readings, tags=None):
    """-> (frozen rows [(ctx, sentence, reading)], draft rows [(ctx, sentence, reading, status)], stats). With `tags` (one per input row)
    a fourth value comes back: the tag of each frozen row, whatever a correction did to its sentence."""
    st = {"rows_in": len(rows), "corrected": 0, "dropped_by_correction": 0, "dropped_check": 0, "corrected_then_check": 0,
          "dropped_unreadable": 0}
    work, wtags = [], []
    for i, (x, s) in enumerate(rows):
        if s in corrections:
            new, rd = corrections[s]
            if not new:
                st["dropped_by_correction"] += 1
                continue
            st["corrected"] += new != s
            work.append((x, new, rd, new != s))
        else:
            work.append((x, s, None, False))
        wtags.append(tags[i] if tags else None)
    rd = readings([s for _, s, _, _ in work])
    draft = []
    for x, s, given, changed in work:
        status, reading = ("ok", given) if given else rd[s]
        draft.append((x, s, reading, status))
        st["corrected_then_check"] += changed and status == "check"
    frozen = [(x, s, r) for x, s, r, status in draft if status == "ok"]
    ftags = [t for t, d in zip(wtags, draft) if d[3] == "ok"]
    st["dropped_check"] = sum(d[3] == "check" for d in draft)
    st["dropped_unreadable"] = sum(d[3] == "unreadable" for d in draft)
    st["rows"] = len(frozen)
    return (frozen, draft, st, ftags) if tags is not None else (frozen, draft, st)


def freeze(work, period, corrections_file=None, draft=False, readings=c.readings, others=()):
    ce3 = ce3_name(period)
    if ce3:
        sample, pool_rows, extra = ce3_rows(work, *ce3, others=load_exclude(others))
        return freeze_ce3(work, period, sample, pool_rows, extra, corrections_file, draft, readings, load_exclude(others))
    for p in PRIOR.get(period, ()):
        if not os.path.isfile(os.path.join(work, f"{p}.sha256")):
            sys.exit("build_chatclause: STOP, freeze the tuning period first (contract 2.1 order (1) before (3))" if p == "tune" else
                     f"build_chatclause: STOP, freeze the {p} period before {period}")
        if period.startswith("chatjudge") and not os.path.isfile(os.path.join(work, f"{p}.rows.txt")):
            sys.exit(f"build_chatclause: STOP, {p}.rows.txt is missing; {period} is checked against it")
    rows, pool_rows = period_rows(work, period)
    frozen, drafted, st = build_period(rows, load_corrections(corrections_file), readings)
    check_share = st["dropped_check"] / st["rows_in"] if st["rows_in"] else 0
    if draft:
        if check_share > CHECK_MAX:
            print(f"build_chatclause: WARNING, {check_share:.1%} of the {period} rows are CHECK (> {CHECK_MAX:.0%}); a real freeze would stop", file=sys.stderr)
        c.write_lf(os.path.join(work, f"{period}.draft.txt"),
                   "".join(f"{x}|{s}|{r}{'|CHECK' if status != 'ok' else ''}\n" for x, s, r, status in drafted))
        return st
    if check_share > CHECK_MAX:
        sys.exit(f"build_chatclause: STOP, {check_share:.1%} of the {period} rows are CHECK (> {CHECK_MAX:.0%}); report to main (contract 2.1)")
    if st["corrected_then_check"]:
        print(f"build_chatclause: WARNING, {st['corrected_then_check']} corrected {period} rows spell as CHECK and are dropped; give READ",
              file=sys.stderr)
    for p in (PRIOR[period] if period.startswith("chatjudge") else ()):     # judge months never meet the pool
        seen = frozen_sentences(work, p)
        if any(s in seen for _x, s, _r in frozen):
            sys.exit(f"build_chatclause: STOP, a {period} sentence is also in the frozen {'chatclause' if p == 'tune' else p}; "
                     f"report to main ({'rerank-size 3.4' if period == 'chatjudge' else 'iteration plan Phase 0 item 7'})")
    text = "".join(f"{x}|{s}|{r}\n" for x, s, r in frozen)
    sha = c.sha256_bytes(text.encode("utf-8"))
    sf = os.path.join(work, f"{period}.sha256")
    if os.path.isfile(sf) and open(sf).read().split()[0] != sha:
        sys.exit(f"build_chatclause: STOP, the {period} period is frozen with another content; it is never rewritten")
    c.write_lf(os.path.join(work, f"{period}.rows.txt"), text)
    c.write_lf(sf, f"{sha}  {period}.rows.txt\n")
    split = json.load(open(os.path.join(work, "split.json"), encoding="utf-8"))
    st.update(period=period, clauses_before_sampling_or_corrections=pool_rows, sha256=sha,
              months=split["judge_months"] if period == "judge" else split["pool_months"])
    c.write_lf(os.path.join(work, f"{period}.manifest.json"), json.dumps(st, ensure_ascii=False, indent=1) + "\n")
    return st


def emit(work, period, out):
    rows_file, sf = os.path.join(work, f"{period}.rows.txt"), os.path.join(work, f"{period}.sha256")
    if not os.path.isfile(sf):
        sys.exit(f"build_chatclause: no frozen {period} period in {work}")
    want = open(sf).read().split()[0]
    got = c.sha256_file(rows_file)
    if got != want:
        sys.exit(f"build_chatclause: REFUSED, {period}.rows.txt hashes to {got[:16]}…, frozen {want[:16]}…")
    c.write_lf(out, open(rows_file, encoding="utf-8").read())


def show_counts(counts):
    for src in sorted(counts):
        extra = {k: v for k, v in counts[src].items() if k.startswith("_")}
        print(f"## {src} {extra if extra else ''}".rstrip())
        print("| month | ime | " + " | ".join(STEPS) + " |\n|---|---|" + "---|" * len(STEPS))
        for mon in sorted(m for m in counts[src] if not m.startswith("_")):
            print(f"| {mon} | {'shanjie' if mon >= SHANJIE_FROM else 'apple'} | " + " | ".join(str(counts[src][mon][s]) for s in STEPS) + " |")


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "split", "freeze", "emit"):
        p = sub.add_parser(name)
        p.add_argument("--work", required=True)
        if name == "prepare":
            p.add_argument("--discord"); p.add_argument("--guild", help="Discord server id; required with --discord (CE3 section 2.1)")
            p.add_argument("--legacy", action="store_true", help="the periods frozen before CE3: no time window, no --guild, duplicates per month")
            p.add_argument("--meta"); p.add_argument("--self-name"); p.add_argument("--exclude", nargs="*", default=[])
        if name == "emit":
            p.add_argument("--period", required=True)
        if name == "freeze":
            p.add_argument("--period", help="tune|judge|chatjudge|chatjudge2, or a CE3 set name (ctune, cdev, cjudge-K)")
            p.add_argument("--set", choices=["ctune", "cdev", "cjudge"], help="CE3 set; cjudge also needs --k")
            p.add_argument("--k", type=int, default=1)
            p.add_argument("--exclude", nargs="*", default=[], help="CE3: row files of the other side (build_ptt_chat output) and of the other chat sets")
        if name == "split":
            # Maintainer 2026-10-13: 2026-09 is skipped (never read or used); the judge period starts at 2026-08. Months from the start
            # up to SHANJIE_FROM are in neither the judge period nor the pool.
            p.add_argument("--judge-start", default=JUDGE_START)
        if name == "freeze":
            p.add_argument("--corrections"); p.add_argument("--draft", action="store_true")
        if name == "emit":
            p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "prepare":
        if not (a.discord or a.meta):
            ap.error("give --discord and/or --meta")
        if a.meta and not a.self_name:
            ap.error("--meta needs --self-name (the author name on your own Facebook comments)")
        if a.discord and not a.guild and not a.legacy:
            ap.error("--discord needs --guild ID (only one server's channels are read)")
        counts = prepare(a.discord, a.meta, a.self_name, a.exclude, a.work, a.guild, a.legacy)
        show_counts(counts)
    elif a.cmd == "split":
        by = mechanical_by_month(json.load(open(os.path.join(a.work, "counts.json"), encoding="utf-8")))
        tf = os.path.join(a.work, "tune.manifest.json")
        frozen_pool = json.load(open(tf, encoding="utf-8"))["months"] if os.path.isfile(tf) else ()
        judge, pool = choose_judge_months(by, frozen_pool, a.judge_start)
        out = {"judge_start": a.judge_start, "judge_months": judge, "pool_months": pool, "mechanical_by_month": dict(sorted(by.items())),
               "judge_min": JUDGE_MIN, "never_from": SHANJIE_FROM}
        text = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
        sp = os.path.join(a.work, "split.json")
        if os.path.isfile(sp) and open(sp, encoding="utf-8").read() != text:
            sys.exit("build_chatclause: STOP, split.json exists with another split; it is never changed")
        c.write_lf(sp, text)
        print(f"judge months {judge} ({sum(by.get(m, 0) for m in judge)} mechanical rows); pool {len(pool)} months, "
              f"{sum(by[m] for m in pool)} rows")
    elif a.cmd == "freeze":
        a.period = a.period or (a.set if a.set in ("ctune", "cdev") else f"cjudge-{a.k}" if a.set else None)
        if a.period not in ("tune", "judge", "chatjudge", "chatjudge2") and not ce3_name(a.period or ""):
            ap.error("give --period (tune, judge, chatjudge, chatjudge2, ctune, cdev, cjudge-K) or --set")
        print(json.dumps(freeze(a.work, a.period, a.corrections, a.draft, others=a.exclude), ensure_ascii=False))
    else:
        emit(a.work, a.period, a.out)


if __name__ == "__main__":
    main()
