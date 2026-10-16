# 契約：「關於」頁與 Sparkle 自動更新（about-update）

狀態：修訂二（2026-10-16；修訂一照 security-reviewer 的審查改寫信任模型、Info.plist、發版流程與驗收；修訂二處理 plan-verifier 的 REVISE：下載網址、正式路徑的驗收、Homebrew 舊使用者，見 §7）。實作時的修訂（2026-10-16，維護者經 main 同意第一項）：ad-hoc 建置不加 hardened runtime，原因是加了之後 library validation 拒絕載入 ad-hoc 的 `Sparkle.framework`（dyld：「mapping process and mapped file (non-platform) have different Team IDs」，`--selftest` 實測）；正式版四處都要 runtime，`verify()` 強制。更新服務用 `startingUpdater: false` 再自己呼叫 `start()`：Sparkle 2.9.3 的 controller 自己啟動失敗時會跳 `runModal` 的警告，會卡住輸入法。

## 0. 依據

- 維護者 2026-10-16 的選擇題與訊息：
  - 版本號放在新的「關於」頁（wclinrd 在 Discord 回饋串建議設定裡顯示版本）。
  - 「把自動更新也做上去」→ 用 Sparkle、照 Syrtis 的做法（`~/side-project/tokenbar-native`：`Sources/Syrtis/UpdaterService.swift`、`scripts/bundle.sh`、`scripts/make_appcast.sh`、`.github/workflows/release.yml` 的 `SPARKLE_PRIVATE_KEY`、cask `auto_updates true`）。
  - 自動檢查預設開、每天最多一次。
  - Homebrew 照 Syrtis 的方式相容（cask 加 `auto_updates true`）。
  - 進 v0.4.7；詞庫與模型分開更新是之後另一片（範圍外）。
- 取代既有決定：`docs/PLAN.md` §7 R9「v1 不做自動安裝（只檢查版本開瀏覽器）」與 §1「非目標：自動更新」，由上面的選擇題取代；同一個 PR 改 PLAN 的這兩處並註明。
- 公開承諾：`site/index.html` 的隱私段寫「目前版本沒有連網功能。之後如果加入更新檢查……會在上線前寫在這一頁：什麼時候連線、送出什麼、能不能關閉。」這一片同一版改那段（網站部署要維護者當下同意）。
- 設計分析：Fable（2026-10-16），數字與檔案位置由 main 核對。
- 安全審查：security-reviewer（2026-10-16，對照 Sparkle 2.9.3 原始碼），無 P0；處置見 §7。

## 0.1 信任模型（寫明，不迴避）

- Sparkle 驗更新是「EdDSA 簽章通過」**或**「新版的 Apple 簽章符合舊版的 designated requirement（同一個 Team ID）」任一成立就接受（`SUUpdateValidator.m`），而且安裝時拿掉隔離屬性，Gatekeeper 與公證不會再看這份更新。所以：**持有 EdDSA 私鑰，或 Developer ID 憑證，再加上能讓使用者讀到的 feed，就能在所有使用者看得到按鍵的程序裡執行程式。** 不 fork Sparkle 改成兩者皆要（兩把金鑰在同一個 release Environment、同一次核准放行，AND 只防單一 secret 外洩，對單人維護不划算）。
- 降低的方法：feed 本身也要 EdDSA 簽章（`SURequireSignedFeed`），沒有私鑰就改不了更新內容與說明；解壓前驗（`SUVerifyUpdateBeforeExtraction`）；只有使用者按下才裝（`SUAllowsAutomaticUpdates` false）；私鑰只在推 tag 的那一步、只在 Sparkle 自己的工具裡出現；降版由 Sparkle 以實際 bundle 的 `CFBundleVersion` 擋下。
- `docs/PLAN.md` §7 加一條新的 R 規則（R10）取代 R9 的「v1 不做自動安裝」，內容是上面這段與 §2 的設定；R9 的其他部分（hardened runtime、無 entitlements、不需 root）不變。

## 1. 要做到什麼、怎麼看到

- **「關於」頁**：設定視窗左欄多一頁「關於」（`SettingsPage` 加一個 case，最後一個），內容：
  - 版本：`CFBundleShortVersionString`；`0.0.0`（dispatch 乾跑與本機開發）顯示「開發版」。
  - 「自動檢查更新」開關（預設開）、「立即檢查」按鈕、上次檢查的日期與結果（已是最新／有新版本 X／檢查失敗）。
  - 有新版本時一顆「更新到 X」按鈕：按下才走 Sparkle 的互動流程（下載進度、更新說明、安裝並重新啟動）。
  - 網站連結、一行授權說明。
