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

## 修訂二：疊在 S2f 上重建的模型 W2（2026-10-08，不出貨）

契約 §8。`mwconv.convert()` 在 zhconv-rs 之後改套 S2f 繁體句的最後一層（`PROTECT_TRAD` 保留、其餘套 `POST_TRAD`），其餘程式照 main（含 S2f 的異體合併與疊加層）。188 重建約 11 分鐘（wiki 計數到 build_lm）。

- **輸入**：`mwdata.json` 沿用 W 那份（188 的 `work/s2w`，SHA-256 `5c1efb26…780c`；Mac 上較舊的一份是 `8ef73637…`，W 用的是 188 那份）；口語計數用 model-v3 的 `counts-colloquial3.pkl`（`13d0ab5b…97ef`，複製前後相同）；疊加層和 main 相同。
- **模型 W2**：SHA-256 `66da13278b2b8b1591a8dabfebae1b9f1192cba7bd3d9256f03bd8d520342ada`，81,258,500 bytes，V=332,341、前文 253,717、條目 8,911,969。
- **cvtune-native-v3**（`cv_native_v3.py`）：S2f 的 2,117 列再加「MW 轉換也不改」→ 2,103 列（`3a5251de…2695`）；用 W 與 Mac 兩份 mwdata 算出來相同。
- **對 model-v3 的驗收**（`--context`，McNemar）：

  | 集合 | 聊天 | 書面 |
  |---|---|---|
  | dev302 | 235 → 236（1／0） | 240 → 240 |
  | typing76 | 66 → 66 | 65 → 66（1／0） |
  | cvtune-native-v3（否決用） | 1,823 → 1,826（4／1，p = 0.38） | 1,828 → 1,831（6／3，p = 0.51） |
  | 錯字回報（39 列，含 2026-10-08 加入的五列，只報告） | 11 → 12（1／0） | 13 → 13（1／1：書面修好「執行緒被」、弄壞「的市佔這麼低了嗎」） |
  | model-v3 的 wikitune（`8dcfe40c…`，只報告） | 2,031 → 2,022（11／20，p = 0.15） | 2,080 → 2,077（16／19，p = 0.74） |
  | wikitune-mw（只報告） | 2,093 → 2,110（26／9，p = 0.006） | 2,148 → 2,169（30／9，p = 0.001） |

  沒有否決。修好的列：決勝的一「戰」、「手錶」（model-v3 書面是「手表」）、「執行緒被」排進。
- S2f 的檢查都過：`check_f2.py` PASS、字形探針 16/16、同分探針「世界線」、竈門、「大概十分鐘後到」、「好吧」。單元檢查 14 項與三個突變見 commit。
- **次數**（188，model-v3 → W2，加權；`char_counts.py`）：段落 −0.55%（維基 −0.59%）；七個殘留詞 0；「喫着爲説裏」每百萬 0。字對：床／牀 13,879／0 → 13,588／0；秘／祕 32,184／9,118 → 38,290／2,596；灶／竈 1,967／22 → 1,963／22；粽／糉 698／0 → 695／0；庄／莊 11,925／37,801 → 11,702／36,612；痴／癡 0／2,176 → 1,536／662；占／佔 17,343／77,311 → 61,129／33,606；布／佈 195,828／80,949 → 254,000／36,853。
- **golden 與測試**：本機暫時換成 W2，依賴模型的 golden 只有 dev302 兩列（決勝的一戰、螺絲）與探針一列改變；SW 降權的條件仍成立；`make test`、debug `cargo test`（170 通過）、C 冒煙測試全過，之後還原。
- **保留集**（fresh verifier，n = 227）：聊天 178 → 176（1／3，p = 0.63）、聊天＋前文 177 → 175（1／3）、書面 184 → 185（2／1）、書面＋前文 183 → 184（2／1）。
- **使用者回報的句子**：「的市佔這麼低了嗎」（前文「現在Windows」，錯字回報第 35 列）在 W2 兩種設定都是「的士站這麼低了嗎」，model-v3 書面是對的（上表書面的弄壞 1 就是這列）。
- **決定（使用者 2026-10-08）**：v0.3.0 維持 model-v3，W2 不出貨。工具（`mwconv` 的新字形層、`cv_native_v3.py`）與結果保留。
