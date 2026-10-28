# 契約：通語 SJ-LM-7 PR 2 的核心（每個設定自己的連文表與 KN）

狀態：**READY**（2026-10-27：plan-verifier 兩次 REVISE——雜湊與工具範圍、參照資料夾與標記、兩張表的讀法與壞檔；App 端 Swift 測試與自我檢查——照改，收尾審查 READY）。修訂一（§2.7，同日 `/code-review` 發現米雅歌被重排換掉，維護者選「PR 2 裡直接修」）：plan-verifier 一次 REVISE（手動詞集合的來源與鍵、遮罩語意、引擎層測試、triage、比對基準、`shanjie.h`）照改，收尾審查 READY。上層契約 `docs/contracts/sj-lm-7.md` §2.8、§2.9、§2.10、§4 第 7 條。維護者 2026-10-27 選擇題「先在分支上做 PR 2 的核心，再用真引擎量」：PR 2 的程式先在分支上用本機模型檔開發與量測，引用 Release 的部分等 Release 建好（§6）。

## 0. 要做到什麼、怎麼看到

- **要做到**：真引擎（核心、FFI、App）照定案組合解碼：
  - 聊天：bigram＋KN（β 照 `kn.sjkn` 檔頭）＋**聊天連文表**（0.95M 條）＋常談 C1＋會意 SJ-SR-7-15M（`f7d0ac63…`），三檔用 §2.9 選中的 cell。
  - 書面：bigram＋KN（**β 0.75**）＋**書面連文表**（WC 7.7M 條）＋會意 SJ-SR-7-15M（`f7d0ac63…`）的檔頭值。
- **怎麼看到**：
  1. 新 CLI 不帶任何研究旗標，在公開集合上的逐列結果，和 §2.9／§2.10 量測用的 CLI 帶研究旗標（聊天 `--chat-tri on-kn`、書面 `--formal-kn --kn-beta 0.75`）逐位元組相同（§3 第 1 條）。
  2. 真引擎（bench 的 replay）在 Mac 閒置窗量到按鍵 p95、重排 p95 與 RSS，過 §4 的門檻。
  3. 維護者實機：App 換上新模型後，聊天、書面各打一句只有新組合才對的公開句子（事先用 CLI 確認），看到選對。

## 1. 檔案

| 檔 | 內容 | 來源 |
|---|---|---|
| `data/lm/bigram.sjlm` | B6（剪枝門檻 6） | 評測機器上的定案建置（`69826c2f…`）；加指紋條目後 `e3fb6056…` |
| `data/lm/classes.sjc`、`kn.sjkn` | B6 的分群與 KN 側檔（β 1） | 同一個資料夾 |
| `data/lm/trigram.sjt3` | 書面連文表 WC 7.7M（檔名不變） | 評測機器上的定案建置（`7ec1869b…`）；重綁到帶指紋的通語後 `34305a2d…` |
| `data/lm/trigram-chat.sjt3`（新） | 聊天連文表 0.95M | 評測機器上的定案建置（`03891d5a…`）；重綁後 `24ee8645…` |
| `data/lm/colloquial.sjlm` | 常談 C1 | 評測機器上的階段 4 建置（`81c2470d…`，不綁通語，不變） |
| `data/lm/tinylm.sjtl` | 會意 SJ-SR-7-15M R1 定案檔 | `f7d0ac63…`（維護者本機） |
| 詞包、`digits.tsv`、`acg-counts.tsv` 等 | 階段 1 在 B6 上重建的檔 | 評測機器上的階段 1 建置；逐檔和分支上的版本比對，不同的照上層 §2.6 換 |