- **輸入選單**：有新版本時多一項「有新版本 X，按此更新…」，按下同樣走互動流程；沒有新版本時不出現。
- **背景檢查不彈窗**：輸入法程序在使用者打字時跳出視窗會搶焦點，所以 Sparkle 自己的排程關掉（`SUEnableAutomaticChecks` false），改由我們每天最多一次呼叫 `checkForUpdateInformation`（只查資訊、不顯示任何視窗，Syrtis 的做法）；查到新版只更新關於頁與選單，不主動開視窗。開關關掉就不做背景檢查；「立即檢查」不受開關影響。
- 怎麼看到：
  - 使用者：設定→關於看得到版本；有新版時選單與關於頁出現更新項，按下看到 Sparkle 的下載進度與說明，裝完輸入法照常能打字。
  - 開發者：`scripts/check-app.sh` 驗得到 Sparkle 已簽章、`SUPublicEDKey` 與 `SUFeedURL` 在 Info.plist、XPC 服務已移除、沒有 entitlements；release 產出 `appcast.xml` 並推到 main。

## 2. 實作

- **相依**：`macos/Package.swift` 加 Sparkle，`exact: "2.9.3"`（2.9.0 起才有簽 feed；2.9.2 修了兩個安全問題）。用官方預編的 framework（二進位由 Sparkle 的 `Package.swift` 內的 checksum 釘住；Syrtis 自己編是為了改 App 名稱，這裡不需要）。`macos/Package.resolved` 進版控，`build-app.sh` 與 CI 用 `--force-resolved-versions`。這是 `macos/Package.swift` 第一個外部相依。
- **Info.plist**（`scripts/build-app.sh`，正式版的值寫死在腳本裡，不從環境變數覆寫）：`SUFeedURL` = `https://raw.githubusercontent.com/Nanako0129/shanjie/main/appcast.xml`；`SUPublicEDKey`（維護者產生的公鑰）；`SURequireSignedFeed` true；`SUVerifyUpdateBeforeExtraction` true；`SUAllowsAutomaticUpdates` false；`SUEnableAutomaticChecks` false；`SUEnableInstallerLauncherService` false；`SUEnableSystemProfiling` false；不設 `SUEnableJavaScript`。版本比對用 `CFBundleVersion`（現在和 `CFBundleShortVersionString` 同值）。測試用的 feed 與公鑰（驗收 4）由另一個只在本機用的打包選項產生，正式版的檢查（下面）會擋下任何不同的值。
- **打包**：`build-app.sh` 把 `Sparkle.framework` 放進 `Contents/Frameworks`、移除 `XPCServices`（不在沙盒，Sparkle 文件允許；照 Syrtis 的 `bundle.sh`），本機仍是 ad-hoc 簽章（〔實作修訂〕由內而外、不加 hardened runtime，見狀態列）。正式的 Developer ID 簽章由內而外（`Sparkle.framework/Versions/B/Autoupdate`、`Versions/B/Updater.app`、framework 本身，再簽 App；hardened runtime、timestamp、沒有 entitlements）寫在 `release.yml` 的 sign 步驟裡，不呼叫 repo 的腳本（sign 工作現在不 checkout、不跑 repo 的程式，維持這個邊界）。
- **更新服務**（新檔，例如 `macos/Sources/Shanjie/Updater.swift`）：照 Syrtis 的 `UpdaterService`：只在從 `.app` 執行時啟動（測試與 `swift test` 不啟動）；`SPUStandardUpdaterController`（〔實作修訂〕`startingUpdater: false`，由更新服務呼叫 `start()` 並接住錯誤，見狀態列）；背景檢查的排程與開關存在 `UserDefaults`（鍵名寫在契約實作裡）；`didFindValidUpdate` 記下可用版本、`updaterDidNotFindUpdate` 清掉；版本與狀態給關於頁和選單用。
- **release**（`.github/workflows/release.yml`）：
  - 正式 zip 的檢查（`verify()`，對解壓後的正式 zip）：App、`Sparkle.framework`、`Versions/B/Autoupdate`、`Versions/B/Updater.app` 四者各自沒有 entitlements、TeamIdentifier 都是 `2LJ882GPY8`（路徑不存在就失敗）；`XPCServices` 不存在；Info.plist 的 `SUFeedURL`、`SUPublicEDKey`、`SURequireSignedFeed`、`SUVerifyUpdateBeforeExtraction`、`SUAllowsAutomaticUpdates`、`SUEnableSystemProfiling` 逐字比對寫在 workflow 裡的值。`scripts/check-app.sh` 同樣逐字比對（本機測試版另有一套預期值）。
  - EdDSA 是獨立的一步，放在「移除簽章用的 keychain」之後，條件 `github.event_name == 'push'`（只有推 tag）：工具用 Sparkle 官方 release 的 tarball（版本與 `Package.resolved` 相同，SHA-256 以字面值寫在 workflow 裡）；舊的 `appcast.xml` 用 sparse checkout 只取這個檔、當資料讀，不執行 repo 的程式；私鑰從 stdin 交給 Sparkle 的工具（`--ed-key-file -`），不寫進檔案。`generate_appcast` 產生多項目的 `appcast.xml`（保留舊項目、App 有 `SURequireSignedFeed` 時 feed 自動簽章），對象是要上傳到 Release 的 `shanjie-<V>.zip`（不新增資產）：輸入資料夾只放這一個 zip（和 publish 上傳的是同一份位元組，不放安裝程式的 zip），並給 `--download-url-prefix https://github.com/Nanako0129/shanjie/releases/download/v<V>/`（不給的話網址會從 `SUFeedURL` 的資料夾拼出來，指到不存在的檔案）。
  - 金鑰成對檢查：乾跑與推 tag 都用 workflow 內嵌的 CryptoKit（`Curve25519.Signing.PrivateKey(rawRepresentation:)`，不是 repo 的程式）從 secret 推出公鑰，和正式 zip 裡 Info.plist 的 `SUPublicEDKey` 比對，不同就失敗。乾跑不用正式金鑰簽任何東西。
  - publish 工作把 `appcast.xml` commit 到 main（照 Syrtis：推之前確認 main 的 `appcast.xml` 沒有在這期間被改過）。
