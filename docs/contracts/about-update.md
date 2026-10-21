# 契約：「關於」頁與 Sparkle 自動更新（about-update）

狀態：修訂二（2026-10-16；修訂一照 security-reviewer 的審查改寫信任模型、Info.plist、發版流程與驗收；修訂二處理 plan-verifier 的 REVISE：下載網址、正式路徑的驗收、Homebrew 舊使用者，見 §7）。實作時的修訂（2026-10-16，維護者經 main 同意第一項）：ad-hoc 建置不加 hardened runtime，原因是加了之後 library validation 拒絕載入 ad-hoc 的 `Sparkle.framework`（dyld：「mapping process and mapped file (non-platform) have different Team IDs」，`--selftest` 實測）；正式版四處都要 runtime，`verify()` 強制。更新服務用 `startingUpdater: false` 再自己呼叫 `start()`：Sparkle 2.9.3 的 controller 自己啟動失敗時會跳 `runModal` 的警告，會卡住輸入法。修訂三（2026-10-16，實作的 /code-review 15 項全部採納，見 §7）：信任模型更正為實際上只有 EdDSA（zip 沒有程式碼簽章可讓 Developer ID 的備援成立），輪換手冊改寫；按「更新到 X」先靜默檢查再開互動流程；本機建置不啟動更新服務（Info.plist `ShanjieUpdateBuild`）；Sparkle 的 MIT 授權隨 App 附上；feed 嵌入發版說明、保留所有版本、只檢查新項目；CI 關卡跳過只改 `appcast.xml` 的 commit；網站隱私段草稿移到附錄 A。

## 0. 依據

- 維護者 2026-10-16 的選擇題與訊息：
  - 版本號放在新的「關於」頁（wclinrd 在 Discord 回饋串建議設定裡顯示版本）。
  - 「把自動更新也做上去」→ 用 Sparkle、照 Syrtis（維護者的另一個專案）的做法（`Sources/Syrtis/UpdaterService.swift`、`scripts/bundle.sh`、`scripts/make_appcast.sh`、`.github/workflows/release.yml` 的 `SPARKLE_PRIVATE_KEY`、cask `auto_updates true`）。
  - 自動檢查預設開、每天最多一次。
  - Homebrew 照 Syrtis 的方式相容（cask 加 `auto_updates true`）。
  - 進 v0.4.7；詞庫與模型分開更新是之後另一片（範圍外）。
- 取代既有決定：`docs/PLAN.md` §7 R9「v1 不做自動安裝（只檢查版本開瀏覽器）」與 §1「非目標：自動更新」，由上面的選擇題取代；同一個 PR 改 PLAN 的這兩處並註明。
- 公開承諾：`site/index.html` 的隱私段寫「目前版本沒有連網功能。之後如果加入更新檢查……會在上線前寫在這一頁：什麼時候連線、送出什麼、能不能關閉。」〔修訂三〕這個 PR 不改網站；發版時照實機錄下的請求（驗收 4(f)）定稿，草稿在附錄 A，在 v0.4.7 上線前部署（網站部署要維護者當下同意）。
- 設計分析：Fable（2026-10-16），數字與檔案位置由 main 核對。
- 安全審查：security-reviewer（2026-10-16，對照 Sparkle 2.9.3 原始碼），無 P0；處置見 §7。

## 0.1 信任模型（寫明，不迴避）

- 〔修訂三更正〕開了 `SUVerifyUpdateBeforeExtraction` 之後，Sparkle 2.9.3 在解壓前驗下載的壓縮檔：先驗 EdDSA 簽章；失敗時的備援是驗**壓縮檔本身**的 Developer ID 簽章是否和舊版同一個 Team（`SUUpdateValidator.m` 的 `validateDownloadPathWithFallbackOnCodeSigning`、`SUCodeSigningVerifier.m` 的 `codeSignatureIsValidAtDownloadURL`）。我們發的是 zip，zip 本身帶不了程式碼簽章，這條備援不會成立，所以**實際上只有 EdDSA 能讓更新被接受**。EdDSA 通過之後，解壓出來的 App 只要有有效的程式碼簽章就行（不比對 Team，ad-hoc 也可以，`passesBasicUpdatePolicy` 只禁止拿掉簽章或拿掉 `SUPublicEDKey`），安裝時拿掉隔離屬性，Gatekeeper 與公證不會再看這份更新。所以：**持有 EdDSA 私鑰，再加上 main 的 `appcast.xml` 的寫入權，就能在所有使用者看得到按鍵的程序裡執行程式。** Developer ID 憑證單獨推不了更新，但 EdDSA 私鑰單獨（加上 feed）就夠。
- 降低的方法：feed 本身也要 EdDSA 簽章（`SURequireSignedFeed`），沒有私鑰就改不了更新內容與說明；解壓前驗（`SUVerifyUpdateBeforeExtraction`）；只有使用者按下才裝（`SUAllowsAutomaticUpdates` false）；私鑰只在 sign 工作的三個步驟出現（簽章材料的非空檢查、乾跑與推 tag 都跑的金鑰成對檢查、只在推 tag 時跑的 feed 簽章），簽東西只在最後那一步、只經 stdin 交給 Sparkle 自己的工具；降版由 Sparkle 以實際 bundle 的 `CFBundleVersion` 擋下。
- 〔2026-10-20，`standalone-updater.md` §3〕v0.5.0 起更新由沙盒外的「善解更新」做。裝在 `/Library` 時，持有 EdDSA 私鑰與 feed 寫入權的人另外能讓 Sparkle 的 `Autoupdate` **以 root 換檔**（系統 domain），換完的輸入法仍以使用者身分執行。善解更新只接受 `application` 類型的更新（套件安裝類型丟錯誤），所以拿不到「以 root 跑 `installer`」這條路。
- `docs/PLAN.md` §7 加一條新的 R 規則（R10）取代 R9 的「v1 不做自動安裝」，內容是上面這段與 §2 的設定；R9 的其他部分（hardened runtime、無 entitlements、不需 root）不變。

