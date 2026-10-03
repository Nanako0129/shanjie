"""注音整句轉換原型：unigram lattice、三種使用者學習策略、LLM 重排。

只為量測設計取捨，不是可用的輸入法。分數一律是 log10（沿用 McBopomofo data.txt）。
"""
import heapq
import math
import os
from collections import defaultdict

DATA = os.path.join(os.path.dirname(__file__), "data", "mcbpmf-data.txt")
BEAM = 32          # 每個位置保留的部分路徑數；N-best 上限也是它
PER_KEY = 12       # 每個讀音只展開前幾高分的詞，控制 lattice 寬度


class Lexicon:
    def __init__(self, path=DATA, overlay=None):
        self.by_reading = defaultdict(list)   # 'ㄔㄤˊ-ㄔㄤˊ' -> [(word, log10p)]
        self.by_word = {}                     # word -> (best reading tuple, log10p)
        for line in open(path, encoding="utf-8"):
            if line[0] in "#_":
                continue
            parts = line.split()
            if len(parts) != 3:
                continue
            reading, word, score = parts[0], parts[1], float(parts[2])
            syls = tuple(reading.split("-"))
            if len(word) != len(syls):
                continue
            self.by_reading[syls].append((word, score))
            if word not in self.by_word or score > self.by_word[word][1]:
                self.by_word[word] = (syls, score)
        # S1 疊加層 `讀音\t詞\t分數\t來源`：接在基底後面（檔案順序），再和基底一起做一次穩定排序，同分時基底在前
        for line in open(overlay, encoding="utf-8") if overlay else ():
            reading, word, score, _ = line.rstrip("\n").split("\t")
            syls, score = tuple(reading.split("-")), float(score)
            if any(w == word for w, _ in self.by_reading[syls]):
                raise ValueError("overlay duplicates a base entry")
            self.by_reading[syls].append((word, score))
            if word not in self.by_word or score > self.by_word[word][1]:
                self.by_word[word] = (syls, score)
        for k in self.by_reading:
            self.by_reading[k].sort(key=lambda x: -x[1])
        self.max_len = max(len(k) for k in self.by_reading)

    def to_syllables(self, text):
        """用詞庫最佳讀音把句子轉成注音；任何字查不到就回 None。

        ponytail: 讀音取「該詞最高分讀音」，破音字會偶爾標錯，這是測試集雜訊的上限來源。
        """
        n = len(text)
        best = [(-math.inf, None)] * (n + 1)
        best[0] = (0.0, None)
        for i in range(1, n + 1):
            for L in range(1, min(self.max_len, i) + 1):
                w = text[i - L:i]
                if w in self.by_word and best[i - L][0] > -math.inf:
                    s = best[i - L][0] + self.by_word[w][1]
                    if s > best[i][0]:
                        best[i] = (s, i - L)
        if best[n][0] == -math.inf:
            return None
        out, i = [], n
        while i > 0:
            j = best[i][1]
            out.append(self.by_word[text[j:i]][0])
            i = j
        return [s for syls in reversed(out) for s in syls]


class NoLearning:
    """不學習。也是其他策略的基底介面。"""
    def bonus(self, prev_word, syls, word):
        return 0.0

    def observe(self, prev_word, syls, wrong, right):
        pass


class GlobalBoost(NoLearning):
    """選一次就全域置頂：Apple「調整用字頻率」官方描述、libchewing max_freq+10 的行為。"""
    def __init__(self, lex):
        self.lex, self.top = lex, {}

    def bonus(self, prev_word, syls, word):
        if self.top.get(syls) != word:
            return 0.0
        best = self.lex.by_reading[syls][0][1]
        mine = dict(self.lex.by_reading[syls])[word]
        return best - mine + 0.01   # 剛好壓過原本第一名

    def observe(self, prev_word, syls, wrong, right):
        self.top[syls] = right


class ContextKeyed(NoLearning):
    """以「前一個詞＋讀音」為 key 才生效：McBopomofo UserOverrideModel 的簡化版（不含時間衰減）。"""
    def __init__(self, lex):
        self.lex, self.mem = lex, {}

    def bonus(self, prev_word, syls, word):
        if self.mem.get((prev_word, syls)) != word:
            return 0.0
        best = self.lex.by_reading[syls][0][1]
        mine = dict(self.lex.by_reading[syls])[word]
        return best - mine + 0.01

    def observe(self, prev_word, syls, wrong, right):
        self.mem[(prev_word, syls)] = right


