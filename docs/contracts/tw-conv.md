# TW-CONV：語料轉換的根因修正（原生繁體不轉、簡體句用台灣用量決定）

狀態：**修訂五**（2026-11-06，實作後）：實作量到三個寫錯的規則，照最小修改（繁體句也套「含真簡體字」的詞組、繁體證據字 T 不收本身是簡體鍵的字、台灣幾乎不用的鍵在繁體句與只有標記字的句子也換）；見 §7。修訂四：第二次收尾審查 REVISE 的兩項照最小修改（被淘汰字的停止條件只看選定值不是 OpenCC 第一個對照的單字列；回程斷言的預期值把大陸用詞換成台灣說法）。修訂三：收尾審查 REVISE 的四項照最小修改（候選先摺疊再計數、只有標記字的簡體句不換沒被詞組蓋到的標記字、詞組 hold 只換 OpenCC 第一個對照的位置、口語池 `pool.tsv` 的種類）。修訂二：plan-verifier 第二次 REVISE 的三項逐項處置，三項都修（連文的計數程式 `count.py` 進入口表、維基新聞匯出算 `mixed`、§5 的原生來源停止條件改成量輸出而不是量判斷）。修訂一：plan-verifier 第一次 REVISE 的六項照最小修改（台灣地名保護條件留著、簡體輸入的 fixture 與斷言 4、詞組 hold 用單字列的選定值並加停止條件、§2.5 補齊呼叫者與共用表、31 個 MediaWiki 值寫進契約、拿掉私人路徑），另補 main 自己找到的漏洞：OpenCC 只有一個值、但鍵本身就是台灣字形的字（灶→竈）也進單字列。草稿 5d7d6ddf。維護者 2026-11-06 選「照這個方向寫契約」：(1) 原本就是繁體的語料不再轉換；(2) 含簡體來源的簡體句用附台灣用量證據的轉換表，取代 OpenCC 第一選擇與八張手寫例外表；(3) 修好判斷簡體的程式；(4) 一道永久守門測試。在下一次重建通語（PTT 抓完整後的那一次）之前完成。顧問：Fable、Astra、Argon（2026-11-06，三位結論一致）。

## 1. 為什麼

- **事故一再發生**，每次都是 `experiments/s2/build_counts.py` 的 `convert()`：
  - 改到原本就是台灣繁體的文字：吃→喫、后→後、里→裏（2026-10-03）；插件→外掛（SJ-LM-7 §2.2）；權限→許可權（會意訓練文字，SR-TW）。
  - 一對多的簡轉繁選錯邊：湿→溼（OpenCC `STCharacters.txt` 只有一個值）、账→賬。「高溼度」在書面勝出、詞庫的 保溼 比 保濕 高，源頭都在這裡（研究紀錄 2026-11-06）。
  - 每次事故補一張手寫表：`TW_VARIANTS_EXCLUDED`、`TRAD_KEEP`、`TAIWAN_KEEP`、`VARIANTS`、`TW_CHAR`、`PROTECT_KEYS`、`PROTECT_KEYS_TRAD`（加上 `MERGE`），沒有一張附台灣用量的證據。
- **量到的事實**（main 2026-11-06，零成本，Mac 單程序；腳本在維護者本機）：
  - 原生來源（各抽 2 萬句）：PTT 內文、PTT 推文、g0v、字幕的真簡體字句數都是 0%；現行 `convert()` 照樣改動 PTT 2.8%、g0v 4.4% 的句子（關掉繁體句的 TWPhrases 後仍改 0.1%：于→於、杰→傑、范→範 這類姓氏，干→幹、游→遊、借→藉…）。
  - 維基前 2,000 篇：380,702 句，真簡體句 49.3%；`is_simplified()` 判為簡體的 50.0%，其中 20,436 句沒有任何真簡體字。
  - 轉換後的字，從簡體句來的對從繁體句來的：溼 381／18、濕 29／135、賬 77／16、帳 102／75。溼、賬幾乎都是 OpenCC 轉出來的。
  - PTT 快照每 10 行抽 1 行的用量（台灣實際寫法）：濕 1,418 對 溼 106；帳 8,721 對 賬 50；簽 4,179 對 籤 1,306；鹼 51 對 硷 0；衝 4,738 對 沖 1,527；吃 28,225 對 喫 29；鹹 1,099 對 咸 19（現行 `TAIWAN_KEEP` 卻把 咸 留著）。