## 1. 要做到什麼、怎麼看到

- **「關於」頁**：設定視窗左欄多一頁「關於」（`SettingsPage` 加一個 case，最後一個），內容：
  - 版本：`CFBundleShortVersionString`；`0.0.0`（dispatch 乾跑與本機開發）顯示「開發版」。
  - 「自動檢查更新」開關（預設開）、「立即檢查」按鈕、上次檢查的日期與結果（已是最新／有新版本 X／檢查失敗）。
  - 有新版本時一顆「更新到 X」按鈕：按下才走 Sparkle 的互動流程（下載進度、更新說明、安裝並重新啟動）。
  - 網站連結、一行授權說明（程式 Apache-2.0、內含的 Sparkle 是 MIT、詞庫與模型見 `Resources/LICENSES`）。
- **輸入選單**：有新版本時多一項「有新版本 X，按此更新…」，按下同樣走互動流程；沒有新版本時不出現；檢查進行中反灰。
- 〔修訂三〕按「更新到 X」或選單項時，先跑一次靜默的 `checkForUpdateInformation`，確認還有更新的版本才開 Sparkle 的互動流程；沒有（已是最新）或檢查失敗就只更新關於頁與選單的狀態，不出現 Sparkle 的警告視窗。使用者進了 Sparkle 的流程之後，下載或安裝出錯時 Sparkle 自己的介面仍可能跳模態警告（驗收 4(h)）。
- 〔實機修正，2026-10-16〕流程進行中，Sparkle 的視窗和設定視窗同層（`.floating`），浮在一般視窗之上；流程已在進行時按「更新到 X」或選單項，走 Sparkle 自己的「把進行中的更新叫到前面」，再把它的視窗（含最小化的）叫回最前面。原因：輸入法沒有 Dock 圖示，macOS 又會擋掉它切到前景的要求，Sparkle 的視窗原本開在設定視窗後面、切到別的 App 之後就叫不回來（驗收 4 實機發現）。
- 〔2026-10-20，`standalone-updater.md` §2.2、§2.3〕「更新到 X」與選單項確認有新版之後，先關掉設定視窗，再用 `NSWorkspace.openApplication` 啟動 `Contents/Helpers/善解更新.app`（`activates = true`、不帶參數），由它顯示 Sparkle 的對話框、下載與安裝。上一點「把 Sparkle 的視窗叫回最前面」的做法刪掉：輸入法不再開 Sparkle 的視窗；流程進行中再按，就再啟動一次善解更新（已在執行時系統只把它叫到前面）。輸入法本身只做 `checkForUpdateInformation`。「更新」區塊多一段依安裝位置的說明（只看 bundle 的上層是不是 `/Library/Input Methods`）：
  - `/Library`：「善解裝給這台 Mac 的所有帳號。按更新後，善解更新會先下載並驗證，再跳出系統的密碼框，請你輸入密碼來允許更新；框裡有一行說明固定是英文：『善解更新 wants permission to update 善解輸入法.』。善解只會在你按下這裡的『更新』之後才要密碼。不是管理員的帳號，請管理員更新。」（2026-10-22 在繁體中文的虛擬機量：密碼框裡系統自己的字翻成中文，這一行是 Sparkle 寫死的英文、不翻譯，`SUInstallerLauncher.m` 的註解寫明刻意不做在地化）
  - `~/Library`（只出 U1 時的版本，結束在這裡；「要改裝給整台 Mac……」那一句等 U2）：「善解只裝在你的帳號，更新不需要密碼。有程式開著安全輸入時，系統會暫時停用善解。」
  - 設定視窗目前只有中文，所以 App 裡不顯示英文。英文版草稿（跟 `system-install.md` 一起定稿）：/Library “Shanjie is installed for every account on this Mac. When you update, 善解更新 downloads and verifies the update, then the system asks for an administrator password to allow the update; one line in that dialog is always English: ‘善解更新 wants permission to update 善解輸入法.’ Shanjie asks for a password only after you press Update here. On an account that is not an administrator, ask an administrator to update.”；~/Library “Shanjie is installed for your account only; updates need no password. While another app holds Secure Keyboard Entry, the system disables Shanjie.”
