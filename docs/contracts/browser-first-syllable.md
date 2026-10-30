# 瀏覽器裡第一個音節不顯示預測列

狀態：plan-verifier 第二輪 READY（2026-10-30），已實作。

## 起因

Discord 回饋（2026-10-29）：在 Google 文件打字時，候選列（含預測列）一開始跑到視窗左上角、被切掉一截。回報者的環境是善解 v0.6.0、macOS 27.2、Chrome 156.0.8078.48／.74，Crest 0.8.50 也一樣。

## 量到的原因（2026-10-30，tart 虛擬機，Chrome 156.0.8078.74）

量法：
- 以訪客身分編輯一份 Google 文件。
- 用 `--trace-startup=ime,input,disabled-by-default-ime` 錄 Chrome 的 trace。
- 偵錯版的殼把每次查詢的結果記進系統記錄。
- 表中的 `{a,b}` 是 trace 裡 `RenderWidgetHostViewMac::SyncGetFirstRectForRange` 記的 `requested range`（起點、終點）。

Docs 用的是 EditContext。它回給輸入法的組字範圍是 `{1,n}`，也就是組字前面多放了一格。

| 問法 | 殼拿到的 | Chrome 收到的查詢 |
|---|---|---|
| `attributes(forCharacterIndex: 0)` | 錯框：視圖原點，換成螢幕座標就是視窗左上角 | `{0,1}`，起點在組字範圍之前，Chrome 回過時的 `first_selection_rect`，也就是 `0,0 1x22` |
| `attributes` 索引 1 到組字長度＋1 | (0, 0)，等於沒有回答 | 沒有，IMK 沒轉給 Chrome |
| `firstRect(forCharacterRange:actualRange:)`，用 Swift 協定或直接取 ObjC 實作呼叫 | 未初始化的值（1.6e-314） | 沒有 |
| 不顯示的 `IMKCandidates`，`show` 之後讀 `candidateFrame()` | `{0,0} 51x266`，沒有定位 | 沒有 |

- trace 裡回對位置的查詢是 `{1,1}`，走 `GetTextSelectionBounds`，例如 `397,241`；它們不是殼發的。
- 蘋果注音按 ↓ 時問的是組字範圍內的字，例如 `{1,2}`，走 `GetFirstRectForCompositionRange`，位置也對，在全新分頁的第一次組字就對。
- 第三方輸入法經過 IMK 能用的三條路，量到都拿不到 Docs 裡的正確位置。
- 前兩片的做法這次都被推翻：
  - `candidate-requery`：重問，加上記住好位置；
  - `caret-line-rect`：加問游標所在處。
  - 兩片都不合併。

## 要做到什麼（維護者 2026-10-30 選定）

1. **瀏覽器清單裡的 App，組字區只有一個音節時不顯示未進入的預測列**。打第一個字的「ㄘ」「ㄘㄜ」「測」都不出預測列；第二個字的第一個注音鍵起照常顯示。
   - 只有殼不顯示，核心照舊計算預測列。所以 Tab 照 s3a 第 8a 條進入預測列；進入後 `candidate_selected ≥ 0`，照常顯示。↓、空白照第 13、15 條開候選窗，照常顯示。
   - 「只有一個音節」照組字區顯示的字串判斷：連續的注音符號（ㄅ–ㄩ U+3105–U+3129，聲調 ˊˇˋ˙）合算一個；其他每個字元各算一個；總數 ≤ 1 就算。
     - 例：「ㄘㄜ」是 1，「測」是 1，「測ㄕ」是 2，「1」是 1。
     - 顯示規則 D 把未完成音節顯示成英文字母時，例如「abc」，照字元數算，不在此限。
     - 「可省略韻母」開著時這條規則不生效。那時「ㄋㄔ」是兩個未完成單位，只能靠預測列解決（s3a 第 9a 條）。App 目前隱藏這個開關（`showsAbbreviation` 是 false）。
2. **瀏覽器清單**：放在 `Shell.browserApps`，和 `chatApps` 並排，用 bundle ID 比對。
   - 名單：`com.google.Chrome`（含 `.beta`、`.dev`、`.canary`）、`org.chromium.Chromium`、`com.microsoft.edgemac`（含 `.Beta`、`.Dev`、`.Canary`）、`com.brave.Browser`（含 `.beta`、`.nightly`）、`company.thebrowser.Browser`（Arc）、`com.vivaldi.Vivaldi`、`com.operasoftware.Opera`、`com.operasoftware.OperaGX`。
   - Crest 的 bundle ID 查不到，先不列，等回報者提供。
   - Electron App 不列：這次沒有量到它們有問題。