## 2. 做法

### 2.1 來源分兩種，由呼叫者明講

| 種類 | 來源 | 處理 |
|---|---|---|
| `native` | PTT（`ptt-clean`）、g0v、Common Voice zh-TW、`synth.txt`、新聞稿、書面來源 B（`docs/contracts/news-corpus.md` §3.1 的代號）、所有評測列，以及這個轉換器已經轉過的文字（`colloquial-train.txt` 等） | 原文照用，一個字都不改 |
| `mixed` | 口語池 `pool.tsv`（WildChat、YODAS、Tatoeba、MASSIVE 四個來源，含簡體原文；常談、連文、會意都讀它）、維基、維基新聞（`experiments/sj-lm-7/wikinews.py export` 的輸出，沒轉過、簡繁混雜）、Tatoeba、字幕（上游已轉過、仍有殘留）、詞庫建置用的維基詞典詞 | 逐句判斷（§2.2），簡體句走 §2.4 的簡體路徑，其他句走繁體路徑 |

- 每個入口的每一份輸入都要給種類，**沒有預設值**；沒給就丟錯誤。理由：預設值讓「沒改到的呼叫者」默默走舊路（SR-TW 的會意漏改就是這樣）。
- 已經轉過的文字標 `native`，不再轉第二次（現在 `s7a-tinylm/prep.py` 會把 `build_tune.py` 轉好的 `colloquial-train.txt` 再轉一次）。

### 2.2 判斷簡體句

- **真簡體字 S**：`STCharacters.txt` 的鍵，對照裡不含自己，而且不出現在 `mcbpmf-data.txt` 任何兩字以上的詞（詞庫的單字條目也收了 个、会 這類簡體字，分數都是最低值，所以只看兩字以上的詞）。現在 3,778 字；床、程、群、秘、灶 不在 S。
- **標記字 M**：`STCharacters.txt` 第一個對照不是自己的鍵（含 后、于、里、台、床）。
- **繁體證據字 T**：`STCharacters.txt` 的值裡不是任何鍵的字，加上 §2.3 摺疊表與 `TWVariants.txt` 的目標字（為、裡、眾、啟…），但扣掉 M 裡的字（么、床、灶、粽 這類本身是簡體鍵的字不算繁體證據；修訂五）（現在的 `TRAD_ONLY` 只取第一個對照，所以 為、裡、眾 不算繁體，「為裡面的群眾提供支持」被判成簡體）。
- 句子的 R、M、T 是這三組字在句中的次數。**簡體句**：(R ≥ 1 且 R ≥ T)，或 (R = 0 且 M ≥ 1 且 T = 0)。第二條留著，「之后他去了」這類只有標記字的簡體句才轉得到（S2n 的七個殘留詞）；只因第二條判成簡體的句子轉法不同（§2.4），台灣句子「奇希里斯」「后里」「今天去台南玩」才能原樣。
- 把台灣句子誤判成簡體句的代價，由 §2.3 的表與 §3 的守門壓到零：簡體路徑作用在台灣句子上必須原樣輸出（§3 第 1 條）。

### 2.3 轉換表 `experiments/s2/tw-conv.tsv`

由 `experiments/s2/build_tw_conv.py` 產生，進版控。證據只用台灣的實際寫法：PTT 快照 `ptt-clean` 的 `body.tsv`＋`push.tsv` 全部（2026-10-25 那一份，檔頭記兩個檔的 SHA-256 與工具 commit）。檔裡只有字、詞與次數，不放句子。四段，判定規則在任何次數出來之前寫死如下：

