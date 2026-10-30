# 接回中英混打的直接回退與單按 Shift，加上「鍵序顛倒不當英文」

狀態：草稿（2026-10-30）。

## 0. 起因與維護者的決定

- **Threads 使用者回報**（2026-10-30）：
  - 他裝了 0.6 版，把「選字」裡的設定都打開了，還是打不出 `ok`、`gta6`。
  - 維護者三天前在 Threads 回過他「下一版會支援 2 字的英文輸入」「下一版會更新」。
- **0.6.0 的現況**（程式與測試）：
  - `ok`：要打開「依按鍵順序判斷英文」，打完按空白，再從英文窗選。
  - `gta6`、`rdr2`、`poe2`：打不出來。
  - 能打出它們的「直接回退」（`zhen-revert.md`，PR #319、#320）和單按 Shift（`shift-toggle.md`，PR #321），10-28 因為實機誤觸太多，由 PR #322 一起 revert。
- **誤觸的原因**（研究紀錄 2026-10-28）：
  - 快打時，同一個音節裡後面欄位的鍵常常先到，例如「個」`e k 7` 打成 `k e 7`。
  - 觸發 (b)「逆序」把這些全當成英文。
  - 窮舉每個讀音的每一種鍵序：顛倒的鍵序共 3,457 種，撞到英文的只有 17 種；實機六個例子在「顛倒不當英文」之下都是中文。
  - 同一天還有第二種誤觸：照順序打的 `u,3` 也變成英文。那時單按 Shift 已經把那個 App 切成英數模式，組字區空著時按鍵整個放行。是哪一次單按 Shift 切過去的、維護者當時有沒有看到「英」的提示，沒有記錄〔原因不明〕。
- **維護者的決定**（2026-10-30）：
  - 「多增加一個功能，開啟後可以防止顛倒不當英文預設開，切換鍵只開放shift」。
  - 範圍選「接回來＋顛倒保護＋Shift」。
  - 版本選「等這個做完一起出」，也就是 v0.6.1 含 #272、#334 與這一片。

## 1. 要做到什麼

1. **接回**：把 PR #322 裡三個 revert commit（`34b556b3`、`5645d259`、`714744fe`）反轉，讓 PR #319、#320、#321 的程式回到 main。
   - 規格照 `zhen-revert.md` 與 `shift-toggle.md`，除了下面第 2、3 點改的地方。
   - 這兩天 main 的改動都保留：SJ-LM-7、詞包、0c 移除、#334 等。衝突照這個原則解。
   - PR #322 的文件 commit（研究紀錄、契約的「已 revert」說明）不反轉。兩份契約開頭的狀態改成「2026-10-30 接回，另加 `zhen-reapply.md`」。
2. **新開關「快打時鍵序顛倒不當英文」**：
   - C ABI：`int32_t shanjie_engine_set_english_reversal_guard(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out)`。回傳碼和 `set_english_key_order` 相同：0 成功並回快照；engine 或 out 是 NULL 回 1；0、1 以外的值回 2，狀態不變。引擎建立時預設 1。開著時觸發 (b)（逆序）不發生，鍵照欄位放進去，和蘋果注音一樣，不管「依按鍵順序判斷英文」開或關。
   - 這個開關不影響 (a) 覆寫、(c) 空白鍵與 (c) 聲調鍵。(c) 聲調鍵仍跟著「依按鍵順序判斷英文」。
   - 第三片的兩字母清單（`TWO_LETTER`）裡，只能靠逆序打出的詞，開著時打不出：`ls mv ux py pm` 等（`zhen-revert.md` §2.6 的逆序那一組）。
   - 殼：UserDefaults 鍵 `englishReversalGuard`，沒有值當 true；每次建引擎都送，放在 `setEnglishKeyOrder` 旁邊。
   - 設定視窗：放在「依按鍵順序判斷英文」之前，反灰條件相同，下一行說明：「快打時，同一個字的注音鍵先後顛倒（例如 ㄍㄜˋ 打成 ㄜㄍˋ）照注音處理，不會變成英文。關掉而且「依按鍵順序判斷英文」開著時，ls、mv 這類字不用按 Shift，但快打容易誤觸。」〔2026-10-30 實作修訂，照 `/code-review`：(b) 要「依按鍵順序判斷英文」開著、新開關關著才會發生；新開關在「依按鍵順序判斷英文」關著時反灰。〕
   - 「依按鍵順序判斷英文」的說明改成：「打了聲調鍵才發現音節不存在時，整段變成英文（馬上按 Esc 變回注音）；「快打時鍵序顛倒不當英文」關著時，注音鍵沒照順序也會變成英文。」
   - 評測工具 `english-sim`（`cli/src/english.rs`）：加 `--reversal-guard on|off`，**預設 off**，建引擎時送 `set_english_reversal_guard`。這樣它的臂 A–D、`revert_b_*` 欄與 `--recall` 的 probe 照舊是 `zhen-revert.md` §4 的意思，`cli/tests/english_sim.rs` 的固定種子數字不變；另加一個測試：`--reversal-guard on` 時 `revert_b_*` 是 0、`probe ls` 是中文。
3. **四個開關在接回後的殼預設值**（UserDefaults 沒有值時；存過值的照存的值）：
   - `englishReversalGuard` 是 true（新）；
   - `englishOverwrite` 是 true（`zhen-revert.md` §2.1）；
   - `englishKeyOrder` 是 true（`zhen-revert.md` §2.1；0.6.0 是 false）；
   - `shiftToggle` 是 true（`shift-toggle.md`）。
