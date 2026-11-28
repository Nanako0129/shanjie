"""tools/vanguard_textmap.py (docs/spec/vanguard-textmap.md). Run: python3 -W ignore -m unittest tools/test_vanguard_textmap.py

The unit tests cover the grouped-cell codec, parse_extra_ngrams, the two override functions and the strict validator. The end-to-end tests run the
tool as a subprocess on a synthetic repo root built from the kn-tiny toy model (eval/golden/kn-tiny): three modes (--out-txtmap, --out-dict,
--base-txtmap), the path-free build_stats.json, the "inputs" record, and the refusal to write inside the repo work tree. Everything is written
under a throwaway folder; no real data, models or private files are read.

The --simplified tests build in-process against a synthetic OpenCC folder (two small tables whose sha256 replaces vt.OPENCC_SHA256 for the
duration of the call); only the "hash differs" and "file missing" cases run the command line against the real constants. None of them
needs the real OpenCC tables."""
import hashlib
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(HERE, "vanguard_textmap.py")
TINY = os.path.join(ROOT, "eval", "golden", "kn-tiny")
sys.path.insert(0, HERE)
import vanguard_textmap as vt  # noqa: E402

BUILD_FLAGS = ["--packs", "none", "--kn-beta", "0.75", "--bi-min-count", "1", "--bi-min-ctx", "1", "--validate"]

FIXTURE = "\n".join([
    "#PRAGMA:VANGUARD_HOMA_LEXICON_HEADER", "VERSION\t2.0", "TYPE\tTYPING", "READING_SEPARATOR\t-",
    "ENTRY_COUNT\t7", "KEY_COUNT\t3", "DEFAULT_PROB_7\t-11", "#PRAGMA:VANGUARD_HOMA_LEXICON_VALUES",
    "@-5\tchsA\tA|B", "@-6\t\tC", ">7\tA|C", "A\t-4\t6\tX", "!B\t6\t-3\tY\t-3.5\tZ|W",
    "@-7\t\tD",
    "_NORM\t1000\t2",
    "#PRAGMA:VANGUARD_HOMA_LEXICON_KEY_LINE_MAP", "ㄅ\t0\t5", "ㄆ\t5\t1", "ㄇ\t6\t1", ""])

EXTRAS = [  # reading, value, context, prob
    ("ㄅ", "A", "", -2), ("ㄅ", "E", "", -6), ("ㄅ", "F", "", -5.5), ("ㄆ", "D", "", -7), ("ㄈ", "G", "", -9),
    ("ㄅ", "A", "X", -1), ("ㄅ", "B", "Y", -2), ("ㄅ", "B", "Q", -4), ("ㄅ", "C", "P|Q", -3),
]


SYN_PHRASES = "# synthetic OpenCC table\n世界線\t世界线\n"
SYN_CHARS = "# synthetic OpenCC table\n綫\t线\n線\t线 綫\n牀\t床\n佔\t占\n"
VENDOR = "BopoMola_善解輸入法"


def make_opencc(base):
    """A synthetic OpenCC folder and the OPENCC_SHA256 that matches it."""
    d = os.path.realpath(os.path.join(base, "opencc"))
    os.makedirs(d)
    shas = {}
    for name, text in (("TSPhrases.txt", SYN_PHRASES), ("TSCharacters.txt", SYN_CHARS)):
        raw = text.encode("utf-8")
        with open(os.path.join(d, name), "wb") as f:
            f.write(raw)
        shas[name] = hashlib.sha256(raw).hexdigest()
    return d, shas


FIXED_COMMIT_DATE = "2020-01-01T00:00:00Z"
FIXED_COMMIT_SECONDS = "1577836800"


def git_repo(root):
    """Commit everything under root as a throwaway git repo; returns (git argv prefix, HEAD sha, committer time)."""
    git = ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
    # a fixed past commit time, so a build that used the wall clock instead of %ct could not match
    env = {**os.environ, "GIT_COMMITTER_DATE": FIXED_COMMIT_DATE, "GIT_AUTHOR_DATE": FIXED_COMMIT_DATE}
    subprocess.run([*git, "commit", "-q", "--no-verify", "-m", "x"], check=True, capture_output=True, env=env)
    out = lambda *a: subprocess.run([*git, *a], check=True, capture_output=True, text=True).stdout.strip()
    return git, out("rev-parse", "HEAD"), out("log", "-1", "--format=%ct")


def build_in_process(root, out_txtmap, opencc_dir, shas, *extra):
    """vt.build_textmap on the synthetic root with the synthetic OpenCC folder trusted; returns the stats dict."""
    args = vt.make_parser().parse_args(["--shanjie-root", root, "--out-txtmap", out_txtmap, *BUILD_FLAGS, "--opencc-dir", opencc_dir, *extra])
    with mock.patch.dict(vt.OPENCC_SHA256, shas):
        return vt.build_textmap(args)


def read_map(path):
    lines = open(path, encoding="utf-8").read().split("\n")
    vi = lines.index("#PRAGMA:VANGUARD_HOMA_LEXICON_VALUES")
    ki = lines.index("#PRAGMA:VANGUARD_HOMA_LEXICON_KEY_LINE_MAP")
    vals = lines[vi + 1:ki]
    return {k: vals[int(st):int(st) + int(c)] for k, st, c in (ln.split("\t") for ln in lines[ki + 1:] if ln)}


def cht_unigrams(block):
    d = {}
    for ln in block:
        p = ln.split("\t")
        if ln.startswith("@") and len(p) == 3:
            for w in vt._decode_grouped_values_strict(p[2], allows_empty_cell=True):
                d[w] = float(p[0][1:])
    return d


