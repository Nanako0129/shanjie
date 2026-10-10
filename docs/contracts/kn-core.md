# KN 進核心（kn-core）：Rust 核心讀 Kneser–Ney 側檔

使用者 2026-10-10 決定 Kneser–Ney 回退分布（θ=1、β=1，`docs/contracts/kn-smoothing.md` §7）併進下一次模型重建（model-v6）一起做進核心，只重產一次 golden；同一天又說「可用的就迭代模型」。這一片先把核心與工具準備好，**不開啟**：沒有側檔時輸出逐位元組不變，golden 不動。model-v6 那一片產生正式的側檔、建 Release、釘雜湊、開啟、重產 golden。

## 0. 依據

- Python 參考實作已經有 KN（`reference/proto/lm.py` 的 `word()`：id ≥ 2 的詞 `pb = β·N′/ΣN′ + (1−β)·10^lp`，`N′ = N + 1`，ΣN′ 每個異體類只算代表一次）；側檔 `SJKN0001` 由 `tools/kn_cont.py` 產生（`kn-smoothing.md` §2.1）。
- `SJKN0001` 沒有 ΣN′，`lm.py` 要呼叫端傳異體類（`kn_classes`）自己算。Rust 核心沒有 `build_lm.variant_classes` 的輸入（詞庫的讀音集合比對），所以 ΣN′ 要放進檔案。
- 全域規則（使用者的 CLAUDE.md）：跨語言、預先彙總的值，驗收要點名上游那一層；Rust 的「全綠」不能證明 ΣN′ 對。這一片的決定性檢查是**真模型上 Python 與 Rust 的逐列比對**（§3 第 5 項）。

## 1. 格式 SJKN0002

little-endian：magic `b"SJKN0002"`、V（u32）、模型檔 SHA-256（32 bytes）、θ（u32）、β（f64）、ΣN′（u64）、N（u32[V]，id 0、1 為 0，非代表成員取代表的 N）。

- ΣN′ 由 `kn_cont.py` 用 `lm.kn_total` 算（和 `lm.py` 現在用的同一個函式、同一套 `build_lm.variant_classes`）。
- β 寫在檔案裡，出貨的設定由檔案決定（model-v6 用 1.0）。
- `SJKN0001` 照舊能讀（研究用），只有 Python 讀；Rust 只讀 `SJKN0002`。

## 2. 做什麼

1. **`tools/kn_cont.py`**：加 `--beta`，輸出 `SJKN0002`；`SJKN0001` 不再產生（舊檔照舊能讀）。
2. **`reference/proto/lm.py`**：讀 `SJKN0002`：β、ΣN′ 取自檔頭；呼叫端另外給 `kn_classes` 時，用 `kn_total` 重算並和檔頭比對，不同就 `ValueError`；給了 `kn_beta` 而且和檔頭不同也 `ValueError`。`lm_eval.py`：`--kn FILE` 讀 `SJKN0002` 時不需要 `--kn-beta`，但**照舊**用自己的詞庫算 `kn_classes`（`build_lm.variant_classes`）並傳進去，所以每次評測都會用 `kn_total` 重算 ΣN′ 和檔頭比對——這是 ΣN′ 這個預先彙總值在上游的檢查，Rust 只讀檔頭。
3. **Rust `core/src/lm.rs`**：
   - `Lm::load(path)`：模型旁邊有 `kn.sjkn` 就讀（`SJKN0002`，核對 magic、V、模型 SHA-256、長度、β ∈ [0, 1]、ΣN′ > 0、N 的長度），不合就 `LmError::Format`（不靜默略過）；沒有這個檔就是關閉。
   - 作用位置和 `lm.py` 一致：詞項的 `pb`（id ≥ 2）換成 `β·N′/ΣN′ + (1−β)·pb`；句尾（id 1）、不在詞彙裡的詞（`None`）不變。所有詞項都經過同一個函式（現在是 `prob_c`），在那裡做一次，不在各個呼叫端各做一次。
   - `Lm::kn_enabled()`（或同等）讓評測 CLI 在摘要行加 `+kn`，和 `lm_eval.py` 同一個格式（`+kn:<側檔 sha8>:θ<θ>:β<β>`），這樣 golden 與比對看得出有沒有開。
4. **評測 CLI `cli/`**：載入時照上面規則（旁邊有 `kn.sjkn` 就開）；`--no-kn` 明確關閉（研究用）。
5. **App 與發版**：這一片**不放**側檔（`scripts/build-app.sh`、`check-app.sh`、CI、`release.yml` 不動）；model-v6 那一片才加釘選與檢查。

## 3. 驗收

