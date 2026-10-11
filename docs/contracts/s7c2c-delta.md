# S7c-2c：門檻 δ 進核心與 `.sjtl`（版本 2）、核心的重排預設關

狀態：READY（2026-10-12：security-reviewer 一次、plan-verifier 第 2 次 READY，§6）；實作完成（PR #122）；fresh verifier CONFIRMED（§3 第 1–6 項重現、9 個突變、保留集聊天 180 → 187（7／0）、書面 184 → 187（3／0））。

## 0. 起因與依據

- **S7e**（PR #120，研究紀錄 2026-10-12）：照使用者的權重「改壞 1 句 ≈ 改對 10 句」，M 在公開調參集上選到聊天 α 0.2、τ 1、**δ 0.25**，書面 α 0.7、τ 1、**δ 2**。δ 是新的門檻：融合分數最高的候選，要比 n-gram 第一名的融合分數高出 δ 以上才換。
- **使用者決定（2026-10-12）**：語意重排**預設關閉**出貨，用 M 與上面的嚴格值；δ 進核心與 `.sjtl`。
- **現況**：`.sjtl` 是 `SJTL0001`（`docs/contracts/s7c-core-tinylm.md` §2.1），檔頭只有每種設定的 α、τ；Rust 的 `pick(ng, ll, α, τ)`（`core/src/tinylm.rs`）沒有 δ。照現行的 `export_sjtl.py` 匯出這組值，δ 會悄悄變成 0，那是沒量過的設定（S7e 契約 §8）。核心引擎的 `rerank_on` 預設是開（`core/src/engine.rs`）。
- **使用者決定（2026-10-12，暫離前）**：這一片照流程做（契約、審查、實作），**不發版、不合併 #118**。

## 1. 要做到什麼、怎麼看到

- 新的 `.sjtl` 版本 2 帶每種設定的 δ；Rust 的 `pick` 用 δ，和 `fusegrid.pick` 逐條相同；舊的 `SJTL0001` 照讀，δ 當 0，行為和現在完全相同。
- 核心引擎的重排預設關（殼的預設在 #118，另外處理）。
- **怎麼看到**：
  - 用 S7e 的值匯出 M 的 int8 版本 2 檔（候選的 `tinylm-v2`，不發版），CLI `--tinylm` 在公開集合上的結果和 S7e `fusegrid.py --break-weight 10` 的 M8 表格逐格相同（§3 第 3 項）；
  - 版本 1 的檔（`tinylm-v1`，`8725204c…`）在 CLI 上的結果和現在逐位元組相同。

## 2. 改動

### 2.1 檔案格式 `SJTL0002`（只由 Python 寫、Rust 只讀）

- 版本 1 的檔頭（100 bytes）不變：magic `SJTL0001`、版本 1、權重型別、形狀、聊天與書面的 α、τ（f64）、n-gram 的 SHA-256（偏移 68–99）。
- 版本 2：magic `SJTL0002`、版本欄 2；前 100 bytes 的欄位與偏移和版本 1 **完全相同**，之後多 16 bytes：偏移 100 聊天 δ（f64）、108 書面 δ（f64）。檔頭共 116 bytes，後面的字表與張量照舊。
- Rust 讀檔的檢查（新增，都回 `Format`）：
  - **先由 magic 與版本欄決定檔頭長度**（版本 1：100、版本 2：116），兩者要一致（`SJTL0001`＋1、`SJTL0002`＋2，其他組合與未知的版本都拒絕）；**讀 δ 之前先確認檔案長度 ≥ 檔頭長度**，否則回 `Format`（不得 panic；s7c2a §2.6「壞檔是錯誤，不是半個模型」）。
  - δ 必須在 [0, 1,000] 之內（`(0.0..=1000.0).contains(&δ)` 一個式子同時擋掉 NaN、無限大與負數；上限是防呆，S7e 的格點最大 4）；−0.0 視同 0（比較 `s[j] − s[0] ≥ δ` 的結果相同）。
  - 字表的起點、張量的起點、`want` 的長度計算都用**同一個**檔頭長度變數；檔案長度仍要完全相等。版本 1 的 δ 一律是 0。
- 其他既有檢查（形狀上限、字表、檔案大小上限 64 MiB、n-gram 綁定、Kneser–Ney 互斥）不變。

### 2.2 Rust：`pick` 與 `rerank`