def ctx_entries(block):
    d = {}
    for ln in block:
        p = ln.split("\t")
        if ln.startswith("!") and len(p) > 4 and p[1] == "6":
            for i in range((len(p) - 2) // 2):
                d[(p[0][1:], tuple(vt._decode_grouped_values_strict(p[3 + 2 * i])))] = float(p[2 + 2 * i])
        elif ln[:1] not in ("@", ">", "!") and len(p) == 4 and p[2] == "6":
            d[(p[0], tuple(vt._decode_grouped_values_strict(p[3])))] = float(p[1])
    return d


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield from strings(k)
            yield from strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from strings(v)


def run_tool(*args, ok=True):
    r = subprocess.run([sys.executable, "-W", "ignore", TOOL, *args], capture_output=True, text=True)
    if ok:
        assert r.returncode == 0, r.stderr
    return r


def make_root(base):
    """A synthetic repo root: the kn-tiny lexicon as mcbpmf-data.txt, empty overlay/sandhi/names/variant-order, the kn-tiny model and side file."""
    root = os.path.realpath(os.path.join(base, "repo"))
    os.makedirs(os.path.join(root, "data", "lexicon"))
    os.makedirs(os.path.join(root, "data", "lm"))
    shutil.copy(os.path.join(TINY, "lexicon.txt"), os.path.join(root, "data", "lexicon", "mcbpmf-data.txt"))
    for n in ("overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "variant-order.tsv"):
        open(os.path.join(root, "data", "lexicon", n), "w").close()
    shutil.copy(os.path.join(TINY, "bigram.sjlm"), os.path.join(root, "data", "lm", "bigram.sjlm"))
    shutil.copy(os.path.join(TINY, "kn.sjkn"), os.path.join(root, "data", "lm", "kn.sjkn"))
    return root


def make_companion(base):
    comp = os.path.realpath(os.path.join(base, "companion"))
    os.makedirs(comp)
    for n in ("phonic_table_Z.txt", "data-punctuations.txt", "data-paired-punctuations.txt", "template-a.txt"):
        open(os.path.join(comp, n), "w").write(n + "\n")
    return comp


class CodecTests(unittest.TestCase):
    def test_grouped_roundtrip_and_strict_decode(self):
        vals = ["a b", "c|d", "e\\f", "x\x07y", "測試"]
        cell = vt.encode_grouped_values(vals)
        self.assertEqual(vt._decode_grouped_values_strict(cell), vals)
        self.assertEqual(vt.encode_grouped_values([]), "\x07")
        self.assertEqual(vt._decode_grouped_values_strict("\x07"), [])
        self.assertEqual(vt._decode_grouped_values_strict("", allows_empty_cell=True), [])
        for bad in ("", "a||b", "a|", "a\\", "a\\x", "a b", "a\tb"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                vt._decode_grouped_values_strict(bad)
        with self.assertRaises(ValueError):
            vt.encode_grouped_values(["a\nb"])

    def test_parse_extra_ngrams(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "e.tsv")
            with open(p, "w", encoding="utf-8") as f:
                f.write("reading\tvalue\tcontext\tprob\tsource\nㄅ\t測\t\t-3.0004\tadjustCount\nㄅ\t測\t我\t-2\tsupport=1\nㄅ\t測\t我\t-1.5\tsupport=2\n")
            self.assertEqual(vt.parse_extra_ngrams(p, 3), {("ㄅ", "測", ()): -3.0, ("ㄅ", "測", ("我",)): -1.5})
            with open(p, "w", encoding="utf-8") as f:
                f.write("ㄅ\t測\t我\n")
            with self.assertRaises(ValueError):
                vt.parse_extra_ngrams(p, 3)

    def test_override_lines(self):
        block = ["@-5\tchsA\tA|B", "@-6\t\tC", ">7\tA|C", "A\t-4\t6\tX"]
        out, existed = vt.override_unigram_lines(block, "B", -6.0, 3)
        self.assertTrue(existed)
        self.assertEqual(out[:2], ["@-5\tchsA\tA", "@-6\t\tC|B"])
        out, existed = vt.override_unigram_lines(block, "Z", -5.5, 3)
        self.assertFalse(existed)
        self.assertEqual(out[1], "@-5.5\t\tZ")
        same, existed = vt.override_unigram_lines(block, "C", -6.0, 3)
        self.assertEqual((same, existed), (block, True))
        out, n_over, n_add = vt.override_contextual_lines(block, "A", {("X",): -1.0, ("Y", "Z"): -2.0}, 3)
        self.assertEqual((n_over, n_add), (1, 1))
        self.assertEqual(ctx_entries(out), {("A", ("X",)): -1.0, ("A", ("Y", "Z")): -2.0})
        self.assertIn(">7\tA|C", out)

    def test_validator_accepts_and_rejects(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.txtMap")
            open(p, "w", encoding="utf-8").write(FIXTURE)
            self.assertEqual(vt.validate_textmap_file(p, check_sorted=False), (7, 3))
            for name, old, new in [
                ("entry count", "ENTRY_COUNT\t7", "ENTRY_COUNT\t8"),
                ("score above ceiling", "@-6\t\tC", "@1\t\tC"),
                ("empty grouped token", "@-6\t\tC", "@-6\t\tC||D"),
                ("unknown escape", "@-6\t\tC", "@-6\t\tC\\x"),
                ("bad key map range", "ㄆ\t5\t1", "ㄆ\t9\t1"),
                ("missing version", "VERSION\t2.0", "VERSION\t1.0"),
                ("raw space in cell", "@-6\t\tC", "@-6\t\tC D"),
            ]:
                bad = os.path.join(d, "bad.txtMap")
                open(bad, "w", encoding="utf-8").write(FIXTURE.replace(old, new, 1))
                with self.assertRaises(ValueError, msg=name):
                    vt.validate_textmap_file(bad, check_sorted=False)
            open(p, "wb").write(FIXTURE.replace("\n", "\r\n").encode())
            with self.assertRaises(ValueError):
                vt.validate_textmap_file(p, check_sorted=False)


class SimplifiedTests(unittest.TestCase):
    def test_t2s_longest_phrase_then_character_then_as_is(self):
        t2s = vt.make_t2s({"頭髮": "头发", "髮廊": "发廊", "發財": "发财", "發財神": "发財神"}, {"髮": "发", "頭": "头", "發": "发", "臺": "台"})
        self.assertEqual(t2s("頭髮廊"), "头发廊")    # left to right: 頭髮 wins at position 0, so 髮廊 never gets its turn
        self.assertEqual(t2s("髮廊"), "发廊")
        self.assertEqual(t2s("發財神"), "发財神")    # the 3-character key beats the 2-character one
        self.assertEqual(t2s("發髮"), "发发")        # no phrase: character table
        self.assertEqual(t2s("臺灣"), "台灣")        # 灣 has no row: kept
        self.assertEqual(t2s(""), "")

    def test_opencc_file_takes_first_value_and_skips_comments(self):
        with tempfile.TemporaryDirectory() as d:
            folder, shas = make_opencc(os.path.realpath(d))
            with mock.patch.dict(vt.OPENCC_SHA256, shas):
                phrases, chars = vt.load_opencc(folder)
        self.assertEqual((phrases, chars), ({"世界線": "世界线"}, {"綫": "线", "線": "线", "牀": "床", "佔": "占"}))

    def test_mirror_check_accepts_c_and_d_lines_of_type_5(self):
        t2s = vt.make_t2s({}, {"佔": "占", "線": "线", "牀": "床"})
        good = ["@-3\t占\t占|佔", "占\t-1\t5\t他", "!占\t5\t-1\t他\t-2\t床|他", "占\t-1\t6\t他", "!佔\t6\t-1\t他\t-2\t牀|他", "_NORM\t1\t2"]
        vt.check_simplified_mirror("k", good, t2s)
        for name, block in [("empty chs cell", ["@-3\t\x07\t占"]), ("chs cell not the conversion", ["@-3\t占\t佔|線"]),
                            ("missing typeID 5", good[:1] + good[3:]), ("extra typeID 5", good + ["占\t-1\t5\t她"])]:
            with self.assertRaises(ValueError, msg=name):
                vt.check_simplified_mirror("k", block, t2s)

    def test_build_writes_the_simplified_column_the_mirror_lines_and_the_vendor_meta_line(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.realpath(d)
            root = make_root(base)
            folder, shas = make_opencc(base)
            plain = os.path.join(base, "plain.txtMap")
            vt_plain = build_in_process(root, plain, folder, shas)
            out = os.path.join(base, "chs.txtMap")
            stats = build_in_process(root, out, folder, shas, "--simplified", "--vendor", VENDOR)
            m = read_map(out)
            self.assertEqual(list(m)[0], "_VENDOR")  # "_" sorts before every bopomofo key
            self.assertEqual(m["_VENDOR"], [f"{VENDOR}\t0\t2"])
            self.assertEqual(m["ㄊㄚ"], ["@-2\t他\t他"])
            # two Traditional spellings at different scores that convert to the same Simplified word each keep their own line
            self.assertEqual(m["ㄑㄧˇ-ㄔㄨㄤˊ"][:2], ["@-4.9\t起床\t起床", "@-7.2\t起床\t起牀"])
            self.assertEqual(m["ㄕˋ-ㄐㄧㄝˋ-ㄒㄧㄢˋ"], ["@-7.17\t世界线\t世界綫|世界線"])  # same score: one Simplified token after dedupe
            # typeID 5 lines come after the B lines and before the typeID 6 lines; 佔 and 占 both become 占 and keep the higher score
            self.assertEqual(m["ㄓㄢˋ"], ["@-3\t占\t占", "@-3.5\t占\t佔", "占\t-1.156\t5\t他", "佔\t-1.612\t6\t他", "占\t-1.156\t6\t他"])
            self.assertEqual((stats["chs_contextual_entries"], stats["chs_contextual_lines"]), (3, 3))
            self.assertEqual(stats["contextual_total_entries"], 4)
            self.assertTrue(stats["validated"])
            self.assertEqual(stats["entry_count"], vt_plain["entry_count"] + 4)  # 1 vendor line + 3 typeID 5 lines
            self.assertEqual(stats["key_count"], vt_plain["key_count"] + 1)
            # without the two flags the output is the same file minus the Simplified column, the typeID 5 lines and the meta line
            def strip(line):
                p = line.split("\t")
                return "\t".join([p[0], "\x07", p[2]]) if line.startswith("@") else line
            plain_map = read_map(plain)
            self.assertEqual(vt_plain["chs_contextual_entries"], 0)
            self.assertEqual({k: [strip(ln) for ln in v if "\t5\t" not in ln] for k, v in m.items() if k != "_VENDOR"}, plain_map)
            self.assertTrue(all(ln.split("\t")[1] == "\x07" for v in plain_map.values() for ln in v if ln.startswith("@")))
            # the checker enforces the mirror rules on the finished file, and rejects a file that breaks them
            self.assertEqual(vt.validate_textmap_file(out, t2s=vt.make_t2s(*self._tables())), (stats["entry_count"], stats["key_count"]))
            text = open(out, encoding="utf-8").read()
            for old, new in [("占\t-1.156\t5\t他", "占\t-1.156\t5\t她"), ("@-2\t他\t他", "@-2\t\x07\t他")]:
                bad = os.path.join(base, "bad.txtMap")
                open(bad, "w", encoding="utf-8").write(text.replace(old, new, 1))
                with self.assertRaises(ValueError, msg=old):
                    vt.validate_textmap_file(bad, t2s=vt.make_t2s(*self._tables()))
            # build_stats: the two OpenCC files are inputs, no absolute path anywhere, the vendor is a flag
            sidecar = json.load(open(out + ".build_stats.json", encoding="utf-8"))
            for s in strings(sidecar):
                self.assertFalse(os.path.isabs(s), f"absolute path in build_stats: {s}")
            files = {f["path"]: f["sha256"] for f in sidecar["inputs"]["files"]}
            self.assertEqual({k: v for k, v in files.items() if k.startswith("TS")}, shas)
            self.assertEqual((sidecar["flags"]["vendor"], sidecar["flags"]["simplified"], sidecar["flags"]["opencc_dir"]), (VENDOR, True, "opencc"))

    @staticmethod
    def _tables():
        return ({"世界線": "世界线"}, {"綫": "线", "線": "线", "牀": "床", "佔": "占"})

    def test_collapsed_chs_entries_keep_the_best_score_and_convert_the_context_and_type_5_d_lines_mirror_type_6(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.realpath(d)
            root = make_root(base)
            folder, shas = make_opencc(base)
            extra = os.path.join(base, "extra.tsv")
            with open(extra, "w", encoding="utf-8") as f:   # 起牀 comes first and scores lower; both become 起床 in the context 世界线
                f.write("reading\tvalue\tcontext\tprob\tsource\n"
                        "ㄑㄧˇ-ㄔㄨㄤˊ\t起牀\t世界線\t-5\tx\nㄑㄧˇ-ㄔㄨㄤˊ\t起床\t世界線\t-2\tx\nㄑㄧˇ-ㄔㄨㄤˊ\t起床\t他\t-3\tx\n")
            out = os.path.join(base, "o.txtMap")
            build_in_process(root, out, folder, shas, "--simplified", "--extra-ngrams", extra)
            block = read_map(out)["ㄑㄧˇ-ㄔㄨㄤˊ"]
            d5 = [ln for ln in block if ln.startswith("!") and ln.split("\t")[1] == "5"]
            d6 = [ln for ln in block if ln.startswith("!") and ln.split("\t")[1] == "6"]
            self.assertTrue(d5 and d6)
            t2s = vt.make_t2s(*self._tables())
            self.assertIn("!起床\t5\t-2\t世界线\t-3\t他", d5)          # highest score kept, context converted
            self.assertIn("!起床\t6\t-2\t世界線\t-3\t他", d6)
            self.assertEqual(sorted(d5), sorted(t2s(ln).replace("\t6\t", "\t5\t", 1) for ln in d6 if ln.startswith("!起床")))
            self.assertFalse(any("\t-5\t" in ln for ln in d5))

    def test_vendor_alone_adds_only_the_meta_line(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.realpath(d)
            root = make_root(base)
            folder, shas = make_opencc(base)
            build_in_process(root, os.path.join(base, "a.txtMap"), folder, shas)
            stats = build_in_process(root, os.path.join(base, "b.txtMap"), folder, shas, "--vendor", VENDOR)
            a, b = read_map(os.path.join(base, "a.txtMap")), read_map(os.path.join(base, "b.txtMap"))
            self.assertTrue(stats["validated"])
            self.assertEqual(stats["chs_contextual_entries"], 0)
            self.assertEqual(b.pop("_VENDOR"), [f"{VENDOR}\t0\t2"])
            self.assertEqual(a, b)

    def test_command_line_refusals_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.realpath(d)
            root = make_root(base)
            folder, _ = make_opencc(base)   # synthetic tables: right format, but not the real sha256
            out = os.path.join(base, "o.txtMap")
            common = ["--shanjie-root", root, "--out-txtmap", out, *BUILD_FLAGS]
            r = run_tool(*common, "--simplified", "--opencc-dir", folder, ok=False)
            self.assertEqual(r.returncode, 2)
            self.assertIn(vt.OPENCC_SHA256["TSPhrases.txt"], r.stderr)  # names the expected hash
            self.assertIn(folder, r.stderr)                              # and where it looked
            empty = os.path.join(base, "empty")
            os.makedirs(empty)
            r = run_tool(*common, "--simplified", "--opencc-dir", empty, ok=False)
            self.assertEqual(r.returncode, 2)
            self.assertIn("not found", r.stderr)
            r = run_tool(*common, "--vendor", "a\tb", ok=False)
            self.assertEqual(r.returncode, 2)
            r = run_tool(*common, "--vendor", "", ok=False)
            self.assertEqual(r.returncode, 2)
            base_map = os.path.join(base, "base.txtMap")
            run_tool(*common[:2], "--out-txtmap", base_map, *BUILD_FLAGS)
            for flag in (["--simplified", "--opencc-dir", folder], ["--vendor", VENDOR]):
                r = run_tool("--shanjie-root", root, "--base-txtmap", base_map, "--out-txtmap", out, *flag, ok=False)
                self.assertEqual(r.returncode, 2)
                self.assertIn("--base-txtmap", r.stderr)
            self.assertFalse(os.path.exists(out))


class ThirdPartyTablesAndBuildMeta(unittest.TestCase):
    """vChewing 5.1.0: only tables 2, 5, 6 are provided (the rest fall back to the built-in dictionary); --build-meta writes the build meta lines."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = os.path.realpath(self.tmp.name)
        self.root = make_root(self.base)
        self.git, self.sha, self.ct = git_repo(self.root)
        self.folder, self.shas = make_opencc(self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, name, *extra):
        out = os.path.join(self.base, name)
        stats = build_in_process(self.root, out, self.folder, self.shas, *extra)
        return out, stats

    def test_build_meta_writes_the_two_lines_and_keys_and_rebuilds_byte_identical(self):
        out, stats = self.build("a.txtMap", "--simplified", "--vendor", VENDOR, "--build-meta")
        m = read_map(out)
        self.assertEqual(list(m)[:3], ["_BUILD_TIMESTAMP", "_COMMIT_HASH", "_VENDOR"])
        self.assertEqual(self.ct, FIXED_COMMIT_SECONDS)
        self.assertEqual(m["_BUILD_TIMESTAMP"], [f"_BUILD_TIMESTAMP\t{FIXED_COMMIT_SECONDS}\t2"])
        self.assertEqual(m["_COMMIT_HASH"], [f"{self.sha}\t0\t2"])
        self.assertEqual(m["_VENDOR"], [f"{VENDOR}\t0\t2"])
        self.assertEqual(len(self.sha), 40)
        self.assertTrue(stats["validated"])
        self.assertNotIn("_NORM", m)
        self.assertEqual(stats["meta_lines"], {"_VENDOR": f"{VENDOR}\t0\t2", "_BUILD_TIMESTAMP": f"_BUILD_TIMESTAMP\t{FIXED_COMMIT_SECONDS}\t2",
                                               "_COMMIT_HASH": f"{self.sha}\t0\t2"})
        time.sleep(1.1)   # the rebuild lands in a later second
        again, _ = self.build("b.txtMap", "--simplified", "--vendor", VENDOR, "--build-meta", "--force")
        self.assertEqual(open(out, "rb").read(), open(again, "rb").read())

    def test_the_build_runs_the_third_party_table_check(self):
        with mock.patch.object(vt, "check_third_party_tables") as chk:
            self.build("c.txtMap")
        chk.assert_called_once()

    def test_without_build_meta_there_are_no_build_meta_keys(self):
        out, _ = self.build("n.txtMap")
        self.assertFalse(any(k.startswith("_") for k in read_map(out)))

    def test_build_meta_refuses_uncommitted_tracked_changes_but_not_untracked_files(self):
        with open(os.path.join(self.root, "untracked.txt"), "w") as f:
            f.write("x")
        out, _ = self.build("u.txtMap", "--build-meta")
        self.assertIn("_COMMIT_HASH", read_map(out))
        with open(os.path.join(self.root, "data", "lexicon", "overlay-add.tsv"), "w") as f:
            f.write("\n")
        r = run_tool("--shanjie-root", self.root, "--out-txtmap", os.path.join(self.base, "d.txtMap"), *BUILD_FLAGS, "--build-meta", ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("uncommitted", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.base, "d.txtMap")))

    def test_build_meta_needs_a_git_work_tree_root(self):
        with tempfile.TemporaryDirectory() as d:
            plain = make_root(os.path.realpath(d))
            r = run_tool("--shanjie-root", plain, "--out-txtmap", os.path.join(self.base, "p.txtMap"), *BUILD_FLAGS, "--build-meta", ok=False)
            self.assertEqual(r.returncode, 2)
            self.assertIn("git work tree", r.stderr)

    def test_build_meta_with_base_txtmap_exits_2(self):
        base_map = write_lines(os.path.join(self.base, "base.txtMap"), FIXTURE.split("\n"))
        r = run_tool("--shanjie-root", self.root, "--base-txtmap", base_map, "--out-txtmap", os.path.join(self.base, "o.txtMap"), "--build-meta", ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--base-txtmap", r.stderr)

    def test_base_mode_keeps_the_factory_tables_and_exits_0(self):
        # the three-table check is for the Shanjie build only: a factory-shaped file with a type-A 7 line and DEFAULT_PROB_7 passes through
        base_map = write_lines(os.path.join(self.base, "base.txtMap"), FIXTURE.split("\n"))
        out = os.path.join(self.base, "o.txtMap")
        run_tool("--shanjie-root", self.root, "--base-txtmap", base_map, "--out-txtmap", out)
        text = open(out, encoding="utf-8").read()
        self.assertIn(">7\tA|C", text)
        self.assertIn("DEFAULT_PROB_7\t-11", text)

    def test_third_party_check_rejects_other_tables_and_default_prob_headers(self):
        out, _ = self.build("t.txtMap", "--simplified")
        vt.check_third_party_tables(out)   # a real build passes
        lines = open(out, encoding="utf-8").read().split("\n")
        vi = lines.index("#PRAGMA:VANGUARD_HOMA_LEXICON_VALUES")
        for name, bad in [("A4", ">4\tA"), ("A7", ">7\tA"), ("C7", "A\t-4\t7\tX"), ("C8", "A\t-4\t8"), ("D9", "!B\t9\t-3\tY\t-3.5\tZ")]:
            path = write_lines(os.path.join(self.base, f"bad-{name}.txtMap"), lines[:vi + 1] + [bad] + lines[vi + 1:])
            with self.assertRaises(SystemExit, msg=name) as cm:
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    vt.check_third_party_tables(path)
            self.assertEqual(cm.exception.code, 2)
            self.assertIn("typeID", err.getvalue())
        path = write_lines(os.path.join(self.base, "bad-header.txtMap"), lines[:vi] + ["DEFAULT_PROB_7\t-11"] + lines[vi:])
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                vt.check_third_party_tables(path)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("DEFAULT_PROB_7", err.getvalue())


HEADER = ["#PRAGMA:VANGUARD_HOMA_LEXICON_HEADER", "VERSION\t2.0", "TYPE\tTYPING", "READING_SEPARATOR\t-", "ENTRY_COUNT\t7", "KEY_COUNT\t3",
          "DEFAULT_PROB_7\t-11", "#PRAGMA:VANGUARD_HOMA_LEXICON_VALUES"]
# a small stand-in for a vChewing factory file: every category it has is one the --simplified --vendor --build-meta output has or FACTORY_ABSENT_OK allows
FACTORY_OK = HEADER + ["@-5\tA\tB", ">7\tA|C", "X\t-1\t8\tY", "A\t-4\t5\tX", "A\t-4\t6\tX", "_NORM\t1000\t2", "_BUILD_TIMESTAMP\t1\t2",
                       "#PRAGMA:VANGUARD_HOMA_LEXICON_KEY_LINE_MAP", "ㄅ\t0\t5", "_NORM\t5\t1", "_BUILD_TIMESTAMP\t6\t1", ""]
# the same shape with a Simplified-free, meta-free body: what a plain (no --simplified, no --vendor) output can satisfy
FACTORY_PLAIN = HEADER[:5] + ["KEY_COUNT\t1", "#PRAGMA:VANGUARD_HOMA_LEXICON_VALUES", "@-5\t\x07\tB", ">7\tA", "A\t-4\t6\tX",
                              "#PRAGMA:VANGUARD_HOMA_LEXICON_KEY_LINE_MAP", "ㄅ\t0\t3", ""]
SPEC = os.path.join(ROOT, "docs", "spec", "vanguard-textmap.md")


def write_lines(path, lines):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    return path


class FactoryCompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = os.path.realpath(cls.tmp.name)
        root = make_root(cls.base)
        folder, shas = make_opencc(cls.base)
        cls.ours = os.path.join(cls.base, "ours.txtMap")
        git_repo(root)
        build_in_process(root, cls.ours, folder, shas, "--simplified", "--vendor", VENDOR, "--build-meta")
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def missing(self, lines):
        table, missing = vt.compare_factory(write_lines(os.path.join(self.base, "f.txtMap"), lines), self.ours)
        return table, missing

    def test_count_categories_covers_every_kind(self):
        lines = HEADER + ["@-5\tA|B\tA", "@-6\t\x07\tC", "@-7\tD\t", ">7\tA", ">7\tB", ">11\tC", "A\t-4\t5\tX", "B\t-4\t8", "!B\t6\t-3\tY\t-3.5\tZ|W",
                          "!B\t5\t-3\tY\t-3.5\tZ|W", "_NORM\t1\t2", "#PRAGMA:VANGUARD_HOMA_LEXICON_KEY_LINE_MAP", "_NORM\t0\t1", "ㄅ\t1\t1", ""]
        got = vt.count_categories(write_lines(os.path.join(self.base, "c.txtMap"), lines), "t")
        self.assertEqual(got, {"header key VERSION": 1, "header key TYPE": 1, "header key READING_SEPARATOR": 1, "header key DEFAULT_PROB_7": 1,
                               "B Simplified cell non-empty": 2, "B Traditional cell non-empty": 2, "A typeID 7": 2, "A typeID 11": 1,
                               "C typeID 5": 1, "C typeID 8": 1, "D typeID 6": 1, "D typeID 5": 1, "C typeID 2": 1, "meta key _NORM": 1})

    def test_1_everything_the_factory_has_is_present_or_allowed(self):
        table, missing = self.missing(FACTORY_OK)
        self.assertEqual(missing, [])
        self.assertIn("meta key _NORM", table)
        self.assertIn("absent, allowed", table)

    def test_2_category_the_output_lacks_and_the_list_does_not_allow_fails(self):
        table, missing = self.missing(FACTORY_OK[:9] + [">99\tX"] + FACTORY_OK[9:])
        self.assertEqual(missing, ["A typeID 99"])
        self.assertRegex(table, r"A typeID 99 +1 +0 +MISSING")

    def test_3_the_same_category_passes_when_it_is_allowed(self):
        with mock.patch.object(vt, "FACTORY_ABSENT_OK", vt.FACTORY_ABSENT_OK | {"A typeID 99"}):
            self.assertEqual(self.missing(FACTORY_OK[:9] + [">99\tX"] + FACTORY_OK[9:])[1], [])

    def test_4_an_extra_header_key_in_the_factory_file_fails(self):
        self.assertEqual(self.missing(FACTORY_OK[:2] + ["NEWKEY\t1"] + FACTORY_OK[2:])[1], ["header key NEWKEY"])

    def test_5_a_line_that_fits_no_category_exits_2_with_line_number_and_head(self):
        junk = "x" * 50
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            self.missing(FACTORY_OK[:9] + [junk] + FACTORY_OK[9:])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("line 10", err.getvalue())
        self.assertIn("x" * 40, err.getvalue())
        self.assertNotIn("x" * 41, err.getvalue())
        for bad in (FACTORY_OK[:2] + ["#PRAGMA:SOMETHING_ELSE"] + FACTORY_OK[2:], FACTORY_OK[:-4] + ["ㄅ\t0"] + FACTORY_OK[-3:]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                self.missing(bad)
            self.assertEqual(cm.exception.code, 2)

    def exits_2(self, lines, text):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            self.missing(lines)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn(text, err.getvalue())

    def test_6_a_factory_file_without_any_b_line_exits_2(self):
        self.exits_2([], "no unigram")
        self.exits_2([ln for ln in FACTORY_OK if not ln.startswith("@")], "no unigram")

    def test_7_a_blank_line_in_the_header_or_key_map_exits_2_with_its_line_number(self):
        self.exits_2(FACTORY_OK[:3] + [""] + FACTORY_OK[3:], "line 4 fits no category")
        self.exits_2(FACTORY_OK[:-2] + [""] + FACTORY_OK[-2:], f"line {len(FACTORY_OK) - 1} fits no category")
        self.exits_2(FACTORY_OK[:-1] + ["", ""], f"line {len(FACTORY_OK)} fits no category")  # only the text after the last newline is not a line

    def test_8_the_tools_own_output_still_counts(self):
        counts = vt.count_categories(self.ours, "output")
        self.assertGreater(counts["B Traditional cell non-empty"], 0)
        self.assertGreater(counts["meta key _VENDOR"], 0)
        self.assertGreater(counts["meta key _BUILD_TIMESTAMP"], 0)

    def test_command_line_prints_the_table_and_sets_the_exit_code(self):
        os.makedirs(os.path.join(self.base, "cli"))
        root = make_root(os.path.join(self.base, "cli"))
        common = ["--shanjie-root", root, *BUILD_FLAGS]
        ok = run_tool(*common, "--out-txtmap", os.path.join(self.base, "cli-ok.txtMap"),
                      "--compare-factory", write_lines(os.path.join(self.base, "fp.txtMap"), FACTORY_PLAIN))
        self.assertRegex(ok.stdout, r"B Traditional cell non-empty +1 +\d+ +ok")
        bad = run_tool(*common, "--out-txtmap", os.path.join(self.base, "cli-bad.txtMap"),
                       "--compare-factory", write_lines(os.path.join(self.base, "fo.txtMap"), FACTORY_OK), ok=False)
        self.assertEqual(bad.returncode, 2)
        self.assertRegex(bad.stdout, r"B Simplified cell non-empty +1 +0 +MISSING")
        self.assertIn("B Simplified cell non-empty", bad.stderr)
        gone = run_tool(*common, "--out-txtmap", os.path.join(self.base, "cli-gone.txtMap"), "--compare-factory", os.path.join(self.base, "nope"), ok=False)
        self.assertEqual(gone.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.base, "cli-gone.txtMap")))  # refused before building

    def test_spec_allow_list_is_the_same_set_as_the_constant(self):
        lines = open(SPEC, encoding="utf-8").read().split("\n")
        start = next(i for i, ln in enumerate(lines) if ln.startswith("### ") and "FACTORY_ABSENT_OK" in ln)
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
        items = [re.fullmatch(r"- `([^`]+)`：(.+)", ln) for ln in lines[start + 1:end] if ln.startswith("- ")]
        self.assertTrue(all(items), "every bullet in the allow-list section is '- `label`：reason'")
        labels = [m.group(1) for m in items]
        self.assertEqual(len(labels), len(set(labels)), "no entry listed twice")
        self.assertEqual(set(labels), set(vt.FACTORY_ABSENT_OK))
        self.assertTrue(all(m.group(2).strip() for m in items))


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = os.path.realpath(cls.tmp.name)
        cls.root = make_root(base)
        cls.comp = make_companion(base)
        cls.out = os.path.join(base, "out")
        os.makedirs(cls.out)
        cls.out1 = os.path.join(cls.out, "one")
        os.makedirs(cls.out1)
        cls.map1 = os.path.join(cls.out1, "A.txtMap")
        cls.stats1 = json.loads(run_tool("--shanjie-root", cls.root, "--out-txtmap", cls.map1, *BUILD_FLAGS).stdout)
        cls.dict2 = os.path.join(cls.out, "dict")
        cls.stats2 = json.loads(run_tool("--shanjie-root", cls.root, "--out-dict", cls.dict2, "--companion-dir", cls.comp, *BUILD_FLAGS).stdout)
        # base mode: the built map plus one unigram override, one new key and one contextual override
        cls.extra = os.path.join(cls.out, "extra.tsv")
        with open(cls.extra, "w", encoding="utf-8") as f:
            f.write("reading\tvalue\tcontext\tprob\tsource\nㄊㄚ\t他\t\t-1\tadjustCount\nㄅㄅ\t測\t\t-4\taddUnigram\nㄓㄢˋ\t占\t他\t-2\tsupport=1\n")
        cls.map3 = os.path.join(cls.out, "B.txtMap")
        cls.stats3 = json.loads(run_tool("--shanjie-root", cls.root, "--base-txtmap", cls.map1, "--extra-ngrams", cls.extra,
                                         "--out-txtmap", cls.map3, "--validate").stdout)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_out_txtmap_builds_validates_and_has_contextual_lines(self):
        self.assertTrue(self.stats1["validated"])
        self.assertGreaterEqual(self.stats1["contextual_lines"], 1)
        self.assertEqual(vt.validate_textmap_file(self.map1), (self.stats1["entry_count"], self.stats1["key_count"]))
        # only the output and its sidecar are written: no companion files
        self.assertEqual(sorted(os.listdir(self.out1)), ["A.txtMap", "A.txtMap.build_stats.json"])

    def test_out_dict_copies_companions_and_validates(self):
        self.assertEqual(sorted(os.listdir(self.dict2)), ["VanguardFactoryDict4Typing.txtMap", "build_stats.json", "data-paired-punctuations.txt",
                                                          "data-punctuations.txt", "phonic_table_Z.txt", "template-a.txt"])
        self.assertEqual(vt.validate_textmap_file(os.path.join(self.dict2, "VanguardFactoryDict4Typing.txtMap")),
                         (self.stats2["entry_count"], self.stats2["key_count"]))
        self.assertEqual(open(self.map1, "rb").read(), open(os.path.join(self.dict2, "VanguardFactoryDict4Typing.txtMap"), "rb").read())

    def test_base_mode_applies_overrides_and_keeps_other_lines(self):
        self.assertTrue(self.stats3["validated"])
        a, b = read_map(self.map1), read_map(self.map3)
        self.assertEqual(cht_unigrams(b["ㄊㄚ"])["他"], -1.0)
        self.assertEqual(cht_unigrams(b["ㄅㄅ"]), {"測": -4.0})
        self.assertEqual(ctx_entries(b["ㄓㄢˋ"])[("占", ("他",))], -2.0)
        self.assertEqual(set(b) - set(a), {"ㄅㄅ"})
        self.assertTrue(all(b[k] == a[k] for k in a if k not in ("ㄊㄚ", "ㄓㄢˋ")))
        self.assertEqual((self.stats3["extra_unigram_overridden"], self.stats3["extra_unigram_added"], self.stats3["extra_ngram_entries"]), (1, 1, 1))

    def test_base_mode_fixture_identity_and_overrides(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, "fixture.txtMap")
            open(base, "w", encoding="utf-8").write(FIXTURE)
            tsv = os.path.join(d, "fx.tsv")
            with open(tsv, "w", encoding="utf-8") as f:
                f.write("reading\tvalue\tcontext\tprob\tsource\n")
                for r, v, c, p in EXTRAS:
                    f.write(f"{r}\t{v}\t{c}\t{p}\tt\n")
            out0 = os.path.join(d, "fx0.txtMap")
            run_tool("--shanjie-root", self.root, "--base-txtmap", base, "--out-txtmap", out0)
            self.assertEqual(open(out0, "rb").read(), open(base, "rb").read())
            out1 = os.path.join(d, "fx1.txtMap")
            stats = json.loads(run_tool("--shanjie-root", self.root, "--base-txtmap", base, "--extra-ngrams", tsv, "--out-txtmap", out1).stdout)
            self.assertEqual((stats["extra_unigram_overridden"], stats["extra_unigram_added"]), (2, 3))
            self.assertEqual((stats["extra_ngram_overridden"], stats["extra_ngram_added"]), (2, 2))
            m = read_map(out1)
            self.assertEqual(list(m), ["ㄅ", "ㄆ", "ㄇ", "ㄈ"])
            self.assertEqual(cht_unigrams(m["ㄅ"]), {"A": -2.0, "B": -5.0, "C": -6.0, "E": -6.0, "F": -5.5})
            self.assertEqual(m["ㄅ"][:4], ["@-2\t\tA", "@-5\tchsA\tB", "@-5.5\t\tF", "@-6\t\tC|E"])
            self.assertIn(">7\tA|C", m["ㄅ"])
            self.assertEqual(m["ㄈ"], ["@-9\t\tG"])
            self.assertEqual(ctx_entries(m["ㄅ"]), {("A", ("X",)): -1.0, ("B", ("Y",)): -2.0, ("B", ("Z", "W")): -3.5,
                                                    ("B", ("Q",)): -4.0, ("C", ("P", "Q")): -3.0})

    def test_build_stats_has_no_absolute_path_and_records_inputs(self):
        sidecars = [self.map1 + ".build_stats.json", os.path.join(self.dict2, "build_stats.json"), self.map3 + ".build_stats.json"]
        for path, stdout_stats in zip(sidecars, (self.stats1, self.stats2, self.stats3)):
            stats = json.load(open(path, encoding="utf-8"))
            self.assertEqual({k: v for k, v in stats.items() if k != "flags"}, stdout_stats)
            for s in strings(stats):
                self.assertFalse(os.path.isabs(s), f"{path}: absolute path in build_stats: {s}")
            self.assertEqual(stats["flags"]["shanjie_root"], ".")
            self.assertIsNone(stats["inputs"]["git_commit"])  # the synthetic root is not a git work tree
            self.assertIsNone(stats["inputs"]["git_dirty"])
        s1 = json.load(open(sidecars[0], encoding="utf-8"))
        self.assertEqual(s1["txtmap_path"], "A.txtMap")
        self.assertEqual(s1["flags"]["out_txtmap"], "A.txtMap")
        self.assertEqual(json.load(open(sidecars[1], encoding="utf-8"))["flags"]["companion_dir"], "companion")
        s3 = json.load(open(sidecars[2], encoding="utf-8"))
        self.assertEqual((s3["base_txtmap"], s3["flags"]["base_txtmap"], s3["flags"]["extra_ngrams"]), ("A.txtMap", "A.txtMap", "extra.tsv"))
        files = {f["path"]: f["sha256"] for f in s1["inputs"]["files"]}
        self.assertEqual(sorted(files), sorted(os.path.join("data", sub) for sub in (
            "lexicon/mcbpmf-data.txt", "lexicon/overlay-add.tsv", "lexicon/sandhi-add.tsv", "lexicon/names-add.tsv",
            "lexicon/variant-order.tsv", "lm/bigram.sjlm", "lm/kn.sjkn")))
        import hashlib
        for rel, sha in files.items():
            self.assertEqual(sha, hashlib.sha256(open(os.path.join(self.root, rel), "rb").read()).hexdigest(), rel)
        self.assertEqual({f["path"] for f in s3["inputs"]["files"]}, {"A.txtMap", "extra.tsv"})  # outside the root: the bare file name

    def test_writing_inside_the_work_tree_is_refused(self):
        inside = os.path.join(self.root, "out.txtMap")
        r = run_tool("--shanjie-root", self.root, "--out-txtmap", inside, *BUILD_FLAGS, ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("outside the --shanjie-root work tree", r.stderr)
        self.assertFalse(os.path.exists(inside))
        r = run_tool("--shanjie-root", self.root, "--out-dict", os.path.join(self.root, "dict"), "--companion-dir", self.comp, *BUILD_FLAGS, ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "dict")))
        # a symlink into the work tree does not get around it
        link = os.path.join(self.out, "link")
        os.symlink(self.root, link)
        r = run_tool("--shanjie-root", self.root, "--out-txtmap", os.path.join(link, "x.txtMap"), *BUILD_FLAGS, ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.root, "x.txtMap")))
        os.unlink(link)

    def test_case_variant_of_the_work_tree_is_refused(self):
        head, tail = os.path.split(self.root)
        variant = os.path.join(head, tail.swapcase())
        if variant == self.root or not os.path.exists(variant):
            self.skipTest("case-sensitive file system: a case variant is another path")
        r = run_tool("--shanjie-root", self.root, "--out-txtmap", os.path.join(variant, "c.txtMap"), *BUILD_FLAGS, ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("outside the --shanjie-root work tree", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.root, "c.txtMap")))

    def test_out_dict_needs_companion_dir(self):
        r = run_tool("--shanjie-root", self.root, "--out-dict", os.path.join(self.out, "nodict"), *BUILD_FLAGS, ok=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--companion-dir", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.out, "nodict")))

    def test_existing_output_needs_force(self):
        r = run_tool("--shanjie-root", self.root, "--out-txtmap", self.map1, *BUILD_FLAGS, ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--force", r.stderr)

    def test_default_root_is_this_repo(self):
        self.assertEqual(vt.make_parser().parse_args(["--out-txtmap", "x"]).shanjie_root, ROOT)


class GitInputs(unittest.TestCase):
    def test_commit_and_dirty_flag(self):
        with tempfile.TemporaryDirectory() as d:
            base = os.path.realpath(d)
            root = make_root(base)
            git = ["git", "-C", root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
            subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
            subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
            subprocess.run([*git, "commit", "-q", "--no-verify", "-m", "x"], check=True, capture_output=True)
            head = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            out = os.path.join(base, "o")
            os.makedirs(out)
            args = ["--shanjie-root", root, *BUILD_FLAGS]
            run_tool(*args, "--out-txtmap", os.path.join(out, "c.txtMap"))
            inputs = json.load(open(os.path.join(out, "c.txtMap.build_stats.json"), encoding="utf-8"))["inputs"]
            self.assertEqual((inputs["git_commit"], inputs["git_dirty"]), (head, False))
            with open(os.path.join(root, "data", "lexicon", "overlay-add.tsv"), "w") as f:
                f.write("\n")
            run_tool(*args, "--out-txtmap", os.path.join(out, "d.txtMap"))
            inputs = json.load(open(os.path.join(out, "d.txtMap.build_stats.json"), encoding="utf-8"))["inputs"]
            self.assertEqual((inputs["git_commit"], inputs["git_dirty"]), (head, True))


if __name__ == "__main__":
    unittest.main()
