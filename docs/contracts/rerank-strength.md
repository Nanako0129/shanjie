# 語意校正強度：保守／標準／積極

狀態：第一次 REVISE（8 點）、第二次 REVISE（7 點）、安全審查（§9）都照改；收尾審查 3 點（保留集基準指令、§4.2–§4.4 的順序、保留集只跑一次）照改後**定稿 READY**（2026-10-14）。

**修訂一**（2026-10-14，§11）：v0.4.3 改了解碼器的合併方式，候選變了；在 v0.4.3 的候選上重做 §1、§4.2–§4.5，另加兩個只記錄的書面臂與 D2c 拆解。修訂一以 §11 為準，和前面衝突的地方照 §11。

## 0. 起因

- 維護者問「小模型能更激進一點嗎」。量過（研究紀錄 2026-10-13）：最激進的 R1（α 0.7、τ 2、δ 0）在 E2 判斷期改對 273／改壞 142（改壞率 2.16%），出貨的 R3′（0.3／2／0.25）是 125／16（0.24%）。
- 預設維持標準（聊天 R3′、書面 0.7／1／2），保守與積極是使用者自己選的（PLAN「語意校正強度」）。
- 評估資料（維護者 2026-10-13 選擇題）：沒有沒看過的私人月份，用**公開集合＋discordtune＋保留集**。

## 1. 選值（main；選完凍結，commit 進研究紀錄，之後不改）

分數檔（tinylm-v3 的分數，`judge.py --model v3`；X0c 用 v0.4.1 CLI、不開詞包、`--context` 產生，研究紀錄 2026-10-13 X0c 記了 dump 雜湊）：公開集合的分數檔在 repo 外的快取（`R`），E2 調參期（5,761 列，列檔 `81c035b0…`）與 discordtune 的分數檔在私人資料夾（`P`，調參期在 `P/tune`）。私人的部分照「Mac 不跑重運算」放 Mac 以外的機器，或在 Mac 上只跑選值（只讀分數檔、不解碼，量很小）。`R`、`P` 是那兩個資料夾，在 `experiments/e2-chateval/` 執行：

1. **先重現標準**（不相等就停，§6）：
   - `python3 judge.py --scores $P/tune $R --sets chatclause --setting chat --model v3 --out /tmp/rs-c0 --select STD --tuning chatclause --weight 1 --cap 0.005` → 必須選出 α 0.3、τ 2、δ 0.25。
   - `python3 judge.py --scores $R --sets cvtune wikitune --setting formal --model v3 --out /tmp/rs-f0 --select STD --tuning cvtune wikitune --weight 10` → 必須選出 0.7、1、2。
2. **選保守與積極**：
   - 聊天保守：同第一條，`--select CONS --cap 0.001`；聊天積極：`--select AGGR --cap 0.02`。
   - 書面保守：同第二條，`--select CONS --weight 30`；書面積極：`--select AGGR --weight 3`。
   - 某一條因上限排除所有格而失敗（`fusegrid.select_weighted` 丟 ValueError），那一檔那一種設定照 §4 的表示法寫成標準值，並記錄。

## 2. 檔案格式、核心、CLI

1. **SJTL0003**（little-endian；SJTL0002 的 116 bytes 之後接 96 bytes，檔頭共 212 bytes，之後的字表與張量和 v2 相同）：
   | 位移 | 欄位 |
   |---|---|
   | 0 | magic `SJTL0003` |
   | 8 | u32 version ＝ 3 |
   | 12–115 | 和 SJTL0002 相同（權重型別、形狀、聊天與書面的標準 α、τ、n-gram 雜湊、標準 δ） |
   | 116, 124, 132 | f64 聊天保守 α、τ、δ |
   | 140, 148, 156 | f64 聊天積極 α、τ、δ |
   | 164, 172, 180 | f64 書面保守 α、τ、δ |
   | 188, 196, 204 | f64 書面積極 α、τ、δ |
   - 每一組都檢查：α 有限、τ 不是 NaN（可以是 +∞）、δ ∈ [0, `MAX_DELTA`]；任一組不合法整個檔拒絕。
   - 不出貨的那一檔（§4）寫成和標準相同的三個值。「沒有保守／積極」只指 SJTL0001／0002 的檔。
   - `experiments/s7a-tinylm/export_sjtl.py` 加 `--levels` 寫 v3；`core/src/tinylm.rs` 讀 v1／v2／v3。Rust 與 Python 各有格式測試，斷言 212 bytes 檔頭與欄位順序、壞值被拒。