- `pick(ng, ll, α, τ, δ)`：原本的規則選出 j 之後，**只有** `s[j] − s[0] ≥ δ` 才回傳 j，否則回傳 0（`s[i] = ng[i] + α·ll[i]`；和 `fusegrid.pick` 同一個比較，邊界含等號）。δ 0 時和現在完全相同。
- `TinyLm` 加讀取 δ 的方法（照 `alpha_tau` 的樣子，回 `(α, τ, δ)`），`rerank` 用它〔實作：`alpha_tau_delta` 取代 `alpha_tau`，舊的方法只剩測試在用，已刪〕。`pick` 的註解改寫，不再說是「現在的」`fusegrid.py pick` 而是寫明含 δ。
- 呼叫者（`RerankJob::run`、CLI 的 `--tinylm`）不改介面；C ABI（`core/include/shanjie.h`）不變。

### 2.3 核心的重排預設

- `core/src/engine.rs` 的 `rerank_on` 預設改成 `false`；`shanjie_engine_set_rerank` 照舊可以打開。寫到預設值的地方一起改：`core/src/engine.rs`（欄位註解與 setter 註解）、`core/include/shanjie.h`（`set_rerank` 的「default 1 at engine creation」）、`docs/contracts/s3a.md`（按鍵規則表的「建立時預設 1」，CLAUDE.md 規定核心行為改動要更新 s3a）、`docs/contracts/s7c2a-core-rerank.md`（§1、§2 寫預設開的句子）；實作時再 grep `rerank_on`、`default 1`、「預設 1」、「預設開」、`default on` 確認沒有漏。
- 現有測試裡依賴「預設開」的，改成明確呼叫 `set_rerank(true)`；另加一個測試：新建的引擎載入 `.sjtl` 之後，沒有呼叫 `set_rerank(true)` 就不產生重排工作。

### 2.4 Python：匯出與參照

- `export_sjtl.py`：`--chat ALPHA TAU [DELTA]`、`--formal ALPHA TAU [DELTA]`。**兩個 DELTA 都給就寫版本 2，都不給就寫版本 1**（輸出和現在逐位元組相同），只給一個就報錯結束；δ 不是有限值、< 0 或 > 1,000 時 `export` 丟 `ValueError`、不寫檔；完成時印出版本與兩個 δ。`read_sjtl`（現在只認版本 1）改成兩個版本都讀，回傳 δ。模組說明（它就是格式的權威描述，`tinylm.rs` 開頭這樣說）一起改。
- `tools/gen_tinylm_tiny.py` 多產生一份版本 2 的小檔 `eval/golden/tinylm-tiny/tiny-int8-v2.sjtl`（聊天 δ 0.25、書面 δ 2；權重與版本 1 的 int8 小檔相同），現有兩份小檔不變。
- `tinylm.py` 或 `fusegrid.py` 不改（`fusegrid.pick` 已經有 δ）。

### 2.5 文件

- `docs/contracts/s7c-core-tinylm.md` §2.1：加修訂一段指向本契約 §2.1；`core/src/tinylm.rs` 開頭的模組說明（「SJTL0001 loader」）改成兩個版本。
- `docs/research-log.md`、`docs/PLAN.md` 的 S7 狀態行。
- `docs/releases/tinylm-v2.md`：候選的說明草稿（檔案、SHA-256、內容、量測）。**這一片不建 Release**；建 Release 要使用者當下同意（`docs/releases/README.md`）。

## 3. 驗收

1. **Rust 單元測試**（`core/tests/tinylm_tiny.rs` 與 `core/src/tinylm.rs` 的測試）：
   - 版本 2 小檔讀出 δ（0.25、2）；版本 1 小檔的 δ 是 0；
   - **版本 2 小檔和版本 1 int8 小檔的其他內容完全相同**：α、τ、n-gram 雜湊相同，`ll_many` 在 `items.tsv` 上的結果逐位元組相同（抓「字表從 100 讀、張量從 116 讀」這類偏移錯誤）；
   - 拒絕（回 `Format`、不 panic）：**版本 2 小檔的每一個前綴長度 0..=116**；magic 與版本欄不一致（兩種交叉，現有 `tinylm_tiny.rs` 的 magic／version 兩個案例就變成這兩種交叉，另加一個未知版本 `SJTL0003`＋3）；δ 是 NaN、負數、無限大、> 1,000；版本 2 完整檔多 1 byte 或少 1 byte；
   - `pick` 的 δ：邊界含等號（`s[j] − s[0]` 正好等於 δ 時換）、δ 很大時一律選 0、δ 0 時和舊公式（測試裡留一份舊的實作）在隨機資料上逐列相同；
   - **和 Python 逐列相同**：同一組隨機 ng／ll（固定種子，寫成測試資料檔，數值用 `repr` 精確寫出，和 `items.tsv` 的做法相同；由 Python `fusegrid.pick` 產生期望值）在 α、τ、δ 的格點上，Rust `pick` 的結果全部相同；δ 的等號邊界案例用能精確表示的值（例如 0.25、2）；
   - 引擎預設不重排（§2.3）；
   - `rerank` 把設定的 δ 傳給 `pick`：現有的 `rerank_is_the_measured_rule` 改成比對 `pick(.., α, τ, δ)`，再加一個案例：同樣的候選，版本 2 小檔的 δ 讓選擇和版本 1 不同。