- **Homebrew**：`packaging/Casks/shanjie.rb` 加 `auto_updates true`（brew 不再主動升級，交給 Sparkle）。
- **隱私與文件**：`site/index.html` 隱私段照實機錄下的請求改寫（驗收 4(f)），至少寫明：每天最多一次、連到 GitHub（`raw.githubusercontent.com`）讀 `appcast.xml`；GitHub 會看到你的 IP 與請求標頭（User-Agent 是「App 名稱/版本 Sparkle/版本」，其他標頭以實測為準）；沒有任何輸入內容、選字記憶或系統資訊；在設定→關於可以關掉自動檢查，「立即檢查」按下時一定會連線；按「更新到 X」時從 GitHub Releases 下載；安裝前驗證 EdDSA 簽章，正式版另經 Apple 公證（公證不寫成驗證項目）。README 對應段落、`docs/PLAN.md`（R9 → R10、非目標、S8）、`docs/contracts/settings-sidebar.md`（加「關於」頁）同 PR 更新。
- **日誌與網路設定**：`Updater.swift` 照 R2，只記靜態字串，不內插版本、網址或錯誤。Sparkle 自己的 log 用 `%{public}` 並寫到磁碟（內容是路徑、網址、錯誤，沒有輸入內容），寫明這是 R2 的例外與理由。R7 的 ephemeral 設定不套用到更新管道（Sparkle 的下載用預設 session），也寫明。
- **金鑰**：EdDSA 金鑰由維護者在自己的終端機產生（Sparkle 的 `generate_keys`），私鑰放進 GitHub 的 release Environment secret `SPARKLE_PRIVATE_KEY`，公鑰寫進 `build-app.sh`。agent 不碰鑰匙圈、不經手私鑰；main 準備好指令交給維護者。

## 3. 驗收

1. 單元測試（Swift，不連網）：關於頁的版本字串（一般版本、`0.0.0` 顯示開發版）；背景檢查的決策（開關關 → 不檢查；距上次不到一天 → 不檢查；超過 → 檢查）；選單在有可用版本時才有更新項；更新服務在非 `.app` 執行時不啟動。突變：開關關掉仍檢查、選單無條件顯示更新項，各要讓測試以斷言失敗。
2. `scripts/check-app.sh` 在本機建出的 App 上通過：Sparkle framework 在、簽章有效、XPC 服務不在、Info.plist 的 Sparkle 鍵逐字等於正式值；四個程式碼位置都沒有 entitlements。突變：把 `SURequireSignedFeed` 拿掉或改值，檢查要失敗。
3. release 乾跑（`workflow_dispatch`）：建置、簽章（含 Sparkle 內層）、公證、`verify()` 通過；金鑰成對檢查通過；不簽 appcast、不發佈任何東西。
4. **實機（決定性的一項，維護者與 main）**：
   - 測試用的 feed 與一把測試金鑰（不用正式金鑰）：本機先裝一個測試版 A，再提供一個測試版 B（版本較新）的 appcast。本機 feed 的做法（`file://` 或本機 HTTPS）由實作時量出、寫進研究紀錄。
   - 在關於頁按「立即檢查」→ 出現「更新到 B」→ 按下 → Sparkle 顯示下載進度與說明 → 安裝並重新啟動。
   - 量：(a) 安裝後輸入法是 B（關於頁版本、`ps` 的程序路徑）；(b) 在 TextEdit 照常打得出字；(c) Caps Lock 切換輸入法仍然有效（之前的已知問題：換 bundle 後再註冊會壞；Sparkle 不呼叫 TIS 註冊，預期不會壞）；(d) 更新前就開著的 Discord、終端機、TextEdit、Safari，各自能不能繼續打字、要不要重開（2026-10-15 的「舊連線」問題，結果寫進研究紀錄）；(e) 背景檢查查到新版時沒有彈出任何視窗；(f) 錄下檢查與下載的實際請求（目的主機、標頭），網站隱私段照這份寫；(g) 把測試 feed 改一個字（簽章失效），檢查要被拒、不顯示新版。
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
- App 沙盒（v0.5.0）：開沙盒時 Sparkle 要加回 XPC 服務與 `network.client`，那一片處理。
- beta 頻道、差分更新（delta）。
- 關於頁顯示模型名稱（通語 SJ-LM 等）。

