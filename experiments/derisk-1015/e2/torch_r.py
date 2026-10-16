"""E2 torch side (training model, exporter, scoring backend). Needs torch; the numpy twin is tinylm_r.NumpyLMR.

GPTR = experiments/s7a-tinylm/torch_model.GPT plus `rd`, an Embedding(R, d, padding_idx=0) added to the input embedding after the
position term (the same order as NumpyLMR). Row 0 is zero and gets no gradient, so rd=None / all-0 is exactly GPT.
"""
import os
import sys

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tinylm_r  # noqa: E402  (puts experiments/s7a-tinylm on sys.path)
import tinylm  # noqa: E402
from tinylm import CTX  # noqa: E402
from torch_model import GPT, export_npz, from_npz  # noqa: E402


class GPTR(GPT):
    def __init__(self, V, n_layer, d, n_head, n_read, ctx=CTX):
        super().__init__(V, n_layer, d, n_head, ctx)
        self.cfg["n_read"] = n_read
        self.rd = nn.Embedding(n_read, d, padding_idx=0)
        nn.init.normal_(self.rd.weight, 0.0, 0.02)
        with torch.no_grad():
            self.rd.weight[0].zero_()

    def forward(self, ids, rd=None):
        """ids (B, T); rd (B, T) TARGET-aligned reading ids (tinylm_r.target_aligned of the token-aligned ids), or None."""
        T = ids.shape[1]
        x = self.emb(ids) + self.pos(torch.arange(T, device=ids.device))
        if rd is not None:
            x = x + self.rd(rd)
        for b in self.blocks:
            x = b(x)
        return self.lnf(x) @ self.emb.weight.t()


def export_npz_r(model, chars, rsyl, path):
    """The S7a exporter's file plus `rd` and `rd_syl` (read by tinylm_r.NumpyLMR and from_npz_r)."""
    assert len(rsyl) + 1 == model.cfg["n_read"]
    export_npz(model, chars, path)
    z = dict(np.load(path, allow_pickle=False))
    z["rd"] = model.rd.weight.detach().cpu().float().numpy()
    z["rd_syl"] = np.array(list(rsyl), dtype="<U8")
    np.savez(path, **z)


def from_npz_r(path, device="cpu"):
    base, vocab = from_npz(path)
    z = np.load(path, allow_pickle=False)
    c = base.cfg
    m = GPTR(c["V"], c["n_layer"], c["d"], c["n_head"], z["rd"].shape[0], c["ctx"])
    r = m.load_state_dict(base.state_dict(), strict=False)
    assert r.missing_keys == ["rd.weight"] and not r.unexpected_keys, r
    with torch.no_grad():
        m.rd.weight.copy_(torch.from_numpy(np.ascontiguousarray(z["rd"])))
    return m.to(device), vocab, tinylm_r.RVocab([str(s) for s in z["rd_syl"]])


class TorchLMR:
    """Scoring backend with tinylm_r.NumpyLMR's interface (.vocab, .rvocab, .has_readings, .logp10(ids, rd)); float32, no TF32."""
    has_readings = True

    def __init__(self, path, device="cpu"):
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        self.model, self.vocab, self.rvocab = from_npz_r(path, device)
        self.model.eval()
        self.device = device

    @torch.no_grad()
    def logp10(self, ids, rd=None):
        t = lambda a: torch.from_numpy(np.asarray(a)).to(self.device)  # noqa: E731
        z = self.model(t(ids), None if rd is None else t(rd)).float()
        return (F.log_softmax(z, dim=-1) / tinylm.LN10).double().cpu().numpy()
