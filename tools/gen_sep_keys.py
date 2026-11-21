"""Cross-language golden of the boundary-format key (docs/spec/semantic-rerank.md §5.2): eval/golden/sep-keys/keys.tsv.

One row per case: `note<TAB>L<TAB>K<TAB>n<TAB>before<TAB>candidate<TAB>key<TAB>ids`. `before` and `candidate` are comma-separated hex code
points (they hold NUL, line breaks and the like); `key` is `tinylm.key_b` with the boundary written `|` (a key never holds a real `|`:
it is outside the Han ranges, so it would have become a boundary); `ids` is the sequence the model reads, [BOS] + key + candidate, as ids of
the model tiny-int8-v6.sjtl (tools/gen_tinylm_tiny.py: [SEP] = V - 1, a character outside its table = 1), cut like `ll_many` when it
exceeds the 64-token context. core/tests/tinylm_tiny.rs patches L and K of that file's header per row and checks `key_for_len` and
`sequence`; the research repo's experiments/cand-loss-2/test_sep_golden.py checks the same rows against its own keys.py and formats.py.
Regenerate: python3 -B tools/gen_sep_keys.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools", "lib"))
import tinylm  # noqa: E402

OUT = os.path.join(ROOT, "eval", "golden", "sep-keys")
CTX = 64
# Han block edges of core `is_han` (learn.rs): the first character outside each range sits next to the last one inside it.
EDGES = [0x33FF, 0x3400, 0x4DBF, 0x4DC0, 0x4E00, 0x9FFF, 0xA000, 0xF8FF, 0xF900, 0xFAFF, 0xFB00, 0x2A6DF, 0x2A6E0, 0x2A700, 0x2EBEF, 0x2EBF0,
         0x2F7FF, 0x2F800, 0x2FA1F, 0x2FA20, 0x30000, 0x3134F, 0x31350, 0x3006, 0x3007, 0x3008]


def cases():
    """[(note, before, n, L, K, cand)]; cand is a plain candidate unless the case is about it."""
    c = []
    add = lambda note, before, n=1, L=62, K=4, cand="大": c.append((note, before, n, L, K, cand))  # noqa: E731
    # ensemble-distill-3 section 3.1 examples (long key; K 1 takes it, K 5 and K 4 vs the 4-Han example show the policy)
    for k in (1, 4, 5):
        add(f"ed3-1 K{k}", "今天好熱。", K=k)
        add(f"ed3-2 K{k}", "好熱，今天真", K=k)
        add(f"ed3-3 K{k}", "，，A1 好", K=k)
        add(f"ed3-4 K{k}", "好）。「今天", K=k)
        add(f"ed3-5 K{k}", "「」", K=k)
    # boundaries: leading punctuation, runs of punctuation and spaces, line breaks, NUL, emoji, combining marks
    add("leading punctuation", "，我們都說", K=1)
    add("leading punctuation and space", "。。 \t我們", K=1)
    add("runs of punctuation and spaces", "我們，，、 。你們", K=1)
    add("line breaks", "我\n你\n\n他，要去", K=1)
    add("NUL is a boundary", "我\0你\0\0他", K=1)
    add("emoji", "我\U0001F44D你\U0001F44D\U0001F44D他", K=1)
    add("combining mark", "é我Á你", K=1)
    add("no Han", "hello, world 123", K=1)
    add("empty before", "", K=1)
    # Han definition: extension A to G, compatibility ideographs, 〇, and the characters just outside each range
    add("one of each Han block", "㐀\U00020000\U0002A700\U0002B740\U0002B820\U0002CEB0\U00030000豈〇", K=1)
    for e in EDGES:
        add(f"edge U+{e:04X}", f"我{chr(e)}你", n=1, K=3)
    add("Han outside the vocabulary is UNK, not SEP", "你龘好，他", n=2, K=1, cand="好嗎")
    add("Han outside the vocabulary counts for K", "龘龘龘龘他", n=1, K=4)
    # length: exactly 64 tokens, a leading SEP after the cut, n >= 63
    add("62 tokens exactly (n 1)", "我，" * 31, K=1)
    add("63 characters, cut to 62 tokens", "，" + "我，" * 31, K=1)
    add("cut leaves a leading SEP (n 2)", "我，" * 31, n=2, K=1, cand="好嗎")
    add("cut leaves a leading SEP (n 3)", "，我" * 40, n=3, K=1, cand="好嗎我")
    add("n 62", "好" * 10 + "，我", n=62, K=1, cand="你" * 62)
    add("n 63", "好嗎", n=63, K=1, cand="你" * 63)
    add("n 64 (a candidate over the context is never scored; the key is empty)", "好嗎", n=64, K=1, cand="你")
    add("n beyond the candidate's own length", "我們都說，你好", n=9, K=1, cand="你")
    add("candidate longer than n (the sequence is cut like ll_many)", "不" * 40, n=2, K=1, cand="你" * 40)
    # the policy key at K - 1, K, K + 1 Han characters; no Han; ends in Han vs ends in punctuation
    for k, han in ((4, 3), (4, 4), (4, 5)):
        add(f"policy K{k} with {han} Han", "，" + "我們都說要來"[:han], K=k)
        add(f"policy K{k} with {han} Han, ends in punctuation", "，" + "我們都說要來"[:han] + "，", K=k)
    add("policy: long key short, ship key 2 Han", "說，你好", K=4)
    add("policy: ship key empty after punctuation", "說你好，", K=9)
    add("policy K 1 with one Han and punctuation", "，好，", K=1)
    # the window: more than L characters, runs of >= 2 non-Han characters inside it (the key is built from before[-L:])
    add("window: 100 characters, English and punctuation inside", "我" * 30 + "hello world" + "你" * 30 + "。。。" + "他" * 20)
    add("window: L 62 cuts through a run", "好" * 50 + "ABCDEFGH" + "你" * 10 + "，，" + "他" * 5)
    add("window: run right at the edge", "ABC" + "好" * 61, K=4)
    add("window: L 10", "我們都說，你好嗎？他說", L=10, K=2)
    add("window: L 1", "我們都說，你好嗎", L=1, K=1)
    add("window: L 1 punctuation", "我們都說，", L=1, K=1)
    add("window: L 62 K 60", "你" * 70, K=60)
    # candidates that are not made of Han characters: every one of their characters is a table lookup (UNK outside it)
    add("candidate with NUL, |, English, punctuation", "你好，他們", n=6, K=4, cand="好A|\0嗎，")
    add("candidate [SEP]-looking", "你好，他們", n=3, K=4, cand="|||")
    add("candidate with extension characters", "你好，他們", n=3, K=4, cand="\U00020000〇龘")
    return c


def build():
    """filename -> bytes. The model file is tiny-int8-v6.sjtl's (tools/gen_tinylm_tiny.py)."""
    import tempfile
    import gen_tinylm_tiny as g
    with tempfile.TemporaryDirectory() as tmp:
        _, rpath = g.v6_model(tmp, *g.v6_hashes())
        voc = tinylm.NumpyLM(rpath).vocab
    hexs = lambda s: ",".join(f"{ord(ch):X}" for ch in s)  # noqa: E731
    rows = []
    for note, before, n, L, K, cand in cases():
        key = tinylm.key_b(before, n, L, K)
        k = [voc.sep_id if ch == tinylm.SEP_CHAR else voc.ids.get(ch, tinylm.UNK) for ch in key]
        cd = voc.enc(cand)
        k = k[max(0, len(k) - (CTX - 1 - len(cd))):]               # only when [BOS] + k + c exceeds the context (ll_many)
        ids = [tinylm.BOS] + k + cd
        rows.append("\t".join([note, str(L), str(K), str(n), hexs(before), hexs(cand), key.replace(tinylm.SEP_CHAR, "|"), ",".join(map(str, ids))]))
    return {"keys.tsv": ("\n".join(rows) + "\n").encode()}


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    os.makedirs(OUT, exist_ok=True)
    for name, b in build().items():
        open(os.path.join(OUT, name), "wb").write(b)
        print(name, len(b))
