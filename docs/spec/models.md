# 模型檔、建置規則與語料轉換

這份寫的是現在的行為。

範圍：App 內附的七個模型檔（清單、檔案格式、彼此的綁定、載入規則）、通語與辨類與承接與連文與會意的建置規則、台灣繁體語料轉換（tw-conv）。解碼公式見 `docs/spec/decoder.md`，語意校正的融合與強度見 `docs/spec/semantic-rerank.md`，詞庫與詞包的內容見 `docs/spec/lexicon-and-packs.md`，C 介面的回傳碼見 `docs/spec/c-abi.md`。

## 1. 模型清單與關於頁

### 1.1 `data/models.tsv`

App 內附的模型只有一份清單：`data/models.tsv`。每列 `檔名<TAB>SHA-256<TAB>中文名<TAB>對外名稱`，沒有表頭、沒有註解、沒有空行，順序就是關於頁的順序：

| 檔名 | 中文名 | 對外名稱 | 角色 |
|---|---|---|---|
| `bigram.sjlm` | 通語 | SJ-LM-7-9.7M | 二元語言模型 |
| `classes.sjc` | 辨類 | SJ-WC-6-512C | 詞類表 |
| `kn.sjkn` | 承接 | SJ-KN-2-397K | Kneser–Ney 接續次數側檔 |
| `trigram.sjt3` | 連文 | SJ-CX-2-7.7M | 書面設定的三連詞表 |
| `trigram-chat.sjt3` | 連文 | SJ-CX-2-950K | 聊天設定的三連詞表 |
| `tinylm.sjtl` | 會意 | SJ-SR-8-15M | 語意校正用的小型字元模型 |
| `colloquial.sjlm` | 常談 | SJ-CL-2-69K | 口語詞頻表 |

- 形狀規則（建置檢查與 Swift 解析器共用）：檔尾是換行、整檔沒有 CR、每列剛好四個非空的 tab 分隔欄位（所以前導 tab、多一個 tab 都不合格）。建置檢查另外要求第二欄是 64 位小寫十六進位、同一個檔名不出現兩次；Swift 解析器不重複這兩條。
- 一列不合格，Swift 解析器把整個檔當成讀不了（回傳空陣列），關於頁不顯示模型一節；不會只略過那一列。
- 第二欄必須等於 `data/<檔名>.sha256` 記的雜湊。換模型時，雜湊與名稱在同一列一起改。
- 名稱不由建置檢查：命名表同一列也留著舊版名稱，用字串找分不出新舊，表裡也沒有雜湊可綁。名稱由審查對照 `docs/releases/README.md` 的命名表核對。

### 1.2 建置時的檢查

`scripts/check-models.sh build [ROOT]`，由 `scripts/build-app.sh` 在建置前呼叫（也可以單獨呼叫）：

- 先檢查 §1.1 的形狀規則。
- 逐列：`ROOT/data/<檔名>.sha256` 的第一個欄位等於該列雜湊；`ROOT/data/lm/<檔名>` 存在；它的 SHA-256 等於該列雜湊。
- 任何一項不符就以非零結束，訊息寫列號與檔名。
- `build-app.sh` 通過後，照 `models.tsv` 的檔名欄把每個 `data/lm/<檔名>` 複製進 `Resources/`（`cp -L`，工作樹裡的模型可能是符號連結），再把 `models.tsv` 複製進去。要打包的模型就是清單上的模型，沒有另一串檔名。
- `build-app.sh` 另外為每個現有模型各留一段缺檔時的下載提示與 `shasum -c`（檔名寫死）。新增模型只要加一列就會被打包；下載提示要自己補。

### 1.3 打包後的檢查

`scripts/check-models.sh bundle RESOURCES [ROOT]`，由 `scripts/check-app.sh` 呼叫，雙向核對：

- `RESOURCES/models.tsv` 通過形狀規則；每列的雜湊等於 `ROOT/data/<檔名>.sha256`；`RESOURCES/<檔名>` 存在而且實際雜湊等於該列。
- `RESOURCES` 裡每個副檔名為 `.sjlm`、`.sjc`、`.sjkn`、`.sjtl`、`.sjt3` 的檔，在 `models.tsv` 剛好有一列。新的模型若用新副檔名，要把它加進腳本裡的副檔名清單，否則「有檔沒有列」的檢查看不到它。
- `check-app.sh` 另外要求 `RESOURCES/models.tsv` 與 `data/models.tsv` 逐位元組相同，並檢查授權署名檔（`LICENSES/CC-BY-SA-4.0-attribution.txt`）列出 `tinylm.sjtl`、`kn.sjkn`、`trigram.sjt3`、`trigram-chat.sjt3`。

### 1.4 關於頁

- 設定視窗「關於」頁（`AboutSections`）在「善解輸入法」一節之後多一節「模型」，每個模型一列 `LabeledContent(中文名, value: 對外名稱)`，順序照 `models.tsv`；不加說明文字。
- 讀 `Bundle.main` 的 `models.tsv`，只讀一次（靜態屬性）；檔案不在或讀不了（開發環境、測試）時整節不顯示。
- 解析是純函式 `ModelList.parse`，載入是 `ModelList.load(from:)`，位於 `macos/Sources/ShanjieKit/ModelList.swift`。

### 1.5 命名

- 每種模型一個中文名與一個「SJ-」開頭的功能代號，名稱寫成「代號－版本－規模」；規模用最能代表該模型大小的量：通語與連文是二元／三元組合條數，辨類是詞類數，承接與常談是詞彙數，會意是參數數。
- Release 代號用小寫（例如 `sj-lm-7-9.7m`）。App 內的檔名與程式碼不隨對外名稱改。

### 1.6 驗證

- `scripts/test-check-models.sh`（CI 跑）：用假的資料夾驗證 `build` 與 `bundle`——照原樣通過；列的雜湊與 `data/*.sha256` 不符、`data/lm` 的檔與列不符、檔案不在、欄位數不對、檔尾沒有換行、多一個 tab、前導 tab、CRLF、空欄位都要失敗並在訊息裡點名檔案；`bundle` 的有檔無列、列的雜湊不符、有列無檔、檔案雜湊不符都要失敗；`data/lm` 已下載時，repo 自己的 `models.tsv` 要通過。
- Swift：`ModelListTests`（`testRowsInFileOrder`、`testEmptyFileHasNoRows`、`testRowWithTheWrongFieldCountMakesTheFileUnreadable`、`testMissingFileHasNoRows`、`testShapesTheBuildRefusesAreUnreadable`、`testTheRepoListHasTheSevenModels`）。
- 實機：設定 → 關於 → 看到「模型」一節與七列，名稱與命名表一致。

## 2. 一組模型檔

### 2.1 綁定

七個檔是一組，通語 `bigram.sjlm` 是根；其餘檔把它的雜湊（SHA-256，檔案全部位元組）寫在檔頭，載入時核對，不符就載入失敗，不會退回舊行為：

| 檔 | 綁定什麼 | 不符時 |
|---|---|---|
| `classes.sjc` | 通語的 SHA-256 與詞彙數 | 載入通語失敗 |
| `kn.sjkn` | 通語的 SHA-256 與詞彙數 | 載入通語失敗 |
| `trigram.sjt3`、`trigram-chat.sjt3` | 通語的 SHA-256 與詞彙數 | 載入通語失敗 |
| `tinylm.sjtl` | 通語的 SHA-256；版本 4 起還有 `kn.sjkn` 的 SHA-256（§8.1） | `load_tinylm` 失敗，只用 n-gram |
| `colloquial.sjlm` | 不綁通語 | 常談只在載入的會意檔的 SHA-256 等於核心常數 `PAIRED_TINYLM_SHA256` 時生效（§8.3） |

- 換通語就要重建辨類、承接、兩張連文、重新匯出會意（檔頭記新的通語與承接雜湊），再把 `PAIRED_TINYLM_SHA256`（會意檔換了的話）、`data/*.sha256` 與 `models.tsv` 一起換。
- 目前這組：通語 `e3fb6056…6148eb5a`，會意 `356a37be…0a97c5de`，常數 `PAIRED_TINYLM_SHA256` 與 `models.tsv` 的會意列相同（`core/src/colloquial.rs`、`data/models.tsv`）。

### 2.2 載入規則

- `shanjie_engine_load_lm`：`classes.sjc` 必須在模型旁邊，缺了或不符回 3；`kn.sjkn` 在模型旁邊就載入，沒有就關閉，存在但讀不了或不符回 3（不靜默略過，連指向不存在檔案的符號連結也算「存在」）；兩張連文表各自在模型旁邊找（書面 `trigram.sjt3`、聊天 `trigram-chat.sjt3`），沒有就只是那個設定不用連文，任何一張存在但壞掉回 3（兩個設定都載入失敗）。
- 連文表以唯讀記憶體映射載入。載入期間檔案不能被原地截斷或改寫（會讀到映射之外的頁而 SIGBUS）；安裝路徑一律整個 bundle 以 rename 換掉。
- `shanjie_engine_load_tinylm`：要先有成功載入的通語；檔案必須是一般檔案、不超過 64 MiB、格式正確；通語雜湊與 KN 狀態要符合檔頭（§8.1）；失敗回 3，引擎保留原本的小模型（或沒有），n-gram 照常運作。
- `shanjie_engine_load_colloquial`：把檔案當成純二元表讀（不載入旁邊的詞類表、KN、連文表）；讀不了或格式錯回 3。
- 錯誤只帶種類，不帶任何內容。

