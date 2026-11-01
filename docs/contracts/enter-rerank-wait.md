# Enter 時等目前的語意校正（產品片 ENTER-WAIT）

狀態：草稿（修訂三：第一次 REVISE 三項、第二次三項、收尾審查五項都照最小修改處理；security-reviewer 的結果見 §9）。審查到上限，開始實作要維護者同意。

前提：PR #348（`exp/punct0`：PUNCT-0 的研究紀錄與重播支援標點）先合併進 main，這片從合併後的 main 開分支。

## 0. 起因

PUNCT-0（研究紀錄 2026-11-01「PUNCT-0」）在虛擬機量到：殼每處理一鍵就把整個組字區重新解碼，前一次套用的語意校正（會意重排）修正跟著消失，要等這一鍵的重排工作跑完才補回。Enter 在那之前到，送出的是整行的 n-gram 解碼，連畫面上已經改對的段也變回去（「縱得莞莞」那行：重排工作 33 ms 後才結束，Enter 先送出「關關類青」「意除卻」）。維護者 2026-11-01 選「Enter 時等目前的重排（有上限）」。

## 1. 要做到什麼

1. 按 Enter（`KeyMap` 對到 Enter 的鍵，含數字鍵盤的 Enter）、組字區有字、而且殼有這個 session 與目前引擎的重排工作在跑或在排隊（`rerankRunning` 或 `rerankPending`）時，殼先在主執行緒等那些工作完成並照現有規則套用（`rerankFinished` → `applyRerankJob`），再把 Enter 交給核心。等候有上限 `ENTER_RERANK_WAIT_MS = 100`（具名常數，註解寫出處：PUNCT-0 在虛擬機量到那一行最後一個重排工作 33～84 ms，上限取高於最大值；Mac 上 dev302 每列 p95 12.6 ms）。只有工作在跑時才會等，最多讓 Enter 晚 100 ms。到上限還沒完成就照現在的行為處理 Enter（送出當下的組字區），還在跑的工作完成後照現有規則被丟掉。
2. 等候期間主執行緒不處理別的事件，所以之後的按鍵順序不變（不另做緩衝）。等候是阻塞式的（每個工作的 `DispatchSemaphore` 初始值 0，或 `DispatchWorkItem.wait(timeout:)`）；禁用巢狀 run loop（`RunLoop.run(until:)` 之類，會在等候中處理後面的按鍵）與沒有上限的 `wait()`。
   - 上限是**整次 Enter 一個期限**：Enter 到時用單調時鐘（`DispatchTime.now()`）算期限，有一個在跑、一個在排隊時，每次等候只給剩下的時間；過了期限就停，排隊的工作照現有規則。
   - 等候放在 `handle()` 的 `claim()`、`applyProfile`、`syncAscii` 之後，緊接在 Enter 的 `send(key)` 之前（放在前面，`applyProfile` 會讓工作過期，等到的結果被 `session === owner` 丟掉）。
   - 候選窗開著、或預測列被選中時不等（核心一律不套用，`engine.rs` `apply_rerank`）。
   - 執行中工作的 session 與引擎存成 weak 參照、用 `===` 比對（沿用 F3），不用位址。
3. 只改 Enter；其他會送出組字區的情況（換 App、點滑鼠、`commitComposition`、自動送出）照舊。語意校正關、沒有工作在跑時，Enter 完全照舊、不等。
4. `RerankRunner` 加一個讓主執行緒有上限地等目前這個工作的方法；等候方法只在這個工作的 `run()` 回傳之後才回報完成，並把 run 的碼一起交回；App 的 `QueueRerankRunner` 每個工作用自己的等候物件（不共用一個信號量，逾時或沒人等的工作留下的訊號不會讓下一次提早返回），測試的 runner 照它們手動完成的方式實作。等到之後由殼直接呼叫完成處理，原本排進主佇列的那次回呼要變成不做事：不套用、不清 `rerankRunning`、不再啟動 `rerankPending`（同一個工作的完成處理只做一次）。
   - `run` 回傳每個工作各自的 handle（MainActor 上的類別實例），殼把它存成執行中的狀態；遲到的回呼只在 `handle === 殼目前的執行中 handle` 而且還沒完成時才走 `rerankFinished`。比對用強參照的 `===`，禁用 `ObjectIdentifier`、指標位址或已處理的 id 集合（新工作可能配到舊位址，`rerankRunning` 會永久卡住、語意校正靜靜失效）。
   - 同步路徑呼叫同一個 `rerankFinished`（保留 F4 的 `defer`），不另寫一份。
   - 碼在送訊號之前寫進盒子，盒子用 `Synchronization.Mutex`，不新增 `@unchecked Sendable`；佇列的 block 以強參照抓住等候物件，等候物件與盒子都不持有原始指標。
   - 已知限制：等候不提升 `.utility` 佇列的優先權（PLAN A7 要求推論用低 QoS），負載高時可能逾時、退回舊行為。
   - 同步套用回 4（引擎重設）時照舊由 `fail()` 處理，之後的 Enter 照現有規則。

