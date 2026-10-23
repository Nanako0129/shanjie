# 契約：讀音項進解碼器（R1e 產品片，reading-decoder）

狀態：修訂一（2026-10-23；plan-verifier 第一次 REVISE 七項照改；第二次 REVISE 一項：延遲量測的測試名與「計時的引擎帶著讀音項」，照改），收尾審查 READY（2026-10-23）。取代 `reading-decode.md`：那份把讀音項放在重排工作裡，只在語意校正開著時生效，已經作廢。

## 0. 起因、要做到什麼、怎麼看到

- **起因**：
  - 維護者 2026-10-22：「變快」被選成「便會」，是 n-gram 只看字、不看讀音造成的，應該從根本修，不該靠常談。
  - 維護者 2026-10-23：「分級本身講的是語意校正強度」「所以R1不是修正的是ngram嗎」「分級只在語意校正調整，其他都是選最好的成績不是嗎」。
- **探索**（`experiments/r1-reading-decode/README.md`）：
  - R1d（讀音項、常談、融合三件一起換）照登記不過。
  - 維護者選「拆開」。R1e 只換一件事：讀音項進 n-gram 解碼器，融合裡的讀音通道項拿掉。
  - R1e 在八種設定（聊天、書面 × 語意校正關、保守、標準、積極）對各自現在的出貨四條全過，三檔 top1 的高低順序保持。
- **要做到**：
  1. **解碼器帶讀音項**：引擎與評測 CLI 的 n-gram 解碼器，對每個單字詞的路徑分數加讀音項。聊天、書面、三種強度、語意校正開或關都生效。每一鍵同步算，組字區一顯示就是加了讀音項的結果。
  2. **融合拿掉讀音通道項**（P5 的 ν 項）。常談照現在的出貨留在融合裡，各檔的常談格不變。
  3. **候選窗也加**（維護者 2026-10-23 選「候選窗也一起加」）：規則在 §1「候選窗」。例如打 ㄎㄨㄞˋ 開窗，「會」（ㄎㄨㄞˋ 是它少見的讀音）往後排。
- **和維護者原話的對照**（寫在送審之前）：
  - 原話是「n-gram 解碼時乘上 P(讀音｜字)，每個設定、每個強度都生效」。這一片做到「解碼器的路徑分數」與「每個設定、每個強度、語意校正開或關」。
  - 候選窗的排序也加（維護者選的），量法在 §2.6。
  - 比原話窄的地方列在 §4「範圍外」。
- **怎麼看到**：
  1. 核心等於探索：產品的 CLI（預設）在 R1e 的 30 組（設定 × 集合）上，和探索分支 R1e 的 CLI 在同一台機器、同一個工作裡逐列相同（§2.1）。
  2. 實機：前文「否則」之後打「變快」，組字區等於產品 CLI 事先算好的選字（§2.7）。

## 1. 規則

- **讀音項**：和 R1e 同一份程式（`core/src/reading.rs`，探索分支的 `--reading-decode`）。
  - 路徑上每個單字詞 w 讀 r，加 λ·max(t(r, w), −c)，其中 t 是詞庫分數相對於該字最常見讀音的差。
  - λ 2.0、c 4。
  - 「一」的 ㄧˊ、ㄧˋ 與「不」的 ㄅㄨˊ 算 0。
  - 合併後詞庫分數等於底分 −6.8 的單字讀音，改用 mc 值（多字詞裡用這個讀音的質量）。
  - 多字詞、新詞、詞庫沒有的（讀音, 字）一律算 0。
  - 常數照現在 `reading.rs` 的具名常數，註解寫出處（R1c、R1e）。
- **哪裡生效**：`core/src/lm.rs` 解碼器（`decode_segment_learned` 的單音節詞條）。這是引擎組字、凍結前段之後的解碼、評測 CLI 共用的路徑。
  - 學習加分照舊：讀音項另外再加，學習守門的規則不變。
  - 沒有載入語言模型時，引擎走 `decode_beam`（只用詞頻），**不加**讀音項（範圍外，§4）。
  - 模糊音（`feat/fuzzy-input`，另一片）合進來時，容錯讀音的詞條也照同一條加讀音項；哪一片先合併，另一片在合併時處理。
