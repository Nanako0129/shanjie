# 契約：App 沙盒第一片（輸入法本體進沙盒、資料移轉）

狀態：**修訂三（2026-10-19 晚上）審查 READY**（§8），實作中。修訂二收尾審查 READY，已合併（`a76e5150`）。

- 修訂三的起因：§4.3 A 第 2 步（沙盒版經 Sparkle 更新成沙盒版）在虛擬機失敗，Sparkle 顯示「An error occurred while connecting to the installer」。根因量到了（§1 的「2026-10-19，虛擬機」）：同一條 framework 路徑，開機後第一次跑的 Sparkle 沒有 XPC 服務時，系統就不替後來的版本登記安裝服務，要重開機才恢復。v0.4.7 的 Sparkle.framework 沒有 XPC 服務，所以正式版 0.4.7 → 0.5.0 之後、重開機之前，0.5.0 → 0.5.1 會失敗。
- 修法（維護者 2026-10-19 選擇題，§6 第 7 項）：0.5.0 起把 Sparkle.framework 放到舊版沒用過的 `Contents/Frameworks/Updater/`（§2.8）。§1、§2.4、§2.6 第 8 步、§2.8、§4.1、§4.3 A、§4.4、§5 跟著改。

- 修訂一在 2026-10-10 收尾審查 READY，實作放在分支 `feat/app-sandbox`（PR #94，草稿）。那時排定在 v0.4.0 之後合併、以 v0.5.0 發佈。
- 到 2026-10-19 清晨，舊分支已落後 main 1,032 個 commit（main 當時在 `e8c44f9e`），其中 v0.4.7 內嵌了 Sparkle 自動更新（`docs/contracts/about-update.md`）。它在輸入法程序裡連網、刪掉了 Sparkle 的 XPC 服務，和修訂一「只有兩個 entitlements」的設計衝突。
- 修訂二改在現在的 main 上重做（新分支 `feat/app-sandbox-r2`）：
  - 舊分支只當參考，不合併；PR #94 在新 PR 開出時關掉。
  - 新增 §2.8（Sparkle 在沙盒裡）與 §2.9（輸入法碰到的其他系統資源）。
  - §0、§1、§2.1、§2.4、§2.5、§4、§5 跟著改。
  - 其餘照修訂一。
- 這片仍是 `docs/iteration-plan.md` 產品車道的第一片（維護者 2026-10-18 的順序：沙盒 → 模糊音開關 → 資訊＋AI 詞包 → 句首單字學習 → demo），以下一個次版本（v0.5.0）發佈。
- 系統行為的量測見研究紀錄 2026-10-09「App 沙盒的可行性」。修訂一的安全審查處置在 §7.1，修訂二的在 §7.2。

## 0. 要做到什麼、怎麼看到

- **沙盒與 entitlements**：正式版的輸入法本體（`善解輸入法.app/Contents/MacOS/shanjie`）在 App 沙盒裡執行，entitlements 只有四個鍵（§2.1）：
  - 沙盒本身；
  - 對外連線（只為了 Sparkle）；
  - 註冊輸入法連線的 mach 名稱；
  - Sparkle 安裝服務的兩個 mach 名稱。
- **資料移轉**：第一次啟動沙盒版時，學習資料與偏好設定搬進 container。使用者看不出差別：學過的選字與新詞、倚天排列、選單與設定視窗的設定都在。
- **自動更新**：Sparkle 升到 2.10.0，在沙盒裡照常運作，「關於」頁的檢查與「更新到 X」照舊。
- **沙盒的定位是縱深防禦**（§2.8、§6 第 4 項）：它擋下沒有針對 Sparkle 的攻擊（例如讀使用者的文件與金鑰），但不是對所有攻擊者都成立的硬邊界，也擋不住把輸入內容送出去。所有對外文字照這個寫。
- **怎麼看到**：
  - CI：
    - entitlements 逐鍵比對；
    - Sparkle 的程式碼位置與 Info.plist 鍵；
    - 沙盒裡的 selftest；
    - 正式 ID 的移轉測試，在拋棄式 runner 上跑。
  - 虛擬機：release dry run 產出的 Developer ID 版。從 0.4.x 升級，再退回、照文件把資料搬回。
  - 使用者實機：
    - 先用開發版 ID 的更新測試版，看 Sparkle 在沙盒裡更新得了（§4.3 A）；
    - 再用 dry run 的 Developer ID 版，正式發佈之前、先備份（§4.3 B）：
      - 活動監視器的「沙盒」欄位是「是」；
      - 升級前學過的選字仍排第一；
      - 倚天排列還在；
      - 安全輸入時選單顯示「學習已暫停（安全輸入）」；
      - Caps Lock 切換正常。

## 1. 量到的與沒量到的

**2026-10-09，研究紀錄**（虛擬機 macOS 26.6.2，拋棄式 ID，帶移轉清單，各一次）：

- selftest 在沙盒裡結束碼 0。
- IMK 的連線名稱登記成功，沒有沙盒拒絕。
- 沙盒裡的 `shanjie install` 能註冊。
- 學習資料夾被「搬」進 container（不是複製），權限與不備份旗標保留。系統另外在資料夾上加了 `com.apple.quarantine`（`…;com.apple.ContainerMigrationService;`）。
- 偏好設定沒寫進清單也被搬走。
- container 的擁有者記成那次的簽章。
- 移轉清單的路徑和 bundle ID 無關：拋棄式 ID 的組建搬走了正式 ID 的學習資料。

**2026-10-19，main 讀程式**（`1d6547db`；scout 只讀盤點，main 核對關鍵處）：

- **輸入法程序寫入的檔案**：只有學習資料夾 `~/Library/Application Support/shanjie`。
  - 路徑由 `FileManager` 算出（`macos/Sources/ShanjieKit/Learning.swift` 的 `Shell.learningURL()`），不是寫死的路徑。
  - 檔案是 `learning.tsv`、`.tmp`、`.corrupt`（`core/src/learn_store.rs`）。新詞學習存在同一個 `learning.tsv`，前文鍵是 `#`，沒有別的檔。
  - 不備份旗標由 `Learning.swift` 用 `isExcludedFromBackupKey` 設定。
  - 偏好設定全在 `UserDefaults.standard`。
  - 沒有其他快取、暫存檔或日誌檔；日誌只用 `os.Logger`。
- **讀取**：模型、詞庫與詞包都從 bundle 的 `Resources` 讀。三元表用唯讀 mmap。系統標點表在 `/System/Library/PrivateFrameworks/CoreChineseEngine.framework`（`PunctuationTable.swift`）。
- **網路**：只有 Sparkle 2.9.3，跑在輸入法程序裡（`Updater.swift` 的 `SPUStandardUpdaterController`）。核心與殼沒有其他網路程式碼。設定視窗的網站連結由系統開啟。
- **子程序與 IPC**：
  - 輸入法程序沒有 `Process`、Apple events、XPC 連線或剪貼簿。
  - 有 `IMKServer` 的 mach 名稱、TIS 註冊（`install` 模式）、`IsSecureEventInputEnabled`。
  - `NSWorkspace` 的用法：前景 App、設定頁「App 設定」列出 App 名稱與圖示，用的是 `urlForApplication(withBundleIdentifier:)` 與 `icon(forFile:)`。
- **鑰匙圈**：不用。
- **安裝程式**（`com.nyanako.shanjie.installer`）：
  - 從外面讀寫輸入法的偏好設定網域，只有 `layout` 一個鍵（`Registration.swift` 約第 169 行），§2.7 處理。
  - 不碰學習資料夾。

**2026-10-19，Sparkle**：

- 官方文件（https://sparkle-project.org/documentation/sandboxing/ ）寫的沙盒條件：
  - Info.plist 的 `SUEnableInstallerLauncherService` 設為 YES；
  - `Installer.xpc` 留在 framework 裡；
  - entitlements 加 `com.apple.security.temporary-exception.mach-lookup.global-name`，值是 `<bundle ID>-spks` 與 `<bundle ID>-spki`；
  - 網路要嘛給 App `com.apple.security.network.client`，要嘛開 `SUEnableDownloaderService` 並留 `Downloader.xpc`；
  - 手動重簽時，XPC 服務、`Autoupdate`、`Updater.app`、framework 都加 `-o runtime`，不要用 `--deep` 簽。
- 預編的 Sparkle 2.9.3（`macos/.build/artifacts` 裡的那份，Package.resolved 釘住）：
  - `Installer.xpc`（`org.sparkle-project.InstallerLauncher`）與 `Downloader.xpc`（`org.sparkle-project.DownloaderService`）都**沒有 entitlements**（`codesign -d --entitlements -`，main 量）。
  - 文件寫 Downloader 從 2.6 起預設不在沙盒裡。
- 安全審查讀 Sparkle 2.9.3 原始碼的結論（§7.2；只讀、沒有執行）：
  - Downloader.xpc 照呼叫者給的請求原樣送出，連線驗證只看 Team。所以它擋不住被攻破的輸入法往外送資料（確認 §2.8 的推論）。
  - Installer.xpc 沒有把「要更新的 App」綁定到呼叫者。被攻破的沙盒輸入法**理論上可能**借它在沙盒外執行程式，而且不需要我們的 EdDSA 私鑰。逃出去之後的能力等於現在沒有沙盒的輸入法。
    - 這是原始碼層級的推論，有兩個環節沒驗證：macOS 的 container 保護會不會擋，以及最後一步能不能湊到。
    - 依維護者的決定（§6 第 6 項），先在虛擬機驗證，成立就私下回報 Sparkle。在 Sparkle 回覆之前，repo 只寫到這個程度，步驟細節只留在維護者本機的審查紀錄。
  - 換掉 `~/Library/Input Methods` 裡真正的 bundle 仍然要通過 EdDSA：公鑰從磁碟上真正的 bundle 讀，輸入法寫不到那裡。Sparkle 也擋降版。
- Sparkle 2.9.3 有三則已公開、之後版本修好的安全公告（GitHub `gh api` 核對過）：
  - GHSA-3x7w-j75x-ppq5（high，安裝端驗證與搬移用的路徑不一致，2.9.6 修）；
  - GHSA-4v99-qgq9-6pxp（high，只影響以 root 執行，善解用不到，2.9.6 修）；
  - GHSA-gmj2-gq3j-vqmj（medium，delta 更新，要 EdDSA 私鑰，2.9.5 修）。
  - 最新是 2.10.0（2026-09-13），最低系統 macOS 12，善解要求 26.0，不受影響。這片升到 2.10.0（§2.8）。

**2026-10-19，虛擬機（修訂三；tart 的 macOS 26.6.2，開發版 ID，ad-hoc）**：