1. **單字（`char`）**：鍵有三類，候選是聯集：
   - `STCharacters.txt` 有兩個以上對照的鍵（276 個）：候選＝它的對照。
   - 鍵不在 S、而且對照不含自己的鍵（鍵本身出現在詞庫兩字以上的詞，是台灣字形；例：灶→竈、床→牀、秘→祕、群→羣、吃→喫、粽→糉）：候選＝對照加上鍵本身。
   - OpenCC 與 MediaWiki zh-tw 單字轉換不同的 31 個鍵：候選＝OpenCC 的對照加上 MediaWiki 的值。MediaWiki 的值（main 2026-11-06 對照 MediaWiki `ZhConversion.php` 的 zh-tw 表，寫成工具裡的常數，工具不 import zhconv_rs、不連網）：冲→沖（OpenCC 衝）、凄→淒（悽）、勋→勛（勳）、卤→鹵（滷）、吣→唚（吣）、哗→嘩（譁）、喂→餵（喂）、埙→塤（壎）、棂→櫺（欞）、湿→濕（溼）、牦→氂（犛）、瘘→瘺（瘻）、硷→鹼（硷）、签→簽（籤）、艳→艷（豔）、袅→裊（嫋）、账→帳（賬）、鉴→鑒（鑑）、钚→鈽（鈈）、钫→鍅（鈁）、锎→鉲（鐦）、锝→鎝（鍀）、锨→杴（鍁）、锫→鉳（錇）、锿→鑀（鎄）、镅→鋂（鎇）、镎→錼（鎿）、镥→鎦（鑥）、鹇→鷴（鷳）、鹚→鶿（鷀）、𩙧→𩗡（䬞）。其他鍵不用 MediaWiki 的值。
   - **先摺疊再計數**：每個候選先套 §2.3 第 3 段的摺疊列（摺疊列還沒定時用 `TWVariants.txt` 第一個值），同一個摺疊後的字只算一個候選，次數用摺疊後的字在 PTT 的次數（例：里 的候選 裏、里，裏 摺成 裡，比的是 裡 對 里）。選定值寫摺疊後的字。
   - 判定：第一名的次數 ≥ 第二名的 2 倍，而且 ≥ 30：選第一名（狀態 `evidence`）。否則選 OpenCC 第一個對照（摺疊後；狀態 `hold`）。
2. **詞組（`phrase`）**：`STPhrases.txt` 裡、鍵含有某個單字列的鍵，而且這個單字列是 31 個鍵之一或選定值不是 OpenCC 第一個對照的條目（現在估 821 條以上）。候選＝OpenCC 的值，加上把那個位置換成該鍵其他候選字的寫法；整串在 PTT 的次數，規則同上（2 倍、≥ 30 → `evidence`）。候選同樣先摺疊再計數。不到門檻時（`hold`）：只有 OpenCC 詞組值在那個位置的字等於該鍵的 OpenCC 第一個對照，才換成單字列的選定值；OpenCC 在那個位置寫了別的字（詞組本來就是單字預設的例外），保留 OpenCC 的值；單字列本身也是 `hold` 就留 OpenCC 的值。例：风湿 的候選 風溼／風濕，PTT 不夠就跟著 湿 的單字列；冲积→沖積 的 沖 不是 冲 的第一個對照（衝），保留 沖積；抽签 的候選 抽籤／抽簽。
3. **摺疊（`fold`）**：候選＝現行 `VARIANTS` 與 `TWVariants.txt`（第一個值）的每一對 X→Y。Y 的次數 ≥ X 的 20 倍、而且 Y ≥ 30 才摺疊；其餘標 `nofold`、不摺疊。用 20 倍（不是 2 倍）是因為摺疊不看上下文：泄→洩 在抽樣是 36／513（14 倍），排泄 的 泄 是正確寫法，這種要留著。
4. **台灣用詞（`tw_phrase`）**：簡體句才套的 `TWPhrases.txt` 子集＝SJ-LM-7 §2.7 量過的大陸用詞清單（331 個，PTT 用量不到台灣說法 10% 的；現在只在維護者本機快取），原樣複製進 `experiments/s2/mainland-terms.txt`，檔頭照抄並記 SHA-256。

- **給維護者看的列**：選定值不是 OpenCC 第一個對照的列（含詞組 `hold` 跟著單字列換掉的），單字的 `hold` 列（不論次數）、有任何次數的詞組 `hold` 列、全部 `nofold` 列。`build_tw_conv.py --review` 印成一張表（鍵、各候選與次數、規則給的結果）。維護者逐列決定時，結果寫進第五欄 `manual`（附 `decisions.md` 的日期），`manual` 優先於規則；表重產時保留 `manual` 欄。

### 2.4 轉換的順序（`mixed`）

