# S3b 契約：Swift 輸入法本體（最小可安裝版）

計畫見 `docs/PLAN.md` §S3b。核心的按鍵規則、C ABI 與 R2 規則見 `docs/contracts/s3a.md`，語言模型見 PLAN §S2c。本片只寫殼：把 macOS 的按鍵事件翻成 `ShanjieKey`、把 `ShanjieOutput` 畫到 App 上。結構參考小麥注音（MIT，本機 `~/side-project/ime-research/repos/McBopomofo`）。

## 1. 外觀參考（使用者 2026-10-03 提供的 macOS 27 內建注音截圖）

截圖只存在本機 `~/side-project/shanjie-private/s3b-reference/`（不進 repo）。觀察：

| 編號 | 畫面 | 觀察 |
|---|---|---|
| 1 | 橫式候選條（深色） | 膠囊形、半透明玻璃底；每個候選前有小號碼（次要文字色）；選取中的候選是系統強調色（藍）的圓角底、白字；一行 7–8 個；最右邊有展開箭頭 ⌄ |
| 2 | 展開後的網格 | 第一列有號碼，其餘沒有；可捲動；底部分頁「詞頻／部首／表情／拆字」 |
| 3 | 單字「一」 | 候選含表情符號（1️⃣）；候選窗開著時，組字區正在選的那一段有底色框 |
| 4 | 直式清單 | 每列號碼＋候選；右側有「常用」標籤，罕用字旁標注音；底部同樣的分頁 |

所有截圖的組字區都是**底線**。淺色模式與選單列圖示沒有截圖，安裝後由使用者對照。

**這一版做**：橫式候選條（截圖 1，不含展開箭頭）、組字底線、數字選字。**不做**（記為之後的 S3b-2）：展開網格與分頁、直式清單、表情候選、「常用」標籤與注音標示、組字區的分段底色框、設定頁。

## 2. 建置與產物

- Swift 程式放在 `macos/`（SwiftPM）。至少分成：
  - 可測試的邏輯（按鍵翻譯、輸出套用、設定判斷），不依賴 InputMethodKit 的執行環境，用假的 client 測試；
  - InputMethodKit 的薄殼（`IMKInputController` 子類別、`main.swift`）。
  - 如何連結 `target/release/libcore.a` 與 `core/include/shanjie.h`（module map 或其他方式）由 executor 決定，寫進 §8。
- `scripts/build-app.sh`：`cargo build --release -p core` → 建 Swift → 組出 `build/shanjie.app` → 簽章。不安裝、不執行 app。
- `shanjie.app/Contents`：
  - `MacOS/shanjie`
  - `Resources/`：`mcbpmf-data.txt`、`overlay-add.tsv`（從 `data/lexicon/` 複製）、`bigram.sjlm`（從 `data/lm/` 複製；不存在就建置失敗，訊息說明怎麼建）、選單列圖示、`zh-Hant.lproj/InfoPlist.strings`。
  - `Info.plist`：照小麥的鍵（`InputMethodConnectionName`、`InputMethodServerControllerClass`、`InputMethodServerDelegateClass`、`LSUIElement`、`ComponentInputModeDict`、`tsVisibleInputModeOrderedArrayKey`），bundle ID `com.nyanako.inputmethod.shanjie`。
  - **兩個輸入模式**：`com.nyanako.inputmethod.shanjie.standard`（顯示名「善解（標準）」）與 `com.nyanako.inputmethod.shanjie.eten`（「善解（倚天）」），`TISIntendedLanguage` 為 `zh-Hant`，`tsInputModeScriptKey` 為 `smTradChinese`。
- 選單列圖示：單色 template 圖，「解」字加圓角方框（呼應網站的印章），由腳本產生（例如用 CoreText 畫成 TIFF），不從網路下載字型或圖。
- 簽章：本機的 Apple Development 憑證（Team `2LJ882GPY8`），hardened runtime；不加 `disable-library-validation` 或其他放寬權限的 entitlement（R9）。不做公證。

## 3. 安裝（由使用者執行）

