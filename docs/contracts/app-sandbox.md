# 契約：App 沙盒第一片（輸入法本體進沙盒、資料移轉）

狀態：**修訂二（2026-10-19）草稿**，送安全審查與 plan-verifier。

- 修訂一在 2026-10-10 收尾審查 READY，實作放在分支 `feat/app-sandbox`（PR #94，草稿）。那時排定在 v0.4.0 之後合併、以 v0.5.0 發佈。
- 之後 main 前進了 1,032 個 commit，其中 v0.4.7 內嵌了 Sparkle 自動更新（`docs/contracts/about-update.md`）。它在輸入法程序裡連網、刪掉了 Sparkle 的 XPC 服務，和修訂一「只有兩個 entitlements」的設計衝突。
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
- **自動更新**：在沙盒裡照常運作，「關於」頁的檢查與「更新到 X」照舊。
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
- **`scripts/install-ime.sh`**（安裝程式裡附的是同一份，`check-installer.sh` 逐位元比對）：從傳入的 bundle 讀 `CFBundleIdentifier` 決定目的地，不寫死開發版的名稱。
  - 正式 ID：目的地 `善解輸入法.app`、舊版 `shanjie.app` 的升級、`.shanjie-previous`，和現在逐行相同。
  - `com.nyanako.inputmethod.shanjie.dev`：
    - 目的地 `善解（開發版）.app`，保留上一版在 `.shanjie-dev-previous`；
    - 沒有舊版名稱的處理；
    - 結束舊程序的比對樣式只比對開發版的路徑。
  - 其他 ID：拒絕安裝，結束碼非 0。
  - `scripts/test-install-ime.sh`（暫存 HOME）加兩個情境：
    - 已有正式版 `善解輸入法.app` 時安裝開發版，正式版原封不動；
    - 正式版照舊裝到 `善解輸入法.app`。
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
  - 再簽 App：加 `--preserve-metadata=entitlements`。
  - 任何一步都不用 `--deep` 簽（驗證可以用 `--deep`）。
  - `sign` 仍然不執行 repo 裡的程式碼。
- **`verify()` 與 `verify_sparkle()` 改成依 bundle 給預期值**。比對方式和 `check-entitlements.sh` 相同，但寫在 workflow 裡，因為 `sign` 不執行 repo 程式碼；兩處的說明互相指向對方。
  - IME：
    - `CFBundleIdentifier` 是正式 ID；
    - entitlements 完全等於 §2.1 的四個鍵；
    - mach 名稱等於由 ID 算出的值。
  - Sparkle 的五個程式碼位置：`Installer.xpc`、`Autoupdate`、`Updater.app`、framework，加上 App 本身的 Team 與 runtime 檢查照舊。
    - 前四個都沒有 entitlements；
    - `Downloader.xpc` 不存在；
    - Info.plist 的 `SUEnableInstallerLauncherService` 是 `true`（原本是 `false`），其他 Sparkle 鍵照舊逐字比對。
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
  - 另外寫進 `docs/sandbox-restore.md`：Sparkle 不會降版，退回只能用安裝程式或 `install-ime.sh` 裝舊版。
- **搬回**：
  - `docs/sandbox-restore.md` 寫一個手動指令：
    1. 先結束輸入法程序；
    2. 把 container 裡的學習資料與偏好設定 `ditto` 複製回原位置，比對雜湊；
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
     1. `CI` 不是 `true`；
     2. `HOME` 不是這個帳號真正的家目錄：用 `dscl` 查 `NFSHomeDirectory`，和 `install-ime.sh` 的做法相同。container 的移轉看的是帳號的家目錄，不是 `$HOME`；
     3. 帳號的家目錄下，下列任一已經存在：`~/Library/Application Support/shanjie`、`~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist`、`~/Library/Containers/com.nyanako.inputmethod.shanjie`、拋棄式 ID 的 container。
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
   - **只搬一次**：再放一份新的來源，再執行一次：不會再移轉，來源原封不動。
   - 結束前確認來源的偏好設定檔 `~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist` 已不存在（被搬走），第 7 步的快照才不會受 cfprefsd 延遲寫入影響。
   - 讀 container 如果在 CI 上跳 App Data 的 TCC 詢問而卡住（N6），改成由同一個沙盒執行檔回報它讀到的內容；改法寫進這份契約再做。
