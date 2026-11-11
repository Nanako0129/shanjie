# 安裝與更新

這份寫的是現在的行為。

## 1. 組成與信任模型

### 1.1 組成

| 元件 | 是什麼 | 在哪裡 |
|---|---|---|
| 輸入法 | `善解輸入法.app`（開發版 `善解（開發版）.app`），App 沙盒，只做更新探測 | `~/Library/Input Methods`，或 `/Library/Input Methods` |
| 善解更新 | 輸入法 bundle 裡不在沙盒的小 App，內嵌 Sparkle，只更新包著自己的那份輸入法 | `<輸入法>.app/Contents/Helpers/善解更新.app` |
| 安裝程式 | `安裝善解輸入法.app`（英文名 Shanjie Installer），內附輸入法的 zip | Release 資產 `shanjie-installer-<版本>.zip` |
| `scripts/install-ime.sh` | 命令列安裝，只裝帳號層 | 安裝程式內也附一份 |
| `shanjie install` | 輸入法執行檔的子命令，註冊並啟用輸入法 | `Contents/MacOS/shanjie` |
| Homebrew cask | 帳號層安裝 | 共用的 tap |
| `appcast.xml` | 更新清單，EdDSA 簽章，放在 main | `SUFeedURL` 指向的位置 |

更新的路徑固定：輸入法每天最多一次靜默探測 feed，查到新版本時發一則系統通知（§3.8）；使用者按「更新到 X」、選單項或點通知才啟動善解更新，由它顯示 Sparkle 的對話框、下載、驗證、換檔、重新啟動。輸入法程序本身不安裝任何東西。

### 1.2 兩個身分

| | 正式 ID | 開發版 ID |
|---|---|---|
| bundle ID | `com.nyanako.inputmethod.shanjie` | `com.nyanako.inputmethod.shanjie.dev`（本機組建的預設） |
| 資料夾與顯示名稱 | `善解輸入法.app`、善解輸入法 / Shanjie | `善解（開發版）.app`、善解（開發版） / Shanjie (Dev) |
| 輸入模式 ID | `<ID>.zhuyin` | 同 |
| 連線名稱 | `<ID>_Connection` | 同 |
| 善解更新的 ID | `<ID>.updater` | 同 |
| 偏好設定網域、container | 依 ID | 依 ID |
| 移轉清單 | 有（§2.1） | 沒有 |
| 可裝進 `/Library` | 是（安裝程式「這台 Mac」） | 否，只裝 `~/Library` |

開發版不碰正式版的任何資料。其他任何 ID 都被 `install-ime.sh` 拒絕；`build-app.sh` 對任何非正式 ID 一律產生開發版名稱。

### 1.3 信任模型

- 接受更新的條件是 EdDSA：開了 `SUVerifyUpdateBeforeExtraction`，Sparkle 在解壓前先驗下載檔的 EdDSA 簽章。Sparkle 的備援（驗壓縮檔本身的 Developer ID 簽章）對 zip 不成立，因為 zip 帶不了程式碼簽章，所以實際上只有 EdDSA 能讓更新被接受。
- EdDSA 通過之後，解壓出的 App 只要有有效的程式碼簽章就行（Sparkle 不比對 Team，ad-hoc 也可以，只禁止拿掉簽章或拿掉 `SUPublicEDKey`）；安裝時拿掉隔離屬性，Gatekeeper 與公證不再看這份更新。
- 所以：持有 EdDSA 私鑰、再加上 main 的 `appcast.xml` 的寫入權，就能在所有使用者看得到按鍵的程序裡執行程式。Developer ID 憑證單獨推不了更新。
- 降低的方法：feed 也要簽章（`SURequireSignedFeed`）；只有使用者按下才安裝（`SUAllowsAutomaticUpdates` false）；善解更新只接受 `application` 類型（§4.5）；私鑰只在 sign 工作的三個步驟出現，簽 feed 只在最後一步、只經 stdin 交給 Sparkle 自己的工具（§7.3）；降版由 Sparkle 以實際 bundle 的 `CFBundleVersion` 擋下。
- 裝在 `/Library` 時，持有私鑰與 feed 寫入權的人另外能讓 Sparkle 的 `Autoupdate` 以 root 換檔（系統 domain）；換完的輸入法仍以使用者身分執行。善解更新不接受套件安裝類型，所以走不到「以 root 跑 `installer`」。
- 沙盒是縱深防禦，不是硬邊界：輸入法有 `network.client`，沙盒擋不住把輸入內容送出去；本規格也不把沙盒當成擋住「已在輸入法程序內執行的程式」的保證。對外說法是「輸入法在 App 沙盒裡執行；更新由另一個不在沙盒裡、只會更新已安裝那一份善解的小程式完成」，不寫「輸入內容無法外流」。
- Sparkle 的版本是發版關卡：系統 domain 讓 Sparkle 以 root 執行，它的安全公告直接相關；`Package.resolved` 釘住版本，有新的安全公告就先升再發。

## 2. 輸入法 bundle 的組建

### 2.1 名稱、ID 與移轉清單

- `scripts/build-app.sh` 的環境變數：
  - `BUNDLE_ID`：預設開發版 ID；只能是 reverse-DNS 格式。
  - `OUT_DIR`：預設 `build`，必須是 repo 內的相對路徑；組裝前刪掉裡面兩個資料夾名稱（`善解輸入法.app`、`善解（開發版）.app`），換 ID 重建不留另一個 ID 的舊 bundle。
  - `SHANJIE_VERSION`：`MAJOR.MINOR.PATCH`；沒給就取最新的 `v*` tag，再沒有就 `0.0.0`。
  - `SHANJIE_RELEASE_BUILD=1`：只有 release workflow 的 build 工作設；要求正式 ID，且不能與更新測試版並用。
  - `SHANJIE_UPDATE_TEST_FEED` 與 `SHANJIE_UPDATE_TEST_KEY`：成對才有效，本機專用（`CI` 有設就拒絕）。feed 只收 `https://` 或 `http://127.0.0.1[:埠]/`（Sparkle 拒絕 `file://`；App Transport Security 不擋連 127.0.0.1 的 http，不需要任何例外）；測試公鑰不得等於正式公鑰。
- 資料夾名稱、`CFBundleDisplayName`、`zh-Hant`／`en` 的 `InfoPlist.strings` 與模式名稱依 §1.2 的表；`CFBundleDisplayName` 必須等於資料夾名稱（不含 `.app`）。
- 只有正式 ID 的組建放 `Contents/Resources/container-migration.plist`，內容恰好是 `Move = [${ApplicationSupport}/shanjie]`。其他 ID 不放：沒有清單時系統不搬資料（CI 逐次斷言這個推論，§10.2），所以開發版與拋棄式組建不會帶走真正的學習資料。偏好設定不寫進清單，系統自己搬（同一個 CI 測試斷言）。新詞學習存在同一個 `learning.tsv`，跟著資料夾搬。
- 輸入法本體是 agent app（`LSUIElement`），`LSMinimumSystemVersion` 26.0，單一輸入模式。

### 2.2 Info.plist 的 Sparkle 與組建類型鍵

正式值寫在 `build-app.sh`，不從環境變數覆寫（更新測試版是另一條本機專用的路）；`check-app.sh` 與 `release.yml` 各有自己的字面副本，三處互相對照：

| 鍵 | 值 |
|---|---|
| `SUFeedURL` | 專案 main 上的 `appcast.xml`（raw.githubusercontent.com） |
| `SUPublicEDKey` | 專案的 EdDSA 公鑰（44 個 base64 字元，結尾一個 `=`） |
| `SURequireSignedFeed` | true |
| `SUVerifyUpdateBeforeExtraction` | true |
| `SUAllowsAutomaticUpdates` | false |
| `SUEnableAutomaticChecks` | false（Sparkle 自己的排程關掉，改由 §3.2 的計時器） |
| `SUEnableInstallerLauncherService` | false（整個 bundle 沒有 XPC 服務） |
| `SUEnableSystemProfiling` | false |
| `SUEnableDownloaderService`、`SUEnableJavaScript` | 不設；`check-app.sh` 與 `verify_sparkle` 斷言不存在 |
| `ShanjieUpdateBuild` | `release`（只有 `SHANJIE_RELEASE_BUILD=1`）、`test`（更新測試版：公鑰不是正式的）、其他一律 `local` |

- 版本比對用 `CFBundleVersion`，與 `CFBundleShortVersionString` 同值。
- Sparkle 以 `exact: "2.10.0"` 釘在 `macos/Package.swift`（2.9.0 起才有簽章的 feed；2.9.2、2.9.5、2.9.6 修過安全問題），用官方預編的 framework（二進位由 Sparkle 自己的 `Package.swift` 內的 checksum 釘住）；`macos/Package.resolved` 進版控，`build-app.sh`、`build-installer.sh` 與 CI 都用 `--force-resolved-versions`。這是 `Package.swift` 唯一的外部相依，只有 `Shanjie` 與 `ShanjieUpdater` 兩個 target 連結它，`ShanjieKit` 與測試不連結。
- `ShanjieUpdateBuild` 為 `release` 或 `test` 才啟動更新服務（§3.1）；本機用 `build-app.sh`、`install-ime.sh` 裝的 `local` 版永遠不讀正式 feed。
- 信任根常數（`SUFeedURL`、`SUPublicEDKey`、團隊 ID）只在上面三處與 `RootStage.requirement`、`Helper.requirementText` 以字面值出現；改一處要同步改其餘。

### 2.3 entitlements

輸入法本體剛好三個鍵，整份 plist 逐項相等，多一個或少一個都失敗：

- `com.apple.security.app-sandbox` = true；
- `com.apple.security.network.client` = true（只為了 Sparkle 探測 feed）；
- `com.apple.security.temporary-exception.mach-register.global-name` = `[<ID>_Connection]`，必須逐字等於 Info.plist 的 `InputMethodConnectionName`，所以由 `BUNDLE_ID` 產生，不寫死。

Sparkle 的程式碼位置、善解更新、安裝程式一律沒有 entitlements。設計取捨：不用 `Downloader.xpc` 而給 `network.client`，因為兩者都擋不住被攻破的輸入法往外送資料，而 Downloader 在沙盒外、快取路徑由呼叫者給的 bundle ID 組成，反而多一條在 container 外寫入與刪除的路。

`scripts/check-entitlements.sh <app>`：用 `codesign -d --entitlements - --xml` 取出，經 `plutil` 正規化後與預期的三個鍵整份比對；連線名稱另與 `InputMethodConnectionName` 比。`release.yml` 的 `verify()` 有同一套比對的字面副本（sign 工作不跑 repo 的程式）。

### 2.4 內容版面與權限

整個 bundle 的規則（`check-app.sh` check 3 與 `release.yml` 的 `verify_sparkle` 同一套斷言，ad-hoc 版不查 Team 與 hardened runtime）：

- 沒有任何 `.xpc`（系統照路徑記住 framework 的 XPC 組合，沒有 XPC 服務就沒有這個問題；`Contents/Frameworks/Updater` 這個資料夾名稱只是沿用）。
- `Contents/Helpers` 裡剛好一個項目：`善解更新.app`。
- 輸入法的 `Contents/Frameworks/Updater/Sparkle.framework`：刪掉 `XPCServices`、`Autoupdate`、`Updater.app`（輸入法只做探測，用不到；少兩個能在沙盒外被送出的程式）。
- 善解更新自己的 `Contents/Frameworks/Sparkle.framework`：只刪 `XPCServices`，`Versions/B/Autoupdate` 與 `Versions/B/Updater.app` 都在。
- 輸入法、兩份 framework、善解更新、善解更新的 `Autoupdate` 與 `Updater.app` 都沒有 entitlements（輸入法本體除外，帶 §2.3 的三個）。對函式庫給 entitlements，`codesign` 不會寫進簽章，所以 framework 的「無 entitlements」斷言以 `Autoupdate` 代表做突變。
- 善解更新的 Info.plist：沒有 `SUFeedURL`、`SUPublicEDKey`、任何 `SUEnable*` 鍵、`LSUIElement`；`CFBundleLocalizations` 含 `zh-Hant`，`CFBundleAllowMixedLocalizations` 為 true（沒有這兩個，Sparkle 的對話框照開發語言顯示英文）。
- 權限（root 擁有的 `/Library` 副本必須所有人讀得到、載得起來，又沒有任何人能改）：每個非符號連結的檔案 `o+r`；目錄與有 `u+x` 的檔案 `o+x`；沒有群組或其他人可寫（符號連結除外，macOS 不看連結自己的模式）；沒有 setuid／setgid。`build-app.sh` 在簽章前 `chmod -R a+rX,go-w`；release 的 sign 步驟解開建置產物用 `tar -xpf`（`umask 077` 下 `tar -xf` 會把 bundle 變成 0700／0600），公證蓋章之後、打包 zip 之前再 `chmod -R a+rX,go-w`（`stapler` 與 `codesign` 在 `umask 077` 下會把檔案寫成 0600）。
- Sparkle 的 MIT 授權與它附帶的第三方授權放在 `Resources/LICENSES/Sparkle-MIT.txt`，`check-app.sh` 斷言與 repo 的 `LICENSES/Sparkle-MIT.txt` 相同且含 `EXTERNAL LICENSES`。

### 2.5 簽章（本機 ad-hoc）

由內而外，不用 `--deep`：善解更新那份的 `Autoupdate` → `Updater.app` → framework → 善解更新 → 輸入法那份 framework → 輸入法（只有它帶 `--entitlements`）。ad-hoc 不加 hardened runtime：加了之後 library validation 拒絕載入 ad-hoc 的 `Sparkle.framework`（dyld 報 Team ID 不同；`--selftest` 量到）。沙盒不需要 runtime；出貨版的 runtime 來自 Developer ID 簽章（§7.2）。刪掉 framework 內的部分會破壞各自的封印，所以組建後一定重簽。

### 2.6 本機組建的限制與 make 目標

