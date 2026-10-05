# S2n 契約：清掉詞庫與語料裡的簡體殘留

使用者 2026-10-05 準備 demo 時，書面設定把「大概十分鐘後到」打成「大概十分鐘**后**到」，並說「這個字剛好是簡體字的後」，要修正。

## 1. 原因（2026-10-05 量測）

兩個來源：

- **語言模型語料**（`experiments/s2/build_counts.py`）：維基百科是簡繁混合，轉換時只轉「簡體專用字」，本身也是正確繁體字的字（后、于、里…）一律不轉。這是 2026-10-03 為了不把正確的「皇后」轉成「皇後」而改的。結果簡體文章裡的「之后、由于、哪里」原樣進了語料：

  | 寫法 | 加權次數 | 正確寫法 | 加權次數 |
  |---|---|---|---|
  | 后（單獨成詞） | 150,151 | 後 | 277,322 |
  | 于（單獨成詞） | 189,250 | 於 | — |
  | 里（單獨成詞） | 106,518 | 裡 | — |
  | 之后 | 32,642 | 之後 | 60,152 |
  | 由于 | 43,613 | 由於 | 74,442 |
  | 最后 | 19,671 | 最後 | 57,282 |

  「于」「里」單獨成詞的次數裡有多少是殘留、多少是正確用法（姓于、公里的里…），還沒量。

- **詞庫疊加層**（`tools/build_overlay.py`，S1）：從維基詞典條目補詞，收了「之后、最后、然后、由于、哪里、此后、以后、后期」等簡體寫法（`data/lexicon/overlay-add.tsv`，來源欄 `wikt`）。解碼器因此能直接選到它們。

## 2. 做法

1. **語料：先判斷簡繁再轉換**。在 `build_counts.py` 的轉換前，以句子為單位判斷：
   - **簡體句**＝含至少一個簡體專用字（`STCharacters` 中第一個對照不是自己的字），而且**不含任何繁體專用字**（出現在 `STCharacters` 對照值裡、本身不是任何鍵的字，例如 後、於、裡、這、們）。兩種都有的句子當成繁體句，維持現行做法（只轉簡體專用字），避免把夾了一個簡體字的繁體句整句轉錯。
   - 簡體句的轉換順序：(a) `STPhrases` 全部詞組，最長匹配優先；(b) 沒被詞組蓋到的字用 `STCharacters` 的第一個對照；(c) 對整句的結果（含詞組輸出）再套一遍台灣用字對照：現有的 `VARIANTS`（裏→裡、爲→為…），加上 臺→台（台灣常用寫法）；(d) 照舊套 `TWPhrases`。
   - 有多個對照、本身也是正確繁體的字，結果如下（由 (a)–(c) 決定；表中是沒有詞組蓋到時的結果）：后→後、于→於、里→裡（經 裏→裡）、台→台、干→幹、余→餘、只→只、系→系、发→發、面→面。詞組優先，所以 皇后、干涉、只有、系統 這類 `STPhrases` 已列的詞照詞組。
   - 單元檢查（固定字串，寫成可執行的測試或自檢）：簡體句中的 之后／由于／哪里／最后／于是／台湾 分別得到 之後／由於／哪裡／最後／於是／台灣；繁體句「皇后說他在里長辦公室」不變；夾一個簡體專用字的繁體句只改那個字。
2. **疊加層：拿掉簡體寫法**。`build_overlay.py` 產生疊加層時，一個詞條只有在以下三點都成立時才拿掉：(i) 用第 1 點的簡體句轉換會變成另一個寫法；(ii) 轉換後的寫法是基底詞庫（`mcbpmf-data.txt`）或疊加層裡已有的詞；(iii) 這個詞條本身不在基底詞庫、也不是萌典詞目。所以「之后」（之後已在詞庫）會拿掉，「台灣」「里長」「干涉」這類本身就在詞庫或萌典的詞保留。同一個 PR 重新產生 `overlay-add.tsv`，並把**完整的被移除清單**存成 `experiments/s2n/overlay-removed.tsv`（詞、轉換後的寫法、讀音）。
3. **重算兩份計數、重建語言模型**。`tools/build_lm.py` 的 `CORPORA` 用兩份計數：維基 `counts-200000.pkl`（×1）與口語 `counts-colloquial3.pkl`（×5）。口語計數裡的 Tatoeba 例句也走同一個 `convert`，所以兩份都要重建，順序：
   1. 疊加層（第 2 點，`tools/build_overlay.py`）；
   2. `experiments/s2/build_tune.py`：重產 `colloquial-train.txt`、cvtune、wikitune（參考句走新的轉換）；
   3. `experiments/s2/build_counts_text.py counts-colloquial3.pkl <輸入檔…>`：口語計數；
   4. `experiments/s2/build_counts.py`（維基 20 萬篇）：維基計數；
   5. `tools/build_lm.py`（參數不變）。
   - **先確認口語計數的輸入**：repo 沒有記錄 `counts-colloquial3.pkl` 用了哪些檔案。實作前用**現行的**轉換、以候選輸入（`colloquial-train.txt`、`synth.txt`、`synth-targeted.txt.ok` 的組合）重建一次，和現有的 `counts-colloquial3.pkl` 逐項比對；找到完全相同的組合才算確認，寫進 `experiments/s2/README.md`。找不到完全相同的組合是停止條件。
   - 第 3 節的殘留量、正確寫法、總句段數與總詞數，兩份計數分開報，再報加權後的總和。