- **簡體句**（§2.2 第一條）：`STPhrases.txt` 最長優先，詞組列有選定值的用選定值 → 沒蓋到的字用單字列的選定值（不在表裡的鍵用 `STCharacters.txt` 唯一的對照）→ `tw_phrase` 子集最長優先 → 摺疊。
- **只有標記字的簡體句**（只因 §2.2 第二條判成簡體）：`STPhrases.txt` 最長優先（同上）→ 沒被詞組蓋到的字只換「台灣幾乎不用的鍵」N（修訂五，定義見 §7）→ `tw_phrase` 子集 → 摺疊；其餘沒被蓋到的字原樣保留（「之后他去了」靠詞組 之后→之後；「奇希里斯」「后里」不變，里、后 不在 N）。
- **繁體句**（含夾雜少數簡體字的句子）：`STPhrases.txt` 裡鍵含有至少一個真簡體字（S）的條目最長優先（詞組列有選定值的用選定值；修訂五）→ 沒被蓋到的真簡體字與 N 的字換成單字列的選定值（或唯一對照）→ 摺疊。其他詞組不套（它們只會碰到不含真簡體字的片段，也就是台灣原本的寫法），不套 `TWPhrases.txt`。
- `native`：不呼叫轉換。

### 2.5 程式

- `build_counts.py`：
  - `convert(text, phrase, char, maxp)` 改成 §2.4 的 `mixed` 轉換；`load_conv()` 照舊回傳三元組，呼叫者寫法不變。
  - `is_simplified()` 改成 §2.2。
  - 拿掉參數 `trad_tw_phrases`、`simp_tw_keys`，以及只為它們存在的程式：`_simp_tw_table`、`_SIMP_TW`、`trad_tw_phrases` 在 `_init`／`article_runs`／`text_runs`／`article_sentences`／`guard_matcher` 的傳遞。
  - 拿掉 `TW_VARIANTS_EXCLUDED`、`TRAD_KEEP`、`TAIWAN_KEEP`、`TW_CHAR`、`PROTECT_KEYS`、`PROTECT_KEYS_TRAD`、`PROTECT`、`PROTECT_TRAD`、`POST_SIMP`、`POST_TRAD`；`VARIANTS` 只留給 `MERGE` 用。
  - **`MERGE` 不變**（逐條和現在相同的 18 條，寫成字面值）：它是 `build_lm.py` 合併異體計數與保留集比對用的，不是轉換；改它會改到模型，留給溼濕排序那一片。
- **其他程式讀的共用表**：
  - `TRAD_SIMP_ONLY` 改成 S（§2.2）。讀它的：`experiments/sj-lm-7/ptt_clean.py`、`p2clean.py`、`clean_subs.py`（丟掉含簡體字的句子）、`tools/build_overlay.py`、`tools/build_names.py`、`experiments/s7f-corpus/clean.py`、`experiments/e2-chateval/build_wikitypo.py`。清洗結果因此改變的句數在 §4.4 報。
  - `experiments/s2w/mwconv.py`（`build_counts.py --mw`、`build_tune.py --mw` 用）的最後一層原本是 `PROTECT_TRAD`＋`POST_TRAD`，改成 §2.3 的摺疊列。
  - `TW_VARIANTS` 改成 `TWVariants.txt` 每條取第一個值的完整對照（不再扣掉例外），只當 §2.2 的 T 與 §2.3 摺疊列的候選來源。`tools/build_overlay.py` 原本用 `MERGE`＋`TW_VARIANTS` 摺疊疊加層的詞，改成 `MERGE`＋§2.3 的摺疊列。
- **入口與輸入種類**（§2.1；每份輸入一個種類，沒給就報錯）：

