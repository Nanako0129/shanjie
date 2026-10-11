# 契約：聊天／書面的切換方式與個別 App 設定

狀態：READY（2026-10-12，security-reviewer 一次、plan-verifier 第 2 次 READY，§6），實作中。

## 0. 起因與依據

- 現況（s3b §9）：殼依前景 App 的 bundle ID 查寫死的聊天 App 清單（`Shell.chatApps`，8 個），在清單內用聊天設定，其餘用書面設定。使用者不能選，也不能改清單。所以在終端機、瀏覽器、編輯器裡打字一律是書面設定。
- PLAN（2026-10-03 的使用者決定）原本就寫「聊天 App 清單可在設定中修改」，延到設定頁；`settings-window.md` §4 把它列為之後的片。
- **使用者要求**（2026-10-12）：「應該要讓人可以自己選或自動或是增加 app 清單」。
- **使用者的決定**（2026-10-12，選擇題）：
  - 範圍：三選一（依 App 自動／一律聊天／一律書面）＋可改的 App 清單，內建的 8 個也能改回書面。
  - 改法：在那個 App 裡從善解選單一鍵切換；設定視窗列出改過的 App，可以移除。不做從「應用程式」資料夾挑 App。

## 1. 要做到什麼、怎麼看到

- 使用者可以在設定視窗選「依 App 自動」「一律聊天」「一律書面」。預設是依 App 自動，和現在完全相同。
- 依 App 自動時，使用者在任何 App 裡打開善解選單，勾或取消「這個 App 用聊天設定」，那個 App 就改用聊天或書面設定，**當下的組字立刻用新的設定重算**。
- 設定視窗列出改過的 App（名稱、圖示、聊天或書面），可以移除；移除後那個 App 回到內建的判斷。
- **怎麼看到**：
  - 殼層測試（§3.1）：打同一串注音，結果隨模式與個別設定改變。
  - 使用者實機（§3.4）：在終端機打自測用的第 226 列，勾選前是書面的「幸好救護車即時趕到」，勾選後是聊天的「幸好救護車及時趕到」。

## 2. 改動

### 2.1 儲存

- 照 `Shell.swift` 現有的模式各加一個協定與記憶體版（`ShanjieKit`），UserDefaults 版放在 `main.swift`：
  - `ProfileModeStore`：`profileMode: String?`，存 `ProfileMode` 的 raw value（`auto`、`chat`、`formal`）。鍵 `profileMode`。沒設過或不是這三個值，都當成 `auto`。
  - `AppProfileStore`：讀取回傳**原始值** `appProfiles: Any?`（UserDefaults 的 `object(forKey:)`），寫入收 `[String: String]`（空的 dictionary 就刪掉這個鍵）。鍵 `appProfiles`，值是 bundle ID → `chat`／`formal`。
  - 過濾是 `ShanjieKit` 的純函式 `AppProfiles.parse(_ raw: Any?) -> [String: UInt32]`，**逐項**判斷：根不是 dictionary → 空；只留鍵是非空字串、值是字串 `chat` 或 `formal` 的項目，其他項目略過，不影響同一份 dictionary 裡的有效項目（不能用整份 `as? [String: String]`：一個壞值就會讓整份變 nil、下一次寫入把有效的設定也蓋掉）。略過的項目在下一次寫入時就消失。這條路徑不得有 `as!` 或強制解包（s3b §9 已禁 `try!`）。
- `Shell` 的 init 加這兩個參數，必填、沒有預設值（和 `glassTintStore` 相同）；值在 init 時讀進快取，之後只經過 `Shell` 的 setter 改。輸入法執行中用 `defaults` 手動改的值不會被讀到，下一次切換會蓋掉它（和其他設定相同）。

### 2.2 判斷

- 純函式 `Shell.profile(bundle: String?, mode: ProfileMode, overrides: [String: UInt32]) -> UInt32`，是決定設定的**唯一**地方：
  - `chat` → 聊天；`formal` → 書面；
  - `auto`：bundle 是 nil 或空字串 → 書面（和現在相同）；`overrides` 有這個 bundle → 用它；否則 `chatApps` 有 → 聊天，沒有 → 書面。
- `Session.applyProfile()` 改成呼叫它，呼叫時機和現在相同（`activateServer`、組字擁有者改變，s3b §5），另外加上：
  - 選單切換之後（§2.3），對這個 session 立刻走一次 `activate()` 的路徑：**先 `claim()`**（別的 session 正在組字時，把它的組字送回它自己的 client），**再 `applyProfile()`**（`set_profile` 重算組字並回傳快照，殼照快照更新這個 session 的畫面）。不能只呼叫 `applyProfile()`：它沒有擁有者檢查，別的 App 的組字會被重算後送進這個 client（安全審查 P2-1）；也不能只加 `shell.owner === self` 的檢查（像其他選單開關那樣），那樣共用的 profile 仍會改到別人的組字。
