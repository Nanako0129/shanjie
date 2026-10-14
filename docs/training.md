# 三個模型檔怎麼產生、怎麼重建或換掉

善解出貨三個大檔：`bigram.sjlm`（n-gram 語言模型）、`classes.sjc`（詞類表）、`tinylm.sjtl`（語意校正用的小型字元語言模型）。它們的配方原本散在各片的契約、實驗資料夾的 README 和研究紀錄裡。這份文件把整條流程照順序排在一起，每一步標出細節放在哪一份文件。

這份文件**不取代**那些來源：數字、指令與判斷規則以來源為準，這裡只彙整並指路。讀法：

- 括號裡是出處（檔案與節）。
- **〔推論〕** 是整理者從來源推出、來源沒有明說的話。
- **沒有紀錄** 是查遍來源也找不到的事，不猜。
- 各檔的現行版本、大小與雜湊看 `docs/data-files.md`，Release 說明看 `docs/releases/`；這裡不重抄整張表。

## 1. 總覽

### 1.1 三個檔案各是什麼

| 檔案 | 現行 Release | 內容 | 由什麼產生 |
|---|---|---|---|
| `bigram.sjlm` | `model-v5` | 詞與詞相鄰出現的次數（SJLM0001 格式）；含少量用來辨識來源的指紋條目（`docs/data-files.md`、`docs/contracts/model-v4.md` §0） | 語料計數 → `tools/build_lm.py` → 加指紋 |
| `classes.sjc` | `classes-v3` | 512 個詞類、每個詞的類別與發射機率、類別接續機率、μ = 0.8（SJCL0001 格式；`tools/build_classes.py` 檔頭、`docs/contracts/s2k-word-classes.md` §4.1） | 對計數做詞類分群 → `tools/build_classes.py` |
| `tinylm.sjtl` | `tinylm-v4` | 1,440 萬參數的字元語言模型，6 層、d 384、字表 9,714 字，int8；檔頭帶聊天與書面各三組（保守、標準、積極）融合參數 α、τ、δ（SJTL0003；`docs/releases/tinylm-v4.md`、`tinylm-v3.md`、`experiments/s7a-tinylm/export_sjtl.py` 檔頭） | 訓練 → 匯出 → 調融合參數 |

### 1.2 三個檔案怎麼互相綁定

```
語料計數 ──build_lm──> bigram.sjlm ──加指紋──> 發佈的 bigram.sjlm（SHA-256 = H）
                                                  │
         分群結果 ──build_classes（讀 H 的檔）──> classes.sjc（檔內記 H）
                                                  │
 小模型權重 ──export_sjtl（--ngram-model 讀 H 的檔）──> tinylm.sjtl（檔內記 H）
                         α、τ、δ 在「H＋classes」之上調
```

| 綁定 | 內容 | 後果 |
|---|---|---|
| 詞類表 → 模型 | `classes.sjc` 檔頭記模型的 SHA-256；缺檔、配到別的模型、內容不對，載入都回 3 | 重建模型就要重建詞類表（`docs/data-files.md`「現在用的檔案」） |
| 語意校正模型 → 模型 | `tinylm.sjtl` 檔頭記調參時用的模型的 SHA-256；不符時核心拒絕載入、只用 n-gram（`docs/data-files.md`） | 重建模型就要重新匯出、重新調參 |
| 語意校正模型 → 詞類表 | **沒有檢查**，只在 α、τ、δ 的調法上依賴：它們是在 model-v5＋classes-v3 上調的 | 只換詞類表也要重調，雜湊攔不住這種錯配，只能靠 `docs/data-files.md` 的版本表 |
| 語意校正 → Kneser–Ney 側檔 | `--tinylm` 要求 `kn.sjkn` 不能開（`experiments/s7a-tinylm/README.md`「S7c-1」）；〔推論〕原因是調參時沒有開，來源只寫了要求 | 之後若開 KN，融合參數要重調（見 §9） |

### 1.3 改了什麼，要發哪些新 Release

直接看 `docs/data-files.md`「版本怎麼往前走」，這裡只列結論：

| 改了什麼 | 要發 |
|---|---|
| 語料、計數或模型格式 | `model-v6`＋`classes-v4`＋下一個 `tinylm-v…` |
| 只重新分群 | `classes-v4`＋下一個 `tinylm-v…` |
| 小模型的訓練、大小、匯出格式或融合參數 | 下一個 `tinylm-v…`（`tinylm-v2`、`tinylm-v3` 都只換融合參數；`tinylm-v4` 換檔頭格式） |

## 2. 語料與輸入

三個檔案的訓練文字**同源**（`docs/releases/tinylm-v1.md`「授權」）。所有語料與中間檔都在 repo 外，不進版控（`CLAUDE.md`「評測與資料」：2026-10-13 起語料、列檔、計數、分數一律放 repo 外）；repo 只放建置程式、合成的測試資料、數字與雜湊。來源檔放 `~/.cache/shanjie/sources/`，中間檔放 `~/.cache/shanjie/work/…`（`experiments/s2/build_counts.py`、`tools/build_lm.py` 的 `WORK`）。

### 2.1 訓練文字

| 輸入 | 來源與版本 | 授權 | 用在 | 釘住的雜湊（SHA-256） |
|---|---|---|---|---|
| 中文維基百科 dump | `zhwiki-20261001-pages-articles.xml.bz2`，取 dump 順序的前 20 萬篇（`--articles 200000`）；SHA-1 已和官方清單比對相符（`experiments/s2/README.md`、PLAN S2） | CC BY-SA 4.0 | n-gram ×1、小模型 ×1 | `5db9052e…7e2f`（`experiments/model-v5/README.md`「輸入雜湊」） |
| Common Voice 繁體中文（台灣） | commit `ff32a0e`（`experiments/s2/README.md`「語料」） | CC0 | 口語計數 | 原始檔的雜湊：沒有紀錄；轉換後的 `colloquial-train.txt` 見下 |
| Tatoeba 的 cmn 例句 | `cmn_sentences.tsv.bz2`；取得日期與版本：沒有紀錄 | CC BY 2.0 FR | 口語計數 | 同上 |
| `colloquial-train.txt` | `experiments/s2/build_tune.py --part cv` 把上面兩者依句子的 md5 切開：餘 5 等於 0 的 20% 進評測（cvtune），其餘 80% 轉換後寫成這個檔；S7a 記錄原始口語 108,432 行，訓練用 86,802 行（`build_tune.py` 檔頭；研究紀錄 2026-10-11 S7a） | 同上 | n-gram ×5、小模型 ×5 | `3e833d06…b4db` |
| `synth.txt` | Cerebras 上 gpt-oss-120b 合成的台灣口語聊天句，`experiments/s2/synth_colloquial.py`；提示詞只寫主題與語氣，不放評測句；S2 時 13.5 萬句、約 US$9（`experiments/s2/README.md`「語料」） | 模型輸出（模型權重 Apache-2.0） | 同上 | `bec7a6a2…99f1` |

加權是 `tools/build_lm.py` 的 `CORPORA`：維基 20 萬篇 ×1、口語（訓練部分加合成句）×5（`tools/build_lm.py` 的 `CORPORA` 與檔頭）。×5 的來歷：S2 時看著 Discord 與新聞稿的數字挑的，所以那兩組當時已經參與選模，不是獨立驗證集（`experiments/s2/README.md`「方法上的更正」）。

