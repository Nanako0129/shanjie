# 每版 KPI 表 `tools/kpi.py`（研究工具，不出貨）

狀態：READY（2026-10-18 收尾審查）；plan-verifier 第一次 REVISE（5 項）、第二次 REVISE（1 項）都已照改。

## 0. 起因

- `docs/iteration-plan.md` Phase 0 第 6 項「每版 KPI 列定義並回填 v0.4.0–v0.4.7」與 §6 的 KPI 表；`docs/methodology.md`「2026-10-18：研究流程改版」#7（連續三個確認片 KPI 不動就換方向）。
- 維護者 2026-10-18 定的欄位（`.agent-local` 的「每版 KPI」）：聊天＝discordtune 趨勢、錯字回報真引擎重播、按鍵分數、error ratio、保留集口語半邊；書面＝wikitune-clean、保留集新聞半邊；速度與記憶體只收閒置窗。
- 現在每版的數字散在 `eval/bench/results/v2-*.json`（跨版本基準測試，套件 v2）與研究紀錄，沒有一張逐版的表；wikitune-clean（2026-10-18 才建）與重播工具（PR #210）的欄位以前的版本沒有。

## 1. 要做到什麼

1. `tools/kpi.py`：
   - `run REF... [--as VERSION] --wikitune-clean FILE`（列檔路徑轉成絕對路徑；`eval/holdout/` 底下的檔拒絕）：參數和 `tools/bench.py run --suite v2` 相同（ref 是 tag 或 commit，`--as` 給最後一個還沒打 tag 的 commit 命名，名稱要在 `VERSIONS2` 裡）。匯出、編譯與模型設定直接 import bench.py 的 `Ver`／`build`／`setup_models`；wikitune-clean 這一格用 `cell_cmd` ＋ `cli_env(False)` ＋ `parse_summary` ＋ `expected_tag` ＋ `parse_rowstats` ＋ `truths` 組起來，做和 `run_cell2` 相同的檢查（設定標記、列數、rowstats 的 top1 等於摘要行）。`run_cell2` 本身不能用（它只認 `SETS2` 裡的集合），**bench.py 一行都不改**。跑出貨設定、書面設定，寫 `eval/bench/kpi/<version>.json`：`sha`（量的 commit）、`wikitune_clean`：`n`、`top1`、`top1_sha256`、設定標記、列檔的 `rows_sha256`。檔案已有的欄位保留，只更新這次跑的。
   - `replay --as VERSION`：在**目前的 checkout** 跑（發版時就是要打 tag 的那個 commit；已追蹤的檔有改動就拒絕，未追蹤的結果檔——`bench.py`／`kpi.py run` 剛寫的——不算；`VERSION` 的 tag 已存在時，它指的 commit 必須就是 HEAD），記下 `git rev-parse HEAD`。模型用 `data/lm/` 裡的檔，先照 `VERSIONS2[VERSION]` 列出該版出貨的檔（`resolve_models` 的那一組；出貨口語表的版本在它的列加 `"colloquial": True`，`kpi.py` 的 `check_models` 才把 `colloquial.sjlm` 算進這一組），每個都要和 `data/*.sha256` 相符，而且 `data/lm/` 不能多出不在這組裡的模型檔；`shanjie-replay` 預設載入 `data/lm/colloquial.sjlm`（照 App），所以這組有口語表時不加旗標、設定標記與 `models:` 行都要帶它（`+colloq:<sha8>`、`colloquial.sjlm:<sha8>`），沒有時加 `--no-colloquial` 並要求沒有這個標記；`SHANJIE_VARIANTS` 照 `cli_env(False)` 設；跑完再用 `shanjie-replay` 在 stderr 印的 `models:` 那行核對雜湊，不符就失敗。列檔固定：錯字回報用 `eval/bench/suite-v2/user-reported.txt`（套件 v2 凍結的 80 列），dev302 用 `eval/bench/suite-v1/dev302.txt`。跑錯字回報（聊天，`--correct`，學習關與開各一次）與 dev302（聊天，`--correct`，學習關），把摘要行的數字、`evalstats compare` 的改對／改壞，以及每個列檔的 `rows_sha256` 與 `n` 寫進同一個 JSON 的 `replay` 欄。含 `shanjie-replay` 之前的版本不回填（舊引擎沒有這個工具，用新工具重播舊模型不等於舊版的行為）。
   - `table`：讀 `eval/bench/results/v2-*.json` 與 `eval/bench/kpi/*.json`，重產 `docs/kpi.md`，不手改。
