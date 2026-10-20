# S7c-2a：重排接進引擎的核心部分（兩段式 API；殼與打包在 S7c-2b）

接續 S7c-1（`docs/contracts/s7c-core-tinylm.md`，合併 b7125a3：Rust 前向、`SJTL0001`、`TinyLm::rerank`，M int8 在 Rust 路徑通過 S7a §4，保留集聊天 180 → 200／227）。**使用者 2026-10-11**：照 PLAN A3／A7 非同步（按鍵路徑 p95 < 16 ms，重排離開按鍵路徑、低 QoS、新按鍵取代進行中的重排，組字區先顯示 n-gram 的結果、算完可能換一次字）；預設開，設定視窗給開關（〔更正，2026-10-12〕使用者決定預設關出貨，核心的預設改成關，見 `docs/contracts/s7c2c-delta.md` §2.3）。設計先請 Fable 5.1 唯讀分析（建議由核心提供同步的兩段式 API、殼負責執行緒），main 採用。**使用者 2026-10-11 另外決定**：模型在主程序裡載入與推論（in-process，不放 XPC helper；PLAN 架構原則裡 XPC 那一行、S5 的模型那一行與風險表 R8 那一列補上例外，條件見 §2.6）；A4 的衝突「先量再決定」（§1）。

S7c-2 分兩片：這一片（2a）只做核心與 FFI，用同步的測試驅動；殼的佇列、開關、模型檔打包、實機檢查是 2b。

## 1. 要做到什麼、怎麼看到

- 引擎在每次改變組字之後，產生一個「重排工作」（只有資料，不跑模型）；呼叫端在別的執行緒算完，再交回引擎套用；套用前若引擎已經有新的輸入，結果直接丟掉。
- **怎麼看到**：核心測試同步驅動「取工作 → 算 → 套用」；dev302 的重播逐鍵套用後，送出的句子和 CLI `--tinylm` 的第一名完全相同；按鍵路徑的 p95 在載入模型後仍 < 16 ms；記錄重排一次的時間與「每個鍵看到字變回 n-gram 再換回」的比例。
- **和 PLAN A4 的衝突（寫明）**：每個鍵都從 n-gram 重新解碼、再重排，所以已經顯示的重排結果，可能在下一個鍵先變回 n-gram 的字、算完再換回來；A4 要求「已鎖定的字後續按鍵 0 次被改寫」，這個設計照現在的樣子**不滿足 A4**。驗收第 5 項的「變回再換回」比例就是 A4 的量測。使用者 2026-10-11 決定先量再決定：這一片只量、不加規則；2b 出貨前帶數字請使用者決定（加「黏住已重排的前綴」的規則，或修改 A4）。

## 2. 做法

### 2.1 引擎的狀態

`tinylm: Option<Arc<TinyLm>>`、`rerank_on: bool`（預設 true；〔更正，2026-10-12〕S7c-2c 起預設 false，要 `set_rerank(true)` 才產生工作，`docs/contracts/s7c2c-delta.md` §2.3）、`generation: u64`（從整個程序共用的 `AtomicU64` 取號，重建的引擎不會重用號碼）、`pending: Option<RerankJob>`。沒有呼叫 `load_tinylm` 時，引擎的一切行為與輸出逐位元組不變。

### 2.2 產生工作（`refresh_lm` 結尾，模型已載入且 `rerank_on`）

- 每一段自由段（兩個固定詞之間，`refresh_lm` 的 gap 迴圈）若 n-best 有 ≥ 2 條：取前 `TOPK` 條路徑（保留 `(詞, lp, delta)`，套用時要重建 `path`）、各條的字串（`cands`）、分數（`ng`）、前文鍵 `context_key(before)`（`before` 就是那一段左邊已顯示的文字加左文；哨兵 → 空的 k 由 `TinyLm::rerank` 處理）、設定、這一段在 `path` 裡的範圍。
- **選字記憶的保護**：某段 n-gram 第一條路徑裡有任何一個詞被學習加分（`lp` 大於該詞在該讀音下不加分時的 `best_lp`），這一段不放進工作（使用者教過的選擇不被重排改掉）。〔2026-10-20 補：只給評測用的 `set_learn_guard(false)`（`shanjie-replay --learn-guard off`，`engine-replay.md` §2）可以關掉這條；App 不呼叫，預設照舊。〕
- 沒有任何一段可以重排時，不產生工作。
- **降權表關掉時不產生工作**（`set_demote(false)`）：α、τ 只對降權表開著的分數成立，和 CLI 拒絕 `--no-demote` 同理。

