# 發版與 CI

這份寫的是現在的行為。

範圍：`.github/workflows/ci.yml` 的 job 分工與略過判斷、`scripts/fetch-models.sh`、`scripts/ci-changes.sh`、`scripts/check-ci-gate.sh`；`.github/workflows/release.yml` 的關卡、發版說明與 GitHub Release 的建立。簽章、公證與 Sparkle 的細節見 `docs/spec/install-and-update.md`；模型的命名與檔案見 `docs/spec/models.md`；評測與基準測試的量法見 `docs/spec/evaluation.md`。

## 1. CI 的觸發與略過

### 1.1 觸發與並行

- `ci.yml` 在每個 pull request 與每次推到 `main` 時跑；不在 tag 上跑。
- 並行群組：PR 以 PR 編號為鍵，新的推送取消舊的 run；推到 `main` 以 run id 為鍵，每個 main commit 各留一個完整的 run（發版關卡要用，見 §5.3）。
- 預設權限 `contents: read`；只有 `changes` 多一個 `actions: read`（推到 main 時要查前一個 main run 的結論）。
- workflow 永遠會跑：test 類 job 被略過時整個 run 的結論仍是 `success`，所以每個 main commit 都有一個已結束的 run。

### 1.2 `changes` job：要不要跑測試

- 用 `git diff --no-renames --name-only` 列出變動的路徑，交給 `scripts/ci-changes.sh`，輸出 `run=true` 或 `run=false`，其餘 job 都 `needs: changes`、條件 `needs.changes.outputs.run == 'true'`。
- 比較範圍：PR 用 merge base（三個點）；推到 main 用兩棵樹直接比（兩個點），所以 force push 掉的 commit 也算。`--no-renames` 是為了不去取檔案內容；改名時舊路徑與新路徑都列出，結果偏向保守（多跑）。
- checkout 用 `fetch-depth: 0` 加 `filter: blob:none`：要完整歷史，不要檔案內容。
- `scripts/ci-changes.sh` 的規則：
  - 只有下列路徑時 `run=false`：`docs/*`（含 `docs/spec/*`）、`site/*`（含 `site/data/*`）、`scripts/deploy-site.sh`、`*.md`、`eval/bench/results/*`、`eval/bench/kpi/*`。這些沒有任何建置或測試會讀（`eval/bench/results/` 只有 `tools/bench.py` 讀、`eval/bench/kpi/` 只有 `tools/kpi.py` 讀，CI 不跑它們；`site/data/*` 只有網站頁讀）。
  - `LICENSES/*` 一律 `run=true`：它會被打包進 App（`scripts/build-app.sh`）。
  - 其他路徑一律 `run=true`。
  - 沒有輸入（空的或讀不到的 diff）時 `run=true`：不讓壞掉的 diff 略過測試。
- 兩種情況直接 `run=true`：比較基準是全零（新分支），或基準 commit 不在歷史裡（force push）。
- **推到 main 時的額外條件**：算出 `run=false` 之後，還要前一個 main commit 的 `ci.yml` push run 結論是 `success`（`gh api …/actions/workflows/ci.yml/runs?head_sha=<前一個 commit>&event=push&branch=main`，取最後建立的那個；查不到記為 `none`），否則改成 `run=true`。理由：只改文件的 commit 如果接在紅的、被取消的或沒跑完的 run 後面就略過，整個 run 會是 `success`，發版關卡就會放行最後一次紅燈的程式。這個檢查看的是決策當下的前一個 run；之後重跑那個 commit 才變紅，不會回頭影響已略過的 commit。
- 逾時 5 分鐘。

### 1.3 驗證略過判斷

- 只改 `eval/bench/results/` 的 PR：`changes` 輸出 `run=false`，其餘 job 全是 skipped，run 結論 `success`。
- 只改一個 `.md` 的 PR 同樣。
- 只改 `site/data/*.json`、`eval/bench/kpi/*` 或 `docs/spec/*` 的 PR 同樣；同一個 PR 再加 `core/src/lib.rs` 之類的程式路徑就 `run=true`。手動核對：`printf 'site/data/bench.json\n' | scripts/ci-changes.sh` 印 `run=false`，`printf 'core/src/lib.rs\n' | scripts/ci-changes.sh` 印 `run=true`。
- `scripts/ci-changes.sh` 沒有單獨的測試腳本；上面兩種用實際的 PR 看 `changes` 的輸出。

## 2. job 一覽

| job | runner | 內容 | 逾時 |
|---|---|---|---|
| `changes` | ubuntu-latest | §1.2 | 5 分 |
| `core` | xcode-27（macOS） | 下載並核對模型；`cargo test --release --locked`（兩個套件全部） | 35 分 |
| `debug` | ubuntu-24.04 | 下載並核對模型；`cargo test --locked -p core`；`cargo test --locked -p cli --lib --bins`；評測 CLI 的 release 建置與六份 rowstats（Windows 比對的 Linux 側） | 20 分 |
| `windows` | windows-2025 | 見 §3.3；`needs: changes`，和其他 job 同時開跑 | 45 分 |
| `windows-cap` | windows-2025 | `core/tests/engine_cap.rs`（release），與 `windows` 並行（§3.3） | 30 分 |
| `rowstats` | ubuntu-24.04 | Linux 與 Windows 的六份 rowstats 逐位元組比對（§3.3）；`needs: [changes, debug, windows]` | 5 分 |
| `python` | ubuntu-24.04 | 下載並核對模型；Python 工具與資料建置腳本的測試 | 20 分 |
| `swift` | xcode-27 | 下載並核對模型；核心靜態庫；C 標頭冒煙測試；Swift 測試 | 30 分 |
| `shell` | xcode-27 | 沙盒組建、容器移轉、selftest、驗收檢查、安裝程式、Homebrew cask（不含 Swift 測試） | 30 分 |

