"""用法：python3 tools/anyctx_seed.py anyctx-cv.tsv chat|formal OUT [--check]
讀 anyctx-cv.tsv 的某一 profile，寫種子學習檔：標頭 `#shanjie-learning v1`，每筆五欄
`種子\t<reading>\t<word>\t1\t<今天的日數>`（欄位順序與 `Record::day` 單位＝本地曆日自 1970-01-01 起的天數，見 core/src/learn_store.rs 檔頭、core/src/learn.rs `local_day`）。
wrong_run 空的列不記；同一個（讀音，詞）只記一筆。--check 時跑最小自我檢查後結束。"""
import sys, datetime
HEADER = "#shanjie-learning v1"
CTX = "種子"

def today():
    return (datetime.date.today() - datetime.date(1970, 1, 1)).days

def seed_lines(tsv, profile, day):
    seen, out = set(), []
    for l in open(tsv, encoding="utf-8").read().splitlines()[1:]:
        p, _row, run, rd = (l.split("\t") + ["", "", "", ""])[:4]
        if p != profile or not run or (rd, run) in seen:
            continue
        seen.add((rd, run))
        out.append(f"{CTX}\t{rd}\t{run}\t1\t{day}")
    return out

def selfcheck():
    import tempfile, os
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(d, "x.tsv")
        open(t, "w", encoding="utf-8").write("profile\trow\twrong_run\treading\nchat\t1\t\t\nchat\t2\t權限\tㄑㄩㄢˊ-ㄒㄧㄢˋ\nchat\t3\t權限\tㄑㄩㄢˊ-ㄒㄧㄢˋ\nformal\t4\t全線\tㄑㄩㄢˊ-ㄒㄧㄢˋ\n")
        assert seed_lines(t, "chat", 5) == ["種子\tㄑㄩㄢˊ-ㄒㄧㄢˋ\t權限\t1\t5"]
        assert seed_lines(t, "formal", 5) == ["種子\tㄑㄩㄢˊ-ㄒㄧㄢˋ\t全線\t1\t5"]
        assert seed_lines(t, "none", 5) == []
    assert today() > 20000
    print("selfcheck ok")

if __name__ == "__main__":
    if "--check" in sys.argv:
        selfcheck(); sys.exit(0)
    tsv, prof, out = sys.argv[1:4]
    lines = seed_lines(tsv, prof, today())
    open(out, "w", encoding="utf-8").write(HEADER + "\n" + "".join(l + "\n" for l in lines))
    print(f"{out}: {len(lines)} records")
