# 中英混打（實驗功能，預設關）第一片

狀態：修訂版（2026-10-14，plan-verifier 第 1、2 次 REVISE 逐項處置在 §9；維護者三個選擇題已答，§3.4、§3.2、§2.3），READY（2026-10-14 收尾審查，唯一一點是 §3.4 第 3 步殘留的「覆蓋」，已刪）；第 0 步已量（§1）。

## 0. 起因與討論

- 維護者 2026-10-13：「多做一個實驗性功能，中英混打」。現在打英文要按 Caps Lock 切輸入法；Caps Lock 切換本身也有系統問題（`CLAUDE.md`「已知的系統問題」）。
- 調查與和 Fable 的三輪討論在研究紀錄 2026-10-14「中英混打的調查與和 Fable 的討論」。結論：第一片只在**空白鍵那一刻**介入、只出候選、不自動送出、送出不加空白、和「可省略韻母」互斥、預設關；不把英文放進解碼器（注音每個音節有聲調鍵收尾，英文只會是最後那個未完成的單位）。
- 讀過的程式（這份契約的規則照它們寫）：`core/src/engine.rs` 的 `Cands`（約 353 行）、`dispatch`（約 1432 行起：⌘⌫、第 1–2 條、`candidate_key`、第 9–14 條、第 15–22 條）、`finish_syllable`（約 1738 行）、`open_candidates`、`forget_highlighted`、`choose`、`take_commit`；`core/src/ffi.rs` 的 `shanjie_engine_set_abbreviation`；`macos/Sources/ShanjieKit/Shell.swift` 的 `build()`（約 700–730 行）；`scripts/build-app.sh`；`LICENSES/data.md`；`tools/bench.py` 的 `SETS`／`SETS2`。

## 1. 第 0 步：先量需求（main，私人資料只回數字）

- 經維護者同意後，在 Mac 上讀原始聊天匯出（E2 用的那份，2026-09 照規則排除），只數：含純字母 ≥ 3 個字母 token 的訊息比例；每百則訊息的這種 token 數；token 後面接中文、空白、標點的比例；句首比例。網址與含數字的 token 不算。只回數字、不存任何 token。
- **兩個數字都 < 5% 就停在研究**，不實作；寫進研究紀錄。
- **結果（2026-10-14，維護者同意後量）**：含中文的自己訊息 58,451 則（Discord、Threads、Facebook 貼文；2026-09 排除），含純字母 ≥ 3 英文 token 的佔 **22.8%**，每百則 **79.5** 個；token 後面接空白 36%、中文 28%、標點與其他 30%、訊息結尾 5%；在訊息開頭的 5%。兩個數字都遠超過門檻，進行實作。不加空白的設計：不需要空白的情況合計 63%，需要的 36%（多按一次空白）。

## 2. 規則（功能生效時；其他時候和今天逐位元相同）

名詞：「覆寫」就是 s3a 第 9 條的「同一欄再按一次是取代」；「影子」是未完成音節附帶的原始按鍵紀錄；「英文窗」是本片新增的候選窗狀態。

### 2.1 生效條件（engine 強制）

- **記錄影子**的條件（四個都成立才記，否則影子一律是 `None`、不配置）：`set_english(1)`、`set_abbreviation(0)`（互斥）、標準排列、單字表已載入（§2.6）。
- **觸發**的條件（§2.2）再加：游標在組字區尾端（`cursor == syls.len()`）、只有一個未完成單位（`pend_prev` 是空的；互斥下必然如此）。
- **倚天排列**：影子不記、永不觸發；開著開關的倚天引擎和關著的逐位元相同（§5.1 有測試）。原因：倚天的字母鍵對應不同，§4 的量測與單字表切法都只對標準排列做過；倚天要另量。
- `set_abbreviation(1)` 時英文窗若開著：關窗、清影子，回傳快照。`set_english(0)` 同樣。

### 2.2 影子與觸發

- **影子**：未完成音節從第一個注音鍵開始，記下每個**注音鍵**（第 9、14 條處理的鍵）的鍵帽字元（`Key.ch`，不含 Shift），最多 32 個；另記「覆寫次數」（第 9 條裡那一欄已有符號、被取代的次數）。聲調鍵與空白鍵不記（它們結束音節或觸發）。
- **第 33 個注音鍵**：把影子清空（長度 0、覆寫 0），之後的鍵重新開始記。
- **影子跟著 `clear_pend` 一起清**：完成音節（第 10 條成功）、Esc（第 12 條）、標點（第 2 條）、送出、reset、回傳碼 4、第 11 條刪到未完成音節消失時，都清。
- **第 11 條 Backspace**（未完成音節還在）：影子丟掉最後一個記下的鍵；覆寫次數不變；欄位照第 11 條（顯示順序）刪。影子與欄位在覆寫過的音節上會分歧，但影子只用來查單字表，分歧無害。
- **觸發**（只在空白鍵、第 10 條之前）：影子原樣（含數字與標點鍵的字元時自然查不到）在單字表裡，而且覆寫次數 ≥ 1；**殘餘音節（目前三欄的符號加一聲）在詞庫裡**時，要覆寫次數 ≥ 2。單字表只有 `[a-z]{3,32}`（§2.6），所以「純字母、≥ 3」由查表保證，引擎不另外檢查。
- 不觸發時空白鍵照第 10 條（含「詞庫沒有的音節不收」）。

