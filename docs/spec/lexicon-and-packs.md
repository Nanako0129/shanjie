# 詞庫與詞包

這份寫的是現在的行為。

範圍：詞庫有哪些層、每一層的檔案格式與載入規則，以及產生這些檔案的建置工具；選用詞包（動漫與遊戲、資訊與 AI、經典台詞）的檔案、位元、App 開關與建置。解碼器怎麼用這些分數、封頂與詞包詞當未知詞的算法、異體排序表、一／不變調與輕聲本調列的規則見 `docs/spec/decoder.md`；詞包開關在設定視窗的位置見 `docs/spec/macos-shell.md`；C ABI 的總則見 `docs/spec/c-abi.md`；模型檔本身見 `docs/spec/models.md`；評測集合見 `docs/spec/evaluation.md`。實作在 `core/src/lib.rs`（詞庫）、`core/src/engine.rs`（載入與詞包位元）、`core/src/ffi.rs`、`cli/src/main.rs`，建置工具在 `tools/`（`build_overlay.py`、`build_names.py`、`build_acg_pack.py`、`build_it_pack.py`、`it_sources.py`、`it_wiki_counts.py`、`build_quote_pack.py`、`readings.py`），App 端的開關在 `macos/Sources/ShanjieKit/Shell.swift`。

## 1. 詞庫的層與載入

### 1.1 各層與載入順序

| 層 | 檔案（`data/lexicon/` 或 `data/packs/`） | 第 4 欄標籤 | 建置工具 | 缺檔 |
|---|---|---|---|---|
| 基底 | `mcbpmf-data.txt`（小麥注音詞庫，MIT） | 無（三欄） | 無，原封不動 | 載入失敗 |
| 疊加層 | `overlay-add.tsv` | `wikt`、`zhwiki`、`萌典補詞`、`manual`、`slang` | `tools/build_overlay.py` | 載入失敗 |
| 變調層 | `sandhi-add.tsv` | `sandhi`、`neutral` | `tools/build_sandhi.py` | 載入失敗 |
| 名單層 | `names-add.tsv` | `names`、`creator`、`store` | `tools/build_names.py` | 載入失敗（評測 CLI 的 `--no-names` 例外） |
| 動漫與遊戲詞包 | `acg-add.tsv` | `acg`、`acg-oov` | `tools/build_acg_pack.py` | 當作沒有這個詞包 |
| 經典台詞詞包 | `quotes-add.tsv` | `quotes` | `tools/build_quote_pack.py` | 當作沒有這個詞包 |
| 資訊與 AI 詞包 | `it-add.tsv` | `it` | `tools/build_it_pack.py` | 當作沒有這個詞包 |
| 迷因與實況用語詞包 | `meme-add.tsv` | `meme` | `tools/build_meme_pack.py` | 當作沒有這個詞包 |
| 美妝與保養詞包 | `beauty-add.tsv` | `beauty` | `tools/build_meme_pack.py --pack beauty` | 當作沒有這個詞包 |

- `variant-order.tsv`（異體排序表）也在載入時讀，缺檔或有一行不成立都是載入失敗；它的格式與規則在 `docs/spec/decoder.md`，這份不重複。
- `rerank-protect.tsv`（重排保護詞，§2.12）不是詞庫的一層，不加詞、不改分數：上表各層合併之後才讀，缺檔或有一列不成立都是載入失敗。
- `names-english.txt`（名單層的英文寫法，§3.1）屬於名單層，但不是解碼的一層：一行一個英文寫法、沒有讀音與標籤欄，編進核心而不是載入的檔，所以沒有缺檔的情況，評測 CLI 的 `--no-names` 也不影響它；只在英文段結束時換大小寫（`docs/spec/english-mixed.md` §4.4）。
- `mixed-words.tsv`（中英混合詞，`docs/spec/english-mixed.md` §5.7）也不是詞庫的一層：不進解碼，只在候選清單多一項；選用，缺檔或有一列不合規就整張不載入、不回錯誤。
- 載入順序（`engine::load_lexicon_split`）：基底；接著疊加層文字 = `overlay-add.tsv`、`sandhi-add.tsv`、`names-add.tsv`、詞包列（動漫詞包先讀，其餘詞包依 `PACK_FILES` 的順序：動漫、經典台詞、資訊、迷因、美妝）、評測專用的 `--extra-overlay` 檔；每一段之間補一個換行。同一個讀音下的所有列最後一起穩定排序（見 §1.3）。
- 一個要求的詞包，檔案不存在時什麼都不貢獻（引擎和沒有它逐位元組相同）；檔案存在卻讀不了或解析失敗是載入失敗。C ABI 與評測 CLI 的差別在 §4.1。
- 封頂用的文字（`engine::capping_overlay`）= `overlay-add.tsv`、`names-add.tsv`、詞包列（沒有 `sandhi-add.tsv`）。封頂規則見 §1.4。
- 疊加層、名單層、詞包彼此的詞串不可重複：同一個（讀音, 詞）在兩個檔都有，核心載入時回 `OverlayDuplicate`（引擎建立失敗）；封頂又是依字串封頂所有讀音，所以各建置工具另外要求「新層的詞串不在任何既有層的任何讀音下」。`data/packs/meme-add.tsv`（迷因詞包，位元 8）存在時，各工具把它的詞也當作已佔用；它的內容與建置見 §9。

### 1.2 檔案格式與解析規則

- 基底：每行 `讀音 詞 分數`，空白分隔、剛好三欄，讀音以 `-` 接音節；以 `#` 或 `_` 開頭的行、欄數不對的行、詞的字數不等於音節數的行略過；分數不是數字回 `BadScore`。
- 疊加層類（疊加層、變調層、名單層、詞包）：每行 `讀音<TAB>詞<TAB>分數<TAB>標籤`，剛好四欄，沒有標頭、沒有註解行。四欄不足、超過或分數不是數字的列是壞列：解析到它就停；它只在沒有更早的重複列時才被回報（`BadOverlayRow`，只帶那一行的字數）。
- 疊加層列接在基底後面：同一個（讀音, 詞）若已出現在更早的列（基底或疊加層），回 `OverlayDuplicate`（只帶詞的字數；同時有好幾個時回檔案順序最前的那一個）；全部沒有列是 `EmptyLexicon`。
- 錯誤只帶種類與長度，不帶輸入文字（隱私規則見 `docs/spec/privacy.md`）。
- 標籤是 `manual` 的列另外收成 `ManualSet`（詞的字與讀音音節序列；字數不等於音節數的列不收），由引擎與評測 CLI 共用，供語意校正保護手動詞（`docs/spec/semantic-rerank.md`）。

### 1.3 內部結構與排序

- 每個讀音下的詞依分數由高到低排；同分時基底在前、疊加層依檔案順序。實作是「先照檔案順序收齊所有列，再對每個讀音各做一次穩定排序」，沒有另外建舊式的對照表。
- 每個詞字串另有一個「勝出的列」（`by_word`）：同一個詞出現在好幾列時取分數最高的；同分取檔案裡最前面的（嚴格較大才取代）。
- 結構（`core/src/lib.rs`，`Lexicon`）：

| 欄位 | 內容 |
|---|---|
| `syl_ids`／`syl_names` | 音節轉成編號（約 1.5 千個不同音節） |
| `key_pool` | 所有不同讀音的音節編號，一個接一個 |
| `readings` | 不同的讀音，依音節編號序列排序；每個 12 位元組（`key_off`、`key_len`、`start`），該讀音的詞是 `ents[start .. 下一個 start]` |
| `ents` | 每個詞一筆（`off`、`len`、`score`），16 位元組，依讀音分組、組內分數由高到低 |
| `words` | 一整塊 UTF-8，`(off, len)` 切出詞 |
| `by_word` | 每個不同詞的勝出列的位置，依詞的位元組排序，每個 4 位元組 |

- 查詢全用二分搜尋：讀音（`&[String]` 每次解碼先轉成編號，不認得的音節是 `u32::MAX`）、詞（依位元組）；由詞反查讀音用 `readings[].start` 的 `partition_point`。
- 分數維持 `f64`；只用標準函式庫，沒有 `unsafe`、沒有 mmap；資料在執行時從文字檔解析。
- 公開的只有 `max_len`、`reading_count()`、`entries(&[String])`、`word_info(&str)` 等讀取函式，欄位都是私有的。
- 記憶體：這個結構落地時量過，載入 50 萬列疊加後的 `shanjie-eval --set dev --limit 302` 峰值常駐記憶體約 87 MB（只載基底約 37 MB）；詞庫之後變大（疊加層約 36 萬列、名單層約 2 千列、詞包再加），這些數字只當量級。
- 驗證：`core/src/tests.rs` 的 `learner_word_score_takes_last_duplicate`、`overlay_duplicate_within_overlay_is_error`、`overlay_bad_rows_report_length_only`（結構改寫不能改變這三個行為）；`cli/tests/golden.rs` 的 `matches_s1_references_byte_for_byte` 逐位元組比 `eval/golden/s1-dev302.txt`、`s1-dev302-nooverlay.txt`、`s1-overlay-sets.txt`，`unigram.txt` 在 `--no-overlay` 下比對。

### 1.4 封頂（只寫與詞包有關的部分）

解碼用的詞庫是 `CappedLexicon`（`core/src/lm.rs`），與載入的詞庫共用字串，只改分數並重排：

- 字串出現在封頂文字第 2 欄的詞（疊加層、名單層、詞包的詞，不論那個詞是新增的還是和基底同詞），在它所有的讀音下分數取 `min(分數, log10(次數 / N))`；通語沒看過的（次數 0）扣 `UNSEEN_OVERLAY_PENALTY`（1.0）。
- 唯一的例外是動漫詞包（`PACK_OOV`）：標籤 `acg-oov` 的列（強制詞）不封頂、不扣罰分，解碼時當成通語不認得的詞；標籤 `acg`、通語沒看過的詞保留詞包給的分數（不扣 1.0）；其餘 `acg` 的詞照一般疊加層封頂。經典台詞與資訊詞包沒有例外：看過的詞封頂在語料頻率，沒看過的扣 1.0。引擎只把動漫詞包的那一段文字交給 `with_packs`（`pack_oov_len`），所以其他詞包不會被當成例外。
- 完整的算法（`model_id`、強制詞的前後文、分級分數）見 `docs/spec/decoder.md`。
- 異體排序表在封頂之後再套一次，同樣見 `docs/spec/decoder.md`。

## 2. 疊加層 `overlay-add.tsv`

`python3 tools/build_overlay.py` 產生；輸出以 CC BY-SA 4.0 散布。

### 2.1 列的順序與格式

每列 `讀音<TAB>詞<TAB>分數<TAB>標籤`，分數是 Python `repr(float)` 的字面（例如 `-7.17149945`）。檔案由上而下：

1. 維基來源的列（標籤 `wikt`、`zhwiki`），依詞的 code point 排序；
2. `萌典補詞` 的列（§2.7）；
3. `lexgap` 的列，照 `--lexgap FILE` 詞表的順序；
4. `manual` 的列（§2.9），照 `overlay-manual.tsv` 的順序；
5. `slang` 的列（§2.10），照 `slang-add.tsv` 的順序。

第 2 段只有帶 `--moedict-supplement` 才有，第 3 段只有帶 `--lexgap` 才有；兩個都不帶時輸出只有 1、4、5 三段，和沒有這兩個旗標的建置逐位元組相同。

`--lexgap FILE`（預設關）：詞表是疊加層格式、第四欄 `lexgap`，`#` 開頭與空白行略過，可以有 BOM。每個詞的讀音照給的寫，分數一律是 `length_scores[字數]`（不加 `MANUAL_BONUS`，檔裡的分數不用）；和這個詞前一個主要讀音用 `sandhi_variant` 算出的變調讀音相同的列，放在哪裡都略過（重複幾次也一樣），變調列由建置照規則寫出；其他列都當主要讀音。下列任何一項就中止並指出行號：欄數或標籤不對、不是漢字、字數沒有分數、讀音的音節數不等於字數或有不認得的音節；詞已經在基底、前面各段、名單層或任一詞包（不論讀音）；同一個讀音上和基底、前面各段或詞表前面的詞只差異體寫法；兩個主要讀音列的（讀音, 詞）相同，或主要讀音列和建置寫出的變調列相同。這份詞表的詞要進通語的計數詞表，也要在常開詞庫裡才打得出來。

### 2.2 候選來源與過濾

來源是鎖定版本的維基標題 dump（`tools/build_overlay.py` 的 `SOURCES` 記網址與 SHA-256，下載到快取資料夾 `$SHANJIE_CACHE/sources/`，SHA-256 不符就中止，dump 不進 repo）：中文維基、英文維基詞典、中文維基詞典的 `all-titles-in-ns0`。過濾依序：

1. 標題是純漢字、2 到 4 個字（U+4E00–U+9FFF）；
2. 不在基底的詞表裡（以詞判斷，不管讀音；基底詞表是解析後 `by_word` 的鍵）；
3. 每個字都是基底的單字詞；
4. 不含簡體專用字（`build_counts.TRAD_SIMP_ONLY`：在簡繁對照表裡只當簡體、台灣不用的字；床、灶、粽 這類台灣字形不算）；
5. 維基詞典（英、中）的標題全收；中文維基的標題只收「複合詞」：去掉第一個字或最後一個字後，剩下的是基底裡的多字詞；
6. 含粵語用字的標題不收（§2.6）。

標籤：出現在任一維基詞典的標 `wikt`，其餘標 `zhwiki`。