| 程式 | 輸入 → 種類 |
|---|---|
| `experiments/s2/build_counts.py` | 維基 → `mixed` |
| `experiments/s2/build_counts_text.py` | 新增 `--native PATH`、`--mixed PATH`（可重複），位置參數拿掉 |
| `experiments/s2/build_tune.py` | CV zh-TW → `native`；Tatoeba、維基 → `mixed`；寫出的 `colloquial-train.txt` 之後一律當 `native` |
| `experiments/s7a-tinylm/prep.py` | `runs`：維基 → `mixed`，`--coll`、`--synth` → `native`；`text`：新增必填的 `--kind native\|mixed` |
| `experiments/sj-lm-7/wiki_text.py` | 維基 → `mixed` |
| `experiments/derisk-1015/e1/count.py`（連文的三元組計數，`docs/releases/sj-cx-2-7.7m.md`） | `wiki` → `mixed`；`text` 新增 `--native PATH`、`--mixed PATH`（可重複），位置參數拿掉；`--no-trad-tw-phrases` 拿掉；`experiments/sj-lm-7/README.md` 的指令跟著改 |
| `experiments/sj-lm-7/wikinews.py`（匯出給 `build_counts_text.py`、`count.py text`） | 匯出本身不轉；讀它的一律給 `--mixed` |
| `tools/holdout_guard.py report`（`--text`、`--row-files`、`--wiki`）、`experiments/sj-lm-7/dedupe_eval.py` | 只做比對、不產生語料：不需要種類，原文與 `mixed` 轉換結果兩種形式都比（涵蓋任何一種計數用的寫法） |
| `tools/build_digits.py` | 維基 → `mixed`，`--colloquial` → `native` |
| `tools/holdout_guard.py` | 比對形式＝原文與 `convert()` 結果，各做 `fold()` |
| `tools/build_overlay.py`、`tools/build_names.py`、`experiments/names/build_packs.py`、`tools/build_english_next.py`、`tools/it_wiki_counts.py` | 維基與維基詞典 → `mixed` |

- **拿掉現在沒有意義的旗標**（傳了要報錯，不默默不做事）：`--no-trad-tw-phrases`（`build_counts.py`、`build_counts_text.py`、`prep.py`、`wiki_text.py`）、`--tune-no-trad-tw-phrases`、`--cv-no-trad-tw-phrases`、`--train-only-new-conv`（`build_tune.py`）、`--simp-tw-keys`、`--changed-flags`（`prep.py`；後者是比較新舊兩種 TWPhrases 設定用的）。`prep.py` 的 `EXPECTED_NO_TW` 拿掉；`EXPECTED` 的輸入雜湊不在這一片改，重建時換成新產生的檔。`experiments/s2/trace_tune_sources.py` 的 TWPhrases 選項一併拿掉。舊實驗的重現指令照研究紀錄裡的 commit 雜湊回到舊版跑。
- `tools/build_names.py` 的保護條件留著，只把 `TRAD_SIMP_ONLY` 換成 S：`is_simplified(name) and any(c in S for c in name)`。§2.2 的第二條（只有標記字）仍會把 台糖、台北、后里 判成簡體，這個條件擋住它們。
- **跟著改的測試**（只改到被拿掉或改名的參數、表與旗標，預期值的改動照 §4.5 逐條列）：`experiments/s2/test_convert.py`、`test_trad_phrases.py`、`test_build_tune.py`、`test_trace_tune_sources.py`、`test_extra_lexicon.py`，`experiments/e2-chateval/test_e2.py`，`experiments/sj-lm-7/test_g0v_clean.py`，`experiments/s7a-tinylm/test_prep.py`，`experiments/sj-lm-7/test_clean_subs.py`、`test_ptt_clean.py`、`test_wikinews.py`，`experiments/p1a-confusion/test_p1a.py`（`build_counts_text` 改用 `--mixed`），`experiments/s7f-corpus/test_s7f.py`，`tools/test_holdout_guard.py`、`test_build_overlay.py`、`test_build_names.py`、`test_build_digits.py`，`experiments/derisk-1015/e1/test_e1.py`。
- **不改、會壞的舊實驗**（只留作歷史紀錄，不在 `make test-python` 裡）：`experiments/s2n/mutations.py`、`experiments/s2f/check_f2.py`（檢查 S2f 的保護詞）、`experiments/s2w/test_mw.py`（要 zhconv-rs 的 venv，斷言 S2f 的最後一層）。其餘舊實驗目錄（`s2n`、`s2w`、`reading-aware`（它 import `count.py`，跟著新的 `count.py` 走）、`digits`、`derisk-1015/e4`）呼叫 `convert(text, *conv)`，照常可跑、行為跟著新轉換；實作時用 grep 確認沒有別的呼叫者傳被拿掉的參數，有就列進這一條。

## 3. 永久守門 `experiments/s2/test_tw_conv.py`

本機的 `make test-python` 執行（`experiments/` 的測試只在本機）；一秒級，不需要語料。