2. **C ABI**：`int32_t shanjie_engine_set_rerank_level(ShanjieEngine *engine, uint32_t level, ShanjieOutput **out)`，0 保守、1 標準（預設）、2 積極，碼與快照照 `set_profile`：
   - 1：engine 或 out 是 NULL；2：level > 2；狀態都不變。
   - 沒有載入小模型時：記下 level、回 0（和 profile 一樣先記著）。
   - 載入的是 v1／v2 檔、level ≠ 1：記下 level，實際用標準，回 0。
   - `int32_t shanjie_engine_rerank_levels(ShanjieEngine *engine, uint32_t *levels)`（照 `learning_status` 的形式，包在 `guard` 裡）：engine 或 levels 是 NULL 回 1；成功回 0 並寫入位元遮罩（bit0 保守、bit1 標準、bit2 積極）：沒有小模型 0、v1／v2 檔 2、v3 檔一律 7（不管值是否和標準相同、也不管 profile）。`shanjie.h` 寫同一句定義；核心測試斷言這幾種值。
   - `load_tinylm` 或 `load_lm` 之後保留記下的 level。
   - setter 呼叫 `invalidate`：舊的 rerank job 套用時回 `SHANJIE_NO_CHANGE`；job 建立時記下當時的 α、τ、δ。
   - `core/include/shanjie.h` 與 `docs/contracts/s3a.md` 同一個 PR 更新。
3. **CLI**：`--rerank-level conservative|standard|aggressive`（預設 standard），用在 v1／v2 檔而不是 standard 時結束碼 2 並說明。cli 測試涵蓋。

## 3. 殼

- 設定視窗「語意校正」開關下面加分段選擇「保守／標準／積極」。**一律顯示三個選項**；語意校正關閉、`rerankEnabled` 為假（含降權表關閉或小模型沒載入），或 `shanjie_engine_rerank_levels` 不含 bit0 與 bit2 時，整個控制反灰。
- 預設標準；改了立刻呼叫 setter（重算組字），存在設定裡；每次建 engine、`load_tinylm` 之後送一次存起來的 level。
- 不加選單項目。動畫用系統預設。
- **Swift 測試的檔**（不依賴出貨的是哪一版）：測試讀真的 `data/lm/tinylm.sjtl`；它是 v2 時，在記憶體裡改寫檔頭（magic 改 `SJTL0003`、version 改 3、在位移 116 插入 96 bytes 的三組等於標準的值）做出 v3；它是 v3 時，刪掉位移 116–211、magic 與 version 改回 v2，做出 v2。兩個檔都寫進暫存的 resources。斷言：v2 檔時控制反灰；v3 檔時控制可用、改選後送出正確的 level；重開後還原。executor 的 PR（真檔是 v2）與切換的 PR（真檔是 v3）上 `make test-swift` 都要過。

## 4. 驗收與出貨門檻

