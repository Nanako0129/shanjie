# 契約：口語項進核心，常談隨 App 出貨（colloquial-term）

狀態：修訂一（2026-10-20，plan-verifier 第一次 REVISE 的 8 項照改，§8）。

## 0. 起因、要做到什麼、怎麼看到

- **起因**：P4d（`experiments/p4d-colloquial/README.md`）在 chatjudge-2 確認通過：108／39，淨值 +69，p 1.1×10⁻⁸，改壞÷改對 0.36。照登記開產品片：口語項進核心，口語表隨 App 出貨。
- **要做到**：聊天設定、語意校正開著、強度「標準」、載入的語意校正模型就是 P4d 量的那一份時，核心的融合多一項口語分數，選字和 P4d 那一格（μ 0.5、α 0.5、軟閘門 T 1.0、δ 0.25、τ 2）逐列相同。其他情況（書面、語意校正關、保守與積極強度、別的語意校正模型、沒有口語表）行為逐位元組不變。
- **怎麼看到**：
  1. CLI 用核心的新路徑跑公開集合，每列選的候選和 `p4d.py --cell 0.5,0.5,1.0` 的離線選字相同；雲端跑 chatclause 與 discordtune，改對／改壞等於 P4d 的 113／37 與 74／43。
  2. 維護者在自己的 Mac 用聊天 App 打 dev302 第 202 列「桌腳的螺絲鬆掉了」（讀音照 `eval/bench/suite-v1/dev302.txt`）：出貨版與「只調 α」的對照都選「羅斯松」，新版選「螺絲鬆」（P4d 的公開重算：dev302 只有口語項才改對的 3 列之一，另外兩列是第 218、223 列）。

## 1. 已經量到的

- P4d 的離線評分 `experiments/p4d-colloquial/p4d.py`，以及 P4b 的 Rust 實作 `cli/src/colloquial.rs`：lc、k、讀詞表的規則都和 `experiments/p4-colloquial/p4.py` 逐字相同，P4b 的四個錨點對上。
- 把 n-gram 第一名固定當錨點，在 n-gram 分差 ≥ τ 的列不會少任何改對（P4d 每個集合都是 0），所以口語項只放在重排裡就夠，不動解碼器。
- P4b 量過 CLI 帶口語表時 RSS +9.2 MiB（只有表，不含 k 的詞集合）、每列多 0.17 ms（下限，連續跑）。

## 2. 設計

### 2.1 常談（口語表，資料）

- 對外名稱「**常談**」，代號 SJ-CL，規模用詞彙數：第一版 `sj-cl-1-68k`＝常談 SJ-CL-1-68K（維護者 2026-10-20 選定）。`docs/releases/README.md` 的模型名稱表加一列。
- 檔案 `data/lm/colloquial.sjlm`，內容就是 P4 的 T2（SHA-256 `2984eeb5…`，V 68,055）：出貨模型 model-v6 建置時的口語計數（`1e7a15ef…`）加上 Phase 2 的公開口語池（`19fdc474…`，143,895 句）的計數，`tools/build_lm.py --corpora`（P4 加的選項）建，剪枝 2、折扣 0.75，不用詞類、不用 KN。建置指令寫進 `docs/releases/sj-cl-1-68k.md`；repo 只放 `data/colloquial.sjlm.sha256`，檔案走資料 Release（手動流程照 `docs/releases/README.md`，建立前問維護者）。
- **授權**：檔案以 **CC BY-SA 4.0** 發佈（和其他模型檔相同；model-v6 的口語計數本來就以 CC BY-SA 4.0 隨模型出貨，口語池四個來源 ODC-BY、CC BY 3.0、CC BY 2.0 FR、CC BY 4.0 都只要求署名，可以併入 BY-SA）。
  - **App**：`scripts/build-app.sh` 產生的模型署名檔（現在逐一列出每個模型檔的授權與來源的那一份）加 `colloquial.sjlm` 一段：授權名稱、model-v6 口語計數的來源（沿用現有說明）、口語池四個來源的名稱與授權連結；`Resources/LICENSES/` 另放 `colloquial-attribution.tsv`。repo 的 `LICENSES/data.md` 同步。`scripts/check-app.sh` 加一條 grep：署名檔裡有 `colloquial.sjlm` 那一段，`colloquial-attribution.tsv` 存在且不是空的。
  - **資料 Release**：附同樣的 NOTICE 與 `colloquial-attribution.tsv`。
  - `colloquial-attribution.tsv` 由一支新腳本（`tools/build_attribution.py`）從口語池 `pool.tsv` 的來源欄位產生，每個來源單位一列（YODAS 影片 id 與頻道、Tatoeba 句子 id 與作者、WildChat 與 MASSIVE 寫資料集出處與授權）；列數要等於同一支腳本從 `pool.tsv` 算出的不同來源單位數，並和 Phase 2 的 manifest 記的各來源句數對照（研究紀錄 2026-10-18「Phase 2 公開句子池」：WildChat 3,491、YODAS 90,080、Tatoeba 36,839、MASSIVE 13,485 句）。來源單位的資料不完整（例如缺頻道或作者）＝停止條件。

