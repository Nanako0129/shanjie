#!/usr/bin/env python3
"""Per-version KPI table (docs/spec/evaluation.md §8). Research tool, not shipped.

  tools/kpi.py run REF... [--as VERSION] --wikitune-clean FILE [--legacy-repo PATH]
  tools/kpi.py replay --as VERSION
  tools/kpi.py table [--results DIR] [--kpi DIR] [--out FILE] [--json FILE]

`run` has the arguments of `tools/bench.py run --suite v2`: it exports and builds each ref with bench.py's own `Ver`, sets up the version's
shipped models, scores FILE (the wikitune-clean rows) with the shipped flags in the formal profile and writes the result into
eval/bench/kpi/<version>.json. It builds old versions: run it on the Windows machine or GCP, not on the Mac. A version released before
this repository (bench.LEGACY_SHA) is exported from the legacy clone given by --legacy-repo (or SHANJIE_LEGACY_REPO), as in bench.py.
`replay` runs in the CURRENT checkout (no changes to tracked files; if the tag --as exists it must be HEAD):
`cargo build --release --locked -p cli --bin shanjie-replay --message-format=json` (the binary path is the compiler's own report) types the pinned user-reported and dev302 rows through the real engine
with data/lm models and writes the `replay` field of eval/bench/kpi/<version>.json.
`table` reads eval/bench/results/v2-*.json and eval/bench/kpi/*.json, writes the public-set part to site/data/kpi.json, which the
site's /kpi page draws (docs/spec/evaluation.md §8.4), and the full markdown table (private sets included) to --out FILE or
$SHANJIE_TABLES/kpi.md, never inside this repository; without either it writes only the JSON.
tools/bench.py is imported for its building blocks. Never reads eval/holdout/ (the holdout halves are typed into the kpi JSON by hand).
"""
import argparse, ast, json, os, re, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402

ROOT = bench.ROOT
KPI_DIR = os.path.join(ROOT, "eval/bench/kpi")
SITE_JSON = os.path.join(ROOT, "site/data/kpi.json")
# When speed numbers count (docs/spec/evaluation.md §8.2). Two rules, by what the result file recorded:
# - the window rule, for a result that has `speed_load_after` (bench.py's `speed_window`, the machine's state right before and right after
#   the speed measurement): Time Machine was not backing up at either end and the 1-minute load right after was at most
#   SPEED_LOAD_AFTER_MAX. The load right before is recorded and not judged: the accuracy cells run before the speed measurement, so it
#   carries bench's own work.
# - the start rule, for the results before that (v0.7.0 and earlier): the 1-minute load when the whole run started was at most LOAD_MAX.
#   It cannot see what starts after the run did (a backup that began two minutes into a v0.7.0 run slowed both versions by about 40%;
#   that run's result was set aside and is in no tracked file, its numbers are from the local record of the run).
# SPEED_LOAD_AFTER_MAX is provisional: it sits between the load after the two undisturbed v0.7.0 runs (2.77 in the result of c3ffeff2,
# 4.25 in the current one) and the disturbed one (5.30, local record only), three runs in all. Set it again, and decide whether the load
# before joins the rule, once three versions carry the new fields.
LOAD_MAX = 2.0
SPEED_LOAD_AFTER_MAX = 5.0
# name -> (rows file, pinned sha256): the replay rows are frozen suite files, not eval/dev/*.
REPLAY_ROWS = {"user-reported": bench.SETS2["user-reported"][:2], "dev302": bench.SETS2["dev302"][:2]}
REPLAY_SUMMARY = re.compile(r"^## (\S+)  (\S+)  (\{.*\})$", re.M)


def die(msg):
    print(f"kpi: {msg}", file=sys.stderr)
    sys.exit(1)


def write_kpi(kpi_dir, version, update):
    """Merge `update` into <kpi_dir>/<version>.json: fields this run does not set stay as they were."""
    p = os.path.join(kpi_dir, version + ".json")
    d = json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else {}
    d.update(update)
    os.makedirs(kpi_dir, exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:  # same directory, so os.replace is atomic: a crash never leaves a half-written record
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(p + ".tmp", p)
    return p


# ---------- run: wikitune-clean, shipped flags, formal profile ----------
def usable_cores():
    return len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)