- 〔修訂三〕只有正式版（release workflow 建的）與更新測試版會啟動更新服務；本機用 `build-app.sh`、`install-ime.sh` 裝的版本永遠不讀正式 feed（Info.plist `ShanjieUpdateBuild`：`release`、`test`、`local`）。
- **背景檢查不彈窗**：輸入法程序在使用者打字時跳出視窗會搶焦點，所以 Sparkle 自己的排程關掉（`SUEnableAutomaticChecks` false），改由我們每天最多一次呼叫 `checkForUpdateInformation`（只查資訊、不顯示任何視窗，Syrtis 的做法）；查到新版只更新關於頁與選單，不主動開視窗。開關關掉就不做背景檢查；「立即檢查」不受開關影響。
- 怎麼看到：
  - 使用者：設定→關於看得到版本；有新版時選單與關於頁出現更新項，按下看到 Sparkle 的下載進度與說明，裝完輸入法照常能打字。
  - 開發者：`scripts/check-app.sh` 驗得到 Sparkle 已簽章、`SUPublicEDKey` 與 `SUFeedURL` 在 Info.plist、XPC 服務已移除、沒有 entitlements（〔2026-10-19，`app-sandbox.md` §2.8〕改成只留 `Installer.xpc`、App 帶四個沙盒 entitlements，見 §3 第 2 項）；release 產出 `appcast.xml` 並推到 main。

## 2. 實作