- **這些是最終檔**：維護者在私人步驟把用來辨識來源的指紋條目加進 bigram，並重綁所有綁定它的檔（只改檔頭的雜湊位元組，其餘內容不變）；上表的雜湊是加指紋之後的值，`colloquial.sjlm` 不受影響。換檔前後在公開集合上的逐列結果逐位元組相同（`--rowstats`，dev302、打字測驗、錯字回報，聊天與書面）。
- 兩張連文表都綁同一個 bigram（`Tri::parse` 檢查模型雜湊），不符就拒絕載入，照現行規則。
- **雜湊與清單在這一片**：每個最終檔的 `data/*.sha256`（新增 `data/trigram-chat.sjt3.sha256`）與 `data/models.tsv` 的列（新增聊天連文表一列；名稱照模型命名規則，規模照檔的實際大小），讓 `scripts/build-app.sh`、`scripts/check-models.sh` 與 `tools/bench.py` 在本機檔上就能用。
- 從 VM 帶回 Mac 的步驟寫成腳本、過 `/code-review` 才跑（GCP 規則）；帶回後逐檔核對 SHA-256。這些檔都是公開語料建的，可以放維護者本機，不進 repo。

## 2. 核心、CLI、App

1. **`Lm`**（`core/src/lm.rs`）：
   - 載入時，`trigram.sjt3`（書面）與 `trigram-chat.sjt3`（聊天）各自找模型旁邊的檔，都用現行的 mmap 載入；缺檔就是沒有那一張，壞檔照現行規則拒絕整個載入。
   - **和上層 §2.8「每個設定只載入自己那張」的關係**：那句的意思讀成「每個設定只用自己那張打分」。引擎（`Engine::load_lm`）兩張都 mmap，因為 App 切換設定（`set_profile`）不重新載入模型；mmap 只有被讀到的頁才佔記憶體，RSS 照 §4 兩種設定分開量。結果：App 裡任一張壞檔都讓整個 `load_lm` 失敗（兩種設定都受影響），和現在「書面表壞檔讓整個載入失敗」同一條規則。
   - **CLI 照現行只讀該設定的表**：聊天只讀 `trigram-chat.sjt3`、書面只讀 `trigram.sjt3`，所以壞掉的書面表不影響 CLI 聊天、壞掉的聊天表不影響 CLI 書面。
   - `scoring(profile)`：聊天＝λ 0.5、KN 開（側檔的 β）、有聊天表就用；書面＝λ 0.7、KN 開、**β ＝ `FORMAL_KN_BETA` 0.75**（具名常數，註解寫出處：研究紀錄 2026-10-26 階段 3、上層契約 §2.5）、有書面表就用。沒有 KN 側檔時兩種設定都是 KN 關（照現行）。
   - 拿掉研究開關：`chat_tri`／`set_chat_tri`、`RESEARCH_FORMAL_KN`、`research_set_kn_beta`。它們的效果現在就是出貨行為；舊的行為只能用舊的 CLI 重現。
   - 摘要行的標記：聊天有聊天表時 `+tri:<聊天表 sha8>`；書面的 KN 標記顯示 β 0.75（`+kn:<sha8>:θ1:β0.75`）。
2. **CLI**（`cli/src/main.rs`）：拿掉 `--chat-tri`、`--formal-kn`、`--kn-beta`；`--no-tri` 兩張表都不讀、`--no-kn` 兩種設定都關 KN。
3. **常談**（`core/src/colloquial.rs`）：`PAIRED_TINYLM_SHA256` ＝ `f7d0ac63…` 的完整雜湊；`STANDARD` (0.7, 2.0, 0.0)、`CONSERVATIVE` (0.7, 2.0, 1.0)、`AGGRESSIVE` (1.0, 2.0, 0.1)，註解寫出處（上層 §2.9 選值結果）。MU、GATE_T、GAMMA_PER_MU、NU、CAP 不變。
4. **FFI 與 App**：`shanjie_engine_load_lm` 照舊從模型旁邊找檔，不加新的函式；`core/include/shanjie.h` 與 `docs/contracts/s3a.md` 寫明兩張表各屬哪個設定、書面 KN β。`scripts/build-app.sh` 要求並核對 `trigram-chat.sjt3`；`ffi.rs` 第 355 行過時的註解一起改。
5. **Python 參考與工具**照同一套規則改，跨語言核對（上層 §3）在最終檔上做：`reference/proto/lm_eval.py`；`tools/bench.py`（`resolve_models`／模型資料夾要有聊天表、`replay_tag`／`formal_tag` 的新標記：聊天 `+tri:`、書面 `+kn:…:β0.75` 加 `+tri:`，量本分支時用一列新的 `VERSIONS2` 與 `--as`）；`tools/triage.py` 的 `expected_tag`／`kn_tag`／`tri_tag`；`cli/src/bin/replay.rs` 印出的模型列（第 145 行）；`tools/kpi.py` 的檔名清單（第 108、131 行）。

