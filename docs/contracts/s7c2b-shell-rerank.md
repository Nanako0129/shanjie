# S7c-2b：語意校正接進輸入法的殼、設定開關與打包

接續 S7c-2a（`docs/contracts/s7c2a-core-rerank.md`，合併 fd990b4：核心的兩段式重排 API 與 FFI）。**使用者 2026-10-11 的決定**：照 PLAN A3／A7 非同步；預設開〔修訂 2026-10-12：預設關，見〈修訂〉；再修訂 2026-10-13：S7h 起又是預設開〕，設定視窗與選單都有開關，名稱「語意校正」；模型在主程序裡（PLAN 的例外，條件是隨 App 打包、路徑寫死、雜湊進版控並在建置時核對——這些是這一片要做的出貨前提）；A4：「先閃回 n-gram 的字再換回」（2a 量到每 100 個重新解碼的鍵約 2 次）先照現況做，由實機打字的感受決定要不要加規則。

## 修訂

| 日期 | 修訂 | 原因 |
|---|---|---|
| 2026-10-12 | **預設關**：`RerankStore` 的 `nil`（沒設過）＝關，`Shell.rerankOn` 起始為 false，設定視窗與選單沒設過時顯示關；存著的 true 照樣開，每次建引擎仍照目前的值呼叫 `setRerank`。**改用 `tinylm-v2`**：`data/tinylm.sjtl.sha256` 記 `710249c2…`（SJTL0002，14,638,224 bytes），CI（兩個 job）與 `release.yml` 下載 `tinylm-v2`，`docs/data-files.md`、`LICENSES/data.md`、CONTRIBUTING 同步。**selftest 與殼的測試改用 dev302 第 65 列**（我等一下在過去 → 再過去）：S7e 的嚴格聊天值下第 25 列不再被重排改掉（量過：`--profile chat --context --tinylm`，第 25 列留在 城市），第 65 列是 v2 聊天設定改對的列之一（第 25、27、46、49、65 列這 5 列的 top1 0 → 4，沒變的那一列就是第 25 列）。selftest 與測試都明確開啟重排，沒有靠預設。測試：沒設過 → 關、殼每次建引擎都傳 `setRerank(false)`（`Shell.rerankSent` 記下每次傳的值，測試逐次核對，初次建置與兩次重建）、沒有工作被取；存 true → 傳 true、照舊產生工作。 |
| 2026-10-12（/code-review 一輪） | 修正 12 項、延後 2 項、1 項不改：授權表與 README 的寫法、`build()` 的註解、`Shell.rerankSent` 讓測試分得出「傳了 false」與「什麼都沒傳」、`docs/data-files.md` 的換檔清單加 selftest 句子、`s3b.md` §13.2 選單順序補「語意校正」、PLAN 兩處、`SelftestTests` 與 `Selftest.swift` 的註解（量測條件改寫成 selftest／XCTest 自己的條件，CLI 數字標明是另一種條件）、兩邊共用的鍵序列對應（`keyEvents`）、本檔三處〔修訂〕標記、新增一個測試：先讓 runner 留住最新的工作（對照組：不切換，它會套用），再切聊天／書面（選單與全域模式各一次），那個舊結果不套用；拿掉兩個切換時測試以斷言失敗（在暫存複本上量）。**延後**：`docs/releases/tinylm-v1.md`（已發佈 Release 的文字，改它要連 Release 一起改，等維護者同意）；`Shell(...)` 測試建構子的重構（約 25 處，之後的 PR）。**不改**：`s7f-colloquial.md` 第 32 行（當時為真的歷史敘述）。 | 維護者 2026-10-12 的決定：重排預設關閉出貨、同意 `tinylm-v2`（S7e、`docs/contracts/s7c2c-delta.md` §4 第一項）。 |
| 2026-10-13（S7h，`docs/contracts/s7h-tinylm-v3.md` §2.3） | **預設改開**：`RerankStore` 的 `nil`（沒設過）＝開，`Shell.rerankOn` 起始為 true，`SettingsModel.rerank` 起始為 true；存著的 false 照樣關，每次建引擎仍照目前的值呼叫 `setRerank`（核心預設仍關）。**改用 `tinylm-v3`**（`7e73ef67…`，聊天 α 0.3、τ 2、δ 0.25，書面不變）。selftest 的第 65 列在 v3 下仍會被改（`RerankTests`）；第 25 列在 v3 下又會被改，是 S7h 的實機句（`DeviceRow`）。 | 維護者 2026-10-12 的決定（1:1 計分、預設開、改壞率 ≤ 0.5%），E2 與 S7h 的量測。 |