- 沙盒版 A（0.0.1）經 Sparkle 更新到 B（0.0.2）失敗：Sparkle 顯示「An error occurred while connecting to the installer」。launchd 在輸入法程序的 pid domain 找不到 `org.sparkle-project.InstallerLauncher`（`failed lookup … error = 3: No such process`），沒有沙盒拒絕。
- 把 `Installer.xpc` 另外放一份到 App 的 `Contents/XPCServices`，launchd 就登記得到；但 Sparkle 2.10.0 啟動時檢查到它就拒絕啟動（`SPUUpdater.m` 的 `checkIfConfiguredProperly`：「must be in the Sparkle framework, not in the application bundle」），所以這條路不能用。
- 同一份 App 換位置執行（直接執行主程式，每秒查一次 `launchctl print pid/<pid>`，最多 15 秒）：
  - 複製到別的資料夾、改 bundle 名稱：2～9 秒內登記好；
  - 已安裝的那條路徑：程序活著，一直沒有登記；`lsregister -u` 與 `-f` 之後一樣；
  - 重開機之後，已安裝的那條路徑由系統啟動，登記好了；接著 A → B 更新成功（容器沿用、學習檔雜湊不變、沒有沙盒拒絕）。
- **乾淨重現**（全新路徑，同一個主 bundle 路徑依序放三種版本）：
  1. framework 裡沒有 XPC 服務的版本（v0.4.7 的配置），先跑一次；
  2. 換成有 `Installer.xpc` 的版本：沒有登記；
  3. 換成同一版、但 framework 搬到 `Contents/Frameworks/U/`（主程式加一條 rpath）：登記好了；
  4. 換回原位置：又沒有登記；
  - 對照組（有服務的版本放在全新路徑）：登記好了。
- **結論**：系統照 framework 的路徑記住「這裡沒有 XPC 服務」，開機期間不會更新，重開機才清。登出能不能清，沒有量。機制在系統內部，這裡只有黑箱量測；其他 macOS 版本沒量（§4.3 B、§4.4 在維護者的系統上補量）。
- **對正式版的影響**：v0.4.7 的 Sparkle.framework 沒有 XPC 服務（about-update 的做法），裝在 `~/Library/Input Methods/善解輸入法.app/Contents/Frameworks/Sparkle.framework`。如果 0.5.0 的 framework 還放在同一個位置，0.4.7 → 0.5.0 會成功（舊版的 Sparkle 不需要安裝服務），但重開機之前 0.5.0 → 0.5.1 會失敗。修法在 §2.8。

**沒量到，而且這片的安全靠它的**：沒有移轉清單時不搬。這是推論：只量過有清單的情況，而偏好設定沒寫進清單也被搬了，所以不能當然成立。§4.1 第 3 項在 CI 斷言；不成立就停（§5）。

其他沒量到的項目，在 §4 用 CI、虛擬機與實機補量：

- 實際打字；
- 由 LaunchServices 啟動；
- `IsSecureEventInputEnabled`；
- Caps Lock；
- 同 ID 重新 ad-hoc 組建之後的 container；
- Developer ID 的 container；
- 沙盒裡的 Sparkle：讀 feed、安裝服務、更新後新版接手 container；
- 從沒有沙盒的版本經 Sparkle 更新成沙盒版；
- 設定頁 App 圖示；
- 其他 macOS 版本。

## 2. 改動

### 2.1 entitlements

- `scripts/build-app.sh` 依 `BUNDLE_ID` 產生 entitlements 檔，四個鍵：
  - `com.apple.security.app-sandbox = true`；
  - `com.apple.security.network.client = true`（§2.8）；
  - `com.apple.security.temporary-exception.mach-register.global-name = [<BUNDLE_ID>_Connection]`。這個名稱必須逐字等於 Info.plist 的 `InputMethodConnectionName`，所以不寫死，由 `BUNDLE_ID` 產生；
  - `com.apple.security.temporary-exception.mach-lookup.global-name = [<BUNDLE_ID>-spks, <BUNDLE_ID>-spki]`（§2.8），同樣由 `BUNDLE_ID` 產生。
- **ad-hoc 簽章**：`codesign --force --sign - --entitlements <檔>`，只加在 App 上，**不加 hardened runtime**。
  - 原因：加了 runtime，library validation 會拒絕 ad-hoc 的 `Sparkle.framework`（about-update 的實作修訂，2026-10-16 實測）。
  - 沙盒不需要 runtime；§2.6 第 3、6 步用「container 之前不存在、之後存在」證明沙盒有套上。
  - 修訂一寫的 `--options runtime` 拿掉。
- **`scripts/check-entitlements.sh <app>`**（新增）：
  - `codesign -d --entitlements - --xml` 經 `plutil` 正規化後，必須**完全等於**上面四個鍵；
  - 兩個 mach 名稱各自等於由該 bundle 的 `CFBundleIdentifier` 算出的值（connection 名稱另外比對 `InputMethodConnectionName`）；
  - 不符就結束碼非 0；
  - `check-app.sh` 第 3 項改呼叫它（原本要求「沒有 entitlements」）。
- Sparkle 的程式碼位置一律沒有 entitlements（§2.8）。
- 安裝程式不變，仍然沒有 entitlements（`check-installer.sh` 不動）。

### 2.2 移轉清單只給正式 ID

- 只有 `BUNDLE_ID` 是正式 ID（`com.nyanako.inputmethod.shanjie`）時，`build-app.sh` 才放 `Contents/Resources/container-migration.plist`，內容是 `Move = [${ApplicationSupport}/shanjie]`。新詞學習也存在這個資料夾裡的 `learning.tsv`（§1），一起搬。
- 其他 ID 不放。依 §1 的推論，沒有清單就不搬，所以開發版與拋棄式組建不會帶走真正的學習資料；§4.1 第 3 項驗證這個推論。
- 偏好設定不寫進清單：系統自己會搬（§1 量到一次），由 §4.1 第 3 項在 CI 斷言。

### 2.3 本機組建預設用開發版 ID（使用者 2026-10-09 決定）

- **ID 與名稱**（`build-app.sh` 依 ID 決定，沒有其他開關）：

  | ID | 資料夾 | `CFBundleDisplayName`、zh-Hant 名稱 | en 名稱 | 移轉清單 |
  |---|---|---|---|---|
  | `com.nyanako.inputmethod.shanjie`（正式） | `善解輸入法.app` | 善解輸入法 | Shanjie | 有 |
  | 其他任何 ID（預設 `com.nyanako.inputmethod.shanjie.dev`；selftest 與 CI 的拋棄式 ID 也是） | `善解（開發版）.app` | 善解（開發版） | Shanjie (Dev) | 沒有 |

  輸入模式 ID、連線名稱、Sparkle 的兩個 mach 名稱、偏好設定網域、container 都跟著 ID 走，開發版不碰正式版的任何一樣。
- `build-app.sh` 的 `BUNDLE_ID` 預設改成 `com.nyanako.inputmethod.shanjie.dev`。
  - 更新測試版（`SHANJIE_UPDATE_TEST_FEED`）沒給 `BUNDLE_ID` 時同樣是開發版 ID。
  - `SHANJIE_RELEASE_BUILD=1` 必須搭配正式 ID，否則 `build-app.sh` 結束碼非 0。
- `Makefile`：
  - 加 `DEV_APP_NAME := 善解（開發版）.app`，`selftest-bundled` 與 `clean-bundle` 用它；
  - `make installer` 只接受正式 ID 的組建：`build/善解輸入法.app` 不存在就報錯，訊息寫要先 `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie`。
  - `make installer` 打包前另外斷言 `build/善解輸入法.app` 的 `CFBundleIdentifier` 是正式 ID、通過 `scripts/check-entitlements.sh`；
  - `make selftest-bundled` 執行的是剛組好的那一份（資料夾名稱依 `SELFTEST_BUNDLE_ID`，規則和 `build-app.sh` 相同）；
  - `clean-bundle` 也清 `build/default/`、`build/nomanifest/` 的 bundle 與 `build-app.sh` 寫在各 `OUT_DIR` 的 `.entitlements-*.plist`。
- `build-app.sh` 組裝前刪掉 `OUT_DIR` 裡**兩個**資料夾名稱（`善解輸入法.app`、`善解（開發版）.app`），換 ID 重建時不會留下另一個 ID 的舊 bundle。
- **`scripts/install-ime.sh`**（安裝程式裡附的是同一份，`check-installer.sh` 逐位元比對）：從傳入的 bundle 讀 `CFBundleIdentifier` 決定目的地，不寫死開發版的名稱。
  - 正式 ID：目的地 `善解輸入法.app`、舊版 `shanjie.app` 的升級、`.shanjie-previous`，和現在逐行相同。
    - 只接受本專案 Developer ID（`Developer ID Application`、`TeamIdentifier=2LJ882GPY8`，從 `codesign -dv --verbose=2` 讀）簽章的 bundle；`codesign` 讀不到簽章也拒絕。在複製任何東西之前檢查，訊息指向開發版 ID 的 `make bundle`。只有 files-only 測試模式（`SHANJIE_INSTALL_FILES_ONLY=1`，假 app 沒有簽章）跳過。
  - `com.nyanako.inputmethod.shanjie.dev`：
    - 目的地 `善解（開發版）.app`，保留上一版在 `.shanjie-dev-previous`；
    - 沒有舊版名稱的處理；
    - 結束舊程序的比對樣式只比對開發版的路徑。
  - 其他 ID：拒絕安裝，結束碼非 0。
  - `scripts/test-install-ime.sh`（暫存 HOME）加兩個情境：
    - 已有正式版 `善解輸入法.app` 時安裝開發版，正式版原封不動；
    - 正式版照舊裝到 `善解輸入法.app`。
    - 沒開 files-only 時，正式 ID 的 ad-hoc 與未簽章 bundle 都被簽章檢查拒絕，什麼都沒建立。
- **`scripts/check-app.sh`**：要檢查的 ID 由環境變數 `EXPECTED_BUNDLE_ID` 給（沒給時用正式 ID），資料夾與各個名稱照上表比對，不再寫死。另外：
  - CI 以外（`CI` 不是 `true`）遇到正式 ID 的組建時，第 6 項（執行 selftest 與被拒絕的參數）不跑，結束碼非 0，訊息寫明原因：本機的 ad-hoc 組建不能先建立正式 ID 的 container。
  - `SKIP_RUN=1` 時只做第 2、3 項，不執行 bundle，結束碼 0。§5 的本機限制期間用這個模式；正式 ID 的組建也一樣。
  - 第 6 項前後快照比對的內容不變。container 是否存在不放進那個相等比對，改由 §2.6 的步驟分別檢查「之前沒有、之後有」。
- `make selftest-bundled` 照舊用拋棄式 ID（本機預設 `com.nyanako.inputmethod.shanjie.selftest`），沒有移轉清單。
  - s3b §9 與 `Makefile` 註解原本寫「selftest 不寫任何檔案或偏好設定」，改成：selftest 本身不寫，但第一次在沙盒裡執行會建立那個 ID 的 container。
