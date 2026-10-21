# 驗證流程

本機與 CI 的檢查各自在哪裡跑、怎麼跑。流程仿 syrtis 的 `Makefile` 與 `docs/knowledge/verification.md`。所有指令都在 repo 根目錄執行；需要 `data/lm/bigram.sjlm`、`data/lm/classes.sjc`、`data/lm/kn.sjkn`、`data/lm/tinylm.sjtl` 與 `data/lm/trigram.sjt3`（`gh release download model-v6 -R Nanako0129/shanjie -p bigram.sjlm -D data/lm`、`gh release download classes-v5 -R Nanako0129/shanjie -p classes.sjc -D data/lm`、`gh release download kn-v1 -R Nanako0129/shanjie -p kn.sjkn -D data/lm`、`gh release download sj-sr-6-14m -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm`、`gh release download sj-cx-1-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D data/lm`，雜湊以 `data/bigram.sjlm.sha256`、`data/classes.sjc.sha256`、`data/kn.sjkn.sha256`、`data/tinylm.sjtl.sha256`、`data/trigram.sjt3.sha256` 為準；說明見 `docs/data-files.md`）。

## 本機關卡

> **App 沙盒那一片的限制期間**（`docs/contracts/app-sandbox.md` §5）：在那一片的 PR 的 CI 證明「沒有移轉清單就不搬」之前，維護者的機器上不執行任何 ID 的沙盒組建，包括 `make selftest-bundled`、`scripts/check-app.sh` 第 6 項與開發版的 `install-ime.sh`。這段期間這些關卡改看 CI 的 `shell` 工作；本機的 `check-app.sh` 一律加 `SKIP_RUN=1`，只做第 2、3 項。正式 ID 的組建（`make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie`）在本機永遠只建置、不執行。

