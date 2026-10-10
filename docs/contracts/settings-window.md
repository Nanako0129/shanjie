# 契約：設定視窗（第一片）

狀態：實作完成，使用者實機驗收 OK、fresh verifier CONFIRMED（2026-10-10，§6）。第三、四輪實機的外觀修訂（背景材質、標題列、滑桿圖示）使用者實機 OK，見 §6。

## 0. 起因與依據

- **使用者要求**（2026-10-09）：「多加一個工項，製作設定視窗，或是整合在 Mac 的輸入法設定」；並請 Syrtis（前 TokenBar）的維護 session 說明它的玻璃深淺設定怎麼做。
- **研究**（2026-10-10，研究紀錄同日）：
  - 第三方輸入法沒有公開的方法把設定放進「系統設定」。系統設定的面板是 ExtensionKit 擴充；蘋果自家輸入法用私有的擴充點，在「鍵盤 › 輸入方式 › 編輯」顯示選項。
  - 第三方在輸入法 bundle 內放 `Preferences.prefPane` 的做法，macOS 10.14 會載入，10.15 起變空白（Gureum #604），Gureum 2020 年改回 App 內的視窗。
  - 看過的 8 個開源輸入法，6 個從輸入選單、在輸入法自己的程序裡開設定視窗：小麥、威注音、macSKK（有開 App 沙盒）、azooKey、Gureum、fcitx5。Google 日文輸入另開一個 App，鼠鬚管開設定資料夾。
  - SwiftUI 的 `Settings` scene 從 macOS 14 起無法從輸入選單打開（macSKK 的註解）。
  - `NSApp.activate()` 從 14 起只是「請求」，不保證把視窗帶到前面。
- **使用者的決定**（2026-10-10）：從選單開設定視窗。
- **玻璃深淺的參考**（Syrtis，使用者轉來的說明）：
  - 只調玻璃的色調，材質不變；
  - 連續滑桿 0–1，色調不透明度＝值 × 0.5，0 是預設，等於原本的樣子；
  - 深色模式疊黑、淺色模式疊白。上限 0.5 是 Syrtis 維護者看實機挑的，沒有和蘋果截圖量過。

## 1. 要做到什麼、怎麼看到

- 輸入選單加一項「善解設定…」，打開一個設定視窗，裡面有選單現有的全部設定，加上候選窗的玻璃深淺。
- 視窗和選單改的是同一份設定，任一邊改了，另一邊馬上看得到；效果和從選單改相同。
- **怎麼看到**：
  - 殼層測試（§3）；
  - 使用者實機：從輸入選單打開設定視窗，視窗跳到最前面、可以直接用滑鼠和鍵盤操作；改每一項都有效果；改了玻璃深淺之後，回到 App 打字，下一次出現的候選窗用新的深淺；關掉後焦點回到原本的 App。

## 2. 改動

### 2.1 視窗

- 選單在「清除選字記憶…」之前加一項「善解設定…」（`MenuEntry.Action.openSettings`）。現有的選單項目不動。
- 在輸入法自己的程序裡開一個 `NSWindow`，內容是 SwiftUI `Form`（`.grouped` 樣式，系統元件、系統預設動畫）。標題「善解設定」。同時只開一個，再點選單就把已開的那個帶到前面。
- **帶到前面**：照現有 `AlertDialogs` 的做法，記下前景 App，`NSApp.activate()` 後 `makeKeyAndOrderFront`；關窗時把焦點還給記下的 App。
  - **修訂（2026-10-10 實機）**：使用者點「善解設定…」看不到視窗。查視窗列表：視窗有開、在畫面上，但層級是一般視窗（layer 0），`activate()` 被系統拒絕，所以被前景 App 蓋住。改成和 `AlertDialogs`（`.modalPanel`）一樣用較高的層級：視窗 `level = .floating`，`makeKeyAndOrderFront` 之後再 `orderFrontRegardless()`（威注音的做法）。視窗會在最前面，但不一定是 key window；使用者點一下就會成為 key。不改成 `.regular`（fcitx5 的做法會在 Dock 多一個圖示）。
  - 實機上仍然帶不到前面時，才照 fcitx5 的做法改：開窗時暫時切成 `.regular`、關掉後切回。改法寫進這份契約再做（§5）。
