"""cluster.py 的 --keep-from（model-v5 契約 §9）：舊詞沿用舊類別，新詞貼進類別。執行：python3 experiments/s2-classes/test_cluster.py"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cluster as C  # noqa: E402


def test_keep_from_keeps_old_classes_and_places_new_words():
    # 詞 0..5；0,1,2 常接 3；新詞 5 的鄰居和 0 一樣，應該進 0 的類別。
    vocab = ["a", "b", "c", "d", "e", "new"]
    V = len(vocab)
    pairs = [(0, 3, 50), (1, 3, 40), (2, 4, 30), (5, 3, 20), (3, V + 1, 60), (4, V + 1, 30), (V, 0, 50), (V, 1, 40), (V, 2, 30), (V, 5, 20)]
    src, dst, cnt = (np.array([p[i] for p in pairs], np.int64) for i in range(3))
    with tempfile.TemporaryDirectory() as d:
        old_vocab = np.array(["a", "b", "c", "d", "e", "gone"], dtype=object)
        np.savez(os.path.join(d, "edges.npz"), vocab=old_vocab)
        np.savez(os.path.join(d, "cls-6-2.npz"), cls=np.array([0, 0, 1, 1, -1, 1], np.int32), K=2, N=6)
        old = C.load_old(d, 6, 2)
    assert old["e"] == -1 and old["gone"] == 1
    cl = C.Clusterer(6, 2, src.astype(np.int32), dst.astype(np.int32), cnt, V)
    cl.init_from(old, vocab, lambda m: None)
    assert list(cl.cls[:4]) == [0, 0, 1, 1]           # 舊詞的類別不動
    assert cl.cls[4] in (0, 1) and cl.cls[5] == 0      # 舊分群沒有類別的詞與新詞都貼進類別；新詞跟著同樣鄰居的 a、b
    res = np.array([old.get(w, r) for w, r in zip(vocab, cl.cls[:V])], np.int32)
    assert res[4] == -1 and res[5] == 0                # main() 的覆寫：舊分群是 −1 的仍是 −1


if __name__ == "__main__":
    test_keep_from_keeps_old_classes_and_places_new_words()
    print("ok")