### 2.3 來源辨識條目

發佈的通語含少量用來辨識來源的條目，條目內容不公開。用 `tools/build_lm.py` 自己從計數重建的模型不含這些條目，雜湊和發佈版不同：`cli/tests/golden.rs` 的 `lm_file_is_the_released_model` 與 `data/*.sha256` 的核對會失敗，所以開發一律下載發佈版。條目要在算雜湊、建詞類表、產生 KN 側檔、匯出會意之前加進去，下游的檔才綁得到最終的雜湊。加入條目前後，受影響的打字情境裡解碼與預測的結果相同；這一項由維護流程在非公開的情境上核對，不隨 repo 散布。

## 3. 檔案格式

全部小端序。讀檔時除了魔術字串與長度，也檢查查表用到的不變量（排序、位移範圍、id 範圍、無多餘位元組），讓任何索引都不會 panic。

### 3.1 通語與常談（`SJLM0001`）

`tools/build_lm.py` 寫，`core/src/lm.rs` 的 `Lm::parse` 與 `reference/proto/lm.py` 讀：

| 欄位 | 型別 | 說明 |
|---|---|---|
| magic | 8 bytes | `SJLM0001` |
| V | u32 | 詞彙數，id 0 是 `<s>`、1 是 `</s>`，其餘依 UTF-8 位元組排序 |
| N | u64 | unigram 總次數（不含 `<s>`、`</s>`；異體類只算一次） |
| eos_total | u64 | 剪枝前所有 (v, `</s>`) 次數之和（同樣類只算一次） |
| D | f64 | absolute discounting 的折扣，0.75 |
| vocab | V 筆 | u16 位元組長度 + UTF-8 |
| unigram | u64[V] | 加權 unigram 次數（`<s>`、`</s>` 是 0） |
| C | u32 | 有保留條目的前文數 |
| ctx_id | u32[C] | 嚴格遞增 |
| ctx_total | u64[C] | 該前文剪枝前的總次數 t，不得為 0 |
| ctx_off | u32[C+1] | 條目區間，單調不減，首項 0，末項 E |
| E | u32 | 條目數 |
| next_id | u32[E] | 每個前文內嚴格遞增，且小於 V |
| count | u32[E] | 加權次數 |

- 載入檢查：V ≥ 2、N ≠ 0、D 有限、`vocab[0]` 是 `<s>`、`vocab[1]` 是 `</s>`、其餘嚴格依位元組遞增、UTF-8 合法、沒有多餘位元組。
- 載入時算出 `back(v) = 1 − Σ(count − D) / t`（依檔案順序加總後除一次）與 `p_eos = eos_total / N`；機率公式本身見 `docs/spec/decoder.md`。
- 常談表（`colloquial.sjlm`）是同一種格式：一般的二元詞頻表，不用詞類、不用 KN，D 0.75，剪枝門檻 2，不綁通語。

### 3.2 辨類（`SJCL0001`）

`tools/build_classes.py` 寫，`Lm::parse_with_classes` 與 `lm.py` 讀。類別表依模型詞 id 存，不在載入時重算：

| 欄位 | 型別 | 說明 |
|---|---|---|
| magic | 8 bytes | `SJCL0001` |
| model_sha | 32 bytes | 通語的 SHA-256 |
| K | u32 | 類別數；`<s>` 是類別 K+1、`</s>` 是 K+2，所以轉移表是 (K+3)×(K+3) |
| μ | f64 | 類別項的權重，必須在 [0, 1] |
| V | u32 | 通語詞彙數，必須相等 |
| cls | u16[V] | 每個詞的類別；`0xFFFF` 是沒有類別 |
| emit | f64[V] | 發射機率 P(w｜c(w)) = uni(w) ÷ 類別 c 的 uni 總和；`<s>`、`</s>` 是 1.0；沒有類別的詞是 0.0 |
| Pc | f64[(K+3)²] | 列主序的 P(d｜c) = (M[c,d] + ε) ÷ (M[c].sum + (K+3)ε)，M 是全部（剪枝前）加權二元組的類別計數，ε = 0.1 |

- 總長 = 8 + 32 + 4 + 8 + 4 + 10V + 8(K+3)²，長度不符、類別 id 超出範圍（≥ K+3 且不是 `0xFFFF`）、任何機率不在 [0, 1] 都是格式錯誤。
- 有類別但不在通語詞彙裡的詞，只記錄數目，不套類別項。

### 3.3 承接（`SJKN0002`）

`tools/kn_cont.py` 寫，`Lm::add_kn` 與 `lm.py` 讀。檔頭 64 bytes，N 陣列接在後面：

| 欄位 | 型別 |
|---|---|
| magic `SJKN0002` | 8 bytes |
| V | u32 |
| 通語的 SHA-256 | 32 bytes |
| θ | u32 |
| β | f64 |
| ΣN′ | u64 |
| N | u32[V]（id 0、1 是 0） |

- 總長 = 64 + 4V。載入時全部符合才收：魔術字串、V 等於通語詞彙數、通語雜湊、長度、β ∈ [0, 1]、`0 < ΣN′`，且 ΣN′ 不小於最大的 N′ 也不大於 id ≥ 2 的 N′ 總和（N′ = N + 1）。
- ΣN′ 是上游（Python，`lm.kn_total` 加 `build_lm.variant_classes`）預先算好寫進檔頭的值，每個異體類只算一次；Rust 只讀檔頭，不重算。
- 核心保留側檔整個位元組與它的 SHA-256（`Lm::kn_sha256`，從檢查過的同一份位元組算，不重讀檔），供會意檔的配對用（§8.1）。
- 摘要行標記：`+kn:<側檔 SHA-256 前 8 碼>:θ<θ>:β<該設定用的 β>`。

### 3.4 連文（`SJT30001`）

`tools/build_trigram.py` 寫（改綁與測試用），`core/src/trigram.rs` 與 `reference/proto/trigram.py` 讀。檔案不複製進陣列，全部欄位就地讀：

| 欄位 | 型別 |
|---|---|
| magic `SJT30001` | 8 bytes |
| V | u32 |
| D | f64，必須剛好 0.75 |
| 通語的 SHA-256 | 32 bytes |
| C | u32 |
| E | u32 |
| key | u64[C]，u × V + v，嚴格遞增且 < V² |
| total | u64[C]，該前文剪枝前的總次數，不得為 0 |
| off | u32[C+1] |
| next | u32[E]，每個前文內嚴格遞增，且 < V |
| count | u32[E] |

- 總長 = 64 + 20C + 8E；每個前文的保留次數總和不得大於 total（否則回退權重會是負的）。
- `build_trigram.py --src-lm … --tri … --dst-lm … --out …` 把一張表改綁到另一個通語（詞用字串對應、前文重新排序、次數與剪枝前總數照抄）；來源詞不在目的詞彙裡預設停止，`--drop-missing` 才丟掉那些詞與含它們的前文與條目。寫檔先寫暫存檔再 rename，因為舊檔可能正被映射。
- 連文項如何參與解碼（地板、回退）見 `docs/spec/decoder.md`。

### 3.5 會意（`SJTL0001`～`SJTL0005`）

`tools/lib/export_sjtl.py` 寫，`core/src/tinylm.rs` 讀。魔術字串與版本欄一起選定標頭長度與有哪些欄位，檔案必須完整含有標頭才讀任何版本欄之後的欄位：

| 版本 | 標頭長度 | 比上一版多的 |
|---|---|---|
| 1 | 100 | magic、u32 版本、u32 權重型別（0 f32、1 int8）、u32 層數／頭數／寬度／前文長／字表數、f64 聊天 α τ 與書面 α τ、32 bytes 通語 SHA-256 |
| 2 | 116 | f64 聊天 δ、書面 δ（位移 100、108） |
| 3 | 212 | 四組 (α, τ, δ) f64：聊天保守 116、聊天積極 140、書面保守 164、書面積極 188 |
| 4 | 244 | 位移 212 的 32 bytes：調參時用的 `kn.sjkn` 的 SHA-256，全 0 表示沒有開 KN 時調的；版本 1～3 視為全 0 |
| 5 | 248 | 位移 244 的 u32：模型讀的左側前文字數 L，必須 1 ≤ L ≤ 前文長 − 2，超出範圍整個檔拒絕 |

