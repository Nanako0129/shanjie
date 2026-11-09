"""S2r 探針（docs/spec/decoder.md §8.1）：把句子裡「一」「不」的讀音換成另一種合規讀音。

規則同 tools/build_sandhi.py（教育部〈單一音讀〉）：讀變調的換成本調；讀本調、而且依規則可以變調的換成變調；
數詞序詞、詞尾、疊字動詞中間的「一」不換。一列換掉所有可換的位置，產生一個探針列；沒有可換位置的列略過。

輸出：
- eval/probe/s2r-probe.txt（commit）：dev302 與 typing76 的探針列，格式同 dev 檔「前文|句子|讀音」。
  不放在 eval/dev/ 底下，免得改變「開發集前 302 列」。
- 其他評測集不寫檔，評測時用同一個 flip 現算。

用法：python3 tools/build_probe.py [--check]   （--check 只重產並比對 commit 的那份）
"""
import argparse
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import build_sandhi as sd  # noqa: E402

PUBLIC = os.path.join(ROOT, "eval", "probe", "s2r-probe.txt")


def rows_of(path):
    """(句子, 音節列) of every 前文|句子|讀音 row of one file."""
    out = []
    for line in open(path, encoding="utf-8"):
        p = line.rstrip("\n").split("|")
        if len(p) == 3:
            out.append((p[1], p[2].split()))
    return out


def public_sets():
    """dev302 = the first 302 rows of eval/dev/*.txt (sorted, user-typing.txt left out); typing76 = eval/dev/user-typing.txt."""
    dev = []
    for f in sorted(glob.glob(os.path.join(ROOT, "eval", "dev", "*.txt"))):
        if not f.endswith("user-typing.txt"):
            dev += rows_of(f)
    return dev[:302], rows_of(os.path.join(ROOT, "eval", "dev", "user-typing.txt"))


def flip(sent, syls):
    """回傳換過讀音的音節列（每個「一」「不」都換成 sd.other_reading）；沒有可換的位置時回 None。"""
    if len(sent) != len(syls):
        return None
    out = [sd.other_reading(sent, syls, i) or s for i, s in enumerate(syls)]
    return out if out != list(syls) else None


def probe_lines(rows):
    lines = []
    for sent, syls in rows:
        new = flip(sent, syls)
        if new is not None:
            lines.append(f"|{sent}|{' '.join(new)}\n")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    dev302, typing76 = public_sets()
    public = probe_lines(dev302) + probe_lines(typing76)
    text = "".join(public)
    if a.check:
        if not os.path.exists(PUBLIC) or open(PUBLIC, encoding="utf-8").read() != text:
            sys.exit("error: eval/probe/s2r-probe.txt differs from a fresh build")
        print("s2r-probe.txt: up to date")
        return
    os.makedirs(os.path.dirname(PUBLIC), exist_ok=True)
    open(PUBLIC, "w", encoding="utf-8").write(text)
    print("probe rows:", {"dev302+typing76": len(public)})


if __name__ == "__main__":
    main()
