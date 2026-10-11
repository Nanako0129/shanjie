# S7c-1：小型字元模型重排的 Rust 推論（離線；不接引擎、不出貨）

接續 S7a（`docs/contracts/s7a-tinylm.md`，M 通過 §4）與 S7b（`docs/contracts/s7b-larger-lm.md`，L／XL 與 int8，進行中）。**使用者 2026-10-11**：S7a「接受，先量 int8」——下一片用 int8（約 15 MB）做 Rust 推論，在 int8 上重跑 §4 與保留集；int8 沒過再問 float32。S7b 已量到 M 的權重 int8「夠用」（numpy）。

S7c 分兩片。這一片（S7c-1）只做**核心的前向、模型檔、`pick`、評測 CLI**，回答「Rust 的 int8 路徑在離線評測上是不是仍然通過 S7a §4」。接進引擎、殼、設定、Release 是 S7c-2（§6 列出它要使用者先決定的事）。模型大小等 S7b 的結論；這一片的程式不綁大小，先用 M。

## 1. 要做到什麼、怎麼看到

- 評測 CLI 加 `--tinylm FILE`：每一列取 n-gram 解碼器的前 8 名，用 Rust 算每個候選的 LL，照 S7a 的融合規則重排；top1、`--rowstats`、摘要行都用重排後的第一名。
- **怎麼看到**：研究紀錄的表格（`--tinylm` 對不加，兩種設定 × 每個集合一行 `evalstats compare`）、S7a §4 的逐項 PASS／FAIL（Rust int8 路徑），以及三道一致性關卡的數字。

## 2. 做什麼

### 2.1 模型檔 `SJTL0001`（只由 Python 寫、Rust 只讀）

- `experiments/s7a-tinylm/export_sjtl.py`：從 `.npz`（`tinylm.save_params` 的格式）寫 `SJTL0001`，little-endian：
  - magic、格式版本、權重型別（`int8` 或 `f32`）、n_layer、n_head、d、ctx、V；
  - 聊天與書面各一組 α、τ（f64；M 是 S7a 凍結的 0.7／2、0.7／2）；
  - 調 α、τ 時用的 n-gram 模型的 SHA-256（32 bytes；M 是 model-v5 的 `bigram.sjlm`，`data/bigram.sjlm.sha256`）；
  - 字表（id 3 起的字，Unicode 碼位 u32；id 0／1／2 是 PAD／UNK／BOS，和 `tinylm.Vocab` 相同）；
  - 張量：順序固定（照 `tinylm.py` 的參數名排序規則，寫在 exporter 與 Rust 讀檔處的同一份註解）。二維的矩陣：`int8` 型別時是每列對稱 int8 加 f32 縮放，**直接呼叫 `latency.quantize_int8`**（和 S7b §2.3 量過的同一個量化）；`f32` 型別時原樣。一維的向量一律 f32。
- 不另外產生 npz 讀取器；Rust 不讀 npz。
- `int8check.py`（`dequant`）在 S7b 的分支（`research/s7b-larger`，未合併）。這一片從 main 開，golden 產生器與 exporter 只依賴 main 已有的 `latency.quantize_int8`，還原用同一個算式（`q.astype(float32) * scale`）；第 4 項的 `tinylm-M8.npz` 是 S7b 已經產生的檔（`b84376c3…`）。

### 2.2 Rust：`core/src/tinylm.rs`

- `TinyLm::load(bytes 或 path)`：核對 magic、版本、型別、形狀與長度、字表無重複；`int8` 在載入時還原成 f32（`q as f32 * scale`，和 `int8check.dequant` 同一個算式）。壞檔回錯誤（新的錯誤型別，訊息點名 `.sjtl`）；**不靜默略過**。
- 前向：和 `tinylm.NumpyLM` 同一個模型（pre-LN、權重共用的嵌入、學得的位置、GELU tanh、因果遮罩），f32。LL 的定義和 `tinylm.ll_many` 完全相同：`[BOS] + k + c`、對 c 的每個位置加總 log10 機率、字表外的字算 UNK、`[BOS]+k+c` 超過 ctx 時從最舊的前文字開始丟、候選本身超過 ctx−1 就是錯誤。8 個候選一批（右側補 PAD，因果遮罩讓 PAD 不影響前面的位置）。
- 矩陣乘法：macOS 用 Accelerate（`cblas_sgemm`；`tanh`／`exp` 用 vForce `vvtanhf`／`vvexpf`），`extern "C"` 加連結 framework，不加任何 crate。其他平台用簡單的純 Rust 版本；兩者的 LL 差 < 1e-5 寫成單元測試（隨機權重）。核心現在沒有外部相依（`core/Cargo.toml`），這一片也不加。
- `pick(ng, ll, α, τ)`：和 `fusegrid.pick` 逐條相同：候選少於 2 個或 `ng[0] − ng[1] ≥ τ` 時選 0；否則選 `ng + α·ll` 最大的，同分取 n-gram 名次較前的。
- 這一片**不碰** `engine.rs`、`ffi.rs`、`shanjie.h`；殼只改一行：`macos/Package.swift` 的 `ShanjieKit` 在 `linkerSettings` 加 `.linkedFramework("Accelerate")`。原因：Rust 的 `#[link(kind = "framework")]` 不會帶進 staticlib，殼用路徑連結 `libcore.a`（`macos/Package.swift` 的 `libcore`），沒有這一行時，只要 `tinylm.rs` 和 FFI 用到的程式落在同一個 archive 成員，`make bundle`／`swift test` 就會缺 `_cblas_sgemm` 等符號。S7c-2 把重排接進引擎時本來就需要這一行。