### 2.2 詞庫、轉換表與詞包（斷詞用）

| 輸入 | 內容 | 授權 | 雜湊 |
|---|---|---|---|
| `data/lexicon/mcbpmf-data.txt` | 小麥注音 3.1.1 的 `data.txt`，基底詞庫（在 repo 裡） | MIT | `0deae7b7…b7ac`（`experiments/model-v5/README.md`） |
| `data/lexicon/overlay-add.tsv` | 疊加層：維基與維基詞典的標題，由 `tools/build_overlay.py` 從 2026-10-01 的標題 dump 產生（在 repo 裡；來源 dump 的 SHA-256 在 `docs/PLAN.md` S1 的來源表） | CC BY-SA 4.0 | `348979c8…547a` |
| `data/lexicon/sandhi-add.tsv` | 「一」「不」等的變調讀音（`docs/contracts/s2r-sandhi-variants.md`）。計數時的詞庫**不含**它（`build_counts.py` 的 `_init` 只讀基底＋`overlay-add.tsv`＋`--extra-lexicon`）；`tools/build_lm.py` 的異體合併用 `ime.OVERLAYS`，含它 | MIT | 沒有紀錄（model-v5 README 的表沒列） |
| OpenCC 對照表 | commit `3ac34aa439a9908dd49fa92b5174b46314787ac2` 的 `STCharacters.txt`、`TWVariants.txt`、`STPhrases.txt`、`TWPhrases.txt`；`build_counts.py` 讀之前核對雜湊，不符就中止 | Apache-2.0 | 四個檔的雜湊在 `docs/PLAN.md` S1 的來源表；其中 `STCharacters.txt` `a0ca1601…582b`、`TWVariants.txt` `245b94eb…cb86` 也在 model-v5 README 的表 |
| ACG 詞包 | `data/packs/acg-add.tsv`（`tools/build_acg_pack.py` 從中文維基與 Wikidata 產生，契約 `docs/contracts/acg-pack.md`），model-v5 起當斷詞詞彙的一部分；實際進計數的是 `count_lexicon.py` 過濾後的詞表 | CC BY-SA 4.0／CC0 | 計數詞表 `1c4aa6cc…058f`（model-v5 README） |

批次檔（`experiments/model-v5/repro.bat`、`count.bat`）一開始寫進 `inputs.sha256` 的是：維基 dump、`colloquial-train.txt`、`synth.txt`、`mcbpmf-data.txt`、`overlay-add.tsv`、`TWVariants.txt`、`STCharacters.txt`，`count.bat` 再加 `acg-add.tsv`；計數詞表 `acg-count.tsv` 的雜湊是 `count_lexicon.py` 跑完才追加的。`STPhrases.txt`、`TWPhrases.txt` 不寫進 `inputs.sha256`，是 `build_counts.py` 讀檔時用 `PHRASE_SHA` 核對。上面標「沒有紀錄」的是這些都沒涵蓋的檔。

### 2.3 只用來評測、不進訓練的資料

評測集（dev302、打字測驗、錯字回報、cvtune、wikitune、保留集、使用者的聊天評測集）都不進訓練，規則見 §6 與 `CLAUDE.md`。使用者的私人訊息放在 repo 外，agent 一律不讀；repo 與 Release 說明只寫統計數字（`CLAUDE.md`「評測與資料」）。三個發佈檔「不含任何私人對話資料」（`docs/releases/model-v5.md`、`tinylm-v3.md`）。

## 3. `bigram.sjlm`：n-gram 語言模型

現行配方是 model-v5（`docs/contracts/model-v5.md`、`experiments/model-v5/README.md`）。它沿用 model-v3 的語料與轉換配方，只多了詞包詞進斷詞詞彙；model-v4 是 model-v3 加指紋，沒有重新計數（`docs/contracts/model-v4.md` §1）。

### 3.1 配方的來歷（為什麼轉換與計數長這樣）

| 片 | 做了什麼 | 契約 | 出貨？ |
|---|---|---|---|
| S2（model-v1） | 維基與口語分別用詞庫最高分切分、計 unigram／bigram | `docs/releases/model-v1.md`；PLAN S2 | model-v1 |
| S2n（model-v2） | 先判斷簡體句或繁體句再轉換（維基是簡繁混合）；計數改成詞圖上的**期望次數**（每種切法依機率計入，`--expected`，單條邊期望次數 < 0.01 不記） | `docs/contracts/s2n-simplified-residue.md`（§6 修訂四） | model-v2 |
| S2f（model-v3） | 台灣字形表（OpenCC `TWVariants.txt`）；同讀音的異體寫法在 `build_lm.py` 合併計數；疊加層拿掉只是基底詞異體寫法的詞 | `docs/contracts/s2f-variant-forms.md` | model-v3 |
| S2w | 用 MediaWiki 自己的台灣正體轉換（`--mw`） | `docs/contracts/s2w-mediawiki-zhtw.md` | **沒有出貨**（使用者 2026-10-08 決定；`model-v4` 這個名字改用在「model-v3 加指紋」，`model-v4.md` §3）。重建時**不要加 `--mw`** |
| model-v4 | model-v3 加指紋條目 | `docs/contracts/model-v4.md` | model-v4 |
| model-v5 | 詞包詞進斷詞詞彙；加法計數；衝突詞與片段不進計數 | `docs/contracts/model-v5.md` §0–§9 | model-v5 |

疊加層與讀音修正（`s2r`、`s2r2`）是詞庫端的改動，已經包含在 `overlay-add.tsv`、`sandhi-add.tsv` 裡，重建模型時只要用 repo 裡現有的檔案（`docs/contracts/s2r-sandhi-variants.md`、`s2r2-overlay-readings.md`）。

### 3.2 步驟

下面的指令與環境變數取自 `experiments/model-v5/repro.bat`、`count.bat`、`README.md`；兩個批次檔是**範本**，要先把 `REPO`、`SYNTH` 換成機器上的絕對路徑。慣例：絕對路徑、`start "" /b /wait /affinity FFF`（綁 P-core）、`PYTHONUTF8=1`、`S2_WORK` 指到新目錄（已有 `counts-200000.pkl` 就停，免得混到舊檔）。

1. **固定輸入並記雜湊**：批次檔開頭把 §2.2 末段列的那幾個輸入的雜湊寫進 `%S2_WORK%\inputs.sha256`。除了詞包，雜湊要和上一版相同（model-v5 契約 §2.1）。
2. **（model-v5 的做法）算計數詞表**：`python experiments\model-v5\count_lexicon.py data\packs\acg-add.tsv data\packs\acg-collisions.tsv experiments\model-v5\collision-readings-v5.txt <acg-count.tsv>`。詞包詞只要有一個讀音落在「衝突讀音」裡連續兩個以上音節，整個詞不進計數；model-v5 排除 377 詞（model-v5 契約 §9「片段的規則」）。目的：同音名字的排序維持使用者在 `acg-collisions.tsv` 的決定。
3. **口語計數**（單一程序）：
   `python -u experiments\s2\build_counts_text.py --expected --extra-lexicon <acg-count.tsv> <S2_WORK>\counts-colloquial3.pkl <colloquial-train.txt> <synth.txt>`
   `--extra-lexicon` 只能和 `--expected` 一起用（`build_counts_text.py`）。口語訓練檔直接沿用上一版的 `colloquial-train.txt`，不重產（model-v5 README）。
