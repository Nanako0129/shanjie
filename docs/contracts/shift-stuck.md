# 單按 Shift 偶爾卡在英文（產品片 SSTK）

狀態：草稿（2026-11-03）。

## 0. 起因與維護者的決定

- 2026-11-03 兩位使用者回報（v0.6.1）：單按 Shift 切換中英，偶爾卡在英文；再按 Shift 切不回中文，游標上方的「中／英」提示也沒出現；用 Ctrl+Space 切到系統英文再切回善解，有時能解除。其中一位已把 Shift 切換關掉。維護者自己也遇到一次：「我當時是打『....(shift按著打)CP』然後在單按shift就卡死了」「沒跳提示一直在英文」「但是很偶發」。
- main 在殼的單元測試（真 `NSEvent`、單一控制器）照這個順序重現不了：英→中→英都正常、提示都出現，切回中文後打注音也是中文（「你CP好」）。
- 三位顧問各自讀程式盤點（2026-11-03），一致把兩條路徑排在前面，都要實機紀錄才能完全證實：
  - (A) Shift 的 `flagsChanged` 落到另一個控制器，它讀到的 bundle ID 是 nil，切換的是共用的 `""` 那一格，提示畫在讀不到游標的 client 上所以不顯示；下一個按鍵回到原控制器，`claim()` → `syncAscii(App)` 又照原 App 的紀錄拉回英文。
  - (B) 另一側 Shift 的裝置位元殘留在旗標裡（藍牙漏了放開、遊戲吃掉事件）：`ShiftTap` 看到兩個 Shift 都按著，每一次單按都不算，直到那一側再按放一次。
- 維護者：「這個狀況很少見幾乎不會發生，我建議先出貨，然後是否可以考慮匿名收集」。這一片只修 (A)(B)、不等實機紀錄，跟下一版出貨；匿名收集另開一片（隱私設計＋security-reviewer）。

## 1. 修 (A)：一個控制器的切換只動它自己那個 App

- `Session` 新增一個欄位 `knownBundle`：最近一次讀到的非空 bundle ID（`activate`、`handle` 換 owner、`toggleAscii` 讀 bundle 的那三處，讀到非空就更新，讀到 nil 不覆蓋）。既有的 `bundle` 欄位與它的瀏覽器首音節語意（browser-first-syllable §4）不動。
- 只改英數狀態用的 App 鍵：`activate` 與 `handle` 換 owner 時呼叫的 `syncAscii`、以及 `toggleAscii` 切換的 `asciiApps` 鍵，讀到的 bundle 是 nil 或空字串時改用 `knownBundle`（PR #355 審查後：空字串和 nil 一樣當成沒讀到；`syncAscii` 在設定關著時也照樣記下 `knownBundle`）；`knownBundle` 也是 nil（這個 session 從來沒讀到過 bundle）才用共用的 `""`（契約 shift-toggle §2「沒有 bundle ID 的客戶端共用一個」照舊）。
- 明寫不變（照今天傳原始讀到的值，nil 就是 nil）：所有 `applyProfile(bundle:)`（`activate`、`handle` 換 owner、`toggleAscii` 換 owner、`Shell.reapplyProfileToOwner`）以及它寫入的 `bundle` 欄位與瀏覽器首音節規則（`hidesFirstSyllableRow`）；`send` 的學習閘門與前文（`applyLearningGate`、`leftContext`）、選單的 `learningPause`（Learning.swift）：nil 照舊當成「暫停學習」；`toggleAppChat`（Shell.swift）與選單的「這個 App 用聊天設定」項目（Learning.swift `menu`）：nil 照舊沒有這一項、不動作。這些地方都不更新 `knownBundle`。
- 其餘不變：切換仍先 `claim()`；提示照舊（讀不到矩形就不畫）。

## 2. 修 (B)：另一側 Shift 早就按著不算破壞

- `ShiftTap` 在按下時記下另一側 Shift 的狀態；放開時，另一側的狀態和按下時相同就照常判斷單按（早就殘留在旗標裡的位元不再擋住），按下到放開之間另一側變了才不算（例如左按下、右按下、左放開：不切換；右先放開則照 §2 的取捨會切換）。
- 其餘條件（其他修飾鍵、Caps Lock、keyDown、滑鼠、`SHIFT_TAP_MAX`）不變。
- 已知取捨：真的按住一側 Shift、再單按另一側，會切換（今天不會）。這個動作沒有別的用途，接受。

## 3. 測試（殼；Mac 跑 `make test-swift` 的相關檔，CI 跑全部）

- (A) 兩個控制器共用一個 shell：A（bundle `com.example.A`）activate、單按切成英文；B 的 client bundle 讀成 nil（但 B 之前 activate 時讀到過 `com.example.A`）收到單按；A 接著打字：狀態是中文，`asciiApps` 裡沒有 `""`。另一個測試：從來沒讀到過 bundle 的客戶端照舊共用 `""`。
  測試支援：`FakeClient.bundle`（`macos/Tests/ShanjieKitTests/Support.swift`）改成可以在測試中途改的 `var`。
- (B) `ShiftTapTests` 新增兩個，各是一串（keyCode、裝置位元、預期回傳）：
  1. 右 Shift 位元一直殘留：左按下（56，位元 0x6）→ false；左放開（56，0x4）→ **true**（修正前是 false：這條在修正前失敗）。
  2. 真的兩個 Shift 一起按：左按下（56，0x2）→ false；右按下（60，0x6）→ false；左放開（56，0x4）→ false；右放開（60，0）→ false（回歸守門：修正前後都通過）。
- 唯一改變的既有斷言：`ShiftTapTests.testBothShiftKeysAreNotATap` 第 98 行改成 `XCTAssertTrue`（按住左 Shift、單按右 Shift 現在會切換，§2 的取捨），第 99 行照舊 false；測試名稱改成說明新行為。其餘 `ShiftTapTests`、`ShiftToggleSessionTests` 全部照舊通過。

## 4. 驗收

- §3 在 Mac 通過、CI 全綠；兩個 fresh verifier（程式審查、結果驗收）沒有未處置的問題。
- 實機（不是這一片的合併關卡）：發版後照平常用、不刻意重現；之後再有人回報卡在英文，研究紀錄記下當時的 App、動作與是否出現提示，作為匿名收集那一片的依據。發版說明不寫「修好了卡住」，寫「修正兩個可能讓單按 Shift 卡在英文的情況」。
- 同一個 PR 改：`docs/contracts/shift-toggle.md` §1 的「放開」條件與 §2 的「沒有 bundle ID 的客戶端」那句、研究紀錄一段。

## 5. 範圍外

- bundle 讀成 nil 時 `applyProfile` 把設定檔切成 auto 下 nil 對應的書面（今天就如此，和卡在英文無關，另案）；匿名收集（另一片）；左右 Shift 分開設定（使用者提議，等 issue）；遊戲的例外清單；IMK 把事件送到哪個控制器的實機量測。

## 6. 停止條件

- 修 (A) 讓任何既有測試的行為改變（除了 §3 列的）：停下報告。
- 實作時發現 §1 沒列到的地方也把 nil bundle 當成 App 鍵或設定檔用：列出來問維護者，不擴大範圍。
