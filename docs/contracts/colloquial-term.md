# 契約：口語項進核心，常談隨 App 出貨（colloquial-term）

狀態：修訂三（2026-10-20，收尾審查 REVISE 的 2 項照改，§8）；照流程收尾審查之後由維護者決定。〔修訂四（2026-10-22）：常談與讀音通道項在保守、積極也生效，各用自己的 (α, τ, δ)，見 `docs/contracts/colloquial-levels.md`；下文原本寫「只在標準強度」的條件，以那份契約為準，這裡逐處改成三檔，標準那一檔的值不變。〕

## 0. 起因、要做到什麼、怎麼看到

- **起因**：P4d（`experiments/p4d-colloquial/README.md`）在 chatjudge-2 確認通過：108／39，淨值 +69，p 1.1×10⁻⁸，改壞÷改對 0.36。照登記開產品片：口語項進核心，口語表隨 App 出貨。
- **要做到**：聊天設定、語意校正開著、載入的語意校正模型就是 P4d 量的那一份時，核心的融合多一項口語分數；標準強度的選字和 P4d 那一格（μ 0.5、α 0.5、軟閘門 T 1.0、δ 0.25、τ 2）逐列相同，保守與積極用自己的 (α, τ, δ)（修訂四，`colloquial-levels.md`）。其他情況（書面、語意校正關、別的語意校正模型、沒有口語表）行為逐位元組不變。
- **怎麼看到**：
  1. CLI 用核心的新路徑跑公開集合，每列選的候選和 `p4d.py --cell 0.5,0.5,1.0` 的離線選字相同；雲端跑 chatclause 與 discordtune，改對／改壞等於 P4d 的 113／37 與 74／43。
  2. 維護者在自己的 Mac 用聊天 App，在同一個輸入框先打「桌子一直在搖」（**不送出**，游標停在「搖」後面；殼只讀游標前面的字當前文），接著直接打 dev302 第 202 列「桌腳的螺絲鬆掉了」（讀音照 `eval/bench/suite-v1/dev302.txt`；前文就是那一列的前文欄，P4d 的量測是帶前文 `--context` 跑的）：出貨版與「只調 α」的對照都選「羅斯松」，新版選「螺絲鬆」（P4d 的公開重算：dev302 只有口語項才改對的 3 列之一，另外兩列是第 218、223 列）。

## 1. 已經量到的

- P4d 的離線評分 `experiments/p4d-colloquial/p4d.py`，以及 P4b 的 Rust 實作 `cli/src/colloquial.rs`：lc、k、讀詞表的規則都和 `experiments/p4-colloquial/p4.py` 逐字相同，P4b 的四個錨點對上。
- 把 n-gram 第一名固定當錨點，在 n-gram 分差 ≥ τ 的列不會少任何改對（P4d 每個集合都是 0），所以口語項只放在重排裡就夠，不動解碼器。
- P4b 量過 CLI 帶口語表時 RSS +9.2 MiB（只有表，不含 k 的詞集合）、每列多 0.17 ms（下限，連續跑）。

## 2. 設計

### 2.1 常談（口語表，資料）

