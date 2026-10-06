# S2w：維基計數改用 MediaWiki 的 zh-tw 轉換

契約：`docs/contracts/s2w-mediawiki-zhtw.md`。以下都在 repo 根目錄、用裝了 zhconv-rs 0.4.2 的 venv 跑（`PY=~/.cache/shanjie/venv-s2w/bin/python`）。
`S2_WORK` 預設 `~/.cache/shanjie/work/s2w/`（`mwdata.json` 與 `review-100-items.tsv` 放這裡）；`build_counts.py`、`build_tune.py` 沒設 `S2_WORK` 時預設是 `work/s2`，所以用 `--mw` 時一律明確設 `S2_WORK`。

| 檔案 | 用途 |
|---|---|
| `mwconv.py` | 一篇條目的轉換（`--mw` 用）：站上轉換表、群組、數字參數寫成 `-{H|…}-` 放在最前面，再交給 `zhconv_rs.zhconv(text, "zh-tw", True)` |
| `mwdata.py` | 從 dump 一趟抽出群組、NoteTA 別名、站上轉換表 → `mwdata.json` |
| `test_mw.py` | 單元檢查（含單向規則、優先順序、兩個突變） |
| `gate1.py` | 第一關（§4.1）；`--decide` 算符號檢定 |
| `cv_native.py` | cvtune-native（§4.3） |

## 指令（依序）

```sh
cd /Users/nanako/side-project/shanjie-s2w   # 或合併後的 main
PY=~/.cache/shanjie/venv-s2w/bin/python

# 1. 單元檢查（不需要 dump）；全部 ok 才往下，單向規則或優先順序不過就停
$PY experiments/s2w/test_mw.py

# 2. 抽轉換資料，一趟讀完整個 dump（讀整份 bz2，時間未量過，預期超過 10 分鐘）；印出群組數、規則數、解不開的群組、站上表條目數
$PY experiments/s2w/mwdata.py

# 3. 第一關：前 2,000 篇，印出每個數字與停止條件；寫 experiments/s2w/diff-top100.tsv、review-100.tsv（空白判讀欄）、$S2_WORK/review-100-items.tsv（含整段，不進 repo）
$PY experiments/s2w/gate1.py

# 4. 人工判讀：填 review-100.tsv 的 verdict 欄（MW 對／S2n 對／兩者都可／都不對）與 reason，之後
$PY experiments/s2w/gate1.py --decide

# 5. cvtune-native（需要 $S2_WORK/tune/cvtune.txt；少於 1,000 列會以非 0 結束）
S2_WORK=<放 cvtune.txt 的工作目錄> python3 experiments/s2w/cv_native.py
```

188 重建（§4.2，第一關過了才做）：`S2_WORK=<S2w 工作目錄>`，`mwdata.py`；`build_counts.py --articles 200000 --mw --expected`；`build_tune.py --part wiki --mw`；`tools/build_lm.py`。
`gate1.py` 重跑時，如果 `review-100.tsv` 已經有判讀就不覆蓋。

## 實作上的決定

- 數字參數與群組規則裡的換行壓成空白（zhconv-rs 抽全文規則的 regex 不跨行）；條目本文自己的 `-{…}-` 原樣交給 zhconv-rs，跨行的只由 `gate1.py` 數出來。
- 群組同名只放一次（`G1=X` 與 `G2=X`）；站上轉換表的條目含 `{ } | ;` 的略過並計數（`site_skipped`）。
- `gate1.py` 的「不加站上轉換表與群組規則」對照仍保留 NoteTA 的數字參數（它們是條目自己寫的規則）。
- `--mw` 的 `build_tune.py` wiki 路徑先還原四個 HTML 實體（和 `build_counts.py --mw` 相同）；沒有 `--mw` 時不還原，和以前一樣。
