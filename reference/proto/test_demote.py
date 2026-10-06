"""降權（docs/contracts/sw-sensitive-demote.md §4 (a)）：δ 只在指定讀音生效、* 對所有讀音生效、特定讀音優先於 *。
不需要模型檔：用一個只有 word/eos 的假 LM。執行：python3 reference/proto/test_demote.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lm as L  # noqa: E402


class FakeLex:
    max_len = 1
    by_reading = {("ㄅ",): [("a", -1.0), ("b", -2.0)], ("ㄆ",): [("a", -1.0), ("b", -2.0)]}


class FakeLM:
    def word(self, lam, v, w, lp):
        return lam * lp

    def eos(self, lam, v):
        return 0.0


def scores(reading, demote):
    return {"".join(ws): s for s, ws in L.decode(FakeLex, [reading], FakeLM(), "chat", demote=demote)}


def load(text):
    with tempfile.NamedTemporaryFile("w", suffix=".tsv", delete=False, encoding="utf-8") as f:
        f.write(text)
    try:
        return L.load_demote(f.name)
    finally:
        os.unlink(f.name)


def main():
    d = load("# c\n\nㄅ\ta\t5.0\treading\tt\n*\tb\t0.25\treading\tt\nㄅ\tb\t1.0\treading\tt\n")
    off_b, on_b, off_p, on_p = scores("ㄅ", None), scores("ㄅ", d), scores("ㄆ", None), scores("ㄆ", d)
    assert on_p["a"] == off_p["a"], "標準讀音不扣"
    assert abs((off_b["a"] - on_b["a"]) - 5.0) < 1e-12
    assert abs((off_b["b"] - on_b["b"]) - 1.0) < 1e-12, "特定讀音優先於 *"
    assert abs((off_p["b"] - on_p["b"]) - 0.25) < 1e-12, "* 對所有讀音生效"
    assert max(on_b, key=on_b.get) == "b" and max(on_p, key=on_p.get) == "a"
    for bad in ["ㄅ\ta\t1\treading\n", "ㄅ\ta\t0\treading\ts\n", "ㄅ\ta\t-1\treading\ts\n", "ㄅ\ta\tnan\treading\ts\n",
                "ㄅ\ta\tinf\treading\ts\n", "ㄅ\ta\t1\treading\ts\nㄅ\ta\t2\treading\ts\n"]:
        try:
            load(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")
    print("ok")


if __name__ == "__main__":
    main()