### 2.3 評測 CLI `cli/`

- `--tinylm FILE`（LM 路徑，`--rows`／`--dev`／`--set holdout` 都可用）：載入失敗就結束並報錯。**模型配對**：載入的 n-gram 模型（`Lm` 解析時算的 SHA-256）和 `.sjtl` 記的不同，或 Kneser–Ney 側檔是開著的（S7a 調 α、τ 時沒有開），就結束並報錯（訊息點名 `.sjtl`），不重排、不輸出結果。前 8 名取自 `decode_from(... BEAM_S1 ...)` 的結果（和 `--dump` 寫的同一份 n-best）；`k` 用 `context_key(前文)`（`--context` 時；不加 `--context` 時 k 為空）；**`context_key` 回哨兵 `SENTINEL`（`"^"`，前文沒有漢字結尾）時 k 為空字串**，和 S7a 打分用的 `tinylm.row_key`／`reference/proto/lm.py` 的 `context_key` 相同（Rust 的哨兵是 `"^"`，Python 的是 `""`）；`"^"` 不可以當成前文字送進字元模型（會變成 UNK）；α、τ 取自檔案、依 `--profile`。
- 摘要行加 `+tl:<.sjtl 的 sha8>`（接在 `+kn` 的位置之後）；沒有 `--tinylm` 時輸出逐位元組不變。
- `--tinylm-scores OUT`：每列一行 JSON，欄位定義和 `score.py` 相同：`i`、`n`、`ng`、`ok`、`ed`（候選對正解的 Levenshtein，`ok` 時為 0）、`glen`、`ll`（`n` ≥ 2 的列才有）。`parity.py` 與 `fusegrid.pick`／`fusegrid.rowstats` 讀得了；`fusegrid.select`／`report` 要的 `key`、`ll0`、`ll16` 不寫（這一片不重選 α、τ）。只有數字，沒有文字。
- 不改 `--dump` 的格式。

## 3. 驗收

1. **沒有 `--tinylm` 時逐位元組不變**：現有 golden 與 `make test` 全過，不改任何 golden 檔。`data/lm/` 裡有沒有 `.sjtl` 都不影響任何測試（這一片不會自動載入它）。
2. **模型配對的拒絕**：CLI 測試——`.sjtl` 記的 n-gram SHA-256 和載入的模型不同時，`--tinylm` 結束碼非 0、訊息點名 `.sjtl`。另一個 CLI 測試：前文是空的、以及前文以非漢字結尾的列，`--tinylm-scores` 的 `ll` 等於 k = 空字串時的 LL（把 `"^"` 當前文送進模型時這個測試要失敗）。
3. **小模型 golden**（Python 產生、Rust 比對，像 `core/tests/kn_tiny.rs`）：`tinylm.random_params` 的小模型分別匯出 `f32` 與 `int8` 兩個 `.sjtl`，加一組 (k, c)（含字表外的字、空的 k、需要丟前文的長 k、單字候選），以及 numpy（float64）在**同一份還原後權重**上算的 LL，放 `eval/golden/tinylm-tiny/`；Rust 每個 |ΔLL| < 1e-4。`pick` 的手算案例（門檻剛好等於 τ、同分、少於 2 個候選）。突變：拿掉因果遮罩、GELU 常數改錯、UNK 改成丟掉、`pick` 的門檻 `≥` 改 `>`、同分取後者，各要讓測試失敗。
4. **真模型的一致性（決定性的一項，main 執行）**：M 的 `tinylm-M.npz`（`32308122…`）匯出 `f32` 與 `int8` 兩個 `.sjtl`，放 session 暫存區或 `~/.cache/shanjie/work/`，**不放進任何 checkout 的 `data/lm`**。
   - **n-best 和 Python 相同**：cvtune、wikitune、dev302、打字測驗、錯字回報（`--limit 66`）兩種設定，`--context`：Rust 的前 8 名字串與順序等於 `reference/proto/lm_eval.py --dump` 的前 8 名，分數差 ≤ 1e-9。（現在的 golden 只比摘要行，前 8 名的順序沒有被比過。）
   - **LL**：cvtune 聊天全部候選（30,328 個），Rust `f32` 檔對 numpy（float64）在 `tinylm-M.npz` 上的 LL、Rust `int8` 檔對 numpy 在 `tinylm-M8.npz`（`int8check.dequant` 的還原權重）上的 LL，最大 |ΔLL| 都 < 1e-4（`parity.py` 讀 `--tinylm-scores`；`ng` 的比較改成容許 1e-9，這個改動在 `parity.py`）。
   - **pick**：`fusegrid.rowstats(rows, [fusegrid.pick(r, α, τ) for r in rows])`（rows＝同一次執行的 `--tinylm-scores`，α、τ 取自檔案）和 Rust CLI 同一次執行的 `--rowstats` 逐位元組相同（`cmp`），兩種設定、第 5 項的每個集合都比。