- 取捨：複合詞規則是一般的構詞規則，不是逐詞挑選；維基標題大多是人名地名，全收會污染候選（量過，全收比只收複合詞差）。
- 不從評測句子挑詞加進疊加層。

### 2.3 簡體寫法與異體寫法

候選詞若只是別的寫法（簡體寫法轉成繁體後的詞、台灣字形摺疊後與基底或已留下的疊加層詞同讀音同字串的異體），就不寫進檔案；被拿掉的詞與它的落點寫到 `build/overlay/overlay-removed.tsv`、`overlay-variant-removed.tsv`（不進版控）。規則的內容（簡繁判斷、`MERGE` 摺疊表、落點分兩階段判定）見 `docs/spec/decoder.md`。粵語詞在候選階段就拿掉，所以這兩條規則看不到它們。

### 2.4 讀音

- 預設：`ime.Lexicon.to_syllables`（把詞切成基底詞，每段取分數最高的讀音），轉不出讀音的詞略過；不做人工確認。
- 後處理 `normalize`：「一」「不」「法」「和」改回審訂表的讀音；規則與「一」「不」的變調列、輕聲詞的本調列見 `docs/spec/decoder.md`。
- 變調列（`overlay_rows`）：含「一」「不」且有位置能換成變調的詞，多輸出一列，緊接在主要列後，分數 = 主要列 − `VARIANT_PENALTY`（0.5）。

### 2.5 分數

同字數基底詞條分數的第 25 百分位（`sorted[len // 4]`，所有讀音長度為 L 的詞條）：2 字 −7.17149945、3 字 −7.04116568、4 字 −6.60980192（建置時重算核對，不符就中止）。5 字以上（手動詞、詞包用）同一個規則，某個字數的基底詞條少於 1,000 條時沿用上一個字數的值（`length_scores`，2 到 10 字）。

### 2.6 粵語用字

`CANTO_CHARS`：冇 嘅 啲 喺 佢 嘢 唔 咁 嚟 搵 睇 畀 乜 啱 攰 嗰 哋 嚿 諗 噉 嗮 噏 揸 攞 餸 嚫 撳 搣 嗌 掟 喐 睩 嗱。維基標題含其中任一字、又不是教育部《重編國語辭典修訂本》詞目（`moe_titles()`，建置本來就讀）的，不進候選（`cantonese(w, headwords)`）。「俾」「咩」「喎」「嘞」國語也用，不在清單內；手動詞與網路用語不套用；基底詞庫與動漫詞包不動。

- 取捨：在候選階段拿掉，簡體與異體的判斷與落點才不會指向被拿掉的詞。
- 驗證：`tools/test_build_overlay.py` 的 `Cantonese.test_rule`（真值表）與 `test_the_shipped_overlay_keeps_only_these_cantonese_character_words`（已提交的疊加層裡含粵語字的非手動、非網路用語詞固定是 10 個辭典詞目；CI 沒有萌典，所以斷言已提交的檔）。

### 2.7 萌典補詞（`--moedict-supplement FILE…`，預設關）

標籤 `萌典補詞`。候選 = 教育部國語辭典詞目（萌典的 `dict-revised`）中，純漢字、字數在 2–10、不在基底／疊加層候選／名單層／任何詞包／手動詞與網路用語、讀音音節數等於字數且每個音節基底都有、每個字是基底的單字詞、不含簡體專用字，而且在給定的純文字語料檔（一行一段，`.gz`／`.bz2` 照副檔名）裡作為子字串出現 ≥ `SUPPLEMENT_MIN`（2）次者（重疊算、跨行不算；`--jobs N` 用 N 個程序數）。讀音 = 簡編本，再萌典第一讀音（§2.8）；分數 = 同字數的 `length_scores`，不加手動詞的加分；之後和維基標題候選一起走 §2.3 的重複與異體判斷。測試：`MoedictSupplement`、`BuildFlags` 的 `test_supplement_*`。

### 2.8 讀音來源旗標（`--moedict-readings`，預設關）

- 讀音依序：教育部《國語辭典簡編本》（xlsx 在 zip 裡，SHA-256 釘在 `CONCISED_SHA256`，路徑預設在快取資料夾，環境變數 `SHANJIE_CONCISED` 可改）→ 萌典第一讀音（路徑 `SHANJIE_MOEDICT`）→ `tools/readings.py` 的規則（規則 3b 開）。每一邊都要音節數等於字數、每個音節基底都有，不合就退到下一個。
- 簡編本一個詞名有好幾列時取「多音排序」最小的一列（空白排最後）；它的「變」輕聲、「語音」另寫第二列；只差「一」「不」變調的「變」不寫（變調列另外補）；「又音」「讀音」不寫。
- 讀音有輕聲的詞，另寫一列把輕聲換成本調的列，排在前面（簡編本給了本調就用它；否則是該字同韻非輕聲單字讀音中分數最高者，同分取該字在基底裡最前的；沒有同韻的位置保留輕聲）。
- `readings.py` 判 `CHECK`（有歧義）或 `GAP` 的詞保留原讀音，印出個數；`--reading-report PATH` 把讀音有變的、保留的與第二列寫成報告。規則改音時 `為 町 蛤 吧 萎 凊` 保留原讀音（`RULE_KEEP_CHARS`）。
- 詞裡有日文新字體字（OpenCC `JPShinjitaiCharacters.txt`，SHA-256 釘在 `JP_SHA256`，且字不在簡編本與萌典任何詞條）的詞不寫，記到 `build/overlay/overlay-jp-removed.tsv`。
- 兩個來源檔的雜湊不符、簡編本表頭缺欄都中止並指出檔案。測試：`BuildFlags`、`ConcisedSources`、`RuleKeepChars`、`ToneRow`。

### 2.9 手動詞（`data/lexicon/overlay-manual.tsv`）

- 欄位：詞、讀音（音節以空白分隔；同一個詞要兩個讀音就寫兩列）、備註；`#` 開頭與空行略過；CC0。標籤 `manual`。
- 每列要：純漢字、字數在分數表裡（2 以上）、讀音音節數等於字數且每個音節基底都有、詞不在基底／疊加層／名單層／動漫與資訊詞包（`taken`）；產生的列（含變調列）同一個（讀音, 詞）不能出現兩次。不符就中止並指出檔名與行號。
- 分數 = 同字數的 `length_scores` + `MANUAL_BONUS`（0.75）。
- 取捨：0.75 讓目前所有手動詞在出貨設定（兩種設定、三種強度）下單獨與放在句子裡都排第一；0.5 時開 KN 的模型在書面設定下有一句會輸；測試 `cli/tests/golden.rs` 的 `manual_words_come_first_in_the_shipping_setting` 逐詞檢查（列在 `eval/golden/manual-words-rows.txt`）。

### 2.10 網路用語（`data/lexicon/slang-add.tsv`）

- 欄位：詞、讀音、來源，可選的第四欄「加分」；標籤 `slang`；CC BY-SA 4.0（來源見下）；是建置輸入，不隨 App 內附（內容已在 `overlay-add.tsv`）。
- 建置（`tail_rows`）：接在手動詞後面，檢查規則同手動詞（詞也不能是手動詞、名單層、動漫／資訊詞包的詞），分數 = `length_scores` + `MANUAL_BONUS` + 加分。沒有這個檔時輸出與沒有網路用語的疊加層逐位元組相同。
- 同一個讀音有好幾個網路用語時，檔案裡排前面的留第一，同讀音第 k 個（k 從 0 起）的列再扣 k × `SLANG_BEHIND`（1.0），只當候選；所以檔案順序有意義，新增詞要插在對的位置（用量多的在前）。
- 加分：只能放在同讀音的第一列，必須是 0.5 的倍數且 0 < 加分 ≤ `BOOST_MAX`（3.0）；放在被扣分的列上會讓它與第一名打平或超過，建置中止。用途：詞庫分數輸給「沒人這樣講的組合」時抬高。核心把模型看過的疊加層詞封頂在語料頻率，所以加分只對模型沒看過的詞有效。
- 收錄準則（來源檔的內容準則，建置工具不檢查）：來源是公開維基與維基詞典的條目標題（CC BY-SA 4.0）、網路社群百科的條目標題（Fandom，文字 CC BY-SA 3.0，依後續版本條款以 4.0 散布）、外部語言模型建議的字串（只取字串）、人工補的詞（CC0）；只取詞的字串，不取解釋文字。台灣公開論壇的文章與推文用量只用來確認收錄與排序，只取詞的字串與計數、不含原文：直接從語料挖出的字串要分區 ≥ 3、文章 ≥ 10，人或模型挑過的候選要分區 ≥ 2、文章 ≥ 3（最早一批較嚴：分區 ≥ 5、文章 ≥ 20）。只收台灣在用的說法，不收只在中國大陸用的、人名與網名（歸名單層）、有明確單一出處的台詞（歸經典台詞詞包）。同音時，原本的第一名（X）的文章數 ≥ 候選就不收（文章數多的留第一）；X 是沒人這樣講的組合時才在第四欄加分。挖掘與分類的腳本不隨 repo 散布，`slang-add.tsv` 是人工審過、提交進 repo 的輸入。
- 驗證：`tools/test_build_overlay.py` 的 `ManualRows`（含第四欄只對 `slang` 有效、加分只能在第一個）、`SlangBehind`、`ShippedOverlay`（已提交的檔尾 = `tail_rows()` 對已提交的 `overlay-manual.tsv` 與 `slang-add.tsv` 的結果，所以只改其中一個而沒重產會失敗）。

### 2.11 重建與驗證

- `python3 tools/build_overlay.py`：產生並寫入（需要 dump；萌典補詞需要 `--moedict-supplement` 給語料檔；§2.7、§2.8 與 `--lexgap`（§2.1）的旗標預設關，不帶旗標的輸出只含維基來源的列，所以和含 `萌典補詞` 列的提交檔不同）。
- `python3 tools/build_overlay.py --check`：重新產生並與提交的檔案比對（旗標要和建出提交檔的那一組相同）；不同 exit 1。
- `python3 tools/build_overlay.py --tails-only`：不讀 dump，保留 `overlay-add.tsv` 中標籤不是 `manual`、`slang` 的列，只重算檔尾；改了手動詞或網路用語時用。不能和 `--lexgap` 一起用（`lexgap` 的列要完整建置才能核對；一起給、或 `--lexgap` 給空字串，都是用法錯誤、exit 2）。
- 沒有 dump 的機器：`tools/test_build_overlay.py` 核對檔尾、粵語詞與各旗標的行為（`make test-python` 與 CI 的「Overlay manual words and readings」步驟都跑）。

### 2.12 重排保護詞（`data/lexicon/rerank-protect.tsv`）

- 用途：語意校正的重排不得從 n-gram 的第一名換掉的常用詞。它不是詞庫的一層：不加詞、不改任何分數，只讓這些詞進入重排的遮罩（`docs/spec/decoder.md` §7 的保護集合 = 手動詞加上這份的詞，遮罩函式 `tinylm::manual_mask` 不變）。
- 格式：三欄以 Tab 分隔，`詞<TAB>讀音<TAB>備註`；讀音的音節以空白分隔；`#` 開頭與空行略過；CC0，隨 App 內附（`Resources/rerank-protect.tsv`）。
- 載入（`engine::load_lexicon_split` 在所有層（基底、疊加層、變調、名單、啟用的詞包、評測用的額外疊加層）合併之後呼叫 `Lexicon::with_protected`；引擎建立、評測 CLI 與重播都走這個函式，`load_lm` 不再讀）：檔案缺、讀不了或任何一列不成立都是載入失敗（C ABI 回 3），錯誤指出列號。一列不成立的情形：不是三欄、詞不是純漢字、字數不等於音節數、（讀音, 詞）不在這份詞庫的任何一層。基底詞可以列入；最後一項讓打錯的保護詞不會無聲地什麼都不保護。清單只放基底或疊加層（`overlay-add.tsv`）的詞：只在詞包或名單層的詞，在那個詞包關掉或 `--no-names` 時會讓載入失敗。它不是詞庫的一層，放在這一章是因為它和手動詞共用遮罩。
- 效果：只在重排遮罩上。重排的某一段，n-gram 第一名若在對應位置以這個讀音含有這個詞，沒有同位置保有它的候選就不能被選；第一名沒用這個詞時什麼都不保護。解碼器、候選窗與所有分數，和這份檔只有註解時逐位元組相同。
- 驗證：`core/tests/rerank_protect.rs`（基底詞被遮罩、加入保護不改分數與詞條、只有註解的檔不保護任何東西、壞列各自報錯並指出列號、出貨檔載得進來、缺檔是載入失敗、出貨設定下的真引擎逐鍵重現）。

## 3. 名單層 `names-add.tsv`

常開的專有名詞層（公司、品牌、學校、店家、餐廳、飯店、創作者與節目名）。載入規則見 §1.1；標籤 `names`（主流程）、`creator`、`store`（檔尾追加）。`tools/build_names.py` 產生；`names-add.tsv`、`names-sources.tsv`、`names.json` 以 CC BY-SA 4.0 散布，`names-manual.tsv`、`names-readings.tsv`、`names-creators.tsv`、`names-stores.tsv` 是 CC0 的建置輸入，不隨 App 內附。名單層另有一份英文寫法 `names-english.txt`（CC0，本專案自寫，編進核心；規則見 `docs/spec/english-mixed.md` §4.4）。

### 3.1 檔案與載入

