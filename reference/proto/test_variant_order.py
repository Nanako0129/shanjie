"""異體排序表（docs/spec/decoder.md §2.3）：格式規則、套用後同一讀音常用寫法在前、原分數較低的少用寫法不變、
詞庫缺寫法就失敗；以及真詞庫加真模型封頂之後，常用寫法的分數仍高於少用寫法（拿掉 cap_overlay 的第二次套用，這個測試要失敗）。
執行：python3 reference/proto/test_variant_order.py"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ime  # noqa: E402
import lm as L  # noqa: E402

ROOT = os.path.join(HERE, "..", "..")


def lexicon(text, variants):
    d = tempfile.mkdtemp()
    for name, body in (("base.txt", text), ("v.tsv", variants)):
        with open(os.path.join(d, name), "w", encoding="utf-8", newline="") as f:
            f.write(body)
    return ime.Lexicon(os.path.join(d, "base.txt"), variants=os.path.join(d, "v.tsv"))


def rejected(text):
    try:
        ime.parse_variant_order(text)
    except ValueError:
        return True
    return False


BASE = "ㄅ-ㄆ 甲甲 -0.2\nㄅ-ㄆ 乙乙 -0.5\nㄅ-ㄆ 丙丙 -3.0\nㄇ 丁 -2.0\nㄇ 戊 -2.0\n"


def main():
    ok = "# c\n\nㄅ-ㄆ\t甲甲\t乙乙\tt\nㄇ\t丁\t戊\tt\n"
    assert ime.parse_variant_order(ok) == [(("ㄅ", "ㄆ"), "甲甲", "乙乙"), (("ㄇ",), "丁", "戊")]
    for bad in ["ㄅ\ta\tb\n", "ㄅ\ta\tb\tt\textra\n", "ㄅ\ta\ta\tt\n", "ㄅ\t a\tb\tt\n", "ㄅ\ta\tb\t\n",
                "ㄅ\ta\tb\tt\nㄅ\ta\tc\tt\n", "ㄅ\ta\tb\tt\nㄆ\tb\tc\tt\n"]:
        assert rejected(bad), bad
    lex = lexicon(BASE, ok)
    assert [w for w, _ in lex.by_reading[("ㄅ", "ㄆ")]][:2] == ["乙乙", "甲甲"], "常用寫法排在前面"
    assert dict(lex.by_reading[("ㄅ", "ㄆ")])["甲甲"] == -0.5 - ime.VARIANT_GAP
    assert dict(lex.by_reading[("ㄅ", "ㄆ")])["丙丙"] == -3.0, "不在表裡的詞不動"
    assert [w for w, _ in lex.by_reading[("ㄇ",)]] == ["戊", "丁"], "平手也是常用在前"
    assert lex.by_word["甲甲"][1] == -0.5 - ime.VARIANT_GAP, "by_word 照覆寫後的分數"
    low = lexicon("ㄅ 甲 -0.5\nㄅ 乙 -1.0\n", "ㄅ\t甲\t乙\tt\n")
    assert dict(low.by_reading[("ㄅ",)])["甲"] == -1.0 - ime.VARIANT_GAP
    keep = lexicon("ㄅ 甲 -9.0\nㄅ 乙 -1.0\n", "ㄅ\t甲\t乙\tt\n")
    assert dict(keep.by_reading[("ㄅ",)])["甲"] == -9.0, "原分數已經比較低：不變"
    for text, v in [(BASE, "ㄅ-ㄆ\t甲甲\t不在\tt\n"), (BASE, "ㄅ-ㄆ\t不在\t乙乙\tt\n"), (BASE, "ㄇ\t甲甲\t乙乙\tt\n")]:
        try:
            lexicon(text, v)
        except ValueError:
            continue
        raise AssertionError("詞庫缺寫法要失敗")

    # docs/spec/decoder.md §2.3：真詞庫加真模型，封頂之後常用寫法 >= 少用寫法
    base = ime.Lexicon(os.path.join(ROOT, "data", "lexicon", "mcbpmf-data.txt"), overlay=ime.overlays(), variants=ime.VARIANT_TSV)
    lm = L.BigramLM(os.path.join(ROOT, "data", "lm", "bigram.sjlm"), classes=True)
    capped = L.cap_overlay(base, ime.capping_words(), lm)
    for more, less in [("堂姐", "堂姊"), ("加濕器", "加溼器"), ("長姐", "長姊")]:
        key = base.by_word[less][0]
        sc = dict(capped.by_reading[key])
        assert sc[more] >= sc[less] + ime.VARIANT_GAP - 1e-12, (more, less, sc[more], sc[less])
    print("ok")


if __name__ == "__main__":
    main()