5. **表格與判斷（int8 檔，Rust 路徑）**：基準（不加 `--tinylm`）對 `--tinylm`，聊天與書面，`--context`，每個集合一行 `evalstats compare`：cvtune、wikitune、dev302、打字測驗、錯字回報（`--limit 66`，前 66 列的 SHA-256 必須是 `6e338dc1…`）、discordtune（main 在本機跑、只回數字、用完刪除），加上**動漫詞包守門**：`eval/dev/acg/` 的四個檔各一行，`--packs acg`（S7a 量的時候詞包是關的，出貨預設是開的；詞包加進來的名字在字元模型下的 LL 可能偏低）。照 S7a §4 判斷（discordtune 聊天 Δtop1 ≥ +1.0 個百分點、精確 McNemar p < 0.05、Δtop1 區間不含 0、ΔCER ≤ 0；其他格沒有「淨值為負而且 p < 0.05」，動漫的格也算在內）。
   - 對照：Rust int8 路徑的 discordtune 兩列與 S7b 的 numpy int8 結果（聊天 4,209 → 4,275、書面 4,143 → 4,228）並列；不要求相等（候選來自 Rust、LL 是 f32），差異寫進研究紀錄。
6. **延遲（只記錄）**：CLI 對每列重排的時間（載入一次後，前 8 名一批），p50／p95，Apple M5；`.sjtl` 的載入時間與載入後的常駐記憶體增量。門檻在 S7c-2 用引擎重播與實機定。
7. **保留集**：**只在第 5 項通過之後**，用通過的那個 `.sjtl`（int8；若使用者改用 float32，就是 f32 檔），fresh verifier 照 `CLAUDE.md` 跑一次（基準與 `--tinylm` 各一次 `--set holdout --rowstats --context`，聊天設定；`evalstats compare` 只回那一行；刪掉兩個 rowstats）。第 5 項沒過就不跑保留集。
8. 研究紀錄、本契約的結果一節、PLAN 的 S7 狀態行；`make test`、CI 全綠（**包含 `shell` job：`make bundle`、`swift test`、selftest**，證明 Accelerate 的連結沒有弄壞殼）；fresh verifier 確認第 1–4 項可以從檔案重現。

## 4. 範圍外、停止條件、預算、限制

- **範圍外**：引擎（`refresh_lm`）、FFI、殼、設定視窗、Release 資產與打包、選字記憶與重排的交互、按鍵延遲門檻（都在 S7c-2）；model-v6 之後重選 α、τ；活化值量化、float16、MLX。
- **停止條件**：第 1 項有任何 golden 改變；第 4 項任何一道關卡不過（n-best 不同、|ΔLL| ≥ 1e-4、pick 不同）——回報，不調整門檻；第 5 項 §4 在 Rust int8 路徑不過——依使用者的規則，**問使用者要不要改用 float32**（f32 檔重跑第 5 項）；動漫守門有「淨值為負而且 p < 0.05」的格——回報，由使用者決定（§6 第 3 項）；錯字回報前 66 列雜湊不符；保留集淨值為負而且 p < 0.05——回報。
- **預算**：executor 實作 1 次＋修正 1 次；main 跑第 3–5 項與 discordtune。
- **限制**：executor 不跑 188、不連網；不讀 `eval/holdout`、`~/side-project/shanjie-private`、`~/.cache/shanjie/work`、學習檔、`~/Library`、鑰匙圈；不安裝、不啟動 App、不在 .app 裡執行；不 push、不開 PR、不碰其他 worktree；`.sjtl` 不進 repo（`eval/golden/tinylm-tiny/` 的隨機小模型除外）。