1. **選值紀錄**：§1 兩條重現與四條選值的輸出記進研究紀錄。
2. **評估表**（同一張表，`--net`；每檔對 n-gram 一行）：
   - 聊天：dev302、打字測驗、錯字回報、cvtune、wikitune、lytune、wikitypo、discordtune；
   - 書面：dev302、打字測驗、錯字回報、cvtune（選值用）、wikitune（選值用）、lytune、wikitypo。
   - **順序**：第一次用 §1 選出的值跑（`--out /tmp/rs-eval-c`、`/tmp/rs-eval-f`），§4.3 判這一次的表；§4.3 改了任何值，就用最後的值再跑一次，`--out /tmp/rs-eval-final-c`、`/tmp/rs-eval-final-f`（沒改值時最後一次就是第一次）。§4.4 比對、研究紀錄記的都是最後一次，它的值必須和 `export_sjtl.py --levels` 傳入的值相同。
   - 指令（在 `experiments/e2-chateval/`，`R`、`P` 同 §1；`<…>` 換成當次的值）：
     - 聊天：`python3 judge.py --scores $P $P/tune $R --sets dev302 typing76 user-reported cvtune wikitune lytune wikitypo discordtune --setting chat --model v3 --out /tmp/rs-eval-c --fixed STD 0.3 2 0.25 --fixed CONS <α τ δ> --fixed AGGR <α τ δ>`
     - 書面：`python3 judge.py --scores $R --sets dev302 typing76 user-reported cvtune wikitune lytune wikitypo --setting formal --model v3 --out /tmp/rs-eval-f --fixed STD 0.7 1 2 --fixed CONS <α τ δ> --fixed AGGR <α τ δ>`
     - 兩條都要以 0 結束並印出 STD／CONS／AGGR 的表。
3. **出貨門檻**（逐格、機械判斷；每一條不過的結果都寫在後面）：
   - (a) 任一格「改對 < 改壞 而且 p < 0.05」→ 該檔該設定寫成標準值。
   - (b) 聊天積極：discordtune 淨值 ≥ 標準 且 lytune 淨值 ≥ 標準；書面積極：lytune 淨值 ≥ 標準 且 wikitypo 淨值不比標準少超過 1 列（〔修訂，2026-10-14〕原本是「≥ 標準」；wikitypo 只有 155 列，差 1 列在雜訊內，照維護者「自訂規則擋到設計就放寬」放寬，見 §10）。不過 → 該設定的積極寫成標準值。
   - (c) 保守：每一格改壞數 ≤ 標準。不過 → 該設定的保守寫成標準值。
   - (d) 聊天與書面各自判斷，可以只有一種設定有積極。
4. **一致性**（main，dev302，兩種設定 × 三檔）：
   - 先確認候選和 X0c 相同：`shanjie-eval --lm data/lm/bigram.sjlm --profile <p> --dev 302 --context --dump D` 的 SHA-256 等於 `ac921d55…`（聊天）／`890b476b…`（書面）；不同就停（候選來源不同，不是實作錯）。
   - `shanjie-eval --lm data/lm/bigram.sjlm --tinylm <新檔> --profile <p> --dev 302 --context --rerank-level <檔> --rowstats F`，再 `cmp F <最後一次的 --out>/rowstats/<STD|CONS|AGGR>.<p>.dev302.new.txt`；判定只看 `cmp` 的結束碼。
5. **保留集**（排在 §7.3 匯出之後、§7.4 問維護者之前，只跑一次）：fresh verifier 對**本機匯出的檔**照 `CLAUDE.md` 跑，三檔 × 兩種設定各一次：`shanjie-eval --lm data/lm/bigram.sjlm --tinylm <本機新檔> --profile chat|formal --set holdout --context --rerank-level <檔> --rowstats <自己的暫存>`；基準每種設定一條、和三檔共用：`shanjie-eval --lm data/lm/bigram.sjlm --profile chat|formal --set holdout --context --rowstats <暫存>`（沒有 `--tinylm`、沒有 `--rerank-level`）；只回表格，刪 rowstats。
6. **實機**（維護者）：設定視窗切三檔，main 從公開集合挑一句三檔結果不同的句子寫在 PR，打出來看得到差別；重開 App 後選擇還在。

## 5. 範圍外