6. **App 端的 Swift 測試與自我檢查**：`macos/Tests/ShanjieKitTests/Support.swift` 的 `TestData.files` 加 `("trigram-chat.sjt3", "data/lm/trigram-chat.sjt3")`；`ModelListTests` 與 `docs/contracts/about-models.md` 改成 `data/models.tsv` 的七列（新名稱與順序）；`macos/Sources/ShanjieKit/Selftest.swift` 的期望句子在最終檔上用新 CLI 重量（照檔裡註解記的做法），只改 CLI 顯示有變的那幾句，註解寫新的量測。

### 2.7 修訂一：重排不換掉手動加的詞（維護者 2026-10-27 選擇題「PR 2 裡直接修」）

- **起因**：`/code-review` 發現最終組合在書面的標準檔與積極檔，把手動加的詞「米雅歌」（`eval/golden/manual-words-rows.txt` 第 4 列，n-gram 第一名對）重排成「米亞哥」；保守檔與聊天沒有這個問題。舊模型已經有一列同類的書面例外（第 8 列「米雅歌唱得很好」→ 米亞）。
- **手動詞集合**（資料來源與鍵）：
  - 來源：`data/lexicon/overlay-add.tsv` 第 4 欄是 `manual` 的列（`tools/build_overlay.py` 從 `overlay-manual.tsv` 接上去的）。`Lexicon` 讀疊加層時，除了照舊建詞條，另外把這些列收成一個集合 `ManualSet`；鍵是（詞的字串，讀音的音節序列），詞庫保證一個字一個音節。其他標籤（`slang`、`names`、詞包…）不收。
  - 建立的時機：引擎建立時（`Engine::new` 讀詞庫的同一處）與 CLI 讀詞庫時，兩者經過同一個 `Lexicon` 函式，不另寫一份。
- **哪些地方受保護**（不需要候選的斷詞）：重排的一段（stretch）有音節序列 `syl[0..n]` 與 n-gram 前 8 名的字串 `cands[0..k]`。對 `ManualSet` 的每一個（詞 w，讀音 r），在 `syl` 裡找所有 `syl[i..i+|r|] == r` 而且 `cands[0]` 在第 i 字起的 `|w|` 個字等於 w 的位置 i；這些（i, w）就是受保護的段。這裡的 `syl` 是 n-gram 第一名那條路徑實際用的讀音：一般輸入就是打的音節；鄰鍵輸入（`docs/contracts/fuzzy-input.md`）把詞庫沒有的音節讀成它的替代讀音時，用那個詞庫讀音（`Lexicon::path_readings`，引擎的工作與 CLI 同一個函式），所以手動詞經由容錯讀音成為第一名時也受保護。候選 c「保有」全部受保護的段＝對每一個（i, w），c 在第 i 字起的 `|w|` 個字等於 w。
- **選擇規則**（遮罩，門檻照舊）：
  - 沒有受保護的段：完全照舊。
  - 有受保護的段：不保有的候選在**選擇時遮掉**（不能被選），但 τ 門檻與所有分數仍用完整的清單算（`pick` 的 `ng[0] − ng[1] ≥ τ` 照舊用原本的第二名）。例：`ng = [0, −0.5, −3]`、τ = 2、候選 1 不保有：門檻 0.5 < 2，重排照常比較，但只能在候選 0 與候選 2 之間選。
  - `colloquial::guarded_pick` 的退回路徑同樣遮掉：退回時也只能退到保有的候選；第一名永遠保有，所以至少有一個可選。
