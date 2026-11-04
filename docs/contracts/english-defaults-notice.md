# 英打設定預設關與更新後的說明視窗（2026-11-04）

## 0. 起因與要做到什麼

維護者 2026-11-04 傳了設定頁「選字」區塊的截圖（「重打同一個位置算英文」「單按 Shift 切換中英」「中英文之間自動加空格」三個開關都是關），原話：「以後預設改這樣避免中打輸入有問題，下一版更新要跳通知說明英打設定具體有什麼影響，主要我認為要強調的是，人打錯注音習慣會直接接著打下個新的注音所以才會導致變成英打這是習慣問題」。接著的選擇題：通知用「善解自己的小視窗」；已經在用的人「所有人都重設成關」。

要做到：

1. 「重打同一個位置算英文」（鍵 `englishOverwrite`）與「單按 Shift 切換中英」（鍵 `shiftToggle`）沒有存值時預設關。「中英文之間自動加空格」（`cjkSpacing`）本來就是沒存值時關，不動。
2. 第一次執行含這一片的版本時，不管之前存的是什麼，這兩個值一律寫成關（只做一次）。
3. 從舊版更新上來的人，在這之後第一次開始打字時看到一個小視窗，說明這兩個設定的影響，重點是打錯注音後直接接著打下一個注音的習慣。新安裝的人不跳。

## 1. 預設值

- `DefaultsEnglishStore.englishOverwrite`、`DefaultsShiftToggleStore.shiftToggle`（`macos/Sources/Shanjie/main.swift`）與 `Shell` 讀它們的地方（`englishStore.englishOverwrite ?? true`、`shiftToggleStore.shiftToggle ?? true`），沒有存值時改成 `false`；`Shell`、`Settings` 的初始值同步改成 `false`。註解裡「absent means true」改成 false，並寫出處（本契約）。
- 其他英文相關的選項（中英混打本身、依按鍵順序判斷英文、快打時鍵序顛倒不當英文）不動。
- 設定頁兩個開關的說明文字不變（它們說的是開了之後的行為）。

## 2. 一次性重設

- 新的布林鍵 `englishDefaults20261104` 在同一個 defaults domain。**執行位置**：`main.swift` 裡，在建 `Shell`、`Updates`、`Updater` 之前（它們之後會寫 `update*` 等鍵，順序不能反）。沒有這個鍵 → 把 `englishOverwrite` 與 `shiftToggle` 都寫成 `false`，再寫這個鍵為 `true`；有這個鍵 → 什麼都不做。之後使用者在設定裡打開，就照存的值（不會再被重設）。
- **判斷「從舊版更新上來」**（重設之前看，寫死，實作者不另外選）：下列任一存在就是舊使用者，同時寫 `englishDefaultsNoticePending = true`：
  1. domain 裡有 `main.swift` 任何一個設定 store 用的鍵（實作時從 `main.swift` 逐一列出、寫成常數清單，並在測試裡核對清單沒有漏掉 store）；
  2. domain 裡有 `updateAutoCheck`、`updateLastCheck`、`updateStatus` 任一個，或任何 `SU` 開頭的鍵（Sparkle）；
  3. 容器的 `Application Support/shanjie/` 底下有任何檔案（學習資料）。
  三者都沒有＝全新安裝，不寫待顯示旗標（兩個值照樣寫成關，無害）。〔注意：從沒改過設定、用開發版（沒有更新程式）、也從沒打出學習資料的舊使用者會被當成全新安裝；這種人兩個值照樣被重設成關，只是不跳視窗。〕
- 重設與待顯示旗標的邏輯放在 ShanjieKit 可測的地方（輸入：目前 domain 的鍵集合與兩個值；輸出：要寫的值），`main.swift` 只負責讀寫 UserDefaults。

## 3. 說明視窗

