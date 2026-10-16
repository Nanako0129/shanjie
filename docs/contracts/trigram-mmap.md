# 契約：三元表改用 mmap（trigram-mmap）

狀態：修訂一（2026-10-16；處理 plan-verifier 的第一次 REVISE：前提與各臂的 build，見 §7）。

## 0. 依據

- trigram-core 第 A 片量到：書面有表時重播的最大 RSS 423 MB，沒表 287 MB，多 136 MB（研究紀錄 2026-10-16「trigram-core 第 A 片」）。原因是 `Lm::load_opts` 用 `std::fs::read` 把 143.6 MB 的 `trigram.sjt3` 整檔讀進 `Vec<u8>`：匿名、已寫入的記憶體，活動監視器的「記憶體」欄（phys_footprint）全部算進去。
- 維護者 2026-10-16 選擇題：「現在就做、擋住 v0.4.7」。
- 檔案用 `mmap` 唯讀映射時，表的頁面是檔案背後的乾淨頁：系統記憶體吃緊時可以直接丟掉、要用再從磁碟讀回，phys_footprint 不算它們。〔推論，第 3 項驗收要量〕max RSS 仍會算進已讀過的頁面（載入時的檢查會讀過整個表），所以這一片看的指標是 peak memory footprint，max RSS 只報。

## 1. 要做到什麼、怎麼看到

- `core/src/trigram.rs` 的 `Tri` 不再持有 `Vec<u8>`，改持有整個 `trigram.sjt3` 的唯讀映射；解析、檢查（magic、V、D、綁定的模型 SHA-256、長度、`ctx_off` 單調、`next_id` 遞增）、打分、`+tri:` 標記的雜湊都不變，只是讀的位元組來自映射。
- 怎麼看到：
  - 開發者：重播程式在 `/usr/bin/time -l` 下的 peak memory footprint，書面有表時比 `Vec<u8>` 版少約一張表的大小；選字輸出逐位元組不變。
  - 使用者：活動監視器裡善解輸入法的「記憶體」欄，v0.4.7 比沒有 mmap 時少約 136 MB（實機由維護者看）。

## 2. 實作

- **前提**：trigram-core 第 C 片（PR #180：表進 `data/lm`、書面 golden 重產、重播程式的 `chat|formal` 參數）先合併，這個分支再合進 main，之後才做驗收。第 C 片沒合併就不做驗收（停止條件）。
- 映射：`core` 不加依賴，照 `core/src/learn_store.rs` 的做法直接宣告 `extern "C"` 的 `mmap`、`munmap`（macOS 與 x86_64 Linux 的 `PROT_READ`、`MAP_PRIVATE` 數值相同；`off_t` 都是 64 位元）。一個小型的映射型別：建立時開檔、用 `File::metadata()` 取長度（不另宣告 `fstat` 與 `struct stat`）、`mmap(PROT_READ, MAP_PRIVATE)`（失敗值是全 1 的指標 `!0`，不是 null）、關檔；`Drop` 時 `munmap`；`Deref<Target = [u8]>`；`unsafe impl Send + Sync`（唯讀，`Lm` 會在執行緒間共用）。長度 0、開檔或映射失敗 → `LmError::TriTable`（和現在讀檔失敗相同）。
- `Tri::parse` 收一個「位元組來源」：映射，或測試用的 `Vec<u8>`（玩具表、壞檔測試照舊用記憶體裡的位元組）。`Lm::load_opts` 讀 `trigram.sjt3` 時用映射；`bigram.sjlm`、`classes.sjc`、`kn.sjkn`、`tinylm.sjtl` 不變。
- 安全：映射期間檔案若被原地截短或改寫，讀到被截掉的頁面會收到 SIGBUS。安裝流程都不原地改寫：`scripts/install-ime.sh` 與安裝程式先 `ditto` 到暫存區再 rename 換上，舊版移成 `.shanjie-previous`，舊程序被結束；Homebrew cask 也是整個 bundle 搬移。這件事寫在映射型別的註解裡，並在 `docs/contracts/s3a.md` 記一行。
- Python 參考實作不動（`numpy` 本來就從檔案讀）。

