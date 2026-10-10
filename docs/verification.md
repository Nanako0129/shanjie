# 驗證流程

本機與 CI 的檢查各自在哪裡跑、怎麼跑。流程仿 syrtis 的 `Makefile` 與 `docs/knowledge/verification.md`。所有指令都在 repo 根目錄執行；需要 `data/lm/bigram.sjlm` 與 `data/lm/classes.sjc`（`gh release download model-v5 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm`、`gh release download classes-v3 -R Nanako0129/shanjie -p classes.sjc -D data/lm`，雜湊以 `data/bigram.sjlm.sha256`、`data/classes.sjc.sha256` 為準；說明見 `docs/data-files.md`）。

## 本機關卡

| 指令 | 內容 |
|---|---|
| `make test` | `cargo test --release --locked`，再跑 `python3 -m unittest tools/test_build_acg_pack.py`（詞包建置工具；需要 python3、真的模型與 cargo 建出來的評測 CLI），再跑 `python3 -m unittest tools/test_build_names.py`（常見專有名詞建置工具與已提交的 `names-add.tsv` 交集檢查；同樣需要真的模型與評測 CLI），再跑 `macos/` 的 `swift test`（真核心＋真模型的殼測試、`log stream` 日誌測試、自測嚴格度） |
| `make selftest-bundled` | 用 release 設定組出 `build/selftest/善解輸入法.app`，從 bundle 自己的 `Resources/` 跑 `--selftest`（含用 `Resources/packs` 打一個詞包詞） |
| `scripts/test-install-ime.sh` | 在暫存的 HOME 裡，用執行檔只會 `exit 1` 的假 app 跑 `install-ime.sh` 的檔案處理（`SHANJIE_INSTALL_FILES_ONLY=1`，不結束行程、不註冊）：沒開 files-only 時拒絕執行（唯讀 HOME，確認訊息來自 HOME 檢查）、全新安裝時複製失敗、全新安裝、覆蓋安裝（上一版保留為 `.shanjie-previous`）、從已安裝那份重裝、覆蓋時複製失敗、只有舊名稱 `shanjie.app` 時升級、新舊名稱並存時升級、舊名稱改名失敗時不放上新版、同時設 files-only 與安裝程式用的 skip-register 時仍停在 files-only。lsregister（`SHANJIE_TEST_LSREGISTER`）、`pkill`、`pgrep` 都換成只記錄呼叫的替身，每個情況都斷言沒被呼叫 |
| `make bundle` ＋ `scripts/check-app.sh` | 組出 `build/善解輸入法.app`（正式 bundle ID、ad-hoc、hardened runtime、沒有 entitlements），再跑 s3b 契約 §10 的檢查 2、3、6（含 §13.4 的單一輸入模式與中英文名稱） |
| `scripts/test-render-cask.sh` | `render-cask.sh`（s3b §14.2）：正常參數只改 cask 範本的 `version`、`sha256` 兩行；參數格式不對、範本缺行或重複、範本不存在時一律 exit 1 且沒有輸出；用 Homebrew 的載入器確認 cask 的 `postflight_steps` 剛好有一個 `match: :full` 的 `terminate_process`（s3b §15），再用 `pgrep -f` 比對替身行程：像執行中輸入法的（含舊名、系統層 `/Library/Input Methods`）符合，只在參數裡提到路徑的、`shanjie install`、上一版 `.shanjie-previous` 都不符合 |
| Homebrew cask 樣式 | 指令照 `.github/workflows/ci.yml` 的「Homebrew cask」步驟：用佔位值產生 cask，放在某個 `Casks/` 資料夾下跑 `brew style`（`brew` 只在路徑含 `Casks/` 時套用 cask 規則） |
| `make installer` ＋ `scripts/check-installer.sh` | 把 `build/善解輸入法.app` 壓成 `build/shanjie-<版本>.zip`，組出 `build/安裝善解輸入法.app`（s3c 契約 §3），檢查結構、bundle ID、內附 zip 與 `install-ime.sh` 逐位元組相同、`Resources/` 沒有展開的 .app、沒有 entitlements、hardened runtime。安裝程式只檢查、不啟動 |
| C 標頭冒煙測試（s3a §7.4） | 指令照 `.github/workflows/ci.yml` 的「C header smoke test」步驟 |
| S2v 寬鬆比對一致性 | 指令照 `.github/workflows/ci.yml` 的「Lenient comparison parity」步驟 |