- 標頭之後：字表 (V − 3) 個 u32 碼位（id 0、1、2 是 PAD、UNK、BOS），然後依固定順序的張量（`emb`、`pos`、`lnf_w`、`lnf_b`，再每層 `ln1_w`、`ln1_b`、`qkv_w`、`qkv_b`、`proj_w`、`proj_b`、`ln2_w`、`ln2_b`、`fc1_w`、`fc1_b`、`fc2_w`、`fc2_b`），沒有逐張量標頭。二維矩陣是 f32 列主序，或 int8 列主序後接每列一個 f32 縮放（每列對稱量化，縮放 = max(|列|, 1e-12) ÷ 127，還原 = q × 縮放）；一維向量一律 f32。
- 載入限制：檔案 ≤ 64 MiB、一般檔案；權重型別 ≤ 1；層數 ≥ 1 且 ≤ 64、頭數 ≥ 1 且整除寬度、寬度 ≤ 2,048、前文長 ≥ 2 且 ≤ 1,024、字表 ≥ 3 且 ≤ 65,536；長度必須剛好；還原後的權重必須有限。
- 每組 (α, τ, δ) 的檢查：α 有限、τ 不是 NaN、δ ∈ [0, 1000]。這三個值如何用在融合與強度，見 `docs/spec/semantic-rerank.md`。
- 寫檔：`export_sjtl.py` 只寫版本 1～4。命令列給 `--levels` 就寫版本 4（沒給 `--kn-side` 時 32 bytes 全 0）；沒給 `--levels` 照舊寫版本 1 或 2；只給 `--kn-side` 是用法錯誤。`--kn-side` 的檔必須是 `SJKN0002` 而且記的通語雜湊等於 `--ngram-model`，寫檔前先驗證。版本 3 只由函式 `export` 在有 `levels` 而沒有 `kn_sha` 時寫（測試用的黃金檔）。
- 驗證：`core/tests/tinylm_tiny.rs`（`header_fields_are_read`、`bad_files_are_format_errors`、`v2_reads_delta_and_otherwise_equals_v1`、`v3_reads_levels_and_otherwise_equals_v2`、`v4_reads_levels_and_the_kn_hash_and_otherwise_equals_v3`、`v4_bad_files_are_format_errors`、`v5_declares_the_left_context_length`）；`tools/test_export_sjtl.py`（`test_levels_write_version_3`、`test_levels_with_kn_side_write_version_4_with_its_hash`、`test_levels_without_kn_side_write_version_4_all_zero`、`test_without_levels_the_output_is_version_1_or_2_as_before`、`test_kn_side_alone_and_bad_side_files_write_nothing`、`test_committed_v4_golden_records_hashlib_of_kn_tiny`）。

## 4. 通語的建置

### 4.1 語料與權重

通語 SJ-LM-7-9.7M 的計數：

| 來源 | 權重 |
|---|---|
| 中文維基百科 dump 的前 200,000 篇主命名空間非重新導向條目（dump 順序），`--expected` | ×1 |
| 口語池：公開口語句（Common Voice 繁體中文〔CC0〕、Tatoeba 的中文句子）、台灣口語合成句（gpt-oss-120b 的輸出）、補次數合成句（§4.5） | ×5 |
| g0v Slack `#rand0m` 頻道的公開訊息（CC0） | ×5 |
| 台灣公開論壇的文章底下的推文（一篇文章的推文合起來算一個單位，每單位最多 50 句） | ×5 |
| 台灣公開論壇的文章內文（每篇最多 50 句） | ×1 |

- 每個來源的句子都先過 §9 的轉換（口語池與論壇等原生來源是 `native`，維基是 `mixed`），再過與評測集、保留集的比對守門（`--holdout-guard`，命中保留集句子的漢字段不計數）。
- 句子全收，沒有再用 Moore–Lewis 之類的方式篩選：篩選那一臂在私人的聊天集合上沒有改善，不公開。
- 計數與建模都不使用私人對話資料。

### 4.2 計數詞表與加法計數

- 斷詞與期望次數用的詞庫：`mcbpmf-data.txt`＋`overlay-add.tsv`（一律在內），再加每個 `--extra-lexicon FILE`（疊加層格式 `讀音<TAB>詞<TAB>分數<TAB>來源`，可給多次）。只在 `--expected` 時允許 `--extra-lexicon`，否則是用法錯誤。名單與詞包的詞經 §4.3 篩過後以這個選項進計數。
- 沒給 `--extra-lexicon` 時，詞庫與計數和以前逐位元組相同（`tools/test_extra_lexicon.py` 的 `test_without_option_equals_old_lexicon`）。
- **期望次數**（`--expected`，取代最高分切分）：在詞圖（每個在詞庫裡的子字串是一條邊，分數用詞庫分數）上做前向後向（log10 空間），詞 w 在 i..j 的期望次數 = 10^(α[i] + lp + β[j] − Z)，相鄰兩詞的二元組同理，句首句尾配 `<s>`、`</s>`；單一段落裡期望次數低於 `EXPECTED_MIN` = 0.01 的詞與二元組不記；長度不到 2 的漢字段不計；詞圖沒有切法的段落整段不計。
- **加法計數**（給了 `--extra-lexicon` 時，`run_expected`）：一般的詞與二元組照「不含額外詞庫」的詞庫（lex0）算，全部照收；額外詞庫（lex1）只「加上」額外詞自己的單詞次數，以及一端是額外詞的二元組。lex0 切不開的段落整段不計；段落裡沒有任何額外詞字串時不必算 lex1。
- 取捨：直接用含額外詞的詞庫斷詞，詞包裡的兩字片段（萊恩、拉斯）會吃掉常用字對的計數，不開詞包時這些字對打不出來；加法計數讓不開詞包時一般詞的計數與沒有詞包時相同。
- 驗證：`tools/test_extra_lexicon.py`（`test_extra_word_is_one_token`、`test_additive_counts_keep_the_plain_counts`、`test_option_given_twice`、`test_text_script_threads_option`、`test_wiki_script_parses_option`）、`tools/test_build_lm.py`。

### 4.3 有同音衝突的詞不進計數

給 `--extra-lexicon` 的計數詞表是從詞包與名單篩過的：

- 衝突讀音 = 詞包與名單各自的同音處置表列出的讀音，加上新模型上重新偵測到的同音衝突讀音。
- 詞只要有一個讀音落在衝突讀音裡，或它的讀音是某個衝突讀音裡連續兩個以上的音節（片段），整個詞不進計數詞表。
- 這些詞在模型裡沒有語料次數，分數維持「沒見過的疊加詞」的處理（`UNSEEN_OVERLAY_PENALTY` = 1.0，見 `docs/spec/lexicon-and-packs.md`），讓詞包原本的同音排序照樣成立。
- 取捨：語料裡較常見的寫法拿到較高的計數，會蓋過詞包對同音名字的排序；片段也排除，是因為詞包裡的片段仍有計數時，拼起來還是同一個名字。
- 建完模型後用 `tools/build_acg_pack.py --recheck` 對新模型重驗詞包的同音處置；沒處置的同音衝突（兩個詞包詞之間、或詞包詞與名單詞之間）要停下，詞包詞擠掉詞庫同音詞的情形要報數字與清單。篩選與重驗用的研究工具之一（產生計數詞表的那支）不在追蹤的樹裡，本節規則沒有在 repo 裡被測試直接驗證。
- 進計數的額外詞庫有三份來源：常開的專有名詞名單、動漫詞包、資訊詞包；它們都不進常開詞庫本身（詞庫內容與詞包的處置見 `docs/spec/lexicon-and-packs.md`）。詞包詞在模型裡的次數由 `acg-counts.tsv` 凍結（標頭記建出它的模型的 SHA-256 與 N），一般建置只讀這張表。

### 4.4 從計數到模型檔

`tools/build_lm.py`（參考實作）：

1. 各計數檔乘上整數權重後相加（預設 維基 `counts-200000.pkl` ×1、口語 `counts-colloquial3.pkl` ×5；`--corpora FILE:WEIGHT …` 換成別的檔與權重，此時必須給 `--out`，實驗用的表不會蓋掉出貨檔）。
2. 每一筆四捨五入成整數、去掉 0。
3. 異體合併（`variant_classes`、`merge_variants`）：`build_counts.MERGE` 套到兩邊後字串相同、而且在詞庫（基底加疊加層）裡讀音集合完全相同的詞併成一類；類的代表成員是詞庫最高分最大者，同分取 MERGE 正規形，再同分取碼位最小。unigram 以類加總後寫回每個成員（每個寫法都要在詞彙表裡才能當前文）；二元組以（類, 類）加總，「後一個詞」只放在代表成員上，前文寫給類裡每個成員。`N`、`eos_total`、每個前文的總次數 t 都讓每類只算一次。可選 `--extra-overlay FILE` 把額外疊加層接在 `ime.OVERLAYS` 之後算異體類（預設關，不給時輸出不變）。
4. 兩端有一端不在詞彙表的二元組丟掉（加法計數下，詞包詞旁邊的一般詞可能二元組留下、單詞四捨五入成 0）；沒有加法計數的輸入不會出現這種二元組，輸出不變（`test_bigram_with_a_word_outside_the_vocabulary_is_dropped`）。
5. 只保留加權次數 ≥ 剪枝門檻（`--prune`，預設 2）的二元組，但每個前文的總次數 t 用剪枝前的完整計數（被剪掉的質量歸給回退）；只存「至少保留一個條目」的前文。
6. 寫出 §3.1 的格式；先寫暫存檔再 rename；`--check` 重建並與現有檔比對 SHA-256 而不寫檔。

- 出貨的通語用 `--prune 6`：門檻 2 時二元組檔約 220 MB、記憶體超過預算，量過的剪枝門檻中選 6。出貨的檔頭記 D = 0.75、剪枝門檻 6（工具預設仍是 2，重現出貨檔要給 `--prune 6` 與 `--corpora`）。
- 規模（Release 說明記錄的值，建置當時讀檔頭所得）：詞彙 396,927、二元組合 9,709,510 條、227,749 個前文、加權 unigram 總次數 617,862,688，檔案 88,467,490 bytes。

### 4.5 口語補次數

- 模型沒見過接續的口語詞與罕見量詞（坨、一整坨、整坨…），在口語池之外另補一份合成口語句，進 ×5 的口語池。種子詞是手寫的一般常識詞，不收私人聊天裡看到的詞。
- 生成管線：gpt-oss-120b，把關規則（簡體字、長度、去重、與評測集重疊的句子拿掉，含與既有口語檔去重）照既有的口語合成管線。輸出檔不進 repo。
- 補次數檔與口語池一起以 `build_counts_text.py --expected` 計數，權重 ×5。