## 3. 驗收

1. golden 不改：第 C 片合併後的 main（表在 `data/lm`，書面 golden 已是有表的版本）上全過的 golden，這個分支照樣全過、不改任何 golden 檔：Windows 機器 WSL 的完整 Rust 測試、Mac 的 `cargo test -p cli --test golden`。沒有表時（`NO_TRI=1` 的 WSL 跑法）也全過。
2. 單元測試：映射來源與 `Vec<u8>` 來源解析同一張玩具表，各個打分完全相同；長度 0 的檔、截短的檔、壞 magic 的檔經映射載入都回 `LmError::TriTable`；`Lm::load_opts` 載入的表確實是映射（測試看得到來源種類，不是只看結果）。突變：把 `Lm::load_opts` 改回 `std::fs::read`，那個「確實是映射」的測試要以斷言失敗。
3. **記憶體與延遲（main 在 Mac 量，決定性的一項）**：重播程式是第 C 片 C4 的 `tools/bench/replay`（第五個參數 `formal`），dev302，模型 model-v6＋classes-v5＋kn-v1。三個臂各跑三次、取中位數：(i) 沒表：第 C 片合併後的 main 的重播程式，模型資料夾不放表；(ii) 有表且 `Vec<u8>`：同一個 main 的重播程式，資料夾放表；(iii) 有表且映射：這個分支（已合進那個 main）的重播程式，資料夾放表。(i)、(ii) 用的 main commit 記進研究紀錄。每次用 `/usr/bin/time -l` 取 peak memory footprint 與 maximum resident set size，重播本身印每鍵 p95 與最大值。
   - 通過條件：(iii) 的 peak footprint 比 (ii) 少至少 100 MB；(iii) 的每鍵 p95 比 (i) 多不超過 1 ms（trigram-core 的上限不變）；重播的選字輸出三臂中 (ii) 與 (iii) 逐列相同。
   - 只報：三個臂的 max RSS、載入時間、最大延遲。
4. 實機（維護者）：v0.4.7 的實機檢查時，在活動監視器看善解輸入法程序的「記憶體」欄，記下數字（和 v0.4.6 比）。

## 4. 停止條件

- 第 C 片（PR #180）還沒合併。
- 第 3 項 footprint 沒少 100 MB（推論錯了）→ 停，回報量到的數字，問維護者（選項包含改成只在需要時讀表的其他做法，或接受 136 MB）。
- 任何選字輸出改變。
- p95 超過上限。

## 5. 範圍外

- `bigram.sjlm` 等其他模型檔改用映射（可以另開一片，同樣的型別；這一片只報 (i) 的 footprint 讓之後估算）。
- 跨版本基準工具的 RSS 欄改成 footprint（`tools/bench.py` 仍報 max RSS；發版說明寫 footprint 的實測數字）。
- 檔案格式、表的內容、Release。

## 6. 限制與回滾

- executor：不跑 Windows 機器、不連網；不讀 `eval/holdout`、repo 外的私人資料、學習檔、`~/Library`、鑰匙圈；不安裝、不啟動 App；不 push、不開 PR、不碰其他 worktree。
- 回滾：revert 這個 PR，回到 `std::fs::read`。

## 7. 審查紀錄

| 輪次 | 結果 | 處置 |
|---|---|---|
| plan-verifier（第 1 次） | REVISE 1 項：驗收第 1、3 項要靠第 C 片（書面 golden 重產、重播程式的 `formal` 參數），契約沒寫前提，也沒寫各臂用哪個 build | FIX：§2 加前提（第 C 片先合併，否則停），第 1 項改成「在第 C 片合併後的 main 上全過的 golden 照樣全過」，第 3 項寫明三個臂的重播程式與 commit；附帶建議（長度用 `File::metadata()`、`MAP_FAILED` 是 `!0`）一併寫進 §2 |