- **表**：讀音項表（含 mc）在**載入詞庫時**建一次（`load_lexicon_packs`／`CappedLexicon::new` 裡），不依賴語言模型（引擎與 CLI 同一個函式）；`CappedLexicon` 與候選窗都讀同一份。解碼器用的部分事先算成和詞條對齊的陣列，解碼時直接取，不再每鍵查字串（`/code-review` 2026-10-23 延後到產品片的那一項）。
- **候選窗**（逐處寫明）：
  - `window_list`：每個窗裡 **l = 1 的那一段**（游標前一個音節的詞條，排在窗的最後），完全吻合的詞庫詞條改用「原始詞庫分數＋讀音項」做**穩定排序**（同分保持原本順序）。l ≥ 2 的段不變；預測列（排在最前面）、新詞（各段開頭）的位置與規則不變。
  - 英文窗裡的中文項（`engine.rs` `open_english` 取 `entries(..).first()`）：**改**，取上面同一個排序的第一個。
  - 英文 token 窗的中文項（`engine.rs` 約 3189 行，`f.zh` 那個音節的 `entries(..).first()`）：**改**，同上。
  - 數字層的另一種讀法（`engine/digit_layer.rs` 約 465 行的 `entries(..).first()`）：**改**，同上。
  - 沒有載入語言模型時，候選窗照樣用這個排序（表在載入詞庫時就建好）。
  - 這幾處共用一個核心函式「單音節詞條照讀音項排序」，不各寫一份。
- **融合**：`core/src/colloquial.rs` 的 `fused_pick`／`guarded_pick` 拿掉 ν 項（`NU`、`channel_of`、`ReadingChannel`）。常談項、軟閘門、各檔的 (α, τ, δ) 不變。重排工作（`engine.rs` 的 `RerankJob`）不再帶 `channel_lex`。
- **評測開關與標記**：
  - CLI 與 replay 的 `--no-reading-decode`（只給評測用）：解碼器與候選窗都不加讀音項，融合照產品（沒有 ν 項）。
  - 拿掉 `--reading-decode`（產品預設就是開）、`--no-reading-channel`、`Engine::set_reading_channel`；加 `Engine::set_reading_decode`（只給評測用，不進 `shanjie.h`）。
  - 摘要行的標記：讀音項開著時，**每一跑**（聊天、書面、語意校正開或關、bench 的 anchor 格、replay）設定標記的最後都加 `+rd`；聊天的 `+colloq:<sha8>` 後面不再有 `+rc`。CLI 與 replay 同一條規則。
- **C ABI、殼**：不改。讀音項一律開，沒有使用者設定。

## 2. 驗收

1. **核心等於探索**（同一台 GCP VM、同一個工作；公開與私人集合都在那裡跑）：
   - **組合**：R1e 的 30 組，`experiments/r1-reading-decode/r1d.py` 的 `ROLES`（含聊天關、書面關）。
   - **列檔**：照 `r1.py`／`r1d.py` 的 `ROW_SHA256` 固定，開跑前核對雜湊；錯字回報＝`eval/dev/user-reported.txt` 的前 154 列（`63f67b72…`）；打字測驗與 dev302 用 `eval/bench/suite-v1/`。
   - **參考**：同一個工作裡，用探索分支 `abd676ab` 建的 CLI，照 R1e 的跑法重跑 30 組（探索 README「R1e」與它的實作註記：聊天開 `--reading-decode --colloquial-core … --no-reading-channel`，關的設定只給 `--reading-decode`）。
   - **產品**：同一個工作裡，用產品 commit 建的 CLI，照產品預設跑同樣 30 組（聊天開帶 `--colloquial-core` 與 `--tinylm`，關的設定不給 `--tinylm`，不給任何讀音相關選項）。
   - **要相等**：每一組的摘要行標記（兩邊都是 `…+rd`，聊天開沒有 `+rc`）、`top1_sha256`、rowstats 檔逐位元組相同。
   - 記錄 VM 的機器型號、兩個 commit、每個列檔的雜湊。任何一組不同就停。
2. **不變的部分**（公開集合：dev302、打字測驗、錯字回報前 154 列；在 Mac 上跑，兩邊同一台）：

   | 設定 | 本分支 | main（`67651c73`） | 要相等 |
   |---|---|---|---|
   | 聊天標準／保守／積極 | `--no-reading-decode --tinylm … --rerank-level L --colloquial-core …` | 同一條指令，`--no-reading-decode` 換成 `--no-reading-channel` | `top1_sha256`、rowstats |
   | 書面標準／保守／積極 | `--no-reading-decode --tinylm … --rerank-level L` | 同一條指令，不給 `--no-reading-decode` | 同上 |
   | 聊天關、書面關 | `--no-reading-decode` | 同一條指令，不給 `--no-reading-decode` | 同上 |
   | replay（聊天、書面各一：打字測驗） | `shanjie-replay --no-reading-decode`，帶小模型與常談表 | `shanjie-replay --no-reading-channel`，帶小模型與常談表 | 送出的文字逐列相同 |

   - 每一跑的共同部分：`--lm data/lm/bigram.sjlm --profile P --context --packs acg --rows <列檔> --rowstats <檔>`。
   - golden：會變的逐行列出、說明原因（讀音項改了哪些第一名），其他不動。