### 2.3 英文窗

- 觸發時空白鍵不當一聲：開英文窗，`Cands` 多一個 `english: bool`（清單項目的音節長度記 0），方向照 `cand_vertical`（和第 15 條開窗相同，開窗時固定）。清單：影子的小寫 `w`、首字大寫、全大寫，依序三項；殘餘音節在詞庫裡時第四項是**詞庫對那個一聲音節的第一個詞條**（維護者 2026-10-14 選擇題）（`lex.entries(&[音節])[0]`，和第 15 條候選窗對同一個音節的第一項相同）。
- 開窗時預測列照「開候選窗」的既有規則清掉；未完成音節的符號照舊顯示在 preedit 裡。
- **按鍵**（英文窗開著時；沒列的照第 3–8 條、直排照第 3v–5p 條，行為不變）：

| 按鍵 | 行為 |
|---|---|
| 1–9、Enter（含 Shift+Enter）、滑鼠 `pick(index)` | 選第 n 項／選取的那項（超出項數照第 3 條忽略）。**英文項**：先 `clear_pend`（影子跟著清，所以 `commit` 不含注音符號），再走 `take_commit` 的送出路徑，`commit` ＝ 組字區顯示的整串（游標在尾端，所以就是全部）＋那個英文，**不加空白**（學習照送出規則：固定詞的待學記錄照常學、英文本身不學；重排工作照送出作廢；左文清掉）；組字區清空、`handled` 1。**中文項**：清影子與未完成音節，把那個一聲音節插在游標處並把那個詞固定上去（`insert_token(音節, Some(詞))`，`pre` 是 `None`，不學），和第 10 條一樣重算、預測列重算 |
| ←→↑、↓（展開）、空白鍵（翻頁：只有一頁，所以回到第 0 項）、Page Up／Down（直排） | 照第 4、5、4v–5p 條，不送出 |
| Esc、Backspace | 關窗、**清影子**，未完成音節保留（第 7 條的「不改變」加上清影子；再按空白鍵就照第 10 條） |
| ⌘⌫ | 吃掉、不動作（`handled` 1、狀態不變、不碰學習檔）；`forget_highlighted` 對英文窗直接回 `Ok(())` |
| 其他鍵（含注音鍵、聲調鍵、Tab、Delete、Home、End） | 第 8 條：關窗、清影子，再從第 9 條起處理這個鍵；注音鍵因此照第 9 條進欄位並重新開始記影子 |
| 標點（第 2 條，含 Shift 表、單按表、Ctrl+\） | 第 2 條在第 3–8 條之前：丟掉未完成音節（影子跟著清）、關窗、插入標點 |

- 選了英文項之後的輸出：`handled` 1、`commit` 非空、`preedit` 空、沒有候選；殼照現有的送出路徑插入文字。要空白的人再按一次空白（組字區空時空白鍵照第 22 條直通）。鍵數：英文接中文＝字母＋空白＋Enter；英文接英文＝字母＋空白＋Enter＋空白。

### 2.4 游標、固定詞、預測列、重排

- 游標不在尾端時不記錄不觸發（§2.1），空白鍵照第 10 條。
- 組字區已有字與固定詞時，英文項的送出包含它們（§2.3），固定詞的待學記錄照 `take_commit` 的規則在這次送出學；中文項的插入照第 10 條對固定詞的平移規則。
- 預測列：開英文窗時清掉；Esc 關窗後不重算（和關候選窗相同），下一個會重算的鍵再算。
- 重排（S7c-2a）：英文項的送出是送出，未套用的工作作廢；開窗、關窗、中文項的插入照「開候選窗／完成音節」的既有規則。

### 2.5 s3a 規則表要加的列（executor 同一個 PR 改 `docs/contracts/s3a.md`）

- §2 加一段「影子」（§2.2 的前四點）。
- §3 表加 **10e**（排在 9a 之後、10 之前）：「有未完成音節（一個單位）、§2.2 的觸發條件成立 | 空白鍵 | 開英文窗」。
- §3 表後加「英文窗」小節（§2.3 的表），註明第 3–8、3v–5p 條的其餘行為不變。
- §6 加兩個函式（§3.1）。

### 2.6 單字表

- 檔案 `english-words.txt`：UTF-8、LF、每行一個 `[a-z]{3,32}`、沒有重複、沒有 BOM、≤ 4 MiB、≤ 200,000 行；不符任一條就是載入失敗（§3.1 回 3），不接受部分載入。
- 內容由 `tools/build_english_words.py` 從 SymSpell `frequency_dictionary_en_82_765.txt` 產生（§3.4）：丟掉含撇號、數字或其他非 `[a-z]` 的字（規則 `^[a-z]{3,32}$`），依檔案順序（頻率遞減）取長度 3 的前 `LEN3_CAP` 個、長度 ≥ 4 的前 `LEN4_CAP` 個，輸出按位元組排序。起始值 `LEN3_CAP = 3000`、`LEN4_CAP = 30000`，最終值由 §4.4 決定後寫回腳本。
- 查表在空白鍵那一刻一次（`HashSet<Box<str>>` 或排序後二分搜尋，實作選），每鍵路徑不查表。

