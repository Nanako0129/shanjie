# 單按 Shift 切換中英（英數模式）

狀態：草稿（2026-10-27），plan-verifier 第一次 REVISE 後修訂（改用核心狀態 `set_ascii`，不加按鍵位元；切換時各種狀態怎麼處理；提示自己一個視窗；設定關掉時清掉狀態），待第二次審查。前提：中英混打回退（PR #319，`zhen-revert.md`）先合併；這一片從那之後的 main 開分支（英數模式在組字區有字時沿用它的英文段）。

## 0. 起因與維護者的決定（2026-10-27）

- Discord 開發串兩位使用者（從 Windows 的微軟注音、唯音過來）希望單按 Shift 切換中英。查到的參考（網路教學文章與原始碼，不是官方文件）：Windows 的微軟注音預設單按 Shift 切換中英，設定可關；唯音（vChewing）有用 Shift 開關「英數模式」的功能，切換時跳出「英數模式 開／關」的提示，並排除「Shift 是跟著打字一起按」的情況、Caps Lock 燈亮時無效；macOS 內建注音沒有這個選項。
- 維護者：「做吧，單按shift預設開」。選擇題：(1) 組字區有字時單按 Shift **保留組字區**，之後打的字母接在後面當英文，Enter 一起送出；(2) 切換時**游標旁短暫顯示「英」／「中」**；(3) 英數模式**每個 App 各自記**。
- **怎麼看到**：維護者在自己的 Mac 單按一下 Shift，游標旁出現「英」，接著打 `hello` 直接是英文；再單按一下出現「中」，打注音照常；在「我要」還沒送出時單按 Shift、打 `ok`、Enter，送出「我要ok」。換到另一個 App 時是那個 App 自己上次的狀態。

## 1. 什麼算「單按 Shift」（殼）

- 殼的 `recognizedEvents` 加上 `flagsChanged`。
- **按下**：左或右 Shift 從放開變按下，而且當下沒有其他修飾鍵（Control、Option、Command、Fn）、Caps Lock 燈沒亮。記下按下的時間。
- **放開**：同一個 Shift 放開時，只有在下面全部成立時才切換：
  1. 按下到放開之間沒有任何 `keyDown`（Shift＋字母、Shift＋方向鍵都不算）；
  2. 沒有按過別的修飾鍵；
  3. 期間沒有滑鼠按下（用 `CGEventSource.secondsSinceLastEventType(.combinedSessionState, …)` 查左、右、其他鍵的滑鼠按下距今的秒數，比按住的時間短就是期間按過：Shift＋點擊選取文字不切換；不需要額外權限）；
  4. 按住的時間 ≤ `SHIFT_TAP_MAX`（具名常數 0.5 秒；註解寫明：沒有參考實測，是避免「按住 Shift 想打大寫又放棄」也切換的推論值，照實機回饋調）。
- 偵測寫成一個不碰 AppKit 的小狀態機（`ShiftTap`：餵入「Shift 按下／放開、其他修飾鍵、keyDown、滑鼠按下距今秒數、時間」，回傳要不要切換），讓測試能直接餵事件。
- 設定「單按 Shift 切換中英」（`shiftToggle`，**預設開**）關掉時：`flagsChanged` 一律不處理，**每個 App 的英數狀態全部清掉**、核心 `set_ascii(0)`，行為和今天逐位元組相同。

## 2. 英數模式的狀態與按鍵