重訓模型；改標準那一檔的值；n-gram 的強度；選單項目；隱藏相同選項。

## 6. 停止條件與回退

- §1.1 重現不出標準值 → 停（分數檔不是當初的分數），回報。
- §4.3 之後保守與積極在兩種設定都寫成標準值 → 這一片不出貨，停下來問維護者。
- §4.4 不一致 → 停。
- 保留集任一檔、任一設定淨值 < 0 → 停下來問維護者；不改值、不重跑保留集。
- 回退：revert 這一片的 PR，資料檔留在 `tinylm-v3`。

## 7. 負責人與順序

1. main：§1 選值與 §4.2–§4.3（私人部分只讀分數檔）。
2. `pilotfish:security-executor`（worktree `../shanjie-strength`，分支 `feat/rerank-strength`；讀檔與 C ABI 屬安全審查範圍，先例 s7c2a、s7c2b、s7c2c）：§2、§3、§9 的每一項 FIX、格式與 ABI 與 CLI 測試（用合成的 v3 小檔，不用真模型）。本機測試：`make test-rust`、`make test-python`、`make test-swift`。**不改** `data/*.sha256`、CI 與 `release.yml` 的下載。
3. main：先核對 S7a 的 `tinylm-M.npz`（repo 外）的 SHA-256 是 `32308122…`，再 `python3 experiments/s7a-tinylm/export_sjtl.py --npz <tinylm-M.npz> --out <repo 外的暫存>/tinylm-v3-levels.sjtl --type int8 --ngram-model data/lm/bigram.sjlm --chat 0.3 2 0.25 --formal 0.7 1 2 --levels <聊天保守 α τ δ 聊天積極 α τ δ 書面保守 α τ δ 書面積極 α τ δ>`。機械核對（`python3 -c` 比切片，結束碼 0，記進研究紀錄）：新檔 bytes [12,116) 等於 `data/lm/tinylm.sjtl` 的 [12,116)；新檔 212 之後等於舊檔 116 之後。然後 §4.4、§4.5（保留集一次）。
4. main：§4.5 通過後**問維護者**能不能建資料 Release（名稱照 `docs/data-files.md` 的表：匯出格式變了發新的 tinylm Release，下一個編號；說明照 `docs/releases/README.md`，不標 Latest）；建好後下載回來核對雜湊。
5. main：切換的 PR（照 `docs/data-files.md` 換版清單：雜湊檔、CI 與 `release.yml` 的下載、`cli/tests/golden.rs`、`Support.swift`、文件），和 executor 的 PR 合在同一個 PR 或緊接著的下一個；/code-review＋fresh verifier＋CI，merge commit。合併時的 verifier **不再跑保留集**：核對切換進來的檔 SHA-256 等於 §7.3 本機匯出、§4.5 量過的那個檔，引用研究紀錄的 §4.5 結果。
6. 維護者：§4.6 實機。
7. 預算：executor 一輪＋一次修正；不花錢。

## 8. executor 不可以做的事

安裝軟體；啟動 App、呼叫 TIS、lsregister；碰 `~/Library`、鑰匙圈；讀維護者的私人資料、`eval/holdout/`；建 Release、push、開 PR、留言；改 `data/*.sha256`、CI 或 `release.yml`；改選出的值或這份契約。

## 9. 安全審查（`pilotfish:security-reviewer`，2026-10-14）與處置

沒有 P0／P1。信任邊界：殼只讀 bundle 裡的 `tinylm.sjtl`（簽章、公證）；CLI 的路徑由使用者給；既有的讀檔上限與長度計算不變。