def cli_takes_threads(src):
    """Whether the exported tree's CLI parses `--threads` (older CLIs stop on an unknown argument). A source check, because the
    flag's presence is all that differs: the binary's behaviour with it is the CLI's own contract."""
    try:
        with open(os.path.join(src, "cli/src/main.rs"), encoding="utf-8") as f:
            return '"--threads"' in f.read()
    except OSError:
        return False


def wikitune_cell(v, rows, rowstats):
    """The checks of bench.run_cell2 (setting tag, row count, rowstats top1 == summary top1) for a set SETS2 does not know. Not
    run_cell2's dump cross-check (the CLI is run without --dump). Decodes on every core this process may use (`--threads`), because the
    remote machines it runs on must be filled; cli/src/main.rs documents the files and summary line as byte for byte those of one
    thread. CLIs built before `--threads` existed (a5f53462, v0.4.x) decode on one thread."""
    cmd = bench.cell_cmd(v, "formal", rows, None, rowstats, False)
    if cli_takes_threads(v.dir):
        cmd += ["--threads", str(usable_cores())]
    m = bench.parse_summary(bench.sh(cmd, cwd=v.dir, env=bench.cli_env(False)))
    want = bench.expected_tag(v, "formal", False)
    if m["tag"] != want:
        raise RuntimeError(f"wikitune-clean/formal: CLI setting tag {m['tag']} != {want} from the settings table")
    if m["n"] != len(bench.truths(rows)):
        raise RuntimeError(f"wikitune-clean/formal: CLI scored {m['n']} rows, the set has {len(bench.truths(rows))}")
    ok = bench.parse_rowstats(rowstats, m["n"])
    if sum(ok) != m["top1"]:
        raise RuntimeError(f"wikitune-clean/formal: rowstats top1 {sum(ok)} != CLI summary top1 {m['top1']}")
    return m


def cmd_run(a, kpi_dir=KPI_DIR):
    bench.set_legacy(a)
    labels = bench.version_labels(a.refs, a.as_)
    if bench.sha_file(bench.VARIANTS) != bench.VARIANTS_SHA:
        die("suite variants.tsv does not match its pinned SHA-256")
    a.wikitune_clean = os.path.abspath(a.wikitune_clean)
    if os.path.realpath(a.wikitune_clean).startswith(os.path.realpath(os.path.join(ROOT, "eval/holdout")) + os.sep):
        die("the rows file is under eval/holdout/: the holdout is measured only by the verifier, once")
    if not os.path.isfile(a.wikitune_clean):
        die(f"missing rows file {a.wikitune_clean}")
    rows_sha = bench.sha_file(a.wikitune_clean)
    with tempfile.TemporaryDirectory() as td:
        for ref, label in zip(a.refs, labels):
            v = bench.Ver(ref)
            v.label = label
            try:
                v.build()
            except Exception as e:
                die(f"{ref}: build failed: {e}")
            bench.setup_models(v, bench.VERSIONS2[label], td)
            try:
                m = wikitune_cell(v, a.wikitune_clean, os.path.join(td, label + ".rowstats"))
            except RuntimeError as e:
                die(f"{label}: {e}")
            path = write_kpi(kpi_dir, label, {"version": label, "sha": v.sha, "wikitune_clean": {
                "n": m["n"], "top1": m["top1"], "top1_sha256": m["top1_sha256"], "tag": m["tag"], "rows_sha256": rows_sha}})
            print(f"kpi: {label} wikitune-clean/formal {m['top1']}/{m['n']} -> {path}")