2. 單元測試 `tools/test_kpi.py`：合成的小 JSON，每條規則一個會失敗的突變（§4）。
3. 發版流程（`docs/verification.md`「跨版本基準測試」一節）加上：跑完套件 v2 之後，`kpi.py run <要打 tag 的 commit> --as <新版> --wikitune-clean FILE`、在那個 commit 的 checkout 上 `kpi.py replay --as <新版>`、`kpi.py table`，結果和基準測試同一個 PR 進 repo。

## 2. 欄位與來源（`docs/kpi.md` 每個發版一列，最新在上）

| 欄 | 來源 | 舊版回填 |
|---|---|---|
| discordtune 聊天 top1（%） | 那一版當「新版」的那個 `v2-*.json`（只出現在較舊一側的 v0.4.0 用它唯一的檔）的 `accuracy["discordtune/chat"]` | 有 |
| error ratio（聊天） | 同一個檔的 `vs_prev["discordtune/chat"]`：改壞 ÷ 改對（新造的錯 ÷ 修好的舊錯，對上一版）；改對為 0 或沒有上一版時寫「—」 | 有（v0.4.0 為「—」） |
| 錯字回報真引擎重播 | `kpi/<v>.json` 的 replay：學習關的第一次顯示 top1，與學習開對學習關的改壞數 | 從含重播工具的版本起 |
| 按鍵分數（KySS） | `kpi/<v>.json` 的 replay：dev302 聊天的 KySS 與錯字回報聊天的 KySS | 從含重播工具的版本起 |
| 顯示後被改掉 | `kpi/<v>.json` 的 replay：dev302 聊天的 `rewrites` ÷ 列數 | 從含重播工具的版本起 |
| 保留集口語半邊／新聞半邊 | 發版前 fresh verifier 照保留集規則量（§3） | 不回填（回填要再用一次保留集） |
| wikitune-clean 書面 top1（%） | `kpi/<v>.json` | 有（`kpi.py run`） |
| lytune 書面 top1（%，只當警示） | 同 discordtune 那個檔的 `accuracy["lytune/formal"]` | 有 |
| 速度與記憶體 | 同一個檔的 `speed`（按鍵 p95、重排 p95、RSS）；只有 `env.load_avg_start` 與 `load_avg_end` 都 ≤ 2.0 時才填數字，否則寫「—（負載 x.xx）」；不同機器（`env.machine`）的數字分欄 | 照規則判（現有的檔都在負載下量，預期全是「—」） |

- 表頭寫明每一欄的集合大小與雜湊（v2 JSON 的 `sets`，以及 `kpi/<v>.json` 記的 `rows_sha256`），換集合時欄位分開，不同集合的數字不放在同一欄；兩種來源都適用。
- 表下方列出每一列的來源檔名。

## 3. 保留集兩個半邊（2026-10-29 修訂：新聞半邊改私人檔，`docs/contracts/news-corpus.md` §2.4）

- 保留集規則不變：只在推 `v*` tag 前由 fresh verifier 跑一次。回報改成：
  - **口語半邊**＝`--set holdout` 全集（`eval/holdout/` 只剩 `talk.txt`，n 150），聊天設定，存在 v2 JSON 的 `holdout.chat` 與 kpi JSON 的 `holdout_talk_chat`；全集合那兩行不再存在，不跨份比；
  - **新聞半邊**＝私人檔（`--rows <holdout-news-1.txt> --profile formal --rowstats`，書面設定），存在 v2 JSON 的 `holdout.formal` 與 kpi JSON 的 `holdout_news_formal`；
  - 欄位：`n`、`top1`、`fixed`、`broken`、`p`，另加那個檔案的 `rows_sha256`；`tools/kpi.py` 與 `tools/bench.py` 的表都依它分欄，換份時不同份的數字不會在同一欄（`tools/test_kpi.py` 的 rows_sha256 分欄測試已涵蓋私人檔，`tools/test_bench.py` 有 bench 表的對應測試）；
  - 量新聞半邊的 verifier，brief 明寫禁止 `--dump`、`--dump-words`、`--tinylm-scores`，只准 `--rowstats`（流程規則，不是技術防護）；
  - 同一次發版前量的這兩行算一次「使用」。
- `docs/verification.md` 與 `eval/README.md` 的保留集段落跟著改；`kpi.py table` 從 `kpi/<v>.json` 的這兩個欄位讀兩個半邊的 top1。