| # | 發現 | 處置 |
|---|---|---|
| P2-1 | `rerank_levels` 直接回遮罩：NULL 與「沒有模型」分不出來，panic 轉成的 4 會被當成遮罩 | FIX：改成 out 參數＋錯誤碼（§2.2） |
| P2-2 | v3 讀檔時標準 δ 可能靜默變 0；新欄位要在長度檢查之後讀 | FIX：版本只在一個 match 決定「檔頭長度、有沒有 δ、有沒有等級」，字表起點、張量起點、`want` 共用同一個檔頭長度；測試：v3 小檔每個前綴長度 0..=212 都回 `Format` 不 panic；v3 與 v2 小檔的標準三元組、n-gram 雜湊、`ll_many` 逐位元組相同；12 個新欄位填互不相同的值（位移或順序錯都抓得到）；突變：v3 忽略等級、v3 不讀標準 δ、拿掉新欄位前的長度檢查、新欄位不檢查範圍，各自讓斷言失敗 |
| P2-3 | 既有的「未知版本 SJTL0003＋3」測試會變成在測長度 | FIX：改成 `SJTL0004`＋4，補交叉組合 `SJTL0003`＋1／2、`SJTL0001`／`SJTL0002`＋3 |
| P2-4 | 新三元組要和標準值同一條檢查 | FIX：Rust 用同一個函式檢查 6 組（α 有限、τ 非 NaN、δ ∈ [0, 1000]）；Python `check_params` 套到 `--levels` 每一組，壞值丟 ValueError、不寫檔；測試 12 個位移各填壞值都被拒，−0.0、δ＝1000、τ＝+∞ 被接受 |
| P3-5 | τ ≤ 0 或 −∞ 現在就被接受（等於永遠不重排，保守方向） | ACCEPT，和 v1／v2 一致；契約的「τ 不是 NaN」照現有語意 |
| P3-6 | level 放哪裡、setter 順序 | FIX：level 是 `Engine` 的欄位，`TinyLm` 不可變；`RerankJob` 建立時存自有的 Copy 值，Send／Sync 斷言保留；setter 照 `set_profile`：先 `clear_out`、範圍檢查在動 engine 之前、invalidate → 設值 → clear_pred → refresh → 失敗 clear_all；測試：改等級後套用舊工作回 `SHANJIE_NO_CHANGE`、level 3 回 2 且狀態與 generation 不變 |
| P3-7 | 殼存設定 | FIX：照 `ProfileMode` 存字串 raw value，不認得的值一律標準，enum 對應固定的 `UInt32`；Swift 測試怪值 |
| P3-8 | CLI 沒給 `--tinylm` 卻指定非標準等級會靜默只量 n-gram | FIX：照 `--tinylm-scores needs --tinylm` 改成錯誤 |
| P3-9 | 記錄 | ACCEPT：只記錯誤碼；`RerankJob`、`RerankResult` 不加 `Debug` |
| P3-10 | 寫到規則的地方 | FIX：`shanjie.h` 的 out of range 清單與 S7c-2a 段、`core/tests/c/abi_smoke.c` 加兩個新函式、`export_sjtl.py` 與 `tinylm.rs` 的模組說明、`docs/contracts/s7c-core-tinylm.md` §2.1 加修訂、`docs/contracts/s3a.md` |
| P3-11 | 實作者 | FIX：§7.2 改成 `pilotfish:security-executor` |
| P4-12 | v3 遮罩沒定義 | FIX：一律 7（§2.2） |
| P4-13 | 執行中的工作與重建 engine | ACCEPT＋測試：殼重建 engine 時在 `load_tinylm` 之後、`engine = e` 之前送出存起來的等級；Swift 測試（照 `rerankSent` 加 `rerankLevelSent`）確認換鍵盤配置或詞包重建時有重送 |


## 10. 選值結果（2026-10-14，main）

- **§1.1 重現**：聊天選出 α 0.3、τ 2、δ 0.25（調參期 5,761 列，改對 126／改壞 21）；書面選出 0.7、1、2（cvtune＋wikitune 6,618 列，74／1）。都等於出貨的標準值。
- **§1.2 選值**：