- **台灣句子**（第 1、2 條用）：`eval/dev/user-typing.txt`、`eval/dev/user-reported.txt` 的句子欄與前文欄，加上 main 寫的 `experiments/s2/tw-conv-fixture.txt`：§2.3 每一條 `evidence` 列與 `fold` 列至少一句用到它的台灣寫法（保濕、帳號、簽名、抽籤、衝刺、沖泡、餵食、鹼性…），以及顧問與事故的句子（在床上看程序、為裡面的群眾提供支持、今天去台南玩、管理員權限、插件、吃不出來、排泄物、竈門炭治郎）。
- **斷言**：
  1. **不變**：每句 s，`convert(s, *conv) == fold_tw(s)`（`fold_tw` 只套 §2.3 的摺疊列）。不論它被判成哪一種句子都要成立。
  2. **回程**：s 用 `STCharacters.txt` 反查成簡體 s′（一個繁體字只對應一個不同於自己的鍵時才換，其餘照留），`convert(s′, *conv)` 必須等於「`fold_tw(s)` 再把句中 `mainland-terms.txt` 的鍵最長優先換成 `tw_phrase` 的值」（s′ 若被判成簡體句，簡體路徑本來就會換大陸用詞；s′ 判成繁體句時，預期值改回 `fold_tw(s)`）；不同的位置，（預期字, 得到字）這一對必須列在 `experiments/s2/tw-conv-ambiguous.tsv`（真正一對多、轉不回來的字對，例如 乾／幹、髮／發、麵／面；每列附理由）。清單外的字對一律失敗；清單的列數和內容進版控，新增要改檔並寫理由。
  3. **native 原樣**：`build_counts_text.py` 用 `--native` 讀一份含 爲、着、溼、程序、插件 的小檔，計數裡這些字串照原樣出現；`experiments/derisk-1015/e1/count.py text --native` 讀同一份檔，三元組計數裡也照原樣。
  4. **簡體輸入**：`experiments/s2/tw-conv-simp.tsv`（main 寫，每列 `簡體句<TAB>預期的台灣寫法`），每列 `convert(簡體句, *conv) == 預期`。涵蓋：31 個鍵每個至少一列，以及 `tw-conv.tsv` 裡選定值不是 OpenCC 第一個對照的每一條單字列與詞組列（例：湿度→濕度、保湿→保濕、风湿病→風濕病、账号→帳號、转账→轉帳、签名→簽名、抽签→抽籤、厨房的灶→廚房的灶），另外固定含：这里→這裡、家里→家裡、公里→公里、冲积平原→沖積平原、之后他去了→之後他去了，與原樣的 奇希里斯、后里、今天去台南玩。涵蓋本身也是斷言：表裡有這種列、fixture 卻沒有含那個鍵的句子，就失敗（表重產後 fixture 要跟著補）。
- **突變**（`experiments/s2/mutate_tw_conv.py`，每項都要讓上面某一條失敗，報告列在 PR）：
  - M1 繁體句也套整張 `TWPhrases.txt` → 第 1 條失敗（權限→許可權、插件→外掛）。
  - M2 `is_simplified` 改回現行版本，而且簡體路徑套整張 `TWPhrases.txt` → 第 1 條失敗（在床上看程序→程式）。
  - M3 單字列與詞組列改回 OpenCC 的值 → 第 4 條失敗（湿度→溼度、账号→賬號）。
  - M4 `build_counts_text.py` 的 `--native` 改成走 `mixed` → 第 3 條失敗；`count.py text` 的 `--native` 改成走 `mixed` → 第 3 條失敗（兩項分開跑）。
  - M6 單字列與詞組列的候選不摺疊就計數 → 第 4 條失敗（家里→家里）。M7 只有標記字的簡體句也套單字列 → 第 1 條失敗（`eval/dev/user-reported.txt` 第 51 列的前文 奇希里斯）。M8 詞組 hold 一律換成單字列的選定值 → 第 4 條失敗（冲积平原→衝積平原）。
  - M5 拿掉摺疊 → 守門裡一條 `mixed` 的例子（「隨着時間他説」→「隨著時間他說」）失敗。
