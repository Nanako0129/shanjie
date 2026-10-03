# 驗證流程

本機與 CI 的檢查各自在哪裡跑、怎麼跑。流程仿 syrtis 的 `Makefile` 與 `docs/knowledge/verification.md`。所有指令都在 repo 根目錄執行；需要 `data/lm/bigram.sjlm`（`gh release download model-v1 -p bigram.sjlm -D data/lm`，雜湊以 `data/bigram.sjlm.sha256` 為準）。

## 本機關卡

| 指令 | 內容 |
|---|---|
| `make test` | `cargo test --release --locked`，再跑 `macos/` 的 `swift test`（真核心＋真模型的殼測試、`log stream` 日誌測試、自測嚴格度） |
| `make selftest-bundled` | 用 release 設定組出 `build/selftest/shanjie.app`，從 bundle 自己的 `Resources/` 跑 `--selftest` |
| `scripts/test-install-ime.sh build/shanjie.app` | 在暫存的 HOME 裡跑 `install-ime.sh` 的檔案替換（`SHANJIE_INSTALL_FILES_ONLY=1`，不結束行程、不註冊）：全新安裝、覆蓋安裝、複製失敗、舊版刪不掉 |
| `make bundle` ＋ `scripts/check-app.sh` | 組出 `build/shanjie.app`（正式 bundle ID、ad-hoc、hardened runtime、沒有 entitlements），再跑 s3b 契約 §10 的檢查 2、3、6 |
| C 標頭冒煙測試（s3a §7.4） | 指令照 `.github/workflows/ci.yml` 的「C header smoke test」步驟 |
| S2v 寬鬆比對一致性 | 指令照 `.github/workflows/ci.yml` 的「Lenient comparison parity」步驟 |

`make build` 只建置（debug），不跑測試。SwiftPM 不一定把 `target/release/libcore.a` 與 `core/include/shanjie.h` 當成相依，所以 Makefile 在建置前先檢查：靜態庫比執行檔新就刪掉執行檔重新連結；標頭比執行檔新就刪掉模組快取和匯入它的 target 產物。2026-10-03 用 Swift 6.4 實測，靜態庫變動時 SwiftPM 本來就會重新連結，這個檢查只是換工具鏈時的保險；標頭內容變動的情況沒辦法在不改 `core/` 的前提下實測。

## bundled selftest 的 bundle ID

`make selftest-bundled` 只有 bundle ID 一個參數。本機預設用拋棄式的 `com.nyanako.inputmethod.shanjie.selftest`，這是**比較弱的關卡**：如果有程式依正式 bundle ID 走不同分支，本機測不到。CI 呼叫 `make selftest-bundled SELFTEST_BUNDLE_ID=`，空值代表用 `scripts/build-app.sh` 的預設，也就是正式 ID（只寫在一個地方，改名時不會漏改而讓關卡悄悄變弱）。自測不寫任何檔案或偏好設定，也不呼叫 TIS，所以兩種 ID 都不會碰到已安裝的版本。

## CI

| 工作流程 | 觸發 | 內容 |
|---|---|---|
| `ci.yml` core | 每個 PR、每次推到 main | 下載並比對模型、`cargo test`（debug 與 release）、C 標頭冒煙測試、寬鬆比對一致性 |
| `ci.yml` shell | 同上 | `make bundle`、`scripts/test-install-ime.sh`、`swift test`、`make selftest-bundled SELFTEST_BUNDLE_ID=`、`scripts/check-app.sh` |
| `release.yml` | 推 `v*` tag；或在 main 手動觸發（演練） | gate（這個 commit 在 main 的 ci.yml 必須全綠）→ build → 在 `release` environment 用一次性鑰匙圈以 Developer ID 簽章、公證、staple、驗證 → 只有 tag 才發布 Release |

## build/ 的 bundle

- `build/` 底下的 app **一律不啟動、不註冊**（不跑 `shanjie install`、不呼叫 TIS）。輸入法實際用的是 `~/Library/Input Methods/shanjie.app`，那一份才是準的；安裝只由使用者執行 `scripts/install-ime.sh`。
- `scripts/build-app.sh` 會在輸出目錄放 `.metadata_never_index`，讓 Spotlight 與 LaunchServices 不收錄本機建置，系統就不會用 bundle ID 找到並啟動 `build/` 裡的那一份。
- 清除：`make clean-bundle`（對兩個 bundle 執行 `lsregister -u`，再刪除）。之後以下兩個指令都不應該列出任何東西：

  ```sh
  mdfind -name shanjie.app | grep -F "$PWD/build"
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -dump | grep -F build/shanjie.app
  ```