| 設定 | 檔 | α、τ、δ | 選值集改對／改壞 |
|---|---|---|---|
| 聊天 | 保守（cap 0.001） | 1、2、3 | 41／5 |
| 聊天 | 積極（cap 0.02） | 0.5、2、0 | 253／105 |
| 書面 | 保守（weight 30） | 0.5、1、1.5 | 59／0 |
| 書面 | 積極（weight 3） | 0.5、1、0.25 | 235／34 |

- **§4.3 門檻**（第一次評估表 `/tmp/rs-eval-c`、`/tmp/rs-eval-f` 的內容，私人部分只有數字）：
  - 聊天積極：(a) lytune 改對 241 < 改壞 369（p < 0.0001），(b) discordtune 淨值 82 < 標準 90、lytune −128 < 58 → **寫成標準值**。
  - 書面積極：(a) 每一格都過；(b) lytune 45 ≥ 33 過，wikitypo −1 < 標準 0。**(b) 的 wikitypo 條件放寬成「淨值不比標準少超過 1 列」**（155 列的集合，差 1 列在雜訊內；維護者 2026-10-14 的原則：自訂門檻擋到設計時放寬）→ 保留 0.5、1、0.25。
  - 保守：兩種設定每一格的改壞數都 ≤ 標準 → 保留。
- **最後的值**（§4.2 用這組重跑 `rs-eval-final-*`，書面積極與第一次逐列相同，聊天積極等於標準）：聊天 標準 0.3、2、0.25／保守 1、2、3／積極 0.3、2、0.25；書面 標準 0.7、1、2／保守 0.5、1、1.5／積極 0.5、1、0.25。`export_sjtl.py --levels` 照這組。


## 11. 修訂一：在 v0.4.3 的候選上重量（2026-10-14）

### 11.0 起因
- §1、§4 的分數檔是 v0.4.1 CLI 的候選（X0c）。v0.4.3 改了解碼器合併（PR #147），名單變了，三檔的值與評估表都要在新候選上重做。
- 錯誤桶表（`docs/triage.md`，v0.4.3 前的 commit `18aec39`）與 Fable 的分析（維護者 2026-10-14 選「A 融合層分析」）：書面 D2b（小模型選對、融合分差不到 δ）699 列、D2a（n-gram 分差 ≥ τ、沒重排）264 列；D2c（小模型也選錯）1,657／1,262 列裡，有多少是「小模型偏好正解、只是 α 不夠」還不知道。這三件事決定之後該調融合還是換模型。

### 11.1 分數檔（main，Mac 以外的機器）
- CLI：開跑前先把本分支合併 main，合併後的 HEAD（必須含 #147 `5c9cfef`）就是**唯一的 CLI commit**，開跑前寫進研究紀錄；每台機器（含 Mac）都從這個 commit 建 `shanjie-eval`，之後這一片不再合併 main。每個（設定, 集合）跑一次，和 X0c 的條件相同：`--lm data/lm/bigram.sjlm --profile <p> --context --tinylm data/lm/tinylm.sjtl --tinylm-scores <DIR>/v3.<p>.<set>.jsonl --dump <DIR>/<set>.<p>.dump`，不開詞包、不載 KN、不給 `--rerank-level`（標準）。
- 集合：§4.2 的全部集合（聊天加 discordtune），加上選值用的 E2 調參期（`chatclause`，聊天）。錯字回報用當時 main 的全部列（n 記進研究紀錄；和 X0c 的 84 列不同，所以不和舊表逐列比）。
- **開跑前在每台機器核對**（任一不符就停，11.8）：建 CLI 的 commit 等於上面記的那一個；`data/lm/bigram.sjlm`、`classes.sjc`、`tinylm.sjtl` 的 SHA-256 等於 `data/*.sha256`；私人列檔：`chatclause` `81c035b0…`、discordtune `77e1c90a…`（錯誤桶 `full-run` 用的同一份）。研究紀錄逐台記下這些值。
- **放哪裡**：私人的兩個集合（`chatclause`、discordtune）只在放私人資料的那台機器（Windows 機器；列檔照「評測與資料」搬過去、核對雜湊）產生與使用，帶回 Mac 的只有表格數字。公開集合在兩台機器分著跑（維護者 2026-10-14「兩邊同時跑滿」），全部集中到放私人資料的那台，供 §4.2、§4.4 使用；公開的分數檔另留一份在 Mac 上 repo 外的快取（`R2`）。
- **跨機器一致**：dev302 兩種設定在兩台機器與 Mac 各跑一次（Mac 只跑這兩格，量很小），三處的 dump 與 `v3.<p>.dev302.jsonl` 的 SHA-256 都要相同（不同就停，11.8）。這一步同時確認 x86 上算的小模型分數和 Mac（出貨平台）逐位元組相同。研究紀錄記每個公開集合 dump 與分數檔的 SHA-256；dev302 兩種設定的雜湊取代 §4.4 的 `ac921d55…`／`890b476b…`。