## 3. 介面與交付物

### 3.1 C ABI（`core/include/shanjie.h`，照 `shanjie_engine_set_abbreviation` 與 `shanjie_engine_load_tinylm` 的慣例）

```c
// zhen-mixed: the English word list. 0 ok; 1 engine or path NULL; 2 path not UTF-8; 3 unreadable, not a regular
// file, over 4 MiB or 200,000 lines, a line outside [a-z]{3,32}, a duplicate line, a BOM. Failure keeps the previous
// list (or none). Does not change the switch or the current display. Call it once after shanjie_engine_new.
int32_t shanjie_engine_load_english(ShanjieEngine *engine, const char *path);
// set_english 0 or 1 (2 otherwise, state unchanged), default 0 at engine creation, like set_abbreviation in its codes
// and its snapshot (handled 1, commit ""); 1 when engine or out is NULL. With 1, and only with a loaded list, the
// standard layout and the abbreviation composer off, Space after an English word typed into one unfinished syllable
// opens the English window (s3a rule 10e). Turning it off while that window is open closes it. No effect without a
// list. Call it after every engine creation, like set_abbreviation.
int32_t shanjie_engine_set_english(ShanjieEngine *engine, uint32_t enabled, ShanjieOutput **out);
```

- `Engine::new` 不讀這個檔、不會因它失敗；沒載入時 `set_english(1)` 只記住開關。
- Rust 公開（給測試與工具，不進 C ABI）：`Engine::load_english(&Path) -> Result<(), EngineError>`、`Engine::set_english(bool) -> Result<Output, EngineError>`、`#[doc(hidden)] pub fn english_open(&self) -> bool`（英文窗是否開著）、`#[doc(hidden)] pub fn english_shadow_len(&self) -> usize`（影子長度；`None` 時 0）。
- R2：影子、單字表、英文候選都是使用者輸入或資料，不進任何訊息與紀錄；錯誤只有碼。

### 3.2 殼（`macos/`）

- `Core.swift`：`loadEnglish(path:) -> Int32`、`setEnglish(_:) -> CoreResult`。
- `Shell`：`EnglishStore` 協定（`var english: Bool?`；`nil` 是關），`MemoryEnglishStore`（Kit 與測試）、`DefaultsEnglishStore`（只在 `Shanjie` target，UserDefaults 鍵 `english`），`Shell.init` 的必要參數（照 `abbreviationStore`）；`englishOn`（預設關）、`englishAvailable`（這個引擎的單字表載入成功，照 `rerankAvailable`）。
- `build()`：在 `load_lm`／`load_tinylm` 之後呼叫 `loadEnglish(path: Resources/english-words.txt)`，非 0 只記錄碼（`Log.shell.error`）、`englishAvailable = false`；然後**每次建引擎都送** `setEnglish(englishOn)`（放在 `setAbbreviation` 旁邊；換排列、開關詞包重建後不掉回關）。
- 選單：`MenuEntry.Text.english = "中英混打（實驗）"`、action `.toggleEnglish`、獨立 selector `toggleEnglish`；位置緊接在「即時預測」之後（可省略韻母隱藏時的那個位置），語意校正之前；預設不打勾；`englishAvailable` 是 false 時反灰（勾選狀態仍顯示）。`applyEnglish(on)` 照 `applyAbbreviation`：有組字擁有者就顯示快照。
- 設定視窗「選字」區塊同一個位置、同樣反灰、和選單同步。
- 不加 `showsEnglish` 參數：出貨 App 顯示這個開關、預設關（維護者 2026-10-14 選擇題）。
- 文件同 PR 改：`docs/contracts/s3b.md` §13.2 的選單順序、`docs/contracts/settings-window.md` §2.2 的「選字」列。

### 3.3 評測 CLI（`cli`）

- 子命令 `english-sim`（§4.1），用 `Engine::new` ＋ `load_english` ＋ `set_english(1)` 驅動真的引擎，觸發以 `english_open()` 為準，不另寫觸發邏輯。

### 3.4 建置、散布、授權

- **散布（維護者 2026-10-14 選擇題：建置時產生）**：單字表不進 git、不建 Release。`scripts/build-app.sh` 建置時從 SymSpell（`wolfgarbe/SymSpell`）釘住的 commit 下載 `SymSpell/frequency_dictionary_en_82_765.txt`（commit 與輸入 SHA-256 寫在 `tools/build_english_words.py` 的常數），核對輸入雜湊，用腳本產生 `english-words.txt`，再核對輸出雜湊（`data/english-words.txt.sha256` 進 git），複製到 `Resources/`；下載失敗或任一雜湊不符就建置失敗。下載的輸入快取在 `~/.cache/shanjie/english/`（有快取且雜湊相符就不再下載）。CI 與 `release.yml` 走同一條（建置本來就要連網下載模型檔）。`docs/data-files.md` 的表加一列（來源、雜湊、怎麼產生）。
- **建置程式** `tools/build_english_words.py`：先核對輸入雜湊、不符就失敗；過濾與切法照 §2.6；選項 `--len3 N --len4 N --out FILE`（§4 的格點用）；印出行數與輸出的 SHA-256。`tools/test_build_english_words.py` 用合成輸入測過濾、切法、排序與輸入雜湊檢查（不連網）。
- **授權**（main 在釘住的 commit 核對 README 的授權段落，摘錄進 `LICENSES/data.md`）：
  - `LICENSES/data.md` 加一列：`english-words.txt`（不在 git；建置時產生）——由 SymSpell 的詞頻表切出：Google Books Ngram（**CC BY 3.0 Unported**，署名 Google Books Ngram Viewer 資料，https://creativecommons.org/licenses/by/3.0/ ）與 SCOWL（寬鬆授權，要保留版權聲明）的交集；SymSpell repo 本身 MIT。
  - `LICENSES/SCOWL.txt`：SCOWL 的 `Copyright` 檔全文（SymSpell 沒寫用的是哪個版本，收目前發行版的檔並註明版本是我們選的）；`LICENSES/SymSpell-MIT.txt`：SymSpell 的 MIT 全文。
  - `scripts/build-app.sh`：複製上面兩個檔到 `Resources/LICENSES/`，另寫 `Resources/LICENSES/english-words-attribution.txt`（三段署名；不併進 CC BY-SA 4.0 那個檔，授權不同）。