4. 同一個腳本的拒絕測試，兩種情況都要結束碼非 0、暫存目錄裡沒有新檔案：
   - `CI= HOME=<暫存目錄> …`；
   - `CI=true HOME=<暫存目錄> …`，log 裡有家目錄不符的訊息。
5. `scripts/test-install-ime.sh`、`swift test`（不變）。
6. `make selftest-bundled SELFTEST_BUNDLE_ID=com.nyanako.inputmethod.shanjie.selftest-$GITHUB_RUN_ID`，前後各檢查一次 `~/Library/Containers/<該 ID>`：之前不存在、之後存在。
   - 原本傳空值、用正式 ID 的那一步（`ci.yml` 現在的 `SELFTEST_BUNDLE_ID=`）取消；正式 ID 的 selftest 已在第 3 步與第 7 步跑過。
7. `EXPECTED_BUNDLE_ID=com.nyanako.inputmethod.shanjie scripts/check-app.sh build/善解輸入法.app`。
8. **突變測試**：複製 `build/善解輸入法.app` 四份。
   - 第一份用 `codesign --force --sign -` 重簽（不帶 `--preserve-metadata`）：`scripts/check-entitlements.sh` 要失敗。
   - 第二份帶 `--preserve-metadata=entitlements` 重簽：`scripts/check-entitlements.sh` 要通過。
   - 第三份刪掉 `Installer.xpc` 再重簽 framework 與 App：`check-app.sh` 第 3 項要失敗。
   - 第四份把 Info.plist 的 `SUEnableInstallerLauncherService` 改成 `false` 再重簽：`check-app.sh` 第 2 項要失敗。
9. `make installer`、`scripts/check-installer.sh`（不變，用第 1 步的正式組建）。

### 2.7 倚天排列的沿用改在換檔之前寫

- **現況**：
  - `Registration.fullFlow` 在註冊、啟用、而且使用者在系統設定接受之後，才寫 `layout=eten`（`Registration.swift` 約第 169 行）。
  - 安裝程式等使用者接受的那段時間，沙盒版可能已經啟動、偏好設定已經搬走。這時寫在 container 外，靜靜地失效。
- **改法**：
  - 把「只開了倚天模式、而且還沒選過排列」的判斷與寫入抽成一個函式。
  - 安裝程式在**換檔之前**、用開始時讀到的輸入法狀態呼叫它一次。那時裝的是沒有沙盒的舊版，container 還不存在；之後第一次啟動沙盒版時，這個值隨偏好設定一起搬。
  - `fullFlow` 裡原本的呼叫保留：`shanjie install` 在沙盒裡執行，寫的就是 container 裡的網域；已經選過排列時不重寫。
- **container 已經存在時**（從沙盒版再升級）：
  - 安裝程式從外面寫的值不會被讀到。
  - 這時輸入法本來就已經有自己的 `layout`，或使用者已經用過沙盒版，所以不需要沿用。
  - 這一點寫進 `Registration.swift` 的註解，不另做處理。
- **測試**：
  - 安裝程式流程的單元測試斷言這個函式在換檔之前被呼叫；
  - §2.6 第 3 步斷言「在第一次啟動之前、用 `defaults` 寫的 `layout`」會被搬進 container。

### 2.8 Sparkle 在沙盒裡（修訂二新增）

- **Info.plist**：`SUEnableInstallerLauncherService` 由 `false` 改成 `true`，所有組建都一樣。本機組建不啟動更新服務（`ShanjieUpdateBuild=local`），這個鍵只在 release 與更新測試版有作用。其他 Sparkle 鍵不變。
- **XPC 服務**：
  - `build-app.sh` 只刪 `Downloader.xpc`，保留 `Sparkle.framework/Versions/B/XPCServices/Installer.xpc`。
  - 斷言 `Installer.xpc` 存在、`Downloader.xpc` 不存在。
  - `check-app.sh` 第 3 項、`release.yml` 的 `sign` 與 `verify_sparkle` 照這個改：原本「XPCServices 不存在就對」改成這兩條。