4. **維基計數**：
   `python -u experiments\s2\build_counts.py --articles 200000 --procs 10 --expected --extra-lexicon <acg-count.tsv>`
   `--articles` 的預設是 50,000（`build_counts.py`），出貨用的是 200,000，要自己給。多程序用 `imap_unordered` 合併，**輸出檔的位元組每次不同**，所以只能比內容（見 §3.4）。
5. **建模型**：`python -u tools\build_lm.py --out <S2_WORK>\bigram-v5-nofp.sjlm`（`S2_WORK` 指向計數目錄）。`build_lm.py` 做的事（檔頭與 `merged_counts`／`build`）：
   1. 讀 `counts-200000.pkl` ×1 與 `counts-colloquial3.pkl` ×5 相加；
   2. 期望次數是小數，加權後每一筆四捨五入成整數、去掉 0；
   3. 異體合併（`variant_classes`、`merge_variants`）：套 `MERGE` 後字串相同、而且讀音集合完全相同的詞併成一類；
   4. 丟掉兩端有一端不在詞彙裡的二元組（加法計數需要，沒有詞包的輸入不受影響）；
   5. 只保留加權次數 ≥ `PRUNE = 2` 的二元組，但前文的總數 t 用剪枝前的完整計數；折扣 `D = 0.75`；
   6. 輸出 SJLM0001（欄位在 `build_lm.py` 檔頭）。
   Python 參考實作是 `reference/proto/lm.py`，Rust 讀同一份檔（`docs/releases/model-v5.md`「產生」）。
6. **加指紋**（見 §3.3）。
7. **對加過指紋的檔算雜湊**，並用它建詞類表（§4）。

model-v5 的最終檔：`bigram.sjlm` 82,535,147 bytes、SHA-256 `f81a021e…13f45af`；不含指紋的版本是 `7961cb9d…`、V = 345,398（研究紀錄 2026-10-10「model-v5」一節的「排除 377 詞後的模型」）。不含指紋版本的位元組數：沒有紀錄。

### 3.3 指紋條目：怎麼處理、為什麼自己建的雜湊不同

- 使用者 2026-10-08 決定：發佈的模型加入用來辨識來源的指紋條目，只公開「含有指紋條目」，不公開是哪些、怎麼放（`docs/contracts/model-v4.md` 開頭、§6）。
- 指紋由**不在 repo 的工具**加入，且要在算雜湊、建詞類表**之前**加（`docs/data-files.md`「版本怎麼往前走」、model-v5 契約 §2.2）。所以：
  - 用 `tools/build_lm.py` 自己重建，得到的是**不含指紋**的檔，雜湊和發佈版不同；
  - `cli/tests/golden.rs` 的 `lm_file_is_the_released_model` 會因此失敗，開發一律下載 Release（model-v4 契約 §1）。
- 驗收：同一個模型加不加指紋，受影響的打字情境裡解碼與預測完全相同；清單與情境不在 repo，由維護者私下跑，只報「N 條；解碼 n 列、相同 n；預測 q 筆、相同 q」（model-v4 契約 §2 第 4 項、model-v5 契約 §3.6）。model-v5 的最終檔核對結果是「全部相同」；N、n、q 的最終數字：沒有紀錄（研究紀錄只寫了中間版本的 336 列、2,090 筆）。
- 這份文件不寫指紋的內容、工具與選法。

### 3.4 重現核對（改了計數程式之後先跑）

先不加 `--extra-lexicon`、用和上一版相同的輸入重算一次，確認之後的比較只反映新東西（model-v5 契約 §2.1、README「重現核對」），任何一項不成立就停（模型檔的位元組不算在內，見第三列）：

| 項目 | 標準 |
|---|---|
| 口語計數 | `counts-colloquial3.pkl` 的 SHA-256 等於 model-v3 的 `13d0ab5b…` |
| 維基計數 | 用 `python3 experiments\model-v5\compare_counts.py <舊的 counts-200000.pkl> <新的>` 比內容：鍵集合、`runs`、`articles` 相同，每個值相對誤差 ≤ 1e-9；舊檔 SHA-256 是 `ca2f1361…` |
| 重建的模型 | 用 `tools/build_lm.py` 建出來；同一個 CLI 執行檔、兩邊都加 `--no-classes`、都不加 `--packs`、加 `--context`，解碼重建的模型與 model-v3（兩邊都不加指紋），dev302、打字測驗、錯字回報，聊天與書面的第一名逐列相同（`top1_sha256` 相同）。不和歷史紀錄的雜湊比；模型檔位元組不同時記下差了幾個條目，但不是停止條件 |

model-v5 實際的結果：三項都成立，重建的模型 `5c7d5a94…` 還和 model-v3 逐位元組相同（研究紀錄 2026-10-10「model-v5 的計數、守門與兩次修訂」；位元組相同是額外的結果，不是標準）。`tools/build_lm.py --check` 重建後和 `--out` 指的檔比 SHA-256、不寫檔；`--out` 預設是 `data/lm/bigram.sjlm`，也就是加過指紋的發佈檔，自己重建的一定不同，所以要比時另外給 `--out <不含指紋的檔>`。不帶 `--check` 也不帶 `--out` 執行會直接覆寫 `data/lm/bigram.sjlm`。

### 3.5 在哪台機器跑、花多久

| 步驟 | 機器 | 時間 | 來源 |
|---|---|---|---|
| 計數、`build_lm.py` | Windows 機器 188（`REPO`、批次檔） | model-v5：每一批 < 8 小時，最長的計數約 1 小時（從排程到寫完）；記憶體沒有量 | model-v5 README「之後」 |
| 同一批（model-v3 的 F4） | 188 | 整批約 11 分鐘，Python 記憶體峰值 11.53 GB（那是候選模型 F 的紀錄） | `experiments/s2f/README.md` |
| 疊加層（`build_overlay.py`） | 不在 188 跑（要讀維基標題 dump）；在哪台跑：沒有紀錄 | 沒有紀錄 | `experiments/s2f/README.md` |
| 加指紋 | 維護者私下 | 沒有紀錄 | model-v4 契約 §2 |

「約 1 小時」與「約 11 分鐘」差很多，來源沒有寫原因（model-v5 加了詞包斷詞與加法計數；兩次的量法也不同，一個是排程到寫完、一個是整批）。**沒有紀錄**能說明差在哪，這裡不推論。

### 3.6 `UNSEEN_OVERLAY_PENALTY` 與模型檔的關係

語料沒出現過的疊加詞與詞包詞，分數是「同長度基底詞的第 25 百分位減 `UNSEEN_OVERLAY_PENALTY`」，這個常數在核心（`core/src/lm.rs`、`reference/proto/lm.py`），**不在模型檔裡**（model-v5 契約 §0）。model-v5 掃過 {1.0, 1.5, 2.0, 3.0}，最後使用者決定維持 1.0，`UNSEEN_OVERLAY_PENALTY` 沒有改（契約 §9「扣分的修訂」）。重建模型時若要動它，是核心與 golden 的改動，不是重建檔案的一部分。

## 4. `classes.sjc`：詞類表

詞類表由兩段做成：先分群（`experiments/s2-classes/cluster.py`），再把分群結果與模型一起寫成檔（`tools/build_classes.py`）。規格見 `docs/contracts/s2k-word-classes.md`、`experiments/s2-classes/README.md`。