## 1. 要做到什麼、怎麼看到

- 裝好的輸入法打字時，組字區先顯示 n-gram 的結果，小模型在背景算完後（2a 量到一次 p95 約 8 ms）若選了別的候選就換字；按鍵本身不等它。
- 「語意校正」在設定視窗與選單都能開關，預設開〔修訂：預設關；再修訂 2026-10-13：S7h 起預設開〕；關掉時立刻回到 n-gram 的結果。
- **怎麼看到**：使用者實機（§3 第 5 項）；殼的測試、selftest、CI。

## 2. 做法

### 2.1 殼的佇列（`macos/Sources/ShanjieKit/Shell.swift`）

- `RerankRunner` 是 `Shell` 的**必填**建構參數（和各個 store 一樣沒有預設值）：App 傳一個用 `DispatchQueue(qos: .utility)` 跑 `shanjie_rerank_run`、跑完回主執行緒的 runner；所有測試注入測試用的 runner（現有的測試注入一個不會自己完成的，結果才不會在不確定的時間換字）。
- 每次把引擎的輸出套到組字區之後（按鍵、候選窗選字、各種設定的快照），呼叫 `take` 取工作；**一次只跑一個**：已經有在跑的，新工作先留著（只留最新的一個，被取代的立刻 free），跑完再接著跑。
- 完成時在主執行緒：確認還是同一個 session、同一個引擎物件（引擎可能因為換排列、開關詞包而重建）；呼叫 `apply`：0 → 照現有的方式套用輸出（`setMarkedText` 與候選列）；5 → 不做事；4 → 照現有處理碼 4 的方式（組字已被引擎清空）。之後若有留著的工作就接著跑。
- 工作用一個 Swift 包裝類別持有，`deinit` 呼叫 `shanjie_rerank_free`（經過一個可注入的 free 函式，測試在那裡計數），所以每條路徑都恰好 free 一次：被新工作取代、引擎重建、開關關掉、結果過期、apply 回 4、正常套用。run 在背景執行時，包裝物件由那個 closure 持有，run 回傳之前不會被 free。
- **包裝類別的規則**（安全審查 F1）：原始指標是 `private`，包裝類別只提供 `run()`（nonisolated）與 `apply(on:)`（@MainActor），沒有任何明確的 free 方法，free 只在 `deinit`；它是 nonisolated 的 `final class`，標 `@unchecked Sendable`，註解寫明三個不變條件（一次只跑一個；apply 只在 run 回傳之後、經 `DispatchQueue.main.async` 排序；free 只在 deinit）；runner 的 closure 抓的是包裝物件，不是指標；可注入的 free 函式型別是 `@Sendable (OpaquePointer) -> Void`（最後一個參照可能在背景執行緒放掉，標頭允許 run 回傳後在任何執行緒 free）。不實作 `CustomStringConvertible`。
- **runner 的位置**（F2）：真的佇列 runner 寫在 ShanjieKit（Swift 6 嚴格並行），App 只建立實例；佇列的 label 是固定字串，不加 signpost；**主執行緒從不等背景**（沒有 `queue.sync`、沒有 semaphore）。
- **完成時的比對**（F3）：用 weak 參照比對 session 與引擎（`===`），不用 `ObjectIdentifier`（新引擎可能配到舊引擎的位址），也不強參照舊引擎（否則兩個約 240 MB 的引擎同時存在）。核心的世代號是第二道防線。
- **不會卡住**（F4）：完成處理一進來就用 `defer` 清掉「執行中」並接著跑留著的工作，不論是過期、session 換人、引擎重建、開關已關或碼 5。
- **`take` 回非 0**（F5）：和 apply 回 4 走同一條處理（清組字區與候選窗，只記靜態字串加回傳碼）。
- 不寫任何紀錄（工作含候選文字與左文，PLAN R2）；run 回非 0 時只記碼。
- **核心的 `set_learning` 不讓結果作廢**（main 決定 2026-10-11，來自實作時的發現；同日 /code-review 後更正）：殼照 S4 每個按鍵前都送一次 `set_learning`（`s4-learning.md` §3），核心原本每次都換世代號，所以任何按鍵（含 ←）都讓進行中的工作過期，上面「不重新解碼的鍵之後照樣套用」做不到。第一版改成「同值不作廢、值改變才作廢」；/code-review 後更正為**不論值都不作廢**：這個開關只決定選字要不要記下來（`pre`），不改變解碼（解碼只要學習資料不是空的就會讀它，`set_learning(0)` 也不停止查詢）。開檔、清除會改學習資料，照樣作廢。`shanjie.h`、`s3a.md` §6、`s7c2a-core-rerank.md` 的過期清單跟著更正；核心測試 `set_learning_keeps_the_job_with_either_value`。