- **整份語料的版本**：`experiments/s2/tw_conv_guard.py FILE...` 對每一句做第 1、2 條，報不合的句數、字對與次數（不印句子）。下一次重建的遠端腳本第一步對整份 PTT 快照跑它，第 1 條不合超過 0.1% 或第 2 條出現清單外的字對就 STOP（寫進那一片的契約）。

## 4. 驗收（怎麼看到它有效）

維護者看得到的：§2.3 的待看列表（每列的次數與結果）、§4.3 的前後對照表。之後重建的通語裡「高濕度」「保濕」「帳號」的選字是那一片的事。

1. **守門**：`test_tw_conv.py` 全過；M1–M8 每一項都讓指定的斷言失敗。`tw-conv-ambiguous.tsv` 不超過 40 列。
2. **轉換表**：`build_tw_conv.py` 在 Mac 單程序跑完整份 PTT 快照，記秒數；各段的列數與狀態分布；`--review` 的表交給維護者。
3. **維基前 2,000 篇，現行對新版**（Mac 單程序）。方法：`articles(2000)` 的每篇經 `article_text`、`SENT` 切句；句中有任何一個「現行 `load_conv()` 回傳的字表的鍵、而且不出現在 `mcbpmf-data.txt` 任何一條（含單字條目）」的字，就算簡體句（這是 §1 量基準時的定義，和 §2.2 的 S 不同，兩版都用它分組）；「現行」＝這個分支的起點 `e38b8827` 的 `build_counts.py`。每句各用現行 `convert()`（`trad_tw_phrases=False`，SJ-LM-7 的計數設定）與新版 `convert()` 轉，數輸出裡各字的次數，依簡體句、繁體句分開。現行的基準：溼 381／18、賬 77／16。
   - 31 個鍵與每條選定值不是 OpenCC 第一個對照的單字列：選定值與 OpenCC 第一個對照各自在簡體句轉出的次數。
   - 判斷不同的句數（現行判簡體、新版判繁體，以及反過來）；各用 `random.Random(20261106)` 抽 50 句，main 逐句看新版輸出是不是正確的台灣寫法，判讀寫進研究紀錄（數字與字對）。
   - 計數用的漢字總數，新版對現行。
   - 2,000 篇轉換的牆鐘秒數，新版對現行。
4. **原生來源**（PTT 內文、推文、g0v，各 2 萬句抽樣；字幕同樣抽 2 萬句）：新版判成簡體句的比例；`mixed` 路徑作用在這些句子上和 `fold_tw` 不同的句數；`TRAD_SIMP_ONLY` 換成 S 之後，`ptt_clean.py` 與 `clean_subs.py` 的簡體字篩選在這些抽樣上多丟、少丟的句數（只報數字與字）。
5. **既有測試**：`make test-python` 全綠；`experiments/s2`、`experiments/s7a-tinylm`、`experiments/sj-lm-7` 的測試全綠。因為新轉換而改的預期值逐條列在 PR，每條對到本契約的一條規則（例：`test_convert.py` 的「管理员权限」→「管理員權限」，因為 權限 不在大陸用詞清單）。
6. **疊加層**：`build_overlay.py` 用新轉換重產到暫存目錄，和現行 `data/lexicon/overlay-add.tsv` 的差異列（例：賬號、轉賬）報在 PR；`data/` 不改。

## 5. 停止條件、預算、範圍外

- **停止條件**：M1–M8 任何一項突變沒被抓到；`tw-conv-ambiguous.tsv` 超過 40 列；§4.3 任一組 50 句裡新版輸出錯的超過 5 句；§4.3 某條選定值不是 OpenCC 第一個對照（摺疊後）的單字 `evidence` 列（例：湿→濕、账→帳），它淘汰的 OpenCC 第一個對照（溼、賬）在簡體句的新版輸出裡仍超過現行次數的 10%（例：溼 超過 38）；不算進去的只有選了這個字的詞組 `evidence` 或 `manual` 列的輸出（停下時列出產生它的詞組給維護者）。選定值等於 OpenCC 第一個對照的列（发→發、冲→衝）不在這條；§4.4 PTT 或 g0v 的抽樣裡 `mixed` 路徑輸出 ≠ `fold_tw(s)` 的句子超過 0.1%（判成簡體句的比例只報、不停：原生來源本來就不轉，§2.2 也容許 台北 這類句子被判成簡體）；§4.3 漢字總數下降超過 1%；轉換時間超過現行的 1.5 倍；`build_tw_conv.py` 在 Mac 超過 10 分鐘（改到 188，照遠端腳本規則）。停下時報數字，不改門檻。
- **預算**：executor 實作 1 回合加 1 次修正；main 寫 fixture、`tw-conv-ambiguous.tsv` 的理由、§4.3 的判讀；維護者看 §2.3 的表。
- **範圍外**：
  - 重建與出貨（通語、辨類、承接、連文、常談、會意都在下一次重建一起重做，照出貨標準量）。
  - 評測集不重建（cvtune-clean-tw0、wikitune、各判讀集的正解維持凍結的檔案）；新轉換不能替新模型改正解加分。
  - `MERGE` 與詞庫裡異體字的分數（溼濕排序那一片）。
  - 字幕上游的轉換錯誤（`clean_subs.py`）、MediaWiki／zhconv-rs、§2.5 列出的舊實驗目錄。