1. **分群**（`cluster.py`）：Brown 式 exchange 演算法，目標是類別 bigram 的互資訊。前 N = 40,000 個高頻詞：前 K 個各自成類，其餘貪婪貼進最佳類別，再依詞頻順序 exchange；N 之外的詞用和已分類詞的 bigram 貼進最佳類別（`cluster.py` 檔頭、s2-classes README「方法」）。K = 512、μ = 0.8：K 在第一輪研究時用 cvtune、wikitune 比 256 與 512 後挑 512；μ 在 model-v3 重跑時，用 cvtune-tune 與 wikitune 兩個調參集，依事先寫死的規則（四格淨修好總和最大，同分取弄壞較少、再取較小的 μ）從 {0.8, 0.9, 0.95, 0.98} 選到 0.8（s2-classes README「調參」「model-v3 重跑」；規則 `s2k-word-classes.md` §1.2）。
   - 輸入：和 `build_lm.py` 同一份加權計數（`S2K_COUNTS` 指到計數目錄，`S2K_OUT` 指到輸出資料夾）。第一次執行把計數轉成 `edges.npz`，放在輸出資料夾。
   - model-v3 那次（從頭分群）：N = 40,000、K = 512、12 輪，在 188 約 12 分鐘，每輪約 48 秒；輸出 `edges.npz` `ea772a02…476c`、`cls-40000-512.npz` `5fee9a50…2225`（s2-classes README）。`cluster.py` 的 `--passes` 預設是 6；紀錄只寫「12 輪」，指令裡有沒有 `--passes 12`：沒有紀錄。
2. **classes-v3 的分群（model-v5）**：
   `python experiments\s2-classes\cluster.py --N 40000 --K 512 --keep-from <classes-v2 那次的輸出資料夾>`
   （`S2K_COUNTS` 指向 model-v5 的計數；`S2K_OUT` 必須是新資料夾，和 `--keep-from` 相同會被擋，因為 `prepare()` 會沿用資料夾裡現成的 `edges.npz`。）
   - 舊詞彙裡的詞沿用 classes-v2 的類別（沒有類別的仍沒有），**不跑交換**；前 N 詞裡的新詞依頻率貼進最佳類別，其餘新詞照罕見詞的方式貼。
   - 為什麼不從頭分群：用 model-v5 的計數重新分群，一般詞的詞類整體漂移，s2r 探針書面 69 → 66 句；沿用舊分群後回到 69（model-v5 契約 §9）。
   - model-v5 的結果：前 4 萬詞裡舊詞 39,457、新詞 543，互資訊 0.8351；輸出 `cls-40000-512.npz` `af637997…`（s2-classes README）。
   - **所以自己從頭分群會得到不同的檔**；沿用的 classes-v2 分群資料在 188 的 `work/s2k`（model-v3 的計數 `work/s2f4`），不在 repo、也沒有公開（`docs/data-files.md`「怎麼產生」）。
3. **寫成檔**：`tools/build_classes.py --edges <S2K_OUT>\edges.npz --cls <S2K_OUT>\cls-40000-512.npz --lm <加過指紋的模型> --out classes.sjc`（在 Mac 跑，model-v5 README「之後」）。這一步：
   - 用 `edges.npz` 的邊與分群重算類別 bigram 表 `Pc`（平滑 ε = 0.1）與每個詞的發射機率；
   - 依**模型**的詞 id 存（`<s>` 是類別 K+1、`</s>` 是 K+2）；模型裡計數為 0 而被剪掉、卻有類別的詞，不套類別項（`s2k-word-classes.md` §4.1 的更正）；
   - **檔頭寫入模型檔的 SHA-256**，這就是綁定。所以一定要對**加過指紋**的模型建（`docs/data-files.md`）。
4. **產出核對**：model-v5 的最終 `classes.sjc` 5,575,836 bytes、SHA-256 `75a5efb3…`（model-v5 契約 §9）。分群那一步只有一個確定性的演算法，沒有隨機種子（s2-classes README「注意事項」：只有 1 個分群種子，確定性演算法，無隨機）。單元檢查：`python3 experiments/s2-classes/test_cluster.py`。
5. **classes-v2 的做法**：不重新分群，對 model-v4 重跑 `build_classes.py`，和 classes-v1 只差檔頭的模型雜湊（`docs/contracts/model-v4.md` §1）。

classes-v3 分群在 188 約花多少時間：沒有紀錄（model-v5 README 只寫每一批 < 8 小時）。

## 5. `tinylm.sjtl`：語意校正的小型字元語言模型

小模型的工作：n-gram 解碼出前 8 名候選後，用字元語言模型替每個候選打分（`LL`），照 `s'(c) = s_ngram(c) + α·LL(c)` 重排；只在 n-gram 第一、二名的分差小於 τ 時重排，而且融合最高分的候選要比第一名高出 δ 以上才換（`docs/contracts/s7a-tinylm.md` §2.3、`s7c2c-delta.md` §2.2）。它只能從這 8 個候選裡挑，不產生新文字（`docs/releases/tinylm-v3.md`）。

### 5.1 訓練文字與字表

`experiments/s7a-tinylm/README.md`「檔案」「契約沒寫死、我做的決定」、`docs/contracts/s7a-tinylm.md` §2.2。

1. **產生訓練文字**：`python -u prep.py runs --work %W% --coll <colloquial-train.txt> --synth <synth.txt> --procs 10`。用和 n-gram 計數**同一組函式**（`build_counts` 的 `articles`、`convert`、`HAN`），維基前 20 萬篇＋口語兩個檔，每個連續漢字段（≥ 2 字）一個序列。核對輸入雜湊（維基 dump `5db9052e…`、`colloquial-train.txt` `3e833d06…`、`synth.txt` `bec7a6a2…`、`TWVariants.txt` `245b94eb…`、`STCharacters.txt` `a0ca1601…`），不符就停（`prep.py` 的 `EXPECTED`）。**不用** S2w 的轉換，也**不用**詞包（model-v5 計數那一步才有）。
2. **建構檢查**：`python -u buildcheck.py construction --coll … --out … [--charcount <DIR>/charcount.json]`：口語那一半對原始來源逐行算 `is_tune(raw)`，核對 `is_tune` 為假的行數等於 `colloquial-train.txt` 的行數，不符就停（結束碼 1）；它核對的是切分，不是「沒有整句重疊」（第 4 步量到的整句重疊就不是 0）。維基那一半只比 `charcount.json` 記的條目數和 wikitune 起點 300,000 兩個數字：給了 `--charcount` 而條目數超過 300,000 也會停（結束碼 1；給了但檔案讀不到也會出錯），沒給時報告寫 `NOT CHECKED`、不會失敗（研究紀錄 2026-10-11 S7a 的〔更正〕：沒有實際讀條目核對）。
3. **字表與編碼**：`python -u prep.py encode --work %W%`。字表取訓練文字（維基、口語各算一次，不乘 5）出現 ≥ 5 次的字，次數多的在前、同次數依碼位，其餘 UNK；id 0／1／2 是 PAD／UNK／BOS。字本身 9,714 個，加三個特殊符號共 9,717 個 id（研究紀錄 2026-10-11 S7a 的更正）。
4. **記錄用的整句重疊比例**：`buildcheck.py substr`：調參集正解句整句出現在訓練文字裡的比例，cvtune 507／3,791（13.37%）、wikitune 671／2,827（23.74%）。只記錄，不是停止條件。

### 5.2 序列、批次與模型大小