- **順序**（一個 PR）：
  1. main：下載輸入到 `~/.cache/shanjie/english/`、記 commit 與雜湊；取 SCOWL `Copyright` 與 SymSpell MIT；核對 README 授權段落。
  2. **PR**（executor）：核心、FFI、CLI `english-sim`、建置程式與測試、`build-app.sh` 的下載與核對、`ci.yml`／`release.yml` 的快取、殼、合成小詞表 `eval/golden/english-tiny.txt`、文件（s3a、shanjie.h、s3b、settings-window、LICENSES、data-files）。executor 不連網：`build-app.sh` 的下載只寫程式、用快取裡的檔測；輸出雜湊先留佔位，由 main 在第 3 步填。
  3. main：在 PR 分支上跑 §4（切法、召回），把最終 `LEN3_CAP`／`LEN4_CAP` 寫回腳本，產生最終檔、寫 `data/english-words.txt.sha256` 與研究紀錄；/code-review＋fresh verifier＋CI 後合併。
  4. 使用者實機（§5.7）；fresh verifier 保留集（§5.8）。

### 3.5 資安面（為什麼不另做 security-reviewer）

- 新增的攻擊面只有：一個布林設定（UserDefaults 鍵 `english`）、一個唯讀資料檔（簽過章的 App bundle 裡，和詞庫同等信任；載入器嚴格、有大小與行數上限、不符就整個拒絕）、未完成音節上最多 32 個鍵的影子（R2：不進紀錄，隨 `clear_pend` 清掉）。沒有網路、沒有新的檔案寫入、沒有新的權限、沒有新的程序間介面。
- 安全輸入開著時系統不把鍵送給第三方輸入法（`CLAUDE.md`「已知的系統問題」），影子收不到密碼欄的鍵。
- 這些都在本地 /code-review 與 fresh verifier 的範圍內；沒有屬於 security-reviewer 的題目（驗證、祕密、加密、硬化）。需要它的情況：之後若單字表改成使用者可自行指定路徑或下載。

## 4. 量測（§4.1–§4.5；main 做，數字進研究紀錄）

### 4.1 工具：`cargo run --release -q -p cli -- english-sim …`

- 選項：`--words FILE`（單字表）、`--rows FILE`（`前文|句子|讀音` 三欄，照 `--rows` 的解析 `core::eval::parse_rows`）、`--p 0.02`（每個注音鍵的錯打機率；`0` 是無錯打）、`--repair overwrite|backspace`（§4.2）、`--seed 20261014`、`--repeat 5`（種子 seed…seed+repeat−1）、`--recall`（§4.5，改量單字表）。
- 每列：讀音依 `core/tests/engine_replay.rs` 的 `keys_of` 轉成標準排列按鍵（符號鍵依聲母、介音、韻母，再聲調鍵或空白鍵），依 §4.2 注入錯打與修正，逐鍵送進引擎；每次空白鍵之後查 `english_open()`，是就計一次觸發，接著送 Esc（關窗、清影子）再送一次空白鍵：觸發只會發生在模擬器要打一聲的那個空白鍵，此時欄位等於預期的合法一聲音節，第二個空白鍵照第 10 條把它完成，引擎和模擬器的狀態一致；列尾送 Enter。每列新的引擎或 `reset(1)`（擇一，固定）。
- 輸出一行 TSV、沒有任何內容文字：`rows_sha256 rows syllables keys triggers per_1000_mean per_1000_max`。`per_1000` 是**每千個音節**的誤觸：每個種子算 `triggers × 1000 / syllables`，mean／max 是跨種子。`cli/tests/` 的固定種子小測試寫死的數字照這個式子算。不載入語言模型（觸發不依賴解碼；§2.2），所以也不帶 `--lm`。
- 程式放 `cli/src/`，和 `run_lm`、`run_predict` 並列；`cli/tests/` 用合成小詞表加一個固定種子的小測試（結果數字寫死，改了錯打模型要同時改測試）。

### 4.2 錯打模型（固定，寫進工具的註解）

