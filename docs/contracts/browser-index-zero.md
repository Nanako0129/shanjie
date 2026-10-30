# 瀏覽器裡不用組字區前面那一格的方框

狀態：照第 0 步停下（2026-10-30）。plan-verifier 兩次 REVISE 之後，main 把範圍縮成先量第 0 步，沒有再送收尾審查。量到的是判讀表第二種結果，維護者選「停在這裡、寫紀錄」，第 1 步以後不做。

## 起因

`browser-first-syllable.md`（PR #334）只把第一個音節的未進入預測列藏起來。審查指出，同一個錯的查詢還會在下面這些情況用到：
- 第一個音節時按 Tab 進入預測列；
- 第一個音節時按 ↓ 或空白開候選窗；
- 游標移到組字區開頭。

這些情況下，Google 文件裡的面板應該一樣跑到視窗左上角〔推論，沒量〕。維護者 2026-10-30 選「開一片」。

## 量到的（研究紀錄 2026-10-30，Chrome 156、tart 虛擬機）

- Google 文件的組字範圍，殼讀到的是 `client.markedRange()` = `{1,n}`。
- 索引 0：Chrome 收到 `{0,1}`。這一格在組字範圍前面，Chrome 回過時的 `first_selection_rect`，也就是視窗左上角。
- 第一個音節時（「ㄘ」「ㄘㄜ」）：索引 1 以上都沒有轉給 Chrome，殼拿到 (0, 0)。
- 「冊ㄕ」：索引 1 轉給了 Chrome，收到 `{1,2}`，Chrome 回組字方框，位置對。
- 所以第一個音節時，殼問不到任何對的方框。
- IMK 什麼時候轉、怎麼把索引換成範圍，只量到上面這幾個點〔一般規則沒量〕。
- Docs 以外的 Chrome 欄位裡，游標前面有字時索引 0 會回什麼，寫這份契約時沒有量過（PLAN 2026-10-12 那次 textarea 的量測只看了位置）。〔2026-10-30 第 0 步量到：textarea 回組字的第一個字，位置對，見研究紀錄。〕

## 第 0 步：先量（實作之前）

用 main 4193ff62，在虛擬機的 Chrome `<textarea>` 量一次，再決定第 1 步的判斷式。
- 欄位裡先放兩行字，游標在第二行最後。
- 打「ㄘㄜˋ」按 ↓。
- 錄 trace，記下兩件事：
  - 索引 0 送到 Chrome 的範圍，以及 Chrome 走哪條路回答；
  - 候選窗落在第一行還是游標那一行。

事先定好的判讀：

| 量到的 | 第 1 步的判斷式 |
|---|---|
| 索引 0 回到第一行或視窗左上角（不是游標那一行） | 照下面寫的：`browser && markedLocation > 0`。這時 textarea 也有同一個問題，這一片一起修好 |
| 索引 0 回到游標那一行（位置對） | 停下來，把 trace 給維護者看，出選擇題。`markedLocation > 0` 會讓這類欄位變差，要另找能分辨 Docs 的訊號 |

量到的結果寫進研究紀錄。

## 要做到什麼（第 0 步是第一種結果時）

〔不做：第 0 步是第二種結果。以下到「範圍外」之前的各節只留作紀錄。〕

1. **ShanjieKit 加一個純函式** `LineQuery.lowestIndex(browser: Bool, markedLocation: Int) -> Int`：
   - `browser` 為真、而且 `markedLocation > 0` 時回 1；
   - 其他情況回 0，和現在相同。
2. **`TextClient.lineRect(cursor:browser:)`**：
   - Session 傳 `browser` = 這個 client 的 bundle 在 `Shell.browserApps`。bundle 用 #334 記在 Session 上的那個值，不另外讀。
   - App 端（`InputController` 的 `ClientAdapter.lineRect`）照舊從第 cursor−1 個往前找，但只找到 `LineQuery.lowestIndex(browser:markedLocation: marked.location)` 為止。
   - 找不到就回 `nil`。面板照現有規則用上一個好位置（`lastLine`）；連上一個都沒有時，照 `PanelPlacement.topLeft` 現有的退路，放在主螢幕可見範圍的左下角。
3. 第一個音節的未進入預測列照 #334 照舊不顯示，這一片不改。
4. 同一個規則寫在別處的一起改：
   - `docs/contracts/s3b2-glass-panel.md` §2.2「從游標前一個字開始…往前找」那一句；
   - `InputController.lineRect` 的文件註解；
   - `browser-first-syllable.md` 的「範圍外」改成指到這份契約。

## 怎麼看到它有效（誰看）

main 在虛擬機用前後兩版對照，截圖給維護者看，量法見驗收 2。

## 驗收

1. **`make test-swift` 全過**，新測試涵蓋：
   - a. `lowestIndex` 的四種組合：(true, 0)→0、(true, 1)→1、(false, 1)→0、(false, 0)→0。
   - b. Session 在清單裡的 bundle 傳 `browser: true`，TextEdit 傳 false。FakeClient 記下收到的值。
   - c. 每鍵 `bundleReads` 的增量仍是 1。
   - 突變：
     - `lowestIndex` 一律回 0：a 失敗；
     - Session 一律傳 false：b 失敗。
2. **虛擬機前後對照**（修正前 main 4193ff62、修正後這一片；Chrome 156；每格停約 3.6 秒，每約 0.9 秒一張）：
   - a. Google 文件：先打「ㄘㄜˋㄕˋ」Enter（讓第二個音節留下一個好位置），再打「ㄘㄜˋ」按 Tab。修正後候選列不在視窗左上角；修正前記錄實際位置。
   - b. Google 文件：同 a，但最後一步改按 ↓。
   - c. Google 文件：新分頁，第一次組字就打「ㄘㄜˋ」按 ↓，沒有上一個好位置。照實記錄位置，預期是主螢幕左下角。
   - d. Chrome `<textarea>`，在開頭打「ㄘㄜˋ」按 ↓：修正前後位置逐張相同。
   - e. Chrome `<textarea>`，在已有兩行文字的第二行最後打「ㄘㄜˋ」按 ↓。開始前先在 TextEdit 打字、叫出候選窗，把 TextEdit 視窗擺在離 Chrome 視窗很遠的地方，讓 `lastLine` 落在 TextEdit 那邊。照實記錄前後兩版的位置，量的是和游標的距離。
   - f. TextEdit 打「ㄘㄜˋ」按 ↓：修正前後位置逐張相同。
3. 研究紀錄寫下 a–f 每一格的位置，並寫明哪些是量到的。

## 停止條件

- 2a 或 2b 修正後還在視窗左上角：停下來錄 trace。
- 2d 或 2f 修正前後位置不同：停下來截圖給維護者。
- 2e 修正後比修正前離游標遠，或落在 TextEdit 那邊、或在螢幕左下角：停下來截圖給維護者，出選擇題。

## 範圍外

- 不在清單的 Chromium 瀏覽器與 Electron App。
- 判斷 IMK 索引的對應方式在 Docs 以外的網頁是否相同：只量第 0 步與 2d、2e。contenteditable（例如 Gmail、Discord 網頁版）不另外量，因為它和 textarea 一樣走 Chrome 的一般文字輸入路徑〔推論〕。2e 若停下，再補量它們。
