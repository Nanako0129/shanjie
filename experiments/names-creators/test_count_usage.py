"""Tests for count_usage.py on a tiny SYNTHETIC PTT directory (hand-made pages, not crawled). Run: python3 experiments/names-creators/test_count_usage.py"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import count_usage as C  # noqa: E402


def put(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class CountUsage(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.ptt = os.path.join(self.d.name, "ptt")
        page = lambda body: f'<html><div id="main-content">{body}<div class="push"><span class="f3 push-content">: x</span></div></div></html>'
        put(os.path.join(self.ptt, "Gossiping", "1.html"), page("統神說國動<b>統</b>神又來了，統神，統神"))   # tags and repeats: one article
        put(os.path.join(self.ptt, "Gossiping", "2.html"), page("統神神 &amp; 鬼鬼"))
        put(os.path.join(self.ptt, "Stock", "3.html"), page("統神"))
        put(os.path.join(self.ptt, "Stock", "4.html"), page("什麼都沒有"))
        put(os.path.join(self.ptt, "Stock", "note.txt"), "統神")

    def test_articles_and_boards_with_overlapping_names(self):
        got = C.count(["統神", "統神神", "國動", "鬼鬼", "沒人"], self.ptt)
        self.assertEqual(got, {"統神": (3, 2), "統神神": (1, 1), "國動": (1, 1), "鬼鬼": (1, 1), "沒人": (0, 0)})
        self.assertEqual(C.count([], self.ptt), {})

    def test_no_pages_stops_with_a_message(self):
        with self.assertRaises(SystemExit) as cm:
            C.count(["統神"], os.path.join(self.d.name, "nowhere"))
        self.assertIn("no ", str(cm.exception))

    def test_tool_commit_marks_a_dirty_tree(self):
        self.assertEqual(C.tool_commit("abc", ""), "abc")
        self.assertEqual(C.tool_commit("abc", "\n"), "abc")
        self.assertEqual(C.tool_commit("abc", " M tools/x.py\n"), "abc-dirty")

    def test_main_skips_names_already_in_the_lexicon_and_sorts_by_articles(self):
        root = os.path.join(self.d.name, "root")
        put(os.path.join(root, "data", "lexicon", "mcbpmf-data.txt"), "ㄍㄨㄟˇ 鬼 -1.0\nㄍㄨㄟˇ-ㄍㄨㄟˇ 鬼鬼 -2.0\n")
        put(os.path.join(root, "data", "lexicon", "overlay-add.tsv"), "ㄍㄨㄛˊ-ㄉㄨㄥˋ\t國動\t-5.0\toverlay\n")
        put(os.path.join(root, "data", "packs", "it-add.tsv"), "")
        cands, out = os.path.join(self.d.name, "c.txt"), os.path.join(self.d.name, "out.tsv")
        put(cands, "# creators\n鬼鬼\n統神神\n統神\n國動\n統神\n\n")
        C.main([cands, root, self.ptt, out])
        with open(out, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual([l.split()[1] for l in lines[:3]], ["ptt_tree_sha256", "input_sha256", "tool_commit"])
        self.assertEqual(lines[1], f"# input_sha256 {C.sha256_file(cands)}")
        self.assertEqual(lines[3:], ["統神\t3\t2", "統神神\t1\t1"])
        C.main(["--include-known", cands, root, self.ptt, out])          # the lexicon names are counted too
        with open(out, encoding="utf-8") as f:
            self.assertEqual(f.read().splitlines()[3:], ["統神\t3\t2", "國動\t1\t1", "統神神\t1\t1", "鬼鬼\t1\t1"])

    def test_jobs_and_the_tree_hash(self):
        names = ["統神", "統神神", "國動", "鬼鬼"]
        one, tree = C.count(names, self.ptt, 1, with_tree=True)
        self.assertEqual(C.count(names, self.ptt, 2, with_tree=True), (one, tree))
        lines = sorted(f"{b}/{n}\0{C.sha256_file(os.path.join(self.ptt, b, n))}\n" for b in ("Gossiping", "Stock")
                       for n in os.listdir(os.path.join(self.ptt, b)) if n.endswith(".html"))
        self.assertEqual(tree, C.hashlib.sha256("".join(lines).encode()).hexdigest())
        put(os.path.join(self.ptt, "Stock", "4.html"), "改了")
        self.assertNotEqual(C.count(names, self.ptt, 1, with_tree=True)[1], tree)


if __name__ == "__main__":
    unittest.main()
