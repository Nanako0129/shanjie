"""L0 model, loss, save / load, and scoring (torch). The encoder is S7a's GPT (torch_model.from_npz) used up to the final LayerNorm,
never through the tied LM head. Registration: README.md in this folder.

Per candidate: h = lnf output at the last real token of [BOS] k [BOS] c (right padding is harmless: attention is causal).
Option scores: keep = keep_head(h of rank 1); option i >= 1 = cand_head(concat(h_i, f_i)), f_i = l0core.features (NF = 8).
"""
import hashlib

import torch
import torch.nn as nn
import torch.nn.functional as F

import l0core
import tinylm
from l0core import NF
from torch_model import GPT


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def hidden(gpt, ids):
    """(B, T) ids -> (B, T, d): GPT.forward without the LM head (final LayerNorm output)."""
    x = gpt.emb(ids) + gpt.pos(torch.arange(ids.shape[1], device=ids.device))
    for b in gpt.blocks:
        x = b(x)
    return gpt.lnf(x)


class L0Model(nn.Module):
    def __init__(self, gpt):
        super().__init__()
        self.gpt = gpt
        d = gpt.cfg["d"]
        self.keep_head, self.cand_head = nn.Linear(d, 1), nn.Linear(d + NF, 1)
        for m in (self.keep_head, self.cand_head):      # GPT's own initialisation (weights N(0, 0.02), zero bias)
            nn.init.normal_(m.weight, 0.0, 0.02)
            nn.init.zeros_(m.bias)

    def forward(self, ids, last, feat, opt):
        """collate()'s tensors -> (B, TOPK) float32 option scores, -inf where opt < 0."""
        h = hidden(self.gpt, ids)[torch.arange(ids.shape[0], device=ids.device), last]
        keep = self.keep_head(h).float().squeeze(-1)
        cand = self.cand_head(torch.cat([h, feat.to(h.dtype)], dim=-1)).float().squeeze(-1)
        o = opt.clamp(min=0)
        s = torch.cat([keep[o[:, :1]], cand[o[:, 1:]]], dim=1)
        return s.masked_fill(opt < 0, float("-inf"))


def list_loss(scores, target, w):
    """Softmax cross entropy over the row's options. Row weight = w when the target is option 0 (gold is rank 1), else 1; the batch value
    is the weighted mean sum(weight * ce) / sum(weight). Returns (loss, mean unweighted ce, list accuracy)."""
    ce = -F.log_softmax(scores.float(), dim=-1).gather(1, target[:, None])[:, 0]
    wt = torch.where(target == 0, torch.full_like(ce, float(w)), torch.ones_like(ce))
    return (wt * ce).sum() / wt.sum(), ce.detach().mean(), (scores.argmax(-1) == target).float().mean()


def to_tensors(batch, device):
    return {k: torch.from_numpy(v).to(device) for k, v in batch.items()}


# ------------------------------------------------------------------ save / load
def save_l0(model, chars, npz_sha256, path, extra=None):
    torch.save({"state": model.state_dict(), "cfg": dict(model.gpt.cfg), "chars": list(chars), "npz_sha256": npz_sha256,
                "nf": NF, "extra": extra or {}}, path)


def load_l0(path, device="cpu"):
    """-> (L0Model in eval mode on device, tinylm.Vocab, meta dict with npz_sha256 / cfg / extra)."""
    z = torch.load(path, map_location="cpu", weights_only=True)
    assert z["nf"] == NF
    m = L0Model(GPT(**z["cfg"]))
    m.load_state_dict(z["state"])
    return m.to(device).eval(), tinylm.Vocab(z["chars"]), {k: z[k] for k in ("npz_sha256", "cfg", "extra")}


# ------------------------------------------------------------------ scoring
@torch.no_grad()
def score_packs(model, packs, device="cpu", bs=64):
    """RowPacks (every row with >= 2 candidates, none too long) -> per row the list of its n option scores. float32, no autocast, no TF32."""
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    out = []
    for s in range(0, len(packs), bs):
        chunk = packs[s:s + bs]
        sc = model(**{k: v for k, v in to_tensors(l0core.collate(chunk), device).items() if k != "target"}).cpu().numpy()
        out += [sc[b, :len(p.lens)].tolist() for b, p in enumerate(chunk)]
    return out
