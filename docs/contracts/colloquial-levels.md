# 契約：常談與讀音通道項在保守、積極也生效（colloquial-levels）

狀態：READY（2026-10-22；plan-verifier REVISE 五項照改後，收尾審查 READY）。

## 0. 起因、要做到什麼、怎麼看到

- **起因**：維護者錄 v0.5.0 demo 時發現，強度不是「標準」就沒有常談：「保守或積極都不應該覆蓋常談」。
- **探索片**（`experiments/strength-colloq/README.md`，分支 `exp/strength-colloq`；全文已搬進研究紀錄 2026-10-26「補記二」）兩次都照登記過關：
  - 積極 (α 0.5, τ 2, δ 0.1)：四條出貨標準與順序門檻全過；對現行積極的 discordtune 108／42。
  - 保守 R2 (1.0, 2, 3.0)：四條與門檻全過；discordtune 17／4。
  - 維護者決定兩檔都「探索過了就出貨」，對這一片的例外，記在維護者的 decisions。
- **要做到**：聊天設定、語意校正開著、載入的 `.sjtl` 就是配對的那一份（`PAIRED_TINYLM_SHA256`）、常談表已載入時，**三種強度**都加口語項與讀音通道項，各用自己的 (α, τ, δ)。逐列和探索片離線選中的格相同：
  - 標準：(0.5, 2, 0.25)，不變；
  - 保守：(1.0, 2, 3.0)；
  - 積極：(0.5, 2, 0.1)。
  - 〔SJ-LM-7 起〕換了底層與小模型（會意 `f7d0ac63…`），三檔的格重選成標準 (0.7, 2, 0)、保守 (0.7, 2, 1)、積極 (1.0, 2, 0.1)（`docs/contracts/sj-lm-7.md` §2.9、`sj-lm-7-pr2.md`）；以下 §1 的常數值是修訂四當時的，現行值以 `core/src/colloquial.rs` 為準。
  - 其他情況（書面、語意校正關、別的小模型、沒有常談表），行為逐位元組不變。
- **怎麼看到**：
  1. CLI 用核心的新路徑跑，保守、積極兩檔的每列選字都等於 `sc.py` 離線算的選中格：公開集合在 Mac，私人集合在 GCP。
  2. 聊天標準的選字逐位元組不變。
  3. 實機：維護者在聊天 App 把強度切到「積極」或「保守」，打一句只有常談才選對的公開句子，看到選對。句子事先用 CLI 確認：v0.5.0 在那一檔選錯，新版選對。

## 1. 規則（`core/src/colloquial.rs`）

- `applies(profile, level, model)`：拿掉 `level == Standard` 這個條件（實作時連 `level` 參數一起拿掉，成為 `applies(profile, model)`）；聊天、配對的小模型兩個條件不變，常談表已載入照舊由呼叫端判斷。
- 新增每一檔的三個值，寫成具名常數，註解寫出處（探索片 README 的哪一次、選格或登記），用一個依 `RerankLevel` 取值的函式取用：
  - `STANDARD` (0.5, 2.0, 0.25)：現在的 `ALPHA`、`TAU`、`DELTA`（SJ-LM-7 起 (0.7, 2.0, 0.0)）；
  - `CONSERVATIVE` (1.0, 2.0, 3.0)（SJ-LM-7 起 (0.7, 2.0, 1.0)）；
  - `AGGRESSIVE` (0.5, 2.0, 0.1)（SJ-LM-7 起 (1.0, 2.0, 0.1)）。
- `fuse` 與 `fused_pick` 收那一檔的 α，口語項係數是 `MU / α`，讀音項係數是 `NU / α`。
  - 口語項與讀音項在融合分數裡的權重和 α 無關：s_i = ng_i + α·ll_i + g_i·μ·Δcol + ν·Δch。
  - 每一檔的 `pick` 用那一檔的 α、τ、δ。
- `guarded_pick` 的退路（任何一個 col 不是有限值）：用**那一檔**的 `.sjtl` 檔頭三個值做原本的 `pick`（探索片的工具也是用各檔自己的）。
- `TinyLm::rerank_colloquial`（`core/src/tinylm.rs` 511–526）收 level：融合用那一檔的常數三個值，退路的 `plain` 改成 `self.params(Profile::Chat, level)`（現在寫死 `RerankLevel::Standard`，524 行）。`RerankJob` 記下建工作時的 level（現在只記 `params` 與 `colloquial`），`RerankJob::run` 把它傳給 `rerank_colloquial`。
- MU、GATE_T、GAMMA_PER_MU、NU、CAP、`PAIRED_TINYLM_SHA256` 不變。**不重匯出 `.sjtl`**：換檔頭會改掉雜湊，`applies` 就會失效。
- `engine.rs` 建工作時傳 `self.rerank_level` 進來（現在只用來判斷 `applies`）；CLI `--colloquial-core` 用同一個函式，不另寫判斷。

## 2. 驗收

