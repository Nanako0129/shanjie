"""tools/vanguard_textmap.py (docs/spec/vanguard-textmap.md). Run: python3 -W ignore -m unittest tools/test_vanguard_textmap.py

The unit tests cover the grouped-cell codec, parse_extra_ngrams, the two override functions and the strict validator. The end-to-end tests run the
tool as a subprocess on a synthetic repo root built from the kn-tiny toy model (eval/golden/kn-tiny): three modes (--out-txtmap, --out-dict,
--base-txtmap), the path-free build_stats.json, the "inputs" record, and the refusal to write inside the repo work tree. Everything is written
under a throwaway folder; no real data, models or private files are read."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

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