- 本機組建預設是開發版 ID。正式 ID 的 ad-hoc 組建第一次執行會建立正式 ID 的 container，並把使用者真正的學習資料與偏好設定搬進去，所以**不在本機執行**：`check-app.sh` 在 `CI` 不是 `true` 時對正式 ID 的組建拒絕 check 6 並以非 0 結束；`SKIP_RUN=1` 只做 check 2 與 3，不執行 bundle，結束碼 0。
- `make bundle`：建置，不執行；`BUNDLE_ID`、`OUT_DIR` 由環境傳給腳本。
- `make installer`：只包正式 ID 的組建（`build/善解輸入法.app` 不在、ID 不對、或過不了 `check-entitlements.sh` 都報錯）；用 `ditto -c -k --keepParent` 壓成 `build/shanjie-<版本>.zip`，再呼叫 `build-installer.sh`。不重建輸入法，所以 `check-app.sh` 驗過的就是被包的那份。
- `make selftest-bundled`：用拋棄式 ID（`SELFTEST_BUNDLE_ID`，預設 `com.nyanako.inputmethod.shanjie.selftest`，沒有移轉清單）建出沙盒組建並執行 `--selftest`；selftest 本身不寫任何檔案或偏好設定，但第一次在沙盒裡執行會建立該 ID 的 container。正式 ID 的 selftest 只在 CI 跑。
- `make clean-bundle`：對 `build`、`build/selftest`、`build/default`、`build/nomanifest` 下的各名稱 bundle 與 `build/安裝善解輸入法.app` 先 `lsregister -u` 再刪，並刪 `build/shanjie-*.zip` 與各 `.entitlements-*.plist`。
- 更新測試版（`SHANJIE_UPDATE_TEST_FEED`）沒給 `BUNDLE_ID` 時同樣是開發版 ID。

## 3. 輸入法裡的更新檢查

程式：`macos/Sources/Shanjie/Updater.swift`（接 Sparkle）、`macos/Sources/ShanjieKit/Updates.swift`（所有判斷，不依賴 Sparkle，單元測試走這裡）。

### 3.1 啟動條件

- `Updates.runsUpdater`：bundle 的副檔名是 `app`，而且 `ShanjieUpdateBuild` 是 `release` 或 `test`，**並且** Info.plist 的 `SUFeedURL` 非空。測試、`swift test`、裸執行檔與 `local` 組建都不啟動。
- 關於頁需要的狀態（`Updates`）永遠存在，不論 Sparkle 有沒有啟動。
- `SPUStandardUpdaterController(startingUpdater: false)`，再由 `Updater` 自己呼叫 `start()` 並接住錯誤：controller 自己啟動失敗時會跳 `runModal` 的警告，會卡住輸入法。起不來時記一行靜態 log，之後每次檢查都讀成「檢查失敗」。
- 計時器：啟動後 60 秒第一次 tick（避開引擎啟動），之後每小時一次；兩個值沒有量過，一小時只限制「到期的檢查最晚晚多久」，每日規則在 `shouldCheck`。
- 日誌只記靜態字串，不內插版本、網址或錯誤；Sparkle 自己的 log 另算（寫到磁碟，內容是路徑、網址、錯誤，沒有輸入內容）。更新管道不套用「ephemeral 設定」，Sparkle 的下載用預設 session。

### 3.2 檢查規則

- Sparkle 自己的排程關閉。背景檢查由 `Updates.tick` 呼叫 `checkForUpdateInformation`（只查資訊、不顯示任何視窗），因為輸入法程序在使用者打字時跳視窗會搶焦點。
- `Updates.shouldCheck(enabled:lastCheck:now:)`：開關關閉 → 不檢查；沒有上次檢查 → 檢查；距上次 ≥ 24 小時 → 檢查；上次檢查在未來（時鐘被往回調）→ 檢查；其餘不檢查。起始時間只在檢查真的開始時記錄，不論結果都算入每日限制。
- 「立即檢查」（`checkNow`）不受開關影響、一定連線；檢查進行中被忽略；更新服務沒在跑時記下日期並顯示「檢查失敗」。
- 探測前：若 Sparkle 有進行中的 session（`sessionInProgress`）回 `busy`（不啟動新檢查，因為 Sparkle 會忽略呼叫也不回報）；沒啟動回 `unavailable`；否則先清掉網域裡的跳過鍵與排程鍵（§3.7），再 `checkForUpdateInformation()`。
- delegate 的 `updater(_:mayPerform:)` 只放行 information 類檢查；使用者觸發的與背景的都丟錯誤（互動更新是善解更新的事）。
- 結果：一輪結束（`didFinishUpdateCycleFor`）時，錯誤不是 `SUNoUpdateError` → 檢查失敗（失敗不清掉已知的新版本）；是 `SUNoUpdateError` → 已是最新（清掉已知版本）；沒有錯誤且 `didFindValidUpdate` 報過版本 → 有新版本 X；沒有錯誤但沒帶版本 → 只結束檢查，已知的版本不變。

### 3.3 結果與儲存

- 鍵（輸入法自己的 UserDefaults 網域，也就是 container 內）：`updateAutoCheck`（Bool，沒有等於開）、`updateLastCheck`（Date）、`updateStatus`（字串字典：`by` 檢查時的版本、`result` 為 `upToDate`／`available`／`failed`、`available` 為較新的版本）、`updateNotify`（Bool，沒有等於開，§3.8）。
- 啟動時，`updateStatus` 的 `by` 不等於目前版本就丟掉（別的版本記下的結果不代表這個版本）。
- 版本顯示：`CFBundleShortVersionString`；`0.0.0` 或沒有值顯示「開發版」。
- 固定字串：「已是最新」、「有新版本 X」、「檢查失敗」、「檢查中…」、「更新到 X」、選單項「有新版本 X，按此更新…」；通知的標題「善解有新版本 X」與內文「按這裡更新；也可以從輸入選單更新。」。

### 3.4 關於頁

設定視窗左欄最後一頁「關於」，內容：

- 「善解輸入法」區：版本。
- 「模型」區：讀 `Resources/models.tsv` 的名稱與代號；檔案缺或讀不出來就沒有這一區。
- 「更新」區：
  - 「自動檢查更新」開關（預設開），旁邊的說明：「開著時每天最多連線一次，到 GitHub 讀取有沒有新版本；有新版本時，關於頁與輸入選單會顯示，按下才更新。」
  - 「有新版本時通知我」開關（預設開，自動檢查關著時反灰），說明：「每天提醒一次，直到更新。第一次會由系統詢問是否允許通知。」系統的通知對善解是拒絕或關著時（兩個開關都開著的前提下），多一行「系統設定裡關閉了善解的通知」與「打開通知設定」按鈕（§3.8）。
  - 依安裝位置的說明：只看 `Bundle.main.bundleURL` 的上層資料夾是不是 `/Library/Input Methods`。
    - 是：「善解裝給這台 Mac 的所有帳號。按更新後，善解更新會先下載並驗證，再跳出系統的密碼框，請你輸入密碼來允許更新；框裡有一行說明固定是英文：『善解更新 wants permission to update 善解輸入法.』。善解只會在你按下這裡的『更新』之後才要密碼。不是管理員的帳號，請管理員更新。」密碼框裡系統自己的字會翻成中文，這一行是 Sparkle 寫死的英文、不翻譯。
    - 否：「善解只裝在你的帳號，更新不需要密碼。有程式開著安全輸入時，系統會暫時停用善解；要改裝給整台 Mac，請重新下載安裝程式並選『這台 Mac』。」
  - 「上次檢查」：日期時間・結果；檢查中顯示「檢查中…」；從未檢查顯示「—」。
  - 「立即檢查」按鈕；有已知新版本時再多一顆「更新到 X」；檢查中兩顆都反灰。
- 網站連結與一行授權說明：程式 Apache-2.0、內含的 Sparkle 是 MIT、詞庫與模型的授權在 `Resources/LICENSES`。設定視窗目前只有中文，所以 App 裡不顯示英文版。

### 3.5 輸入選單

有已知新版本時多一項「有新版本 X，按此更新…」（動作 `installUpdate`，等同按「更新到 X」）；沒有新版本時不出現；檢查進行中反灰。

### 3.6 「更新到 X」

`Updates.install`（關於頁按鈕與選單項共用）：

1. 檢查中就記下這次按下，不另開檢查：正在跑的那次檢查結果是有新版本，才進第 4 步；結果是已是最新、失敗，或檢查結束卻沒有帶回結果（`finished(nil)`），記下的這次按下就作廢，不開任何視窗。選單項與按鈕在檢查中是反灰的，所以這一條主要發生在點通知時（§3.8）；選單開著時剛好開始檢查，按選單項也會走到這裡。
2. 先跑一次靜默探測。探測回 `started` 時記下「檢查完要裝」；回 `busy`（Sparkle 有 session）時直接進第 4 步；回 `unavailable` 時記日期並顯示「檢查失敗」。
3. 檢查結果是有新版本才進第 4 步；已是最新或失敗只更新關於頁與選單的狀態，不出現任何 Sparkle 視窗。
4. 先 `closeSettings()` 關掉設定視窗，再 `NSWorkspace.openApplication(at: Contents/Helpers/善解更新.app)`，`activates = true`，不帶參數；啟動失敗記一行靜態 log。善解更新已在執行時，再按只是把它叫到前面（LaunchServices）。

關閉設定視窗的原因：設定視窗是 `.floating`（為了讓 agent App 的視窗看得到），會蓋住一般 App 的視窗，也就蓋住善解更新的對話框；`.floating` 不改。關閉後關於頁沒有東西要顯示，所以沒有「已開啟善解更新」的狀態列。沙盒程序傳給 `NSWorkspace` 的參數與環境變數，系統一律忽略，所以啟動善解更新的人什麼都給不了。

### 3.7 Sparkle 設定的釘住

Sparkle 對不是主 bundle 的 host，用 host 的 bundle ID 開一個 user defaults，先讀它再讀 Info.plist，所以網域裡的值能蓋掉 Info.plist。處理（輸入法與善解更新都做，純函式在 `UpdateHost`）：

- feed 網址：delegate 的 `feedURLString(for:)` 回傳 Info.plist 的 `SUFeedURL`（`infoDictionary`，不經 user defaults）；讀不到就不啟動。否則寫得進網域的程式能讓更新去讀一份重播的舊 appcast（有簽章、但沒有比現在新的版本），使用者永遠看不到新版。
- 跳過版本：Sparkle 把「跳過這個版本」存在 host 網域（鍵 `SUSkippedVersion`、`SUSkippedMajorVersion`、`SUSkippedMajorSubreleaseVersion`），探測會照它濾掉版本；善解更新寫的是 `~/Library/Preferences/<ID>.plist`、輸入法讀的是 container，按了跳過之後兩邊不一致，而且寫一個很大的跳過版本就能讓探測永遠看不到新版。所以輸入法每次探測前、善解更新啟動時，都刪掉這三個鍵。善解只在使用者按下「更新」才裝，「跳過」本來就沒有用處。
- 排程鍵：`SUEnableAutomaticChecks`、`SUScheduledCheckInterval`、`SUAutomaticallyUpdate` 也先讀網域，所以同一時機一併刪掉（網域裡有值會啟動 Info.plist 關掉的背景檢查；在善解更新裡，背景 session 還會讓使用者的檢查不開視窗就返回）。
- 刪除透過 `KeyRemoving` 協定，測試用記憶體實作，不寫真實偏好。

### 3.8 通知

程式：判斷在 `Updates`（`shouldNotify`、`showsPermissionLine`，單元測試），接系統的是 `macos/Sources/Shanjie/UpdateNotifier.swift`。

- 層級：系統原生的通知（`UNUserNotificationCenter`），由輸入法程序發出；樣式、橫幅、通知中心、勿擾與專注模式都由系統決定。不自己畫提示視窗（打字時跳視窗會搶焦點，§3.2）；Sparkle 的溫和提醒只在善解更新裡，而它平常不在執行。不需要新的 entitlement（§2.3 仍是三個鍵）。
- 何時發：背景檢查（`tick`）查到新版本，而且「有新版本時通知我」與「自動檢查更新」都開著。背景檢查一天最多一次（§3.2），所以同一版每天最多一則，直到更新或關掉開關。「立即檢查」與「更新到 X」查到新版本不發（使用者正看著關於頁）。`Updates` 在開始一次檢查時記下是不是背景檢查，`finished` 讀完就清掉。
- 一個識別碼（`update-available`）：每天的那則取代前一則，通知中心只留一則。檢查結果是已是最新時，把留在通知中心的那則清掉；上次檢查的時間不跟著版本重設，所以更新完之後，舊的那則會留到新版第一次檢查（最多約一天）才清掉，期間點它只會靜默檢查一次、查到已是最新就清掉，不開任何視窗。背景檢查進行中點了通知、那次檢查又查到新版本時，只開善解更新，不再多發一則。
- 授權：每次要發時先讀系統的狀態。還沒決定 → `requestAuthorization`（第一次真的有新版本時才問），允許了才發；已允許 → 發；已拒絕 → 什麼都不做，不再問、不改用別的方式提醒（輸入選單那一項照舊在）。授權詢問在輸入法程序活著時一直掛著等回答；**沒回答、程序就結束（登出、更新）時，系統把它記成拒絕**，之後不再詢問（虛擬機量到）。
- 點了通知：等同選單項「有新版本 X，按此更新…」（`Updates.install`：先靜默確認一次，再開善解更新）。delegate 在 `app.run()` 之前設好：輸入法程序沒在跑時點通知，系統另外啟動輸入法程序，一啟動就送來這次點擊；之後輸入法機制用的是同一個程序，不會再另起一份（虛擬機量到）。
- 關於頁：每次顯示時重讀系統的通知狀態；拒絕或「橫幅與通知中心都關著」時，在兩個開關都開著的前提下顯示「系統設定裡關閉了善解的通知」與「打開通知設定」。按鈕先關設定視窗（理由同 §3.6），再打開系統設定的通知頁（`x-apple.systempreferences:com.apple.Notifications-Settings.extension?id=<bundle ID>`）。
- 通知要裝上帶這個功能的版本才會作用：更舊的版本查到新版本時，仍只在輸入選單與關於頁顯示。

## 4. 善解更新.app

程式：`macos/Sources/ShanjieUpdater/main.swift`（接 Sparkle）、`macos/Sources/UpdateHost/UpdateHost.swift`（判斷，只用 Foundation，單元測試不需要 Sparkle）。

### 4.1 bundle