- **狀態**：每個 App（`client.bundleIdentifier`）各自一個布林，存在殼的記憶體裡（輸入法程序重開就回到中文；新 App 預設中文）。沒有 bundle ID 的客戶端共用一個。殼在 `activate`、換 owner、切換時把這個 App 的狀態交給核心（`set_ascii`，§4）。
- **組字區是空的、英數模式**：所有按鍵直通給 App（`handle` 回 false，不經核心），包括標點與空白。
- **組字區有字、英數模式**（決定 1，保留）：殼照今天把鍵送進核心，但可印字元的鍵（`event.characters` 是一個 ASCII 0x21–0x7E 的字元，沒有 Control／Option／Command）送 `ShanjieKey(kind: .char, ch: 那個字元, modifiers: 0)`：字元由殼照目前的鍵盤配置從 `event.characters` 取（所以 Shift＋`1` 是 `!`、Shift＋`/` 是 `?`），不是鍵帽。核心在 ASCII 狀態下把它原樣接進英文段（§4）。空白、Enter、Backspace、Esc、方向鍵照今天組字區的規則（空白結束英文段成 token；Enter 送出整個組字區）。組字區送出或清空之後，下一個鍵照「組字區是空的」直通。
- **核心沒有英文段可用時**（倚天排列、可省略韻母開著、或中英混打關著：核心的 `shadow_on()` 是 false）：組字區有字時切到英數，殼先照 Enter 送出組字區（`finish(mode: 0)`），再進英數模式（組字區變空，之後直通）。
- **回中文**：再單按 Shift。
- Caps Lock：燈亮時單按 Shift 不切換；英數模式本身不改變 Caps Lock 的行為（Caps Lock 鍵照今天直通，規則 1）。
- 安全輸入（密碼欄）：今天殼不送鍵給核心的情形照舊；英數模式不改變。

## 3. 提示

- 每次切換顯示「英」或「中」，`SHIFT_HINT_SECONDS`（具名常數 1.0 秒）後淡出（系統預設的動畫曲線）。
- **自己一個視窗**（不用候選窗那個 `PanelWindow`，候選窗或英文段提示開著時兩個都看得到）；外觀用候選窗的玻璃面板（`s3b2-glass-panel.md`）、只放一個字，字級、留白放具名常數，註解寫「照候選窗的值」。
- **位置**：錨在客戶端游標的矩形：組字區有字時用標記文字的矩形（今天候選窗用的那個），組字區空時用 `selectedRange` 的位置向客戶端要矩形（IMK 的 `attributes(forCharacterIndex:lineHeightRectangle:)`）；放在這一行的**上方**、左緣對齊游標（候選窗在下方，不重疊）。客戶端給不出矩形（零矩形或例外）時**不顯示**，不退到畫面角落或上一次的位置。
- 實機回饋再調外觀（`.agent-local/CLAUDE.md`「外觀與行為」：每輪只改維護者指出的地方）。

## 4. 核心與 FFI

- 新函式 `shanjie_engine_set_ascii(ShanjieEngine *engine, int32_t on)`（回傳碼照既有慣例：0 成功、1 NULL、2 `on` 不是 0／1）；不加按鍵位元（位元 4 已是 Caps Lock，`MOD_*` 不動）。
- **`set_ascii(1)`**（組字區有字時；`shadow_on()` 是 false 時殼不會在組字區有字時呼叫，見 §2）照順序：
  1. 候選窗或預測列開著 → 關掉，不選；
  2. 數字串開著 → 照數字層的邊界規則結束（`digits.md`：緩衝吐成 token）；
  3. 有未完成音節 → 影子的鍵照欄位順序成為英文段的開頭（和 Shift＋字母開段相同，`start_run` 的做法：打 ㄏ 再切英數，段是 `c`）；
  4. 沒有開著的英文段 → 在游標處開一段（空的，下一個字元進來才有內容）。
- **ASCII 狀態下的鍵**：`kind == .char` 而且 `ch` 是 0x21–0x7E → 原樣接進英文段（不當注音鍵、聲調鍵或標點；不觸發回退或切分；段長上限照英文段的 32 字）；其他鍵照今天的規則（空白結束段成 token、Enter 送出、Backspace 刪段的最後一個字元…）。
- **`set_ascii(0)`**：開著的英文段照空白的規則結束成 token（不插空白），回到一般規則（之後的注音鍵照常）；組字區其他部分不動。
- 組字區是空的時 `set_ascii` 只記下狀態，不改組字區。
- `docs/contracts/s3a.md` §3.1 加 ASCII 狀態的規則（上面兩段）；`core/include/shanjie.h` 加函式說明。

