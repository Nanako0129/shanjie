"""S2r-2 §4.6 (c)：疊加層的變調列在 Python 端的行為（和 core/tests/engine_lm.rs overlay_sandhi_rows_load_and_cap 對應）。

一丈紅有兩列（主要列 ㄧ 在前、變調列 ㄧˊ 在後，變調列分數低 0.5）：ime.Lexicon.by_word 要給主要列的讀音，
兩個讀音都在 by_reading 裡，分數差 0.5。--self-test 把兩列對調並拿掉降分（變成舊的同分排法）後必須失敗。
用法：python3 reference/proto/check_overlay_variants.py [--self-test]
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ime  # noqa: E402

PRIMARY, VARIANT = ("ㄧ", "ㄓㄤˋ", "ㄏㄨㄥˊ"), ("ㄧˊ", "ㄓㄤˋ", "ㄏㄨㄥˊ")


def check(overlays):
    lex = ime.Lexicon(os.path.join(os.path.dirname(ime.OVERLAYS[0]), "mcbpmf-data.txt"), overlays)
    assert lex.by_word["一丈紅"][0] == PRIMARY, f"by_word gives {lex.by_word['一丈紅'][0]}, want the primary reading {PRIMARY}"
    for r in (PRIMARY, VARIANT):
        assert any(w == "一丈紅" for w, _ in lex.by_reading[r]), f"一丈紅 missing under {r}"
    sc = {r: next(s for w, s in lex.by_reading[r] if w == "一丈紅") for r in (PRIMARY, VARIANT)}
    assert abs(sc[PRIMARY] - sc[VARIANT] - 0.5) < 1e-9, f"variant must score the primary minus 0.5, got {sc}"


def main():
    check(ime.OVERLAYS)
    print("ok: by_word('一丈紅') is the primary reading; both readings are loaded")
    if "--self-test" in sys.argv:
        rows = open(ime.OVERLAYS[0], encoding="utf-8").readlines()
        i = next(k for k, l in enumerate(rows) if l.split("\t")[1] == "一丈紅")
        assert rows[i].startswith("ㄧ-") and rows[i + 1].startswith("ㄧˊ-")
        p, v = rows[i].split("\t"), rows[i + 1].split("\t")
        v[2] = p[2]                                 # 拿掉降分，再對調：變調列同分且排在前面
        rows[i], rows[i + 1] = "\t".join(v), "\t".join(p)
        with tempfile.NamedTemporaryFile("w", suffix=".tsv", encoding="utf-8", delete=False) as f:
            f.writelines(rows)
        try:
            check([f.name, ime.OVERLAYS[1]])
        except AssertionError as e:
            print(f"self-test ok: swapped rows fail ({e})")
        else:
            sys.exit("self-test FAILED: swapped rows passed")
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    main()