- CONTRIBUTING 與 `docs/verification.md` 的本機安裝說明改成：本機組建裝成「善解（開發版）」，要在系統設定加入一次，學習資料和正式版分開。

### 2.4 釋出流程

- `release.yml` 的 build 步驟明確傳 `BUNDLE_ID=com.nyanako.inputmethod.shanjie`（和 `SHANJIE_RELEASE_BUILD=1` 一起）。
- `sign` 由內而外重簽：
  - 先簽 Sparkle：`Installer.xpc`、`Autoupdate`、`Updater.app`、framework，都加 runtime、timestamp，不加 entitlements（§2.8）；
  - 再簽 App：用 workflow 裡以字面值產生的四個鍵（正式 ID）加 `--entitlements` 簽，不沿用 build 產出的 entitlements（F7）。這樣出貨的 entitlements 不依賴 build 工作，`verify` 是第二道檢查。
  - 任何一步都不用 `--deep` 簽（驗證可以用 `--deep`）。
  - `sign` 仍然不執行 repo 裡的程式碼。
- **`verify()` 與 `verify_sparkle()` 改成依 bundle 給預期值**。比對方式和 `check-entitlements.sh` 相同，但寫在 workflow 裡，因為 `sign` 不執行 repo 程式碼；兩處的說明互相指向對方。
  - IME：
    - `CFBundleIdentifier` 是正式 ID；
    - entitlements 完全等於 §2.1 的四個鍵；
    - mach 名稱等於由 ID 算出的值；
    - `Contents/Resources/container-migration.plist` 存在，正規化後完全等於 `Move = [${ApplicationSupport}/shanjie]`（§2.2，和 `check-app.sh` 同一個比對）。
  - Sparkle 的五個程式碼位置：`Installer.xpc`、`Autoupdate`、`Updater.app`、framework，加上 App 本身的 Team 與 runtime 檢查照舊。
    - 前四個都沒有 entitlements；
    - `XPCServices` 裡只有 `Installer.xpc`；整個 framework 裡（不跟隨符號連結）唯一的 `.xpc` 是 `Versions/B/XPCServices/Installer.xpc`，`Versions/Current` 是指向 `B` 的符號連結，最上層的 `XPCServices` 若存在必須是指向 `Versions/Current/XPCServices` 的符號連結（`sign` 與 `verify_sparkle` 都查，和 `check-app.sh` 第 3 項同一個函式）；
    - Info.plist 的 `SUEnableInstallerLauncherService` 是 `true`（原本是 `false`），`SUEnableDownloaderService` 不存在，其他 Sparkle 鍵照舊逐字比對。
    - 〔修訂三〕framework 在 `Contents/Frameworks/Updater/Sparkle.framework`：`Contents/Frameworks` 裡只有 `Updater`，`Updater` 裡只有 `Sparkle.framework`（`sign` 與 `verify_sparkle` 都查，和 `check-app.sh` 第 3 項同一條，§2.8）。
  - entitlements 的比對一律是「正規化後整份相等」，不是「每個預期的鍵都在」；多一個鍵就失敗（F7）。
  - 安裝程式：`CFBundleIdentifier` 是 `com.nyanako.shanjie.installer`，沒有任何 entitlements（現行規則）。
- **dry run 保留產出檔一天**（使用者 2026-10-09 決定）：dry run 用 `actions/upload-artifact` 上傳簽章、公證後的兩個 zip，`retention-days: 1`，給 §4.2 的虛擬機與 §4.3 B 的實機驗收用。正式發佈不變。
- 「沒有 entitlements」「XPC 服務移除」的說法要跟著改：
  - s3b §3、§9；
  - `docs/verification.md`；
  - about-update §2、§3 第 2 項、§5；
  - PLAN §7 R9：改成「hardened runtime（出貨版）＋App 沙盒（四個 entitlements）」；
  - PLAN §7 R10：`SUEnableInstallerLauncherService` 改為 true、Installer.xpc 保留、App 有 `network.client`。

### 2.5 卸載、退回與搬回

- cask 的 `zap` 加上 `~/Library/Application Support/shanjie` 與 `~/Library/Containers/com.nyanako.inputmethod.shanjie`。
- **退回**：
  - 移轉是「搬」。降回沒有沙盒的版本時，舊版看到的是空的學習資料與預設設定。
  - 退回期間學到的資料留在舊位置；再升級時 container 已經存在，不會再搬。
  - 這兩點寫進 `install-ime.sh` 與安裝程式「重新安裝」的退回說明。
  - 另外寫進 `docs/sandbox-restore.md`：Sparkle 不會降版，安裝程式也不會（`InstallerPlan.decide`），所以退回要用 `install-ime.sh`，或先刪掉已安裝的那一份再用舊版的安裝程式。
- **搬回**：
  - `docs/sandbox-restore.md` 寫一個手動指令：
    1. 先結束輸入法程序；
    2. 把 container 裡的學習資料夾 `ditto` 複製回原位置，比對雜湊；偏好設定用 `defaults export`（讀 container 裡的 plist）與以原位置 plist 完整路徑當網域的 `defaults import` 寫回（用網域名稱寫會寫進 container，2026-10-20 §4.2 第 3 項量到），不直接複製 plist（cfprefsd 的快取可能忽略直接寫入的檔案〔推論〕，和 §5 一致）；
    3. 相同時刪掉**整個** `~/Library/Containers/com.nyanako.inputmethod.shanjie`。
  - 一定要刪 container：它只在第一次建立時移轉，留著的話，下次升級到沙盒版不會再搬，搬回來的資料就看起來不見了。
  - §4.2 第 3 項在虛擬機驗證。不做自動搬回。
- 不照蘋果文件「刪掉 container 再測一次」的步驟，那會刪掉唯一的一份資料。只在拋棄式 runner 或虛擬機裡這樣做。

### 2.6 CI 的步驟與順序（`ci.yml` 的 shell job）

照這個順序，前面的步驟不得啟動任何沙盒組建：

0. `make bundle OUT_DIR=build/default`（不帶 `BUNDLE_ID`）：只建置。用 PlistBuddy 斷言：
   - `CFBundleIdentifier` 是 `com.nyanako.inputmethod.shanjie.dev`；
   - 資料夾與名稱照 §2.3 的表；
   - 沒有 `Contents/Resources/container-migration.plist`。
1. `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie`：正式 ID，`build/善解輸入法.app`。只建置，不執行。斷言有 `container-migration.plist`。
2. `make bundle BUNDLE_ID=com.nyanako.inputmethod.shanjie.ci-nomanifest OUT_DIR=build/nomanifest`：拋棄式 ID，沒有移轉清單。只建置。

   2a. **本機防呆的驗證**（在 CI 裡做，不在維護者的機器上）：
   - `CI= EXPECTED_BUNDLE_ID=com.nyanako.inputmethod.shanjie scripts/check-app.sh build/善解輸入法.app` 必須結束碼非 0、印出拒絕訊息；
   - 接著斷言 `~/Library/Containers/com.nyanako.inputmethod.shanjie` 仍然不存在；
   - `SKIP_RUN=1` 的同一個指令必須結束碼 0（只做第 2、3 項）。
   - 防呆如果寫錯，第 3 步的「container 已存在就拒絕」也會擋下來。
3. **`scripts/test-sandbox-migration.sh build/善解輸入法.app "build/nomanifest/善解（開發版）.app"`**：這個 job 第一次啟動沙盒組建。
   - **拒絕條件**：結束碼非 0、什麼都不寫、不啟動任何東西。依這個順序檢查：
     1. `CI` 不是 `true`、`GITHUB_ACTIONS` 不是 `true`，或 `RUNNER_ENVIRONMENT` 不是 `github-hosted`（只在拋棄式的 GitHub-hosted runner 上跑，不在 self-hosted 或只設了 `CI` 的機器上跑）；
     2. `HOME` 不是這個帳號真正的家目錄：用 `dscl` 查 `NFSHomeDirectory`，和 `install-ime.sh` 的做法相同。container 的移轉看的是帳號的家目錄，不是 `$HOME`；
     3. 帳號的家目錄下，下列任一已經存在：`~/Library/Application Support/shanjie`、`~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist`、`~/Library/Containers/com.nyanako.inputmethod.shanjie`、拋棄式 ID 的 container、已安裝的 `~/Library/Input Methods/善解輸入法.app`。
   - **放好來源**：
     - 合成的 `learning.tsv`（含一筆前文鍵 `#` 的新詞記錄）與 `learning.tsv.corrupt`；
     - 對目錄設不備份旗標；
     - `defaults write com.nyanako.inputmethod.shanjie layout eten`（安裝程式寫的就是這個鍵，§2.7）。
     - 記下每個檔的雜湊與權限。
   - **沒有清單不搬**（§1 的推論）：執行拋棄式組建的 `--selftest` 一次。斷言：
     - 來源還在原位、逐位元相同；
     - 拋棄式 ID 的 container 之前不存在、之後存在（沙盒有套上）；
     - container 裡沒有學習資料。
   - **移轉**：執行正式組建的 `--selftest` 一次。斷言：
     - 來源消失；
     - container 裡逐位元組相同，0700／0600；
     - 不備份旗標保留；
     - container 裡的偏好設定 `layout=eten`。
   - **只搬一次**：再放一份新的學習資料來源（只放學習檔，不寫偏好設定），再執行一次：不會再移轉，學習資料來源原封不動。
   - 結束前確認來源的偏好設定檔 `~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist` 已不存在（被搬走），第 7 步的快照才不會受 cfprefsd 延遲寫入影響。
   - 讀 container 如果在 CI 上跳 App Data 的 TCC 詢問而卡住（N6），改成由同一個沙盒執行檔回報它讀到的內容；改法寫進這份契約再做。
4. 同一個腳本的拒絕測試，兩種情況都要結束碼非 0、暫存目錄裡沒有新檔案：
   - `CI= HOME=<暫存目錄> …`，log 裡有「CI is not true」的拒絕訊息；
   - `CI=true HOME=<暫存目錄> …`，log 裡有家目錄不符的訊息。
5. `scripts/test-install-ime.sh`、`swift test`（不變）。
6. `make selftest-bundled SELFTEST_BUNDLE_ID=com.nyanako.inputmethod.shanjie.selftest-$GITHUB_RUN_ID`，前後各檢查一次 `~/Library/Containers/<該 ID>`：之前不存在、之後存在。
   - 原本傳空值、用正式 ID 的那一步（`ci.yml` 現在的 `SELFTEST_BUNDLE_ID=`）取消；正式 ID 的 selftest 已在第 3 步與第 7 步跑過。