### 2.2 核心

- 新模組 `core/src/colloquial.rs`：`cli/src/colloquial.rs` 的 lc、k、`read_sets` 搬進核心（`Colloquial`），CLI 刪掉自己那一份。
  - `Colloquial::load(table, lexicon_dir, packs_dir, acg) -> Result<Colloquial, ColloquialError>`：表用 `Lm::load_opts(path, false, false, false)`（純雙連詞）；兩個詞集合照 `read_sets` 的規則重讀詞庫檔（基本詞表、疊加層，acg 詞包開著才算進疊加層），成本照 §3 第 5 點量。
  - `Colloquial::col(words) = lc(words) − 0.5·k(words)`（lc 從 `<s>` 加到 `</s>`）。
- **共用的評分函式**（引擎與 CLI 都只呼叫這一個）：`TinyLm::rerank_colloquial(&self, ng: &[f64], cands: &[&str], words: &[Vec<&str>], key: &str, co: &Colloquial) -> Result<(usize, Option<Vec<f64>>), TinyLmError>`，和 `rerank` 同樣取前 `TOPK` 個、同樣的 `ll_many`，之後：
  - col_i = `co.col(words[i])`，g_i = 1 / (1 + exp(−(ll_i − ll_0) / T))（不會溢位的寫法），ll_eff_i = ll_i + g_i·(μ / α)·(col_i − col_0)；
  - `pick(ng, ll_eff, α, τ, δ)`，`pick` 不改。
  - 參數全部是 `colloquial.rs` 的具名常數，註解寫出處（P4d 登記、chatjudge-2 確認）：`MU = 0.5`、`GATE_T = 1.0`、`GAMMA_PER_MU = 0.5`、`ALPHA = 0.5`、`TAU = 2.0`、`DELTA = 0.25`。不讀 `.sjtl` 檔頭的值。
- **何時用**（`engine.rs` 的 `refresh_lm` 建工作時決定，記在 `RerankJob` 裡；之後 `RerankJob::run` 對每一段呼叫 `rerank_colloquial` 或原本的 `rerank`）：口語表已載入、設定是聊天、強度是標準、而且載入的 `.sjtl` 的 SHA-256 等於常數 `PAIRED_TINYLM_SHA256`（P4d 量的那一份，即目前 `data/tinylm.sjtl.sha256` 記的值）。四個條件任一不成立 → 原本的 `rerank` 與檔頭的參數，行為不變。`load_tinylm` 時把 `.sjtl` 的 SHA-256 記在 `TinyLm` 裡（核心已有 `sha256_hex`）。
- **段與列**：引擎的一段（兩個固定詞或標點之間）當一句算 lc（`<s>` 起、`</s>` 止）。CLI 與 `p4d.py` 一列就是一段，所以兩者相同；多段組字的差異是已知的，寫進研究紀錄，驗收的公開集合都是一段。
- **載入與生命週期**：`Engine::load_colloquial(path)` 從引擎載入詞庫的同一個資料夾讀詞集合；可以在 `load_lm`／`load_tinylm` 之前或之後呼叫（表和 n-gram、語意校正模型無關；配對在每次建工作時檢查，換語意校正模型、換設定或強度，下一次解碼就生效）。失敗時引擎維持原狀（原本沒有表就仍然沒有；原本有表就保留原本那一份），作廢進行中的工作。
- **FFI**：`shanjie_engine_load_colloquial(engine, path)`，回傳碼和 `load_tinylm` 相同（0 成功、1 參數 NULL、3 讀取或格式錯誤），子程序輸出不帶路徑。`core/include/shanjie.h`、`core/tests/c/abi_smoke.c`、`docs/contracts/s3a.md` 的 FFI 表一起改；按鍵規則表不變。

### 2.3 App、CLI、重播