| 項目 | 值 | 來源 |
|---|---|---|
| 序列 | 每段 `BOS` ＋ 漢字，不把多段接在一起；超過 65 個 token 的段切成重疊 1 個 token 的片 | S7a README |
| 批次 | 同批只放同長度序列，每批約 32,768 token；一個 epoch，維基各片 ×1、口語各片 ×5，打散；批次順序種子 20261010 | S7a README、`train.py`、`batching.py` |
| 架構 | pre-LN decoder，tied embedding，可學習位置（context 64），GELU tanh 形式，MLP 4d，無 dropout | S7a README |
| 出貨的大小 | M：6 層、d 384、6 頭；1,440 萬參數 | `s7a-tinylm.md` §2.2、`torch_model.py` 的 `CONFIGS` |
| 試過的其他大小 | S（4 層、d 256）：沒過；L（8 層、d 512）、XL（12 層、d 640）：沒有出貨（見 §5.5） | 研究紀錄 2026-10-11 S7a／S7b |

### 5.3 訓練

指令：`python -u train.py --size M --work %W% --out %W%`（`--work` 放 `vocab.txt`、`ids-wiki.npy`、`ids-coll.npy`）。輸出 `tinylm-M.npz` 與 `train-M.json`；結束碼 2 是發散（`train.py` 檔頭）。

| 項目 | 值 | 來源 |
|---|---|---|
| 最佳化 | AdamW（β 0.9／0.95，權重衰減 0.1 只給矩陣）、峰值學習率 1e-3、前 200 步線性暖機再 cosine 到 0、梯度裁切 1.0、CUDA 上 bf16 autocast | `train.py`、S7a README |
| 暖機 | 偏離契約（契約只寫 AdamW＋cosine）；沒有跑過不暖機的對照，需不需要沒有量過 | 研究紀錄 2026-10-11 S7a |
| 種子 | 20261010 | `train.py` 的預設 |
| 步數與 token | 8,836 步，255,373,634 token | 研究紀錄 2026-10-11 S7a |
| 出貨那一個權重 | `tinylm-M.npz` SHA-256 `32308122782d3c5aba8a838521f031a466671d47d48ec0f54dcfb9256451815e`，沒有另外發布 | `docs/releases/tinylm-v2.md`、`tinylm-v3.md` |
| 最終損失 | M 3.993 nats／token | 研究紀錄 2026-10-11 S7a |

**出貨的 M 是在 188 上訓練的**：Windows 機器 188，torch 2.11＋CUDA，GPU 是 RTX 3070（8 GB），用時 5,209 秒（研究紀錄 2026-10-11 S7a；契約 `s7a-tinylm.md` §5 寫 RTX 3070、`torch 2.11＋cu128`；研究紀錄 2026-10-11 S7b 的 XL 一節也提到 8 GB GPU）。S7a 的 S 與 S7b 的 L 也在 188（S 2,641 秒、L 12,262 秒）。

### 5.4 匯出成 `tinylm.sjtl`（int8）

指令（`experiments/s7a-tinylm/export_sjtl.py` 檔頭、`docs/releases/tinylm-v4.md`「產生」）：

```sh
python3 experiments/s7a-tinylm/export_sjtl.py --npz ~/.cache/shanjie/work/s7a/tinylm-M.npz \
  --out <輸出>.sjtl --type int8 --ngram-model data/lm/bigram.sjlm \
  --chat 0.3 2 0.25 --formal 0.7 1 2 \
  --levels 1 2 3 0.3 2 0.25 0.7 0.5 2 0.5 1 0.25
```

- 格式 SJTL0001（檔頭 100 bytes：magic、版本、權重型別、形狀、聊天與書面各一組 α、τ、**32 bytes 的 n-gram 模型 SHA-256**）；給 δ 時寫 SJTL0002（再多 16 bytes：聊天 δ、書面 δ）；再給 `--levels` 時寫 SJTL0003（再多 96 bytes：聊天保守、聊天積極、書面保守、書面積極各一組 α、τ、δ，檔頭 212 bytes）。δ 要兩邊都給或都不給（`export_sjtl.py`、`docs/contracts/s7c-core-tinylm.md` §2.1、`s7c2c-delta.md` §2.1）。
- 二維權重每列對稱 int8 加一個 float32 縮放（`latency.quantize_int8`），載入時還原成 float32；一維向量是 float32（`export_sjtl.py`）。
- `--ngram-model` 讀的是「加過指紋的發佈版模型」，它的雜湊被寫進檔頭，這就是和 n-gram 的綁定（§1.2）。
- 重現同一個檔：`tinylm-v4` 用上面的指令（含 `--levels`）匯出，SHA-256 必須是 `7b39c7529071d97fa911380ce96aad93f9f89b69ef554e383eacacca425c0865`，14,638,320 bytes；它和 `tinylm-v3` 的檔只差檔頭（116 變 212 bytes，多 96 bytes），字表與張量逐位元組相同（`docs/releases/tinylm-v4.md`）。`tinylm-v3`（不帶 `--levels`）的雜湊是 `7e73ef67…12ce`，14,638,224 bytes，和 `tinylm-v2` 的檔只差 9 個 byte（`docs/contracts/s7h-tinylm-v3.md` §2.1）。
- int8 夠用：M 的權重 int8 對 float32 在 12 格都沒有「淨值為負而且 p < 0.05」（研究紀錄 2026-10-11 S7b）。Rust 的 int8 路徑與 numpy 逐列相同（最大 |ΔLL| 約 1.6e-5，門檻 1e-4；研究紀錄 2026-10-11 S7c-1）。

### 5.5 融合參數 α、τ、δ：怎麼調、每一版怎麼選的

**格點**（`fusegrid.py`）：α ∈ {0.1, 0.2, 0.3, 0.5, 0.7, 1, 1.5, 2}；τ ∈ {0.5, 1, 2, 3, 5, 不設門檻}；δ ∈ {0, 0.25, 0.5, 1, 1.5, 2, 3, 4}（δ 從 S7e 起才有）。**打分流程**（S7a README「main 的命令順序」）：

1. 用出貨的 n-gram（model-v5＋classes-v3，`--context`、不開詞包）對每個集合各產生一份前 8 名候選（`run_sets.py dumps`）；
2. 用小模型替每個候選算 `LL`（`run_sets.py score`，torch 或 numpy 後端）；
3. `fusegrid.py` 掃格點、選值、做表（`tools/evalstats.py compare`）；
4. 一致性關卡：numpy 對 torch，最大 |ΔLL| < 1e-4 才往下（S7a 實測 M 為 1.48e-5）。

**每一版的值與選法**：