1. **沒有側檔時逐位元組不變**：現有 golden 測試（`cli/tests/golden.rs` 等）全過，不改任何 golden 檔。
2. **Rust 單元測試**（玩具模型＋玩具側檔，數值和 `tools/test_kn_cont.py` 的手算相同）：β = 0 與沒有側檔逐位元相同；β = 1 時保留條目、詞類項、一般回退三個分支各一個手算值；句尾與詞彙外的詞不變；壞檔（magic、V、雜湊、長度、β 超出範圍、ΣN′ = 0）都回 `LmError::Format`。突變：把 KN 那一行拿掉、把 id ≥ 2 的條件拿掉，各要讓測試失敗。
3. **Python**：`SJKN0002` 的讀寫、檔頭 ΣN′ 和 `kn_total` 不同時的錯誤、`kn_beta` 不符的錯誤；`tools/test_kn_cont.py` 照舊全過（改成產生 `SJKN0002`）。
4. **小模型的跨語言比對**：同一個玩具模型＋側檔，Rust 與 Python 對一組句子的解碼輸出（前 N 名與分數）逐位元組相同，寫成 golden 測試（像現有的 golden 測試，Python 產生、Rust 比對）。
5. **真模型的跨語言比對（決定性的一項，main 執行）**：model-v5＋classes-v3＋用 model-v5 計數重產的 θ=1、β=1 `SJKN0002` 側檔（188 產生，不進 repo）。三個檔的**實體複本**放在一個專用暫存目錄（例如 `~/.cache/shanjie/work/kn-core/lm/`），CLI 的 `--lm` 指向那裡；不放進任何 checkout 的 `data/lm`。
   - **上游的值**：側檔檔頭的 ΣN′ 等於研究紀錄的更正值 23,700,571；側檔的 N 陣列和研究用的 θ=1 側檔（`SJKN0001`，`fceb70cd…`）的 N 陣列逐位元組相同；`lm_eval.py` 讀它時有傳 `kn_classes`、沒有 `ValueError`（`kn_total` 重算等於檔頭）。
   - **跨語言**：dev302、打字測驗、錯字回報、cvtune、wikitune 兩種設定，`--context`：Rust CLI 與 `lm_eval.py` 的摘要行逐字相同（含 `top1_sha256`）。
   - **對研究數字**（只限研究之後內容沒變的集合）：cvtune（`31de456d…`，n 3,791）、wikitune（`8dcfe40c…`，n 2,827）、dev302（n 302）、打字測驗（n 76）的 top1 等於研究紀錄 KN 那一節 θ1β1 的列；錯字回報用 `--limit 66` 對研究數字（研究時是 66 列），全 69 列只做跨語言比對。
6. **延遲**：真模型開著側檔時，`perf_*` 測試照舊通過；另報每鍵 p95 開關對照。這一步要把 `kn.sjkn` 放進**這個 worktree 自己的** `data/lm/`（`bigram.sjlm`、`classes.sjc`、`kn.sjkn` 都是實體檔，不是指向共用目錄的 symlink），量完立刻移除，並記下移除後 `data/lm/kn.sjkn` 不存在。
7. `make test`、CI 全綠；fresh verifier 確認第 1、5 項。第 1 項與 `make test` 執行前先確認 `data/lm/kn.sjkn` 不存在；主 checkout 的 `data/lm` 只有 `bigram.sjlm` 與 `classes.sjc`。
8. **PR #105 verifier 留下的 P4**（這一片一起處理）：`tools/test_kn_cont.py` 補「沒有 kn 時只給 `kn_beta`」「只給 `kn_classes`」各一個案例；PLAN 那一行重複的「（研究紀錄同日）」與 `kn-smoothing.md` §7 漏標的探針側檔已在這個分支改掉。`wi >= 2` 的條件目前沒有可觀察的影響（`word()` 不會收到 id 0、1），Rust 版照同一個條件、在 Rust 測試裡用句尾覆蓋。

## 4. 範圍外、停止條件、預算、限制、回滾

- **範圍外**：model-v6 的計數、側檔的 Release、釘雜湊、App 打包、開啟；KN 與選字記憶加分的交互（開著 KN 時學到的詞的 `pb` 加分被換掉，只剩 `(1−λ)·lp` 那一項：model-v6 要用 S4 的鏡像、A2、污染測試量，這一片不量）；Pitman–Yor。
- **停止條件**：沒有側檔時任何 golden 改變；第 5 項的上游檢查不成立（ΣN′ 或 N 陣列不同、`kn_total` 重算不等於檔頭）；第 5 項跨語言有任何一行不同；第 5 項列出的未變集合（含錯字回報 `--limit 66`）的 top1 和研究紀錄不同；延遲測試失敗；`kn.sjkn` 出現在主 checkout 或其他 worktree 的 `data/lm`。
- **預算**：executor 實作 1 次＋修正 1 次。
- **限制**：executor 不跑 188、不連網；不讀 `eval/holdout`、`~/side-project/shanjie-private`、學習檔、`~/Library`、鑰匙圈；不安裝、不啟動 App；不 push、不開 PR、不碰其他 worktree。
- **回滾**：revert 這一片；沒有側檔時本來就是關閉，使用者端不受影響。

## 5. 審查紀錄

- 第一次 plan-verifier（2026-10-10）：REVISE 3 點，全部 **FIX**。
  1. 決定性的比對驗不到預先彙總的 ΣN′（兩邊都讀檔頭）：`lm_eval.py` 照舊傳 `kn_classes` 讓 `lm.py` 重算比對；第 5 項加 ΣN′ = 23,700,571 與 N 陣列和 `fceb70cd…` 逐位元組相同。
  2. 錯字回報從 66 列變 69 列，不能直接對研究數字：只對內容沒變的集合（列出雜湊或 n），錯字回報用 `--limit 66`，停止條件同步。
  3. 側檔放錯地方會讓其他 checkout 都開著 KN：第 5 項用專用暫存目錄的實體複本；第 6 項只放這個 worktree 自己的 `data/lm`、用完移除；測試前確認不存在。
