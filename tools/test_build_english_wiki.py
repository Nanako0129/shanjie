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