- 對外名稱「**常談**」，代號 SJ-CL，規模用詞彙數：第一版 `sj-cl-1-68k`＝常談 SJ-CL-1-68K（維護者 2026-10-20 選定）。`docs/releases/README.md` 的模型名稱表加一列。
- 檔案 `data/lm/colloquial.sjlm`，內容就是 P4 的 T2（SHA-256 `2984eeb5…`，V 68,055）：出貨模型 model-v6 建置時的口語計數（`1e7a15ef…`）加上 Phase 2 的公開口語池（`19fdc474…`，143,895 句）的計數，`tools/build_lm.py --corpora`（P4 加的選項）建，剪枝 2、折扣 0.75，不用詞類、不用 KN。建置指令寫進 `docs/releases/sj-cl-1-68k.md`；repo 只放 `data/colloquial.sjlm.sha256`，檔案走資料 Release（手動流程照 `docs/releases/README.md`，建立前問維護者）。
- **授權**：檔案以 **CC BY-SA 4.0** 發佈（和其他模型檔相同；model-v6 的口語計數本來就以 CC BY-SA 4.0 隨模型出貨，口語池四個來源 ODC-BY、CC BY 3.0、CC BY 2.0 FR、CC BY 4.0 都只要求署名，可以併入 BY-SA）。
  - **App**：`scripts/build-app.sh` 產生的模型署名檔（現在逐一列出每個模型檔的授權與來源的那一份）加 `colloquial.sjlm` 一段：授權名稱、model-v6 口語計數的來源（沿用現有說明）、口語池四個來源的名稱與授權連結；`Resources/LICENSES/` 另放 `colloquial-attribution.tsv`。repo 的 `LICENSES/data.md` 同步。`scripts/check-app.sh` 檢查 bundle 裡的 `colloquial.sjlm` 與 `colloquial-attribution.tsv` 存在、不是空的，雜湊和 `data/*.sha256` 相同（2026-10-21 實作修訂：原本寫的 grep 檢查的是 build-app 自己寫進去的文字，不可能失敗，審查後拿掉）。
  - **資料 Release**：附同樣的 NOTICE 與 `colloquial-attribution.tsv`。`colloquial-attribution.tsv` 只有來源 id、名稱與句數，不含語料文字；它是資料 Release `sj-cl-1-68k` 的附件，SHA-256 記在 `data/colloquial-attribution.tsv.sha256`，`build-app.sh` 從 `data/lm/` 複製進 `Resources/LICENSES/`（和模型檔一樣：存在而且雜湊相符才組建）。
  - `colloquial-attribution.tsv` 由一支新腳本（`tools/build_attribution.py`）從口語池 `pool.tsv` 的來源欄位產生，每個來源單位一列（YODAS 影片 id 與影片網址、Tatoeba 句子 id 與作者、WildChat 與 MASSIVE 寫資料集出處與授權），並有一欄這個單位涵蓋的句數；各來源的句數加總要等於研究紀錄 2026-10-18「Phase 2 公開句子池」記的 WildChat 3,491、YODAS 90,080、Tatoeba 36,839、MASSIVE 13,485 句（這幾個數字在 repo 裡，不是腳本自己算的）。`pool.tsv` 的完整 SHA-256 寫進 `docs/releases/sj-cl-1-68k.md`。YODAS 只能照資料集提供的內容署名：資料集本身只給影片 id，頻道名稱查不回來（oEmbed 抽 5 支都是 404／400），CC BY 3.0 §4(b) 的作者名稱是「有提供的話」；維護者 2026-10-20 選定照資料集提供的內容署名（資料集名稱、CC BY 3.0、每支影片的 id、網址與句數）。缺來源單位 id、或 Tatoeba 缺作者＝停止條件。

### 2.2 核心

- 新模組 `core/src/colloquial.rs`：`cli/src/colloquial.rs` 的 lc、k、`read_sets` 搬進核心（`Colloquial`），CLI 刪掉自己那一份。
  - `Colloquial::load(table, base_lexicon_text, overlay_text, pack_text: Option<&str>) -> Result<Colloquial, ColloquialError>`：表用 `Lm::load_opts(path, false, false, false)`（純雙連詞）；基本詞表來自 `mcbpmf-data.txt`，疊加層是 `overlay-add.tsv` **加上引擎建立時讀的詞包內容**（`new_with_packs` 讀進來、引擎留著的 `pack_text`；沒有詞包建的引擎就沒有詞包的列），規則同 `read_sets`（基本詞表三欄、跳過 `#`／`_`、每音節一字；疊加層四欄）。CLI 用同一個函式，給它 `--packs acg` 讀的同一份詞包文字。成本照 §3 第 5 點量。
  - `Colloquial::load_from_dir(table, lexicon_dir, pack_text: Option<&str>)`：從詞庫資料夾讀 `mcbpmf-data.txt` 與 `overlay-add.tsv` 再呼叫 `load`（讀不到＝`ColloquialError::Read`）。`Engine::load_colloquial` 與 CLI 的 `--colloquial-core` 都只呼叫它，k 的詞集合只有這一份讀法。
  - `Colloquial::col(words) = lc(words) − 0.5·k(words)`（lc 從 `<s>` 加到 `</s>`）。
