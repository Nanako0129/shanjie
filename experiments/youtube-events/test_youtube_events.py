"""Tests of the youtube-events tools on synthetic data. No network (common.http_get is replaced or never reached) and no real data."""
import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import extract_candidates as E  # noqa: E402
import fetch_videos as FV  # noqa: E402
import fetch_wiki_years as FW  # noqa: E402
import ptt_longterm as PL  # noqa: E402
import route as R  # noqa: E402
import verify_news as V  # noqa: E402

DAY = 86400


class Chapters(unittest.TestCase):
    def test_mm_ss_hh_mm_ss_and_decorated_lines(self):
        d = "影片說明\n0:00 開場\n12:34 - 高歌離席\n1:02:03：結尾\n▶ 05:10 「已知用火」\n(3:21) 括號時間\n沒有時間的行\n10:00"
        self.assertEqual(E.chapters(d), [(0, "開場"), (754, "高歌離席"), (3723, "結尾"), (310, "「已知用火」"), (201, "括號時間")])

    def test_a_time_in_the_middle_of_a_line_is_not_a_chapter(self):
        self.assertEqual(E.chapters("明天 12:30 開播"), [])


class Extract(unittest.TestCase):
    def test_quotes_runs_and_stop_phrases(self):
        self.assertEqual(E.quoted("【峰哥】他說「高歌離席」《某某事件》 “不是這樣” 『abc』"), ["高歌離席", "某某事件", "不是這樣"])
        self.assertEqual(E.han_runs("峰哥 中秋烤肉｜為什麼大家都愛看颱風假嗎"), ["大家都愛看颱風假"])      # series suffix and the question word are cut out; 都 stays
        self.assertEqual(E.han_runs("一二三四五六七八九"), [])                                              # a 9-character run is too long
        self.assertEqual(E.han_runs("好"), [])
        self.assertEqual(E.han_runs("最高法院判決，和平協議，現在罷免"), ["最高法院判決", "和平協議", "現在罷免"])   # single-character function words do not split

    def test_tags_and_wiki_titles(self):
        self.assertEqual([E.tag_string(t) for t in ("罷免", "峰哥", "Taiwan", "颱風 假")], ["罷免", None, None, "颱風假"])
        self.assertEqual([E.wiki_title(t) for t in ("某人 (政治人物)", "太長的標題超過八個字了", "ABC")], ["某人", None, None])

    def test_candidates_rows(self):
        videos = [{"id": "x", "published": "2024-05-20T10:00:00Z", "title": "峰哥 談「罷免」", "description": "0:00 開場\n3:00 聊聊颱風假", "tags": ["罷免", "峰哥"]}]
        events = [{"date": "2024-05", "text": "某事件發生，「大停電」", "links": ["大停電", "2024年", "某人 (政治人物)"]}]
        rows = E.candidates(videos, events)
        self.assertIn(("罷免", "峰哥 談「罷免」", "2024-05-20", "quote"), rows)
        self.assertIn(("罷免", "峰哥 談「罷免」", "2024-05-20", "tag"), rows)
        self.assertIn(("聊聊颱風假", "峰哥 談「罷免」 / 聊聊颱風假", "2024-05-20", "chapter-run"), rows)
        self.assertIn(("大停電", "某事件發生，「大停電」", "2024-05", "wiki-link"), rows)
        self.assertIn(("某人", "某事件發生，「大停電」", "2024-05", "wiki-link"), rows)
        self.assertFalse([r for r in rows if "峰哥" in r[0] or r[0] == "開場"])
        self.assertEqual(len(rows), len(set(rows)))


