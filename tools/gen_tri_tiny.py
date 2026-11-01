"""Trigram toy golden for the cross-language check (docs/contracts/trigram-core.md section 2, acceptance 2 and 3): Python writes, core/tests/tri_tiny.rs compares.

The toy bigram model, classes.sjc and kn.sjkn are the ones in eval/golden/kn-tiny/ (tools/gen_kn_tiny.py); this adds, under eval/golden/tri-tiny/:
  trigram.sjt3 (the formal profile's table) and trigram-chat.sjt3 (the chat profile's; other counts, so the two profiles score differently), both SJT30001 bound
  to that bigram.sjlm (tools/build_trigram.from_counts), lexicon.txt (the kn-tiny lexicon plus the word 他占 for ㄊㄚ-ㄓㄢˋ, so one surface has two segmentations
  that differ in the previous word) and decode.tsv (the Python reference decoder with the profile's own table: config, start word, sentence, rank, surface, score
  repr). Configs: formal and chat score with kn-tiny's kn.sjkn (beta 1 in the file; formal takes lm.FORMAL_KN_BETA, chat the file's), formal-nokn and chat-nokn
  without a side file.
The table covers every case the formula has: a kept entry, a context whose entry is missing, a context that is not stored, an unknown u (a start word that is not <s>).
Regenerate: python3 -B tools/gen_tri_tiny.py (tools/test_build_trigram.py checks the committed files equal the current output).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for sub in ("tools", "reference/proto"):
    sys.path.insert(0, os.path.join(ROOT, sub))
import build_trigram  # noqa: E402
import ime  # noqa: E402
import lm as L  # noqa: E402
import trigram  # noqa: E402

KN_TINY = os.path.join(ROOT, "eval", "golden", "kn-tiny")
OUT = os.path.join(ROOT, "eval", "golden", "tri-tiny")
STARTS = ["<s>", "他", "占", "起牀"]
SENTENCES = ["ㄊㄚ ㄓㄢˋ", "ㄊㄚ ㄓㄢˋ ㄊㄚ ㄓㄢˋ", "ㄑㄧˇ ㄔㄨㄤˊ", "ㄊㄚ ㄑㄧˇ ㄔㄨㄤˊ", "ㄓㄢˋ ㄊㄚ ㄓㄢˋ", "ㄊㄚ ㄓㄢˋ ㄊㄚ"]
# (u, v, w) -> count. The comments name the case each row is for; the totals below are the pre-prune ones (more than the kept sum where a row was "pruned").
ENTRIES = {
    ("<s>", "<s>", "他"): 12, ("<s>", "<s>", "起床"): 6,                       # sentence start
    ("<s>", "他", "占"): 3, ("<s>", "他", "佔"): 1, ("<s>", "他", "起床"): 2,    # a kept entry, and (<s>, 他) -> 起牀 is not kept
    ("他", "占", "</s>"): 2,                                                  # the closing term; (他, 占) -> 他 is not kept
    ("<s>", "占", "他"): 5,                                                   # the transition into a fixed word on the right (End::Next)
    ("他", "佔", "</s>"): 1,
    ("他", "起床", "</s>"): 4,
    ("<s>", "起床", "</s>"): 6,                                               # back3 = 1 - (6 - 0.75) / 6
    ("占", "他", "占"): 9,                                                    # the recombination sentence: 他|占|他 and 他占|他 end alike, only the first has this future
    ("佔", "他", "佔"): 1,
    ("<s>", "起牀", "佔"): 9,                                                 # a context whose history word has only </s> in the bigram
}
TOTALS = {("<s>", "<s>"): 20, ("<s>", "他"): 8, ("他", "占"): 9, ("他", "佔"): 3, ("他", "起床"): 5, ("<s>", "起床"): 6, ("<s>", "占"): 6, ("占", "他"): 10,
          ("佔", "他"): 1, ("<s>", "起牀"): 10}
# The chat table: other counts on purpose, so that a profile reading the other profile's table is caught.
CHAT_ENTRIES = {("<s>", "<s>", "佔"): 7, ("<s>", "<s>", "他"): 5, ("<s>", "佔", "</s>"): 3, ("<s>", "他", "佔"): 4, ("他", "佔", "</s>"): 2}
CHAT_TOTALS = {("<s>", "<s>"): 14, ("<s>", "佔"): 4, ("<s>", "他"): 6, ("他", "佔"): 3}
EXTRA_LEXICON = "ㄊㄚ-ㄓㄢˋ 他占 -1.0\n"
CONFIGS = (("formal", "formal", True), ("formal-nokn", "formal", False), ("chat", "chat", True), ("chat-nokn", "chat", False))   # (name, profile, side file)


def generate():
    """File name -> bytes (nothing is written)."""
    model = open(os.path.join(KN_TINY, "bigram.sjlm"), "rb").read()
    lexicon = open(os.path.join(KN_TINY, "lexicon.txt"), "rb").read().decode() + EXTRA_LEXICON
    tables = {"formal": build_trigram.from_counts(ENTRIES, TOTALS, model), "chat": build_trigram.from_counts(CHAT_ENTRIES, CHAT_TOTALS, model)}
    sha = __import__("hashlib").sha256(model).digest()
    with tempfile.TemporaryDirectory() as tmp:
        for name, b in (("bigram.sjlm", model), ("classes.sjc", open(os.path.join(KN_TINY, "classes.sjc"), "rb").read()),
                        ("trigram.sjt3", tables["formal"]), ("trigram-chat.sjt3", tables["chat"]), ("lexicon.txt", lexicon.encode())):
            open(os.path.join(tmp, name), "wb").write(b)
        lines = []
        for config, profile, side in CONFIGS:
            lm = L.BigramLM(os.path.join(tmp, "bigram.sjlm"), kn=os.path.join(KN_TINY, "kn.sjkn") if side else None)
            tri_lm = trigram.TriLM(L.profile_lm(lm, profile), trigram.TriTable.from_path(os.path.join(tmp, "trigram.sjt3" if profile == "formal" else "trigram-chat.sjt3"), sha))
            lex = L.cap_overlay(ime.Lexicon(os.path.join(tmp, "lexicon.txt")), set(), lm, demote_path=None)
            for start in STARTS:
                for i, s in enumerate(SENTENCES, 1):
                    for r, (sc, ws) in enumerate(trigram.decode3(lex, s.split(), tri_lm, profile, start=start), 1):
                        lines.append(f"{config}\t{start}\t{i}\t{r}\t{''.join(ws)}\t{sc!r}\n")
    return {"trigram.sjt3": tables["formal"], "trigram-chat.sjt3": tables["chat"], "lexicon.txt": lexicon.encode(), "decode.tsv": "".join(lines).encode()}


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, b in generate().items():
        open(os.path.join(OUT, name), "wb").write(b)
        print(name, len(b))
