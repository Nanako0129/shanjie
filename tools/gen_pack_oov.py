"""Cross-language golden for docs/contracts/pack-oov.md: the Python decoder's output with optional-pack words, which
core/tests/pack_oov.rs compares byte for byte against the Rust decoder.

The model is eval/golden/kn-tiny/ (他, 佔, 占, 起床, 起牀: bigram 他→占 kept, the Kneser-Ney side file, the class term), so a pack
word here IS in the model's vocabulary and has kept bigrams: the case where the model's contribution used to win. Rows:
  side file (plain, beta1) / pack set / start word / profile / sentence / rank / surface / score repr.
Pack sets: none; A = {占, 起床} (words the sentences decode); B = {他}, with the start word `history("占他")`, which is the pack word
他 itself (the left context ends with a pack word; `history` is unchanged, so both decoders get 他 and score it as unknown). A and B are
forced rows (`acg-oov`); C and D cover unforced pack words (see UNFORCED, OVERLAY_ONLY).
Regenerate: python3 -B tools/gen_pack_oov.py (tools/test_pack_oov.py checks the committed file equals the current output).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "reference", "proto"))
import ime  # noqa: E402
import lm as L  # noqa: E402

KN = os.path.join(ROOT, "eval", "golden", "kn-tiny")
OUT = os.path.join(ROOT, "eval", "golden", "pack-oov")
SENTENCES = ["ㄊㄚ ㄑㄧˇ ㄔㄨㄤˊ", "ㄊㄚ ㄓㄢˋ", "ㄑㄧˇ ㄔㄨㄤˊ", "ㄓㄢˋ ㄊㄚ", "ㄊㄚ ㄓㄢ", "ㄊㄚ ㄓㄢˋ ㄑㄧˇ ㄔㄨㄤˊ", "ㄊㄚ ㄕˋ ㄐㄧㄝˋ ㄒㄧㄢˋ"]
PACKS = {"none": frozenset(), "A": frozenset({"占", "起床"}), "B": frozenset({"他"}), "C": frozenset(), "D": frozenset()}
# pack-oov section 1b: unforced pack words (column 4 `acg`); C holds 世界線 (the model never saw it: keeps the pack score) and 占 (known:
# capped as in main). D puts 世界線 in the overlay without any pack, so it loses UNSEEN_OVERLAY_PENALTY: C and D differ only there.
UNFORCED = {"C": frozenset({"世界線", "占"})}
OVERLAY_ONLY = {"D": frozenset({"世界線"})}
LEFT = "占他"
ABOVE_CAP = {"占": "-0.05", "起床": "-0.1"}


def lexicon_text():
    """kn-tiny's lexicon with 占 and 起床 scored above any corpus frequency, so the overlay cap would lower them (a pack word is not capped)."""
    rows = []
    for l in open(os.path.join(KN, "lexicon.txt"), encoding="utf-8").read().splitlines():
        key, w, lp = l.split(" ")
        rows.append(f"{key} {w} {ABOVE_CAP.get(w, lp)}\n")
    return "".join(rows)


def generate():
    """檔名 -> 位元組（不寫檔）。"""
    with tempfile.TemporaryDirectory() as tmp:
        lex_path = os.path.join(tmp, "lexicon.txt")
        open(lex_path, "w", encoding="utf-8").write(lexicon_text())
        lines = decode_lines(lex_path)
    return {"decode.tsv": "".join(lines).encode(), "lexicon.txt": lexicon_text().encode()}


def decode_lines(lex_path):
    lines = []
    for side in ("plain", "beta1"):
        lm = L.BigramLM(os.path.join(KN, "bigram.sjlm"), kn=os.path.join(KN, "kn.sjkn") if side == "beta1" else None)
        for pack, words in PACKS.items():
            # The overlay holds the pack words too, as the engine's capping text does: forced words must still not be capped.
            unforced = UNFORCED.get(pack, frozenset())
            overlay = words | unforced | OVERLAY_ONLY.get(pack, frozenset())
            lex = L.cap_overlay(ime.Lexicon(lex_path), overlay, lm, demote_path=None, pack_words=words, unseen_pack_words=unforced)
            for start in ("<s>", L.history(LEFT, lm)):
                for profile in ("chat", "formal"):
                    for i, s in enumerate(SENTENCES, 1):
                        for r, (sc, ws) in enumerate(L.decode(lex, s.split(), lm, profile, start=start), 1):
                            lines.append(f"{side}\t{pack}\t{start}\t{profile}\t{i}\t{r}\t{''.join(ws)}\t{sc!r}\n")
    return lines


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, b in generate().items():
        open(os.path.join(OUT, name), "wb").write(b)
        print(name, len(b))
