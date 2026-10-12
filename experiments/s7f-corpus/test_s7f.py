"""S7f corpus tools: unit tests (docs/contracts/s7f-colloquial.md section 4 item 1). Small hand-made fixtures, no network, no OpenCC
files (the simplified-only set is a fixture), no private data.
Run: python3 -W ignore -m unittest experiments/s7f-corpus/test_s7f.py -v   (from the repo root)
"""
import bz2
import contextlib
import csv
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import archive_tw  # noqa: E402
import build_chat  # noqa: E402
import clean  # noqa: E402
import cofacts  # noqa: E402
import discord_overlap  # noqa: E402
import generate  # noqa: E402
import invade  # noqa: E402
import wikitalk  # noqa: E402

SIMP = frozenset("们这说话门们发")       # fixture stand-in for build_counts.SIMP_ONLY
DEV_SENT = "他常常遲到"                  # eval/dev/existing.txt row 3: inside dev302
TUNE_SENT = "這款遊戲的畫面很漂亮"
TYPING_SENT = "回來再順便買早餐"          # eval/dev/user-typing.txt row 2


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def tune_dir(root):
    write(os.path.join(root, "tune", "cvtune.txt"), f"|{TUNE_SENT}|ㄓㄜˋ\n")
    write(os.path.join(root, "tune", "wikitune.txt"), "|維基的句子在這裡|ㄨㄟˊ\n")
    return os.path.join(root, "tune")


def eval_set(tmp):
    return clean.eval_sentences(tune_dir(tmp))[0]