### 2.3 世代與丟棄

每個會改變狀態的公開方法（所有 `&mut self` 的公開方法，`take_rerank_job` 與 `apply_rerank` 除外）一進來就換新的世代號、清掉 `pending`；**`clear_all`（送出與清空組字都經過它）也換世代號、清掉 `pending`**，所以同一次呼叫裡「先重算、再自動送出」（40 音節自動送出、預測列選字觸發的送出，`insert_token`、`choose_pred` 都是先 `refresh()` 再 `take_commit()`）產生的工作會作廢。工作帶著產生時的世代號。
- **換 n-gram 模型時丟掉小模型**：`load_lm`、`set_lm` 成功後把 `tinylm` 設為 None（殼在載入 n-gram 之後才載入小模型；`.sjtl` 綁的是 n-gram 的雜湊）。

**修正一（2026-10-11，PR #116 /code-review）取代上面「每個公開方法一進來就換號」的規則**：只有會改變解碼結果的地方換號並作廢工作——每次 `refresh()`（重新解碼時產生新工作、新號碼）、`clear_all`、`load_lm`／`set_lm`／`load_tinylm`、`set_rerank`、`set_profile`、`set_demote`、選字記憶的開關／開檔／清除（2026-10-20 加：只給評測用的 `set_learn_guard`）。不重新解碼的鍵（游標移動、還沒完成音節的注音符號、進入預測列、直通鍵）**不再**作廢進行中的工作，算完照樣套用；原規則會讓「打得快」時 Enter 送出 n-gram 的字。另外工作記下每一段在 `path` 裡原本的詞，套用時逐一核對，不同就整個不套用（不只靠號碼）。

**更正（2026-10-11，S7c-2b 的 /code-review）**：上面清單裡的「選字記憶的開關」拿掉。`set_learning` 不改變解碼（解碼只要學習資料不是空的就會讀它；開關只決定選字要不要記下來），所以不再換號；開檔、清除會改學習資料，照樣換號。

### 2.4 計算與套用

- `RerankJob::run(&self) -> RerankResult`：只用工作裡的 `Arc<TinyLm>` 與資料（不碰引擎），對每一段呼叫 `TinyLm::rerank`；`TooLong` 記為選 0。`RerankJob`、`RerankResult` 是 `Send`。
- **修正一**：`RerankJob::run` 依序處理各段，後一段的前文鍵用前面各段**選定後**的字（加上中間的固定詞與左文）重算 `context_key`，不用 n-gram 第一名的字；只有一段時和原本相同（CLI 的一致性不變）。套用時：**候選窗開著 → `None`**；**預測列只在套用當下非空時才重算**（V3 規則清掉的列不會因為套用又出現）。
- `Engine::apply_rerank(result) -> Result<Option<Output>, EngineError>`：世代號不同 → `None`；**預測列已進入（`pred_sel` 有值）→ `None`**（不把使用者在列裡的選取踢掉）；每一段都選 0 → `None`（不輸出，不閃）；工作記的範圍超出現在的 `path` → `None`（防呆，正常不會發生）；否則把選到的路徑換進 `path` 的那一段、重建 `display`、重算預測列，回傳新的輸出。不動 `syls`、游標、固定詞、候選窗。
- **送出**：Enter、40 個字元自動送出、`reset` 等，一律送出畫面上的內容（標點不是送出：它進組字區、重新解碼、產生新工作）；還沒套用的工作因為換了世代號而作廢（不等待）。

### 2.5 FFI 與標頭（`core/src/ffi.rs`、`core/include/shanjie.h`、`docs/contracts/s3a.md` §6）