class Wiki(unittest.TestCase):
    TEXT = ("== 大事記 ==\n=== 1月 ===\n* [[1月3日]]：[[某人]]宣布參選。<ref>x</ref>\n** 後續：[[某黨]]回應\n* 5日，發生'''大停電'''\n"
            "=== 2月 ===\n* 2月 發生[[颱風]]\n== 參見 ==\n* [[不要這個]]事件\n")

    def test_parse(self):
        ev = FW.parse(self.TEXT, 2024)
        self.assertEqual([(e["date"], e["text"], e["links"]) for e in ev], [
            ("2024-01-03", "某人宣布參選。", ["某人"]), ("2024-01-03", "後續：某黨回應", ["某黨"]),
            ("2024-01-05", "發生大停電", []), ("2024-02", "發生颱風", ["颱風"])])

    def test_undated_depth1_bullet_does_not_leak_previous_date(self):
        ev = FW.parse("=== 1月 ===\n* 3日，甲事件發生\n* 乙事件沒有日期\n** 乙的後續報導\n", 2024)
        self.assertEqual([e["date"] for e in ev], ["2024-01-03", "2024-01", "2024-01"])

    def test_link_templates_keep_display_name(self):
        raw = "{{link-en|中文名|English}}與{{tsl|en|Foreign|譯名}}、{{tsl|en|OnlyForeign}}和{{Cite web|x}}結束"
        self.assertEqual(FW.clean(raw), "中文名與譯名、OnlyForeign和結束")
        self.assertEqual(FW.links(raw), ["中文名", "譯名", "OnlyForeign"])

    def test_fetch_uses_one_stubbed_request(self):
        got = []
        def get(url):
            got.append(url)
            return '{"parse": {"wikitext": "hello"}}'.encode()
        self.assertEqual(FW.fetch_wikitext(2021, get), "hello")
        self.assertIn("2021%E5%B9%B4%E8%87%BA%E7%81%A3", got[0])