- **相依**：`macos/Package.swift` 加 Sparkle，`exact: "2.9.3"`（2.9.0 起才有簽 feed；2.9.2 修了兩個安全問題）。〔2026-10-19，`app-sandbox.md` §2.8〕升到 `exact: "2.10.0"`（2.9.5、2.9.6 修了三則安全公告），`release.yml` 的 `generate_appcast` tarball 與 `LICENSES/Sparkle-MIT.txt` 跟著 2.10.0（授權原文和 2.9.3 逐位元組相同）。用官方預編的 framework（二進位由 Sparkle 的 `Package.swift` 內的 checksum 釘住；Syrtis 自己編是為了改 App 名稱，這裡不需要）。`macos/Package.resolved` 進版控，`build-app.sh` 與 CI 用 `--force-resolved-versions`。這是 `macos/Package.swift` 第一個外部相依。
- **Info.plist**（`scripts/build-app.sh`，正式版的值寫死在腳本裡，不從環境變數覆寫）：`SUFeedURL` = `https://raw.githubusercontent.com/Nanako0129/shanjie/main/appcast.xml`；`SUPublicEDKey`（維護者產生的公鑰）；`SURequireSignedFeed` true；`SUVerifyUpdateBeforeExtraction` true；`SUAllowsAutomaticUpdates` false；`SUEnableAutomaticChecks` false；`SUEnableInstallerLauncherService` false（〔2026-10-19，`app-sandbox.md` §2.8〕改為 true：輸入法進了沙盒，安裝要經 `Installer.xpc`；`SUEnableDownloaderService` 不設，網路由 App 的 `network.client` 走）；`SUEnableSystemProfiling` false；不設 `SUEnableJavaScript`。版本比對用 `CFBundleVersion`（現在和 `CFBundleShortVersionString` 同值）。〔修訂三〕另加 `ShanjieUpdateBuild`：`release` 只在 `release.yml` 的 build 工作（`SHANJIE_RELEASE_BUILD=1`），`test` 是更新測試版，其他一律 `local`；更新服務只在前兩者啟動，`check-app.sh` 與 `verify()` 檢查它的值。測試用的 feed 與公鑰（驗收 4）由另一個只在本機用的打包選項產生，正式版的檢查（下面）會擋下任何不同的值。
- 〔2026-10-20，`standalone-updater.md` §2.1、§2.2、§2.4〕輸入法的 `SUEnableInstallerLauncherService` 改回 false，整個 bundle 沒有 XPC 服務，entitlements 三個（沒有 Sparkle 的 mach-lookup）。輸入法與善解更新都由 delegate 的 `feedURLString(for:)` 把 feed 釘在輸入法 Info.plist 的 `SUFeedURL`（不經 user defaults），每次檢查前刪掉自己讀的網域裡的 `SUSkippedVersion`、`SUSkippedMajorVersion`、`SUSkippedMajorSubreleaseVersion`。善解更新的 Info.plist 沒有 `SUFeedURL`、`SUPublicEDKey`、任何 `SUEnable*` 鍵，bundle ID 是 `<輸入法 ID>.updater`。下面打包與簽章的現行規格是：輸入法那份 framework（`Contents/Frameworks/Updater/`）刪掉 `XPCServices`、`Autoupdate`、`Updater.app`；善解更新在 `Contents/Helpers/善解更新.app`，它自己的 `Contents/Frameworks/Sparkle.framework` 只刪 `XPCServices`；簽章前 `chmod -R a+rX,go-w`；由內而外：善解更新那份的 `Autoupdate`、`Updater.app`、framework → 善解更新 → 輸入法那份 framework → 輸入法（只有它帶 entitlements）。`release.yml` 同一個順序，解開建置產物用 `tar -xpf`。下面兩段裡修訂二、三的 `Installer.xpc`、四個 entitlements 與資料夾規則都是歷史。
- **打包**：`build-app.sh` 把 `Sparkle.framework` 放進 `Contents/Frameworks`、移除 `XPCServices`（不在沙盒，Sparkle 文件允許；照 Syrtis 的 `bundle.sh`；〔2026-10-19，`app-sandbox.md` §2.8〕改成只刪 `Downloader.xpc`、保留 `Installer.xpc`，`XPCServices` 裡只能有它；App 帶四個沙盒 entitlements，Sparkle 的五個程式碼位置裡除了 App 都沒有；〔2026-10-19 晚上，`app-sandbox.md` 修訂三〕framework 改放 `Contents/Frameworks/Updater/`，主程式的 rpath 跟著改，原因見該契約 §1、§2.8），本機仍是 ad-hoc 簽章（〔實作修訂〕由內而外、不加 hardened runtime，見狀態列）。正式的 Developer ID 簽章由內而外（`Sparkle.framework/Versions/B/Autoupdate`、`Versions/B/Updater.app`、framework 本身，再簽 App；hardened runtime、timestamp、沒有 entitlements）寫在 `release.yml` 的 sign 步驟裡（〔2026-10-19〕最內層多簽 `Installer.xpc`，App 改用 workflow 裡以字面值寫的四個 entitlements 簽），不呼叫 repo 的腳本（sign 工作不跑 repo 的程式，維持這個邊界；〔修訂三〕它唯一的 checkout 是推 tag 時 sparse checkout 兩個資料檔：`appcast.xml` 與這個 tag 的發版說明，只讀不執行）。〔修訂三〕`LICENSES/Sparkle-MIT.txt`（Sparkle 2.9.3 的 `LICENSE` 原文，含它附帶的第三方授權）隨 App 放進 `Resources/LICENSES`，`check-app.sh` 比對和 repo 的那份相同。
- **更新服務**（新檔，例如 `macos/Sources/Shanjie/Updater.swift`）：照 Syrtis 的 `UpdaterService`：只在從 `.app` 執行、而且 `ShanjieUpdateBuild` 是 `release` 或 `test` 時啟動（測試、`swift test` 與本機建置不啟動）；`SPUStandardUpdaterController`（〔實作修訂〕`startingUpdater: false`，由更新服務呼叫 `start()` 並接住錯誤，見狀態列）；背景檢查的排程與開關存在 `UserDefaults`（鍵名寫在契約實作裡）；`didFindValidUpdate` 記下可用版本，一輪檢查結束（`didFinishUpdateCycle`）時沒有錯誤就是有新版、`SUNoUpdateError` 就清掉、其他錯誤是檢查失敗（失敗不清掉已知的版本）；版本與狀態給關於頁和選單用。
- **release**（`.github/workflows/release.yml`）：
  - 正式 zip 的檢查（`verify()`，對解壓後的正式 zip）：App、`Sparkle.framework`、`Versions/B/Autoupdate`、`Versions/B/Updater.app` 四者各自沒有 entitlements、TeamIdentifier 都是 `2LJ882GPY8`（路徑不存在就失敗）；`XPCServices` 不存在；〔2026-10-19，`app-sandbox.md` §2.4〕改成五個位置（加 `Installer.xpc`）都有 Team 與 runtime，除了 App 都沒有 entitlements，App 的 entitlements 整份等於四個沙盒鍵；`XPCServices` 只有 `Installer.xpc`；`SUEnableInstallerLauncherService` 是 true、`SUEnableDownloaderService` 不存在；Info.plist 的 `SUFeedURL`、`SUPublicEDKey`、`SURequireSignedFeed`、`SUVerifyUpdateBeforeExtraction`、`SUAllowsAutomaticUpdates`、`SUEnableSystemProfiling` 逐字比對寫在 workflow 裡的值。`scripts/check-app.sh` 同樣逐字比對（本機測試版另有一套預期值）。
  - EdDSA 是獨立的一步，放在「移除簽章用的 keychain」之後，條件 `github.event_name == 'push'`（只有推 tag）：工具用 Sparkle 官方 release 的 tarball（版本與 `Package.resolved` 相同，SHA-256 以字面值寫在 workflow 裡）；舊的 `appcast.xml` 用 sparse checkout 只取這個檔、當資料讀，不執行 repo 的程式；私鑰從 stdin 交給 Sparkle 的工具（`--ed-key-file -`），不寫進檔案。`generate_appcast` 產生多項目的 `appcast.xml`（保留舊項目、App 有 `SURequireSignedFeed` 時 feed 自動簽章），對象是要上傳到 Release 的 `shanjie-<V>.zip`（不新增資產）：輸入資料夾只放這一個 zip（和 publish 上傳的是同一份位元組，不放安裝程式的 zip），並給 `--download-url-prefix https://github.com/Nanako0129/shanjie/releases/download/v<V>/`（不給的話網址會從 `SUFeedURL` 的資料夾拼出來，指到不存在的檔案）。〔修訂三〕輸入資料夾另放這個 tag 的發版說明（`docs/releases/v<V>.md`，gate 本來就要求它存在；sparse checkout 當資料讀）改名成 `shanjie-<V>.md`，加 `--embed-release-notes` 嵌進新項目（markdown，Sparkle 用文字檢視顯示，沒有網頁檢視、遠端資源或 JavaScript）；`--maximum-versions 0`（2.9.3 原始碼：0 代表保留所有版本）與 `--versions <V>`，替舊版本線補發的版本不會被刪掉或略過。產生後只檢查新項目：恰好一個 enclosure 網址等於那個 zip 的項目、`length` 等於 zip 大小、有 EdDSA 簽章、版本正確、有嵌入的說明；整份 feed 有簽章區塊。
  - 金鑰成對檢查（簽章鑰匙圈刪掉之後）：乾跑與推 tag 都用 workflow 內嵌的 CryptoKit（`Curve25519.Signing.PrivateKey(rawRepresentation:)`，不是 repo 的程式）從 secret 推出公鑰，和正式 zip 裡 Info.plist 的 `SUPublicEDKey` 比對，不同就失敗。乾跑不用正式金鑰簽任何東西。
  - publish 工作把 `appcast.xml` commit 到 main（照 Syrtis：推之前確認 main 的 `appcast.xml` 沒有在這期間被改過；寫入用 contents API 帶 main 目前的 blob SHA）。〔修訂三〕這個 commit 由 `GITHUB_TOKEN` 做，不會觸發 `ci.yml`；`scripts/check-ci-gate.sh` 往回跳過「對唯一的父 commit 只改了 `appcast.xml`」的 commit（最多 20 個），用第一個別的 commit 的 CI 結果，`scripts/test-check-ci-gate.sh` 測這件事。