| Release | 聊天 α／τ／δ | 書面 α／τ／δ | 選法 | 來源 |
|---|---|---|---|---|
| `tinylm-v1` | 0.7／2／（無） | 0.7／2／（無） | S7a：cvtune＋wikitune 合計 top1 最高，同分取 τ 較小、再取 α 較小；選好凍結。S7d 只用和訓練文字沒有整句重疊的調參列重選，M 選到的值完全不變 | `s7a-tinylm.md` §2.3、§8；`s7d-tune-overlap.md`；研究紀錄 2026-10-11 S7a、S7d |
| `tinylm-v2` | 0.2／1／0.25 | 0.7／1／2 | S7e：在 cvtune＋wikitune 合計（6,618 列）上，照「改對 − 10 × 改壞」選；`fusegrid.py --break-weight 10`。「改壞 1 句 ≈ 改對 10 句」是使用者的權重 | `s7e-fewer-breaks.md` §2.2；研究紀錄 2026-10-12 S7e；`docs/releases/tinylm-v2.md` |
| `tinylm-v3` | **0.3／2／0.25** | 0.7／1／2（不變） | E2：計分改 1:1 的「改對 − 改壞」、改壞率 ≤ 0.5%；在**維護者自己的聊天訊息**建的調參期（5,761 列）上選，先凍結（`judge.py --select R3p --tuning chatclause --weight 1 --cap 0.005`；`R3p` 只是名稱（輸出那一列與 rowstats 檔名的前綴），值由 `--tuning`、`--weight`、`--cap` 選出），之後只在判斷期 2026-08（6,559 列）算一次 | `e2-chat-eval.md`；`s7h-tinylm-v3.md` §0；研究紀錄 2026-10-13「E2 的新調參期與 R3′」「E2 判斷期（2026-08）的表與結論」 |
| `tinylm-v4` | 0.3／2／0.25（同 v3） | 0.7／1／2（同 v3） | 標準值不變，另加保守與積極兩檔（SJTL0003）。保守：聊天 1／2／3、書面 0.7／0.5／2；積極：聊天 0.3／2／0.25（選出的 0.5／1／0 在 `lytune` 改對 236 < 改壞 374，沒過出貨門檻，照規則寫成標準值）、書面 0.5／1／0.25。聊天用維護者的聊天訊息建的調參集，書面用 cvtune＋wikitune，依各檔的改壞上限或權重選值，再過契約 §4.3 的門檻 | `rerank-strength.md` §11；`docs/releases/tinylm-v4.md`；研究紀錄 2026-10-14「語意校正強度修訂一」 |

注意事項：

- **v2、v3、v4 與 v1 的權重相同**（同一個 `tinylm-M.npz`），只是檔頭的融合參數不同；v3 與 v2 的檔只差檔頭裡聊天的 α、τ（9 個 byte）（`docs/releases/tinylm-v2.md`、`tinylm-v3.md`）。所以換融合參數不需要重新訓練，只要重新匯出。
- v3 的聊天值是在維護者私人訊息建的評測集上選的（檔案裡沒有任何訊息的文字，評測集不公開）。**repo 裡只有選出的值、判斷用的程式（`experiments/e2-chateval/judge.py`）與數字，沒有那份評測集**，所以別人無法用 repo 重選這三個數字（`docs/releases/tinylm-v3.md`、`CLAUDE.md`「私有資料」）。
- 判斷期用過就退役，下一次要用新的月份（`e2-chat-eval.md` §2.1 的順序）。
- 融合參數是在 model-v5＋classes-v3 上調的；換 n-gram 或詞類表就要重調（§1.2）。
- v3 判斷期的結果：聊天改對 125／改壞 16、淨值 +109、改壞率 0.24%（`docs/releases/tinylm-v3.md`）。

### 5.6 試過、沒有出貨的方向

| 方向 | 結果 | 來源 |
|---|---|---|
| S 與 M | M 過 S7a §4，S 沒過 | `s7a-tinylm.md` §8 |
| L（8 層、d 512） | 調參集比 M 好，使用者的聊天比 M 差，沒過；維持 M | 研究紀錄 2026-10-11 S7b |
| XL（12 層、d 640） | 在 188 上因 8 GB GPU 放不下而停（約 22.5 小時、預算約 8 小時）；之後在 Colab 訓練，S7e 用同一條規則比，M、L、XL 在使用者的聊天上都只差第 1 項，模型變大沒有幫助（研究紀錄 2026-10-12 S7e） | 同左 |
| S7f：加真人口語與生成句重訓 M | 兩項都沒過（使用者聊天上的淨值 A1 −126、A2 −107，沒贏過只換平台與種子的雜訊界線 −21），**不再加量，核心的小模型不換**；那些研究語料沒有進任何已發佈的檔案 | `docs/contracts/s7f-colloquial.md` §7、`LICENSES/data.md`「研究語料（未出貨）」、PLAN S7f 狀態 |

## 6. 出貨前的評測關卡

細節各有專門文件，這裡只列順序與規則。

| 關卡 | 規則 | 在哪裡 |
|---|---|---|
| 一律「同一張表」 | 改到選字結果的改動，dev302、打字測驗、錯字回報，聊天與書面兩種設定各報一行：n、top1 基準／新、改對／改壞、精確 McNemar p、CER 基準／新、Δtop1 與 ΔCER 的配對 bootstrap 95% 區間；做法是基準與新版各跑 CLI 的 `--rowstats`，再 `python3 tools/evalstats.py compare BASE CAND --label 名稱`；`--net` 多報淨值、改壞率與改壞數的 95% 精確 Poisson 上界 | `CLAUDE.md`「評測與資料」、`docs/contracts/eval-stats.md` |
| 守門與否決 | cvtune、wikitune、dev302、打字測驗任何一格出現「配對檢定 p < 0.05 而且淨值為負」就否決；調參數只用 cvtune、wikitune，不用 dev302 | model-v5 契約 §3.1、§3.2；`CLAUDE.md` |
| 固定探針 | 「大概十分鐘後到」、竈門、好吧、s2r 探針不退步；字形探針 `experiments/s2f/probe.txt` 從 S2f 起每次重建都跑 | model-v5 契約 §3.4；字形探針見 `docs/contracts/s2f-variant-forms.md`、`s2k-word-classes.md` |
| 動漫詞包 | `eval/dev/acg/*` 開詞包的表；詞包衝突在新模型上重驗，沒處置的衝突讀音要停下來問使用者 | model-v5 契約 §3.3 |
| 規模 | 模型 < 100 MB；每一批計數 < 8 小時、記憶體 < 48 GB（model-v5 的記憶體沒有量）；每鍵延遲照既有的 perf 測試 | model-v5 契約 §3.5 |
| 指紋核對 | 見 §3.3 | model-v4 契約 §2、model-v5 契約 §3.6 |
| golden | 所有依模型的 golden 用 Python 參考實作重產，Rust 逐位元組相同；`sp-predict.txt` 照它的程序 | model-v5 契約 §2.5 PR 2；`docs/data-files.md` |
| 保留集 `eval/holdout/` | 只在一片收尾時由 **fresh verifier** 跑一次、只回數字：基準與新版各跑一次 `--set holdout --rowstats`（寫到它自己的暫存目錄、檔內只能有 `[0-9\t\n]`），再跑 `tools/evalstats.py compare`，只回那一行表格，然後刪掉兩個 rowstats。其他時候不讀、不用來調參 | `CLAUDE.md`「評測與資料」 |
| 私人資料 | 使用者的聊天評測集、Discord 調參集由維護者（main）跑，只貼表格數字；agent 一律不讀。2026-10-13 起重運算不在 Mac 上跑，私人評測集可以放 188 或 Colab，搬過去要核對雜湊、只用來評測 | `CLAUDE.md`「評測與資料」 |
| 語意校正專屬 | 判斷期淨值 > 0、改壞率點估計 ≤ 0.5%、公開與守門集合沒有「改對 < 改壞而且 p < 0.05」的格；保留集在建 Release 之前跑一次 | `s7h-tinylm-v3.md` §0、§3 |
| 合併關卡 | 本地 `/code-review` 沒有未處置的問題、fresh `pilotfish:verifier` CONFIRMED、CI 全綠，merge commit（repo 未滿 100 顆星） | `CLAUDE.md`「一片工作的流程」第 4 項 |
| 跨版本基準 | 輸入法版本（`v*`）發版前才跑，不是模型檔發佈前；指令與條件只寫在 `docs/verification.md`「跨版本基準測試」（`CLAUDE.md`「發版」），這裡不另抄 | `docs/verification.md`「跨版本基準測試」、`docs/contracts/sbench-v2.md` |