### 4.6 重現與驗證

- 建置的重現核對：同一份計數、同一個腳本，模型（加來源辨識條目之前）逐位元組相同；計數腳本、`build_lm.py`、`build_classes.py`、`kn_cont.py` 有改動就先重做這個核對。
- Python：`tools/test_build_lm.py`、`tools/test_extra_lexicon.py`、`tools/test_build_classes.py`、`tools/test_kn_cont.py`、`tools/test_build_trigram.py`、`tools/test_export_sjtl.py`（`make test-python`）。
- Rust 與 Python 的跨語言核對：最終檔（加側檔、名單層）上，Rust CLI 與 `lm_eval.py` 在 dev302、打字測驗、錯字回報的摘要行逐字相同（含 `+kn:` 與 `top1_sha256`）。macOS 與 Linux 的 libm 末位不同，開 KN 後 n-gram 分數欄只有最後一位會差（量過最大約 7e-15），所以跨平台比 dump 時，候選字串與順序要逐行相同、n-gram 分數差 ≤ 1e-9。

## 5. 辨類

### 5.1 內容

- 512 個詞類（K = 512），詞彙與通語相同（396,927）；每個詞的類別、發射機率與類別接續機率都依同一批計數重算；檔案 6,091,126 bytes（Release 說明記錄）。
- 詞類項在解碼裡的用法（只作用在沒有保留條目的詞對，混進回退項）見 `docs/spec/decoder.md`。

### 5.2 μ

- 檔頭的類別項權重 μ（位移 44 的 f64）現行值是 0.2；`tools/build_classes.py` 的常數 `MU` 寫的就是這個值，用該工具重建會保留出貨值。
- μ 在出貨的組合（通語＋承接＋會意＋詞包）上掃過，0.2 相對舊值 0.8 在判讀集上有顯著進步、調參集與回歸集沒有顯著退步，所以取 0.2（掃描的集合與數字不公開）。
- 只改 μ 而詞類、發射、接續機率都不變，不需要重調會意；重新分群（類別或機率表變了）才要重調。會意檔只綁通語與承接的雜湊，不綁詞類表。
- 跟著 μ 的測試：`cli/tests/golden.rs` 的 `manual_words_come_first_in_the_shipping_setting` 釘著出貨設定下仍沒對的列（書面第 8 列 `米雅歌唱得很好`，n-gram 的第一名本身就是 `米亞歌唱得很好`）。

### 5.3 建置

- 分群：研究工具對同一批計數的前 40,000 個詞做 K = 512 的分群，輸出邊檔（詞彙、unigram、二元組邊）與分群檔；`tools/build_classes.py --edges … --cls … --lm … --out …` 讀這兩個檔，對通語建 §3.2 的格式。現行 SJ-WC-6-512C 是用和通語同一批計數重做分群得到的。分群工具不在追蹤的樹裡。
- 分群工具有一個選項可以讓舊詞沿用上一版分群的類別（不跑交換），新詞依頻率貼進最佳類別；較早的建置用過它，理由是重新分群會讓整體漂移、讓若干探針句退步。現行這一版有沒有用它，Release 說明沒有寫，這裡未驗證。
- `--extra-overlay` 預設關，給了就把額外疊加層的詞算進「詞庫產得出的詞」，只影響記錄數目。

### 5.4 量詞

量詞詞類表是手寫的一份常見量詞清單。分群之後、`build_classes.py` 之前，加權 `uni` 計數（維基 ×1＋口語 ×5，與 `--edges` 讀的同一欄）少於 1,000 而且類別與「個」不同的量詞，改成「個」的類別，使罕見量詞與常見量詞同類；不在詞彙裡的清單詞略過，`uni` ≥ 1,000 的量詞不動。這是詞類表自上一代起的建置規則；這支工具與清單不在追蹤的樹裡，本條沒有在 repo 裡被直接測試，現行 SJ-WC-6-512C 是否仍套用，Release 說明沒有寫，這裡未驗證。

## 6. 承接

### 6.1 接續次數

- `N(w)` = 接在 w 前面、出現次數至少 θ 次的不同前文詞數；前文不含 `<s>`，必須在模型詞彙裡，異體類只算一種前文；類的非代表成員取代表成員的 N；模型詞彙裡有、計數裡沒有的詞 N = 0。θ ∈ {1, 2, 3}，計數用 §4.4 同一套加權、四捨五入、去 0、異體合併的整數二元組。
- 在解碼裡：模型詞彙裡的詞（id ≥ 2）的回退分布 `pb` 換成 `β · N′ ÷ ΣN′ + (1 − β) · 10^lp`（N′ = N + 1，`lp` 是詞庫分數）；句尾與不在詞彙裡的詞（沒見過的疊加詞、詞包詞）的 `pb` 不變。三個機率分支（保留條目、詞類項、一般回退）都用換過的 `pb`。完整公式見 `docs/spec/decoder.md`。
- 動機：回退時看「這個詞接在幾種不同的詞後面」而不是它出現幾次，只出現在固定搭配裡的字回退機率會變小，對準「常用單字組合贏過整詞」這類錯。

### 6.2 選定值

- 現行側檔：θ = 1、β = 1（檔頭記 β 1）。
- 聊天設定用側檔檔頭的 β（1）；書面設定用核心常數 `FORMAL_KN_BETA` = 0.75（`core/src/lm.rs`），不論檔頭記的 β 是多少。兩種設定的 β 都出現在摘要行的 `+kn:…:β…`。
- 取捨：θ、β 在調參集的格點（θ ∈ {1, 2, 3} × β ∈ {0.5, 1}）上選，θ = 1、β = 1 是調參集合計最高且沒有任何集合被否決的一組；書面的 0.75 是書面有連文表之後另外量過、只改執行時設定的值。θ 的計數是「四捨五入成整數＋門檻」的近似，因為計數檔只存期望值的加總，做不到分數次數上的 Kneser–Ney。
- 規模（Release 說明記錄）：檔案 1,587,772 bytes、詞彙 396,927、ΣN′ = 46,087,912。
- 側檔在模型旁邊就自動載入，C 介面的行為見 `docs/spec/c-abi.md`；出貨的 App 附一份。

### 6.3 與學習

選字記憶把某個詞條的詞條分數提高時，這個詞條的語言模型項用「不經過 KN 替換」的機率（與沒有側檔時同一個公式，`pb` 是加分後的值），沒被加分的詞條照常經過 KN；`Engine::total_score` 套同一個例外。細節見 `docs/spec/learning.md` 與 `docs/spec/decoder.md`；測試是 `core/tests/kn_tiny.rs` 的 `learned_entry_skips_kneser_ney_and_its_neighbour_does_not` 與 `total_score_equals_the_top_decode_with_a_learned_word_and_kn_on`。

### 6.4 驗證

- Python：`tools/test_kn_cont.py`（`test_side_file_layout`、`test_counts_by_hand`、`test_beta_zero_bit_identical`、`test_beta_one_three_branches`、`test_unknown_and_eos_unchanged`、`test_monotone_in_N`、`test_mismatch_raises`、`test_thetas_differ`、`test_kn_tiny_golden_is_current`）。
- Rust：`core/tests/kn_tiny.rs`（`beta_one_three_branches_by_hand`、`formal_takes_beta_075_and_chat_the_files`、`beta_zero_is_bit_identical_to_no_side_file`、`bad_side_files_are_format_errors`、`decode_matches_python_byte_for_byte`）。

## 7. 連文

- 兩張表各服務一個設定，每個設定只載入自己那張：聊天用 `trigram-chat.sjt3`（SJ-CX-2-950K，聊天開連文、KN 開，β 1），書面用 `trigram.sjt3`（SJ-CX-2-7.7M，書面開連文、KN β 0.75）。
- 聊天表：通語的全部語料與同樣的權重數三元組，相對熵剪到 950,000 條（剪成 7.7M、3.85M、1.9M、0.95M 四個大小比過，守得住記憶體預算的只有 1.9M 與 0.95M，選較小的，記憶體只多約 25 MiB）。書面表：維基 ×1＋舊口語池 ×5 的三元組，剪到 7,700,964 條；不含 g0v 與論壇語料，因為含論壇語料的三元組讓書面更像口語，書面的調參集與 dev302 變差。
- 三元組計數與通語用同一個詞庫與計數詞表、同一套轉換（§9），保留每個前文剪枝前的總次數；折扣 D 固定 0.75。
- 規模（Release 說明記錄）：聊天表 15,114,784 bytes、375,736 個前文；書面表 133,297,536 bytes、3,584,488 個前文。
- 連文計數與剪枝用的研究工具不在追蹤的樹裡；repo 裡驗證的是格式與改綁（`tools/test_build_trigram.py`、`core/tests/tri_tiny.rs`、`eval/golden/tri-tiny`）。

## 8. 會意與常談的配對

### 8.1 會意綁通語與承接

