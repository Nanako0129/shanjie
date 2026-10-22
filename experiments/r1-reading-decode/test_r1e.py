"""Tests for r1e.py (tags and one judged run on synthetic files). Run: python3 -m unittest test_r1e (in this directory)."""
import os, shutil, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import r1e as E  # noqa: E402

KN = "+kn:abcd1234:θ1:β0.5"
CHAT_ON = f"lm-chat+ctx{KN}+tl:e7c179d8+acg+colloq:2984eeb5"
CHAT_OFF = f"lm-chat+ctx{KN}+acg"
FORMAL = "lm-formal+ctx+tl:e7c179d8+acg+tri:fdd3b05d"


class Tags(unittest.TestCase):
    def bad(self, setting, base, tag):
        with self.assertRaises(SystemExit):
            E.check_tags(setting, base, tag)

    def test_good_tags(self):
        E.check_tags("chat-std", CHAT_ON + "+rc", CHAT_ON + "+rd")
        E.check_tags("chat-off", CHAT_OFF, CHAT_OFF + "+rd")
        E.check_tags("formal-std", FORMAL, FORMAL + "+rd")
        cons = CHAT_ON.replace("+acg", "+rl:conservative+acg")
        E.check_tags("chat-cons", cons + "+rc", cons + "+rd")

    def test_bad_tags(self):
        self.bad("chat-std", CHAT_ON + "+rc", CHAT_ON)                    # no +rd
        self.bad("chat-std", CHAT_ON + "+rc", CHAT_ON + "+rc+rd")         # the reading channel still on
        self.bad("chat-std", CHAT_ON, CHAT_ON + "+rd")                    # baseline is not shipping
        self.bad("chat-off", CHAT_OFF, CHAT_ON + "+rd")                   # small model in an -off run
        self.bad("chat-std", (CHAT_ON + "+rc").replace(KN, ""), CHAT_ON.replace(KN, "") + "+rd")   # no KN
        self.bad("formal-std", FORMAL, FORMAL.replace("+tri:fdd3b05d", "") + "+rd")
        self.bad("chat-cons", CHAT_ON + "+rc", CHAT_ON + "+rd")           # no +rl:conservative


class Judge(unittest.TestCase):
    def test_one_run_counts_and_verdict(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        n = E.R.ROW_COUNTS["typing76"]
        base = [1] * (n - 3) + [0, 0, 0]
        new = [1] * (n - 3) + [1, 1, 0]
        new[0] = 0                                                         # 2 fixes, 1 break
        for k, ok, tag in (("base", base, FORMAL), ("e", new, FORMAL + "+rd")):
            with open(os.path.join(d, f"{k}.formal-std.typing76.r"), "w") as f:
                f.writelines(f"{i}\t{o}\t{0 if o else 1}\t5\n" for i, o in enumerate(ok, 1))
            with open(os.path.join(d, f"{k}.formal-std.typing76.cli.txt"), "w") as f:
                f.write(f"## typing76  {tag}  {{'n': {n}, 'top1': {sum(ok)}, 'oracle@64': {n}, 'top1_sha256': 'x'}}\n")
        r = E.judge_one(d, "formal-std", "typing76")
        self.assertEqual((r["fixes"], r["breaks"], r["net"], r["top1_before"], r["top1_after"]), (2, 1, 1, n - 3, n - 2))
        self.assertTrue(r["verdict"])                                      # clause 2a: breaks <= 2 and net >= 0


if __name__ == "__main__":
    unittest.main()