- **ad-hoc 簽章順序**（由內而外，不用 `--deep`）：`Installer.xpc` → `Autoupdate` → `Updater.app` → framework → App（只有 App 帶 entitlements）。
- **網路：給輸入法 `network.client`，不用 Downloader.xpc**（main 的選擇，請安全審查挑戰）：
  - **Downloader.xpc 的好處有限**：它讓輸入法程序本身沒有連網權限。但 Sparkle 2.9.3 的 Downloader.xpc 不在沙盒裡、沒有 entitlements（§1），而且接受 App 傳來的請求。輸入法程序如果被攻破，仍然可以透過它送出任意請求。所以在「被攻破的輸入法能不能把資料送出去」這件事上，兩種做法沒有差別〔推論，來自 Sparkle 的架構與 §1 的量測；安全審查確認或推翻〕。
  - **network.client 的好處**：這是 Sparkle 的標準做法，少一個在沙盒外、可被 App 驅動的程序，也沒有 Downloader 的已知限制（更新說明的網頁檢視退回舊元件）。
  - **沙盒實際擋下的**：被攻破的輸入法讀不到使用者的文件、SSH 金鑰、其他 App 的資料，也寫不到 container 以外的地方。網路本來就擋不住，現在沒有沙盒時同樣擋不住。
  - **對外說明**：網站隱私段的內容（只有 Sparkle 連到 GitHub）不變。連線行為沒有改，這片不改網站。
- **安裝服務**：`Installer.xpc` 啟動 Sparkle 的 `Autoupdate`。它在沙盒外，由它換掉 `~/Library/Input Methods` 裡的 bundle。
  - EdDSA 驗證仍在 Sparkle 的安裝端做（R10 的信任模型不變）。
  - 被攻破的輸入法要裝進未簽章的更新，仍然要先有 EdDSA 私鑰〔推論，來自 Sparkle 2 的設計文件；安全審查確認〕。
- **更新之後的 container**：新版同 ID、同 Team（正式版）。container 照簽章的指定需求比對，由新版接手（§4.2 第 1 項、§4.3 B 量）。
- **從沒有沙盒的 0.4.x 經 Sparkle 更新到沙盒版**：舊版的 Sparkle（沒有安裝服務）換掉 bundle，新版第一次啟動時才移轉。這和用安裝程式換檔的差別只在誰換檔；移轉看的是新版第一次啟動（§4.3 A 第 2 項量開發版 ID 的同一條路）。

### 2.9 輸入法碰到的其他系統資源（修訂二新增）

照 §1 的盤點，沙盒可能影響的只有下面幾處，都列進驗收或停止條件：

- **系統標點表**：從 `/System/Library/PrivateFrameworks` 讀。讀不到就停（§5，修訂一已有）。
- **設定頁「App 設定」**：用 `NSWorkspace` 取得 App 的位置、名稱與圖示。實機看清單顯示得出 App 名稱與圖示（§4.3 B 第 8 項）。顯示不出來不停，記下來另開一片。
- **前景 App 的 bundle ID**（聊天／書面的自動判斷）：`NSWorkspace.frontmostApplication`。實機在聊天 App 與文字編輯器之間切換，設定照舊切換（§4.3 B 第 9 項）。
- **網站連結**：SwiftUI `Link` 交給系統開啟，不需要額外權限。

## 3. 不在這一片（第二片或之後）

- 網站隱私說明補 Time Machine（F6）；
- 學習的 denylist 擴充（F7）；
- 安裝程式的簽章檢查（F9）；
- 使用者層的詞包位置（N7，ACG 詞包 C 片要先決定）；
- 註冊用的輔助程式：§1 量到不需要，除非 §4 的實機推翻；
- **下載的模型與 R8 的 XPC 輔助程式**：
  - 0c 實驗開關的 B 片要下載第三方權重，PLAN R8 要求它在沒有網路的沙盒 XPC 輔助程式裡解析。
  - 這片只讓輸入法本體進沙盒，不做輔助程式。
  - B 片要在沙盒版上設計下載位置（container 裡）與輔助程式。
- 詞庫與模型的更新管道（#121）。

