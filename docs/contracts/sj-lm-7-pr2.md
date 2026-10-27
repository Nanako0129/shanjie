# 契約：通語 SJ-LM-7 PR 2 的核心（每個設定自己的連文表與 KN）

狀態：**READY**（2026-10-27：plan-verifier 兩次 REVISE——雜湊與工具範圍、參照資料夾與標記、兩張表的讀法與壞檔；App 端 Swift 測試與自我檢查——照改，收尾審查 READY）。上層契約 `docs/contracts/sj-lm-7.md` §2.8、§2.9、§2.10、§4 第 7 條。維護者 2026-10-27 選擇題「先在分支上做 PR 2 的核心，再用真引擎量」：PR 2 的程式先在分支上用本機模型檔開發與量測，引用 Release 的部分等 Release 建好（§6）。

## 0. 要做到什麼、怎麼看到

- **要做到**：真引擎（核心、FFI、App）照定案組合解碼：
  - 聊天：bigram＋KN（β 照 `kn.sjkn` 檔頭）＋**聊天連文表**（0.95M 條）＋常談 C1＋會意 `bbb387d0…`，三檔用 §2.9 選中的 cell。
  - 書面：bigram＋KN（**β 0.75**）＋**書面連文表**（WC 7.7M 條）＋會意 `bbb387d0…` 的檔頭值。
- **怎麼看到**：
  1. 新 CLI 不帶任何研究旗標，在公開集合上的逐列結果，和 §2.9／§2.10 量測用的 CLI 帶研究旗標（聊天 `--chat-tri on-kn`、書面 `--formal-kn --kn-beta 0.75`）逐位元組相同（§3 第 1 條）。
  2. 真引擎（bench 的 replay）在 Mac 閒置窗量到按鍵 p95、重排 p95 與 RSS，過 §4 的門檻。
  3. 維護者實機：App 換上新模型後，聊天、書面各打一句只有新組合才對的公開句子（事先用 CLI 確認），看到選對。

## 1. 檔案

| 檔 | 內容 | 來源 |
|---|---|---|
| `data/lm/bigram.sjlm` | B6（剪枝門檻 6） | GCP `~/s1u-lm/K0.95M/bigram.sjlm`（`69826c2f…`） |
| `data/lm/classes.sjc`、`kn.sjkn` | B6 的分群與 KN 側檔（β 1） | 同一個資料夾 |
| `data/lm/trigram.sjt3` | 書面連文表 WC 7.7M（檔名不變） | `~/s1v-lm/WC-K7.7M/trigram.sjt3`（`7ec1869b…`） |
| `data/lm/trigram-chat.sjt3`（新） | 聊天連文表 0.95M | `~/s1u-lm/K0.95M/trigram.sjt3`（`03891d5a…`） |
| `data/lm/colloquial.sjlm` | 常談 C1 | `~/s4/C1.sjlm`（`81c2470d…`） |
| `data/lm/tinylm.sjtl` | 會意 SJ-SR-7 R1 定案檔 | `bbb387d0…`（Mac `~/.cache/shanjie/sj7-final/`） |
| 詞包、`digits.tsv`、`acg-counts.tsv` 等 | 階段 1 在 B6 上重建的檔 | GCP `~/s3a-cli/data/`；逐檔和分支上的版本比對，不同的照上層 §2.6 換 |

- 兩張連文表都綁同一個 bigram（`Tri::parse` 檢查模型雜湊），不符就拒絕載入，照現行規則。
- **雜湊與清單在這一片**：每個最終檔的 `data/*.sha256`（新增 `data/trigram-chat.sjt3.sha256`）與 `data/models.tsv` 的列（新增聊天連文表一列；名稱照模型命名規則，規模照檔的實際大小），讓 `scripts/build-app.sh`、`scripts/check-models.sh` 與 `tools/bench.py` 在本機檔上就能用。
- 從 VM 帶回 Mac 的步驟寫成腳本、過 `/code-review` 才跑（GCP 規則）；帶回後逐檔核對 SHA-256。這些檔都是公開語料建的，可以放 hermit，不進 repo。

## 2. 核心、CLI、App