- **Homebrew**：`packaging/Casks/shanjie.rb` 加 `auto_updates true`（brew 不再主動升級，交給 Sparkle）。
- **隱私與文件**：`site/index.html` 隱私段照實機錄下的請求改寫（驗收 4(f)；〔修訂三〕發版時定稿，草稿在附錄 A，這個 PR 不改網站），至少寫明：每天最多一次、連到 GitHub（`raw.githubusercontent.com`）讀 `appcast.xml`；GitHub 會看到你的 IP 與請求標頭（User-Agent 是「App 名稱/版本 Sparkle/版本」，其他標頭以實測為準）；沒有任何輸入內容、選字記憶或系統資訊；在設定→關於可以關掉自動檢查，「立即檢查」按下時一定會連線；按「更新到 X」時從 GitHub Releases 下載；安裝前驗證 EdDSA 簽章，正式版另經 Apple 公證（公證不寫成驗證項目）。README 對應段落、`docs/PLAN.md`（R9 → R10、非目標、S8）、`docs/contracts/settings-sidebar.md`（加「關於」頁）同 PR 更新。
- **日誌與網路設定**：`Updater.swift` 照 R2，只記靜態字串，不內插版本、網址或錯誤。Sparkle 自己的 log 用 `%{public}` 並寫到磁碟（內容是路徑、網址、錯誤，沒有輸入內容），寫明這是 R2 的例外與理由。R7 的 ephemeral 設定不套用到更新管道（Sparkle 的下載用預設 session），也寫明。
- **金鑰**：EdDSA 金鑰由維護者在自己的終端機產生（Sparkle 的 `generate_keys`），私鑰放進 GitHub 的 release Environment secret `SPARKLE_PRIVATE_KEY`，公鑰寫進 `build-app.sh`。agent 不碰鑰匙圈、不經手私鑰；main 準備好指令交給維護者。

## 3. 驗收