- **只對注音鍵錯打**，聲調鍵與空白鍵不錯打（錯按聲調鍵會提早結束音節，是另一類錯誤，不在本片的量測裡）。
- **鄰鍵**：同一列（`1234567890-=`、`qwertyuiop[]`、`asdfghjkl;'`、`zxcvbnm,./`）左右各一個，只留**標準排列的注音鍵**（丟掉聲調鍵 3、4、6、7 與非注音鍵 `=`、`[`、`'` 等）；剩 0 個就這個位置不錯打；剩 1 或 2 個均勻抽。鄰鍵可能是別的欄（例如 `u`ㄧ 介音旁邊是 `i`ㄛ 韻母、`y`ㄗ 聲母），所以修正要看欄位。
- **修正**（模擬器自己依第 9、11 條推算欄位狀態 A 與預期狀態 S，引擎只負責計觸發）：
  - `overwrite`：錯鍵和對鍵同一欄 → 按對鍵一次（一次覆寫）；不同欄 → 用 `backspace` 的方式。
  - `backspace`：按 Backspace 直到 A（顯示順序）是 S 的前綴，再依欄位順序補打少掉的符號。
- 修正完成後 A 等於 S，接著打音節剩下的鍵。

### 4.3 資料集（檔案與雜湊；工具印出它讀到的雜湊，寫進研究紀錄）

- **選門檻**：套件 v2 的 cvtune 與 wikitune（`tools/bench.py` `SETS2`；`~/.cache/shanjie/s7c2c-public/tune/cvtune.txt` `31de456d66ece98eb5d00154a31c3f7e9c9b38240690f7f76203485ca82acc51`、`wikitune.txt` `8dcfe40c74cce73e0a070b49e166636580ef955da8555ea9ffbd65af20c998f2`）。選 v2 不選 v1：v2 是現行跨版本基準測試用的套件（`CLAUDE.md` 發版一節），v1 凍結給歷史比較。
- **驗證**：dev302（`eval/bench/suite-v1/dev302.txt` `603850e444efee38b8c121e3d666d6c540acd3a27d5a6a5070a78a962dd98456`）、typing76（`eval/bench/suite-v1/typing76.txt` `c061f66dfade21996803137320aa986a81a3e72e3e4e1dd559abd5dc04b68d08`）。
- 不在同一個集合上選又評；保留集不碰（§5.8 例外）。
- 計算量：每個格點兩個集合各 5 個種子的引擎重播，Mac 上預估分鐘級；單一格點超過 10 分鐘就改在 Mac 以外的 Windows 機器跑（`CLAUDE.md`「Mac 不跑重運算」）。

### 4.4 切法的搜尋與選法

- 格點：`LEN3_CAP ∈ {0, 1000, 3000, 5000}` × `LEN4_CAP ∈ {10000, 20000, 30000, 50000, 全部}`，每格用 `build_english_words.py --len3 --len4` 產生詞表。
- 每格量：cvtune、wikitune 各跑 `--p 0.02 --repair overwrite --repeat 5`（最壞情況：修正全用覆寫），記 per_1000_mean；另跑 `--p 0` 一次（必須 0）。
- **可行格**：兩個集合的 per_1000_mean 都 ≤ 0.5，而且 `--p 0` 是 0。
- 在可行格裡取**詞數最多**的切法（維護者訊息的覆蓋率不量：第 0 步照約定只回數字、沒有存 token 表，為了選切法再讀一次私人資料不值得；之後真的需要時另外問）。
- 選定後在 dev302、typing76 各跑一次 `--p 0.02`（兩種 `--repair` 都報，`overwrite` 是門檻）與 `--p 0`：per_1000_mean ≤ 0.5、無錯打 0；任一不過就是停止條件。
- 全部格點的表（兩個集合 × 兩個數字）進研究紀錄；選定的常數寫回腳本與 Release 說明。

### 4.5 召回（`english-sim --recall --words FILE`）

- 分母：**選定單字表的全部詞條**，不加權、不先剔除。
- 每個詞：新引擎（或 `reset(1)`），逐字母送 `Key::ch(字母, 0)`，再送空白鍵；分子是「英文窗開著而且 `candidates[0]` 等於這個詞」。
- 同時印四個計數：覆寫 0 次、覆寫 1 次且殘餘在詞庫、覆寫 ≥ 2 次且殘餘在詞庫、殘餘不在詞庫（後兩類會觸發，前兩類不會）；「殘餘在詞庫」的比例因此有精確值（取代之前對話裡「三到五成」的估計）。
- **召回 < 95% 就停**，回報是哪一類佔多數再決定（例如對覆寫 1 次的字放寬）。

## 5. 驗收

### 5.1 引擎測試（`core/tests/engine_english.rs`，真實詞庫 `data/lexicon`，單字表 `eval/golden/english-tiny.txt`）

合成小詞表（每行的用途寫在測試裡，不寫進檔案）：`rust`（殘餘 ㄔㄧ 不在詞庫、覆寫 2）、`google`（殘餘 ㄍㄠ 在詞庫、覆寫 4）、`bug`（殘餘 ㄕㄧ 在詞庫、覆寫 1）、`dip`（殘餘 ㄎㄣ 不在詞庫、覆寫 1）、`cup`（覆寫 0、殘餘 ㄏㄧㄣ 不在詞庫）、`hello`。executor 實作前先用 `lex.entries` 核對這些殘餘；不符就換字並寫進測試註解。按鍵都是標準排列、無修飾鍵；`␣` 是空白鍵。