- 每次 run 同時佔 3 個 macOS runner（`core`、`swift`、`shell`）。GitHub 免費與 Pro 方案整個帳號同時最多 5 個 macOS job，名額由帳號下所有專案共用，所以不再按套件把 release 測試拆成更多 macOS job。
- 所有 action 都用 commit SHA 釘住（註解寫版本）。
- 所有 checkout 都 `persist-credentials: false`。

### 2.1 工具鏈

- 每個跑 cargo 的 job（`core`、`debug`、`windows`、`windows-cap`、`python`、`swift`、`shell`）都有同一個 `dtolnay/rust-toolchain` 步驟，`toolchain: 1.97.1`；action 本身釘在 master 的一個 commit（它的 `stable` 分支每次合併都重生）。
- repo 沒有 `rust-toolchain` 檔，工具鏈只靠這一步固定。理由：評測分數要與 Python 參考實作的 golden 逐位元組相同，編譯器的程式碼生成變動曾讓一個分數差 1 ulp（`powf` 被降成 `exp10`）。
- `release.yml` 的 `build` 用同一個版本與同一個 action；改版本要兩個檔一起改。
- 工作區的 debug 測試用 `[profile.test] opt-level = 1`（`Cargo.toml`），保留 debug assertions 與整數溢位檢查；完全不最佳化時，逐鍵的預測掃描讓 dev302 重播超過 20 分鐘的 job 上限。

### 2.2 模型下載：`scripts/fetch-models.sh`

- 所有 `ci.yml` job 下載與核對模型的唯一一份；`release.yml` 的 `build` 另有自己的一份（相同的資產與核對，見 §5.1）。
- 用 `gh release download <Release 代號> --repo $GITHUB_REPOSITORY -p <檔名> -D data/lm` 抓七個 Release 的資產：通語（`bigram.sjlm`）、辨類（`classes.sjc`）、承接（`kn.sjkn`）、會意（`tinylm.sjtl`）、連文書面與聊天（`trigram.sjt3`、`trigram-chat.sjt3`）、常談（`colloquial.sjlm` 與 `colloquial-attribution.tsv`）。現行 Release 代號與版本以 `data/models.tsv` 與 `docs/spec/models.md` 為準。
- 抓完立刻用進版控的 `data/*.sha256` 核對（`shasum -a 256 -c`；Windows runner 的 Git Bash 沒有 `shasum` 時改用 `sha256sum -c`，檔案格式相同）。核對放在建置之前，讓 job 在幾秒內帶著清楚的訊息失敗，不是建完才失敗。
- 需要 `gh` 與 `GH_TOKEN`（Actions 的 `github.token`）；repo 用 Actions 設好的 `$GITHUB_REPOSITORY`。
- 換模型時，雜湊檔、`cli/tests/golden.rs`（重複前兩個模型的雜湊）、`release.yml` 的下載要在同一個 PR 改；先把新雜湊合進 main 會讓 CI 拿舊檔對新雜湊而失敗。

## 3. 各 job 的內容

### 3.1 `core`（macOS）

- `cargo test --release --locked`，兩個套件（`core`、`cli`）全部。
- 必須在 release 與 macOS 上跑的原因：
  - 重播測試的逐鍵 p95 < 16 ms 斷言只有 release 才有意義；
  - 兩個 f64 golden（`core/tests/engine_digits.rs` 的 `row49_scores_match_the_python_golden_bit_for_bit`、`predict_digits_matches_the_python_golden_bit_for_bit`）在 Linux 的 libm 末位不同，只有 macOS 實際比對；
  - 小模型在 macOS 走 Accelerate，在 Linux 走純 Rust 矩陣乘法。

### 3.2 `debug`（Linux）

- 目的：debug 才有的檢查（整數溢位、標準函式庫的 debug 專屬檢查），放在 Linux runner 上跑，與 macOS 的 release 測試並行。
- 只跑 `core` 套件的全部測試、`cli` 套件的單元測試（`--lib --bins`），與 Windows 線上格式 `shanjie-win-proto` 的測試（純 Rust，三個平台都跑）。
- 評測 CLI（`shanjie-eval`）用 release 建置，再由 `scripts/ci-rowstats.sh rowstats-linux` 跑六份 rowstats：dev302、打字測驗、錯字回報，各在聊天與書面設定下，用出貨的旗標（前文、`acg` 與 `it` 詞包、會意小模型；聊天另加常談表）。檔案以 artifact 上傳（保留 1 天）；比對在 `rowstats` job 做，對象是 `windows` 上傳的那六份。
- `scripts/ci-rowstats.sh` 是這六條指令的唯一一份；`--threads` 不改輸出的任何位元組。

### 3.3 `windows`