class Videos(unittest.TestCase):
    def test_paging_and_quota(self):
        def call(endpoint, key, **p):
            self.assertEqual(key, "K")
            if endpoint == "channels":
                return {"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
            if endpoint == "playlistItems":
                return {"items": [{"contentDetails": {"videoId": f"v{i}"}} for i in range(50)], "nextPageToken": "t"} if "pageToken" not in p \
                    else {"items": [{"contentDetails": {"videoId": "last"}}]}
            return {"items": [{"id": i, "snippet": {"publishedAt": "2024-01-01T00:00:00Z", "title": "t", "description": "d"}, "contentDetails": {"duration": "PT1M"}}
                              for i in p["id"].split(",")]}
        videos, units = FV.fetch("UC1", "K", call)
        self.assertEqual((len(videos), units), (51, 1 + 2 + 2))
        self.assertEqual(videos[0]["tags"], [])

    def test_missing_key_stops_before_any_request(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(common, "http_get", side_effect=AssertionError("network")):
            with self.assertRaises(SystemExit):
                FV.main("UC1", "unused.json")


def rss(items):
    body = "".join(f"<item><title>{t} - {s}</title><pubDate>{d}</pubDate><source url='http://x'>{s}</source></item>" for t, d, s in items)
    return f"<rss><channel>{body}</channel></rss>".encode()


class News(unittest.TestCase):
    def test_window(self):
        import datetime as dt
        self.assertEqual(V.window("2024-05-20"), (dt.date(2024, 5, 13), dt.date(2024, 5, 27)))
        self.assertEqual(V.window("2024-02"), (dt.date(2024, 1, 25), dt.date(2024, 3, 7)))
        self.assertIsNone(V.window(""))

    def test_pass_needs_three_headlines_from_two_outlets_inside_the_window(self):
        items = [("高歌離席登上版面", "Mon, 20 May 2024 08:00:00 GMT", "A報"), ("高歌離席再延燒", "Tue, 21 May 2024 08:00:00 GMT", "B報"),
                 ("高歌離席後續", "Wed, 22 May 2024 08:00:00 GMT", "A報"), ("高歌離席太早", "Mon, 01 Apr 2024 08:00:00 GMT", "C報"),
                 ("無關新聞", "Mon, 20 May 2024 08:00:00 GMT", "D報")]
        calls = []
        with tempfile.TemporaryDirectory() as d:
            def get(u):
                calls.append(u)
                return rss(items)
            r = V.verify("高歌離席", "2024-05-20", d, get=get)
            self.assertEqual(r, {"headlines": 3, "outlets": 2, "pass": 1})
            V.verify("高歌離席", "2024-05-20", d, get=get)
            self.assertEqual(len(calls), 1)                                     # second call is served from the cache
            self.assertEqual(len(os.listdir(d)), 1)
            self.assertIn("after%3A2024-05-13", calls[0])
            self.assertEqual(V.verify("高歌離席", "2024-05-20", d, operators=False, get=lambda u: rss(items[:2]))["pass"], 0)

    def test_one_outlet_is_not_enough_and_bad_date_is_not_fetched(self):
        items = [(f"某詞{i}報導", "Mon, 20 May 2024 08:00:00 GMT", "A報") for i in range(4)]
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(V.verify("某詞", "2024-05-20", d, get=lambda u: rss(items))["pass"], 0)
            with mock.patch.object(common, "http_get", side_effect=AssertionError("network")) as g:
                self.assertEqual(V.verify("某詞", "", d)["pass"], "nodate")
                self.assertEqual(g.call_count, 0)
                with self.assertRaises(AssertionError):                     # the default really resolves common.http_get at call time
                    V.verify("某詞", "2024-05-21", d)


def make_ptt(root, board, stamps, text="這個「高歌離席」好笑"):
    os.makedirs(os.path.join(root, board), exist_ok=True)
    for i, ts in enumerate(stamps):
        with open(os.path.join(root, board, f"M.{ts}.A.{i:03X}.html"), "w", encoding="utf-8") as f:
            f.write(f'<div id="main-content"><span>{text if text else "無關"}</span> &amp; {i}</div>')


class Ptt(unittest.TestCase):
    def test_long_term_counts_only_boards_spanning_180_days(self):
        t0 = 1_700_000_000
        with tempfile.TemporaryDirectory() as root:
            for b in range(5):                                      # five boards spanning 200 days, 4 articles each with the string -> 20 articles
                make_ptt(root, f"B{b}", [t0, t0 + 50 * DAY, t0 + 100 * DAY, t0 + 200 * DAY])
            make_ptt(root, "Short", [t0, t0 + 10 * DAY])               # board whose pages cover 10 days: ignored
            make_ptt(root, "Other", [t0 + 5 * DAY, t0 + 260 * DAY], text="")   # qualifies by span but never contains the string
            r = PL.scan(root, ["高歌離席", "不存在"])
            self.assertEqual(r["高歌離席"], {"articles": 20, "boards": 5, "span_days": 200, "long_term": 1})
            self.assertEqual(r["不存在"]["articles"], 0)
            self.assertEqual(PL.scan(root, ["高歌離席"], jobs=2)["高歌離席"], r["高歌離席"])

    def test_each_threshold_is_needed(self):
        t0 = 1_700_000_000
        with tempfile.TemporaryDirectory() as root:
            for b in range(4):                                      # only four boards
                make_ptt(root, f"B{b}", [t0 + i * 10 * DAY for i in range(25)])
            self.assertEqual(PL.scan(root, ["高歌離席"])["高歌離席"], {"articles": 100, "boards": 4, "span_days": 240, "long_term": 0})
        with tempfile.TemporaryDirectory() as root:
            for b in range(5):                                      # 5 boards x 3 articles = 15 < 20
                make_ptt(root, f"B{b}", [t0, t0 + 100 * DAY, t0 + 200 * DAY])
            self.assertEqual(PL.scan(root, ["高歌離席"])["高歌離席"]["long_term"], 0)
        with tempfile.TemporaryDirectory() as root:
            make_ptt(root, "Wide", [t0, t0 + 400 * DAY], text="")        # one board gives the span, others have the string only in 100 days
            for b in range(5):
                make_ptt(root, f"N{b}", [t0 + 150 * DAY + i * DAY for i in range(5)])
            r = PL.scan(root, ["高歌離席"])["高歌離席"]
            self.assertEqual((r["articles"], r["boards"], r["span_days"], r["long_term"]), (0, 0, 0, 0))   # N boards span 4 days: not counted at all


class Route(unittest.TestCase):
    def test_route_priority_and_lexicon_filter(self):
        cands = [{"string": s, "event": f"事件{s}", "date": "2024-05-20", "source": "tag"} for s in ("甲甲", "乙乙", "丙丙", "丁丁", "戊戊")]
        news = [{"string": s, "date": "2024-05-20", "headlines": "5", "outlets": "3", "pass": p} for s, p in
                (("甲甲", "1"), ("乙乙", "1"), ("丙丙", "1"), ("丁丁", "0"), ("戊戊", "1"))]
        ptt = [{"string": "乙乙", "articles": "30", "boards": "6", "span_days": "300", "long_term": "1"},
               {"string": "甲甲", "articles": "30", "boards": "6", "span_days": "300", "long_term": "1"}]
        rows = {r["string"]: r for r in R.build(cands, news, ptt, {"戊戊"}, names=["甲甲"])}
        self.assertEqual({s: r["route"] for s, r in rows.items()}, {"甲甲": "names", "乙乙": "net-slang", "丙丙": "hot-word", "丁丁": "drop"})
        self.assertEqual(rows["乙乙"]["ptt_articles"], "30")

    def test_same_reading_flag_needs_readings_and_counts(self):
        cands = [{"string": "新詞", "event": "e", "date": "2024-05", "source": "tag"}]
        by = {"ㄒㄧㄣ-ㄘˊ": ["心詞", "新詞"]}
        kw = dict(cands=cands, news=[], ptt=[], lex_words={"心詞"}, by_reading=by, readings={"新詞": "ㄒㄧㄣ ㄘˊ"})
        self.assertEqual(R.build(**kw, counts={"心詞": 12})[0]["same_reading"], "心詞:12")
        self.assertEqual(R.build(**kw)[0]["same_reading"], "")

    def test_lexicon_reads_every_layer(self):
        with tempfile.TemporaryDirectory() as d:
            lex, packs = os.path.join(d, "lex"), os.path.join(d, "packs")
            os.makedirs(lex), os.makedirs(packs)
            files = {lex: {"mcbpmf-data.txt": "# c\nㄅㄚ-ㄅㄚ 爸爸 -1.0\n", "overlay-add.tsv": "ㄅㄚ-ㄅㄚ\t罷罷\t-5\twikt\n", "names-add.tsv": "ㄇ-ㄇ\t名名\t-5\tnames\n",
                              "slang-add.tsv": "# c\n梗梗\tㄍㄥˇ ㄍㄥˇ\tPTT\n"}, packs: {"acg-add.tsv": "ㄅ-ㄅ\t動動\t-5\tacg\n", "it-add.tsv": "ㄅ-ㄆ\t資資\t-5\tit\n"}}
            for folder, fs in files.items():
                for n, t in fs.items():
                    with open(os.path.join(folder, n), "w", encoding="utf-8") as f:
                        f.write(t)
            words, by = R.lexicon(lex, packs)
            self.assertEqual(words, {"爸爸", "罷罷", "名名", "梗梗", "動動", "資資"})
            self.assertEqual(by["ㄅㄚ-ㄅㄚ"], ["爸爸", "罷罷"])
            self.assertEqual(by["ㄍㄥˇ-ㄍㄥˇ"], ["梗梗"])

    def test_main_end_to_end_on_tiny_tsvs(self):
        with tempfile.TemporaryDirectory() as d:
            lex, packs = os.path.join(d, "lex"), os.path.join(d, "packs")
            os.makedirs(lex), os.makedirs(packs)
            for p in ("mcbpmf-data.txt", "overlay-add.tsv", "names-add.tsv", "slang-add.tsv"):
                open(os.path.join(lex, p), "w").close()
            for p in ("acg-add.tsv", "it-add.tsv"):
                open(os.path.join(packs, p), "w").close()
            common.write_tsv(os.path.join(d, "c.tsv"), ("string", "event", "date", "source"), [{"string": "高歌離席", "event": "e", "date": "2024-05-20", "source": "tag"}])
            common.write_tsv(os.path.join(d, "n.tsv"), ("string", "date", "headlines", "outlets", "pass"), [{"string": "高歌離席", "date": "2024-05-20", "headlines": 4, "outlets": 3, "pass": 1}])
            common.write_tsv(os.path.join(d, "p.tsv"), ("string", "articles", "boards", "span_days", "long_term"), [{"string": "高歌離席", "articles": 1, "boards": 1, "span_days": 0, "long_term": 0}])
            with contextlib.redirect_stdout(io.StringIO()):
                R.main([os.path.join(d, "c.tsv"), os.path.join(d, "n.tsv"), os.path.join(d, "p.tsv"), os.path.join(d, "out.tsv"), "--lexicon-dir", lex, "--packs-dir", packs])
            self.assertEqual([r["route"] for r in common.read_tsv(os.path.join(d, "out.tsv"))], ["hot-word"])


if __name__ == "__main__":
    unittest.main()