- **共用的評分函式**（引擎與 CLI 都只呼叫這一個）：`TinyLm::rerank_colloquial(&self, ng: &[f64], cands: &[&str], words: &[Vec<&str>], key: &str, co: &Colloquial) -> Result<(usize, Option<Vec<f64>>), TinyLmError>`，和 `rerank` 同樣取前 `TOPK` 個、同樣的 `ll_many`，之後：
  - col_i = `co.col(words[i])`，g_i = 1 / (1 + exp(−(ll_i − ll_0) / T))（不會溢位的寫法），ll_eff_i = ll_i + g_i·(μ / α)·(col_i − col_0)；
  - `pick(ng, ll_eff, α, τ, δ)`，`pick` 不改。
  - 任何一個 col_i 不是有限值（`inf`、NaN）時，這一次呼叫整個不加口語項：用同一批 ll 與檔頭聊天、同一強度的 (α, τ, δ) 做原本的 `pick`（`colloquial::guarded_pick`；修訂四前只有標準強度），不讓無限大或 NaN 決定選字。
  - 參數全部是 `colloquial.rs` 的具名常數，註解寫出處（P4d 登記、chatjudge-2 確認）：`MU = 0.5`、`GATE_T = 1.0`、`GAMMA_PER_MU = 0.5`，以及每一檔的 (α, τ, δ)：標準 `STANDARD = (0.5, 2.0, 0.25)`（修訂四前是 `ALPHA`、`TAU`、`DELTA`），保守與積極見 `colloquial-levels.md`，由 `colloquial::cell(level)` 取用。不讀 `.sjtl` 檔頭的值。
- **何時用**（`engine.rs` 的 `refresh_lm` 建工作時決定，記在 `RerankJob` 裡；之後 `RerankJob::run` 對每一段呼叫 `rerank_colloquial` 或原本的 `rerank`）：口語表已載入、設定是聊天（任何強度，修訂四前只有標準）、而且載入的 `.sjtl` 的 SHA-256 等於常數 `PAIRED_TINYLM_SHA256`（P4d 量的那一份，即目前 `data/tinylm.sjtl.sha256` 記的值）。三個條件任一不成立（修訂四前還有「強度是標準」）→ 原本的 `rerank` 與檔頭的參數，行為不變。決定只有一個函式：`colloquial::applies(profile, &TinyLm)`（聊天與配對的小模型兩個條件；修訂四前是 `applies(profile, level, &TinyLm)`，多一個「強度是標準」；另一個條件是「有表」，由呼叫端判斷），引擎每次建工作與 CLI 都呼叫它。`.sjtl` 的 SHA-256（`TinyLm::file_sha256`）是 `load_paired` 解析的那一份位元組的雜湊，在 `load_paired` 裡當場算、字串記在 `TinyLm`、位元組隨即釋放（不讓 14.6 MB 的原始位元組在載入口語表時還活著）；量過：release 版、真的 tinylm.sjtl（14,638,352 bytes）、7 次，核心的軟體 SHA-256 35 到 52 ms（中位數約 37 ms），`parse` 5 到 9 ms。
- **段與列**：引擎的一段（兩個固定詞或標點之間）當一句算 lc（`<s>` 起、`</s>` 止）。CLI 與 `p4d.py` 一列就是一段，所以兩者相同；多段組字的差異是已知的，寫進研究紀錄，驗收的公開集合都是一段。
- **載入與生命週期**：`Engine::load_colloquial(path)` 從引擎載入詞庫的資料夾讀 `mcbpmf-data.txt` 與 `overlay-add.tsv`，詞包用引擎自己留著的 `pack_text`（不從詞庫資料夾找 `acg-add.tsv`）；可以在 `load_lm`／`load_tinylm` 之前或之後呼叫（表和 n-gram、語意校正模型無關；配對在每次建工作時檢查，換語意校正模型、換設定或強度，下一次解碼就生效）。失敗時引擎維持原狀（原本沒有表就仍然沒有；原本有表就保留原本那一份），作廢進行中的工作。
- **FFI**：`shanjie_engine_load_colloquial(engine, path)`，回傳碼和 `load_tinylm` 相同（0 成功、1 參數 NULL、3 讀取或格式錯誤），子程序輸出不帶路徑。`core/include/shanjie.h`、`core/tests/c/abi_smoke.c`、`docs/contracts/s3a.md` 的 FFI 表一起改；按鍵規則表不變。