## 4. 驗收

1. 單元測試：
   - 同一版出現在兩個 `v2-*.json` 時，取它當新版的那個檔（突變成取另一個就失敗）；
   - error ratio：改對 0 寫「—」，否則改壞 ÷ 改對（突變成反過來就失敗）；
   - 速度：負載 > 2.0 時不填數字；
   - 舊版沒有 replay 欄位時寫「—」，不報錯；
   - 換集合時表頭分欄：v2 JSON 的 `sets` 雜湊不同，以及兩個 `kpi/<v>.json` 的 replay 或 wikitune-clean `rows_sha256` 不同，兩種都要測；
   - 保留集兩個半邊從合成的 `kpi/<v>.json`（`holdout_talk_chat`、`holdout_news_formal`）畫進表；
   - `replay` 遇到 `data/lm/` 的模型檔雜湊不符或多一個檔時失敗；
   - `table` 兩次輸出逐位元組相同。
2. `kpi.py run v0.4.0 … v0.4.7` 在 Windows 機器或 GCP 跑完（不在 Mac），8 個 JSON 進 repo；`kpi.py table` 產生的 `docs/kpi.md` 進 repo。抽一版用 CLI 手動跑同一個指令，top1 與 sha256 相同。
   - `kpi.py replay` 的成功路徑也真跑一次：在 Windows 機器或 GCP 對目前的 main（乾淨的 checkout）跑 `kpi.py replay --as <任一名稱>`，輸出寫到暫存、不進 repo；同一台機器直接執行同樣的 `shanjie-replay` 指令與 `evalstats compare`，`replay` 欄的每一個數字（學習關的 top1、學習開對學習關的改壞數、兩個 KySS、`rewrites`、`rows_sha256`、`n`）都和直接執行的相同。結果寫進研究紀錄。
3. 研究紀錄記下回填結果與「從下一版起」的欄位。
4. `/code-review` ＋ fresh verifier ＋ CI（程式改動的關卡）。

## 5. 範圍外

- 不改 `tools/bench.py` 的套件 v2 與既有結果檔；不把 wikitune-clean 加進套件 v2。
- 不回填保留集半邊、不重量舊版的速度。
- 使用者感受那一列（計數器）等 App 計數器做好再加。
- 2026-10 以後的錯字回報分欄（研究顧問的建議）：錯字回報檔沒有日期欄，另案。

## 6. 停止條件

- §1.1 列的那些 bench.py 函式沒辦法在不改 bench.py 的情況下組起來 → 回報，決定是改 bench.py 還是另寫。
- 某一版 wikitune-clean 的 CLI 設定標記和 `VERSIONS2` 的不符。

## 7. 負責人

- 契約、執行（Windows 機器／GCP）、研究紀錄：main。
- 實作與單元測試：executor（`../shanjie-kpi`，分支 `feat/kpi`）。

## 8. 審查紀錄

- plan-verifier 第一次 REVISE 5 項，全照改：重用 bench.py 的低階函式而不是 `run_cell2`（§1.1）；`run` 收 `REF --as VERSION`、JSON 記 `sha`（§1.1、§1.3）；`replay` 在目前的 checkout 跑，寫清楚模型、異體字表與雜湊核對（§1.1）；列檔固定並記 `rows_sha256`、表頭分欄也看 kpi JSON（§1.1、§2、§4）；保留集兩個半邊的設定、列號範圍與存放欄位（§3）。
- plan-verifier 第二次 REVISE 1 項（`replay` 的成功路徑沒有驗收會跑到），照改：§4.2 加一次真跑，逐欄對照直接執行的結果。
- `/code-review` 第一輪（PR #212）15 項：修 13 項（replay 的乾淨檢查只看已追蹤的檔、`--as` 對 tag 核對 HEAD、保留集半邊加 `rows_sha256` 分欄、速度依機器分欄、列檔絕對路徑與拒絕保留集路徑、JSON 原子寫入、改對／改壞從 rowstats 算、`cargo build --locked` 取實際的執行檔路徑、缺雜湊檔正常報錯、負載兩位小數、「顯示後被改掉」寫成每百列次數、三處保留集指令一致用 `--net`、說明文字和實際檢查一致）；延後 2 項：保留集半邊的切檔與寫入做成子指令（現在照文件手動）、假的 `shanjie-replay` 用同一套範本（格式改變只有 §4.2 的真跑抓得到）。