3. **測試**：
   - 單元：讀音項的兩個例外、mc、單字以外算 0、事先算好的陣列等於逐字查的結果、候選窗排序函式（穩定、只動單字詞條）。
   - 引擎：
     - 一列公開資料，讀音項讓組字區的第一名改變（例如錯字回報第 10 列「版控」）：引擎組字區等於 CLI 的選字，語意校正關也成立；
     - 學習加分的段照舊顯示學到的詞，`guard_skips` 照舊；
     - 融合沒有 ν 項（給一組讀音通道項會改變選字的分數，斷言選字不受影響）；
     - 候選窗：打 ㄎㄨㄞˋ 開窗，「會」的名次比原始詞庫順序後面；英文窗的中文項、英文 token 窗的中文項、數字層的另一種讀法，各一個測試斷言取到的是排序後的第一個（挑一個讀音項會改變第一名的音節）。
   - 突變：拿掉變調免扣、拿掉 mc、λ 改 0、融合加回 ν、讀音項只在重排開時加、候選窗不加讀音項。每個至少讓一個測試以斷言失敗（看結束碼與斷言數，編譯錯誤不算）。
4. **延遲與記憶體**（這台 Mac、release、閒置窗，和 main `67651c73` 用同樣的方式量，兩邊交錯各跑一次）：
   - 重排與每鍵延遲：`cargo test --release -p core --test engine_predict timing_composition_cap_128_syllables -- --ignored --nocapture --test-threads=1`（`Engine::new`＋`load_lm`＋小模型＋常談表），取它印的「median of 5」三個數：重排開的每鍵 p95 ≤ 16 ms、`RerankJob::run` 的 p95 ≤ 300 ms、重排關的每鍵 p95 ≤ 16 ms（就是它斷言的三個門檻）；和 main 的倍數一起報。
   - 另一道：`timing_typing76_keys`（同一個指令換測試名）印的那一行 typing76 p95 ≤ 16 ms。
   - **計時的引擎帶著讀音項**：讀音項表在 `load_lexicon_packs`／`CappedLexicon::new` 裡建（§1「表」），所以 `engine_predict.rs` 的 `shared()` 與 `Engine::new` 兩條路都帶。兩個計時測試開頭各加一個斷言：一個已知會因讀音項改變第一名的公開列（例如錯字回報第 10 列），在被計時的那個引擎上選到加了讀音項的結果。
   - RSS：`/usr/bin/time -l` 量 CLI 跑 dev302 聊天標準（帶 `--tinylm` 與 `--colloquial-core`）的最大常駐記憶體，本分支對 main 增加 ≤ 10 MiB。
   - 載入時間：引擎建構加 `load_lm` 的時間（本分支對 main），只報，不設門檻。
   - 超過門檻就停。
5. **工具**：
   - `tools/triage.py`、`kpi.py`、`bench.py`：v0.5.0 之後的版本，每一格 CLI 與 replay 的標記最後都是 `+rd`（兩種設定、bench 的 anchor 格、replay 格），聊天的 `+colloq:<sha8>` 後面沒有 `+rc`；v0.5.0 與之前的版本不變。
   - `test_bench.py`、`test_kpi.py`、`test_triage.py` 對 v0.5.0 之後的聊天、書面、anchor、replay 各有一個標記斷言；`make test-python` 通過。
   - 用新 CLI 跑一次 `tools/triage.py`，聊天與書面都要跑完。
6. **候選窗**（事先固定，看到任何數字之前）：
   - **量法**：每一列的每一個漢字位置，用它的正解讀音單獨開窗（組字區只有那一個音節、預測關），算正解字在窗裡的名次（從 1 起）。等於在那個讀音的單音節詞條裡排名，讀音項加前（原始詞庫順序）、加後（§1 的排序）各算一次，用核心的同一個排序函式。只報數字，不含字。
   - **不算的位置**：正解字在那個讀音下不在詞庫裡、或那一列的漢字數不等於音節數（整列跳過）。兩種各自計數並報出。
   - **集合**：打字測驗、錯字回報前 154 列（真人讀音）、dev302、cvtune-clean 與 discordtune（私人，在 §2.1 同一個 GCP 工作裡算）。
   - **報**：名次變好、變差的位置數，以及「從第一頁（前 9 名）掉出去」的位置數。
   - **閘門**：錯字回報與打字測驗上，從第一頁掉出去的位置數要 0，名次變差的位置數不超過變好的。不過就停下來和維護者討論，不改規則。候選窗確實有改到，由 §2.3 的測試與突變證明，不靠這個閘門。
   - 〔推論〕工具標讀音的集合（dev302、cvtune、discordtune）幾乎只看得到變好，真人讀音集是唯一看得到「次要讀音被往後排」的地方。
