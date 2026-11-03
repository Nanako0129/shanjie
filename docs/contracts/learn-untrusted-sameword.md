# 前文不可信的 App，與畫面已是該詞也記（LC2）

狀態：**修訂一**（2026-11-03；plan-verifier 第一次 REVISE 三項：確認規則照維護者兩次選擇題（第二次：開窗直接選畫面上的詞要學）改寫，並拿掉舊程式的「先動過選取」條件、量測改成逐位元組相同、上一片的紀錄帶進 main）。從 `docs/contracts/learn-anyctx.md`（修訂五，分支 `feat/learn-anyctx`）拆出來：那一片的確認一發照登記不過（研究紀錄 2026-11-03），兩條不過都只碰機制 1（跨前文篩重排池）。維護者在三位顧問的意見之後選「拿掉機制 1、重訓後再看」：機制 2、3 從 main 重組成這一片（本契約裡編成 §1 第 1、2 點）；機制 1 等會意重訓（`docs/contracts/sr-no-twphrases.md`，分支 `feat/sr-no-twphrases`）出來後先看還需不需要。

## 0. 起因

- 維護者在 Orca（`com.stablyai.orca`，Electron＋xterm.js）裡改選「權限」好幾次，下一次還是被換掉，學習檔裡只有一筆。
- Orca 回給輸入法的游標前文字是隱藏輸入框累積的舊字，不是終端機那一行（VM 診斷版量到：送出「權限加好了」、Enter 換行後，下一次讀到的前文仍是「權限加好了」）。每次改選都記在對不上的前文底下，下次查不到。
- 改選時畫面已經是那個詞（`pre == word`）時不學（`learn_commit` 的過濾）。〔推論〕「改很多次只有一筆」可能部分來自這裡。

## 1. 機制

1. **前文不可信的 App**：殼多一份清單（`LeftContext.untrusted`），清單裡的 App 一律不讀游標前的字：`leftContext` 回傳 `(nil, nil)`，不呼叫 `attributedSubstring`。在這些 App 裡，跟前文有關的東西都當成「前面沒有字」：n-gram 的前文與學習鍵（每次組字都是句首，句首合成鍵與 SINIT 的句首單字規則因此生效）、會意的前文（context-return §1 的 `left_full` 是空的）、預測列、英文後接預測的前文判斷、中英間空白（cjk-spacing）讀的前一個字。先放已量過的 Orca；其他 App 量過才加（研究紀錄記下量法與結果）。終端機清單（只傳漢字尾巴）照舊。
2. **畫面已是那個詞也照記**：候選窗選的就是畫面上那一段的字（`pre == word`），而且是兩個字以上的詞時，送出時照預測列選取的方式學（s4-learning §1.2 那一列）：被換掉的字當成空字串交給 `teach`，詞 +1.0；`teach` 的鍵規則（句首合成鍵，不記裸 `^`）與通用化照舊；`at_pick` 不減半（沒有被換掉的字）。什麼算「選了」（維護者 2026-11-03 選擇題「畫面上已經是『權限』，而且它是候選窗第一項。你按 ↓ 開窗、不移動選取、直接 Enter 選它——這樣要不要學？」選「要學」，`.agent-local/decisions.md` 同日）：候選窗開著時選到畫面上那個詞就學，不管它是不是第一項、開窗後有沒有動過選取，鍵盤（Enter、數字鍵）與滑鼠點選都一樣；之前挑過別的詞再選回原字也算（`pre_pick` 留著第一次選字前的字，原字 +1.0，X 不學、不減半）。沒開候選窗、直接 Enter 送出組字不算。經容錯音（模糊音、輕聲容錯等）讀出來的詞不學。候選窗照詞長排序（`window_list`），畫面上那個詞不一定是第一項。
   - 從 `feat/learn-anyctx` 帶過來的程式在選字處多一個「先動過選取（`Cands.moved`）」的條件（`engine.rs` 約 4243 行 `(tolerant || !c.moved) && pre == word …`）：照上面的決定拿掉 `!c.moved` 這一項，`tolerant` 的排除留著；`moved` 欄位沒有別的用途就一起拿掉。單字不適用（照舊不學，SINIT 的句首單字學習不擴大）。新詞 B 的 `new_word_spans` 過濾不變。
3. **不變**：重排池與守門（s7c2a §2.2）、學習的鍵、檔案格式、`LEARN_EPS`、`LEARN_EPS_GLOBAL`（0）、通用化規則、SINIT、⌘⌫ 忘記。核心在沒有任何學習紀錄時行為不變。
4. **範圍說清楚**：沒有跨前文保護。Orca 裡因為每次都是句首，「權限加好了」的學習鍵永遠是 `^>ㄐㄧㄚ`，下一次打同一句會命中、守門保住；後接的音節不同（例如「權限設定」）就查不到，照舊可能被會意換掉。這一點寫進 PR 與給使用者的說明。

## 2. 實作

- 從 main 重做，不整個 cherry-pick `feat/learn-anyctx` 的 `76466540`（它混了三個機制）。可以照抄那個分支裡屬於機制 2、3 的程式與測試：
  - 機制 1（第 1 點）：`macos/Sources/ShanjieKit/Learning.swift` 的 `LeftContext.untrusted` 與 `leftContext` 的早退、`LearningTests.swift` 的對應測試、`core/include/shanjie.h` 前文參數說明的那一句。
  - 機制 2（第 2 點）：`core/src/engine.rs` 的 `learn_commit` 與 `pre_pick`、「選取離開過」的判斷（`0616ddcd`）、`core/tests/engine_learn.rs`、`core/tests/engine_fuzzy.rs` 對應的測試。
  - 不帶過來：`protected_words`、`keeps`、池篩選、`sinit_swap` 的 `protect` 參數、`Learner::has_active`（只有機制 1 用）與它們的測試，以及 `eval/learn/anyctx-*`、`tools/anyctx_seed.py`。