### 2.2 開關「語意校正」

- 照「即時預測」的寫法：`RerankStore` 協定與 `MemoryRerankStore`、App 用 `UserDefaults` 的 `DefaultsRerankStore`（`macos/Sources/Shanjie/main.swift`），沒設過＝開〔修訂：沒設過＝關〕。
- `Shell` 每次建引擎後照設定呼叫 `set_rerank`（`load_tinylm` 不改開關，所以重建引擎不會把關掉的打開）；切換時對目前的 session 呼叫 `set_rerank` 並套用回傳的快照（關掉時立刻回到 n-gram 的結果）。
- 選單在「即時預測」之後加「語意校正」（打勾表示開）；設定視窗加同名的開關（`SettingsModel`、`SettingsWindow`），兩邊同步。

### 2.3 載入模型

- 建引擎時，在 `load_lm` 之後載入 `Resources/tinylm.sjtl`：檔名是一個具名常數，`build()` 與 selftest 都用 `resources` 接上它；不新增任何 init 參數、defaults 鍵或環境變數，也不下載（安全審查 F10）。檔案不在或載入失敗：照常使用 n-gram（不重排），不中斷建引擎；記一行只有靜態字串加回傳碼的訊息（不含路徑）。
- 「避免把敏感字詞排在前面」（降權表）關掉時核心不產生工作，語意校正等於沒作用：設定視窗的「語意校正」照「可省略韻母」的寫法反灰（放在降權開關正下方）、選單項目同樣停用（F11）。小模型沒載入（檔案不在、載入失敗、或 `load_lm` 失敗而沒有載入）時同樣反灰（/code-review 後補，2026-10-11）：`Shell` 每次建引擎都記下這次有沒有載入；打勾仍顯示存著的選擇。

### 2.4 打包與發佈