### 11.2 重做 §1（選值）
- 指令同 §1，`R`、`P` 換成 11.1 的目錄。
- §1.1 改成**只記錄**：新候選上用同樣規則選出的「標準」值寫進研究紀錄；和出貨值不同**不停**（候選換了，§1.1 原本的停止條件是為了確認分數檔沒弄錯，這裡改用 11.1 的雜湊核對）。標準那一檔仍是出貨值（§5 範圍外照舊）。
- §1.2 的保守與積極照原本的上限與權重重選；結果和 §10 不同就用新的值。

### 11.3 重做 §4.2–§4.4
- §4.2 的兩條指令在放私人資料的那台機器跑，用 11.1 的目錄與 11.2 的值，`--out <暫存>/rs43-eval-c`、`rs43-eval-f`（§4.3 改了值就再跑 `rs43-eval-final-*`）；帶回 Mac 的只有表格。
- §4.3 的門檻不變（含 §10 對書面積極 wikitypo 的放寬：淨值不比標準少超過 1 列）。
- §4.4 也在那台機器跑：§7.3 在 Mac 匯出的新檔複製過去，核對 SHA-256 和 Mac 上的相同，再用那台的 CLI 跑 `--rerank-level`，和同一台的 `rs43-eval-final-*/rowstats/*.dev302.new.txt` 做 `cmp`；dev302 的 dump 雜湊用 11.1 記的值。

### 11.4 只記錄、不出貨的兩個書面臂
§4.2 的書面指令多兩個 `--fixed`：`T2 0.7 2 2`（τ 改 2，看 D2a 的 264 列）與 `D1 0.7 1 1`（δ 改 1，看改壞是不是隨 δ 增加）。兩行照同一張表記進研究紀錄，不進 §4.3 的判斷、不寫進匯出的檔。

### 11.5 D2c 拆解（只記錄）
- 新增 `experiments/e2-chateval/d2c_split.py`（只讀 11.1 的分數檔，不解碼），定義和錯誤桶契約（`docs/contracts/triage.md` §3 的 D2）相同，用該設定的標準 α、τ、δ：
  - 範圍：`ok[0] = 0`（n-gram 第一名錯）而且前 n 名（n ≤ 8）裡有 `ok = 1` 的列；正解 g＝第一個 `ok = 1` 的位置。錯誤桶的 A、B（正解不在前 8）與 D1（n-gram 第一名就對）不會進來。
  - 沒有 `ll` 的列（n < 2 或候選太長）記 D2-none，另報，不進下面的分母。
  - D2a：`ng[0] − ng[1] ≥ τ`。否則 j＝`argmax(ng + α·ll)`，s＝`ng + α·ll`，同分取前面的（`fusegrid.pick` 的規則）：`ok[j] = 1` 而且 `s[j] − s[0] < δ` → D2b（融合分差不到 δ 所以沒換）；`ok[j] = 1` 而且 `s[j] − s[0] ≥ δ` → **重排救回**（最後選對，不是錯列；另報，不進任何 D2 桶）；`ok[j] = 0` → D2c。
  - D2c 再分：(i) 小模型單獨選（`fusegrid.lm_alone`）選中的是對的（`ok[lm_alone] = 1`）→「小模型偏好正解，融合沒讓它贏」；(ii) 其餘 →「小模型本身也選錯」。