- **第一輪實機回饋（2026-10-10）**：
  - **置中**：視窗出現在螢幕上方（CGWindowList：X=1279、Y=31、420×532），推論是 `center()` 在內容撐出尺寸之前就跑了。改成每次 `show()` 先設內容尺寸（`NSHostingView.fittingSize`）再置中，置中在滑鼠所在的螢幕（使用者剛在那裡點了選單；`NSScreen.main` 是 key window 所在的螢幕，輸入法的程序沒有）。
  - **視窗背景是系統的毛玻璃**（結構照 Syrtis 的設定視窗：玻璃在內容後面、延伸到標題列底下；材質不同，Syrtis 用 `.hudWindow`）：`NSVisualEffectView`（材質 `.popover`、`.behindWindow`）放在內容**後面**填滿整個視窗，連標題列底下也是（`.fullSizeContentView`、`titlebarAppearsTransparent`），SwiftUI 的 `Form` 從標題列下面開始、`.scrollContentBackground(.hidden)` 隱藏自己的不透明背景。玻璃不包住內容，否則內容會套 vibrancy、文字變淡。視窗 `isOpaque = false`、背景透明，標題文字與關閉鈕維持系統的。只用系統樣式，沒有自訂的模糊或透明度數值。
    - 修訂（2026-10-10 第三、四輪實機）：原本是 Liquid Glass（`NSGlassEffectView`），使用者覺得設定視窗太透，換成較平的毛玻璃；標題列原本在玻璃外面、視窗背景透明，所以幾乎全透（「標題列不要做那麼透」），改成玻璃延伸到標題列底下。材質 `.popover` 是看系統的清單挑的，沒有量測；使用者實機看過 OK。
- 視窗裡**沒有文字欄位**：只有選擇器、開關、滑桿與按鈕。所以不需要自己裝 Edit 選單，也不會讓善解在自己的視窗裡處理打字。
- 安全輸入期間選單是反灰的，打不開設定視窗。這是系統行為，照 CLAUDE.md「已知的系統問題」處理，不另外做。

### 2.2 內容（順序照選單）

| 區塊 | 項目 | 寫到哪裡 |
|---|---|---|
| 鍵盤 | 鍵盤排列：標準／倚天（選擇器） | `Shell.selectLayout`，和選單相同 |
| 選字 | 即時預測、可省略韻母（緊接在即時預測之後；即時預測關閉時反灰，勾選狀態仍顯示）、避免把敏感字詞排在前面、動漫與遊戲詞（開關） | `Shell` 現有的 setter，和選單相同 |
| 外觀 | 候選窗方向（分段控制「橫排／直排」，放在玻璃深淺之前；下一次出現的候選窗用新的方向，開著的不變；`docs/contracts/candidate-vertical.md` §2.1） | UserDefaults 鍵 `candidateVertical`（Bool，沒設過是 false＝橫排）；`Shell.applyCandidateVertical`，核心每次建 engine 與設定改變時收到 |
| 外觀 | 候選窗玻璃深淺（滑桿 0–1，兩端是 Syrtis 的圖示：左邊中空的 `rectangle.on.rectangle`、右邊實心的 `rectangle.fill.on.rectangle.fill`，只是裝飾、VoiceOver 不讀） | UserDefaults 鍵 `glassTint`（Double，沒設過是 0） |
| 選字記憶 | 不要備份選字記憶（開關）、清除選字記憶…（按鈕，走現有的確認視窗） | `Shell` 現有的路徑 |

- **狀態列**：「選字記憶」區塊最上面只顯示「學習已暫停（安全輸入）」（`IsSecureEventInputEnabled`，開窗時與每次重新讀取時查）與「選字記憶無法存檔」（`Shell` 的 learningUnavailable）。「學習已暫停（此 App）」取自某一個輸入 session 的 client，設定視窗不屬於任何 session，所以只留在選單，不在視窗顯示。
- **同步**：`Shell` 在任何設定改變時發一個程序內通知，設定視窗收到就重新讀取 `Shell` 已快取的設定值（排列、四個開關、玻璃深淺、候選窗方向）。外部狀態（安全輸入、備份旗標、無法存檔）沒有通知，只在開窗與視窗成為 key window 時讀；視窗自己改備份旗標或清除之後也立刻重讀。選單每次打開時本來就會重讀，不需要改。
- **之後的重構（不在這一片）**：用 `@Observable` 的 Shell 狀態取代 `SettingsModel` 的鏡像與通知。

### 2.3 玻璃深淺

- **儲存**：照 `Shell.swift` 現有的模式，`ShanjieKit` 加 `GlassTintStore` 協定與測試用的 `MemoryGlassTintStore`；App 端在 `main.swift` 加 `DefaultsGlassTintStore`，鍵 `glassTint`（Double，沒設過是 0）。`Shell` 持有它，和其他設定一樣有讀取值與 setter。
- **換算**：`ShanjieKit/GlassTint.swift` 的純函式 `GlassTint.tint(value:dark:) -> GlassTint.Tint?`：
  - 值先 clamp 到 0…1（NaN 當 0）；結果是 0 時回 `nil`（不設色調）；
  - 否則回（黑或白, 不透明度）：深色外觀黑、淺色外觀白，不透明度＝值 × `GLASS_TINT_MAX`；
  - `GLASS_TINT_MAX`＝0.5，具名常數，註解寫出處：Syrtis 維護者看實機挑的，沒有對蘋果截圖量過。
