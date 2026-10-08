# 評測統計：配對比較、字元錯誤率、信賴區間（2026-10-08）

## 0. 起因與目標

S2k 的保留集只回總數（聊天 180、書面 184，對第一段的 179、183），看不出是「改對 1 句」還是「改對 3 句、改壞 2 句」。另外，227 句上差 1 句遠在雜訊內：配對的精確符號檢定要改對對改壞差到約 6 比 0 或 9 比 1，p 才小於 0.05。dev302（302）、typing76（76）、錯字回報（40 列）、Discord 調參集（私有）也有同樣的問題。

使用者決定（2026-10-08）：保留集與 dev302、typing76、錯字回報、Discord 調參集都改成下面的報法，從下一片開始用；這次 S2k 的結果不重跑。

**目標**：任何「改到選字結果」的改動，每個集合在聊天與書面兩種設定都報同一張表：

| 欄 | 意思 |
|---|---|
| n | 列數 |
| top1 基準／新 | 第一名正確列數（寬鬆對照，和現在相同） |
| 改對／改壞 | 基準錯、新對的列數／基準對、新錯的列數 |
| p | 精確 McNemar（只看改對＋改壞的列，二項分布 1/2 的雙尾機率；Dietterich 1998） |
| CER 基準／新 | 字元錯誤率：各列字元錯誤數總和 ÷ 正解字數總和 |
| Δtop1 95% 區間 | 配對 bootstrap（Koehn 2004）：每次對列有放回抽 n 列，算「新 − 基準」的第一名列數差，取 2.5% 與 97.5% 分位 |
| ΔCER 95% 區間 | 同一批抽樣算 CER 差 |

## 1. 逐列統計檔 `rowstats`（不含任何文字）

- 格式：每列一行 `列號\t對錯\t錯字數\t正解字數`，列號從 1 開始依評測檔順序，對錯是 `0` 或 `1`，後兩欄是非負整數。檔案裡只有數字、tab、換行。
- 對錯：和現在的 top1 相同（寬鬆對照）。
- 錯字數：對的列是 0；錯的列是第一名 surface 與正解句子的 Levenshtein 距離，以 Unicode 純量值為單位。正解字數是正解句子的 Unicode 純量值個數。
- 產生：
  - Rust CLI：`shanjie-eval lm … --rowstats FILE`，可以和 `--dev`、`--rows`、`--set holdout` 一起用（`--dump` 對保留集仍然禁止）。
  - Python：`reference/proto/lm_eval.py … --rowstats FILE`，同樣的位元組。
  - 摘要行不變。

## 2. 比較工具 `tools/evalstats.py`

- `python3 tools/evalstats.py compare BASE CAND [--label 名稱] [--seed S] [--resamples N]`：讀兩個 rowstats，列數不同或列號不對齊就報錯結束；印出 §0 的一行表格（Markdown）。
- 預設 `--seed 20261008`、`--resamples 10000`；同樣的輸入與參數，輸出逐位元組相同。
- 只用 Python 標準函式庫。
- 函式（`mcnemar_exact`、`levenshtein`、`paired_bootstrap`）給 `tools/bench.py` 與其他工具 import，取代 `bench.py` 裡自己的 `mcnemar`。

## 3. 用在哪裡

- **一般改動**：PR 與研究紀錄裡，dev302、typing76、錯字回報在聊天與書面各一行（基準＝main）；調參數時 cvtune、wikitune 也照這個報。repo 根目錄 `CLAUDE.md`「評測與資料」那一條改成這個報法。
- **Discord 調參集**（私有）：main 在本機跑，只貼表格的數字。
- **`tools/bench.py`**：跨版本比較的每一格，除了現在的改對／改壞與 p，加上兩個版本的 CER 與 Δtop1、ΔCER 的 95% 區間；`docs/benchmark.md` 的表格跟著改。CER 從現有的 dump 算，不必重跑。
- **保留集**：片結束時的 fresh verifier 對基準與新版各跑一次 `--set holdout --rowstats`（寫到它自己的暫存目錄），再跑 `evalstats.py compare`，只回報那一行表格，然後刪掉兩個 rowstats。基準由 main 在 brief 裡指定（通常是 main 的 HEAD）。`docs/methodology.md` 與 `eval/README.md` 的保留集規則跟著改。