## 4. 驗收

### 4.1 CI（§2.6 的步驟，PR 的 CI 紀錄裡看得到順序與 ID）

1. entitlements 與 Sparkle：
   - 第 7 步的 `check-app.sh` 第 2、3 項通過：四個鍵、`Installer.xpc` 在、`Downloader.xpc` 不在、Sparkle 位置沒有 entitlements、`SUEnableInstallerLauncherService` 是 true；
   - 第 8 步的四份突變照預期失敗或通過。
2. 沙盒有套上：第 3 步拋棄式 ID 的 container、第 6 步 selftest ID 的 container，都是之前不存在、之後存在。
3. 移轉與「沒有清單不搬」：第 3 步的所有斷言。
4. 拒絕：第 4 步。
5. 名稱與移轉清單（§2.6 第 0、1 步）：非正式 ID 的組建沒有 `container-migration.plist`、名稱照 §2.3 的表；正式 ID 的組建有。
6. `make bundle` 不帶參數時的 `CFBundleIdentifier` 是開發版 ID（§2.6 第 0 步）。
7. `test-install-ime.sh` 的兩個新情境（§2.3）；`check-installer.sh` 照樣通過。
8. 本機防呆：§2.6 第 2a 步（在 CI 裡驗證，不在維護者的機器上跑任何正式 ID 組建）。
9. §2.7 的單元測試。

### 4.2 虛擬機（main 執行，用 dry run 的 Developer ID 版）

1. **升級**：在全新帳號裝 0.4.x（現行 Latest），學一個詞，再用安裝程式升級成沙盒版。斷言：
   - 學習資料與偏好設定搬好；
   - selftest 正常；
   - `log` 沒有善解或 Sparkle 的沙盒拒絕。
2. **同 ID 重建**：同一個開發版 ID 重新 ad-hoc 組建兩次，第二次仍能讀到第一次 container 裡的學習資料，也不跳詢問。
3. **退回、搬回、再升級**：升級之後降回 0.4.x，照 `docs/sandbox-restore.md` 的指令搬回。斷言：
   - 指令結束碼 0、沒有跳詢問；
   - `learning.tsv` 和升級前的那份逐位元相同、權限 0600；
   - container 已不存在。
   - 接著再升級成沙盒版一次：container 裡的 `learning.tsv` 和搬回的那份逐位元相同，沒有跳詢問。
   - 虛擬機裡沒辦法用介面打字，所以用檔案比對，不用「選字排第一」。
4. **倚天排列**：從只開倚天模式的雙模式舊版，用安裝程式升級。斷言 container 裡的偏好設定 `layout=eten`（`~/Library/Containers/com.nyanako.inputmethod.shanjie/Data/Library/Preferences/`）。
   - 舊版要在系統設定手動加入。虛擬機的介面操作做不到時，這一項改由 §2.7 的單元測試與 §2.6 第 3 步代替，並在 PR 寫明。
5. **Sparkle 讀得到 feed**：沙盒版用 `launchctl asuser` 啟動後，開關於頁的「立即檢查」需要介面操作。做不到時，這一項改在 §4.3 A 量。

### 4.3 實機（維護者與 main；每次 ≤ 15 秒的部分由 `imeshot` 做）

**A. Sparkle 在沙盒裡（合併後、dry run 之前；開發版 ID，不碰正式版的資料）**

- **準備**（main 執行）：用測試金鑰、本機 feed（`http://127.0.0.1`，about-update §3 第 4 項的做法）建三個更新測試版，都是開發版 ID：
  - A0：合併前的 main，沒有沙盒。合併前的 `install-ime.sh` 只會裝到正式版的資料夾，所以 A0 由 main 手動放到 `~/Library/Input Methods/善解（開發版）.app`，不經 `install-ime.sh`；
  - A：這片，有沙盒；
  - B：這片，有沙盒、版本較新。
