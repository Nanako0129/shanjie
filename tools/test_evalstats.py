"""Tests for tools/evalstats.py. Run: python3 -m unittest tools.test_evalstats   (from the repo root). No network, no data files."""
import math, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402
import evalstats as E  # noqa: E402


def write(d, name, text):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


class Mcnemar(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(E.mcnemar_exact(6, 0), 0.03125)
        self.assertEqual(E.mcnemar_exact(9, 1), 0.021484375)
        self.assertEqual(E.mcnemar_exact(0, 0), 1.0)
        self.assertEqual(E.mcnemar_exact(3, 3), 1.0)

    def test_same_as_bench(self):
        for b in range(13):
            for c in range(13):
                self.assertEqual(E.mcnemar_exact(b, c), bench.mcnemar(b, c), (b, c))


class PoissonUpper(unittest.TestCase):
    def test_known_values(self):
        # one-sided exact 95% upper limits (Garwood; the standard table: 2.996, 4.744, 6.296, 10.513, 15.705 at 9)
        for k, want in ((0, 2.9957), (1, 4.7439), (2, 6.2958), (5, 10.5130), (9, 15.7052), (10, 16.9622)):
            self.assertAlmostEqual(E.poisson_upper95(k), want, places=3, msg=k)

    def test_large_k_does_not_underflow(self):
        # roots of the cdf = 0.05, checked once with 60-digit Decimal arithmetic; exp(-lam) underflows for lam > 745 (k = 1000 gave 745.13)
        for k, want in ((1000, 1053.6031), (5000, 5117.8861)):
            self.assertAlmostEqual(E.poisson_upper95(k), want, places=3, msg=k)

    def test_is_the_cdf_root(self):
        for k in (0, 3, 40):
            lam = E.poisson_upper95(k)
            cdf = sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k + 1))
            self.assertAlmostEqual(cdf, 0.05, places=9)