| 項目 | 值 |
|---|---|
| 位置 | `<輸入法>.app/Contents/Helpers/善解更新.app`，跟輸入法一起簽章、公證、安裝、更新 |
| bundle ID | `<輸入法 ID>.updater` |
| 名稱、圖示 | `善解更新`；`AppIcon.icns`（更新期間 Dock 的圖示） |
| 一般 App | 不設 `LSUIElement`（更新期間 Dock 會短暫出現圖示）；啟用策略 `.regular`；`LSMinimumSystemVersion` 26.0 |
| 沙盒與 entitlements | 都沒有；release 版是 hardened runtime |
| Info.plist 的 Sparkle 鍵 | 沒有（見 §2.4）；Sparkle 的公鑰與簽章要求取自 host 的 Info.plist |
| 參數與輸入 | 不讀命令列參數、環境變數、URL、剪貼簿或任何 IPC；實作一個什麼都不做的 `application(_:open:)`，避免 AppKit 對送來的檔名跳「無法打開」的警告 |
| 日誌 | 靜態字串；失敗時 `exit(1)`，不開視窗 |

### 4.2 host

host 是善解更新往上三層的 bundle（`<host>/Contents/Helpers/善解更新.app`），符合才繼續：

- 先處理 App Translocation：善解更新的路徑含 `/AppTranslocation/` 時（隔離的 bundle 可能從唯讀掛載執行），用 Security 的 `SecTranslocateCreateOriginalPathForURL`（執行時 `dlsym`，SDK 標頭沒宣告）換回原路徑；查不到就沒有 host。
- 往上三層取 `realpath`，必須**完全等於** `<根>/<名稱>`：根是 `/Library/Input Methods` 或 `<家目錄>/Library/Input Methods`；名稱是 `善解輸入法.app` 或 `善解（開發版）.app`。家目錄用 `getpwuid` 的 `pw_dir`（不用 `$HOME`）再取 `realpath`，所以經符號連結、大小寫不同或結尾斜線的家目錄仍然符合；家目錄不存在就照原值去掉結尾斜線。
- 其他位置（container 或桌面的副本、符號連結指到別處、不是三層深）都沒有 host，結束碼 1、不開視窗。比對是整串路徑相等，所以家目錄 `<根>/a` 與 `<根>/ab` 不會混。

### 4.3 組建類型

讀 host 的 `ShanjieUpdateBuild`（`UpdateHost.trust(build:)`）：

- `release`：host 必須通過指定需求（`SecStaticCodeCheckValidity`，`kSecCSCheckAllArchitectures`）：`anchor apple generic and identifier "com.nyanako.inputmethod.shanjie" and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists and certificate leaf[subject.OU] = "<團隊 ID>"`。兩個憑證欄位是 Developer ID 的中繼憑證與 Developer ID Application 的葉憑證；只看 Apple 錨點與 Team 的話，同團隊的 Apple Development 憑證也會過。不過就結束。
- `test`（ad-hoc 的更新測試版）：不驗簽章。
- 其他（含 `local`、缺值）：結束。

### 4.4 流程

1. host 與組建類型通過，host 的 Info.plist 有 `SUFeedURL` 與 bundle ID，才往下；任一缺少就結束。
2. 清掉 host 網域的跳過鍵與排程鍵（§3.7）。
3. `SPUUpdater(hostBundle: host, applicationBundle: host, userDriver: SPUStandardUserDriver(hostBundle: host))`，`start()` 失敗就結束。
4. 先靜默 `checkForUpdateInformation()`。有新版本（沒有錯誤且報過版本）時，**下一個 main-queue turn** 再 `checkForUpdates()`（Sparkle 的標準對話框：說明、下載、驗證、安裝並重新啟動；資訊 session 結束後才開互動 session）；沒有新版本時不出現任何視窗，所以被攻破的輸入法啟動它也叫不出任何畫面。
5. 沒有新版本、使用者取消或更新完成就結束：一個 main-queue turn 之後若沒有進行中的 session 才 `terminate`（安裝進行中讓 App 活著）。背景類檢查的結束不影響流程。
6. 介面只有 Sparkle 的標準對話框，用 host 的名字與圖示，不自己畫視窗。

`updater(_:mayPerform:)` 放行使用者觸發的與資訊類檢查，拒絕背景檢查（`UpdateHost.helperMayPerform`）；輸入法自己只放行資訊類（`inputMethodMayPerform`）。

### 4.5 安裝類型、跳過、feed

- delegate 的 `shouldProceedWithUpdate` 只放行 `installationType == "application"`（`UpdateHost.allowsInstall`），其他（`package`、`interactive-package`）丟錯誤，因為套件安裝類型會以 root 跑 Apple 的 installer。接續先前中斷的安裝時 Sparkle 不呼叫這個 delegate，但 `SUInstaller` 會比對壓縮檔裡實際是 `.app` 還是 `.pkg` 與項目的類型，不符就失敗，而能先起一個安裝的只有不在沙盒的同使用者程式。
- feed 與跳過鍵的處理同 §3.7。

### 4.6 root 換檔與密碼框

- 裝在 `/Library` 時（root 擁有、使用者寫不進），Sparkle 的 `Autoupdate` 走系統 domain：系統跳出管理員密碼框，說明文字是「善解更新 wants permission to update 善解輸入法.」（`<主 bundle 名> wants permission to update <host 名>.`，Sparkle 寫死、不在地化）。換完的 bundle 仍是 `root:wheel`、全部可讀，container 沿用。
- 裝在 `~/Library` 時走使用者 domain，不問密碼；輸入法被結束並重新啟動。
- `Autoupdate` 以 root 執行時：下載檔搬進只有 root 能讀的唯一目錄、隨機檔名、確認是一般檔；檢查已安裝 host 的 bundle ID；新版 `CFBundleVersion` 低於 host 時中止（擋降版）；EdDSA 公鑰取自 host 的 Info.plist。Sparkle 2.10.0 的原始碼裡沒有找到「新 bundle 的 ID 要等於 host」這一條，不當成保證。
- 更新路徑上以 root 執行的程式碼只有 Sparkle 的 `Autoupdate`；善解更新本身以使用者身分執行。

### 4.7 共存與移轉

| 情境 | 行為 |
|---|---|
| 沒有沙盒的舊版（0.4.7）經它自己的 Sparkle 更新到 0.5.0 | 舊版自己的 Sparkle 換檔；新版第一次啟動才搬資料（§9.1） |
| 0.5.0 之後的每次更新 | 善解更新：`~/Library` 使用者 domain、不問密碼；`/Library` 系統 domain、一次管理員密碼 |
| 從 `~/Library` 搬到 `/Library`（或反向） | 只有安裝程式；Sparkle 只原地換檔 |
| 多使用者 Mac（`/Library`） | 任一管理員按更新，換的是所有人共用的那一份；其他已登入帳號的舊程序〔推論〕要到登出或重新載入才換；標準帳號按更新會停在密碼框 |
| Homebrew 裝的那份 | 帳號層；按更新也走善解更新（使用者 domain） |
| 開發版 | `local` 組建的善解更新直接結束；更新測試版（`test`）只在虛擬機用 |

更新壞掉時不回頭用別的更新路徑：向前修一版，使用者下載安裝程式重裝即可（安裝程式與 Sparkle 無關，永遠可用），資料在 container 裡不受影響。

## 5. 安裝程式

程式：`macos/Sources/ShanjieInstaller/main.swift`（AppKit，用程式碼建視窗，不用 nib）、`macos/Sources/ShanjieInstall/`（`InstallerPlan.swift`、`RootStage.swift`、`Registration.swift`、`InstallerFlow.swift`、`InstallCommand.swift`，不連結 ShanjieKit；ShanjieKit 不連結 TIS）。

### 5.1 產物

- `安裝善解輸入法.app`：bundle ID `com.nyanako.shanjie.installer`（刻意不用輸入法 ID 當開頭，免得 LaunchServices 與模式 ID 的查詢混在一起）；一般 GUI App（沒有 `LSUIElement`）；`LSMinimumSystemVersion` 26.0；arm64；hardened runtime；**沒有 entitlements**；`CFBundleShortVersionString` 與 `CFBundleVersion` 都是建置時的 `SHANJIE_VERSION`。
- 內容：`Contents/MacOS/shanjie-installer`；`Contents/Resources/shanjie-<版本>.zip`（Release 的同名附件原檔，已簽章、公證、staple，逐位元組相同）；`install-ime.sh`（repo 的 `scripts/install-ime.sh` 原樣複製）；`LICENSES/`（`LICENSE`、`McBopomofo-MIT.txt`、`data.md`）；`Assets.car` 與 `AppIcon.icns`（和輸入法同一個圖示，Info.plist 的 `CFBundleIconName`、`CFBundleIconFile` 是 `AppIcon`）；`zh-Hant.lproj`、`en.lproj` 的顯示名稱。
- `Resources/` 裡**不放展開的 .app**：展開的輸入法放在那裡，可能被 LaunchServices 登記成同一個 bundle ID 的第二份，codesign 也只把它當資源。
- 介面字串寫在程式裡（`L(zh, en)`），第一個偏好語言以 `zh-Hant`、`zh-TW`、`zh-HK`、`zh-MO` 開頭用繁體中文，否則英文。
- Release 附件：`shanjie-installer-<版本>.zip`（ASCII 檔名；解壓後是 `安裝善解輸入法.app`）與 `.sha256`。

### 5.2 組建與檢查

- `scripts/build-installer.sh <輸出資料夾> [shanjie-<版本>.zip]`：輸出資料夾必須是 repo 內的相對路徑；`SHANJIE_VERSION`（`MAJOR.MINOR.PATCH`）必填（版本規則只在 `build-app.sh`，由呼叫端傳入）；給了 zip 就複製進 `Resources/` 並 ad-hoc 簽章（hardened runtime、沒有 entitlements、不加 `--deep`），zip 檔名必須是 `shanjie-<版本>.zip`（安裝程式用自己的版本找 zip）；沒給 zip 就輸出未簽章骨架（release 的 build 工作用，zip 由 sign 工作放入）。
- **root 階段接受的版本編進執行檔**：用 `-Xlinker -sectcreate __TEXT __shanjie_ver <檔>` 連結進 `__TEXT,__shanjie_ver`（檔名帶版本，建置前先刪舊執行檔，免得重用別的版本的連結結果）。`scripts/installer-version.sh <執行檔>` 用 `segedit -extract` 讀出（只讀檔，不執行；沒有該 section 時什麼都不印）。建置腳本與 `check-installer.sh` 都斷言它等於安裝程式的版本。
- `scripts/check-installer.sh [安裝程式] [輸入法 zip]`（只讀，不啟動安裝程式）斷言：執行檔在；bundle ID；`LSMinimumSystemVersion` 26.0；沒有 `LSUIElement`；執行檔內的版本等於 `CFBundleShortVersionString`；`Resources/shanjie-<版本>.zip` 在且與輸入法 zip `cmp` 相同；`Resources/` 沒有任何 `.app`；`install-ime.sh` 與 repo 的逐位元組相同；授權檔、App 圖示與兩個 Info.plist 鍵；沒有 entitlements；flags 含 `runtime`；`codesign --verify --strict --deep` 通過。

### 5.3 兩個選項與決策表

歡迎頁並列兩個同樣大小的選項，**沒有預設按鈕**（Return 不綁任何一個，兩個都能用 Tab 選到）：

| | 這台 Mac | 只有我的帳號 |
|---|---|---|
| 位置 | `/Library/Input Methods/善解輸入法.app`，`root:wheel`、目錄與執行檔 755、其他 644、沒有群組或其他人可寫、沒有 setuid／setgid | `~/Library/Input Methods/善解輸入法.app` |
| 安全輸入 | 照常能用（學習暫停、不讀前文） | 系統會暫時停用善解 |
| 安裝與更新 | 安裝與每次更新各一次管理員密碼 | 不要密碼、一鍵更新 |
| 誰能裝 | 管理員 | 任何帳號 |
| 其他帳號 | 都能用，各有一個 container | 看不到 |

選項的文字（中文）：「這台 Mac 的每個帳號都能用。有程式開著安全輸入時（密碼管理器、終端機的『加密的鍵盤輸入』），善解照常能用。」代價「安裝和每次更新都要輸入一次管理員密碼。」按鈕「安裝到這台 Mac」；「不需要密碼，一鍵更新。有程式開著安全輸入時，系統會暫時停用善解。」代價「其他帳號看不到善解。」按鈕「只裝在我的帳號」。英文版同義（按鈕 Install for This Mac／My Account Only）。

設計取捨：位置影響安全輸入期間是否被系統反灰（裝在使用者自己寫得進的 `~/Library` 時，任何程式持有安全輸入都會讓第三方輸入法反灰；同一份放在 `/Library` 就不會）；代價是 `/Library` 要管理員密碼，所以兩個都留給使用者選。一台 Mac 同一時間只留一份正式 ID。開發版 ID 只在 `~/Library`，安裝程式只裝正式 ID。

`InstallDecision.decide(choice:system:accountCopy:account:bundled:listedBeforeCopy:)`（純函式，單元測試逐列）。輸入：`/Library` 那份的版本 S（沒有／讀不到版本／較舊／同版／較新，與內附版本比；讀不到版本的當成較舊）、帳號層有沒有正式 ID 的那份 U（含舊名 `shanjie.app`）、帳號層那份的版本、`listedBeforeCopy`（§5.8）。

| 選擇 | S | U | 動作 | 密碼 | 登記 | 完成頁要求登出 |
|---|---|---|---|---|---|---|
| 這台 Mac | 任意 | 有，而且比內附版本新 | `accountNewer`：停下來，畫面說明「你的帳號裡是更新的版本 X」，請用 X 的安裝程式；不改任何東西 | — | — | — |
| 這台 Mac | 沒有 | 沒有 | 安裝到 `/Library` | 要 | 強制 | 要 |
| 這台 Mac | 沒有 | 有 | 安裝到 `/Library`；帳號層那份移走 | 要 | 強制 | 要 |
| 這台 Mac | 較舊（含讀不到版本） | 沒有 | 更新 `/Library`，舊版留成 `/Library/Input Methods/.shanjie-previous` | 要 | 照舊 | 不要 |
| 這台 Mac | 較舊 | 有 | 同上；帳號層那份移走 | 要 | 強制 | 要 |
| 這台 Mac | 同版 | 沒有 | 只啟用；次要動作「重新安裝」＝「較舊、沒有」那一列 | 不要（重新安裝才要） | 照舊 | 不要 |
| 這台 Mac | 同版或較新 | 有 | 不換 `/Library`；帳號層那份移走（`moveToSystem`） | 不要 | 強制 | 要 |
| 這台 Mac | 較新 | 沒有 | 只啟用，不降版 | 不要 | 照舊 | 不要 |
| 只有我的帳號 | 有（含讀不到版本、懸空的符號連結） | 任意 | `blocked`：畫面說明「這台 Mac 已經裝了善解」並印出 §6.1 的移除指令；不改任何東西 | — | — | — |
| 只有我的帳號 | 沒有 | 沒有，而且系統已列出善解（`listedBeforeCopy`） | 安裝到 `~/Library`（反方向換位置） | 不要 | 強制 | 要 |
| 只有我的帳號 | 沒有 | 其他 | `InstallerPlan.decide(installed:bundled:)`：沒裝或讀不到版本→安裝；較舊→更新；同版→只啟用（次要動作「重新安裝」）；較新→只啟用，不降版 | 不要 | 照舊 | 照舊 |