7. `EXPECTED_BUNDLE_ID=com.nyanako.inputmethod.shanjie scripts/check-app.sh build/善解輸入法.app`。
8. **突變測試**（`scripts/test-sandbox-mutations.sh`，本機與 CI 共用；實作時改成腳本）：複製 `build/善解輸入法.app` 十份（〔修訂三〕原本八份），各做一種改動（APFS 的 clone，`cp -Rc`，不支援時退回 `ditto`；放複本的暫存目錄先 `touch .metadata_never_index`）。
   - 用 `codesign --force --sign -` 重簽（不帶 `--preserve-metadata`）：`scripts/check-entitlements.sh` 要失敗。
   - 帶 `--preserve-metadata=entitlements` 重簽：`scripts/check-entitlements.sh` 要通過。
   - entitlements 多一個鍵（例如 `com.apple.security.cs.disable-library-validation`）重簽：`scripts/check-entitlements.sh` 要失敗。
   - 兩個 mach 名稱換成別的 ID 重簽：`scripts/check-entitlements.sh` 要失敗。
   - 刪掉 `Installer.xpc` 再重簽 framework 與 App：`check-app.sh` 第 3 項要失敗。
   - `XPCServices` 裡多放一個 `.xpc`（複製 `Installer.xpc` 改名）再重簽：`check-app.sh` 第 3 項要失敗。
   - `XPCServices` 以外（`Versions/B/`）多放一個 `.xpc` 再重簽：`check-app.sh` 第 3 項的整個 framework 檢查要失敗。
   - 把 Info.plist 的 `SUEnableInstallerLauncherService` 改成 `false` 再重簽：`check-app.sh` 第 2 項要失敗。
   - 〔修訂三〕把 `Sparkle.framework` 搬回 `Contents/Frameworks/`（舊位置）再重簽 App：`check-app.sh` 第 3 項的位置檢查要失敗。
   - 〔修訂三〕新位置之外，`Contents/Frameworks/` 再放一份 `Sparkle.framework` 再重簽 App：`check-app.sh` 第 3 項的位置檢查要失敗（`Contents/Frameworks` 裡多一項）。
   - 第 3 步在拒絕檢查通過之後、放來源之前，在 log 印出 runner 的 macOS 版本（`sw_vers`），「沒有清單不搬」量到的是哪一版要看得到（F4）。拒絕路徑因此完全沒有輸出以外的副作用。
   - `release.yml` 的那份比對不在分支上跑反向測試（`sign` 只在 main 觸發）；它和 `check-entitlements.sh` 用同一套比對方式，兩處的說明互相指向。
9. `make installer`、`scripts/check-installer.sh`（不變，用第 1 步的正式組建）。

### 2.7 倚天排列的沿用改在換檔之前寫

- **現況**：
  - `Registration.fullFlow` 在註冊、啟用、而且使用者在系統設定接受之後，才寫 `layout=eten`（`Registration.swift` 約第 169 行）。
  - 安裝程式等使用者接受的那段時間，沙盒版可能已經啟動、偏好設定已經搬走。這時寫在 container 外，靜靜地失效。
- **改法**：
  - 把「只開了倚天模式、而且還沒選過排列」的判斷與寫入抽成一個函式。
  - 安裝程式在**換檔之前**、用開始時讀到的輸入法狀態呼叫它一次。那時裝的是沒有沙盒的舊版，container 還不存在；之後第一次啟動沙盒版時，這個值隨偏好設定一起搬。
  - 寫了 `layout` 之後、換檔之前，安裝程式輪詢 `~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist`，等到磁碟上的檔案真的有 `layout`（最多 10 秒，和 §2.6 第 3 步等 `defaults write` 的做法相同）：系統搬的是檔案，不是 cfprefsd 的快取。等不到就記一行靜態訊息、照樣安裝，不卡住。
  - `fullFlow` 裡原本的呼叫保留：`shanjie install` 在沙盒裡執行，寫的就是 container 裡的網域；已經選過排列時不重寫。
- **container 已經存在時**（從沙盒版再升級）：
  - 安裝程式從外面寫的值不會被讀到。
  - 這時輸入法本來就已經有自己的 `layout`，或使用者已經用過沙盒版，所以不需要沿用。
  - 這一點寫進 `Registration.swift` 的註解，不另做處理。
- **測試**：
  - 安裝程式流程的單元測試斷言這個函式在換檔之前被呼叫、換檔等到檔案有 `layout` 才開始、等待有上限；測的是安裝程式複製路徑實際呼叫的 `InstallerFlow.copy`（`main.swift` 只傳入路徑與換檔函式）；
  - §2.6 第 3 步斷言「在第一次啟動之前、用 `defaults` 寫的 `layout`」會被搬進 container。

### 2.8 Sparkle 在沙盒裡（修訂二新增）

- **升到 Sparkle 2.10.0**（維護者 2026-10-19，§6 第 5 項）：
  - `macos/Package.swift` 改 `exact: "2.10.0"`，`Package.resolved` 跟著更新；
  - `release.yml` 下載 `generate_appcast` 的官方 tarball 改成 2.10.0，SHA-256 照樣以字面值寫在 workflow 裡；
  - `LICENSES/Sparkle-MIT.txt` 換成 2.10.0 的 `LICENSE` 原文；
  - about-update 與 PLAN R10 裡的版本號跟著改。
  - 2.10.0 的行為變動以它的 release notes 為準；`Updater.swift` 用到的介面要能編譯、單元測試照過。§4.3 A 的更新實機測試同時就是 2.10.0 的重測。
- **Info.plist**：
  - `SUEnableInstallerLauncherService` 由 `false` 改成 `true`，所有組建都一樣。本機組建不啟動更新服務（`ShanjieUpdateBuild=local`），這個鍵只在 release 與更新測試版有作用。
  - `SUEnableDownloaderService` 不設；`check-app.sh` 與 `verify_sparkle` 斷言它不存在（F6）。
  - 其他 Sparkle 鍵不變。
- **XPC 服務**：
  - `build-app.sh` 只刪 `Downloader.xpc`，保留 `Sparkle.framework/Versions/B/XPCServices/Installer.xpc`。
  - 斷言 `XPCServices` 裡**只有** `Installer.xpc`（F7）。
  - `check-app.sh` 第 3 項、`release.yml` 的 `sign` 與 `verify_sparkle` 照這個改：原本「XPCServices 不存在就對」改成這一條，另外查整個 framework 的版面（§2.4）。
- **framework 的位置**（修訂三，維護者 2026-10-19 選擇題，§6 第 7 項）：
  - `Sparkle.framework` 放在 `Contents/Frameworks/Updater/Sparkle.framework`，不再放 `Contents/Frameworks/Sparkle.framework`。
  - 原因（§1 的「2026-10-19，虛擬機」；黑箱量測，機制是〔推論〕）：看起來系統照 framework 的路徑記住「沒有 XPC 服務」，重開機才清。舊位置被 v0.4.7（沒有 XPC 服務）用過，0.5.0 留在那裡的話，更新上來之後、重開機之前，系統不會登記安裝服務。新位置在任何舊版裡都沒出現過，第一次出現就帶著 `Installer.xpc`。
  - 主程式的 rpath 由 `@executable_path/../Frameworks` 改成 `@executable_path/../Frameworks/Updater`（`macos/Package.swift`）。`Contents/Frameworks` 不再在搜尋路徑上，framework 留在舊位置時程式啟動就失敗（CI 的 selftest 會抓到）。SwiftPM 另外會自己加 `@loader_path`（`Contents/MacOS`），組建只在那裡放執行檔。
  - 檢查（舊位置被搬回來，Sparkle 照樣能跑，只有更新會壞，所以一定要擋）：
    - `check-app.sh` 第 3 項、`release.yml` 的 `sign` 與 `verify_sparkle` 斷言 `Contents/Frameworks` 裡只有 `Updater`、`Updater` 裡只有 `Sparkle.framework`；
    - 突變測試多一份「搬回舊位置」（§2.6 第 8 步）。
  - `Updater` 底下的 XPC 服務之後要一直剛好是 `Installer.xpc`：哪一版要改（例如加回 `Downloader.xpc`、Sparkle 改了服務名稱），就把 framework 換到新的資料夾名稱，否則同樣的問題可能在那次換版重現（〔推論〕快取記的可能是整組服務，不只「有沒有」）。這一點寫進 `build-app.sh` 的註解。
  - Sparkle 自己用 `NSBundle` 找自己的 bundle 與 `Autoupdate`（`InstallerLauncher` 從 `Installer.xpc` 往上兩層找），和 framework 放在哪一層無關；`checkIfConfiguredProperly` 查的也是 framework 自己的 `XPCServices`。
- **ad-hoc 簽章順序**（由內而外，不用 `--deep`）：`Installer.xpc` → `Autoupdate` → `Updater.app` → framework → App（只有 App 帶 entitlements）。
- **網路：給輸入法 `network.client`，不用 Downloader.xpc**（安全審查確認，§7.2 F6）：
  - 在「被攻破的輸入法能不能把資料送出去」這件事上，兩種做法沒有差別：Downloader.xpc 照呼叫者給的請求原樣送出（§1）。
  - Downloader.xpc 反而更糟：它在沙盒外，快取路徑由呼叫者給的 bundle ID 組成，沒有清理，可能成為在 container 外寫入與刪除的代理（審查的推論）。
  - `network.client` 是 Sparkle 的標準做法，也沒有 Downloader 的已知限制（更新說明的網頁檢視退回舊元件）。
- **安裝服務與沙盒的定位**（維護者 2026-10-19：保留一鍵更新、說法改縱深防禦，§6 第 4 項）：
  - `Installer.xpc` 啟動 Sparkle 的 `Autoupdate`。它在沙盒外，由它換掉 `~/Library/Input Methods` 裡的 bundle。
  - 換掉真正的 bundle 仍然要通過 EdDSA（§1），R10 的信任模型不變。另外，持有 EdDSA 私鑰的人推的更新可以拿掉 entitlements，沙盒對這條路沒有幫助（和今天一樣）。
  - Installer.xpc 理論上可能讓被攻破的沙盒輸入法在沙盒外執行程式（§1）。所以沙盒是**縱深防禦**：它讓一般的漏洞利用讀不到使用者的文件、金鑰與其他 App 的資料，也寫不到 container 以外；但針對 Sparkle 的攻擊者可能逃出，逃出之後等於今天沒有沙盒的輸入法。
  - 網路本來就擋不住（`network.client`），現在沒有沙盒時同樣擋不住。
  - **對外說明**：
    - 網站隱私段的內容（只有 Sparkle 連到 GitHub）不變，連線行為沒有改。
    - 新增的說法只能是「輸入法在 App 沙盒裡執行（縱深防禦）」，不寫「沙盒讓輸入內容無法外流」「沙盒擋下任何攻擊」之類的話。
    - PLAN R9／R10、README、發版說明照這個口徑。
  - **根本解**（沙盒外、固定更新對象的獨立更新程式）不在這片：排到 0c B 片，和 R8 的 XPC 輔助程式一起做（§3）。
