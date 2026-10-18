# 每版 KPI 表 `tools/kpi.py`（研究工具，不出貨）

狀態：草稿（2026-10-18），送 plan-verifier。

## 0. 起因

- `docs/iteration-plan.md` Phase 0 第 6 項「每版 KPI 列定義並回填 v0.4.0–v0.4.7」與 §6 的 KPI 表；`docs/methodology.md`「2026-10-18：研究流程改版」#7（連續三個確認片 KPI 不動就換方向）。
- 維護者 2026-10-18 定的欄位（`.agent-local` 的「每版 KPI」）：聊天＝discordtune 趨勢、錯字回報真引擎重播、按鍵分數、error ratio、保留集口語半邊；書面＝wikitune-clean、保留集新聞半邊；速度與記憶體只收閒置窗。
- 現在每版的數字散在 `eval/bench/results/v2-*.json`（跨版本基準測試，套件 v2）與研究紀錄，沒有一張逐版的表；wikitune-clean（2026-10-18 才建）與重播工具（PR #210）的欄位以前的版本沒有。

## 1. 要做到什麼

1. `tools/kpi.py`：
   - `run VERSION... --wikitune-clean FILE`：對 `tools/bench.py` 的 `VERSIONS2` 裡列出的版本，用 bench.py 既有的匯出、編譯與模型設定（`setup_models`、`cell_cmd`、`run_cell2` 那一套，import 進來用，不複製）跑出貨設定、書面設定的 wikitune-clean，寫 `eval/bench/kpi/<version>.json`（`n`、`top1`、`top1_sha256`、設定標記、列檔的 sha256）。檔案已有的欄位保留，只更新這次跑的。
   - `replay VERSION --rows-reported FILE --dev302 FILE`：只對含 `shanjie-replay` 的版本（PR #210 之後）有效：用那個版本的 `shanjie-replay` 跑錯字回報（聊天，`--correct`，學習關與開各一次）與 dev302（聊天，`--correct`，學習關），把摘要行的數字與 `evalstats compare` 的改對／改壞寫進同一個 JSON。比這更舊的版本不回填（舊引擎沒有這個工具，用新工具重播舊模型不等於舊版的行為）。
   - `table`：讀 `eval/bench/results/v2-*.json` 與 `eval/bench/kpi/*.json`，重產 `docs/kpi.md`，不手改。
2. 單元測試 `tools/test_kpi.py`：合成的小 JSON，每條規則一個會失敗的突變（§4）。
3. 發版流程（`docs/verification.md`「跨版本基準測試」一節）加上：跑完套件 v2 之後，`kpi.py run` 新版、`kpi.py replay` 新版、`kpi.py table`，結果和基準測試同一個 PR 進 repo。

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

- 表頭寫明每一欄的集合大小與雜湊（從 JSON 的 `sets`），換集合時欄位分開，不同集合的數字不放在同一欄。
- 表下方列出每一列的來源檔名。

## 3. 保留集兩個半邊（從下一個發版起）

- 保留集規則不變：只在推 `v*` tag 前由 fresh verifier 跑一次。改的只是回報：除了全集合那一行 `evalstats compare`，另對 `talk.txt`（口語半邊）與 `news.txt`（新聞半邊）各回一行（同一份 rowstats 依列號切開，或各跑一次 `--rows`，由 verifier 照 `docs/verification.md` 的指令）。同一次發版前量的三行算一次「使用」。
- `docs/verification.md` 與 `eval/README.md` 的保留集段落跟著改；`kpi.py table` 從那一版的 JSON 讀兩個半邊的 top1。

## 4. 驗收

1. 單元測試：
   - 同一版出現在兩個 `v2-*.json` 時，取它當新版的那個檔（突變成取另一個就失敗）；
   - error ratio：改對 0 寫「—」，否則改壞 ÷ 改對（突變成反過來就失敗）；
   - 速度：負載 > 2.0 時不填數字；
   - 舊版沒有 replay 欄位時寫「—」，不報錯；
   - 換集合（sets 的雜湊不同）時表頭分欄；
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

- bench.py 的版本匯出與模型設定沒辦法在不改它的情況下重用（要改 bench.py 才能跑）→ 回報，決定是改 bench.py 還是另寫。
- 某一版 wikitune-clean 的 CLI 設定標記和 `VERSIONS2` 的不符。

## 7. 負責人

- 契約、執行（Windows 機器／GCP）、研究紀錄：main。
- 實作與單元測試：executor（`../shanjie-kpi`，分支 `feat/kpi`）。