- `InstallerPlan.decide` 用數字比較（`.numeric`）比對版本字串。
- 「強制登記」＝不看 §5.8 的跳過規則，對這次安裝的位置呼叫 `TISRegisterInputSource` 並啟用（`moved`）；「照舊」＝§5.8 的規則（系統已認得就不碰）。`moved` 只在換了位置時為真：選「這台 Mac」而帳號層那份被移走或 `/Library` 原本沒有；或選「只有我的帳號」而帳號層原本沒有、系統卻已列出善解。同位置的更新不強制，以免觸發 Caps Lock 的問題（§5.8）。
- 「帳號層那份移走」在 root 階段成功之後才做；使用者在密碼框按取消時什麼都不動。
- `firstInstall`（`InstallDecision.firstInstall`）：選「這台 Mac」、`/Library` 沒有善解、帳號層沒有、`listedBeforeCopy` 為假；在任何 TIS 呼叫之前就知道，換位置後走哪個登出畫面由它定（§5.9）。

### 5.4 畫面

視窗 560×430，依畫面內容伸縮高度（最小 430）；標題「安裝善解輸入法」／「Shanjie Installer」；只有標題列與關閉按鈕。複製、登記進行中（`isBusy`）關不掉視窗、也不能結束（否則腳本會變孤兒、什麼都沒啟用又沒有訊息）。頂端步驟指示四個點：歡迎、安裝、啟用、試打；目前步驟實心，已完成或跳過的灰色實心，未到的空心。外觀跟系統的淺色、深色；印章（朱紅圓角方塊裡的「解」）與標題字型比照網站；視覺參數都是 `Look` 裡的具名常數。

| 畫面 | 內容與按鈕 |
|---|---|
| 歡迎 | 印章、「善解輸入法 <版本>」、「開源的 macOS 注音輸入法。」；`/Library` 有一份時加「已裝給這台 Mac，版本 X。」（讀不到版本寫「版本不明」）；帳號層有時加「已裝在你的帳號，版本 X。」；兩個選項；可展開的「詳細資訊」（兩個選項的完整說明、執行中的舊版會被結束、上一版保留在同一資料夾的 `.shanjie-previous`、退回沒有沙盒的 0.4.x 以前版本的說明與搬回步驟的位置〔§9〕、授權摘要與「顯示授權」）；按鈕「取消」 |
| 同版 | 「這個版本已經裝好了」；「啟用」（只做 §5.8，不呼叫 `install-ime.sh`）與「重新安裝」（會把目前這份當成上一版保留，取代原本保留的上一版）、「取消」 |
| 較新 | 「已經裝了更新的版本」，不降版；「啟用」、「取消」 |
| 帳號層較新 | 「你的帳號裡是更新的版本」，說明沒有安裝也沒有改任何東西；「返回」（回歡迎頁）、「完成」 |
| 被擋下 | 「這台 Mac 已經裝了善解」；可選取的移除指令與「拷貝指令」按鈕；「返回」、「完成」 |
| 安裝中 | 轉圈加一句狀態：「正在複製…」、「請在系統的視窗輸入管理員密碼。」、「正在把你帳號裡的那份移到 .shanjie-previous…」、「正在請系統改用新的那份…」、「正在請系統啟用…如果系統問要不要允許，請按『允許』。」、「正在等系統啟用。如果系統跳出視窗，請允許『善解輸入法』。」 |
| 失敗 | 「沒有裝好」加白話說明，下面是可選取的錯誤細節（家目錄顯示成 `~`）與「拷貝錯誤細節」；只有「完成」 |
| 啟用（到系統設定加入） | 「還差一步：到系統設定加入」，三步：按「打開輸入方式設定」；在「文字輸入」的「輸入方式」按「編輯⋯」再按「+」；選「繁體中文」裡的「善解輸入法」按「加入」。按鈕「打開輸入方式設定」（`NSWorkspace.open` 開 `x-apple.systempreferences:com.apple.Keyboard-Settings.extension`）、「重新檢查」、「完成」。重新檢查沒偵測到時加一句「還沒偵測到，請照上面三步加入。」 |
| 試打 | §5.11 |
| 請登出、請登出再加入 | §5.9 |

沒有「現在登出」按鈕：要送 Apple Event 給 loginwindow，hardened runtime 下需要 `automation.apple-events` entitlement，與安裝程式沒有 entitlements 的規則衝突。

### 5.5 複製與解開

按下後按鈕立刻停用（避免重複執行）：

- 用 `/usr/bin/ditto -x -k --noqtn` 把 `Resources/shanjie-<版本>.zip` 解到 `NSTemporaryDirectory()` 底下新建的 `shanjie-installer-<UUID>` 資料夾。`--noqtn`：不讓隔離屬性跟著進已安裝的那份；外層安裝程式啟動時已經過 Gatekeeper，內層由外層簽章封住。
- 帳號層：以 `/bin/bash` 執行 `Resources/install-ime.sh <暫存區/善解輸入法.app>`，參數用陣列（不經 `sh -c`），**不繼承環境**，只給 `HOME`（取自 `getpwuid(getuid())`，和算目的地同一個來源）、`PATH=/usr/bin:/bin:/usr/sbin:/sbin`、`SHANJIE_INSTALL_SKIP_REGISTER=1`（繼承來的 `SHANJIE_INSTALL_FILES_ONLY`、`BASH_ENV`、`PATH` 會讓安裝靜靜走錯路）。stdout 丟棄；stderr 讀到 EOF 才 `waitUntilExit()`（避免管線塞滿卡住），保留最後 4 KB 當純文字顯示（腳本的警告在前、錯誤在最後一行）。結束後刪暫存資料夾。腳本非 0 就顯示失敗與 stderr，不做後續步驟。
- 「這台 Mac」：先在安裝程式自己的程序裡驗解開的 bundle 滿足 §5.6 的指定需求（給使用者看的提早失敗，不是安全檢查），不過就不跳密碼框、直接顯示「內附的輸入法簽章不對，沒有安裝。請重新下載安裝程式。」；再進 §5.6。

### 5.6 root 階段（「這台 Mac」）

- **常數指令**：編進安裝程式的固定指令字串，**不執行 bundle 裡的任何腳本**（安裝程式與它的資源在使用者寫得到的位置）。唯一的變數是安裝程式自己解開的暫存路徑；組字串前先斷言它只含字母數字（任何文字）、空格與 `/._-`、以 `/` 開頭、沒有 `.` 或 `..` 段，再用單引號跳脫。編進執行檔的版本必須是點分數字，否則拒絕（建置時沒有寫入版本的安裝程式裝不了「這台 Mac」）。
- **執行**：`NSAppleScript` 的 `do shell script "<指令>" with administrator privileges`，在主執行緒呼叫（畫面已繪製之後）。指令結構固定為 `/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh -c '<腳本>'`（`env -i` 只作用在它後面那一個指令，`umask 022` 是 shell 內建，所以腳本必須在 `sh -c` 裡；腳本裡全部用絕對路徑）。兩層跳脫（腳本裡的暫存路徑用 shell 單引號、整串放進 AppleScript 字串字面值時跳脫 `\` 與 `"`）由同一個純函式負責。使用者按取消（AppleScript 錯誤 −128）就回到歡迎頁。取得授權的呼叫在 hardened runtime、沒有任何 entitlement 的 ad-hoc App 上量過可用（回 `uid=0`，授權記錄的 client 是該 App）。
- **腳本步驟**（任何一步失敗就停、清掉 staging、不動現有的 bundle）：
  1. 若根目錄存在：斷言是目錄、不是符號連結、擁有者 uid 等於預期（正式值 0）、模式為 3 或 4 位八進位且群組與其他人都不可寫（不限定群組是 wheel）；不存在就 `mkdir -m 755` 並 `chown` 成預期擁有者。
  2. `mktemp -d "<根>/.shanjie-staging.XXXXXX"`（root 擁有、0700）。
  3. `ditto --noqtn --noacl <暫存路徑>/善解輸入法.app <staging>/善解輸入法.app`：從使用者的暫存**複製進 root 擁有的目錄**，之後使用者改不到它。
  4. `chown -R root:wheel` 與 `chmod -R u+rwX,go+rX,go-w,ug-s`（兩者都不跟隨符號連結）。
  5. 在 root 擁有的副本上驗簽章：`codesign --verify --strict --deep -R=<指定需求>`（驗完整封印，不是讀資訊）。指定需求同 §4.3 的 release 版：正式 ID、Developer ID 中繼憑證與葉憑證、團隊 ID 常數。`--strict` 會拒絕指向 bundle 外的連結。
  6. 清 ACL 並斷言：`find <副本> ! -type l -exec chmod -N {} +`（逐一清、跳過符號連結；不能用 `chmod -RN`，它會跟隨副本裡的符號連結一層，同使用者的程式趁輸入密碼時放一個指向外面的連結，root 就會清掉外面任意路徑的 ACL）；`ls -leR` 的輸出不得有 ACL 列；副本的 `CFBundleVersion` 必須**等於編進安裝程式的常數**（不讀安裝程式的 Info.plist），而且不低於 `/Library` 現有那份的版本（現有版本必須是點分數字；「重新安裝」允許相同）。這兩條擋住「趁輸入密碼的幾秒對解開的檔加 ACL」與「把暫存換成任何一個 Developer ID 簽過的舊版」。
  7. 目的地若是符號連結就停；現有的 `善解輸入法.app` 改名成 `.shanjie-previous`（舊的 `.shanjie-previous` 先移進 staging，新的一份就位之後才跟著 staging 一起刪；任何一步改名失敗就放回原處，放不回去就把 staging 留著並印出位置），副本 `mv` 到 `善解輸入法.app`（同一個檔案系統，改名是原子的），刪掉 staging。
- 只寫 `/Library/Input Methods` 底下的 `善解輸入法.app`、`.shanjie-previous`、`.shanjie-staging.*` 三種路徑；不讀 HOME、不碰任何帳號的檔案；不加 entitlement、不改簽章規則。
- 失敗時畫面是「裝到這台 Mac 時失敗了，原本裝著的沒有變動。」加錯誤細節。
- 對外說法：可以寫「程式檔由系統保護、更新需要管理員密碼」；不寫「root 擁有讓攻擊者碰不到」。

### 5.7 使用者階段

root 階段成功之後（或 `moveToSystem` 時不經 root 階段），在安裝程式程序裡以使用者身分，先做 §5.10 的倚天延續（不能晚於換檔），再執行 `install-ime.sh` 的不複製模式（`SHANJIE_INSTALL_NO_COPY=1`、`SHANJIE_INSTALL_SKIP_REGISTER=1`，參數是 `/Library/Input Methods/善解輸入法.app`）：

1. 帳號層有正式 ID 那份時（含舊名 `shanjie.app`）：`lsregister -u` 它、移到 `~/Library/Input Methods/.shanjie-previous`（原有的先刪）；
2. `lsregister -f` `/Library` 那份；
3. 結束舊程序：樣式同時比對 `/Library/Input Methods/` 與家目錄的 `Library/Input Methods/` 兩個位置，只結束自己帳號的；
4. 登記（§5.8），之後走 §5.9。

失敗時畫面是「把你帳號裡的那份移開時失敗了。」加 stderr。

### 5.8 登記

`Registration`（`shanjie install`、安裝程式共用）。設計取捨：bundle 換過之後再呼叫註冊／啟用，系統的 Caps Lock 切換會壞（要用慢速按鍵修）；而對「系統認得但還沒接受」的輸入法，程式化的啟用呼叫回 noErr 卻什麼都不改。所以只在必要時才改任何 TIS 狀態。

1. `Registration.run` 一開始只讀三件事：`.zhuyin` 模式有沒有出現在已安裝清單（`modeListed`）；本體與 `.zhuyin` 模式是否都在已啟用清單（`accepted`）；有沒有已啟用的舊模式（`<ID>.standard`、`<ID>.eten`，`legacyEnabled`）。
2. `Registration.decide(modeListed:accepted:legacyEnabled:moved:)`：

| 情況 | 動作 | 回傳 |
|---|---|---|
| `moved` 為真，或系統不認得模式（第一次安裝），或有舊模式 | 完整流程 | 依流程 |
| 已接受、沒有舊模式 | 什麼都不改 | `done`，`skipped = true` |
| 認得、還沒接受、沒有舊模式 | 什麼都不改 | `notAccepted`，`skipped = true` |

