import os, sys, tempfile, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge2 as b
import shutil
d = tempfile.mkdtemp(); os.makedirs(f"{d}/dec"); o = tempfile.mkdtemp()   # d: SRC (decodes), o: OUT (results only)
try:
    # fake table: words a b c x y; class of b = 0; (a,0,c) seen 100 of 100 -> bridge favours c over x
    V, K3 = 5, 4
    ids = {w: i for i, w in enumerate("abcxy")}
    b.G.update(lenient=({}, 0), ids=ids, cls=np.array([1, 0, 2, 3, 3]), V=V, K3=K3,
               tab={(0 * K3 + 0) * V + 2: 100.0}, ctx={0 * K3 + 0: 100.0}, pcw={0 * V + 2: 0.01, 0 * V + 3: 0.5})
    assert b.corr(["a", "b", "c"]) > 0 and b.corr(["a", "b", "x"]) == 0 and b.corr(["a", "b"]) == 0
    # 3 rows, gold abc / abc / ab; base ok = 0,1,0 ; cand 1 = x-version (score higher), cand 2 = c-version
    gold = ["abc", "abc", "abz"]; open(f"{d}/gold", "w").write("\n".join(gold) + "\n")
    for arm, okv in (("base", [0, 1, 0]), ("A", [0, 1, 0])):
        p = f"{d}/dec/{arm}.{b.UNIT}"
        open(p + ".r", "w").write("".join(f"{i}\t{v}\t0\n" for i, v in enumerate(okv, 1)))
        # row1: x wins by 0.05 (<beta*corr), c second; row2: c is first (ok); row3: ab only
        open(p + ".dump", "w").write("1\t1\tabx\t-1.0\n1\t2\tabc\t-1.05\n2\t1\tabc\t-1.0\n2\t2\tabx\t-2\n3\t1\tab\t-1\n")
        open(p + ".words", "w").write("1\t1\ta b x\n1\t2\ta b c\n2\t1\ta b c\n2\t2\ta b x\n3\t1\ta b\n")
    parts = [(arm, 1, b.job((d, f"{d}/gold", arm, 1, 4))[2]) for arm in b.ARMS]
    by = b.summarize(d, o, parts, 3)
    assert not os.path.exists(f"{d}/res") and os.listdir(o) == ["res"]
    assert parts[0][2] == [1, 1, 0] and by["br"] == (1, 0) and by["dg"] == (0, 0) and by["dgbr"] == (1, 0), (parts, by)
    assert open(f"{o}/res/cells.tsv").read() == "dg\tchatclause.chat\t0\t0\nbr\tchatclause.chat\t1\t0\ndgbr\tchatclause.chat\t1\t0\n"
    t32, t31 = b.make_tasks("s", "g", 5641, 32), b.make_tasks("s", "g", 5641, 31)
    assert len(t32) == 32 and len(t31) == 30, (len(t32), len(t31))
    for t in (t32, t31):   # each arm's chunks cover rows 1..5641 once
        for arm in b.ARMS:
            r = [i for _, _, a, lo, hi in t if a == arm for i in range(lo, hi)]
            assert r == list(range(1, 5642)), arm
    print("selftest ok")
finally:
    shutil.rmtree(d); shutil.rmtree(o)