- `shanjie_engine_load_tinylm(engine, path)`：讀檔失敗、格式錯、沒有載入 n-gram 模型、KN 側檔開著、`.sjtl` 記的 n-gram 雜湊和載入的模型不同 → 錯誤碼 3，引擎維持原狀（沒有小模型或原本那個），輸入法照常用 n-gram；照 `load_lm` 的寫法。**不改 `rerank_on`**（建立引擎時預設 true，〔更正，2026-10-12〕S7c-2c 起預設 false，`docs/contracts/s7c2c-delta.md` §2.3；殼每次建引擎後照設定呼叫 `set_rerank`，和 `set_candidate_vertical` 一樣，使用者關掉的設定不會因為重新載入而被打開）。n-gram 雜湊的比對只是「配對」檢查，**不是**對 `.sjtl` 本身的完整性檢查。
- `shanjie_engine_set_rerank(engine, on, out)`：照 `set_demote` 的樣子回傳快照；關掉時重算，畫面回到 n-gram 的結果。
- `shanjie_engine_rerank_take(engine, **job)`（沒有工作時 `*job` 為 NULL）、`shanjie_rerank_run(job)`（任何執行緒都可以呼叫，不需要引擎；結果存在工作裡，對應 Rust 的 `RerankJob::run` 產生 `RerankResult`）、`shanjie_engine_rerank_apply(engine, job, out)`（用工作裡的結果呼叫 `apply_rerank`：有改變 → 0 與快照；沒有改變、過期或還沒 run → 新的代碼，`*out` 為 NULL）、`shanjie_rerank_free(job)`。
- **回傳碼與 panic**（安全審查 F1）：每個新匯出都包現有的 `guard`。「沒有改變、過期、還沒 run、已套用過」用新的具名常數 `SHANJIE_NO_CHANGE`（5，不和 1–4 撞號）。標頭寫明：`take`／`run`／`apply` 的非 0 碼**不代表按鍵直通**（它們沒有對應的按鍵）。碼 4 逐一寫：
  - `shanjie_engine_rerank_take` 回 4：和其他引擎函式相同，引擎照現有寫法 `discard`（組字清空），`*job` 為 NULL。
  - `shanjie_rerank_run`（回傳 `int32_t`：0 成功、1 job 為 NULL、4 攔下的 panic）回 4：**沒有碰任何引擎**，組字不變；不存結果，之後 apply 回 `SHANJIE_NO_CHANGE`。
  - `shanjie_engine_rerank_apply` 回 4：引擎 `discard`（組字清空），`*out` 為 NULL。
  - `docs/contracts/s3a.md` §6 那條「任何函式回 4 後引擎已重設」加一行例外：`shanjie_rerank_run` 的 4 不重設任何東西（它沒有引擎）。
- **NULL 規則**：`take` 的 engine 或 job 參數為 NULL → 1；job 參數非 NULL 時一律先設 `*job = NULL`。`run(NULL)` → 1。`apply` 的 engine、job、out 任一為 NULL → 1，`*out` 為 NULL。`free(NULL)` 不做事。
- **所有權與執行緒**（標頭每個函式寫明，s3a §6 也寫）：`take` 交出的每個工作都由呼叫端恰好 `free` 一次；`apply` 不釋放工作，套用後同一個工作再 apply 一律回「沒有改變」；同一個工作不可同時在兩個執行緒使用（run 回傳之後才能 apply 或 free）；引擎本身仍是單執行緒；`shanjie_rerank_run` 可以在別的執行緒呼叫，`shanjie_rerank_free` 在 run 回傳之後也可以在任何執行緒呼叫。工作只放擁有的資料（字串、分數、範圍、世代號、設定、`Arc<TinyLm>`），**不借用引擎**（不放 `&Engine`、指標、`Arc<Lm>`、`Arc<CappedLexicon>`），所以引擎釋放之後工作仍可 run 或 free。標頭列出未定義行為：同一個工作 free 兩次、free 之後再 run 或 apply、free 一個不是 `take` 交出的指標、run 進行中同時 apply 或 free。run 不可取消（新按鍵只能讓結果作廢，殼一次只跑一個 run，這寫進 2b）。結果放在 `Mutex<Option<RerankResult>>`，apply 用 `take()` 取走，所以同一個工作再 apply 一律回「沒有改變」，即使殼違規讓 run 與 apply 同時發生也不會是資料競爭。工作與結果都是不透明型別：沒有 `Debug`、不寫進任何紀錄（內含候選文字）；`load_tinylm` 的路徑也不寫進任何紀錄（照標頭現有的日誌規則）。
- **型別保證**（F8）：不寫任何 `unsafe impl Send/Sync`；用編譯期斷言確認 `TinyLm: Send + Sync`、`RerankJob: Send`、`RerankResult: Send`。

### 2.6 讀檔加固與 PLAN 的例外（安全審查 F2–F6）

