"""A1 demotion table generator (contract docs/contracts/reading-aware-0.md 3.2).

Emits the ˙ rows of the 44 characters of the contract's "建議清單" (the group that is demoted: a character that never reads neutral tone
when it stands alone, e.g. 尾巴, 東西) at delta (default 1.0), category `reading-neutral`, in the format of docs/contracts/sw-sensitive-demote.md
section 2 (`reading<TAB>word<TAB>delta<TAB>category<TAB>source`, syllables joined by `-`). Two checks, both stop the run:
  - every character has a ˙ reading and a non-˙ reading in the lexicon (contract 3.2, last bullet);
  - the written table loads through lm.load_demote against the lexicon (every row's word exists under that reading; format rules).
The shipped data/lexicon/demote.tsv is not touched; ra_eval.py merges this table with it at run time.

  python3 a1_table.py --out FILE [--delta 1.0]
"""
import argparse

import rcommon
import lm as L

# Contract reading-aware-0 3.2, the "降" group of the suggested list (main lists, the maintainer looks, then it is frozen): 44 characters
# whose ˙ reading only appears at the end of a word. 琶 and 杷 are here because their first-ranked reading in the lexicon is ˙ (琵琶, 枇杷)
# while standing alone they read ㄆㄚˊ. NOT in it: 的 了 著 麼 得 地 吧 啊 呀 啦 嗎 呢 嘛 哪 哩 嘞 唄 嗯 們 個 下 子 頭 (a single ˙ is real there).
DEMOTE_44 = "西公巴水太謝意思和阿夫寶弟妹爸姊嫂舅叔嬸姑婆娘奶姥娃鼓係價遮匙裳碌簍蟆蟇掇蔔罷嗦遢晢琶杷"
CATEGORY = "reading-neutral"
SOURCE = "reading-aware-0"
DEFAULT_DELTA = 1.0
HEADER = ("# A1 demotion table of docs/contracts/reading-aware-0.md 3.2: the ˙ reading of a character that only reads neutral tone at the end of a word.\n"
          "# Format of docs/contracts/sw-sensitive-demote.md section 2. Merged with data/lexicon/demote.tsv by experiments/reading-aware/ra_eval.py.\n")


def fmt_delta(d):
    s = f"{float(d):.6f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def rows(lex, delta=DEFAULT_DELTA, chars=DEMOTE_44):
    """[(reading, char, delta text, category, source)] in the order of `chars`, then reading. Raises ValueError for a listed character
    that lacks a ˙ reading or a non-˙ reading (or repeats in the list)."""
    if len(set(chars)) != len(chars):
        raise ValueError("a character is listed twice")
    tab = rcommon.char_readings(lex)
    out = []
    for c in chars:
        rs = [r for r, _ in tab.get(c, ())]
        dots, plain = sorted(r for r in rs if rcommon.is_dot(r)), [r for r in rs if not rcommon.is_dot(r)]
        if not dots or not plain:
            raise ValueError(f"{c}: needs both a ˙ and a non-˙ reading, has {rs}")
        out += [(r, c, fmt_delta(delta), CATEGORY, SOURCE) for r in dots]
    return out


def table_text(lex, delta=DEFAULT_DELTA, chars=DEMOTE_44):
    return HEADER + "".join("\t".join(r) + "\n" for r in rows(lex, delta, chars))


def write_checked(path, lex, delta=DEFAULT_DELTA, chars=DEMOTE_44):
    """Write the table, then load it like the engine does (ValueError if a row matches no lexicon entry or the format is wrong).
    Returns the number of rows."""
    text = table_text(lex, delta, chars)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return len(L.load_demote(path, lex))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--delta", type=float, default=DEFAULT_DELTA)
    a = ap.parse_args(argv)
    n = write_checked(a.out, rcommon.load_lexicon(), a.delta)
    print(f"a1_table: {n} rows, delta {fmt_delta(a.delta)}, {len(DEMOTE_44)} characters -> {a.out}")


if __name__ == "__main__":
    main()