## 6. executor 不可以做的事

- 不連網、不安裝、不推分支、不開 PR、不建 Release。
- 不讀 `eval/holdout/`、`~/side-project/shanjie-private/`、聊天紀錄、學習檔；PTT 快照只用來計數，不印、不寫出任何一句。
- 不改 `core/`、`macos/`、`data/`、`eval/`；不安裝、不啟動 App，不呼叫 TIS 或 lsregister，不碰 `~/Library` 與鑰匙圈。
- Mac 不跑完整測試套件與大型編譯；只跑本契約列出的 Python 測試與 §4 的單程序量測。
- 新檔名不含空白。

## 7. 修訂五（2026-11-06，實作之後）

實作（feat/tw-conv 9b94ea16）跑出來、逐條核對預期值時，看到三個由契約本身造成的錯誤輸出：

| 輸入 | 修訂四的輸出 | 原因 |
|---|---|---|
| 這個國家的經濟發展关系着我們 | 這個國家的經濟發展關系著我們 | 繁體句完全不套詞組，关系 只剩單字轉換 |
| 什么 | 什么 | 么 是 `TWVariants.txt` 的目標字而被算成繁體證據，整句判成繁體句；么 又不在 S |
| （含 装 的只有標記字句子） | 装 不轉 | 装 出現在詞庫的兩字詞裡，不在 S；只有標記字的句子不套單字表 |

修法（上面 §2.2 與 §2.4 已改）：

1. **T 扣掉 M**：本身是簡體鍵（第一個對照不是自己）的字不算繁體證據。為、裡、眾 不是鍵，照算。
2. **繁體句套含真簡體字的詞組**：只用鍵裡有 S 字的 `STPhrases.txt` 條目，所以只會改到含簡體字的片段；原生台灣文字沒有 S 字（§1 量過 0%），碰不到。
3. **N：台灣幾乎不用的鍵**＝單字列裡選定值不是鍵本身、而且鍵本身在 PTT 的次數不到選定值（摺疊後）的 1/20 的鍵（和摺疊列同樣用 20 倍：例 装 對 裝、庄 對 莊、痒 對 癢、虱 對 蝨；里 對 裡、后 對 後 兩邊都常用，不在 N）。N 的字在繁體句與只有標記字的句子也換成選定值。N 由 `build_tw_conv.py` 寫進 `tw-conv.tsv`（單字列多一欄或狀態標記），`--review` 也列出 N。

**守門跟著加**：`tw-conv-fixture.txt`（台灣句子）加 executor 放在 `test_tw_conv.py` 的四句只有標記字的句子（從測試檔搬進 fixture，fixture 是唯一來源）；`tw-conv-simp.tsv` 加：`这个国家的经济发展关系着我们`→`這個國家的經濟發展關係著我們`、`你在说什么`→`你在說什麼`、`我们住在街里`→`我們住在街裡`（M6 的單字列例子）、一句只有標記字加 装 的句子（main 寫）。

**突變補一項**：M9 把 T 改回含 M 的字 → 第 4 條失敗（什么 那列）；M10 把繁體句的詞組拿掉 → 第 4 條失敗（关系着 那列）；M11 把 N 拿掉 → 第 4 條失敗（装 那列）。

其餘照修訂四。預算：用 §5 剩下的那次修正回合。

