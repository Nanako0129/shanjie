"""Tests for tools/build_english_wiki.py (docs/contracts/zhen-words.md section 5.2): synthetic wikitext only, no dump, no network.
Run: python3 -m unittest tools/test_build_english_wiki.py   (from the repo root)."""
import collections, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_english_wiki as W  # noqa: E402


class Words(unittest.TestCase):
    def test_letters_glued_to_digits_are_dropped_and_length_is_3_to_32(self):
        t = "推出 GPT4 與 x86、ab、abc、" + "a" * 32 + "、" + "b" * 33 + " 和 Claude。"
        self.assertEqual(W.words(t), ["abc", "a" * 32, "Claude"])

    def test_only_sentences_with_han(self):
        self.assertEqual(W.words("English only line here.\n中文裡的 OpenAI。"), ["OpenAI"])

    def test_category_file_links_and_urls_are_removed_before_counting(self):
        raw = "'''OpenAI'''是公司，見 https://openai.com/blog 。\n[[Category:人工智慧公司]]\n[[File:Logo.png|thumb|OpenAI 標誌 [[連結]]]]\n推出[[ChatGPT|ChatGPT]]。"
        self.assertEqual(W.words(W.clean(raw)), ["OpenAI", "ChatGPT"])


    def test_original_name_glosses_in_brackets_are_dropped(self):
        t = "阿爾特施文特（德語：Altschwendt）是市鎮(英語:Lowell)，居民用 Windows。"
        self.assertEqual(W.words(t), ["Windows"])

    def test_brackets_without_han_are_dropped(self):
        self.assertEqual(W.words("珍妮特（Janet Shearon）是常規系(Regular)成員，愛用 iPad。"), ["iPad"])

    def test_infobox_and_table_residue_lines_are_skipped(self):
        t = "| established_date1 = 1911年\n{{About|雲|Cloud}}\n! 欄 Header\n成立於 1911 年，用 Linux 跑 api_key。"
        self.assertEqual(W.words(t), ["Linux"])

    def test_template_leftovers_entities_language_codes_and_code_blocks(self):
        raw = ("見{{Cite web|archiveurl=x|accessdate=2020}}與 {{a|editor=Lee|{{b|{{c|{{d|x}}}}}}}} 這句。\n"
               "空&nbsp;格與 -{zh-hans:软件;zh-hant:軟體}- 命题zh-hans:里; zh-hant:裡 用 <math>\\mathcal{L}</math> 和 <code>printf</code> 在 Linux 上。")
        self.assertEqual(W.words(W.clean(raw)), ["Linux"])

    def test_letters_after_a_dot_or_slash_are_not_a_word(self):
        self.assertEqual(W.words("圖檔 logo.png 的濃度 g/mol，用 Python 寫。"), ["Python"])

    def test_english_phrases_longer_than_three_words_are_dropped(self):
        t = "他讀《The Lord of the Rings》，用 Microsoft Office 365 寫心得。"
        self.assertEqual(W.words(t), ["Microsoft", "Office"])

    def test_letters_next_to_accented_letters_are_not_a_word(self):
        self.assertEqual(W.words("畫家 Édouard Manet 的作品。"), ["Manet"])


class Count(unittest.TestCase):
    def test_df_counts_articles_not_occurrences(self):
        acc = W.count(["用 Rust 和 Rust 寫。", "Rust 很快。", "只有 Go 和 Swift。"])
        self.assertEqual(acc["rust"][0], 2)
        self.assertEqual(acc["rust"][1], collections.Counter({"Rust": 3}))
        self.assertEqual(W.emit(acc, 2), ["Rust"])

    def test_canonical_is_the_most_frequent_form_ties_to_the_smallest_bytes(self):
        acc = W.count(["openai 和 OpenAI 與 OpenAI。", "GitHub 和 Github。"])
        self.assertEqual(W.emit(acc, 1), ["GitHub", "OpenAI"])

    def test_counts_file_round_trip(self):
        acc = W.count(["用 Rust 和 rust。", "OpenAI 的 API。"])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "c.tsv")
            W.write_counts(acc, p, "# input x\n")
            back, header = W.read_counts(p)
        self.assertEqual(back, acc)
        self.assertEqual(header, "# input x\n")


if __name__ == "__main__":
    unittest.main()