# ---------- replay: current checkout, data/lm models ----------
def git_clean_head(root, version):
    """HEAD of `root`, if no tracked file changed (untracked files, such as the results bench.py / kpi.py run just wrote, do not count;
    nor does this version's own kpi JSON, which `kpi.py run` rewrites when the version is measured again after its file was committed)
    and, when the tag `version` already exists, it points at HEAD."""
    if version in bench.LEGACY_SHA:   # this repository's tag of that name is not the version's commit (bench.LEGACY_SHA)
        die(f"{version} is a legacy version (commit {bench.LEGACY_SHA[version][:8]} of the legacy repository): replay measures the current checkout, "
            f"so run it from a checkout of that commit, not from this repository")
    own = os.path.relpath(KPI_DIR, ROOT) + f"/{version}.json"
    if bench.sh(["git", "-C", root, "status", "--porcelain", "--untracked-files=no", "--", f":(top,exclude){own}"]).strip():
        die("a tracked file has changes: replay measures the commit it records")
    head = bench.sh(["git", "-C", root, "rev-parse", "HEAD"]).strip()
    t = subprocess.run(["git", "-C", root, "rev-parse", "--verify", "--quiet", version + "^{commit}"], capture_output=True, text=True)
    if t.returncode == 0 and t.stdout.strip() != head:
        die(f"tag {version} is {t.stdout.strip()[:8]}, HEAD is {head[:8]}: check out the tagged commit")
    return head


def check_models(root, spec):
    """-> {file name: sha256} of exactly the files the version ships (resolve_models' set), read from data/lm and matching data/*.sha256."""
    names = ["bigram.sjlm", "classes.sjc"] + (["kn.sjkn"] if spec.get("kn") else []) + (["trigram.sjt3"] if spec.get("tri") else []) \
        + (["trigram-chat.sjt3"] if spec.get("tri_chat") else []) + (["tinylm.sjtl"] if spec["tinylm"] else []) + (["colloquial.sjlm"] if spec.get("colloquial") else [])
    lm = os.path.join(root, "data/lm")
    extra = sorted(f for f in os.listdir(lm) if f not in names and not f.startswith("."))
    if extra:
        die(f"data/lm holds files the version does not ship: {', '.join(extra)}")
    out = {}
    for n in names:
        hf = os.path.join(root, "data", n + ".sha256")
        if not os.path.isfile(hf):
            die(f"data/{n}.sha256 is missing")
        want = open(hf).read().split()[0]
        p = os.path.join(lm, n)
        if not os.path.isfile(p):
            die(f"data/lm/{n} is missing")
        if bench.sha_file(p) != want:
            die(f"data/lm/{n} does not match data/{n}.sha256")
        out[n] = want
    return out


def models_line(models):
    """What shanjie-replay prints on stderr (cli/src/bin/replay.rs): bigram, then classes/kn/trigram/trigram-chat that exist, then tinylm or none, then the colloquial table when loaded."""
    s = " ".join(f"{n}:{models[n][:8]}" for n in ("classes.sjc", "kn.sjkn", "trigram.sjt3", "trigram-chat.sjt3") if n in models)
    co = f" colloquial.sjlm:{models['colloquial.sjlm'][:8]}" if "colloquial.sjlm" in models else ""
    return f"models: bigram.sjlm:{models['bigram.sjlm'][:8]} {s} tinylm.sjtl:{models['tinylm.sjtl'][:8] if 'tinylm.sjtl' in models else 'none'}{co}"


def build_replay(root):
    """cargo build --locked; -> the executable path the compiler reports for the shanjie-replay bin."""
    r = subprocess.run(["cargo", "build", "--release", "--locked", "-p", "cli", "--bin", "shanjie-replay", "--message-format=json"],
                       cwd=root, capture_output=True, text=True)
    if r.returncode:
        die(f"cargo build failed ({r.returncode}): {r.stderr[-400:]}")
    for line in r.stdout.split("\n"):
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("reason") == "compiler-artifact" and get(m, "target", "name") == "shanjie-replay" and "bin" in get(m, "target", "kind") and m.get("executable"):
            return m["executable"]
    die("cargo reported no shanjie-replay executable")


def pack_tag(spec):
    """The packs part of the replay tag the version ships, from its VERSIONS2 flags (`--packs acg,it` -> `+acg+it`). The replay binary formats its
    own tag from the pack mask it builds the engine with (cli/src/replay.rs PACKS), so a binary whose packs differ from the version's is refused."""
    f = spec["flags"]
    return "".join("+" + p for p in f[f.index("--packs") + 1].split(",")) if "--packs" in f else ""