class TestClean(unittest.TestCase):
    def test_scrub_url_email_phone(self):
        text = ("請打0912-345-678或(02)2345-6789或+886 912 345 678問，信寄a.b@example.com，網址https://x.com/a?b=1和www.example.tw看"
                "，2026-10-12共100000元")
        out = list(clean.sentences(clean.scrub(text)))
        joined = "\n".join(out)
        for bad in ("0912", "2345", "886", "@", "example", "https", "www"):
            self.assertNotIn(bad, joined)
        self.assertIn("2026-10-12共100000元", joined)       # a date and an amount are not phone numbers
        self.assertIn("請打", out)                          # the scrubbed string is a break, not glued text
        for keep in ("發布日期（2020-03-15）大家注意", "會議在（2026.10.12）舉行", "總金額(12345678元)", "比例是0.12345678喔"):
            self.assertEqual(clean.scrub(keep), keep)        # dates, amounts and decimals of 8+ digits are not phones
        self.assertEqual(list(clean.sentences(clean.scrub("電話（02-2345-6789）請撥"))), ["電話", "請撥"])   # no stray `）`

    def test_simp_set_is_the_traditional_one(self):
        # build_counts.SIMP_ONLY holds 群 床 秘 峰 ..., standard in Taiwan; TRAD_SIMP_ONLY is the set for traditional sentences
        with mock.patch.object(clean.bc, "load_conv"), mock.patch.object(clean.bc, "SIMP_ONLY", set("们群床")), \
                mock.patch.object(clean.bc, "TRAD_SIMP_ONLY", set("们")):
            simp = clean.load_simp_only()
        f = clean.Filter(simp, set())
        self.assertTrue(f.keep("我把你加進群組了"))
        self.assertTrue(f.keep("我剛起床而已啦"))
        self.assertFalse(f.keep("我们今天去吃飯"))

    def test_eval_overlap_after_conversion(self):
        f = clean.Filter(SIMP, {"在這裡等我"}, convert=lambda s: s.replace("裏", "裡"))
        self.assertFalse(f.keep("你在這裏等我一下"))       # training sees 這裡: the eval sentence is inside
        self.assertTrue(clean.Filter(SIMP, {"在這裡等我"}).keep("你在這裏等我一下"))   # without the converter it is not found

    def test_split(self):
        self.assertEqual(list(clean.sentences("今天好熱。要不要去？\n好啊！走吧…嗯")), ["今天好熱", "要不要去", "好啊", "走吧", "嗯"])

    def test_simplified_length_dedupe(self):
        f = clean.Filter(SIMP, set())
        self.assertFalse(f.keep("我们今天去吃飯"))          # one simplified-only character is enough
        self.assertFalse(f.keep("好的嗎"))                  # 3 characters
        self.assertTrue(f.keep("好的好的"))                 # 4
        self.assertTrue(f.keep("好" * 60))
        self.assertFalse(f.keep("好" * 61))
        self.assertFalse(f.keep("好的好的"))                # duplicate
        self.assertFalse(f.keep("OK OK OK"))                # no Han character
        self.assertEqual(dict(f.removed), {"simplified": 1, "length": 2, "duplicate": 1, "no_han": 1})

    def test_eval_sentence_inside_new_sentence_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            ev = eval_set(tmp)
            self.assertIn(DEV_SENT, ev); self.assertIn(TUNE_SENT, ev); self.assertIn(TYPING_SENT, ev)
            f = clean.Filter(SIMP, ev)
            for s in (f"我說{DEV_SENT}了啦", f"欸{TUNE_SENT}", f"{TYPING_SENT}好了"):
                self.assertFalse(f.keep(s), s)
            self.assertTrue(f.keep("他常常準時到"))
            self.assertEqual(f.removed["eval_overlap"], 3)

    def test_short_eval_rows_do_not_remove(self):
        rows = {"吧": ["user-reported.txt:29"], "試了": ["user-reported.txt:40"], "等一下再說": ["fw:3"], "要不要去": ["cvtune.txt:7"]}
        f = clean.Filter(SIMP, rows)
        self.assertTrue(f.keep("那我們走吧"))                       # a 1-character row inside: not an overlap
        self.assertTrue(f.keep("我試了好幾次吧"))
        self.assertFalse(f.keep("好啦等一下再說吧"))                 # a 5-character row inside: removed
        self.assertFalse(f.keep("你要不要去"))                       # exactly 4 characters: removed
        self.assertEqual(f.removed["eval_overlap"], 2)
        self.assertEqual(f.short_report(), {"user-reported.txt:29": 2, "user-reported.txt:40": 1})

    def test_real_short_row_kept_and_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, info = clean.eval_sentences(tune_dir(tmp))
        self.assertIn("user-reported.txt:29", rows["吧"])
        self.assertEqual(info["below_min_rows"], sum(len(ids) for t, ids in rows.items() if len(t) < 4))
        self.assertGreater(info["below_min_rows"], 0)
        f = clean.Filter(SIMP, rows)
        self.assertTrue(f.keep("那我們走吧"))
        self.assertEqual(f.short_report()["user-reported.txt:29"], 1)
        self.assertNotIn("吧", json.dumps(f.short_report(), ensure_ascii=False))

    def test_eval_sources_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, info = clean.eval_sentences(tune_dir(tmp))
            self.assertEqual(info["dev302"]["rows"], 302)
            for name in ("user-typing.txt", "user-reported.txt", "s7e-function-words.txt", "cvtune.txt", "wikitune.txt"):
                self.assertIn(name, info)
            with self.assertRaises(SystemExit):
                clean.eval_sentences(os.path.join(tmp, "missing"))

    def test_sample_to_cap_exact_and_deterministic(self):
        lines = [f"{i:04d}" + "字" * (i % 50) for i in range(3000)]
        a = clean.sample_to_cap(lines, 20000, seed=7)
        self.assertEqual(sum(map(len, a)), 20000)
        self.assertEqual(a, clean.sample_to_cap(lines, 20000, seed=7))
        self.assertNotEqual(a, clean.sample_to_cap(lines, 20000, seed=8))
        self.assertEqual(a, sorted(a))                      # input order kept
        self.assertEqual(clean.sample_to_cap(lines[:10], 10 ** 6), lines[:10])

    def test_water_fill(self):
        self.assertEqual(clean.water_fill([3, 4, 5], 20), [3, 4, 5])
        self.assertEqual(clean.water_fill([8, 8, 8], 20), [7, 7, 6])
        self.assertEqual(clean.water_fill([2, 8, 8], 12), [2, 5, 5])      # the small source whole, no back-fill beyond the level