class Lev(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(E.levenshtein("", "abc"), 3)
        self.assertEqual(E.levenshtein("abc", ""), 3)
        self.assertEqual(E.levenshtein("abc", "abc"), 0)
        self.assertEqual(E.levenshtein("abc", "axc"), 1)
        self.assertEqual(E.levenshtein("abc", "acd"), 2)  # delete b, insert d
        self.assertEqual(E.levenshtein("𠮷野家", "吉野家"), 1)
        self.assertEqual(E.levenshtein("𠮷", ""), 1)


class RowStats(unittest.TestCase):
    def test_ok_row_is_zero(self):
        self.assertEqual(E.row_stats("他很好", "她很好", True), (0, 3))
        self.assertEqual(E.row_stats("他很好", "她很好", False), (1, 3))

    def test_format_counts_from_one(self):
        out = E.format_rowstats([(True, "a", "a"), (False, "ab", "ac")])
        self.assertEqual(out, "1\t1\t0\t1\n2\t0\t1\t2\n")


class Bootstrap(unittest.TestCase):
    base = [(1, 0, 5), (0, 2, 5), (0, 1, 5), (1, 0, 5), (0, 3, 5), (1, 0, 5)]
    cand = [(1, 0, 5), (1, 0, 5), (0, 1, 5), (0, 2, 5), (1, 0, 5), (1, 0, 5)]

    def test_seed_fixed(self):
        self.assertEqual(E.paired_bootstrap(self.base, self.cand, 7, 500), E.paired_bootstrap(self.base, self.cand, 7, 500))

    def test_unseeded_would_differ(self):
        # different seeds should give a different interval for this data, so the seed is really used
        self.assertNotEqual([E.paired_bootstrap(self.base, self.cand, s, 50) for s in range(5)].count(E.paired_bootstrap(self.base, self.cand, 0, 50)), 5)

    def test_identical_is_zero(self):
        self.assertEqual(E.paired_bootstrap(self.base, self.base), ((0, 0), (0.0, 0.0)))

    def test_contains_point_estimate(self):
        (lo, hi), (clo, chi) = E.paired_bootstrap(self.base, self.cand)
        self.assertTrue(lo <= 1 <= hi)  # +1 net top1 row
        cer = (sum(r[1] for r in self.cand) - sum(r[1] for r in self.base)) / sum(r[2] for r in self.base)
        self.assertTrue(clo <= cer <= chi)


class Compare(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def good(self, name="a"):
        return write(self.d, name, "1\t1\t0\t3\n2\t0\t2\t4\n")

    def test_ok(self):
        out = E.compare(self.good("a"), self.good("b"), "x", resamples=100)
        self.assertEqual(out.splitlines()[-1].split("|")[1:3], [" x ", " 2 "])

    def test_row_count_differs(self):
        with self.assertRaises(ValueError):
            E.compare(self.good(), write(self.d, "s", "1\t1\t0\t3\n"))

    def test_misaligned_ids(self):
        with self.assertRaises(ValueError):
            E.compare(self.good(), write(self.d, "m", "1\t1\t0\t3\n3\t0\t2\t4\n"))

    def test_not_numbers(self):
        with self.assertRaises(ValueError):
            E.compare(self.good(), write(self.d, "n", "1\t1\t0\t3\n2\t0\tx\t4\n"))

    def test_non_ascii_digits(self):
        # str.isdigit() takes fullwidth and Arabic-Indic digits; rowstats are ASCII only.
        for bad in ("1\t1\t0\t3\n2\t0\t２\t4\n", "1\t1\t0\t3\n2\t0\t٢\t4\n"):
            with self.assertRaises(ValueError):
                E.compare(self.good(), write(self.d, "u", bad))

    def test_empty_files(self):
        with self.assertRaises(ValueError):
            E.compare(write(self.d, "e1", ""), write(self.d, "e2", ""))

    def test_label_pipe_is_escaped(self):
        out = E.compare(self.good("a"), self.good("b"), "dev302|chat", resamples=100)
        self.assertIn("dev302\\|chat", out)
        self.assertEqual(out.splitlines()[-1].count("|") - out.count("\\|"), out.splitlines()[0].count("|"))


class Net(unittest.TestCase):
    """E2: --net appends three columns; without it the output is the old one."""
    def setUp(self):
        d = tempfile.mkdtemp()
        # 10 rows: base right on 1-5; cand fixes rows 6, 7, 8 and breaks row 1
        self.base = write(d, "b", "".join(f"{i}\t{int(i <= 5)}\t{0 if i <= 5 else 1}\t4\n" for i in range(1, 11)))
        self.cand = write(d, "c", "".join(f"{i}\t{int(i in (2, 3, 4, 5, 6, 7, 8))}\t{0 if i in (2, 3, 4, 5, 6, 7, 8) else 1}\t4\n" for i in range(1, 11)))

    def test_default_is_unchanged(self):
        a = E.compare(self.base, self.cand, "x", resamples=100)
        self.assertEqual(a, E.compare(self.base, self.cand, "x", resamples=100, net=False))
        self.assertEqual(a.splitlines()[0].count("|"), 12)           # the old 11 columns
        self.assertTrue(a.splitlines()[0].endswith("ΔCER 95%（百分點） |"))

    def test_net_columns(self):
        out = E.compare(self.base, self.cand, "x", resamples=100, net=True).splitlines()
        plain = E.compare(self.base, self.cand, "x", resamples=100).splitlines()
        self.assertEqual(out[0], plain[0] + " 淨值 | 改壞率 | 改壞數 95% 上界 |")
        self.assertTrue(out[2].startswith(plain[2]))
        cells = [c.strip() for c in out[2].strip("|").split("|")]
        self.assertEqual(cells[4:6], ["3", "1"])                      # fixed, broken
        self.assertEqual(cells[-3:], ["2", "10.00%", "4.7"])         # net 3 - 1, 1 / 10, upper limit of 1 event
        self.assertEqual(out[1].count("|"), out[0].count("|"))


if __name__ == "__main__":
    unittest.main()