- **讀檔**（`core/src/tinylm.rs`）：讀之前用 `metadata` 確認是一般檔案、長度不超過 64 MiB（具名常數；M 的 int8 約 15 MB）；檔頭上限收緊到出貨模型的量級（具名常數、註解寫來源：`V ≤ 65,536`、`ctx ≤ 1,024`、`d ≤ 2,048`、`n_layer ≤ 64`），讓前向計算的配置有上界；int8 還原後的權重也檢查有限值；Accelerate 分支的長度前提從 `debug_assert` 改成 `assert!`（靜態訊息）。
- **PLAN 的例外**（架構原則裡 XPC 那一行，並同步 S5 的模型那一行與風險表 R8 那一列的但書；不寫行號，行號會隨文件漂移）：「自己訓練、自寫格式（SJTL）、雜湊進版控並在建置時核對、隨簽章的 App 打包、路徑不可設定的 S7 字元模型，和 `bigram.sjlm` 同等對待，在主程序以嚴格讀檔器載入；只能在 n-gram 前 8 名之間重排、不能產生任意文字。下載的、第三方的或使用者指定的權重仍照 R8 放在 XPC helper。」條件中「自寫格式、嚴格讀檔器、失敗時關閉重排不讓程序當掉、只在前 8 名重排」在這一片成立；「隨 App 打包、路徑寫死、雜湊進版控並在建置時核對」是 2b 的出貨前提。
- **載入時是否再核對 `.sjtl` 的雜湊**：不做，和 `bigram.sjlm` 的現況相同（只在建置時核對）；做的話要在核心對讀進來的同一份位元組算，`load_tinylm` 要多一個參數，每次建引擎多約 40–90 ms。這個取捨記錄在這裡，之後若改成要做，是 ABI 變更。

### 2.7 不在這一片

殼（佇列、QoS、主執行緒套用）、設定開關的介面、模型檔的打包與 Release、實機檢查（2b）。保留集不重跑：計分是同一個 `TinyLm::rerank`、同一個 n-best 函式；差別是有固定詞時的自由段結尾（`End::Next`）不在 S7a 的量測範圍內，記錄為沒量過。

## 3. 驗收

1. **沒有模型或重排關掉**：`take` 永遠是 None；現有測試與 golden 全過、不改任何 golden 檔。
2. **引擎測試**（不開執行緒，`core/tests/engine_rerank.rs`）：套用後 `display`、`path`、預測列等於 `cands[TinyLm::rerank 的選擇]`；過期（取工作後再按鍵、改設定）→ None；兩個引擎不共用世代號；被學習加分的段不重排、其他段照樣重排；兩段夾一個固定詞時兩段都重排、固定詞不動，之後選字時的 `pre` 是重排後的文字；有工作還沒套用時按 Enter 送出畫面上的字、之後套用 → None；**打滿 40 音節自動送出後 `take` 為 None（或 apply → None），預測列選字觸發的送出也是**；**`set_demote(false)` 之後 `take` 為 None**；**`load_lm`／`set_lm` 換模型之後 `take` 為 None**；**預測列已進入（⌘⌫ 後）時 apply → None 且 `pred_sel` 不變**；全部選 0 → None。突變：拿掉世代檢查、拿掉 `clear_all` 的清除、拿掉學習保護、拿掉降權檢查、套用時漏掉重算預測列，各要讓測試失敗。
3. **FFI 測試**（照 `ffi.rs` 現有的子程序測試）：載入的各種錯誤碼、`load_tinylm` 失敗時子程序輸出不含暫存路徑、沒有工作時 NULL、run → apply → free、還沒 run 就 apply（回 `SHANJIE_NO_CHANGE`）、同一個工作 apply 兩次、所有 NULL 組合、`free(NULL)`、run 在注入點 panic → 回 4 且子程序的 stdout／stderr 不含標記（另用預設 hook 做正向對照，照 s3a §7.3），**之後引擎的組字不變**（下一個快照仍是原本的組字），同一個工作 apply 回 `SHANJIE_NO_CHANGE`、apply 碼 4 後組字為空、`load_tinylm` 不改重排的開關。**C 標頭冒煙測試**（`core/tests/c/abi_smoke.c`）每個新函式至少呼叫一次（含 NULL 路徑、沒有模型時 `take` 得到 NULL），並照 s3a §7.4 做一次對調欄位的反向檢查。**壞 `.sjtl` 的單元測試**：超過上限的檔頭（例如 V 2^24、d 1、檔長吻合）→ `Format`；int8 縮放值為 `f32::MAX` 使還原成 inf → `Format`；長度差 1 byte → `Format`；不是一般檔案、超過大小上限 → 錯誤。突變：拿掉 run 的 `guard`，注入 panic 的子程序測試要失敗。
4. **一致性重播**（main 執行；真模型的 `.sjtl` 用環境變數給路徑，不放進任何 `data/lm`；沒給時測試標明略過）：引擎用 `Engine::new(<repo>/data/lexicon, Layout::Standard)`（和 `engine_lm.rs` 的效能測試同一個建法） ＋ `load_lm(<repo>/data/lm/bigram.sjlm)` ＋ `load_tinylm(<.sjtl>)`、聊天設定、降權開著（預設）、不呼叫 `set_left_context`（不用 `engine_lm.rs` 的 `shared()`：它的 capped 沒有降權表）；dev302 每列逐鍵打、每個鍵之後同步「取 → 算 → 套用」、最後 Enter。對照：`cargo run --release -q -p cli -- --lm data/lm/bigram.sjlm --profile chat --dev 302 --tinylm <.sjtl> --rowstats …`（不加 `--context`、不加 `--no-demote`）的每列第一名；302 句逐字相同。
5. **量測**（main，空閒的機器，release）：載入模型時按鍵路徑 p95（含產生工作）< 16 ms；一次 run 的 p50／p95；CPU 時間／牆鐘時間比；重播裡「產生的工作數、套用後字有變的次數、按鍵數」與「同一個字在連續兩個鍵之間先變回 n-gram 再換回」的比例。寫進研究紀錄。
6. `make test`、CI 全綠；`/code-review` 沒有未處置的問題；fresh verifier 確認第 1–4 項。

