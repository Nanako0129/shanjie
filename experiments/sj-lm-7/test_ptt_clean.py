"""ptt_clean.py: push merging (same account, adjacent, <= 3 minutes), the 50-sentences-per-unit cap, and the page readers, on synthetic HTML.
The p2clean rules are replaced by a stub here (their own behaviour is the copied file's); load_p2clean() is not called."""
import collections
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ptt_clean as P  # noqa: E402


def push(user, content, stamp, tag="推 "):
    return (f'<div class="push"><span class="hl push-tag">{tag}</span><span class="f3 hl push-userid">{user}</span>'
            f'<span class="f3 push-content">: {content}</span><span class="push-ipdatetime"> {stamp}\n</span></div>')


class Merge(unittest.TestCase):
    def test_same_account_adjacent_within_three_minutes(self):
        page = (push("aaa", "這個真的太扯了我覺得", "10/24 12:01") + push("aaa", "應該要退貨才對", "10/24 12:02")
                + push("bbb", "同意", "10/24 12:02") + push("aaa", "另一句話", "10/24 12:03") + push("aaa", "很久以後", "10/24 13:30"))
        self.assertEqual(P.merged_pushes(page), ["這個真的太扯了我覺得應該要退貨才對", "同意", "另一句話", "很久以後"])

    def test_the_gap_is_measured_from_the_previous_push_and_three_minutes_still_merges(self):
        page = push("a", "一", "10/24 12:00") + push("a", "二", "10/24 12:03") + push("a", "三", "10/24 12:06") + push("a", "四", "10/24 12:10")
        self.assertEqual(P.merged_pushes(page), ["一二三", "四"])         # 12:00 to 12:03 merges, 12:03 to 12:06 merges (chain), 12:06 to 12:10 is 4 minutes

    def test_a_different_account_in_between_breaks_the_run_even_for_the_same_account_again(self):
        page = push("a", "甲", "10/24 12:00") + push("b", "乙", "10/24 12:00") + push("a", "丙", "10/24 12:01")
        self.assertEqual(P.merged_pushes(page), ["甲", "乙", "丙"])

    def test_missing_time_merges_same_account_and_empty_pushes_are_skipped(self):
        page = push("a", "甲", "??") + push("a", "", "10/24 12:00") + push("a", "乙", "10/24 12:00")
        self.assertEqual(P.merged_pushes(page), ["甲乙"])

    def test_join_adds_a_space_only_between_ascii_alphanumerics(self):
        self.assertEqual(P.join("iPhone", "15"), "iPhone 15")
        self.assertEqual(P.join("好", "啊"), "好啊")
        self.assertEqual(P.join("iPhone", "很好"), "iPhone很好")