1. 單元測試（Swift，不連網）：關於頁的版本字串（一般版本、`0.0.0` 顯示開發版）；背景檢查的決策（開關關 → 不檢查；距上次不到一天 → 不檢查；超過 → 檢查）；選單在有可用版本時才有更新項；更新服務在非 `.app` 執行時不啟動。突變：開關關掉仍檢查、選單無條件顯示更新項，各要讓測試以斷言失敗。〔修訂三〕另測：按更新先靜默檢查、確認有新版才開互動流程；檢查中選單項反灰、再按被忽略；更新服務起不來時「立即檢查」記下日期並顯示檢查失敗；一輪結束沒帶版本不清掉已知版本；`local` 與沒有標記的 App 不啟動更新服務。〔實機修正〕流程進行中再按更新只叫回 Sparkle 的視窗、不開新的檢查。各有對應的突變。
2. `scripts/check-app.sh` 在本機建出的 App 上通過：Sparkle framework 在、簽章有效、XPC 服務不在、Info.plist 的 Sparkle 鍵逐字等於正式值；四個程式碼位置都沒有 entitlements。突變：把 `SURequireSignedFeed` 拿掉或改值，檢查要失敗。〔2026-10-19，`app-sandbox.md` §2.8〕改成：`XPCServices` 只有 `Installer.xpc`、`SUEnableInstallerLauncherService` 是 true、`SUEnableDownloaderService` 不存在；Sparkle 的四個程式碼位置（`Installer.xpc`、`Autoupdate`、`Updater.app`、framework）沒有 entitlements，App 本身是四個沙盒 entitlements（`check-entitlements.sh`）。〔2026-10-19 晚上，`app-sandbox.md` 修訂三〕另外：`Contents/Frameworks` 剛好是 `Updater/Sparkle.framework`。突變照 `app-sandbox.md` §2.6 第 8 步。
   〔2026-10-20，`standalone-updater.md` §4.1〕單元測試另有：「更新到 X」先關設定視窗、再啟動善解更新（流程進行中再按也一樣）；善解更新的路徑判斷（兩個位置 × 兩個名稱、往上三層、符號連結、container 與桌面的副本、家目錄字串的邊界）、組建類型、只放行 `application` 類型、feed 只讀 Info.plist、刪掉三個跳過鍵；第 2 項的 `check-app.sh` 斷言與突變改照 `standalone-updater.md` §2.4（24 種）。實機與虛擬機的驗收照那份 §4.2–§4.4。
3. release 乾跑（`workflow_dispatch`）：建置、簽章（含 Sparkle 內層）、公證、`verify()` 通過；金鑰成對檢查通過；不簽 appcast、不發佈任何東西。
4. **實機（決定性的一項，維護者與 main）**：
   - 測試用的 feed 與一把測試金鑰（不用正式金鑰）：本機先裝一個測試版 A，再提供一個測試版 B（版本較新）的 appcast。本機 feed 用 `http://127.0.0.1:埠/`：Sparkle 2.9.3 拒絕 `file://`（「The download request URL must use http or https」，實測），所以 `SHANJIE_UPDATE_TEST_FEED` 只收 `https://` 或 `http://127.0.0.1`；ATS 不擋 http 連 127.0.0.1，不用任何例外（實測，見研究紀錄）。結果（2026-10-16）：(a)–(h) 通過，見研究紀錄。
   - 在關於頁按「立即檢查」→ 出現「更新到 B」→ 按下 → Sparkle 顯示下載進度與說明 → 安裝並重新啟動。
   - 量：(a) 安裝後輸入法是 B（關於頁版本、`ps` 的程序路徑）；(b) 在 TextEdit 照常打得出字；(c) Caps Lock 切換輸入法仍然有效（之前的已知問題：換 bundle 後再註冊會壞；Sparkle 不呼叫 TIS 註冊，預期不會壞）；(d) 更新前就開著的 Discord、終端機、TextEdit、Safari，各自能不能繼續打字、要不要重開（2026-10-15 的「舊連線」問題，結果寫進研究紀錄）；(e) 背景檢查查到新版時沒有彈出任何視窗；(f) 錄下檢查與下載的實際請求（目的主機、標頭），網站隱私段照這份寫；(g) 把測試 feed 改一個字（簽章失效），檢查要被拒、不顯示新版；(h) 使用者按下更新之後，下載或安裝出錯時 Sparkle 自己的警告視窗是否出現在最前面、按掉之後輸入法照常打字（模態視窗期間輸入法程序的主執行緒被佔住）。
   - 這一項不通過（例如 B 沒有接手、Caps Lock 壞掉、需要登出）→ 停，回報維護者再決定（例如更新後由輸入法自己呼叫既有的安裝流程）。
5. Homebrew：cask 有 `auto_updates true`。v0.4.6 以前用 brew 裝的人還沒有 Sparkle，一般的 `brew upgrade` 可能略過有 `auto_updates` 的 cask，所以 v0.4.7 的發版說明與 README 的 Homebrew 段寫明一次性的 `brew upgrade --cask nanako0129/tap/shanjie`（指名升級）。只報：在 tart 的測試 VM 從 tap 裝 0.4.6、換成有 `auto_updates true` 的 cask 後，一般的 `brew upgrade` 實際做了什麼，寫進研究紀錄。
6. **正式路徑（推 v0.4.7 tag 之後，維護者與 main）**：(a) main 的 `appcast.xml` 有 v0.4.7 的項目，enclosure 網址等於 `https://github.com/Nanako0129/shanjie/releases/download/v0.4.7/shanjie-0.4.7.zip`、`length` 等於上傳的 zip 大小；(b) 這個網址回 200，下載下來的 SHA-256 等於 `shanjie-0.4.7.zip.sha256`；(c) 已安裝的 v0.4.7 在設定→關於按「立即檢查」顯示「已是最新」，不是「檢查失敗」（證明出貨的版本讀得到正式 feed、驗得過 feed 簽章）。真正的第一次更新（0.4.7 → 0.4.8）在下一次發版時實機看。