| 檔 | 內容 |
|---|---|
| `names-add.tsv` | 疊加層格式；先 `names` 列，再 `creator` 列，再 `store` 列 |
| `names-sources.tsv` | 每個詞的來源：`word`、`source_kind`、`sources`、`qid`、`note`（`POLY` 表示含多音字） |
| `names.json` | 版號 `nsi<NSI commit 前 8 碼>-moe<學年度>-<names-add.tsv 雜湊前 8 碼>`、各來源詞數、各過濾丟掉的數量、未處置的同音衝突讀音數、每個檔的 SHA-256 |
| `names-manual.tsv` | 人工補的名字：詞、備註、選填讀音 |
| `names-readings.tsv` | 人工核對過的整名讀音：名字、讀音（音節數要等於字數） |
| `names-collisions.tsv` | 同音衝突的處置（人工維護的輸入，建置只讀不改） |
| `names-creators.tsv`、`names-stores.tsv` | 創作者與店家名字的來源檔（§3.5） |
| `names-english.txt` | 名單層的英文寫法：一行一個寫法（`[A-Za-z]{3,32}`、至少一個大寫字母，`#` 開頭的行與空行略過），和 `data/english-extra.txt` 合成一張正式寫法表，小寫不得重複。編進核心，不由 `tools/build_names.py` 產生、不隨資料夾載入、不受 `--no-names` 影響 |

- 引擎一律載入 `names-add.tsv`（缺檔是載入失敗）；評測 CLI 的 `--no-names` 才把它拿掉。名單層的詞照 §1.4 封頂；名單層的分數已含 `NAMES_BONUS`，載入端不另外加。

### 3.2 主流程（`build()`）

來源與規則（`names-add.tsv` 的 `names` 列）：

1. **name-suggestion-index（NSI）**，BSD-3-Clause，釘一個 commit（`NSI_COMMIT`；`--nsi-commit` 可覆寫）。類別固定 17 個：`amenity/` 的 cafe、restaurant、fast_food、bank、fuel、pharmacy，`shop/` 的 convenience、supermarket、department_store、mall、electronics、mobile_phone、clothes、cosmetics、bakery、tea、beverages。台灣品牌 = `locationSet.include` 有 `tw` 且 `exclude` 沒有 `tw`（`001` 全球不算）。取名字依序 `name:zh-Hant`、`name:zh-TW`、`name:zh`、`brand:zh-Hant`、`brand:zh`、`name`、`brand`，取第一個「全部是漢字、沒有簡體專用字」的值；沒有可用的名字就略過。
2. **Wikidata（CC0）＋中文維基**：NSI 的 `brand:wikidata` 有中文維基 sitelink 時，也收該條目在 `variant=zh-tw` 下的顯示標題（去掉消歧義括號）；中文維基分類（`Category:台灣手搖茶飲品牌`、`台灣餐飲公司`、`台灣公司`、`台灣電子商務網站`、`臺灣的大學`，各取分類內條目與一層子分類；不存在的分類記在 `names.json` 的 `categories`、不自動換名）。所有維基標題一律取 zh-tw 的顯示標題。分類來源的名字去掉公司後綴（股份有限公司、有限公司、公司、集團，長的先試，剩下不到兩個字就不去）；分類來源裡結尾是「列表」的條目不是名字。
3. **教育部統計處學校名錄**（政府資料開放授權條款第 1 版）：大專校院與一般高級中等學校兩個 CSV，只取「學年度」是數字且等於該檔最大學年度的列；學校簡稱（政大、清大）只收中文維基有重定向到該校條目、且比全名短、是全名的子序列（台、臺視為同字）的標題。
4. **手動補** `names-manual.tsv`。
5. 所有名字要全部是漢字、2–10 字，含英文、數字、符號的整個不收（不截取漢字部分），含簡體專用字的不收。

不收已在基底、`overlay-add.tsv`、`sandhi-add.tsv`、動漫詞包（以及存在時的迷因詞包）任何讀音下的字串；建置最後再檢查輸出與這些檔的詞集合交集為空（`check_disjoint`），不為空就中止。`python3 tools/build_names.py --check` 與 `make test-python` 對已提交的檔做同一件檢查。

### 3.3 讀音與分數

- 讀音：`tools/readings.py`（手動列自己的讀音優先），再套 `names-readings.tsv` 取代該名字的讀音（音節數必須等於字數）；標 `CHECK` 的與多音字（樂 行 長 重 藏 都 會 種 朝）列在 `check-readings.txt` 給人核對；拼不出讀音的不收，列在 `unreadable.txt`。不改 `tools/reading_overrides.tsv`（那張表會改到評測集合與動漫詞包的讀音）。
- 分數 = 同字數的 `length_scores`（2–10 字）+ `NAMES_BONUS`（2，log10），建置時寫進檔案。同讀音的先後依序：處置列名次、來源數多的在前、字串；同讀音第 k 位的分數減 k × `TIE`（1e-6）。
- 取捨：名單詞多半不在語料詞表裡，只拿到「第 25 百分位 − 1.0」的分數，會輸給常用字組合；加 2 讓店名與品牌在出貨設定下打得出來而不擠掉一般集合。

### 3.4 同音衝突

`build_names.py` 自己提供 `decode`：評測 CLI 加 `--no-names`（沒有名單的基準）與 `--extra-overlay`（候選名單），語言模型用 `data/lm` 的出貨模型，聊天與書面都解碼；不修改 `build_acg_pack.py`。

- 類型 (c)：名字把既有詞庫的詞擠下第一名，不收（既有詞勝）；丟掉之後別的詞可能改變，重跑到沒有 (c) 為止（上限 `LEXICON_RULE_ROUNDS` = 5 輪，超過報錯）。
- 類型 (a)：名單內同音，要在 `names-collisions.tsv` 有處置（欄位：讀音、保留的詞、排除的詞、理由；排除欄寫 `+詞` 表示兩個都留，`+` 後面是另一個留下的詞；同讀音的先後照列出現的順序，保留的詞在前）。
- 名字與動漫詞包的詞同音（詞包預設開）：名字把詞包詞擠下第一名時一律當未處置的 (a)；處置列可以把詞包詞排在最前面、名字用 `+` 留著，這時名字在那個讀音的分數最多是「詞包詞的分數 − `TIE`」（`below_pack`）。
- 未處置的列寫到 `build/names/collisions.txt`；`names.json` 的 `unresolved_collision_readings` 要是 0 才合併。
- 其他報告：`unreadable.txt`、`check-readings.txt`。

### 3.5 創作者與店家名字（`--append-creators`，檔尾追加）

- 來源檔 `names-creators.tsv`（實況主、YouTuber、VTuber、節目與頻道名，含台灣以外中文圈的名人；標籤 `creator`）與 `names-stores.tsv`（店家、餐廳、飯店、企業；標籤 `store`）：欄位為詞、讀音（音節以空白分隔）、備註，可選的第四欄加分；`#` 開頭與空行略過；CRLF 照 LF 讀。
- 追加模式不重建既有名單：`names-add.tsv`／`names-sources.tsv` 去掉 `creator`、`store` 列後逐位元組保留，檔尾依序接 creator、store 的列（`names` → `creator` → `store`）；重跑換掉的是這兩種標籤的列；兩個來源檔都不存在或沒有資料列時什麼都不碰；`names.json` 的 `sources` 分別計兩種標籤。完整重建（不帶 `--append-creators`、`--out` 是詞庫資料夾）也會接著追加。追加不連網、不讀快取。
- 每列要：詞是 2–10 個漢字、字數在分數表裡、不在 `taken`（基底、疊加層、名單其他列、詞包、前面的列）；讀音音節數等於字數，每個音節都是基底裡那個字的讀音（輕聲音節允許該字同音節的任何聲調）；同一個（詞, 讀音）重複中止。同一個詞可以有不同讀音的好幾列（只限同標籤）；店家不能用 creator 已有的詞（換讀音也不行）。不符就中止並指出路徑與行號。
- 被擋掉（印到 stderr、不中止）：詞已在迷因詞包；讀音等於 `names` 既有列的讀音；店家的讀音等於 creator 列的讀音。
- 分數 = 同字數的 `length_scores` + `NAMES_BONUS` + 加分 − 同標籤同讀音的名次 × `SLANG_BEHIND`（1.0）。檔案順序就是名次，工具不依備註的數字重排。加分必須是有限值、≥ 0，且只能放在同讀音的第一列。模型看過的詞被封頂，加分無效。含「一」「不」的詞照 `overlay_rows` 附變調列。
- 收錄準則（來源檔的內容準則，工具不檢查）：名字取網友常打的中文寫法，純英文帳號與混字母的不收；台灣公開論壇的文章與推文用量（分區 ≥ 3、文章 ≥ 10，只取詞與計數）；同讀音的詞庫詞用量 ≥ 名字的不收；店家另外要出貨解碼器現在打錯、用量多於原本的第一名；用量是子字串計數，被更長字串（全名、地名）灌水的短稱不收；名字字串來自外部語言模型的建議與人工補，只取名稱。
- 驗證：`tools/test_build_names.py` 的 `AppendCreators`（既有列逐位元組不變且雜湊固定、同讀音的名次扣分、各種無效列、被擋列不中止、店家與 creator 的互斥、CRLF、第四欄加分、來源檔缺或空時完全不動）、`MainRebuild`。

### 3.6 評測專用的 `--extra-overlay FILE`

只在評測 CLI：檔案的列併進詞庫，也併進封頂用文字（和 `--packs` 同一條路），所以詞彙外的詞會扣 `UNSEEN_OVERLAY_PENALTY`；可與 `--packs` 併用；缺檔時結束碼非 0 並印出路徑；給了選項時摘要行加 `+xo:<檔案 SHA-256 前 8 碼>`，沒給時輸出一字不變。測試：`cli/tests/golden.rs` 的 `extra_overlay_adds_words_and_fails_loudly_when_missing`。

### 3.7 抓取、重現與驗證

- 抓取只連 `zh.wikipedia.org`、`www.wikidata.org` 的 `/w/api.php`，`raw.githubusercontent.com`（只限 NSI 那個 commit 的 `data/brands/` 路徑），`data.gov.tw` 與 `stats.moe.gov.tw` 的 CSV；其他網址與轉址到清單外都拒絕。限速、User-Agent、`maxlag` 沿用動漫詞包的 `Api`（每秒 ≤ 1 個請求）；一次建置請求 ≤ `MAX_REQUESTS`（3,000）、快取 ≤ `MAX_CACHE`（500,000,000 位元組），超過中止。`--offline` 只讀快取，缺檔中止。
- 同一份快取離線建兩次，三個輸出檔逐位元組相同；建置前後 `names-collisions.tsv` 不變。
- 測試（`make test-python`；`tools/test_build_names.py`，夾具由真實的 NSI 與名錄資料裁剪，不連網）：`Rules`（`locationSet`、取名順序、簡體判斷、名錄只取最大學年度、後綴、簡稱、各字數的分數）、`Fetching`（允許清單、轉址、請求與快取上限、離線）、`Build`（每個來源與過濾、四份檔任何讀音下的詞都不收、尾端交集檢查會中止、已提交的 `names-add.tsv` 與其他層沒有交集且分數有限、讀音表、(a)(c) 衝突與處置、兩次建置逐位元組相同、manifest）。
- CI 的「Names lexicon builder」步驟跑整個 `tools/test_build_names.py`。

## 4. 詞包（各詞包共通）

### 4.1 位元、檔案與載入（引擎與 C ABI）

| 詞包 | 位元（C 巨集／Rust 常數） | 檔案 | CLI 名稱 |
|---|---|---|---|
| 動漫與遊戲詞 | 1（`SHANJIE_PACK_ACG`／`PACK_ACG`） | `acg-add.tsv` | `acg` |
| 經典台詞 | 2（`SHANJIE_PACK_QUOTES`／`PACK_QUOTES`） | `quotes-add.tsv` | `quotes` |
| 資訊與 AI 詞 | 4（`SHANJIE_PACK_IT`／`PACK_IT`） | `it-add.tsv` | `it` |
| 迷因與實況用語 | 8（`SHANJIE_PACK_MEME`／`PACK_MEME`） | `meme-add.tsv` | `meme` |
| 美妝與保養詞 | 16（`SHANJIE_PACK_BEAUTY`／`PACK_BEAUTY`） | `beauty-add.tsv` | `beauty` |

- `shanjie_engine_new_packs(data_dir, layout, packs_dir, packs, out)`：`packs` 是位元遮罩，遮罩 0 忽略 `packs_dir`（可為 NULL），等於 `shanjie_engine_new`。回傳碼：1 = `packs_dir` 是 NULL 而遮罩非 0（或其他空指標）、2 = 遮罩有未知的位元或路徑不是 UTF-8、3 = 詞包檔存在卻讀不了或解析失敗（也包括基底、疊加層、名單層、變調層載入失敗）。
- 詞包列併進詞庫，所以一個引擎的詞包組合是固定的；殼用「釋放引擎、再建一個」切換，和換鍵盤排列相同。遮罩內缺檔的詞包什麼都不貢獻。
- 關掉的詞包不讀檔：選字結果、候選清單與預測和沒有那個詞包檔的引擎逐位元組相同。
- 評測 CLI：`--packs acg|quotes|it|meme|beauty` 或逗號串列，`--packs-dir DIR`（預設 `data/packs`）。要求的詞包檔不存在時 CLI 報錯並印路徑（引擎不報錯，這是 CLI 與引擎的差別）；單用 `--packs-dir` 沒有 `--packs` 是錯誤；兩個檢查都在讀模型之前。摘要行在載入詞包時依序多 `+acg`、`+quotes`、`+it`、`+meme`、`+beauty` 標記，沒有詞包時一字不變。`PACKS=acg,it,meme,beauty`（`PACKS_DIR` 預設 `data/packs`）讓 `core/tests/engine_predict.rs` 的延遲測試載入出貨的遮罩，並斷言詞庫列數等於基底加所列詞包。
- 驗證：`core/tests/engine_pack.rs`（`the_pack_switch_changes_the_result_and_off_is_identical_to_no_pack_file`、`the_it_pack_has_its_own_bit_and_file`、`the_meme_pack_has_its_own_bit_and_file`、`an_unknown_pack_bit_is_refused`、`the_quote_bit_reads_quotes_add_tsv_only`、`the_names_layer_is_capped_on_the_engine_path`、`an_anime_pack_word_keeps_its_score_on_the_engine_path`）；`cli/tests/golden.rs` 的 `packs_options_fail_loudly`、`unreadable_pack_file_names_the_path_and_kind`、`unparsable_pack_is_a_parse_error`。