- 從設定視窗改模式或移除 App：`Shell` 存下來並發 `didChangeSettings`，**不**主動重設 profile；回到 App 時的 `activateServer` 會用新的值。推論（沒量過）：從設定視窗回到 App 時，IMK 會對那個 App 的 controller 呼叫 `activateServer`。由實機確認（§3.4）；不成立時照 §5 停下來。

### 2.3 選單

- 新項目「這個 App 用聊天設定」（`MenuEntry.Action.toggleAppChat`），放在「動漫與遊戲詞」與它的資料日期之後、「善解設定…」之前。其他項目與順序不變。標題是固定字串，**不含 App 名稱**（選單標題不放 bundle ID 或 App 名稱，`Learning.swift` 的選單規則）。
- 勾選狀態：這個 session 的 client 目前的設定（§2.2 的結果）是聊天就打勾。
- 可不可以按：
  - client 的 bundle ID 是 nil 或空字串：**不顯示**這一項（沒有東西可以存）。
  - 模式不是 `auto`：顯示成反灰（`enabled: false`），勾選照強制的設定。
- 按下去：新的值是目前結果的相反。
  - 新的值和內建判斷相同（`chatApps` 有這個 bundle 且新值是聊天，或沒有且新值是書面）：從 `appProfiles` **移除**這個 bundle；
  - 不同：寫入 `appProfiles[bundle] = 新值`。
  - 然後對這個 session 走 §2.2 的 `claim()` → `applyProfile()`，並發 `didChangeSettings`。
- 這樣清單只會留下和內建判斷不同的 App；同一個 App 按兩次就回到沒有個別設定。

### 2.4 設定視窗

- 在「選字」與「外觀」之間加一個區塊「聊天／書面」：
  - 選擇器「切換方式」：「依 App 自動」「一律聊天」「一律書面」。
  - 說明文字（灰色小字）：「依 App 自動：聊天 App（Discord、LINE、訊息等）用聊天設定，其他 App 用書面設定。」
  - 「個別設定的 App」清單：每列是 App 圖示、名稱、右邊「聊天」或「書面」、一個移除按鈕（圖示按鈕，VoiceOver 讀「移除」）。名稱與圖示用 `NSWorkspace.urlForApplication(withBundleIdentifier:)` 找；找不到（App 已刪除）時名稱顯示 bundle ID、用一般 App 圖示。依名稱排序。
  - 清單是空的時候，顯示灰色小字：「在 App 裡打開善解選單，勾選「這個 App 用聊天設定」，就會列在這裡。」
  - 模式不是「依 App 自動」時，清單照常顯示但反灰（個別設定保留，切回自動時生效）。
- `SettingsModel` 加 `profileMode`、`appProfiles`（排好序的列）、`setProfileMode(_:)`、`removeAppProfile(_:)`，都經過 `Shell` 的 setter；收到 `didChangeSettings` 時重讀（`refreshSettings`）。名稱與圖示的查找放在 App target（SwiftUI 畫面），`SettingsModel` 只給 bundle ID 與設定，讓測試不碰 `NSWorkspace`。
- 視窗仍然沒有文字欄位（`settings-window.md` §2.1）。

### 2.5 隱私（修訂 s3b §9）

- s3b §9 原本規定 bundle ID「不傳給核心、不寫 log、不存檔」。改成：
  - **只有使用者在選單上按過的 App** 的 bundle ID 會存在 App 自己的 UserDefaults（`appProfiles`），值只有 `chat`／`formal`；其他 App 的 bundle ID 照舊不存。
  - 照舊不傳給核心（核心只收到列舉）、不寫 log。
  - 設定視窗顯示的是使用者自己的設定，不送到任何地方。
- 「清除選字記憶…」不清這份清單：它是設定，不是學習資料。移除要在設定視窗逐項移除。這份設定和其他偏好一樣會被 Time Machine 備份（「不要備份選字記憶」只管學習資料夾）；Homebrew 的 `zap` 會刪掉偏好檔。
- `appProfiles` **不影響**學習的 privacyGate：`learningPause` 只看安全輸入、寫死的 denylist 與取不到 bundle ID，不讀這份清單（密碼管理器被設成聊天，照樣暫停學習）。
- `didChangeSettings` 維持程序內的 `NotificationCenter`、沒有 `userInfo`；不用 `DistributedNotificationCenter`，也不用 key path（bundle ID 有點，會被拆開）存這份清單。
- 日誌規則（s3b §9 R2）不變：新增的程式只能記靜態字串與回傳碼。