`make build` 只建置（debug），不跑測試。SwiftPM 不一定把 `target/release/libcore.a` 與 `core/include/shanjie.h` 當成相依，所以 Makefile 在建置前先檢查：靜態庫比執行檔新就刪掉執行檔重新連結；標頭比執行檔新就刪掉模組快取和匯入它的 target 產物。2026-10-03 用 Swift 6.4 實測，靜態庫變動時 SwiftPM 本來就會重新連結，這個檢查只是換工具鏈時的保險；標頭內容變動的情況沒辦法在不改 `core/` 的前提下實測。

## 跨版本基準測試（S-bench，`docs/contracts/sbench.md`）

| 指令 | 什麼時候 | 內容 |
|---|---|---|
| `tools/bench.py run <新版 commit> --private-root ~/side-project/shanjie-private` | 發版前 | 套件 v1 的準確率與速度，結果寫 `eval/bench/results/<label>.json`；再請 fresh verifier 在該版 tag 的 worktree 量保留集，填進同一個 JSON。兩者隨發版前的 PR 進 repo |
| `tools/bench.py reference --private-root ~/side-project/shanjie-private` | commit 或指紋改變時 | 「小麥資料基準」列（`--no-overlay`、不帶 `--lm` 的 unigram） |
| `tools/bench.py check-static` | 改動 `eval/bench/static/typing-test.json` 或 `docs/typing-test.md` 時 | 實打對照表逐格對照原文 |
| `tools/bench.py table` | 上面任一個之後 | 重產 `docs/benchmark.md`，不手改 |

## bundled selftest 的 bundle ID

`make selftest-bundled` 只有 bundle ID 一個參數。本機預設用拋棄式的 `com.nyanako.inputmethod.shanjie.selftest`，這是**比較弱的關卡**：如果有程式依正式 bundle ID 走不同分支，本機測不到。CI 呼叫 `make selftest-bundled SELFTEST_BUNDLE_ID=`，空值代表用 `scripts/build-app.sh` 的預設，也就是正式 ID（只寫在一個地方，改名時不會漏改而讓關卡悄悄變弱）。自測不寫任何檔案或偏好設定，也不呼叫 TIS，所以兩種 ID 都不會碰到已安裝的版本。

## CI

| 工作流程 | 觸發 | 內容 |
|---|---|---|
| `ci.yml` core | 每個 PR、每次推到 main | 下載並比對模型、`cargo test`（debug 與 release）、C 標頭冒煙測試、寬鬆比對一致性 |
| `ci.yml` shell | 同上 | `make bundle`、`scripts/test-install-ime.sh`、`swift test`、`make selftest-bundled SELFTEST_BUNDLE_ID=`、`scripts/check-app.sh` |
| `release.yml` | 推 `v*` tag；或在 main 手動觸發（演練） | gate（這個 commit 在 main 的 ci.yml 必須全綠）→ build → 在 `release` environment 用一次性鑰匙圈以 Developer ID 簽章、公證、staple、驗證 → 只有 tag 才發布 Release。推 tag 時 `docs/releases/<tag>.md` 必須存在而且有內容（在等 CI 之前檢查），發布用它當說明；版本號最大時標成 Latest（`docs/releases/README.md`） |

## build/ 的 bundle

- `build/` 底下的 app **一律不啟動、不註冊**（不跑 `shanjie install`、不呼叫 TIS）。輸入法實際用的是 `~/Library/Input Methods/善解輸入法.app`，那一份才是準的；真正的安裝只由使用者執行 `scripts/install-ime.sh`；agent 與 CI 只透過 `scripts/test-install-ime.sh`（暫存 HOME、假 app）執行它。
- `scripts/build-app.sh` 會在輸出目錄放 `.metadata_never_index`，讓 Spotlight 與 LaunchServices 不收錄本機建置，系統就不會用 bundle ID 找到並啟動 `build/` 裡的那一份。
- 清除：`make clean-bundle`（對 `build/` 與 `build/selftest/` 裡的 `善解輸入法.app` 與改名前的 `shanjie.app` 都執行 `lsregister -u`，再刪除）。之後以下三個指令都不應該列出任何東西。`build/` 有 `.metadata_never_index` 又從不登記，`mdfind` 與 `lsregister -dump` 本來就看不到那裡的 bundle，所以第一個 `find` 才是真正會抓到殘留的檢查：

  ```sh
  find build -name '*.app'
  mdfind -name 善解輸入法.app | grep -F "$PWD/build"; mdfind -name shanjie.app | grep -F "$PWD/build"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -dump | grep -E 'build/(selftest/)?(善解輸入法|shanjie)\.app'
  ```