### 4.2 App 的開關與預設

| 詞包 | 偏好鍵 | 沒設過時 | 設定視窗標籤 |
|---|---|---|---|
| 動漫與遊戲詞 | `acgPack` | 開 | 動漫與遊戲詞 |
| 經典台詞 | `quotePack` | **關** | 經典台詞 |
| 資訊與 AI 詞 | `itPack` | 開 | 資訊與 AI 詞 |
| 美妝與保養詞 | `beautyPack` | 開 | 美妝與保養詞 |
| 迷因與實況用語 | `memePack` | 開 | 迷因與實況用語 |

- 開關只在設定視窗「詞包」區塊，依序動漫、經典台詞、資訊、美妝、迷因；新詞包加在這一區。「動漫與遊戲詞」下方有一行灰字「資料更新至 YYYY-MM-DD（維基百科）」，日期取 `Resources/packs/acg.json` 的 `latest_source_revision`（UTC 日期，讀一次；檔案缺、欄位缺或讀不了就不顯示）；詞包關掉時照樣顯示。
- 切換一個詞包：先把組字送出，再改設定並寫入偏好，再重建引擎（同值不做任何事）。
- 輸入選單沒有詞包開關，只有一段不能點的「已開啟的詞包」：系統的選單小標題「已開啟的詞包」，下面依設定順序每個詞包一行，列引擎**實際載入**的詞包（`loadedPackMask`：照設定的遮罩建成功就是它，缺檔的詞包不算；退回後只含留下的詞包），前後各一條分隔線（`docs/spec/macos-shell.md` §7）。每個詞包各一行，不寫成一長行，選單不會隨詞包變多而變寬。一個都沒載入就整段不顯示。選單不讀偏好值。
- 遮罩（`packMask`）= 各開關為開的位元；一個從沒動過任何開關的 App 建出遮罩 29（動漫、資訊、迷因、美妝）。

### 4.3 載入失敗的退回

合併建置失敗時（`Shell.build`）：

1. 先建一個不帶詞包的引擎，只為判斷是不是詞包的錯，建完立刻釋放；只有一個啟用的詞包檔案在時（只開一個詞包，或其他開著的詞包缺檔）沒有「少一個」可試，這個引擎成功就直接留下當引擎（共建兩次），不釋放再建；
2. 它也失敗：原因不是詞包，記兩個回傳碼，不再嘗試，沒有引擎（所有按鍵直通）；
3. 它成功：對每個啟用、檔案也在的詞包依設定順序各試「少這一個」（`mask & ~bit`；缺檔的詞包本來就沒載入，拿掉它幫不上忙，不試），第一個成功的留下，其餘詞包照開，記一行含回傳碼與留下遮罩的日誌；
4. 沒有任何一個少一個的組合成功（兩個以上的檔壞了）：建不帶詞包的引擎。

偏好值不動，也不顯示錯誤，下一次重建會再試。驗證：`ShellTests` 的 `testCorruptPackFallsBackToAnEngineWithoutIt`、`testWithBothPacksOnABrokenFileKeepsTheOtherPack`、`testBrokenQuotesFileKeepsAcgAndIt`（引擎建置次數 4）、`testBrokenItPackDropsBothPacksFromTheMenuLine`、`testBrokenNonPackFileStopsAfterTwoBuilds`、`testOnlyPackBrokenKeepsTheProbeEngine`（只開一個詞包，引擎建置次數 2）、`testOnePresentPackBrokenWithAnotherFileMissingKeepsTheProbeEngine`（開兩個、一個缺檔，建置次數 2）、`testBrokenBeautyFileKeepsAcgAndIt`（引擎建置次數 5）、`testBrokenMemeFileKeepsTheOtherPacks`（引擎建置次數 6：合併、不帶詞包、少動漫、少資訊、少美妝、少迷因）、`testPackLineNamesTheBeautyPackInSettingsOrder`、`testPackLineNamesTheMemePackLast`、`testPackLineNamesTheQuotePackAfterAcg`、`testPackLineNamesTheLoadedPacks`、`testAcgPackMenuItemTogglesStoresAndSurvivesRestart`、`testItPackMenuItemTogglesStoresAndSurvivesRestart`、`testAcgDataDateLineComesFromTheManifest`；`SettingsTests` 的 `testBeautyPackSwitchFromTheWindowReachesStoreMaskAndTypedResult`、`testMemePackSwitchFromTheWindowReachesStoreMaskAndTypedResult`。

### 4.4 打包與自測

- `scripts/build-app.sh` 把詞包的執行期檔放進 `Resources/packs/`：`acg-add.tsv`、`acg-sources.tsv`、`acg.json`、`quotes-add.tsv`、`quotes.json`、`it-add.tsv`、`it-sources.tsv`、`it.json`、`beauty-add.tsv`、`beauty-sources.tsv`、`beauty.json`、`meme-add.tsv`、`meme-sources.tsv`、`meme.json`；建置輸入（`meme-collisions.tsv`、`acg-groups.tsv`、`acg-collisions.tsv`、`acg-exclude.tsv`、`acg-manual.tsv`、`quotes-manual.tsv`、`it-manual.tsv`、`it-collisions.tsv`、`it-readings.tsv` 等）不進 App。疊加層、變調層、名單層與 `digits.tsv` 放在 `Resources/` 根。
- `scripts/check-app.sh`：五個清單檔（`acg.json`、`quotes.json`、`it.json`、`beauty.json`、`meme.json`）列出的每個檔都在、SHA-256 與位元組數與實際檔案相符，且必須列出對應的 `*-add.tsv`；署名檔 `LICENSES/CC-BY-SA-4.0-attribution.txt` 要列 `packs/acg-add.tsv`、`packs/it-add.tsv`、`packs/meme-add.tsv`、`names-add.tsv`、政府資料開放授權條款的標示與 name-suggestion-index 的 BSD-3 全文；`Resources/names-add.tsv`、`digits.tsv` 與 repo 的檔逐位元組相同。
- 自測（`shanjie --selftest`）帶動漫、資訊、迷因與美妝詞包建引擎：要求 `ㄕㄣˊ ㄧㄡˊ ㄕˇ` 得到 `榊遊矢`、`ㄧㄥˊ ㄇㄨˋ ㄅㄠˇ ㄏㄨˋ ㄑㄧˋ` 得到 `螢幕保護器`、`ㄇㄟˊ ㄘㄞˇ ㄍㄠ` 得到 `眉彩膏`、`ㄎㄜˇ ㄧˇ ㄙㄜˋ ㄙㄜˋ` 得到 `可以色色`（迷因詞包檔的一列，§9.3），所以四個詞包缺檔、壞檔或沒生效都失敗；經典台詞預設關，不在自測裡。
- 授權：`acg-*`、`it-*`、`meme-*` 的資料檔與 `names-add.tsv`、`overlay-add.tsv` 以 CC BY-SA 4.0 散布（署名維基與維基詞典貢獻者，資訊詞包另標政府資料開放授權條款的提供機關，名單層另附 NSI 的版權聲明）；迷因詞包（`meme-*`）同樣以 CC BY-SA 4.0 散布，署名檔有它的一段（§9.1）；經典台詞的清單與詞包、美妝詞包 CC0。逐檔的授權與署名寫在 `LICENSES/data.md`。

### 4.5 共通的建置機制

各詞包的建置工具共用 `tools/build_acg_pack.py` 與 `tools/build_overlay.py` 的函式（`import`），不各寫一份：

- **字串**：只收 2–10 個漢字（`HAN`）；含英文、數字、符號的不收；數量列在報告。
- **去重**：詞包的字串不得出現在基底、`overlay-add.tsv`、`sandhi-add.tsv`、`names-add.tsv`（`lexicon_words()`）任何讀音下；資訊與經典台詞詞包另外不得出現在先建的詞包（動漫；經典台詞也含資訊）。原因：封頂依字串封頂所有讀音，收進來會改到既有詞的分數；同一個（讀音, 詞）重複還會讓核心載入失敗。
- **讀音**：`tools/readings.py`（規則依序：`reading_overrides.tsv` 的人工確認 → 「一」「不」打本調 → 多字詞取萌典第一讀音（必須是詞庫裡這個詞的讀音之一）→ 單字取小麥破音字表的主要讀音 → 詞庫只有一個讀音或第一名明顯較高（差 ≥ 0.5）取第一名 → 其他取詞庫第一名並標 `CHECK`；萌典只用來選讀音，不散布內容）。讀不出的（`UNREADABLE`）丟掉，`CHECK` 的保留並記在 sources 檔與報告。環境變數 `SHANJIE_MOEDICT`、`SHANJIE_HETERO` 指向萌典與小麥破音字表；沒設時需要它們的測試跳過並印 NOTE。
- **分數與變調列**：同字數的 `length_scores`（資訊詞包另加 3，動漫詞包另有分級，見各節）；含「一」「不」的詞照 `overlay_rows` 附變調列；同讀音的詞包列依 `ordered` 的先後各減 k × `TIE`（1e-6），因為解碼器遇到同分時的先後與檔案順序無關，要讓排序成立只能靠分數。
- **同音衝突**（`detect_collisions`）：對每個詞包詞 w 的每個讀音（含變調列的讀音），聊天與書面各解碼兩次（不開／開這個詞包），只在**開了之後第一名有改變**時才算：
  - (a) 開了之後第一名是另一個詞包詞：詞包內同音；
  - (b) 不開時第一名不是 w、而且在「參考名單」（建置時抓到的所有來源名稱，在任何過濾之前）裡：搶走了拼出來的名字；
  - (c) 不開時第一名是既有詞庫的詞，開了之後變成 w：詞包詞把既有詞擠下第一名。
  
  第一名沒變的，詞包的寫法只多一個候選，不必處置。(c) 依規則處理：既有詞勝，詞包詞丟掉，丟掉之後別的詞可能跟著變，重跑到沒有 (c) 為止（`LEXICON_RULE_ROUNDS` = 5，超過報錯）；動漫、資訊詞包與名單層的 (a)(b) 要在各自的處置檔有對應列，沒處置的列進報告，合併前未處置的讀音數必須是 0（寫在清單檔的 `unresolved_collision_readings`）；經典台詞詞包只處理 (c)。處置檔格式同 `names-collisions.tsv`（§3.4）。
- **解碼**：呼叫評測 CLI（`target/release/shanjie-eval`，建置前先 `cargo build --release --locked`，`SHANJIE_EVAL` 可指定別的執行檔），執行緒數 `SHANJIE_THREADS`（Mac 預設 1，其他機器預設用滿核心；CLI 保證輸出與執行緒數無關）。
- **輸出**：`*-add.tsv`、`*-sources.tsv`（動漫、資訊）、清單檔 `*.json`（版號、各來源數量、每個檔的 SHA-256 與位元組數）；先寫 `.tmp` 再改名。同一份快取（與計數檔）離線建兩次，輸出逐位元組相同。
- **抓取**：每秒不超過 1 個請求、描述用途的 User-Agent、維基 API 加 `maxlag=5`；快取每個請求一個檔；`--offline` 只用快取，缺了就中止；抓取只做一次，之後的建置與測試都離線。
- **不連網的測試**：假的 API／網站，不連網、不需要真資料。

## 5. 動漫與遊戲詞包

`data/packs/acg-add.tsv` 等；`python3 tools/build_acg_pack.py`。

### 5.1 檔案

| 檔 | 內容 |
|---|---|
| `acg-add.tsv`、`acg-sources.tsv`、`acg.json` | 詞包、每個詞的來源（種類 `cgroup`／`title`／`char`／`manual`，頁面@revision）、清單檔（版號 `<最新來源頁日期>-<acg-add.tsv 雜湊前 8 碼>`、各來源數量、`latest_source_revision`、`yearly_lists`、revision 範圍、各檔 SHA-256、`unresolved_collision_readings` 等） |
| `acg-groups.tsv` | 公共轉換組的分類：區段、組名、模組、`include`／`exclude`、類別或理由；第四欄拼錯就報錯，不當成排除 |
| `acg-manual.tsv` | 人工補的詞：詞、作品、備註、選填的另外讀音（音節數要等於字數；這個詞同時有工具選的讀音和這一個，各有一列）；標籤來源 `manual` |
| `acg-exclude.tsv` | 從條目抽出、但不是名字的字串（句子片段、轉換錯誤）與理由；不收進詞包也不算參考名單 |
| `acg-collisions.tsv` | 同音衝突的處置（§5.6） |
| `acg-keep-both.tsv`、`acg-kept-c.tsv` | 兩種寫法都留的（讀音, 詞）、讓詞包名字排第一的 (c) 詞（讀音、詞包詞、被擠下的詞庫詞） |
| `acg-yield.tsv` | 讓位清單：讀音、詞包詞、讓位後該回來的第一名；這些列再扣 3.0 |
| `acg-removed.tsv` | 已出貨、但來源已沒有而人工核准拿掉的詞 |
| `acg-v040-words.txt` | v0.4.0 出貨詞包的詞（第二欄、位元組排序去重，SHA-256 釘在 `V040_SHA`），用來區分「已出貨的詞」與新詞 |
| `acg-counts.tsv` | 凍結的次數表（§5.5） |

