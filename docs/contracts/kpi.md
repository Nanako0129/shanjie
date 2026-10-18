# 每版 KPI 表 `tools/kpi.py`（研究工具，不出貨）

狀態：草稿（2026-10-18）；plan-verifier 第一次 REVISE（5 項）已照改，送第二次審查。

## 0. 起因

- `docs/iteration-plan.md` Phase 0 第 6 項「每版 KPI 列定義並回填 v0.4.0–v0.4.7」與 §6 的 KPI 表；`docs/methodology.md`「2026-10-18：研究流程改版」#7（連續三個確認片 KPI 不動就換方向）。
- 維護者 2026-10-18 定的欄位（`.agent-local` 的「每版 KPI」）：聊天＝discordtune 趨勢、錯字回報真引擎重播、按鍵分數、error ratio、保留集口語半邊；書面＝wikitune-clean、保留集新聞半邊；速度與記憶體只收閒置窗。
- 現在每版的數字散在 `eval/bench/results/v2-*.json`（跨版本基準測試，套件 v2）與研究紀錄，沒有一張逐版的表；wikitune-clean（2026-10-18 才建）與重播工具（PR #210）的欄位以前的版本沒有。

## 1. 要做到什麼

1. `tools/kpi.py`：
   - `run REF... [--as VERSION] --wikitune-clean FILE`：參數和 `tools/bench.py run --suite v2` 相同（ref 是 tag 或 commit，`--as` 給最後一個還沒打 tag 的 commit 命名，名稱要在 `VERSIONS2` 裡）。匯出、編譯與模型設定直接 import bench.py 的 `Ver`／`build`／`setup_models`；wikitune-clean 這一格用 `cell_cmd` ＋ `cli_env(False)` ＋ `parse_summary` ＋ `expected_tag` ＋ `parse_rowstats` ＋ `truths` 組起來，做和 `run_cell2` 相同的檢查（設定標記、列數、rowstats 的 top1 等於摘要行）。`run_cell2` 本身不能用（它只認 `SETS2` 裡的集合），**bench.py 一行都不改**。跑出貨設定、書面設定，寫 `eval/bench/kpi/<version>.json`：`sha`（量的 commit）、`wikitune_clean`：`n`、`top1`、`top1_sha256`、設定標記、列檔的 `rows_sha256`。檔案已有的欄位保留，只更新這次跑的。
   - `replay --as VERSION`：在**目前的 checkout** 跑（發版時就是要打 tag 的那個 commit；工作區不乾淨就拒絕），記下 `git rev-parse HEAD`。模型用 `data/lm/` 裡的檔，先照 `VERSIONS2[VERSION]` 列出該版出貨的檔（`resolve_models` 的那一組），每個都要和 `data/*.sha256` 相符，而且 `data/lm/` 不能多出不在這組裡的模型檔；`SHANJIE_VARIANTS` 照 `cli_env(False)` 設；跑完再用 `shanjie-replay` 在 stderr 印的 `models:` 那行核對雜湊，不符就失敗。列檔固定：錯字回報用 `eval/bench/suite-v2/user-reported.txt`（套件 v2 凍結的 80 列），dev302 用 `eval/bench/suite-v1/dev302.txt`。跑錯字回報（聊天，`--correct`，學習關與開各一次）與 dev302（聊天，`--correct`，學習關），把摘要行的數字、`evalstats compare` 的改對／改壞，以及每個列檔的 `rows_sha256` 與 `n` 寫進同一個 JSON 的 `replay` 欄。含 `shanjie-replay` 之前的版本不回填（舊引擎沒有這個工具，用新工具重播舊模型不等於舊版的行為）。
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
| 速度與記憶體 | 同一個檔的 `speed`（按鍵 p95、重排 p95、RSS）；只有 `env.load_avg_start` 與 `load_avg_end` 都 ≤ 2.0 時才填數字，否則寫「—（負載 x.x）」 | 照規則判（現有的檔都在負載下量，預期全是「—」） |

- 表頭寫明每一欄的集合大小與雜湊（v2 JSON 的 `sets`，以及 `kpi/<v>.json` 記的 `rows_sha256`），換集合時欄位分開，不同集合的數字不放在同一欄；兩種來源都適用。
- 表下方列出每一列的來源檔名。

## 3. 保留集兩個半邊（從下一個發版起）

- 保留集規則不變：只在推 `v*` tag 前由 fresh verifier 跑一次，基準與新版各跑 `--set holdout --rowstats`（聊天、書面各一次）。改的只是回報：
  - 全集合照舊每種設定一行（聊天、書面），存在 v2 JSON 的 `holdout.chat`／`holdout.formal`；
  - 另加兩行：口語半邊用**聊天**設定、新聞半邊用**書面**設定。`--set holdout` 依檔名排序讀，`news.txt` 是第 1–150 列、`talk.txt` 是第 151–300 列；基準與新版的 rowstats 用同樣的方式切出那一段、列號從 1 重編，再跑 `evalstats compare`；
  - 兩個半邊存在 `eval/bench/kpi/<version>.json` 的 `holdout_talk_chat`、`holdout_news_formal`（欄位同 v2 JSON 的 holdout：`n`、`top1`、`fixed`、`broken`、`p`）；
  - 同一次發版前量的這四行算一次「使用」。
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
