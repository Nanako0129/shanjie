"""S7a: run_sets.py plumbing with lm_eval.py stubbed out (no model file needed): the command line, the .top1 file, the score loop."""
import contextlib
import io
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
            with mock.patch.object(run_sets.subprocess, "run", fake_run), contextlib.redirect_stdout(io.StringIO()):
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


if __name__ == "__main__":
    unittest.main()