來源是中文維基百科（公共轉換組 `Module:CGroup/*`、動畫作品條目標題、條目與角色列表裡的登場人物，CC BY-SA 4.0）與 Wikidata（作品清單，CC0）；不用萌娘百科（授權不相容）。

### 5.2 抓取與詞的來源

- 只組出 `zh.wikipedia.org/w/api.php` 與 `query.wikidata.org/sparql` 的網址；一次建置請求 ≤ `MAX_REQUESTS`（5,000）、快取 ≤ `MAX_CACHE`（1,500,000,000 位元組）；快取放 `$SHANJIE_CACHE/sources/acg/`，每次抓取記 revision ID（條目的取自 `action=parse` 同一次回應；`missingtitle` 回應也快取，離線才跑得完）。
- **轉換組**：收哪些組依 `acg-groups.tsv`。`Template:CGroup/list` 的 ACG 段混著電影、電視，所以不能整段收；清單上沒有的新組不收，列在輸出的 `unclassified_groups`。規則解析：模板規則（`{{CItem|…}}`）照最外層 `|` 拆成參數，具名參數丟掉；Lua 模組的 `Item(` 與 `rule =` 也解析，同一頁兩種都有時取聯集；取每條規則的 zh-tw 值（含 `X=>zh-tw:Y`），沒有 zh-tw 才退回 zh-hant 並標記；多值依 `/` 拆開、去掉《》；只留 2–10 個漢字。
- **作品清單**：Wikidata 上類別為動畫影集（Q63952888）或動畫電影（Q20650540）、有中文維基條目、sitelink 數最多的 `WORKS` = 400 部；加上年度清單「N年日本動畫列表」，年份從 `LAST_YEAR − YEARS + 1` 到 `LAST_YEAR`（常數 2026 與 7，即 2020–2026；不看系統日期，同一份快取任何一天重建都相同；要換年份時明確改常數）。年度清單只取「作品名」欄裡的連結（用 rowspan 補位對齊、語言轉換 `-{ }-` 只取 zh-tw 分支、紅連結不取），連結先用 `converttitles` 與 `redirects` 查實際標題，查不到的記在 `articles_missing`、不去抓。
- 建置在這些情況中止（頁面結構變了）：某個 wikitable 的標題列沒有「作品名」欄、某列欄數和標題列對不上（橫跨整列的註腳除外）、`colspan=0`、一頁取到的作品連結少於 `MIN_YEAR_WORKS`（100）。
- **作品標題**：作品條目的 zh-tw 顯示標題（去掉消歧義括號，只留 2–10 個漢字）也收進詞包。
- **手動詞**：`acg-manual.tsv`；`--a3 off`（開發用，重建 v0.4.0 詞包）時不併入 `A3_MANUAL`（獄門疆、虎杖悠仁）。

### 5.3 人名抽取

- 對每個作品條目與它連到的角色列表條目（標題符合 `LIST_RE`：…角色／人物列表、一覽），從「登場人物」類小標題（`CHAR_RE`，排除 `CHAR_NEG`）底下的 `dt`、`li`、表格第一欄、粗體段首與小標題本身取候選；名字取到第一個切斷字元（括號、冒號、頓號、「CV」、「聲優」等）為止；含空白、斜線的複合寫法不收。
- **含間隔號的名字**（`・·•･`，不含空白與斜線）：依間隔號拆成各部分，每一部分各自照單名規則過濾；原名的判斷看**全名**緊接的括號，通過就各部分都算通過；出處記全名；不收連起來的全名。
- **角色列表條目整頁算人物段落**：標題符合 `LIST_RE` 的條目，不要求小標題符合 `CHAR_RE`，但頁尾的參考、注釋、外部連結、相關條目等小節（`BACK_WORDS`）與導覽框、參考文獻框不算；作品條目照舊。這種條目會混進稱號，所以**只對 v0.4.0 詞包沒有的新詞**擋結尾是女王、王子、公主、國王、王妃、皇后、皇帝、殿下、大人、舞娘的字串（`TITLE_END`）；已出貨的詞不受影響。
- **嚴格過濾**（`strict_ok`）：2–10 個漢字；不在基底詞庫（含疊加層、變調層、名單層）；不是小標題；不含「的」「之母」這類關係詞組（`REL`）；不是角色、社團、職稱這類泛稱（`ROLE_END`、`ROLE_ANY`、`STOP`、`STOP_RE`）；名字（含間隔號時是全名）**緊接的括號**裡有原名：假名，或不在 `NOT_ORIGINAL`（CV、OVA、TV、PV、ED、OP…）的 2 個以上拉丁字母詞；括號裡第一個聲優標記（CV、聲優、配音、演員、飾演、「聲：」…）之後的文字不算證據。
- **同一個名字每一次出現都判斷**，有一次通過就收；`acg-sources.tsv` 記通過的那一次。
- 取捨：原名必須緊接在名字後面，比「摘要前 120 字任何地方」嚴，詞包因此少一些詞，但誤收的句子片段少很多。

### 5.4 讀音、順序與分數

- 讀音：`tools/readings.py`（`CHECK` 的保留並標記，`UNREADABLE` 的丟掉）。名字裡的「麗」讀 ㄌㄧˋ（`reading_overrides.tsv`，ㄌㄧˊ 只留給詞庫裡的詞如高麗）；虎 ㄏㄨˇ、拓 ㄊㄨㄛˋ、条 ㄊㄧㄠˊ、繆 ㄇㄧㄡˋ 同一種處理，**取代**舊讀音，不另外保留。
- 同讀音的順序（`ordered`）：處置列點名的詞照處置名次（每列保留的詞在前）；其次已出貨的詞（v0.4.0）在新詞前面；再來源數多的在前；同數依字串。已出貨的詞的來源數不算 v0.4.0 之後才有的出處（間隔號、段落外、後來補的手動詞），所以同讀音已出貨的詞之間的順序不隨來源擴充改變。
- 讀音上有已出貨的詞時，新詞的列另外減 `NEW_BEHIND_SHIPPED`（3.0），新詞只當候選、不搶已出貨的選字（處置列點名排第一的詞不扣）；光靠 `TIE` 不夠，因為模型對某些新詞的語料次數會讓它贏過分數較高的已出貨詞。`acg-yield.tsv` 的（讀音, 詞）再扣一次 3.0。

### 5.5 分級分數、強制詞與 `regrade`

- 分數 `g(w) = max(S_len, log10(c(w) / N) + B)`：`S_len` 是 §2.5 的字數分數，`c`、`N` 取自凍結次數表 `acg-counts.tsv`（檔頭兩行記模型 SHA-256 與 N；次數 0 的詞不寫，查不到就是 `S_len`），`B` = 0（出貨值，`--bonus` 可改）。次數表只有子命令 `freeze-counts [--expected-model SHA]` 會寫（要求 `data/lm` 的模型雜湊等於預期值），檔的 SHA-256 釘在 `COUNTS_SHA`；一般建置只讀這份表（雜湊不符或缺檔中止），不看 `data/lm` 是哪一版。同一讀音內第 i 列 = `min(g, 第 i−1 列 − TIE)`，所以分級只會整個讀音一起抬高或壓低，不改讀音內先後。
- 強制詞（第 4 欄 `acg-oov`）：處置讀音（`acg-collisions.tsv` 有指定第一名的讀音，加上 `acg-yield.tsv` 的讀音）上的詞包字串，以字串封閉；其中排第一、通語認得（次數 > 0）、而且封頂分數 `log10(c/N)` 不低於 `S_len − 0.5`（`KEEP_MARGIN`）的不強制，保留通語統計；它若在別的處置讀音上排在後面，照樣強制；強制列再壓到「沒強制的第一名封頂後的分數」之下。解碼端怎麼對待強制詞見 `docs/spec/decoder.md`。
- `python3 tools/build_acg_pack.py regrade --src DIR --out DIR [--bonus B] [--empty-counts]`：只重算分數，讀音、詞、來源標籤與順序不動；輸入必須是固定的未分級詞包（SHA-256 釘在 `REGRADE_SRC_SHA`；取自 git 歷史的 `REGRADE_SRC_COMMIT`），分級過的詞包會被拒絕再分級一次；`acg-sources.tsv` 的來源欄套 `SOURCE_FIXES`。出貨的 `acg-add.tsv` 就是這個子命令的輸出（目前 328 個字串、332 列是 `acg-oov`），`acg.json` 記 `regrade_bonus`。
- 驗證：`tools/test_build_acg_pack.py` 的 `PackOovRegrade`（公式與偏移、處置名次、已提交的詞包 = A4 來源的 regrade 結果、讓位清單的列都在詞包裡、偏移不在四組內就中止）、`PackOovV1`（強制詞集合、標籤、排序、已提交的詞包 = v1 輸出、328／332）、`PackOovGrading`；`tools/test_pack_oov.py` 與 `core/tests/pack_oov.rs` 逐位元組比對 Python 與 Rust 解碼器對強制詞的算法（`eval/golden/pack-oov/`）。

### 5.6 同音衝突與處置檔

- 衝突偵測用**規則模型**（固定的舊模型：`bigram.sjlm` SHA-256 `06768f29…`、`classes.sjc` `9e343d3e…`，兩個雜湊寫成 `RULE_LM_SHA`）而不是 `data/lm`，加 `--no-kn --no-names` 固定結果；`--rule-lm DIR`（預設 `build/rule-lm/`）指定資料夾，雜湊不符或缺檔就中止並印下載指令。理由：處置與保留的清單是相對於「詞包詞沒有語料次數」的模型訂出的，釘住後同一份快取重建得到相同的判斷。
- `acg-collisions.tsv`：讀音、保留的詞、排除的詞、理由。排除欄寫 `+詞` 表示兩個都留，`+` 後面是另一個留下的詞；排除的詞不收；一列同時保留並點名同一個詞是錯誤。處置只涵蓋它點名的詞，同讀音來了沒被點名的新詞，照樣列為未處置。
- 名次（`collision_rank`）：依列出現的先後，每列保留的詞在前，供排序與驗證共用；已處置的讀音，開了詞包之後的第一名必須是該讀音名次最前、還在詞包的那個詞（`first_named`）。已知例外 `FIRST_NAMED_EXCEPTIONS`：書面設定下 `ㄉㄨㄛ ㄌㄧˋ ㄙ` 的多利斯（解碼器用通語認得的「麗絲」拼出「多麗絲」）。
- `acg-keep-both.tsv`：兩種寫法都留、順序照出貨模型。建置最後檢查其中每個詞都在輸出裡，少一個就中止；一個衝突只有在詞包詞和開詞包後的第一名都是同一個讀音的 keep-both 詞時才豁免（也豁免 `first_named` 檢查）；開詞包後的第一名不是這個讀音的詞包詞時，詞包詞是 keep-both 詞也豁免。開詞包後的第一名是已出貨的詞也豁免（已出貨的排在新詞前面，由 `--recheck --base` 驗證）。
- `acg-kept-c.tsv`：讓詞包名字排第一的 (c) 詞，是 (c) 規則的例外（只給已出貨的詞；新詞若在出貨模型上擠下詞庫詞仍不收，用 `acg-collisions.tsv` 的處置列排除）。
- `acg-removed.tsv`：來源條目裡已經沒有、人工核准拿掉的已出貨詞；「v0.4.0 的詞不能少」的檢查不算它們，也不算 `acg-collisions.tsv` 理由欄以 `model-v6：` 開頭的列所排除的 (c) 詞。建置發現 v0.4.0 的詞少了（扣掉這兩類）就中止、不寫檔。
- 驗證（`--recheck`，不重建，用出貨的 `data/lm` 重驗 `--out` 裡的詞包；`--recheck-rule` 改用規則模型；`--recheck-packs acg,it` 指定開詞包解碼時的 `--packs`；`--base DIR --base-eval CLI` 另比基準詞包每一個（詞, 讀音）的第一名有沒有變，total 必須是 0 或只來自核准的拿掉、讀音覆寫與處置過的讀音）：報告 (a)(b)(c) 的組數、(c) 與 `acg-kept-c.tsv` 的差、未處置的 (a)(b)、`first_named` 失效清單（要空）、keep-both 讀音的第一名。

### 5.7 其他報告與檢查集

- 建置另外寫 `build/acg-pack/`：`collisions.txt`、`unreadable.txt`、`reference.txt`（參考名單）、`new-words.tsv`（比 v0.4.0 多的詞與來源種類）、`interpunct.txt`（檢查集）。
- 檢查集 `eval/dev/acg/interpunct.txt`：從間隔號與角色列表整頁新增的詞（不在 v0.4.0、讀音不是 `CHECK` 的）依作品分層抽 `CHECK_N` = 40 個（固定種子 `CHECK_SEED` = 20261010，作品洗牌後輪流各抽一個），加 `CHECK_FIXED` 五列（洛琪希、塞妮絲、瑞傑路德、獄門疆、虎杖悠仁；「虎杖悠仁」用正確讀音）。
- 其他評測集合：`eval/dev/acg/` 的 `works`、`characters`、`character-parts`、`recent`（授權見 `LICENSES/data.md`）。動漫集不用來調任何參數。