- **更新之後的 container**：新版同 ID、同 Team（正式版）。container 照簽章的指定需求比對，由新版接手（§4.2 第 1 項、§4.3 B 量）。
- **從沒有沙盒的 0.4.x 經 Sparkle 更新到沙盒版**：舊版的 Sparkle（沒有安裝服務）換掉 bundle，新版第一次啟動時才移轉。這和用安裝程式換檔的差別只在誰換檔；移轉看的是新版第一次啟動（§4.3 A 第 1 步量開發版 ID 的同一條路）。

### 2.9 輸入法碰到的其他系統資源（修訂二新增）

照 §1 的盤點，沙盒可能影響的只有下面幾處，都列進驗收或停止條件：

- **系統標點表**：從 `/System/Library/PrivateFrameworks` 讀。讀不到就停（§5，修訂一已有）。
- **設定頁「App 設定」**：用 `NSWorkspace` 取得 App 的位置、名稱與圖示。實機看清單顯示得出 App 名稱與圖示（§4.3 B 第 8 項）。顯示不出來不停，記下來另開一片。
- **前景 App 的 bundle ID**（聊天／書面的自動判斷）：`NSWorkspace.frontmostApplication`。實機在聊天 App 與文字編輯器之間切換，設定照舊切換（§4.3 B 第 9 項）。
- **網站連結**：SwiftUI `Link` 交給系統開啟，不需要額外權限。
- **設定視窗的焦點**：代理型 App（`LSUIElement`）在沙盒裡呼叫 `NSApp.activate()`，關閉時把焦點還給前一個 App（`SettingsWindow.swift`、`InputController.swift` 的同一個做法）。實機開關一次設定視窗，看焦點有沒有回去（§4.3 B 第 11 項）。
- `~/Applications` 裡的 App 在沙盒裡可能讀不到圖示〔推論〕；照上面「顯示不出來不停」處理。

## 3. 不在這一片（第二片或之後）

- 〔修訂三〕更新失敗時把 Sparkle 的「connecting to the installer」錯誤轉成「重新開機後就能更新」的提示（審查建議，涵蓋沒量過的情況：登出、其他 macOS 版本）；先靠 §4.3 A 第 2b 步與 §4.4 的實機量測，碰到再做；

- 網站隱私說明補 Time Machine（F6）；
- 學習的 denylist 擴充（F7）；
- 安裝程式的簽章檢查（F9）；
- 使用者層的詞包位置（N7，ACG 詞包 C 片要先決定）；
- 註冊用的輔助程式：§1 量到不需要，除非 §4 的實機推翻；
- **下載的模型與 R8 的 XPC 輔助程式**：
  - 0c 實驗開關的 B 片要下載第三方權重，PLAN R8 要求它在沒有網路的沙盒 XPC 輔助程式裡解析。
  - 這片只讓輸入法本體進沙盒，不做輔助程式。
  - B 片要在沙盒版上設計下載位置（container 裡）與輔助程式。
  - 沙盒外、固定更新對象的獨立更新程式（F1 的根本解，§6 第 4 項）一起放在 B 片設計。
- 詞庫與模型的更新管道（#121）。

## 4. 驗收

### 4.1 CI（§2.6 的步驟，PR 的 CI 紀錄裡看得到順序與 ID）

1. entitlements 與 Sparkle：
   - 第 7 步的 `check-app.sh` 第 2、3 項通過：四個鍵、`Installer.xpc` 在、`Downloader.xpc` 不在、Sparkle 位置沒有 entitlements、`SUEnableInstallerLauncherService` 是 true、framework 在 `Contents/Frameworks/Updater/`（修訂三）；
   - 第 8 步的十份突變各自照預期：
     - 不帶 `--preserve-metadata` 重簽、多一個鍵、mach 名稱換成別的 ID：`check-entitlements.sh` 失敗；
     - 帶 `--preserve-metadata=entitlements` 重簽：`check-entitlements.sh` 通過；
     - 刪掉 `Installer.xpc`、`XPCServices` 裡或外多一個 `.xpc`：`check-app.sh` 第 3 項失敗；
     - `SUEnableInstallerLauncherService` 改成 `false`：`check-app.sh` 第 2 項失敗；
     - framework 搬回 `Contents/Frameworks/`，或兩個位置都有：`check-app.sh` 第 3 項失敗（修訂三）。
2. 沙盒有套上：第 3 步拋棄式 ID 的 container、第 6 步 selftest ID 的 container，都是之前不存在、之後存在。
3. 移轉與「沒有清單不搬」：第 3 步的所有斷言。
4. 拒絕：第 4 步。
5. 名稱與移轉清單（§2.6 第 0、1 步）：非正式 ID 的組建沒有 `container-migration.plist`、名稱照 §2.3 的表；正式 ID 的組建有。
6. `make bundle` 不帶參數時的 `CFBundleIdentifier` 是開發版 ID（§2.6 第 0 步）。
7. `test-install-ime.sh` 的兩個新情境（§2.3）；`check-installer.sh` 照樣通過。
8. 本機防呆：§2.6 第 2a 步（在 CI 裡驗證，不在維護者的機器上跑任何正式 ID 組建）。
9. §2.7 的單元測試。

### 4.2 虛擬機（main 執行，用 dry run 的 Developer ID 版）

**dry run 的版本號是 0.0.0**（`release.yml` 的 `workflow_dispatch` 寫死，關於頁顯示「開發版」）。安裝程式不降版：已裝 0.4.x 時遇到內附 0.0.0，只啟用、不複製（`InstallerPlan.decide`）。所以：

- 這一節與 §4.3 B 一律用 dry run 的 zip 解出來，以 `scripts/install-ime.sh` 安裝；它不比版本。
- 安裝程式的升級路徑（0.4.x → 0.5.0，含 §2.7 的倚天排列）在 v0.5.0 發佈後量（§4.4）。

虛擬機裡沒辦法用介面打字，所以學習資料用合成的檔案，驗收用檔案比對，不用「選字排第一」。

1. **升級**：在全新帳號裝 0.4.x（現行 Latest）。第一次啟動沙盒版之前，放好來源（和 §2.6 第 3 步相同的做法）：
   - `~/Library/Application Support/shanjie/learning.tsv`（合成，含一筆前文鍵 `#` 的新詞記錄），資料夾 0700、檔案 0600；
   - `defaults write com.nyanako.inputmethod.shanjie layout eten`（非預設值）；
   - 記下 `learning.tsv` 的雜湊與權限。
   - 用 `install-ime.sh` 換成 dry run 的沙盒版，用 `--selftest` 與 `launchctl asuser` 各啟動一次。斷言：
     - 原位置的 `learning.tsv` 消失，container 裡的那份雜湊相同、0600；
     - container 裡的偏好設定 `layout=eten`；
     - selftest 結束碼 0；
     - `log show --start <換檔前的時間>` 沒有善解或 Sparkle 的沙盒拒絕。
2. **同 ID 重建**：同一個開發版 ID 連續 ad-hoc 組建兩次，各啟動一次（`--selftest`）。斷言：
   - 第二次不跳任何詢問；
   - container 沒有被重建（`Data` 資料夾的 inode 與建立時間不變）。
   - 「第二個組建讀得到第一個組建學到的詞」需要打字，改在 §4.3 A 第 2 步量：A 與 B 是同一個開發版 ID 的兩個不同 ad-hoc 組建。
3. **退回、搬回、再升級**：接著第 1 項的狀態，用 `install-ime.sh` 降回 0.4.x，照 `docs/sandbox-restore.md` 的指令搬回。斷言：
   - 指令結束碼 0、沒有跳詢問；
   - `learning.tsv` 和第 1 項放的那份雜湊相同、權限 0600；
   - `defaults read com.nyanako.inputmethod.shanjie layout` 是 `eten`；
   - container 已不存在。
   - 接著再升級成沙盒版一次：container 裡的 `learning.tsv` 和搬回的那份雜湊相同，沒有跳詢問。
4. **倚天排列**：dry run 的 0.0.0 用安裝程式裝不上去（見本節開頭），所以合併前由 §2.7 的單元測試與 §2.6 第 3 步代替。真正的安裝程式路徑在 v0.5.0 發佈後量（§4.4）。
5. **Sparkle 讀得到 feed**：沙盒版用 `launchctl asuser` 啟動後，開關於頁的「立即檢查」需要介面操作。做不到時，這一項改在 §4.3 A 量。


**2026-10-20 結果**（dry run 39265871734，`shanjie-0.0.0.zip` 雜湊與 Developer ID 簽章、公證裝訂、沙盒 entitlements、`Contents/Frameworks/Updater/Sparkle.framework` 都核對過；全新的 tart 虛擬機，macOS 26.6.2，帳號 admin；全新帳號裡 `install-ime.sh` 會停在「系統還沒接受新的輸入法」（啟用要在系統設定手動加入），檔案已複製到 `~/Library/Input Methods`，這一節只需要那份檔案，以版本號確認）：
- 第 1 項通過：0.4.7 → dry run，原位置的 `learning.tsv` 消失，container 裡的雜湊相同、0600，偏好設定 `layout=eten`，`--selftest` 結束碼 0，換檔之後沒有善解或 Sparkle 的沙盒拒絕。`launchctl asuser` 啟動後程序沒有留著（不是由輸入法框架啟動，推論是自行結束），搬移在 `--selftest` 那次就完成了。
- 第 2 項通過：同一個開發版 ID 的兩個 ad-hoc 組建（CDHash 不同），兩次 `--selftest` 都結束碼 0，container 的 `Data` inode 與建立時間不變。「不跳詢問」在無畫面的虛擬機截不到圖；`tccd` 的 log 在這段時間沒有和善解有關的紀錄、也沒有詢問紀錄（佐證，不是證明）。
- 第 3 項**第一次沒過**：`docs/sandbox-restore.md` 的指令以網域名稱 `defaults import` 寫回偏好設定，container 還在時 `defaults` 把這個網域導向 container 裡的那一份，寫進去的是 container，接著刪 container 時設定一起不見（學習資料正確搬回）。改成以原位置 plist 的完整路徑寫回、並檢查原位置有檔之後，在重新建立的全新虛擬機上第 1、3 項都通過：學習資料雜湊相同、0600，`layout=eten`，container 已刪，再升級時搬回的資料正確搬進新 container，沒有沙盒拒絕。沙盒版還沒發佈，沒有使用者跑過舊的指令。
- 第 4 項由單元測試代替（照本節開頭），第 5 項留給 §4.3 A。終端機刪 container 會不會跳詢問：經 SSH 執行的指令不能代表「終端機」App，這一項仍沒量過。