## 2. 怎麼看到它有效

- 單元測試（`macos/Tests/ShanjieKitTests/RerankTests.swift`，用手動完成的 runner）：
  - 工作在上限內完成：Enter 送出的是套用重排後的文字；回呼只處理一次。
  - 工作到上限還沒完成：Enter 送出當下的組字區（n-gram 的字），之後工作完成不改任何東西。
  - 一個在跑、一個排隊：兩個都在上限內完成時，送出的是排隊那個（最新狀態）的結果。
  - 沒有工作、語意校正關、組字區空白：Enter 不等（runner 的等候方法沒被呼叫）。
  - 逾時之後工作才完成：下一個鍵的工作有交給 runner（`runner.received` 增加），`rerankRunning` 沒有卡住。
  - 一個在跑、一個在排隊、整次期限到了：總等候不超過期限（注入的時鐘或上限）。
  - 遲到的回呼：手動 runner 在等候成功後保留原本的 `done`，之後再呼叫它（模擬 `QueueRerankRunner` 排進主佇列的那一次）：client 沒有多出任何呼叫，下一個工作只啟動一次（`runner.received`、`held.count`）。
- 殼加可注入的等候上限（測試掛勾，和 `rerankFree`、`rerankApply` 同一種寫法）：真實佇列測試給 10 s，另外斷言出貨常數是 100 ms。
- 真實佇列的測試（`QueueRerankRunner`，照 `testQueueRunnerAppliesOnTheMainThread` 的寫法）：先打一個組字區，讓它的工作走一般的 main.async 回呼完成、沒有人等（`spin { marked == RerankRow.reranked }`）；接著打 `RerankRow`，在同一個主執行緒回合按 Enter（工作還沒完成），再轉 run loop：送出的是 `RerankRow.reranked`（只有等候真的等到這個工作的 run 回傳才會出現），送出後 client 沒有其他呼叫，`frees.freed == shell.rerankTaken`、`doubleFrees == 0`。突變（各在暫存複本上，各自要讓某個測試失敗，結果記進研究紀錄，時序相關的照實寫）：所有工作共用一個信號量；遲到的回呼不檢查 handle；同步路徑不經過 `rerankFinished`；每個工作各給一次完整上限。
- `swift test --sanitize=thread`（至少 `RerankTests`，含新的真實佇列測試）全過、沒有報告。
- 虛擬機（main 執行，不佔維護者的鍵盤）：用 PUNCT-0 那台 VM 與同樣的 w1 錄影設定（每鍵 25 ms、步間 300 ms、錄影開）打 3 次（最多 6 次），第 4 行每次都送出「綜得關關，關關累清，站牌苦思，亦除卻巫山飛雲也」（main 上 `shanjie-replay --rows experiments/punct0/W.rows --profile chat --learn off --rerank each --firsts F` 的第 4 行，PR #348 驗過）。

## 3. 驗收

