"""g0v_clean.py: one unit per message (line number), the per-unit cap, dedupe across messages. The p2clean rules are a stub here."""
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g0v_clean as G  # noqa: E402
import ptt_clean  # noqa: E402

STUB = SimpleNamespace(split_sentences=lambda x: [p for p in x.split("。") if p], reject=lambda s, t: "short" if len(s) < 2 else None,
                       bc=SimpleNamespace(TRAD_SIMP_ONLY=set()), dedupe_key=lambda s: s)


class Clean(unittest.TestCase):
    def test_units_are_line_numbers_duplicates_and_rejects_are_counted(self):
        rows, why = G.clean(STUB, ["第一句。第二句\n", "第一句。短。甲\n", "第三句\n"])
        self.assertEqual(rows, [("1", "第一句"), ("1", "第二句"), ("3", "第三句")])
        self.assertEqual(why, {"duplicate": 1, "short": 2})   # 短 and 甲

    def test_one_message_gives_at_most_the_unit_cap(self):
        rows, why = G.clean(STUB, ["。".join(f"句子{i}" for i in range(ptt_clean.UNIT_CAP + 5))])
        self.assertEqual((len(rows), why["unit_cap"]), (ptt_clean.UNIT_CAP, 5))


if __name__ == "__main__":
    unittest.main()