- **App**：殼的 `build()` 在 `load_tinylm` 之後呼叫 `load_colloquial`，檔案是 bundle 的 `Resources/colloquial.sjlm`（和其他模型檔同一層）。失敗只記靜態 log，照沒有口語表的方式重排。沒有新的設定開關（跟著語意校正開關與強度走）。關於頁的模型列表加「常談 SJ-CL-1-68K」。
- **selftest**：`--selftest` 加一步：`load_colloquial` 回傳 0，而且在聊天、標準強度下打 dev302 第 202 列的讀音，第一次顯示（套用重排之後）是「桌腳的螺絲鬆掉了」。bundle 裡沒有 `colloquial.sjlm` 或它沒有作用，selftest 失敗，`check-app.sh`（會執行 selftest）跟著失敗。
- **打包**：`scripts/build-app.sh` 檢查 `data/lm/colloquial.sjlm` 存在、雜湊和 `data/colloquial.sjlm.sha256` 相同，複製進 `Resources/`；`check-app.sh` 檢查它在 bundle 裡、雜湊相同。
- **CLI**：新選項 `--colloquial-core FILE`。它讓 CLI 和引擎一樣載入 `Colloquial`，每列照引擎的四個條件決定用 `rerank_colloquial` 或 `rerank`；不拒絕任何設定（書面、別的強度、沒有 `--tinylm` 時就照條件不加口語項），這樣「其他地方不變」才能用同一個選項驗。檔案不存在 → 開始前就報錯。摘要行的設定標記加 `+colloq:<表的 sha256 前 8 碼>`。`--set holdout` 照舊允許（出貨之後保留集要用出貨設定跑，出貨設定會帶這個選項）。
  - P4b 的 `--colloquial`、`--colloquial-mu`、`--colloquial-gamma` **移除**（P4b 的結果已寫在研究紀錄，要重現就 checkout 當時的 commit）。
- **`p4d.py`** 加 `--out-picks FILE`：每列一行選中的候選編號（0 起，前 8 名內），供 §3 第 2 點比對。
- **重播**：`shanjie-replay` 加 `--colloquial FILE`（照 App，在 `load_tinylm` 之後載入）；摘要行標記加 `+colloq:<sha8>`。不帶這個選項的輸出和現在逐位元組相同。

## 3. 驗收

1. **核心單元測試**（每條規則一個「規則壞掉就會失敗」的測試）：口語項是相對錨點的差；閘門用 ll_0；不會溢位；只在聊天、標準強度、配對的語意校正模型（換一份 `.sjtl` 就沒有口語項）；沒有表時逐位元組不變；載入失敗引擎不變、原本的表保留；k 只算疊加層與詞包、而且不在基本詞表的詞。突變：拿掉相對錨點、閘門改用 ll_i、α 不換成 0.5、拿掉配對檢查，各要讓測試失敗。
2. **和離線逐列相同**：
   ```
   shanjie-eval --lm data/lm/bigram.sjlm --profile chat --context --packs acg --tinylm data/lm/tinylm.sjtl \
     --colloquial-core data/lm/colloquial.sjlm --rows <集合> --dump D --dump-words W --rowstats R --tinylm-scores S
   python3 experiments/p4d-colloquial/p4d.py --name <集合名> --rows <集合> --dump D0 --dump-words W0 --rowstats R0 \
     --tinylm-scores S0 --t2 data/lm/colloquial.sjlm --cell 0.5,0.5,1.0 --out-picks P --out-rowstats RC --out O.json
   ```
   （D0／W0／R0／S0 是不帶 `--colloquial-core` 的出貨設定那一次）。CLI 的 `--tinylm-scores` 每列的 `c` 欄要等於 `P` 的那一列，CLI 的 `R` 要和 `RC` 逐位元組相同。集合：dev302、打字測驗、錯字回報、cvtune-clean（Mac 可跑公開的三份；cvtune-clean 與下面兩份在雲端）。雲端另跑 chatclause 與 discordtune，CLI 對出貨的改對／改壞要等於 113／37 與 74／43。差異列要歸因，不能歸因就停。
3. **其他地方不變**（`top1_sha256` 都要和 main 不帶口語表的出貨 CLI 相同；集合 dev302、打字測驗、錯字回報）：

   | 設定 | 口語表 | 工具 |
   |---|---|---|
   | 書面 | 有（`--colloquial-core`） | CLI |
   | 聊天、保守強度（`--rerank-level conservative`） | 有 | CLI |
   | 聊天、積極強度 | 有 | CLI |
   | 聊天、沒有 `--tinylm`（語意校正關） | 有 | CLI |
   | 聊天、標準、換一份別的 `.sjtl`（測試用、雜湊不同） | 有 | CLI（只看「和同一份 `.sjtl` 不帶口語表」相同） |
   | 聊天、標準 | 沒有 | CLI 與重播（重播不帶 `--colloquial`，`--learn off --rerank each`，和 main 的重播相同） |

   golden 不變。