3. 模式不在清單且不是 `moved` 時，先每 0.25 秒重讀，最多 2 秒（剛換完檔，系統可能晚一點才列出），所以全新安裝會等滿 2 秒。`moved` 不重讀。
4. 完整流程：`TISRegisterInputSource(bundleURL)`；找 `.zhuyin` 模式（找不到→ `modeNotListed`，或註冊也失敗時 `registrationFailed`）；啟用本體（沒有模式 ID 的那個 source）與 `.zhuyin`（任一失敗→ `enableFailed`）；確認 `accepted`（否→ `notAccepted`）；倚天延續（§5.10）；停用舊的 `.standard`、`.eten`（失敗只記 `legacyDisableFailed`，不改結果）。本體必須一併啟用，否則模式在任何地方都不列出。
5. 傳給 `run` 的 bundle URL 與 ID 是參數：安裝程式傳**已安裝那一份**（`/Library` 或 `~/Library` 那份）與從那個 bundle 讀出的 ID，用 `UserDefaults(suiteName: <ID>)`，絕不傳自己 bundle 裡的路徑（被 App Translocation 時那是隨機的唯讀路徑）；`shanjie install` 傳 `Bundle.main`、`UserDefaults.standard`。
6. `listedOrEnabled(bundleID:)`（歡迎頁、複製之前讀一次，不重讀）：系統列出善解的模式，**或**使用者的已啟用清單裡有善解的 bundle ID（`com.apple.inputsources` 的 `AppleEnabledThirdPartyInputSources`、`com.apple.HIToolbox` 的 `AppleEnabledInputSources`）。為什麼兩者都看：用 `sudo rm -rf` 刪掉 `/Library` 那份之後，系統的輸入法列表數秒內不再列出善解，但已啟用清單還留著它（系統設定看到的就是它）；只看前者會把退回當成全新安裝。不能用 `run` 開頭的那次讀取，因為它在複製之後。
7. 「系統是否已接受」一律用**新程序**讀（`--accepted-check`，§5.12）：安裝程式自己的程序讀到的輸入法列表不會跟著使用者在系統設定的回答更新（另一個程序 20 秒內就讀到已啟用，它 2 分鐘後仍讀成未接受）。

### 5.9 登記之後的畫面

`InstallerFlow.next(after:)`（未換位置）：`done` → 試打；`modeNotListed` → 啟用（到系統設定加入）；`notAccepted` → 等待（安裝畫面的狀態，不是另一個畫面）；`registrationFailed`、`enableFailed` → 失敗（文字「系統沒有接受這個輸入法。可以到『系統設定 → 鍵盤』手動加入：…」）。等待：複製過才輪詢——每 0.5 秒起一個新程序讀 `--accepted-check`（逾時 5 秒當未接受），最多 60 次（30 秒）；接受了（不在前景就把安裝程式帶回前面）再呼叫一次 `Registration.run`（這時它跳過，有舊模式的話做完舊模式的處理）後到試打；等不到就到「啟用」並讓 Dock 圖示跳一次（不搶前景，使用者可能還在系統設定裡回答）。沒有複製過（「啟用」按鈕）時 `notAccepted` 直接到「啟用」。所有在接受之後走到試打的路都經過 `Registration.run`。

「重新檢查」：先用新程序確認系統已接受，沒接受就回「啟用」並加「還沒偵測到…」，不碰任何會改 TIS 的函式；接受了才呼叫 `Registration.run`，這時 `notAccepted` 是本程序列表過期，仍到試打。

**換位置後**（`moved`）`InstallerFlow.next(after:moved:firstInstall:)`：登記完成之前不輪詢，也不帶使用者去系統設定，因為換位置後到登出之前，系統設定裡都還沒有善解：

| 登記結果 | 第一次安裝 | 畫面 |
|---|---|---|
| `done` | 任何 | 「請登出」 |
| `notAccepted`、`modeNotListed` | 否 | 「請登出」 |
| `notAccepted`、`modeNotListed` | 是 | 「請登出，再加入」（步驟列停在「啟用」） |
| `registrationFailed`、`enableFailed` | 任何 | 失敗畫面，加同一句登出提示（失敗文字是「系統沒有登記這個輸入法。」或「系統沒有啟用這個輸入法。」，不叫使用者去系統設定） |

- 「請登出」：「✓ 已經裝好了，請登出再登入」＋「系統設定如果問要不要允許善解，先按『允許』再登出。」＋「請登出再登入一次，善解才會在所有 App 裡正常。登入後輸入法選單裡如果沒有善解，請重新打開這個安裝程式。」只有「好」一個按鈕（結束安裝程式，不替使用者登出）。
- 「請登出，再加入」：「1. 請登出再登入一次。2. 登入後到『系統設定 → 鍵盤』，在『文字輸入』的『輸入方式』按『編輯⋯』，再按『+』。3. 選『繁體中文』裡的『善解輸入法』，按『加入』。」加「系統設定如果現在自己打開了：問要不要允許善解，就先按『允許』；然後可以關掉，登入後再加。登入後如果找不到善解，請重新打開這個安裝程式。」第一次安裝的系統不接受，登出後輸入選單裡也沒有善解；從別的位置換過來的善解是系統原本就列出的，登出後照樣列出，所以不叫使用者加入。
- 換位置且結果是 `notAccepted` 時，登記後在背景等系統的答覆（每 0.5 秒一個新程序，最多 240 次，約 2 分鐘），讀到已接受時：第一次安裝的畫面從「請登出，再加入」換成「請登出」，並寫一行 `return:` log（接受時安裝程式在不在前景，或逾時）。這個等待本身不叫回安裝程式。
- **系統設定叫回來**：系統在啟用呼叫的同一刻自己打開系統設定，蓋住安裝程式的最後一頁。`InstallerFlow.watchesSystemSettings`：只在兩個登出頁，而且這次登記真的呼叫了啟用（沒有跳過、結果是 `done`、`notAccepted` 或 `enableFailed`）才監看。接下來 10 秒內，`com.apple.systempreferences` 一拿到前景，就把安裝程式帶回前面（`activate`、`orderFrontRegardless`、`makeKeyAndOrderFront`）並讓 Dock 圖示跳（`.criticalRequest`，安裝程式變成 active 時自動停）；當下系統設定已在前面就立刻做；使用者按了任何按鈕就停止監看。叫使用者去系統設定的頁面（啟用、失敗）一律不叫回來。〔推論〕系統設定若問「允許」，那個問題會在安裝程式後面，所以兩個登出頁都寫「先按允許再登出」。
- 靜態 log（subsystem `com.nyanako.shanjie.installer`）：`registration: forced (location changed)` 或 `registration: as before`；`registration outcome: <結果>`；`return: accepted, not in front`／`return: accepted, in front`／`return: not accepted within the wait`；倚天延續沒在換檔前寫進檔案時 `layout carry-over not on disk before the swap; continuing`。
- 登出再登入後重開安裝程式，會落在「同版、沒有帳號層」：只啟用、照舊登記，系統還沒接受時才照一般流程引導到系統設定加入。所以「到系統設定加入」一律在登出之後。

### 5.10 倚天排列沿用

從兩模式舊版升級時，只開了倚天模式、而且還沒選過排列，要保留倚天：`Registration.carryOverEten` 寫 `layout=eten`（條件：`layout` 沒設、`.eten` 已啟用、`.standard` 沒啟用；已選過排列不重寫）。

- 沙盒版啟動後偏好設定在 container 裡，從外面寫的值不會被讀到，所以安裝程式在**換檔之前**、用開始時讀到的輸入法 ID 與已啟用模式呼叫一次（那時裝的是沒有沙盒的舊版、container 還不存在；之後第一次啟動沙盒版時這個值隨偏好設定一起搬）。寫了之後輪詢 `~/Library/Preferences/<ID>.plist` 直到磁碟上的檔案真的有 `layout`（每 0.25 秒、最多 10 秒；系統搬的是檔案，不是 cfprefsd 的快取），等不到記一行靜態 log、照樣安裝，不卡住。「這台 Mac」的 carry-over 在密碼框成功之後才做（取消不留下寫入）。
- `fullFlow` 裡的呼叫保留：`shanjie install` 在沙盒裡跑，寫的是 container 的網域。container 已經存在時（從沙盒版再升級）從外面寫的值不會被讀到，但那時輸入法已經有自己的 `layout`，不需要沿用。

### 5.11 試打

最後一頁「✓ 已經裝好了」：一個可輸入的一般文字框（不是 `NSSecureTextField`——安全輸入會讓善解反灰；內容不存到任何地方）、「切換到善解」按鈕、一句「之後從選單列的輸入法選單，或按 Caps Lock、⌃空白鍵切換到善解。」、「完成」。

- 「切換到善解」只在使用者按下時做，不會自動切換：`Registration.selectMode` 在已啟用的 source 裡只選輸入方式 ID 等於 `<已安裝那一份的 ID>.zhuyin` 且 `kTISPropertyInputSourceIsSelectCapable` 為真的那一個（同一個 bundle ID 也會列出輸入法本體——不能選，以及停用失敗時留下的舊模式，選錯會靜靜地沒切到；純函式 `InstallerFlow.modeIndex`），呼叫 `TISSelectInputSource`，讓文字框成為 first responder，0.3 秒後重讀 `TISCopyCurrentKeyboardInputSource`，不是善解才算失敗（回傳 noErr 但沒切過去，例如安全輸入開著，第三方輸入法反灰，也算失敗）。
- 失敗時按鈕旁寫「切換失敗。請從選單列的輸入法選單選『善解輸入法』。」
- 停用舊模式失敗時（`legacyDisableFailed`），試打頁多一句：如果輸入方式清單裡還有舊版的「善解（標準）」或「善解（倚天）」，請到「系統設定 → 鍵盤 → 輸入方式」移除。
- 這些呼叫都不需要 entitlement。

### 5.12 命令列參數

安裝程式的執行檔只認兩個特殊形式，其餘一律照一般啟動：

- `shanjie-installer --render-steps <資料夾>`（剛好這兩個參數）：資料夾必須已存在；把每個畫面在淺色、深色下各畫成一張 2× 的 PNG（`<名稱>-light.png`、`<名稱>-dark.png`），以不覆蓋的方式寫（同名檔已存在就失敗），然後結束。畫面共 16 個（歡迎的三種狀態、同版、被擋下、安裝中、密碼、失敗、啟用中、啟用、試打、請登出、請登出再加入、失敗加登出、較新、帳號層較新），共 32 張；版本號與錯誤文字是寫死的假資料。不開視窗，不讀 `~/Library/Input Methods`，不呼叫複製、登記、TIS，也不寫那個資料夾以外的東西。真正的流程與畫面輸出共用同一份建畫面程式碼（`Screen`／`StepView`），建畫面的函式只接參數、不引用 `Paths`、`InstallerPlan.decide`、複製或登記。這是「agent 與 CI 不啟動安裝程式」之外唯一允許執行的形式，只由 main 在 repo 外的暫存資料夾對 `swift build` 出的裸執行檔執行。
- `shanjie-installer --accepted-check <bundle ID>`（剛好這兩個參數）：在建立 `NSApplication` 之前，讀 `Registration.isAccepted`，已接受結束碼 0，否則 1。安裝程式用自己的執行檔以新程序呼叫它，逾時 5 秒當未接受。

安裝程式不寫自己的偏好設定（只有 §5.10 寫進輸入法網域的 `layout`）、不連網、不留下背景程序；按「完成」或關閉視窗就結束。agent 與 CI 都不啟動它、不執行它的執行檔（CI 只檢查 bundle 結構與簽章）。

## 6. install-ime.sh 與 `shanjie install`

### 6.1 install-ime.sh

用法：`scripts/install-ime.sh <善解輸入法.app 或 善解（開發版）.app>`。使用者本人執行，不用 sudo、拒絕 root；一次只能跑一個。

- 目的地依 bundle 的 `CFBundleIdentifier`：正式 ID → `~/Library/Input Methods/善解輸入法.app`，上一版留成 `.shanjie-previous`，改名前的舊名 `shanjie.app` 也會被升級；開發版 ID → `善解（開發版）.app`，上一版 `.shanjie-dev-previous`，絕不碰正式版；其他 ID 拒絕。
- 正式 ID 只接受本專案 Developer ID 簽章的 bundle（`codesign -dv --verbose=2` 要有 `Authority=Developer ID Application: …` 與 `TeamIdentifier=<團隊 ID>`；讀不到簽章也拒絕），在複製任何東西之前檢查，訊息指向開發版的 `make bundle`。原因：本機 ad-hoc 簽的正式 ID 組建會建立正式 container 並搬走真正的資料。
- 只有真的安裝才碰系統（LaunchServices、執行中的程序、輸入來源）：`HOME` 必須是這個帳號真正的家目錄（`dscl` 的 `NFSHomeDirectory`）。`SHANJIE_INSTALL_FILES_ONLY=1`（測試用）在檔案交換之後、碰任何系統之前停下，並允許 `SHANJIE_TEST_LSREGISTER`（假的 `lsregister`，當 tripwire）與 `SHANJIE_TEST_SYSTEM_IM`（`/Library/Input Methods` 的替身）。
- 一台 Mac 一份正式 ID：要把正式 ID 裝進 `~/Library`、而 `/Library/Input Methods/善解輸入法.app` 存在（含懸空連結）時，什麼都不改，印出：`sudo rm -rf "/Library/Input Methods/善解輸入法.app" "/Library/Input Methods/.shanjie-previous"` 後結束碼 1。
- `SHANJIE_INSTALL_NO_COPY=1` 且參數剛好是 `/Library/Input Methods/善解輸入法.app`（安裝程式的使用者階段）：不複製，只做 §5.7 的三步，登記留給安裝程式。
- 步驟：(1) 複製到 `~/Library/Input Methods/.shanjie-staging-XXXXXX`（`ditto`；失敗不動已安裝的）；(2) 把舊版（DEST，或只裝了舊名時的 `shanjie.app`）移成上一版（先 `chmod -R u+w` 再刪原有的上一版），新的 `mv` 到位；兩個名稱都裝過時，新名稱的那份當上一版、舊名稱的只刪除；(3) `lsregister -f` 新的、結束執行中的舊程序（`pkill -U <uid> -f` 全路徑樣式，家目錄的正規表示式字元跳脫，正式 ID 比對兩個名稱與兩個位置，開發版只比對自己的資料夾，所以裝開發版不會停掉正式版），最多等 5 秒（10×0.5）；(4) 除非 `SHANJIE_INSTALL_SKIP_REGISTER=1` 或不複製模式，執行 `<目的地>/Contents/MacOS/shanjie install`。中途被打斷且舊版已移開、新版還沒到位時，清理程序把新版放到位並印出補救指令。
- 結束碼：`shanjie install` 回 3 → 印「系統還沒接受新的輸入法：請打開『系統設定 → 鍵盤 → 輸入方式』，按『+』，選『繁體中文』裡的『<名稱>』，按『加入』。」並以 3 結束（不重跑整個腳本：那會把剛裝的當上一版、刪掉真正的上一版）；其他非 0 → 印失敗訊息、依上一版是兩模式舊版或一般舊版印出退回指令，正式 ID 另提醒退回沒有沙盒的版本時看不到學習資料與設定（§9）；成功印最後一行「Next:…」。
- 沒有 `--deep`、沒有 sudo、不寫系統目錄。