### 5.8 驗證

- 假的 API 回應與假的解碼器（`make test-python`，`tools/test_build_acg_pack.py`；CI 的「ACG pack builder」步驟也跑，並建置評測 CLI 做衝突偵測）：規則解析、年度清單解析（作品名欄、rowspan、colspan、註腳、zh-tw 分支、錯位或太少時中止）、嚴格過濾、所有出現都判斷、去重（已提交的詞包與基底、疊加層、變調層、名單層沒有交集）、排序、衝突 (a)(b)(c) 的偵測與 (c) 重跑、處置只涵蓋點名的詞、`first_named`、`acg-exclude.tsv` 與處置套用到已提交的詞包、間隔號與角色列表整頁、`TITLE_END` 只擋新詞、v0.4.0 詞表與規則模型雜湊不符時中止、keep-both、同一份快取建兩次逐位元組相同、離線重播 `missingtitle`、解碼用滿核心（`DecodeThreads`）、已提交的清單檔（`test_the_committed_lists`）。
- 詞包關掉時的選字結果與沒有詞包逐位元組相同（`core/tests/engine_pack.rs`）；開著時一般集合不退步由評測規格的出貨標準把關（`docs/spec/evaluation.md`）。

## 6. 資訊與 AI 詞包

`data/packs/it-add.tsv`、`it-sources.tsv`、`it.json`；`python3 tools/build_it_pack.py`。詞包預設開。

### 6.1 來源與授權

| 來源 | 授權 | 用法 |
|---|---|---|
| 國家教育研究院「電子計算機學術名詞」（data.gov.tw 資料集 15416） | 政府資料開放授權條款第 1 版（標示提供機關） | 「中文名稱」欄，過維基詞頻門檻 |
| 國家教育研究院「兩岸對照名詞—計算機學術名詞」（15275） | 同上 | 「中文名稱」照收；「中國大陸譯名」欄（轉 zh-hant）當大陸寫法清單 |
| 中文維基 `Module:CGroup/IT`、`Module:CGroup/Electronics` | CC BY-SA 4.0 | zh-tw 值照收；zh-cn 值（轉 zh-hant）當大陸寫法清單 |
| 中文維基 AI 分類的條目標題（8 個根分類：人工智能、机器学习、大型语言模型、自然語言處理、深度学习、人工神经网络、生成式人工智能、计算机视觉，往下兩層，每個分類的 ns0 成員都算） | CC BY-SA 4.0 | zh-tw 標題，過頻率門檻與專有名詞過濾 |
| `it-manual.tsv` | CC0 | 手寫清單：詞、類別（AI／開發／雲端與維運／介面與裝置）、備註，選填第四欄另外的讀音；照收 |

只收詞（字串）與讀音，不收定義或內文；整份詞包以 CC BY-SA 4.0 散布。下載的檔案記網址、大小、SHA-256、抓取日期，寫進 `it.json`。

### 6.2 建置規則

候選字串：15416 與 15275 每一列的「中文名稱」先去掉全形與半形括號裡的部分，再依「；」「，」「、」切開，留下 2–10 個漢字的字串；AI 標題取 zh-tw 轉換後 2–10 個漢字的標題。計數工具與建置工具共用同一個切法（`it_sources.split_names`、`ai_title`）。每個（詞, 來源種類）依序過：

1. **子字串標記**（只用在 15416 與 AI 標題）：含 網絡、信息、算法、軟件、硬件、數據庫、默認、視頻、鼠標、內存、緩存、服務器、文件夾、激活、兼容 任一個就不收；比對前先遮掉台灣寫法的「演算法」。
2. **大陸寫法**（只擋加進來，不刪既有詞）：字串在大陸寫法清單裡就不收，**除非**它也出現在任何台灣來源（手寫清單、CGroup zh-tw、15275、15416）。
3. **頻率門檻**（只用在 15416 與 AI 標題）：在中文維基全部 ns0 條目的不重疊出現次數 ≥ `MIN_COUNT`（50）才收，49 次不收；手寫清單、15275、CGroup 照收。次數由 `tools/it_wiki_counts.py` 數：正文取 `build_counts.article_sentences`，用 MediaWiki 的 zh-tw 轉換、詞組表給恆等函式（不套台灣用詞轉換，免得台灣用詞被改寫），每個句子的 `str.count` 加總；輸入 dump 的 SHA-256 釘在 `DUMP_SHA`；輸出 `字串<TAB>次數`（依字串排序，含 0 次），第一行 `# dump_sha256=… candidates_sha256=… tool_commit=…`；結果與 `--procs` 無關。建置工具用 `--counts` 讀它，候選字串的 SHA-256 必須等於檔頭的 `candidates_sha256`（候選變了而計數檔沒重做就中止）。
4. **專有名詞**（只用在 AI 標題）：Wikidata P31（含 P279* 子類）屬於人（Q5）、組織（Q43229）、公司（Q4830453）、作品（Q17537576）就不收。查詢只對次數 ≥ `PROPER_LOOKUP_MIN`（20）的標題做（分批的網址隨名單變，所以 `--min-count` 不可低於 20，否則要重新抓取）。

一個詞只要有一個來源活下來就收。之後：

- 去重：不得在詞庫（基底、疊加層、變調層、名單層）或動漫詞包任何讀音下；`it-collisions.tsv` 排除的詞不收；讀音用 `readings.py`，`it-readings.tsv` 的人工確認讀音取代它的結果（工具讀不出來的詞也收，也不再是 `CHECK`）；
- 「已經打得對」的詞不收：用出貨的 `data/lm` 解碼（只開動漫詞包），聊天與書面的第一名**都**已經是這個詞；
- 同音衝突（§4.5）：解碼只開動漫詞包的「不開」與動漫加資訊的「開」；(c) 包括資訊詞擠掉動漫詞包的詞（既有的動漫詞勝，資訊詞丟掉）；參考名單 = 所有來源在任何過濾之前的 2–10 漢字字串（台灣寫法；大陸寫法清單本身不算）；(a)(b) 由 `it-collisions.tsv` 處置（格式同 `acg-collisions.tsv`），未處置的必須是 0。
- 分數 = 同字數的 `length_scores` + `BONUS`（3.0），標籤 `it`；含「一」「不」附變調列。仍受 §1.4 封頂：模型看過的詞封頂在語料頻率（加的 3 被封頂吞掉），沒看過的詞扣 1.0，所以仍比沒加分高 2。
- 輸出：`it-add.tsv`、`it-sources.tsv`（每個詞的來源種類、頻率與 `CHECK` 備註）、`it.json`（版號 `<快取裡最晚的抓取日期>-<it-add.tsv 雜湊前 8 碼>`、各來源與各規則的數量與丟掉的數量、計數檔與手寫清單的雜湊、來源檔的大小與雜湊）；報告放 `build/it-pack/`。

### 6.3 抓取（`tools/it_sources.py`）

- 主機只有 `data.gov.tw`、`opendata.naer.edu.tw`、`zh.wikipedia.org/w/api.php`、`query.wikidata.org`；其他主機拒絕。每秒 ≤ 1 個請求、User-Agent、維基加 `maxlag=5`；5xx、429 與 maxlag 退避重試（最多 6 次）；每個請求一個快取檔（檔名是網址的 SHA-256），`manifest.json` 記每個下載的網址、大小、SHA-256、抓取日期；`--offline` 缺檔中止。
- `python3 tools/it_sources.py fetch`（連網）／`candidates --offline --out FILE`（候選字串，排序去重，一行一個）；`python3 tools/build_it_pack.py fetch --counts FILE`（補齊建置要的快取）與 `build --offline --counts FILE`（`build` 只接受 `--offline`）。建置前核對出貨模型的五個檔（`bigram.sjlm`、`classes.sjc`、`kn.sjkn`、`trigram.sjt3`、`trigram-chat.sjt3`）的雜湊等於 `data/*.sha256`。

### 6.4 檢查集與驗證

- 檢查集 `eval/dev/it/words.txt`：從詞包依來源比例（一個詞歸 `KINDS` 裡第一個有它的來源；最大餘數法分配名額）抽 `CHECK_N` = 60 個，固定種子 `SEED` = 20261024，一列一詞、沒有前文，讀音 `CHECK` 的詞不抽。
- 測試（`make test-python`）：`tools/test_build_it_pack.py`（`SourceRules`：標記與「演算法」遮罩、頻率 50／49、計數檔缺字串中止、大陸清單與放行、專有名詞只對 AI 標題；`Pipeline`：去重、固定讀音、已經打對的丟掉、參考名單與 (b)、(c)、資訊詞擠掉動漫詞、同音在處置前是衝突、排除、變調列與分數、檢查集比例與種子；`Offline`：候選字串與計數工具同切法、線上抓取後離線建兩次逐位元組相同、離線缺快取中止、`write_all` 不碰 `acg.json`、出貨模型檢查含聊天 trigram）、`tools/test_it_sources.py`、`tools/test_it_wiki_counts.py`。CI 的「IT pack builder」步驟不連網、不需要評測 CLI。
- 這一版：詞包約 1 千個詞（1,017 個詞、1,021 列）。

## 7. 經典台詞詞包

`data/packs/quotes-manual.tsv`（清單，CC0）→ `quotes-add.tsv`、`quotes.json`；`python3 tools/build_quote_pack.py [--list FILE] [--out DIR] [--report DIR] [--acg-dir DIR]`（`--acg-dir` 要同時有 `acg-add.tsv` 與 `it-add.tsv`）。**預設關**，在設定視窗開。

### 7.1 清單

- 欄位（tab 分隔，`#` 開頭是註解）：台詞原文（含標點）、出處、年份、核對來源、收錄人、選填的讀音（各分句的注音以 `｜` 分隔，分句之間可留空白交給讀音工具）。少於五欄、原文空白、讀音的分句數多於原文的分句數、某個 2–10 字漢字分句的讀音音節數對不上字數，都中止並指出行號。
- **每句都要核對原文用字才收**：核對來源欄空白或寫「待核對」（`UNCHECKED`）的列不收，列在報告裡。只收公開影視作品的台詞。

### 7.2 建置、引擎與 App

- **分句**：在標點與空白（含全形與半形的引號、刪節號、破折號）處切開；每個分句 2–10 個漢字（`MIN_LEN`、`MAX_LEN`）才收，各成一個詞條。11 字以上、1 字、含非漢字（英文、數字、符號）的分句不收，列在報告（`skipped`）。上限 10 字的理由：分數規則（`length_scores`）只涵蓋 2–10 字，而且引擎每個位置試的詞長取決於詞庫最長的詞。漢字包含基本區、擴充 A、相容區、〇與擴充 B 以後。
- **讀音**：`readings.py`，清單第 6 欄有就以它為準；兩行給同一個分句不同讀音中止；`CHECK` 的列在報告；讀不出的不收。含「一」「不」的分句附變調列，衝突檢查與報告的解碼都含變調讀音。
- **分數**：同字數的 `length_scores`（不加分），標籤 `quotes`；同讀音的順序：出處多的在前、同數依字串，第 k 位減 k × `TIE`。照 §1.4 封頂。
- **去重**：分句已經以同一個字串出現在基底、`overlay-add.tsv`、`sandhi-add.tsv`、`names-add.tsv`、動漫詞包或資訊詞包的任何讀音下，就不收（`skipped` 的 `in the lexicon`）。
- **衝突**只有一種處理：開了詞包後某個讀音的第一名由既有詞變成台詞分句，既有詞勝，那個分句不收，寫進報告（重跑到沒有為止，上限 5 輪）。台詞分句之間的同音、開了詞包後第一名不是分句自己的，只報告。解碼設定：出貨的 `data/lm`（`bigram.sjlm`、`classes.sjc`、`kn.sjkn`、`trigram.sjt3`、`trigram-chat.sjt3` 五個檔，雜湊讀 `data/*.sha256`，開始前核對），「不開」帶動漫與資訊詞包（`--packs acg,it`），「開」再加台詞（`--packs acg,quotes,it`），聊天與書面都做。只開台詞、關掉另外兩個詞包的組合不另外解碼。
- **輸出**：`quotes-add.tsv`、`quotes.json`（版號 = `quotes-add.tsv` 雜湊前 8 碼、清單的 SHA-256、各項個數、`skipped` 統計、`first_without_quote_pack`、解碼用的五個模型檔與動漫、資訊詞包檔的雜湊）；報告 `build/quote-pack/report.txt`。同一份輸入建兩次逐位元組相同，與清單的行序無關。
- **引擎**：位元 2、檔案 `quotes-add.tsv`（§4.1）；載入、封頂、缺檔行為同其他詞包。**App**：偏好鍵 `quotePack`，沒設過當作**關**（§4.2）；打包與清單檔核對見 §4.4；自測不含這個詞包。
- 授權：清單與詞包 CC0（每列記出處與核對來源）。

### 7.3 驗證

