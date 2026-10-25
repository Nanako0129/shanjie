"""g0v_clean.py on synthetic archive pages: one unit per message (ts, each once), subtypes and bots skipped, Slack markup, the (no CC0)
opt-out, the --exclude increment, the per-message cap, and main's output. The p2clean rules are a stub here."""
import contextlib
import html
import io
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g0v_clean as G  # noqa: E402
import ptt_clean  # noqa: E402

STUB = SimpleNamespace(split_sentences=lambda x: [p for p in x.replace("\n", "。").split("。") if p],
                       reject=lambda s, t: "short" if len(s) < 2 else ("code" if "<" in s else None),
                       bc=SimpleNamespace(TRAD_SIMP_ONLY=set()), dedupe_key=lambda s: s, han_count=len)


def msg(ts, text, **extra):
    meta = html.escape(json.dumps({"type": "message", "ts": ts, "text": text, **extra}, ensure_ascii=False))
    return f'<div class="message d-flex mb-2 " id="ts-{ts}"><span class="message-time small" title="{meta}"><a>00:00</a></span></div>'


class Messages(unittest.TestCase):
    def test_each_ts_once_across_pages_and_only_people(self):
        seen = set()
        a = G.messages(msg("1.0", "甲") + msg("2.0", "加入", subtype="channel_join") + msg("3.0", "機器", bot_id="B1")
                       + msg("4.0", "回覆", subtype="thread_broadcast") + msg("1.0", "甲"), seen)
        b = G.messages(msg("4.0", "回覆", subtype="thread_broadcast") + msg("5.0", "乙"), seen)
        self.assertEqual((a, b), ([("1.0", "甲"), ("4.0", "回覆")], [("5.0", "乙")]))

    def test_plain_drops_quotes_mentions_and_emoji_and_keeps_link_labels(self):
        self.assertEqual(G.plain("&gt; 別人的話\n<@U123> 你好 :smile: 看<https://x.tw|這篇>&amp;那篇"), " 你好  看這篇&那篇")
        self.assertEqual(G.plain("網址 <https://x.tw>"), "網址 https://x.tw")
        self.assertEqual(G.plain("我說\n> 新聞原文\n &gt; 另一段引文\n好"), "我說\n好")
        self.assertEqual(G.plain("交通工具*肇*事 公休呀~~~這樣 _XREX_ :smile_cat:"), "交通工具肇事 公休呀~~~這樣 _XREX_ ")
        self.assertEqual(G.plain("我寫的\n```貼來的\n客服回覆```\n也是我寫的"), "我寫的\n\n\n也是我寫的")
        self.assertEqual(G.plain("全食是 7:11-7:25，記者會 16:15:30 :100:"), "全食是 7:11-7:25，記者會 16:15:30 ")


class Clean(unittest.TestCase):
    def test_units_are_messages_with_opt_out_increment_and_duplicates(self):
        rows, why = G.clean(STUB, [("1", "第一句。第二句"), ("2", "第一句。短。舊句"), ("3", "不給用 (no CC0)"), ("4", "第三句")],
                            exclude=frozenset({"舊句"}))
        self.assertEqual(rows, [("1", "第一句"), ("1", "第二句"), ("4", "第三句")])
        self.assertEqual(why, {"duplicate": 1, "short": 1, "already_in_pool": 1, "no_cc0_message": 1})

    def test_one_message_gives_at_most_the_unit_cap(self):
        rows, why = G.clean(STUB, [("1", "。".join(f"句子{i}" for i in range(ptt_clean.UNIT_CAP + 5)))])
        self.assertEqual((len(rows), why["unit_cap"]), (ptt_clean.UNIT_CAP, 5))


class Main(unittest.TestCase):
    def test_reads_month_pages_in_order_and_writes_ts_rows(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "2020-02.html"), "w", encoding="utf-8").write(msg("9.0", "後面的月份"))
            open(os.path.join(d, "2019-12.html"), "w", encoding="utf-8").write(msg("1.0", "前面的月份。池裡有的"))
            open(os.path.join(d, "index.html"), "w", encoding="utf-8").write(msg("5.0", "不是月份頁"))
            open(os.path.join(d, "pool.txt"), "w", encoding="utf-8").write("池裡有的\n")
            out = os.path.join(d, "out.tsv")
            orig, ptt_clean.load_p2clean = ptt_clean.load_p2clean, lambda: STUB
            try:
                with contextlib.redirect_stdout(io.StringIO()) as buf:
                    G.main(d, out, [os.path.join(d, "pool.txt")])
            finally:
                ptt_clean.load_p2clean = orig
            rows = open(out, encoding="utf-8").read().splitlines()
        self.assertEqual(rows, ["1.0\t前面的月份", "9.0\t後面的月份"])
        stats = json.loads(buf.getvalue())
        self.assertEqual((stats["messages"], stats["sentences"], stats["han"]), (2, 2, 10))


if __name__ == "__main__":
    unittest.main()