- 模型檔〔修訂：`tinylm-v2`，SHA-256 `710249c2…`，SJTL0002，14,638,224 bytes，S7e 的嚴格值加 δ；下面的 `8725204c…`、14,638,208 bytes 與 `tinylm-v1` 是原契約的 v1〕：S7c-1 匯出的 M int8 `.sjtl`（SHA-256 `8725204c…`，綁 model-v5 的雜湊，聊天與書面 α 0.7、τ 2）。開發時放 `data/lm/tinylm.sjtl`（不進 git，和 `bigram.sjlm` 一樣）；雜湊記在進版控的 `data/tinylm.sjtl.sha256`。殼的測試從 `data/lm/tinylm.sjtl` 以 `tinylm.sjtl` 的名稱連進測試用的 Resources（缺檔時照現有的寫法失敗，不略過）；CI 的 `shell` job 在 `swift test` 之前下載並核對。
- **發佈時核對雜湊**（F9）：`docs/releases/README.md` 的資料類 Release 流程加兩步——上傳前 `shasum -a 256 -c data/tinylm.sjtl.sha256`，上傳後重新下載再核對一次；研究紀錄補上 `.sjtl` 完整的 64 碼 SHA-256 與大小（14,638,208 bytes），寫雜湊檔時兩者都要相符。
- **`release.yml`**（F7）：它的 build job 不跑 check-app 與 selftest（現有做法，靠同一個 commit 先過 CI，`build-app.sh` 自己也核對）；漏掉下載只會讓發版時建置失敗。合併後、下一個 `v*` tag 之前，在 main 用 `workflow_dispatch` 跑一次 dry run（對外動作，到時候先問使用者）；verifier 讀 `release.yml` 的 diff。
- **Release 的順序**（和 `docs/releases/README.md` 一致，避免「Release 等說明進 main、說明等 CI、CI 等 Release」）：(1) 一個只含 `docs/releases/tinylm-v1.md` 與 `data/tinylm.sjtl.sha256` 的小文件 PR 先合併進 main；(2) 問使用者；(3) 同意後從 main 照 README 的指令建 `tinylm-v1`（附件 `tinylm.sjtl`，`--latest=false`）；(4) 這一片的程式 PR 的 CI 才下載得到檔案，全綠後合併。
- `scripts/build-app.sh`：檔案存在、雜湊相符才打包，複製到 `Resources/`，隨附的授權說明加一段（訓練文字和 `bigram.sjlm` 同源：維基百科 CC BY-SA 4.0、Common Voice CC0、Tatoeba CC BY 2.0 FR）；`scripts/check-app.sh` 檢查 bundle 裡有這個檔（加進非空檔的清單與授權說明的檢查），並把 `Resources/tinylm.sjtl` 算出的雜湊和 `data/tinylm.sjtl.sha256` 裡**記的值**比對（不是和 `data/lm/` 的檔互比，那只證明複製正確；F6）；`.github/workflows/ci.yml` 與 `release.yml` 照 `model-v5` 的方式下載並核對；`docs/data-files.md` 加一列（寫明和 `bigram.sjlm` 同源、不含私人對話資料）與「版本怎麼往前走」（重訓或改 α、τ → `tinylm-v2`〔修訂 2026-10-12：v2 已用掉，下一個是 `tinylm-v3`，見 `docs/data-files.md`〕；換 n-gram 模型（model-v6）時 `.sjtl` 綁的雜湊會對不上，要一起發新的 `.sjtl`，否則語意校正會靜靜地沒載入——selftest 會抓到）；`LICENSES/data.md` 加一列。

### 2.5 Selftest（`Selftest.swift`）

- 〔修訂：句子改用 dev302 第 65 列 `ㄨㄛˇ ㄉㄥˇ ㄧ ㄒㄧㄚˋ ㄗㄞˋ ㄍㄨㄛˋ ㄑㄩˋ`，我等一下在過去 → 再過去；第 25 列在 tinylm-v2 的聊天設定下不被改；selftest 明確開啟重排，不靠預設。〕載入模型；**聊天設定**下用一句已知會被重排改掉的句子（從 2a 的 dev302 聊天重播挑一句，CLI 第一名和 n-gram 第一名不同的），同步地打字、每個鍵後「取 → 算 → 套用」，最後的組字等於重排後的句子、而且和 n-gram 的句子不同；關掉開關重打一次，等於 n-gram 的句子。模型沒載入時 selftest 失敗（不讓「語意校正靜靜地沒作用」的 bundle 出貨）。

### 2.6 文件

- PLAN A4 加註（使用者 2026-10-11）：重排造成的短暫閃回另外計，由實機感受決定是否加規則；S7 狀態行。A7 的「推論期間按鍵 p95 < 16 ms」在殼內沒有打點量測，證據是 2a 的引擎重播（按鍵 p95 約 2.6 ms、套用 p95 約 0.3 ms）與實機；寫明沒有殼內量測。
- 研究紀錄、本契約的結果一節、README 的 S7 列。

## 3. 驗收