### 2.3 App、CLI、重播

- **App**：殼的 `build()` 在 `load_tinylm` 之後、而且小模型載入成功時才呼叫 `load_colloquial`，檔案是 bundle 的 `Resources/colloquial.sjlm`（和其他模型檔同一層）。失敗只記靜態 log，照沒有口語表的方式重排。沒有新的設定開關（跟著語意校正開關與強度走）。關於頁不加東西（現在沒有模型列表）。
- **selftest**：`--selftest` 加一步：`load_colloquial` 回傳 0，而且在聊天、標準強度下先 `set_left_context("桌子一直在搖")`（第 202 列的前文欄），再打第 202 列的讀音，第一次顯示（套用重排之後）是「桌腳的螺絲鬆掉了」。實作時先用 CLI 在同一個條件（`--context`、這一列）量一次：出貨與「只調 α」是「羅斯松」、新路徑是「螺絲鬆」，數字寫進 PR；不成立就停（換句子要回頭問維護者）。bundle 裡沒有 `colloquial.sjlm` 或它沒有作用，selftest 失敗，`check-app.sh`（會執行 selftest）跟著失敗。
- **打包**：`scripts/build-app.sh` 檢查 `data/lm/colloquial.sjlm` 存在、雜湊和 `data/colloquial.sjlm.sha256` 相同，複製進 `Resources/`；`check-app.sh` 檢查它在 bundle 裡、雜湊相同。
- **CLI**：新選項 `--colloquial-core FILE`。它讓 CLI 和引擎一樣載入 `Colloquial`，每列照引擎的條件（修訂四前四個，現在沒有強度這一項）決定用 `rerank_colloquial` 或 `rerank`；不拒絕任何設定（書面、沒有 `--tinylm` 時就照條件不加口語項），這樣「其他地方不變」才能用同一個選項驗。檔案不存在 → 開始前就報錯。摘要行的設定標記加 `+colloq:<表的 sha256 前 8 碼>`。`--set holdout` 照舊允許（出貨之後保留集要用出貨設定跑，出貨設定會帶這個選項）。
  - P4b 的 `--colloquial`、`--colloquial-mu`、`--colloquial-gamma` **移除**（P4b 的結果已寫在研究紀錄，要重現就 checkout 當時的 commit）。
- **`p4d.py`** 加 `--out-picks FILE`：每列一行選中的候選編號（0 起，前 8 名內），供 §3 第 2 點比對。
- **重播**：`shanjie-replay` 照 App，小模型開著且 `data/lm/colloquial.sjlm` 存在時預設在 `load_tinylm` 之後載入口語表（詞包照重播的 `build_engine`，acg 開）；`--colloquial FILE` 改載另一份、`--no-colloquial` 不載；摘要行標記加 `+colloq:<sha8>`。`--no-colloquial` 的輸出和加這個功能以前逐位元組相同。`tools/kpi.py replay` 跟著 `data/*.sha256` 有沒有口語表決定要不要帶 `--no-colloquial`、標記該不該有 `+colloq:`。
- **CI 與發版流程**：`.github/workflows/ci.yml` 每一個下載 `tinylm.sjtl` 的 job、`.github/workflows/release.yml` 的 build，都加 `gh release download sj-cl-1-68k -p colloquial.sjlm -p colloquial-attribution.tsv -D data/lm` 與 `shasum -a 256 -c data/colloquial.sjlm.sha256 data/colloquial-attribution.tsv.sha256`。資料 Release `sj-cl-1-68k` 要在實作 PR 第一次跑 CI 之前建好（建立前問維護者）。