### 4.3 實機（維護者與 main；每次 ≤ 15 秒的部分由 `imeshot` 做）

**A. Sparkle 在沙盒裡（合併後、dry run 之前；開發版 ID，不碰正式版的資料）**

- 〔2026-10-19〕A 改在 tart 的 macOS 虛擬機做：開發版 ID 在維護者的 Mac 上沒出現過，要登出一次才加得進系統設定；虛擬機裡維護者一樣可以點選、打字。Caps Lock 切換在虛擬機測不了，(c) 留到 B 第 6 項。
- 結果：第 1 步通過；第 2 步第一次失敗（§1 的「2026-10-19，虛擬機」），重開機之後通過。修訂三加第 2b 步，2026-10-19 晚上用修訂三 PR 的 head 做完，通過（研究紀錄同日）。

- **準備**（main 執行）：
  - **先做 B 第 0 步的備份**（F4）：A0 沒有沙盒，會讀寫真正的學習資料夾。
  - 用測試金鑰、本機 feed（`http://127.0.0.1`，about-update §3 第 4 項的做法）建三個更新測試版，都是開發版 ID：
    - A0：合併前的 main，沒有沙盒。合併前的 `install-ime.sh` 只會裝到正式版的資料夾，所以 A0 由 main 手動放到 `~/Library/Input Methods/善解（開發版）.app`，不經 `install-ime.sh`。它的顯示名稱仍是「善解輸入法」（合併前的 `build-app.sh` 寫死），和正式版同名，選單裡用 ID 區分；
    - A：這片，有沙盒；
    - B：這片，有沙盒、版本比 A0 與 A 都新。
    - 版本號用 `SHANJIE_VERSION` 指定：A0 與 A 都是 `0.0.1`，B 是 `0.0.2`。合併前的 `build-app.sh` 預設會取最新的 tag（0.4.x），A0 一定要明確給 `0.0.1`，否則 B 比它舊，Sparkle 會顯示「已是最新」。
- 開發版 ID 沒有移轉清單，所以這一段不搬任何資料。
- **步驟**（順序照這裡，F4）：
  1. **A0 → B**（沒有沙盒的版本經 Sparkle 更新成沙盒版，對應 0.4.x → 0.5.0 的路）：
     - 裝 A0，在系統設定加入一次。這段期間不用 A0 打字，也不用正式版打字，避免兩個程序同時寫同一份學習資料（`learn_store` 的完整重寫沒有鎖）。
     - B 第一次啟動之前與之後，立刻各取一次 `~/Library/Application Support/shanjie` 的雜湊。
     - 設定→關於→立即檢查→「更新到 B」→ Sparkle 下載、安裝、重新啟動。量：
       - (a) 執行中的是 B（關於頁版本、`ps` 的路徑），活動監視器的「沙盒」是「是」；
       - (b) 在 TextEdit 照常打得出字；
       - (c) Caps Lock 切換正常；
       - (d) `log show --last 5m --predicate 'sender == "Sandbox" AND (eventMessage CONTAINS "shanjie" OR eventMessage CONTAINS "sparkle")'` 沒有 deny；
       - (e) 開發版 ID 的 container 是 B 第一次啟動時才建立；
       - (f) 前後兩次雜湊相同：開發版 ID 沒有清單，學習資料夾沒有被搬。這是「沒有清單不搬」在維護者機器的 macOS 上的量測（CI 量的是 runner 的版本）。
  2. **A → B**（沙盒版經 Sparkle 更新成沙盒版）：先用 `install-ime.sh` 裝回 A（開發版的位置），用 A 學一個詞，再照上面更新到 B。量：
     - (a)～(d) 同上；
     - (g) A 學的詞在 B 仍排第一（同一個 container）。
  2b. **A0 → B3 → C3，中間不重開機**（修訂三；虛擬機）：
     - **合併前**做（審查：沒看到它有效就不合併）：用修訂三 PR 的 head 建 B3（`0.0.2`）與 C3（`0.0.3`），都有沙盒、framework 在新位置；A0 沿用（`0.0.1`，framework 在舊位置、沒有 XPC 服務）。PR 之後再改到組建時要重做。
     - 虛擬機先重開機，清掉第 1、2 步留下的狀態。裝 A0、讓系統啟動它一次：等於使用者的機器開機後跑過 0.4.7。
     - feed 只放 B3，A0 → B3。接著 feed 只放 C3，**不重開機**，B3 → C3。量：
       - B3 執行中時，`launchctl print pid/<B3 的 pid>` 列得到 `org.sparkle-project.InstallerLauncher`；
       - 更新到 C3 之後，C3 執行中時同樣列得到（同一條路徑換了一版之後仍有登記，之後每一次更新都是這種情況）；
       - 沒有「connecting to the installer」的錯誤，更新後執行中的是 C3；
       - container 沿用（建立時間不變），裡面的 `learning.tsv` 雜湊和 B3 → C3 之前相同；
       - (d) 同上。
  3. 做完當場移除開發版（`~/Library/Input Methods/善解（開發版）.app` 與系統設定裡的輸入方式）。
- **不通過**（更新失敗、需要登出、B 沒有接手）→ 停（§5），回報維護者。
- 這一段的 A 與 B 都是 ad-hoc 簽章。ad-hoc 時 Sparkle 不驗呼叫者的 Team，和正式版不同（F3、F8），所以正式版的沙盒更新另外在 §4.4 的 canary 量。

**B. 正式 ID 的沙盒版（dry run 的 Developer ID 版，正式發佈之前）**

0. **先備份**（使用者自己做，main 給指令）：§4.3 A 開始前做一次；第一次啟動正式 ID 的沙盒版之前再做一次。把 `~/Library/Application Support/shanjie` 複製到備份位置，偏好設定用 `defaults export com.nyanako.inputmethod.shanjie <檔>` 匯出。
1. **安裝**：記下開始時間。用 dry run 的 zip 解出來，以 `install-ime.sh` 安裝（0.0.0，見 §4.2 開頭），記下它最後一步 `shanjie install` 的結果。
2. 安裝後第一次打字之前：`log show --start <第 1 步的時間> --predicate 'sender == "Sandbox" AND eventMessage CONTAINS "shanjie"'` 沒有任何 deny（涵蓋第一次啟動、資料搬移與 IMK 登記）。
3. 打一句、改選、按 Enter。斷言：
   - 升級前學過的選字仍排第一；
   - 學過的新詞仍出現在候選裡；
   - 倚天排列、選單與設定視窗的設定都還在。
4. Terminal 開 Secure Keyboard Entry，到別的 App 開輸入法選單：第一項是「學習已暫停（安全輸入）」。
5. 清除學習資料的視窗出得來，關掉後焦點回到原本的 App。
6. 換成沙盒版之後，Caps Lock 切換正常。
7. 活動監視器的「沙盒」欄位是「是」。
8. 設定→App 設定的清單顯示得出 App 名稱與圖示（§2.9）。
9. 在聊天 App 與文字編輯器之間切換，聊天／書面的自動判斷照舊（§2.9）。
10. 設定→關於→立即檢查：dry run 是 0.0.0，所以要顯示「有新版本 <當時的 Latest>」，不是「檢查失敗」。這證明沙盒版讀得到正式 feed、驗得過簽章。**不要按「更新到 X」**：按了 Sparkle 會換回沒有沙盒的 0.4.x，資料留在 container 裡；萬一按了，照 `docs/sandbox-restore.md` 搬回，或用第 0 步的備份。
11. 開關一次設定視窗，關掉之後焦點回到原本的 App（§2.9）。
    - 〔2026-10-19，`settings-sidebar.md`〕同一次也看：左欄和右邊同一塊玻璃；設定視窗不是 key 視窗時（從輸入選單打開、還沒點它）看得出選中的是哪一類；左欄文字和右邊一樣清楚；`settings-sidebar.md` §3 第 6、7 步（超出上限、夾回）。
12. 最後再跑一次第 2 步的 `log show --start <第 1 步的時間>`：從安裝到這一步都沒有善解的沙盒 deny；log 裡沒有 "system table unavailable"（系統標點表讀得到）。

### 4.4 順序與 tag

- dry run 只能從 main 觸發（`release.yml` 的 `gate` 與 `sign` 只接受 `refs/heads/main` 的 `workflow_dispatch`）。所以順序是：
  1. §4.1 的 CI 通過；〔修訂三〕修訂三的 PR 另外要先過 §4.3 A 第 2b 步；
  2. 合併；
  3. §4.3 A；
  4. dry run（main 觸發，事先問維護者）；
  5. §4.2；
  6. §4.3 B。
- 從合併到 §4.3 B 全部通過之前，main 不推任何 tag。這段期間其他片照常合併，但都等這片的實機通過後才一起發版。
- 實機都通過之後，才推正式的 tag（照例要使用者當下同意）。這片以下一個次版本（v0.5.0）發佈。
- **§4.3 B 之後維護者機器上的版本**：
  - 維護者繼續用 dry run 的 0.0.0 沙盒版，直到 v0.5.0 發佈。這段期間「有新版本 0.4.x」的提示不要按（§4.3 B 第 10 項）。
  - v0.5.0 發佈後，從 0.0.0 經 Sparkle 更新到 0.5.0。這是正式簽章下「沙盒版經 Installer.xpc 更新成沙盒版」的第一次，等於提早做一次 canary：
    - 斷言更新成功；
    - container 裡的學習資料還在；
    - `log` 沒有沙盒拒絕。
    - 不通過就照下面 canary 的停止條件處理。
  - 如果這片在發佈前被 revert，改用 `install-ime.sh` 裝回 0.4.x，再用 §4.3 B 第 0 步的備份還原。
- **安裝程式的升級路徑**（§4.2 開頭）：v0.5.0 發佈後，在虛擬機裝 0.4.x（只開倚天模式），用 v0.5.0 的安裝程式升級。斷言：
  - 學習資料搬進 container；
  - container 裡的偏好設定 `layout=eten`。
- 第一次真正經 Sparkle 從 0.4.x 更新到沙盒版，要在 v0.5.0 發佈之後實機看（同 about-update §3 第 6 項的做法）：
  - 已安裝的 0.4.x 按「更新到 0.5.0」；
  - 斷言更新後是沙盒版、學習資料已搬進 container。