### 2.6 文件

- `docs/contracts/s3b.md` §9：加修訂一段，指向本契約 §2.5；§14 第 275 行「目前唯一的使用者資料，也就是 `layout` 偏好」已經過時，一併改寫。
- 程式註解裡同一個承諾的其他地方（安全審查 P3-2 列出的，實作時再 grep 一次 `never kept`、`never .* stored`、`stored or shown`）：`Shell.swift` 的 `chatApps` 註解（約 168 行）與 `applyProfile` 註解（約 664 行）、`Learning.swift` 開頭的日誌規則（約 4 行）、`learningDenylist` 註解（約 105 行；denylist 本身仍然不存，改寫而不是刪掉）。
- `docs/contracts/settings-window.md` §4：「聊天 App 清單」的編輯介面改指向本契約。
- `docs/PLAN.md`：2026-10-03 那條使用者決定下面加狀態行。
- 網站與 README 只在合併、實機驗收後再看要不要提（另外問）。

## 3. 驗收

1. **殼層測試**（`ShanjieKitTests`）：
   - `Shell.profile` 的表：三種模式 ×（內建聊天 App、其他 App、nil、空字串、其他 App 設成聊天、內建聊天 App 設成書面）。
   - 可觀察的結果（照 `testProfileFollowsTheApp` 的做法打 Row226 再 Enter）：TextEdit 按選單切換後送出聊天的結果；Discord 切換後送出書面的結果；模式「一律聊天」時 TextEdit 是聊天、「一律書面」時 Discord 是書面；**組字中按選單**，不用重新啟用，Enter 送出的已經是新設定的結果。
   - 選單：項目的位置；勾選跟著結果；模式不是 `auto` 時反灰；bundle ID 是 nil 時不出現。
   - 按兩次：其他 App 按兩次後 `appProfiles` 沒有這個 bundle；內建聊天 App 按一次後存的是 `formal`、再按一次就移除。
   - store：`profileMode` 沒設過或是未知字串 → `auto`。`AppProfiles.parse`：nil、根不是 dictionary（字串、陣列）→ 空；值是 NSNumber、巢狀 dictionary、其他字串、鍵是空字串的項目被略過；**一個壞項目和幾個有效項目放在一起時，有效的都留下**。
   - **啟動時讀回存檔**（照 `ShellTests` 的 `MemoryLayoutStore("eten")`、`SettingsTests` 的 `MemoryGlassTintStore(0.4)` 的做法）：用已經有值的 store 建 `Shell`：(a) `profileMode` 是 `chat` 時 TextEdit 打 Row226＋Enter 是聊天的結果，`formal` 時 Discord 是書面的結果；(b) `appProfiles` 的原始值是 {TextEdit: `chat`, 一個壞項目}：TextEdit 的 Row226 是聊天的結果、選單在 TextEdit 打勾、`SettingsModel` 的清單只有 TextEdit；再對另一個 App 按一次選單之後，store 裡 TextEdit 那一項還在（快取沒有從空的開始、沒有把存檔蓋掉）。
   - **擁有者**：A 組字中，對 B 的 session 按 `toggleAppChat`：A 的組字送回 A 的 client（照 s3b §5），B 的 client 沒有收到 A 的組字。
   - `SettingsModel`：選擇器寫進 store 並發通知；清單和 `appProfiles` 一致、排序固定；移除後清單與 store 都少一項；選單切換後 model 收到通知、清單跟著變。
   - `LogTests`：用帶標記的 bundle ID 按 `toggleAppChat` 兩次，再用 `SettingsModel` 改模式、移除這個 App，擷取的日誌裡沒有標記（加進現有的負向動作）。
2. **突變**（每一項都要讓某個測試失敗；build error 不算）：
   - `Shell.profile` 忽略模式；
   - `Shell.profile` 忽略 `overrides`；
   - 按下時新值和內建判斷相同也寫入（不移除）；
   - 按下後不立刻 `applyProfile`；
   - 按下時不先 `claim()`；
   - `AppProfiles.parse` 改成整份 `as? [String: String]`；
   - `Shell.init` 不讀 `profileMode`／`appProfiles` 的 store（從 `auto`／空的開始）；
   - 按下後不發 `didChangeSettings`。