- 開發版 ID 沒有移轉清單，所以這一段不搬任何資料。正式版的學習資料原封不動。
- **步驟**：
  1. 裝 A，在系統設定加入「善解（開發版）」一次。設定→關於→立即檢查→「更新到 B」→ Sparkle 下載、安裝、重新啟動。量：
     - (a) 執行中的是 B（關於頁版本、`ps` 的路徑），活動監視器的「沙盒」是「是」；
     - (b) 在 TextEdit 照常打得出字；
     - (c) Caps Lock 切換正常；
     - (d) `log show --last 5m --predicate 'sender == "Sandbox" AND (eventMessage CONTAINS "shanjie" OR eventMessage CONTAINS "sparkle")'` 沒有 deny；
     - (e) A 學的一個詞在 B 仍排第一（同一個 container）。
  2. 裝 A0（沒有沙盒），同樣更新到 B。量：
     - (a)～(d) 同上；
     - B 的 container 是第一次建立；
     - A0 寫在 `~/Library/Application Support/shanjie` 的學習資料原封不動。開發版 ID 沒有清單，這也是「沒有清單不搬」在實機上的第二次量測。
     - 注意：A0 沒有沙盒，用的就是真正的學習資料夾，所以這一步只看、不改那個資料夾。
- **不通過**（更新失敗、需要登出、B 沒有接手）→ 停（§5），回報維護者。

**B. 正式 ID 的沙盒版（dry run 的 Developer ID 版，正式發佈之前）**

0. **先備份**（使用者自己做，main 給指令）：第一次啟動沙盒版之前，把 `~/Library/Application Support/shanjie` 與 `~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist` 複製到備份位置。
1. 打一句、改選、按 Enter。斷言：
   - 升級前學過的選字仍排第一；
   - 學過的新詞仍出現在候選裡；
   - 倚天排列、選單與設定視窗的設定都還在。
2. Terminal 開 Secure Keyboard Entry，到別的 App 開輸入法選單：第一項是「學習已暫停（安全輸入）」。
3. 清除學習資料的視窗出得來，關掉後焦點回到原本的 App。
4. 更新之後 Caps Lock 切換正常。
5. 活動監視器的「沙盒」欄位是「是」。
6. 安裝時記下 `shanjie install` 或安裝程式的結果。
7. `log show --last 5m --predicate 'sender == "Sandbox" AND eventMessage CONTAINS "shanjie"'` 沒有任何 deny；log 裡沒有 "system table unavailable"（系統標點表讀得到）。
8. 設定→App 設定的清單顯示得出 App 名稱與圖示（§2.9）。
9. 在聊天 App 與文字編輯器之間切換，聊天／書面的自動判斷照舊（§2.9）。
10. 設定→關於→立即檢查：顯示「已是最新」，不是「檢查失敗」。這證明沙盒版讀得到正式 feed、驗得過簽章。

### 4.4 順序與 tag

- dry run 只能從 main 觸發（`release.yml` 的 `gate` 與 `sign` 只接受 `refs/heads/main` 的 `workflow_dispatch`）。所以順序是：
  1. §4.1 的 CI 通過；
  2. 合併；
  3. §4.3 A；
  4. dry run（main 觸發，事先問維護者）；
  5. §4.2；
  6. §4.3 B。
- 從合併到 §4.3 B 全部通過之前，main 不推任何 tag。這段期間其他片照常合併，但都等這片的實機通過後才一起發版。
- 實機都通過之後，才推正式的 tag（照例要使用者當下同意）。這片以下一個次版本（v0.5.0）發佈。
- 第一次真正經 Sparkle 從 0.4.x 更新到沙盒版，要在 v0.5.0 發佈之後實機看（同 about-update §3 第 6 項的做法）：
  - 已安裝的 0.4.x 按「更新到 0.5.0」；
  - 斷言更新後是沙盒版、學習資料已搬進 container。

## 5. 停止條件、預算、回滾

