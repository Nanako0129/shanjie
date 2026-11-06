# TW-CONV：語料轉換的根因修正（原生繁體不轉、簡體句用台灣用量決定）

狀態：草稿（2026-11-06），等 plan-verifier。維護者 2026-11-06 選「照這個方向寫契約」：(1) 原本就是繁體的語料不再轉換；(2) 含簡體來源的簡體句用附台灣用量證據的轉換表，取代 OpenCC 第一選擇與八張手寫例外表；(3) 修好判斷簡體的程式；(4) 一道永久守門測試。在下一次重建通語（PTT 抓完整後的那一次）之前完成。顧問：Fable、Astra、Argon（2026-11-06，三位結論一致）。

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
| `native` | PTT（`ptt-clean`）、g0v、Common Voice zh-TW、`synth.txt`、新聞、所有評測列，以及這個轉換器已經轉過的文字（`colloquial-train.txt` 等） | 原文照用，一個字都不改 |
| `mixed` | 維基、Tatoeba、字幕（上游已轉過、仍有殘留）、詞庫建置用的維基詞典詞 | 逐句判斷（§2.2），簡體句走 §2.4 的簡體路徑，其他句走繁體路徑 |

- 每個入口的每一份輸入都要給種類，**沒有預設值**；沒給就丟錯誤。理由：預設值讓「沒改到的呼叫者」默默走舊路（SR-TW 的會意漏改就是這樣）。
- 已經轉過的文字標 `native`，不再轉第二次（現在 `s7a-tinylm/prep.py` 會把 `build_tune.py` 轉好的 `colloquial-train.txt` 再轉一次）。

### 2.2 判斷簡體句

- **真簡體字 S**：`STCharacters.txt` 的鍵，對照裡不含自己，而且不出現在 `mcbpmf-data.txt` 任何兩字以上的詞（詞庫的單字條目也收了 个、会 這類簡體字，分數都是最低值，所以只看兩字以上的詞）。現在 3,778 字；床、程、群、秘、灶 不在 S。
- **標記字 M**：`STCharacters.txt` 第一個對照不是自己的鍵（含 后、于、里、台、床）。
- **繁體證據字 T**：`STCharacters.txt` 的值裡不是任何鍵的字，加上 §2.3 摺疊表與 `TWVariants.txt` 的目標字（為、裡、眾、啟…；現在的 `TRAD_ONLY` 只取第一個對照，所以 為、裡、眾 不算繁體，「為裡面的群眾提供支持」被判成簡體）。
- 句子的 R、M、T 是這三組字在句中的次數。**簡體句**：(R ≥ 1 且 R ≥ T)，或 (R = 0 且 M ≥ 1 且 T = 0)。第二條留著，「之后他去了」這類只有標記字的簡體句才轉得到（S2n 的七個殘留詞）。
- 把台灣句子誤判成簡體句的代價，由 §2.3 的表與 §3 的守門壓到零：簡體路徑作用在台灣句子上必須原樣輸出（§3 第 1 條）。

### 2.3 轉換表 `experiments/s2/tw-conv.tsv`

由 `experiments/s2/build_tw_conv.py` 產生，進版控。證據只用台灣的實際寫法：PTT 快照 `ptt-clean` 的 `body.tsv`＋`push.tsv` 全部（2026-10-25 那一份，檔頭記兩個檔的 SHA-256 與工具 commit）。檔裡只有字、詞與次數，不放句子。四段，判定規則在任何次數出來之前寫死如下：

1. **單字（`char`）**：鍵＝`STCharacters.txt` 有兩個以上對照的鍵（276 個），加上 OpenCC 與 MediaWiki zh-tw 單字轉換不同的 31 個鍵（冲 凄 勋 卤 吣 哗 喂 埙 棂 湿 牦 瘘 硷 签 艳 袅 账 鉴 钚 钫 锎 锝 锨 锫 锿 镅 镎 镥 鹇 鹚 𩙧；常數寫在工具裡，對照日期 2026-11-06）。候選＝OpenCC 的對照，加上 MediaWiki 的值。
   - 第一名的次數 ≥ 第二名的 2 倍，而且 ≥ 30：選第一名（狀態 `evidence`）。
   - 否則選 OpenCC 第一個對照（狀態 `hold`）。
2. **詞組（`phrase`）**：`STPhrases.txt` 裡、鍵含有某個單字列的鍵，而且這個單字列是 31 個鍵之一或選定值不是 OpenCC 第一個對照的條目（現在估 821 條以上）。候選＝OpenCC 的值，加上把那個位置換成該鍵其他候選字的寫法；整串在 PTT 的次數，規則同上（2 倍、≥ 30；否則留 OpenCC 的值）。例：风湿 的候選 風溼／風濕，抽签 的候選 抽籤／抽簽。
3. **摺疊（`fold`）**：候選＝現行 `VARIANTS` 與 `TWVariants.txt`（第一個值）的每一對 X→Y。Y 的次數 ≥ X 的 20 倍、而且 Y ≥ 30 才摺疊；其餘標 `nofold`、不摺疊。用 20 倍（不是 2 倍）是因為摺疊不看上下文：泄→洩 在抽樣是 36／513（14 倍），排泄 的 泄 是正確寫法，這種要留著。
4. **台灣用詞（`tw_phrase`）**：簡體句才套的 `TWPhrases.txt` 子集＝SJ-LM-7 §2.7 量過的大陸用詞清單（331 個，PTT 用量不到台灣說法 10% 的；現在只在維護者本機快取），原樣複製進 `experiments/s2/mainland-terms.txt`，檔頭照抄並記 SHA-256。