- `scripts/install-ime.sh`：把 `build/shanjie.app` 複製到 `~/Library/Input Methods/`（已有舊版就先結束舊行程再取代），再執行 `shanjie.app/Contents/MacOS/shanjie install`，用 `TISRegisterInputSource`／`TISEnableInputSource` 註冊並啟用兩個輸入模式（照小麥 `main.swift` 的 `install()`）。
- 腳本最後印出下一步：到「系統設定 → 鍵盤 → 輸入方式」確認「善解」已出現；沒出現就登出再登入。
- **agent 不得執行 `install-ime.sh`、`shanjie install`、或啟動 app**；只由使用者執行。

## 4. 行程與引擎

- 啟動時（IMK server 建立後）用 `Resources/` 建一個引擎（目前選的輸入模式的排列；第一次預設標準），接著 `shanjie_engine_load_lm(Resources/bigram.sjlm)`。任一步回傳非 0：記錄錯誤碼（不含路徑），引擎維持沒有 LM 的狀態仍可打字；建不出引擎時所有按鍵直通。
- **同一時間只有一個引擎**（每個約 230 MB）。切換輸入模式（標準 ↔ 倚天）時：`reset(0)` 送出目前的組字、釋放舊引擎、用新排列重建、重新載入 LM 與目前的設定。
- 所有 C ABI 呼叫都在主執行緒（IMK 的回呼執行緒）。

## 5. 按鍵翻譯

- 只處理 `keyDown`；`flagsChanged` 不送進核心（Caps Lock 切換由系統的「使用大寫鎖定鍵切換」處理，使用者 2026-10-03 選 Caps Lock）。
- `kind` 依 `keyCode`：Return／keypad Enter → ENTER、Space → SPACE、Delete(51) → BACKSPACE、Forward Delete(117) → DELETE、Esc → ESC、←→↑↓、Home(115)、End(119)、Tab。
- `CHAR` 的 `ch` 依 `keyCode` 查 **ANSI 實體鍵位表**取「不按 Shift 的 ASCII」（a–z、0–9、`` ` ``、`-`、`=`、`[`、`]`、`\`、`;`、`'`、`,`、`.`、`/`），不使用 `event.characters`，因為它受 Shift 與目前鍵盤配置影響。
- **表外的鍵**（數字鍵盤、功能鍵等）不送進核心：組字區有字時先 `reset(0)` 送出，然後回傳「不處理」；組字區空時直接回傳「不處理」。
- `modifiers`：Shift、Control、Option、Command、Caps Lock 依 `modifierFlags` 對到 bit0–4。

## 6. 輸出套用

- 回傳碼非 0：當作「不處理」直通，不記錄按鍵內容。
- `commit` 非空：`insertText(commit, replacementRange: NSRange(location: NSNotFound, length: 0))`。
- `preedit`：非空時 `setMarkedText`，整段單線底線，選取範圍 `NSRange(location: cursor_utf16, length: 0)`；變成空字串時用空字串清掉組字。
- `handled = 0`：先套用 commit 與 preedit，再回傳 `false` 讓 App 處理這個鍵。
- 收到輸出後在同一個呼叫裡把字串複製成 Swift `String`，立刻 `shanjie_output_free`。

## 7. 候選窗

- 先用 InputMethodKit 的 `IMKCandidates`，單列樣式（`kIMKSingleRowSteppingCandidatePanel`），顯示核心給的這一頁（≤ 9 個）與選取位置，號碼 1–9；位置在組字區下方。按鍵一律由核心處理，`IMKCandidates` 只負責顯示（不把按鍵交給它）。
- `candidate_count` 變成 0 就隱藏。
- 若 `IMKCandidates` 無法只當顯示用（例如會自己吃按鍵、選取無法同步），executor 停下來回報，不自行改做自建視窗；自建玻璃視窗是下一輪的事。

## 8. 設定判斷與隱私