- `tools/test_build_quote_pack.py`：`Rules`（未核對列排除並報告、沒有核對列時不解碼、分句在標點處切開且上限 10 字、非漢字與單字不收、已在詞庫／動漫詞包／名單層的不收、「一」「不」變調列、分數規則、讀音欄優先且檢查音節數、同分句兩行合併為一個詞、(c) 丟掉並報告、台詞間同音只報告、第一名不是自己的只列出、臺灣刪節號與全形標點與擴充漢字、不收的分句附讀音不會中止、兩行讀音不同中止、輸出可重現）、`RealCli`（用真的評測 CLI 確認詞包使分句成為第一名；需要 `SHANJIE_MOEDICT` 與 `SHANJIE_HETERO`，沒設就跳過）、`Stale`（`quotes.json` 記的模型與詞包雜湊要等於 `data/*.sha256` 和現有詞包檔，不符就失敗並提示重建指令 `python3 tools/build_quote_pack.py`；缺雜湊檔報清楚的錯）。
- 關掉時的選字結果與沒有詞包逐位元組相同：`core/tests/engine_pack.rs` 的 `the_quote_bit_reads_quotes_add_tsv_only`；`ShellTests` 的 `testWithBothPacksOnABrokenFileKeepsTheOtherPack`。
- 這一版：208 句核對過的台詞切成 278 個分句、311 列（含變調列）。

## 8. 美妝與保養詞包

`data/packs/beauty-add.tsv`、`beauty-sources.tsv`、`beauty.json`；`python3 tools/build_meme_pack.py --pack beauty`（三段，§8.2）。詞包預設開。

### 8.1 來源與授權

| 代號 | 內容 | 用法 |
|---|---|---|
| `S` | 三家藥妝通路的商品名與商品說明 | 只當候選（挖出的字串），不存原文、不散布 |
| `B` | 27 個保養、彩妝品牌的台灣官網頁面 | 同上 |
| `G` | 11 個美妝、時尚媒體與部落格的文章 | 同上 |
| `A` | 論壇 A（台灣公開論壇的文章與推文）的五個保養、彩妝、香水、美髮、醫美討論區 | 挖詞當候選，也算進用量 |
| `brandlist` | 藥妝通路品牌清單裡的中文名 | 只當候選 |

- 公開的地方只寫代號，網站、通路、媒體的名稱與網域不進 repo；`beauty-sources.tsv` 的來源欄只記類別代號，沒有一列能對到單一網站。品牌的中文名是詞，照 §8.2 的規則收不收。
- 所有來源只取字串；收不收只看 §8.2 的規則（論壇 A 的用量、現在打不對、讓不讓第一名）。
- 詞包檔是 CC0（名稱清單與用量判定）；沒有 CC BY-SA 的來源，所以署名檔不列它。
- 挖詞、計數、分類的腳本與候選檔不隨 repo 散布（§10）。

### 8.2 建置規則

`tools/build_meme_pack.py --pack meme|beauty`（預設 `meme`）。`beauty` 時標籤 `beauty`，所有檔名的 `meme` 換成 `beauty`（`beauty-candidates.txt`、`beauty-x-strings.txt`、`beauty-collisions.tsv`、`beauty-readings.tsv`、工作目錄 `build/beauty-pack/` 等），檢查集種子 20261108，自己的 `beauty-add.tsv` 不算進去重，`beauty.json` 的 `candidate_files` 只記檔名（不記路徑）。`--pack beauty` 的輸出不受迷因詞包影響（`tools/test_build_meme_pack.py`、`tools/test_build_beauty_pack.py`）；`--pack meme` 見 §9.2。

1. `--emit-candidates`：讀候選檔（一類一個檔，`字串<TAB>來源`，來源欄省略時用檔名去掉副檔名），含 ASCII 英數的整串不收，其餘在非漢字處切開、只收 2–10 個漢字的段，寫出要計數的候選。已在詞庫與其他詞包任何讀音下的字串不收（§4.5 的去重）。
2. `--emit-x --counts C1`：第一次計數（每個字串的文章數與討論區數）過門檻：討論區 ≥ 3、文章 ≥ 10（`MIN_BOARDS`、`MIN_ARTICLES`）。每個過門檻候選的讀音用出貨的解碼層（見下）在聊天與書面各解一次，記下聊天的第一名 X 與書面的第一名，寫出 X 的字串（`beauty-x-strings.txt`）供第二次計數。
3. `--counts C1 C2`（建詞包）：
   - **讓不讓第一名**：X 就是候選 → 已經打得對，不收；X 的文章數 ≥ 候選 → 不收；X 的文章數 < 候選 → 收。同讀音兩個候選只留文章數多的。
   - **已經打得對**：聊天與書面的第一名都已是這個詞，不收（同資訊詞包，§6.2）。
   - **解碼層**：評測 CLI 的 n-gram 層加名單層與 `--packs acg,it`，沒有會意與常談（迷因詞包開著的出貨預設不在這一組裡，美妝的詞包不看迷因詞包；迷因詞包的建置另用 `acg,it,beauty`，§9.2）；完整的出貨設定由 §8.4 的量測守門。
   - **剔除**：`--exclude`（預設 `data/packs/beauty-exclude.tsv`，`字串<TAB>類別`，工具只讀第一欄；預設檔不存在時是空集合，明給的檔一定要存在）與 `--exclude-private`（含網站、通路、媒體名稱的剔除字串，放在 repo 外；`beauty.json` 只記它的 SHA-256 與筆數）。剔除和衝突處置檔在同一處生效（判讓不讓之前），所以被剔除的詞不擋住同讀音的其他候選；剔除的字串也不進 (b) 的參考名單。
   - 兩份計數檔的頁面目錄雜湊（`tree_sha256`）要相同，第二份的 `input_sha256` 要等於 `beauty-x-strings.txt` 的 SHA-256。
   - **讀音**：`tools/readings.py`；`beauty-readings.tsv`（`詞<TAB>讀音`）取代它的結果。
   - **分數**：同字數的 `length_scores` 加 `BONUS`（3.0），標籤 `beauty`，照 §1.4 封頂。
   - **衝突**（§4.5）：(b) 的參考名單是過了用量門檻、不在詞庫與其他詞包的候選（剔除的不算）；某個詞在聊天、主要讀音上擠下的第一名，若就是這一段為這個讀音比過文章數的那個 X，不算 (b)，也不算未處置的 (c)（其他讀音與書面照舊列）。(a)(b) 由 `beauty-collisions.tsv`（格式同 `acg-collisions.tsv`）處置，`beauty.json` 的 `unresolved_collision_readings` 必須是 0。
   - **輸出**：`beauty-add.tsv`、`beauty-sources.tsv`（詞、來源代號、論壇 A 的文章數與討論區數、備註）、`beauty.json`（版號＝`beauty-add.tsv` 雜湊前 8 碼、各來源筆數、候選檔的檔名與雜湊、兩個計數檔的檔頭、剔除檔的雜湊與筆數、各條規則擋下的數量）。同一份輸入建兩次逐位元組相同。

建詞包之前，收進來的詞先分類、再人工審（這兩步不在工具裡）：

- **美妝專屬度**＝在美妝五區的文章數 ÷ 在整棵頁面樹的文章數；< 0.3 直接剔除。
- 專屬度 ≥ 0.3 的詞交 Claude Sonnet 分成六類（美妝詞、一般詞組、片段、網站或通路名、人名、其他），每批 ≤ 500 個、同一份固定提示；每批輸出都核對每個輸入剛好一行、順序相同、類別在六類之內，不符就整批重送。品牌名與產品系列名算美妝詞。
- 人工審看「美妝詞」與來源含 `brandlist` 的詞，剔除網站樣板與導購用語、斷詞片段、跟美妝無關的一般詞組與藝人名、醫美療程與藥品名、只差異體字的寫法（含異體排序表降權的那一側）、大陸寫法、錯字。
- 剔除的字串先對網站、通路、媒體名稱的清單做子字串比對（清單在 repo 外），有命中的放私人剔除檔，其餘寫進 `beauty-exclude.tsv`。
- 剔除後重跑第三段；重跑多出沒審過的詞就照同樣兩步再審，直到重跑不再多出新詞。

### 8.3 引擎與 App

- 位元 16（`SHANJIE_PACK_BEAUTY`／`PACK_BEAUTY`），檔案 `beauty-add.tsv`，接在迷因詞包之後讀（`PACK_FILES` 的最後一個）；CLI `--packs beauty`。照 §1.4 封頂（不在 `PACK_OOV` 裡）。
- App：偏好鍵 `beautyPack`，沒設過當作開（§4.2）；設定視窗「詞包」區塊在「資訊與 AI 詞」之後加「美妝與保養詞」（迷因詞包之後又加在它下面，§9.3）（SwiftUI `Toggle`，和其他詞包開關同一種原生元件）；選單「已開啟的詞包」那一段照設定順序列出。
- 打包：`beauty-add.tsv`、`beauty-sources.tsv`、`beauty.json` 進 `Resources/packs/`，建置輸入（`beauty-exclude.tsv`、`beauty-collisions.tsv`、`beauty-readings.tsv`）不進；`check-app.sh` 核對 `beauty.json`（§4.4）。
- 自測：帶動漫、資訊與美妝詞包的引擎要求 `ㄇㄟˊ ㄘㄞˇ ㄍㄠ` 得到 `眉彩膏`（不帶美妝詞包時第一名是別的寫法），所以美妝詞包缺檔、壞檔或沒生效都失敗。
- 評測重播 `shanjie-replay` 不給 `--packs` 時用 App 的預設 `acg,it,meme,beauty`（`docs/spec/evaluation.md` §4）。

### 8.4 檢查集與驗證

- 檢查集 `eval/dev/beauty/words.txt`：從詞包用種子 20261108 抽 60 個（讀音 `CHECK` 的詞不抽），一列一詞、沒有前文，讀音照建置用的。門檻：會意關（n-gram 第一名）聊天至少 75% 正確；書面只守門（0 改壞）。
- 出貨前照出貨標準（`docs/spec/evaluation.md`）在三種強度都量過；詞包的回歸集合（`eval/dev/` 的動漫、名單、資訊、網路用語、輕聲）聊天與書面 0 改壞；關掉時 dev302、打字測驗、錯字回報的 rowstats 和沒有這個詞包逐位元組相同。
- 測試（`make test-python`）：`tools/test_build_beauty_pack.py`（`BeautyPipeline`：檔名、標籤與種子、`candidate_files` 只記檔名、兩個資料夾有同名候選檔就停、`--exclude` 只讀第一欄、預設讀 `beauty-exclude.tsv`、`--exclude-private` 只留雜湊與筆數；`PackWords`：自己的 `beauty-add.tsv` 不算進去重；`Exclude`：被剔除的同讀音贏家不擋輸的那個、剔除的字串不在 (b) 的參考名單；`ReferenceList`：比過文章數的 X 不算 (b)、另一個過門檻的候選被搶走照樣列出、判過的 (c) 不算未處置、沒過門檻的候選不在名單裡）。引擎與 App 的測試見 §4.1–§4.4。
- 這一版：539 個詞、539 列（沒有變調列），未處置衝突 0。

## 9. 迷因與實況用語詞包

`data/packs/meme-add.tsv`、`meme-sources.tsv`、`meme.json`；`python3 tools/build_meme_pack.py`（預設 `--pack meme`，三段，§8.2）。詞包預設開。

### 9.1 內容、來源與授權

- 詞包收**迷因、口頭禪與實況用語的整個詞條**，分兩類（`meme-sources.tsv` 的 `class` 欄、`meme.json` 的 `allow.shipped`）：
  - `meme`：迷因、口頭禪、有梗的固定說法與固定的玩笑詞組；
  - `offensive`：政治綽號與政治梗、成人與毒品梗、粗口與貶義詞。
- 收哪些詞由**審定清單** `data/packs/meme-allow.tsv`（建置輸入，不隨 App 出貨）決定：`詞<TAB>類別`，類別是 `meme` 或 `offensive`，照位元組順序排，`#` 開頭與空行略過。不在清單裡的候選一律不收。
- 不在清單裡的詞與去處：不帶梗的論壇用語與口語進主詞庫（`slang-add.tsv`，§2.10）；人名屬於名單層（§3）；動漫與遊戲的作品名、角色與術語屬於動漫詞包的範圍（§5）；一般詞組與專有名詞不收。已在主詞庫的迷因不搬進詞包。
- **現在就打得對的詞也收**：聊天與書面的第一名都已經是這個詞的，照樣收成詞條。詞包只加詞條，不壓制任何字串：關掉詞包時，逐字拼得出來的詞照樣由較短的詞條組出來。
- 候選來自六類來源，`meme-sources.tsv` 的來源欄記代號：`mined`（從語料挖出）、`fandom`（Fandom 的迷因 Wiki 頁面標題）、`pttpedia`（Fandom 上 PTT 鄉民百科的分類標題）、`muse`（模型建議）、`discord`（社群的公開用語）、`siteA`（迷因網站 A：沒有開放授權的迷因詞條網站，只當候選字串，不取定義）。
- 詞包檔以 CC BY-SA 4.0 散布：標題來自 Fandom 上的迷因 Wiki 與 PTT 鄉民百科（CC BY-SA 3.0，依「或更新版本」改用 4.0）與中文維基（CC BY-SA 4.0），App 的署名檔 `LICENSES/CC-BY-SA-4.0-attribution.txt` 有 `packs/meme-add.tsv` 一段，`LICENSES/data.md` 有一列（審定清單是建置輸入，列在同一列）。沒有開放授權的來源（迷因網站 A、模型建議）只當候選字串，公開處只寫代號與類型；論壇只寫「論壇 A」。
- 挖詞、計數、分類的腳本與候選檔不隨 repo 散布（§10）；`meme.json` 的 `candidate_files` 只記候選檔的檔名與雜湊，`allow` 記清單的檔名、SHA-256、筆數與各類別收進的詞數。

