# 契約：CI 加速（ci-speed）

狀態：修訂一（2026-10-17；plan-verifier 第一次 REVISE：基準數字、Swift 並行時的測試數量、`docs/verification.md` 的 CI 表）。實作後放寬兩處（研究紀錄 2026-10-17「CI 加速」）：§3.1 的 Swift 略過數改用測試名稱核對；§2.3 的快取只留在 core。

## 0. 依據

- 維護者 2026-10-17 看到 CI 的 core、shell 各跑十多分鐘（截圖時兩個 job 都還在跑），問「這真的沒有改善空間嗎？」。
- 量到的時間（PR #184 的 run 38945349011，xcode-27-arm64 runner）：
  - 兩個 job 同時在 18:12:10 開始；shell 683 秒、core 904 秒結束，所以「最慢 job 的結束時間」是 904 秒（15 分 4 秒），由 core 決定。（原本寫的 11 分 22 秒是 run 還在跑時 `gh pr checks` 顯示的經過時間，更正。）
  - core 904 秒：模型下載 7 秒；`cargo test`（debug）455 秒，其中編譯 52.6 秒，`engine_learn` 152 秒、`engine_lm` 87 秒；`cargo test --release` 252 秒；C 標頭冒煙 20 秒；Python 各步合計約 150 秒（最長的 model-v5 建置腳本 41 秒、X0c 33 秒、評測統計 29 秒）。debug 與 release 串著跑，CI 沒有任何 Rust 編譯快取。
  - shell 683 秒：`make bundle` 87 秒；`swift test` 467 秒，其中編譯 28.5 秒，其餘是測試依序執行（最慢的一組 126 秒）；selftest、驗收檢查、安裝程式、Homebrew cask 合計約 100 秒。
  - v0.4.7 的發版說明 PR 只改文件與 `eval/bench/results/*.json`，但結果檔不在 `scripts/ci-changes.sh` 的略過名單裡，PR 與 main 各跑了一次完整的 macOS 測試；發版的 gate 因此多等約 11 分鐘。CI 沒有任何測試讀 `eval/bench/results/`（`tools/test_bench.py` 不讀）。
- 發版的 gate（`scripts/check-ci-gate.sh`）看的是整個 `ci.yml` run 的結論，不看個別 job 名稱；main 的保護規則目前停用。所以 job 拆分不會讓 gate 漏檢。

## 1. 要做到什麼、怎麼看到

- 改程式的 PR，CI 的等待時間（最慢一個 job 的結束時間）變短；跑的測試一個都不少。
- 只改文件與 bench 結果檔的 PR（例如發版說明），macOS 的測試直接略過；合併到 main 後，前一個 main run 是綠的就同樣略過，發版的 gate 幾秒內通過。
- 看到它有效：本片的 PR 上量兩次 CI（第一次沒有快取、第二次有快取），和上面的基準比，結果寫進研究紀錄；維護者在 GitHub 上看到 run 的時間變短。

## 2. 改動

逐項做、逐項量；量起來沒有變快的項目拿掉，不硬留。

1. **略過名單**：`scripts/ci-changes.sh` 把 `eval/bench/results/*` 當成文件（和 `docs/*`、`*.md` 同一類）。
2. **core 拆成兩個並行的 job**：`core` 留下 release 測試、C 標頭冒煙測試與所有 Python 步驟；新 job `core-debug` 只跑 `cargo test --locked`（debug）。兩個 job 各自下載並核對模型（同一段步驟）。`needs`、`if` 條件和 `timeout-minutes` 照 `core`。
3. **Rust 編譯快取**：`actions/cache`（repo 已在用、已釘 SHA）快取 `~/.cargo/registry`、`~/.cargo/git` 與 `target/`，鍵含 runner 作業系統與架構、工具鏈版本（1.97.1）、`Cargo.lock` 的雜湊與 job 名稱。只快取編譯產物，不快取模型檔或測試輸出。量過之後只留在 `core`：release 編譯冷 58 秒、熱 43 秒；`core-debug`（29 → 32 秒）與 `shell` 沒有變快，照本節開頭拿掉。
4. **Swift 測試並行**：`swift test --parallel --xunit-output <暫存檔>`。並行時不會印 XCTest 的 `Executed N tests` 總計，測試數量改從 xunit 檔數（testcase 與 skipped 的個數）。如果出現原本沒有的失敗（例如共用偏好設定或暫存檔），這一項拿掉，記下原因。
5. **文件**：`docs/verification.md` 的 CI 表（core、shell 兩列）改成 `core`、`core-debug`、`shell` 三列，每列的內容和 `ci.yml` 一致；`ci.yml` 裡「cargo test already ran in the core job」這類提到 job 分工的註解跟著改。

## 3. 驗收

1. 測試數量不變：
   - Rust：拆分前後，debug 與 release 各自的 `test result: ok. N passed` 加總相同，`ignored` 的數量也相同。
   - Swift：改動前（依序執行）每個測試 bundle 的 `Executed N tests` 加總，和並行時 xunit 檔裡的 testcase 總數相同，而且兩邊的測試名稱逐一相同。SwiftPM 並行時 xunit 不標 XCTSkip（實測：唯一的 XCTSkip `CandidateCellTests.testBenchmark` 在 xunit 裡沒有 skipped），所以略過數不比，改看那個測試仍在名稱清單裡。
   - 其他步驟（C 冒煙、Python 各步、`make bundle` 等）照樣執行，結束碼都是 0。
2. 時間：本片 PR 上兩次 run（冷、熱快取）的每個 job 時間與「最慢 job 結束時間」寫進研究紀錄，和基準並列；每一項改動各自的效果盡量分開報（同一個 run 看各步驟時間）。
3. 略過名單：在本片 PR 之外，用一個只改 `eval/bench/results/` 的檔的分支測一次，`changes` 的輸出是 `run=false`、macOS job 是 skipped。
4. gate 不變：`scripts/check-ci-gate.sh` 不用改；在研究紀錄寫明理由（看 run 的結論）。

## 4. 停止條件

- 任何一項讓測試數量變少，或讓某個原本會跑的步驟不跑：拿掉那一項。
- `--parallel` 讓 Swift 測試出現不穩定的失敗：拿掉那一項，不為它改測試。
- 快取讓 CI 結果和沒有快取時不同（例如測試因為舊的建置產物而通過）：拿掉快取。

## 5. 範圍外

- 改測試本身（例如讓慢的測試變快、共用模型載入）：先看這一片的效果，再決定要不要另開一片。
- release.yml 的 build 工作。
- 換 runner 機型（要花錢的選項先問維護者）。

## 6. 限制

- 只改 `.github/workflows/ci.yml`、`scripts/ci-changes.sh`、`docs/verification.md` 的 CI 表與研究紀錄。
- 不改 GitHub 的設定（保護規則、runner、secrets）。
