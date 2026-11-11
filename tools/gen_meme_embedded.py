"""迷因詞包的嵌入句產生器（docs/spec/lexicon-and-packs.md 的迷因詞包一節）：由 words-all.txt（`|詞|讀音` 一列一詞）產生嵌入句，
每個詞接三個固定的開頭、兩個固定的結尾，一詞六列，照輸入的先後（詞、開頭、結尾）：`|<開頭><詞><結尾>|<開頭讀音> <讀音> <結尾讀音>`。
用法：python3 tools/gen_meme_embedded.py WORDS OUT；`--check WORDS OUT` 在 OUT 和產生的內容不同時以非 0 結束。同一份輸入永遠產生同樣的位元組。"""
import sys

OPENINGS = (("他說", "ㄊㄚ ㄕㄨㄛ"), ("真的是", "ㄓㄣ ㄉㄜ˙ ㄕˋ"), ("我覺得", "ㄨㄛˇ ㄐㄩㄝˊ ㄉㄜ˙"))
ENDINGS = (("了", "ㄌㄜ˙"), ("吧", "ㄅㄚ˙"))


def generate(words_text):
    out = []
    for l in words_text.splitlines():
        if not l.strip():
            continue
        f = l.split("|")
        if len(f) != 3 or f[0] != "" or not f[1] or not f[2]:
            sys.exit(f"bad row (want |word|reading): {l!r}")
        _, word, reading = f
        out += [f"|{o}{word}{e}|{orr} {reading} {er}\n" for o, orr in OPENINGS for e, er in ENDINGS]
    return "".join(out)


def main(argv):
    check = argv[:1] == ["--check"]
    args = argv[1:] if check else argv
    if len(args) != 2:
        sys.exit(__doc__.splitlines()[-2])
    with open(args[0], encoding="utf-8") as f:
        text = generate(f.read())
    if check:
        with open(args[1], encoding="utf-8") as f:
            if f.read() != text:
                sys.exit(f"{args[1]} differs from what gen_meme_embedded.py makes from {args[0]}")
        return
    with open(args[1], "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


if __name__ == "__main__":
    main(sys.argv[1:])
