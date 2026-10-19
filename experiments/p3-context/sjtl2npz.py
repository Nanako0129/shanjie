"""The shipped reranker file as an .npz, so score_p3.py scores what the App runs (int8 weights restored as q * scale).

  python3 experiments/p3-context/sjtl2npz.py data/lm/tinylm.sjtl OUT.npz
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "s7a-tinylm"))
import tinylm  # noqa: E402
from export_sjtl import read_sjtl  # noqa: E402

if __name__ == "__main__":
    head, chars, params = read_sjtl(sys.argv[1])
    tinylm.save_params(sys.argv[2], params, chars, head["n_layer"], head["n_head"], head["ctx"])
    print(f"chat {head['chat']} delta {head['delta'][0]}, formal {head['formal']} delta {head['delta'][1]}, wtype {head['wtype']}")
