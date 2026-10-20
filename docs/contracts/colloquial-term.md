# 契約：口語項進核心（colloquial-term）

狀態：草稿（2026-10-20），待 plan-verifier。

## 0. 起因、要做到什麼、怎麼看到

- **起因**：P4d（`experiments/p4d-colloquial/README.md`）在 chatjudge-2 確認通過：108／39，淨值 +69，p 1.1×10⁻⁸，改壞÷改對 0.36。照登記開產品片：口語項進核心、口語表隨 App 出貨。
- **要做到**：聊天設定、語意校正開著、強度「標準」時，核心的融合多一項口語分數，選字和 P4d 那一格（μ 0.5、α 0.5、軟閘門 T 1.0、δ 0.25、τ 2）逐列相同。書面設定、語意校正關著、其他強度，行為逐位元組不變。
- **怎麼看到**：
  1. CLI 用核心的新路徑跑公開集合，選字和 `p4d.py --cell 0.5,0.5,1.0` 的離線選字逐列相同；雲端跑 chatclause 與 discordtune，改對／改壞等於 P4d 的 113／37 與 74／43。
  2. 維護者在自己的 Mac 用聊天 App 打一句 P4d 改對的公開句子（從錯字回報或 dev302 裡挑，事先用 CLI 確認出貨版選錯、新版選對），看到新版選對。

## 1. 已經量到的

- P4d 的離線評分（`p4d.py`）與 P4b 的 Rust 實作（`cli/src/colloquial.rs`：lc、k、讀詞表的規則都和 `experiments/p4-colloquial/p4.py` 逐字相同，P4b 的四個錨點對上）。
- 把 n-gram 第一名固定當錨點，在 n-gram 分差 ≥ τ 的列不會少任何改對（P4d 每個集合都是 0），所以口語項只放在重排的工作裡就夠，不需要動解碼器。
- P4b 量過 CLI 帶口語表時 RSS +9.2 MiB、每列多 0.17 ms（下限，連續跑）。

## 2. 設計

### 2.1 口語表（資料）

- 檔案 `data/lm/colloquial.sjlm`，內容就是 P4 的 T2（SHA-256 `2984eeb5…`，V 68,055）：出貨模型 model-v6 建置時的口語計數（`1e7a15ef…`）加上 Phase 2 的公開口語池（`19fdc474…`，143,895 句）的計數，`tools/build_lm.py --corpora`（P4 加的選項）建，剪枝 2、折扣 0.75，不用詞類、不用 KN。repo 只放 `data/colloquial.sjlm.sha256` 與建置指令（`docs/` 一節），檔案走資料 Release，和其他模型檔一樣（`docs/releases/README.md`）。
- 對外名稱照命名規則取一個中文名與「SJ-」代號，由維護者選（建議：口吻 SJ-CL，規模用詞彙數 68K，第一版 `sj-cl-1-68k`）。
- **授權與署名**：口語池四個來源都要求署名（WildChat-1M ODC-BY、YODAS CC BY 3.0、Tatoeba CC BY 2.0 FR、MASSIVE CC BY 4.0；研究紀錄 2026-10-18「Phase 2 公開句子池」）。App 的 `Resources/LICENSES` 加一節：四種授權全文或連結、來源名稱，加上 `attribution.tsv`（YODAS 的影片 id 與頻道、Tatoeba 的句子 id 與作者、WildChat 與 MASSIVE 的資料集出處）。model-v6 的口語計數部分本來就在出貨模型裡，沿用現有的授權說明。

### 2.2 核心

- 新模組 `core/src/colloquial.rs`：`cli/src/colloquial.rs` 的 `Colloq`（lc、k、讀詞表）搬進核心，CLI 改成用核心這一份，不再有兩份實作。
- `Engine::load_colloquial(path)`：讀表（`Lm::load_opts(path, false, false, false)`，純雙連詞），從引擎載入詞庫的同一個資料夾讀疊加層與基本詞表（`read_sets` 的規則；acg 詞包開著才算進疊加層）。讀不到或格式錯 → 錯誤，引擎維持原狀（沒有口語項）。換 n-gram 模型時不丟（口語表和 n-gram 無關）。
- **重排**（`tinylm.rs` 每一段的評分）：聊天設定、強度標準、口語表已載入時，對該段前 8 條路徑（n-gram 順序，第 0 條是錨點）：
  - col_i = lc_i − 0.5·k_i（lc 從 `<s>` 加到 `</s>`，以該段的路徑為一句；離線的列只有一段，所以兩者相同）；
  - g_i = 1 / (1 + exp(−(ll_i − ll_0) / 1.0))，用不會溢位的寫法；
  - ll_eff_i = ll_i + g_i·(0.5 / α)·(col_i − col_0)；
  - `pick(ng, ll_eff, α = 0.5, τ = 2, δ = 0.25)`，`pick` 本身不改。
  - μ 0.5、T 1.0、α 0.5、γ/μ 0.5 寫成具名常數，註解寫出處（P4d 登記與 chatjudge-2 確認）。聊天標準強度原本的 α 0.3 只在口語表載入時被取代；保守、積極兩種強度與書面照舊（P4d 只量了這一組）。