1. **`Lm`**（`core/src/lm.rs`）：
   - 載入時，`trigram.sjt3`（書面）與 `trigram-chat.sjt3`（聊天）各自找模型旁邊的檔，都用現行的 mmap 載入；缺檔就是沒有那一張，壞檔照現行規則拒絕整個載入。
   - **和上層 §2.8「每個設定只載入自己那張」的關係**：那句的意思讀成「每個設定只用自己那張打分」。引擎（`Engine::load_lm`）兩張都 mmap，因為 App 切換設定（`set_profile`）不重新載入模型；mmap 只有被讀到的頁才佔記憶體，RSS 照 §4 兩種設定分開量。結果：App 裡任一張壞檔都讓整個 `load_lm` 失敗（兩種設定都受影響），和現在「書面表壞檔讓整個載入失敗」同一條規則。
   - **CLI 照現行只讀該設定的表**：聊天只讀 `trigram-chat.sjt3`、書面只讀 `trigram.sjt3`，所以壞掉的書面表不影響 CLI 聊天、壞掉的聊天表不影響 CLI 書面。
   - `scoring(profile)`：聊天＝λ 0.5、KN 開（側檔的 β）、有聊天表就用；書面＝λ 0.7、KN 開、**β ＝ `FORMAL_KN_BETA` 0.75**（具名常數，註解寫出處：研究紀錄 2026-10-26 階段 3、上層契約 §2.5）、有書面表就用。沒有 KN 側檔時兩種設定都是 KN 關（照現行）。
   - 拿掉研究開關：`chat_tri`／`set_chat_tri`、`RESEARCH_FORMAL_KN`、`research_set_kn_beta`。它們的效果現在就是出貨行為；舊的行為只能用舊的 CLI 重現。
   - 摘要行的標記：聊天有聊天表時 `+tri:<聊天表 sha8>`；書面的 KN 標記顯示 β 0.75（`+kn:<sha8>:θ1:β0.75`）。
2. **CLI**（`cli/src/main.rs`）：拿掉 `--chat-tri`、`--formal-kn`、`--kn-beta`；`--no-tri` 兩張表都不讀、`--no-kn` 兩種設定都關 KN。
3. **常談**（`core/src/colloquial.rs`）：`PAIRED_TINYLM_SHA256` ＝ `bbb387d0…` 的完整雜湊；`STANDARD` (0.7, 2.0, 0.0)、`CONSERVATIVE` (0.7, 2.0, 1.0)、`AGGRESSIVE` (1.0, 2.0, 0.1)，註解寫出處（上層 §2.9 選值結果）。MU、GATE_T、GAMMA_PER_MU、NU、CAP 不變。
4. **FFI 與 App**：`shanjie_engine_load_lm` 照舊從模型旁邊找檔，不加新的函式；`core/include/shanjie.h` 與 `docs/contracts/s3a.md` 寫明兩張表各屬哪個設定、書面 KN β。`scripts/build-app.sh` 要求並核對 `trigram-chat.sjt3`；`ffi.rs` 第 355 行過時的註解一起改。
5. **Python 參考與工具**照同一套規則改，跨語言核對（上層 §3）在最終檔上做：`reference/proto/lm_eval.py`；`tools/bench.py`（`resolve_models`／模型資料夾要有聊天表、`replay_tag`／`formal_tag` 的新標記：聊天 `+tri:`、書面 `+kn:…:β0.75` 加 `+tri:`，量本分支時用一列新的 `VERSIONS2` 與 `--as`）；`tools/triage.py` 的 `expected_tag`／`kn_tag`／`tri_tag`；`cli/src/bin/replay.rs` 印出的模型列（第 145 行）；`tools/kpi.py` 的檔名清單（第 108、131 行）。

6. **App 端的 Swift 測試與自我檢查**：`macos/Tests/ShanjieKitTests/Support.swift` 的 `TestData.files` 加 `("trigram-chat.sjt3", "data/lm/trigram-chat.sjt3")`；`ModelListTests` 與 `docs/contracts/about-models.md` 改成 `data/models.tsv` 的七列（新名稱與順序）；`macos/Sources/ShanjieKit/Selftest.swift` 的期望句子在最終檔上用新 CLI 重量（照檔裡註解記的做法），只改 CLI 顯示有變的那幾句，註解寫新的量測。

## 3. 驗收