- `needs: changes`，和其他 job 同時開跑，被略過的條件與 `core` 相同；所有步驟用 Git Bash，不用 PowerShell。
- 內容：
  1. 下載並核對模型（§2.2）；
  2. 核心套件除了 `core/tests/engine_cap.rs` 以外的全部測試，release：`--lib --bins`，加上 `core/tests/*.rs` 除了 `engine_cap.rs` 以外每個檔的 `--test <檔名>`（清單在 job 裡由檔名產生，新增的測試檔自動跑到；核心套件沒有 `[[test]]` 改名、examples 與 benches，所以檔名就是測試目標名；產生的數量不對就失敗），再另跑 `--doc`；略過七個逐鍵 p95 斷言的測試（4 vCPU 的 runner 量不準；它們的選字斷言在 Linux 與 macOS 的 job 照跑）；
  3. 同樣的評測 CLI 與六份 rowstats，以 artifact `rowstats-windows` 上傳（保留 1 天）；
  4. 工作樹沒有 CRLF（`git ls-files --eol data eval scripts core/tests cli/tests`：不得有 `w/crlf`、`w/mixed`，每個 `i/lf` 都是 `w/lf`；理由：`digits.tsv` 帶 CRLF 會無聲地關掉數字層）；
  5. 核心也為 `i686-pc-windows-msvc`、`aarch64-pc-windows-msvc` 編譯（只建置，不跑測試）；
  6. `shanjie-tip`（Windows 文字服務 DLL）與 `shanjie-host`（常駐程序）以 release 建置，各帶與不帶 `timing` 功能一次；核對兩者都沒有 C 執行階段的匯入、DLL 沒有 `shanjie_` 匯出也不相依核心；再跑 `shanjie-win-proto`、`shanjie-tip`、`shanjie-host` 的測試（`docs/spec/windows-tip.md` §5.10）。
- 比對在另一個 job `rowstats`（ubuntu-24.04，逾時 5 分，不 checkout）：`needs: [changes, debug, windows]`，下載兩邊的 artifact，逐檔 `cmp`，兩邊都必須剛好 6 份。拆成自己的 job，是為了讓 `windows` 不必等 `debug` 跑完才開始（以前最長的一條是 `debug` 加 `windows`）。`debug` 與 `windows` 都成功時它一定會跑；其中一個失敗時它被略過，但整個 run 照樣是 failure，所以比對只會跟著 `changes` 判定整趟不跑而略過。
- `core/tests/engine_cap.rs` 在另一個 job `windows-cap`（windows-2025，逾時 30 分，`needs: changes`，略過條件與 `core` 相同）用 release 跑，與 `windows` 並行。它沒有逐鍵 p95 的測試。拆出來的理由：Windows 沒有 Accelerate，小模型的重排走純 Rust，而這個檔每個測試打 70–128 個音節、每一鍵都重排，在三次 run 裡占了 `windows` 核心測試時間的 33–52%（302／589／463 秒；同一個檔在 macOS 是 56–83 秒），runner 的快慢差也主要出在它。
- Windows 版本身的規格見 `docs/spec/windows-tip.md`。

### 3.4 `python`（Linux）

- 都是開發工具與資料建置腳本的測試，golden 在 Linux 上與 macOS 逐位元組相同（含 dev302 的 rowstats）。
- 環境變數 `SHANJIE_TW_CLEAN=off`：台灣用語轉換表不在 repo 裡，Python 測試用自己的表或不用表。
- 步驟（照 `ci.yml` 的順序）：
  1. 寬鬆比對一致性（Rust 與 Python 對同一份探針的輸出相同，變體表確實有套用）；
  2. 變調列（sandhi）、公開探針與 unigram overlay 的重建與比對；
  3. 在拋棄式 venv 裝 `numpy==2.5.4`（需要 numpy 的步驟用；其餘步驟用 runner 的 `python3`）；
  4. 評測統計（`tools.test_evalstats`），與 Python 版 dev302 rowstats 逐位元組等於 `eval/golden/s2-lm-dev302-{chat,formal}.rowstats`（書面那一份讀三元表，所以在 numpy venv 裡跑）；
  5. 詞包與詞庫建置腳本的測試（動漫與遊戲詞包、資訊與 AI 詞包、迷因與實況用語詞包、美妝詞包、人名詞庫、變調建置、`pack-oov` golden 的 Python 一半）；
  6. 模型建置腳本（`tools/test_build_lm.py`、`tools/test_extra_lexicon.py`）、承接檔（`tools/test_kn_cont.py`）、基準測試工具（`tools/test_bench.py`）、讀音解碼豁免表、KPI 表工具、英文詞表建置、覆蓋詞與讀音、常談署名表、保留集守衛、三元表（`tools/test_build_trigram.py`，需要 numpy venv）。
- 如果出現只在 macOS 才成立的 golden，把那一步移回 macOS 的 job。

### 3.5 `swift`（macOS）