2. **Python 測試**（`experiments/s7a-tinylm/test_export_sjtl.py`）：不給 δ 時輸出和現在逐位元組相同（用現有 golden 比對）；給兩個 δ 時寫出版本 2 且讀回的 δ 正確；只給一個 δ、或 δ 是 NaN／無限大／負數／> 1,000 時報錯而且沒有寫出檔案。
3. **端對端**（main 跑，公開集合，數字記進研究紀錄）。指令全部寫死，和 S7e 的候選設定相同（`run_sets.py dumps`：出貨的 model-v5＋classes-v3、`--context`、不開詞包、不載 KN）：
   - **匯出**：先核對 `~/.cache/shanjie/work/s7a/tinylm-M.npz` 的 SHA-256 是 `32308122…`（S7a，研究紀錄 S7c-1），再 `python3 experiments/s7a-tinylm/export_sjtl.py --npz ~/.cache/shanjie/work/s7a/tinylm-M.npz --out ~/.cache/shanjie/s7c2c-public/tinylm-M8-v2.sjtl --type int8 --ngram-model data/lm/bigram.sjlm --chat 0.2 1 0.25 --formal 0.7 1 2`（`data/lm/bigram.sjlm` 是 model-v5，`f81a021e…`，同資料夾有 classes-v3 的 `classes.sjc`）。候選檔的 SHA-256 記進研究紀錄。
   - **每個集合、每種設定各跑兩次**（基準不給 `--tinylm`、候選給 `--tinylm ~/.cache/shanjie/s7c2c-public/tinylm-M8-v2.sjtl`）：`cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile chat|formal --context --rowstats OUT` 加上集合的列：cvtune `--rows ~/.cache/shanjie/s7c2c-public/tune/cvtune.txt`、wikitune `--rows ~/.cache/shanjie/s7c2c-public/tune/wikitune.txt`、dev302 `--dev 302`、打字測驗 `--rows eval/dev/user-typing.txt`、錯字回報 `--rows eval/dev/user-reported.txt`（72 列，不加 `--limit`）、功能字測試句 `--rows eval/rerank/s7e-function-words.txt`。不加 `--packs`。
   - **比較**：每格 `python3 tools/evalstats.py compare BASE CAND --label "M8 <設定> <集合>"`，輸出的表格行要和 S7e 研究紀錄（2026-10-12）完整 `evalstats` 表裡同名的那一行（對 n-gram 的那一行，不是「(vs shipped)」）**逐字元相同**（n、top1、改對／改壞、p、CER、區間；區間的 bootstrap 有固定種子）。不同就停（S7c-1 記過 Rust int8 和 numpy int8 逐列相同，所以應該完全一致）。 推論（沒量過）：Rust 與 numpy 的 LL 差約 1e-4，`s[j] − s[0]` 剛好落在 δ 附近 1e-4 以內的列可能不同；停下來時回報那幾列的差距（只有數字），分得出是邊界誤差還是程式錯誤。
4. **版本 1 不變**：改動前（81e4453）main 已用 `tinylm-v1`（`data/lm/tinylm.sjtl`，`8725204c…`）跑好 dev302 兩種設定（`--lm data/lm/bigram.sjlm --profile chat|formal --context --dev 302 --rowstats … --tinylm data/lm/tinylm.sjtl`），摘要行與 rowstats 存在 `~/.cache/shanjie/s7c2c-public/pre/`（聊天 top1 276、書面 273，`top1_sha256` 在 `.out` 檔裡）。改動後用同一個指令再跑一次，摘要行與 rowstats 必須逐位元組相同。
5. **突變**（每一項都要讓某個測試在斷言失敗，不是 panic 或編譯錯誤）：δ 的比較改成 `>`；讀檔時版本 2 忽略 δ（一律 0）；不檢查 δ 的範圍；拿掉「讀 δ 之前的長度檢查」；`rerank` 傳 0 給 `pick`；`rerank_on` 預設改回 true；`export_sjtl.py` 在沒給 δ 時也寫版本 2。
6. `make test`、CI 全綠；fresh verifier 重現第 3、4 項，並**照 CLAUDE.md 的保留集規則跑一次**：基準與候選各跑 `cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile chat|formal --context --set holdout --rowstats <verifier 自己的暫存檔>`（候選加 `--tinylm ~/.cache/shanjie/s7c2c-public/tinylm-M8-v2.sjtl`），再 `tools/evalstats.py compare`，聊天與書面各只回表格那一行，rowstats 用完刪。
   - **verifier 的輸入**（都在它能讀的地方，不在 `~/.cache/shanjie/work`）：`~/.cache/shanjie/s7c2c-public/` 裡的候選檔 `tinylm-M8-v2.sjtl`（SHA-256 記在研究紀錄）、`tune/cvtune.txt`（`31de456d…`）與 `tune/wikitune.txt`（`8dcfe40c…`）、`pre/`（改動前的基準）；worktree 的 `data/lm/`（model-v5、classes-v3、`tinylm-v1`）；S7e 的完整表在研究紀錄裡。