## 4. 驗收

1. `tools/test_evalstats.py`（`python3 -m unittest tools.test_evalstats`）：
   - `mcnemar_exact`：(6, 0) → 0.03125、(9, 1) → 0.021484375、(0, 0) → 1.0、(3, 3) → 1.0，和 `bench.py` 原本的函式在 0 ≤ b、c ≤ 12 全部相同；
   - `levenshtein`：空字串、相同、一個替換、一插一刪、組合字元以外的 BMP 外字元（例如 𠮷）各算對；
   - `paired_bootstrap`：固定 seed 兩次結果相同；兩個 rowstats 相同時區間是 [0, 0]；區間包含點估計；
   - `compare`：列數不同、列號不對齊、欄位不是數字時報錯。
2. rowstats 一致：dev302 聊天與書面，`lm_eval.py --rowstats` 與 CLI `--rowstats` 逐位元組相同（`cli/tests/golden.rs` 新增一項，比對 Python 事先產生、放進 `eval/golden/` 的檔）；對錯欄加總等於摘要行的 top1。
3. 保留集的檔沒有文字：
   - rowstats 只由一個寫出函式產生，`--set holdout` 與其他集合共用；CLI 測試對 dev302 的 rowstats 檢查只含 `[0-9\t\n]`。
   - CLI 的參數檢查接受 `--set holdout --rowstats FILE`、仍拒絕 `--set holdout --dump FILE`（測試只檢查參數，不讀保留集；repo 現在沒有讀保留集的自動測試，這一片也不加）。
   - 片結束的 fresh verifier 實際跑保留集時，對兩個 rowstats 再檢查一次只含 `[0-9\t\n]`。
4. `bench.py`：`table` 重產後，`docs/benchmark.md` 每一格有 CER 與兩個區間；舊的 results JSON 沒有 CER 時顯示「—」，不報錯。
5. 突變（每一項要讓指名的測試以斷言失敗）：McNemar 改成單尾；Levenshtein 的替換成本改成 2；bootstrap 不用 seed；CLI 的錯字數在對的列不歸零；rowstats 的列號從 0 開始。
6. `cargo test`（debug、release）、`swift test`、上面的 Python 測試全綠。
7. 文件：`CLAUDE.md`、`docs/methodology.md`、`eval/README.md`、`CONTRIBUTING.md`（若提到怎麼報數字）一致；研究紀錄記下這次的改法與理由。

## 5. 不在這一片

- **D：擴大或輪替保留集**（使用者同意，排在 v0.3.0 之後另寫契約）：由不參與實作的 agent 盲寫新的句子；每個版本換一批，舊的降成開發集。
- **Discord 調參集挑長句**（使用者 2026-10-08：「Discord 的調參集雖然夠口語，但是挑出來測試的句子都太短」）：改挑長一點的句子，或把同一串連續訊息合成一列，和 D 一起做。
- 可重複使用的保留集（Dwork 等 2015，Thresholdout）：只列為參考。
- 改變任何解碼結果。

## 6. 停止條件、預算、限制

- **停止條件**：Python 與 CLI 的 rowstats 對不齊；任何現有摘要行或 golden 改變；`bench.py` 舊的 results JSON 讀不進來。
- **預算**：executor 1 回合加 1 次修正。
- **executor 不可以做的事**：不連網；不讀 `eval/holdout/`（保留集的那項測試只在 fresh verifier 的環境跑）、`~/side-project/shanjie-private/`、學習檔、聊天紀錄；不安裝、不啟動 App，不執行 `.app` 裡的程式，不呼叫 TIS 或 lsregister，不碰 `~/Library` 與鑰匙圈；不 push、不開 PR；不碰其他 worktree；`bench.py run` 會建置並跑多個版本，只准跑 `table` 與單元測試。