- 下載並核對模型；`cargo build --release --locked -p core`（產生 `macos/Package.swift` 連結的 `target/release/libcore.a`）；不需要 `make bundle`。
- **C 標頭冒煙測試**：先用 `cargo rustc … --print native-static-libs` 取得需要連結的系統函式庫並確認非空（macOS 的 `cc` 少了這些也能連結，空的擷取會無聲地通過），再用 `cc -std=c11 -Wall -Wextra -Werror` 編 `core/tests/c/abi_smoke.c`，執行兩次：一次只給詞庫與模型，一次再給一個全新的學習資料夾（跑學習的檢查）。
- **Swift 測試**：`swift test --package-path macos --force-resolved-versions --parallel --xunit-output <暫存檔>`，之後從 xUnit 檔印出 testcase 數。
  - `--parallel` 每個測試方法一個 xctest 程序。
  - 並行時 XCTest 不印 `Executed N tests` 總計，所以數量從 xUnit 數；SwiftPM 並行時 xUnit 不標 `XCTSkip`，所以沒有略過數，要確認某個被略過的測試仍在，就看 testcase 名稱清單。
  - 包含行為測試 `LogTests.testShellLogsNothingButStaticTextAndCodes`，它需要 `/usr/bin/log stream`，runner 的使用者可以執行（`docs/spec/privacy.md`）。
- 不啟動任何沙盒組建：安裝程式測試自己建拋棄式的假 bundle，所以不受 §3.6 的順序限制。

### 3.6 `shell`（macOS）

依序做下列步驟（順序是規格：第 3 步之前不得啟動任何沙盒組建）：

0. 預設組建（開發版 ID `com.nyanako.inputmethod.shanjie.dev`、名稱「善解（開發版）」/ `Shanjie (Dev)`、沒有移轉清單），只建置並核對 Info.plist 與 `InfoPlist.strings`。
1. 正式 ID 組建（`BUNDLE_ID=com.nyanako.inputmethod.shanjie`），只建置；`container-migration.plist` 的 `Move` 恰為 `${ApplicationSupport}/shanjie`。
2. 沒有移轉清單的拋棄式組建（`…ci-nomanifest`），只建置。
3. `check-app.sh` 在 `CI` 未設定時拒絕執行正式 ID 的組建，且不得建立正式 container；`SKIP_RUN=1` 時通過第 2、3 項檢查。
4. 第一批沙盒啟動：容器移轉測試（`scripts/test-sandbox-migration.sh`，有沒有清單的兩種情況），逾時 10 分鐘（容器讀取若觸發系統隱私詢問會卡住）；並確認 CI 未設定或家目錄是暫存目錄時，這個測試拒絕執行且不寫入該目錄。
5. 發版關卡腳本對假 `gh` 的測試（`scripts/test-check-ci-gate.sh`，§5.3）。
6. 關於頁模型清單的檢查（`scripts/test-check-models.sh`）。
7. 安裝腳本在暫存家目錄與假 App 下的檔案處理（`scripts/test-install-ime.sh`；不可能註冊任何東西）。
8. 內附 selftest（拋棄式 ID）：執行前該 ID 的 container 不存在、執行後存在（證明在沙盒裡執行）。
9. 正式 ID 組建的驗收檢查（`scripts/check-app.sh`）。
10. entitlements 與 Sparkle 的十種變造（`scripts/test-sandbox-mutations.sh`，只檢查變造後的副本，不啟動）。
11. 安裝程式的檢查（`make installer`、`scripts/check-installer.sh`，只檢查不啟動）。
12. Homebrew cask：`scripts/test-render-cask.sh`，再把 `scripts/render-cask.sh` 產生的 cask 放進 `Casks/` 目錄跑 `brew style`（brew 只在 `Casks/` 目錄下套 cask 規則）。

## 4. 取捨與已知限制

- **為什麼拆成這些 job**：macOS runner 是 3 核、7 GiB；Rust 測試的執行檔一個接一個跑、每個執行檔內的測試同時跑，release 時平均同時約 2.4 個測試在跑，CPU 已接近滿載，同一個 job 裡再並行省不了多少，只能把工作分到別台機器或減少工作。所以：debug 與 Python 搬到 Linux，Swift 測試與 C 冒煙測試搬到 `swift`，最慢的 `core` 只剩 release 測試。
- **`cli` 的整合測試不在 debug 跑**：Linux 沒有 Accelerate，小模型走純 Rust 矩陣乘法，`cli` 的整合測試（重播、golden、常談、英文模擬、執行緒、小模型）在 Linux 上是 macOS 的 2.4 倍以上（opt-level 3 也一樣）。它們測的是評測工具（不出貨）與引擎在整份資料上的結果，只在 release 跑；引擎本身的 debug 檢查由 `core` 套件在 Linux 跑（含 dev302 重播）。`core/src` 與 `cli/src` 只有一個 `debug_assert!`。
- **debug 看不到的兩塊**：只在 macOS 才編進去的分支（小模型的 Accelerate 呼叫、`learn_store.rs` 的 macOS 常數），以及上述兩個在 Linux 直接 `return` 的 f64 golden（在 Linux 的 debug 裡算通過但什麼都沒檢查）。它們在 macOS 的 release 測試照跑；Accelerate 呼叫前的長度檢查是 `assert!`，release 也檢查。
- **`--parallel` 的代價**：一個測試殘留給下一個測試的狀態，在同一個程序內才會被看到；並行時每個方法一個程序，抓不到這種問題，只有序列的 `make test-swift` 抓得到。
- **沒有 Rust 編譯快取**：沒有 `actions/cache` 步驟。編譯只佔各 job 的一小段，其餘是測試本身的執行時間。以 main 一次 push CI 的 log 為例：最慢的 `core`，`cargo test --release` 共 1017 秒，其中編譯 59.7 秒；`windows` 的核心測試編譯 56.3 秒，`shanjie-tip`、`shanjie-host` 的四次建置共 45 秒；`debug` 41.3 秒；模型下載每個 job 6–12 秒。快取命中時，每次合併幾乎都會改到的自己的 crate 照樣重編，省得到的只有相依套件那一段，不值得承擔快取留下錯誤建置的風險。
- **`cli/tests/replay.rs` 同時最多兩個引擎**：每個引擎約 700 MB。以前一次一個，在 `core` job 跑 175–209 秒，是最慢的測試檔。在 Mac 上用 3 個測試執行緒（模仿 3 核的 macOS runner）交錯量四次，加總測試程序與它所有子程序的 RSS：一次一個 108.9、108.9 秒，峰值 1166、1066 MiB；同時兩個 66.0、66.7 秒，峰值 1856、1643 MiB（runner 是 7 GiB）。
- **不拆最慢的 Swift 測試**：`VerticalShellTests.testPageKeysOutsideTheVerticalWindowTakeTheOffTablePath` 約 47 秒、照字母排在最後才開始跑，讓 Swift 測試多了約 40 秒的尾巴；`swift` 變成最慢的 job 時才拆。
- **引擎建立的成本**：Swift 測試與一部分 Rust 測試的主要成本是建立完整引擎（本機 release 約 0.85 秒：模型 0.26、預測索引 0.22、詞庫 0.14、常談 0.10、截頂詞庫 0.07、小模型 0.04），這也是輸入法啟動與切換設定時重建引擎的成本；CI 不處理它。
- runner 的快慢差可到將近兩倍，所以比較 CI 時間只下跨 run 都成立的結論。
- 不改 GitHub 的設定（保護規則、runner、secrets）。