def replay_cell(binary, root, rows, models, learn, rowstats, packs, reading=False):
    """One shanjie-replay run (chat, --correct); checks its models line, setting tag and row count. -> the summary dict.
    The replay loads data/lm/colloquial.sjlm by default, as the App does: a version that ships the table (it is in `models`) runs as is and its tag
    ends in +colloq:<sha8>+rc (the reading-channel term rides on the colloquial term, chat); one that does not ships no data/lm/colloquial.sjlm, and `--no-colloquial` keeps the replay from loading another.
    A version with `"reading": True` in VERSIONS2 (the decoder's reading term, docs/spec/decoder.md §5) has no `+rc` and ends its tag in `+rd+rx:<sha8 of its data/lexicon/std-exempt.tsv>+rm` (`+rm`: the reading-mass rule)."""
    cmd = [binary, "--rows", rows, "--profile", "chat", "--correct", "--learn", "on" if learn else "off", "--rowstats", rowstats]
    if "tinylm.sjtl" not in models:
        cmd.append("--no-tinylm")
    if "colloquial.sjlm" not in models:
        cmd.append("--no-colloquial")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=root, env=bench.cli_env(False))
    if r.returncode:
        raise RuntimeError(f"shanjie-replay failed ({r.returncode}): {r.stderr[-300:]}")
    if models_line(models) not in r.stderr.split("\n"):
        raise RuntimeError(f"shanjie-replay models line does not match data/*.sha256: {r.stderr.strip()[-300:]}")
    found = REPLAY_SUMMARY.findall(r.stdout)
    if len(found) != 1:
        raise RuntimeError(f"expected one summary line, got {len(found)}")
    name, tag, d = found[0]
    s = {**ast.literal_eval(d), "tag": tag}
    lm8, tl8 = models["bigram.sjlm"][:8], models.get("tinylm.sjtl", "")[:8]
    want = f"replay-chat+ctx{packs}+nopred+noen+lm:{lm8}" + (f"+tl:{tl8}" if tl8 else "-notinylm") + ("+learn" if learn else "") + "+correct" \
        + (f"+colloq:{models['colloquial.sjlm'][:8]}" + ("" if reading else "+rc") if "colloquial.sjlm" in models else "")
    if reading:
        want += f"+rd+rx:{bench.sha_file(os.path.join(root, 'data', 'lexicon', 'std-exempt.tsv'))[:8]}+rm"
    if tag != want:
        raise RuntimeError(f"setting tag {tag} != {want}")
    n = len(bench.truths(rows))
    if s["n"] != n:
        raise RuntimeError(f"replay scored {s['n']} rows, the set has {n}")
    if sum(bench.parse_rowstats(rowstats, n)) != s["top1"]:
        raise RuntimeError("rowstats top1 differs from the summary line")
    return s


def fixed_broken(base, cand, n):
    """Fixed / broken counts of `evalstats.py compare` (a row right only in cand / only in base), from the two rowstats files."""
    b, c = bench.parse_rowstats(base, n), bench.parse_rowstats(cand, n)
    return sum(y and not x for x, y in zip(b, c)), sum(x and not y for x, y in zip(b, c))


def cmd_replay(a, root=ROOT, kpi_dir=KPI_DIR, build=build_replay):
    label = a.as_
    if label not in bench.VERSIONS2:
        die(f"{label!r} is not in VERSIONS2 ({', '.join(bench.VERSIONS2)})")
    if bench.sha_file(bench.VARIANTS) != bench.VARIANTS_SHA:
        die("suite variants.tsv does not match its pinned SHA-256")
    sha = git_clean_head(root, label)
    models = check_models(root, bench.VERSIONS2[label])
    for name, (f, want) in REPLAY_ROWS.items():
        if bench.sha_file(f) != want:
            die(f"{name}: {f} does not match its pinned SHA-256")
    binary = build(root)
    rep = {"sha": sha, "models": models, "variants_sha256": bench.VARIANTS_SHA}
    with tempfile.TemporaryDirectory() as td:
        def cell(name, learn):
            rs = os.path.join(td, f"{name}.{'on' if learn else 'off'}.rs")
            try:
                return replay_cell(binary, root, REPLAY_ROWS[name][0], models, learn, rs, pack_tag(bench.VERSIONS2[label]), bool(bench.VERSIONS2[label].get("reading"))), rs
            except RuntimeError as e:
                die(f"{name}: {e}")
        for name in ("user-reported", "dev302"):
            off, rs_off = cell(name, False)
            rep[name] = {"rows_sha256": REPLAY_ROWS[name][1], "n": off["n"], "off": off}
            if name == "user-reported":
                rep[name]["on"], rs_on = cell(name, True)
                rep[name]["learn_fixed"], rep[name]["learn_broken"] = fixed_broken(rs_off, rs_on, off["n"])
    print(f"kpi: {label} replay -> {write_kpi(kpi_dir, label, {'version': label, 'replay': rep})}")