## 7. 建 Release 與切換

整體順序（model-v4／model-v5 契約的做法；`docs/contracts/model-v4.md` §4、`model-v5.md` §2.5）：

1. **PR 1（不換檔）**：契約、`docs/releases/<tag>.md` 說明檔、研究紀錄；本地驗收（§6）通過後開，`/code-review`、fresh verifier、CI 全綠後 merge。保留集在這個階段、**建 Release 之前**跑（`s7h-tinylm-v3.md` §2.1）。
2. **建 Release（對外動作，先問使用者）**：指令、核對步驟與規則（資料類 Release 不標 Latest、上傳前後用說明檔的雜湊核對、說明檔不能留草稿標記）只寫在 `docs/releases/README.md`（`CLAUDE.md`「發版」），照那裡做，這裡不另抄一份。
3. **PR 2（換引用）**：同一個 PR 更新 `docs/data-files.md`「換版時同一個 PR 更新」那一串：雜湊檔（`data/*.sha256`）、CI 兩個 job 與 `release.yml` 的下載、`cli/tests/golden.rs` 的雜湊與訊息、`core/tests/engine_*.rs` 的缺檔訊息、`macos/Tests/ShanjieKitTests/Support.swift`、`scripts/build-app.sh`、README、CONTRIBUTING、`docs/verification.md`、`LICENSES/data.md`、`docs/PLAN.md` 的模型檔那一條與 `docs/data-files.md` 本身；換 `tinylm.sjtl` 時另外照 `docs/releases/README.md`，融合參數有變時要重量 selftest 的那一句（`Selftest.rerankRowStandard` 等）；依模型的 golden 重產。
4. **舊 Release**：保留，可以回到那一版；model-v3 與 classes-v1 是依 `docs/contracts/model-v4.md` §4 第 4 步的條件移除的例外（`docs/data-files.md`）。
5. **換檔順序**：先把新雜湊合進 main 會讓 CI 拿舊檔對新雜湊而失敗，所以雜湊檔和 CI 下載在**同一個切換的 PR** 裡改；第一次加的新檔沒有人讀，雜湊檔可以和說明檔一起先進 main（`docs/releases/README.md`「雜湊檔什麼時候改」）。

不在這份文件範圍：輸入法版本（`v*`）的推 tag、簽章與公證；那是另一套流程（`docs/releases/README.md`、`CLAUDE.md`「發版」）。

## 8. 現在在哪裡訓練

**現行做法（維護者 2026-10-12 起）**：

- **模型訓練跑在 Google Colab**：A100 80GB，一次訓練約 10 分鐘（研究紀錄 2026-10-12 S7e／S7f：S7f 的 M 重訓 554–560 秒、種子 20261012；S7e 的 L 904 秒、XL 1,622 秒、用 `train.py` 的預設設定；都是 8,836 步、每批 32,768 token）。Windows 機器 188 只做計數與推論，這是維護者的做法（研究紀錄 2026-10-12 S7f 寫的是「這一片的訓練全部在 Colab」），還沒有寫成 repo 的規則；公開資料由 Colab 的 VM 自己抓。
- 訓練送上 Colab 的只有公開的程式與資料。私人評測集（discordtune、E2 的聊天列檔）從 2026-10-13 起可以上 Colab 算評測，不進訓練；學習檔與原始聊天紀錄不上（`CLAUDE.md`「評測與資料」、`experiments/s7a-tinylm/README.md`「Colab：資料怎麼傳」）。
- 資料怎麼傳（2026-10-12 實測，使用者定案；同一節）：
  - 公開資料讓 VM 自己抓：維基 dump 走 your.org 鏡像、8 路平行分段，約 39.5 MiB/s（官方站單線約 4.8 MiB/s）；Hugging Face 約 54.5 MiB/s、GitHub 約 12.5 MiB/s。
  - 只在 Mac 上的檔案：用有時效的下載網址交給 VM，網址在 Mac 上產生、不印出、用完刪掉嵌著網址的腳本。
  - 結果用 `colab download` 取回，約 3.2 MiB/s，適合幾百 MB 以內。
  - `colab upload` 約 2.2 MiB/s，不用；Google Drive 每個 session 都要使用者授權一次，不用。
- 工具：Google 官方的 `google-colab-cli` 0.7.4；`colab new --gpu A100 --high-mem` 拿到 A100 80GB（研究紀錄 2026-10-12 S7e「Colab 的做法」）。
- Colab 用量記帳：S7f 整片 14.28 個運算單元（研究紀錄 2026-10-12 S7f）；S7f 契約 §5 定的是整片的 Colab 預算（10 個運算單元，三次 M 的訓練加備案），不是每個 session 的上限。

**過去實際的跑法**（事實，來自紀錄）：

| 訓練 | 機器 | 時間 | 來源 |
|---|---|---|---|
| S7a 的 S、M；S7b 的 L（出貨的 `tinylm-M.npz` 就是這一次） | 188，RTX 3070，torch 2.11＋CUDA | S 2,641 秒、M 5,209 秒、L 12,262 秒 | 研究紀錄 2026-10-11 S7a、S7b |
| S7b 的 XL | 188；前 100 步用了 917 秒，照這速度推估 8,836 步要約 22.5 小時（推估，不是實際跑的時間），超過預算所以停掉 | 沒有權重 | 研究紀錄 2026-10-11 S7b「XL 觸發停止條件」 |
| S7e 的 L、XL；S7f 的 A0′、A1、A2 | Colab A100 80GB | 904／1,622 秒；A0′ 560、A1 554、A2 559 秒 | 研究紀錄 2026-10-12 S7e、S7f |
| n-gram 計數、`build_lm.py`、分群 | 188 | 見 §3.5、§4 | model-v5 README、s2-classes README |

兩個要小心的事實：

- **在 Colab 重訓同一份資料，得到的是不同的權重**：S7f 的 A0′（同資料、固定訓練預算，種子 20261012）權重 `4210eba1…`，和出貨的 `32308122…` 不同（研究紀錄 2026-10-12 S7f）。A0′ 用的種子也不同，所以來源沒有把「換平台」和「換種子」分開；S7f 把這個差距當成「只換平台與種子」的雜訊界線 −21（PLAN S7f 狀態）。
- 研究紀錄寫過一次 188 上的 A0″（同資料、同種子）在約 6,200 步被停掉、沒有用，原因是維護者說訓練用 Colab 省時間；因此「188 與 Colab 在同種子下是否位元組相同」：沒有紀錄。

## 9. 這份文件沒辦法重現的部分，與未決事項

### 9.1 只靠 repo 無法精確重現

