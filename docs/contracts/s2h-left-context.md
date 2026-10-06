# S2h 契約：前文接進 n-gram 解碼

使用者 2026-10-06 回報「每次打吧都會變巴」。重現（研究紀錄同日）：ㄅㄚ˙ 單獨成一段組字時，兩種設定都是「巴」；同一段裡前面有「好、走、對」時都對。殼傳進核心的前文（`set_left_context`，最多 2 個漢字）目前只給選字記憶用，語言模型每次組字的第一個詞都從 `<s>`（句首）算起。使用者先送出前半句、再接著打，第一個詞就被當成句首。「照建議」打成「趙建議」也是這個形狀（研究紀錄 2026-10-05）。PLAN「貝氏模型研究」把這一項排在最前面，融合實驗的先驗也要以前文為條件。

**怎麼算做到**：使用者打「好」送出，接著打 ㄅㄚ˙，組字區直接出現「吧」（現在是「巴」）。自動測試證明引擎行為（§5.7），使用者實機確認（§7）。

## 1. 規則（核心，一個函式）

- 新函式 `lm::history(left, lm) -> &str`。`left` 是核心已經存的前文：`context_key` 的結果，最多 2 個漢字，空字串表示沒有。
  - 依序試：`left` 整段（2 個字時）、`left` 的最後 1 個字。第一個**在模型裡有 bigram 歷史紀錄**的就是歷史詞。「有歷史紀錄」的定義：Rust 是 `lm.ctx_of(lm.word_id(x))` 不是 `None`；Python 是 `lm.ctx.get(lm.ids.get(x, -1))` 不是 `None`。
  - 都沒有，或 `left` 是空的：歷史詞是 `<s>`，和現在一樣。
  - 理由：歷史詞沒有紀錄時，`prob` 直接回傳詞庫機率，等於不看前文；這時退回 `<s>`，維持現行行為，不另外引入「沒有句首、也沒有前文」的第三種狀態。
- **只用在組字的第一段**：左邊沒有固定詞的那一段（`refresh_lm` 的 gap 0）。其他段落照舊：左邊是固定詞就用那個詞，左邊是標點就用 `<s>`（s3d §4）。
- `total_score` 的起點改成同一個歷史詞，和解碼一致。
- 前文本身不計分（不加 P(前文)），只當第一個詞的條件。句尾 `eos` 不變。
- 選字記憶（S4）不變：它本來就用前文，學習與查表的 key 不動。

## 2. 資料與隱私

- 殼讀什麼、讀多少、什麼時候讀，全部照 S4 §2（R4）：組字開始讀一次，最多 2 個漢字，只在記憶體；`privacyGate` 擋的時候傳 NULL，歷史詞就是 `<s>`。
- C ABI 不變。`core/include/shanjie.h` 只改 `set_left_context` 的註解，補一句「也當第一個詞的 bigram 條件」。
- 不另做 security-reviewer：沒有新讀取、新儲存或新傳送，只是把已經在記憶體的 2 個字多用在計分。

## 3. 參考實作與評測 CLI

- Python `reference/proto/lm.py`：新增 `history(left, lm)`（規則同 §1）；`decode(lex, syls, lm, profile, beam=64, start="<s>")`，第一個詞以 `start` 為歷史。預設值讓既有呼叫的結果逐位元組不變。
- 前文欄的截斷：評測檔的前文是整段文字，先用和 `context_key` 相同的規則截成尾端連續漢字、最多 2 字，再進 `history`。Python 新增對應的 `context_key`。
- `reference/proto/lm_eval.py` 與 Rust `cli` 各加 `--context` 旗標：用每列的前文決定第一個詞的歷史。摘要行的 profile 欄寫成 `lm-chat+ctx`／`lm-formal+ctx`，其餘欄位格式不變。
- **不加旗標時行為不變**：既有 golden（`eval/golden/s2-lm.txt` 等）一個位元組都不改。
- 新 golden `eval/golden/s2h-lm-context.txt`：dev302、typing76、user-reported 在兩種設定、`--context` 下的摘要行。Rust 與 Python 逐位元組相同，`cli/tests/golden.rs` 檢查。
- 引擎重播：有前文的列以 `set_left_context(前文)` 開始組字，第一名要和 `cli --context` 相同（比 `top1_sha256`），寫在 `core/tests/engine_lm.rs` 或同層的新檔。

## 4. 切尾集（量目標情境）

公開評測集的前文大多是上一個子句，中間原本有標點；實際打字時前文會停在標點，歷史詞是 `<s>`，所以它們量不到「送出前半句再接著打」。另外產生切尾集量這個情境：

- 程式 `experiments/s2h/split_tail.py`（進 git），輸入是 cvtune、wikitune（`~/.cache/shanjie/work/s2/tune/{cvtune,wikitune}.txt`，不進 git），輸出寫到 `~/.cache/shanjie/work/s2h/`。
- 每一列：用詞庫的最高分切分（和 `experiments/s2/build_counts.py` 的 `segment()` 同一規則）切正解句子。至少 2 個詞的列才用。**尾段**是最後一個詞，**前文**是其餘部分，讀音照字數切開。
- 量法：只解碼尾段，比「歷史詞＝`<s>`」與「歷史詞＝`history(前文)`」兩種的第一名，寬鬆對照同 `lm_eval.py`。
- 不調任何參數（規則沒有參數），所以用調參集量不會造成調參洩漏。

## 5. 驗收（自動）