| 名稱 | 按鍵 | 預期 |
|---|---|---|
| `opens_on_space_commits_without_space` | `r u s t ␣` 然後 `1` | 空白後：`english_open()`、候選 `rust`、`Rust`、`RUST`、`selected` 0、`total` 3、preedit 仍含 ㄔㄧ；`1` 後：`commit == "rust"`、preedit 空、`syls` 空、沒有候選 |
| `enter_and_shift_enter_pick` | `r u s t ␣ Enter`；另一引擎 `… ␣ Shift+Enter` | 兩者 `commit == "rust"`、`handled` 1 |
| `pick_equals_digit` | `r u s t ␣` 後一個引擎 `pick(0)`、另一個按 `1` | 兩個 `Output` 相等（`PartialEq`） |
| `left_composition_is_committed_too` | `s u 3`（你）`r u s t ␣ 1` | `commit == "你rust"`（左邊的字照當時顯示），preedit 空、`syls` 空、左文清掉 |
| `learning_follows_the_commit_rules` | 學習開、`learning_open` 暫存目錄；打兩個音節、開候選窗改選一個**兩個字的詞**（避開單字詞的前文規則；產生待學記錄）；`r u s t ␣ Enter` | 學習檔有那筆記錄（和用 Enter 送出相同） |
| `rerank_job_is_voided` | `engine_rerank.rs` 加一項：有工作時 `r u s t ␣ 1` 再 apply | `SHANJIE_NO_CHANGE`（作廢） |
| `valid_residual_needs_two_overwrites` | `b u g ␣` | 沒有英文窗；`syls == ["ㄕㄧ"]`（第 10 條） |
| `valid_residual_with_two_overwrites_opens` | `g o o g l e ␣` | 英文窗，`total` 4，第四項等於 `lex.entries(&["ㄍㄠ"])[0]` |
| `choose_chinese_item` | `g o o g l e ␣ 4` | 沒有候選；`syls == ["ㄍㄠ"]`；顯示的字等於窗裡第四項；有一個長度 1 的固定詞、`pre` 是 `None`（學習開著、送出後學習檔不變） |
| `invalid_residual_one_overwrite_opens` | `d i p ␣` | 英文窗，`total` 3 |
| `zero_overwrite_never_opens` | `c u p ␣` | 沒有英文窗；pend 不變（ㄏㄧㄣ 不在詞庫，第 10 條不收） |
| `not_in_list_never_opens` | `r u s t y ␣` | 沒有英文窗；pend 不變 |
| `esc_then_space_is_tone_one` | `g o o g l e ␣ Esc ␣` | Esc 後沒有候選、pend 是 ㄍㄠ、`english_shadow_len() == 0`；第二個空白後 `syls == ["ㄍㄠ"]` |
| `backspace_in_window_clears_shadow` | `g o o g l e ␣ Backspace ␣` | 同上 |
| `space_in_window_wraps` | `r u s t ␣ ␣` | 仍是英文窗、`selected` 0 |
| `digit_beyond_total_is_ignored` | `r u s t ␣ 5` | 仍是英文窗、狀態不變 |
| `letter_in_window_closes_and_restarts` | `r u s t ␣ w ␣` | `w` 後沒有候選、`english_shadow_len() == 1`、pend 是 ㄊㄧ；第二個空白後 `syls == ["ㄊㄧ"]` |
| `punctuation_in_window` | `r u s t ␣ Shift+,` | preedit 是「，」、沒有候選、pend 空、影子 0 |
| `cmd_backspace_is_eaten` | 學習開、`learning_open`、先讓檔案存在（教一筆並送出）；`r u s t ␣ ⌘⌫` | `handled` 1、仍是英文窗、狀態不變、學習檔位元組不變 |
| `backspace_pops_shadow` | `r u s t t Backspace ␣` | Backspace 後 pend 是 ㄔ（第 11 條刪 ㄧ）、影子長度 4；空白後英文窗、`total` 4（殘餘 ㄔ 在詞庫、覆寫 3） |
| `backspace_to_empty_clears_shadow` | `r u Backspace Backspace s t ␣` | 兩次 Backspace 後影子 0；空白後沒有英文窗、`syls == ["ㄔ"]` |
| `thirty_third_key_clears_shadow` | 33 個 `a` 然後 `␣`；再 `r u s t ␣` | 33 個後 `english_shadow_len() == 0`；第一個空白沒有英文窗（ㄇ 一聲不在詞庫，pend 不變）；`rust` 後英文窗（影子重新開始） |
| `cursor_not_at_end` | `s u 3 c l 3 ← r u s t ␣` | 沒有英文窗、pend 不變 |
| `off_means_no_shadow` | `set_english(0)`（預設）、單字表已載入、`r u s t ␣` | `english_shadow_len() == 0`、沒有英文窗、第 10 條不收 |
| `no_list_means_no_window` | 沒 `load_english`、`set_english(1)`、`r u s t ␣` | 同上 |
| `eten_is_bitwise_off` | 倚天引擎、載入單字表、`set_english(1)`：`r u s t ␣`，另一個 `set_english(0)` 同按鍵 | 每個輸出相等；`english_shadow_len()` 一律 0 |
| `turning_off_closes_window` | `r u s t ␣` 後 `set_english(0)` | 快照沒有候選、pend 仍是 ㄔㄧ、影子 0；再 `␣` 照第 10 條 |
| `abbreviation_on_closes_window` | `r u s t ␣` 後 `set_abbreviation(1)` | 同上；之後打字影子一律 0 |
| `vertical_window` | `set_candidate_vertical(1)`、`r u s t ␣ ↓` | `vertical == 1`、`selected` 1 |
| `replay_with_english_on` | `engine_replay.rs` 加第三個 run：標準排列、載入小詞表、`set_english(1)`，dev302 | 302／302 相同、全程 `english_open()` 從未為真；release 時 p95 < 16 ms |