| 項目 | 原因 | 來源 |
|---|---|---|
| 發佈的 `bigram.sjlm` 雜湊 | 指紋條目由不在 repo 的工具加入，自己重建得到的是不含指紋的檔（model-v5 是 `7961cb9d…`），雜湊不同 | `docs/data-files.md`、model-v4 契約 §6 |
| `classes.sjc` | classes-v3 沿用 classes-v2 的分群（188 的 `work/s2k`，`cls-40000-512.npz` `5fee9a50…`），那份資料不在 repo；自己從頭分群的結果不同。另外詞類表綁加過指紋的模型 | `docs/data-files.md`、model-v5 契約 §9 |
| 維基計數的位元組 | 多程序 `imap_unordered` 讓鍵順序與浮點加總順序每次不同，只能比內容（誤差 ≤ 1e-9） | model-v5 契約 §2.1 |
| 輸入資料本身 | 維基 dump、Common Voice、Tatoeba、`synth.txt` 都在 repo 外；`synth.txt` 是模型輸出（Cerebras 上的 gpt-oss-120b），重新生成不會得到同一份，只能核對雜湊 `bec7a6a2…99f1`；Wikimedia 會下架舊 dump（PLAN S1：「Wikimedia 會下架舊 dump」，所以來源檔雜湊要記下來） | §2、`docs/PLAN.md` |
| 疊加層與詞包 | `overlay-add.tsv` 在 repo；詞包 `data/packs/acg-add.tsv` 也在 repo，但重建詞包要讀維基，且詞包的 (c) 規則會用 `data/lm` 的模型解碼（見 §9.3） | model-v5 契約 §9「待辦」 |
| 小模型權重 | 出貨的 `tinylm-M.npz` 是 188 的 GPU 訓練出的；換平台（Colab）重訓得到不同的權重（§8）；是否能在同一台機器上位元組重現：沒有紀錄 | 研究紀錄 2026-10-11、2026-10-12 |
| ×5 的口語權重、小模型 v3 的聊天值 | ×5 是看著 Discord 與新聞稿的數字挑的（S2），v3 的聊天值是用維護者的聊天訊息選的；這些私人資料不在 repo，只有數字 | `experiments/s2/README.md`、`docs/releases/tinylm-v3.md` |
| 私人口語資料 | 三個發佈檔都**沒有**用私人訊息訓練（`docs/releases/model-v5.md`、`tinylm-v3.md`）；但私人訊息影響了出貨的選擇：口語權重 ×5 是看著 Discord 的數字挑的（上一列）、S7a 用 discordtune 決定出貨 M、model-v5 的守門報了 Discord 調參集、v3 的聊天值在維護者的訊息上選。研究用的真人口語與生成句（S7f）沒有進任何已發佈的檔案 | `LICENSES/data.md`「研究語料（未出貨）」 |

### 9.2 沒有紀錄的事

- Common Voice、Tatoeba 原始檔的雜湊與 Tatoeba 取得日期；
- `sandhi-add.tsv` 的雜湊（`experiments/model-v5/README.md` 的輸入表沒列）；
- 疊加層重產與加指紋花多久；
- model-v5 計數的記憶體用量；classes-v3 分群花多久；
- 不含指紋的 model-v5 的位元組數；指紋核對最終的 N／n／q；
- 12 輪分群時的指令是否帶 `--passes 12`；
- 同種子、同資料在 188 與 Colab 的小模型權重是否相同；
- `tinylm-M.npz` 在 188 的訓練指令的完整參數（只有 `train.py --size M --work %W% --out %W%` 與預設值）。

### 9.3 未決與之後（model-v6 計畫）

| 項目 | 狀態 | 來源 |
|---|---|---|
| **Kneser–Ney 回退分布（側檔）** | **沒有出貨**。離線量測有效（調參集多對 33 列），使用者決定併進 model-v6。核心已能讀 `kn.sjkn`（SJKN0002；沒有側檔時輸出不變），App 與 CI 不放側檔。側檔由 `tools/kn_cont.py` 產生，測試在 `tools/test_kn_cont.py`；model-v6 那一片才產生正式側檔、釘雜湊、開啟。開啟之後 `--tinylm` 現在不能和它同時用，融合參數要重調 | `docs/contracts/kn-smoothing.md` §7、`kn-core.md`、PLAN |
| **領域詞包（資訊與 AI、醫藥、法律）** | 跟 model-v6：詞包的詞要放進 model-v6 的斷詞詞表、加領域語料重建（動漫詞包在 model-v5 的做法） | `docs/PLAN.md`「領域詞包」 |
| **錯字回報當線索補語料** | 下一次重建模型（model-v6）一起訓練；評分不能用當過線索的列 | `docs/PLAN.md` |
| **詞包 (c) 規則的待辦** | 重建詞包前要先改 `tools/build_acg_pack.py` 的 (c) 規則（`LEXICON_RULE_ROUNDS`）或加例外，否則換成 model-v5 的模型解碼會丟掉使用者決定保留的名字 | model-v5 契約 §9「待辦」 |
| **model-v6 需要的連鎖** | `model-v6`＋`classes-v4`＋下一個 `tinylm-v…`（§1.3）；小模型的**權重**要不要用新語料重訓：沒有紀錄（目前只決定融合參數與檔頭雜湊要重做） | `docs/data-files.md` |
| **聊天評測的下一個判斷期** | 2026-08 這一期已用掉；下一次要用新的月份 | `e2-chat-eval.md` |

## 10. 引用的來源

| 文件／程式 | 這份文件用在 |
|---|---|
| `docs/data-files.md` | §1、§3.3、§7、§9 |
| `docs/releases/README.md`、`model-v1`–`v5.md`、`classes-v1`–`v3.md`、`tinylm-v1`–`v3.md` | §1、§2、§5、§7 |
| `tools/build_lm.py`、`experiments/s2/build_counts.py`、`build_counts_text.py`、`build_tune.py`、`synth_colloquial.py`、`experiments/s2/README.md` | §2、§3 |
| `docs/contracts/model-v5.md`、`experiments/model-v5/README.md`、`repro.bat`、`count.bat`、`count_lexicon.py`、`compare_counts.py` | §3、§4、§6 |
| `docs/contracts/model-v4.md`、`s2f-variant-forms.md`、`experiments/s2f/README.md`、`s2n-simplified-residue.md`、`s2w-mediawiki-zhtw.md`、`s2r-sandhi-variants.md`、`s2r2-overlay-readings.md` | §3 |
| `tools/build_classes.py`、`experiments/s2-classes/cluster.py`、`experiments/s2-classes/README.md`、`docs/contracts/s2k-word-classes.md` | §4 |
| `docs/contracts/kn-smoothing.md`、`kn-core.md`、`tools/kn_cont.py`、`tools/test_kn_cont.py` | §9.3（側檔沒有出貨） |
| `experiments/s7a-tinylm/README.md`、`prep.py`、`train.py`、`torch_model.py`、`export_sjtl.py`、`fusegrid.py` | §5、§8 |
| `docs/contracts/s7a-tinylm.md`、`s7b-larger-lm.md`、`s7c-core-tinylm.md`、`s7c2c-delta.md`、`s7d-tune-overlap.md`、`s7e-fewer-breaks.md`、`s7f-colloquial.md`、`e2-chat-eval.md`、`s7h-tinylm-v3.md` | §5、§6、§8 |
| `docs/research-log.md` 的 model-v5、S7a、S7b、S7c-1、S7d、S7e、S7f、E2、S7h 各節 | §3、§5、§8 |
| `docs/PLAN.md`、`docs/verification.md`、`LICENSES/data.md`、`CLAUDE.md` | §2、§6、§9 |