### 6.2 `shanjie install`

`Registration.run` 之後，若結果是跳過的 `notAccepted`，呼叫 `waitUntilAccepted`：每 0.5 秒查一次已接受，最多 5 秒，期間不呼叫任何會改 TIS 的函式。`InstallCommand.report`：

| 結果 | 訊息 | 結束碼 |
|---|---|---|
| 完整流程 `done` | `install: registered and enabled`（停用舊模式失敗時另加警告） | 0 |
| 跳過的 `done`，或跳過的 `notAccepted` 在等待內接受 | `install: already enabled; registration skipped` | 0 |
| `modeNotListed` | `install: the input mode is not listed yet` ＋系統設定的步驟 | 3 |
| `notAccepted`（完整流程後，或跳過而等不到） | `install: the system has not accepted the input method yet` ＋系統設定的步驟 | 3 |
| `registrationFailed` | `install: registration failed and the input mode is not listed` | 1 |
| `enableFailed` | `install: TISEnableInputSource failed` | 1 |

`TISRegisterInputSource` 回錯誤另加一行警告。輸入法執行檔的參數只認：沒有參數（跑輸入法）、`install`、`--selftest`；其他一律以非 0 結束且什麼都不碰（`check-app.sh` check 6 逐個測 `foo`、`--selftest x`、`install x`、`--SELFTEST`、`-selftest`、`Install`、`--install`、空字串）。

## 7. 發版（與更新有關的部分）

`.github/workflows/release.yml`；其餘（gate、版本、Release 說明、環境與 secret 的總則）見 `docs/spec/release-and-ci.md`。

### 7.1 流程與資產

- 觸發：推 `v*` tag（完整發佈），或在 main 上 `workflow_dispatch`（乾跑，§7.5）。其他 ref 在 gate、sign、publish 都被拒絕。
- 資產：`shanjie-<版本>.zip` 與 `.sha256`（輸入法）；`shanjie-installer-<版本>.zip` 與 `.sha256`（安裝程式）；main 上的 `appcast.xml`；tap 裡的 cask。
- job 順序：`gate`（要求這個 tag 的 `docs/releases/<tag>.md` 存在且非空；要求這個 commit 在 main 上的 `ci.yml` push run 是 `success`，`scripts/check-ci-gate.sh`）→ `build`（無 secret，唯讀 token）→ `sign`（進 `release` Environment）→ `publish`（只有 tag）。
- `build`：以 `SHANJIE_RELEASE_BUILD=1`、`BUNDLE_ID=com.nyanako.inputmethod.shanjie` 呼叫 `build-app.sh`（ad-hoc，沒有 runtime）；再用 `build-installer.sh` 組出**未簽章的安裝程式骨架**（除了輸入法 zip 都在），好讓 `sign` 不跑任何 repo 的程式；兩者用 tar 上傳（tar 保留模式與符號連結，`upload-artifact` 不保留）。
- `check-ci-gate.sh`：對這個 commit 的 `ci.yml` push run（`head_sha`、`main`、`push`）輪詢到 `completed`；run 還不存在最多寬限 `CI_GATE_GRACE`（預設 300 秒，tag 可能比 CI 先到）；進行中最多等 `CI_GATE_TIMEOUT`（預設 1800 秒）；API 連續三次讀不到就失敗；失敗訊息附復原方法。**只改 `appcast.xml` 的 commit**（對唯一的父 commit 只改了 `appcast.xml`；它是 publish 工作以 `GITHUB_TOKEN` 寫到 main 的 feed，這種 push 不會觸發 `ci.yml`）往回跳過，最多 20 個，用第一個別的 commit 的 CI 結果——`appcast.xml` 是沒有任何建置或測試讀的資料。`CI_GATE_GH` 可替換 `gh`（`scripts/test-check-ci-gate.sh` 用替身測）。

### 7.2 簽章與檢查（sign 工作）

sign 工作不 checkout、不跑任何 repo 的程式：只用 Apple 的工具與 Sparkle 官方的工具；唯一的 checkout 是推 tag 時的 sparse checkout 兩個資料檔（`appcast.xml` 與這個 tag 的發版說明），只讀不執行。

1. 入口檢查：ref 必須是 tag 或 main 的 dispatch；簽章材料、公證金鑰、`SPARKLE_PRIVATE_KEY`、各變數都非空；`APPLE_TEAM_ID` 等於預期的團隊（空的 secret 必須讓發版停下，絕不產出 ad-hoc 的版本）。
2. 用拋棄式 keychain（隨機密碼、`-x` 匯入不可匯出的私鑰），只選預期團隊的 Developer ID Application 身分（以 SHA-1 指定）。
3. `tar -xpf` 解開建置產物，先檢查版面（§2.4，同一套斷言）。
4. 由內而外簽，不用 `--deep`：善解更新的 `Autoupdate`、`Updater.app`、framework、善解更新、輸入法的 framework，各自 `--options runtime --timestamp`、沒有 entitlements；再簽輸入法本體（runtime、timestamp、`--entitlements` 用 workflow 裡以**字面值**寫的三個鍵，不沿用 build 產出的 entitlements，所以出貨的 entitlements 不依賴 build 工作）。
5. 公證：`notarytool submit --wait`，判讀 JSON 的 `status` 必須是 `Accepted`（`notarytool` 對被拒絕的提交也可能回 0）；失敗時印 log 再失敗；成功後 `stapler staple`。公證蓋章之後、打包之前 `chmod -R a+rX,go-w`。`ditto -c -k --keepParent` 打成 zip 並算 `.sha256`。
6. `verify()`（對從 zip 解出的副本）：`codesign --verify --strict --deep`；`Authority=Developer ID Application:`；`TeamIdentifier` 等於預期；hardened runtime 旗標。輸入法另外：bundle ID 是正式 ID；`InputMethodConnectionName` 等於 `<ID>_Connection`；移轉清單恰好 `Move = [${ApplicationSupport}/shanjie]`；entitlements 整份等於三個鍵（正規化後 `cmp`）。安裝程式另外：bundle ID 是 `com.nyanako.shanjie.installer`、沒有 entitlements。兩者都要 `stapler validate`，且 `spctl -a -t exec -vv` 回 `source=Notarized Developer ID`。
7. `verify_sparkle()`：版面（§2.4）；輸入法、輸入法的 framework、善解更新、善解更新的 framework、`Autoupdate`、`Updater.app` 各自存在（路徑不在就失敗，絕不讀成「沒有 entitlements」）且 Team 與 runtime 正確、沒有 entitlements（輸入法本體除外）；善解更新的 bundle ID 與 Info.plist 規則；輸入法的 Sparkle 鍵與 `ShanjieUpdateBuild = release` 逐字比對（含型別）workflow env 裡的字面值，`SUEnableDownloaderService`、`SUEnableJavaScript` 不存在；權限斷言。
8. 輸入法 zip 通過後才組安裝程式：把剛打好的 zip 複製進骨架的 `Resources/`、重新解鎖 keychain（第一次公證可能等很久，keychain 自動上鎖是 3600 秒）、簽（runtime、timestamp、不加 `--deep`，zip 只是資源）、公證蓋章、打包；從 zip 解出驗證，並 `cmp` 安裝程式內的 zip 與要發佈的附件相同。
9. 兩次公證都在同一個 step 裡，p8 金鑰由該 step 的 `trap` 刪除；`timeout-minutes` 150。「移除簽章用的 keychain」是 `always()` 步驟。

### 7.3 feed（sign 工作）

- **金鑰成對檢查**（乾跑與推 tag 都跑，在簽章 keychain 刪掉之後）：用 workflow 內嵌的 CryptoKit（`Curve25519.Signing.PrivateKey(rawRepresentation:)`，不是 repo 的程式）從 secret 推出公鑰，與正式 zip 裡 Info.plist 的 `SUPublicEDKey` 比對，不同就失敗（否則這把私鑰簽的更新會被出貨的 App 全部拒絕）。私鑰從 stdin 進、只輸出公鑰；乾跑不用正式金鑰簽任何東西。
- **簽 feed**（獨立一步，條件 `github.event_name == 'push'`，在簽章 keychain 刪掉之後）：工具是 Sparkle 官方 release 的 tarball（版本與 `Package.resolved` 相同，SHA-256 以字面值寫在 workflow 裡）的 `generate_appcast`；舊的 `appcast.xml` 與這個 tag 的發版說明（改名成 `shanjie-<版本>.md`）用 sparse checkout 取、當資料讀。輸入資料夾只放舊 feed、這一個 zip（與 publish 上傳的同一份位元組，不放安裝程式的 zip）與發版說明。私鑰從 stdin 交給工具（`--ed-key-file -`），不寫進檔案。參數：
  - `--download-url-prefix https://github.com/Nanako0129/shanjie/releases/download/v<版本>/`（不給的話網址會從 `SUFeedURL` 的資料夾拼出來，指到不存在的檔案）；
  - `--embed-release-notes`（markdown 嵌進新項目；Sparkle 用文字檢視顯示，沒有網頁檢視、遠端資源或 JavaScript）；
  - `--maximum-versions 0`（保留所有版本）與 `--versions <版本>`（替舊版本線補發的版本不會被刪掉或略過）；
  - App 有 `SURequireSignedFeed`，所以 feed 自動簽章。
- 產生後只檢查**新項目**：恰好一個 enclosure 網址等於那個 zip、`length` 等於 zip 大小、有 EdDSA 簽章、`sparkle:version` 正確、有嵌入的 markdown 說明；整份 feed 有 `<!-- sparkle-signatures:` 簽章區塊；輸入資料夾裡的 zip 沒被改過。

### 7.4 發佈（publish 工作，只有 tag）

- 先確認 main 的 `appcast.xml` 的 blob SHA 等於被打 tag 的 commit 的，或等於新 feed 的（重跑）；否則停下，不發佈任何東西（另一個發版在這期間改了 feed，發佈會丟掉它的項目）。
- 建立 GitHub Release 要能重跑：這個 tag 的 release 已存在時，兩個 zip 都在且 `.sha256` 與這次的相同才略過建立，其他情況失敗，讓 cask 不會指向不同的雜湊。Latest 只給最新的版本線。
- 把 `appcast.xml` 以 contents API 寫到 main，帶剛讀到的 blob SHA（中間有變動就由 GitHub 拒絕，409）；已經一樣就略過。這個 commit 由 `GITHUB_TOKEN` 做，不觸發 `ci.yml`。安裝中的 App 從 `SUFeedURL` 讀它。
- 之後渲染 cask 並推到 tap（§8）。

### 7.5 乾跑

只能從 main 觸發（`workflow_dispatch`）：版本寫死 `0.0.0`（關於頁顯示「開發版」）；跑完 gate、build、sign、公證、`verify()`、`verify_sparkle()`、金鑰成對檢查；不簽 feed、不發佈任何東西。簽章、公證後的兩個 zip 以 `actions/upload-artifact` 保留一天（`signed`），給虛擬機與實機驗收 Developer ID 版用。`0.0.0` 的安裝程式遇到已裝的 0.4.x 只啟用、不複製（不降版），所以驗收這個版本用解出的 zip 加 `install-ime.sh`（它不比版本）。

### 7.6 金鑰輪換與外洩

- EdDSA 金鑰由專案維護人在自己的終端機用 Sparkle 的 `generate_keys` 產生；私鑰放進 `release` Environment 的 secret `SPARKLE_PRIVATE_KEY`，公鑰寫進 `build-app.sh`；agent 不碰鑰匙圈、不經手私鑰。
- 輪換不能走 Developer ID（zip 不帶程式碼簽章）：輪換那一版用**舊**私鑰簽壓縮檔與 feed、App 裡帶**新**公鑰，之後的版本才用新私鑰。舊私鑰遺失時 Sparkle 推不了任何更新，只能請使用者手動重裝。EdDSA 沒有撤銷，還沒更新的使用者在攻擊者同時有舊私鑰與 feed 寫入權時仍有風險。
- Sparkle 拒絕「新版拿掉 `SUPublicEDKey`」的更新（只允許輪換、不允許移除），所以要拿掉 Sparkle 的那一版仍要保留 `SUPublicEDKey`、用目前的私鑰簽、保留有效的程式碼簽章；或讓 appcast 不再有新項目，使用者改用安裝程式更新。
- 裝在 `/Library` 時，金鑰外洩的影響多了「以 root 換檔」（§1.3）。

## 8. Homebrew

`packaging/Casks/shanjie.rb` 是範本（`version`、`sha256` 兩行是佔位值 `0.0.0`／64 個 0）；`scripts/render-cask.sh <MAJOR.MINOR.PATCH> <sha256>` 只替換這兩行後印到 stdout，格式不對或範本裡不是各恰好一行就結束碼 1 且沒有輸出（`SHANJIE_CASK_TEMPLATE` 只給測試用）。CI 與 release 都用這支腳本，cask 的內容只有範本一處。