- 多段的組字（標點、固定詞把組字切成幾段）：每段各自當一句算 lc。這和離線（一列一段）不同，是已知的差異，寫進研究紀錄；驗收用的公開集合都是一段。
- FFI：`shanjie_engine_load_colloquial(engine, path)`，錯誤碼照 `load_tinylm`；`core/include/shanjie.h`、C 標頭冒煙測試、s3a 的 FFI 表一起改。按鍵規則不變。

### 2.3 App 與 CLI

- 殼建引擎時，在 `load_tinylm` 之後載入 `colloquial.sjlm`（檔案在 bundle 的 `Resources/lm/`）；載入失敗只記靜態 log，照常用沒有口語項的重排。沒有新的設定開關：它跟著「語意校正」開關與強度走（關掉語意校正就沒有作用）。關於頁的模型列表多一列名稱與版本。
- `scripts/build-app.sh`：檢查 `data/lm/colloquial.sjlm` 存在且雜湊相符，放進 bundle；`check-app.sh` 一併檢查。
- CLI：`--colloquial-core FILE`（或把 P4b 的 `--colloquial` 改成走核心）讓評測走核心的新路徑；P4b 的 `--colloquial-mu`／`--colloquial-gamma` 舊選項保留給研究（行為不變）或移除，實作時定，寫進 CLI 說明。

## 3. 驗收

1. **核心單元測試**（每條規則一個「規則壞掉就會失敗」的測試）：口語項是相對錨點的差、閘門用 ll_0、溢位、只在聊天標準強度、沒有表時逐位元組不變、表載入失敗引擎不變、k 只算疊加層與詞包且不在基本詞表的詞。突變：拿掉相對錨點、閘門改用 ll_i、α 不換成 0.5，各要讓測試失敗。
2. **和離線逐列相同**：CLI 走核心的新路徑，在 dev302、打字測驗、錯字回報、cvtune-clean（聊天）上，每列選的候選與 `p4d.py --cell 0.5,0.5,1.0` 相同（比 rowstats 與選字；差異列要歸因，不能歸因就停）。雲端的 chatclause、discordtune：改對／改壞等於 113／37、74／43（只帶回數字）。
3. **不變的部分**：書面設定、語意校正關、保守與積極強度、沒有口語表時，dev302、打字測驗、錯字回報兩種設定的 `top1_sha256` 和 main 相同；golden 不變。
4. **重播一致**：`shanjie-replay --rerank each` 的第一次顯示和 CLI 相同（engine-replay §6.1 的做法）。
5. **速度與記憶體**（188 或雲端，單執行緒）：每鍵 p95 不超過 16 ms 的按鍵預算；App 的 RSS 增加 ≤ 40 MiB（P4b 量到 +9.2 MiB）。
6. **授權**：`Resources/LICENSES` 有四個來源的授權與署名，`attribution.tsv` 的列數和口語池的來源單位對得上。
7. **實機**（維護者，15 秒級）：照 §0 第 2 點打一句；關於頁看得到口吻的名稱與版本。
8. `/code-review` ＋ fresh verifier ＋ CI。

## 4. 發佈

- 資料 Release（口語表）照 `docs/releases/README.md` 的手動流程，建立前問維護者。
- 跟在 v0.5.0 與 canary v0.5.1 之後的版本出（v0.5.1 只放小改動）。
- 開發層與判讀集的數字已經在 P4d 量過（chatjudge-2 已用掉），這一片不再判讀模型效果；它的驗收是「核心和離線逐列相同」，所以 §3 第 2 點的逐列相同就是品質的保證。發版說明寫：聊天設定的語意校正多參考口語用字；數字（chatjudge-2 淨值 +69）。
- 每版 KPI 照常記。

## 5. 停止條件、範圍外

- **停止條件**：CLI 與離線有不能歸因的差異；口語池任一來源的署名資料不完整（不出貨，先補）；記憶體或延遲超過 §3 第 5 點；需要改 `pick` 或解碼器才能做到逐列相同。
- **範圍外**：書面設定、保守與積極強度的口語項（沒量）、重新選 μ／T／α、跨段的 lc、App 計數器。