| 指令 | 內容 |
|---|---|
| `make test`（＝`test-rust`、`test-python`、`test-swift` 三個目標依序跑；另有只跑 golden 比對的 `make test-golden`，各改動 commit 前跑哪個見 `CLAUDE.md`「改動分級」） | `cargo test --release --locked`，再跑 `make test-python` 的 Python 單元測試（工具與部分實驗，清單在 `Makefile`；含常見專有名詞建置工具 `tools/test_build_names.py` 與已提交的 `names-add.tsv` 交集檢查；需要 python3、numpy、真的模型與 cargo 建出來的評測 CLI），再跑 `macos/` 的 `swift test`（真核心＋真模型的殼測試、`log stream` 日誌測試、自測嚴格度） |
| `make selftest-bundled` | 用 release 設定、拋棄式 ID 組出 `build/selftest/善解（開發版）.app`（沙盒、沒有移轉清單），從 bundle 自己的 `Resources/` 跑 `--selftest`（含用 `Resources/packs` 打一個詞包詞）。第一次執行會建立那個 ID 的 container |
| `scripts/test-install-ime.sh` | 在暫存的 HOME 裡，用執行檔只會 `exit 1` 的假 app 跑 `install-ime.sh` 的檔案處理（`SHANJIE_INSTALL_FILES_ONLY=1`，不結束行程、不註冊）：沒開 files-only 時拒絕執行（唯讀 HOME，確認訊息來自 HOME 檢查）、全新安裝時複製失敗、全新安裝、覆蓋安裝（上一版保留為 `.shanjie-previous`）、從已安裝那份重裝、覆蓋時複製失敗、只有舊名稱 `shanjie.app` 時升級、新舊名稱並存時升級、舊名稱改名失敗時不放上新版、同時設 files-only 與安裝程式用的 skip-register 時仍停在 files-only、已有正式版時安裝開發版（裝到 `善解（開發版）.app`、上一版在 `.shanjie-dev-previous`，正式版原封不動）、已有開發版時正式版照舊裝到 `善解輸入法.app`、其他 bundle ID 拒絕安裝、沒開 files-only 時正式 ID 的 ad-hoc 與未簽章 bundle 在複製前就被拒絕（訊息來自簽章檢查）。lsregister（`SHANJIE_TEST_LSREGISTER`）、`pkill`、`pgrep` 都換成只記錄呼叫的替身，每個情況都斷言沒被呼叫 |
| `make bundle` ＋ `scripts/check-app.sh` | 不帶參數時組出開發版 `build/善解（開發版）.app`（`com.nyanako.inputmethod.shanjie.dev`、沒有移轉清單）；`make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie` 組出正式版 `build/善解輸入法.app`（多一份 `container-migration.plist`）。兩者都是 ad-hoc、App 沙盒的三個 entitlements（`app-sandbox.md` §2.1–§2.3；內嵌 Sparkle 之後 ad-hoc 建置不加 hardened runtime，原因見 s3b 契約 §2，出貨版本由 `release.yml` 加上並檢查）。`check-app.sh` 跑 s3b 契約 §10 的檢查 2、3、6，要檢查的 ID 由 `EXPECTED_BUNDLE_ID` 給（沒給時是正式 ID），`SKIP_RUN=1` 只做第 2、3 項、不執行 bundle；CI 以外遇到正式 ID 時第 6 項拒絕執行、結束碼非 0（含 §13.4 的單一輸入模式與中英文名稱，以及 `standalone-updater.md` §2.4 的檢查：整個 bundle 沒有 `.xpc`、`Contents/Helpers` 剛好是 `善解更新.app`、它的 Info.plist 沒有 `SUFeedURL`／`SUPublicEDKey`／`SUEnable*`、輸入法那份 framework 沒有 `Autoupdate` 與 `Updater.app`、善解更新那份兩個都在、除了輸入法都沒有 entitlements、權限（`o+r`、目錄與執行檔 `o+x`、沒有群組或其他人可寫、沒有 setuid／setgid）、Info.plist 的 Sparkle 鍵逐字等於正式值、`SUEnableInstallerLauncherService` 是 false、`SUEnableDownloaderService` 不存在；app-icon 契約的 App 圖示：`Assets.car`、`AppIcon.icns`、`CFBundleIconName`／`CFBundleIconFile`）。更新實機測試用的測試版：`SHANJIE_UPDATE_TEST_FEED=<網址> SHANJIE_UPDATE_TEST_KEY=<測試公鑰> scripts/build-app.sh`，檢查時 `scripts/check-app.sh` 也要帶同樣兩個變數；不帶的話測試版會被擋下。本機建置的 Info.plist `ShanjieUpdateBuild` 是 `local`（更新服務不啟動）；`release.yml` 的建置帶 `SHANJIE_RELEASE_BUILD=1` 寫成 `release`，要在本機檢查這種建置就讓 `check-app.sh` 也帶 `SHANJIE_RELEASE_BUILD=1` |
| `scripts/check-entitlements.sh <app>` | entitlements 經 `plutil` 正規化後必須整份等於三個鍵（`standalone-updater.md` §2.2）：`app-sandbox`、`network.client`、mach-register `[<ID>_Connection]`（也要等於 `InputMethodConnectionName`），`<ID>` 是該 bundle 的 `CFBundleIdentifier`；多一個鍵就失敗。`check-app.sh` 第 3 項呼叫它；`release.yml` 的 `verify()` 有同一套比對的副本。只讀，不執行 bundle |
| `scripts/test-sandbox-mutations.sh [app]` | `app-sandbox.md` §2.6 第 8 步、`standalone-updater.md` §2.4：正式 ID 組建的 24 份複本各改一處（善解更新的 `CFBundleLocalizations` 拿掉一份，清單在腳本開頭：entitlements 四種、`.xpc`、`Autoupdate`／`Updater.app` 的有無、善解更新的 entitlements 與 Info.plist 鍵、`SUEnableInstallerLauncherService` 改 true、`Contents/Helpers` 多一個 App、權限六種），需要時 ad-hoc 重簽，各自要讓 `check-entitlements.sh` 或 `check-app.sh`（`SKIP_RUN=1`）照預期失敗或通過。複本放在暫存目錄，從不執行 |
| `scripts/test-sandbox-migration.sh`（只在 CI） | `app-sandbox.md` §2.6 第 3 步：沒有清單的組建不搬資料、正式 ID 的組建把學習資料與偏好設定搬進 container、只搬一次。`CI` 不是 `true`、`HOME` 不是帳號的家目錄、或資料已經存在時拒絕，什麼都不寫、不啟動。**不要在自己的機器上跑**：成功時學習資料就在正式版的 container 裡了 |
| `scripts/test-render-cask.sh` | `render-cask.sh`（s3b §14.2）：正常參數只改 cask 範本的 `version`、`sha256` 兩行；參數格式不對、範本缺行或重複、範本不存在時一律 exit 1 且沒有輸出；用 Homebrew 的載入器確認 cask 的 `postflight_steps` 剛好有一個 `match: :full` 的 `terminate_process`（s3b §15），再用 `pgrep -f` 比對替身行程：像執行中輸入法的（含舊名、系統層 `/Library/Input Methods`）符合，只在參數裡提到路徑的、`shanjie install`、上一版 `.shanjie-previous` 都不符合 |
| Homebrew cask 樣式 | 指令照 `.github/workflows/ci.yml` 的「Homebrew cask」步驟：用佔位值產生 cask，放在某個 `Casks/` 資料夾下跑 `brew style`（`brew` 只在路徑含 `Casks/` 時套用 cask 規則） |
| `make installer` ＋ `scripts/check-installer.sh` | 只接受正式 ID 的組建（先 `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie`）。把 `build/善解輸入法.app` 壓成 `build/shanjie-<版本>.zip`，組出 `build/安裝善解輸入法.app`（s3c 契約 §3），檢查結構、bundle ID、內附 zip 與 `install-ime.sh` 逐位元組相同、`Resources/` 沒有展開的 .app、App 圖示（`Assets.car`、`AppIcon.icns`、`CFBundleIconName`／`CFBundleIconFile`）、沒有 entitlements、hardened runtime。安裝程式只檢查、不啟動 |
| C 標頭冒煙測試（s3a §7.4） | 指令照 `.github/workflows/ci.yml` 的「C header smoke test」步驟 |
| S2v 寬鬆比對一致性 | 指令照 `.github/workflows/ci.yml` 的「Lenient comparison parity」步驟 |