## 5. 驗收

- **核心測試**（`core/tests/engine_english.rs`）：
  - 「我要」＋`set_ascii(1)`＋`o`、`k`＋Enter → 送出「我要ok」；`english_reverted() == None`；
  - 「我要」＋`set_ascii(1)`＋`ok`＋`set_ascii(0)`＋`j i 3`＋Enter → 送出 `ok` 接中文（ㄨㄛˇ 的解碼），不是 `okji3`；
  - 打 ㄏ（`c`）再 `set_ascii(1)`＋`o` → 英文段 `co`；
  - 候選窗開著時 `set_ascii(1)` → 窗關掉、組字區不變；預測列進入後 `set_ascii(1)`＋`1` → 不選預測，`1` 接進英文段；
  - 數字串開著（`1 2`）時 `set_ascii(1)` → 先吐成數字 token；
  - ASCII `!`、`3`（聲調鍵的位置）、`/` 原樣接進段；
  - 組字區空時 `set_ascii(1)` 不改任何東西；沒有呼叫 `set_ascii` 時所有既有測試不變；FFI 的 `on` 不是 0／1 回 2。
- **殼的單元測試**（`ShiftTapTests`、`SessionTests`）：
  - 單按 → 切換；Shift＋字母、Shift＋方向鍵、Shift＋其他修飾鍵、期間滑鼠按下、按住 > 0.5 秒、Caps Lock 燈亮 → 不切換；
  - 兩個 App 各自記（App A 切英文、換到 B 是中文、回 A 是英文）；
  - 英數模式組字區空時 `handle` 回 false（直通）；組字區有字時 Shift＋`1` 送出 `ch == "!"`；
  - 倚天排列（或可省略韻母、或混打關）組字區有字時切英數 → 組字區先送出；
  - 設定關掉 → `flagsChanged` 不處理；App A 原本是英數，關掉後 A 的字母鍵照注音送進核心；預設是開。
- **回歸**：`make test-golden`、`make test-rust`（188）、`make test-swift`、dev302／打字測驗／錯字回報 `evalstats compare` 逐列相同（選字不動）。
- **VM 實機**（main，tart 繁中 VM，`imeshot` 每次 ≤ 15 秒）：剛打開的 App、組字區空時單按 Shift，「英」出現在文字游標旁、打 `hello` 直通；再按出現「中」；「我要」＋單按 Shift＋`ok`＋Enter 送出「我要ok」；候選窗開著時單按 Shift，兩個視窗都看得到；Shift＋點擊不切換；兩個 App 各自記。
- **合併關卡**：程式改動（`/code-review`＋fresh verifier＋CI＋188 完整 Rust 測試）。
- **維護者實機**：合併、裝好之後照 §0「怎麼看到」打一次；提示的外觀照回饋一輪一輪調。看到之前不寫「完成」。

## 6. 文件同一個 PR 改

- `docs/contracts/s3a.md`（ASCII 狀態、英數模式）、`core/include/shanjie.h`（`shanjie_engine_set_ascii`）、`docs/contracts/settings-window.md`（新設定）、README 的功能說明、`docs/PLAN.md`、研究紀錄。

## 7. 停止條件與範圍外

- **停止**：`flagsChanged` 在某些 App 收不到（實機看到就停下來回報，不自己加例外清單）；`CGEventSource` 查滑鼠的方法需要額外權限。
- **範圍外**：遊戲等 App 的例外清單（唯音有；實機有人回報再做）；英數模式的全形英數；選單打勾顯示狀態；把狀態存進偏好檔跨重開保留。
- **限制**：agent 不安裝、不啟動 App、不執行 `.app` 裡的程式、不呼叫 TIS 或 lsregister、不碰 `~/Library` 與鑰匙圈、不 push；Mac 只跑改到的測試檔。