- **把上一片的紀錄帶進 main**（`feat/learn-anyctx` 還沒合併）：這個 PR 一併帶進 `docs/contracts/learn-anyctx.md`（修訂五原文，加一行狀態：確認一發照登記不過、機制 1 未出貨、2 與 3 移到本契約）與研究紀錄 2026-11-03「換前文學習（learn-anyctx 修訂五）確認一發：照登記不過」那一節；兩者進 main 之前 `feat/learn-anyctx` 不刪。`docs/contracts/sr-no-twphrases.md` 在分支 `feat/sr-no-twphrases`，引用時標明分支。
- 文件同一個 PR 一起改：`core/include/shanjie.h`、`docs/contracts/s4-learning.md` §1.2（表格與「只學選字前畫面上那一段的字和選到的詞不同的段落」那一句）、`docs/contracts/context-return.md` §1、`docs/contracts/learn-single-ctx.md`、`docs/PLAN.md`、README 若有提到。

## 3. 測試（Mac 只跑改到的測試檔；完整 `make test-rust` 在 188）

- 殼：清單裡的 bundle 不呼叫 `attributedSubstring`，送給核心的前文是 nil，cjk-spacing 用的前一個字（`last`）也是 nil；不在清單的照舊。
- `pre == word`：開窗後沒動選取、直接選畫面上那個兩字詞（它是第一項）會學；畫面上那個詞不是第一項、移過去選它會學；滑鼠點選它會學；沒開窗直接 Enter 送出不學；經容錯音讀出的詞選了也不學；選取移到別項再移回來後選它，送出後學到的紀錄鍵是 `^>…`（不是 `^`），原本這個詞的權重沒有減半而是 +1.0；單字選畫面上那個字照舊不學；先選別的再選回原字算確認原字。現有「選同一個詞不學」的測試改成新的斷言。
- 沒有學習紀錄時核心行為不變：既有測試全過。

## 4. 量測（GCP 主跑、188 第二份；新版對 main 臂）

- 這一片不改選字的計分與解碼：機制 1 只在殼，`shanjie-eval`、`shanjie-replay` 看不到；機制 2 只在候選窗「選了畫面上的同一個詞」時多學一筆，`shanjie-replay --correct` 只在顯示與正解不同時，對第一個不同的字所在的那一段挑含正解字的候選（`cli/src/replay.rs` 的 `correct()`：開窗後直接按數字選，選取不會離開再回來），挑到的詞在那個位置一定和畫面不同（`pre != word`），碰不到這條路徑（main 讀過 2026-11-03 main 的 `replay.rs`）。
- 所以量測是回歸，通過條件是**逐位元組相同**：學習關，出貨標準 8 個集合的 rowstats 與 `top1_sha256` 兩臂相同；學習開（`--learn on --correct`，聊天與書面，標準檔），dev302、打字測驗、錯字回報、句首學習集、SINIT 三份確認組，兩臂的 `.r`、`.keys` 與摘要行逐位元組相同。任何差異（含變好、改選變少）都表示上面的前提不對，停下說明。`--strict` 照 SINIT 的分法。
- 腳本從 `.agent-local/scripts/anyctx/` 複製一份改：兩臂換成 main `fe6b0371` 與本分支的 HEAD；拿掉 anyctx 與 cv 的工作、種子產生與 anyctx 檔的上鎖核對；`report.py` 的判定改成兩臂逐位元組相同（任何差異就不過）。照規則送審、從複本執行、Monitor 監看；GCP 與 188 逐位元組相同才判讀。
- 這是程式改動的回歸量測，不是確認片，不用判讀集。

## 5. 驗收

- §3 在 Mac 通過（只跑改到的測試檔），完整 `make test-rust` 在 188 通過，`make test-swift` 通過；§4 成立。
- 實機（維護者，VM 的 Orca，聊天、語意校正開）：句首打「權限加好了」，改選一次「權限」、Enter；換行再打同一句兩次都是「權限」；重開 Orca 後也是。另在 TextEdit 確認前文照常讀（不在清單的 App 不受影響）。
- 機制 2 的實機：在 TextEdit 打一個兩字詞，按 ↓ 開候選窗、不移動選取、直接 Enter 選畫面上那個詞、送出，學習檔多一筆 `^>…` 的紀錄（讀檔前問維護者；VM 裡的學習檔不是維護者的私人資料）。
- 合併關卡照程式改動：兩個 fresh verifier、CI 全綠、merge commit。

## 6. 範圍外

- 跨前文保護（原機制 1）：等會意重訓出來，先用重訓後的會意、學習關跑 `feat/learn-anyctx` 的 anyctx 集 pos 列，看「不用學就對」的比例，再決定要不要開探索片（要做的話改成融合分數裡的一項、不硬篩池）。
- 通用紀錄加分；其他 Electron 終端機（量過才加）；殼自動判斷「前文不可信」。

## 7. 停止條件

- 學習關 8 個集合不同、學習開的否決集合兩臂有任何位元組不同、GCP 與 188 不同：停下報數字，不換組、不開子片救。
- 實機在 Orca 仍記不住：停下，加診斷 log 再量。

## 8. 預算與回滾

- 實作：executor 1 次、修正 1 次。量測：GCP 一份 30 分鐘內，188 第二份只設防卡死的時限。實機：維護者在 VM 的 Orca 與 TextEdit，每次佔用鍵盤 ≤ 15 秒。
- 回滾：兩個機制各自可退：清空 `LeftContext.untrusted`；恢復 `pre != word` 過濾。學習檔格式不變，機制 2 多寫的紀錄在舊程式裡照常讀。