`make build` 只建置（debug），不跑測試。SwiftPM 不一定把 `target/release/libcore.a` 與 `core/include/shanjie.h` 當成相依，所以 Makefile 在建置前先檢查：靜態庫比執行檔新就刪掉執行檔重新連結；標頭比執行檔新就刪掉模組快取和匯入它的 target 產物。2026-10-03 用 Swift 6.4 實測，靜態庫變動時 SwiftPM 本來就會重新連結，這個檢查只是換工具鏈時的保險；標頭內容變動的情況沒辦法在不改 `core/` 的前提下實測。

## 跨版本基準測試（S-bench，`docs/contracts/sbench.md`）

| 指令 | 什麼時候 | 內容 |
|---|---|---|
| `tools/bench.py run --suite v2 <上一版 tag> <要打 tag 的 commit> --as <新版> --private-root ~/side-project/shanjie-private`，再 `tools/bench.py table` | 發版前（推 `v*` tag 之前） | 套件 v2（`docs/contracts/sbench-v2.md`）：每一版用自己出貨的模型、詞類表、小模型與旗標量準確率與速度，結果寫 `eval/bench/results/v2-<label>.json`；要打 tag 的 commit 定下來之後，請 fresh verifier 照 CLAUDE.md 量保留集（上一版對新版的 `evalstats compare` 一行），填進同一個 JSON（可以在最後幾個只改綁定路徑以外檔案的 PR 合併前就量，條件見 `docs/contracts/sbench-v2.md` §2.3 的修訂）。兩者隨發版前的 PR 進 repo；推 tag 前核對被量的 commit 和要打 tag 的 commit 在 `Cargo.toml Cargo.lock core cli data eval/variants.tsv macos scripts` 上沒有差異。新版要先在 `tools/bench.py` 的 `VERSIONS2` 加一列（Release 名、出貨旗標、摘要行設定標記，照 App 的預設值與 `scripts/build-app.sh` 包進去的模型；出貨口語表的版本加 `"colloquial": True`，`bench.py` 與 `kpi.py` 都照這個鍵決定：bench 讀 `data/colloquial.sjlm.sha256`、把表放進該版的模型資料夾、聊天的格帶 `--colloquial-core`、聊天的設定標記最後多 `+colloq:<sha8>`，書面與錨點不變） |
| `tools/kpi.py run <要打 tag 的 commit> --as <新版> --wikitune-clean <列檔>`（Windows 機器或 GCP，不在 Mac），在那個 commit 的乾淨 checkout 上 `tools/kpi.py replay --as <新版>`，再 `tools/kpi.py table` | 套件 v2 跑完之後、推 tag 之前 | 每版 KPI（`docs/contracts/kpi.md`）：`run` 用 `bench.py` 的匯出、編譯與模型設定，以出貨旗標、書面設定量 wikitune-clean，寫 `eval/bench/kpi/<版本>.json` 的 `sha` 與 `wikitune_clean`；`replay` 在目前的 checkout 用 `data/lm/` 的模型（要和 `VERSIONS2[新版]` 出貨的那一組完全一致、雜湊符合 `data/*.sha256`，已追蹤的檔有改動就拒絕，未追蹤的結果檔不算；`--as` 的 tag 已存在時必須就是 HEAD），先 `cargo build --release --locked -p cli --bin shanjie-replay`（執行檔路徑取編譯器回報的），再以真引擎重播 `eval/bench/suite-v2/user-reported.txt`（學習關與開）和 `eval/bench/suite-v1/dev302.txt`（學習關），寫同一個 JSON 的 `replay`；`table` 重產 `docs/kpi.md`，不手改。三者的結果和上一列的基準測試結果同一個 PR 進 repo |
| `tools/bench.py run <commit> --private-root ~/side-project/shanjie-private` | 不再用於發版 | 套件 v1（固定 model-v1），保留給 v0.1.x 的歷史表 |
| `tools/bench.py reference --private-root ~/side-project/shanjie-private` | commit 或指紋改變時 | 「小麥資料基準」列（`--no-overlay`、不帶 `--lm` 的 unigram） |
| `tools/bench.py check-static` | 改動 `eval/bench/static/typing-test.json` 或 `docs/typing-test.md` 時 | 實打對照表逐格對照原文 |
| `tools/bench.py table` | 上面任一個之後 | 重產 `docs/benchmark.md`，不手改 |