### 9.2 建置規則

照 §8.2（同一支工具、同樣的三段），差別：

- **另外的讀音**（`data/packs/meme-extra-readings.tsv`，建置輸入，不隨 App 出貨）：`詞<TAB>讀音（空白分隔）`，一列一個另外的讀音，`#` 開頭與空行略過。給句尾語助詞有兩種打法的詞（啦 `ㄌㄚ`／`ㄌㄚ˙`、嗎 `ㄇㄚ`／`ㄇㄚ˙`、耶 `ㄧㄝˊ`／`ㄧㄝ`）與疊字有兩種打法的詞用：主要讀音照舊，這裡的每個讀音在 `meme-add.tsv` 多一列（含它的變調列），分數和主要讀音的列相同，兩種都打得到。只在最後一段用（`--extra-readings FILE`，預設這個檔，不存在就沒有；`--pack beauty` 給這個旗標就停）；解碼表只看主要讀音。同音衝突 (a)(b)(c) 在另外的讀音上照樣偵測。列裡的詞沒有收進詞包（含因為另外的讀音被 (c) 丟掉，訊息寫出那個讀音與被擠下的詞）、音節數不等於字數、和主要讀音相同、它或它的「一」「不」變調列和主要讀音或同一個詞的另一個另外讀音寫出的列重複、同一個詞重複同一個讀音，都以非 0 結束、不寫 `data/packs`；檔頭的 BOM 略過。`words-all.txt` 在原本的列之後接上這些讀音各一列（確認過的讀音，所以主要讀音標 `CHECK` 的詞也列）；`meme.json` 多記 `extra_readings`（列數）。
- **審定清單只在最後一段套用**：`--emit-x` 照舊對所有過用量門檻的候選解碼（輸出不看清單，只檢查清單檔存在、能解析），所以解碼表與清單無關；最後一段在核對解碼表之後、判規則之前拿掉不在清單裡的候選（`meme.json` 的 `stages.not_allowed`）。這些候選不擋同讀音的清單詞，也不在 (b) 的參考名單裡。`--allow FILE` 預設 `data/packs/meme-allow.tsv`；`--pack beauty` 不收這個旗標；別的類別、缺類別欄、重複的詞（NFKC 之後比）都點名行號後停止。
- **現在就打得對的清單詞照收**（§6.2 的「已經打得對」只對 `--pack beauty` 生效）；「讓不讓第一名」（X 是更常見的寫法就不收）與 (c) 不變。
- **同讀音**：處置檔 `meme-collisions.tsv` 有 `+` 列點名的兩個詞都留（詞包詞在前）；沒有 `+` 列只留文章數多的。處置檔只放同讀音的處置，要不收的詞不放進審定清單即可。
- 解碼用的詞包是 `acg,it,beauty`（迷因以外預設開的詞包；經典台詞詞包預設關，`quotes-add.tsv` 在資料夾裡也不帶），所以建置時要有 `beauty-add.tsv`；美妝詞包建置仍用 `acg,it`。
- 門檻：討論區 ≥ 3、文章 ≥ 10；種子 20261025；少於 10 個詞就停（`MIN_WORDS`）。
- 衝突偵測「開」的那次，預設把詞包的列接在 `it-add.tsv` 後面（近似）；`--native-pack`（只有 `--pack meme`）改成真的 `--packs acg,it,meme,beauty`，每次建好詞包後要用它重驗一次，三個輸出檔與報告和沒加旗標的結果逐位元組相同、`unresolved_collision_readings` 是 0。
- 輸出列的先後：先放聊天沒有詞包時打不對的詞，再放本來就對的，最後放處置檔 `+` 的同讀音列（先後不影響分數）；`meme.json` 的 `candidate_files` 只記檔名，兩個同名候選檔就停。
- 報告（`build/meme-pack/`）：`allow-missing.tsv`（清單裡每個沒進 `meme-add.tsv` 的詞一列，附原因與細節；收進的詞數加這份表的列數要等於清單筆數，對不上就以非 0 結束）、`words-all.txt`（§9.4）。`--pack meme` 不收 `--exclude` 與 `--exclude-private`（給了就拒絕），審定清單是唯一的剔除路徑。

### 9.3 引擎與 App

- 位元 8（`SHANJIE_PACK_MEME`／`PACK_MEME`），檔案 `meme-add.tsv`，在 `PACK_FILES` 裡排在資訊詞包之後、美妝詞包之前；CLI `--packs meme`。照 §1.4 封頂（不在 `PACK_OOV` 裡）。
- App：偏好鍵 `memePack`，沒設過當作開（§4.2）；設定視窗「詞包」區塊在「美妝與保養詞」之後加「迷因與實況用語」（SwiftUI `Toggle`，和其他詞包開關同一種原生元件）；選單「已開啟的詞包」那一段照設定順序列出（動漫、資訊、美妝、迷因，經典台詞開著時在動漫之後）；選單沒有開關。
- 打包：`meme-add.tsv`、`meme-sources.tsv`、`meme.json` 進 `Resources/packs/`，建置輸入 `meme-collisions.tsv`、`meme-allow.tsv`、`meme-extra-readings.tsv` 不進；`check-app.sh` 核對 `meme.json` 與署名檔的 `packs/meme-add.tsv` 一段（§4.4）。
- 自測：帶動漫、資訊、迷因與美妝詞包的引擎要求 `ㄎㄜˇ ㄧˇ ㄙㄜˋ ㄙㄜˋ` 得到 `可以色色`（`meme-add.tsv` 的第一列；不帶迷因詞包時聊天的第一名是 `可以瑟瑟`），所以迷因詞包缺檔、壞檔或沒生效都失敗。
- 評測重播 `shanjie-replay` 不給 `--packs` 時用 App 的預設 `acg,it,meme,beauty`，遮罩 29（`docs/spec/evaluation.md` §4）。Windows 輸入法的常駐程序不載入迷因詞包（遮罩仍是 `1 | 4`）。
- 關掉詞包：不載入這些詞條，選字、候選窗、預測列和沒有這個詞包檔的引擎逐位元組相同。詞包不壓制字串：逐字或用較短的詞條拼得出來的詞，關掉後照樣打得出來；核心沒有壓制機制。

### 9.4 檢查集與驗證

- 檢查集（`eval/dev/meme/`）：
  - `words.txt`：建置輸出的 `words.txt`（種子 20261025，讀音 `CHECK` 的詞不抽，抽 60 個，不到就全收），一列一詞、沒有前文，讀音照建置用的；
  - `words-all.txt`：建置輸出的同名檔，詞包全部的詞（讀音 `CHECK` 的除外），格式同上，照 `meme-add.tsv` 的順序、每個詞取第一次出現的列；之後接另外的讀音各一列（主要讀音標 `CHECK` 的詞也列）；
  - `embedded.txt`：`tools/gen_meme_embedded.py` 由 `words-all.txt` 產生的嵌入句，每個詞前面接三個固定開頭（他說、真的是、我覺得）、後面接兩個固定結尾（了、吧），讀音寫在工具裡；同一份輸入產生兩次逐位元組相同，`make test-python` 核對已提交的檔和重新產生的相同。
- 驗證（出貨前，詞包預設開，所以都對現在出貨的預設比；量測腳本、臂與強度見 `docs/spec/evaluation.md`）：
  1. 關掉時與沒有詞包相同：dev302、打字測驗、錯字回報，聊天與書面，rowstats 逐位元組相同。
  2. 出貨標準四條（`docs/spec/evaluation.md`）在三種強度各自過；只加詞的詞包，第 4 條在錯字回報改壞 0 時算過。
  3. 回歸集合（`eval/dev/` 的動漫、名單、資訊、網路用語、輕聲、美妝檢查集）聊天與書面、三種強度 0 改壞；唯一事先列出的例外是網路用語檢查集裡正解含「啦幹」的那一列被詞包詞切開（只在「啦幹」在詞包裡時適用）。
  4. 檢查集（會意關）：(a) `words.txt` 裡不開詞包時聊天打不對的列，開著時聊天第一名正確至少 75%（這類列不到 10 列時只報、不設門檻）；(b) `words-all.txt` 開著時聊天與書面的第一名正確都至少 95%，不開時對、開了變錯的列是 0，沒對的逐列寫原因；(c) `words-all.txt` 裡開與不開第一名都對的列，用 `--dump` 的分數算第一名減第二名（只有一個候選的列不算），開著時的中位數要大於不開時，聊天、書面各自成立，兩個中位數記進研究紀錄。
  5. 嵌入句（會意關與標準檔，聊天與書面）：會意關時逐字比對，開了變錯的列是 0；標準檔用 rowstats 的對錯（寬鬆對照），開了變錯的列是 0，修好的列數在聊天、書面都大於 0。這個集合是工具造的句子，只確認方向，不當出貨標準的證據。
  6. 預測列：`core/tests/engine_pack.rs` 的 `the_meme_pack_offers_whole_words_in_the_prediction_row`（讀真的 `data/packs` 與 `words-all.txt`，不是夾具）：取三個音節以上的詞照檔案順序的前 30 個，各打到剩最後一個音節為止，聊天設定、沒有前文、預測開著，開著詞包時被動預測列含整個詞的詞數嚴格多於不開時，而且大於 0；測試印出兩個數字與沒出現的詞。
  7. 記憶體與延遲（Mac 閒置窗）：尖峰 RSS 對 `acg,it,beauty` 增加不超過 40 MiB；`PACKS=acg,it,meme,beauty` 的 `timing_typing76_keys` p95 不超過 16 ms。
- 測試（`make test-python`）：`tools/test_build_meme_pack.py`（`MIN_WORDS`、`candidate_files` 只記檔名、同名就停、列的先後、解碼的詞包組合、`--native-pack`、審定清單的各項規則與 `allow-missing.tsv`、`words-all.txt`、另外的讀音（含另外讀音上的 (a)(b)(c)、和變調列重複、BOM））、`tools/gen_meme_embedded.py` 的重建核對。引擎與 App 的測試見 §4.1–§4.4。
- 這一版：139 個詞、155 列（9 列變調列、7 列另外的讀音），`meme` 類 112 個、`offensive` 類 27 個；審定清單 139 筆全部收進；未處置衝突 0。檢查集：`words.txt` 60 列、`words-all.txt` 138 列（131 個詞的主要讀音加 7 列另外的讀音）、`embedded.txt` 828 列。

## 10. 已知限制

- 重建 `build_acg_pack.py`、`build_it_pack.py`、`build_quote_pack.py` 的輸出時，這三支不讀 `meme-add.tsv`：迷因詞包建置時已對它們現在的輸出去重，所以現在的檔沒有交集，但重建之後可能出現同一個詞串在兩個檔裡（核心載入時回 `OverlayDuplicate`），要另外處理和迷因詞包的去重。

- 重建出貨的動漫詞包：出貨的 `acg-add.tsv` 是對固定的未分級詞包做 `regrade` 的結果（§5.5）。不帶子命令的 `build_acg_pack.py` 會用最新來源整份重建，寫出的是 `acg` 標籤的分級列，沒有 `acg-oov` 的強制詞標籤，內容也會隨維基的新版本改變，所以不會與出貨檔相同；更新來源後要重新處置新的衝突，並自己接上強制詞。
- 動漫詞包的衝突偵測釘在舊的規則模型，出貨模型上的重驗要另外跑 `--recheck`（§5.6）；兩者在少數讀音上第一名不同，那些讀音靠 `acg-keep-both.tsv`、`FIRST_NAMED_EXCEPTIONS` 與讓位清單吸收。
- 要求的詞包檔不存在時，引擎靜默當作沒有（回 0），只有評測 CLI 會報錯；殼用「檔案存在」判斷哪些詞包算載入（§4.2）。
- 詞包的更新只隨 App 出貨：repo 裡沒有排程重建詞包的流程，也沒有使用者端獨立更新詞包的管道；輸入法讀的是 bundle 內的 `Resources/packs/`。
- 引擎一輩子留著整份詞包文字（四個詞包檔合計約 2.9 MB）供封頂與常談表載入使用，沒有只留需要的部分。
- 名單層與網路用語的收錄準則（論壇用量門檻、同音讓不讓第一名）是來源檔的內容準則，建置工具只檢查格式、重複與分數，不檢查用量。挖掘與分類的腳本不隨 repo 散布。
- 經典台詞詞包的衝突偵測只在動漫與資訊詞包都開的組合下做；只開台詞時，被另外兩包的詞蓋過的基底詞，可能被台詞分句擠掉而建置看不到（分句 2–10 字且與基底詞同讀音的少，未量）。
- 資訊詞包的頻率門檻 50 與加分 3 是在探索格點上選的；詞包的詞只在模型沒看過時才拿到完整加分。
- 讀音工具對破音字只能猜，名單層、資訊詞包與動漫詞包的 `CHECK` 讀音靠人工核對檔（`names-readings.tsv`、`it-readings.tsv`、`reading_overrides.tsv`）修正；沒進這些檔的 `CHECK` 讀音會照工具的暫定值收進詞包。
- 疊加層的來源 dump、簡編本、OpenCC 表、萌典與小麥破音字表都不進 repo，需要時從環境變數（`SHANJIE_CACHE`、`SHANJIE_CONCISED`、`SHANJIE_MOEDICT`、`SHANJIE_HETERO`、`SHANJIE_UNIHAN`）找；沒設時對應的測試跳過並印 NOTE，跳過不等於通過。
