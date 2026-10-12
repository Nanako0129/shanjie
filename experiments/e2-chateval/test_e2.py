"""E2 builders (docs/contracts/e2-chat-eval.md section 5 item 1): synthetic fixtures only, no network, no data files, no private data.
Run: python3 -W ignore -m unittest experiments/e2-chateval/test_e2.py -v   (from the repo root)
"""
import contextlib
import csv
import io
import json
import os
import random
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_chatclause as bc  # noqa: E402
import build_lytune as bl  # noqa: E402
import build_wikitypo as bw  # noqa: E402
import e2common as c  # noqa: E402


def fake_readings(sentences):
    return {s: ("ok", " ".join("ㄅ" for _ in s)) for s in set(sentences)}


def ts(day, hm, month="2026-03"):
    """A Discord timestamp in UTC; 12:00 UTC is 20:00 in UTC+8, the same day."""
    return f"{month}-{day:02d} {hm}:00.000000+00:00"


def package(td, channels):
    """channels: {folder: [(id, timestamp, contents)]} -> a Discord messages folder (JSON for c-folders, CSV for the others)."""
    root = os.path.join(td, "Messages")
    os.makedirs(root)
    json.dump({"1": "x"}, open(os.path.join(root, "index.json"), "w"))
    for ch, msgs in channels.items():
        d = os.path.join(root, ch)
        os.makedirs(d)
        if ch.startswith("c"):
            json.dump([{"ID": i, "Timestamp": t, "Contents": x, "Attachments": ""} for i, t, x in msgs], open(os.path.join(d, "messages.json"), "w", encoding="utf-8"))
        else:
            with open(os.path.join(d, "messages.csv"), "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["ID", "Timestamp", "Contents", "Attachments"])
                w.writerows([(i, t, x, "") for i, t, x in msgs])
    return root


def clauses_of(work):
    return [(m, s, x, g) for m, s, x, g in bc.read_clauses(work)]


class Context(unittest.TestCase):
    """Contract 2.1: the previous segment of the same message, else the channel's own previous message within 10 minutes."""

    def prepare(self, channels, **kw):
        td = tempfile.mkdtemp()
        root = package(td, channels)
        counts = bc.prepare(root, kw.get("threads"), kw.get("exclude", []), os.path.join(td, "w"))
        return os.path.join(td, "w"), counts

    def test_three_segment_message_with_a_previous_message_5_and_15_minutes_earlier(self):
        work, _ = self.prepare({
            # channel 1: a message 20 minutes before M, a message 5 minutes before M, then M with three segments
            "c1": [("1", ts(5, "11:55"), "更早的一句話"), ("2", ts(5, "12:10"), "五分鐘前說了第一句，又說第二句"),
                   ("3", ts(5, "12:15"), "甲段落，乙段落，丙段落")],
            # channel 2: the only earlier message is 15 minutes before
            "c2": [("4", ts(5, "12:00"), "十五分鐘前的話"), ("5", ts(5, "12:15"), "丁段落，戊段落，己段落")]})
        rows = {s: x for _m, _src, x, s in clauses_of(work)}
        self.assertEqual((rows["甲段落"], rows["乙段落"], rows["丙段落"]), ("又說第二句", "甲段落", "乙段落"))  # first: 5 minutes back; then the message's own
        self.assertEqual((rows["丁段落"], rows["戊段落"], rows["己段落"]), ("", "丁段落", "戊段落"))          # 15 minutes back: blank
        self.assertEqual(rows["五分鐘前說了第一句"], "")           # 15 minutes after the 11:55 one: no context either

    def test_context_is_the_last_segment_of_the_previous_message_not_the_first(self):
        work, _ = self.prepare({"c1": [("1", ts(5, "12:00"), "先說這個，再說那個"), ("2", ts(5, "12:03"), "接著回答")]})
        self.assertEqual({s: x for _m, _src, x, s in clauses_of(work)}["接著回答"], "再說那個")

    def test_threads_first_segment_blank_and_later_segments_chain(self):
        td = tempfile.mkdtemp()
        tf = os.path.join(td, "t.jsonl")
        open(tf, "w", encoding="utf-8").write("\n".join(json.dumps(r, ensure_ascii=False) for r in [
            {"url": "https://t/1", "time": "2026-03-05T12:00:00+00:00", "text": "貼文一，貼文二"},
            {"url": "https://t/2", "time": "2026-03-05T12:01:00+00:00", "text": "第二篇文"},      # one minute later: still blank (no cross-post context)
            {"url": "https://t/3", "text": "沒有時間"},
            {"url": "https://t/4", "time": "2026-03-05T12:02:00+00:00", "text": ""}]) + "\n")
        counts = bc.prepare(None, tf, [], os.path.join(td, "w"))
        rows = {s: x for _m, src, x, s in clauses_of(os.path.join(td, "w")) if src == "threads"}
        self.assertEqual(rows, {"貼文一": "", "貼文二": "貼文一", "第二篇文": ""})
        self.assertEqual(counts["threads"]["_dropped_no_url_time_text"], 2)

    def test_csv_and_json_packages_both_read_and_other_folders_ignored(self):
        work, counts = self.prepare({"c1": [("1", ts(5, "12:00"), "甲乙丙丁")], "2": [("2", ts(6, "12:00"), "戊己庚辛")]})
        self.assertEqual(sorted(s for _m, _s, _x, s in clauses_of(work)), ["戊己庚辛", "甲乙丙丁"])


class Exclusions(unittest.TestCase):
    def test_counts_per_step_and_month_grouping_in_utc_plus_8(self):
        td = tempfile.mkdtemp()
        ex = os.path.join(td, "other.txt")
        open(ex, "w", encoding="utf-8").write("|別的集合的句子|ㄅ\n")
        root = package(td, {"c1": [
            ("1", ts(31, "15:59", "2026-08"), "八月底的話"),          # 23:59 on 08-31 in UTC+8
            ("2", ts(31, "16:00", "2026-08"), "九月初的話"),          # 00:00 on 09-01 in UTC+8
            ("3", "2026-09-02 10:00:00.000000+00:00", "有網址 https://x.y 的話"),
            ("4", "2026-09-02 10:01:00.000000+00:00", "有程式碼```print(1)```的話"),
            ("5", "2026-09-02 10:02:00.000000+00:00", "a 好 b"),      # no Han segment of 2+
            ("6", "2026-09-02 10:03:00.000000+00:00", "別的集合的句子"),
            ("7", "2026-09-02 10:04:00.000000+00:00", "重複的句子，別的集合的句子"),
            ("8", "2026-09-03 10:05:00.000000+00:00", "重複的句子"),
            ("9", "2026-10-03 10:06:00.000000+00:00", "重複的句子")]})   # another month: not a duplicate
        counts = bc.prepare(root, None, [ex], os.path.join(td, "w"))["discord"]
        self.assertEqual(sorted(counts), ["2026-08", "2026-09", "2026-10"])
        s = counts["2026-09"]
        self.assertEqual({k: s[k] for k in ("messages", "url", "code", "no_segment", "segments", "overlap", "duplicate", "mechanical")},
                         {"messages": 7, "url": 1, "code": 1, "no_segment": 1, "segments": 5, "overlap": 2, "duplicate": 1, "mechanical": 2})
        self.assertEqual(counts["2026-08"]["mechanical"], 1)
        self.assertEqual(counts["2026-10"]["mechanical"], 1)
        got = [(m, s) for m, _src, _x, s in clauses_of(os.path.join(td, "w"))]
        self.assertEqual(got, [("2026-08", "八月底的話"), ("2026-09", "九月初的話"), ("2026-09", "重複的句子"), ("2026-10", "重複的句子")])


class Months(unittest.TestCase):
    def by(self, **kw):
        return {k.replace("m", "2026-", 1): v for k, v in kw.items()}

    def test_judge_runs_back_from_2026_09_until_2950(self):
        by = {"2026-09": 1000, "2026-08": 1000, "2026-07": 1000, "2026-06": 400, "2026-05": 4000, "2025-12": 700}
        judge, pool = bc.choose_judge_months(by)
        self.assertEqual((judge, pool), (["2026-07", "2026-08", "2026-09"], ["2025-12", "2026-05", "2026-06"]))
        self.assertEqual(bc.choose_judge_months({"2026-09": 2950, "2026-08": 9000})[0], ["2026-09"])
        # a month without data is passed and counts 0; the year boundary is crossed
        judge, pool = bc.choose_judge_months({"2026-09": 1500, "2026-06": 1000, "2026-02": 300, "2025-12": 100, "2025-11": 3000, "2025-01": 50})
        self.assertEqual(judge, ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"])
        self.assertEqual(pool, ["2025-01"])

    def test_2026_10_and_later_never_in_the_judge_period_or_the_pool(self):
        by = {"2026-10": 9000, "2026-11": 9000, "2026-09": 3000, "2026-08": 500}
        judge, pool = bc.choose_judge_months(by)
        self.assertEqual(judge, ["2026-09"])
        self.assertEqual(pool, ["2026-08"])
        # all the rows before 2026-10 together under 2,500: STOP although later months are large
        with self.assertRaises(SystemExit) as e:
            bc.choose_judge_months({"2026-10": 9000, "2026-09": 2000, "2026-08": 400})
        self.assertIn("2400 mechanical rows before 2026-10", str(e.exception.code))

    def test_a_month_already_in_the_tuning_pool_is_never_a_judge_month(self):
        by = {"2026-09": 1000, "2026-08": 1000, "2026-07": 1000, "2026-06": 1000}
        with self.assertRaises(SystemExit) as e:
            bc.choose_judge_months(by, pool_months=("2026-08", "2026-06"))
        self.assertIn("2026-08", str(e.exception.code))
        self.assertEqual(bc.choose_judge_months(by, pool_months=("2026-05",))[0], ["2026-07", "2026-08", "2026-09"])

    def test_running_out_of_months_stops(self):
        with self.assertRaises(SystemExit):
            bc.choose_judge_months({"2026-09": 1500, "2026-08": 1100})      # 2,600 usable but < 2,950 judge rows: stop


class Freeze(unittest.TestCase):
    """prepare -> split -> freeze tune -> freeze judge -> emit, with small thresholds and a fake reading tool."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        # months 2026-05 .. 2026-09, three clauses each (distinct text), one channel
        msgs = []
        for i, mon in enumerate(["2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]):
            for j in range(3):
                msgs.append((f"{i}{j}", f"{mon}-10 {10 + j}:00:00.000000+00:00", "一二三四五六七八九十"[i] + "甲乙丙丁戊己"[j] + "字"))
        self.root = package(self.td, {"c1": msgs})
        self.work = os.path.join(self.td, "w")
        patches = [mock.patch.object(bc, "JUDGE_MIN", 6), mock.patch.object(bc, "MIN_USABLE", 6), mock.patch.object(bc, "TUNE_MAX", 4)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        bc.prepare(self.root, None, [], self.work)
        with contextlib.redirect_stdout(io.StringIO()):
            bc.main(["split", "--work", self.work])

    def test_split_is_fixed_by_counts_and_never_changes(self):
        sp = json.load(open(os.path.join(self.work, "split.json")))
        self.assertEqual((sp["judge_months"], sp["pool_months"]), (["2026-08", "2026-09"], ["2026-05", "2026-06", "2026-07"]))
        with contextlib.redirect_stdout(io.StringIO()):
            bc.main(["split", "--work", self.work])                                   # the same counts: fine
        with mock.patch.object(bc, "JUDGE_MIN", 3):
            with self.assertRaises(SystemExit) as e:
                bc.main(["split", "--work", self.work])                               # another split: refused
        self.assertIn("never changed", str(e.exception.code))

    def test_tune_sample_is_seeded_min_of_cap_and_pool_and_judge_takes_everything(self):
        tune = bc.freeze(self.work, "tune", readings=fake_readings)
        pool = [(x, s) for m, _src, x, s in bc.read_clauses(self.work) if m in ("2026-05", "2026-06", "2026-07")]
        self.assertEqual(tune["rows"], 4)                                              # min(TUNE_MAX 4, pool 9)
        want = random.Random(20261012).sample(pool, 4)
        got = [tuple(l.split("|")[:2]) for l in open(os.path.join(self.work, "tune.rows.txt"), encoding="utf-8").read().split("\n") if l]
        self.assertEqual(got, want)
        judge = bc.freeze(self.work, "judge", readings=fake_readings)
        self.assertEqual(judge["rows"], 6)
        self.assertEqual(judge["months"], ["2026-08", "2026-09"])
        self.assertFalse({s for _x, s in want} & {l.split("|")[1] for l in open(os.path.join(self.work, "judge.rows.txt"), encoding="utf-8") if l.strip()})

    def test_judge_needs_the_tune_freeze_first(self):
        with self.assertRaises(SystemExit) as e:
            bc.freeze(self.work, "judge", readings=fake_readings)
        self.assertIn("tuning period first", str(e.exception.code))

    def test_frozen_hash_mismatch_refuses_to_emit(self):
        bc.freeze(self.work, "tune", readings=fake_readings)
        out = os.path.join(self.td, "out.txt")
        bc.emit(self.work, "tune", out)
        self.assertEqual(open(out, encoding="utf-8").read(), open(os.path.join(self.work, "tune.rows.txt"), encoding="utf-8").read())
        self.assertEqual(open(os.path.join(self.work, "tune.sha256")).read().split()[0], c.sha256_file(out))
        os.remove(out)
        with open(os.path.join(self.work, "tune.rows.txt"), "a", encoding="utf-8") as f:     # a row edited after the freeze
            f.write("|多一列|ㄅ ㄅ ㄅ\n")
        with self.assertRaises(SystemExit) as e:
            bc.emit(self.work, "tune", out)
        self.assertIn("REFUSED", str(e.exception.code))
        self.assertFalse(os.path.exists(out))

    def test_a_frozen_period_is_not_rewritten_with_other_content(self):
        bc.freeze(self.work, "tune", readings=fake_readings)
        corr = os.path.join(self.td, "corr.tsv")
        first = open(os.path.join(self.work, "tune.rows.txt"), encoding="utf-8").read().split("|")[1]
        open(corr, "w", encoding="utf-8").write(f"{first}\t\n")                              # drop that row
        with self.assertRaises(SystemExit) as e:
            bc.freeze(self.work, "tune", corr, readings=fake_readings)
        self.assertIn("never rewritten", str(e.exception.code))

    def test_corrections_and_reading_status(self):
        rows = [("", "甲乙丙"), ("甲乙丙", "丁戊己"), ("", "庚辛壬"), ("", "癸子丑")]
        corr = {"甲乙丙": ("甲乙丁", None), "丁戊己": ("", None), "庚辛壬": ("庚辛壬", "ㄅ ㄆ ㄇ")}

        def readings(sents):
            return {s: {"庚辛壬": ("check", "ㄅ ㄅ ㄅ"), "癸子丑": ("unreadable", "")}.get(s, ("ok", "ㄅ")) for s in sents}
        frozen, draft, st = bc.build_period(rows, corr, readings)
        self.assertEqual(frozen, [("", "甲乙丁", "ㄅ"), ("", "庚辛壬", "ㄅ ㄆ ㄇ")])            # the confirmed reading keeps a CHECK row
        self.assertEqual((st["corrected"], st["dropped_by_correction"], st["dropped_check"], st["dropped_unreadable"], st["rows"]), (1, 1, 0, 1, 2))


class PublicBuilders(unittest.TestCase):
    def test_lytune_strips_labels_and_stamps_filters_and_is_reproducible(self):
        td = tempfile.mkdtemp()
        src = os.path.join(td, "ly")
        os.makedirs(os.path.join(src, "data", "csv"))
        text = ("陳委員玉珍：（15時16分）你知道我是哪裡來的委員嘛？\\n主席：請交通部王部長說明。王部長國材：好不好，沒有口語詞的一句。\\n"
                "陳委員玉珍：（15時20分）是不是這樣")
        p = os.path.join(src, "data", "csv", "1.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ivod_id", "transcript"])
            w.writerow(["1", text.replace("\\n", "\n")])
        man = {"revision": "r", "files": [{"path": "data/csv/1.csv", "sha256": c.sha256_file(p)}]}
        json.dump(man, open(os.path.join(src, "manifest.json"), "w"))
        outs = []
        for k in range(2):
            out = os.path.join(td, f"o{k}.txt")
            n = bl.run(src, out, fake_readings)
            outs.append(open(out, "rb").read())
        self.assertEqual(outs[0], outs[1])                                                    # byte reproducible
        rows = sorted(l.split("|")[:2] for l in outs[0].decode().split("\n") if l)
        sentences = [r[1] for r in rows]
        self.assertEqual(sorted(sentences), sorted(["你知道我是哪裡來的委員嘛", "好不好", "是不是這樣"]))
        self.assertTrue(all("委員玉珍" not in s and "王部長國材" not in s and "主席" not in s for r in rows for s in r))
        ctx = {r[1]: r[0] for r in rows}
        self.assertEqual(ctx["你知道我是哪裡來的委員嘛"], "")        # first segment of the speech
        self.assertEqual(ctx["好不好"], "請交通部王部長說明")
        self.assertEqual((n["speeches"], n["clauses_2_30_han"], n["clauses_colloquial"], n["rows"]), (1, 5, 3, 3))
        json.dump({**man, "files": [{"path": "data/csv/1.csv", "sha256": "0" * 64}]}, open(os.path.join(src, "manifest.json"), "w"))
        with self.assertRaises(SystemExit):
            bl.run(src, os.path.join(td, "bad.txt"), fake_readings)                           # CSV hash differs from the manifest

    def test_wikitypo_drops_the_rule_rows_keeps_same_reading_pairs_and_is_reproducible(self):
        td = tempfile.mkdtemp()
        # a context rule (lookahead) that rewrites 藉 as 籍: its pairs are dropped; the rule without a lookaround is kept
        wiki = ('<Typo word="*籍" find="(?:美|英)藉(?!水)" replace="$1籍"/>\n<Typo word="籍貫" find="藉(貫|贯)" replace="籍$1"/>\n')
        wf = os.path.join(td, "awb.wikitext")
        open(wf, "w", encoding="utf-8").write(wiki)
        pairs = ["wrong\tright\tsource",
                 f"美藉\t美籍\t{bw.AWB}", f"英藉\t英籍\t{bw.AWB}",
                 f"藉貫\t籍貫\t{bw.AWB}",                        # kept (rule has no lookaround)
                 f"痕蹟\t痕跡\t{bw.AWB}", f"痕蹟\t痕跡\tother",     # the same pair twice: one row
                 f"全然\t全部\t{bw.AWB}",                          # different reading
                 f"生澀\t生涩\t{bw.AWB}",                          # unreadable right word
                 f"沒有\t\t{bw.AWB}",                              # empty right word
                 f"藉口\t籍口\tother"]                              # 藉 but not from the AWB rule: kept
        pf = os.path.join(td, "pairs.tsv")
        open(pf, "w", encoding="utf-8").write("\n".join(pairs) + "\n")
        json.dump({"pages": [{"title": bw.AWB, "file": "awb.wikitext", "sha256": c.sha256_file(wf)}]}, open(os.path.join(td, "manifest.json"), "w"))
        table = {"痕蹟": "ㄏ ㄐ", "痕跡": "ㄏ ㄐ", "全然": "ㄑ ㄖ", "全部": "ㄑ ㄅ", "藉貫": "ㄐ ㄍ", "籍貫": "ㄐ ㄍ", "藉口": "ㄐ ㄎ", "籍口": "ㄐ ㄎ"}
        rd = lambda ss: {s: ("ok", table[s]) if s in table else ("unreadable", "") for s in set(ss)}
        outs = []
        for k in range(2):
            out = os.path.join(td, f"o{k}.txt")
            n = bw.run(td, out, rd, check_hash=False)
            outs.append(open(out, "rb").read())
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0].decode(), "|籍貫|ㄐ ㄍ\n|痕跡|ㄏ ㄐ\n|籍口|ㄐ ㄎ\n")
        self.assertEqual((n["pairs"], n["after_empty_or_same"], n["dropped_jie_rule_rows"], n["distinct_pairs"], n["rows"]), (9, 8, 2, 5, 3))
        with self.assertRaises(SystemExit):
            bw.run(td, os.path.join(td, "x.txt"), rd)                                         # the pinned pairs.tsv hash does not match


if __name__ == "__main__":
    unittest.main()