## 5. 發版：`release.yml`

### 5.1 觸發與 job

- `push` 一個 `v*` tag：關卡、建置、簽章、發佈。
- `workflow_dispatch`（只能在 `main` 上）：乾跑，做關卡、建置、簽章與驗證，不簽 feed、不發佈；兩個簽好的 zip 留成一天的 artifact `signed`，給虛擬機與實機驗收。
- 其他事件與 ref 一律拒絕（`gate`、`sign`、`publish` 各自再檢查一次）。
- 頂層 `permissions: {}`，每個 job 自己寫權限。簽章材料與 tap 金鑰只存在 `release` Environment 的 secret，不放 repo secret；`sign` 與 `publish` 進這個 Environment，且各自只引用自己需要的 secret。
- `gate`（ubuntu-latest，40 分）：§5.2、§5.3。
- `build`（xcode-27，30 分，沒有 secret）：用同一個 `rust-toolchain` 1.97.1；自己下載與核對七個模型資產（`release.yml` 內的一份，雜湊檔與 `scripts/fetch-models.sh` 相同）；`scripts/build-app.sh`（`SHANJIE_RELEASE_BUILD=1`、正式 bundle ID）與 `scripts/build-installer.sh build`，產物以 tar 傳給 `sign`（tar 保留模式與符號連結，upload-artifact 不保留）。
- `sign`、`publish`：見 `docs/spec/install-and-update.md`；本份只涵蓋發版說明與 Release 的建立（§5.4–§5.6）。

### 5.2 關卡：發版說明必須在 repo 裡

- 在 `gate` 的「等 CI」之前執行，缺檔一秒內就失敗。
- 只在 `push` 事件檢查（乾跑不發佈，不檢查）。
- `docs/releases/$GITHUB_REF_NAME.md` 必須存在，且去掉空白後仍有內容（`grep -q '[^[:space:]]'`），否則以錯誤結束，訊息寫出缺哪個檔。
- `gate` 的 checkout 用 sparse 只取 `scripts` 與 `docs/releases`，且不帶任何 secret。

### 5.3 關卡：CI 必須是綠的

- CI 不在 tag 上跑，所以 `gate` 執行 `scripts/check-ci-gate.sh "$GITHUB_SHA"`：被 tag 的那個 commit 在 `main` 上最新一次 `ci.yml` 的 push run 必須已結束且結論是 `success`。它防的是失誤，不是安全邊界（tag 的 commit 自己帶著這支腳本）。
- 只看整個 run 的結論，不看個別 job 名稱，所以 job 怎麼拆都不影響關卡。
- 參數：`CI_GATE_INTERVAL`（輪詢間隔，預設 30 秒）、`CI_GATE_TIMEOUT`（run 還在跑時最多等 1800 秒）、`CI_GATE_GRACE`（run 還不存在時最多等 300 秒：tag 可能比 CI 先到）；`CI_GATE_GH` 可替換 `gh`（給測試用）。
- 失敗的情形：run 的結論不是 `success`；等待逾時；連續三次讀不到 API；commit SHA 不是 40 個十六進位字元（結束碼 2）。失敗訊息附復原做法：`main` 上 CI 綠了之後重跑這個 Release run；tag 打在錯的 commit 要在合併 commit 上重打。
- **appcast 的 commit**：`publish` 會用 `GITHUB_TOKEN` 把 `appcast.xml` 寫回 main，這種 push 不觸發 `ci.yml`。關卡看到某個 commit 相對於它唯一的父 commit 只改了 `appcast.xml`，就改看父 commit 的 run，最多往回走 20 個；有兩個父 commit、或除了 `appcast.xml` 還改了別的檔，都不往回走。
- 測試：`scripts/test-check-ci-gate.sh`（對假 `gh`，無網路）涵蓋 appcast-only 的 commit 用父 commit 的 run 判斷，以及同時改了 `appcast.xml` 與其他檔的 commit 不被略過；`ci.yml` 的 `shell` job 執行它。