class Pages(unittest.TestCase):
    META = ('<div class="article-metaline"><span class="article-meta-tag">作者</span><span class="article-meta-value">x (y)</span></div>'
            '<div class="article-metaline-right"><span class="article-meta-tag">看板</span><span class="article-meta-value">Gossiping</span></div>{extra}'
            '<div class="article-metaline"><span class="article-meta-tag">標題</span><span class="article-meta-value">[心得] 標題文字</span></div>'
            '<div class="article-metaline"><span class="article-meta-tag">時間</span><span class="article-meta-value">Fri Oct 23 12:00:00 2026</span></div>')
    TAIL = '\n\n今天去看電影，很好看。\n--\n<span class="f2">※ 發信站: 批踢踢實業坊</span>' + push("aaa", "推文內容", "10/24 12:01") + "</div>"

    def test_body_is_the_text_between_the_header_lines_and_the_signature(self):
        for extra in ("", "</div>"):    # the board line is sometimes followed by a stray closing tag
            b = P.body('<div id="main-content" class="bbs-screen bbs-content">' + self.META.format(extra=extra) + self.TAIL)
            self.assertEqual(b.strip(), "今天去看電影，很好看。", extra)

    def test_only_lines_at_the_wrap_width_are_joined(self):
        wrapped = "今天去看了一部電影，劇情真的很好看，演員也都很到位，整體來說非常推薦給大家去看"   # 39 full-width + 1 = 78 columns
        self.assertEqual(P.width(wrapped), 78)
        text = wrapped + "\n一下。\n\n第二段只有一行。\n今天好累\n明天再說"
        self.assertEqual(P.paragraphs(text), wrapped + "一下。\n第二段只有一行。\n今天好累\n明天再說")
        page = '<div id="main-content" class="bbs-screen bbs-content">' + self.META.format(extra="") + "\n\n" + text + "\n--\n" + "</div>"
        self.assertEqual(P.body(page).split("\n"), [wrapped + "一下。", "第二段只有一行。", "今天好累", "明天再說"])

    def test_wrap_width_bounds(self):
        self.assertEqual((P.WRAP_MIN, P.WRAP_MAX), (77, 80))
        for n, joined in ((76, False), (77, True), (80, True), (81, False)):
            line = "a" * n
            self.assertEqual(P.paragraphs(line + "\n好"), line + ("好" if joined else "\n好"), n)

    def test_quoted_reply_lines_are_dropped(self):
        text = "※ 引述《aaa (bbb)》之銘言：\n: 這是被引用的一句話。\n: 第二行引文\n我的回應在這裡，\n接著說下去。\n\n: 另一段引文\n結尾。"
        self.assertEqual(P.paragraphs(text), "我的回應在這裡，\n接著說下去。\n結尾。")
        self.assertEqual(P.paragraphs(":沒有空格的冒號開頭不算引文"), ":沒有空格的冒號開頭不算引文")

    def test_a_page_without_main_content_is_empty(self):
        self.assertEqual(P.body("<html></html>"), "")


class Cap(unittest.TestCase):
    P2 = SimpleNamespace(split_sentences=lambda t: [x for x in t.split("。") if x], reject=lambda s, simp: "han_len" if len(s) < 4 else None,
                         bc=SimpleNamespace(TRAD_SIMP_ONLY=frozenset()))

    def test_fifty_sentences_per_unit_in_order(self):
        texts = [f"第{i}句話好長" for i in range(60)]
        why = collections.Counter()
        kept = P.unit_sentences(self.P2, ["。".join(texts[:30]), "。".join(texts[30:])], why)    # two remarks of one unit share the cap
        self.assertEqual(kept, texts[:50])
        self.assertEqual(why, {"unit_cap": 10})

    def test_rejected_sentences_do_not_count_toward_the_cap(self):
        why = collections.Counter()
        kept = P.unit_sentences(self.P2, ["短。" * 80 + "。".join(f"第{i}句話好長" for i in range(50))], why)
        self.assertEqual(len(kept), 50)
        self.assertEqual(why, {"han_len": 80})

    def test_constants_are_the_contract_values(self):
        self.assertEqual((P.UNIT_CAP, P.MERGE_MINUTES), (50, 3))


class EndToEnd(unittest.TestCase):
    def test_main_writes_body_and_push_units_with_dedupe_and_counts(self):
        stub = Cap.P2
        stub = SimpleNamespace(**vars(stub), dedupe_key=lambda s: s, han_count=len)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "in", "Gossiping"))
            for name, extra in (("M.1", "同一句推文"), ("M.2", "同一句推文")):
                open(os.path.join(d, "in", "Gossiping", name + ".html"), "w", encoding="utf-8").write(
                    '<div id="main-content">正文裡面的一句話。</div>' + push("a", extra, "10/24 12:00") + push("a", "接著說下去", "10/24 12:01"))
            out = os.path.join(d, "out")
            orig, P.load_p2clean = P.load_p2clean, lambda: stub
            try:
                import contextlib, io
                with contextlib.redirect_stdout(io.StringIO()) as buf:
                    P.main(os.path.join(d, "in"), out)
            finally:
                P.load_p2clean = orig
            push_rows = open(os.path.join(out, "push.tsv"), encoding="utf-8").read().splitlines()
            body_rows = open(os.path.join(out, "body.tsv"), encoding="utf-8").read().splitlines()
        self.assertEqual(push_rows, ["Gossiping/M.1\t同一句推文接著說下去"])          # M.2's identical merged remark is a duplicate
        self.assertEqual(len(body_rows), 1)
        self.assertIn('"units": 2', buf.getvalue())


if __name__ == "__main__":
    unittest.main()
