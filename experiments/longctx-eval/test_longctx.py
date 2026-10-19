"""Tests of the long-context evaluation builders: synthetic data only. Run from the repo root:
python3 -W ignore -m unittest discover -s experiments/longctx-eval -p 'test_*.py' -v
"""
import contextlib
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ctx64  # noqa: E402  (also puts e2-chateval on sys.path)
import chatctx  # noqa: E402
import longtext  # noqa: E402
import build_chatclause as bc  # noqa: E402
import e2common as c  # noqa: E402
from test_e2 import package, ts  # noqa: E402



def han(i, n=3):
    """A distinct n-character Han string for i."""
    return "".join(chr(0x4E00 + (i * n + k) * 7 % 20000) for k in range(n))


def ok_readings(sentences):
    return {s: ("ok", " ".join("ㄅ" for _ in s)) for s in set(sentences)}


def quiet(f, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return f(*a, **k)


class Normalize(unittest.TestCase):
    def test_order_and_cap(self):
        n = ctx64.normalize_ctx
        self.assertEqual(n("甲乙，丙 "), "甲乙，丙")
        self.assertEqual(n("甲|"), "甲")                 # | becomes a space, then the trailing run goes
        self.assertEqual(n("說#話\n好!!"), "說 話 好")
        self.assertNotIn("#", n("#標題 好的，"))          # a context that would start with # does not
        self.assertFalse(n("#標題 好的，").startswith("#"))
        self.assertEqual(n("甲" * 70 + "!!!"), "甲" * 64)  # the cap counts after the strip
        self.assertEqual(n("123 abc"), "")
        self.assertEqual(ctx64.segments_with_ctx("先說，再說。最後"), [("先說", ""), ("再說", "先說"), ("最後", "先說，再說")])

    def test_check_rows(self):
        td = tempfile.mkdtemp()
        p = os.path.join(td, "r.txt")

        def run(text, n):
            open(p, "w", encoding="utf-8", newline="").write(text)
            ctx64.check_rows(p, n)
        run("甲 乙|丙丁|ㄅㄆ\n戊己||ㄅ\n", 2)
        for text, n in (("a|b|c\n", 2), ("a|b\n", 1), ("a|b|c|d\n", 1), ("a#b|c|d\n", 1), ("#a|c|d\n", 1), ("a\rb|c|d\n", 1), ("a\tb|c|d\n", 1)):
            with self.assertRaises(SystemExit, msg=text):
                run(text, n)


def discord_fixture(td):
    return package(td, {
        "c1": [("1", ts(5, "12:00"), "先說這個，再說那個"), ("2", ts(5, "12:03"), "接著回答，好的"),
               ("3", ts(5, "12:04"), "看這個 https://x.com 網頁"), ("4", ts(5, "12:05"), "電話 0912-345-678 打給我"),
               ("5", ts(5, "12:06"), "```程式碼區塊```說明"), ("6", ts(5, "12:07"), "<@123456789012345678>你好嗎，再說那個"),
               ("7", ts(6, "12:00", "2026-04"), "再說那個，全新一句")],
        "c2": [("8", ts(5, "12:01"), "別的頻道，的話")]})


META = {"threads": ([("t", "1", dt.datetime(2026, 3, 5, 20, 0, tzinfo=bc.TZ), "串文一，串文二"),
                     ("t", "2", dt.datetime(2026, 3, 5, 20, 1, tzinfo=bc.TZ), "串文三")], {})}


class Superset(unittest.TestCase):
    def test_parity_with_prepare(self):
        td = tempfile.mkdtemp()
        root = discord_fixture(td)
        with mock.patch.object(bc, "meta_messages", lambda r, n: META):
            sup = chatctx.superset_rows(root, "meta", "me")
            ex = os.path.join(td, "ex.txt")
            open(ex, "w", encoding="utf-8").write("別的頻道\n")
            quiet(bc.prepare, root, "meta", "me", [ex], os.path.join(td, "w"))
        old = [(m, s_, x, s) for m, s_, x, s in bc.read_clauses(os.path.join(td, "w"))]
        self.assertEqual([r[:4] for r in sup if r[3] != "別的頻道"], old)
        self.assertEqual(len(sup), len(old) + 1)
        got = {(r[1], r[3]): r for r in sup}
        self.assertEqual(got[("discord", "接著回答")][2:], ("再說那個", "接著回答", ""))      # old: previous message; new: none
        self.assertEqual(got[("discord", "好的")][2:], ("接著回答", "好的", "接著回答"))
        self.assertEqual(got[("discord", "你好嗎")][4], "")                              # markup stripped, nothing before it
        self.assertEqual(got[("threads", "串文三")][4], "")
        self.assertEqual(got[("threads", "串文二")][4], "串文一")
        self.assertNotIn("說明", {r[3] for r in sup})                                    # code, url, phone messages are gone


def work_dir(td, n=24):
    """A frozen-style work dir: clauses.tsv from prepare over 2 pool months, split.json. Three segments per message, so the third
    segment's old context (the previous segment) differs from its new one (all the text before it)."""
    msgs = []
    for i in range(n):
        month = "2026-03" if i % 2 else "2026-04"
        msgs.append((str(i), ts(1 + i % 25, f"{10 + i % 10}:00", month), f"{han(3 * i)}，{han(3 * i + 1)}，{han(3 * i + 2)}"))
    msgs.append(("x", ts(3, "09:00", "2026-04"), f"{han(0)}，{han(1)}，{han(2)}"))      # the same sentences again in another message
    root = package(td, {"c1": msgs[:len(msgs) // 2], "c2": msgs[len(msgs) // 2:]})
    w = os.path.join(td, "w")
    quiet(bc.prepare, root, None, None, [], w)
    json.dump({"pool_months": ["2026-03", "2026-04"], "judge_months": ["2026-02"]}, open(os.path.join(w, "split.json"), "w"))
    return root, w


class Rebuild(unittest.TestCase):
    def setUp(self):
        self.patch = [mock.patch.object(bc, "TUNE_MAX", 30), mock.patch.object(bc, "CHATJUDGE_MAX", 15)]
        for p in self.patch:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patch])

    def test_replay_equals_period_rows(self):
        _root, w = work_dir(tempfile.mkdtemp())
        cl = bc.read_clauses(w)
        for period in ("tune", "chatjudge"):
            want, _pool = bc.period_rows(w, period)
            got = chatctx.replay(cl, {"2026-03", "2026-04"}, period)
            self.assertEqual([(r[2], r[3]) for r in got], want)
            self.assertEqual(len(want), 30 if period == "tune" else 15)

    def freeze_all(self, td):
        root, w = work_dir(td)
        sd = os.path.join(td, "sup")
        quiet(chatctx.cmd_superset, mock.Mock(discord=root, meta=None, self_name=None, out=sd))
        out, readings = {}, {}
        for period in ("tune", "chatjudge"):
            rows, _ = bc.period_rows(w, period)
            victim, deleted, changed = (rows[0][1] if period == "tune" else None), rows[1][1], rows[2][1]
            corr = os.path.join(td, f"{period}.corr.tsv")
            open(corr, "w", encoding="utf-8").write(f"{deleted}\t\n{changed}\t{han(900 + len(period))}\t{'ㄅ' * 3}\n")

            def rd(ss, victim=victim):
                return {s: ("check" if s == victim else "ok", "ㄅ ㄅ ㄅ") for s in set(ss)}
            readings[period] = rd
            quiet(bc.freeze, w, period, corr, False, rd)
            out[period] = os.path.join(td, f"{period}.ctx64.rows.txt")
        return w, sd, out, readings

    def test_rebuild_end_to_end(self):
        td = tempfile.mkdtemp()
        w, sd, out, readings = self.freeze_all(td)
        sup = {(m, s): (x, nx) for m, _src, x, s, nx in (l.rstrip("\n").split("\t") for l in open(os.path.join(sd, "superset.tsv"), encoding="utf-8"))}
        expect_new = {s: nx for (m, s), (_x, nx) in sup.items()}
        for period in ("tune", "chatjudge"):
            corr = os.path.join(td, f"{period}.corr.tsv")
            log = io.StringIO()
            with contextlib.redirect_stdout(log):
                chatctx.rebuild(w, sd, period, corr, out[period], readings[period])
            self.assertEqual(log.getvalue().count("input "), 4)             # clauses.tsv, split.json, frozen rows, corrections
            frozen = [l.split("|") for l in open(os.path.join(w, f"{period}.rows.txt"), encoding="utf-8").read().splitlines()]
            new = [l.split("|") for l in open(out[period], encoding="utf-8").read().splitlines()]
            self.assertEqual(len(frozen), len(new))
            self.assertIn(han(900 + len(period)), [f[1] for f in frozen])
            for f, n in zip(frozen, new):
                self.assertEqual(f[1:], n[1:])                      # sentence and reading untouched
            changed = 0
            for f, n in zip(frozen, new):
                if f[1] in expect_new:
                    self.assertEqual(n[0], expect_new[f[1]])        # the NEW context, not the old one
                    changed += n[0] != f[0]
            self.assertGreater(changed, 0)                          # some rows really have a different new context
        before = open(os.path.join(w, "tune.rows.txt")).read()
        self.assertEqual(open(os.path.join(w, "tune.rows.txt")).read(), before)
        # --out inside the frozen dir is refused
        with self.assertRaises(SystemExit) as cm:
            quiet(chatctx.rebuild, w, sd, "tune", os.path.join(td, "tune.corr.tsv"), os.path.join(w, "o.txt"), readings["tune"])
        self.assertIn("inside", str(cm.exception.code))
        # superset refuses a work dir that holds a clauses.tsv
        with self.assertRaises(SystemExit):
            quiet(chatctx.cmd_superset, mock.Mock(discord=None, meta=None, self_name=None, out=w))
        # an old context that differs in the superset stops the build, with the superset message
        p = os.path.join(sd, "superset.tsv")
        good = open(p, encoding="utf-8").read()
        lines = good.split("\n")
        parts = lines[0].split("\t"); parts[2] = "別的"; lines[0] = "\t".join(parts)
        open(p, "w", encoding="utf-8").write("\n".join(lines))
        with self.assertRaises(SystemExit) as cm:
            quiet(chatctx.rebuild, w, sd, "tune", os.path.join(td, "tune.corr.tsv"), out["tune"], readings["tune"])
        self.assertIn("superset", str(cm.exception.code))
        open(p, "w", encoding="utf-8").write(good)
        # a frozen file whose (old context, sentence) sequence differs from the replay stops with the sequence message
        fz = os.path.join(w, "tune.rows.txt")
        rows = open(fz, encoding="utf-8").read().split("\n")
        rows[0] = "別的前文" + rows[0][len(rows[0].split("|")[0]):]
        open(fz, "w", encoding="utf-8").write("\n".join(rows))
        with self.assertRaises(SystemExit) as cm:
            quiet(chatctx.rebuild, w, sd, "tune", os.path.join(td, "tune.corr.tsv"), out["tune"], readings["tune"])
        self.assertIn("sequence", str(cm.exception.code))

    def test_corrected_sentence_equal_to_another_rows_check_sentence(self):
        picked = [("m", "d", "", "在見"), ("m", "d", "好", "再見"), ("m", "d", "", "其他")]
        rd = lambda ss: {s: ("check" if s == "再見" else "ok", "ㄅ") for s in set(ss)}   # noqa: E731
        got = chatctx.rebuild_core(picked, {"在見": ("再見", "ㄗㄞˋ ㄐㄧㄢˋ")}, rd)
        self.assertEqual([(r[2], s, x) for r, s, x in got], [("", "再見", "ㄗㄞˋ ㄐㄧㄢˋ"), ("", "其他", "ㄅ")])   # the second row (CHECK) goes, the corrected one stays


class DiscordTune(unittest.TestCase):
    MSGS = ["甲乙丙，丁戊己", "丁戊己，庚辛壬", "甲乙丙", "說，好嗎"]

    def test_join(self):
        rows = [("", "甲乙丙"), ("甲乙丙", "丁戊己"), ("", "丁戊己"), ("丁戊己", "庚辛壬"), ("", "甲乙丙")]   # 甲乙丙 twice from '' is ambiguous
        new = quiet(chatctx.discordtune, self.MSGS, rows)
        self.assertEqual(new, ["", "甲乙丙", "", "丁戊己", ""])

    def test_ambiguous_row_follows_order(self):
        # the same (old context, sentence) twice in the source: the rows take the occurrences in order, each with its own text before it
        msgs = ["說，甲乙丙", "ok 說，甲乙丙"]
        new = quiet(chatctx.discordtune, msgs, [("說", "甲乙丙"), ("說", "甲乙丙")])
        self.assertEqual(new, ["說", "ok 說"])

    def test_checks_stop(self):
        with self.assertRaises(SystemExit):      # (a) sentence not found
            quiet(chatctx.discordtune, self.MSGS, [("", "沒有的句子")])
        with self.assertRaises(SystemExit):      # (b) unique (old, sentence) but the order-based search lands elsewhere
            quiet(chatctx.discordtune, self.MSGS, [("", "丁戊己")])
        with self.assertRaises(SystemExit):      # (a) rows out of message order
            quiet(chatctx.discordtune, self.MSGS, [("", "甲乙丙"), ("丁戊己", "庚辛壬"), ("甲乙丙", "丁戊己")])

    def test_markup_is_blanked_and_lone_cr_does_not_split(self):
        msgs = ["甲乙<@123456789012345678>丙丁，戊己"]
        new = quiet(chatctx.discordtune, msgs, [("", "甲乙"), ("甲乙", "丙丁"), ("丙丁", "戊己")])
        self.assertEqual(new[:2], ["", "甲乙"])
        self.assertEqual(new[2], "甲乙 丙丁")        # one space per markup, as in chatclause; the Han before it stays inside the window
        long = "甲" * 30 + "<a:" + "x" * 12 + ":123456789012345678>" + "乙" * 3 + "，丙丙丙"        # a 35-character custom emoji
        self.assertEqual(quiet(chatctx.discordtune, [long], [("", "甲" * 30), ("甲" * 30, "乙乙乙"), ("乙乙乙", "丙丙丙")])[2], "甲" * 30 + " 乙乙乙")
        td = tempfile.mkdtemp()
        m, r, o = (os.path.join(td, n) for n in ("m.txt", "r.txt", "o.txt"))
        open(m, "w", encoding="utf-8", newline="").write("甲乙丙\r丁戊己\n庚辛壬\n")        # a lone \r stays inside the first message
        open(r, "w", encoding="utf-8").write("|甲乙丙|ㄅ\n|庚辛壬|ㄅ\n")
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            chatctx.cmd_discordtune(mock.Mock(msgs=m, rows=r, out=o))
        self.assertIn("messages 2", log.getvalue())
        self.assertEqual(open(o, encoding="utf-8").read(), "|甲乙丙|ㄅ\n|庚辛壬|ㄅ\n")


def doc(i, tail="天地玄黃宇宙洪荒"):
    return (f"d{i}", [han(i + 1000, 18) + "，" + han(i + 5000, 4) + "。短句，" + tail])


def env_clean():
    return {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}


class Long(unittest.TestCase):
    def test_context_needs_16_han_not_16_characters(self):
        def n(para):
            return [s for _d, _x, s in longtext.rows_from_docs([("a", [para])])]
        self.assertEqual(n("甲" * 15 + "，天地人"), [])
        self.assertEqual(n("甲" * 16 + "，天地人"), ["天地人"])
        self.assertEqual(n("甲" * 6 + "ab12" + "乙" * 6 + "，丙丙丙"), [])      # 16 characters of context, 12 of them Han

    def test_sampling_caps_and_determinism(self):
        rows = [(f"d{i}", "x", f"s{i}_{j}") for i in range(1500) for j in range(5)]
        a, b = longtext.sample_rows(rows), longtext.sample_rows(rows)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 3000)
        self.assertEqual([rows.index(r) for r in a], sorted(rows.index(r) for r in a))
        small = longtext.sample_rows(rows[:20])
        self.assertEqual({d: sum(r[0] == d for r in small) for d in {r[0] for r in small}}, {"d0": 3, "d1": 3, "d2": 3, "d3": 3})

    def test_document_order_within_a_document_after_sampling(self):
        rows = [(f"d{i}", "x", f"s{i}_{j}") for i in range(100) for j in range(9)]       # under the 3,000 cap: only the per-document pick sorts
        for pick in (longtext.sample_rows(rows), longtext.rows_from_docs.__globals__["sample_rows"](rows, 7)):
            idx = [rows.index(r) for r in pick]
            self.assertEqual(idx, sorted(idx))
        docs = [("a", ["甲" * 16 + "，" + "，".join(han(k, 3) for k in range(8))])]
        got = [s for _d, _x, s in longtext.rows_from_docs(docs, 3)]
        self.assertEqual(len(got), 3)
        self.assertEqual(got, sorted(got, key=lambda s: [han(k, 3) for k in range(8)].index(s)))

    def make_docs_and_training(self, td):
        docs = [doc(i) for i in range(6)]
        r = longtext.rows_from_docs(docs)
        self.assertEqual(len(r), 6 * 3)      # per paragraph: the 18-Han run has no context; the 4-Han run, 短句 and the tail have
        # training files written by hand in the two real formats; d0's and d2's first sentences overlap, d3's first row is only reported
        d0c, d0s, d2c, d2s, d3c, d3s = han(1000, 18), han(5000, 4), han(1002, 18), han(5002, 4), han(1003, 18), han(5003, 4)
        pool = os.path.join(td, "pool.txt")          # L0: `context (punctuation kept)|clause|reading`
        open(pool, "w", encoding="utf-8").write(f"他說，{d0c[:5]}，{d0c[5:]}，|{d0s}|ㄅ ㄅ\n完全無關的，前文|無關句|ㄅ\n")
        wiki = os.path.join(td, "wiki.txt")          # M: one Han run per line
        # a paragraph split into runs at its punctuation, one run per line (what prep.py writes); the key spans two lines
        open(wiki, "w", encoding="utf-8").write(f"前面的話\n{d2c}\n{d2s}\n後面的話\n")
        report = os.path.join(td, "runs.txt")
        open(report, "w", encoding="utf-8").write(f"{d3c}，|{d3s}|ㄅ\n")
        return docs, r, pool, wiki, report

    def test_build_overlap_both_formats_readings_and_manifest(self):
        td = tempfile.mkdtemp()
        docs, r, pool, wiki, report = self.make_docs_and_training(td)
        victim = r[3][2]

        def readings(ss):
            return {s: ("check" if s == victim else "ok", "ㄅ") for s in set(ss)}
        out = os.path.join(td, "o.txt")
        with mock.patch.object(longtext, "MIN_ROWS", 1):
            quiet(longtext.build, docs, out, [pool], [pool, wiki], [report], 1, readings)
        m = json.load(open(out + ".manifest.json"))
        n = m["counts"]
        self.assertEqual((n["dropped_check"], n["overlap_dropped"], n["overlap_report"]), (1, {"pool.txt": 1, "wiki.txt": 1}, {"runs.txt": 1}))
        self.assertEqual(n["overlap_conversion"]["pool.txt"]["lines_with_pipe"], 2)
        self.assertEqual(n["overlap_conversion"]["wiki.txt"]["lines_without_pipe"], 4)
        self.assertIn("20261019", n["sampling"])
        self.assertEqual((n["after_readings"], n["after_overlap"], n["final_rows"]), (17, 15, 15))
        kept = open(out, encoding="utf-8").read().split("\n")[:-1]
        self.assertEqual(len(kept), 15)
        self.assertFalse(any(l.split("|")[1] == han(5000, 4) for l in kept))      # d0's first sentence went
        self.assertEqual(len(open(out + ".proofread.txt", encoding="utf-8").read().split("\n")) - 1, 2)
        self.assertEqual(m["input_sha256"][pool], c.sha256_file(pool))
        self.assertEqual(m["output_sha256"], c.sha256_file(out))
        # a control: a training file with nothing in common drops nothing
        none = os.path.join(td, "none.txt")
        open(none, "w", encoding="utf-8").write("無關，的|別句|ㄅ\n乙乙乙乙乙\n丙丙丙\n")
        with mock.patch.object(longtext, "MIN_ROWS", 1):
            quiet(longtext.build, docs, out, [none], [none], [], 1, ok_readings)
        self.assertEqual(json.load(open(out + ".manifest.json"))["counts"]["overlap_dropped"], {"none.txt": 0})

    def test_u2028_in_a_context_never_splits_a_row(self):
        td = tempfile.mkdtemp()
        docs = [("a", ["甲" * 10 + " " + "乙" * 10 + "\x0b\x0c\x85，丙丙丙。丁丁丁"])]
        out = os.path.join(td, "o.txt")
        with mock.patch.object(longtext, "MIN_ROWS", 1):
            quiet(longtext.build, docs, out, [], [], [], 1, ok_readings)
        lines = open(out, encoding="utf-8", newline="").read().split("\n")[:-1]
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(l.split("|")) == 3 for l in lines))
        self.assertIn(" ", lines[0])
        self.assertIn(open(out + ".proofread.txt", encoding="utf-8", newline="").read().split("\n")[0], lines)

    def test_scripts_run_without_the_test_path(self):
        script = os.path.join(HERE, "longtext.py")
        r = subprocess.run([sys.executable, script, "--help"], env=env_clean(), capture_output=True, text=True, cwd=tempfile.mkdtemp())
        self.assertEqual(r.returncode, 0, r.stderr)
        td = tempfile.mkdtemp()
        titles = os.path.join(td, "t.txt")
        open(titles, "w").write("")
        rep = os.path.join(td, "runs.txt")
        open(rep, "w", encoding="utf-8").write("甲乙丙\n")
        out = os.path.join(td, "o.txt")
        r = subprocess.run([sys.executable, script, "wikisource", "--fetched", td, "--titles", titles, "--out", out, "--procs", "1",
                            "--overlap-report", rep], env=env_clean(), capture_output=True, text=True, cwd=td)
        self.assertEqual(r.returncode, 1)
        self.assertIn("STOP, 0 rows", r.stderr)
        self.assertNotIn("ModuleNotFoundError", r.stderr)
        m = json.load(open(out + ".manifest.json"))          # buildcheck was imported: the overlap pass ran
        self.assertIn("runs.txt", m["counts"]["overlap_conversion"])
        self.assertIn("stopped", m)
        self.assertFalse(os.path.exists(out) or os.path.exists(out + ".proofread.txt"))      # a stop writes the manifest only

    @staticmethod
    def hplt_line(reg, url, pii=(), text="甲乙\n丙丁", **kw):
        d = {"text": text, "u": url, "pii": list(pii), "web-register": reg}
        d.update(kw)
        return json.dumps(d, ensure_ascii=False)

    def zst(self, lines):
        td = tempfile.mkdtemp()
        raw = os.path.join(td, "s.jsonl")
        open(raw, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        subprocess.run(["zstd", "-q", "-f", raw, "-o", raw + ".zst"], check=True)
        return raw + ".zst"

    @unittest.skipUnless(shutil.which("zstd"), "zstd binary missing")
    def test_hplt_filters(self):
        d = self.hplt_line
        lines = [d({"ID": .6, "NA": .1, "LY": .3}, "https://www.ptt.cc/bbs/x"),            # kept
                 d({"LY": .8, "ID": .1}, "https://a.com.tw/"),                              # register
                 d({"ID": .9}, "https://example.com/"),                                     # host
                 d({"ID": .9}, "https://foo.com.tw/", pii=[[0, 1]]),                        # pii
                 d({"NA": .9}, "https://notptt.cc/"),                                       # host: only ptt.cc and its subdomains
                 "not json", "[1, 2]",                                                      # bad json, not an object
                 d({"OP": .4, "av": .9, "ID": .1}, "https://x.pixnet.net/blog"),            # kept: lower-case label ignored
                 d({"OP": .4}, "https://x.gov.hk/"),                                        # host
                 d({"ID": .9}, "http://[bad/", text="甲乙"),                                 # unparseable URL: counted, no crash
                 d({"ID": .9}, "https://a.com.tw/", text=None)]                             # null text
        counts = {}
        got = list(longtext.hplt_docs([self.zst(lines)], counts))
        self.assertEqual([g[0] for g in got], ["s.jsonl.zst:0", "s.jsonl.zst:7"])
        self.assertEqual(got[0][1], ["甲乙", "丙丁"])
        self.assertEqual(counts, {"docs": 11, "bad_json": 1, "not_object": 1, "null_text": 1, "register": 1, "host": 3, "bad_url": 1,
                                  "pii": 1, "spam": 0, "passing": 2, "kept": 2})

    @unittest.skipUnless(shutil.which("zstd"), "zstd binary missing")
    def test_hplt_strict_records(self):
        d = {"text": "甲乙", "u": "https://a.com.tw/", "pii": [], "web-register": {"ID": 1.0}}
        for key in ("u", "text", "pii", "web-register"):
            bad = {k: v for k, v in d.items() if k != key}
            with self.assertRaises(SystemExit, msg=key):
                list(longtext.hplt_docs([self.zst([json.dumps(bad)])], {}))
        with self.assertRaises(SystemExit):      # web-register must be a dict
            list(longtext.hplt_docs([self.zst([json.dumps({**d, "web-register": [{"ID": 1.0}]})])], {}))

    @unittest.skipUnless(shutil.which("zstd"), "zstd binary missing")
    def test_hplt_document_selection_is_seeded_and_sized(self):
        lines = [self.hplt_line({"ID": 1.0}, "https://a.com.tw/", text=f"{han(i)}") for i in range(200)]
        shard = self.zst(lines)
        td = tempfile.mkdtemp()
        args = lambda **k: mock.Mock(shards=[shard], out=os.path.join(td, "o.txt"), overlap_drop=[], overlap_report=[], procs=1,  # noqa: E731
                                     doc_fraction=k.get("f"), target_docs=k.get("t", 50), gibberish_ref=False)
        ids = []
        for _ in range(2):
            with self.assertRaises(SystemExit):          # too few rows for the minimum: the manifest is still written
                quiet(longtext.cmd_hplt, args())
            m = json.load(open(os.path.join(td, "o.txt.manifest.json")))
            ids.append(m["counts"]["kept"])
        self.assertEqual(ids, [50, 50])
        self.assertIn("50 smallest of 200", m["counts"]["selection"])
        with self.assertRaises(SystemExit):
            quiet(longtext.cmd_hplt, args(f=0.5))
        kept = json.load(open(os.path.join(td, "o.txt.manifest.json")))["counts"]["kept"]
        self.assertEqual(kept, sum(longtext.doc_hash(f"s.jsonl.zst:{i}") < 0.5 for i in range(200)))

    @unittest.skipUnless(shutil.which("zstd"), "zstd binary missing")
    def test_hplt_shard_change_is_reported_before_the_row_stop(self):
        shard = self.zst([self.hplt_line({"ID": 1.0}, "https://a.com.tw/")])
        td = tempfile.mkdtemp()
        it = iter(["a", "a", "b", "b", "b"])
        with mock.patch.object(longtext, "sha256_stream", lambda p: next(it)):
            with self.assertRaises(SystemExit) as cm:
                quiet(longtext.cmd_hplt, mock.Mock(shards=[shard], out=os.path.join(td, "o.txt"), overlap_drop=[], overlap_report=[],
                                                   procs=1, doc_fraction=1.0, target_docs=1, gibberish_ref=False))
        self.assertIn("changed", str(cm.exception.code))
        self.assertFalse(os.path.exists(os.path.join(td, "o.txt")))

    @unittest.skipUnless(shutil.which("zstd"), "zstd binary missing")
    def test_hplt_spam_filter_threshold(self):
        def text(n):
            return "\n".join(["今天天氣很好"] + ["我要貸款"] * n)
        lines = [self.hplt_line({"ID": 1.0}, "https://a.com.tw/", text=text(k)) for k in (4, 5, 6)]
        lines.append(self.hplt_line({"ID": 1.0}, "https://a.com.tw/", text="週轉 周轉 借錢 借款 信貸"))                      # 5 different keywords
        lines.append(self.hplt_line({"ID": 1.0}, "https://a.com.tw/", text="小額小額小額小額"))                                # 4 matches
        counts = {}
        got = list(longtext.hplt_docs([self.zst(lines)], counts))
        self.assertEqual([g[0] for g in got], ["s.jsonl.zst:0", "s.jsonl.zst:4"])
        self.assertEqual((counts["spam"], counts["passing"]), (3, 2))
        self.assertEqual((longtext.SPAM_MIN, longtext.GIBBERISH_PERCENTILE), (5, 0.01))

    def test_gibberish_threshold_from_reference_and_row_drop(self):
        ref = [-3.0 + 2.0 * i / 199 for i in range(200)]      # -3 .. -1
        self.assertEqual(longtext.gibberish_threshold(ref), sorted(ref)[2])     # floor(0.01 * 200)
        self.assertEqual(longtext.gibberish_threshold(list(reversed(ref))), sorted(ref)[2])
        td = tempfile.mkdtemp()
        docs = [doc(i) for i in range(6)]
        victim = longtext.rows_from_docs(docs)[3][2]
        g = {"score": lambda pairs: [-9.0 if s == victim else -2.0 for s, _rd in pairs], "ref_scores": ref,
             "ref_counts": {"dev302": 150, "user-typing": 50}}
        out = os.path.join(td, "o.txt")
        with mock.patch.object(longtext, "MIN_ROWS", 1):
            quiet(longtext.build, docs, out, [], [], [], 1, ok_readings, None, g)
        n = json.load(open(out + ".manifest.json"))["counts"]
        self.assertEqual((n["gibberish"], n["after_gibberish"], n["final_rows"]), (1, 17, 17))
        self.assertEqual(n["gibberish_threshold"], sorted(ref)[2])
        self.assertEqual(n["gibberish_reference"]["counts"], {"dev302": 150, "user-typing": 50})
        self.assertNotIn(victim, [l.split("|")[1] for l in open(out, encoding="utf-8").read().splitlines()])
        g["score"] = lambda pairs: [sorted(ref)[2]] * len(pairs)      # exactly at the threshold stays
        with mock.patch.object(longtext, "MIN_ROWS", 1):
            quiet(longtext.build, docs, out, [], [], [], 1, ok_readings, None, g)
        self.assertEqual(json.load(open(out + ".manifest.json"))["counts"]["gibberish"], 0)

    def test_model_files_are_checked(self):
        with self.assertRaises(SystemExit) as cm:
            longtext.check_models(tempfile.mkdtemp())
        self.assertIn("missing", str(cm.exception.code))

    @unittest.skipUnless(os.path.isfile(os.path.join(c.ROOT, "data", "lm", "bigram.sjlm")), "data/lm model files absent in this checkout")
    def test_real_scorer_ranks_gibberish_below_text(self):
        g = longtext.load_gibberish(1)
        self.assertEqual(g["ref_counts"]["user-typing"], 76)
        good, bad = g["score"]([("今天天氣很好", "ㄐㄧㄣ ㄊㄧㄢ ㄊㄧㄢ ㄑㄧˋ ㄏㄣˇ ㄏㄠˇ"), ("騏鑫琥釭汲忒", "ㄑㄧˊ ㄒㄧㄣ ㄏㄨˋ ㄍㄤ ㄐㄧˊ ㄊㄜˋ")])
        thr = longtext.gibberish_threshold(g["ref_scores"])
        self.assertGreaterEqual(good, thr)
        self.assertLess(bad, thr)

    def test_wikisource_html_boundaries_and_noise(self):
        h = "<div class='x'><p>第一段<sup>[1]</sup>，續</p><p>第二<br/>段</p><style>p{}</style><h2>標題</h2>尾巴</div>"
        self.assertEqual(longtext.html_paragraphs(h), ["第一段[1]，續", "第二", "段", "標題", "尾巴"])
        self.assertEqual(longtext.html_paragraphs("<ul><li>甲</li><li>乙</li></ul><dl><dt>丙</dt><dd>丁</dd></dl>"), ["甲", "乙", "丙", "丁"])
        self.assertEqual(longtext.html_paragraphs("<span>前</span></style>後</script><p>末</p>"), ["前後", "末"])   # a stray end tag changes nothing
        self.assertEqual(longtext.html_paragraphs("<table><caption>表題</caption><tr><td>甲</td><th>乙</th></tr></table><p>正文</p>"), ["正文"])
        for cls in ("header", "licenseContainer", "noprint", "ws-noexport x"):
            self.assertEqual(longtext.html_paragraphs(f"<p>前</p><div class='{cls}'><div>內層</div>仍在外框</div><p>後</p>"), ["前", "後"], cls)
        self.assertEqual(longtext.html_paragraphs("<p>前<style>p{}</style>後，<span class='a noprint b'>注</span>末</p>"), ["前後，末"])   # inline noise keeps the paragraph whole
        self.assertEqual(longtext.html_paragraphs("<div class='subheader-text'>留下</div><div class='header x'>去掉</div>"), ["留下"])
        # td/th/caption outside a skipped table would still be block boundaries
        p = longtext.Paragraphs()
        p.feed("<span>甲</span><td>乙</td><th>丙</th><caption>丁</caption>")
        p.flush()
        self.assertEqual(p.paras, ["甲", "乙", "丙", "丁"])

    def test_wikisource_fetch_cache_and_titles(self):
        urls = []

        def get(url):
            urls.append(url)
            return {"parse": {"revid": 7, "text": "<p>簡體字</p>" if "variant=zh-tw" in url else "<p>别的</p>"}}
        r = longtext.fetch_title("某篇", get)
        self.assertEqual((r["revid"], r["paragraphs_tw"], r["paragraphs_plain"], r["conversion_differs"]), (7, ["簡體字"], ["别的"], True))
        self.assertEqual(sum("variant=zh-tw" in u for u in urls), 1)
        self.assertEqual(len(urls), 2)
        self.assertTrue(all("redirects=1" in u for u in urls))
        self.assertIn("github.com/Nanako0129/shanjie", longtext.UA)
        self.assertNotIn("@", longtext.UA)
        td = tempfile.mkdtemp()
        titles, cache = os.path.join(td, "t.txt"), os.path.join(td, "cache")
        open(titles, "w", encoding="utf-8").write("甲篇\n乙篇\n")
        with mock.patch.object(longtext, "http_get", get):
            quiet(longtext.cmd_fetch, mock.Mock(titles=titles, out=cache))
        self.assertEqual(sorted(os.listdir(cache)), sorted([longtext.cache_name("甲篇"), longtext.cache_name("乙篇")]))
        n_urls = len(urls)
        open(titles, "w", encoding="utf-8").write("丙篇\n乙篇\n")      # the list changed: the cache is by title, not by position
        with mock.patch.object(longtext, "http_get", get):
            quiet(longtext.cmd_fetch, mock.Mock(titles=titles, out=cache))
        self.assertEqual(len(urls) - n_urls, 2)                      # only 丙篇 was fetched
        with self.assertRaises(SystemExit):                           # 甲篇 is cached but not in the list; 丙篇 and 乙篇 are read
            open(titles, "w", encoding="utf-8").write("丁篇\n")
            quiet(longtext.cmd_wikisource, mock.Mock(fetched=cache, titles=titles, out=os.path.join(td, "o.txt"), overlap_drop=[], overlap_report=[], procs=1))
        open(titles, "w", encoding="utf-8").write("丙篇\n乙篇\n")
        with self.assertRaises(SystemExit):                           # too few rows: the manifest is still written
            quiet(longtext.cmd_wikisource, mock.Mock(fetched=cache, titles=titles, out=os.path.join(td, "o.txt"), overlap_drop=[], overlap_report=[], procs=1))
        m = json.load(open(os.path.join(td, "o.txt.manifest.json")))
        self.assertEqual(sorted(os.path.basename(p) for p in m["input_sha256"]), sorted([longtext.cache_name("丙篇"), longtext.cache_name("乙篇")]))


if __name__ == "__main__":
    unittest.main()