class TestArchiveTw(unittest.TestCase):
    def test_files_and_lines(self):
        with tempfile.TemporaryDirectory() as repo:
            write(os.path.join(repo, ".alternates"), "# pairs\n2020-01-01-Talk.md\t2020-01-01-演講.md\n")
            write(os.path.join(repo, "2020-01-01-Talk.md"), "# 2020-01-01 Talk\n\n### Audrey Tang:\n\nThis is English and it is long enough.\n")
            write(os.path.join(repo, "2020-01-01-演講.md"), "# 2020-01-01 演講\n\n### 唐鳳：\n\n這是翻譯過來的稿子喔。\n")
            write(os.path.join(repo, "README.md"), "這不是逐字稿的中文說明檔案。\n")
            write(os.path.join(repo, "2020-02-02-座談.md"),
                  "# 2020-02-02 座談\n\n### 主持人甲：\n\n大家好，今天天氣很好。\n\n> （休息十分鐘）\n\nsli.do：我們來看[這份文件](https://x.tw/a)吧！"
                  "\n\n11:30 我覺得**這樣**可以。\n\n比如：先做這個再說。\n")
            f, counts = clean.Filter(SIMP, set()), clean.collections.Counter()
            kept = archive_tw.run(repo, f, counts)
        self.assertEqual(kept, ["大家好，今天天氣很好", "我們來看這份文件吧", "我覺得這樣可以", "比如：先做這個再說"])  # only sli.do is a label
        self.assertEqual((counts["file_used"], counts["file_alternates"], counts["file_not_chinese"]), (1, 1, 1))
        self.assertEqual(counts["line_blockquote"], 1)