4. 新的模型檔是 `model-v2`：SHA-256 更新到 `data/bigram.sjlm.sha256`，所有 `model-v1` 的引用改成 `model-v2`（至少：`.github/workflows/ci.yml`、`release.yml`、`cli/tests/golden.rs`、`core/tests/engine_lm.rs`、`scripts/build-app.sh`、`macos/Tests/ShanjieKitTests/Support.swift`、`CONTRIBUTING.md`、`docs/verification.md`、`LICENSES/data.md`、`docs/PLAN.md`；`grep -rn model-v1` 只剩歷史紀錄）。
5. **順序**：(a) 本機建好、所有測試與量測都對本機的新模型通過；(b) **問使用者**是否建立 GitHub Release `model-v2`（`bigram.sjlm` 與授權檔，雜湊等於 commit 裡的 `.sha256`）——對外動作，使用者當下同意才做；(c) Release 建好、下載回來比對雜湊相符後，才推分支、開 PR，讓 CI 下載 `model-v2`。`model-v1` 永遠不刪、不覆蓋。
6. **回滾**：revert 這片的 commit，`.sha256`、`model-v1` 的引用、疊加層與 golden 檔一起回到原狀；`model-v1` 仍可下載。

## 3. 驗收

- **殘留量**：重建後語料裡「后、于」單獨成詞與「之后、由于、最后、然后、以后、此后、哪里」的次數，報改前改後；這些殘留詞要降到原本的 5% 以下。
- **轉到正確的寫法**：對應的正確寫法（之後、由於、最後、然後、以後、此後、哪裡、於、後）增加的次數，至少是殘留減少量的 80%。一個故意弄壞的轉換（例如 里→裏 之後不再換成 裡）要讓這一條不過。
- **沒有丟資料**：報改前改後的總句段數與總詞數；下降超過 1% 是停止條件（代表轉換後的文字斷不了詞被丟掉）。
- **正確用法保留**：皇后、王后、太后、公里、里長、鄰里、台灣、干涉、只有、系統 的次數報改前改後，任一個下降超過 20% 要說明原因，無法說明就是停止條件。
- **疊加層**：`experiments/s2n/overlay-removed.tsv` 是完整的被移除清單；被移除的詞與基底詞庫、萌典詞目的交集是空的；「台灣」「里長」類的詞條仍在 `overlay-add.tsv`。
- **使用者的句子**：「大概十分鐘後到」兩種設定都對；`eval/dev/user-reported.txt` 的結果照列。
- **評測**：dev302、打字測驗、錯字回報、cvtune、wikitune（重建後的參考句），聊天與書面兩種設定，改前改後配對比較（修好／弄壞／McNemar p）。dev302 與打字測驗不得顯著退步（p < 0.05 的退步是停止條件）。
- **golden 檔與測試**：
  - **必須逐位元組不變**：`eval/golden/unigram.txt`、`eval/golden/s1-dev302-nooverlay.txt`（不用疊加層、不用語言模型）。
  - **可以變、照現有指令重產**：`eval/golden/s1-dev302.txt`、`s1-overlay-sets.txt`、`s2r-probe-*`、`s2-lm.txt`、`s2-lm-dev302-top1.tsv`，以及 `data/lexicon/sandhi-add.tsv`（`build_sandhi.py` 會和疊加層去重；重產，或說明它沒變）。每一個變動的 golden 列都要能追到一個被移除的疊加層詞或模型的改變。
  - `core/tests/c/abi_smoke.c` 選的第 10 列要求 unigram、聊天、書面三者輸出不同；改了之後不成立就重選一列，找不到這樣的列就是停止條件。
  - S2c 的引擎重播規則照舊（302／302，標準排列的聊天走正式路徑）。
  - `git diff --stat eval/golden data/lexicon core/tests/c` 只出現上面列出的檔案；`cargo test`、C 冒煙測試、`swift test` 對本機的 model-v2 全綠。不得為了讓測試過而改核心程式。
- **保留集**：這一片收尾時由 fresh verifier 跑一次，只回數字。
- **demo 用的 14 個子句**（main 準備的散文、聊天、日常、打屁例句，不在 repo）：main 報改前改後的輸出。

## 4. 停止條件與預算

- 「皇后」類正確用法被大量轉錯、dev302 或打字測驗顯著退步、找不到口語計數的原始輸入、重建語料超過 3 小時，都停下來回報。
- 計數重建很吃 CPU：用 `nice -n 19`、單一程序；使用者在工作。
- 預算：實作加量測最多 2 輪。

## 5. 範圍外

- 詞類實驗（`exp/word-classes`）在乾淨語料上重跑：下一片，用這一片產生的計數。
- 其他簡繁或異體問題（裡／裏 已由 VARIANTS 處理）。
- 模型檔以外的發佈步驟（app 版本、Release notes）。