- **何時出現**：`englishDefaultsNoticePending` 為真時，`activateServer` 之後收到的第一個 keyDown 觸發顯示；這個鍵照常交給 `Session` 處理，不被吃掉、不延遲。同一個程序裡視窗已顯示時不重複顯示。
- **收起與再跳**：按「知道了」、按「打開設定」或按視窗的關閉鈕 → 收起並清掉旗標，之後不再出現。都沒按就切走（`deactivateServer`）→ 視窗收起、旗標保留；之後再一次 `activateServer` 後的第一個 keyDown 再跳（同一個程序或下次程序啟動都一樣）。
- **樣式**：善解自己的小視窗，不搶走使用者正在打字的 App 的鍵盤焦點（`NSPanel` 的 nonactivating 樣式；能點按鈕）。位置在螢幕上方中間或游標附近，不遮住組字區。外觀用系統預設元件與動畫。
- **按鈕**：「打開設定」：打開善解的設定視窗並直接切到側欄的「選字」頁（`SettingsWindow` 的 `page`）；「知道了」：關閉。不搶焦點、點得到按鈕的視窗做法照 `InputController.swift` 既有的 `PanelWindow`（`canBecomeKey` 為 false）。
- **文字**（初稿，維護者可改字；改字不必重審契約）：
  - 標題：「英打相關設定改成預設關」
  - 內文：
    - 「這一版把下面兩個設定都改成關，你之前的設定也一起重設了。」
    - 「重打同一個位置算英文：開著時，在已經有注音的位置再按一個鍵，善解會把這一串當成英文。很多人打錯注音時，習慣不刪掉、直接接著打下一個注音，這個習慣剛好會被當成英文，看起來就像中文打到一半突然變成英文。常打 ok、rust、gta6 這類字的人，可以再打開。」
    - 「單按 Shift 切換中英：開著時，單按一下 Shift 就會切成英文；打字時不小心碰到 Shift，就會在沒注意時切走。」
    - 「要再打開，到「設定 → 選字」。」

## 4. 怎麼看到它有效（驗收）

1. **單元測試**（`make test-swift`）：
   - 重設邏輯：空 domain、沒有學習檔 → 兩個值寫成關、不寫待顯示旗標；有舊設定鍵、兩個值都是開 → 兩個值寫成關、寫待顯示旗標；domain 只有 `updateLastCheck`（或只有 `updateStatus`、只有 `SU` 開頭的鍵）、兩個開關都沒存值 → 兩個值寫成關、寫待顯示旗標；domain 空但有學習檔 → 寫待顯示旗標；已有 `englishDefaults20261104` → 不動任何值（包括使用者之後自己打開的開）；鍵清單涵蓋 `main.swift` 所有 store 的鍵（測試核對）。
   - 預設值：兩個 store 都沒有存值時，`Shell` 的 `englishOverwriteOn`、`shiftToggleOn` 是 false。
   - 視窗決策：旗標為真時 `activateServer` 後第一個 keyDown 顯示，而且這個鍵照常交給 `Session`；已顯示時再來的鍵不重複顯示；按任一按鈕或關閉鈕後旗標清掉、之後不再顯示；沒按就 `deactivateServer` → 收起、旗標保留、下一次啟用後第一個鍵再顯示；旗標為假時不顯示。
2. **tart 虛擬機實機**（繁體中文系統）：前後兩個組建都用開發版 ID `com.nyanako.inputmethod.shanjie.dev`（`scripts/build-app.sh` 預設），才會讀到同一個容器 domain。舊版是 `origin/main` 的 `fe6b0371`，新版是這一片的 commit。兩個情境分開做、都要做：
   - (a) 舊版裝好後用 `defaults write` 把 `englishOverwrite`、`shiftToggle` 存成開；換裝新版 → 第一次打字跳出視窗、文字如 §3、正在打字的 App 仍保有鍵盤焦點（再按鍵仍進組字區）；按「知道了」後不再出現（重開輸入法程序也不跳）；設定頁兩個開關都是關。
   - (b) 舊版裝好後不改任何設定、打幾個字讓學習資料出現（或 `defaults write` 一個 `updateLastCheck`，模擬出貨版會自動寫的鍵）；換裝新版 → 同 (a) 跳出視窗、兩個開關是關。另驗一次「沒按就切走」：視窗出現後切到別的 App，視窗收起；切回來打字再跳。
   - (c) 全新的容器（清空）直接裝新版：不跳視窗，兩個開關是關。
   - 截圖放 session 暫存區，不進 repo。
3. **文件**：README、網站（`site/`）、`docs/contracts/zhen-revert.md`、`shift-toggle.md` 等寫到這兩個預設值的地方一起改（grep「預設開」與兩個設定名）；下一版的發版說明草稿要寫這個改變。

## 5. 範圍外

- 「中英文之間自動加空格」的值（本來就預設關，選擇題沒有包含它）。
- 中英混打本身與其他英文判斷選項的預設。
- 其他版本的更新說明機制（這一片只做這一則；之後要通用的「新版說明」另開一片）。
- 改核心行為：這一片只動殼與設定，核心不變，`docs/contracts/s3a.md` 與 `core/include/shanjie.h` 不改。

## 6. 停止條件

- 找不到不搶焦點又能點按鈕的視窗做法（在 tart 實測）：停下回報，問維護者改用別的形式。
- 「從舊版更新上來」在實機上判斷不出來（全新安裝也跳、或更新上來不跳）：停下回報。