def zip_csv(path, name, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header); w.writerows(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(name, buf.getvalue())
    return path


class TestCofacts(unittest.TestCase):
    def test_only_text_and_reason_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            art = zip_csv(os.path.join(tmp, "articles.csv.zip"), "articles.csv",
                          ["id", "articleType", "status", "text", "userIdsha256", "appId"],
                          [["a1", "TEXT", "NORMAL", "快轉給家人！打0912345678領獎金\n詳情看https://lin.ee/abc", "USERHASH1", "X"],
                           ["a2", "IMAGE", "NORMAL", "圖片裡面的文字內容", "USERHASH2", "X"],
                           ["a3", "TEXT", "BLOCKED", "被封鎖的使用者訊息", "USERHASH3", "X"]])
            req = zip_csv(os.path.join(tmp, "reply_requests.csv.zip"), "reply_requests.csv",
                          ["articleId", "reason", "status", "userIdsha256"],
                          [["a1", "這個看起來很可疑耶", "NORMAL", "USERHASH4"], ["a1", "封鎖的理由文字", "BLOCKED", "USERHASH5"]])
            f, counts = clean.Filter(SIMP, set()), clean.collections.Counter()
            kept = cofacts.run(art, req, f, counts)
            self.assertEqual(kept, ["快轉給家人", "這個看起來很可疑耶"])      # 打 / 領獎金 / 詳情看 are shorter than 4
            self.assertEqual(counts["scrub_phone"], 1); self.assertEqual(counts["scrub_url"], 1)
            self.assertNotIn("USERHASH", "".join(kept))
            users = zip_csv(os.path.join(tmp, "anonymized_users.csv.zip"), "u.csv", ["userIdsha256"], [["x"]])
            with self.assertRaises(SystemExit):
                cofacts.main(["--articles", users, "--requests", req, "--revision", "r", "--tune", tune_dir(tmp), "--out", tmp])


WIKI_XML = """<mediawiki>
  <page>
    <title>某條目</title>
    <ns>0</ns>
    <revision><text bytes="1" xml:space="preserve">條目內文不應該被收進來。</text></revision>
  </page>
  <page>
    <title>Talk:某條目</title>
    <ns>1</ns>
    <revision><text bytes="1" xml:space="preserve">== 關於來源 ==
{{talkheader}}
:我覺得這個來源不太可靠。[[User:Alice|Alice]]（[[User talk:Alice|留言]]）2026年10月1日 (三) 12:34 (UTC)
::同意，建議換成[[某書|另一本書]]比較好。--[[User:Bob|Bob]] 2026年10月1日 (三) 13:00 (UTC)
:我们这边也这样认为啦，大家说是不是。
存檔通知：本頁已經自動存檔完成了。[[User:Cewbot|Cewbot]]（[[User talk:Cewbot|留言]]）
在生平的那個部分補一下吧-- 04:10 2006年5月11日 (UTC)
:我也覺得要改--發言者： 16:56 2007年3月13日 (UTC)
:那就這樣決定--  2016年11月5日 (六) 15：34 (UTC)
您好，Dd19980908
我覺得——其實不用改
您好，Alice。謝謝你補了來源
您好，Alice
:請補上2019-2020的數據[[User:Alice|Alice]] 2026年10月1日 (三) 12:34 (UTC)
這段可以再精簡一點<small>2007年7月23日 (一) 17:45 (UTC)</small>
需要更多來源—以上未簽名的留言由（｜）於2012年5月12日 (六) 07:58加入
:那就先這樣—— 2007年7月23日 (一) 17:45
——不夠豐富
&lt;!-- 隱藏的註解不要收 --&gt;</text></revision>
  </page>
  <page>
    <title>User talk:Alice</title>
    <ns>3</ns>
    <revision><text bytes="1" xml:space="preserve">謝謝你上次幫忙修改條目。</text></revision>
  </page>
  <page>
    <title>Wikipedia:互助客棧/技術</title>
    <ns>4</ns>
    <revision><text bytes="1" xml:space="preserve">請問手機版為什麼打不開？</text></revision>
  </page>
  <page>
    <title>Wikipedia:互助客栈/求助</title>
    <ns>4</ns>
    <revision><text bytes="1" xml:space="preserve">電腦版也一樣打不開。</text></revision>
  </page>
  <page>
    <title>Talk:舊名</title>
    <ns>1</ns>
    <revision><text bytes="1" xml:space="preserve">#重定向 [[Talk:新名]]</text></revision>
  </page>
  <page>
    <title>Wikipedia:投票/某提案</title>
    <ns>4</ns>
    <revision><text bytes="1" xml:space="preserve">投票頁的內容不應該收進來。</text></revision>
  </page>
</mediawiki>
"""


class TestWikitalk(unittest.TestCase):
    def test_pages_signatures_bots_simplified(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "dump.xml.bz2")
            with bz2.open(p, "wt", encoding="utf-8") as f:
                f.write(WIKI_XML)
            f, counts = clean.Filter(SIMP, set()), clean.collections.Counter()
            kept = wikitalk.run([p], f, counts)
        self.assertEqual(kept, ["我覺得這個來源不太可靠", "同意，建議換成另一本書比較好", "在生平的那個部分補一下吧", "我也覺得要改",
                                "那就這樣決定", "我覺得——其實不用改", "謝謝你補了來源", "請補上2019-2020的數據", "這段可以再精簡一點", "需要更多來源", "那就先這樣",
                                "不夠豐富", "謝謝你上次幫忙修改條目", "請問手機版為什麼打不開", "電腦版也一樣打不開"])
        self.assertEqual((counts["para_bot"], counts["sent_greeting"], counts["pages_redirect"]), (1, 3, 1))
        self.assertEqual(f.removed["duplicate"], 0)          # the repeated greeting is a greeting, never seen by the filter
        self.assertEqual(counts["para_simplified"], 1)       # the whole paragraph, though some of its sentences have none
        self.assertEqual((counts["pages_ns1"], counts["pages_ns3"], counts["pages_ns4"]), (1, 1, 2))


class TestBuildChat(unittest.TestCase):
    SRC = [("a", [f"甲{i:03d}" + "句" * (i % 5) for i in range(200)]),         # 1,200 chars, lengths 4..8
           ("b", [f"乙{i:03d}" + "啊" * (i % 5) for i in range(200)] + ["共同的一句話"]),
           ("c", ["丙句子一二三", "共同的一句話"])]

    def test_caps_and_whole_small_source(self):
        out, rep = build_chat.build(self.SRC, seed=1, per_source=1000, total=1500)
        chars = {n: sum(map(len, got)) for n, got in out}
        self.assertEqual(chars["c"], 6)                           # small source whole; its duplicate of b removed
        self.assertEqual(rep["c"]["cross_source_duplicates"], 1)
        self.assertLessEqual(max(chars.values()), 1000)            # per-source cap
        self.assertEqual(sum(chars.values()), 1500)                # total cap, filled exactly
        self.assertEqual(chars["a"], 747); self.assertEqual(chars["b"], 747)
        again, _ = build_chat.build(self.SRC, seed=1, per_source=1000, total=1500)
        self.assertEqual(out, again)

    def test_per_source_cap_alone(self):
        out, _ = build_chat.build(self.SRC, seed=1, per_source=700, total=10 ** 6)
        self.assertEqual([sum(map(len, got)) for _, got in out], [700, 700, 6])

    def test_stop_below_minimum(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = write(os.path.join(tmp, "a.txt"), "一句很短的話\n")
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
                build_chat.main(["--out", tmp, f"a={p}"])
            self.assertEqual(cm.exception.code, 3)
            self.assertFalse(os.path.exists(os.path.join(tmp, "chat-real.txt")))


def invade_repo(root):
    d = os.path.join(root, "invade", "database", "vocabs")
    write(os.path.join(d, "鼠標.yml"), "#\nword    : 鼠標\nbopomofo: ㄕㄨˇ ㄅㄧㄠ\n")
    write(os.path.join(d, "視頻.yml"), "#\nword       : 視頻\n")
    write(os.path.join(d, "計算機.yml"), "#\nword    : 計算機\ndeprecation: 台灣也用\n")
    write(os.path.join(d, "寄.yml"), "#\nword    : 寄\n")
    return os.path.join(root, "invade")


class TestInvade(unittest.TestCase):
    def test_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            terms, info = invade.load(invade_repo(tmp))
        self.assertEqual(terms, {"鼠標", "視頻"})
        self.assertEqual(info["deprecated"], ["計算機"])
        self.assertEqual(info["single_char_excluded"], ["寄"])
        self.assertEqual(info["sha256"], invade.list_sha256({"視頻", "鼠標"}))


SEEDS = ["珍珠奶茶", "捷運", "夜市", "原神", "LINE", "雞排", "颱風假"]


class FakeAPI:
    """Stands in for generate.post: answers each prompt with `reply(seeds, n)` and a fixed cost."""

    def __init__(self, reply, cost=0.001):
        self.reply, self.cost, self.bodies = reply, cost, []

    def __call__(self, host, path, body, key):
        self.bodies.append(body)
        seeds = [w for w in SEEDS if w in body["messages"][0]["content"]]
        content = self.reply(seeds, len(self.bodies))
        usage = {"prompt_tokens": 50, "completion_tokens": 20}
        if self.cost is not None:
            usage["cost"] = self.cost
        return 200, json.dumps({"choices": [{"message": {"content": content}}], "usage": usage}).encode()


def unique_reply(seeds, n):
    return f"「今天{seeds[0]}第{n}次好讚耶{'啊' * (n % 9)}」"


class TestGenerate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.seeds = write(os.path.join(t, "seeds.txt"), "# 種子\n" + "\n".join(SEEDS) + "\n")
        self.args = ["--provider", "cerebras", "--model", "gpt-oss-120b", "--seeds", self.seeds, "--invade", invade_repo(t),
                     "--tune", tune_dir(t), "--out", os.path.join(t, "out"), "--ledger", os.path.join(t, "spend.json"), "--workers", "1"]
        self.patches = [mock.patch.object(clean, "load_simp_only", return_value=SIMP),
                        mock.patch.object(clean, "converter", return_value=lambda s: s),
                        mock.patch.dict(os.environ, {"CEREBRAS_API_KEY": "sk-test-SECRET", "OPENROUTER_API_KEY": "sk-test-SECRET"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_main(self, api, *extra):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(generate, "post", api), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                generate.main(self.args + list(extra))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue() + err.getvalue()

    def files(self):
        d = os.path.join(self.tmp.name, "out")
        return {f: open(os.path.join(d, f), encoding="utf-8").read() for f in os.listdir(d)} if os.path.isdir(d) else {}

    def test_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            f, terms = clean.Filter(SIMP, eval_set(tmp)), clean.Matcher(invade.load(invade_repo(tmp))[0])
        ws = ["夜市", "捷運"]
        self.assertEqual(generate.judge("「等等去夜市吃東西」", ws, terms, f), "等等去夜市吃東西")
        self.assertIsNone(generate.judge("等等去公館吃東西", ws, terms, f))                 # no seed word of this prompt
        self.assertIsNone(generate.judge("等等去雞排店吃東西", ws, terms, f))               # a seed word, but of another prompt
        self.assertIsNone(generate.judge("在捷運上看視頻好無聊", ws, terms, f))             # invade term (multi-character)
        self.assertEqual(generate.judge("逛完夜市再寄信給你", ws, terms, f), "逛完夜市再寄信給你")   # 寄 is a single-character term: not used
        self.assertIsNone(generate.judge("我们去夜市吧好不好", ws, terms, f))               # simplified-only character
        self.assertIsNone(generate.judge("等等去夜市吃東西", ws, terms, f))                 # duplicate
        self.assertIsNone(generate.judge(f"夜市那邊{DEV_SENT}", ws, terms, f))              # eval sentence inside
        self.assertIsNone(generate.judge("去夜市\n然後搭捷運回家", ws, terms, f))
        self.assertEqual(dict(f.removed), {"no_seed": 2, "invade": 1, "simplified": 1, "duplicate": 1, "eval_overlap": 1, "multi_line": 1})

    def test_exact_quota(self):
        api = FakeAPI(unique_reply)
        code, log = self.run_main(api, "--chars", "200", "--budget-usd", "5", "--price-in", "0")
        self.assertEqual(code, 0, log)
        txt = [v for k, v in self.files().items() if k.endswith(".txt")][0]
        self.assertEqual(sum(len(l) for l in txt.split("\n")), 200)
        m = json.loads(self.files()["cerebras-gpt-oss-120b.manifest.json"])
        self.assertEqual((m["chars"], m["quota"]), (200, 200))
        self.assertGreater(m["kept_before_sampling"], 200)
        self.assertEqual(m["invade"]["terms"], 2)

    def test_prompts_carry_only_seed_words_and_the_instruction(self):
        api = FakeAPI(unique_reply)
        code, log = self.run_main(api, "--chars", "100", "--budget-usd", "5")
        self.assertEqual(code, 0, log)
        for b in api.bodies:
            self.assertEqual(len(b["messages"]), 1)
            content = b["messages"][0]["content"]
            ws = sorted((w for w in SEEDS if w in content), key=content.index)
            self.assertTrue(3 <= len(ws) <= 5)
            self.assertEqual(content, generate.prompt_text(ws))
            self.assertEqual(set(b) - {"model", "messages", "max_tokens", "temperature", "reasoning_effort"}, set())
        self.assertNotIn("SECRET", log)
        for name, text in self.files().items():
            self.assertNotIn("SECRET", text, name)
        self.assertNotIn("SECRET", open(os.path.join(self.tmp.name, "spend.json")).read())

    def test_budget_ledger_stops(self):
        api = FakeAPI(unique_reply, cost=0.01)
        code, log = self.run_main(api, "--chars", "100000", "--budget-usd", "0.03")
        self.assertEqual(code, 3)
        self.assertIn("budget", log)
        self.assertEqual(len(api.bodies), 3)
        self.assertAlmostEqual(json.load(open(os.path.join(self.tmp.name, "spend.json")))["usd"], 0.03)
        self.assertFalse(any(k.endswith(".txt") for k in self.files()))     # under quota: no sentence file
        code, _ = self.run_main(api, "--chars", "100000", "--budget-usd", "0.03", "--resume")
        self.assertEqual((code, len(api.bodies)), (3, 3))                   # the ledger persists: nothing more is sent

    def test_under_quota_exits(self):
        api = FakeAPI(lambda seeds, n: "這句話沒有任何種子詞")                # every response fails no_seed
        code, log = self.run_main(api, "--chars", "100", "--budget-usd", "0.005")
        self.assertEqual(code, 3)
        m = json.loads(self.files()["cerebras-gpt-oss-120b.manifest.json"])
        self.assertEqual(m["chars"], 0); self.assertEqual(m["removed"]["no_seed"], len(api.bodies))

    def test_no_cost_and_no_price_stops(self):
        api = FakeAPI(unique_reply, cost=None)
        code, log = self.run_main(api, "--chars", "100", "--budget-usd", "5")
        self.assertEqual((code, len(api.bodies)), (3, 1))
        self.assertIn("budget cannot be enforced", log)

    def test_error_body_with_200_stops_with_the_provider_error(self):
        api = lambda host, path, body, key: (200, json.dumps({"error": {"code": 502, "message": "upstream provider error"}}).encode())
        code, log = self.run_main(api, "--chars", "100", "--budget-usd", "5", "--price-in", "0.5", "--price-out", "1")
        self.assertEqual(code, 3)
        self.assertIn("upstream provider error", log)
        self.assertNotIn('"content"', self.files().get("cerebras-gpt-oss-120b.jsonl", ""))   # not kept as an empty reply

    def test_resume_continues(self):
        api = FakeAPI(unique_reply, cost=0.01)
        self.run_main(api, "--chars", "100000", "--budget-usd", "0.02")
        code, log = self.run_main(api, "--chars", "60", "--budget-usd", "1", "--resume")
        self.assertEqual(code, 0, log)
        raw = self.files()["cerebras-gpt-oss-120b.jsonl"].splitlines()
        self.assertEqual([json.loads(l)["i"] for l in raw], list(range(len(raw))))
        code, _ = self.run_main(api, "--chars", "60", "--budget-usd", "1")
        self.assertEqual(code, "generate: " + os.path.join(self.tmp.name, "out", "cerebras-gpt-oss-120b.jsonl")
                         + " exists; pass --resume to continue it")

    def test_openrouter_provider_order_no_fallback(self):
        api = FakeAPI(unique_reply)
        self.args[1] = "openrouter"
        code, _ = self.run_main(api, "--chars", "50", "--budget-usd", "5")
        self.assertEqual(code, 2)                                           # --or-provider is required
        code, log = self.run_main(api, "--chars", "50", "--budget-usd", "5", "--or-provider", "deepinfra,novita")
        self.assertEqual(code, 0, log)
        self.assertEqual(api.bodies[0]["provider"], {"order": ["deepinfra", "novita"], "allow_fallbacks": False})

    def test_dry_run_sends_nothing(self):
        api = FakeAPI(unique_reply)
        with mock.patch.dict(os.environ, {"CEREBRAS_API_KEY": ""}):
            code, log = self.run_main(api, "--chars", "50", "--budget-usd", "5", "--dry-run", "3")
        self.assertEqual((code, len(api.bodies)), (0, 0))
        self.assertEqual(len(log.strip().split("\n")), 3)
        self.assertTrue(all(l.startswith("請寫一句台灣人") for l in log.strip().split("\n")))


class TestDiscordOverlap(unittest.TestCase):
    def test_counts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = write(os.path.join(tmp, "rows.txt"), "|祕密句子一號|ㄇ\n前文|祕密句子二號|ㄇ\n|完全沒出現|ㄇ\n|祕密句子一號|ㄇ\n")
            c1 = write(os.path.join(tmp, "a.txt"), "前面祕密句子一號後面\n無關\n")
            c2 = write(os.path.join(tmp, "b.txt"), "祕密句子二號\n祕密句子一號\n")
            mask = os.path.join(tmp, "mask.txt")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                discord_overlap.main(["--rows", rows, "--corpus", c1, c2, "--rows-out", mask])
            text = out.getvalue()
            self.assertEqual(text, "rows\t4\na.txt\t2\nb.txt\t3\nany\t3\n")
            for s in ("祕密", "完全", "前文"):
                self.assertNotIn(s, text)
            self.assertEqual(open(mask).read(), "1\n2\n4\n")

    def test_net_skips_the_overlap_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            # rows 1..5: 2 fixed (rows 1, 4), 2 broken (rows 2, 5), row 3 unchanged; rows 4 and 5 overlap
            base = write(os.path.join(tmp, "b.txt"), "1\t0\t2\t5\n2\t1\t0\t5\n3\t1\t0\t5\n4\t0\t1\t5\n5\t1\t0\t5\n")
            new = write(os.path.join(tmp, "n.txt"), "1\t1\t0\t5\n2\t0\t1\t5\n3\t1\t0\t5\n4\t1\t0\t5\n5\t0\t2\t5\n")
            ex = write(os.path.join(tmp, "ex.txt"), "4\n5\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                discord_overlap.main(["--net", base, new, "--exclude", ex, "--weight", "10"])
            self.assertEqual(out.getvalue(), "rows\t3\nfixed\t1\nbroken\t1\nnet\t-9\n")
            bad = write(os.path.join(tmp, "bad.txt"), "1\t1\t0\t5\n2\t0\t1\t9\n3\t1\t0\t5\n4\t1\t0\t5\n5\t0\t2\t5\n")   # row 2's gold length
            with self.assertRaises(SystemExit):
                discord_overlap.main(["--net", base, bad, "--exclude", ex])
            gap = write(os.path.join(tmp, "gap.txt"), "1\t1\t0\t5\n3\t1\t0\t5\n")
            with self.assertRaises(ValueError):                     # evalstats.read_rowstats: rows must be aligned
                discord_overlap.main(["--net", gap, gap, "--exclude", ex])

    def test_min_len_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = write(os.path.join(tmp, "rows.txt"), "|吧|ㄅ\n|好想睡覺喔|ㄏ\n")
            c = write(os.path.join(tmp, "c.txt"), "那就這樣吧\n我好想睡覺喔\n")
            for extra, want in (([], "any\t2\n"), (["--min-len", "4"], "any\t1\n")):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    discord_overlap.main(["--rows", rows, "--corpus", c] + extra)
                self.assertTrue(out.getvalue().endswith(want), extra)


if __name__ == "__main__":
    unittest.main()