### 5.4 發版說明檔

- 每個 GitHub Release 的說明本文是 repo 裡的一個檔：`docs/releases/<tag>.md`，檔名就是 tag（`v0.6.1.md`、資料類 Release 則是 `sj-lm-7-9.7m.md` 這類）。繁體中文、手寫；標題不在檔內（App 版標題由 `release.yml` 給「善解 <版本>」，資料類 Release 建立時另給）。
- 不用 `--generate-notes`；repo 以此檔為準：在網頁上改了說明，要同步回這個檔。
- 推 `v*` tag 之前，說明檔先寫好、經 PR 合併到 `main`；跨版本基準測試與保留集也要先跑完、結果進 `main`（量法見 `docs/spec/evaluation.md`）。
- 說明檔同時是 Sparkle feed 的內嵌發版說明：`sign` 在推 tag 時把它當 markdown 嵌進新的 appcast 項目（Sparkle 用純文字視圖顯示：沒有網頁視圖、遠端資源與 JavaScript），所以內容必須是能這樣顯示的 Markdown。
- 草稿標記：說明檔在 Release 建好之前可以有「〔草稿…〕」一行；建 Release 前要先用 PR 拿掉。資料類 Release 的建立指令前先跑 `! grep -q '〔草稿' docs/releases/<tag>.md`，有標記就停。這一條只寫在 `docs/releases/README.md` 的流程裡，`release.yml` 不檢查。

### 5.5 `publish`：建立 Release 與 Latest

- `publish` 的 checkout 取 `scripts`、`packaging`、`docs/releases`。
- 先核對兩個 zip（輸入法與安裝程式）的 `.sha256`。
- Release 已存在（重跑這個 job）：兩個 zip 都必須已是附件，且兩個 `.sha256` 與本次的逐位元組相同，否則失敗（cask 不能指向不同的 zip）；通過就不重建，並比對 Release 本文與說明檔，不同時印 `::warning::`，不失敗。
- Release 不存在：`gh release create $GITHUB_REF_NAME <兩個 zip 與兩個 .sha256> --title "善解 $VERSION" --notes-file "$GITHUB_WORKSPACE/docs/releases/$GITHUB_REF_NAME.md" --latest=<true|false>`。
- **Latest**：只有版本號最大的 `v*` Release 標成 Latest。取 `gh release list --limit 200` 的 tag 加上本次的 tag，只留 `v` 開頭，`sort -V | tail -n 1`，等於本次 tag 才 `--latest=true`，否則 `--latest=false`；替舊版本線補發的修正版不搶 Latest。tag 是單純的 `X.Y.Z`，沒有預發版。清單用單純賦值取得，讓列舉失敗時 `errexit` 讓步驟失敗（放在管線的命令替換裡不會，tag 就會被預設標成 Latest）。
- 版本字串必須符合 `^[0-9]+\.[0-9]+\.[0-9]+$`，否則 `build` 失敗；乾跑的版本是 `0.0.0`。

### 5.6 資料類 Release

- 模型與資料的 Release（`sj-…`，以及舊代號 `model-v…`、`classes-v…`、`tinylm-v…`）手動建立，說明檔同樣先進 `main`；一律 `--latest=false`，讓 Releases 頁的 Latest 永遠是輸入法的最新版。
- 上傳的是雜湊檔記的那個路徑（`data/lm/<檔名>`，附件名就是檔名）。上傳前用進版控的雜湊檔核對，上傳後重新下載、用同一個雜湊檔再核對一次，兩次都看結束碼。
  - 指令：`shasum -a 256 -c data/<檔名>.sha256`；`gh release create <tag> data/lm/<檔名> --notes-file docs/releases/<tag>.md --latest=false`；`gh release download <tag> -p <檔名> -D <暫存>/data/lm` 後在暫存資料夾裡再核對。
  - 換掉現有檔時，雜湊檔在切換的 PR 才改，建 Release 當下 repo 的雜湊檔與 `data/lm/` 都還是舊的：改在暫存資料夾放新檔，上傳前後都用說明檔裡寫的雜湊核對（`echo "<雜湊>  data/lm/<檔名>" | shasum -a 256 -c`）。
  - 第一次加的新檔沒有人讀，雜湊檔可以和說明檔一起先進 `main`。
- 常談（`sj-cl-…`）有三個附件：`colloquial.sjlm`、`colloquial-attribution.tsv`（雜湊檔 `data/colloquial-attribution.tsv.sha256`）與 NOTICE（`docs/releases/<tag>.NOTICE.txt`，列來源的授權；CC BY-SA 4.0 的檔案要附署名）；下載時用兩個 `-p`。建立指令寫在該 Release 的說明檔。
- 換模型的發版也要改 `data/models.tsv` 那一列（雜湊與對外名稱）；關於頁的模型列表跟著它走，建置與 `scripts/check-app.sh` 會核對雜湊。