- 版本 4 起，會意檔記著調 α、τ、δ 時用的 `kn.sjkn` 的 SHA-256（整個檔案，32 bytes，與 Python `hashlib.sha256` 相同；全 0 表示沒有開 KN 時調的）。
- `TinyLm::load_paired` 的規則：通語雜湊必須相同，否則 `OtherNgram`。KN 狀態必須相符，否則 `KneserNey`，並分三種情形各給不同的補救訊息：載入了側檔而檔裡是全 0（版本 1～3 也算）——`TunedWithout`；載入了側檔但雜湊不同——`OtherSideFile`；沒載入側檔但檔裡記著雜湊——`TunedWith`。32 bytes 全部比對，不是前綴。
- 側檔「載入了」指模型旁邊載入了 `kn.sjkn`；書面有連文表時打分不用 KN，但側檔照樣載入、照樣參與配對。
- 標頭讀法：版本 4 與 5 的 `kn_sha` 都在位移 212..244，長度檢查通過後才讀；版本 4 同時有 δ 與三檔。
- 驗證：`core/tests/tinylm_tiny.rs` 的 `load_paired_binds_the_kn_side_file`、`v4_reads_levels_and_the_kn_hash_and_otherwise_equals_v3`、`v4_bad_files_are_format_errors`（每個前綴長度都回格式錯誤不 panic）；Python `hashlib` 與 Rust `kn_sha256` 對 `eval/golden/kn-tiny/kn.sjkn` 的值相同。
- `shanjie_engine_load_tinylm` 的錯誤訊息不寫「用 `--no-kn`」這種對新情形不正確的建議，而是照三種情形分開說。

### 8.2 訓練文字

會意的訓練文字（維基 20 萬篇 ×1、口語池 ×5、合成句、g0v ×5、論壇推文 ×5、論壇內文 ×1、OPUS OpenSubtitles 繁體字幕 ×1）：

- 繁體句不套 OpenCC `TWPhrases`，所以「權限」「插件」之類台灣原文不會被改寫；簡體來源的句子只換 §9.3 的 331 條大陸用詞。取捨：若繁體句也套，訓練文字裡的「權限」會變成「許可權」，會意從沒看過「權限」這個寫法，分數就偏向「全線」。
- 訓練文字經由 §9 的 `native`／`mixed` 分類：已轉換過的口語池標 `native`，不轉第二次。
- 每一步都開保留集比對守門。
- 現行 SJ-SR-8-15M 的權重是從上一版（SJ-SR-7-15M）的權重接續訓練的（3,000 步、峰值學習率 1e-4、暖身 100 步、weight decay 0、沿用上一版字表），架構不變：6 層、寬 384、6 頭、前文 64、字表 10,351 個 id、14,646,912 個參數、int8（每列對稱量化）；檔案 14,886,880 bytes。匯出用 `export_sjtl.py --type int8 --ngram-model <通語> --kn-side <承接>`，格式 `SJTL0004`。匯出的方法核對：用上一版的 npz 與上一版檔頭的全部值重匯出，結果必須與上一版出貨檔逐位元組相同。（訓練與核對記錄來自 Release 說明，訓練腳本不在追蹤的樹裡，未在此驗證。）
- 會意只在 n-gram 解碼出的前 8 名候選之間重排，不會產生新的文字；重排模型記憶體上限 250 MiB。

### 8.3 常談配對

- 常談（`colloquial.sjlm`）只在聊天設定、載入的會意檔的 SHA-256 等於 `core/src/colloquial.rs` 的 `PAIRED_TINYLM_SHA256`（`356a37be…0a97c5de`）時生效（`colloquial::applies`）；其他情形核心的行為與沒有常談表時相同。（評測 CLI 的 `--tinylm-paired` 量測時略過這個雜湊條件，`docs/spec/semantic-rerank.md` §4.6；出貨的判斷不變）。換了會意檔卻沒換這個常數，常談會悄悄關掉，所以兩者要在同一個變更裡一起改，並用 CLI 聊天三檔的摘要行都有 `+colloq:81c2470d` 與新的 `+tl:<會意前 8 碼>` 確認。
- 常談表的內容與融合：見 `docs/spec/semantic-rerank.md`。常談表只有口語計數（詞彙 69,457、二元組合 365,888 條），用通語的計數詞表重數：口語計數檔 ×1 加公開口語池 ×1，繁體句不套 `TWPhrases`，剪枝門檻 2；不含 g0v 與論壇語料（含了之後記憶體超過預算，保守檔在私人的聊天集合上顯著變差）。
- 常談表的署名清單 `colloquial-attribution.tsv` 與常談表同一次建置，每列有來源與句數，不含句子文字；`data/lm/colloquial-attribution.tsv` 的雜湊記在 `data/colloquial-attribution.tsv.sha256`，App 打包進 `Resources/LICENSES/`。

## 9. 語料轉換（tw-conv）

目的：計數與訓練用的文字保持台灣的實際寫法。原本就是台灣繁體的文字不轉換；簡體來源的句子用附台灣用量證據的轉換表轉。實作在 `tools/lib/build_counts.py`（`convert`、`load_conv`、`sentence_kind`、`fold_tw`），資料在 `data/lexicon/tw-conv.tsv`。

### 9.1 輸入種類

| 種類 | 來源 | 處理 |
|---|---|---|
| `native` | 台灣公開論壇的文章與推文、g0v 訊息、Common Voice zh-TW、合成句、新聞稿等書面來源、所有評測列，以及這個轉換器已經轉過的文字（口語池等） | 原文照用，一個字都不改 |
| `mixed` | 口語池的簡繁混雜原文（WildChat、YODAS、Tatoeba、MASSIVE）、維基、維基新聞匯出、字幕（上游已轉過仍有殘留）、詞庫建置用的維基詞典詞 | 逐句判斷（§9.2），簡體句走簡體路徑，其他句走繁體路徑 |

- 每個入口的每一份輸入都要說明種類，沒有預設值：`build_counts_text.py out --native PATH --mixed PATH …`（可重複，至少一個，位置參數拿掉）；維基入口固定是 `mixed`；`text_runs(line, conv, kind)` 與 `check_kind` 遇到不是 `native`／`mixed` 的種類丟 `ValueError`。理由：預設值讓沒改到的呼叫者默默走舊路。
- `native` 的行直接取漢字段，`mixed` 的行先 `convert` 再取漢字段。
- 舊的旗標（`--no-trad-tw-phrases`、`--tune-no-trad-tw-phrases`、`--cv-no-trad-tw-phrases`、`--simp-tw-keys` 等）與 `trad_tw_phrases` 參數不存在，傳了會報錯。
- 只比對、不產生語料的入口（`holdout_guard.py`）不需要種類：樣式同時含句子原樣與它的 `mixed` 轉換結果，掃描的文字也以原樣與轉換後兩種形式比。

### 9.2 判斷簡體句

- 真簡體字 S：`STCharacters.txt` 的鍵，對照的值不含自己，而且不出現在 `mcbpmf-data.txt` 任何兩字以上的詞裡（單字條目也收了 个、会 這類簡體字，分數都是最低值，所以只看兩字以上的詞）。床、程、群、秘、灶 不在 S。
- 標記字 M：`STCharacters.txt` 第一個對照不是自己的鍵（含 后、于、里、台、床）。
- 繁體證據字 T：`STCharacters.txt` 的值裡不是任何鍵的字，加上摺疊列與 `TWVariants.txt` 的目標字（為、裡、眾、啟…），扣掉 M 裡的字（么、床、灶、粽 本身是簡體鍵，不算繁體證據）。
- 句子裡 S、M、T 的字數記為 r、m、t。`sentence_kind`：
  - `simp`（簡體句）：r ≥ 1 且 r ≥ t。
  - `marker`（只有標記字的句子）：r = 0 且 m ≥ 1 且 t = 0。「之后他去了」靠這條轉得到；奇希里斯、后里 也落在這一類，所以這一類轉得比簡體句少（§9.4）。
  - 其餘是 `trad`（繁體句）。
- `is_simplified` = 種類不是 `trad`。把台灣句子誤判成簡體句的代價，由轉換表與 §9.10 的守門壓到零：簡體路徑作用在台灣句子上必須原樣輸出。
- 其他程式共用 S 當「真簡體字」：語料清洗（丟掉含簡體字的句子）、`build_overlay.py`、`build_names.py`（名單另外要求至少一個字在 S，因為 `is_simplified` 對短字串過敏，台糖、台北、后里 會被誤擋）。

### 9.3 轉換表 `data/lexicon/tw-conv.tsv`

以 Tab 分欄：`段, 鍵, 值, 狀態, manual, 次數[, N|R]`。證據只用台灣的實際寫法：台灣公開論壇的文章與推文快照的字、詞與次數（檔裡不放句子；檔頭記輸入檔雜湊與建表工具的 commit）。讀表時 `manual` 欄（`值@日期`）優先於規則；`keep`、`li` 兩段不經過 `manual`。

OpenCC 的對照表（`STCharacters`、`STPhrases`、`TWVariants`、`TWPhrases`，commit 3ac34aa，Apache-2.0）在使用前核對 SHA-256，不符就停。段與規則：