# ---------- table ----------
def get(d, *path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def vkey(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))


def load_json_dir(d, prefix=""):
    return [(f, json.load(open(os.path.join(d, f), encoding="utf-8"))) for f in sorted(os.listdir(d)) if f.startswith(prefix) and f.endswith(".json")] if os.path.isdir(d) else []


def pick_v2(version, v2):
    """The v2 file that measured `version` as the newer side (it has the vs_prev), else the one that has it as the older side; latest date wins."""
    new, old = [], []
    for name, d in v2:
        for i, r in enumerate(d.get("versions", [])):
            if r.get("version") == version:
                (new if i > 0 else old).append((d.get("date", ""), name, d, r))
    pool = new or old
    return max(pool, key=lambda t: t[:2]) if pool else None


def pct(top1, n):
    return f"{100 * top1 / n:.2f}%（{top1}/{n}）"


def sha8(h):
    return "#" + h[:8]


def acc_col(title, cell, setname):
    def f(x):
        a = get(x["rec"], "accuracy", cell)
        s = get(x["res"], "sets", setname)
        if not a or "top1" not in a or not s or "sha256" not in s:
            return None
        return s["sha256"], a["n"], pct(a["top1"], a["n"])
    return title, f


def error_ratio(x):
    v = get(x["rec"], "vs_prev", "discordtune/chat")
    s = get(x["res"], "sets", "discordtune")
    if not v or "fixed" not in v or not s or "sha256" not in s:
        return None
    n = get(x["rec"], "accuracy", "discordtune/chat", "n")
    return s["sha256"], n, "—" if v["fixed"] == 0 else f"{v['broken'] / v['fixed']:.2f}（{v['broken']}/{v['fixed']}）"


def kpi_col(title, path, text):
    """A column read from kpi/<v>.json: the block at `path` carries n and rows_sha256; `text(block)` is the cell (None = no number)."""
    def f(x):
        b = get(x["kpi"], *path)
        t = text(b) if isinstance(b, dict) else None
        return None if t is None else (b.get("rows_sha256"), b.get("n"), t)
    return title, f


def holdout_col(title, field):
    def f(x):
        h = get(x["kpi"], field)
        return (h.get("rows_sha256"), h["n"], pct(h["top1"], h["n"])) if isinstance(h, dict) and "top1" in h and "n" in h else None
    return title, f


def speed_verdict(env):
    """(rule, idle, the cell's text when not idle, the note after the numbers) for a result's machine block, or None when it recorded
    neither rule's field. The window rule applies exactly when `speed_load_after` is there; a missing Time Machine field is unknown."""
    if "speed_load_after" in env:
        tms, after = (env.get("speed_tm_before"), env.get("speed_tm_after")), env["speed_load_after"]
        if any(t is True for t in tms):   # a backup comes before the load as the reason
            return "window", False, "—（備份中）", ""
        if after > SPEED_LOAD_AFTER_MAX:
            return "window", False, f"—（速度段後負載 {after:.2f}）", ""
        return "window", True, None, "（備份狀態未知）" if any(t is None for t in tms) else ""
    if "load_avg_start" not in env:
        return None
    load = env["load_avg_start"]
    return ("start", False, f"—（負載 {load:.2f}）", "") if load > LOAD_MAX else ("start", True, None, "")


def speed(x):
    s, env = get(x["rec"], "speed"), get(x["res"], "env")  # the column splits per machine (env.machine)
    v = speed_verdict(env) if s and "p95_us" in s and env else None
    if not v:
        return None
    _, idle, reason, note = v
    if not idle:
        return env.get("machine"), None, reason
    t = f"按鍵 p95 {s['p95_us'] / 1000:.2f} ms"
    if "rerank_p95_ms" in s:
        t += f"／重排 p95 {s['rerank_p95_ms']:.1f} ms"
    return env.get("machine"), None, t + f"／RSS {s['rss_mb']:.0f} MB" + note