## 3. 驗收

1. **核心單元測試**（每條規則一個「規則壞掉就會失敗」的測試）：口語項是相對錨點的差；閘門用 ll_0；不會溢位；只在聊天、配對的語意校正模型（換一份 `.sjtl` 就沒有口語項；修訂四起三種強度都用，各用自己的格）；沒有表時逐位元組不變；載入失敗引擎不變、原本的表保留；k 只算疊加層與詞包、而且不在基本詞表的詞；一個只在 acg 詞包裡的詞，引擎用 `PACK_ACG` 建時算進 k、沒有詞包建時不算。突變：拿掉相對錨點、閘門改用 ll_i、α 不換成 0.5、拿掉配對檢查，各要讓測試失敗。
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
   | ~~聊天、保守強度（`--rerank-level conservative`）~~ | 有 | 修訂四起這兩列不再是「不變」：保守、積極也有口語項，驗收改為 `colloquial-levels.md` §2.2 的逐列比對；「沒有口語表」的保守、積極仍和 main 相同 |
   | ~~聊天、積極強度~~ | 有 | （同上） |
   | 聊天、沒有 `--tinylm`（語意校正關） | 有 | CLI |
   | 聊天、標準、換一份別的 `.sjtl`（測試用、雜湊不同） | 有 | CLI（只看「和同一份 `.sjtl` 不帶口語表」相同） |
   | 聊天、標準 | 沒有 | CLI 與重播（重播帶 `--no-colloquial`，`--learn off --rerank each`，和 main 的重播相同） |

   golden 不變。
4. **重播一致**：`shanjie-replay --profile chat --learn off --rerank each`（預設就載入 `data/lm/colloquial.sjlm`；明給 `--colloquial data/lm/colloquial.sjlm` 一樣）在 dev302、打字測驗、錯字回報的第一次顯示，和 §3 第 2 點 CLI 新路徑的選字逐列相同（engine-replay §6 第 1 點的做法：先比 `top1_sha256`，不同再逐列歸因）。
5. **速度與記憶體**（每一項都是「有口語表」對「沒有口語表」，同一台機器）：
   - 雲端 VM 或 Windows 機器，`shanjie-replay --rerank each`（單執行緒，dev302，聊天）：用 `/usr/bin/time -v` 量尖峰 RSS 與總時間；尖峰 RSS 增加 ≤ 40 MiB。
   - 同一台，CLI `--threads 1` 的重排時間（摘要的延遲行）p95 增加 ≤ 2 ms。
   - 同一台，載入時間：`/usr/bin/time -v shanjie-replay --rows <只有 dev302 第 1 列的列檔> …`，有（預設）與 `--no-colloquial` 各跑 3 次取中位數，總時間增加 ≤ 300 ms（一列的打字時間可以忽略，差就是載入的差）。
   - 維護者的 Mac 一次：裝好新版，用「活動監視器」看善解的記憶體，和舊版相比增加 ≤ 40 MiB（15 秒級）。
   超過任一項＝停止條件（先找原因，例如 k 的詞集合改從引擎已載入的詞庫取）。
6. **授權**：`check-app.sh` 的存在與雜湊檢查通過（§2.1）；`colloquial-attribution.tsv` 每列有一欄「涵蓋的句數」，依來源加總要**正好等於**研究紀錄記的 WildChat 3,491、YODAS 90,080、Tatoeba 36,839、MASSIVE 13,485（合計 143,895）；`tools/build_attribution.py` 遇到任何一列沒有來源單位 id（影片 id、句子 id 等）就以非 0 結束，單元測試涵蓋這兩條。
7. **實機**（維護者，15 秒級）：照 §0 第 2 點，先打「桌子一直在搖」不送出，接著打那一句，選「螺絲鬆」。
8. `/code-review` ＋ fresh verifier ＋ CI。