3. **開關**：設定視窗「選字」區、「即時預測」下面加一個開關「在瀏覽器裡，第一個字不顯示預測」，下面一行說明。
   - 預設開。
   - 「即時預測」關掉時這個開關反灰，設定保留。
   - 存在 `PredictionStore` 的 `browserFirstSyllable`（App 端 UserDefaults 鍵 `browserFirstSyllable`），沒存過算開。
   - 選單不加。
4. **規則放在 `Session.apply(_:)`**。所以每一種輸出都照同一條判斷：按鍵、點選、重排工作（`applyRerankJob`）、各個設定的快照、activate。
   - 條件：這個輸出是未進入的預測列（`candidate_selected == -1`、有候選、沒有英文段提示）、開關開著、「可省略韻母」關著、只有一個音節、bundle 在清單。
   - 成立時 `hideCandidates()`，不顯示。
   - bundle 不另外讀。Session 記住最近一次既有讀取的值：`send` 每鍵讀的那一次，以及 `applyProfile(bundle:)` 收到的那一次（activate、按鍵時的 claim、`toggleAppChat`）。不新增任何 `bundleIdentifier` 讀取。

同一句寫在別處的一起改：
- `docs/contracts/v3-engine.md` 第 125 行「面板位置照現有的 `lineRect` 規則」後面加一句，指到這份契約。
- README 的即時預測那一行加「瀏覽器裡第一個字不顯示（設定可關）」。
- `docs/contracts/settings-window.md` §2.2 的「選字」列：新開關放在「即時預測」之後；即時預測關閉時反灰；鍵 `browserFirstSyllable`。
- `docs/contracts/settings-sidebar.md` 第 28 行的「選字」項目清單。

## 怎麼看到它有效（誰看）

- main 在虛擬機的 Google 文件用新版打「ㄘㄜˋ」並停，截圖給維護者看：第一個字的期間畫面上沒有預測列。
- 維護者在自己的 Mac 上確認。
- 發版後請 Discord 回報者確認。

## 驗收

1. **`make test-swift` 全過**。新的 ShanjieKit 測試用假的 client 與面板，涵蓋：
   - a. bundle 在清單、開關開、打第一個音節的每一鍵：面板不顯示；打第二個音節的第一鍵：顯示預測列。
   - b. 同樣的鍵，bundle 不在清單：每一鍵都和現在相同。
   - c. 開關關：和 b 相同。
   - d. 在清單裡、第一個音節時按 Tab：顯示已進入的預測列。
   - e. 在清單裡、第一個音節時按 ↓：顯示候選窗。
   - f. 單音節判斷的純函式：「ㄘ」「ㄘㄜ」「測」「1」是 true，「測ㄕ」「測試」是 false，空字串是 true。
   - g. 每一鍵 `bundleReads` 的增量都是 1，清單內外都一樣（既有的 `testTheBundleIDIsReadOncePerKey` 照舊過）。
   - h. 開關的存取：沒存過是開；關掉後 store 是 false；重建 Shell 仍是關。
   - i. 非按鍵的輸出：在清單裡、第一個音節時，`applyPrediction` 的設定快照也不顯示預測列。
   - j. 「可省略韻母」開：在清單裡打 ㄋ、ㄔ（兩個未完成單位）之後，預測列有顯示。
   - 突變：
     - 拿掉 bundle 判斷，b 失敗；
     - 拿掉音節判斷，a 的第二個音節失敗；
     - 拿掉 `selected == -1`，d 失敗；
     - 判斷只放在 `send` 的路徑，i 失敗；
     - 拿掉可省略韻母的條件，j 失敗。
2. **虛擬機 Google 文件**：
   - 新版打「ㄘ」→「ㄘㄜ」→「ㄘㄜˋ」並停，每 0.9 秒一張，每一張都沒有預測列。
   - 接著打「ㄕˋ」：記錄這時預測列出現在哪裡，照實寫進研究紀錄。這一片不處理它的位置。
3. **TextEdit**：虛擬機打同樣的鍵，第一個音節期間照舊顯示預測列（開關只對清單裡的 App 生效）。

## 停止條件

- 驗收 2 有任何一張在第一個字的期間出現預測列：停下來查。
- 驗收 3 的預測列不見了：停下來查。

## 範圍外

- 第一個音節時按 Tab 進入的預測列、游標移到組字區開頭（←）之後的預測列：殼問的仍是起點 0 的範圍，應該一樣跑到左上角〔推論〕，寫成已知限制。
- 第一個音節時的候選窗（↓、空白）在 Docs 的位置：問的是同一個起點 0 的範圍，應該一樣錯〔推論〕，寫成已知限制。第二個音節起殼問到的是組字範圍內的字，量到位置是對的（研究紀錄 2026-10-30）。
- 向 Chromium 回報：另外問維護者。
- Crest 與其他沒列的 Chromium 瀏覽器、Electron App。