**保留集的兩個半邊（從下一個發版起，`docs/contracts/kpi.md` §3）**：保留集規則不變（只在推 `v*` tag 前由 fresh verifier 跑一次，基準與新版各跑 `--set holdout --rowstats`，聊天、書面各一次）。全集合照舊每種設定一行，存在 v2 JSON 的 `holdout.chat`、`holdout.formal`。另加兩行：口語半邊用**聊天**設定、新聞半邊用**書面**設定。`--set holdout` 依檔名排序讀，`news.txt` 是第 1–150 列、`talk.txt` 是第 151–300 列；基準與新版的 rowstats 用同樣的方式切出那一段、列號從 1 重編（`evalstats` 要求列號從 1 連續），再跑 `evalstats compare --net`。兩個半邊存進 `eval/bench/kpi/<版本>.json` 的 `holdout_talk_chat`、`holdout_news_formal`（欄位同 v2 JSON 的 `holdout`：`n`、`top1`、`fixed`、`broken`、`p`，另加那個半邊檔案的 `rows_sha256`，表依它分欄），`kpi.py table` 從這兩個欄位畫口語與新聞半邊的 top1。同一次發版前量的這四行算一次「使用」。

## bundled selftest 的 bundle ID

`make selftest-bundled` 只有 bundle ID 一個參數，一律是沒有移轉清單的拋棄式 ID：本機預設 `com.nyanako.inputmethod.shanjie.selftest`，CI 每次用 `…selftest-$GITHUB_RUN_ID`，並檢查那個 ID 的 container 執行前不存在、執行後存在（沙盒有套上）。正式 ID 的 selftest 只在 CI 跑（`scripts/test-sandbox-migration.sh` 與 `check-app.sh` 第 6 項）：正式 ID 的沙盒組建第一次執行，會把真正的學習資料與偏好設定搬進它的 container，本機的 ad-hoc 組建不能先建立那個 container（`app-sandbox.md` §2.3、§7.1 的 N1、N3）。自測本身不寫任何檔案或偏好設定，也不呼叫 TIS；第一次在沙盒裡執行時，系統會建立那個 ID 的 container。

## CI

