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
import build_ptt_chat as bp  # noqa: E402
import build_lytune as bl  # noqa: E402
import build_wikitypo as bw  # noqa: E402
import e2common as c  # noqa: E402


def old_prepare(*a, **k):
    """The periods frozen before CE3: no window, no per-source duplicate rule."""
    return bc.prepare(*a, legacy=True, **k)


def fake_readings(sentences):
    return {s: ("ok", " ".join("ㄅ" for _ in s)) for s in set(sentences)}


def ts(day, hm, month="2026-03"):
    """A Discord timestamp in UTC; 12:00 UTC is 20:00 in UTC+8, the same day."""
    return f"{month}-{day:02d} {hm}:00.000000+00:00"


def package(td, channels, types=None, guilds=None):
    """channels: {folder: [(id, timestamp, contents)]} -> a Discord messages folder (JSON for c-folders, CSV for the others)."""
    root = os.path.join(td, "Messages")
    os.makedirs(root)
    json.dump({"1": "x"}, open(os.path.join(root, "index.json"), "w"))
    for ch, msgs in channels.items():
        d = os.path.join(root, ch)
        os.makedirs(d)
        if (types or {}).get(ch, "GUILD_TEXT") is not None:
            info = {"id": ch, "type": (types or {}).get(ch, "GUILD_TEXT")}
            if (guilds or {}).get(ch):
                info["guild"] = {"id": guilds[ch], "name": "synthetic"}
            json.dump(info, open(os.path.join(d, "channel.json"), "w"))
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
        counts = old_prepare(root, None, None, kw.get("exclude", []), os.path.join(td, "w"))
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
            counts = old_prepare(None, root, me, [], os.path.join(td, "w"))
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
        counts = old_prepare(root, exp, "我", [], os.path.join(td, "w"))
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
        counts = old_prepare(package(td, {"c1": msgs}), None, None, [], os.path.join(td, "w"))["discord"]
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
        counts = old_prepare(root, None, None, [], os.path.join(td, "w"))["discord"]
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
            counts = old_prepare(root, None, None, [], os.path.join(td, "w"))
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
        counts = old_prepare(root, None, None, [ex], os.path.join(td, "w"))["discord"]
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
        old_prepare(self.root, None, None, [], self.work)
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
        old_prepare(self.root, None, None, [], w2)
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

    def test_chatjudge_leaves_out_the_tuning_sample_and_its_sentences(self):
        # rerank-size section 3.4: the unseen chat set is drawn from the pool without the tuning sample, with its own seed.
        pool = [(x, s) for m, _src, x, s in bc.read_clauses(self.work) if m in ("2026-05", "2026-06", "2026-07")]
        tuned = {s for _x, s in random.Random(20261012).sample(pool, 4)}
        rest = [r for r in pool if r[1] not in tuned]
        with mock.patch.object(bc, "CHATJUDGE_MAX", 3):
            bc.freeze(self.work, "tune", readings=fake_readings)
            st = bc.freeze(self.work, "chatjudge", readings=fake_readings)
        got = [tuple(l.split("|")[:2]) for l in open(os.path.join(self.work, "chatjudge.rows.txt"), encoding="utf-8").read().split("\n") if l]
        self.assertEqual(got, random.Random(20261017).sample(rest, 3))
        tune = {l.split("|")[1] for l in open(os.path.join(self.work, "tune.rows.txt"), encoding="utf-8") if l.strip()}
        self.assertFalse(tune & {s for _x, s in got})
        self.assertEqual((st["rows"], st["months"]), (3, ["2026-05", "2026-06", "2026-07"]))

    def test_chatjudge_leaves_out_a_tuning_sentence_repeated_elsewhere(self):
        # 2026-06 repeats 2026-05 word for word, so a sentence the tuning sample drew can sit at another pool position too.
        td = tempfile.mkdtemp()
        texts = {"2026-05": "甲乙丙", "2026-06": "甲乙丙", "2026-07": "丁戊己", "2026-08": "庚辛壬", "2026-09": "子丑寅"}
        msgs = [(f"{i}{j}", f"{mon}-10 {10 + j}:00:00.000000+00:00", ch + "字") for i, (mon, t) in enumerate(texts.items()) for j, ch in enumerate(t)]
        work = os.path.join(td, "w")
        old_prepare(package(td, {"c1": msgs}), None, None, [], work)
        with contextlib.redirect_stdout(io.StringIO()):
            bc.main(["split", "--work", work])
        pool = [(x, s) for m, _src, x, s in bc.read_clauses(work) if m in ("2026-05", "2026-06", "2026-07")]
        idx = random.Random(20261012).sample(range(len(pool)), 4)                 # the same positions as sampling the rows
        tuned = {pool[i][1] for i in idx}
        self.assertTrue([i for i, r in enumerate(pool) if r[1] in tuned and i not in idx])   # a drawn sentence also sits elsewhere
        with mock.patch.object(bc, "CHATJUDGE_MAX", 100):
            bc.freeze(work, "tune", readings=fake_readings)
            bc.freeze(work, "chatjudge", readings=fake_readings)
        got = [l.split("|")[1] for l in open(os.path.join(work, "chatjudge.rows.txt"), encoding="utf-8") if l.strip()]
        self.assertEqual(sorted(got), sorted(s for _x, s in pool if s not in tuned))

    def test_chatjudge_stops_when_a_correction_meets_a_chatclause_sentence(self):
        with mock.patch.object(bc, "CHATJUDGE_MAX", 3):
            bc.freeze(self.work, "tune", readings=fake_readings)
            rows, _pool = bc.period_rows(self.work, "chatjudge")
            tune = [l.split("|")[1] for l in open(os.path.join(self.work, "tune.rows.txt"), encoding="utf-8") if l.strip()]
            corr = os.path.join(self.td, "c.tsv")
            open(corr, "w", encoding="utf-8").write(f"{rows[0][1]}\t{tune[0]}\n")
            with self.assertRaises(SystemExit) as e:
                bc.freeze(self.work, "chatjudge", corrections_file=corr, readings=fake_readings)
        self.assertIn("also in the frozen chatclause", str(e.exception.code))
        self.assertFalse(os.path.exists(os.path.join(self.work, "chatjudge.rows.txt")))

    def test_chatjudge2_leaves_out_the_tuning_and_chatjudge_samples(self):
        # iteration plan Phase 0 item 7: the second chat judge set never repeats a sentence of the first one or of chatclause.
        pool = [(x, s) for m, _src, x, s in bc.read_clauses(self.work) if m in ("2026-05", "2026-06", "2026-07")]
        tuned = {s for _x, s in random.Random(20261012).sample(pool, 4)}
        rest = [r for r in pool if r[1] not in tuned]
        first = {s for _x, s in random.Random(20261017).sample(rest, 2)}
        rest2 = [r for r in rest if r[1] not in first]
        with mock.patch.object(bc, "CHATJUDGE_MAX", 2), mock.patch.object(bc, "CHATJUDGE2_MAX", 2):
            bc.freeze(self.work, "tune", readings=fake_readings)
            with self.assertRaises(SystemExit) as e:
                bc.freeze(self.work, "chatjudge2", readings=fake_readings)
            self.assertIn("freeze the chatjudge period before chatjudge2", str(e.exception.code))
            bc.freeze(self.work, "chatjudge", readings=fake_readings)
            st = bc.freeze(self.work, "chatjudge2", readings=fake_readings)
        got = [tuple(l.split("|")[:2]) for l in open(os.path.join(self.work, "chatjudge2.rows.txt"), encoding="utf-8").read().split("\n") if l]
        self.assertEqual(got, random.Random(20261018).sample(rest2, 2))
        self.assertFalse((tuned | first) & {s for _x, s in got})
        self.assertEqual(st["rows"], 2)

    def test_chatjudge2_stops_when_a_correction_meets_a_chatjudge_sentence(self):
        with mock.patch.object(bc, "CHATJUDGE_MAX", 2), mock.patch.object(bc, "CHATJUDGE2_MAX", 2):
            bc.freeze(self.work, "tune", readings=fake_readings)
            bc.freeze(self.work, "chatjudge", readings=fake_readings)
            rows, _pool = bc.period_rows(self.work, "chatjudge2")
            first = [l.split("|")[1] for l in open(os.path.join(self.work, "chatjudge.rows.txt"), encoding="utf-8") if l.strip()]
            corr = os.path.join(self.td, "c2.tsv")
            open(corr, "w", encoding="utf-8").write(f"{rows[0][1]}\t{first[0]}\n")
            with self.assertRaises(SystemExit) as e:
                bc.freeze(self.work, "chatjudge2", corrections_file=corr, readings=fake_readings)
        self.assertIn("a chatjudge2 sentence is also in the frozen chatjudge", str(e.exception.code))
        self.assertFalse(os.path.exists(os.path.join(self.work, "chatjudge2.rows.txt")))

    def test_chatjudge2_leaves_out_repeated_and_corrected_sentences(self):
        # A sentence chatjudge drew also sits at another pool position, and a chatjudge correction turns a row into a sentence that
        # sits elsewhere in the pool: chatjudge2 holds neither.
        td = tempfile.mkdtemp()
        texts = {"2026-05": "甲乙丙丁", "2026-06": "甲乙丙丁", "2026-07": "戊己庚辛", "2026-08": "壬癸子丑", "2026-09": "寅卯辰巳"}
        msgs = [(f"{i}{j}", f"{mon}-10 {10 + j}:00:00.000000+00:00", ch + "字") for i, (mon, t) in enumerate(texts.items()) for j, ch in enumerate(t)]
        work = os.path.join(td, "w")
        old_prepare(package(td, {"c1": msgs}), None, None, [], work)
        with contextlib.redirect_stdout(io.StringIO()):
            bc.main(["split", "--work", work])
        pool = [(x, s) for m, _src, x, s in bc.read_clauses(work) if m in ("2026-05", "2026-06", "2026-07")]
        with mock.patch.object(bc, "TUNE_MAX", 2), mock.patch.object(bc, "CHATJUDGE_MAX", 2), mock.patch.object(bc, "CHATJUDGE2_MAX", 100):
            bc.freeze(work, "tune", readings=fake_readings)
            drawn, _ = bc.period_rows(work, "chatjudge")
            tuned = bc.frozen_sentences(work, "tune")
            target = next(s for _x, s in pool if s not in tuned and s not in {d for _x, d in drawn})
            corr = os.path.join(td, "c.tsv")
            open(corr, "w", encoding="utf-8").write(f"{drawn[0][1]}\t{target}\n")
            bc.freeze(work, "chatjudge", corrections_file=corr, readings=fake_readings)
            bc.freeze(work, "chatjudge2", readings=fake_readings)
        got = [l.split("|")[1] for l in open(os.path.join(work, "chatjudge2.rows.txt"), encoding="utf-8") if l.strip()]
        read = tuned | {s for _x, s in drawn} | {target}
        self.assertTrue([s for _x, s in pool if s in {d for _x, d in drawn}][1:])          # a drawn sentence repeats in the pool
        self.assertEqual(sorted(got), sorted(s for _x, s in pool if s not in read))

    def test_chatjudge2_stops_on_a_tune_sentence_a_changed_pool_or_a_missing_rows_file(self):
        with mock.patch.object(bc, "CHATJUDGE_MAX", 2), mock.patch.object(bc, "CHATJUDGE2_MAX", 2):
            bc.freeze(self.work, "tune", readings=fake_readings)
            bc.freeze(self.work, "chatjudge", readings=fake_readings)
            rows, _pool = bc.period_rows(self.work, "chatjudge2")
            corr = os.path.join(self.td, "c3.tsv")
            open(corr, "w", encoding="utf-8").write(f"{rows[0][1]}\t{sorted(bc.frozen_sentences(self.work, 'tune'))[0]}\n")
            with self.assertRaises(SystemExit) as e:
                bc.freeze(self.work, "chatjudge2", corrections_file=corr, readings=fake_readings)
            self.assertIn("a chatjudge2 sentence is also in the frozen chatclause; report to main (iteration plan", str(e.exception.code))
            tman = os.path.join(self.work, "tune.manifest.json")       # as the work-v2 tune freeze (2026-10-13) wrote it: the sample size
            m = json.load(open(tman, encoding="utf-8"))
            m["clauses_before_sampling_or_corrections"] = m["rows_in"]
            open(tman, "w", encoding="utf-8").write(json.dumps(m))
            self.assertEqual(bc.period_rows(self.work, "chatjudge2")[0], rows)      # not a reason to stop
            ct = os.path.join(self.work, "clauses.tsv")                 # same rows, other order: the pool size alone would not tell
            lines = open(ct, encoding="utf-8").read().splitlines(keepends=True)
            open(ct, "w", encoding="utf-8").write("".join(reversed(lines)))
            with self.assertRaises(SystemExit) as e:
                bc.freeze(self.work, "chatjudge2", readings=fake_readings)
            self.assertIn("cannot be replayed", str(e.exception.code))
            open(ct, "w", encoding="utf-8").write("".join(lines))
            os.remove(os.path.join(self.work, "chatjudge.rows.txt"))
            with self.assertRaises(SystemExit) as e:
                bc.freeze(self.work, "chatjudge2", readings=fake_readings)
            self.assertIn("chatjudge.rows.txt is missing", str(e.exception.code))
        self.assertFalse(os.path.exists(os.path.join(self.work, "chatjudge2.rows.txt")))

    def test_a_corrected_row_that_spells_as_check_is_counted(self):
        def readings(sentences):
            return {s: ("check", "") if s == "新句" else ("ok", " ".join("ㄅ" for _ in s)) for s in set(sentences)}
        _frozen, _draft, st = bc.build_period([("", "舊句"), ("", "甲字")], {"舊句": ("新句", None)}, readings)
        self.assertEqual((st["corrected"], st["corrected_then_check"], st["dropped_check"], st["rows"]), (1, 1, 1, 1))
        _frozen, _draft, st = bc.build_period([("", "舊句")], {"舊句": ("新句", "ㄒㄧㄣ ㄐㄩˋ")}, readings)
        self.assertEqual((st["corrected_then_check"], st["rows"]), (0, 1))                # READ given: kept

    def test_chatjudge_needs_the_tune_freeze_first(self):
        with self.assertRaises(SystemExit) as e:
            bc.freeze(self.work, "chatjudge", readings=fake_readings)
        self.assertIn("tuning period first", str(e.exception.code))

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