1. **殼的測試**（`ShanjieKitTests`，用現有的 FakeClient，client 的 bundle 設成聊天 App，例如 `com.apple.MobileSMS`，句子和 §2.5 相同）：按鍵後有一個工作交給 runner；完成且有改變 → 多一次 `setMarkedText`，內容是重排後的字（和 n-gram 的字不同）；完成但沒改變（5）→ 沒有多的呼叫；取工作後又有一個會重新解碼的鍵，舊結果不套用；不重新解碼的鍵（例如 ←）之後完成，照樣套用；開關關掉 → 沒有工作交給 runner、組字回到 n-gram 的字；session 換人或引擎重建之後才完成 → 不套用；一次只跑一個（第二個工作等第一個跑完）。**free 的計數在可注入的 free 函式**：涵蓋被取代、引擎重建、開關關掉、過期、碼 4、正常套用，最後「取到的工作數 ＝ free 的次數」而且沒有任何一個 free 兩次；拿掉任何一條路徑的 free 或多 free 一次，測試要失敗。**開關**（比照「可省略韻母」的測試）：選單「語意校正」預設打勾〔修訂 2026-10-12：預設不打勾；打開之後才有工作、才重排，測試用存著的 true 打開〕、切換會寫進 store；關掉之後換排列、切動漫詞包（兩種都會重建引擎）仍沒有工作；用存了「關」的 store 建新的 `Shell` 也沒有工作；設定視窗切換後選單同步（比照 SettingsTests 現有的開關）。突變：拿掉 `build()` 裡的 `set_rerank`，測試要失敗。
2. **Selftest**：本機與 CI（`make selftest-bundled`）通過；拿掉模型檔時失敗。**真的佇列 runner 的測試**（F2）：用 ShanjieKit 裡 App 用的那個 runner，按鍵 → 背景 run → 主執行緒 apply，組字換成重排後的字，「取到的工作數 ＝ free 的次數」；main 試跑一次 `swift test --sanitize=thread`，沒跑成就在研究紀錄標明沒量過。殼的測試另外加：過期的完成之後，下一個工作照樣交給 runner（F4）；`take` 回非 0 的處理（F5）；降權表關掉時「語意校正」反灰（F11）。
3. **打包**：`build-app.sh` 在缺檔或雜湊不符時失敗；`check-app.sh` 檢查 bundle 裡的檔與雜湊；CI 的 `shell` job 全綠。
4. `make test`、CI 全綠；`/code-review` 沒有未處置的問題；fresh verifier 確認第 1–3 項，並讀 diff 確認：模型路徑只從 bundle 的 Resources 接具名常數（這是靠構造保證，原始碼掃描不算防護）、包裝類別的規則（§2.1）、`release.yml` 的下載與核對。
   - **S7c-2a 安全審查留下的三個出貨前提各由哪一項證明**：隨簽章的 App 打包 → 第 3 項（`check-app.sh` 對記的雜湊比對）加現有的 `codesign --verify --strict --deep`；路徑寫死 → 第 4 項 verifier 讀 diff；雜湊進版控並在建置時核對 → 第 3 項（`build-app.sh`）與 CI，`release.yml` 由 verifier 讀 diff 與發版前的 dry run。
5. **實機**（使用者，每次佔用鍵盤 ≤ 15 秒；main 用 `scripts/install-ime.sh` 安裝，裝完提醒 Caps Lock 切換可能失效與復原方法）：在**訊息 App**（`com.apple.MobileSMS`，聊天設定；只打字、不按 Enter 送出）打 §2.5 那一句，最後顯示重排後的字（和 n-gram 的字不同）；〔修訂：預設關，要先到設定視窗打開「語意校正」，打完再關掉重打一次，顯示 n-gram 的字，句子是第 65 列的〕到設定視窗關掉「語意校正」再打一次，顯示 n-gram 的字；請使用者說打字時的閃動能不能接受（A4 的決定）。

## 4. 範圍外、停止條件、預算、限制、回滾

- **範圍外**：「沿用重排前綴」之類消除閃動的規則（等實機感受）；model-v6 之後重選 α、τ；MLX；XPC。
- **停止條件**：selftest 在 CI 失敗；實機顯示錯字、卡頓或明顯延遲；使用者覺得閃動不能接受（停下來決定規則）；建 Release 沒有得到使用者同意（程式先合併不了：CI 下載會失敗）。
- **預算**：executor 實作 1 次＋修正 1 次；main 做打包核對、Release（經同意）、安裝與實機協調。
- **限制**：executor 不安裝、不啟動 App、不呼叫 TIS 或 lsregister、不在 .app 裡執行任何東西（selftest 只在 CI 與 main 跑）；不讀 `~/side-project/shanjie-private`、`~/.cache/shanjie/work`、學習檔、`~/Library`、鑰匙圈、`eval/holdout`；不 push、不開 PR、不建 Release、不碰其他 worktree。
- **回滾**：預設改成關是一行〔修訂 2026-10-12：已經是預設，不需要回滾這一步〕；整片 revert 時，bundle 裡留著的 `tinylm.sjtl` 沒有人載入，無害。

## 5. 審查紀錄