## 4. 發佈與回退

- 資料 Release `sj-cl-1-68k`（建立前問維護者），說明檔 `docs/releases/sj-cl-1-68k.md` 先進 main。
- 跟在 v0.5.0 與 canary v0.5.1 之後的版本出（v0.5.1 只放小改動）。發版說明寫：聊天設定的語意校正多參考口語用字的常談（SJ-CL-1-68K）；chatjudge-2 淨值 +69。
- 判讀集的數字已經在 P4d 量過（chatjudge-2 已用掉），這一片不再判讀模型效果；它的品質保證就是 §3 第 2 點的逐列相同。
- **回退**：發佈前 revert 這個 PR。發佈後，下一版從 bundle 拿掉 `colloquial.sjlm`（核心照沒有口語表的方式重排）；資料 Release 不刪。

## 5. 預算、停止條件、範圍外

- **預算**：`pilotfish:executor` 實作 1 次＋修正 1 次；雲端 VM ≤ 1.5 小時（所有集合同一台、同一種 CPU；私人集合的傾印只在 VM 上、跑完刪掉，帶回的只有數字）。
- **停止條件**：CLI 與離線有不能歸因的差異；口語池任一來源的署名資料不完整；§3 第 5 點任一項超過門檻；需要改 `pick` 或解碼器才能做到逐列相同；署名或授權的推論被證明不成立。
- **範圍外**：書面設定；保守與積極強度的口語項（原本沒量，修訂四由 `colloquial-levels.md` 補上）；重新選 μ／T／α；跨段的 lc；App 計數器；設定頁的開關。

## 8. 審查紀錄

- plan-verifier 第一次（`898495b8`）REVISE 8 項，照改：共用的核心函式與 CLI 選項釘死、P4b 選項移除、`p4d.py --out-picks`（§2.2、§2.3、§3.2）；重播載入口語表與比對對象（§2.3、§3.4）；「其他地方不變」逐項標出有沒有口語表與工具（§3.3）；參數全部是常數、和語意校正模型的配對規則與測試（§2.2、§3.1）；bundle 路徑、selftest 的一步（§2.3）；檔案授權、App 與資料 Release 兩邊的署名、`check-app.sh` 的檢查、來源單位數的出處（§2.1、§3.6）；量法的工具、機器、基準與門檻（§3.5）；回退與預算（§4、§5）。名稱「常談」由維護者同日選定；驗收句由維護者同日指正改成「桌腳的螺絲鬆掉了」（「大夥」台灣很少人說）。
- plan-verifier 第二次（`95e369a2`）REVISE 6 項，逐項處置：第 202 列的前文（FIX：selftest 與實機都先給前文「桌子一直在搖」，實作時先用 CLI 在同條件量一次）；k 的疊加層要含引擎建立時的詞包（FIX：用 `pack_text`，加有詞包／沒詞包的測試）；CI 與 release.yml 下載常談（FIX，資料 Release 先建）；署名的完整性檢查不能循環（FIX：每列句數、依來源加總對 repo 裡的數字、缺 id 就失敗）；關於頁沒有模型列表（拿掉這一項）；載入時間的量法（改用 `/usr/bin/time -v` 跑一列）。之後一次收尾審查。
- 收尾審查（`88f556bd`）REVISE 2 項，照改：實機步驟的前文要不送出、游標接在後面（殼只讀游標前的字）；署名檔改成資料 Release 的附件、`data/` 記雜湊、CI 與 release.yml 一起下載（乾淨的 checkout 才拿得到）。
- 實作前（2026-10-20）：YODAS 的頻道名稱查不回來，觸發 §2.1 原本的停止條件；維護者選「照資料集提供的內容署名」，§2.1 照改，其餘不變。