## 4. 範圍外、停止條件、預算、限制

- **範圍外**：見 §2.7；model-v6 後重選 α、τ；活化值量化。
- **停止條件**：第 4 項任何一句不同（回報，不調整）；任何 golden 改變；按鍵 p95 ≥ 16 ms，或產生工作讓按鍵 p95 增加超過 1 ms；「變回再換回」的比例（A4 的量測）只記錄，由使用者在 2b 之前決定。
- **預算**：executor 實作 1 次＋修正 1 次；main 跑第 4、5 項。
- **限制**：executor 不跑 188、不連網；不讀 `eval/holdout`、`~/side-project/shanjie-private`、`~/.cache/shanjie/work`、學習檔、`~/Library`、鑰匙圈；不安裝、不啟動 App、不在 .app 裡執行；不 push、不開 PR、不碰其他 worktree；`.sjtl` 不放進任何 `data/lm`。

## 5. 回滾

revert 這一片；沒有呼叫 `load_tinylm` 時引擎行為不變，殼也還沒呼叫它。

## 6. 使用者的決定與 2b 之前要決定的事

- 已決定（2026-10-11）：非同步（A3／A7）；預設開＋設定開關（〔更正，2026-10-12〕改成預設關，`docs/contracts/s7c2c-delta.md`）；模型在主程序裡（in-process；這一片把 PLAN 的架構原則（XPC 那一行）、S5 的模型那一行與風險表 R8 那一列補上例外，條件見 §2.6）；A4 的衝突先量再決定。
- 2b 之前要決定：(1) A4：帶著第 5 項的比例，決定加「黏住已重排的前綴」的規則或修改 A4；(2) 開關的名稱與位置（設定視窗＋選單，名稱例如「語意校正」）；(3) Release 資產名稱（例如 `tinylm-v1`）與 `LICENSES/data.md` 的那一列（訓練文字是維基 20 萬篇與口語語料）。

## 7. 結果（2026-10-11）