- **突變**（每一項讓某個測試失敗；看結束碼與斷言數，build error 不算）：觸發不看覆寫次數（`valid_residual_needs_two_overwrites`）；殘餘在詞庫時只要 1 次（同上）；Esc 不清影子（`esc_then_space_is_tone_one`）；送出加空白（`opens_on_space_commits_without_space`）；倚天也記影子（`eten_is_bitwise_off`）；`forget_highlighted` 不分英文窗（`cmd_backspace_is_eaten`）；游標不在尾端也觸發（`cursor_not_at_end`）；第 33 個鍵不清（`thirty_third_key_clears_shadow`）；中文項不固定詞（`choose_chinese_item`）。

### 5.2 FFI 與 C 冒煙

- `ffi.rs` 子行程測試（照 `child_rerank` 的寫法，暫存目錄放小詞表）：`load_english` 的碼 1（NULL）、2（非 UTF-8 路徑）、3（不存在、資料夾、超過 4 MiB、一行不合 `[a-z]{3,32}`、重複行、BOM），失敗時保留之前的詞表；`set_english` 的碼 0／1／2 與 NULL；經 C ABI 打 `rust␣` 得 `candidate_count` 3、`candidates[0]` 是 `rust`，按 `1` 得 `commit` `rust`；子行程輸出不含暫存路徑與任何詞。
- `abi_smoke.c`：`load_english(NULL)` 回 1、不存在的檔回 3；`set_english` 0／1 回 0、2 回 2。

### 5.3 殼（`swift test`）

- 選單有「中英混打（實驗）」、位置在「即時預測」之後、語意校正之前、預設不打勾；切換後 store 是 true 並送給引擎；打開後換排列、開關詞包重建引擎，新引擎仍收到 1（照 `toggleAbbreviation` 的測試）；`loadEnglish` 回非 0 時 `englishAvailable` 是 false、項目反灰（照 `RerankTests` 對 `rerankAvailable` 的寫法）；設定視窗同一個開關位置、反灰、同步；`build()` 每次都送 `setEnglish`。
- 突變：`build()` 不送 `setEnglish`（重建的測試失敗）；項目放錯位置（位置測試失敗）。

### 5.4 不變與速度

- 功能關時（預設）：所有既有 golden、engine 測試、`engine_replay.rs` 的兩個 run、`cli/tests/golden.rs` 一個位元組不改（`git diff origin/main -- eval/golden/` 只有新增的 `english-tiny.txt`）。
- `replay_with_english_on` 的 p95 < 16 ms（release）；既有的兩個計時照舊。

### 5.5 量測門檻（§4；main 跑，fresh verifier 重跑一次抽查）

- 選定切法在 cvtune、wikitune、dev302、typing76：`--p 0` 全部 0；`--p 0.02 --repair overwrite` 的 per_1000_mean 都 ≤ 0.5。
- 召回 ≥ 95%（§4.5）；四個分類的計數與「殘餘在詞庫」的比例寫進研究紀錄與 Release 說明。

### 5.6 建置程式與文件

- `tools/test_build_english_words.py` 通過；最終檔用腳本從釘住的輸入重產，SHA-256 等於 `data/english-words.txt.sha256`（CI 的建置也核對）。
- `python3 -I -m py_compile` 與既有的 `make test` 項目全綠；CI 全綠。
- 文件：`docs/contracts/s3a.md`（§2.5）、`core/include/shanjie.h`、`docs/contracts/s3b.md` §13.2、`docs/contracts/settings-window.md` §2.2、`LICENSES/data.md`、`LICENSES/SCOWL.txt`、`LICENSES/SymSpell-MIT.txt`、`scripts/build-app.sh`、`docs/data-files.md`、`docs/PLAN.md` 的功能清單一行、研究紀錄（量測表、數字、雜湊）、`docs/methodology.md`（若有方法上的教訓）。

### 5.7 實機（維護者，≤ 15 秒）

- 安裝合併後的 App；選單打開「中英混打（實驗）」；`imeshot` 打 `rust` 空白、`1`、接著打中文「編譯」送出：截圖看英文窗三項與送出的 `rust編譯`（中間沒有空白）。
- 關掉開關後同樣的按鍵：空白鍵不開窗（ㄔㄧ 不收）。

### 5.8 保留集

- fresh verifier 收尾跑一次（基準與新版各一次 `--set holdout --rowstats`，只回那一行表格）；功能預設關，選字結果應不變。

## 6. 停止條件