- **聊天／書面設定**：每次 `activateServer` 或 client 改變時，用 `client.bundleIdentifier()` 查內建的聊天 App 清單：Discord（`com.hnc.Discord`）、LINE（`jp.naver.line.mac`）、訊息（`com.apple.MobileSMS`）、Slack（`com.tinyspeck.slackmacgap`）、Telegram（`ru.keepcoder.Telegram`）、WhatsApp（`net.whatsapp.WhatsApp`）、Messenger（`com.facebook.archon`）。在清單內用 chat，其餘用 formal，以 `shanjie_engine_set_profile` 傳**列舉**給核心；bundle ID 不傳給核心、不寫 log、不存檔。清單在這一版寫在程式裡，設定頁之後再做。
- **reset 時機**：`commitComposition(_:)` → `reset(0)` 並送出；`deactivateServer` → `reset(0)` 並送到原本的 client，隱藏候選窗；client 改變時同樣處理。
- **日誌（R2）**：只能用 `os.Logger`；組字、候選、送出內容、按鍵字元、bundle ID、路徑一律不寫進 log。不得用 `print`、`NSLog`、`debugPrint`、`dump`；`fatalError`／`precondition` 不得內插輸入文字。
- 這一版沒有學習、雲端、左文，所以 privacyGate（R3）沒有東西可擋；在 S4（學習）引入時實作。secure input 期間系統會讓密碼欄位跳過輸入法，由使用者實測確認。
- **自測**：`shanjie --selftest` 從 `Resources/` 建引擎、載入 LM、用標準排列打「你好」與 Enter，送出「你好」就 exit 0。自測不得呼叫 TIS 註冊、不得寫任何檔案、不得建立 NSApplication 或 IMK server。

## 9. 驗收

**agent 可做的（executor 做、verifier 重做）：**
1. `scripts/build-app.sh` 成功，產出 `build/shanjie.app`。
2. `plutil -lint` 通過；兩個輸入模式、bundle ID、`InputMethodConnectionName`、`InputMethodServerControllerClass` 都在；`Resources/` 有三個資料檔與圖示。
3. `codesign --verify --strict --deep` 通過；`codesign -d --entitlements -` 沒有放寬權限的 entitlement；hardened runtime 旗標有開。
4. Swift 測試（`swift test`，在 `macos/`）：
   - 按鍵翻譯：ANSI 表每個鍵、兩種排列的 37 個注音鍵與 5 個聲調鍵、各特殊鍵、修飾鍵位元。
   - 假 client：打「你好」＋Enter → `insertText("你好")` 且組字清空；打 ㄋㄧˇ＋空白 → 候選顯示（1–9 個）、按 2 → 組字更新、候選隱藏；帶 Caps Lock 的鍵 → 回傳 false 且輸出不變；組字中按表外的鍵 → 先送出再回傳 false；`commitComposition` → 送出並清空。
   - 設定：Discord 的 bundle ID → chat；TextEdit → formal；確認傳給核心的只有列舉。
   - 切換輸入模式：標準組字中切到倚天 → 先送出、之後倚天的鍵位生效。
5. **日誌行為測試（R2）**：測試經由殼的邏輯輸入一段標記字串（注音鍵打出的字）並送出，結束後 `/usr/bin/log show --last 5m --predicate '<測試行程>'` 找不到標記；**正向對照**：同一測試另用 `Logger` 以 `.public` 記一次標記，`log show` 必須找得到，否則測試無效（zsh 的 `log` 是內建指令，必須寫完整路徑）。
6. `build/shanjie.app/Contents/MacOS/shanjie --selftest` exit 0；執行前後 `~/Library/Input Methods/` 與 TIS 的輸入法清單沒有變化。
7. 核心的 `cargo test` 照舊全綠。

**使用者實測（安裝後，由使用者執行並回報）：**
8. 安裝：執行 `scripts/install-ime.sh`，「善解（標準）」「善解（倚天）」出現在輸入方式中。
9. 在 TextEdit、備忘錄、Safari 各打陷阱集前 10 句（main 會提供按鍵清單）：組字有底線、候選窗出現在下方、數字選字、Enter 送出。
10. 對照截圖 1：候選條的形狀、號碼、選取色、深淺色模式；不像的地方記下來，進下一輪。
11. Caps Lock 切到英文再切回；倚天模式打幾句；Safari 密碼欄位與終端機的 Secure Keyboard Entry 下不會出現組字。
12. 聊天 App（例如 Discord）與 TextEdit 打同一句，結果可能不同（聊天／書面設定）。

## 10. 範圍外

展開網格、分頁、直式、表情候選、標籤、分段底色框、自建玻璃候選窗（若 IMKCandidates 不夠用，下一輪）、設定頁、聊天 App 清單的修改介面、學習（S4）、一鍵校正（H）、雲端（S6）、公證與發布（S8）。