| 工作流程 | 觸發 | 內容 |
|---|---|---|
| `ci.yml` core（macOS） | 每個 PR、每次推到 main（只改文件、網站、`eval/bench/results/` 時略過，`scripts/ci-changes.sh`；推到 main 時還要前一個 main 的 run 是 success 才略過） | 下載並比對模型（`scripts/fetch-models.sh`，下面每個 job 都用它）、`cargo test --release`（兩個套件） |
| `ci.yml` debug（Linux，ubuntu-24.04） | 同上，和 core 同時跑 | `cargo test`（debug）：`core` 套件全部、`cli` 套件只跑單元測試。cli 的整合測試用小模型打分，Linux 沒有 Accelerate，只在 core 跑 release；只在 macOS 才編進去的分支與在 Linux 直接 return 的兩個 digit golden 也只在 core 跑（`docs/contracts/ci-speed-2.md` §2.1） |
| `ci.yml` python（Linux，ubuntu-24.04） | 同上 | 寬鬆比對一致性（Rust 對 Python）、連讀變調列與 probe 重建、單字解碼一致性、Python 各步（評測統計與 dev302 rowstats golden、詞包、專有名詞、model-v5 建置腳本、KN 側檔、benchmark、KPI、英文詞表、手動加詞、常談署名、保留集防護、X0c 與三元表） |
| `ci.yml` swift（macOS） | 同上 | `cargo build --release -p core`、C 標頭冒煙測試、`swift test --parallel`（測試數從 xUnit 檔印出；不需要 `make bundle`，也不啟動沙盒組建） |
| `ci.yml` shell | 同上 | 照 `app-sandbox.md` §2.6 的順序：預設（開發版）、正式、沒有清單三種組建（只建置）→ `check-app.sh` 的本機防呆 → 移轉測試（印出 runner 的 `sw_vers`）→ 移轉測試的拒絕 → `scripts/test-check-ci-gate.sh`、模型列表檢查（`scripts/test-check-models.sh`）、`scripts/test-install-ime.sh` → `make selftest-bundled`（每次不同的拋棄式 ID，前後檢查 container）→ 正式組建的 `check-app.sh` → `scripts/test-sandbox-mutations.sh` → `make installer`＋`scripts/check-installer.sh` → Homebrew cask（`scripts/test-render-cask.sh`、`brew style`） |
| `release.yml` | 推 `v*` tag；或在 main 手動觸發（演練） | gate（這個 commit 在 main 的 ci.yml 必須全綠）→ build → 在 `release` environment 用一次性鑰匙圈以 Developer ID 簽章（`tar -xpf`，由內而外：善解更新那份 Sparkle、善解更新、輸入法那份 framework，最後輸入法用 workflow 裡以字面值寫的三個沙盒 entitlements）、公證、staple、`chmod -R a+rX,go-w`（staple 在 `umask 077` 下寫出的檔是 0600）、驗證（`verify()` 依 bundle 比對 entitlements 整份相等）→ 只有 tag 才發布 Release；演練時兩個簽好的 zip 留在一天的 `signed` artifact。推 tag 時 `docs/releases/<tag>.md` 必須存在而且有內容（在等 CI 之前檢查），發布用它當說明；版本號最大時標成 Latest（`docs/releases/README.md`） |

## build/ 的 bundle

- `build/` 底下的 app **一律不啟動、不註冊**（不跑 `shanjie install`、不呼叫 TIS）。輸入法實際用的是 `~/Library/Input Methods/` 裡的那一份；真正的安裝只由使用者執行 `scripts/install-ime.sh`；agent 與 CI 只透過 `scripts/test-install-ime.sh`（暫存 HOME、假 app）執行它。
- **本機組建裝成「善解（開發版）」**（`app-sandbox.md` §2.3）：`make bundle` 之後 `scripts/install-ime.sh "build/善解（開發版）.app"`，裝到 `~/Library/Input Methods/善解（開發版）.app`，第一次要在「系統設定 → 鍵盤 → 輸入方式」加入一次。它的 bundle ID、輸入模式、偏好設定與 container 都和正式版分開，學習資料也分開，不碰已裝好的正式版。（限制期間不要裝，見上面的本機關卡。）
- `scripts/build-app.sh` 會在輸出目錄放 `.metadata_never_index`，讓 Spotlight 與 LaunchServices 不收錄本機建置，系統就不會用 bundle ID 找到並啟動 `build/` 裡的那一份。
- 清除：`make clean-bundle`（對 `build/` 與 `build/selftest/` 裡的 `善解輸入法.app`、`善解（開發版）.app` 與改名前的 `shanjie.app` 都執行 `lsregister -u`，再刪除）。之後以下三個指令都不應該列出任何東西。`build/` 有 `.metadata_never_index` 又從不登記，`mdfind` 與 `lsregister -dump` 本來就看不到那裡的 bundle，所以第一個 `find` 才是真正會抓到殘留的檢查：

  ```sh
  find build -name '*.app'
  mdfind -name 善解輸入法.app | grep -F "$PWD/build"; mdfind -name '善解（開發版）.app' | grep -F "$PWD/build"; mdfind -name shanjie.app | grep -F "$PWD/build"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -dump | grep -E 'build/(selftest/)?(善解輸入法|善解（開發版）|shanjie)\.app'
  ```