- **套用**：候選窗（`CandidatePanelAdapter`，App target）每次顯示時，用目前的值與外觀（照現有規則跟著 App，s3b2 §10）交給 `GlassTint.Applier`：值有變才賦值，從大於 0 改回 0 時回「清除」、把 `NSGlassEffectView` 的色調設成 nil；沒有變就不賦值（避免 s3b2 記過的閃爍），所以從沒上過色的面板在 0 時完全不動。`glassTint` 是 0 時的外觀和現在逐位元相同（預設仍是實測的蘋果注音）。
- **什麼時候看得到**：改值之後，下一次出現的候選窗用新的值；還在畫面上的候選窗若有，也立刻更新。推論（沒量過）：點設定視窗會讓輸入法自己的程序到前景，原本的 App 失去焦點、組字被送出、候選窗收起，所以拖滑桿時通常看不到候選窗；由實機確認。
- **預覽（第一輪實機回饋）**：滑桿下面放一條範例候選列（「1 善　2 解　3 輸入法」，第一個被選取）。它是 App target 的 `SampleCandidateBar`，用和真的候選窗一樣的東西：`NSGlassEffectView`、`CandidateCells` 的格子、`Metrics` 的列高與間距與圓角、`GlassTint.Applier`（決定指派什麼，含 nil）、`GlassTint.isDark`（黑或白）。不同處：沒有展開用的箭頭（沒有東西可展開）。拖滑桿時 `SettingsModel.glassTint` 每次變就更新；外觀跟著它自己的 effective appearance（視窗的淺色或深色）。決定放在 ShanjieKit（`isDark`、`Applier`、`tint`）並有測試，玻璃本身的樣子由使用者實機確認。
- `NSGlassEffectView` 的色調用哪個屬性設，實作時查 SDK；它在 macOS 26 的效果沒有實測（推論），由使用者實機確認。

## 3. 驗收

1. **殼層測試**（`ShanjieKitTests`；設定視窗的 view model `SettingsModel` 放在 `ShanjieKit/Settings.swift`，SwiftUI 畫面在 App target 只綁定它）：
   - 每個控制項改的是和選單同一個 store 與 setter：視窗切換「即時預測」後，`Session.menu` 的勾選跟著變；選單切換後，view model 收到通知、值跟著變。
   - 排列選擇器呼叫 `selectLayout`；動漫與遊戲詞開關會重建引擎，和選單相同。
   - `MemoryGlassTintStore` 沒設過時 `Shell` 讀到 0；`GlassTint.tint`：NaN、負數、大於 1 都會 clamp；值 0 回 nil；值 1 回不透明度 0.5；深色外觀黑、淺色白；同一個呼叫序列從 0.6 改回 0，第二次回 nil。
   - 狀態列：安全輸入與無法存檔照規則出現；「此 App」那一列在視窗裡不出現，這是結構上的：`SettingsModel` 沒有對應的屬性、`SettingsForm` 也沒有對應的元件，所以不寫測試（沒有東西可以斷言）。
   - 選單多一項「善解設定…」，其餘項目與順序不變（既有的選單測試只加這一項）。
2. **突變**（每一項都要讓某個測試失敗；build error 不算；都在 `ShanjieKitTests` 碰得到的程式裡）：
   - 視窗的某個開關呼叫的 setter 或 store 和選單不同；
   - 不發同步通知；
   - `GlassTint.tint` 不 clamp；
   - 值 0 時回非 nil。
3. `make test`、CI 全綠。
4. **實機**（使用者，每次 ≤ 15 秒的部分由 `imeshot` 做）：
   - 從選單打開，視窗在最前面；點一下就能操作（是否一打開就是 key window 一併記下）；
   - 每一項改了都有效果，選單的勾選跟著變；
   - 改玻璃深淺，回到 App 打字，看候選窗的深淺；深色與淺色 App 各看一次；再改回 0，候選窗回到原本的樣子；
   - 拖滑桿時候選窗是不是被收起（§2.3 的推論）記下來；
   - 關窗後焦點回到原本的 App。

## 4. 範圍外

- 系統設定整合（§0：沒有公開的方法）。
- 聊天 App 清單、學習 denylist 的編輯介面；組字區上限的設定（之後的片）。
- 精簡選單（之後看使用者的意見）。
- 網站與說明文件的截圖。

