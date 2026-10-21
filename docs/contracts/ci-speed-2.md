# 契約：CI 加速第二片（ci-speed-2）

狀態：修訂一（2026-10-22；plan-verifier 第一次 REVISE：每個跑 cargo 的 job 都釘工具鏈、`release.yml` 註解與 `app-sandbox.md` §2.6 第 5 步、Linux 上直接 return 的兩個測試、量哪兩次 run；第二次審查 READY）。實作後依 `/code-review` 改兩處：C 標頭冒煙測試移到 `swift`（`core` 是最慢的 job），Linux 的 job 釘 `ubuntu-24.04`（量測用的映像）。

## 0. 依據

- 維護者 2026-10-21：「你的ci跑太久了，優化一下」。
- **基準**（main `f0a28c38` 的 run 39621444983 第 1 次，xcode-27-arm64；時間是每個 job 自己的開始到結束）：
  - 整個 run 從第一個 job 開始到最後一個結束：822 秒。
  - `shell` 811 秒，其中 Swift 測試 394 秒。
  - `core-debug` 688 秒，其中 `cargo test`（debug）656 秒。
  - `core` 651 秒，其中 `cargo test --release` 459 秒、C 標頭冒煙測試 25 秒、Python 各步合計約 135 秒。
  - 10-17 第一片做完時 `core-debug` 是 278–385 秒；之後新加的常談、讀音、英文測試讓 Rust 測試變成將近兩倍。
- **量測**（PR #255，只量不合併）：
  - macOS runner 是 3 核、7 GiB（`sysctl`）。
  - Rust 測試的測試執行檔一個接一個跑，每個執行檔裡的測試同時跑：
    - release：各測試時間的總和 1,144 秒，執行檔合計 473 秒，平均同時有 2.4 個測試在跑。
    - CPU 已經接近滿載，同一個 job 裡再並行省不了多少，要省時間只能把工作分到別台機器，或減少工作。
  - 兩個套件各自的時間（run 39621444983 第 1 次）：

    | 部分 | release | debug |
    |---|---|---|
    | `core` 套件（622 個通過、14 個略過） | 235 秒 | 307 秒 |
    | `cli` 套件的整合測試（68 個） | 179 秒 | 279 秒 |
    | `cli` 套件的單元測試（8 個） | 3 秒 | 7 秒 |
  - Linux（ubuntu-latest，4 核、15 GiB）跑 debug：
    - `core` 套件 238–254 秒，比 macOS 快。
    - `cli` 套件 725–775 秒，是 macOS 的 2.4 倍以上，opt-level 3 也一樣。原因是 cli 的整合測試大量用小模型打分，Linux 沒有 Accelerate，走純 Rust 的矩陣乘法。
  - Python 各步在 Linux 全部通過（含 dev302 的 rowstats golden 逐位元組相同），整個 job 226 秒（run 39633313814）。
  - Swift：335 個測試，各測試時間總和 718 秒；172 個超過 1 秒；只建一個 Shell 的測試約 2 秒。
    - 本機量一次 release 建完整引擎約 0.85 秒：模型 0.26、預測索引 0.22、詞庫 0.14、常談 0.10、截頂詞庫 0.07、小模型 0.04。
    - runner 上的 Swift 測試大部分時間花在這裡。
  - GitHub 免費與 Pro 方案：整個帳號同時最多 5 個 macOS job（Actions limits 文件）。現在每次 run 佔 3 個。

## 1. 要做到什麼、怎麼看到

- 改程式的 PR 從推上去到 CI 全綠的時間變短。估計從約 14 分鐘變成約 8 分半，瓶頸是 macOS 的 release 測試。
- 每次 run 佔的 macOS 名額不變（3 個），macOS 的總機時從約 2,150 秒降到約 1,350 秒（估計）。
- 看到它有效：本片 PR 上量兩次 CI，每個 job 的時間與整個 run 的時間和基準並列，寫進研究紀錄。維護者在 GitHub 上看到 run 的時間變短。

## 2. 改動

只改 `.github/workflows/ci.yml`，新增 `scripts/fetch-models.sh`，並更新文件。

