"""Tests for tools/build_names.py (docs/contracts/names-lexicon.md §3 items 6 and 8). Run: python3 -m unittest tools.test_build_names   (from the repo root).
No network. Fixtures: tools/testdata/names/ holds items and rows cropped verbatim from the real NSI files and MOE name lists (public data, see
LICENSES/data.md); every Wikipedia/Wikidata response below is SYNTHETIC, hand-made in the real API shape (formatversion=2), not fetched.
The simplified-character sets are patched to tiny stand-ins so the tests do not need the OpenCC files (CI has none). The build runs against
a fake API, a fake reading tool and a fake decoder; one test runs the real evaluation CLI (built on demand with cargo, needs data/lm)."""
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_names as B  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata", "names")
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def put(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def get(path):
    with open(path, "rb") as f:
        return f.read()


def fixture(name):
    with open(os.path.join(DATA, name), "rb") as f:
        return f.read()


@contextlib.contextmanager
def tiny_simplified():
    """is_simplified's tables reduced to 库 统 捞 汉 (simplified) and 庫 統 撈 漢 (traditional); 台 is a marker with no traditional partner, like the real table."""
    with mock.patch.object(B.bc, "MARKERS", set("库统捞汉台")), mock.patch.object(B.bc, "TRAD_ONLY", set("庫統撈漢")), \
            mock.patch.object(B.bc, "SIMP_ONLY", set("库统捞汉")), mock.patch.object(B.bc, "TRAD_SIMP_ONLY", set("库统捞汉")):
        yield


class Rules(unittest.TestCase):
    def test_location_set(self):
        self.assertTrue(B.is_taiwan({"include": ["tw"]}))
        self.assertTrue(B.is_taiwan({"include": ["cn", "tw"], "exclude": ["hk", "mo"]}))
        self.assertFalse(B.is_taiwan({"include": ["001"]}), "001 is the whole world, not Taiwan")
        self.assertFalse(B.is_taiwan({"include": ["001"], "exclude": ["cn", "tw"]}))
        self.assertFalse(B.is_taiwan({"include": ["tw"], "exclude": ["tw"]}))
        self.assertFalse(B.is_taiwan({}))

    def test_nsi_brands_of_the_real_cafe_sample(self):
        with tiny_simplified():
            got = B.nsi_brands(json.loads(fixture("nsi-cafe.json"))["items"])
        # 50嵐 and 85度C have a digit in every name: the name is None (a name with a digit or letter is dropped whole), the brand is still a Taiwan brand.
        # Chatime, Starbucks and Quickly are not Taiwan brands (001 with tw excluded).
        self.assertEqual([(n, q) for n, q, _ in got], [(None, "Q106926258"), (None, "Q4644852"), ("快樂檸檬", "Q109968422"), ("貢茶", "Q5581670"), ("星乃珈琲店", "Q88396880")])

    def test_nsi_name_order_and_filters(self):
        with tiny_simplified():
            tags = {"name": "乙乙", "brand": "甲甲", "brand:zh": "丙丙", "brand:zh-Hant": "丁丁", "name:zh": "戊戊", "name:zh-TW": "己己", "name:zh-Hant": "庚庚"}
            for k in B.NAME_KEYS:        # the contract's order: each key is taken only when every key before it is gone
                self.assertEqual(B.nsi_name(tags), tags[k], k)
                del tags[k]
            self.assertEqual(B.NAME_KEYS, ["name:zh-Hant", "name:zh-TW", "name:zh", "brand:zh-Hant", "brand:zh", "name", "brand"])
            self.assertIsNone(B.nsi_name(tags))
            self.assertEqual(B.nsi_name({"brand:zh": "麥味登", "name": "Mai Wei Deng"}), "麥味登", "Taiwan brands often have only name:zh or brand:zh")
            self.assertEqual(B.nsi_name({"name:zh": "海底捞火锅", "brand:zh": "海底撈火鍋"}), "海底撈火鍋", "a value with simplified-only characters is skipped, the next one is taken")
            self.assertIsNone(B.nsi_name({"name:zh": "海底捞火锅", "name": "Haidilao Hot Pot"}))
            self.assertIsNone(B.nsi_name({"name": "らーめん山頭火"}), "a Japanese name is not all Han")
            self.assertIsNone(B.nsi_name({"name:zh": "CoCo壹番屋", "name": "CoCo壱番屋"}), "a Latin letter anywhere: not taken, nothing is cut out of it")
            self.assertEqual(B.nsi_name({"name:zh": "食其家", "name": "すき家"}), "食其家", "the Japanese name is never used when a Chinese one exists")

    def test_taiwan_names_with_the_marker_character_are_not_simplified(self):
        with tiny_simplified():
            self.assertFalse(B.simplified("台糖"), "is_simplified alone says True for 台糖, 台塑, 台北")
            self.assertTrue(B.bc.is_simplified("台糖"))
            self.assertTrue(B.simplified("库邦"))

    def test_moe_takes_only_numeric_rows_of_the_latest_year(self):
        year, names = B.moe_names(fixture("moe-u1_new.csv"))
        self.assertEqual((year, names), (115, ["國立政治大學", "國立清華大學", "國立臺灣大學", "淡江大學", "朝陽科技大學"]))
        year, names = B.moe_names(fixture("moe-high.csv"))
        self.assertEqual((year, names), (115, ["國立華僑高級中等學校", "私立淡江高中", "國立鳳新高中", "國立高科實驗高中"]), "the stray line 高中部尚未招生 has no numeric 學年度")
        self.assertEqual(B.moe_names("學年度,學校名稱\n9,甲甲\n10,乙乙\n高中部尚未招生\n".encode())[1], ["乙乙"], "10 > 9 numerically, not as text")
        with self.assertRaises(SystemExit):
            B.moe_names("學年度,學校名稱\n高中部尚未招生\n".encode())
        with self.assertRaises(SystemExit):
            B.moe_names("a,b\n1,2\n".encode())

    def test_company_suffix(self):
        self.assertEqual(B.strip_company("統一超商股份有限公司"), "統一超商")
        self.assertEqual(B.strip_company("蝦皮有限公司"), "蝦皮")
        self.assertEqual(B.strip_company("遠東集團"), "遠東")
        self.assertEqual(B.strip_company("好公司"), "好公司", "nothing meaningful would be left")

    def test_abbreviation(self):
        self.assertTrue(B.is_abbreviation("政大", "國立政治大學"))
        self.assertTrue(B.is_abbreviation("台大", "國立臺灣大學"), "台 and 臺 are the same character here")
        self.assertTrue(B.is_abbreviation("北一女", "臺北市立第一女子高級中學"))
        self.assertFalse(B.is_abbreviation("國立政治大學", "國立政治大學"))
        self.assertFalse(B.is_abbreviation("清大", "國立政治大學"))
        self.assertFalse(B.is_abbreviation("NCCU", "國立政治大學"))

    def test_scores_of_every_length(self):
        sc = B.bap.scores(B.bap.base_lexicon())
        self.assertEqual(sorted(sc), list(range(2, 11)))


class Fetching(unittest.TestCase):
    def api(self, **kw):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        return B.NamesApi(d, kw.pop("offline", False), kw.pop("commit", COMMIT))

    def test_allow_list(self):
        ok = ["https://zh.wikipedia.org/w/api.php?action=query", "https://www.wikidata.org/w/api.php?x=1",
              f"https://raw.githubusercontent.com/osmlab/name-suggestion-index/{COMMIT}/data/brands/amenity/cafe.json",
              "https://stats.moe.gov.tw/files/opendata/u1_new.csv", "https://data.gov.tw/x/y.csv"]
        for u in ok:
            self.assertTrue(B.allowed(u, COMMIT), u)
        bad = ["http://zh.wikipedia.org/w/api.php", "https://en.wikipedia.org/w/api.php", "https://zh.wikipedia.org/wiki/酷澎", "https://query.wikidata.org/sparql?query=x",
               f"https://raw.githubusercontent.com/osmlab/name-suggestion-index/{'f' * 40}/data/brands/amenity/cafe.json",
               "https://raw.githubusercontent.com/osmlab/name-suggestion-index/main/data/brands/amenity/cafe.json",
               f"https://raw.githubusercontent.com/someone/else/{COMMIT}/data/brands/a.json", "https://stats.moe.gov.tw/files/x.zip", "https://evil.example/w/api.php",
               "https://zh.wikipedia.org@evil.example/w/api.php", "https://zh.wikipedia.org:8443/w/api.php"]
        for u in bad:
            self.assertFalse(B.allowed(u, COMMIT), u)
        self.assertFalse(B.allowed(ok[2], ""), "no pinned commit: raw.githubusercontent.com is refused")

    def test_urls_outside_the_allow_list_are_refused_before_any_request(self):
        api = self.api()
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")):
            with self.assertRaises(SystemExit):
                api.json_url("https://query.wikidata.org/sparql?query=x")
            with self.assertRaises(SystemExit):
                api.text("https://example.com/a.csv")
            with self.assertRaises(SystemExit):
                api.json_url("https://raw.githubusercontent.com/osmlab/name-suggestion-index/main/data/brands/amenity/cafe.json")

    def test_a_redirect_leaving_the_allow_list_is_refused(self):
        h = B._Redirect(COMMIT)
        with self.assertRaises(SystemExit):
            h.redirect_request(urllib.request.Request("https://stats.moe.gov.tw/files/a.csv"), None, 302, "Found", {}, "https://evil.example/a.csv")
        req = h.redirect_request(urllib.request.Request("https://data.gov.tw/a.csv"), None, 302, "Found", {}, "https://stats.moe.gov.tw/a.csv")
        self.assertEqual(req.full_url, "https://stats.moe.gov.tw/a.csv")

    def test_request_cap_stops_the_run_and_cache_hits_do_not_count(self):
        api = self.api()

        class R:
            def __init__(s, body):
                s.body = body

            def read(s):
                return s.body
        with mock.patch.object(B, "MAX_REQUESTS", 2), mock.patch.object(B.bap.time, "sleep"), \
                mock.patch.object(urllib.request, "urlopen", side_effect=lambda *a, **k: R(b'{"query": {}}')) as op:
            api.wiki(action="query", titles="a")
            api.wiki(action="query", titles="b")
            api.wiki(action="query", titles="a")             # cached: does not count, does not reach the network
            self.assertEqual(op.call_count, 2)
            with self.assertRaises(SystemExit) as cm:
                api.wiki(action="query", titles="c")
            self.assertIn("stop", str(cm.exception))
            self.assertEqual(op.call_count, 2)

    def test_cache_size_cap_stops_the_run(self):
        api = self.api()
        api.size = B.MAX_CACHE + 1
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")):
            with self.assertRaises(SystemExit):
                api.wiki(action="query", titles="a")

    def test_offline_without_a_cache_stops(self):
        api = self.api(offline=True)
        with mock.patch.object(urllib.request, "urlopen", side_effect=AssertionError("network")):
            for call in (lambda: api.wiki(action="query", titles="a"), lambda: api.wikidata(action="wbgetentities", ids="Q1"),
                         lambda: api.text("https://stats.moe.gov.tw/files/opendata/u1_new.csv"),
                         lambda: api.json_url(f"https://raw.githubusercontent.com/osmlab/name-suggestion-index/{COMMIT}/data/brands/amenity/cafe.json")):
                with self.assertRaises(SystemExit) as cm:
                    call()
                self.assertIn("--offline", str(cm.exception))

    def test_text_is_cached_and_read_back_offline(self):
        api = self.api()
        url = "https://stats.moe.gov.tw/files/opendata/u1_new.csv"

        class R:
            def read(s):
                return fixture("moe-u1_new.csv")
        with mock.patch.object(B.time, "sleep"), mock.patch.object(urllib.request, "urlopen", return_value=R()) as op:
            self.assertEqual(api.text(url), fixture("moe-u1_new.csv"))
            self.assertEqual(api.text(url), fixture("moe-u1_new.csv"))
            self.assertEqual(op.call_count, 1)
        offline = B.NamesApi(api.cache, True, COMMIT)
        self.assertEqual(offline.text(url), fixture("moe-u1_new.csv"))


# ---------------------------------------------------------------- the build against a fake API (SYNTHETIC wiki and Wikidata responses)

PARSE = {   # page → (actual title, displaytitle as the zh-tw variant shows it, revid); SYNTHETIC
    "五十嵐": ("五十嵐", "五十嵐", 101), "貢茶": ("貢茶", "<span class=\"mw-page-title-main\">貢茶</span>", 102), "85度C": ("85度C", "85度C", 103),
    "统一超商股份有限公司": ("统一超商股份有限公司", "統一超商股份有限公司", 201), "麥味登 (公司)": ("麥味登 (公司)", "麥味登 (公司)", 202),
    "库邦": ("库邦", "酷澎", 203), "國立政治大學": ("國立政治大學", "國立政治大學", 301),
    "政大": ("政大", "政大", 401), "政治大學": ("政治大學", "政治大學", 402), "台大": ("台大", "臺大", 403),
}
SITELINKS = {"Q106926258": ("五十嵐", 9001), "Q5581670": ("貢茶", 9002), "Q4644852": ("85度C", 9003)}     # SYNTHETIC wbgetentities answers; others have no zhwiki article
CATEGORY = {   # category → (pages, subcategories); a category absent here does not exist
    "Category:台灣手搖茶飲品牌": (["统一超商股份有限公司", "Coupang", "库邦"], ["Category:台灣餐飲子類"]),
    "Category:台灣餐飲子類": (["麥味登 (公司)"], []),
}
REDIRECTS = {"國立政治大學": ["政大", "政治大學", "NCCU", "國立政治大學校長"], "國立臺灣大學": ["台大"]}


class FakeApi:
    """The parts of NamesApi that build() calls, answering from the fixtures above."""

    def __init__(self):
        self.calls = []

    def json_url(self, url):
        name = url.rsplit("/", 1)[1]
        return json.loads(fixture("nsi-" + name))

    def text(self, url):
        return fixture({"u1": "moe-u1_new.csv", "high": "moe-high.csv"}[url])

    def wikidata(self, **p):
        self.calls.append(("wikidata", p["ids"]))
        return {"entities": {q: ({"type": "item", "id": q, "lastrevid": SITELINKS[q][1], "sitelinks": {"zhwiki": {"site": "zhwiki", "title": SITELINKS[q][0], "badges": []}}} if q in SITELINKS
                                 else {"type": "item", "id": q, "lastrevid": 1, "sitelinks": {}}) for q in p["ids"].split("|")}}

    def wiki(self, **p):
        a = p["action"]
        if a == "parse":
            self.calls.append(("parse", p["page"]))
            assert p["variant"] == "zh-tw", "every Wikipedia title is read as its zh-tw display title"
            assert ("redirects" in p) == (p["page"] not in ("政大", "政治大學", "台大")), "an abbreviation page is parsed itself, not followed to the school"
            if p["page"] not in PARSE:
                raise RuntimeError({"code": "missingtitle"})
            t, d, r = PARSE[p["page"]]
            return {"parse": {"title": t, "pageid": r, "revid": r, "displaytitle": d}}
        if p.get("list") == "categorymembers":
            pages, subs = CATEGORY[p["cmtitle"]]
            ms = [{"pageid": 1, "ns": 0, "title": t} for t in pages] if p["cmtype"] == "page" else [{"pageid": 2, "ns": 14, "title": t} for t in subs]
            if p["cmtype"] == "page" and len(ms) > 1 and "cmcontinue" not in p:       # the continue protocol: first answer is truncated
                return {"continue": {"cmcontinue": "page|x", "continue": "-||"}, "query": {"categorymembers": ms[:1]}}
            return {"query": {"categorymembers": ms[1:] if "cmcontinue" in p else ms}}
        if p.get("prop") == "info":
            return {"query": {"pages": [{"title": p["titles"], "pageid": 5} if p["titles"] in CATEGORY else {"title": p["titles"], "missing": True}]}}
        if p.get("prop") == "redirects":
            return {"query": {"pages": [dict({"title": t}, **({"redirects": [{"pageid": 9, "ns": 0, "title": r} for r in REDIRECTS[t]]} if t in REDIRECTS else {})) for t in p["titles"].split("|")]}}
        if "converttitles" in p:
            return {"query": {"pages": [{"title": t} for t in p["titles"].split("|")]}}       # every school has an article of the same title
        raise AssertionError(p)


def fake_readings(special=None):
    """Words → (syllables, CHECK); every character gets its own syllable so no two words collide unless `special` says so."""
    special = special or {}

    def read(words):
        return {w: (special.get(w) or [f"s{ord(c)}" for c in w], False) for w in words}
    return read


def identity_decode(winners=None):
    """Without the names layer every pair decodes to itself; with it (a packs directory) a reading in `winners` decodes to the given word."""
    winners = winners or {}

    def dec(pairs, profile, packs_dir=None):
        return [winners.get(" ".join(s), w) if packs_dir else w for w, s in pairs]
    return dec


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.manual = self.write("manual.tsv", "# comment\n酷澎\t使用者回報\tㄎㄨˋ ㄆㄥˊ\n")

    def write(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def build(self, have=(), readings_tsv="", collisions="", readings=None, decode=None, manual=None, api=None, **kw):
        inputs = [manual or self.manual, self.write("readings.tsv", readings_tsv), self.write("collisions.tsv", collisions)]
        before = [get(p) for p in inputs]
        files, manifest, col, unread, ldrop, check = B.build(
            api or FakeApi(), COMMIT, manual_tsv=inputs[0], readings_tsv=inputs[1], collisions_tsv=inputs[2],
            have=set(have), decode=decode or identity_decode(), readings=readings or fake_readings(), nsi_categories=[("amenity", "cafe"), ("amenity", "restaurant"), ("amenity", "fast_food")],
            wiki_categories=["Category:台灣手搖茶飲品牌", "Category:不存在的分類"], moe_files={"u1_new.csv": "u1", "high.csv": "high"}, **kw)
        self.assertEqual([get(p) for p in inputs], before, "the build only reads names-manual.tsv, names-readings.tsv and names-collisions.tsv")
        self.files, self.manifest, self.col, self.unread, self.ldrop, self.check = files, manifest, col, unread, ldrop, check
        self.words = [l.split("\t")[1] for l in files["names-add.tsv"].splitlines()]
        return files

    def sources_of(self, word):
        return {l.split("\t")[1]: l.split("\t")[2] for l in self.files["names-sources.tsv"].splitlines()[1:] if l.split("\t")[0] == word}

    def test_every_source_lands_with_its_filters(self):
        with tiny_simplified():
            self.build()
        w = set(self.words)
        self.assertLessEqual({"快樂檸檬", "貢茶", "星乃珈琲店", "食其家", "麥當勞"}, w, "NSI names (name:zh and the zh-Hant order)")
        self.assertNotIn("50嵐", w, "a digit anywhere drops the whole name")
        self.assertIn("五十嵐", w, "the Chinese Wikipedia title of a brand with a QID is taken as well as the NSI name")
        self.assertEqual(set(self.sources_of("貢茶")), {"nsi", "nsi-wikipedia"}, "same string from two routes: one row, both sources")
        self.assertIn("wikidata:Q5581670@9002", self.sources_of("貢茶")["nsi-wikipedia"])
        self.assertIn("zhwiki:貢茶@102", self.sources_of("貢茶")["nsi-wikipedia"])
        self.assertEqual({"統一超商", "麥味登", "酷澎"} - w, set(), "category members: zh-tw display title, company suffix and disambiguation stripped")
        self.assertEqual(set(self.sources_of("酷澎")), {"category", "manual"}, "库邦 shows as 酷澎 in zh-tw")
        self.assertNotIn("统一超商股份有限公司", w)
        self.assertLessEqual({"國立政治大學", "淡江大學", "朝陽科技大學", "國立華僑高級中等學校", "國立高科實驗高中"}, w, "school names, latest year only")
        self.assertLessEqual({"政大", "政治大學", "臺大"}, w, "abbreviations are the redirects of the school article; 台大 is shown as 臺大")
        self.assertEqual(self.sources_of("臺大")["school-abbr"], "zhwiki:台大->國立臺灣大學@403")
        self.assertTrue({"NCCU", "國立政治大學校長", "Coupang", "85度C", "Mister Donut"}.isdisjoint(w), "Latin letters drop the whole name; longer or non-subsequence redirects are not abbreviations")
        self.assertTrue({"Chatime", "Starbucks", "Quickly", "海底撈火鍋"}.isdisjoint(w), "001 and exclude-tw brands are not Taiwan brands")
        self.assertEqual(self.manifest["categories"]["Category:不存在的分類"], {"exists": False})
        self.assertEqual(self.manifest["categories"]["Category:台灣手搖茶飲品牌"], {"exists": True, "pages": 4, "subcategories": 1}, "the truncated first answer is continued")
        self.assertEqual(self.manifest["moe"]["u1_new.csv"]["year"], 115)
        self.assertEqual(self.manifest["moe"]["u1_new.csv"]["sha256"], hashlib.sha256(fixture("moe-u1_new.csv")).hexdigest())
        self.assertEqual(self.manifest["nsi_taiwan_brands"]["amenity/cafe"], 5)
        self.assertEqual(self.manifest["dropped"]["title_not_han"], 1)

    def test_list_articles_in_categories_are_not_names(self):
        """2026-10-11 抽樣：分類成員裡的「…列表」條目（臺灣鐵道公司組織列表）不是名字，不收。"""
        pages, subs = CATEGORY["Category:台灣手搖茶飲品牌"]
        PARSE["台灣機車公司列表"] = ("台灣機車公司列表", "台灣機車公司列表", 77)
        CATEGORY["Category:台灣手搖茶飲品牌"] = (pages + ["台灣機車公司列表"], subs)
        try:
            with tiny_simplified():
                self.build()
        finally:
            CATEGORY["Category:台灣手搖茶飲品牌"] = (pages, subs)
            del PARSE["台灣機車公司列表"]
        self.assertNotIn("台灣機車公司列表", self.words)
        self.assertNotIn("台灣機車公司", self.words, "the company suffix is not stripped off a list title")
        self.assertEqual(self.manifest["dropped"]["list_article"], 1)

    def test_company_suffix_is_only_removed_for_category_sources(self):
        items = json.loads(fixture("nsi-cafe.json"))
        items["items"][0]["tags"].update({"name:zh": "五十嵐有限公司", "brand:wikidata": ""})
        api = FakeApi()
        api.json_url = lambda url: items if url.endswith("/cafe.json") else json.loads(fixture("nsi-" + url.rsplit("/", 1)[1]))
        with tiny_simplified():
            self.build(api=api)
        self.assertIn("五十嵐有限公司", self.words)
        self.assertIn("統一超商", self.words)

    def test_words_in_any_of_the_four_files_under_any_reading_are_not_taken(self):
        with tiny_simplified():
            self.build(have={"快樂檸檬", "政大", "麥味登"})
        self.assertTrue({"快樂檸檬", "政大", "麥味登"}.isdisjoint(self.words))
        self.assertIn("貢茶", self.words)
        self.assertEqual(self.manifest["dropped"]["in_lexicon"], 3)

    def test_known_words_reads_all_four_files_whatever_the_reading(self):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        put(os.path.join(d, "mcbpmf-data.txt"), "ㄅㄚ 八 -1.0\nㄅㄚ 巴 -2.0\nㄅㄚˊ 拔 -2.0\n")      # 巴 is not the first word of its reading
        put(os.path.join(d, "overlay-add.tsv"), "ㄇㄚˋ\t罵\t-5.0\twikt\n")
        put(os.path.join(d, "sandhi-add.tsv"), "ㄅㄨˋ-ㄏㄠˇ\t不好\t-4.0\tsandhi\n")
        acg = os.path.join(d, "acg-add.tsv")
        put(acg, "ㄐㄧㄠˋ-ㄏㄨˊ\t叫胡\t-6.0\tacg\n")
        self.assertEqual(B.known_words(d, acg), {"八", "巴", "拔", "罵", "不好", "叫胡"})
        # a name equal to any of them is dropped by the build and caught by the end-of-build check
        with self.assertRaises(SystemExit):
            B.check_disjoint(["叫胡", "酷澎"], B.known_words(d, acg))
        B.check_disjoint(["酷澎"], B.known_words(d, acg))

    def test_end_of_build_check_stops_when_the_dedupe_is_missing(self):
        # mutation guard: if build() let a lexicon word through (say its dedupe were dropped), the intersection check must still stop the run
        with tiny_simplified(), mock.patch.object(B, "check_disjoint", wraps=B.check_disjoint) as chk:
            self.build(have={"貢茶"})
            self.assertEqual(chk.call_count, 1)
        with self.assertRaises(SystemExit):
            B.check_disjoint(["貢茶", "麥味登"], {"貢茶"})

    def test_committed_names_add_shares_no_word_with_the_four_files(self):
        B.check_committed()          # the files in the repo; names-add.tsv absent or empty passes

    def test_committed_check_fails_when_acg_add_gains_a_names_word(self):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        for f in ("mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv"):
            put(os.path.join(d, f), "ㄅㄚ 八 -1.0\n" if f.startswith("mcbpmf") else "")
        put(os.path.join(d, "names-add.tsv"), "ㄎㄨˋ-ㄆㄥˊ\t酷澎\t-7.2\tnames\n")
        acg = os.path.join(d, "acg-add.tsv")
        put(acg, "")
        B.check_committed(d, acg)
        put(acg, "ㄎㄨˋ-ㄆㄥˊ\t酷澎\t-7.0\tacg\n")
        with self.assertRaises(SystemExit):
            B.check_committed(d, acg)

    def test_score_of_a_six_character_name_is_the_length_six_percentile(self):
        with tiny_simplified():
            self.build()
        sc = B.bap.scores(B.bap.base_lexicon())
        rows = [l.split("\t") for l in self.files["names-add.tsv"].splitlines()]
        six = [r for r in rows if len(r[1]) == 6 and "ㄧ" not in r[0] and "ㄅㄨ" not in r[0]]
        self.assertTrue(six)
        for r in six:
            self.assertEqual(float(r[2]), sc[6], r)
        self.assertEqual({r[3] for r in rows}, {"names"})

    def test_names_readings_replace_the_tool_reading_and_must_have_one_syllable_per_character(self):
        wrong = ["ㄓㄠ", "ㄧㄤˊ", "ㄎㄜ", "ㄐㄧˋ", "ㄉㄚˋ", "ㄒㄩㄝˊ"]       # what tools/readings.py really returns for 朝陽科技大學 (2026-10-10)
        right = "ㄔㄠˊ ㄧㄤˊ ㄎㄜ ㄐㄧˋ ㄉㄚˋ ㄒㄩㄝˊ"
        with tiny_simplified():
            self.build(readings=fake_readings({"朝陽科技大學": wrong}))
            self.assertIn("ㄓㄠ-ㄧㄤˊ-ㄎㄜ-ㄐㄧˋ-ㄉㄚˋ-ㄒㄩㄝˊ\t朝陽科技大學\t", self.files["names-add.tsv"])
            self.build(readings=fake_readings({"朝陽科技大學": wrong}), readings_tsv=f"# header\n朝陽科技大學\t{right}\n")
            self.assertIn("ㄔㄠˊ-ㄧㄤˊ-ㄎㄜ-ㄐㄧˋ-ㄉㄚˋ-ㄒㄩㄝˊ\t朝陽科技大學\t", self.files["names-add.tsv"])
            self.assertNotIn("ㄓㄠ-ㄧㄤˊ", self.files["names-add.tsv"])
            with self.assertRaises(SystemExit):
                self.build(readings_tsv="朝陽科技大學\tㄔㄠˊ ㄧㄤˊ ㄎㄜ\n")
            with self.assertRaises(SystemExit):
                self.build(readings_tsv="不在名單\tㄅ ㄅ ㄅ\n")        # checked even for a name that is not in the list

    def test_manual_reading_is_used_and_checked(self):
        with tiny_simplified():
            self.build()
            self.assertIn("ㄎㄨˋ-ㄆㄥˊ\t酷澎\t", self.files["names-add.tsv"])
            with self.assertRaises(SystemExit):
                self.build(manual=self.write("bad.tsv", "酷澎\t備註\tㄎㄨˋ\n"))

    def test_unreadable_names_are_left_out_and_listed(self):
        def read(words):
            return {w: v for w, v in fake_readings()(words).items() if w != "麥當勞"}
        with tiny_simplified():
            self.build(readings=read)
        self.assertNotIn("麥當勞", self.words)
        self.assertEqual(self.unread, ["麥當勞"])

    def test_same_sound_inside_the_list_is_reported_until_it_is_dispositioned(self):
        same = {"貢茶": ["ㄍㄨㄥˋ", "ㄔㄚˊ"], "五十嵐": ["ㄍㄨㄥˋ", "ㄔㄚˊ"]}      # SYNTHETIC: two names pretending to sound alike
        dec = identity_decode({"ㄍㄨㄥˋ ㄔㄚˊ": "貢茶"})
        with tiny_simplified():
            self.build(readings=fake_readings(same), decode=dec)
            self.assertEqual(list(self.col), ["ㄍㄨㄥˋ ㄔㄚˊ"])
            self.assertEqual(self.manifest["unresolved_collision_readings"], 1)
            self.assertIn("ㄍㄨㄥˋ ㄔㄚˊ\t五十嵐\ta\t", B.collisions_text(self.col, self.ldrop))
            self.build(readings=fake_readings(same), decode=dec, collisions="ㄍㄨㄥˋ ㄔㄚˊ\t貢茶\t五十嵐\t使用者決定\n")
            self.assertEqual(self.col, {})
            self.assertEqual(self.manifest["unresolved_collision_readings"], 0)
            self.assertNotIn("五十嵐", self.words)
            self.assertIn("貢茶", self.words)

    def test_a_name_that_displaces_an_existing_word_is_dropped(self):
        same = {"貢茶": ["ㄍㄨㄥˋ", "ㄔㄚˊ"]}
        names_win = identity_decode({"ㄍㄨㄥˋ ㄔㄚˊ": "貢茶"})

        def dec(pairs, profile, packs_dir=None):
            if packs_dir:
                return names_win(pairs, profile, packs_dir)
            return ["供茶" if " ".join(s) == "ㄍㄨㄥˋ ㄔㄚˊ" else w for w, s in pairs]       # without the names layer the existing word 供茶 is first
        with tiny_simplified():
            self.build(have={"供茶"}, readings=fake_readings(same), decode=dec)
        self.assertNotIn("貢茶", self.words)
        self.assertEqual([x[1] for x in self.ldrop], ["貢茶"])
        self.assertEqual(self.manifest["dropped"]["type_c_dropped"], 1)

    def test_two_builds_from_the_same_inputs_are_byte_identical(self):
        col = "# 處置\nㄍㄨㄥˋ ㄔㄚˊ\t貢茶\t五十嵐\t理由\n"
        with tiny_simplified():
            a = self.build(collisions=col)
            b = self.build(collisions=col)
        self.assertEqual(a, b)
        out = os.path.join(self.tmp, "out")
        B.write_all(a, self.manifest, out)
        first = {f: get(os.path.join(out, f)) for f in os.listdir(out)}
        B.write_all(b, self.manifest, out)
        self.assertEqual(first, {f: get(os.path.join(out, f)) for f in os.listdir(out)})
        self.assertEqual(sorted(first), ["names-add.tsv", "names-sources.tsv", "names.json"])

    def test_manifest_has_the_version_counts_and_filters(self):
        with tiny_simplified():
            self.build()
        m = self.manifest
        self.assertTrue(m["version"].startswith("nsi01234567-moe115.115-"))
        self.assertEqual(m["nsi_commit"], COMMIT)
        self.assertEqual(m["words"], len(self.words))
        self.assertEqual(m["rows"], len(self.files["names-add.tsv"].splitlines()))
        self.assertEqual(set(m["sources"]), {"nsi", "nsi-wikipedia", "category", "school", "school-abbr", "manual"})
        self.assertIn("not_han_or_length", m["dropped"])
        self.assertEqual(m["unresolved_collision_readings"], 0)


class RealDecode(unittest.TestCase):
    def test_decode_uses_the_extra_overlay_option(self):
        """The real CLI: without the names layer 酷澎 loses to 酷朋; with a rows file passed as --extra-overlay it wins (needs data/lm and cargo)."""
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        with open(os.path.join(d, "acg-add.tsv"), "w", encoding="utf-8") as f:
            f.write("ㄎㄨˋ-ㄆㄥˊ\t酷澎\t-3.0\tnames\n")      # a high score: with the real length-2 percentile (-7.17) the name still loses before model-v6
        pair = [("酷澎", ["ㄎㄨˋ", "ㄆㄥˊ"])]
        self.assertEqual(B.decode(pair, "chat"), ["酷朋"])
        self.assertEqual(B.decode(pair, "chat", d), ["酷澎"])


if __name__ == "__main__":
    unittest.main()