COLUMNS = [
    acc_col("discordtune 聊天 top1", "discordtune/chat", "discordtune"),
    ("error ratio（聊天，弄壞÷修好，對上一版）", error_ratio),
    kpi_col("錯字回報重播 top1（學習關）", ("replay", "user-reported"), lambda b: pct(b["off"]["top1"], b["n"]) if get(b, "off", "top1") is not None and "n" in b else None),
    kpi_col("錯字回報重播 學習開改壞", ("replay", "user-reported"), lambda b: str(b["learn_broken"]) if "learn_broken" in b else None),
    kpi_col("按鍵分數 KySS（dev302 聊天）", ("replay", "dev302"), lambda b: f"{b['off']['kyss']:.4f}" if get(b, "off", "kyss") is not None else None),
    kpi_col("按鍵分數 KySS（錯字回報聊天）", ("replay", "user-reported"), lambda b: f"{b['off']['kyss']:.4f}" if get(b, "off", "kyss") is not None else None),
    kpi_col("顯示後被改掉（dev302 聊天，每百列次數）", ("replay", "dev302"), lambda b: f"{100 * b['off']['rewrites'] / b['n']:.1f} 次／百列（{b['off']['rewrites']}/{b['n']}）" if get(b, "off", "rewrites") is not None and b.get("n") else None),
    holdout_col("保留集口語半邊（聊天）", "holdout_talk_chat"),
    holdout_col("保留集新聞半邊（書面）", "holdout_news_formal"),
    kpi_col("wikitune-clean 書面 top1", ("wikitune_clean",), lambda b: pct(b["top1"], b["n"]) if "top1" in b and "n" in b else None),
    acc_col("lytune 書面 top1（只當警示）", "lytune/formal", "lytune"),
    ("速度與記憶體（閒置窗）", speed),
]


def load(results_dir, kpi_dir):
    """-> (rows, kpi): the one read of the JSON files that both the full table and site/data/kpi.json are made from.
    rows = [(version, {"rec", "res", "kpi"}, v2 file name or None)], newest first."""
    v2, kpi = load_json_dir(results_dir, "v2-"), {f[:-5]: d for f, d in load_json_dir(kpi_dir)}
    versions = {r["version"] for _, d in v2 for r in d.get("versions", []) if "version" in r} | set(kpi)
    rows = []
    for v in sorted(versions, key=lambda v: (vkey(v), v), reverse=True):
        p = pick_v2(v, v2)
        rows.append((v, {"rec": p[3] if p else {}, "res": p[2] if p else {}, "kpi": kpi.get(v, {})}, p[1] if p else None))
    return rows, kpi


def render_data(rows, kpi):
    cols = []  # (header, {version: cell}); a column splits into one per distinct set (key = its hash, or the machine for the speed column)
    for title, f in COLUMNS:
        groups = {}
        for v, x, _ in rows:
            r = f(x)
            if r:
                groups.setdefault(r[0], (r[1], {}))[1][v] = r[2]
        for key, (n, cells) in groups.items() or [(None, (None, {}))]:
            head = ([f"n={n}"] if n is not None else []) + ([sha8(key) if re.fullmatch("[0-9a-f]{64}", key) else key] if key else [])
            cols.append((title + ("<br>" + "，".join(head) if head else ""), cells))
    L = ["# 每版 KPI（`tools/kpi.py table` 產生，不手改）", "",
         "規格：`docs/spec/evaluation.md §8`。最新的版本在上。欄位的來源是 `eval/bench/results/v2-*.json`（套件 v2）與 `eval/bench/kpi/<版本>.json`（`tools/kpi.py run`、`replay` 與保留集兩個半邊）；"
         "「—」是沒有這個數字（舊版沒有重播工具、保留集不回填，或速度是在有負載時量的）。表頭的 n 與 `#` 開頭的 8 碼是列檔雜湊；換了列檔，欄位另開，不同列檔的數字不放同一欄。", "",
         "| 版本 | " + " | ".join(h for h, _ in cols) + " |", "|---|" + "---|" * len(cols)]
    for v, _, _ in rows:
        L.append(f"| {v} | " + " | ".join(c.get(v, "—") for _, c in cols) + " |")
    L += ["", "## 來源檔", ""]
    for v, _, f in rows:
        src = ([f"`results/{f}`"] if f else []) + ([f"`kpi/{v}.json`"] if v in kpi else [])
        L.append(f"- {v}：" + ("、".join(src) or "（無）"))
    return "\n".join(L) + "\n"