1. **job 的分工**（全部 `needs: changes`、`if: needs.changes.outputs.run == 'true'`，略過名單的行為不變）：

   | job | runner | 內容 |
   |---|---|---|
   | `core` | xcode-27 | 下載並核對模型、`cargo test --release --locked`（兩個套件全部） |
   | `debug` | ubuntu-24.04 | 下載並核對模型、`cargo test --locked -p core`、`cargo test --locked -p cli --lib --bins` |
   | `python` | ubuntu-24.04 | 下載並核對模型、寬鬆比對一致性（Rust 對 Python）、原本 `core` 的所有 Python 步驟（同樣的指令、同樣的順序） |
   | `shell` | xcode-27 | 原本的步驟，拿掉 Swift 測試 |
   | `swift` | xcode-27 | 下載並核對模型、`cargo build --release --locked -p core`、C 標頭冒煙測試、原本那一步 Swift 測試（`--parallel`、從 xUnit 印測試數） |

   拿掉 `core-debug`。會跑 cargo 的 job（`core`、`debug`、`python`、`swift`、`shell`）都有同一個釘住的 `dtolnay/rust-toolchain` 步驟（`toolchain: 1.97.1`）：repo 沒有 `rust-toolchain` 檔，golden 要逐位元組相同，工具鏈只靠這一步固定（`ci.yml` 的註解）。

   `swift` 和 `shell` 分在兩台 runner 上：Swift 測試不啟動任何沙盒組建（測試自己建暫存的假 bundle），不受 `app-sandbox.md` §2.6 的順序限制。
2. **`scripts/fetch-models.sh`**：CI 各 job 下載並核對模型的唯一一份（原本 `ci.yml` 有三份一樣的步驟，這片之後要用的 job 變成五個）。下載與雜湊核對照原本的步驟。`release.yml` 的下載不改（範圍外）。
3. **文件**：
   - `docs/verification.md` 的 CI 表改成上面五個 job，每列的內容和 `ci.yml` 一致。
   - `docs/data-files.md` 與 `docs/training.md` 的換版清單把「CI 三個 job（core、core-debug、shell）」改成 `scripts/fetch-models.sh`。
   - `ci.yml` 裡提到 job 分工的註解跟著改；`release.yml` 第 92 行「The same toolchain as ci.yml (core, core-debug and shell jobs)」的註解改成新的 job 名（只改註解，下載步驟不動）。
   - `docs/contracts/app-sandbox.md` §2.6 第 5 步加一句：`swift test` 改在 `swift` job 跑，不在 `shell` 的順序裡（理由同上）。

### 2.1 取捨（寫進 `ci.yml` 的註解與研究紀錄）

- **debug 檢查少了兩塊**：
  - cli 的 68 個整合測試（replay、golden、colloquial、english_sim、threads、tinylm）只在 release 跑。
    - 它們測的是評測工具（不出貨）與引擎在整份資料上的結果。
    - 引擎的 debug 檢查（整數溢位、標準函式庫在 debug 下的檢查）仍由 `core` 套件的測試在 Linux 上跑，裡面也有 dev302 的重播。
    - `core/src` 與 `cli/src` 只有 1 個 `debug_assert!`。
  - `core` 套件的 debug 改在 Linux 跑，只在 macOS 才編進去的分支不再有 debug 檢查：小模型呼叫 Accelerate 的部分、`learn_store.rs` 的 macOS 常數。這些在 macOS 的 release 測試照樣跑，Accelerate 呼叫前的長度檢查是 `assert!`，release 也檢查。
  - `core/tests/engine_digits.rs` 的 `row49_scores_match_the_python_golden_bit_for_bit` 與 `predict_digits_matches_the_python_golden_bit_for_bit` 在 Linux 直接 `return`（Linux 的 libm 末位不同）：在 Linux 的 debug 裡算通過、但什麼都沒檢查，所以 §3.1 的 622 看不出它們少了 debug 的覆蓋。它們照樣在 macOS 的 release 測試裡跑。
