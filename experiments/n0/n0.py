"""N0: semantic decoding feasibility. M0 unigram, M1 N-best rerank, M2 reading-constrained beam search.

Usage (on 188, from the shanjie-n0 root):
  python experiments/n0/n0.py --model gemma|qwen --method m1|m2 [--beams 4] [--limit 50] [--out FILE]
  python experiments/n0/n0.py --method m0
Only eval/dev is read. Raw per-row outputs go to --out (jsonl); metric lines go to stdout.
"""
import argparse, json, os, re, sys, time
from collections import defaultdict
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
from ime import Lexicon, decode  # noqa: E402

PREAMBLE = "以下是一段台灣繁體中文文字。\n"
LENIENT = str.maketrans("她妳它牠嘗周臺裏", "他你他他嚐週台裡")  # same as reference/proto/eval.py
FILES = ["existing", "homophones", "oov", "user-reported"]
MISS_FILES = {"oov", "user-reported"}
HAN = re.compile(r"^[㐀-䶿一-鿿]+$")
GEMMA = os.path.expandvars(r"%USERPROFILE%\models\gemma-4-e2b")
QWEN = "Qwen/Qwen3-1.7B-Base"


def load_dev(limit=None):
    rows = []
    for f in FILES:
        for line in open(os.path.join(ROOT, "eval", "dev", f + ".txt"), encoding="utf-8"):
            line = line.rstrip("\n")
            if line and not line.startswith("#"):
                ctx, sent, rd = line.split("|")
                rows.append((f, ctx, sent, rd.split()))
    return rows[:limit] if limit else rows


def load_model(which):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    path = GEMMA if which == "gemma" else QWEN
    tok = AutoTokenizer.from_pretrained(path)
    if which == "qwen":
        model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.bfloat16, device_map={"": 0}).eval()
        return tok, model
    from transformers import AutoModelForImageTextToText
    model = AutoModelForImageTextToText.from_pretrained(path, dtype=torch.bfloat16).eval()  # all on CPU
    lm = model.model.language_model
    ple = lm.embed_tokens_per_layer
    lm.embed_tokens_per_layer = torch.nn.Identity()  # keep the 2.35B-param table out of the .to()
    model.to("cuda")
    lm.embed_tokens_per_layer = ple                  # stays on CPU, no accelerate hooks
    orig = ple.forward

    def cpu_lookup(input_ids, *a, **k):  # look up rows on CPU, ship only the rows to GPU
        return orig(input_ids.to("cpu"), *a, **k).to("cuda", non_blocking=False)
    ple.forward = cpu_lookup
    return tok, model


class Scorer:
    def __init__(self, which):
        import torch
        self.torch = torch
        self.tok, self.model = load_model(which)
        self.dev = "cuda"
        self.pre_len = len(self.tok.encode(PREAMBLE))
        self._build_vocab()

    # ---- M1 ----
    def score(self, context, cands, chunk=8):
        out = []
        for i in range(0, len(cands), chunk):
            out += self._score(context, [c + "。" for c in cands[i:i + chunk]])
        return out

    def _score(self, context, cands):
        torch, tok = self.torch, self.tok
        seqs = [tok.encode(PREAMBLE + context + c) for c in cands]
        T = max(map(len, seqs))
        pad = tok.eos_token_id or 0
        x = torch.tensor([s + [pad] * (T - len(s)) for s in seqs], device=self.dev)
        lens = torch.tensor([len(s) for s in seqs], device=self.dev)[:, None]
        attn = (torch.arange(T, device=self.dev)[None, :] < lens).long()
        with torch.inference_mode():
            lg = self.model(input_ids=x[:, :-1], attention_mask=attn[:, :-1]).logits.float()
            lp = lg.gather(-1, x[:, 1:, None])[..., 0] - torch.logsumexp(lg, -1)
            idx = torch.arange(T - 1, device=self.dev)[None, :]
            mask = (idx >= self.pre_len - 1) & (idx < lens - 1)
            return (lp * mask).sum(1).tolist()

    # ---- M2 ----
    def _build_vocab(self):
        tok = self.tok
        self.tstr = {}                       # id -> pure-Han string
        for i in range(len(tok)):
            raw = tok.convert_ids_to_tokens(i)
            if not isinstance(raw, str) or "▁" in raw or "Ġ" in raw:
                continue
            s = tok.decode([i])
            if HAN.match(s):
                self.tstr[i] = s
        self.by_first = defaultdict(list)
        for i, s in self.tstr.items():
            self.by_first[s[0]].append((i, s))
        stop = tok.encode("。", add_special_tokens=False)
        assert len(stop) == 1, stop
        self.stop = stop[0]
        self.eos = tok.eos_token_id

    def constrained(self, lex_chars, context, syls, beams):
        torch, tok = self.torch, self.tok
        n = len(syls)
        # chars readable as each syllable
        allowed = {}

        def allowed_at(p):
            if p not in allowed:
                ids = []
                for c in lex_chars.get(syls[p], ()):
                    for i, s in self.by_first.get(c, ()):
                        k = len(s)
                        if k <= n - p and all(syls[p + j] in lex_chars_rev.get(s[j], ()) for j in range(1, k)):
                            ids.append(i)
                allowed[p] = ids or [self.stop]  # dead end: nothing reachable; keep generate alive
            return allowed[p]
        lex_chars_rev = self.rev
        prompt = tok.encode(PREAMBLE + context)
        L = len(prompt)

        def fn(b, ids):
            gen = ids[L:].tolist()
            if gen and gen[-1] == self.stop:
                return [self.eos]
            p = sum(len(self.tstr.get(g, "")) for g in gen)
            return [self.stop] if p >= n else allowed_at(p)
        x = torch.tensor([prompt], device=self.dev)
        with torch.inference_mode():
            out = self.model.generate(input_ids=x, attention_mask=torch.ones_like(x), num_beams=beams,
                                      do_sample=False, length_penalty=0.0, max_new_tokens=n + 2,
                                      prefix_allowed_tokens_fn=fn, eos_token_id=self.eos,
                                      pad_token_id=self.eos, early_stopping=True)
        text = tok.decode(out[0][L:], skip_special_tokens=True)
        return text.rstrip("。")