## 6. 驗證

- 關卡腳本：`scripts/test-check-ci-gate.sh`（CI 的 `shell` job 跑）。
- 說明檔關卡：`release.yml` 的 shell 片段沒有單獨的測試；改它時抽出來在本機跑四種情況：檔案不存在（結束碼非 0、訊息含檔名）、只有空白（非 0）、有內容（0）、`workflow_dispatch`（0，不檢查）。
- `rg -- '--generate-notes' .github` 不得有輸出。
- 推 tag 後核對：Release 本文等於說明檔、Latest 標在對的版本；`main` 的 `appcast.xml` 有這一版的項目（步驟見 `docs/releases/README.md` 與 `docs/spec/install-and-update.md`）。
- 改動 `ci.yml` 的 job 分工時：每一步都還在某個 job 裡、結束碼 0；每個跑 cargo 的 job 的 log 印出 `rustc 1.97.1`；`grep -rn 'core-debug'` 沒有結果；§2 的 job 表與 `ci.yml` 一致。
- 測試數量不能因拆分而變少：`core` job 的通過數與略過數、`debug` job 的 `-p core` 與 `-p cli --lib --bins`、`swift` job 的 xUnit testcase 數，合起來不得少於拆分前的總和。

## 7. 本機關卡

所有指令都在 repo 根目錄執行，需要 `data/lm/` 的七個模型檔與兩個署名檔，雜湊以 `data/*.sha256` 為準（下載見 `docs/spec/models.md` §11）。各 script 的檢查內容見 `docs/spec/install-and-update.md` §10.2、§10.3。

| 指令 | 內容 |
|---|---|
| `make test` | `test-rust`、`test-python`、`test-swift` 三個目標依序跑；`make test-golden` 只跑 golden 比對。改動跑哪一塊：改 Rust 跑 `test-rust`，改任何 Python（含 `reference/proto/`、`tools/`）跑 `test-python`，改 Swift 跑 `test-swift`，跨語言就都跑 |
| `make test-rust` | `cargo test --release --locked` |
| `make test-python` | Python 單元測試（工具與評測，清單在 `Makefile`；需要 python3、numpy、真的模型與 cargo 建出來的評測 CLI）；詞包建置工具的兩個真 CLI 測試（`test_build_acg_pack`、`test_build_quote_pack`）需要環境變數 `SHANJIE_MOEDICT`（教育部辭典 JSON）與 `SHANJIE_HETERO`（破音字表），沒設時印 `NOTE: SHANJIE_MOEDICT and SHANJIE_HETERO are not both set …` 並跳過那兩個測試，所以「跳過」不等於「通過」 |
| `make test-swift` | `macos/` 的 `swift test`（真核心＋真模型的殼測試、`log stream` 日誌測試、自測嚴格度） |
| `make selftest-bundled` | 用 release 設定、拋棄式 ID 組出 `build/selftest/善解（開發版）.app`（沙盒、沒有移轉清單），從 bundle 自己的 `Resources/` 跑 `--selftest`（含用 `Resources/packs` 打一個詞包詞） |
| `scripts/test-install-ime.sh` | 在暫存 HOME 用只會 `exit 1` 的假 app 跑 `install-ime.sh` 的檔案處理（`SHANJIE_INSTALL_FILES_ONLY=1`，不結束行程、不註冊）；`lsregister`、`pkill`、`pgrep` 換成只記錄呼叫的替身，每個情況都斷言沒被呼叫 |
| `make bundle` ＋ `scripts/check-app.sh` | 不帶參數時組出開發版 `build/善解（開發版）.app`；`make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie` 組出正式版 `build/善解輸入法.app`（多一份 `container-migration.plist`）。兩者都是 ad-hoc、App 沙盒的三個 entitlements。要檢查的 ID 由 `EXPECTED_BUNDLE_ID` 給（沒給時是正式 ID）；`SKIP_RUN=1` 只做 check 2、3、不執行 bundle；`CI` 以外遇到正式 ID 時 check 6 拒絕執行 |
| `scripts/check-entitlements.sh <app>` | entitlements 經 `plutil` 正規化後必須整份等於三個鍵：`app-sandbox`、`network.client`、mach-register `[<ID>_Connection]`（也要等於 `InputMethodConnectionName`）。`check-app.sh` check 3 呼叫它；`release.yml` 的 `verify()` 有同一套比對的副本。只讀，不執行 bundle |
| `scripts/test-sandbox-mutations.sh [app]` | 正式 ID 組建的 24 份複本各改一處，各自要讓 `check-entitlements.sh` 或 `check-app.sh`（`SKIP_RUN=1`）照預期失敗或通過；複本放在暫存目錄，從不執行 |
| `scripts/test-sandbox-migration.sh`（只在 CI） | 沒有清單的組建不搬資料、正式 ID 的組建把學習資料與偏好設定搬進 container、只搬一次。`CI` 不是 `true`、`HOME` 不是帳號的家目錄、或資料已經存在時拒絕，什麼都不寫、不啟動。**不要在自己的機器上跑**：成功時學習資料就在正式版的 container 裡了 |
| `scripts/test-render-cask.sh` | `render-cask.sh`：正常參數只改 cask 範本的 `version`、`sha256` 兩行；參數格式不對、範本缺行或重複、範本不存在時一律 exit 1 且沒有輸出；用 Homebrew 的載入器確認 cask 的 `postflight_steps` 剛好有一個 `match: :full` 的 `terminate_process`，再用 `pgrep -f` 比對替身行程 |
| Homebrew cask 樣式 | 指令照 `ci.yml` 的「Homebrew cask」步驟：用佔位值產生 cask，放在某個 `Casks/` 資料夾下跑 `brew style`（`brew` 只在路徑含 `Casks/` 時套用 cask 規則） |
| `make installer` ＋ `scripts/check-installer.sh` | 只接受正式 ID 的組建。把 `build/善解輸入法.app` 壓成 `build/shanjie-<版本>.zip`，組出 `build/安裝善解輸入法.app`，檢查結構、bundle ID、內附 zip 與 `install-ime.sh` 逐位元組相同、`Resources/` 沒有展開的 .app、App 圖示、沒有 entitlements、hardened runtime。安裝程式只檢查、不啟動 |
| C 標頭冒煙測試、寬鬆比對一致性 | 指令照 `ci.yml` 的「C header smoke test」與「Lenient comparison parity」步驟 |