- **重開機與否要記下來**（修訂三）：上面「0.0.0 → 0.5.0」與下面「0.5.0 → 0.5.1」兩次經 Sparkle 的更新，按下更新之前都記下開機時間（`sysctl -n kern.boottime`）。和上一次換版之間沒有重開機，才算在維護者的 macOS 上量到 §1 的情況；有重開機也照樣記，結論寫明。
- **canary 0.5.1**（F3）：正式版「沙盒版經 Installer.xpc 更新成沙盒版」這條路，在 Developer ID 下第一次執行會是 0.5.0 → 0.5.1；壞掉的話所有 0.5.0 的使用者都卡住。所以：
  - v0.5.0 發佈後，盡快發一個只有小改動的 v0.5.1（推 tag 照例問維護者）；
  - 維護者實機從 0.5.0 按「更新到 0.5.1」：更新成功、container 裡的學習資料還在、`log` 沒有 Sparkle 或善解的沙盒拒絕；
  - 通過之前不發別的版本。
  - v0.5.0 的發版說明與網站安裝段寫明：自動更新失敗時，下載安裝程式重裝即可，資料在 container 裡不受影響。

### 4.5 Sparkle 安裝服務的逃脫路徑（虛擬機驗證，維護者 2026-10-19，§6 第 6 項）

- **目的**：確認 §1 的推論（被攻破的沙盒輸入法能不能借 Installer.xpc 在沙盒外執行程式）。成立就私下回報 Sparkle，不成立就把 §2.8 的說法改回去。
- **做法**：
  - 只在 tart 的 macOS 虛擬機做，不在維護者的機器上做。
  - 用一個最小的沙盒測試程式（和輸入法同樣的 entitlements，連結 Sparkle 2.10.0），不改輸入法本身。
  - 測試程式與步驟細節放在 repo 外，不進 repo；研究紀錄只寫「做了、結果是成立或不成立、已回報」，細節等 Sparkle 回覆或修好之後再公開。
- **回報**：用 GitHub 的私下安全回報（Sparkle 的 security advisories），只描述機制，不帶私人資訊。送出前把內容給維護者看，維護者同意才送。
- **時機**：和這片的實作平行，不擋合併。結果改變 §2.8 的說法時，用同一片的 PR 或下一個文件 PR 更正。

## 5. 停止條件、預算、回滾

- **停止條件**：
  - 沒有移轉清單時資料被搬走（§2.6 第 3 步、§4.3 A 第 1 步 (f)）；
  - IMK 在沙盒裡連不上；
  - `IsSecureEventInputEnabled` 在沙盒裡永遠是 false，學習會在密碼欄繼續；
  - 移轉測試掉資料，或偏好設定沒有搬；
  - Developer ID 版接手不了 container；
  - 搬回的指令跳詢問或資料不同；
  - Caps Lock 壞掉；
  - 系統標點表在沙盒裡讀不到；
  - **Sparkle 在沙盒裡讀不到 feed、安裝服務被拒，或更新後新版接手不了 container**（§4.3 A、B 第 10 項）；
  - **canary 0.5.0 → 0.5.1 經 Sparkle 失敗**（§4.4）：停發其他版本，回報維護者；
  - **新位置在不重開機的情況下仍登記不到安裝服務**（§4.3 A 第 2b 步，修訂三）：回報維護者，不改用其他位置再試；
  - 〔修訂三〕§4.3 A 第 2 步第一次失敗已處理（§1、§2.8），不再算停止條件；
  - Sparkle 2.10.0 的行為變動讓「關於」頁的檢查或更新流程不能照 about-update 的規格運作；
  - 需要第五個 entitlement 才能運作：停下來問，不自行加。
- **預算**：`pilotfish:security-executor` 實作 1 次＋修正 1 次。
  - 實作從修訂一的分支 `feat/app-sandbox`（`dee0c771`）讀改法，在現在的 main 上重寫，不合併那個分支。
- **限制**：
  - agent 不安裝、不啟動 App、不呼叫 TIS 或 lsregister、不在 .app 裡執行任何東西、不碰 `~/Library` 與鑰匙圈；
  - 本機不執行正式 ID 的沙盒組建，`test-sandbox-migration.sh` 只在 CI 跑；
  - **在這片 PR 的 CI 跑過 §2.6 第 3 步、證明「沒有清單不搬」之前，維護者的機器上不執行任何 ID 的沙盒組建**（`make selftest-bundled`、`check-app.sh` 第 6 項、開發版的 `install-ime.sh`），main 與使用者都一樣，不只 agent。
    - 這段期間 `docs/verification.md` 的本機關卡改用 CI；`check-app.sh` 在本機可以加 `SKIP_RUN=1`，只做第 2、3 項；
  - 不推 tag。dry run 由 main 觸發，事先問使用者。
- **回滾**：
  - 合併之後、發佈之前（§4.2 或 §4.3 碰到停止條件）：先在 main 上 revert 這一片（任何 tag 之前），再處理資料：裝回 0.4.x，照 `docs/sandbox-restore.md` 搬回，或用 §4.3 B 第 0 步的備份。
  - §4.3 A 第 1 步 (f) 不通過（開發版 ID 沒有清單，卻把真正的學習資料搬進它的 container）：
    1. 先在系統設定停用開發版、結束它的程序；
    2. 用 §4.3 B 第 0 步（§4.3 A 開始前做的那次）的備份，還原 `~/Library/Application Support/shanjie` 與偏好設定（`defaults import`）；
    3. 再移除開發版與它的 container。
  - §4.3 A 的其他失敗只動開發版 ID 的東西，移除開發版即可。
  - 發佈之後（F5）：
    - Sparkle 擋降版，所以 revert 要發成版本號更高的新版。
    - 已經更新到沙盒版的使用者，經 Sparkle 收到的是沒有沙盒的新版，它看到的學習資料與偏好設定是空的。只靠使用者照發版說明跑 Terminal 指令不可靠，所以 revert 版**內建一次性的自動複製**：
      - 第一次啟動時，舊位置是空的、而且自己 ID 的 container 存在，就把 container 裡的學習資料複製回原位置；偏好設定用 `defaults` 的匯出與匯入，不直接複製 plist（cfprefsd 的快取可能忽略直接寫入的檔案〔推論〕）。
      - 只複製、不刪 container；在虛擬機量過才發。
    - 之後重新出沙盒版時，container 已經存在、不會再搬；回滾期間學到的資料要另外合併，到時候寫進那一片的契約。
    - `docs/sandbox-restore.md` 的手動指令仍然保留，給用安裝程式或 brew 降版的人。

## 6. 使用者的決定

1. **本機組建用開發版 ID**「善解（開發版）」（§2.3；2026-10-09）。
2. **release dry run 保留產出檔一天**，供虛擬機與實機驗收 Developer ID 版（§2.4；2026-10-09）。
3. **產品車道的順序**：沙盒是第一片（2026-10-18，`docs/iteration-plan.md`）。
4. **保留一鍵更新、說法改縱深防禦**（2026-10-19，選擇題，安全審查 F1 之後）：保留 Installer.xpc；所有對外文字把沙盒寫成縱深防禦；沙盒外、固定更新對象的獨立更新程式（根本解）排到 0c B 片，和 R8 的 XPC 輔助程式一起做（§2.8、§3）。
5. **Sparkle 跟這片一起升到 2.10.0**（2026-10-19，選擇題）：更新流程的實機重測只做一次（§4.3 A）；如果這片延後，改成單獨一小片先升。
6. **先在虛擬機驗證逃脫路徑，成立再私下回報 Sparkle**（2026-10-19，選擇題，§4.5）：只描述機制、不帶私人資訊，送出前給維護者看。
7. **Sparkle.framework 搬到 `Contents/Frameworks/Updater/`**（2026-10-19 晚上，選擇題，修訂三）：另外兩個選項是「不改配置，失敗時提示重新開機」與「沙盒延後到沙盒外的獨立更新程式」（§2.8）。

## 7. 安全審查的處置

### 7.1 修訂一（2026-10-09 審查，2026-10-10 處置）

| 項目 | 嚴重度 | 內容 | 處置 |
|---|---|---|---|
| F1 | P0 | 釋出時 `codesign --force` 重簽會丟掉 entitlements，`verify()` 與 `check-app.sh` 卻要求它是空的，全部綠燈 | FIX：§2.1、§2.4、§2.6 第 8 步 |
| N1 | P0 | 移轉清單的路徑和 ID 無關，任何 ID 的沙盒組建第一次啟動都會搬走真正的資料，包括本機的 `check-app.sh` 與 `make selftest-bundled` | FIX：§2.2（只有正式 ID 放清單）、§2.3（預設開發版 ID、`check-app.sh` 拒絕）、§2.6 第 3 步驗證「沒有清單不搬」。審查建議其他 ID 用二元素路徑；改成不放清單，比較簡單，但要靠 CI 驗證那個推論 |
| F3a | P1 | 沒有移轉清單，升級後選字記憶「消失」；清單必須和 entitlements 在同一版 | FIX：§2.2；同一個 PR |
| F3b | P1 | 蘋果文件沒寫偏好設定會自動搬 | 量過（§1）：會自動搬。CI 斷言（§2.6 第 3 步）；搬不了就停 |
| F10 | P1 | selftest 不建 IMKServer，mach 名稱寫錯 CI 抓不到 | FIX：名稱由 `BUNDLE_ID` 產生，`check-entitlements.sh` 斷言等於 `InputMethodConnectionName`；IMK 實際能不能用由實機驗收（§4.3 B 第 3 項） |
| N2 | P1 | 改了預設 ID，釋出版可能帶錯 ID | FIX：§2.4，`release.yml` 明確傳、`verify()` 依 bundle 斷言 |
| N3 | P1（推測） | ad-hoc 組建先建立的 container，Developer ID 版可能接手不了 | 避免：§2.3、§5 本機不執行正式 ID 的沙盒組建。量：§4.2 第 1 項；接手不了就停 |
| F3c | P2 | 安裝程式在沙盒外寫 `layout` | FIX：§2.7 改在換檔之前寫；§4.2 第 4 項 |
| F4 | P2 | `shanjie install` 和輸入法是同一個執行檔，會在沙盒裡註冊 | 量過（§1）：做得到。實機記結果（§4.3 B 第 1 項）；做不到才加輔助程式 |
| F2 | P2 | `IsSecureEventInputEnabled` 在沙盒裡沒量過；永遠 false 會讓密碼欄繼續學習 | 實機量（§4.3 B 第 4 項）；停止條件 |
| N4 | P2 | 不備份旗標搬完後還在不在 | 量過（§1）：保留。CI 斷言（§2.6 第 3 步） |
| N6 | P2（推測） | 測試讀別的 App 的 container，可能跳 TCC 詢問 | §2.6 第 3 步寫好替代做法；搬回指令在 §4.2 第 3 項量 |
| F8 | P3 | selftest「不碰任何檔案」在沙盒下不再成立 | FIX：§2.3 改說法；container 由 §2.6 第 3、6 步分別檢查 |
| F5 | P3 | cask 的 `zap` 漏了學習資料與 container | FIX：§2.5 |
| F12 | P3 | 只有實機看得到沙盒拒絕 | §4.3 B 第 2、12 項、§4.3 A 第 1 步 (d) |
| N5 | P3 | 退回舊版讓資料滯留 | FIX：§2.5 寫進退回說明與搬回文件 |
| F6、F7 | P3 | 網站沒提 Time Machine；denylist 太短 | DEFER：第二片（§3） |
| F9 | P4 | 安裝程式的簽章檢查 | DEFER：第二片；F1 修好後 `verify()` 已會檢查 |
| F11 | P4 | CI runner 是不是 GitHub-hosted | 確認是（`xcode-27` 是 GitHub-hosted 映像）；實作時看 job log 的 Runner Image 一次 |
| N7 | — | 使用者層詞包的位置 | DEFER：ACG 詞包 C 片 |

