"""S2r 探針（docs/spec/decoder.md §8.1）：把句子裡「一」「不」的讀音換成另一種合規讀音。

規則同 tools/build_sandhi.py（教育部〈單一音讀〉）：讀變調的換成本調；讀本調、而且依規則可以變調的換成變調；
數詞序詞、詞尾、疊字動詞中間的「一」不換。一列換掉所有可換的位置，產生一個探針列；沒有可換位置的列略過。

輸出：
- eval/probe/s2r-probe.txt（commit）：dev302 與 typing76 的探針列，格式同 dev 檔「前文|句子|讀音」。
  不放在 eval/dev/ 底下，免得改變「開發集前 302 列」。
- 其他評測集不寫檔，評測時用同一個 flip 現算。

三聲變調探針（docs/spec/evaluation.md §1.1、contract tone3-sandhi §3.1）：把評測列的讀音照實際讀音改寫，以標點切成的子句為單位。
- 雙單版（ds）：連續上聲的每一段，除了最後一個都改成陽平；上聲接本調上聲的輕聲時，前字改陽平（輕聲字當那一段的最後一個）。
- 單雙版（sd）：三個以上連續上聲的段，第一個保留上聲、其餘照雙單版；兩個連續上聲的段和雙單版相同。
- 沒有任何位置改到的列不進探針。探針檔不進 repo：`--tone3 ds|sd --rows IN --out OUT` 寫到 repo 外（例如 ~/.cache/shanjie/），不覆寫已有的檔。

用法：python3 tools/build_probe.py [--check]   （--check 只重產並比對 commit 的那份）
      python3 tools/build_probe.py --tone3 ds|sd --rows IN --out OUT
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


_tone_base = None


def base_third(ch):
    """「本調是上聲」：這個字當單字詞的非輕聲讀音裡，分數最高的是上聲（基底詞庫，同分取讀音字串較小的）。輕聲字的本調只看同韻的讀音，見 is_third_neutral。"""
    global _tone_base
    if _tone_base is None:
        _tone_base = {}
        for word, syls, score, _ in sd.base_rows():
            if len(word) == 1 and syls[0][-1] != "˙":
                _tone_base.setdefault(word, {})[syls[0]] = max(score, _tone_base.get(word, {}).get(syls[0], score))
    return _tone_base.get(ch, {})


def is_third_neutral(ch, syl):
    """輕聲 syl（例如 ㄒㄧㄤ˙）的本調是上聲：這個字同韻的非輕聲單字讀音裡分數最高的是 Xˇ（build_sandhi 的 neutral 規則用的同一個「本調」）。"""
    if not syl.endswith("˙"):
        return False
    rime = syl[:-1]
    cand = {r: s for r, s in base_third(ch).items() if r.rstrip(sd.TONE_MARKS) == rime}
    if not cand:
        return False
    top = max(cand.values())
    return min(r for r, s in cand.items() if s == top).endswith("ˇ")


def tone3_flip(sent, syls, variant, third_neutral=is_third_neutral):
    """回傳照實際讀音改寫過的音節列（variant "ds" 雙單版、"sd" 單雙版）；沒有改到任何位置時回 None。字數和音節數不同的列回 None。
    沒有注音符號的音節（標點）切開子句；`third_neutral(字, 輕聲讀音)` 判斷輕聲字的本調是不是上聲（測試可換掉）。"""
    if len(sent) != len(syls) or variant not in ("ds", "sd"):
        return None
    out = list(syls)
    member = [s.endswith("ˇ") or third_neutral(sent[i], s) for i, s in enumerate(syls)]
    i = 0
    while i < len(syls):
        if not (syls[i].endswith("ˇ")):
            i += 1
            continue
        j = i
        while j + 1 < len(syls) and member[j + 1] and syls[j].endswith("ˇ"):
            j += 1   # a run of third tones, which a third-tone neutral ends (it is the run's last member)
        run = list(range(i, j + 1))
        keep_first = variant == "sd" and len(run) >= 3
        for k in run[:-1]:
            if syls[k].endswith("ˇ") and not (keep_first and k == run[0]):
                out[k] = syls[k][:-1] + "ˊ"
        i = j + 1
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
    ap.add_argument("--tone3", choices=("ds", "sd"), help="三聲變調探針：ds 雙單版、sd 單雙版（需要 --rows 與 --out）")
    ap.add_argument("--rows")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.tone3:
        if not a.rows or not a.out:
            sys.exit("error: --tone3 needs --rows and --out")
        if os.path.exists(a.out):
            sys.exit("error: --out exists (not overwritten)")
        lines = []
        for line in open(a.rows, encoding="utf-8"):
            p = line.rstrip("\n").split("|")
            if len(p) == 3:
                new = tone3_flip(p[1], p[2].split(), a.tone3)
                if new is not None:
                    lines.append(f"{p[0]}|{p[1]}|{' '.join(new)}\n")
        with open(a.out, "x", encoding="utf-8") as f:
            f.write("".join(lines))
        print("tone3 probe rows:", len(lines))
        return
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
