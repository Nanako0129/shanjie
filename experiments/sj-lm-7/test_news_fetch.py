"""news_fetch.py on local fixtures only: robots handling, the rate limiter (fake clock), extraction, each cleaning rule, exclusion markers.
No network: every request goes through a fake opener."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import news_fetch as N  # noqa: E402


class Net:
    """Fake clock + opener. pages: url -> (status, bytes) or bytes (200). Records (time, url, headers)."""

    def __init__(self, pages):
        self.pages, self.t, self.calls = pages, 100.0, []

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def opener(self, url, headers, timeout):
        self.calls.append((self.t, url, headers))
        self.t += 0.3     # a request takes time
        r = self.pages.get(url, (404, b""))
        return r if isinstance(r, tuple) else (200, r)

    def fetcher(self, **kw):
        return N.Fetcher(opener=self.opener, clock=self.clock, sleep=self.sleep, **kw)


ROBOTS = b"User-agent: *\nDisallow: /private/\n"


class Robots(unittest.TestCase):
    def test_disallowed_path_is_blocked_and_never_requested(self):
        net = Net({"https://x.test/robots.txt": ROBOTS, "https://x.test/a": b"ok", "https://x.test/private/b": b"no"})
        f = net.fetcher()
        self.assertEqual(f.get("https://x.test/a"), b"ok")
        with self.assertRaises(N.Blocked):
            f.get("https://x.test/private/b")
        self.assertNotIn("https://x.test/private/b", [c[1] for c in net.calls])
        self.assertEqual([c[1] for c in net.calls].count("https://x.test/robots.txt"), 1)   # read once per origin, first

    def test_robots_is_read_before_the_first_page(self):
        net = Net({"https://x.test/robots.txt": ROBOTS, "https://x.test/a": b"ok"})
        net.fetcher().get("https://x.test/a")
        self.assertEqual([c[1] for c in net.calls], ["https://x.test/robots.txt", "https://x.test/a"])

    def test_missing_robots_allows_and_server_error_blocks(self):
        net = Net({"https://x.test/a": b"ok"})
        self.assertEqual(net.fetcher().get("https://x.test/a"), b"ok")      # 404 robots.txt: no restriction
        net = Net({"https://x.test/robots.txt": (503, b""), "https://x.test/a": b"ok"})
        with self.assertRaises(N.Blocked):
            net.fetcher().get("https://x.test/a")
        self.assertEqual(len(net.calls), 1)                                  # only the robots request went out

    def test_official_endpoints_skip_robots(self):
        net = Net({"https://api.test/d": b"[]"})
        net.fetcher().get("https://api.test/d", check_robots=False)
        self.assertEqual([c[1] for c in net.calls], ["https://api.test/d"])

    def test_user_agent_is_sent_and_its_token_is_what_robots_sees(self):
        net = Net({"https://x.test/robots.txt": b"User-agent: shanjie-research-fetcher\nDisallow: /\n", "https://x.test/a": b"ok"})
        with self.assertRaises(N.Blocked):
            net.fetcher().get("https://x.test/a")
        self.assertTrue(all(c[2]["User-Agent"] == N.USER_AGENT for c in net.calls))
        self.assertIn("research", N.USER_AGENT)

    def test_crawl_delay_larger_than_two_seconds_is_honoured(self):
        net = Net({"https://x.test/robots.txt": b"User-agent: *\nCrawl-delay: 5\n", "https://x.test/a": b"1", "https://x.test/b": b"2"})
        f = net.fetcher()
        f.get("https://x.test/a"); f.get("https://x.test/b")
        a, b = [c[0] for c in net.calls[-2:]]
        self.assertGreaterEqual(b - (a + 0.3), 5.0 - 1e-9)


class RateLimit(unittest.TestCase):
    def test_gap_between_requests_is_at_least_two_seconds(self):
        net = Net({f"https://x.test/{i}": b"ok" for i in range(4)})
        f = net.fetcher()
        for i in range(4):
            f.get(f"https://x.test/{i}", check_robots=False)
        starts = [c[0] for c in net.calls]
        for prev, cur in zip(starts, starts[1:]):
            self.assertGreaterEqual(cur - (prev + 0.3), 2.0 - 1e-9)       # measured from the end of the previous request
        self.assertEqual(starts[0], 100.0)                                   # the first request does not wait

    def test_no_extra_sleep_when_enough_time_has_passed(self):
        net = Net({"https://x.test/a": b"1", "https://x.test/b": b"2"})
        f = net.fetcher()
        f.get("https://x.test/a", check_robots=False)
        net.t += 10
        before = net.t
        f.get("https://x.test/b", check_robots=False)
        self.assertEqual(net.calls[-1][0], before)

    def test_robots_request_counts_against_the_limit(self):
        net = Net({"https://x.test/a": b"ok"})
        net.fetcher().get("https://x.test/a")
        (t0, u0, _), (t1, u1, _) = net.calls
        self.assertGreaterEqual(t1 - (t0 + 0.3), 2.0 - 1e-9)


class Extraction(unittest.TestCase):
    def test_p_inside_article_wins_and_script_nav_are_dropped(self):
        h = "<html><nav><p>導覽列</p></nav><script>var a='腳本';</script><article><h1>標題</h1><p>第一段，很長。</p><p>第二&amp;段</p></article><p>頁尾</p></html>"
        self.assertEqual(N.html_paragraphs(h), ["第一段，很長。", "第二&段"])

    def test_without_article_all_p_and_without_p_all_blocks(self):
        self.assertEqual(N.html_paragraphs("<div><p>甲</p><p>乙</p></div>"), ["甲", "乙"])
        self.assertEqual(N.html_paragraphs("<div>甲<br>乙</div>"), ["甲", "乙"])

    def test_plain_text_is_split_by_line(self):
        self.assertEqual(N.html_paragraphs("甲\n\n  乙  \n"), ["甲", "乙"])

    def test_sitemap_and_index_with_lastmod_window(self):
        sm = (b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://x.test/1</loc><lastmod>2026-09-02T10:00:00+08:00</lastmod></url>'
              b'<url><loc>https://x.test/2</loc><lastmod>2026-08-01</lastmod></url><url><loc>https://x.test/3</loc></url></urlset>')
        got = N.sitemap_urls(sm)
        self.assertEqual([g[0] for g in got], ["https://x.test/1", "https://x.test/2", "https://x.test/3"])
        keep = [g[0] for g in got if N.in_window(g[1], "2026-09-01", "2026-10-25")]
        self.assertEqual(keep, ["https://x.test/1", "https://x.test/3"])          # no lastmod: cannot be judged, kept
        idx = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://x.test/s1.xml</loc></sitemap></sitemapindex>'
        self.assertEqual(N.sitemap_urls(idx), [("https://x.test/s1.xml", None, True)])

    def test_records_from_json_csv_xml(self):
        self.assertEqual(N.records_from(b'{"meta":1,"items":[{"id":1,"content":"a"}]}'), [{"id": 1, "content": "a"}])
        self.assertEqual(N.records_from("﻿id,內容\n1,甲\n".encode()), [{"id": "1", "內容": "甲"}])
        xml = b"<rss><channel><item><guid>g1</guid><description>d1</description></item><item><guid>g2</guid><description>d2</description></item></channel></rss>"
        self.assertEqual(N.records_from(xml), [{"guid": "g1", "description": "d1"}, {"guid": "g2", "description": "d2"}])

    def test_record_units_pick_the_text_and_id_fields(self):
        recs = [{"id": 7, "title": "t", "內容": "<p>甲甲</p><p>乙乙</p>"}, {"id": 8, "title": "no text"}, {"title": "T", "content": "丙丙"}]
        self.assertEqual(N.record_units(recs, "d1"), [("d1:7", ["甲甲", "乙乙"]), ("d1:T", ["丙丙"])])

    def test_dataset_downloads(self):
        meta = json.dumps({"result": {"distribution": [{"resourceDownloadUrl": "https://d.test/a.csv", "resourceFormat": "CSV"},
                                                      {"resourceDownloadUrl": "https://d.test/b.pdf", "resourceFormat": "PDF"}]}}).encode()
        self.assertEqual(N.dataset_downloads(meta), ["https://d.test/a.csv"])


class Cleaning(unittest.TestCase):
    def test_field_lines_are_dropped(self):
        units = [("u1", ["內文第一段很長。", "聯絡人：王小明", "發布日期: 2026-10-01", "附件：公告.pdf", "電話：02-1234", "正文第二段也很長。"])]
        out, st = N.clean_pool(units)
        self.assertEqual(out, [("u1", ["內文第一段很長。", "正文第二段也很長。"])])
        self.assertEqual(st["lines_dropped"], 4)

    def test_a_clause_seen_five_times_is_kept_once(self):
        boiler = "歡迎民眾踴躍參加"
        units = [(f"u{i}", [f"本次活動第{'甲乙丙丁戊'[i]}場舉行，{boiler}。"]) for i in range(5)]
        out, st = N.clean_pool(units)
        text = [p for _, ps in out for p in ps]
        self.assertEqual(sum(boiler in p for p in text), 1)
        self.assertIn(boiler, text[0])                                   # the first occurrence is the one kept
        self.assertEqual(st["clauses_repeat_dropped"], 4)
        self.assertTrue(all("本次活動" in p for p in text))               # the rest of each paragraph stays

    def test_four_occurrences_are_left_alone(self):
        units = [(f"u{i}", [f"各有不同的前文{'甲乙丙丁'[i]}，歡迎民眾踴躍參加。"]) for i in range(4)]
        out, st = N.clean_pool(units)
        self.assertEqual(sum("歡迎民眾踴躍參加" in p for _, ps in out for p in ps), 4)
        self.assertNotIn("clauses_repeat_dropped", st)

    def test_short_clauses_never_count_as_boilerplate(self):
        units = [(f"u{i}", [f"好的，今天第{'甲乙丙丁戊己'[i]}號議題。"]) for i in range(6)]
        out, _ = N.clean_pool(units)
        self.assertEqual(sum("好的" in p for _, ps in out for p in ps), 6)

    def test_a_paragraph_that_only_repeated_boilerplate_disappears_and_so_does_an_empty_article(self):
        units = [(f"u{i}", ["歡迎民眾踴躍參加。"]) for i in range(6)]
        out, _ = N.clean_pool(units)
        self.assertEqual([u for u, _ in out], ["u0"])

    def test_exclusion_marker_drops_the_whole_article(self):
        units = [("keep", ["正常文章的第一段。"]), ("drop", ["正文開頭。", "（本文轉載自某處）", "更多正文。"]), ("ad", ["內容⟦贊助⟧"])]
        out, st = N.clean_pool(units, exclude_markers=["本文轉載自", "⟦贊助⟧"])
        self.assertEqual([u for u, _ in out], ["keep"])
        self.assertEqual(st["articles_excluded"], 2)

    def test_boilerplate_pattern_drops_matching_lines_only(self):
        units = [("u", ["正文一。", "－－－廣告－－－", "正文二。"])]
        out, _ = N.clean_pool(units, boilerplate=[r"^－+廣告－+$"])
        self.assertEqual(out, [("u", ["正文一。", "正文二。"])])


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)

    def run_main(self, net, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            N.main(argv, opener=net.opener, clock=net.clock, sleep=net.sleep)
        return buf.getvalue()

    def test_pages_from_sitemap_with_robots_markers_and_tsv(self):
        art = lambda t: f"<html><article><p>{t}</p></article></html>".encode()
        net = Net({
            "https://x.test/robots.txt": ROBOTS,
            "https://x.test/sitemap.xml": b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>/n/1</loc></url><url><loc>/n/2</loc></url>'
                                          b'<url><loc>/private/3</loc></url><url><loc>/n/4</loc></url></urlset>',
            "https://x.test/n/1": art("第一篇的正文。"), "https://x.test/n/2": art("第二篇的正文。⟦贊助⟧"), "https://x.test/private/3": art("不該抓"),
            "https://x.test/n/4": (500, b""),
        })
        out = os.path.join(self.d.name, "o.tsv")
        so = self.run_main(net, ["pages", "--sitemap", "https://x.test/sitemap.xml", "--exclude-marker", "⟦贊助⟧", "--out", out])
        self.assertEqual(open(out, encoding="utf-8").read(), "https://x.test/n/1\t第一篇的正文。\n")
        self.assertNotIn("/private/3", [c[1] for c in net.calls])
        for k in ("blocked_by_robots=1", "fetch_failed=1", "articles_excluded=1", "articles_kept=1"):
            self.assertIn(k, so)
        self.assertNotIn("正文", so)                                        # numbers only
        starts = [c[0] for c in net.calls]
        self.assertTrue(all(b - a >= 2.3 - 1e-9 for a, b in zip(starts, starts[1:])))

    def test_config_file_supplies_markers_and_patterns(self):
        cfg = os.path.join(self.d.name, "c.json")
        json.dump({"exclude_markers": ["由 AI 協助生成"], "boilerplate": ["^廣告$"]}, open(cfg, "w", encoding="utf-8"), ensure_ascii=False)
        urls = os.path.join(self.d.name, "u.txt")
        open(urls, "w").write("# comment\nhttps://x.test/a\nhttps://x.test/b\n")
        net = Net({"https://x.test/a": "<p>甲文章。</p><p>廣告</p>".encode(), "https://x.test/b": "<p>乙文章。由 AI 協助生成</p>".encode()})
        out = os.path.join(self.d.name, "o.tsv")
        self.run_main(net, ["pages", "--urls", urls, "--config", cfg, "--out", out])
        self.assertEqual(open(out, encoding="utf-8").read(), "https://x.test/a\t甲文章。\n")

    def test_press_from_dataset_api_and_direct_urls(self):
        meta = json.dumps({"result": {"distribution": [{"resourceDownloadUrl": "https://d.test/one.json", "resourceFormat": "JSON"}]}}).encode()
        one = json.dumps([{"id": 1, "內容": "第一則新聞稿的內文。\n聯絡人：王小明\n第二段內文。"}], ensure_ascii=False).encode()
        two = "id,content\n9,另一則新聞稿\n".encode()
        net = Net({"https://api.test/ds/8372": meta, "https://d.test/one.json": one, "https://d.test/two.csv": two})
        out = os.path.join(self.d.name, "o.tsv")
        so = self.run_main(net, ["press", "--dataset-id", "8372", "--dataset-api", "https://api.test/ds/{id}", "--dataset-url", "https://d.test/two.csv", "--out", out])
        self.assertEqual(open(out, encoding="utf-8").read().splitlines(),
                         ["d8372:1\t第一則新聞稿的內文。", "d8372:1\t第二段內文。", "u0:9\t另一則新聞稿"])
        self.assertIn("downloads=2", so)
        self.assertNotIn("robots.txt", " ".join(c[1] for c in net.calls))   # official endpoints: no page crawl, no robots

    def test_all_datasets_lists_the_23_ids(self):
        self.assertEqual((len(N.DATASET_IDS), len(set(N.DATASET_IDS))), (23, 23))


if __name__ == "__main__":
    unittest.main()