1. **一致性**：新 golden 的 Rust 與 Python 逐位元組相同；引擎重播與 `cli --context` 的 `top1_sha256` 相同。
2. **沒有前文就不變**：既有 golden 全部通過、沒有修改；`--context` 模式下前文為空的列，第一名和不加旗標時相同（逐列比對）。
3. **目標情境（關卡）**：cvtune 切尾集與 wikitune 切尾集，聊天與書面兩種設定，四格都要「修好 − 弄壞 > 0」且 McNemar p < 0.05。任何一格沒過就是停止條件。
4. **不退步（守門）**：dev302、typing76、user-reported 在 `--context` 下，兩種設定的第一名前後數字、修好／弄壞、McNemar。任何一格淨值為負且 p < 0.05 就否決（methodology 的守門規則）。報告註明這些前文多半是上一個子句。
5. **回報形狀（只報告、不當關卡）**：新探針檔 `eval/probe/s2h-context.txt`（CC0，main 寫）：

   ```
   好|吧|ㄅㄚ˙
   走|吧|ㄅㄚ˙
   對|吧|ㄅㄚ˙
   我們|照建議|ㄓㄠˋ ㄐㄧㄢˋ ㄧˋ
   不如|照建議|ㄓㄠˋ ㄐㄧㄢˋ ㄧˋ
   我|試了|ㄕˋ ㄌㄜ˙
   我昨天|試了一下|ㄕˋ ㄌㄜ˙ ㄧ ㄒㄧㄚˋ
   ```

   報每列前後的第一名與分數。這幾列是照已知的錯誤挑的，所以不當成效果的證據。
6. **延遲**：release 重播的每鍵 p95 < 16 ms 斷言照常通過；報前後的 p95。
7. **引擎行為**：整合測試以真模型：`set_left_context("好")` 後打 ㄅㄚ˙，`preedit` 是「吧」；不給前文時是「巴」（記錄現況，前文為空時行為不變）。
8. **保留集**：片收尾時由 fresh verifier 跑一次，預設與 `--context` 兩種模式，只回數字。
9. **選字記憶（S4）的驗收不動**：`core/tests/engine_learn.rs` 會先 `set_left_context`（「可以」「我們」「好」與 dev 列的前文）再解碼，所以這片的規則會影響它們。現有的每一個測試與斷言都要**原封不動地通過**：不得修改、刪除或放寬。任何一個失敗就是停止條件，交給 main 決定是否另寫修訂（重新寫明 S4 §12 的關卡：0 污染、學會率 ≥ 80%、鏡像 ≥ 9、ε_global 的選值，並送審）。

## 6. 範圍外

殼的改動、C ABI、讀更多前文、trigram、詞彙表外的歷史詞改用 unigram、標點感知的歷史、選字記憶的改動、判斷器融合（下一片）、改變評測 CLI 的預設模式。

## 7. 驗收（要使用者實機，agent 不能做）

main 安裝後，請使用者在 TextEdit：打「好」按 Enter 送出，再打 ㄅㄚ˙，看組字區是不是「吧」；再試「我們」送出後打 ㄓㄠˋ ㄐㄧㄢˋ ㄧˋ，看是不是「照建議」。

## 8. 負責人、範圍、預算、停止條件

- `pilotfish:executor` 在 worktree `/Users/nanako/side-project/shanjie-s2h`（分支 `feat/s2h-left-context`）實作 §1、§3、§4、§5.1–5.7。main 審查、跑關卡、推送、開 PR。
- **main 在同一個 PR 更新的規格**（CLAUDE.md：改到核心行為要同步規格）：`docs/contracts/s3a.md` 的解碼與 `total_score` 兩行（第一段的前文詞改由 §1 的規則決定）；`docs/contracts/s3d-punctuation.md` §4（組字區內的標點之後仍從 `<s>` 起算，第一段改用 §1）；`docs/PLAN.md`「前文接進 n-gram 解碼」的做法敘述改成 §1 的規則（現在寫的是「用詞庫切出最後一個詞」）；研究紀錄。
- 可改的檔案：`core/src/lm.rs`、`core/src/engine.rs`、`core/include/shanjie.h`（只改註解）、`cli/src/main.rs`、`cli/tests/golden.rs`、`core/tests/` 底下新增測試檔或在既有檔案**新增**測試（不改 `engine_learn.rs` 既有的測試與斷言）、`reference/proto/lm.py`、`reference/proto/lm_eval.py`、新增 `eval/golden/s2h-lm-context.txt`、新增 `eval/probe/s2h-context.txt`、`eval/README.md`（列出新探針檔）、新增 `experiments/s2h/`。文件（研究紀錄、PLAN）由 main 寫。
- 預算：executor 1 回合加 1 次修正。
- 停止條件：§5.3 任何一格沒過；§5.4 否決；一致性在 1 次修正後仍不是逐位元組相同；p95 斷言失敗；任何前文為空的列改變；`core/tests/engine_learn.rs` 任何既有斷言失敗（§5.9）。
- 回滾：revert 這個 PR。沒有資料、檔案格式或 ABI 遷移。

## 9. executor 不可以做的事

- 不安裝、不啟動 App、不呼叫 TIS 或 lsregister、不碰 `~/Library` 與鑰匙圈。
- 不連網、不用任何 API token、不做付費呼叫。
- 不讀 `eval/holdout/`、`~/side-project/shanjie-private/`，不讀使用者的 Discord 資料、學習檔或聊天紀錄。
- 不 push、不開 PR、不在 GitHub 上留言或改任何東西。
- 不改既有的 golden 檔，不改 §8 列表以外的檔案。
- 不修改、刪除或放寬 `core/tests/engine_learn.rs` 既有的任何測試或斷言；不碰 `docs/contracts/` 底下的規格（由 main 改）。