def char_readings(lex):
    ch = defaultdict(set)
    for syls, ents in lex.by_reading.items():
        if len(syls) == 1:
            for w, _ in ents:
                ch[syls[0]].add(w)  # syllable -> chars
    return ch


def pct(v, q):
    v = sorted(v)
    return round(1000 * v[int(len(v) * q)]) if q != .5 else round(1000 * v[len(v) // 2])


def report(name, method, rows, outs, lat):
    for grp in FILES + ["dev"]:
        idx = [i for i, r in enumerate(rows) if grp == "dev" or r[0] == grp]
        if not idx:
            continue
        ok = len_ok = ch_ok = chars = 0
        for i in idx:
            t, o = rows[i][2], outs[i]
            ok += o == t
            len_ok += o.translate(LENIENT) == t.translate(LENIENT)
            ch_ok += sum(a == b for a, b in zip(o, t))
            chars += len(t)
        n = len(idx)
        res = {"n": n, "sent_acc": round(ok / n, 3), "lenient_acc": round(len_ok / n, 3),
               "char_acc": round(ch_ok / chars, 4)}
        if lat:
            l = [lat[i] for i in idx]
            res["p50_ms"], res["p95_ms"] = pct(l, .5), pct(l, .95)
        print(f"## {grp}  {method}  {res}")
        if grp in MISS_FILES:
            for i in idx:
                if outs[i] != rows[i][2]:
                    print(f"   x {rows[i][2]} -> {outs[i]}")


def pin():
    """N0_PIN=1: P-cores only (logical 0-11, mask 0xFFF) + HIGH priority; i5-12600K has slow E-cores 12-15."""
    import ctypes
    k = ctypes.windll.kernel32
    h = k.GetCurrentProcess()
    assert k.SetProcessAffinityMask(ctypes.c_void_p(h), ctypes.c_size_t(0xFFF))
    assert k.SetPriorityClass(ctypes.c_void_p(h), 0x80)
    print("## pinned affinity=0xFFF priority=HIGH", file=sys.stderr)


def main():
    if os.environ.get("N0_PIN"):
        pin()
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", required=True, choices=["m0", "m1", "m2"])
    ap.add_argument("--model", choices=["gemma", "qwen"])
    ap.add_argument("--beams", type=int, default=4)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out")
    a = ap.parse_args()
    rows = load_dev(a.limit)
    lex = Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"))
    outs, lat = [], []
    if a.method == "m0":
        outs = ["".join(decode(lex, r[3])[0][1]) for r in rows]
        report("", "M0-unigram", rows, outs, None)
    else:
        import torch
        sc = Scorer(a.model)
        sc.rev = defaultdict(set)  # char -> syllables
        syl_chars = char_readings(lex)
        for s, cs in syl_chars.items():
            for c in cs:
                sc.rev[c].add(s)
        sc.score("", ["暖機"]) if a.method == "m1" else sc.constrained(syl_chars, "", ["ㄋㄧˇ", "ㄏㄠˇ"], 2)
        torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats()
        extra = []
        for f, ctx, truth, syls in rows:
            t = time.perf_counter()
            if a.method == "m1":
                texts = ["".join(ws) for _, ws in decode(lex, syls)]
                s = sc.score(ctx, texts)
                o = max(zip(s, texts))[1]
            else:
                o = sc.constrained(syl_chars, ctx, syls, a.beams)
            torch.cuda.synchronize()
            lat.append(time.perf_counter() - t)
            outs.append(o)
        name = f"{'M1-nbest32' if a.method == 'm1' else 'M2-B%d' % a.beams}/{a.model}"
        report("", name, rows, outs, lat)
        print(f"## peak_gpu_mem_MiB  {name}  {round(torch.cuda.max_memory_allocated() / 2**20)}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            for r, o, l in zip(rows, outs, lat or [None] * len(rows)):
                f.write(json.dumps({"file": r[0], "truth": r[2], "out": o, "s": l}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