1. **核心單元測試**（每條規則一個「規則壞掉就會失敗」的測試）：
   - 三檔都 `applies`。書面、別的 `.sjtl` 仍然不 `applies`。
   - 每一檔的 `fused_pick` 用自己的三個值：同一組輸入在三檔選出不同的候選，測試事先寫好預期的索引。
   - 口語項與讀音項的權重和 α 無關：α 1.0 時改成用 0.5 去除，測試會失敗。
   - 退路用那一檔的檔頭三個值，**在 `rerank_colloquial` 這一層測**：保守檔、一列非有限的 col，把 524 行改回寫死標準時測試會失敗。
   - **引擎路徑**（`core/tests/engine_colloquial.rs` 新增）：載入常談表，保守與積極各挑公開的列，那一檔的格選的候選要和標準格、檔頭純 `pick` 都不同。工作的選字要等於事先寫好的索引，索引取自 CLI `--rerank-level L --colloquial-core`。在引擎裡換成標準的三個值，或建工作時沒記下 level，測試都會失敗。
   - **改成「有口語項」的既有測試**：`core/tests/engine_colloquial.rs` 108–139、191–220（現在斷言保守、積極「不用」），`cli/tests/colloquial.rs` 140–172 `nothing_changes_elsewhere` 的保守、積極兩格。
2. **和探索片逐列相同**（`sc.py` 加一個 `--expect-core` 模式，或另寫一個比對腳本；PR 寫明工具與指令）：
   - **跑法**：用本分支的 commit、同一份模型（雜湊照 `data/*.sha256`），對六個集合各跑三次 CLI。
     - 聊天標準的出貨設定：`--profile chat --context --packs acg --tinylm data/lm/tinylm.sjtl --colloquial-core data/lm/colloquial.sjlm --dump --dump-words --rowstats --tinylm-scores`。
     - 同樣的旗標加 `--rerank-level conservative`，再跑一次 `--rerank-level aggressive`；這兩次只要 `--tinylm-scores --rowstats`。
     - 三次摘要行的設定標記都要以 `+rc` 結尾。
     - 集合：錯字回報是 `eval/dev/user-reported.txt` 的前 157 列（`5659f5f6…`），其他照探索片登記的列檔與雜湊。
   - **離線那一邊**：用聊天標準那一次的前 8 名，算 `sc.py` 的 `cell_pick(r, 選中格, hdr[那一檔])`。
   - **比的欄位**：每一列 `--tinylm-scores` 的 `c` 與對錯（ok）。
   - 六個集合 × 兩檔都要 0 列不同，並報 CLI 的 commit 與模型雜湊。
   - 公開集合（dev302、打字測驗、錯字回報）在 Mac，私人集合（chatclause、discordtune、cvtune-clean）在 GCP。任何一列不同就停。
   - 注意：探索片的 `sc.py run` 現在要求非標準檔不帶 `+rc`、錨點 2 用純 `pick`，那是量現行出貨用的；比對模式要另外寫，不改那兩個錨點。
3. **不變的部分**：
   - 聊天標準、書面三檔、沒有常談表，這幾種的 dev302、打字測驗、錯字回報 `top1_sha256` 和 main 相同。
   - golden 不變。
   - 重播（`shanjie-replay --rerank each`）在聊天標準六格相同。
4. **實機**（維護者，15 秒級）：
   - 在繁中虛擬機裝這一版，聊天設定。
   - 用 CLI 先挑兩句公開句子：在積極或保守，v0.5.0 選錯、新版選對（從錯字回報與 dev302 找）。
   - 切到那一檔打這兩句，看到選對。
5. 延遲：融合多一個查表，不另量。

## 3. 要一起改的地方

- `docs/contracts/colloquial-term.md`：狀態行與 §0、§2 的「只在標準強度」改成三檔；§9 的表把保守、積極改成「有」。
- `docs/contracts/reading-channel.md`：§0、§3.3、範圍外。
- `docs/contracts/s3a.md`：247 行的「標準強度」條件。
- `core/include/shanjie.h` 有提到這個條件的地方。
- `docs/contracts/rerank-strength.md`：§11 之後補一節，說明保守、積極在常談下用的三個值（核心常數，不在檔頭）。
- 其他寫著「只在標準」的地方：`site/index.html` 193、350 行（350：「保守、積極也要生效的版本在規劃中」）、`site/updates.html` 78、`docs/data-files.md` 22、`docs/releases/sj-cl-1-68k.md` 1、`docs/contracts/engine-replay.md` 30、`macos/Sources/ShanjieKit/Core.swift` 66、`tools/triage.py` 436，以及程式註解 `core/src/colloquial.rs` 8–9、24、160–161，`core/src/engine.rs` 54–55、2538，`core/src/tinylm.rs` 511–516，`cli/src/main.rs` 202–215，`cli/src/bin/replay.rs` 156。網站要部署才會生效，部署前照規則先問。
- 研究紀錄。
- 發版說明之後另寫。
- 改完後 grep「標準強度」「只在標準」「保守與積極」「強度「標準」」「「標準」」以及英文的 "standard strength"、"standard level"、"standard rerank"：除了研究紀錄的歷史段落，沒有一句還把常談或讀音通道項限定在標準強度。

## 4. 停止條件、範圍外

- **停止**：
  - §2.2 的逐列比對有任何一列不同；
  - §2.3 不變的部分有任何改變；
  - 需要重匯出 `.sjtl`。
- **預算**：GCP VM ≤ 2 小時（私人集合的 CLI 與比對），超過 1.5 倍就停下來問；做完當場停機。
- **回滾**：revert 本片的 merge commit。只撤一檔時，那一檔的常數改回「不套用」（`applies` 對那一檔回 false），不動 `.sjtl`。
- **範圍外**：
  - 書面設定加常談；
  - R1 讀音解碼。它的產品片會拿掉融合層的讀音通道項，並在這一片之後重跑三檔的傾印。
  - 設定頁的文字；
  - App 計數器。