def many(n, month="2026-05", prefix="", segs=3, salt="字"):
    """n distinct messages [(id, timestamp, text)]: `segs` segments each, every segment distinct across messages."""
    chars = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"
    word = lambda i, j: chars[i % 20] + chars[(i // 20) % 20] + chars[(i // 400) % 20] + chars[j] + salt
    return [(f"{prefix}{i}", f"{month}-{1 + i % 27:02d} {10 + i % 10}:00:00.000000+00:00", "，".join(word(i, j) for j in range(segs))) for i in range(n)]


def ids_in(name, n, source="discord", start=100000):
    """n message ids (strings) whose bucket is `name`."""
    out, i = [], start
    while len(out) < n:
        if c.bucket_set(source, str(i)) == name:
            out.append(str(i))
        i += 1
    return out


def freeze_raw(*a, **k):
    """bc.freeze with the S1 cdev minimum off (cdev_min=N to set it) and the fake reading tool; a stop raises SystemExit."""
    readings, cdev_min = k.pop("readings", fake_readings), k.pop("cdev_min", 0)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), mock.patch.object(bc, "CDEV_MIN_S1", cdev_min):
        return bc.freeze(*a, readings=readings, **k)


def freeze_quiet(*a, **k):
    """freeze_raw for a freeze that must go through: any stop (a second guard included) fails the test as an assertion."""
    try:
        return freeze_raw(*a, **k)
    except SystemExit as e:
        raise AssertionError(f"the freeze stopped: {e}")


def han_msgs(ids, month, per=3, salt="甲", day=3):
    """Messages [(id, timestamp, text)] of `per` distinct 4-character clauses each; the salt keeps sets of messages apart."""
    h = "一二三四五六七八九十"
    return [(mid, ts(day, "10:00", month), "，".join(f"句{h[i // 10]}{h[i % 10]}{salt}{h[j]}"[:5] for j in range(per))) for i, mid in enumerate(ids)]


def write_clauses(work, rows):
    """A clauses.tsv by hand: rows (month, source, sentence, message id)."""
    os.makedirs(work, exist_ok=True)
    open(os.path.join(work, "clauses.tsv"), "w", encoding="utf-8").write("".join(f"{m}\t{src}\t\t{sent}\t{mid}\t{mid}\n" for m, src, sent, mid in rows))
    return work


def rows_of(work, period):
    return [l.split("|") for l in open(os.path.join(work, f"{period}.rows.txt"), encoding="utf-8").read().splitlines()]


class CE3Discord(unittest.TestCase):
    """docs/contracts/chat-eval-3.md sections 2.1, 3, 4, 5: the server filter, the time window, buckets, sets."""

    def prepared(self, msgs, extra=(), legacy=False, guild=None, td=None):
        td = td or tempfile.mkdtemp()
        root = package(td, {"c1": list(extra) + msgs})
        work = os.path.join(td, "w")
        bc.prepare(root, None, None, [], work, guild=guild, legacy=legacy)
        return work

    def test_only_the_given_server_is_read_and_the_other_channels_are_never_opened(self):
        td = tempfile.mkdtemp()
        types = {"c1": "GUILD_TEXT", "c2": "GUILD_TEXT", "c3": "GUILD_TEXT", "c4": "DM", "c5": "GUILD_TEXT"}
        guilds = {"c1": "111", "c2": "222", "c3": None, "c4": "111", "c5": "111"}     # c3: no guild; c4: a DM of that guild id
        root = package(td, {ch: [("1", ts(5, "12:00"), f"頻道{'一二三四五'[int(ch[1]) - 1]}乙乙的話語")] for ch in types}, types, guilds)
        real_open, opened = open, []

        def spy(f, *a, **k):
            if isinstance(f, (str, os.PathLike)) and os.fspath(f).startswith(root):
                opened.append(os.path.relpath(os.fspath(f), root))
            return real_open(f, *a, **k)
        with mock.patch("builtins.open", spy):
            counts = bc.prepare(root, None, None, [], os.path.join(td, "w"), guild="111")
        for ch in ("c2", "c3", "c4"):
            self.assertNotIn(os.path.join(ch, "messages.json"), opened, ch)
        self.assertIn(os.path.join("c1", "messages.json"), opened)
        self.assertEqual(sorted(s for _m, _s, _x, s in clauses_of(os.path.join(td, "w"))), sorted(f"頻道{n}乙乙的話語" for n in "一五"))
        self.assertEqual(counts["discord"]["_skipped_channels_by_type"], {"GUILD_OTHER": 1, "GUILD_NONE": 1, "DM": 1})

    def test_no_channel_of_the_guild_stops(self):
        td = tempfile.mkdtemp()
        root = package(td, {"c1": [("1", ts(5, "12:00"), "甲乙丙丁")]}, guilds={"c1": "222"})
        with self.assertRaises(SystemExit):
            bc.prepare(root, None, None, [], os.path.join(td, "w"), guild="111")

    def test_the_cli_needs_a_guild_with_discord(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            bc.main(["prepare", "--work", tempfile.mkdtemp(), "--discord", tempfile.mkdtemp()])

    def test_the_window_is_the_default_and_only_the_legacy_opt_out_reads_other_months(self):
        msgs = [("1", ts(5, "12:00", "2026-09"), "九月的話語"), ("2", ts(5, "12:00", "2026-05"), "五月的話語")]
        self.assertEqual([s for _m, _s, _x, s in clauses_of(self.prepared(msgs))], ["五月的話語"])
        self.assertEqual(sorted(s for _m, _s, _x, s in clauses_of(self.prepared(msgs, legacy=True))), ["九月的話語", "五月的話語"])

    def test_a_message_outside_the_window_is_dropped_before_any_text_function_sees_it(self):
        """Through main(), the path the CLI takes. Discord and Meta, with spies on every function that reads text, hashing included."""
        S_SEP, S_OLD, S_OK = "哨兵九月專用", "哨兵舊時專用", "哨兵窗內專用"
        td = tempfile.mkdtemp()
        root = package(td, {"c1": [("1", ts(5, "12:00", "2026-09"), S_SEP), ("2", ts(5, "12:00", "2015-06"), S_OLD),
                                   ("3", ts(5, "12:00", "2026-10"), S_SEP + "十月"), ("4", "壞的時間", S_SEP + "無時間"),
                                   ("5", ts(5, "12:00", "2026-08"), S_OK)]}, guilds={"c1": "111"})
        seen = []

        class Spy:     # records the text it is asked about, then behaves as the wrapped regex
            def __init__(self, inner):
                self.inner = inner

            def search(self, t):
                seen.append(t)
                return self.inner.search(t)

            def sub(self, r, t):
                seen.append(t)
                return self.inner.sub(r, t)

        def record(f):
            def w(t, *a, **k):
                seen.append(t if isinstance(t, str) else t.decode("utf-8"))
                return f(t, *a, **k)
            return w
        meta = os.path.join(td, "meta")
        os.makedirs(os.path.join(meta, "threads"))
        stamp = lambda y, m: dt.datetime(y, m, 3, 4, tzinfo=dt.timezone.utc).timestamp()
        json.dump({"text_post_app_text_posts": [{"title": S_SEP + "串", "creation_timestamp": stamp(2026, 9)}, {"title": "", "creation_timestamp": stamp(2026, 10)},
                                                {"title": S_OLD + "串", "creation_timestamp": stamp(2015, 5)}, {"title": S_OK + "串", "creation_timestamp": stamp(2026, 8)}]},
                  open(os.path.join(meta, "threads", "threads_and_replies.json"), "w"))
        work = os.path.join(td, "w")
        with mock.patch.object(bc, "URL", Spy(bc.URL)), mock.patch.object(bc, "DISCORD_MARKUP", Spy(bc.DISCORD_MARKUP)), \
                mock.patch.object(bc, "private_rule", record(bc.private_rule)), mock.patch.object(c, "segments", record(c.segments)), \
                mock.patch.object(bc.hashlib, "sha256", record(bc.hashlib.sha256)), contextlib.redirect_stdout(io.StringIO()):
            bc.main(["prepare", "--work", work, "--discord", root, "--guild", "111", "--meta", meta, "--self-name", "me"])
        self.assertTrue(any(S_OK + "串" in t for t in seen), "the spies are wired: an in-window Meta post is hashed")
        self.assertTrue(any(S_OK == t for t in seen))
        self.assertFalse([t for t in seen if "哨兵九月" in t or "哨兵舊時" in t or "無時間" in t], "no text function saw it")
        for dirpath, _d, files in os.walk(work):
            for f in files:
                data = open(os.path.join(dirpath, f), encoding="utf-8").read()
                for bad in ("哨兵九月", "哨兵舊時", "無時間"):
                    self.assertNotIn(bad, data)
        counts = json.load(open(os.path.join(work, "counts.json"), encoding="utf-8"))
        self.assertEqual(counts["discord"]["_dropped_out_of_window"], {"2015-06": 1, "2026-09": 1, "2026-10": 1})
        self.assertEqual(counts["discord"]["_dropped_no_timestamp"], 1)
        self.assertEqual(counts["threads"]["_dropped_out_of_window"], {"2015-05": 1, "2026-09": 1, "2026-10": 1})    # the empty one is out of window, not no-text
        self.assertNotIn("_dropped_no_text", counts["threads"])
        self.assertEqual(sorted(s for _m, _s, _x, s in clauses_of(work)), sorted([S_OK, S_OK + "串"]))

    def test_buckets_keep_the_clauses_of_a_message_together_and_use_all_three_sets(self):
        work = self.prepared(many(90, "2026-05"))
        full = bc.read_clauses(work, full=True)
        self.assertEqual(len(full), 270)
        sets = {}
        for _m, src, _x, sent, _ch, mid in full:
            sets.setdefault(mid, set()).add(c.bucket_set(src, mid))
        self.assertTrue(all(len(v) == 1 for v in sets.values()))
        self.assertEqual({next(iter(v)) for v in sets.values()}, {"ctune", "cdev", "cjudge"})

    def test_buckets_do_not_move_when_the_export_gains_an_earlier_and_a_later_message(self):
        base = many(40, "2026-05")
        early = [("0", ts(1, "01:00", "2019-01"), "最早的新增訊息")]
        late = [("999999", ts(1, "01:00", "2026-08"), "最晚的新增訊息")]
        a = bc.read_clauses(self.prepared(base), full=True)
        b = bc.read_clauses(self.prepared(base, early + late), full=True)
        bucket = lambda rows: {(mid, sent): c.bucket_set(src, mid) for _m, src, _x, sent, _ch, mid in rows}
        ba, bb = bucket(a), bucket(b)
        self.assertEqual({k: v for k, v in bb.items() if k in ba}, ba)
        self.assertEqual(len(bb) - len(ba), 2)
        td = tempfile.mkdtemp()      # Meta ids hold no position either: a post added in front keeps the ids of the others
        os.makedirs(os.path.join(td, "threads"))
        post = lambda t, n: {"title": n, "creation_timestamp": t}
        write = lambda posts: json.dump({"text_post_app_text_posts": posts}, open(os.path.join(td, "threads", "threads_and_replies.json"), "w"))
        write([post(1700000100, "甲乙丙丁"), post(1700000200, "戊己庚辛")])
        ids1 = [m[1] for m in bc.meta_messages(td, "me")["threads"][0]]
        write([post(1600000000, "新的最前面")] + [post(1700000100, "甲乙丙丁"), post(1700000200, "戊己庚辛")] + [post(1800000000, "新的最後面")])
        ids2 = [m[1] for m in bc.meta_messages(td, "me")["threads"][0]]
        self.assertEqual(ids2[1:3], ids1)

    def test_a_clause_repeated_in_two_months_of_one_source_keeps_its_first_copy(self):
        a, b = ids_in("cdev", 2)
        msgs = [(a, ts(3, "10:00", "2026-05"), "重複的子句，甲獨有"), (b, ts(3, "10:00", "2026-06"), "重複的子句，乙獨有")]
        self.assertEqual(sorted(s for _m, _s, _x, s in clauses_of(self.prepared(msgs))), ["乙獨有", "甲獨有", "重複的子句"])
        self.assertEqual(sorted(s for _m, _s, _x, s in clauses_of(self.prepared(msgs, legacy=True))), ["乙獨有", "甲獨有", "重複的子句", "重複的子句"])

    def test_freeze_the_three_sets_disjoint_cjudge_without_2026_08(self):
        td = tempfile.mkdtemp()
        msgs = many(150, "2026-05") + many(150, "2026-07", "j", salt="丑") + many(150, "2026-08", "k", salt="寅")
        work = self.prepared(msgs, td=td)
        out = {}
        for period in ("ctune", "cdev", "cjudge-1"):
            st = freeze_quiet(work, period)
            out[period] = [r[1] for r in rows_of(work, period)]
            self.assertEqual(len(open(os.path.join(work, f"{period}.sources.txt")).read().splitlines()), len(out[period]))
            self.assertEqual(st["rows_by_source"], {"discord": len(out[period])})
        self.assertTrue(all(out.values()))
        names = sorted(out)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                self.assertFalse(set(out[a]) & set(out[b]), (a, b))
        month_of = {s: m for m, _src, _x, s in bc.read_clauses(work)}
        self.assertTrue(all(month_of[s] <= "2026-07" for s in out["cjudge-1"]))
        self.assertGreater(st["not_used_cjudge_bucket_after_2026_07_messages"], 0)
        with self.assertRaises(SystemExit):
            freeze_raw(work, "cjudge-3")       # cjudge-2 is not frozen yet

    def test_a_correction_that_changes_or_drops_a_sentence_keeps_the_source_tags_aligned(self):
        """Discord and Threads rows mixed in one set: each frozen row's source is the source of the clause it was drawn from."""
        td = tempfile.mkdtemp()
        root = package(td, {"c1": many(60, "2026-05")})
        meta = os.path.join(td, "meta")
        os.makedirs(os.path.join(meta, "threads"))
        posts = [{"title": t, "creation_timestamp": dt.datetime(2026, 5, 1 + i % 27, 4, tzinfo=dt.timezone.utc).timestamp()}
                 for i, (_i, _t, t) in enumerate(many(60, "2026-05", "m", salt="丑"))]
        json.dump({"text_post_app_text_posts": posts}, open(os.path.join(meta, "threads", "threads_and_replies.json"), "w"))
        work = os.path.join(td, "w")
        bc.prepare(root, meta, "me", [], work)
        source_of = {s: src for _m, src, _x, s in bc.read_clauses(work)}
        sample = bc.ce3_rows(work, "ctune")[0]
        self.assertEqual({src for _x, _s, src, _m in sample}, {"discord", "threads"})
        change, drop = sample[0][1], sample[1][1]
        corr = os.path.join(td, "corr.tsv")
        open(corr, "w", encoding="utf-8").write(f"{drop}\t\n{change}\t{change}改\t{' '.join('ㄅ' * (len(change) + 1))}\n")
        st = freeze_quiet(work, "ctune", corr)
        rows = [r[1] for r in rows_of(work, "ctune")]
        sources = open(os.path.join(work, "ctune.sources.txt")).read().split()
        self.assertEqual(len(rows), len(sample) - 1)
        self.assertNotIn(drop, rows)
        self.assertEqual(sources, [source_of[s[:-1] if s == change + "改" else s] for s in rows])      # row by row, not only in total
        self.assertEqual(st["rows_by_source"], {k: sources.count(k) for k in ("discord", "threads")})
        # and build_period alone: the tags of the kept rows, whatever sits before them
        _f, _d, _st, tags = bc.build_period([("", "甲乙"), ("", "丙丁"), ("", "戊己")], {"甲乙": (None, None)}, fake_readings, ["a", "b", "c"])
        self.assertEqual(tags, ["b", "c"])

    def test_a_later_cjudge_never_takes_back_a_corrected_dropped_or_check_row(self):
        work = self.prepared(han_msgs(ids_in("cjudge", 6), "2026-06"))
        with mock.patch.object(bc, "CJUDGE_HALF", 12):
            first = bc.ce3_rows(work, "cjudge")[0]
            self.assertEqual(len(first), 12)
            changed, dropped, checked = first[0][1], first[1][1], first[5][1]
            corr = os.path.join(os.path.dirname(work), "corr.tsv")
            open(corr, "w", encoding="utf-8").write(f"{changed}\t{changed}改\t{' '.join('ㄅ' * (len(changed) + 1))}\n{dropped}\t\n")
            check = lambda ss: {s: (("check", "") if s == checked else ("ok", "ㄅ")) for s in set(ss)}
            freeze_quiet(work, "cjudge-1", corr, readings=check)
        self.assertNotIn(checked, {r[1] for r in rows_of(work, "cjudge-1")})
        with mock.patch.object(bc, "CJUDGE_HALF", 100):                  # the pool is small enough for cjudge-2 to take everything left
            second = bc.ce3_rows(work, "cjudge", 2)[0]
            freeze_quiet(work, "cjudge-2")
        everything = {s for _m, _src, _x, s in bc.read_clauses(work)}
        self.assertEqual(len(everything), 18)
        self.assertEqual({s for _x, s, _src, _m in second}, everything - {s for _x, s, _src, _m in first})       # exactly the two messages left
        self.assertFalse({changed, dropped, checked} & {r[1] for r in rows_of(work, "cjudge-2")})

    def test_a_correction_into_a_row_of_the_other_side_or_another_frozen_set_stops(self):
        work = self.prepared(many(80, "2026-06"))
        freeze_quiet(work, "cdev")
        other = rows_of(work, "cdev")[0][1]
        victim = bc.ce3_rows(work, "ctune")[0][0][1]
        corr = os.path.join(os.path.dirname(work), "corr.tsv")
        open(corr, "w", encoding="utf-8").write(f"{victim}\t{other}\t{' '.join('ㄅ' * len(other))}\n")
        with self.assertRaises(SystemExit):
            freeze_raw(work, "ctune", corr)                               # into a frozen set of the folder
        self.assertFalse(os.path.isfile(os.path.join(work, "ctune.rows.txt")))
        theirs = os.path.join(os.path.dirname(work), "ptt.rows.txt")      # into a row of the other side's --exclude file
        open(theirs, "w", encoding="utf-8").write("|外來的句子|ㄅ\n")
        open(corr, "w", encoding="utf-8").write(f"{victim}\t外來的句子\t{' '.join('ㄅ' * 5)}\n")
        with self.assertRaises(SystemExit):
            freeze_raw(work, "ctune", corr, others=[theirs])
        self.assertFalse(os.path.isfile(os.path.join(work, "ctune.rows.txt")))
        freeze_quiet(work, "ctune", corr)                                 # without that file the same correction goes through
        self.assertIn("外來的句子", [r[1] for r in rows_of(work, "ctune")])

    def test_a_message_drawn_with_other_exclusions_is_not_drawn_again(self):
        (mid,) = ids_in("cjudge", 1)
        work = self.prepared([(mid, ts(4, "10:00", "2026-07"), "句乙甲，句乙乙，句乙丙")])
        x = os.path.join(os.path.dirname(work), "x.rows.txt")
        open(x, "w", encoding="utf-8").write("|句乙乙|ㄅ\n")
        with mock.patch.object(bc, "CJUDGE_HALF", 100):
            self.assertEqual({s for _x, s, _src, _m in bc.ce3_rows(work, "cjudge", 1, {"句乙乙"})[0]}, {"句乙甲", "句乙丙"})
            freeze_quiet(work, "cjudge-1", others=[x])
            self.assertEqual({r[1] for r in rows_of(work, "cjudge-1")}, {"句乙甲", "句乙丙"})
            self.assertEqual(len(open(os.path.join(work, "cjudge-1.origin.tsv")).read().splitlines()), 3)    # the filtered clause is recorded too
            self.assertEqual(bc.ce3_rows(work, "cjudge", 2)[0], [])      # another --exclude: the message still is cjudge-1's
            freeze_quiet(work, "cjudge-2")
        self.assertEqual(rows_of(work, "cjudge-2"), [])

    def test_the_first_month_that_does_not_fit_ends_the_cjudge_draw(self):
        ids = ids_in("cjudge", 7)
        work = self.prepared(han_msgs(ids[:3], "2026-07") + han_msgs(ids[3:], "2026-06", per=1, salt="乙"))
        with mock.patch.object(bc, "CJUDGE_HALF", 7):
            sample, pool, _x, _d = bc.ce3_rows(work, "cjudge")
        self.assertEqual(len(sample), 6)                  # two 3-clause messages of 2026-07; one row is left over ...
        self.assertTrue(all(m in ids[:3] for _x, _s, _src, m in sample))        # ... and a 1-clause message of 2026-06 does not take it

    def test_the_same_sentence_from_two_sources_is_in_a_set_once_and_not_in_two_judge_sets(self):
        d1, d2 = ids_in("cdev", 1)[0], ids_in("cdev", 1, "threads")[0]
        work = write_clauses(os.path.join(tempfile.mkdtemp(), "w"), [("2026-05", "discord", "共同句子", d1), ("2026-05", "discord", "甲獨有句", d1),
                                                                     ("2026-06", "threads", "共同句子", d2), ("2026-06", "threads", "乙獨有句", d2)])
        sample = bc.ce3_rows(work, "cdev")[0]
        self.assertEqual(sorted(s for _x, s, _src, _m in sample), ["乙獨有句", "共同句子", "甲獨有句"])
        self.assertEqual([src for _x, s, src, _m in sample if s == "共同句子"], ["discord"])        # the first by time keeps it
        a, b = ids_in("cjudge", 1)[0], ids_in("cjudge", 1, "threads")[0]
        work = write_clauses(os.path.join(tempfile.mkdtemp(), "w"), [("2026-07", "discord", "共同句子", a), ("2026-06", "threads", "共同句子", b),
                                                                     ("2026-06", "threads", "丙獨有句", b)])
        with mock.patch.object(bc, "CJUDGE_HALF", 1):
            freeze_quiet(work, "cjudge-1")
            self.assertEqual(sorted(s for _x, s, _src, _m in bc.ce3_rows(work, "cjudge", 2)[0]), ["丙獨有句"])     # a message not drawn: its clause stays
            freeze_quiet(work, "cjudge-2")
        self.assertEqual([r[1] for r in rows_of(work, "cjudge-1")], ["共同句子"])
        self.assertEqual([r[1] for r in rows_of(work, "cjudge-2")], ["丙獨有句"])

    def test_a_frozen_set_is_compared_file_by_file_and_never_rewritten(self):
        work = self.prepared(many(60, "2026-05"))
        freeze_quiet(work, "ctune")
        names = [f"ctune.{x}" for x in ("rows.txt", "sha256", "sources.txt", "origin.tsv", "manifest.json")]
        same = {n: open(os.path.join(work, n), encoding="utf-8").read() for n in names}
        freeze_quiet(work, "ctune")
        self.assertEqual(same, {n: open(os.path.join(work, n), encoding="utf-8").read() for n in names})
        for n in ("ctune.sources.txt", "ctune.origin.tsv", "ctune.manifest.json"):
            with open(os.path.join(work, n), "a", encoding="utf-8") as f:
                f.write("tampered\n")
            with self.assertRaises(SystemExit):
                freeze_raw(work, "ctune")
            self.assertTrue(open(os.path.join(work, n), encoding="utf-8").read().endswith("tampered\n"), n)    # not repaired by the rerun
            open(os.path.join(work, n), "w", encoding="utf-8").write(same[n])

    def test_cdev_stops_when_the_s1_bucket_holds_under_1000_rows(self):
        work = self.prepared(many(300, "2026-05"))
        n = bc.ce3_rows(work, "cdev")[1]
        self.assertTrue(0 < n < bc.CDEV_MIN_S1)
        with self.assertRaises(SystemExit) as e:
            freeze_raw(work, "cdev", cdev_min=bc.CDEV_MIN_S1)
        self.assertIn(str(n), str(e.exception.code))
        self.assertFalse(os.path.isfile(os.path.join(work, "cdev.rows.txt")))
        with self.assertRaises(SystemExit):
            freeze_raw(work, "cdev", cdev_min=n + 1)
        freeze_quiet(work, "cdev", cdev_min=n)                         # exactly the minimum goes through

    def test_shared_sentences_are_left_out_and_cjudge_sets_never_repeat(self):
        ct, cd = ids_in("ctune", 1)[0], ids_in("cdev", 1)[0]
        m1, m2 = ids_in("cjudge", 2)
        msgs = [(ct, ts(3, "10:00", "2026-05"), "跨桶共用句，獨有句甲"), (cd, ts(3, "10:00", "2026-06"), "跨桶共用句，獨有句乙"),
                (m1, ts(4, "10:00", "2026-07"), "獨有句丙，並列共用句"), (m2, ts(5, "10:00", "2026-06"), "獨有句丁，並列共用句")]
        work = self.prepared(msgs, legacy=True)          # the per-month duplicate rule lets the same clause sit in two months
        self.assertEqual([s for _x, s, _src, _m in bc.ce3_rows(work, "ctune")[0]], ["獨有句甲"])          # before any freeze
        freeze_quiet(work, "ctune")
        freeze_quiet(work, "cdev")
        self.assertEqual([r[1] for r in rows_of(work, "ctune")], ["獨有句甲"])      # 跨桶共用句 sits in two sets' buckets: in neither
        self.assertEqual([r[1] for r in rows_of(work, "cdev")], ["獨有句乙"])
        with mock.patch.object(bc, "CJUDGE_HALF", 2):
            freeze_quiet(work, "cjudge-1")
            freeze_quiet(work, "cjudge-2")
        self.assertEqual([r[1] for r in rows_of(work, "cjudge-1")], ["獨有句丙"])      # 並列共用句: the first copy by time is m2's ...
        self.assertEqual([r[1] for r in rows_of(work, "cjudge-2")], ["獨有句丁"])     # ... but m1 drew it, so cjudge-2 does not hold it either; m1 is not drawn again

    def test_the_other_sides_rows_are_taken_out_of_the_s1_set(self):
        a, b = ids_in("ctune", 2)
        work = self.prepared([(a, ts(3, "10:00"), "同一句話，甲獨有"), (b, ts(4, "10:00"), "乙獨有")])
        other = os.path.join(os.path.dirname(work), "ptt.rows.txt")
        open(other, "w", encoding="utf-8").write("|同一句話|ㄅ\n")
        self.assertEqual(sorted(s for _x, s, _src, _m in bc.ce3_rows(work, "ctune", 1, {"同一句話"})[0]), ["乙獨有", "甲獨有"])
        freeze_quiet(work, "ctune", others=[other])
        self.assertEqual(sorted(r[1] for r in rows_of(work, "ctune")), ["乙獨有", "甲獨有"])

    def test_clauses_of_a_message_are_drawn_together(self):
        work = self.prepared(many(120, "2026-05") + many(120, "2026-06", "q", salt="丑"))
        with mock.patch.object(bc, "CE3_CAPS", {"ctune": 10, "cdev": 10}), mock.patch.object(bc, "CJUDGE_HALF", 7):
            for name in ("ctune", "cdev", "cjudge"):
                sample = bc.ce3_rows(work, name)[0]
                per = {}
                for _x, s, src, mid in sample:
                    per.setdefault(mid, []).append(s)
                self.assertTrue(per and all(len(v) == 3 for v in per.values()), name)       # each drawn message has all its three clauses
                self.assertLessEqual(len(sample), 10 if name != "cjudge" else 7)
                self.assertEqual(len(sample), 9 if name != "cjudge" else 6)
            freeze_quiet(work, "cjudge-1")
            freeze_quiet(work, "cjudge-2")
        mids = [{l.split("\t")[1] for l in open(os.path.join(work, f"cjudge-{k}.origin.tsv"), encoding="utf-8")} for k in (1, 2)]
        self.assertFalse(mids[0] & mids[1])
        for k in (1, 2):      # a second clause's context is the first clause of the same set, never a sentence of the other judge set
            rows = rows_of(work, f"cjudge-{k}")
            sents = {r[1] for r in rows}
            self.assertTrue(all(r[0] == "" or r[0] in sents for r in rows if r[0] and r[0].endswith(("字", "丑"))))


class CE3Ptt(unittest.TestCase):
    def page(self, month_line, pushes):
        meta = f'<div class="article-metaline"><span class="article-meta-tag">時間</span><span class="article-meta-value">{month_line}</span></div>'
        body = "".join(f'<div class="push"><span class="hl push-tag">推 </span><span class="f3 hl push-userid">{u}</span>'
                       f'<span class="f3 push-content">: {t}</span><span class="push-ipdatetime"> 10/24 12:{i % 60:02d}\n</span></div>' for i, (u, t) in enumerate(pushes))
        return f'<div id="main-content" class="bbs-screen bbs-content">{meta}\n正文\n--\n{body}</div>'

    def snapshot(self, td, articles, month="Mon May 11 12:00:00 2026", name="src"):
        """articles: [(file stem, month line, [(account, text)])] -> the snapshot folder."""
        os.makedirs(os.path.join(td, name, "Gossiping"), exist_ok=True)
        for stem, mline, pushes in articles:
            open(os.path.join(td, name, "Gossiping", f"{stem}.html"), "w", encoding="utf-8").write(self.page(mline or month, pushes))
        return os.path.join(td, name)

    def words(self, n, start=0):
        chars = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"
        return [chars[i % 20] + chars[(i // 20) % 20] + chars[(i // 400) % 20] + "話" for i in range(start, start + n)]

    def distinct_accounts(self, n, start=0):
        w = self.words(2 * n, 2 * start)
        return [(f"M.{start + i}", None, [(f"acct{start + i}", f"{w[2 * i]}，{w[2 * i + 1]}")]) for i in range(n)]

    def test_draw_takes_whole_messages_and_respects_the_account_limit(self):
        items = [(f"m{i}", [("", f"句{i}a"), ("", f"句{i}b")], "a" if i < 8 else "b") for i in range(10)]
        got = c.draw_messages(items, 100, random.Random(1), 6)
        self.assertEqual(sum(1 for _k, _r, a in got if a == "a"), 3)          # 3 messages x 2 rows = 6 for account a
        self.assertEqual(sum(1 for _k, _r, a in got if a == "b"), 2)
        self.assertTrue(all(len(r) == 2 for _k, r, _a in got))
        self.assertEqual(got, c.draw_messages(items, 100, random.Random(1), 6))
        self.assertEqual(len(c.draw_messages(items, 5, random.Random(1))), 2)   # whole messages only: 5 rows fit two of two rows

    def test_build_window_cap_and_one_set_per_sentence(self):
        td = tempfile.mkdtemp()
        w = self.words(400)
        arts, n = [], 0
        for a in range(60):
            pushes = []
            for j in range(3):
                pushes += [("spam", f"{w[n]}，{w[n + 1]}"), ("other%d" % a, "同意同意")]
                n += 2
            arts.append((f"M.{a}", "Mon May 11 12:00:00 2026" if a % 3 else "Sat Aug 08 12:00:00 2026", pushes))
        arts += [("M.sep", "Tue Sep 01 12:00:00 2026", [("x", "哨兵九月專用推文")]), ("M.old", "Tue Jun 02 12:00:00 2015", [("x", "哨兵舊時專用推文")])]
        src = self.snapshot(td, arts)
        out = os.path.join(td, "out")
        rep = bp.build(src, out, ["ctune", "cdev", "cjudge-1"], readings=fake_readings)
        self.assertEqual(rep["counts"]["articles_out_of_window"], 2)
        sets = {}
        for name in ("ctune", "cdev", "cjudge-1"):
            rows = rows_of(out, name)
            sets[name] = [r[1] for r in rows]
            self.assertEqual(len(open(os.path.join(out, f"{name}.sources.txt")).read().splitlines()), len(rows))
            self.assertLessEqual(len([r for r in rows if "同意" not in r[1]]), 20, name)
            self.assertFalse([r for r in rows if "哨兵" in r[1]])
        self.assertTrue(any(len([s for s in v if "同意" not in s]) == 20 for v in sets.values()), "the cap was reached")
        self.assertEqual(sum(s.count("同意同意") for s in sets.values()), 1)
        names = sorted(sets)
        self.assertFalse(set(sets[names[0]]) & set(sets[names[1]]) | set(sets[names[1]]) & set(sets[names[2]]) | set(sets[names[0]]) & set(sets[names[2]]))

    def test_a_frozen_set_is_never_rewritten_and_later_sets_leave_the_earlier_ones_alone(self):
        td = tempfile.mkdtemp()
        src = self.snapshot(td, self.distinct_accounts(150))
        sha = lambda d, n: c.sha256_file(os.path.join(d, f"{n}.rows.txt"))
        with mock.patch.object(bp, "CJUDGE_SIZE", 6):
            one, two = os.path.join(td, "one"), os.path.join(td, "two")
            bp.build(src, one, ["ctune", "cdev", "cjudge-1"], readings=fake_readings)
            bp.build(src, two, ["ctune", "cdev", "cjudge-1", "cjudge-2"], readings=fake_readings)
            before = {n: sha(one, n) for n in ("ctune", "cdev", "cjudge-1")}
            self.assertEqual(before, {n: sha(two, n) for n in before})
            bp.build(src, one, ["cjudge-2"], readings=fake_readings)                                   # drawn later, from the files on disk
            self.assertEqual(before, {n: sha(one, n) for n in before})
            self.assertEqual(sha(one, "cjudge-2"), sha(two, "cjudge-2"))
            mids = [{l.split("\t")[1] for l in open(os.path.join(one, f"cjudge-{k}.origin.tsv"), encoding="utf-8")} for k in (1, 2)]
            self.assertTrue(mids[0] and mids[1] and not mids[0] & mids[1])
            with self.assertRaises(SystemExit):
                bp.build(src, os.path.join(td, "fresh"), ["cjudge-2"], readings=fake_readings)          # cjudge-1 is not there
            # another snapshot (the pool changes): the frozen ctune is not rewritten
            src2 = self.snapshot(td, self.distinct_accounts(150) + self.distinct_accounts(40, 500), name="src2")
            with self.assertRaises(SystemExit):
                bp.build(src2, one, ["ctune", "cdev", "cjudge-1"], readings=fake_readings)
            self.assertEqual(before, {n: sha(one, n) for n in before})

    def test_a_ptt_message_drawn_with_other_exclusions_is_not_drawn_again(self):
        td = tempfile.mkdtemp()
        src = self.snapshot(td, self.distinct_accounts(150))
        pool = bp.collect(src)[0]["cjudge"]
        mid = sorted(pool)[0]
        x = pool[mid][2][1][1]                          # the second clause of that message
        out = os.path.join(td, "out")
        with mock.patch.object(bp, "CJUDGE_SIZE", 1000):
            bp.build(src, out, ["cjudge-1"], exclude={x}, readings=fake_readings)
            self.assertNotIn(x, [r[1] for r in rows_of(out, "cjudge-1")])
            bp.build(src, out, ["cjudge-2"], readings=fake_readings)          # no exclusion now: x is back in the pool, its message is cjudge-1's
        self.assertEqual(rows_of(out, "cjudge-2"), [])

    def test_sets_are_built_in_the_documented_order_and_a_stop_leaves_the_earlier_ones_frozen(self):
        td = tempfile.mkdtemp()
        src = self.snapshot(td, self.distinct_accounts(150))
        calls, real = [], bc.freeze_ce3

        def rec(work, period, *a, **k):
            calls.append(period)
            return real(work, period, *a, **k)
        with mock.patch.object(bc, "freeze_ce3", rec), mock.patch.object(bp, "CJUDGE_SIZE", 6):
            bp.build(src, os.path.join(td, "all"), ["cjudge-2", "cdev", "cjudge-1", "ctune"], readings=fake_readings)
        self.assertEqual(calls, ["ctune", "cdev", "cjudge-1", "cjudge-2"])
        n = [0]

        def second_call_all_check(ss):
            n[0] += 1
            return fake_readings(ss) if n[0] == 1 else {s: ("check", "") for s in set(ss)}
        out = os.path.join(td, "stopped")
        with self.assertRaises(SystemExit):
            bp.build(src, out, ["cjudge-1", "cdev", "ctune"], readings=second_call_all_check)
        self.assertEqual(sorted(f for f in os.listdir(out) if f.endswith(".rows.txt")), ["ctune.rows.txt"])

    def test_more_than_ten_percent_check_stops_the_ptt_freeze(self):
        td = tempfile.mkdtemp()
        src = self.snapshot(td, self.distinct_accounts(40))
        with self.assertRaises(SystemExit):
            bp.build(src, os.path.join(td, "out"), ["ctune"], readings=lambda ss: {s: ("check", "") for s in set(ss)})
        self.assertFalse(os.path.isfile(os.path.join(td, "out", "ctune.rows.txt")))

    def test_sentences_of_the_other_side_are_taken_out_in_both_directions(self):
        td = tempfile.mkdtemp()
        w = self.words(30)
        # S1 side first: its ctune rows go to the PTT build as --exclude
        work = CE3Discord.prepared(self, many(150, "2026-05"), td=td)
        freeze_quiet(work, "ctune")
        s1_rows = os.path.join(work, "ctune.rows.txt")
        s1_sent = rows_of(work, "ctune")[0][1]
        src = self.snapshot(td, [(f"M.{i}", None, [(f"a{i}", f"{s1_sent if i == 0 else w[i]}，{w[i + 10]}")]) for i in range(20)])
        pools, counts = bp.collect(src)
        self.assertIn(s1_sent, {r[1] for name in pools for v in pools[name].values() for r in v[2]})
        pools, counts = bp.collect(src, bc.load_exclude([s1_rows]))
        self.assertNotIn(s1_sent, {r[1] for name in pools for v in pools[name].values() for r in v[2]})
        self.assertEqual(counts["overlap"], 1)
        # PTT side first: its frozen rows go to the S1 freeze as --exclude
        out = os.path.join(td, "out")
        bp.build(src, out, ["ctune", "cdev"], readings=fake_readings)
        ptt_sent = (rows_of(out, "ctune") + rows_of(out, "cdev"))[0][1]
        a = ids_in("ctune", 1)[0]
        work2 = CE3Discord.prepared(self, [(a, ts(3, "10:00"), f"{ptt_sent}，另一句獨有")], td=tempfile.mkdtemp())
        freeze_quiet(work2, "ctune", others=[os.path.join(out, "ctune.rows.txt"), os.path.join(out, "cdev.rows.txt")])
        self.assertEqual([r[1] for r in rows_of(work2, "ctune")], ["另一句獨有"])


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
