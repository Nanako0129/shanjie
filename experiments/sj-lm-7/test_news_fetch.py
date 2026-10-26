"""news_fetch.py on local fixtures only: robots handling, the rate limiter (fake clock), extraction, each cleaning rule, exclusion markers.
No network: every request goes through a fake opener."""
import contextlib
import hashlib
import http.client
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
        if isinstance(r, Exception):
            raise r
        if not isinstance(r, tuple):
            r = (200, r)
        return r + ("",) * (3 - len(r))

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

    def test_robots_status_rules(self):
        net = Net({"https://x.test/a": b"ok"})
        self.assertEqual(net.fetcher().get("https://x.test/a"), b"ok")      # 404 robots.txt: no restriction
        net = Net({"https://x.test/robots.txt": (410, b""), "https://x.test/a": b"ok"})
        self.assertEqual(net.fetcher().get("https://x.test/a"), b"ok")      # any other 4xx too
        for status in (401, 403, 503):                                       # access denied: disallow all (as RobotFileParser); 5xx: skip the origin
            net = Net({"https://x.test/robots.txt": (status, b""), "https://x.test/a": b"ok"})
            with self.assertRaises(N.Blocked, msg=status):
                net.fetcher().get("https://x.test/a")
            self.assertEqual(len(net.calls), 1, status)                      # only the robots request went out

    def test_unreachable_robots_skips_the_origin(self):
        net = Net({"https://x.test/robots.txt": ConnectionResetError("boom"), "https://x.test/a": b"ok"})
        with self.assertRaises(N.Blocked):
            net.fetcher().get("https://x.test/a")
        net = Net({"https://x.test/robots.txt": http.client.IncompleteRead(b"x"), "https://x.test/a": b"ok"})
        with self.assertRaises(N.Blocked):
            net.fetcher().get("https://x.test/a")

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
    def test_unclosed_p_is_closed_by_the_next_p_or_a_block_tag(self):
        h = "<p>第一段<p>第二段<div class=nav>導覽列首頁</div><footer>版權所有</footer>"
        self.assertEqual(N.html_paragraphs(h), ["第一段", "第二段"])
        self.assertEqual(N.html_paragraphs("<article><p>甲<br>乙<ul><li>清單</li></ul><p>丙</article><p>外面</p>"), ["甲", "乙", "丙"])   # <br> still ends a block; the list and the unclosed second <p> do not leak
        self.assertEqual(N.html_paragraphs("<p>甲<b>粗</b>乙<span>丙</span></p>"), ["甲粗乙丙"])

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

    def test_record_units_pick_the_text_and_id_fields_and_never_use_a_title(self):
        third = {"title": "標題文字", "content": "丙丙"}
        recs = [{"id": 7, "title": "t", "內容": "<p>甲甲</p><p>乙乙</p>"}, {"id": 8, "title": "no text"}, third]
        got = N.record_units(recs, "d1")
        h = hashlib.sha256(json.dumps(third, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        self.assertEqual(got, [("d1:7", ["甲甲", "乙乙"]), (f"d1:h{h}", ["丙丙"])])    # no id field: a stable hash of the record
        self.assertNotIn("標題", got[1][0])

    def test_no_text_field_is_a_failure_not_zero_units(self):
        with self.assertRaises(N.NoTextField):
            N.record_units([{"id": 1, "x": "y"}], "d")
        with self.assertRaises(N.NoTextField):
            N.record_units([], "d")

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


class Decoding(unittest.TestCase):
    def test_header_then_bom_then_utf8_then_big5(self):
        big5 = "臺灣新聞稿".encode("cp950")
        self.assertEqual(N.decode_body(big5, "text/html; charset=Big5"), "臺灣新聞稿")
        self.assertEqual(N.decode_body(big5), "臺灣新聞稿")                                  # not UTF-8: cp950 fallback
        self.assertEqual(N.decode_body("甲乙".encode("utf-8")), "甲乙")
        self.assertEqual(N.decode_body("\ufeff甲乙".encode("utf-8")), "甲乙")                  # BOM stripped
        self.assertEqual(N.decode_body("甲乙".encode("utf-16"), "text/plain"), "甲乙")           # BOM-marked UTF-16
        self.assertEqual(N.decode_body("甲乙".encode("utf-8"), "text/html; charset=nonsense"), "甲乙")   # unknown label: fall through
        self.assertEqual(N.decode_body("甲乙".encode("utf-8"), "text/html; charset=big5"), "甲乙")        # header that cannot decode: fall through
        with self.assertRaises(UnicodeDecodeError):
            N.decode_body(b"<p>\x81</p>")                                                  # no silent errors=replace

    def test_big5_csv_download(self):
        body = "id,內容\n1,第一則新聞稿內文\n".encode("cp950")
        self.assertEqual(N.records_from(body), [{"id": "1", "內容": "第一則新聞稿內文"}])
        self.assertEqual(N.records_from(body, "text/csv; charset=big5")[0]["內容"], "第一則新聞稿內文")
        self.assertEqual(N.records_from('[{"id":1,"content":"臺"}]'.encode("cp950")), [{"id": 1, "content": "臺"}])


class PublishDate(unittest.TestCase):
    def test_meta_then_jsonld_then_time(self):
        self.assertEqual(N.publish_date('<meta property="article:published_time" content="2026-09-14T08:30:00+08:00"><time datetime="2026-01-01">x</time>'), "2026-09-14")
        ld = '<script type="application/ld+json">{"@graph":[{"@type":"Person"},{"@type":"NewsArticle","datePublished":"2026-10-02T01:00:00Z"}]}</script>'
        self.assertEqual(N.publish_date(ld + '<time datetime="2026-01-01">x</time>'), "2026-10-02")
        self.assertEqual(N.publish_date('<p>x</p><time datetime="2026-09-30 12:00">y</time>'), "2026-09-30")
        self.assertIsNone(N.publish_date('<p>沒有日期</p><script type="application/ld+json">{broken</script><meta property="og:title" content="x">'))

    def test_window(self):
        self.assertTrue(N.in_window("2026-09-01T00:00:00", "2026-09-01", "2026-10-25"))
        self.assertTrue(N.in_window("2026-10-25", "2026-09-01", "2026-10-25"))
        self.assertFalse(N.in_window("2026-10-26", "2026-09-01", "2026-10-25"))
        self.assertFalse(N.in_window("2026-08-31", "2026-09-01", None))
        self.assertTrue(N.in_window("1999-01-01", None, None))


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
        for k in ("blocked_by_robots=1", "failed_http_status=1", "articles_excluded=1", "articles_kept=1"):
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

    def sitemap(self, *locs):
        return ('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(f"<url><loc>{l}</loc>{m}</url>" for l, m in locs) + "</urlset>").encode()

    def test_failures_are_counted_by_kind_and_the_run_goes_on(self):
        art = lambda t: f"<article><p>{t}</p></article>".encode()
        net = Net({
            "https://x.test/sitemap.xml": self.sitemap(("/ok1", ""), ("/read", ""), ("http://[bad", ""), ("javascript:void(0)", ""), ("/gone", ""),
                                                        ("/garbled", ""), ("/net", ""), ("/ok2", "")),
            "https://x.test/ok1": art("第一篇。"), "https://x.test/ok2": art("第二篇。"),
            "https://x.test/read": http.client.IncompleteRead(b"par"), "https://x.test/gone": (404, b""),
            "https://x.test/garbled": b"<p>\x81</p>", "https://x.test/net": ConnectionResetError("reset"),
        })
        out = os.path.join(self.d.name, "o.tsv")
        so = self.run_main(net, ["pages", "--sitemap", "https://x.test/sitemap.xml", "--out", out])
        self.assertEqual(open(out, encoding="utf-8").read().splitlines(), ["https://x.test/ok1\t第一篇。", "https://x.test/ok2\t第二篇。"])
        for k in ("failed_protocol=1", "failed_bad_url=2", "failed_http_status=1", "failed_decode=1", "failed_network=1", "articles_kept=2"):
            self.assertIn(k, so)
        self.assertFalse(os.path.exists(out + ".partial"))

    def test_a_crash_keeps_what_was_already_fetched(self):
        art = lambda t: f"<p>{t}</p>".encode()
        net = Net({"https://x.test/a": art("甲文章。"), "https://x.test/b": art("乙文章。"), "https://x.test/c": RuntimeError("unexpected")})
        urls = os.path.join(self.d.name, "u.txt")
        open(urls, "w").write("https://x.test/a\nhttps://x.test/b\nhttps://x.test/c\n")
        out = os.path.join(self.d.name, "o.tsv")
        with self.assertRaises(RuntimeError):
            self.run_main(net, ["pages", "--urls", urls, "--out", out])
        self.assertEqual(open(out + ".partial", encoding="utf-8").read().splitlines(), ["https://x.test/a\t甲文章。", "https://x.test/b\t乙文章。"])
        self.assertFalse(os.path.exists(out))

    def test_page_date_window(self):
        page = lambda d: (f'<meta property="article:published_time" content="{d}"><article><p>文章{d[5:7]}。</p></article>' if d else "<p>無日期。</p>").encode()
        net = Net({
            "https://x.test/sitemap.xml": self.sitemap(("/in", "<lastmod>2026-09-10</lastmod>"), ("/old-lastmod", "<lastmod>2026-08-01</lastmod>"),
                                                        ("/edited-later", "<lastmod>2026-11-30</lastmod>"), ("/nolastmod-out", ""),
                                                        ("/nodate", "<lastmod>2026-10-01</lastmod>")),
            "https://x.test/in": page("2026-09-10T08:00:00+08:00"), "https://x.test/old-lastmod": page("2026-08-01"),
            "https://x.test/edited-later": page("2026-10-20"), "https://x.test/nolastmod-out": page("2026-08-15"), "https://x.test/nodate": page(None),
        })
        out = os.path.join(self.d.name, "o.tsv")
        so = self.run_main(net, ["pages", "--sitemap", "https://x.test/sitemap.xml", "--lastmod-from", "2026-09-01", "--lastmod-to", "2026-10-25", "--out", out])
        self.assertEqual([l.split("\t")[0] for l in open(out, encoding="utf-8").read().splitlines()], ["https://x.test/in", "https://x.test/edited-later"])
        for k in ("out_of_window=1", "no_date=1", "articles_kept=2"):    # old-lastmod was never fetched; no-lastmod is judged by its page date
            self.assertIn(k, so)
        self.assertNotIn("https://x.test/old-lastmod", [c[1] for c in net.calls])

    def test_out_must_be_a_tsv(self):
        for argv in (["pages", "--urls", "u", "--out", os.path.join(self.d.name, "o.txt")], ["press", "--dataset-url", "https://d.test/a", "--out", "o"]):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                N.main(argv, opener=Net({}).opener)

    def test_press_big5_csv_and_a_download_without_text_fields(self):
        big5 = "id,內容\n5,另一則新聞稿的內文\n".encode("cp950")
        net = Net({"https://d.test/b5.csv": big5, "https://d.test/none.json": b'[{"id": 1, "x": "y"}]', "https://d.test/bad.json": b"[{"})
        out = os.path.join(self.d.name, "o.tsv")
        so = self.run_main(net, ["press", "--dataset-url", "https://d.test/b5.csv", "--dataset-url", "https://d.test/none.json",
                                 "--dataset-url", "https://d.test/bad.json", "--out", out])
        self.assertEqual(open(out, encoding="utf-8").read(), "u0:5\t另一則新聞稿的內文\n")
        self.assertIn("failed_no_text_field=1", so)
        self.assertIn("failed_parse=1", so)

    def test_all_datasets_lists_the_23_ids(self):
        self.assertEqual((len(N.DATASET_IDS), len(set(N.DATASET_IDS))), (23, 23))


if __name__ == "__main__":
    unittest.main()