## 5. 停止條件、預算、限制

- **停止條件**：
  - 實機上視窗帶不到前面、或不是 key window：先照 §2.1 的備案改契約再做；
  - 玻璃色調在 26 上沒有效果或造成閃爍（s3b2 記過的閃爍問題）；
  - 設定視窗開著時打字或候選窗的行為有任何改變。
- **預算**：executor 實作 1 次＋修正 1 次；外觀照使用者的實機回饋收斂，每一輪只改指出的地方。
- **限制**：agent 不安裝、不啟動 App、不呼叫 TIS、不在 .app 裡執行任何東西、不碰 `~/Library` 與鑰匙圈。

## 6. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| plan-verifier 第 1 次 REVISE（2026-10-10） | 1. 玻璃色調的測試寫不進 `ShanjieKitTests`（候選窗在 App target），沒有 store、換算函式沒有位置，從大於 0 改回 0 沒有規則，兩個突變抓不到；2.「打字中拖滑桿、候選窗跟著變」大概做不到（點設定視窗會讓原本的 App 失去焦點），也沒標成推論；3.「此 App」的學習暫停來自 session 的 client，設定視窗沒有 client | FIX：1 → §2.3 加 `GlassTintStore`、`GlassTint.tint` 純函式、每次套用連 nil 一起設，§3 改測試與突變；2 → §1、§2.3、§3.4 改成「下一次出現的候選窗」並標推論；3 → §2.2 只顯示安全輸入與無法存檔，「此 App」留在選單 |
| plan-verifier 第 2 次 READY（2026-10-10） | READY（第 1 次 REVISE 的三項已處置） | 狀態改為 READY，開始實作 |
| 實機檢查（2026-10-10） | 使用者點「善解設定…」看不到視窗：視窗有開、層級是一般視窗，`activate()` 被拒絕，被前景 App 蓋住 | 修訂 §2.1：`level = .floating` 加 `orderFrontRegardless()`；§3.4 的「是 key window」改為「在最前面、點一下可操作」 |
| 本地 /code-review（2026-10-10） | 滑桿不即時作用於已顯示的候選窗；關窗在非作用中時搶焦點；狀態列在成為 key 時不重讀；清除色調的 nil 路徑在 App target 且沒測試；`glassTintStore` 有預設值；`applyDemote`／`applyPrediction` 無 owner 時吞掉 `.failed`；每次按鍵讀 UserDefaults、每次變更讀外部狀態；寬度魔術數字；SDK 註解；重複的程式；無意義的測試 | FIX：`CandidatePanel.setGlassTint` 與 `GlassTint.Applier`（App 只照它的回傳賦值，含 nil）、`NSApp.isActive` 才還焦點、`windowDidBecomeKey` 重讀、`glassTintStore` 必填且 `Shell` 快取、無 owner 時記錄並丟掉組字、`SettingsModel` 延後建立並分成 `refreshSettings`／`refreshExternal`、`Shell.confirmAndClear` 與 `changed()` 合併重複、刪除 `testThereIsNoThisAppRow`。延後：`@Observable` 重構（§2.2） |
| 第一輪實機回饋（2026-10-10） | 視窗出現在螢幕上方、不在中間；要 Liquid Glass 視窗背景；滑桿下要有即時預覽 | §2.1 置中與玻璃背景、§2.3 預覽；`GlassTint.isDark` 抽到 ShanjieKit 讓候選窗與預覽共用，加測試。玻璃的外觀、置中的位置與預覽的深淺是否和真的候選窗一致，等使用者實機看 |
| 第二輪實機驗收（2026-10-10） | 使用者：「善解設定驗收OK」（置中、Liquid Glass 背景、預覽、各項設定） | 驗收通過 |
| fresh verifier（2026-10-10，676bae8） | CONFIRMED；P3 文件狀態沒跟上實機結果、P4 §2.3「套用」的寫法和 `Applier` 不一致、P4 無 owner 時的失敗路徑沒有測試 | 文件兩項修正；無 owner 失敗路徑核心無法按需失敗，只靠讀程式確認，記在這裡 |
| 第三、四輪實機（2026-10-10，v0.4.0 前） | 滑桿兩端改成 Syrtis 那樣的圖示（「左邊應該是中空的」）；Liquid Glass 太透，要較平的玻璃；「標題列不要做那麼透」 | 圖示放在滑桿兩旁的 `HStack`（放進 `Slider` 自己的兩端標籤時，在分組的 `Form` 裡兩個都變實心）；背景改成 `.popover` 毛玻璃並延伸到標題列底下。使用者：「設定面板OK」。滑桿兩端的圖示改成不給 VoiceOver 讀（滑桿本身有標籤；/code-review） |