- **唯一的實作點**：遮罩函式 `manual_mask(syl, cands, &ManualSet) -> Vec<bool>` 放在 `core/src/tinylm.rs`，`TinyLm::rerank` 與 `rerank_colloquial` 都多收 `&ManualSet`（與這一段的音節）並在各自的選擇步驟套用；不在 CLI、引擎或殼另外過濾。
  - 書面的 `RerankJob` 現在只帶聊天常談用的 `channel_lex`：改成兩種設定的工作都帶 `ManualSet`（引擎建立時的那一份，`Arc` 共用），`shanjie_rerank_run` 照舊不需要引擎，資料在工作裡。
  - `shanjie-eval --tinylm` 與 `shanjie-replay` 經過上面兩個函式。
- **`--tinylm-scores` 與 triage**：`--tinylm-scores` 的每列多一個欄位，列出被遮掉的候選索引（沒有就是空）。`tools/triage.py` 的一致性檢查（CLI 選的索引要等於 Python `pick(ng, ll, *par)`）改成套用同樣的遮罩；遮罩欄位缺少時照舊。
- **對外**：`docs/contracts/s3a.md` 的按鍵規則表加一列；`core/include/shanjie.h` S7c-2a 的註解加一句：「這一段的 n-gram 第一名含有 `manual` 疊加層詞時，重排只會換成在同樣音節上保有它的候選」。研究紀錄記規則與量測。
- **測試**（每條規則一個「規則壞了就失敗」的測試）：
  1. 遮罩（`manual_mask`）：第一名含手動詞時，不保有的候選被遮掉；手動詞出現在別的音節位置不算保有。
  2. 選擇：`ng = [0, −0.5, −3]`、τ = 2、候選 1 不保有，原本會選候選 1 的分數 → 選出的是候選 0 或 2 中分數較高的那個；同一組分數拿掉遮罩會選候選 1。
  3. 另一個候選也保有同一段的同一個手動詞而且分數較高 → 可以選它。
  4. 非 `manual` 標籤的疊加層詞（例如 `names`）在同樣情形下照舊可以被換掉。
  5. `guarded_pick` 的退回路徑：退回時不會退到被遮掉的候選。
  6. **引擎層**（`core/tests/engine_rerank.rs`，最終檔）：書面、標準檔與積極檔，打 `米雅歌`（manual-words 第 4 列的讀音），套用後的重排結果是 米雅歌；工作建成沒有 `ManualSet`（空集合）時這個測試要失敗。
  7. `cli/tests/golden.rs` 的 `manual_words_come_first_in_the_shipping_setting`：書面與聊天、三檔（標準、保守、積極）都量，misses 依新規則實測寫死；第 4、8 列若變對就拿掉例外，docstring 改成實測結果。
  8. `tools/test_triage.py`：一列第一名含手動詞、CLI 保留索引 0 而無遮罩的 `pick` 會選別的 → triage 接受；遮罩相同但索引真的不一致時照舊報錯。
- **基準與比對**（數字之前把這段量法與基準 SHA commit 進研究紀錄）：
  - 基準＝修訂一之前的最後一個 commit `353c3dd9e17d06f4bfcbc83f03b804ab8ff77b9a`：`/code-review` 的審查修正（A–E）做完，§3 第 1 條在它上面重跑逐位元相同（66／66）。研究紀錄寫同一個完整 SHA。§3 第 1 條的「沒有例外」對這個基準成立；修訂一之後，對 §2.9 的 CLI 的差異只能出現在本節列出的變動列。
  - GCP 同一台 VM、§2.9 的單位與集合（discordtune、cvtune-clean、打字測驗、dev302、錯字回報、wikitune-clean、動漫五集、名單三集、網路用語三集、資訊詞包）：新 CLI 對基準 CLI 逐列比 rowstats，六個單位各跑一次。
  - 逐列相同的集合：§2.9 與 §2.10 的判讀照舊成立。
  - 有變的列：列出變對／變壞的列號，對 MAIN 重算出貨標準四條與共同關卡；任何一條由過變不過就停，把數字給維護者。
- **範圍外**：名單層（`names`）與詞包詞要不要同樣保護（另問）；手動詞的分數與 `MANUAL_BONUS`。

## 3. 驗收