- **給維護者看的列**：選定值不是 OpenCC 第一個對照的列，以及有任何次數的 `hold` 列、全部 `nofold` 列。`build_tw_conv.py --review` 印成一張表（鍵、各候選與次數、規則給的結果）。維護者逐列決定時，結果寫進第五欄 `manual`（附 `decisions.md` 的日期），`manual` 優先於規則；表重產時保留 `manual` 欄。

### 2.4 轉換的順序（`mixed`）

- **簡體句**：`STPhrases.txt` 最長優先，詞組列有選定值的用選定值 → 沒蓋到的字用單字列的選定值（不在表裡的鍵用 `STCharacters.txt` 唯一的對照）→ `tw_phrase` 子集最長優先 → 摺疊。
- **繁體句**（含夾雜少數簡體字的句子）：只換句中的真簡體字（單字列或唯一對照）→ 摺疊。不套任何詞組表，不套 `TWPhrases.txt`。
- `native`：不呼叫轉換。

### 2.5 程式

- `build_counts.py`：
  - `convert(text, phrase, char, maxp)` 改成 §2.4 的 `mixed` 轉換；`load_conv()` 照舊回傳三元組，呼叫者寫法不變。
  - `is_simplified()` 改成 §2.2。
  - 拿掉參數 `trad_tw_phrases`、`simp_tw_keys`，以及只為它們存在的程式：`_simp_tw_table`、`_SIMP_TW`、`trad_tw_phrases` 在 `_init`／`article_runs`／`text_runs`／`article_sentences`／`guard_matcher` 的傳遞。
  - 拿掉 `TW_VARIANTS_EXCLUDED`、`TRAD_KEEP`、`TAIWAN_KEEP`、`TW_CHAR`、`PROTECT_KEYS`、`PROTECT_KEYS_TRAD`、`PROTECT`、`PROTECT_TRAD`、`POST_SIMP`、`POST_TRAD`；`VARIANTS` 只留給 `MERGE` 用。
  - **`MERGE` 不變**（逐條和現在相同的 18 條，寫成字面值）：它是 `build_lm.py` 合併異體計數與保留集比對用的，不是轉換；改它會改到模型，留給溼濕排序那一片。
- 入口照 §2.1 給種類：`build_counts.py`（維基＝`mixed`）、`build_counts_text.py`（新增 `--native PATH`／`--mixed PATH`，位置參數拿掉）、`build_tune.py`（CV zh-TW＝`native`、Tatoeba 與維基＝`mixed`）、`experiments/s7a-tinylm/prep.py`（同上）、`tools/build_digits.py`、`tools/holdout_guard.py`（比對形式＝原文與 `convert()` 結果，各做 `fold()`）、`tools/build_overlay.py`、`tools/build_names.py`、`experiments/names/build_packs.py`、`tools/build_english_next.py`、`tools/it_wiki_counts.py`。
- 拿掉現在沒有意義的旗標（傳了要報錯，不默默不做事）：`--no-trad-tw-phrases`、`--tune-no-trad-tw-phrases`、`--cv-no-trad-tw-phrases`、`--simp-tw-keys`、`--train-only-new-conv`。舊實驗的重現指令照研究紀錄裡的 commit 雜湊回到舊版跑。
- `tools/build_names.py` 為了 `is_simplified` 誤判「台糖」加的 `TRAD_SIMP_ONLY` 條件，改用 S 之後會多餘：拿掉，`test_build_names.py` 的「台糖」列留著當斷言。
- 這些目錄以外的舊實驗（`experiments/s2n`、`s2w`、`derisk-1015`、`reading-aware`、`p1a-confusion`、`s7f-corpus`、`digits`）不改；它們呼叫 `convert(text, *conv)` 的寫法仍可用，行為跟著新轉換。

## 3. 永久守門 `experiments/s2/test_tw_conv.py`

本機的 `make test-python` 執行（`experiments/` 的測試只在本機）；一秒級，不需要語料。