def render(results_dir, kpi_dir):
    return render_data(*load(results_dir, kpi_dir))


# ---------- site/data/kpi.json: the public-set part of the same data ----------
# The replay rows (user-reported, dev302), the talk half of the holdout and the speed numbers. Not here: the private chat set and its
# error ratio, the lytune and wikitune-clean columns, the news half of the holdout.
REPLAY_SETS = ("user-reported", "dev302")
REPLAY_KEYS = ("n", "top1", "kyss", "rewrites", "picks_per100")


def site_row(x):
    out, replay = {}, get(x["kpi"], "replay") or {}
    for name in REPLAY_SETS:
        b = replay.get(name)
        if not isinstance(b, dict) or "off" not in b:
            continue
        r = {k: b[k] for k in ("rows_sha256", "n") if b.get(k) is not None}
        r["off"] = {k: b["off"][k] for k in REPLAY_KEYS if k in b["off"]}
        if "on" in b:
            r["on"] = {k: b["on"][k] for k in REPLAY_KEYS if k in b["on"]}
        r.update({k: b[k] for k in ("learn_fixed", "learn_broken") if k in b})
        out.setdefault("replay", {})[name] = r
    h = get(x["kpi"], "holdout_talk_chat")
    if isinstance(h, dict) and "top1" in h and "n" in h:
        out["holdout_talk_chat"] = {k: h[k] for k in ("n", "top1", "fixed", "broken", "p", "rows_sha256") if k in h}
    s, env = get(x["rec"], "speed"), get(x["res"], "env")
    v = speed_verdict(env) if s and "p95_us" in s and env else None
    if v:   # the same verdict as the table's (`speed`)
        rule, idle = v[:2]
        if rule == "window":   # a row of the start rule keeps the shape it always had: no `rule` key
            out["speed"] = {"machine": env.get("machine"), "rule": "window", "idle": idle, "speed_load_before": env.get("speed_load_before"),
                            "speed_load_after": env["speed_load_after"], "speed_tm_before": env.get("speed_tm_before"),
                            "speed_tm_after": env.get("speed_tm_after")}
        else:
            out["speed"] = {"machine": env.get("machine"), "load_avg_start": env["load_avg_start"], "idle": idle}
        if idle:
            out["speed"].update({k: s[k] for k in ("p95_us", "rerank_p95_ms", "rss_mb") if k in s})
    return out


def site_data(rows, kpi):
    return {"versions": [{"version": v, **site_row(x)} for v, x, _ in rows]}


def cmd_table(a):
    md = bench.md_path(a.out, "kpi.md", "kpi")
    rows, kpi = load(a.results, a.kpi)
    bench.write_json(a.json, site_data(rows, kpi))
    if md:
        bench.write_md(md, render_data(rows, kpi))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("refs", nargs="+")
    r.add_argument("--as", dest="as_", help="settings-table version for the last ref (a commit without a tag yet)")
    r.add_argument("--wikitune-clean", required=True, help="rows file (not in the repo)")
    bench.add_legacy_arg(r)
    p = sub.add_parser("replay")
    p.add_argument("--as", dest="as_", required=True, help="settings-table version the current checkout ships")
    t = sub.add_parser("table")
    t.add_argument("--results", default=bench.RESULTS)
    t.add_argument("--kpi", default=KPI_DIR)
    t.add_argument("--out", help="where to write the full table (default $SHANJIE_TABLES/kpi.md; never inside this repository)")
    t.add_argument("--json", default=SITE_JSON)
    a = ap.parse_args()
    {"run": cmd_run, "replay": cmd_replay, "table": cmd_table}[a.cmd](a)


if __name__ == "__main__":
    main()