## 4. 停止條件

- 第 4 項實機不通過。
- 第 6 項任一點不通過（正式 feed、下載網址或簽章有問題，所有 v0.4.7 使用者會更新不了）：當場修 appcast 或 workflow，並回報維護者。
- Sparkle 的簽章或公證在 release 乾跑失敗、而且原因不是設定錯誤。
- 需要 entitlements 才能運作（例如網路）——現在不在沙盒，預期不需要；需要就停下來問。

## 5. 範圍外

- 詞庫與模型分開更新（下一片，維護者 2026-10-16）。
- App 沙盒（v0.5.0）：開沙盒時 Sparkle 要加回 XPC 服務與 `network.client`，那一片處理。〔2026-10-19〕已由 `app-sandbox.md` §2.8 處理：只加回 `Installer.xpc`，網路用 App 的 `network.client`，不用 `Downloader.xpc`。沙盒是縱深防禦，不是對所有攻擊者都成立的硬邊界，也擋不住輸入內容送出去。
- beta 頻道、差分更新（delta）。
- 關於頁顯示模型名稱（通語 SJ-LM 等）。

## 6. 限制與回滾

- executor：不連網（不下載 Sparkle 以外的東西；Sparkle 由 SwiftPM 解析時下載算在允許內）、不跑 Windows 機器；不讀 `eval/holdout`、repo 外的私人資料、學習檔、`~/Library`、鑰匙圈（不產生、不讀、不改任何金鑰）；不安裝、不啟動 App；不 push、不開 PR、不碰其他 worktree；不改 GitHub secret 或 Environment。
- 回滾：revert 這個 PR（Sparkle、關於頁、release 步驟一起）。已經裝了帶 Sparkle 的版本的使用者：Sparkle 拒絕「新版拿掉 `SUPublicEDKey`」的更新（只允許輪換、不允許移除），所以之後要拿掉 Sparkle 的那一版仍要保留 `SUPublicEDKey`、用目前的 EdDSA 私鑰簽，並保留有效的程式碼簽章；或者讓 appcast 不再有新項目，使用者改用安裝程式更新（cask 是 `auto_updates true`，`brew upgrade` 不會主動升級）。
- 金鑰外洩的處理寫進 `docs/releases/README.md`（第一次發帶 Sparkle 的版本前寫好）。〔修訂三更正〕輪換不能走 Developer ID（zip 不帶程式碼簽章，見 §0.1）：輪換那一版用**舊**私鑰簽壓縮檔與 feed、App 裡帶**新**公鑰，之後的版本才用新私鑰；舊私鑰遺失時 Sparkle 推不了任何更新，只能請使用者手動重裝。EdDSA 沒有撤銷，還沒更新的使用者在攻擊者同時有舊私鑰與 feed 寫入權時仍有風險。
- agent 不核准 release Environment 的部署、不推 `v*` tag（推 tag 本來就要維護者當下同意）。

## 7. 審查紀錄