### 7.2 修訂二（2026-10-19 審查，同日處置）

`pilotfish:security-reviewer` 只讀審查（讀檔案與 Sparkle 2.9.3 的公開原始碼，沒有執行）；研究顧問另外核對了 F1、F2 的原始碼與版本。

| 項目 | 嚴重度 | 內容 | 處置 |
|---|---|---|---|
| F1 | P1（原始碼推論） | Installer.xpc 沒有把要更新的 App 綁定到呼叫者，被攻破的沙盒輸入法理論上可能借它在沙盒外執行程式，不需要我們的 EdDSA 私鑰；兩個環節沒驗證。逃出去等於今天沒有沙盒，不是退步，但 §2.8 原本「寫不到 container 外」「要裝未簽章的更新仍要私鑰」兩句不能當沙盒保證 | 維護者選擇題（§6 第 4、6 項）：保留一鍵更新，§0、§2.8 與所有對外文字改成縱深防禦；根本解排到 0c B 片；§4.5 在虛擬機驗證，成立就私下回報。repo 只寫到高層描述 |
| F2 | P2 | Sparkle 2.9.3 有已公開的安全公告（2.9.6 修）；改成沙盒裡的程序驅動沙盒外的安裝端之後，安裝端的問題變得相關 | FIX：§2.8 升到 2.10.0（§6 第 5 項） |
| F3 | P2 | 正式版「沙盒版經 Installer.xpc 更新成沙盒版」第一次執行會在 0.5.0 → 0.5.1，壞了所有 0.5.0 使用者都卡住 | FIX：§4.4 canary 0.5.1、發版說明寫明失敗時重裝；§5 停止條件 |
| F4 | P2 | §4.3 A 的 A0 和正式版同時寫真正的學習資料（完整重寫沒有鎖）；第 2 步「第一次建立 container」和順序矛盾；CI 的 macOS 版本沒記；A0 和正式版同名 | FIX：§4.3 A 備份移到最前、A0 → B 先做、期間不打字、前後立刻取雜湊、做完當場移除；§2.6 第 3 步印 `sw_vers`；同名寫明用 ID 區分 |
| F5 | P2 | 發佈後的回滾只靠使用者手動搬回 | FIX：§5 revert 版內建一次性自動複製（只複製不刪、偏好設定用 `defaults` 匯出匯入、虛擬機量過才發） |
| F6 | P3 | 網路選擇的推論確認；Downloader.xpc 更糟（沙盒外、快取路徑由呼叫者組成） | 維持 `network.client`；§2.8 斷言 `SUEnableDownloaderService` 不存在；對外不寫「沙盒讓輸入內容無法送出」 |
| F7 | P3 | 驗證只有「整份相等」才擋得下多出來的鍵；突變缺「多一個鍵」「別的 ID」；沒斷言 XPCServices 只有 Installer.xpc；release 那份沒有反向測試 | FIX：§2.4 寫明整份相等、`sign` 用 workflow 的字面值 `--entitlements`；§2.6 第 8 步加三種突變；§2.8 斷言只有 Installer.xpc；release 那份寫明不在分支上跑、和 `check-entitlements.sh` 同一套比對 |
| F8 | P4 | ad-hoc 不加 runtime 對本機與 CI 沒有實質影響；ad-hoc 的 Installer.xpc 不驗呼叫者 | 接受；§4.3 A 註明 ad-hoc 和正式版的差別，正式版由 canary 量 |
| F9 | P4 | mach-lookup 例外本身沒有額外風險，風險在它後面的服務（F1） | 接受 |
| F10 | P3／P4 | §7.1 沒有被推翻，但沙盒的價值被 F1 削弱；EdDSA 私鑰推的更新可以拿掉 entitlements；§2.9 漏了設定視窗的焦點與 `~/Applications` 的圖示 | §2.8 寫明 EdDSA 那條路沙盒幫不上；§2.9 補兩項、§4.3 B 第 11 項 |

## 8. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| plan-verifier 第 1 次 REVISE（2026-10-10） | 1. CI 的步驟、ID 與順序沒寫，和現有腳本矛盾；2. 移轉測試在 CI 以外會動到真正的資料；3.「沒有清單不搬」寫成量過，其實沒量；4. `verify()` 同時檢查安裝程式，照原寫法每次發佈都會失敗；5. entitlements 的突變測試只能在 main 上做、還要花一次公證；6. 開發版的名稱與安裝位置沒定，`install-ime.sh` 會把正式版蓋掉；7. 倚天排列沿用的寫入時機在使用者接受之後，推論站不住；8. 搬回沒有驗收，實機升級前沒有備份 | 全部 FIX：1 → §2.6；2 → §2.6 第 3、4 步的拒絕條件；3 → §1 改成推論、§2.6 第 3 步驗證、§5 停止條件；4 → §2.4 依 bundle 給預期值；5 → §2.6 第 8 步在分支上做；6 → §2.3 的名稱表與 `install-ime.sh`、`check-app.sh` 的改法；7 → §2.7；8 → §2.5 的搬回文件、§4.2 第 3 項、§4.3 第 0 步 |
| 收尾審查 REVISE（2026-10-10） | 1. §4.1 第 8 項要在維護者的機器上驗證本機防呆，防呆寫錯就會搬走真正的資料；P3：第 4 步家目錄檢查的順序、`SKIP_RUN` 的結束碼、第 7 步快照可能受 cfprefsd 影響 | 使用者決定「修掉再審一次」：1 → §2.6 第 2a 步在 CI 驗證、§4.1 第 8 項改指向它；P3 → 拒絕條件寫明檢查順序、`SKIP_RUN` 結束碼 0、第 3 步結束前確認來源偏好設定檔已搬走 |
| plan-verifier 第 2 次 REVISE（2026-10-10；第二次之後逐項處置） | 1. 虛擬機與實機驗收只能在合併後做，沒寫順序、沒凍結 tag；2. 「沒有清單不搬」證明之前，本機的 selftest、check-app、開發版安裝都會啟動沙盒組建；3. 遷移測試的拒絕條件看 `$HOME`，`CI=true HOME=<暫存>` 可以繞過；4. §4.1 第 5、6 項沒有對應的 CI 步驟；5. 搬回後 container 還在，下次升級不會再搬 | 全部 FIX：1 → §4.4（合併後的順序、凍結 tag）、§5 回滾先 revert；2 → §5 限制擴及維護者，`check-app.sh` 加 `SKIP_RUN`；3 → §2.6 第 3 步用 `dscl` 查帳號家目錄、第 4 步加這個情況；4 → §2.6 第 0 步；5 → §2.5 刪整個 container、§4.2 第 3 項加再升級 |
| plan-verifier（修訂二，第 1 次，2026-10-19） | REVISE：1. dry run 的版本是 0.0.0，安裝程式不降版、「立即檢查」會顯示 0.4.x，§4.2、§4.3 B 照寫做不到；2. §4.1 第 1 項寫四份突變，§2.6 第 8 步是七份；3. §2.6 第 3 步「只搬一次」的第二份來源含偏好設定，和結束前「偏好設定檔已不存在」衝突；4. §4.3 A 第 1 步 (f) 不通過時的回滾寫錯、停止條件的參照指錯；5. 搬回用 `ditto` 複製 plist，和 §5 的 cfprefsd 推論衝突，偏好設定也沒有驗收 | 全部照最小修改：1 → §4.2 開頭寫明 0.0.0、改用 `install-ime.sh`，安裝程式路徑移到發佈後（§4.4），§4.3 B 第 10 項改成「顯示有新版本、不要按」，§4.4 寫明 0.0.0 之後怎麼回到正式版（v0.5.0 發佈後經 Sparkle 更新，等於提早的 canary）；2 → §4.1 逐份列出預期；3 → 第二份只放學習檔；4 → §5 寫出用備份還原的步驟、參照改成 §4.3 A 第 1 步 (f)；5 → §2.5 改用 `defaults export／import`，§4.2 第 3 項加偏好設定的比對 |
| plan-verifier（修訂二，第 2 次，2026-10-19；第二次之後逐項處置） | 前一輪五項都已解決；新的 REVISE：1. §4.3 B 的安裝排在第 6 步，前面的檢查都在沒有沙盒的版本上做，log 也看不到第一次啟動；2. §4.2 第 1–3 項要學習資料，虛擬機卻不能打字；3. §4.3 A 沒寫 A0、A、B 的版本號，B 可能不比 A0 新 | 三項都 FIX：1 → 安裝改成第 1 步、第 2 步裝完立刻看 log、第 12 步再看一次涵蓋全程的 log，§7.1 的交叉參照跟著改；2 → 第 1 項在第一次啟動前放合成的 `learning.tsv`（含 `#` 新詞）與 `layout=eten`、用雜湊比對，第 2 項改成可觀察的「不跳詢問、container 沒重建」、「讀得到」移到 §4.3 A 第 2 步，第 3 項接著第 1 項的資料；3 → A0 與 A 是 `0.0.1`、B 是 `0.0.2`，A0 一定要明確給版本號 |
| plan-verifier 收尾審查（修訂二，2026-10-19） | READY | — |
| plan-verifier（修訂三，2026-10-19 晚上） | READY | — |
| `/code-review` 與 fresh verifier（修訂三的實作，PR #226） | verifier CONFIRMED；審查 8 項：1. 「只留一條 rpath」不對，SwiftPM 另加 `@loader_path`；2. 第 2b 步沒查 C3 的登記；3. 規則太窄（XPC 服務的組合一變可能重演），也沒有失敗時的提示；4. 沒看到更新有效就要合併；5. `verification.md` 與 about-update 驗收 2 沒寫位置檢查；6. 方法論與註解把黑箱推論寫成事實；7. 突變沒測「只能有這一份」；8. `release.yml` 同一個檢查寫兩次 | 1、2、5、6、7、8 照改；3 → 規則改成「XPC 服務的組合一變就換資料夾」，失敗提示記進 §3；4 → 第 2b 步改在合併前、用 PR 的 head |