- **台灣句子**：`eval/dev/user-typing.txt`、`eval/dev/user-reported.txt` 的句子欄與前文欄，加上 main 寫的 `experiments/s2/tw-conv-fixture.txt`：§2.3 每一條 `evidence` 列與 `fold` 列至少一句用到它的台灣寫法（保濕、帳號、簽名、抽籤、衝刺、沖泡、餵食、鹼性…），以及顧問與事故的句子（在床上看程序、為裡面的群眾提供支持、今天去台南玩、管理員權限、插件、吃不出來、排泄物、竈門炭治郎）。
- **斷言**：
  1. **不變**：每句 s，`convert(s, *conv) == fold_tw(s)`（`fold_tw` 只套 §2.3 的摺疊列）。不論它被判成哪一種句子都要成立。
  2. **回程**：s 用 `STCharacters.txt` 反查成簡體 s′（一個繁體字只對應一個不同於自己的鍵時才換，其餘照留），`convert(s′, *conv)` 必須等於 `fold_tw(s)`；不同的位置，（預期字, 得到字）這一對必須列在 `experiments/s2/tw-conv-ambiguous.tsv`（真正一對多、轉不回來的字對，例如 乾／幹、髮／發、麵／面；每列附理由）。清單外的字對一律失敗；清單的列數和內容進版控，新增要改檔並寫理由。
  3. **native 原樣**：`build_counts_text.py` 用 `--native` 讀一份含 爲、着、溼、程序、插件 的小檔，計數裡這些字串照原樣出現。
- **突變**（`experiments/s2/mutate_tw_conv.py`，每項都要讓上面某一條失敗，報告列在 PR）：
  - M1 繁體句也套整張 `TWPhrases.txt` → 第 1 條失敗（權限→許可權、插件→外掛）。
  - M2 `is_simplified` 改回現行版本，而且簡體路徑套整張 `TWPhrases.txt` → 第 1 條失敗（在床上看程序→程式）。
  - M3 單字列改回 OpenCC 第一個對照 → 第 2 條失敗（濕→湿→溼、帳→账→賬）。
  - M4 `build_counts_text.py` 的 `--native` 改成走 `mixed` → 第 3 條失敗。
  - M5 拿掉摺疊 → 守門裡一條 `mixed` 的例子（「隨着時間他説」→「隨著時間他說」）失敗。
- **整份語料的版本**：`experiments/s2/tw_conv_guard.py FILE...` 對每一句做第 1、2 條，報不合的句數、字對與次數（不印句子）。下一次重建的遠端腳本第一步對整份 PTT 快照跑它，第 1 條不合超過 0.1% 或第 2 條出現清單外的字對就 STOP（寫進那一片的契約）。

## 4. 驗收（怎麼看到它有效）

維護者看得到的：§2.3 的待看列表（每列的次數與結果）、§4.3 的前後對照表。之後重建的通語裡「高濕度」「保濕」「帳號」的選字是那一片的事。

1. **守門**：`test_tw_conv.py` 全過；M1–M5 每一項都讓指定的斷言失敗。`tw-conv-ambiguous.tsv` 不超過 40 列。
2. **轉換表**：`build_tw_conv.py` 在 Mac 單程序跑完整份 PTT 快照，記秒數；各段的列數與狀態分布；`--review` 的表交給維護者。
3. **維基前 2,000 篇，現行對新版**（Mac，`.agent-local/scripts/opencc/measure_wiki.py` 的方法）：
   - 31 個鍵各自在簡體句轉出的字（溼 381、賬 77 這些數字的新版）。
   - 判斷不同的句數（現行判簡體、新版判繁體，以及反過來）；各用 `random.Random(20261106)` 抽 50 句，main 逐句看新版輸出是不是正確的台灣寫法，判讀寫進研究紀錄（數字與字對）。
   - 計數用的漢字總數，新版對現行。
   - 2,000 篇轉換的牆鐘秒數，新版對現行。
4. **原生來源**（PTT 內文、推文、g0v，各 2 萬句抽樣）：新版判成簡體句的比例；`mixed` 路徑作用在這些句子上和 `fold_tw` 不同的句數。
5. **既有測試**：`make test-python` 全綠；`experiments/s2`、`experiments/s7a-tinylm`、`experiments/sj-lm-7` 的測試全綠。因為新轉換而改的預期值逐條列在 PR，每條對到本契約的一條規則（例：`test_convert.py` 的「管理员权限」→「管理員權限」，因為 權限 不在大陸用詞清單）。
6. **疊加層**：`build_overlay.py` 用新轉換重產到暫存目錄，和現行 `data/lexicon/overlay-add.tsv` 的差異列（例：賬號、轉賬）報在 PR；`data/` 不改。

## 5. 停止條件、預算、範圍外

- **停止條件**：任何一項突變沒被抓到；`tw-conv-ambiguous.tsv` 超過 40 列；§4.3 任一組 50 句裡新版輸出錯的超過 5 句；§4.4 PTT 或 g0v 判成簡體句超過 0.1%；§4.3 漢字總數下降超過 1%；轉換時間超過現行的 1.5 倍；`build_tw_conv.py` 在 Mac 超過 10 分鐘（改到 188，照遠端腳本規則）。停下時報數字，不改門檻。
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
