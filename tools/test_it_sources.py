"""Tests for tools/it_sources.py (docs/contracts/it-pack.md 2, 2a). Fixtures only, no network."""
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import it_sources as S  # noqa: E402


class Splitter(unittest.TestCase):
    def test_split_names(self):
        self.assertEqual(S.split_names("演算法；資料庫，檔案、串流"), ["演算法", "資料庫", "檔案", "串流"])
        self.assertEqual(S.split_names("記憶體（memory）"), ["記憶體"])
        self.assertEqual(S.split_names("快取(cache)、緩衝（暫存、區）"), ["快取", "緩衝"])   # 括號裡的「、」不切
        self.assertEqual(S.split_names("A/B測試；一；十一個字的名稱太長了啊啊"), [])          # 含英文、單字、超過 10 字都不收
        self.assertEqual(S.split_names("三位元組"), ["三位元組"])

    def test_ai_title(self):
        self.assertEqual(S.ai_title("機器學習"), ["機器學習"])
        self.assertEqual(S.ai_title("GPT-4"), [])
        self.assertEqual(S.ai_title("深度學習 (方法)"), [])
        self.assertEqual(S.ai_title("人"), [])


class FetcherTests(unittest.TestCase):
    def fetcher(self, responses, offline=False, cache=None):
        self.calls = []

        def opener(url):
            self.calls.append(url)
            return responses[url]
        return S.Fetcher(cache or tempfile.mkdtemp(), offline, opener, sleep=lambda s: None)

    def test_cache_and_manifest(self):
        url = "https://zh.wikipedia.org/w/api.php?x=1"
        cache = tempfile.mkdtemp()
        f = self.fetcher({url: b"hello"}, cache=cache)
        self.assertEqual(f.get(url), b"hello")
        self.assertEqual(f.get(url), b"hello")
        self.assertEqual(self.calls, [url])
        m = json.load(open(os.path.join(cache, "manifest.json")))
        (e,) = m.values()
        self.assertEqual((e["url"], e["size"], e["sha256"]), (url, 5, hashlib.sha256(b"hello").hexdigest()))
        self.assertRegex(e["fetched"], r"^\d{4}-\d\d-\d\d$")
        g = self.fetcher({}, offline=True, cache=cache)       # 另一個實例從快取讀、不連網
        self.assertEqual(g.get(url), b"hello")
        self.assertEqual(self.calls, [])

    def test_offline_miss_aborts(self):
        with self.assertRaises(SystemExit):
            self.fetcher({}, offline=True).get("https://zh.wikipedia.org/w/api.php?y=1")

    def test_host_not_allowed(self):
        with self.assertRaises(SystemExit):
            self.fetcher({"https://example.com/a": b"x"}).get("https://example.com/a")

    def test_maxlag_retry(self):
        url = "https://zh.wikipedia.org/w/api.php?z=1"
        seq = [b'{"error":{"code":"maxlag"}}', b'{"ok":1}']
        f = S.Fetcher(tempfile.mkdtemp(), False, lambda u: seq.pop(0), sleep=lambda s: None)
        self.assertEqual(f.get(url), b'{"ok":1}')


class Pipeline(unittest.TestCase):
    def test_candidates_from_cache(self):
        csv_url = "https://opendata.naer.edu.tw/x.csv"
        meta = {"result": {"distribution": [{"resourceFormat": "CSV", "resourceDownloadUrl": csv_url}]}}
        page = lambda **p: S.WIKI + "?" + S.urllib.parse.urlencode(sorted({**p, "format": "json", "formatversion": "2", "maxlag": "5"}.items()))
        cat = lambda c, kind, ns, members: (page(action="query", list="categorymembers", cmtitle=c, cmtype=kind, cmnamespace=ns, cmlimit="max"),
                                            json.dumps({"query": {"categorymembers": [{"title": m} for m in members]}}).encode())
        resp = {S.DATASETS["15416"]: json.dumps(meta).encode(), csv_url: "編號,中文名稱\n1,演算法；資料庫（database）\n2,GPU\n".encode("utf-8-sig")}
        for r in S.AI_ROOTS:
            c = "Category:" + r
            resp.update([cat(c, "page", "0", ["机器学习" if r == "机器学习" else "x"]), cat(c, "subcat", "14", [])])
        k, v = page(action="parse", text="x\n\n机器学习", contentmodel="wikitext", prop="text", disablelimitreport="1", variant="zh-tw"), None
        resp[k] = json.dumps({"parse": {"text": "<p>x</p>\n<p>機器學習</p>"}}).encode()
        f = S.Fetcher(tempfile.mkdtemp(), False, lambda u: resp[u], sleep=lambda s: None)
        self.assertEqual(S.candidates(f), ["機器學習", "演算法", "資料庫"])
        o = S.Fetcher(f.cache, True)                                      # 同一份快取，離線得到同樣的結果
        self.assertEqual(S.candidates(o), ["機器學習", "演算法", "資料庫"])


if __name__ == "__main__":
    unittest.main()