## 4. 範圍外

- 殼（#118）：重排開關的預設值改成關、改讀版本 2 的檔、Release 資產與雜湊檔（`data/tinylm.sjtl.sha256`、build-app、check-app、CI、release.yml），以及寫「核心預設開」的註解（`Core.swift`、`Shell.swift`）。等 `tinylm-v2` Release 建好（要使用者同意）之後另做。#118 每次建引擎都明確呼叫 `setRerank`，所以這一片的核心預設改動不會讓它的開關失效（security-reviewer 核對過）。
- 建 `tinylm-v2` Release、推 tag、改網站。
- 重新訓練（S7f）。

## 5. 停止條件、預算、限制、回滾

- **停止條件**：§3 第 3 項有任何一格和 S7e 不同；第 4 項有任何不同；保留集上候選檔的改壞 > 改對且 p < 0.05（回報使用者）。
- **預算**：security-executor 實作 1 次＋修正 1 次。
- **限制**：agent 不讀 `eval/holdout`（verifier 照保留集規則除外）、`~/side-project/shanjie-private`、`~/.cache/shanjie/work`、`~/Library`、鑰匙圈（verifier 可以讀 `~/.cache/shanjie/s7c2c-public/`，那裡只有公開的檔與候選檔）；不安裝、不啟動 App、不在 .app 裡執行任何東西；不建 Release、不推 tag。
- **回滾**：revert 即可；版本 1 的檔照舊可讀，`tinylm-v1` 不受影響。

## 6. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| security-reviewer（2026-10-12） | P1-1：版本 2 的檔若只有 100–115 bytes，讀 δ 時會切片越界而 panic；P2-2：字表／張量起點與 `want` 若用不同的檔頭長度，可能靜默讀錯；P2-3：沒有測試保證 `rerank` 把 δ 傳下去；P2-4：只給一個 δ 會悄悄變 0、NaN／負數的 δ 會寫出讀不了的檔（功能靜默失效）；P2-5：寫預設值與格式的地方比 grep 列出的多（`shanjie.h`、`s3a.md`、export 的說明、`read_sjtl`、模組說明）；P3：未知版本的案例、−0.0、測試資料要精確寫出、Rust 與 numpy 的 LL 誤差可能在 δ 邊界造成一格不同 | 全部 **FIX**：§2.1 先定檔頭長度再檢查長度、同一個檔頭長度變數、δ 範圍一個式子、−0.0；§2.3／§2.5 列出所有檔案；§2.4 兩個 δ 都給或都不給、壞值報錯；§3.1 加前綴長度 0..=116、v1／v2 內容相同、`rerank` 傳 δ、未知版本；§3.2 加壞 δ；§3.3 停下時回報邊界列的差距；§3.5 加兩個突變；§4 列出 #118 要改的註解 |
| plan-verifier 第 1 次 REVISE（2026-10-12） | 2 點：(1) §3 第 3 項與保留集沒寫確切的指令：`--context`、出貨模型、不開詞包，各集合的列從哪來，改對／改壞要怎麼比；匯出指令缺 `--npz`／`--out`／`--ngram-model` 與 `tinylm-M.npz` 的雜湊；(2) verifier 要用的候選檔、cvtune／wikitune、改動前的基準都在它不能讀的地方或沒人產生 | 全部 **FIX**：§3 第 3 項寫死匯出指令（含雜湊核對）、12 組 CLI 指令（基準與候選）、`evalstats compare` 與 S7e 表逐字元比對；第 4 項的改動前基準 main 已在 81e4453 產生並存進 `~/.cache/shanjie/s7c2c-public/pre/`（聊天 276、書面 273）；第 6 項寫明保留集指令與 verifier 的輸入資料夾；§5 限制加上這個資料夾 |
| plan-verifier 第 2 次（2026-10-12） | READY | 開始實作 |