- **停止條件**：
  - 沒有移轉清單時資料被搬走（§2.6 第 3 步、§4.3 A 第 2 項）；
  - IMK 在沙盒裡連不上；
  - `IsSecureEventInputEnabled` 在沙盒裡永遠是 false，學習會在密碼欄繼續；
  - 移轉測試掉資料，或偏好設定沒有搬；
  - Developer ID 版接手不了 container；
  - 搬回的指令跳詢問或資料不同；
  - Caps Lock 壞掉；
  - 系統標點表在沙盒裡讀不到；
  - **Sparkle 在沙盒裡讀不到 feed、安裝服務被拒，或更新後新版接手不了 container**（§4.3 A、B 第 10 項）；
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
  - 合併之後、發佈之前（§4.2 或 §4.3 碰到停止條件）：先在 main 上 revert 這一片（任何 tag 之前），再處理資料：裝回 0.4.x，照 `docs/sandbox-restore.md` 搬回，或用 §4.3 B 第 0 步的備份。§4.3 A 只動開發版 ID，不需要處理正式版的資料。
  - 發佈之後：
    - revert 這一片並發新版，再照同一份文件搬回；
    - 已經更新到沙盒版的使用者，經 Sparkle 收到的是沒有沙盒的新版。它看不到 container 裡的資料，所以發版說明要附搬回指令。

## 6. 使用者的決定

1. **本機組建用開發版 ID**「善解（開發版）」（§2.3；2026-10-09）。
2. **release dry run 保留產出檔一天**，供虛擬機與實機驗收 Developer ID 版（§2.4；2026-10-09）。
3. **產品車道的順序**：沙盒是第一片（2026-10-18，`docs/iteration-plan.md`）。

## 7. 安全審查的處置

### 7.1 修訂一（2026-10-09 審查，2026-10-10 處置）

| 項目 | 嚴重度 | 內容 | 處置 |
|---|---|---|---|
| F1 | P0 | 釋出時 `codesign --force` 重簽會丟掉 entitlements，`verify()` 與 `check-app.sh` 卻要求它是空的，全部綠燈 | FIX：§2.1、§2.4、§2.6 第 8 步 |
| N1 | P0 | 移轉清單的路徑和 ID 無關，任何 ID 的沙盒組建第一次啟動都會搬走真正的資料，包括本機的 `check-app.sh` 與 `make selftest-bundled` | FIX：§2.2（只有正式 ID 放清單）、§2.3（預設開發版 ID、`check-app.sh` 拒絕）、§2.6 第 3 步驗證「沒有清單不搬」。審查建議其他 ID 用二元素路徑；改成不放清單，比較簡單，但要靠 CI 驗證那個推論 |
| F3a | P1 | 沒有移轉清單，升級後選字記憶「消失」；清單必須和 entitlements 在同一版 | FIX：§2.2；同一個 PR |
| F3b | P1 | 蘋果文件沒寫偏好設定會自動搬 | 量過（§1）：會自動搬。CI 斷言（§2.6 第 3 步）；搬不了就停 |
| F10 | P1 | selftest 不建 IMKServer，mach 名稱寫錯 CI 抓不到 | FIX：名稱由 `BUNDLE_ID` 產生，`check-entitlements.sh` 斷言等於 `InputMethodConnectionName`；IMK 實際能不能用由實機驗收（§4.3） |
| N2 | P1 | 改了預設 ID，釋出版可能帶錯 ID | FIX：§2.4，`release.yml` 明確傳、`verify()` 依 bundle 斷言 |
| N3 | P1（推測） | ad-hoc 組建先建立的 container，Developer ID 版可能接手不了 | 避免：§2.3、§5 本機不執行正式 ID 的沙盒組建。量：§4.2 第 1 項；接手不了就停 |
| F3c | P2 | 安裝程式在沙盒外寫 `layout` | FIX：§2.7 改在換檔之前寫；§4.2 第 4 項 |
| F4 | P2 | `shanjie install` 和輸入法是同一個執行檔，會在沙盒裡註冊 | 量過（§1）：做得到。實機記結果（§4.3 B 第 6 項）；做不到才加輔助程式 |
| F2 | P2 | `IsSecureEventInputEnabled` 在沙盒裡沒量過；永遠 false 會讓密碼欄繼續學習 | 實機量（§4.3 B 第 2 項）；停止條件 |
| N4 | P2 | 不備份旗標搬完後還在不在 | 量過（§1）：保留。CI 斷言（§2.6 第 3 步） |
| N6 | P2（推測） | 測試讀別的 App 的 container，可能跳 TCC 詢問 | §2.6 第 3 步寫好替代做法；搬回指令在 §4.2 第 3 項量 |
| F8 | P3 | selftest「不碰任何檔案」在沙盒下不再成立 | FIX：§2.3 改說法；container 由 §2.6 第 3、6 步分別檢查 |
| F5 | P3 | cask 的 `zap` 漏了學習資料與 container | FIX：§2.5 |
| F12 | P3 | 只有實機看得到沙盒拒絕 | §4.3 B 第 7 項、§4.3 A 第 1 項 (d) |
| N5 | P3 | 退回舊版讓資料滯留 | FIX：§2.5 寫進退回說明與搬回文件 |
| F6、F7 | P3 | 網站沒提 Time Machine；denylist 太短 | DEFER：第二片（§3） |
| F9 | P4 | 安裝程式的簽章檢查 | DEFER：第二片；F1 修好後 `verify()` 已會檢查 |
| F11 | P4 | CI runner 是不是 GitHub-hosted | 確認是（`xcode-27` 是 GitHub-hosted 映像）；實作時看 job log 的 Runner Image 一次 |
| N7 | — | 使用者層詞包的位置 | DEFER：ACG 詞包 C 片 |

