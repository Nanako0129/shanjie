# 契約：「關於」頁與 Sparkle 自動更新（about-update）

狀態：草稿（2026-10-16）。

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

- **相依**：`macos/Package.swift` 加 Sparkle（`https://github.com/sparkle-project/Sparkle`，`from: "2.6.0"`，`Package.resolved` 釘住）。用官方預編的 framework（Syrtis 自己編是為了改 App 名稱，這裡不需要）。
- **Info.plist**（`scripts/build-app.sh`）：`SUFeedURL` = `https://raw.githubusercontent.com/Nanako0129/shanjie/main/appcast.xml`；`SUPublicEDKey`（維護者產生的公鑰）；`SUEnableAutomaticChecks` false；`SUEnableInstallerLauncherService` false；`SUEnableSystemProfiling` false。版本比對用 `CFBundleVersion`（現在和 `CFBundleShortVersionString` 同值）。
- **打包**：`build-app.sh` 把 `Sparkle.framework` 放進 `Contents/Frameworks`、移除 `XPCServices`（不在沙盒，Sparkle 文件允許；照 Syrtis 的 `bundle.sh`）；簽章由內而外（framework 裡的 `Autoupdate`、`Updater.app`、framework 本身，再簽 App），Developer ID、hardened runtime、timestamp，仍然沒有 entitlements；`scripts/check-app.sh` 加對應檢查。
- **更新服務**（新檔，例如 `macos/Sources/Shanjie/Updater.swift`）：照 Syrtis 的 `UpdaterService`：只在從 `.app` 執行時啟動（測試與 `swift test` 不啟動）；`SPUStandardUpdaterController(startingUpdater: true, …)`；背景檢查的排程與開關存在 `UserDefaults`（鍵名寫在契約實作裡）；`didFindValidUpdate` 記下可用版本、`updaterDidNotFindUpdate` 清掉；版本與狀態給關於頁和選單用。
- **release**（`.github/workflows/release.yml`）：sign 工作用 `SPARKLE_PRIVATE_KEY`（release Environment secret）對 App 的 zip 做 EdDSA 簽章，用 Sparkle 的 `generate_appcast` 產生多項目的 `appcast.xml`（保留舊項目，照 Syrtis 的 `make_appcast.sh`）；publish 工作把 `appcast.xml` commit 到 main；Sparkle 下載的檔案是 Release 上已有的 `shanjie-<V>.zip`（不新增資產）。缺 secret 時 dispatch 乾跑要報錯（照 Syrtis 的檢查）。
- **Homebrew**：`packaging/Casks/shanjie.rb` 加 `auto_updates true`（brew 不再主動升級，交給 Sparkle）。
- **隱私與文件**：`site/index.html` 隱私段改寫成：每天最多一次、連到 `raw.githubusercontent.com` 讀 `appcast.xml`、送出的只有 Sparkle 的 User-Agent（App 名稱與版本），沒有任何輸入內容、選字記憶或系統資訊；在設定→關於可以關掉；更新檔有 EdDSA 簽章與 Apple 公證。README 對應段落、`docs/PLAN.md`（R9、非目標、S8）、`docs/contracts/settings-sidebar.md`（加「關於」頁）同 PR 更新。
- **金鑰**：EdDSA 金鑰由維護者在自己的終端機產生（Sparkle 的 `generate_keys`），私鑰放進 GitHub 的 release Environment secret `SPARKLE_PRIVATE_KEY`，公鑰寫進 `build-app.sh`。agent 不碰鑰匙圈、不經手私鑰；main 準備好指令交給維護者。

## 3. 驗收

1. 單元測試（Swift）：關於頁的版本字串（一般版本、`0.0.0` 顯示開發版）；背景檢查的決策（開關關 → 不檢查；距上次不到一天 → 不檢查；超過 → 檢查）；選單在有可用版本時才有更新項；更新服務在非 `.app` 執行時不啟動。突變：開關關掉仍檢查、選單無條件顯示更新項，各要讓測試以斷言失敗。
2. `scripts/check-app.sh` 在本機建出的 App 上通過：Sparkle framework 在、簽章有效、XPC 服務不在、Info.plist 有 `SUFeedURL`、`SUPublicEDKey`、`SUEnableAutomaticChecks` false、`SUEnableSystemProfiling` false；沒有 entitlements。
3. release 乾跑（`workflow_dispatch`）：建置、簽章、公證、驗證通過，`appcast.xml` 產出且簽章可驗、不發佈任何東西。
4. **實機（決定性的一項，維護者與 main）**：
   - 測試用的 feed 與一把測試金鑰（不用正式金鑰）：本機先裝一個測試版 A，再提供一個測試版 B（版本較新）的 appcast。本機 feed 的做法（`file://` 或本機 HTTPS）由實作時量出、寫進研究紀錄。
   - 在關於頁按「立即檢查」→ 出現「更新到 B」→ 按下 → Sparkle 顯示下載進度與說明 → 安裝並重新啟動。
   - 量：(a) 安裝後輸入法是 B（關於頁版本、`ps` 的程序路徑）；(b) 在 TextEdit 照常打得出字；(c) Caps Lock 切換輸入法仍然有效（之前的已知問題：換 bundle 後再註冊會壞；Sparkle 不呼叫 TIS 註冊，預期不會壞）；(d) 更新前就開著的 Discord、終端機、TextEdit、Safari，各自能不能繼續打字、要不要重開（2026-10-15 的「舊連線」問題，結果寫進研究紀錄）；(e) 背景檢查查到新版時沒有彈出任何視窗。
   - 這一項不通過（例如 B 沒有接手、Caps Lock 壞掉、需要登出）→ 停，回報維護者再決定（例如更新後由輸入法自己呼叫既有的安裝流程）。
5. Homebrew：cask 有 `auto_updates true`，`brew upgrade` 不再主動升級善解（brew 的 `auto_updates` 行為，用 tap 的 cask 確認）。

## 4. 停止條件

- 第 4 項實機不通過。
- Sparkle 的簽章或公證在 release 乾跑失敗、而且原因不是設定錯誤。
- 需要 entitlements 才能運作（例如網路）——現在不在沙盒，預期不需要；需要就停下來問。

## 5. 範圍外

- 詞庫與模型分開更新（下一片，維護者 2026-10-16）。
- App 沙盒（v0.5.0）：開沙盒時 Sparkle 要加回 XPC 服務與 `network.client`，那一片處理。
- beta 頻道、差分更新（delta）。
- 關於頁顯示模型名稱（通語 SJ-LM 等）。

## 6. 限制與回滾

- executor：不連網（不下載 Sparkle 以外的東西；Sparkle 由 SwiftPM 解析時下載算在允許內）、不跑 Windows 機器；不讀 `eval/holdout`、repo 外的私人資料、學習檔、`~/Library`、鑰匙圈（不產生、不讀、不改任何金鑰）；不安裝、不啟動 App；不 push、不開 PR、不碰其他 worktree；不改 GitHub secret 或 Environment。
- 回滾：revert 這個 PR（Sparkle、關於頁、release 步驟一起）；已經裝了帶 Sparkle 的版本的使用者，下一版拿掉 `SUFeedURL` 或讓 appcast 不再有新項目即可停止更新。

## 7. 審查紀錄

| 輪次 | 結果 | 處置 |
|---|---|---|