- 安裝指令：`brew install --cask nanako0129/tap/shanjie`。範本必須放在路徑含 `Casks/` 的位置，`brew style` 才套 cask 規則。
- 內容：`url` 指向 Release 的 `shanjie-#{version}.zip`；`name`「善解輸入法」、「Shanjie」；`depends_on arch: :arm64`、`macos: :tahoe`；`input_method "善解輸入法.app"`（Homebrew 搬到 `~/Library/Input Methods`，只做帳號層；`/Library` 用安裝程式。brew 裝的時候 `/Library` 已有一份會變成兩份，cask 不擋）。
- `auto_updates true`：brew 不再主動升級，交給 Sparkle。因此一般的 `brew upgrade` 可能略過這個 cask；指名升級用 `brew upgrade --cask nanako0129/tap/shanjie`。實際行為未量（範本的註解標明）。
- **不在 cask 裡執行 `shanjie install`**：`postflight do` 會被 `brew style` 判為違規，`postflight_steps` 在 Homebrew 的沙盒裡執行（`HOME` 是暫存資料夾、擋住讀取真正的家目錄），`~` 開頭的指令路徑也不展開；就算放行，TIS 在那個沙盒裡能不能註冊也沒有量過。所以由 `caveats` 請使用者執行 `"$HOME/Library/Input Methods/善解輸入法.app/Contents/MacOS/shanjie" install`，結束碼 3 時到系統設定 → 鍵盤 → 輸入方式加入。
- 升級後由新版接手：`postflight_steps` 裡一步 `terminate_process`（`match: :full`，帶一句 notice），pattern 是 `^[^ ]*/Library/Input Methods/(善解輸入法|shanjie)\.app/Contents/MacOS/shanjie$`。結尾錨定執行檔，所以帶參數的 `shanjie install` 與別的程式只是在參數裡提到這個路徑都不符合；開頭 `[^ ]*` 可為空，所以系統層的 `/Library/Input Methods` 也符合；`.shanjie-previous` 裡的上一版不符合；家目錄路徑含空白的帳號不會被結束（要手動結束舊程序或登出）。pattern 不錨定家目錄，因為 postflight 在 Homebrew 沙盒裡、HOME 是暫存資料夾，而 `pkill` 本來就只能結束自己帳號的程序。為什麼在 postflight 而不用 `uninstall on_upgrade`：升級時 Homebrew 從已安裝那一版的 receipt 讀解除安裝步驟，`on_upgrade` 在 JSON 裡是字串，`uninstall_phase` 只認 Symbol 或 Array，所以被略過（Homebrew 7.0.7 實測）；就算修好，`Uninstall` 也排在把新 bundle 搬進來之前，只會讓舊版有機會被重新啟動。postflight 是安裝階段最後一步，系統之後啟動的一定是新版。
- `uninstall signal: ["TERM", "com.nyanako.inputmethod.shanjie"]`：解除安裝時結束執行中的程序。
- `zap trash`：`~/Library/Application Support/shanjie`、`~/Library/Containers/com.nyanako.inputmethod.shanjie`、`~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist`（沙盒版把前兩者以外的資料搬進 container）。
- Homebrew 升級換 bundle 的那一小段，系統會暫時把善解標成「未啟用」（本體不在時），新版回來後 0.07 到 0.12 秒內自動恢復；曾有一次沒有恢復，原因未解——選單裡找不到善解時，到「系統設定 → 鍵盤 → 輸入方式」加回來；程式化的重新啟用在那種狀態下無效（§5.8）。
- 發佈到 tap：`publish` 工作用 deploy key（只在 `publish` 引用，`sign` 不引用，免得 key 進到持有簽章材料的 runner）clone tap、複製渲染好的 cask、內容沒變就不 commit、不加 `--force`；tap 的 ruleset 也禁止。注入防護：`run:` 區塊不用 `${{ }}` 內插 needs／steps 的輸出，一律經 `env`；雜湊由 `render-cask.sh` 再驗一次格式。

## 9. 資料移轉、退回與搬回

### 9.1 移轉到 container

沙盒版第一次啟動時，系統依 `container-migration.plist` 把學習資料夾 `~/Library/Application Support/shanjie`（含 `learning.tsv`、`.tmp`、`.corrupt` 與新詞學習記錄；權限 0700／0600 與不備份旗標保留，系統另在資料夾上加 `com.apple.quarantine`）**搬**進 `~/Library/Containers/<ID>`，偏好設定 `~/Library/Preferences/<ID>.plist` 也一併搬走。只搬一次：container 已存在就不再搬。輸入法程序寫入的檔案只有學習資料夾；偏好設定全在 `UserDefaults.standard`；模型、詞庫、詞包從 bundle 的 `Resources` 讀；系統標點表從 `/System/Library/PrivateFrameworks` 讀（沙盒裡讀不到是停止條件）。

### 9.2 退回

- 降回沒有沙盒的版本（0.4.x 以前）時，舊版只看原位置，所以看到的是空的學習資料與預設設定；退回期間學到的留在原位置。再升級成沙盒版時 container 已經存在，系統不再搬，那些資料就看起來不見了。
- Sparkle 不降版（關於頁的「更新到 X」只往新版走），安裝程式也不降版。所以退回要用舊版的 zip（Release 的 `shanjie-<版本>.zip`）解開後以 `scripts/install-ime.sh` 安裝，或先刪掉已安裝的那一份再用舊版的安裝程式。
- 裝在 `/Library` 的那份刪除要管理員：`sudo rm -rf "/Library/Input Methods/善解輸入法.app" "/Library/Input Methods/.shanjie-previous"`（完整加引號）。刪掉之後才能用 `install-ime.sh` 或安裝程式的「只有我的帳號」裝回帳號層。container 每個帳號各一份，各自在自己的帳號刪。
- 不做自動搬回。這個說明寫在 `install-ime.sh` 的失敗訊息與安裝程式歡迎頁的詳細資訊裡。

### 9.3 搬回指令

先把輸入法切到別的（例如 ABC），再在「終端機」貼上下面整段。它依序：結束善解的程序（`/Library` 與 `~/Library` 兩個位置、沙盒版與舊版）；原位置已有資料（退回期間學到的，或上一次執行到一半留下的複本）就停下來、不覆蓋；用 `ditto` 把 container 裡的學習資料夾複製回原位置並逐檔比對 SHA-256；偏好設定用 `defaults export` 讀出 container 裡的那一份，再以**原位置 plist 的完整路徑**當網域 `defaults import` 寫回，從同一個路徑匯出一次比對內容，並確認原位置真的有那個檔；兩樣都相同才刪掉**整個** container，任何一步不同就停下來、container 留著。

為什麼這樣寫：

- 不能用 `com.nyanako.inputmethod.shanjie` 這個網域名稱寫回：container 還在時，`defaults` 會把這個網域導向 container 裡的那一份，寫進去的是 container，接著刪 container 時設定就一起不見。
- 不直接複製 plist 檔：cfprefsd 有快取，直接換掉的檔案可能被忽略（推論，沒量過）。
- 一定要刪 container：它只在第一次建立時移轉，留著的話，下次升級到沙盒版不會再搬，搬回來的資料就看起來不見了。刪掉後下次升級系統會重新搬一次，帶上搬回來的資料。
- 讀不到 container（例如系統擋住存取）就在 `ls "$C/Data/Library"` 這一步停下，不會把「讀不到」當成「沒有東西要搬」；container 的設定檔是空的（`plutil -p` 只有兩行）就不寫回。

```bash
bash -s <<'EOF'
set -euo pipefail
ID=com.nyanako.inputmethod.shanjie
C="$HOME/Library/Containers/$ID"
SRC_AS="$C/Data/Library/Application Support/shanjie"
SRC_PREFS="$C/Data/Library/Preferences/$ID.plist"
DST_AS="$HOME/Library/Application Support/shanjie"
DST_PREFS="$HOME/Library/Preferences/$ID.plist"
[ -d "$C" ] || { echo "沒有 ${C}，不需要搬回。"; exit 0; }
pkill -U "$(id -u)" -f "^($HOME/Library/Input Methods/|/Library/Input Methods/)(善解輸入法|shanjie)\.app/Contents/MacOS/shanjie( |\$)" || true
sleep 1
for p in "$DST_AS" "$DST_PREFS"; do
  [ ! -e "$p" ] || { echo "原位置已經有 ${p}（退回期間產生的，或上一次執行到一半停下時留下的複本）。先把它移到別處備份，再執行一次。" >&2; exit 1; }
done
ls "$C/Data/Library" > /dev/null   # 讀不到（例如系統擋住存取）就在這裡停下，不會把「讀不到」當成「沒有東西要搬」
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
sums() { (cd "$1" && find . -type f -exec shasum -a 256 {} + | sort -k 2); }
if [ -d "$SRC_AS" ]; then
  ditto "$SRC_AS" "$DST_AS"
  [ "$(sums "$SRC_AS")" = "$(sums "$DST_AS")" ] || { echo "學習資料複製後不一致，container 保留。" >&2; exit 1; }
fi
if [ -f "$SRC_PREFS" ]; then
  defaults export "${SRC_PREFS%.plist}" "$T/from-container.plist"
  LINES="$(plutil -p "$T/from-container.plist" | wc -l)"   # 空的設定檔印成兩行 {、}
  if [ "$LINES" -gt 2 ]; then
    defaults import "${DST_PREFS%.plist}" "$T/from-container.plist"
    [ -f "$DST_PREFS" ] || { echo "偏好設定沒有寫到原位置，container 保留。" >&2; exit 1; }
    defaults export "${DST_PREFS%.plist}" "$T/written.plist"
    plutil -convert xml1 "$T/from-container.plist" "$T/written.plist"
    cmp -s "$T/from-container.plist" "$T/written.plist" || { echo "偏好設定寫回後不一致，container 保留。" >&2; exit 1; }
  fi
fi
rm -rf "$C"
echo "已搬回，container 已刪除。"
ls -la "$DST_AS" 2>/dev/null || true
defaults read "$ID" 2>/dev/null || true
EOF
```

搬回之後：再切回善解，學過的選字、新詞與排列設定應該都在；`defaults read` 印出的設定和沙盒版用的不同（例如排列不對）時，登出再登入一次再看；再升級成沙盒版時系統會重新搬一次。

發佈後若要發出「回到沒有沙盒」的版本（Sparkle 擋降版，所以必須是版本號更高的新版），該版必須內建一次性的自動複製：第一次啟動時，舊位置是空的、而且自己 ID 的 container 存在，就把 container 裡的學習資料複製回原位置；偏好設定用 `defaults` 的匯出與匯入，並以原位置 plist 的完整路徑或 App 自己的 UserDefaults 寫回，不用網域名稱呼叫 `defaults` 命令列；只複製、不刪 container。

## 10. 驗證

### 10.1 單元測試（Swift，不連網）

| 測試 | 檔案 | 涵蓋 |
|---|---|---|
| `UpdatesTests` | `macos/Tests/ShanjieKitTests/UpdatesTests.swift` | 位置說明（`testLocationNote`）、版本字串（`testVersionText`）、每日規則（`testShouldCheckDecision`）、tick 跟開關與天數（`testTickFollowsToggleAndDay`）、立即檢查不受開關與進行中的檢查影響（`testCheckNowIgnoresToggleAndARunningCheck`）、更新服務起不來（`testCheckNowWhenTheUpdaterCannotCheck`）、更新先檢查（`testInstallChecksFirst`）、有 Sparkle session 時（`testInstallWhileBusyFocuses`）、背景發現不開視窗也不丟已知版本（`testBackgroundFindDoesNotOpenAWindowOrDropAVersion`）、結果與還原（`testResultsAndRestore`）、只有 release／test 組建啟動（`testUpdaterRunsOnlyFromAReleaseOrTestBundle`）、選單項（`testMenuItemOnlyWhenAnUpdateIsKnown`）、通知（背景查到才發、每天一次、使用者按的不發、兩個開關、其他結果不發、開關預設開、狀態行、文字：`testBackgroundFindNotifiesOncePerDay`、`testUserChecksDoNotNotify`、`testSwitchesOffDoNotNotify`、`testOtherResultsDoNotNotify`、`testNotifySwitchDefaultsOnAndIsStored`、`testPermissionLine`、`testNotificationText`）、檢查中點通知會記下（`testInstallDuringARunningCheckIsKept`）、已是最新清掉通知（`testUpToDateClearsTheNotification`）、設定視窗重讀權限（`testSettingsRefreshReadsThePermission`） |
| `UpdateHostTests` | `macos/Tests/UpdateHostTests/UpdateHostTests.swift` | host 路徑（兩個根 × 兩個名稱、往上三層、符號連結、container 與桌面副本、家目錄字串邊界、家目錄經 realpath、translocation）、feed 只讀 Info.plist、跳過鍵與排程鍵被清掉、哪些檢查可以跑、只放行 `application`、組建類型三種值與缺值 |
| `InstallerPlanTests` | `macos/Tests/ShanjieInstallTests/InstallerPlanTests.swift` | `InstallerPlan.decide` 與 §5.3 的決策表每一列一個案例（含反方向換位置、帳號層較新、`/Library` 讀不到版本、「複製前沒有列出」才算換位置） |
| `RootStageTests` | `macos/Tests/ShanjieInstallTests/RootStageTests.swift` | root 指令以一般使用者對暫存根目錄執行：成功安裝與模式、重新安裝保留上一版、改名失敗保留上一版、staging 不被打開、更新、來源帶 ACL 時結果沒有 ACL、指向外面的連結不被跟隨、副本裡的連結保留、正式需求拒絕 ad-hoc 與未簽章的副本與開發版 ID、上層可寫／別人擁有／是符號連結都停、版本低於或高於常數被拒、降版被拒、目的地是符號連結被拒、路徑含不預期字元被拒、版本缺或格式錯被拒、正式參數值、AppleScript 來源還原後與指令逐字相同（含路徑裡的空格與全形字）、指定需求可解析且提早檢查用同一份 |
| `RegistrationTests` | `macos/Tests/ShanjieInstallTests/RegistrationTests.swift` | 已啟用清單的判定、`decide` 全部組合、`accepted` 需要本體與 `.zhuyin`、未列出會重讀 2 秒、`moved` 永遠走完整流程、跳過時不改任何東西（以注入的計數斷言）、`waitUntilAccepted`（5 秒內接受／一直沒接受）、完整流程後不等 |
| `InstallerFlowTests` | `macos/Tests/ShanjieInstallTests/InstallerFlowTests.swift` | 每個結果對應的畫面、換位置後的表格每一格、系統設定監看條件、挑 `.zhuyin` 模式、複製路徑的倚天延續（等檔案到磁碟才換檔、等待有上限、沒有延續時只換檔）、`layoutOnDisk` |
| `InstallCommandTests` | `macos/Tests/ShanjieInstallTests/InstallCommandTests.swift` | `shanjie install` 的結束碼與訊息 |

- 不呼叫 TIS：狀態用注入的；沒有 `UpdateHost` 以外的 Sparkle 呼叫在測試裡。
- `Updater` 與 `ShanjieUpdater` 的 Sparkle 接線不在單元測試裡（app target 沒有測試），由 §10.4 的虛擬機項目驗證。

### 10.2 腳本與 CI

`ci.yml` 的 `shell` 工作按這個順序（前面的步驟不得啟動任何沙盒組建；Swift 測試在獨立的 `swift` 工作，不啟動任何沙盒組建，只連結 `target/release/libcore.a`）：

