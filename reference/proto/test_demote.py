"""降權（docs/contracts/sw-sensitive-demote.md §2、§4 (a)）：δ 只在指定讀音生效、* 對所有讀音生效、特定讀音優先於 *；
表的格式規則與「每一列必須對得上詞庫的一個詞條」和核心一樣嚴格。不需要模型檔：用一個只有 word/eos 的假 LM。
執行：python3 reference/proto/test_demote.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lm as L  # noqa: E402


class FakeLex:
    max_len = 2
    by_reading = {("ㄅ",): [("a", -1.0), ("b", -2.0)], ("ㄆ",): [("a", -1.0), ("b", -2.0)], ("ㄅ", "ㄆ"): [("ab", -1.0)]}
    by_word = {"a": (("ㄅ",), -1.0), "b": (("ㄅ",), -2.0), "ab": (("ㄅ", "ㄆ"), -1.0)}
    demote = {}


class FakeLM:
    def word(self, lam, v, w, lp):
        return lam * lp

    def eos(self, lam, v):
        return 0.0


def scores(reading, demote):
    FakeLex.demote = TABLE
    return {"".join(ws): s for s, ws in L.decode(FakeLex, [reading], FakeLM(), "chat", demote=demote)}


def load(text):
    with tempfile.NamedTemporaryFile("wb", suffix=".tsv", delete=False) as f:
        f.write(text.encode("utf-8"))
    try:
        return L.load_demote(f.name, FakeLex)
    finally:
        os.unlink(f.name)


def rejects(text):
    try:
        load(text)
    except ValueError:
        return True
    return False


TABLE = {}


def main():
    global TABLE
    TABLE = load("# c\n\nㄅ\ta\t5.0\treading\tt\n*\tb\t0.25\treading\tt\nㄅ\tb\t1.0\treading\tt\n")
    off_b, on_b = scores("ㄅ", False), scores("ㄅ", True)
    off_p, on_p = scores("ㄆ", False), scores("ㄆ", True)
    assert on_p["a"] == off_p["a"], "標準讀音不扣"
    assert abs((off_b["a"] - on_b["a"]) - 5.0) < 1e-12
    assert abs((off_b["b"] - on_b["b"]) - 1.0) < 1e-12, "特定讀音優先於 *"
    assert abs((off_p["b"] - on_p["b"]) - 0.25) < 1e-12, "* 對所有讀音生效"
    assert max(on_b, key=on_b.get) == "b" and max(on_p, key=on_p.get) == "a"
    # decode 的預設是開（和核心的 decode 一樣）。
    dflt = {"".join(ws): s for s, ws in L.decode(FakeLex, ["ㄅ"], FakeLM(), "chat")}
    assert dflt == on_b
    # 格式：核心的 Demote::parse 同一份清單。
    assert not rejects("ㄅ\ta\t2\treading\ts\r\n\r\n"), "CRLF 與空行"
    for bad in ["ㄅ\ta\t1\treading\n", "ㄅ\ta\t1\treading\ts\textra\n", "ㄅ\ta\t0\treading\ts\n", "ㄅ\ta\t0.0\treading\ts\n",
                "ㄅ\ta\t-1\treading\ts\n", "ㄅ\ta\tinf\treading\ts\n", "ㄅ\ta\tnan\treading\ts\n", "ㄅ\ta\tx\treading\ts\n",
                "ㄅ\ta\t2_0\treading\ts\n", "ㄅ\ta\t 2\treading\ts\n", "ㄅ\ta\t2 \treading\ts\n", "ㄅ\ta\t+2\treading\ts\n",
                "ㄅ\ta\t1e1\treading\ts\n", "ㄅ\ta\t.5\treading\ts\n", "ㄅ\ta\t2.\treading\ts\n", "ㄅ\ta\t２\treading\ts\n",
                "ㄅ\ta\t٢\treading\ts\n", " ㄅ\ta\t1\treading\ts\n", "ㄅ\ta \t1\treading\ts\n", "ㄅ\ta\t1\t\ts\n",
                "ㄅ\ta\t1\treading\ts \n", "ㄅ\ta\t1\treading\ts\nㄅ\ta\t2\treading\ts\n"]:
        assert rejects(bad), f"accepted {bad!r}"
    # 對照詞庫：打錯的讀音（空格）、詞庫沒有的讀音、這個讀音下沒有的詞、* 的詞不存在。
    assert not rejects("ㄅ-ㄆ\tab\t1\treading\ts\n") and not rejects("*\tab\t1\treading\ts\n")
    for bad in ["ㄅ ㄆ\tab\t1\treading\ts\n", "ㄆ-ㄆ\tab\t1\treading\ts\n", "ㄅ\tab\t1\treading\ts\n", "*\tzz\t1\treading\ts\n"]:
        assert rejects(bad), f"accepted {bad!r}"
    print("ok")


if __name__ == "__main__":
    main()