1. **逐位元組相同**（Mac，公開集合，聊天三檔＋書面三檔）：新 CLI 不帶研究旗標，對 §2.9／§2.10 量測用的 CLI（分支 `7808c7b4`＋配對 `bbb387d0` 與三個 cell，帶研究旗標），在 dev302、typing76、user-reported、動漫五集、名單三集的 `--rowstats`、`--dump` 與**整行摘要**都逐位元組相同（沒有例外）。
   - 參照的模型資料夾（舊 CLI 只認 `trigram.sjt3`，所以兩種設定各一個資料夾）：聊天＝B6 `bigram.sjlm` `69826c2f`、`classes.sjc` `4315f0ed`、`kn.sjkn` `2b332df9`、`trigram.sjt3`＝聊天表 `03891d5a`；書面＝同樣三個檔、`trigram.sjt3`＝書面表 `7ec1869b`。兩者都用會意 `bbb387d0`、常談 C1 `81c2470d`（聊天）。
   - 新 CLI 用一個資料夾，兩張表各用自己的檔名；摘要行要含：聊天 `+kn:2b332df9:θ1:β1` 與 `+tri:03891d5a`，書面 `+kn:2b332df9:θ1:β0.75` 與 `+tri:7ec1869b`。
2. **測試**（含 §2 第 6 條的 Swift 測試；含壞檔：壞掉的 `trigram-chat.sjt3` 讓 CLI 聊天失敗、CLI 書面照常、`Engine::load_lm` 失敗；壞掉的 `trigram.sjt3` 反過來）：改寫釘住舊行為的測試（`core/src/lm.rs` 的 scoring 真值表、`core/tests/tri_tiny.rs`、`kn_tiny.rs`、`cli/tests/golden.rs` 的 `trigram_table_is_read_for_formal_only` 與 dev302 golden、`engine_lm.rs` 有連文表的列、`tinylm_tiny.rs`、`cli/tests/tinylm.rs`），每條新規則一個「規則壞了就失敗」的測試：聊天讀聊天表、書面讀書面表、各自缺檔時只影響自己、書面 β 0.75、`--no-tri`、`--no-kn`、配對雜湊與三個 cell。golden 在最終檔上重產。
3. Mac 上跑改到的測試檔、`make test-python`、`make test-swift`。188 WSL 的完整 Rust 測試與 CI 會從 GitHub 下載 Release、核對模型雜湊，所以排在 §6 換完 Release 引用之後。
4. 用本機檔建出的 App：`Contents/Resources` 有 `trigram-chat.sjt3`（`03891d5a…`），`scripts/check-models.sh bundle` 通過，`Contents/MacOS/shanjie --selftest` 結束碼 0；`make test-swift` 在 `data/lm` 放最終檔時通過。

## 4. 量測（閒置窗；量法在數字之前寫進研究紀錄）

- `tools/bench.py` 的速度量測（replay 三輪交錯＋CLI 的重排延遲），main 與本分支在同一台 Mac、同一個閒置窗（開始與結束負載都 ≤ 2.0）。replay 印出的模型列要有 `trigram-chat.sjt3:03891d5a`，聊天、書面的標記照 §3 第 1 條。
- **門檻**：按鍵 p95 ≤ 16 ms；重排 p95 ≤ 300 ms；RSS 不超過 main ＋40 MiB（上層 §6 的停止條件），兩種設定分開看。
- 不過就停下來，把數字給維護者。

## 5. 審查與合併

- 契約送 fresh `pilotfish:plan-verifier`；實作照「一片工作的流程」，`/code-review`＋fresh `pilotfish:verifier`＋CI。改到 FFI 與 App 的載入，`shanjie.h` 與 s3a 契約同一個 PR 更新。
- **不先合併**：這個分支等 Release 建好、換完引用（§6）才開 PR 合併，main 不會進到「核心規則換了、模型還沒換」的狀態（上層 §4 第 7 條）。

## 6. 範圍外（之後的 commit，同一個 PR）

- Release（通語 `model-v7`、辨類、承接、連文兩張、會意、常談；每個 Release 建立前都問維護者）、`ci.yml`／`release.yml`／`scripts/fetch-models.sh` 與 `build-app.sh` 下載提示裡的 Release 名稱與網址、授權（上層 §5）、chatjudge-3 確認一發、保留集；之後才跑 §3 第 3 條的 188 與 CI。