3. `make test`、CI 全綠。
4. **實機**（使用者；鍵盤操作由 `imeshot` 做，每次 ≤ 15 秒）：
   - 在終端機打第 226 列（`ㄒㄧㄥˋ ㄏㄠˇ ㄐㄧㄡˋ ㄏㄨˋ ㄔㄜ ㄐㄧˊ ㄕˊ ㄍㄢˇ ㄉㄠˋ`，`Selftest.row226Standard`、測試的 `Row226`）＋Enter，得到書面的「幸好救護車即時趕到」；勾選「這個 App 用聊天設定」後再打一次，得到聊天的「幸好救護車及時趕到」。使用者的選字記憶可能讓實機結果和測試不同，不同時記下來，改用兩種設定結果不同的另一句；
   - 設定視窗列出終端機（聊天）；按移除，回到終端機再打，回到書面（同時確認 §2.2 的推論：從設定視窗回到 App 時有重新套用）；
   - 模式改「一律聊天」，在 TextEdit 打第 226 列得到聊天的結果；改回「依 App 自動」；
   - 實機時另開 `log stream`（s3b §10 實測 16 的做法），涵蓋選單切換**與打開清單不是空的設定視窗**（`NSWorkspace` 查 App 名稱在 App target，殼層測試看不到；LaunchServices 會不會用公開等級記下查詢的 ID 沒查證，這一步就是檢查），確認輸出裡沒有 bundle ID。

## 4. 範圍外

- 從「應用程式」資料夾挑 App 加入清單（使用者 2026-10-12 沒選）。
- 學習 denylist 的編輯介面。
- 在清單裡直接切換聊天／書面（用選單切換或移除就夠）。
- 改內建的 8 個聊天 App。
- 和語意重排（S7）的互動：重排照目前的 profile 用那個設定的參數，本片不改。

## 5. 停止條件、預算、限制

- **停止條件**：
  - 實機上從設定視窗回到 App 時沒有重新套用設定（§2.2 的推論不成立）：先改契約（例如 `Shell` 對擁有者主動重設）再做；
  - 常用 App 的 client 取不到 bundle ID，使得選單項目在那些 App 都不出現：記下 App 名稱，問使用者；
  - 實機的 `log stream` 出現 bundle ID 或任何輸入內容。
- **預算**：executor 實作 1 次＋修正 1 次；設定視窗的外觀照使用者的實機回饋收斂，每一輪只改指出的地方。
- **限制**：agent 不安裝、不啟動 App、不呼叫 TIS 或 lsregister、不在 .app 裡執行任何東西、不碰 `~/Library` 與鑰匙圈、不改真實的 UserDefaults（測試只用記憶體版 store）。
- **和 #118 的關係**：#118（S7c-2b，未合併）也改選單、設定視窗與 `Shell` 的 init。後合併的那一個 rebase 並處理衝突，選單順序以兩份契約合起來為準。

## 6. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| security-reviewer（2026-10-12） | 沒有 P0／P1。P2-1：選單切換後只呼叫 `applyProfile()`，它沒有擁有者檢查，別的 App 的組字會被重算後送進這個 client。P3-1：整份 `as? [String: String]` 一個壞值就讓整份消失，過濾的位置沒寫、沒測試。P3-2：程式註解裡還有「bundle ID 不存」的承諾（`Shell.swift`、`Learning.swift`、s3b §14）。P3-3：日誌檢查漏了設定視窗的路徑與 App target 的 `NSWorkspace` 查找；選單標題要寫明不含 App 名稱。P4：清單不影響學習 gate、通知不帶 `userInfo`、備份與 `zap`、執行中手動改的值會被蓋掉 | 全部 **FIX**：§2.2／§2.3 改成 `claim()` → `applyProfile()`，§3.1 加擁有者測試、§3.2 加突變；§2.1 改成原始值＋`AppProfiles.parse` 逐項過濾、禁 `as!`，§3.1 加壞值測試、§3.2 加突變；§2.6 列出要改的註解；§3.1 `LogTests` 加設定視窗的動作、§3.4 `log stream` 涵蓋設定視窗、§2.3 標題固定字串；§2.1、§2.5 加 P4 的四句 |
| plan-verifier 第 1 次 REVISE（2026-10-12） | 沒有測試或突變證明建 `Shell` 時會讀回已存的 `profileMode`／`appProfiles`；init 忽略 store 時所有測試照過、§3.4 也不重啟，使用者的設定在重開後失效，下一次切換還會把空的快取寫回、清掉存檔 | **FIX**：§3.1 加「啟動時讀回存檔」測試（兩種模式、有效＋壞項目、切換別的 App 後原項目仍在），§3.2 加突變「init 不讀 store」 |
| plan-verifier 第 2 次（2026-10-12） | READY | 開始實作 |