1. **逐位元組相同**（Mac，公開集合，聊天三檔＋書面三檔）：新 CLI 不帶研究旗標，對 §2.9／§2.10 量測用的 CLI（分支 `7808c7b4`＋配對 `f7d0ac63` 與三個 cell，帶研究旗標），在 dev302、typing76、user-reported、動漫五集、名單三集的 `--rowstats`、`--dump` 與**整行摘要**都逐位元組相同（沒有例外）。
   - 參照的模型資料夾（舊 CLI 只認 `trigram.sjt3`，所以兩種設定各一個資料夾）：聊天＝B6 `bigram.sjlm` `e3fb6056`、`classes.sjc` `4c1071f2`、`kn.sjkn` `880ee87c`、`trigram.sjt3`＝聊天表 `24ee8645`；書面＝同樣三個檔、`trigram.sjt3`＝書面表 `34305a2d`。兩者都用會意 SJ-SR-7-15M `f7d0ac63`、常談 C1 `81c2470d`（聊天）。
   - 新 CLI 用一個資料夾，兩張表各用自己的檔名；摘要行要含：聊天 `+kn:880ee87c:θ1:β1` 與 `+tri:24ee8645`，書面 `+kn:880ee87c:θ1:β0.75` 與 `+tri:34305a2d`。
2. **測試**（含 §2 第 6 條的 Swift 測試；含壞檔：壞掉的 `trigram-chat.sjt3` 讓 CLI 聊天失敗、CLI 書面照常、`Engine::load_lm` 失敗；壞掉的 `trigram.sjt3` 反過來）：改寫釘住舊行為的測試（`core/src/lm.rs` 的 scoring 真值表、`core/tests/tri_tiny.rs`、`kn_tiny.rs`、`cli/tests/golden.rs` 的 `trigram_table_is_read_for_formal_only` 與 dev302 golden、`engine_lm.rs` 有連文表的列、`tinylm_tiny.rs`、`cli/tests/tinylm.rs`），每條新規則一個「規則壞了就失敗」的測試：聊天讀聊天表、書面讀書面表、各自缺檔時只影響自己、書面 β 0.75、`--no-tri`、`--no-kn`、配對雜湊與三個 cell。golden 在最終檔上重產。
3. Mac 上跑改到的測試檔、`make test-python`、`make test-swift`。評測機器上 WSL 的完整 Rust 測試與 CI 會從 GitHub 下載 Release、核對模型雜湊，所以排在 §6 換完 Release 引用之後。
4. 用本機檔建出的 App：`Contents/Resources` 有 `trigram-chat.sjt3`（`24ee8645…`），`scripts/check-models.sh bundle` 通過，`Contents/MacOS/shanjie --selftest` 結束碼 0；`make test-swift` 在 `data/lm` 放最終檔時通過。

## 4. 量測（閒置窗；量法在數字之前寫進研究紀錄）

- `tools/bench.py` 的速度量測（replay 三輪交錯＋CLI 的重排延遲），main 與本分支在同一台 Mac、同一個閒置窗（開始與結束負載都 ≤ 2.0）。replay 印出的模型列要有 `trigram-chat.sjt3:24ee8645`，聊天、書面的標記照 §3 第 1 條。
- **門檻**：按鍵 p95 ≤ 16 ms；重排 p95 ≤ 300 ms；RSS 不超過 main ＋40 MiB（上層 §6 的停止條件），兩種設定分開看。
- 不過就停下來，把數字給維護者。

## 5. 審查與合併

- 契約送 fresh `pilotfish:plan-verifier`；實作照「一片工作的流程」，`/code-review`＋fresh `pilotfish:verifier`＋CI。改到 FFI 與 App 的載入，`shanjie.h` 與 s3a 契約同一個 PR 更新。
- **不先合併**：這個分支等 Release 建好、換完引用（§6）才開 PR 合併，main 不會進到「核心規則換了、模型還沒換」的狀態（上層 §4 第 7 條）。

## 6. 範圍外（之後的 commit，同一個 PR）

- Release（通語 `model-v7`、辨類、承接、連文兩張、會意、常談；每個 Release 建立前都問維護者）、`ci.yml`／`release.yml`／`scripts/fetch-models.sh` 與 `build-app.sh` 下載提示裡的 Release 名稱與網址、授權（上層 §5）、chatjudge-3 確認一發、保留集；之後才跑 §3 第 3 條在評測機器上的測試與 CI。
