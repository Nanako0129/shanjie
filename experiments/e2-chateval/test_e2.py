"""E2 builders (docs/contracts/e2-chat-eval.md section 5 item 1): synthetic fixtures only, no network, no data files, no private data.
Run: python3 -W ignore -m unittest experiments/e2-chateval/test_e2.py -v   (from the repo root)
"""
import contextlib
import csv
import datetime as dt
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


def package(td, channels, types=None):
    """channels: {folder: [(id, timestamp, contents)]} -> a Discord messages folder (JSON for c-folders, CSV for the others)."""
    root = os.path.join(td, "Messages")
    os.makedirs(root)
    json.dump({"1": "x"}, open(os.path.join(root, "index.json"), "w"))
    for ch, msgs in channels.items():
        d = os.path.join(root, ch)
        os.makedirs(d)
        if (types or {}).get(ch, "GUILD_TEXT") is not None:
            json.dump({"id": ch, "type": (types or {}).get(ch, "GUILD_TEXT")}, open(os.path.join(d, "channel.json"), "w"))
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
        counts = bc.prepare(root, None, None, kw.get("exclude", []), os.path.join(td, "w"))
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

    def test_meta_export_exact_files(self):
        td = tempfile.mkdtemp()
        root = os.path.join(td, "export")
        me = "陳小明"

        def moji(x):        # Meta's escaping: the UTF-8 bytes as latin-1 characters
            return x.encode("utf-8").decode("latin-1")

        def mj(o):          # every string of a fixture, keys included
            if isinstance(o, str):
                return moji(o)
            if isinstance(o, list):
                return [mj(v) for v in o]
            return {moji(k): mj(v) for k, v in o.items()} if isinstance(o, dict) else o

        def put(rel, obj, raw=None):
            p = os.path.join(root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(raw if raw is not None else json.dumps(mj(obj), ensure_ascii=True))
            return p

        t0 = int(dt.datetime(2026, 3, 5, 12, 0, tzinfo=dt.timezone.utc).timestamp())
        pth = put("threads/threads_and_replies.json", {"text_post_app_text_posts": [
            {"media": [{"uri": "u", "creation_timestamp": t0, "media_metadata": {}, "title": "串文一，串文二", "cross_post_source": {}, "text_app_post": {}}]},
            # multi-media post: its own title and time win over media[0]
            {"title": "多媒體的貼文", "creation_timestamp": t0 + 60, "media": [{"creation_timestamp": t0 + 999, "title": "不用這個標題"}, {"title": "也不用"}]},
            {"media": [{"creation_timestamp": t0 + 120, "title": ""}]}]})
        put("facebook/your_posts__check_ins__photos_and_videos_1.json", [
            {"timestamp": t0, "attachments": [], "title": "某某更新了狀態", "data": [{"post": "臉書貼文，第二段"}, {"update_timestamp": 5}, {"post": "第二個欄位"}]},
            {"timestamp": t0 + 1, "title": "某某分享了連結", "data": [{"external_context": {"url": "x"}}]}])      # no post text: skipped
        put("facebook/your_posts__check_ins__photos_and_videos_2.json", [{"timestamp": t0 + 2, "title": "標題", "data": [{"post": "第二個檔案的貼文"}]}])
        put("facebook/posts_on_other_pages_and_profiles.json", [
            {"timestamp": t0, "media": [], "fbid": "1", "label_values": [{"label": "網址", "value": "不是這個"}, {"label": "訊息", "value": "別人的牆上寫的話"}]}])
        put("facebook/comments.json", {"comments_v2": [
            {"timestamp": t0, "title": "某某在別人的貼文留言", "data": [
                {"comment": {"timestamp": t0 + 5, "comment": "我的留言，第二句", "author": me}},
                {"comment": {"timestamp": t0 + 6, "comment": "別人的留言不讀", "author": "別人"}}]}]})
        # decoys: other files of the real export, valid JSON of a shape no reader here knows; the opened-files check below is the guard
        decoy = put("facebook/followers.json", None, raw='{"relationships_followers": [{"timestamp": 1, "post": "不能讀"}]}')
        put("threads/followers.json", None, raw='{"text_post_app_text_posts": [{"title": "不能讀的追蹤者", "creation_timestamp": 1}]}')
        put("threads/liked_threads.json", None, raw='{"text_post_app_text_posts": [{"title": "不能讀的按讚", "creation_timestamp": 1}]}')
        raw = open(pth, encoding="utf-8").read()
        self.assertIn("\\u00e4", raw)                                  # the fixture really is mojibake on disk
        self.assertTrue(all(ord(ch) < 128 for ch in raw))
        real_open, opened = open, []

        def spy(f, *a, **k):
            if isinstance(f, (str, os.PathLike)) and os.fspath(f).startswith(root):
                opened.append(os.path.relpath(os.fspath(f), root))
            return real_open(f, *a, **k)
        with mock.patch("builtins.open", spy):
            counts = bc.prepare(None, root, me, [], os.path.join(td, "w"))
        self.assertEqual(sorted(opened), sorted(["threads/threads_and_replies.json", "facebook/your_posts__check_ins__photos_and_videos_1.json",
                                                 "facebook/your_posts__check_ins__photos_and_videos_2.json",
                                                 "facebook/posts_on_other_pages_and_profiles.json", "facebook/comments.json"]))
        rows = {(src, s): x for _m, src, x, s in clauses_of(os.path.join(td, "w"))}
        self.assertEqual(rows, {("threads", "串文一"): "", ("threads", "串文二"): "串文一", ("threads", "多媒體的貼文"): "",
                                ("facebook_posts", "臉書貼文"): "", ("facebook_posts", "第二段"): "臉書貼文",
                                ("facebook_posts", "第二個欄位"): "第二段", ("facebook_posts", "第二個檔案的貼文"): "",
                                ("facebook_other_pages", "別人的牆上寫的話"): "",
                                ("facebook_comments", "我的留言"): "", ("facebook_comments", "第二句"): "我的留言"})
        self.assertEqual(counts["facebook_comments"]["_dropped_other_author"], 1)
        self.assertEqual(counts["threads"]["_dropped_no_text"], 1)
        self.assertEqual(counts["facebook_posts"]["_dropped_no_text"], 1)
        self.assertEqual({k: sum(v[m]["messages"] for m in v if not m.startswith("_")) for k, v in counts.items()},
                         {"threads": 2, "facebook_posts": 2, "facebook_other_pages": 1, "facebook_comments": 1})
        # the post-level time of the multi-media post is the one used: 2026-03 either way, so check the timestamp itself
        msgs = bc.meta_messages(root, me)["threads"][0]
        self.assertEqual(msgs[1][2].timestamp(), t0 + 60)
        # no export at all: nothing is read and nothing fails
        self.assertEqual({k: v for k, v in bc.meta_messages(os.path.join(td, "none"), me).items() if v[0]}, {})

    def test_unmojibake(self):
        self.assertEqual(bc.unmojibake("好"), "好")
        self.assertEqual(bc.unmojibake("好".encode("utf-8").decode("latin-1")), "好")
        self.assertEqual(bc.unmojibake("café"), "café")                    # latin-1 text that is not UTF-8 bytes stays
        self.assertEqual(bc.unmojibake("abc"), "abc")

    def test_csv_and_json_packages_both_read_and_other_folders_ignored(self):
        work, counts = self.prepare({"c1": [("1", ts(5, "12:00"), "甲乙丙丁")], "2": [("2", ts(6, "12:00"), "戊己庚辛")]})
        self.assertEqual(sorted(s for _m, _s, _x, s in clauses_of(work)), ["戊己庚辛", "甲乙丙丁"])


class Privacy(unittest.TestCase):
    DROP = {
        "email": "寫信到 someone@example.com 給我",
        "tw_id": "他的身分證是A123456789喔",
        "phone": "打給我 0912-345-678 好了",
        "phone2": "電話 (02) 2345 6789 謝謝",
        "digits6": "訂單編號123456完成了",
        "token_run": "金鑰是 abcd1234efgh5678ijkl9012 不要外傳",
        "token_sk": "用 sk-proj-abc 這個",
        "token_ghp": "ghp_xxxxxxxx 這個",
        "token_pat": "github_pat_11AAAA 試試",
        "token_slack": "xoxb-12-ab 換掉",
        "token_aws": "AKIA 開頭的那個",
        "token_google": "AIzaSy 的那個",
        "token_jwt": "eyJhbGciOiJIUzI1 看這個",
        "token_bearer": "Authorization 是 Bearer 後面接字",
        "token_kv": "設定 API_KEY=abc 之後",
        "token_kv2": "my_password = hunter",
        "token_pem": "-----BEGIN PRIVATE KEY----- 貼出來了",
    }
    KEEP = {
        "year": "二〇二六年是2026年的事",
        "short": "我有3個蘋果和10個橘子",
        "words": "internationalizationally 是很長的英文字",     # 20+ letters, no digits
        "hyphen_words": "這是一個well-known-and-widely-used-example 的說法",
    }

    def test_each_rule_drops_and_near_misses_stay(self):
        for name, text in self.DROP.items():
            self.assertIsNotNone(bc.private_rule(text), name)
        self.assertEqual({bc.private_rule(self.DROP[k]) for k in ("email", "tw_id", "phone", "digits6", "token_run")},
                         {"email", "tw_id", "phone", "digits6", "token"})
        for name, text in self.KEEP.items():
            self.assertIsNone(bc.private_rule(text), name)

    def test_prepare_drops_whole_messages_in_every_source_and_counts_per_rule(self):
        td = tempfile.mkdtemp()
        msgs = [(str(i), f"2026-03-05 12:{i:02d}:00.000000+00:00", t) for i, t in enumerate(list(self.DROP.values()) + list(self.KEEP.values()))]
        root = package(td, {"c1": msgs})
        exp = os.path.join(td, "export")
        os.makedirs(os.path.join(exp, "facebook"))
        t0 = 1772712000
        json.dump({"comments_v2": [{"timestamp": t0, "data": [{"comment": {"timestamp": t0, "comment": "留言裡有 a@b.co 信箱", "author": "我"}},
                                                                {"comment": {"timestamp": t0, "comment": "乾淨的留言內容", "author": "我"}}]}]},
                  open(os.path.join(exp, "facebook", "comments.json"), "w", encoding="utf-8"))
        counts = bc.prepare(root, exp, "我", [], os.path.join(td, "w"))
        self.assertEqual(counts["discord"]["_dropped_private_by_rule"], {"email": 1, "tw_id": 1, "phone": 2, "digits6": 1, "token": 12})
        self.assertEqual(counts["facebook_comments"]["_dropped_private_by_rule"], {"email": 1})
        rows = [s for _m, _src, _x, s in clauses_of(os.path.join(td, "w"))]
        self.assertIn("乾淨的留言內容", rows)
        self.assertTrue(any("蘋果" in s for s in rows))            # the near-misses survive (3 and 10 are not digits runs)
        self.assertFalse(any(k in s for s in rows for k in ("someone", "信箱", "身分證", "金鑰", "訂單編號")))     # the whole message is gone
        self.assertEqual(counts["discord"]["2026-03"]["messages"], len(self.KEEP))

    def test_discord_markup_is_stripped_before_the_private_filter(self):
        sf = "123456789012345678"
        markup = [f"<@{sf}>", f"<@!{sf}>", f"<@&{sf}>", f"<#{sf}>", f"<:你好嗎:{sf}>", f"<a:跳舞:{sf}>", "<t:1700000000:R>", "<t:1700000000>", "<id:customize>"]
        msgs = [(str(i), f"2026-03-05 12:{i:02d}:00.000000+00:00", f"{m} 這句話{'甲乙丙丁戊己庚辛壬癸'[i]}沒問題 {m}") for i, m in enumerate(markup)]
        msgs += [("20", ts(5, "12:30"), f"<@{sf}> 這是真的電話 0912-345-678 不能留"),            # markup does not hide a real phone number
                 ("21", ts(5, "12:31"), f"明碼{sf}混在文字裡"),                                  # a bare id outside markup still counts (as a phone-like run)
                 ("22", ts(5, "12:32"), "看這個 https://example.com/abcdefghij1234567890klmnop 很好玩"),   # URL first: counted as url, not token
                 ("23", ts(5, "12:33"), f"<@{sf}> 金鑰 abcd1234efgh5678ijkl9012 不要貼")]            # markup stripped, the token stays
        td = tempfile.mkdtemp()
        counts = bc.prepare(package(td, {"c1": msgs}), None, None, [], os.path.join(td, "w"))["discord"]
        rows = [s for _m, _src, _x, s in clauses_of(os.path.join(td, "w"))]
        for i in range(len(markup)):
            self.assertIn(f"這句話{'甲乙丙丁戊己庚辛壬癸'[i]}沒問題", rows)             # kept, Han text segmented as usual
        self.assertFalse(any("你好嗎" in s or "跳舞" in s for s in rows))                # the emoji name inside the markup is gone
        self.assertEqual(counts["_dropped_private_by_rule"], {"phone": 2, "token": 1})
        self.assertEqual(counts["2026-03"]["url"], 1)
        self.assertEqual(counts["2026-03"]["messages"], len(msgs) - 3)

    def test_markup_becomes_a_space_and_a_segment_boundary_never_a_drop(self):
        td = tempfile.mkdtemp()
        root = package(td, {"c1": [("1", ts(5, "12:00"), "我跟<@123456789012345678>說好了"), ("2", ts(5, "12:30"), "<t:1700000000:R>")]})
        counts = bc.prepare(root, None, None, [], os.path.join(td, "w"))["discord"]
        self.assertEqual(sorted((s, x) for _m, _src, x, s in clauses_of(os.path.join(td, "w"))), sorted([("我跟", ""), ("說好了", "我跟")]))
        self.assertEqual(counts["_dropped_private_by_rule"], {})
        self.assertEqual({k: counts["2026-03"][k] for k in ("messages", "segments", "no_segment", "mechanical")},
                         {"messages": 2, "segments": 2, "no_segment": 1, "mechanical": 2})      # the markup-only message is empty, not dropped as private

    def test_dm_and_private_channels_are_never_opened(self):
        td = tempfile.mkdtemp()
        msgs = lambda t: [("1", ts(5, "12:00"), t)]
        name = lambda ch: "甲乙丙丁戊己庚辛壬癸"[int(ch[1:]) - 1] * 2
        types = {"c1": "GUILD_TEXT", "c2": "DM", "c3": "GROUP_DM", "c4": "PRIVATE_THREAD", "c5": "PUBLIC_THREAD", "c6": "GUILD_VOICE",
                 "c7": "SOMETHING_NEW", "c8": None, "c9": "GUILD_ANNOUNCEMENT", "c10": "ANNOUNCEMENT_THREAD"}   # c8: no channel.json
        root = package(td, {ch: msgs(f"頻道{name(ch)}的話語") for ch in types}, types)
        # a decoy that would produce rows (and raise on an unreadable messages file) if a skipped channel were read
        real_open, opened = open, []

        def spy(f, *a, **k):
            if isinstance(f, (str, os.PathLike)) and os.fspath(f).startswith(root):
                opened.append(os.path.relpath(os.fspath(f), root))
            return real_open(f, *a, **k)
        with mock.patch("builtins.open", spy):
            counts = bc.prepare(root, None, None, [], os.path.join(td, "w"))
        for ch in ("c2", "c3", "c4", "c7", "c8"):
            self.assertNotIn(os.path.join(ch, "messages.json"), opened, ch)
        self.assertEqual(sorted(s for _m, _s, _x, s in clauses_of(os.path.join(td, "w"))),
                         sorted(f"頻道{name(f'c{n}')}的話語" for n in (1, 5, 6, 9, 10)))
        self.assertEqual(counts["discord"]["_skipped_channels_by_type"], {"DM": 1, "GROUP_DM": 1, "PRIVATE_THREAD": 1, "SOMETHING_NEW": 1, "MISSING": 1})


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
        counts = bc.prepare(root, None, None, [ex], os.path.join(td, "w"))["discord"]
        self.assertEqual(sorted(m for m in counts if not m.startswith("_")), ["2026-08", "2026-09", "2026-10"])
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

    def test_a_moved_judge_start_leaves_the_skipped_month_out_of_both(self):
        # maintainer 2026-10-13: 2026-09 is skipped, the judge period starts at 2026-08
        by = {"2026-09": 9000, "2026-08": 3000, "2026-07": 500, "2026-06": 400}
        self.assertEqual(bc.choose_judge_months(by, start="2026-08"), (["2026-08"], ["2026-06", "2026-07"]))
        self.assertEqual(bc.choose_judge_months({"2026-09": 9000, "2026-08": 1000, "2026-07": 2000, "2026-06": 400}, start="2026-08"),
                         (["2026-07", "2026-08"], ["2026-06"]))
        for bad in ("2026-10", "2026-8", "x"):
            with self.assertRaises(SystemExit):
                bc.choose_judge_months(by, start=bad)


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
        bc.prepare(self.root, None, None, [], self.work)
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

    def test_split_judge_start_option(self):
        w2 = os.path.join(self.td, "w2")
        bc.prepare(self.root, None, None, [], w2)
        with contextlib.redirect_stdout(io.StringIO()):
            bc.main(["split", "--work", w2, "--judge-start", "2026-07"])
        sp = json.load(open(os.path.join(w2, "split.json")))
        self.assertEqual((sp["judge_start"], sp["judge_months"], sp["pool_months"]), ("2026-07", ["2026-06", "2026-07"], ["2026-05"]))
        judge = bc.freeze(w2, "tune", readings=fake_readings) and bc.freeze(w2, "judge", readings=fake_readings)
        months = {m for m, _src, _x, s in bc.read_clauses(w2) if s in {r.split("|")[1] for r in open(os.path.join(w2, "judge.rows.txt"), encoding="utf-8").read().splitlines()}}
        self.assertEqual((judge["rows"], months), (6, {"2026-06", "2026-07"}))     # nothing from 2026-08 or 2026-09

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

    def test_judge_draft_also_needs_the_tune_freeze_first(self):
        with self.assertRaises(SystemExit):
            bc.freeze(self.work, "judge", draft=True, readings=fake_readings)
        self.assertFalse(os.path.exists(os.path.join(self.work, "judge.draft.txt")))

    def test_manifest_records_the_pool_size_before_sampling(self):
        bc.freeze(self.work, "tune", readings=fake_readings)
        m = json.load(open(os.path.join(self.work, "tune.manifest.json"), encoding="utf-8"))
        self.assertEqual((m["rows"], m["clauses_before_sampling_or_corrections"]), (4, 9))

    def test_more_than_10_percent_check_stops_the_freeze_but_not_the_draft(self):
        def checky(sentences):      # 2 of 4 sampled rows are CHECK
            bad = sorted(set(sentences))[:2]
            return {s: ("check" if s in bad else "ok", "ㄅ") for s in set(sentences)}
        with self.assertRaises(SystemExit) as e:
            bc.freeze(self.work, "tune", readings=checky)
        self.assertIn("CHECK", str(e.exception.code))
        for f in ("tune.rows.txt", "tune.sha256", "tune.manifest.json"):
            self.assertFalse(os.path.exists(os.path.join(self.work, f)), f)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            bc.freeze(self.work, "tune", draft=True, readings=checky)
        self.assertIn("WARNING", err.getvalue())
        self.assertTrue(os.path.exists(os.path.join(self.work, "tune.draft.txt")))

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
                "陳委員玉珍：（15時20分）是不是這樣。主席：請說。是不是這樣")
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
        self.assertEqual(sorted(sentences), sorted(["你知道我是哪裡來的委員嘛", "是不是這樣"]))   # 好不好 is only a filter word; the second 是不是這樣 is a duplicate
        self.assertTrue(all("委員玉珍" not in s and "王部長國材" not in s and "主席" not in s for r in rows for s in r))
        ctx = {r[1]: r[0] for r in rows}
        self.assertEqual(ctx["你知道我是哪裡來的委員嘛"], "")        # first segment of the speech
        self.assertEqual(ctx["是不是這樣"], "沒有口語詞的一句")      # the first occurrence is kept
        self.assertEqual((n["speeches"], n["clauses_2_30_han"], n["clauses_colloquial"], n["after_dropping_bare_filter_words"],
                          n["after_dropping_duplicate_sentences"], n["rows"]), (1, 7, 4, 3, 2, 2))
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
                 f"藉口\t籍口\tother",
                 "筆蹟\t笔跡\tother",                                # simplified-only gold: dropped
                 "痕迹\t痕跡\tother"]                                # the gold 痕跡 again: dropped                              # 藉 but not from the AWB rule: kept
        pf = os.path.join(td, "pairs.tsv")
        open(pf, "w", encoding="utf-8").write("\n".join(pairs) + "\n")
        json.dump({"pages": [{"title": bw.AWB, "file": "awb.wikitext", "sha256": c.sha256_file(wf)}]}, open(os.path.join(td, "manifest.json"), "w"))
        table = {"痕蹟": "ㄏ ㄐ", "痕跡": "ㄏ ㄐ", "全然": "ㄑ ㄖ", "全部": "ㄑ ㄅ", "藉貫": "ㄐ ㄍ", "籍貫": "ㄐ ㄍ", "藉口": "ㄐ ㄎ", "籍口": "ㄐ ㄎ", "筆蹟": "ㄅ ㄐ", "笔跡": "ㄅ ㄐ", "痕迹": "ㄏ ㄐ"}
        rd = lambda ss: {s: ("ok", table[s]) if s in table else ("unreadable", "") for s in set(ss)}
        outs = []
        for k in range(2):
            out = os.path.join(td, f"o{k}.txt")
            n = bw.run(td, out, rd, check_hash=False, simp=lambda: set("笔"))
            outs.append(open(out, "rb").read())
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0].decode(), "|籍貫|ㄐ ㄍ\n|痕跡|ㄏ ㄐ\n|籍口|ㄐ ㄎ\n")
        self.assertEqual((n["pairs"], n["after_empty_or_same"], n["dropped_jie_rule_rows"], n["distinct_pairs"], n["rows"]), (11, 10, 2, 7, 3))
        self.assertEqual((n["same_reading_pairs"], n["dropped_simplified_gold"], n["dropped_duplicate_gold"]), (5, 1, 1))
        with self.assertRaises(SystemExit):
            bw.run(td, os.path.join(td, "x.txt"), rd)                                         # the pinned pairs.tsv hash does not match


if __name__ == "__main__":
    unittest.main()