1. **單字（`char`）**：單字列只在異體字之間改選。候選是 `STCharacters` 有多個對照的鍵、鍵本身是台灣字形的鍵（灶→竈、床→牀、吃→喫），以及 OpenCC 與 MediaWiki zh-tw 單字轉換不同的 31 個鍵（冲 凄 勋 卤 吣 哗 喂 埙 棂 湿 牦 瘘 硷 签 艳 袅 账 鉴 钚 钫 锎 锝 锨 锫 锿 镅 镎 镥 鹇 鹚 𩙧）。
   - **證據是同一個詞的兩種寫法**：詞庫的兩字以上詞與《重編國語辭典修訂本》《國語辭典簡編本》的詞條裡，含這個字任一寫法的每個詞，用每種寫法各寫一次，在快照數整串的次數再加總（濕度／溼度、保濕／保溼…）。單字本身的次數不當證據（象、像是不同的字，不是異體字對）。
   - 候選先過摺疊列再計數。另一寫法加總 ≥ OpenCC 寫法的 10 倍而且 ≥ 30：改選（狀態 `evidence`）。31 個鍵裡候選字合計不到 30 次的：用 MediaWiki 的值（`mediawiki`）。其餘留 OpenCC 的值（`hold`）。
   - **標準字（`dict`）**：`hold` 的單字列，若選定值在《重編國語辭典修訂本》的單字詞條釋義以「「X」的異體字」開頭，而且 X 是這一列的候選，改選 X；除非選定值的同詞次數 ≥ X 的 10 倍而且 ≥ 30（沒有同詞次數時用摺疊後的單字次數）。目前表裡只有 灶 是這一類（竈 是辭典的異體字）。
2. **詞組（`phrase`）**：`STPhrases` 裡鍵含有「單字列有被改選或屬 31 個鍵」的字的條目。候選恰好一個是字典詞條時用字典的寫法（`dict`）；別的寫法 ≥ 字典寫法的 10 倍而且 ≥ 30 才改用它（`over-dict`）；都不是或有多個是字典詞條時，照 2 倍而且 ≥ 30（`evidence`）。`hold` 的詞組只在單字列是 `evidence` 或 `mediawiki` 時跟著換，否則留 OpenCC 的值。
3. **摺疊（`fold`）**：候選是 `build_counts.MERGE`（18 條）與 `TWVariants.txt` 第一個值的每一對 X→Y。Y 的次數 ≥ X 的 20 倍而且 Y ≥ 30 才摺疊（`fold`），其餘標 `nofold`、鍵留原樣。用 20 倍是因為摺疊不看上下文：泄→洩 約 14 倍，排泄 的 泄 是正確寫法。摺疊列套在每一種句子的最後（`fold_tw`），所以台灣原文與轉換結果都經過它。`MERGE` 本身是建模型合併異體詞用的，不是轉換。
4. **台灣用詞（`term`）**：只在簡體路徑與只有標記字的路徑套用的大陸用詞：`TWPhrases.txt` 的鍵（兩字以上）中，快照裡的用量不到對應台灣說法 10% 的 331 條，與 `tools/lib/mainland-terms.txt` 內容一致。
5. **N（欄 7 標 `N` 的單字列）**：台灣幾乎不用的鍵：選定值不是鍵本身、鍵本身在快照的次數不到選定值（摺疊後）的 1/20、而且不到 30 次（裝 痒 苹 价 這類）。后、准、于、几、里、台、干 都不在 N。N 的字在繁體句與只有標記字的句子也換成選定值。
6. **R（欄 7 標 `R` 的單字列）**與 R2：R 是標記字 M 裡選定值不是鍵本身、鍵本身不到選定值 1/20 但已達 30 次的鍵（万 于 后 几 据 种 么 胜…；不含 里、采、云、余、党、干、面、征、游、姜）。R2 = R 的列，加上 N 的列中鍵在 M 裡的。R2 只在 §9.5 的混雜句用。
7. **保留詞（`keep`，一列一詞）**：《重編國語辭典修訂本》與《國語辭典簡編本》裡兩字以上、含 R2 字、不含 S 字的詞（皇后、准許、咸陽、并州…），在混雜句裡原樣輸出。
8. **里的證據（`li`，`li<TAB>prev|next<TAB>字<TAB>裡的次數<TAB>里的次數`）**：快照裡裡／里前一個字與後一個字（先套摺疊列，裏算裡）的次數，給 §9.6 用。

- 驗證：`tools/test_tw_conv_guard.py` 的 `Kinds`（`test_a4_sentence_kinds`、`test_n_excludes_the_marker_characters_taiwan_writes`：后 准 于 几 里 台 干 不在 N）、`Revision8.test_r8_tables`（R2 含 于 后 几 万 据 种 么，不含 里 余 采 云 党；保留詞含 咸陽 并州 皇后 准許，且不含 S 字；`li` 兩段有讀到）。表的每一列是否都有例句，測試沒有逐列斷言（只固定檢查 §9.10 列出的那些列）。
- 建表工具與快照不在追蹤的樹裡；表檔本身進版控，雜湊由檔頭與 `tw-clean` 表的檔頭互相綁定（§9.8）。

### 9.4 轉換順序（`mixed`）

- **簡體句**（`simp`）：`STPhrases.txt` 最長優先（詞組列有選定值的用選定值，否則 OpenCC 的值）→ 沒被詞組蓋到的字用單字列的選定值（不在表裡的鍵用 `STCharacters.txt` 的第一個對照；沒被蓋到的 里 照 §9.6）→ `term` 子集最長優先 → 摺疊。
- **只有標記字的句子**（`marker`）：`STPhrases.txt` 最長優先 → 沒被詞組蓋到的字只換 N 的字，其餘原樣（「之后他去了」靠詞組 之后→之後；奇希里斯、后里 不變）→ `term` 子集 → 摺疊。
- **繁體句**（`trad`）：句中沒有任何 S 字時，只把 N 的字換成選定值，再摺疊；其餘一個字不動。句中有 S 字時走 §9.5。繁體句不套 `TWPhrases.txt` 的詞組，也不套 `term`。
- `native`：不呼叫轉換。
- 取捨：單字的簡轉繁一對多（湿→溼／濕、账→賬／帳）若照 OpenCC 取第一個，會系統性選到台灣少用的寫法；詞組、單字、摺疊三層都用台灣用量裁決。

### 9.5 有簡體字的繁體句

句子是 `trad` 而且含至少一個 S 字（簡繁夾雜）時：

- 保留詞與「鍵含 S 字或 R2 字」的 `STPhrases` 條目一起最長優先（同一個鍵時保留詞優先；詞組列有選定值的用選定值）。
- 沒被蓋到的 S 字、N 字、R2 字換成單字列的選定值，再摺疊。
- 咸、涌 另有規則（§9.7）。不套其他詞組、不套 `TWPhrases.txt`。
- 句中沒有 S 字的繁體句原樣（台灣句子裡的 于美人、后里 不會被改）。
- 驗證：`test_tw_conv_guard.py` 的 `Revision8.test_r8_rows_are_in_the_table_and_their_kinds`（咸陽、并州、逝世后、万余各一列，同時斷言每列是繁體句且含 S 字）、`test_r8_tables`。

### 9.6 里

簡體句裡沒有詞組蓋到的 里，依序（`li_decide`）：

1. 前一個輸出字是數字（〇一二三四五六七八九十百千萬兩、半形 0–9）→ 里（十萬里）。
2. 前一個輸出字是收尾標點（`》）」』”)>`）或半形拉丁字母 → 裡（《紅樓夢》裡、body裡）。
3. 里 前面最多 6 個輸出字（先套摺疊列）有任何長度 2～6 的結尾是詞庫 `mcbpmf-data.txt` 的詞 → 裡（學校裡）。
4. 其餘看快照同一個兩字串的寫法：log((前字裡 ＋ 1) ÷ (前字里 ＋ 1)) ＋ log((裡後字 ＋ 1) ÷ (里後字 ＋ 1)) ≥ log 10 → 裡，否則 里。前字是 里 前面最後一個輸出字（已摺疊）；後字是輸入裡 里 的下一個字經單字列轉換後的字（不看詞組），先套摺疊列；句首沒有前字、句尾沒有後字時該項為 0。

- 理由：音譯名（埃里克、利比里亞）和 里 當單位時，不看上下文一律轉 裡 會錯，字典詞條要多放數萬詞進 repo 而改善很小，所以第 3 步只用詞庫。已知的誤判：句尾沒有後字時，次數不到 10 倍的 街里 維持 里。
- 繁體句與只有標記字的句子裡的 里 不轉（里 不在 N、R2）。

### 9.7 咸與涌

只改 §9.5（有 S 字的繁體句）的單字那一步，詞組那一步不變（咸丰→咸豐、咸水→鹹水、东涌→東涌、涌现→湧現 仍照 `STPhrases` 的鍵）：

- 沒被詞組蓋到的 咸 原樣（不轉 鹹；繁體句裡的 咸 幾乎都是名字，韋斯咸、碧咸、咸攝）。
- 沒被詞組蓋到的 涌：輸入裡下一個字是 入、现、現、出、至、起、动、動 之一才轉 湧，否則原樣（東涌、鰂魚涌、句尾都原樣）。
- 簡體句、只有標記字的句子、沒有 S 字的繁體句都不受影響。
- 實作：`R10_AS_IS = {"咸"}`、`R10_FOLLOW = {"涌": "入现現出至起动動"}`；`convert(…, rev9=True)` 給出這兩條規則之前的輸出，用作校對表的鍵（§9.8）。
- 驗證：`test_tw_conv_guard.py` 的 `Revision10`（韋斯咸、涌入、東涌站、句尾涌、咸丰各一列，`test_r10_rev9_is_the_output_before_contract_12` 驗 `rev9`）。

### 9.8 校對表（`tw-clean`）

維基前 200,000 篇的句子有一批轉換結果需要逐句校對（簡體句的 里／裡、只有標記字的繁體句裡的 于／后／并 等，以及混雜句的轉錯方向）。校對結果收成一張表：