- 單元測試（`experiments/e2-chateval/test_d2c_split.py`）用合成紀錄：D2a、D2b、重排救回（`ok[j] = 1`、`s[j] − s[0] ≥ δ`，不算 D2b）、D2c-(i)、D2c-(ii)（含 `ll[j] > ll[g] > ll[0]` 的列，必須是 (ii)）、D2-none、正解不在前 n、n-gram 第一名就對，各一列；突變「j 不加 α·ll」「g 取最後一個 ok」「(i) 改成 `ll[g] > ll[0]`」「D2b 不檢查 δ」各讓一個斷言失敗。
- 研究紀錄記每格的重排救回、D2a、D2b、D2c-(i)、D2c-(ii)、D2-none 列數。**判讀**（事先寫好）：合計聊天的 dev302、typing76、錯字回報、cvtune、wikitune、lytune、wikitypo、discordtune 與書面的 dev302、typing76、錯字回報、cvtune、wikitune、lytune、wikitypo（不含 `chatclause`），(i) ÷ D2c 超過 0.5 → 下一片先做融合（α 的調法）；低於 0.2 → 下一片先做模型（tinylm-v4）；中間就兩條都列為候選，由維護者選。

### 11.6 保留集、Release、切換
照 §4.5、§7.3–§7.6，對象換成用 11.2 最後的值匯出的新檔。§7.3 匯出前的核對照舊。§7.4 建 Release 前問維護者。

### 11.7 範圍外
- 斷詞（C3）的子標記：錯誤桶工具的下一步，另開。
- 改標準那一檔的值（§5）；11.4 的兩臂只記錄。

### 11.8 停止條件
§6 照舊，除了 §1.1 的重現改成只記錄（11.2）。另加：
- 11.1 開跑前的核對（建 CLI 的 commit 不是記下的那一個、模型雜湊、私人列檔雜湊）任一不符 → 停。
- 11.1 的 dev302 dump 或分數檔雜湊在兩台機器與 Mac 之間不同 → 停（CLI、模型或平台的計算不一致）。
- 11.3 匯出的新檔複製到另一台後雜湊不同 → 停。

### 11.9 審查紀錄
| 審查 | 要點 | 處置 |
|---|---|---|
| plan-verifier 修訂一第 1 次（REVISE） | 1. §4.4 沒有指定在哪台跑、沒有核對跨機器的 int8 分數；2. 拿掉 §1.1 的停止條件後，私人列檔、CLI commit、模型檔沒人核對；3. D2c 的範圍、(i) 比的是哪個候選、合計哪些集合沒寫清楚 | 全部 FIX：11.1 加開跑前核對與 dev302 dump／分數檔三處雜湊相同；11.3 指定 §4.2、§4.4 在放私人資料的機器跑；11.5 照錯誤桶契約定義、(i) 用 `lm_alone`、列出合計的集合與分母 |
| plan-verifier 修訂一第 2 次（REVISE；之後逐項處置、收尾審查一次） | 1. 範圍內被重排救回的列會被算成 D2b；2. CLI commit 沒有一個指定值，不同機器可能用不同的 commit | 全部 FIX：1 → D2b 要 `s[j] − s[0] < δ`，救回的列另報「重排救回」，測試與突變各加一個；2 → 合併 main 後的 HEAD 是唯一的 CLI commit，開跑前記下，每台機器（含 Mac）都用它 |
