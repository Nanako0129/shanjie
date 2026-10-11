"""S7a: run_sets.py plumbing with lm_eval.py stubbed out (no model file needed): the command line, the .top1 file, the score loop."""
import contextlib
import io
import argparse
import os
import subprocess
import tempfile
import unittest
from unittest import mock

import numpy as np

import run_sets
import tinylm


class TestRunSets(unittest.TestCase):
    def test_dumps_then_score(self):
        chars = list("你好嗎我他")
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            dump = cmd[cmd.index("--dump") + 1]
            with open(dump, "w", encoding="utf-8") as f:
                f.write("1\t1\t你好\t-1.0\n1\t2\t你嗎\t-2.0\n")
            return subprocess.CompletedProcess(cmd, 0, "## x  lm-chat+ctx  {'n': 1, 'top1': 1, 'oracle@64': 1, 'top1_sha256': 'ab'}\n", "")

        with tempfile.TemporaryDirectory() as td:
            tune = os.path.join(td, "tune")
            os.makedirs(tune)
            with open(os.path.join(tune, "cvtune.txt"), "w", encoding="utf-8") as f:
                f.write("|你好|ㄋㄧˇ ㄏㄠˇ\n")
            tinylm.save_params(os.path.join(td, "tinylm-S.npz"), tinylm.random_params(chars, 1, 16, 2, seed=1), chars, 1, 2)
            with mock.patch.object(run_sets.subprocess, "run", fake_run), mock.patch.object(run_sets, "check_model"), contextlib.redirect_stdout(io.StringIO()):
                run_sets.main(["dumps", "--work", td, "--tune", tune, "--sets", "cvtune"])
                run_sets.main(["score", "--work", td, "--tune", tune, "--sets", "cvtune", "--models", "S", "--out", os.path.join(td, "sc"), "--ppl"])
            self.assertEqual(len(calls), 2)
            self.assertIn("--context", calls[0])
            self.assertEqual([c[c.index("--profile") + 1] for c in calls], ["chat", "formal"])
            self.assertEqual(open(os.path.join(td, "dumps", "cvtune.chat.top1")).read(), "1")
            for setting in ("chat", "formal"):
                self.assertTrue(os.path.isfile(os.path.join(td, "sc", f"S.{setting}.cvtune.jsonl")))
            self.assertTrue(os.path.isfile(os.path.join(td, "ppl", "S.cvtune.json")))
            self.assertFalse(os.path.exists(os.path.join(td, "ppl", "S.wikitune.json")))

    def test_dumps_refuse_a_wrong_model(self):
        with tempfile.TemporaryDirectory() as td:
            lm = os.path.join(td, "bigram.sjlm")
            open(lm, "wb").write(b"not the pinned model")
            with mock.patch.object(run_sets.subprocess, "run") as run:
                with self.assertRaises(SystemExit) as e:
                    run_sets.main(["dumps", "--work", td, "--sets", "dev302", "--lm", lm])
            self.assertIn("bigram.sjlm", str(e.exception.code))
            run.assert_not_called()
            # right model, wrong classes.sjc beside it
            pinned = os.path.join(run_sets.ROOT, "data", "lm", "bigram.sjlm")
            if os.path.isfile(pinned):
                os.remove(lm)
                os.symlink(pinned, lm)
                open(os.path.join(td, "classes.sjc"), "wb").write(b"wrong")
                with self.assertRaises(SystemExit) as e:
                    run_sets.main(["dumps", "--work", td, "--sets", "dev302", "--lm", lm])
                self.assertIn("classes.sjc", str(e.exception.code))

    def test_missing_summary_line_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as td:
            ok = subprocess.CompletedProcess([], 0, "garbage output\n", "")
            with mock.patch.object(run_sets, "check_model"), mock.patch.object(run_sets.subprocess, "run", return_value=ok):
                with self.assertRaises(SystemExit) as e:
                    run_sets.main(["dumps", "--work", td, "--sets", "dev302"])
            self.assertIn("no summary line", str(e.exception.code))
            self.assertIn("garbage output", str(e.exception.code))

    def test_user_reported_is_pinned_to_the_first_66_rows_only_when_asked(self):
        """S7b §2.2: --reported-rows 66 adds --limit 66 and checks the head first; without it the whole file is used."""
        pinned, whole = argparse.Namespace(tune=None, discord=None, reported_rows=66), argparse.Namespace(tune=None, discord=None, reported_rows=None)
        self.assertEqual(run_sets.rows_args("user-reported", pinned)[-2:], ["--limit", "66"])
        self.assertNotIn("--limit", run_sets.rows_args("user-reported", whole))
        real = open(os.path.join(run_sets.EVAL_DEV, "user-reported.txt"), encoding="utf-8").read()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "user-reported.txt")
            open(path, "w", encoding="utf-8", newline="").write(real + "|多加一列|ㄉㄨㄛ ㄐㄧㄚ ㄧ ㄌㄧㄝˋ\n")
            run_sets.check_reported(path)                                                  # appended rows are fine
            open(path, "w", encoding="utf-8", newline="").write(real.replace("\n", "\r\n"))
            run_sets.check_reported(path)                                                  # a CRLF checkout hashes the same
            open(path, "w", encoding="utf-8", newline="").write("|改過的第一列|ㄍㄞˇ\n" + real)
            with self.assertRaises(SystemExit) as e:
                run_sets.check_reported(path)
            self.assertIn("first 66 rows", str(e.exception.code))
            # main checks before any set runs: nothing is decoded when the head changed
            with mock.patch.object(run_sets, "EVAL_DEV", td), mock.patch.object(run_sets, "check_model"), \
                    mock.patch.object(run_sets.subprocess, "run") as sp:
                with self.assertRaises(SystemExit):
                    run_sets.main(["dumps", "--work", td, "--sets", "dev302", "user-reported", "--reported-rows", "66"])
                sp.assert_not_called()

if __name__ == "__main__":
    unittest.main()