- 實作 29e3b8a（security-executor，main 審 diff）：`make test` 全綠、沒有改 golden；第 2、3 項的測試都在，列出的突變（世代檢查、`clear_all` 的換號、學習保護、降權檢查、重算預測列、run 的 `guard`）各讓斷言失敗。
- 第 4 項：302 句和 CLI 逐字相同。
- 第 5 項：按鍵路徑 p95 2.45／3.53 ms（兩次；沒有小模型 2.75 ms，增加 0.78 ms，未超過 1 ms）；run p95 7.63／12.10 ms；CPU／牆鐘 1.00／0.97；「變回再換回」55／8,336 個鍵（0.66%）。數字在研究紀錄同日 S7c-2a 一節。
- 和契約不同的地方（executor 回報，main 同意）：套用成功後也換世代號（同一個工作 run 兩次時第二個結果作廢）；結果的 `Mutex<Option<…>>` 放在 FFI 的包裝裡，Rust API 用傳值保證只用一次；`refresh()` 開頭先清掉 `pending`（一次呼叫重算兩次時只留最後的工作）；直通鍵（⌘ 快捷鍵）也讓工作作廢（照 §2.3 字面，2b 要知道）；不是一般檔案回 `Io`、超過上限回 `Format`（C ABI 都是 3）；檔頭上限的測試用剛好超過一格的值；C 冒煙測試沒有成功的 run／apply（CI 沒有 `.sjtl`，成功路徑由 FFI 子程序測試負責）；take 回 4 沒有注入點、沒有測。

- **修正一後重量（d703754）**：302 句和 CLI 相同；按鍵 p95 2.65 ms（沒有小模型 2.49，+0.16）；run p95 8.32 ms；套用 p95 0.27 ms（有改字 0.55）；A4（2,559 個重新解碼的鍵）：有小模型 flicker 11.37%、net rewrite 9.34%、變回再換回 2.15%；沒有小模型 14.50%、14.50%、0。

## 8. 審查紀錄

- 安全審查（`pilotfish:security-reviewer`，2026-10-11，唯讀）：沒有可遠端觸發的嚴重問題；in-process 載入的實際風險和主程序解析 `bigram.sjlm` 相同（要先能寫入 App bundle）。12 項處置：F1（新 FFI 的回傳碼、panic、discard 語意）**FIX**，§2.5、§3 第 3 項；F2（PLAN 例外的行號與三處同步、成立條件）**FIX**，§2.6，打包與建置雜湊是 2b 的出貨前提，載入時不再核對雜湊（和 `bigram.sjlm` 相同，取捨記在 §2.6）；F3（檔案大小上限、一般檔案）、F4（檔頭上限收緊）、F5（int8 還原後的有限值）、F6（Accelerate 前的 `assert!`）**FIX**，§2.6、§3 第 3 項；F7（NULL 規則、未定義行為清單、工作不借用引擎、apply 取走結果、run 不可取消）**FIX**，§2.5；F8（Send／Sync 編譯期斷言、不寫 `unsafe impl`）**FIX**，§2.5；F9（結果沒有 `Debug`、路徑不進紀錄、失敗不洩漏路徑的測試）**FIX**；F10（`load_tinylm` 不改開關）**FIX**，§2.5；F11（C 冒煙測試涵蓋新函式）**FIX**，§3 第 3 項；F12（n-gram 雜湊比對不是完整性檢查）寫進 §2.5。
- 第一次 plan-verifier（2026-10-11）：REVISE 7 點。1–5 **FIX**：自動送出時 `clear_all` 也作廢工作、套用前核對範圍；FFI 工作的所有權與執行緒規則；降權關掉不產生工作、換 n-gram 模型丟掉小模型；第 4 項寫明引擎的建法與 CLI 指令；預測列已進入時不套用。6、7 由使用者決定後 **FIX**：A4 的衝突寫明、先量再決定；模型在主程序裡，PLAN 補例外（安全審查後改成架構原則裡 XPC 那一行，並同步 S5 與 R8；verifier 建議之後不寫行號）。
- 第二次 plan-verifier（2026-10-11）：前 7 點確認關閉；REVISE 1 點（缺安全審查），逐項處置 **FIX**：跑了安全審查，12 項都處置並寫進 §2.5、§2.6、§3（之後一次收尾審查）。
- 收尾審查（2026-10-11）：REVISE 1 點（碼 4 在 take／run／apply 的意思混在一起；run 沒有引擎、不會清空組字）。照流程停下來問使用者；**使用者 2026-10-11 決定**：照修並再送一次收尾審查。§2.5 三個函式各寫碼 4、s3a §6 加例外、第 3 項的 run panic 測試加「組字不變」。
- 第二次收尾審查（2026-10-11）：READY。
- /code-review（PR #116，2026-10-11）：15 項，13 項修正（d703754；設計上的改動寫成 §2.3、§2.4 的「修正一」），延後 2 項（用記住的選擇消掉閃動，留給使用者的 A4 決定；測試工具的重複）。