- 第 0 步兩個數字都 < 5%（已過）。
- `--p 0` 在任一集合觸發 > 0。
- 格點裡沒有可行格（§4.4），或選定切法在 dev302／typing76 超過 0.5。
- 召回 < 95%。
- 功能關時任何 golden、解碼或重播結果改變；`replay_with_english_on` 的 p95 ≥ 16 ms。
- 要改 s3a 第 2、7、11、12、22 條對既有候選窗或未完成音節的行為才做得到（英文窗是新狀態、另列規則，不算）。
- 上游輸入的雜湊、授權段落和腳本記的不符；建置產生的輸出雜湊和 `data/english-words.txt.sha256` 不符。

## 7. 範圍外

和縮寫模式同開；倚天排列；英文後接標點或 Enter（沒經過空白）；含數字的 token；網址；句首與品牌大小寫（`iPhone`）；英文的學習；Shift 逃生鍵；行內提示；選單內空白鍵＝選第一個＋空白的捷徑；中文項用解碼結果而不是詞庫第一項；單字表讓使用者自訂路徑。

## 8. 負責人、順序、預算、限制

- **main**：第 0 步（已做）、這份契約 → plan-verifier → §3.4 第 1 步的輸入與授權文字 → executor → §4 量測與最終檔（量測在 Mac 以外的 Windows 機器跑，照「Mac 不跑重運算」）→ /code-review＋fresh verifier＋CI → 合併 → 使用者實機 → 保留集。
- **executor**：核心、FFI、CLI、建置程式、殼、測試、文件；預算 1 回合＋1 次修正。
- **executor 不可以**：連網（輸入檔由 main 放到 `~/.cache/shanjie/english/`）；讀 `eval/holdout/`、維護者的私人資料（Discord 資料、學習檔、聊天紀錄）；安裝、啟動 App，呼叫 TIS 或 lsregister，碰 `~/Library` 與鑰匙圈（殼只改原始碼並跑 `swift test`）；push、開 PR、留言、建 Release、發版；碰其他 worktree；改 `eval/bench/` 的檔或雜湊。
- **main 不可以**：把第 0 步的任何 token、詞或訊息寫進 repo、紀錄、commit、PR。

## 9. 審查紀錄

| 審查 | 問題 | 處置 |
|---|---|---|
| plan-verifier 第 1 次 REVISE（2026-10-14） | 1. 影子在 Backspace（第 11、7 條）、英文窗內打字母（第 8 條）、第 33 個鍵下沒有規則；2. 排列範圍沒定；3. 英文窗的實作、按鍵表、中文項的定義與選後處理沒定；4. 游標不在尾端、固定詞、預測列、學習、重排沒定；5. C ABI、單字表的檔名與載入時機、缺檔行為、互斥的強制、殼的鍵與位置、出貨是否顯示沒定；6. 授權與散布（repo 沒有 NOTICE）、建置核對、過濾規則、資安面沒定；7. §3.3 的工具、錯打模型、資料集雜湊、覆蓋公式、格點、及格線沒定；8. 召回分母沒定；9. §2 每條規則沒有具名測試，CI 的合成詞表沒定 | 全部 FIX：1 → §2.2（Backspace 丟最後一鍵、`clear_pend` 一起清、第 33 個清空）與 §2.3 的表（窗內 Backspace／Esc 清影子、其他鍵第 8 條後重新記），測試 `backspace_pops_shadow`、`backspace_to_empty_clears_shadow`、`backspace_in_window_clears_shadow`、`letter_in_window_closes_and_restarts`、`thirty_third_key_clears_shadow`；2 → §2.1 只限標準排列，測試 `eten_is_bitwise_off`；3 → §2.3（`Cands.english`、按鍵表、中文項＝詞庫第一個詞條、選後固定不學），測試 `cmd_backspace_is_eaten`、`pick_equals_digit`、`choose_chinese_item`；4 → §2.1、§2.4，測試 `cursor_not_at_end`、`left_composition_is_committed_too`、`learning_follows_the_commit_rules`、`rerank_job_is_voided`；5 → §3.1–§3.2（`load_english`／`set_english`、`Engine::new` 不碰、互斥 engine 強制、鍵 `english`、位置、出貨顯示）；6 → §3.4–§3.5（`LICENSES/data.md` 一列＋兩個全文檔＋署名檔、Release 附件＋雜湊、腳本核對輸入、過濾規則、資安面說明）；7 → §4.1–§4.4；8 → §4.5；9 → §5.1 的表與 `eval/golden/english-tiny.txt` |
| 維護者選擇題（2026-10-14） | 第四項、散布、出貨顯示 | 第四項＝詞庫第一個詞條（§2.3）；散布＝建置時從釘住的上游產生、一個 PR、不建 Release（§3.4）；出貨顯示開關、預設關（§3.2） |
| plan-verifier 第 2 次 REVISE（2026-10-14） | 1. 選切法用的覆蓋需要一份沒存的 token 表；2. 誤觸的分母沒定、觸發後只送 Esc 會讓引擎和模擬器狀態分歧；（附）dev302／typing76 路徑在 suite-v1 | 1 → 不量覆蓋，可行格取詞數最多（§4.4），刪 §4.6 與相關字句；2 → §4.1 定義每千音節、觸發後 Esc＋空白；路徑改 suite-v1 |