### 7.2 修訂二（2026-10-19）

待 `pilotfish:security-reviewer` 審查（重點：§2.8 的網路選擇與安裝服務的信任邊界、§2.1 不加 runtime 的 ad-hoc 簽章、§4.3 A 在維護者機器上執行沙盒組建的時機）。

## 8. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| plan-verifier 第 1 次 REVISE（2026-10-10） | 1. CI 的步驟、ID 與順序沒寫，和現有腳本矛盾；2. 移轉測試在 CI 以外會動到真正的資料；3.「沒有清單不搬」寫成量過，其實沒量；4. `verify()` 同時檢查安裝程式，照原寫法每次發佈都會失敗；5. entitlements 的突變測試只能在 main 上做、還要花一次公證；6. 開發版的名稱與安裝位置沒定，`install-ime.sh` 會把正式版蓋掉；7. 倚天排列沿用的寫入時機在使用者接受之後，推論站不住；8. 搬回沒有驗收，實機升級前沒有備份 | 全部 FIX：1 → §2.6；2 → §2.6 第 3、4 步的拒絕條件；3 → §1 改成推論、§2.6 第 3 步驗證、§5 停止條件；4 → §2.4 依 bundle 給預期值；5 → §2.6 第 8 步在分支上做；6 → §2.3 的名稱表與 `install-ime.sh`、`check-app.sh` 的改法；7 → §2.7；8 → §2.5 的搬回文件、§4.2 第 3 項、§4.3 第 0 步 |
| 收尾審查 REVISE（2026-10-10） | 1. §4.1 第 8 項要在維護者的機器上驗證本機防呆，防呆寫錯就會搬走真正的資料；P3：第 4 步家目錄檢查的順序、`SKIP_RUN` 的結束碼、第 7 步快照可能受 cfprefsd 影響 | 使用者決定「修掉再審一次」：1 → §2.6 第 2a 步在 CI 驗證、§4.1 第 8 項改指向它；P3 → 拒絕條件寫明檢查順序、`SKIP_RUN` 結束碼 0、第 3 步結束前確認來源偏好設定檔已搬走 |
| plan-verifier 第 2 次 REVISE（2026-10-10；第二次之後逐項處置） | 1. 虛擬機與實機驗收只能在合併後做，沒寫順序、沒凍結 tag；2. 「沒有清單不搬」證明之前，本機的 selftest、check-app、開發版安裝都會啟動沙盒組建；3. 遷移測試的拒絕條件看 `$HOME`，`CI=true HOME=<暫存>` 可以繞過；4. §4.1 第 5、6 項沒有對應的 CI 步驟；5. 搬回後 container 還在，下次升級不會再搬 | 全部 FIX：1 → §4.4（合併後的順序、凍結 tag）、§5 回滾先 revert；2 → §5 限制擴及維護者，`check-app.sh` 加 `SKIP_RUN`；3 → §2.6 第 3 步用 `dscl` 查帳號家目錄、第 4 步加這個情況；4 → §2.6 第 0 步；5 → §2.5 刪整個 container、§4.2 第 3 項加再升級 |
