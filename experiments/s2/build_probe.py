"""S2r 探針（docs/contracts/s2r-sandhi-variants.md §4.0）：把句子裡「一」「不」的讀音換成另一種合規讀音。

規則同 tools/build_sandhi.py（教育部〈單一音讀〉）：讀變調的換成本調；讀本調、而且依規則可以變調的換成變調；
數詞序詞、詞尾、疊字動詞中間的「一」不換。一列換掉所有可換的位置，產生一個探針列；沒有可換位置的列略過。

輸出：
- eval/probe/s2r-probe.txt（commit）：dev302 與 typing76 的探針列，格式同 dev 檔「前文|句子|讀音」。
  不放在 eval/dev/ 底下，免得改變「開發集前 302 列」。
- cvtune（含 Tatoeba 句子，CC BY 2.0 FR）與 discordtune（私有）不寫檔：只印列數，評測時由
  experiments/s2/s2r_eval.py 用同一個 flip 現算，私有與否照 iter2.all_sets 的標記。

用法：python3 experiments/s2/build_probe.py [--check]   （--check 只重產並比對 commit 的那份）
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, HERE)
import build_sandhi as sd  # noqa: E402
from iter2 import all_sets  # noqa: E402

PUBLIC = os.path.join(ROOT, "eval", "probe", "s2r-probe.txt")


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
    sets = all_sets()
    public = probe_lines(sets["dev302"][0]) + probe_lines(sets["typing76"][0])
    text = "".join(public)
    if a.check:
        if not os.path.exists(PUBLIC) or open(PUBLIC, encoding="utf-8").read() != text:
            sys.exit("error: eval/probe/s2r-probe.txt differs from a fresh build")
        print("s2r-probe.txt: up to date")
        return
    os.makedirs(os.path.dirname(PUBLIC), exist_ok=True)
    open(PUBLIC, "w", encoding="utf-8").write(text)
    counts = {"dev302+typing76": len(public)}
    for name in ("cvtune", "discordtune"):
        if name in sets:
            counts[name] = len(probe_lines(sets[name][0]))
    print("probe rows:", counts)


if __name__ == "__main__":
    main()