- **Python 改在 Linux 跑**：都是開發工具，golden 在 Linux 上逐位元組相同（PR #255 量過）。之後如果出現只在 macOS 才對的 golden，把那一步移回 macOS 的 job。
- **不把 release 再按套件拆成兩個 macOS job**：拆了估計再省約 1–1.5 分鐘，但每次 run 改佔 4 個 macOS 名額。名額是整個帳號共用，其他專案的 CI 也要用。
- **不拆最慢的 Swift 測試**：`VerticalShellTests.testPageKeysOutsideTheVerticalWindowTakeTheOffTablePath`（47 秒，照字母排在最後才開始跑）讓 Swift 測試多了約 40 秒的尾巴。照估計 Swift job 不是瓶頸，這片不改測試；之後 `swift` 變成最慢的 job 再拆。

## 3. 驗收

1. **測試數量**（同一個 commit 的 run）：
   - release：`core` job 的通過數與略過數加總，等於基準的 698 個通過、14 個略過。
   - debug：`-p core` 是 622 個通過、14 個略過；`-p cli --lib --bins` 是 8 個通過。
   - Swift：xUnit 的 testcase 數等於基準的 335。
2. **每一步都還在**：基準 `ci.yml` 的每一步都出現在新 `ci.yml` 的某個 job 裡，結束碼都是 0。對照表寫在 PR 說明，例外只有 §2.1 寫明的 debug cli 整合測試。
   - 對照表另列一欄：每個會跑 cargo 的 job 都有釘住的工具鏈步驟，log 印出 `rustc 1.97.1`。
3. **時間**：量的是 PR 上最後一個改到研究紀錄以外檔案的 head，它的 CI run 的第 1 次與第 2 次 attempt（第 2 次用一次 `gh run rerun`）。只量這兩次，不再重跑挑快的；之後只補研究紀錄的 commit 不重量，它那次 run 的時間也照列。
   - 兩次的每個 job 時間、整個 run 的時間寫進研究紀錄（run ID 與 attempt 編號），和基準並列。
   - 兩次都要比基準的 822 秒短。
   - runner 的快慢差可到將近兩倍（10-17 的研究紀錄），所以只寫跨 run 都成立的結論。
4. **舊名字不留**：`grep -rn 'core-debug'`（排除 `docs/research-log.md` 與 `docs/contracts/ci-speed*.md`）只剩歷史紀錄；`app-sandbox.md` §2.6 第 5 步和新的分工一致；§2.1 與 `ci.yml` 對應的註解寫到那兩個在 Linux 直接 `return` 的測試。
5. **略過名單**：用一個以本片分支為 base、只改一個 `.md` 檔的 PR 測一次。`changes` 印 `run=false`，五個 job 都是 skipped，run 的結論是 success。PR 關閉、不合併。
6. **gate 不變**：`scripts/check-ci-gate.sh` 看整個 `ci.yml` run 的結論，不看 job 名稱，不用改。

## 4. 停止條件

- 測試數量和 §3.1 對不上，或 §3.2 有一步沒跑：先修，修不了就把那一部分移回原本的 job。
- §3.3 量的兩次裡任何一次沒有比基準的 822 秒短：停下來回報，不合併。
- Python 或 debug 在 Linux 上出現 macOS 上沒有的失敗：那一步移回 macOS，記下原因。

## 5. 範圍外

- **引擎建立的速度**（產品）：模型載入、預測索引、詞庫解析。這是 Swift 測試與一部分 Rust 測試的主要成本，也是輸入法啟動與切換設定時重建引擎的成本，另開產品片。
- 減少測試的資料量（例如 cli 重播改用較少的列）、改測試本身。
- `release.yml`（它有自己的模型下載步驟）。
- 換 runner 機型（要花錢）。

## 6. 限制

- 只改 `.github/workflows/ci.yml`、`scripts/fetch-models.sh`、§2 第 3 項列的文件（含 `release.yml` 那一行註解與 `app-sandbox.md` 那一句）與研究紀錄。
- 不改 GitHub 的設定（保護規則、runner、secrets）。
- PR #255（量測用）在本片合併後關閉；`ci/probe-times` 分支刪前先問維護者。