0. `make bundle OUT_DIR=build/default`（不帶 `BUNDLE_ID`）：只建置；斷言 ID 是開發版、名稱與 `zh-Hant`／`en` 名稱照 §1.2、沒有移轉清單、沒有正式資料夾。
1. 正式 ID 的組建（`BUNDLE_ID=com.nyanako.inputmethod.shanjie`）：只建置；斷言移轉清單的 `Move:0`。
2. 拋棄式 ID（`…ci-nomanifest`、`OUT_DIR=build/nomanifest`）：只建置。
2a. 本機防呆：`CI= EXPECTED_BUNDLE_ID=<正式 ID> scripts/check-app.sh` 必須非 0 並印 `check 6 refused`，正式 container 仍不存在；`SKIP_RUN=1` 的同一個指令必須 0。
3. `scripts/test-sandbox-migration.sh <正式組建> <無清單組建>`（`timeout-minutes: 10`；讀 container 若在 CI 上跳 App Data 的詢問會卡住）：這個 job 第一次啟動沙盒組建。
4. 同一個腳本的拒絕測試：`CI=` 空與 `CI=true HOME=<暫存目錄>` 都必須非 0、暫存目錄沒有新檔案，log 分別有 `refused: CI is not true` 與「is not this account's home directory」。
5. `scripts/test-install-ime.sh`、`scripts/test-check-ci-gate.sh`、`scripts/test-check-models.sh`。
6. `make selftest-bundled SELFTEST_BUNDLE_ID=…selftest-$GITHUB_RUN_ID`：前後各檢查一次 `~/Library/Containers/<該 ID>`，之前不存在、之後存在（證明沙盒有套上）。
7. `EXPECTED_BUNDLE_ID=<正式 ID> scripts/check-app.sh`（check 2、3、6）。
8. `scripts/test-sandbox-mutations.sh`（§10.3）。
9. `make installer` 與 `scripts/check-installer.sh`。
10. cask：`scripts/test-render-cask.sh`，再對渲染出的 `0.0.0` cask 跑 `brew style`。

- `scripts/test-sandbox-migration.sh` 只在拋棄式的 GitHub-hosted runner 上跑，依序拒絕：`CI` 不是 `true`；`GITHUB_ACTIONS` 不是 `true` 或 `RUNNER_ENVIRONMENT` 不是 `github-hosted`；`HOME` 不是這個帳號真正的家目錄（`dscl` 的 `NFSHomeDirectory`，因為移轉看的是帳號的家目錄）；該家目錄下學習資料夾、正式偏好設定檔、正式 container、無清單組建的 container、已安裝的 `善解輸入法.app` 任一已存在。拒絕時結束碼非 0、什麼都不寫、不啟動任何東西。通過後印 `sw_vers`（「沒有清單不搬」量到的是哪一版 macOS 要看得到），放好來源（合成的 `learning.tsv`（含前文鍵 `#` 的新詞記錄）與 `.corrupt`、資料夾不備份旗標、`defaults write <ID> layout eten`），然後斷言：(a) 無清單組建執行 `--selftest` 後，它的 container 之前沒有、之後有，來源逐位元相同、偏好設定在原位、container 裡沒有學習資料；(b) 正式組建執行後來源消失、container 裡逐位元組相同且 0700／0600、不備份旗標保留、container 的偏好設定 `layout=eten`、原偏好設定檔消失；(c) 再放一份新來源後第二次執行不再搬；(d) 結束時偏好設定檔不存在（不受 cfprefsd 延遲寫入影響）。
- `scripts/check-app.sh`：check 2（Info.plist、Resources、名稱、移轉清單、模型清單、詞包、授權、Sparkle 鍵）、check 3（簽章、entitlements、版面、權限，§2.4）、check 6（`--selftest` 與八個必須被拒絕的參數，前後的 `Input Methods` 資料夾、偏好設定檔雜湊與輸入來源清單快照必須相同）。
- `scripts/test-install-ime.sh`：在暫存 `HOME` 用假 app 與 files-only 測 `install-ime.sh`：複製失敗不留東西、全新安裝不留上一版、覆蓋保留上一版、從已安裝的那份重裝、複製失敗停在 `ditto`、只裝了舊名 `shanjie.app` 的升級、兩個名稱都裝過、搬走舊名失敗、files-only 與 skip-register 並用（stdout 必須是 `files only:` 那行）、開發版與正式版並存互不影響、其他 ID 被拒、非 files-only 時正式 ID 的 ad-hoc／未簽章 bundle 被簽章檢查拒絕且什麼都沒建立、`/Library` 有一份時正式 ID 不裝進帳號層並印移除指令、使用者階段的不複製模式。

### 10.3 突變（`scripts/test-sandbox-mutations.sh`）

對正式 ID 組建的複本各做一種改動、重簽 ad-hoc，對 `check-entitlements.sh` 或 `check-app.sh`（`SKIP_RUN=1`）：原本的先要兩者都過，否則突變什麼都證明不了；每個突變要失敗在**預期的檢查與預期的訊息**，而不是別的原因（`check 2` 要先過才測 check 3）。複本用 APFS clone（`cp -Rc`，不支援時退回 `ditto`），暫存目錄先 `touch .metadata_never_index`，不啟動任何東西，結束時刪除。共 24 種，結尾印 `sandbox mutations: all 24 as expected`：

| # | 改動 | 失敗的檢查 |
|---|---|---|
| 1 | 不帶 `--preserve-metadata` 重簽（entitlements 全沒了） | `check-entitlements.sh`（沒有 entitlements） |
| 2 | 帶 `--preserve-metadata=entitlements` 重簽 | 兩者**通過** |
| 3 | 多一個 entitlement | `check-entitlements.sh`（不是剛好三個鍵） |
| 4 | 加回 Sparkle 的 `-spks` mach-lookup 例外 | 同上 |
| 5、6 | 輸入法的／善解更新的 framework 裡放一個 `.xpc` | check 3（整個 bundle 不得有 `.xpc`） |
| 7、8 | 輸入法那份 framework 裡放回 `Autoupdate`／`Updater.app` | check 3 |
| 9、10 | 善解更新那份少了 `Autoupdate`／`Updater.app` | check 3 |
| 11、12 | 善解更新／它的 `Autoupdate` 帶 entitlements | check 3 |
| 13–15 | 善解更新的 Info.plist 塞 `SUFeedURL`／`SUPublicEDKey`／`SUEnable*` | check 3 |
| 16 | 輸入法的 `SUEnableInstallerLauncherService` 改 true | check 2 |
| 17 | `Contents/Helpers` 多一個 App | check 3 |
| 18–23 | 檔案 `chmod 700`、目錄少 `o+x`、執行檔少 `o+x`、檔案 `g+w`、setuid、setgid | check 3 |
| 24 | 善解更新的 `CFBundleLocalizations` 拿掉 | check 3 |

`release.yml` 的 `verify()` 有同一套比對的字面副本，不在分支上做反向測試（`sign` 只在 main 觸發）；兩處的註解互相指向，改一處要同步改另一處。

### 10.4 虛擬機與實機項目

這些 CI 做不到，在 tart 虛擬機（macOS 26）或實機做，只量一次不重複；任何一項不成立就是對應功能的停止條件：

- **更新能不能走完**：用開發版 ID、ad-hoc、本機 feed（`http://127.0.0.1`）、測試金鑰建三個更新測試版，A0（沒有沙盒的舊版）→ B（新配置）→ C：A0 經自己的 Sparkle 更新到 B（B 接手、資料搬進 container）；B 的關於頁按「更新到 C」→ 沙盒輸入法啟動善解更新、關掉設定視窗之後它的視窗在最前面；帳號層 B → C 使用者 domain 不問密碼、C 接手、container 沿用、輸入法被結束並重新啟動；`/Library`（`root:wheel`、全部可讀）B → C 系統 domain 跳管理員密碼框、輸入密碼後成功、`root:wheel`、全部可讀、container 沿用，取消時沒有錯誤視窗、輸入法照常打字；把輸入法 bundle 複製到桌面再開裡面的善解更新，立刻結束且沒有視窗；`open -a 善解更新.app <檔>` 沒有視窗、沒有警告。
- **沙盒拒絕的允許清單**：從按下更新到新版啟動的 `log`，由腳本逐筆比對，**只允許**程序名稱是輸入法、操作是 `mach-lookup`、服務名稱整串等於 `<ID>-spks` 的那一種（輸入法裡只做探測的 Sparkle 會查它，沙盒擋下、Sparkle 當成沒有，探測與更新都不受影響）；其他任何一筆（含 `-spki`）都算失敗。`log` 會合併重複的報告、也有不記錄的規則，所以「沒有其他拒絕」只是煙霧測試，不是證明；證明靠下面的正面量測。
- **偏好設定左右不了更新**：在輸入法 container 的偏好設定與 `~/Library/Preferences/<ID>.plist` 各寫 `SUFeedURL=http://127.0.0.1:<另一個埠>/x`，啟動善解更新與按「立即檢查」：那個埠收不到任何請求、照常讀測試 feed；兩個網域都寫 `SUSkippedVersion=999`：「立即檢查」照樣顯示新版、按「更新到 X」照樣出現 Sparkle 對話框；在善解更新的對話框按「跳過這個版本」再按「更新到 X」：對話框照常出現。
- **沙盒擋不擋 `SMJobSubmit`**：對輸入法的程式套一個不進 repo 的小 patch，讓關於頁的一個按鈕直接呼叫一次 `SMJobSubmit(kSMDomainUserLaunchd, …)` 送良性工作（在 container 外建一個標記檔）。先在拿掉沙盒的組建上跑對照組，必須回傳 true 且標記檔出現；沙盒組建回傳 false、沒有標記檔、`launchctl print` 查不到才算關住。結果只作為資訊，本規格不把沙盒當成擋住已在程序內執行程式的保證（§1.3）。
- **安裝程式**：全新安裝「這台 Mac」（出廠的 `/Library/Input Methods` 擁有者與權限、有沒有「Allow … to enable "Shanjie"?」詢問、完成頁走哪個畫面、`log show` 的 `registration outcome:` 與畫面對照、登出再登入後新開的 App 能打字、背景持有安全輸入時不反灰且選單顯示學習暫停）；取消密碼框（回歡迎頁、`/Library/Input Methods` 沒有新檔含 staging）；帳號層 → 「這台 Mac」的移轉（帳號層只剩 `.shanjie-previous`、log 有強制登記、container 裡的學習檔雜湊不變、0 沙盒拒絕）；「只有我的帳號」被擋下（印移除指令、什麼都沒改）；全新「只有我的帳號」（`registration: as before`、不停在「請登出」）；「這台 Mac」同版重新安裝（成功、上一版在 `.shanjie-previous`、不要求登出）；退回（`sudo rm -rf` 兩個路徑後選「只有我的帳號」，log 有強制登記、停在「請登出」、登出後新開的 App 能打字）。「搬了沒登記」的壞狀態由三條抓到：log 讀不到 `registration: forced (location changed)`、登入後新開的 App 打不了字、系統設定看不到善解（程序路徑抓不到它，只記錄、不當判準）。
- **實機換位置兩次**（Caps Lock 在虛擬機測不了）：退回帳號層、再裝回這台 Mac，每次登出後 Caps Lock 切換仍正常；壞掉先用慢速按鍵修（系統設定 → 輔助使用 → 鍵盤打開慢速按鍵、按一次 Caps Lock、再關掉），修得回來就記下，修不回來是停止條件。
- **正式路徑**（推 tag 之後）：main 的 `appcast.xml` 有新項目，enclosure 網址等於 Release 的 zip、`length` 等於上傳的 zip 大小；該網址回 200，下載的 SHA-256 等於 `.sha256`；已安裝的新版在關於頁「立即檢查」顯示「已是最新」而不是「檢查失敗」（證明出貨的版本讀得到正式 feed、驗得過 feed 簽章）。第一次真正的更新（上一版 → 這一版）在下一次發版時實機看：`/Library` 與 `~/Library` 兩條都過，container 裡的學習資料還在，`log` 沒有允許清單以外的沙盒拒絕；兩條都過之前不發別的版本。
- **更新測試版的其他量測**：背景檢查查到新版時沒有彈出任何視窗；把測試 feed 改一個字（簽章失效）檢查要被拒、不顯示新版；錄下檢查與下載的實際請求（目的主機、標頭）；Caps Lock 切換仍有效（Sparkle 不呼叫 TIS 註冊）；更新前就開著的 App 能不能繼續打字、要不要重開。

## 11. 已知限制

- 輸入法程序裡仍有 Sparkle（XML 與簽章解析）在看得到按鍵的程序裡；`network.client` 不變，沙盒擋不住把輸入內容送出去。
- ad-hoc 的更新測試版，Sparkle 不驗呼叫者的 Team，和正式版不同；Developer ID 簽章下兩條 domain（系統與使用者）的更新，要在發版後實機看（§10.4）。
- 多使用者 `/Library` 的其他已登入帳號，舊程序要到登出或重新載入才換〔推論〕。
- 家目錄路徑含空白的 Homebrew 帳號，升級後不會自動結束舊程序（§8）。
- 標準帳號（非管理員）無法更新 `/Library` 那份，也無法安裝「這台 Mac」；沒有自動偵測，只靠說明文字。
- 密碼框裡的「善解更新 wants permission to update 善解輸入法.」是 Sparkle 寫死的英文，不翻譯。
- 選「這台 Mac」時，安全輸入期間按鍵會經過善解（學習暫停、不讀前文、不記錄）；終端機開著「加密的鍵盤輸入」時，`sudo` 的密碼提示也一樣會經過善解。系統的密碼欄（`NSSecureTextField`）應該強制 ASCII、不經輸入法〔沒量〕，但不是密碼欄的地方不受這條保護。所有會讀取或留存使用者文字的功能都要經過 `learningPause`。
- Space（換到別的桌面）時 Sparkle 的視窗叫不回來：設定視窗本來就有同樣的限制，沒有實測。
- `ci.yml` 的 Step 8 註解仍寫「ten changed copies」，實際是 24 種突變（§10.3）。
- 沒有 beta 頻道與差分更新（delta）；詞庫與模型不能獨立於 App 更新。
- 通知的授權詢問沒回答、輸入法程序就結束時，系統記成拒絕，之後不再詢問；這樣的使用者要從關於頁的「打開通知設定」或系統設定自己打開（§3.8）。