class Promotion(GlobalBoost):
    """自然輸入法式門檻：改選 1 次只進暫存（小加分），累積 3 次才全域置頂。"""
    TEMP_BONUS = 0.5   # log10 約 3 倍；不足以翻過「常常 vs 嚐嚐」約 1.9 的差距
    PROMOTE_AT = 3

    def __init__(self, lex):
        super().__init__(lex)
        self.count = defaultdict(int)

    def bonus(self, prev_word, syls, word):
        c = self.count[(syls, word)]
        if c >= self.PROMOTE_AT:
            self.top[syls] = word
            return super().bonus(prev_word, syls, word)
        return self.TEMP_BONUS if c else 0.0

    def observe(self, prev_word, syls, wrong, right):
        self.count[(syls, right)] += 1


def decode(lex, syls, learner=None, beam=BEAM):
    """Beam N-best。回傳 [(score, (word, ...)), ...]，score 高者在前。"""
    learner = learner or NoLearning()
    syls = tuple(syls)
    n = len(syls)
    hyps = [[] for _ in range(n + 1)]
    hyps[0] = [(0.0, ())]
    for i in range(1, n + 1):
        cand = {}
        for L in range(1, min(lex.max_len, i) + 1):
            key = syls[i - L:i]
            entries = lex.by_reading.get(key)
            if not entries or not hyps[i - L]:
                continue
            for word, lp in entries[:PER_KEY]:
                for s, ws in hyps[i - L]:
                    prev = ws[-1] if ws else "<s>"
                    sc = s + lp + learner.bonus(prev, key, word)
                    surface = "".join(ws) + word
                    if surface not in cand or sc > cand[surface][0]:
                        cand[surface] = (sc, ws + (word,))
        hyps[i] = heapq.nlargest(beam, cand.values(), key=lambda x: x[0])
    return hyps[n]


def segment_words(lex, text):
    """把正解句切成詞並帶讀音，用來在學習模擬裡找出「使用者改了哪個詞」。"""
    syls = lex.to_syllables(text)
    if syls is None:   # 和核心的 segment_words 一樣：讀不出就回 None，不要在下面的回溯當掉
        return None
    out, i = [], 0
    n = len(text)
    # 重用 to_syllables 的切法：再跑一次 DP 取詞界
    best = [(-math.inf, None)] * (n + 1)
    best[0] = (0.0, None)
    for k in range(1, n + 1):
        for L in range(1, min(lex.max_len, k) + 1):
            w = text[k - L:k]
            if w in lex.by_word and best[k - L][0] > -math.inf:
                s = best[k - L][0] + lex.by_word[w][1]
                if s > best[k][0]:
                    best[k] = (s, k - L)
    k = n
    while k > 0:
        j = best[k][1]
        out.append(text[j:k])
        k = j
    out.reverse()
    pos, res = 0, []
    for w in out:
        res.append((w, tuple(syls[pos:pos + len(w)])))
        pos += len(w)
    return res


class LMScorer:
    """用因果語言模型對候選整句打分：log P(前文＋候選)，前文相同所以等價於 P(候選|前文)。"""
    PREAMBLE = "以下是一段台灣繁體中文文字。\n"

    def __init__(self, path):
        import mlx.core as mx
        from mlx_lm import load
        self.mx = mx
        self.model, self.tok = load(path)
        self.pre_len = len(self.tok.encode(self.PREAMBLE))

    def score(self, context, candidates):
        mx = self.mx
        seqs = [self.tok.encode(self.PREAMBLE + context + c) for c in candidates]
        T = max(len(s) for s in seqs)
        pad = self.tok.eos_token_id or 0
        x = mx.array([s + [pad] * (T - len(s)) for s in seqs])
        logits = self.model(x[:, :-1]).astype(mx.float32)
        logp = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
        tgt = x[:, 1:]
        tok_lp = mx.take_along_axis(logp, tgt[..., None], axis=-1)[..., 0]
        # 只計前言之後、padding 之前的 token
        idx = mx.arange(T - 1)[None, :]
        lens = mx.array([len(s) for s in seqs])[:, None]
        mask = (idx >= self.pre_len - 1) & (idx < lens - 1)
        out = (tok_lp * mask).sum(axis=1)
        mx.eval(out)
        return out.tolist()