## 6. 限制與回滾

- executor：不連網（不下載 Sparkle 以外的東西；Sparkle 由 SwiftPM 解析時下載算在允許內）、不跑 Windows 機器；不讀 `eval/holdout`、repo 外的私人資料、學習檔、`~/Library`、鑰匙圈（不產生、不讀、不改任何金鑰）；不安裝、不啟動 App；不 push、不開 PR、不碰其他 worktree；不改 GitHub secret 或 Environment。
- 回滾：revert 這個 PR（Sparkle、關於頁、release 步驟一起）。已經裝了帶 Sparkle 的版本的使用者：Sparkle 拒絕「新版拿掉 `SUPublicEDKey`」的更新（只允許輪換、不允許移除），所以之後要拿掉 Sparkle 的那一版仍要保留 `SUPublicEDKey` 並用同一個 Team ID 簽；或者讓 appcast 不再有新項目，使用者改用安裝程式更新（cask 是 `auto_updates true`，`brew upgrade` 不會主動升級）。
- 金鑰外洩的處理寫進 `docs/releases/README.md`（第一次發帶 Sparkle 的版本前寫好）：輪換走 Developer ID 那條路（同一個 Team 簽的新版帶新公鑰、壓縮檔用新私鑰簽）；簽 feed 時舊用戶端最多約 20 天收不到新 feed；EdDSA 沒有撤銷，還沒更新的使用者在攻擊者同時有舊私鑰與 feed 寫入權時仍有風險。
- agent 不核准 release Environment 的部署、不推 `v*` tag（推 tag 本來就要維護者當下同意）。

## 7. 審查紀錄

| 輪次 | 結果 | 處置 |
|---|---|---|
| plan-verifier（修訂二） | READY | — |
| plan-verifier（修訂一） | REVISE 3 項：`generate_appcast` 沒給下載網址前綴（會拼出不存在的網址）；沒有任何驗收走到正式的 feed、金鑰與下載網址；v0.4.6 以前的 Homebrew 使用者在 cask 加 `auto_updates` 後可能永遠升不到 v0.4.7 | 都 FIX：§2 寫明 `--download-url-prefix` 與輸入資料夾只放那個 zip；§3 加第 6 項（推 tag 後核對 appcast、下載網址、已安裝版本「立即檢查」顯示已是最新）並列入停止條件；第 5 項改成發版說明與 README 一律寫一次性的指名升級指令，VM 實測只報 |
| security-reviewer（草稿） | 無 P0。P1：EdDSA 或 Developer ID 任一加 feed 寫入權即可推更新且繞過公證，契約沒寫；發版設計和 sign 工作的邊界衝突（巢狀程式只有 ad-hoc、工具與舊 appcast 的來源）。P2：feed 未簽可釣魚／外洩 IP、檢查只看外層 App 與本機版、回滾方式會被 Sparkle 拒絕、乾跑用正式金鑰且沒驗公私鑰成對、隱私文字三處不準確。P3：Sparkle 的 log 與 R2、Package.resolved、agent 不核准部署、外洩處理手冊 | 全部 FIX：§0.1 信任模型與 PLAN R10；Info.plist 加 `SURequireSignedFeed`、`SUVerifyUpdateBeforeExtraction`、`SUAllowsAutomaticUpdates` false，Sparkle `exact: 2.9.3`；內層 Developer ID 簽章寫在 sign 步驟；EdDSA 獨立一步、只在推 tag、官方 tarball 釘雜湊、stdin 給私鑰；`verify()` 逐一檢查四個程式碼位置與 Info.plist 逐字；乾跑只做金鑰成對檢查；回滾與外洩處理改寫；隱私文字照實測寫、驗收加 (f)(g)。P4（XPC 維持移除、不開 JavaScript、feed 凍結重播接受）照建議 |