| 輪次 | 結果 | 處置 |
|---|---|---|
| 實機修正 64395ec6 的 /code-review（2026-10-16） | 14 項：最小化的 Sparkle 視窗叫不回來；隱私段標頭是本機 HTTP/1.1 量的、正式走 HTTPS；層級寫死、沒和設定視窗共用；`.busy` 的說明三處沒跟上、自己的檢查中再按沒測；ATS 例外沒實測過；check-app.sh 的 ATS 檢查不夠嚴；「沒有每個視窗的呼叫」說法不準；focus 重做了 Sparkle 的 `showUpdateInFocus`；整個流程每秒輪詢四次；`hidesOnDeactivate` 沒作用；focus 裡多餘的 `watch()`；輪詢間隔沒命名；換到別的桌面（Space）叫不回來；研究紀錄 (c) 把推論寫成事實 | FIX 11 項：focus 改走 Sparkle 的 `showUpdateInFocus` 再提層級、含最小化視窗；層級共用 `SettingsWindowController.level`；`install` 在自己的檢查中直接略過並測；ATS 實測後整段刪掉（127.0.0.1 不擋，對照 http://example.com 是 -1022），check-app.sh 的分支一起刪；隱私段標頭改標〔發版後核對〕；註解與命名常數；(c) 改引 Sparkle 原始碼。DEFER 2 項：輪詢（成本是每 0.25 秒掃一次自己的幾個視窗；要拿掉得換掉 `SPUStandardUpdaterController`）；Space（設定視窗本來就有同樣的限制，沒有實測，記成已知限制）。〔ATS 檢查不夠嚴那項隨 ATS 刪除消失〕 |
| 實作的 /code-review（修訂三，2026-10-16） | 15 項：Developer ID 輪換對 zip 不成立；沒附 Sparkle 授權；按更新直接開互動流程會跳 Sparkle 的警告；檢查中選單可按、清掉 `found` 的時機；起不來時「立即檢查」沒日期、檢查中再按算失敗；appcast commit 沒有 CI 會卡下一次發版的關卡；`generate_appcast` 預設只留 3 版；網站在 PR 裡先改；feed 檢查看整份而不是新項目；feed 沒有發版說明；追蹤檔案裡的私人路徑；sign 工作與金鑰出現位置的描述不一致；本機建置會讀正式 feed；`settings-sidebar.md` 量法的分類數；多餘的公鑰比對 | 全部 FIX，見狀態列與 §0.1、§1、§2、§3、§6、附錄 A |
| plan-verifier（修訂二） | READY | — |
| plan-verifier（修訂一） | REVISE 3 項：`generate_appcast` 沒給下載網址前綴（會拼出不存在的網址）；沒有任何驗收走到正式的 feed、金鑰與下載網址；v0.4.6 以前的 Homebrew 使用者在 cask 加 `auto_updates` 後可能永遠升不到 v0.4.7 | 都 FIX：§2 寫明 `--download-url-prefix` 與輸入資料夾只放那個 zip；§3 加第 6 項（推 tag 後核對 appcast、下載網址、已安裝版本「立即檢查」顯示已是最新）並列入停止條件；第 5 項改成發版說明與 README 一律寫一次性的指名升級指令，VM 實測只報 |
| security-reviewer（草稿） | 無 P0。P1：EdDSA 或 Developer ID 任一加 feed 寫入權即可推更新且繞過公證，契約沒寫；發版設計和 sign 工作的邊界衝突（巢狀程式只有 ad-hoc、工具與舊 appcast 的來源）。P2：feed 未簽可釣魚／外洩 IP、檢查只看外層 App 與本機版、回滾方式會被 Sparkle 拒絕、乾跑用正式金鑰且沒驗公私鑰成對、隱私文字三處不準確。P3：Sparkle 的 log 與 R2、Package.resolved、agent 不核准部署、外洩處理手冊 | 全部 FIX：§0.1 信任模型與 PLAN R10；Info.plist 加 `SURequireSignedFeed`、`SUVerifyUpdateBeforeExtraction`、`SUAllowsAutomaticUpdates` false，Sparkle `exact: 2.9.3`；內層 Developer ID 簽章寫在 sign 步驟；EdDSA 獨立一步、只在推 tag、官方 tarball 釘雜湊、stdin 給私鑰；`verify()` 逐一檢查四個程式碼位置與 Info.plist 逐字；乾跑只做金鑰成對檢查；回滾與外洩處理改寫；隱私文字照實測寫、驗收加 (f)(g)。P4（XPC 維持移除、不開 JavaScript、feed 凍結重播接受）照建議 |

## 附錄 A：網站隱私段草稿（發版時照實機錄下的請求定稿）

取代 `site/index.html` 隱私段的第一項（「目前版本沒有連網功能。……」）。標頭照驗收 4(f) 實機錄下的請求寫（2026-10-16，本機 feed，HTTP/1.1 明文）；正式 feed 走 HTTPS（可能是 HTTP/2，Accept-Encoding 可能多 `br`、沒有 Connection），所以標頭和主機都標〔發版後核對〕，在驗收第 6 項對正式 feed 核對後定稿，再部署（要維護者當下同意）。

> 更新檢查（v0.4.7 起）：設定→關於的「自動檢查更新」開著時，每天最多一次連到 GitHub 讀取更新清單 `appcast.xml`（〔發版後核對：目的主機〕`raw.githubusercontent.com`）。GitHub 會看到你的 IP 位址與請求標頭〔發版後核對：標頭〕：User-Agent 裡有善解與 Sparkle 的版本號，Accept-Language 是你系統的語言偏好（例如 `zh-TW`），其餘是一般的 Accept、Accept-Encoding、Connection。不送出任何輸入內容、選字記憶或其他系統資訊。自動檢查可以在設定→關於關掉；按「立即檢查」時一定會連線。按「更新到 X」時〔發版後核對：下載經過的主機〕從 GitHub Releases 下載新版（標頭同上），安裝前驗證 EdDSA 簽章；正式版另經 Apple 公證。
>
> 除了更新檢查，目前版本沒有其他連網功能。之後如果加入雲端校正，會在上線前寫在這一頁：什麼時候連線、送出什麼、能不能關閉。