- **格式**：不進 repo 的快取檔（維基與模型輸出建出的列）。前三行固定是檔頭 `# tw-conv.tsv <SHA-256>`、`# mcbpmf-data.txt <SHA-256>`、`# entries <列數>`，其餘每一行都是資料（`#` 開頭也照樣是鍵），以第一個 Tab 切成兩欄：`轉換的輸出<TAB>校對後的句子`；第二欄空白＝這句不計數。讀表只去掉行尾的 `\n`，不 `strip()`（頭尾空白是鍵的一部分）。同一個第一欄對到兩種校對結果的不收。
- **套用**（`article_sentences`）：`convert()` 之後查表，命中就換成第二欄（空白則不輸出這句），沒命中原樣；MediaWiki 路徑不查。含 咸 或 涌 的句子，表的鍵是 §9.7 之前的輸出（`rev9=True`）：命中時，只在兩份輸出不同的位置（就是 §9.7 動到的 咸、涌）、而且校對後的句子在那個位置還是舊字時換成新規則的字，其他位置保留校對結果；長度不同時原樣用校對結果。
- **讀取**：每次 `load_conv()` 都讀；`article_sentences` 第一次查表時若這個程序還沒讀過（spawn 起來的子程序）就自己讀一次，所以每條經過它的路徑都一定套到表。環境變數 `SHANJIE_TW_CLEAN`：未設＝讀預設位置的檔，檔的 SHA-256 必須等於 `build_counts.py` 寫死的 `TW_CLEAN_SHA`；`off`＝空表，每個程序第一次讀時在 stderr 印一次 `tw-clean: off`；其他值＝一個路徑，不核對寫死的雜湊但仍核對檔頭。
- **找不到表就停**：檔不存在、預設檔雜湊不符、檔頭少了三行、檔頭兩個雜湊和目前的 `tw-conv.tsv`、`mcbpmf-data.txt` 不同（轉換表或詞庫改過，要重跑缺口量測與校對）、某行沒有 Tab，都 `sys.exit`，訊息說明補救辦法與 `SHANJIE_TW_CLEAN=off`。CI 的 Python 作業與 `make test-python` 一律設 `SHANJIE_TW_CLEAN=off`，因為真的表不在 repo 裡；測試要用自己的小表時另設路徑。
- **紀錄**：`tw_clean_state()` 回傳 `sha256:<表的雜湊>` 或 `off`。通語計數寫進輸出 pickle 的 `tw_clean` 與 `tw_clean_hits`（命中的第一欄集合，分片的可以聯集）並印 `tw-clean: N distinct entries hit, N replaced, N dropped`；`build_digits.py` 在輸出檔旁寫 `<輸出檔>.tw-clean`。重建時逐一核對每個模型的維基輸入紀錄都是寫死的那個雜湊、不是 `off`。
- 範圍：只涵蓋前 200,000 篇；換 dump 或加篇數時表要重做。
- 驗證：`test_tw_conv_guard.py` 的 `TwClean`（`test_t_first_sentence_replaced`、`test_t_empty_second_column_not_yielded`、`test_t_a_sentence_not_in_the_table_is_unchanged`、`test_r10_f_the_key_is_the_output_before_contract_12`、`test_r10_g_a_phrase_covered_xian_and_yong_stay_converted_on_a_hit`、`test_t_hash_key_is_looked_up`、`test_t_table_format_keeps_spaces_and_carriage_returns`、`test_t_hits_are_counted_per_sentence_found`、`test_t_header_hash_mismatch`、`test_t_bad_header_and_missing_tab_stop`、`test_t_missing_table_stops`、`test_t_unset_uses_the_pinned_default_and_a_placeholder_never_loads`、`test_t_off_is_the_output_before_contract_11`、`test_t_state_is_the_table_hash`、`test_t_a_spawned_process_reads_the_table_itself`、`test_t_count_through_init_hits_what_a_direct_lookup_hits`）。

### 9.9 程式與入口

- `build_counts.py`：維基是 `mixed`；`convert(text, phrase, char, maxp, rev9=False)` 是 §9.4 的轉換，`load_conv()` 回傳 `(phrase, char, 最長詞組長度)` 並填 S、M、T、N、R2、保留詞、`li`、摺疊、`term` 等模組表。`build_counts_text.py` 的入口見 §9.1。`build_digits.py`：維基 `mixed`，`--colloquial` 是 `native`。
- `tools/lib/mwconv.py`（MediaWiki zh-tw 轉換，研究用，出貨不用）的最後一層只剩 `tw-conv.tsv` 的摺疊列（`fold_tw`）。
- `holdout_guard.py` 的比對形式是原文與 `convert()` 結果，各做 `fold()`（`MERGE` 套到每個字元）。
- `MERGE` 不隨轉換改動：它是模型合併異體計數與保留集比對用的。

### 9.10 永久守門

`tools/test_tw_conv_guard.py`（`make test-python` 最後一項，只在本機跑，數秒、不需要語料；需要快取資料夾裡通過雜湊核對的 OpenCC 檔），語料版是 `tools/lib/tw_conv_guard.py FILE…`：

- 台灣句子 = `eval/dev/user-typing.txt` 與 `eval/dev/user-reported.txt` 的前文與句子欄，加上 `tools/testdata/tw-conv/tw-conv-fixture.txt`（手寫的台灣句子：用到保濕、帳號、簽名、抽籤、衝刺、沖泡、鹼性 這類單字列的台灣寫法，以及歷次出過錯的句子：在床上看程序、為裡面的群眾提供支持、今天去台南玩、管理員權限、插件、吃不出來、排泄物、竈門炭治郎，和 現象 萬象 宿舍 贊成 鍾情 甲冑 樸素 欄杆 發霉 亙古 蝨子 土虱 于美人 等）。`test_a1_the_fixture_has_the_incident_sentences` 斷言出錯過的那幾句都在檔裡。
- **斷言 1（不變）**：每句 s，`convert(s) == fold_tw(s)`。不論它被判成哪一種句子都要成立。
- **斷言 2（回程）**：s 用 `STCharacters.txt` 反查成簡體 s′（一個繁體字只被一個不同於自己的鍵列出時才換），`convert(s′)` 必須等於 `fold_tw(s)`（s′ 被判成簡體句時，再把句中 `mainland-terms.txt` 的鍵最長優先換成 `term` 的值）。不同的位置，（預期字，得到字）這一對必須列在 `tools/lib/tw-conv-ambiguous.tsv`（真正一對多、轉不回來的字對，如 乾／幹、髮／發；每列附理由，最多 40 列，測試 `test_a2_the_ambiguous_list_is_small_and_every_row_has_a_reason` 斷言）。
- **斷言 3（native 原樣）**：`build_counts_text.py --native` 讀含 爲、着、溼、程序、插件 的小檔，計數裡這些字串照原樣出現，轉換後的寫法不出現。
- **斷言 4（簡體輸入）**：`tools/testdata/tw-conv/tw-conv-simp.tsv`（每列 `簡體句<TAB>預期的台灣寫法`，目前 102 列），每列 `convert(簡體句) == 預期`（`test_a4_simplified_rows`）；固定含 这里→這裡、家里→家裡、公里→公里、冲积平原→沖積平原、之后他去了→之後他去了、厨房的灶→廚房的灶，以及原樣的 奇希里斯、后里、今天去台南玩，和 §9.5～§9.7 各條的列（`Revision8`、`Revision10` 逐列斷言種類與輸出）。逐一涵蓋轉換表每個改變的列，目前沒有被測試斷言；列的選擇是人工維護的。
- 語料版 `tw_conv_guard.py` 只印數字與字對，不印句子；斷言 1 不合的句子超過 0.1%（`MAX_UNCHANGED_FAIL`），或斷言 2 出現清單外的字對，結束碼為 1。重建模型的第一步要對整份論壇快照跑它。
- 突變：每個關鍵規則都有一個突變要讓某條斷言失敗（繁體句也套 `TWPhrases`、`is_simplified` 退回舊判斷、單字列改回 OpenCC 的值、native 走 mixed、不摺疊、只有標記字的句子也套單字列、詞組 `hold` 一律換、T 含 M、拿掉 N、拿掉異體字條件、保留詞、里 一律轉裡、咸涌規則等）。突變腳本不在追蹤的樹裡，這一條沒有在 repo 裡被直接驗證。

## 10. 已知限制

- 通語、辨類、承接、連文的計數、分群、剪枝與會意訓練用的研究工具（計數詞表產生器、分群、量詞搬移、三元組計數與剪枝、轉換表建表器、校對表建表器、訓練腳本）不在追蹤的樹裡；repo 裡驗證的是格式、綁定、載入與改綁，建置規則本身只有在可重現的環節（`build_lm.py`、`build_classes.py`、`kn_cont.py`、`export_sjtl.py`、`build_trigram.py`、`build_counts*.py`）有測試。
- 出貨的通語要靠 `--prune 6`、`--corpora` 與一組不在 repo 的計數檔重現；工具預設值（剪枝 2、維基加口語兩個檔）不是出貨設定。
- 出貨的通語、連文、常談、會意是在 §9 的轉換規則定案之前建的：通語的繁體句當時只關掉 `TWPhrases`，其餘轉換與現行規則不同；下一次重建通語時才套用 §9 的現行規則（含校對表）。重建的第一步要跑 §9.10 的語料版守門，並核對 §9.8 的命中數。
- `build-app.sh` 的缺檔下載提示寫死檔名；`check-app.sh` 只對四個檔名檢查署名檔；新增模型要手動補這兩處。
- 關於頁的名稱不由建置檢查，只由審查核對。
- 來源辨識條目的內容與核對程序不公開（§2.3）。
- 連文表與會意檔的規模欄、Release 說明記錄的檔案大小與詞彙數，是建置當時讀檔頭所得，這份規格沒有在此重新量測。