## 5. 回滾

這一片只加檔案與 CLI 選項，沒有 `--tinylm` 時輸出不變、不出貨；revert 即可。

## 6. S7c-2 要先決定的事（記錄；這一片不做）

1. **延遲設計**：PLAN A3／A7（使用者核定）寫的是「神經驗證非同步、不阻塞按鍵；推論期間按鍵到組字區 p95 仍 < 16 ms；推論用低 QoS、新按鍵就取消」，S7 的目標是模型延遲 p95 ≤ 50 ms 且滿足 A7。照字面就是**非同步**：引擎先顯示 n-gram 的結果，重排算完再換（可能看到字換一次）。另一個選項是同步、把有重排時的每鍵門檻改成 p95 ≤ 50 ms（不會閃，但要改 A3）。這是使用者的決定。
2. **預設與設定**：預設開＋設定視窗開關（建議）；預設開、不給開關；預設關＋開關。
3. **動漫詞包守門不過時**：開詞包時不重排（建議）；另訂詞包規則；照樣出貨並記錄。
4. **選字記憶**：重排不改「n-gram 第一名裡有學過加分的詞」的那一段（建議，S7c-2 的契約寫測試）。
5. **大小**：S7b 的結論出來前用 M；檔案格式與程式不綁大小。

## 7. 結果（2026-10-11）

- 實作 a628142（executor，main 審 diff）：`make test` 全綠，沒有改任何 golden；小模型 golden 的 |ΔLL| 最大 9.2e-6；8 個突變都由斷言抓到。殼加的那一行 Accelerate 是必要的：拿掉時 `swift build` 缺 `_cblas_sgemm`（executor 量）。
- 第 4 項（main）：前 8 名 10 格和 Python 相同（分數最大差 7.11e-15）；LL f32 2.05e-5、int8 1.59e-5；pick 20 格逐位元組相同（a628142）。
- 第 5 項（main）：§4 在 Rust int8 路徑全部通過（discordtune 聊天 +66／4,958，p = 0.0010，區間 [28, 104]，ΔCER −0.23）；動漫 8 格沒有顯著退步。表格在研究紀錄同日「S7c-1」一節。
- 第 6 項：int8 每列重排 p50 7.8–12.7 ms、p95 11.4–25.4 ms（兩次、連續跑、Mac 都有其他負載，數字主要反映負載）；讀檔加解析 int8 10–21 ms、f32 35 ms〔更正：第一次記的 56–59 ms 大多是雜湊，見研究紀錄〕；常駐記憶體增量約 55–70 MiB。
- /code-review 修正（85dd4d3）後第 4、5 項整套重跑：LL f32 1.58e-5、int8 1.43e-5；前 8 名與 pick 照樣全部相同；表格逐字相同。
- 第 7 項（保留集，fresh verifier，一次，聊天，int8）：top1 180 → 200／227，p = 0.0003，CER 2.73% → 1.64%；verifier 判定 CONFIRMED（第 1–4 項可從檔案重現）。

## 8. 審查紀錄

- 第一次 plan-verifier（2026-10-11）：REVISE 4 點，全部 **FIX**。
  1. pick 的關卡比不出「兩邊都錯但選了不同候選」：`--tinylm-scores` 加 `ed`、`glen`，關卡改成 `fusegrid.rowstats`（用 `fusegrid.pick`）和 CLI 的 `--rowstats` 逐位元組相同；「`fusegrid.py` 直接讀」縮小成 `pick`／`rowstats`。
  2. `.sjtl` 記的 n-gram 雜湊沒有人檢查、KN 側檔會自動開：`--tinylm` 在雜湊不符或 KN 開著時拒絕；加 CLI 測試（第 2 項）。
  3. 保留集的時機與檔案沒有綁到判斷：只在第 5 項通過後、用通過的那個檔跑一次。
  4. Accelerate 不會跟著 staticlib，殼的連結可能壞：允許 `macos/Package.swift` 加一行 `.linkedFramework("Accelerate")`；第 8 項明列 CI 的 `shell` job。
- 第二次 plan-verifier（2026-10-11）：前一次 4 點都確認已處理；REVISE 1 點，逐項處置，**FIX**（之後再一次收尾審查）。Rust 的 `context_key` 在前文沒有漢字結尾時回哨兵 `"^"`，Python（S7a 打分用的）回 `""`：照原寫法這些列的 k 會變成 UNK、LL 和 S7a 不同。§2.3 寫明哨兵對應空的 k，第 2 項加一個會抓到這件事的 CLI 測試。
- 收尾審查（2026-10-11）：READY。