class TorchScorer(LMScorer):
    """同一套打分，跑在 CUDA（188 的 RTX 3070）。

    model 名稱後綴：'@4bit' 用 bitsandbytes NF4 載入；'@ple' 把 Gemma 4 的 per-layer embedding
    與視覺／語音塔放 CPU、其餘留 GPU；可合併成 '@4bit+ple'。
    """

    def __init__(self, path):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        path, _, flags = path.partition("@")
        flags = set(flags.split("+")) if flags else set()
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(path)
        kw = {"dtype": torch.bfloat16, "device_map": _ple_map(path) if "ple" in flags else "auto"}
        if "4bit" in flags:
            from transformers import BitsAndBytesConfig
            kw["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16,
                llm_int8_enable_fp32_cpu_offload=True)   # 放 CPU 的模組保持不量化
        try:
            self.model = AutoModelForCausalLM.from_pretrained(path, **kw).eval()
        except ValueError:   # Gemma 3 4B／Gemma 4 是多模態架構，只餵文字
            from transformers import AutoModelForImageTextToText
            self.model = AutoModelForImageTextToText.from_pretrained(path, **kw).eval()
        self.pre_len = len(self.tok.encode(self.PREAMBLE))

    CHUNK = 8   # 8 GB VRAM 放不下 32 候選 × 15 萬詞表的 float32 logits，分批算

    def score(self, context, candidates):
        if os.environ.get("IME_EOS"):   # 句子已打完：加句尾，避免「去八」被當成「去八里」的開頭
            candidates = [c + "。" for c in candidates]
        out = []
        for i in range(0, len(candidates), self.CHUNK):
            out += self._score(context, candidates[i:i + self.CHUNK])
        return out

    def _score(self, context, candidates):
        torch = self.torch
        seqs = [self.tok.encode(self.PREAMBLE + context + c) for c in candidates]
        T = max(len(s) for s in seqs)
        pad = self.tok.eos_token_id or 0
        dev = self.model.get_input_embeddings().weight.device
        x = torch.tensor([s + [pad] * (T - len(s)) for s in seqs], device=dev)
        lens = torch.tensor([len(s) for s in seqs], device=dev)[:, None]
        attn = (torch.arange(T, device=dev)[None, :] < lens).long()
        with torch.inference_mode():
            logits = self.model(input_ids=x[:, :-1], attention_mask=attn[:, :-1]).logits.float()
            x, lens = x.to(logits.device), lens.to(logits.device)
            tok_lp = logits.gather(-1, x[:, 1:, None])[..., 0] - torch.logsumexp(logits, -1)
            idx = torch.arange(T - 1, device=logits.device)[None, :]
            mask = (idx >= self.pre_len - 1) & (idx < lens - 1)
            return (tok_lp * mask).sum(1).tolist()


def _ple_map(path):
    """Gemma 4：查表用的 embed_tokens_per_layer（E2B 2.35B、E4B 2.82B 參數）與多模態塔放 CPU。

    bitsandbytes 的 4-bit 層被 accelerate 卸到 CPU 會壞（meta tensor），所以運算層必須全在 GPU。
    """
    from accelerate import init_empty_weights
    from transformers import AutoConfig, AutoModelForImageTextToText
    with init_empty_weights():
        m = AutoModelForImageTextToText.from_config(AutoConfig.from_pretrained(path))
    dm = {}
    for name, child in m.named_children():
        if name != "model":
            dm[name] = 0
            continue
        for n2, c2 in child.named_children():
            if n2 != "language_model":
                dm[f"model.{n2}"] = "cpu"
                continue
            for n3, _ in c2.named_children():
                dm[f"model.language_model.{n3}"] = "cpu" if n3 == "embed_tokens_per_layer" else 0
    return dm


def make_scorer(name):
    try:
        import torch
        if torch.cuda.is_available():
            return TorchScorer(name)
    except ImportError:
        pass
    return LMScorer(name)


def rerank(lm, context, nbest):
    """對 N-best 重排；回傳最佳句子字串。只用 LM 分數，lattice 只負責出候選。"""
    texts = ["".join(ws) for _, ws in nbest]
    scores = lm.score(context, texts)
    return max(zip(scores, texts))[1]


if __name__ == "__main__":
    lex = Lexicon()
    s = lex.to_syllables("輸入法常常選錯字")
    assert s[3:5] == ["ㄔㄤˊ", "ㄔㄤˊ"], s
    top = "".join(decode(lex, s)[0][1])
    print("unigram:", top)
    assert top == "輸入法常常選錯字", top

    # 學錯一次的情境：在「蛋糕嚐嚐看」改選嚐嚐，再打「輸入法常常選錯字」
    expect = {NoLearning: "常常", GlobalBoost: "嚐嚐", ContextKeyed: "常常", Promotion: "常常"}
    for cls, want in expect.items():
        L = cls() if cls is NoLearning else cls(lex)
        L.observe("蛋糕", ("ㄔㄤˊ", "ㄔㄤˊ"), "常常", "嚐嚐")
        out = "".join(decode(lex, s, L)[0][1])
        print(f"{cls.__name__:12s} after one 嚐嚐 correction ->", out)
        assert out[3:5] == want, (cls.__name__, out)
    print("self-check ok")