## 11. 下載與 Release

模型檔很大，不放進 git，放在 GitHub Release 的附件；repo 只記它們的 SHA-256（`data/<檔名>.sha256`、`data/models.tsv`），CI、打包 App 與測試都先比對雜湊再用。這些 Release **不是輸入法的發行版**（發行版是 `v0.x.y`）。

| 檔案 | Release | 大小（bytes） | 雜湊檔 |
|---|---|---|---|
| `bigram.sjlm` | `sj-lm-7-9.7m` | 88,467,490 | `data/bigram.sjlm.sha256` |
| `classes.sjc` | `sj-wc-6-512c` | 6,091,126 | `data/classes.sjc.sha256` |
| `kn.sjkn` | `sj-kn-2-397k` | 1,587,772 | `data/kn.sjkn.sha256` |
| `trigram.sjt3` | `sj-cx-2-7.7m` | 133,297,536 | `data/trigram.sjt3.sha256` |
| `trigram-chat.sjt3` | `sj-cx-2-950k` | 15,114,784 | `data/trigram-chat.sjt3.sha256` |
| `tinylm.sjtl` | `sj-sr-8-15m` | 14,886,880 | `data/tinylm.sjtl.sha256` |
| `colloquial.sjlm` | `sj-cl-2-69k` | 4,713,933 | `data/colloquial.sjlm.sha256` |
| `colloquial-attribution.tsv` | `sj-cl-2-69k`（同一個 Release 的另一個附件） | 2,000,233 | `data/colloquial-attribution.tsv.sha256` |

- 全部放在同一個資料夾 `data/lm/`。前三個一起用；`classes.sjc` 不含模型本身，`sj-wc-6-512c` 也只有這一個檔。綁定與載入規則見 §2。
- App 裡：打包時清單上的模型放進 `Resources/`（§1.2），使用者不用另外下載。
- 英文單字表（中英混打，`docs/spec/english-mixed.md`）不是 Release 附件，也不進 git，每次建置時由 `scripts/build-app.sh` 產生。

```sh
gh release download sj-lm-7-9.7m -R Nanako0129/shanjie -p bigram.sjlm -D data/lm
gh release download sj-wc-6-512c -R Nanako0129/shanjie -p classes.sjc -D data/lm
gh release download sj-kn-2-397k -R Nanako0129/shanjie -p kn.sjkn -D data/lm
gh release download sj-sr-8-15m -R Nanako0129/shanjie -p tinylm.sjtl -D data/lm
gh release download sj-cl-2-69k -R Nanako0129/shanjie -p colloquial.sjlm -p colloquial-attribution.tsv -D data/lm
t=$(mktemp -d) && gh release download sj-cx-2-7.7m -R Nanako0129/shanjie -p trigram.sjt3 -D "$t" && mv "$t/trigram.sjt3" data/lm/ && rmdir "$t"
t=$(mktemp -d) && gh release download sj-cx-2-950k -R Nanako0129/shanjie -p trigram-chat.sjt3 -D "$t" && mv "$t/trigram-chat.sjt3" data/lm/ && rmdir "$t"
shasum -a 256 -c data/bigram.sjlm.sha256 data/classes.sjc.sha256 data/kn.sjkn.sha256 data/tinylm.sjtl.sha256 data/trigram.sjt3.sha256 data/trigram-chat.sjt3.sha256 data/colloquial.sjlm.sha256 data/colloquial-attribution.tsv.sha256
```

- 兩張連文表先下載到暫存目錄再 `mv` 進 `data/lm`，不要用 `--clobber` 原地覆蓋：核心以記憶體映射讀這兩個檔，正在執行的程序（測試、CLI）映射著它時原地改寫會讓它們收到 SIGBUS 或讀到壞資料，rename 則不會。
- 沒有登入 `gh` 時，改用 `https://github.com/Nanako0129/shanjie/releases/download/<Release>/<檔名>` 下載。
- CI 的下載是 `scripts/fetch-models.sh`（`docs/spec/release-and-ci.md` §2.2）。

## 12. 版本怎麼往前走

| 改了什麼 | 要發的新 Release |
|---|---|
| 語料、計數或模型格式（重建 `bigram.sjlm`） | 新的通語 `sj-lm-…`，而且：辨類（`sj-wc-…`，舊的綁舊通語的雜湊，配新通語載入失敗）、承接（`sj-kn-…`，綁通語的雜湊）、兩張連文表（`sj-cx-…`，綁通語的雜湊；配不上的表會讓每個設定的載入都失敗，App 就整個沒有語言模型，所以一定同時換，`tools/build_trigram.py` 把表重新對應到新通語的詞 id）、會意（`sj-sr-…`，要重新調參：舊的綁舊通語與舊承接的雜湊，配新的不會載入）都要重發 |
| 只重新分群（類別數、分群用的資料） | 新的辨類；依詞類表的 golden（`eval/golden/sp-predict.txt`、`sw-probe.txt` 的分數也依賴詞類表）要重產；會意的 α、τ、δ 要重新調，發新的 `sj-sr-…`（雜湊檔攔不下這種錯配，要靠這張表） |
| 只改詞類權重 μ（檔頭一個值） | 新的辨類，golden 同樣重產；μ 是開著出貨的會意量過才選的，就沿用原來的會意；沒量過就照上一列重調 |
| 只換連文表 | 書面的語意校正三檔是在現在的表上選的，會意檔頭不記表的雜湊，換表時沒有檢查會攔下錯配：開著出貨的會意重量書面；沒量過就重調書面三檔，發新的 `sj-sr-…` |
| 會意的訓練、大小、匯出格式或融合參數 | 新的 `sj-sr-…`，通語與辨類不變 |
| 常談表 | 新的 `sj-cl-…`；常談只在會意檔的雜湊等於核心常數時生效（§8.3），換會意時要重量常談的參數 |

- 舊的 Release 保留，可以回到那一版。
- 發佈的通語要先加上來源辨識條目（§2.3）再算雜湊，詞類表也對加過的檔建。
- 換版時同一個 PR 更新：雜湊檔（`data/*.sha256`）與 `data/models.tsv` 的那一列、CI 的 `scripts/fetch-models.sh` 與 `release.yml` 的下載、`cli/tests/golden.rs` 的雜湊與訊息、`core/tests/engine_*.rs` 的缺檔訊息、`macos/Tests/ShanjieKitTests/Support.swift`、`scripts/build-app.sh` 的提示與內附的署名文字、網站 `site/updates.html` 的「已合併、還沒發版」（發版時再改其他頁的模型名稱）、CONTRIBUTING、`LICENSES/data.md`、這份規格 §11 的表；換會意檔時另外照 `docs/releases/README.md` 的上傳與核對步驟。換會意檔而融合參數有變時，要重新確認 selftest 用的那一句仍會被重排改掉（`Selftest.rerankRowStandard`、`rerankedText`、`ngramText`，與測試的 `RerankRow`），不會就重挑一句。依模型的 golden 照各自的產生方式用 Python 參考實作重產，Rust 逐位元組相同。
- 換檔順序：先把新雜湊合進 main 會讓 CI 拿舊檔對新雜湊而失敗，所以雜湊檔和 CI 下載在同一個切換的 PR 裡改；第一次加的新檔沒有人讀，雜湊檔可以和說明檔一起先進 main（`docs/releases/README.md`）。

## 13. 怎麼產生

各檔從語料到發佈的完整流程（輸入、步驟順序、融合參數的選法、重現與缺口）在 `docs/training.md`；下表只列工具與規格的位置。

| 檔案 | 工具 | 規格 |
|---|---|---|
| `bigram.sjlm` | `tools/build_lm.py`（§4.4）；計數用 `tools/lib/build_counts.py`、`build_counts_text.py`（§4.2、§9） | §4、§9 |
| `classes.sjc` | 分群（工具之後加回）→ `tools/build_classes.py` | §5 |
| `kn.sjkn` | `tools/kn_cont.py` | §6 |
| `trigram.sjt3`、`trigram-chat.sjt3` | 計數與剪枝（工具之後加回）→ `tools/build_trigram.py` 改綁 | §7 |
| `tinylm.sjtl` | 訓練（之後加回）→ `tools/lib/export_sjtl.py` 匯出 | §3.5、§8 |
| `colloquial.sjlm`、`colloquial-attribution.tsv` | `tools/build_lm.py --corpora`、`tools/build_attribution.py` | §3.1、§8.3 |

## 14. 授權

`bigram.sjlm`、`classes.sjc`、`tinylm.sjtl` 與 `colloquial.sjlm` 都是 **CC BY-SA 4.0**：前兩個是語料的詞頻計數，`tinylm.sjtl` 是用同一批語料訓練的權重，`colloquial.sjlm` 是口語語料的詞頻計數（另含四個公開口語來源，各自的授權都只要求署名；`colloquial-attribution.tsv` 與 `docs/releases/sj-cl-2-69k.NOTICE.txt` 逐一署名）。署名與來源見 `LICENSES/data.md`。不含任何私人對話資料。