4. **切換鍵只有單按 Shift**：照 `shift-toggle.md`，預設開，不加 Caps Lock 選項。任務 #56 裡 Geoffeuz 要的 Caps Lock 不做，維護者 2026-10-30 決定。
5. 文件同 PR 改：
   - `s3a.md` 的規則表（第 9、10e 條與 §2.3 的逆序列註明新開關）、`core/include/shanjie.h`；
   - `settings-window.md`、`settings-sidebar.md`；
   - `zhen-revert.md`、`shift-toggle.md` 的狀態；
   - README、`docs/PLAN.md` 的中英混打一項；
   - 研究紀錄。

## 2. 怎麼看到它有效（誰看）

先由 main 刪掉維護者 Mac 上開發版容器偏好檔裡這四個鍵（`defaults delete`；10-28 曾寫入 `shiftToggle` false、`englishKeyOrder` false），再讀回確認四個都回到第 1 節第 3 點的預設值，結果記進研究紀錄。接著維護者在自己的 Mac 上裝開發版，平常快打中文，確認沒有誤觸；再打 `ok␣`、`gta6␣`、`rdr2␣`、`poe2␣` 看到英文，單按 Shift 後打 `hello` 是英文。這一步要維護者親自做，合併前要做完。

## 3. 驗收

1. **核心測試**（`core/tests/engine_english.rs`，真實詞庫、標準排列、預設開關）：
   - a. 實機六例 `k e 7`、`u v , ␣`、`j n o 6`、`u r . 4`、`i a 7`、`k x 7` 分別是「個、些、隨、就、麼、了」。
   - b. `o k ␣`、`g t a 6 ␣`、`r d r 2 ␣`、`p o e 2 ␣` 都是英文 token，`preedit` 等於那串字。
   - c. 新開關關掉後，`l s ␣` 是英文 token；開著時是中文。
   - e. FFI（`core/src/ffi.rs` 的子程序測試）：`set_english_reversal_guard` 的回傳碼 0、1（NULL）、2（值 2）；在 C ABI 上開著時 `l s ␣` 是中文，關掉時 `preedit` 是 `ls`。`core/tests/c/abi_smoke.c` 加一段呼叫它。
   - d. `zhen-revert.md` §5.1 與 `shift-toggle.md` 的測試照舊通過。原本依賴 (b) 預設開的測試，改成明確關掉新開關後跑。
   - 突變：
     - 新開關不生效（(b) 照舊觸發）→ a 失敗；
     - 新開關擋掉 (a) → b 失敗。
2. **窮舉測試**：第三片 §4 的 `key_order_clean_typing_of_every_reading_is_never_english` 加一個版本，在預設開關下，每個讀音的每一種**顛倒**鍵序都不是英文。上面 17 種撞英文的也一樣不是英文。
3. `make test-swift` 全過；Rust 完整測試在另一台 Linux 機器上跑；CI 全綠。
4. **殼**：新開關的存取（沒存過是開、關掉後存 false、重建引擎仍送）、設定視窗的位置與反灰。
5. **維護者實機**（§2）：
   - 平常打字至少一段時間，沒有中文被當成英文。
   - 「因為單按 Shift 切成英數模式，字母變成英文」也算誤觸：維護者記下當時有沒有看到「英」的提示。
   - 英文四個字與單按 Shift 都對。

## 4. 停止條件

- 驗收 1a 任一例不是中文：停下來查。
- 維護者實機回報誤觸，包括單按 Shift 造成的：停下來，拿誤觸的鍵序重現，出選擇題。
- 解衝突時發現 #319–#321 的程式和這兩天 main 的改動在行為上互相矛盾（不只是文字衝突）：停下來問。

## 5. 範圍外

- 兩鍵時間差（`NSEvent.timestamp`）：等收到真人按鍵資料再說。
- Caps Lock 切換。
- 英文單字表、短詞清單的內容。

## 6. 負責人、順序、回復、預算

- **順序**：這份契約 → fresh `pilotfish:plan-verifier`（READY 才做）→ main 在這一片的 worktree 反轉三個 revert、解衝突（判斷多，main 自己做）→ executor 加新開關（核心、FFI、C 冒煙、`cli/`（`english-sim` 的旗標與測試）、殼、設定視窗、測試、文件）→ main 跑改到的 Rust 測試檔與 `make test-swift`，完整 Rust 測試推到 GitHub 後在另一台 Linux 機器上跑 → `/code-review`＋fresh verifier＋CI → main 清掉維護者 Mac 上的四個鍵、裝開發版 → 維護者實機（§2）→ merge commit → v0.6.1 發版流程。
- **回復**：
  - `git revert -m 1` 這一片的合併。程式回到 0.6.0 的樣子，含 #320 拿掉的單字表打包也會回來；建置照舊從 `data/english-words.txt.sha256` 下載單字表。
  - 舊版殼讀不到 `englishReversalGuard`、`englishOverwrite`、`shiftToggle`，直接忽略。
  - `englishKeyOrder` 沒存過值的使用者，在舊版下讀成 false（舊預設）〔推論，和 `zhen-revert.md` §8 同理〕。
- **executor 不可以**：連網；讀 `eval/holdout/` 與維護者的私人資料；安裝、啟動 App；執行 `.app` 裡的程式；呼叫 TIS 或 lsregister；碰 `~/Library` 與鑰匙圈；push、開 PR、發版；碰其他 worktree；在 Mac 跑完整 `make test-rust`（只跑改到的測試檔）。
- **預算**：
  - executor 1 回合加 1 次修正；
  - 完整 Rust 測試（另一台 Linux 機器）一次，≤ 30 分鐘；
  - 維護者實機 1 次，長度由維護者決定。只有這一步要佔用維護者的時間。
