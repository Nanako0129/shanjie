"""中英混打的單字表 english-words.txt（docs/contracts/zhen-mixed.md §2.6、§3.4）。

輸入：SymSpell 的 frequency_dictionary_en_82_765.txt（每行「字 次數」，頻率遞減），釘在下面的 commit 與 SHA-256；
輸入雜湊不符就失敗。過濾 `^[a-z]{3,32}$`（含撇號、數字、大寫或其他字元的字丟掉），依檔案順序取長度 3 的前 LEN3_CAP 個、
長度 >= 4 的前 LEN4_CAP 個，重複的字只留第一個，輸出按位元組排序，一行一字、LF 結尾。
輸出的 SHA-256 記在 data/english-words.txt.sha256（scripts/build-app.sh 與 CI 核對）。

用法：python3 tools/build_english_words.py --out FILE [--input FILE] [--len3 N] [--len4 N|all]
印出行數與輸出的 SHA-256。--print-url、--print-input-sha256 只印釘住的下載網址與輸入雜湊（scripts/build-app.sh 用，
下載與核對的常數只有這裡一份）。--len3、--len4 給 §4 的格點用；LEN3_CAP、LEN4_CAP 是出貨的值（起始值 3000 / 30000，
最終值由 §4.4 的量測決定後寫回這裡）。
"""
import argparse
import hashlib
import os
import re
import sys

SYMSPELL_COMMIT = "c239062ae02961df18ab7da1671d01b4388204e0"
SYMSPELL_FILE = "SymSpell/frequency_dictionary_en_82_765.txt"
SYMSPELL_URL = f"https://raw.githubusercontent.com/wolfgarbe/SymSpell/{SYMSPELL_COMMIT}/{SYMSPELL_FILE}"
INPUT_SHA256 = "c604e1121e398ae7c7fbf777f11e0a0f2fa66eda932cb9fba1321466cf3acd7b"
DEFAULT_INPUT = os.path.expanduser("~/.cache/shanjie/english/frequency_dictionary_en_82_765.txt")

LEN3_CAP = 3000
LEN4_CAP = 30000

WORD = re.compile(r"[a-z]{3,32}")


def check_input(path):
    """輸入檔的位元組；SHA-256 不是釘住的那個就結束（不接受別的版本）。"""
    with open(path, "rb") as f:
        data = f.read()
    got = hashlib.sha256(data).hexdigest()
    if got != INPUT_SHA256:
        sys.exit(f"error: {path} has SHA-256 {got}, expected {INPUT_SHA256} (SymSpell {SYMSPELL_COMMIT})")
    return data


def build(lines, len3=LEN3_CAP, len4=LEN4_CAP):
    """lines：輸入的每一行（「字 次數」）。回傳排好序的字。len4 為 None 是不設上限。"""
    seen, n3, n4 = set(), 0, 0
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        w = fields[0]
        if not WORD.fullmatch(w) or w in seen:
            continue
        if len(w) == 3:
            if n3 >= len3:
                continue
            n3 += 1
        else:
            if len4 is not None and n4 >= len4:
                continue
            n4 += 1
        seen.add(w)
    return sorted(seen, key=lambda w: w.encode())


def render(words):
    return "".join(w + "\n" for w in words).encode("ascii")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--out")
    ap.add_argument("--print-url", action="store_true")
    ap.add_argument("--print-input-sha256", action="store_true")
    ap.add_argument("--len3", type=int, default=LEN3_CAP)
    ap.add_argument("--len4", default=str(LEN4_CAP), help="N 或 all")
    a = ap.parse_args(argv)
    if a.print_url or a.print_input_sha256:
        print(SYMSPELL_URL if a.print_url else INPUT_SHA256)
        return
    if not a.out:
        ap.error("--out is required")
    len4 = None if a.len4 == "all" else int(a.len4)
    text = check_input(a.input).decode("utf-8")
    out = render(build(text.splitlines(), a.len3, len4))
    with open(a.out, "wb") as f:
        f.write(out)
    lines_n = out.count(b"\n")
    print(f"{lines_n} lines, SHA-256 {hashlib.sha256(out).hexdigest()}")


if __name__ == "__main__":
    main()