4. **重播一致**：`shanjie-replay --profile chat --learn off --rerank each --colloquial data/lm/colloquial.sjlm` 在 dev302、打字測驗、錯字回報的第一次顯示，和 §3 第 2 點 CLI 新路徑的選字逐列相同（engine-replay §6 第 1 點的做法：先比 `top1_sha256`，不同再逐列歸因）。
5. **速度與記憶體**（每一項都是「有口語表」對「沒有口語表」，同一台機器）：
   - 雲端 VM 或 Windows 機器，`shanjie-replay --rerank each`（單執行緒，dev302，聊天）：用 `/usr/bin/time -v` 量尖峰 RSS 與總時間；尖峰 RSS 增加 ≤ 40 MiB。
   - 同一台，CLI `--threads 1` 的重排時間（摘要的延遲行）p95 增加 ≤ 2 ms。
   - 同一台，引擎建立到可以打字的時間（重播在第一列之前的載入時間，印在 stderr）增加 ≤ 300 ms。
   - 維護者的 Mac 一次：裝好新版，用「活動監視器」看善解的記憶體，和舊版相比增加 ≤ 40 MiB（15 秒級）。
   超過任一項＝停止條件（先找原因，例如 k 的詞集合改從引擎已載入的詞庫取）。
6. **授權**：`check-app.sh` 的 grep 通過；`colloquial-attribution.tsv` 的列數等於 `tools/build_attribution.py` 從 `pool.tsv` 算出的來源單位數，各來源句數和 manifest 相同。
7. **實機**（維護者，15 秒級）：§0 第 2 點那一句選「螺絲鬆」；關於頁看得到「常談 SJ-CL-1-68K」。
8. `/code-review` ＋ fresh verifier ＋ CI。

## 4. 發佈與回退

- 資料 Release `sj-cl-1-68k`（建立前問維護者），說明檔 `docs/releases/sj-cl-1-68k.md` 先進 main。
- 跟在 v0.5.0 與 canary v0.5.1 之後的版本出（v0.5.1 只放小改動）。發版說明寫：聊天設定的語意校正多參考口語用字的常談（SJ-CL-1-68K）；chatjudge-2 淨值 +69。
- 判讀集的數字已經在 P4d 量過（chatjudge-2 已用掉），這一片不再判讀模型效果；它的品質保證就是 §3 第 2 點的逐列相同。
- **回退**：發佈前 revert 這個 PR。發佈後，下一版從 bundle 拿掉 `colloquial.sjlm`（核心照沒有口語表的方式重排）；資料 Release 不刪。

## 5. 預算、停止條件、範圍外

- **預算**：`pilotfish:executor` 實作 1 次＋修正 1 次；雲端 VM ≤ 1.5 小時（所有集合同一台、同一種 CPU；私人集合的傾印只在 VM 上、跑完刪掉，帶回的只有數字）。
- **停止條件**：CLI 與離線有不能歸因的差異；口語池任一來源的署名資料不完整；§3 第 5 點任一項超過門檻；需要改 `pick` 或解碼器才能做到逐列相同；署名或授權的推論被證明不成立。
- **範圍外**：書面設定、保守與積極強度的口語項（沒量）；重新選 μ／T／α；跨段的 lc；App 計數器；設定頁的開關。

## 8. 審查紀錄

- plan-verifier 第一次（`898495b8`）REVISE 8 項，照改：共用的核心函式與 CLI 選項釘死、P4b 選項移除、`p4d.py --out-picks`（§2.2、§2.3、§3.2）；重播載入口語表與比對對象（§2.3、§3.4）；「其他地方不變」逐項標出有沒有口語表與工具（§3.3）；參數全部是常數、和語意校正模型的配對規則與測試（§2.2、§3.1）；bundle 路徑、selftest 的一步（§2.3）；檔案授權、App 與資料 Release 兩邊的署名、`check-app.sh` 的檢查、來源單位數的出處（§2.1、§3.6）；量法的工具、機器、基準與門檻（§3.5）；回退與預算（§4、§5）。名稱「常談」由維護者同日選定；驗收句由維護者同日指正改成「桌腳的螺絲鬆掉了」（「大夥」台灣很少人說）。