- 安全審查（`pilotfish:security-reviewer`，2026-10-11，唯讀）：沒有可遠端觸發的嚴重問題，沒有違反 R2 的設計；核心已擋住「套用到別的引擎」。處置：F1（指標不能帶出包裝物件）、F2（真的 runner 放 Swift 6 模組、加真佇列測試、主執行緒不等背景）、F3（weak 比對、不強參照舊引擎）、F4（`defer` 清旗標）、F5（`take` 回非 0）、F6（check-app 對記的雜湊比）、F10（具名常數、verifier 讀 diff）、F11（降權關掉時反灰）、F12（不實作描述、固定 label、只記碼、data-files 寫不含私人資料）**FIX**，見 §2.1、§2.3、§2.4、§3；F7（release.yml）**FIX**：verifier 讀 diff，發版前的 dry run 到時候問使用者；F9（上傳前後核對、完整雜湊進紀錄）**FIX**；GitHub 的 immutable releases 是 repo 設定，**留給使用者**之後決定；F8、F13（執行時完整性、沙盒）記錄，不需改動。三個出貨前提的對應寫在 §3 第 4 項。
- 第一次 plan-verifier（2026-10-11）：REVISE 6 點。1–5 **FIX**：實機改在訊息 App（聊天設定）、selftest 與殼的測試都在聊天設定下核對「重排後和 n-gram 不同」；Release 的順序改成先合併只含說明與雜湊的文件 PR、問使用者、從 main 建 Release、再跑程式 PR 的 CI；free 的計數改在可注入的 free 函式並涵蓋所有路徑；開關的保存、重建、選單與設定視窗同步都加測試；殼的測試用 `data/lm/tinylm.sjtl`、`RerankRunner` 是必填參數。6（缺安全審查）：跑了安全審查，處置見下一條。
- 第二次 plan-verifier（2026-10-11）：READY。
- 實作（security-executor，2026-10-11）：殼的 ← 測試在真的殼裡失敗（`set_learning` 每鍵讓工作過期，見 §2.1 最後一條），回報 main；main 選「核心只在值改變時作廢」（不改殼的 S4 呼叫方式），在這一片處理。
- /code-review（2026-10-11）後 main 處置：`set_learning` 改成不論值都不作廢（開關不改變解碼，見 §2.1 最後一條）；模型載入失敗時「語意校正」反灰；選單的切換改走 `shell.applyRerank`（交給擁有組字的 session）；apply 回 4 以外非 0 碼的測試；`rerankTake` 非 0 時不再處理指標（標頭保證非 0 時是 NULL）；設定視窗裡「語意校正」移到降權開關之後。

## 6. 結果

- 實作見研究紀錄 2026-10-11 S7c-2b 一節。TSan：RerankTests 在 `--sanitize=thread` 下 11／11、沒有報告（只跑這一組）。
- §2.4 的 `docs/data-files.md` 與 `LICENSES/data.md` 兩列改由文件 PR #117 帶進 main（它們描述的是那個 Release）；Release `tinylm-v1` 照 §2.4 的順序在使用者同意後建立，上傳前後的核對都 OK。
- selftest-bundled 與 check-app 在 CI 的 `shell` job 跑（main 與 agent 不在本機執行 .app 裡的程式）；實機（§3 第 5 項）由 main 安裝後交給使用者。
- 〔2026-10-12〕main 在本機跑了 `make bundle`、`scripts/check-app.sh`（檢查 2、3、6 都 ok）與 `make selftest-bundled`（rc 0），在 557d3df 與 c7e074f 各一次；和上一條「main 不在本機執行 .app 裡的程式」的做法不同，照實記。
- 實機（§3 第 5 項，使用者，2026-10-12）：main 用 `scripts/install-ime.sh` 安裝 c7e074f 的 build（f1c8354 只改測試與文件；系統已啟用，註冊跳過），刪掉 10-11 試用時留下的 `rerank` 設定值後重開輸入法程序：選單與設定視窗的「語意校正」沒有打勾；聊天 App 裡 ㄨㄛˇ ㄉㄥˇ ㄧ ㄒㄧㄚˋ ㄗㄞˋ ㄍㄨㄛˋ ㄑㄩˋ 打出「我等一下在過去」，打開後是「我等一下再過去」。使用者：「確認OK」。