- 第 2 節的單元測試、真實佇列測試與 `make test-swift` 通過；TSan 下 `RerankTests` 全過、沒有報告（合併的必要條件，結果記進研究紀錄）。
- 虛擬機：每次 Enter 有沒有等、等了幾 ms 從 log 讀（只在這次的診斷版加 log，不 commit；log 只記有沒有等、等了幾 ms，不記候選或組字文字，格式寫進研究紀錄），每次都寫進研究紀錄。通過條件：**每一次**第 4 行都和重播相同，而且至少一次是「Enter 到時這個 session 的工作在跑或在排隊，並在上限內等到」（等了 > 0 ms、< 100 ms）。「最多 6 次」只用來湊出那一次等候；打到 6 次都沒碰到等候，就照實報「等候路徑沒在虛擬機觀察到」並停下，不算通過。
- 規則文字改成同一個說法（Enter 有上限的等候是唯一的例外，附出處本契約）：`docs/PLAN.md` 的 A3（非同步、不阻塞按鍵）與 A7（推論期間按鍵到顯示 p95 < 16 ms）、`docs/contracts/s7c2b-shell-rerank.md` §1「按鍵本身不等它」、§2.1 的 F1 不變條件與 F2「主執行緒不等佇列」、`Shell.swift` 的 `QueueRerankRunner` 註解（"The main thread never waits for the queue"），以及 `macos/Sources/ShanjieKit/Core.swift` 的 `RerankJob` 文件註解（`@unchecked Sendable` 依賴的不變條件 2），改成兩條排序都成立：main.async 回呼，或背景 `run()` 回傳之後才發訊號的有上限等候；兩條都保證 apply 只在 run 回傳之後，遲到的回呼不再套用。同一個 PR；`grep -rn "never waits\|不等它\|不阻塞\|主執行緒不等" docs macos/Sources README.md site` 找不到和新行為矛盾的句子。

## 4. 範圍外

- 核心（解碼與重排的規則、每鍵整行重新解碼的方式）不改。「保留沒變的段的重排選擇」是另一種修法，這片不做。
- 其他送出組字區的路徑（第 1 節第 3 項）。
- 重播加模擬 Enter 搶先的模式。

## 5. 停止條件

- 主執行緒的等候會讓 IMK 回報逾時或卡住輸入（虛擬機看到按鍵遺失或順序錯）：停下報告。
- 3 次裡有一次第 4 行仍送出 n-gram 的字、而且 log 顯示等到了上限：停下報告上限是否要改（由維護者決定數值）。
- 任何一次虛擬機的第 4 行和重播不同：停下報告，不論 log 有沒有等到上限，不用後面的次數補過。
- TSan 報出新的資料競爭：停下修，修不了就報告。TSan 跑不成：停下，用選擇題問維護者。

## 6. 檔案

- `macos/Sources/ShanjieKit/Shell.swift`（`RerankRunner`、`QueueRerankRunner`、Enter 的處理）、`macos/Sources/ShanjieKit/Core.swift`（只改 `RerankJob` 的文件註解）、`macos/Tests/ShanjieKitTests/RerankTests.swift`（與 `Support.swift` 的測試 runner，若在那裡）。
- `docs/PLAN.md`（A3、A7）、`docs/contracts/s7c2b-shell-rerank.md`、`docs/contracts/enter-rerank-wait.md`、`docs/research-log.md`。

## 7. 預算

executor 實作 1 次、修正 1 次；虛擬機最多 6 次。超過就停下問維護者。

## 8. 回滾

整個 PR revert，回到只有 main.async 的路徑；沒有資料或設定要遷移。

## 9. 審查紀錄

- plan-verifier：第一次 REVISE 3 項、第二次 3 項、收尾審查 5 項，全部照改（修訂一到三）。
- security-reviewer（2026-11-01，唯讀）：沒有 P0／P1。全部 FIX：P2-A 遲到回呼綁 handle 的 `===`、同步路徑走同一個 `rerankFinished`、補「逾時後不卡住」測試（§1 第 4 項、§2）；P2-B 整次 Enter 一個期限、禁巢狀 run loop 與無上限 wait（§1 第 2 項、§2）；P2-C 信號量初始 0、`Synchronization.Mutex`、不新增 `@unchecked Sendable`（§1 第 4 項）；P3-D 優先權反轉寫成已知限制（§1 第 4 項）；P3-E 等候位置、候選窗與預測列不等、weak＋`===`（§1 第 2 項）；P3-F 可注入的等候上限（§2）；P3-G 規則文字逐處列出（§3）；P4-H 回 4 照舊（§1 第 4 項）；P4-I log 只記數字（§3）。另外 s7c2b §5 的審查紀錄加一條本次處置。