7. **實機**（繁中 tart 虛擬機，裝本分支的開發版）：
   - 設定照 `CLAUDE.md`「實機測試與虛擬機」用 `defaults write` 改：聊天、書面 × 語意校正關、保守、標準、積極，共 8 種。
   - 打法：前文「否則」送出之後，打「變快」（ㄅㄧㄢˋ ㄎㄨㄞˋ）。imeshot 從 .app 裡執行（要讀前文）。
   - **預期**：每一種設定的組字區，等於產品 CLI 在同一種設定下跑 `否則|變快|ㄅㄧㄢˋ ㄎㄨㄞˋ` 這一列的選字（事先算好、寫進 PR）。任何一種不符就停。

## 3. 要一起改的地方

- 契約：
  - `reading-decode.md` 狀態改成作廢，指到本契約；
  - `reading-channel.md`：融合層的 ν 項由本片取代，狀態行與內容一起改；
  - `colloquial-term.md`、`colloquial-levels.md`：融合不再有 ν 項；
  - `s3a.md`、`core/include/shanjie.h`：說明裡提到重排、解碼或候選窗排序的地方。
- 文件：
  - `docs/verification.md` 的出貨標記；
  - README、`docs/PLAN.md` 提到讀音通道項的段落；
  - `docs/iteration-plan.md` 的研究順序。
- 研究紀錄：同日寫進。
- grep 「讀音通道項」「`NU`」「`+rc`」「`--no-reading-channel`」「`set_reading_channel`」：除了研究紀錄的歷史段落，沒有一句還在說融合層有讀音項。

## 4. 停止條件、預算、回滾、範圍外

- **停止**：§2.1 有任何一組不同；§2.2 不變的部分有改變；§2.4 超過門檻；§2.6 閘門不過；§2.7 不符。
- **預算**：GCP ≤ 1.5 小時（§2.1 的兩邊各 30 組，加 §2.6 的私人集合）。
- **回滾**：revert 本片的 merge commit。
- **範圍外**（比維護者原話窄的地方都在這裡；送審前已經和維護者說明，候選窗由維護者決定加入本片）：
  - **P(讀音｜字) 的估法**：仍是詞庫分數加 mc 的近似。把它估準是「讀音計數 B」，下一片。
  - **沒有語言模型時的解碼**（`decode_beam`）：不加讀音項，沒有量過；出貨的 App 一定載入語言模型。
  - **常談該不該移出融合**（分級原則）：R1d 量到直接移出會退步，另外處理。
  - 模糊音的 δ 與它和語意校正的關係：`fuzzy-input.md` 那一片。

## 5. 實作補記（2026-10-23，只補契約沒寫的選擇，規則與數字沒動）

- **表放在哪**：`Lexicon` 的 `rd` 欄位（`core/src/reading.rs` 的 `Lexicon::with_reading_terms`），由 `load_lexicon_extra_detailed`（`load_lexicon_packs` 的共用路徑）建；只含單音節讀音的詞條，和詞庫的詞條同順序。`CappedLexicon::new` 複製一份，依封頂分數重排單音節讀音時，把 (詞條, 項) 成對排，所以項跟著詞條走。`Scoring` 加 `rd` 欄位當開關（`Engine::set_reading_decode`、CLI 的 `--no-reading-decode`）；CLI 因此改呼叫 `lm::decode_from_scoring`。
- **候選窗的函式**：`Lexicon::entries_by_reading(key)`：一個音節的 key 用「原始分數＋項」穩定排序，其他 key 與沒有表的詞庫回 `entries(key)`。引擎的 `window_entries` 在讀音項關著時回 `entries`。
- **載入時的記憶體**：多字詞列只留「有一個位置會加到底分字的質量」的那些（求和結果相同；單音節詞條的項對探索的逐字查法位元相同）。全部留著的初版，CLI 的最大常駐記憶體比 main 多 17.5～19.6 MiB。
- **§2.6 的工具**：`cli/examples/window_rank.rs`，指令 `cargo run --release --locked -p cli --example window_rank -- --rows 列檔 --name 集合名 --packs acg [--detail 檔]`；「漢字數不等於音節數」用句子的字元數對音節數，整列跳過；`--detail` 只寫數字（列、位置、前後名次）。
- **golden**：`cli/tests/golden.rs` 釘的是 Python 參考實作的數字，那些跑加 `--no-reading-decode`，golden 檔不動；預設的跑和 golden 相比的差異寫在研究紀錄。