- 正式 ID 的組建（`make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie`）在本機永遠只建置、不執行；它的執行只在 CI（`scripts/test-sandbox-migration.sh` 與 `check-app.sh` check 6）。
- 更新實機測試用的測試版：`SHANJIE_UPDATE_TEST_FEED=<網址> SHANJIE_UPDATE_TEST_KEY=<測試公鑰> scripts/build-app.sh`，檢查時 `scripts/check-app.sh` 也要帶同樣兩個變數，不帶的話測試版會被擋下。本機建置的 Info.plist `ShanjieUpdateBuild` 是 `local`（更新服務不啟動）；`release.yml` 的建置帶 `SHANJIE_RELEASE_BUILD=1` 寫成 `release`，要在本機檢查這種建置就讓 `check-app.sh` 也帶 `SHANJIE_RELEASE_BUILD=1`。
- `make build` 只建置（debug），不跑測試。SwiftPM 不一定把 `target/release/libcore.a` 與 `core/include/shanjie.h` 當成相依，所以 Makefile 在建置前先檢查：靜態庫比執行檔新就刪掉執行檔重新連結；標頭比執行檔新就刪掉模組快取和匯入它的 target 產物。換工具鏈時這個檢查是保險；標頭內容變動的情況沒有在不改 `core/` 的前提下實測過。

### 7.1 bundled selftest 的 bundle ID

`make selftest-bundled` 只有 bundle ID 一個參數，一律是沒有移轉清單的拋棄式 ID：本機預設 `com.nyanako.inputmethod.shanjie.selftest`，CI 每次用 `…selftest-$GITHUB_RUN_ID`，並檢查那個 ID 的 container 執行前不存在、執行後存在（沙盒有套上）。正式 ID 的 selftest 只在 CI 跑：正式 ID 的沙盒組建第一次執行，會把真正的學習資料與偏好設定搬進它的 container，本機的 ad-hoc 組建不能先建立那個 container。自測本身不寫任何檔案或偏好設定，也不呼叫 TIS；第一次在沙盒裡執行時，系統會建立那個 ID 的 container。

### 7.2 `build/` 的 bundle

- `build/` 底下的 app **一律不啟動、不註冊**（不跑 `shanjie install`、不呼叫 TIS）。輸入法實際用的是 `~/Library/Input Methods/` 裡的那一份；真正的安裝只由使用者執行 `scripts/install-ime.sh`；自動化與 CI 只透過 `scripts/test-install-ime.sh`（暫存 HOME、假 app）執行它。
- 本機組建裝成「善解（開發版）」：`make bundle` 之後 `scripts/install-ime.sh "build/善解（開發版）.app"`，裝到 `~/Library/Input Methods/善解（開發版）.app`，第一次要在「系統設定 → 鍵盤 → 輸入方式」加入一次。它的 bundle ID、輸入模式、偏好設定與 container 都和正式版分開，學習資料也分開，不碰已裝好的正式版。
- `scripts/build-app.sh` 會在輸出目錄放 `.metadata_never_index`，讓 Spotlight 與 LaunchServices 不收錄本機建置，系統就不會用 bundle ID 找到並啟動 `build/` 裡的那一份。
- 清除：`make clean-bundle`（對 `build/` 與 `build/selftest/` 裡的 `善解輸入法.app`、`善解（開發版）.app` 與改名前的 `shanjie.app` 都執行 `lsregister -u`，再刪除）。之後以下指令都不應該列出任何東西。`build/` 有 `.metadata_never_index` 又從不登記，`mdfind` 與 `lsregister -dump` 本來就看不到那裡的 bundle，所以第一個 `find` 才是真正會抓到殘留的檢查：

  ```sh
  find build -name '*.app'
  mdfind -name 善解輸入法.app | grep -F "$PWD/build"; mdfind -name '善解（開發版）.app' | grep -F "$PWD/build"; mdfind -name shanjie.app | grep -F "$PWD/build"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -dump | grep -E 'build/(selftest/)?(善解輸入法|善解（開發版）|shanjie)\.app'
  ```
