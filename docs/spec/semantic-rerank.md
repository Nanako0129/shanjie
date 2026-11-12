# 語意校正（會意、常談、前文）

這份寫的是現在的行為。

語意校正是 n-gram 解碼器之後的一道重排：解碼器對每一段自由輸入給出 n-best，小型字元模型會意（SJ-SR）在前 8 名之間重新選一次；聊天設定下再加一項常談（SJ-CL）的口語分數。它只能在解碼器給的候選之間選，不會產生新的文字。解碼器、詞庫與學習記憶見 `docs/spec/decoder.md`、`docs/spec/lexicon-and-packs.md`、`docs/spec/learning.md`；C ABI 的總則見 `docs/spec/c-abi.md`；輸入法殼見 `docs/spec/macos-shell.md`；模型檔的清單與版本見 `docs/spec/models.md`；評測方法見 `docs/spec/evaluation.md`。

## 1. 總覽

### 1.1 資料流

1. 每次組字區重新解碼（`Engine::refresh`），對每一段自由段（兩個固定詞或標點之間）取 n-gram 的 n-best，從中挑出前 `TOPK`（8）條沒有模糊音詞的路徑當候選池。
2. 引擎把候選的文字、n-gram 分數、前文、設定打包成一個重排工作（`RerankJob`，只含自己擁有的資料，不借用引擎），組字區先顯示 n-gram 的第一名。
3. 殼用 `take` 取走工作，在背景執行緒 `run`（算會意的對數似然，聊天設定再加常談項，用 `pick` 選一個），回主執行緒 `apply`；若期間組字有重新解碼，結果作廢，否則把選中的路徑換進組字區，字可能換一次。
4. 評測用的命令列工具（`shanjie-eval`）與重播工具（`shanjie-replay`）呼叫同一組函式（`TinyLm::rerank`、`TinyLm::rerank_colloquial`、`colloquial::applies`、`rerank_pool`、`manual_mask`），所以引擎與評測的選字相同。例外是評測 CLI 的 `--tinylm-paired`（4.6）：它把 `colloquial::applies` 的模型雜湊條件當成成立，只用在量測換會意模型時常談項照出貨方式作用的情形。

層次：n-gram 分數（`ng`）由 Rust 核心的解碼器算好再傳進來，重排不重算；會意的對數似然 `ll` 在重排時算；常談項、學習項與手動詞遮罩是同一次 `pick` 裡的修正，不在解碼器裡。

### 1.2 預設值與開關

- 核心：重排預設關（`set_rerank` 才開）；強度預設標準；載入模型（`load_tinylm`）不改這兩個值。`set_demote(false)`（降權表關）時不產生工作（會意的 α、τ 只對降權表開著的分數成立）。
- App：「語意校正」預設開，強度預設標準；存下的選擇照用。每次建引擎後殼把自己的開關與強度送給引擎，所以重建引擎（換排列、換詞包）不會把關掉的重新打開。
- 評測 CLI：不給 `--tinylm` 就沒有重排，輸出與沒有這個功能時逐位元組相同。

## 2. 會意模型檔（SJTL）

只由 `tools/lib/export_sjtl.py` 寫、`core/src/tinylm.rs` 讀。壞檔是錯誤，不會變成半個模型；錯誤只帶種類，不帶文字（不含路徑、不含檔案內容）。

### 2.1 版本與檔頭

全部小端序。版本由 magic 與版本欄一起選，檔頭長度只由這一個選擇定出，字表起點、張量起點與總長計算都用同一個長度；檔案長度必須恰好等於計算值，檔頭不完整的檔（任何前綴長度）一律 `Format`。

| 版本 | magic | 檔頭長度 | 內容 |
|---|---|---|---|
| 1 | `SJTL0001` | 100 | 偏移 0 magic、8 u32 版本、12 u32 權重型別（0 f32、1 int8）、16 n_layer、20 n_head、24 d、28 ctx、32 V（皆 u32）；36 聊天 α、44 聊天 τ、52 書面 α、60 書面 τ（f64）；68 起 32 bytes：調 α、τ 時用的 n-gram 模型（`bigram.sjlm`）的 SHA-256。δ 當 0 |
| 2 | `SJTL0002` | 116 | 前 100 bytes 同版本 1（magic、版本欄不同），偏移 100 聊天 δ、108 書面 δ（f64） |
| 3 | `SJTL0003` | 212 | 同版本 2，之後四組 f64 (α, τ, δ)：116 聊天保守、140 聊天積極、164 書面保守、188 書面積極；標準組是偏移 36–60 與 100、108 |
| 4 | `SJTL0004` | 244 | 同版本 3，偏移 212 起 32 bytes：調參時用的 Kneser–Ney 側檔（`kn.sjkn`）的 SHA-256；全 0 表示調參時沒開 KN |
| 5 | `SJTL0005` | 248 | 同版本 4，偏移 244 一個 u32：宣告的前文長度 L（字元數，1 ≤ L ≤ ctx − 2，越界拒絕載入）。版本 1 到 4 沒有宣告 |

檔頭之後：字表 (V − 3) 個 u32 Unicode 碼位（id 3 起；id 0、1、2 是 PAD、UNK、BOS）；再來是張量，順序固定：`emb`(V, d)、`pos`(ctx, d)、`lnf_w`(d)、`lnf_b`(d)，然後每層依序 `ln1_w`、`ln1_b`、`qkv_w`(d, 3d)、`qkv_b`、`proj_w`(d, d)、`proj_b`、`ln2_w`、`ln2_b`、`fc1_w`(d, 4d)、`fc1_b`、`fc2_w`(4d, d)、`fc2_b`。二維矩陣：f32 型別照原樣，int8 型別是每列 int8 加一個 f32 縮放（每列對稱量化，縮放 = max(|列|, 1e-12) / 127，還原為 `q as f32 * scale`）；一維向量一律 f32。

融合參數三組依 `RerankLevel`（0 保守、1 標準、2 積極）取用：版本 3 以上各有自己的值；版本 1、2 三個等級都用標準組。每一組都過同一個檢查（`valid_params`）：α 有限、τ 不是 NaN（可以是 +∞，等於沒有門檻）、δ ∈ [0, 1000]（`MAX_DELTA`；−0.0 視同 0）；任何一組不合法整個檔拒絕。取參數的方法：`TinyLm::params(profile, level)`；`alpha_tau_delta(profile)` 是標準組。

出貨的會意 SJ-SR-8-15M 是版本 4：6 層、寬 384、6 頭、ctx 64、字表 10,351 個 id、int8，14,886,880 bytes；沒有宣告前文長度（見 5.2）。

### 2.2 讀檔檢查

- `TinyLm::load` 與 `load_paired` 只讀一般檔案（先開檔、再檢查同一個 handle；開檔時用 `O_NONBLOCK`，所以 FIFO 不會卡住）且不超過 `MAX_FILE`（64 MiB）；不是一般檔案回 `Io`，超過上限回 `Format`；最多讀上限加 1 個 byte。
- 檔頭上限（讓前向計算的配置有上界）：V ≤ 65,536、ctx ≤ 1,024、d ≤ 2,048、n_layer ≤ 64；另要求權重型別 ≤ 1、n_layer 與 n_head 不為 0、d 能被 n_head 整除、ctx ≥ 2、V ≥ 3。
- 字表碼位必須是合法 Unicode 純量值且不重複；int8 還原後與 f32 權重都必須是有限值（縮放有限但乘上 127 仍可能溢位為 inf，一樣拒絕）。
- Accelerate 的矩陣乘法前，長度前提用 `assert!` 檢查（release 也檢查），因為呼叫用原始指標。

### 2.3 配對

`TinyLm::load_paired(path, lm)` 是引擎與評測 CLI 唯一的載入入口：

- 檔頭記的 n-gram SHA-256 必須等於載入的 `bigram.sjlm`（`OtherNgram`）。
- Kneser–Ney 側檔：檔頭記的雜湊必須等於 `lm` 載入的側檔的完整 32 bytes；檔頭全 0（或版本 1 到 3）時 `lm` 不可有側檔。三種不合各有自己的錯誤（`KnPairing::TunedWithout`、`OtherSideFile`、`TunedWith`），因為各需要不同的處理。「載入」指模型旁的側檔，與書面設定實際用不用它無關。
- 這只是配對檢查，不是對 `.sjtl` 本身的完整性檢查。`file_sha256()` 是 `load_paired` 剛解析的那一份位元組的 SHA-256，當場算好、字串留在 `TinyLm`、位元組隨即釋放（常談表之後載入時不會遇到它們還活著）；用 `parse` 或 `load` 得到的模型此欄為空。量過：release 版、真的 14.6 MB 檔、7 次，核心的軟體 SHA-256 為 35 至 52 ms（中位數約 37 ms），`parse` 為 5 至 9 ms。
- 載入雜湊的取捨：載入時算一次雜湊，是因為常談的配對（4.3）要認出「量這些格時用的那一份檔」；代價是每次建引擎多這一段時間。

換 n-gram 模型（`load_lm`、`set_lm` 成功）時引擎丟掉小模型（它記的是前一個 n-gram 的雜湊）；常談表不丟。

### 2.4 模型與前向

模型是 `tools/lib/tinylm.py` 的 `NumpyLM`：pre-LN 解碼器、輸入與輸出共用嵌入、學得的位置嵌入、GELU（tanh 形式，常數 0.7978845608028654 與 0.044715）、因果遮罩、LayerNorm eps 1e-5，f32 計算。

- macOS 的矩陣乘法走 Accelerate（`cblas_sgemm`；tanh、exp 用 vForce），其他平台用純 Rust 版；兩者的 LL 差小於 1e-5（核心單元測試用隨機權重斷言）。核心沒有外部 crate 相依。`macos/Package.swift` 的 `ShanjieKit` 因此連結 Accelerate framework（Rust 的 `#[link(kind = "framework")]` 不會帶進靜態庫）。
- 每批最多 `BATCH`（8）個候選，右側補 PAD；因果遮罩讓 PAD 不影響前面的位置。

### 2.5 匯出工具與小模型 golden

- `tools/lib/export_sjtl.py`：`--chat ALPHA TAU [DELTA]`、`--formal ALPHA TAU [DELTA]`，兩個 DELTA 都給才寫版本 2 以上，只給一個報錯；`--levels` 四組（聊天保守、聊天積極、書面保守、書面積極，各 α τ δ，共 12 個數）寫版本 4，並需要 `--kn-side`（側檔必須是記著 `--ngram-model` 雜湊的 `SJKN0002` 檔，寫入前檢查）或不給（全 0）；`--kn-side` 沒有 `--levels` 是用法錯誤。每一組值寫檔前過與讀檔相同的檢查，壞值丟 `ValueError`、不寫檔。命令列不寫版本 3（只有 `export` 帶 `levels` 且無 KN 雜湊時寫，給 golden 用）。
- 版本 5 目前沒有匯出工具：只有 Rust 讀得了，測試檔由測試程式把版本 1 的檔改寫成版本 5（`core/src/ffi.rs` 的 `as_v5`、`core/tests/common/toy.rs`）。
- `tools/gen_tinylm_tiny.py` 產生 `eval/golden/tinylm-tiny/`：固定種子的隨機小模型（`tiny-f32.sjtl`、`tiny-int8.sjtl`、`tiny-int8-v2.sjtl`、`tiny-int8-v3.sjtl`、`tiny-int8-v4.sjtl`）、`items.tsv`（numpy float64 在同一份還原後權重上算的 LL）、`pick.tsv`（`fusegrid.pick` 在固定種子隨機資料與融合差剛好是 0.25、2 的手寫例子上的選擇）。權重事後放大，讓 attention 與 GELU 的非線性真的被用到。

## 3. 打分與選字

### 3.1 候選池

- 引擎與 CLI 的候選池都是 `lm::rerank_pool(best, TOPK)`：n-best 的前 `TOPK` 條**沒有模糊音詞**的路徑，保持 n-best 順序；n-gram 第一名本身有模糊音詞時回 `None`（這段不重排，顯示 n-gram 的結果）。沒有模糊音位元時就是前 8 條。池少於 2 條的段不產生工作。
- 重排時 `n = min(候選數, ng 長度, TOPK)`。

### 3.2 對數似然

`ll_many(k, c)`：序列 `[BOS] + k + c`，對 c 的每個字元加總 log10 P；字表外的字算 UNK；`[BOS] + k + c` 超過 ctx 時從 k 最舊的字開始丟（模型實際看到 k 的最後 min(|k|, ctx − 1 − |c|) 個字元）；c 本身超過 ctx − 1 個字元是 `TooLong`；空的 c 為 0。引擎遇到 `TooLong` 或其他錯誤時這一段保留 n-gram 第一名；CLI 計數並保留第一名。少於 2 個候選時模型不跑。

k（前文鍵）怎麼來見 5.2。k 對版本 1 到 4 的模型：`SENTINEL`（"^"，沒有漢字結尾）當成空字串，因為 "^" 當前文送進字元模型會變成 UNK；對宣告 L 的模型照原文送，"^" 也照原文。

### 3.3 pick

`pick_masked(ng, ll, α, τ, δ, masked, learn)`（`pick` 是沒有遮罩與學習項的特例，與 `tools/lib/fusegrid.py` 的 `pick` 逐條相同）：

1. 少於 2 個候選回 0。`sure` = `ng[0] − ng[1] ≥ τ`。
2. `learn[i] > learn[0]` 的候選叫被教過；`masked[i]` 為真的候選不可選。`sure` 且沒有可選的被教過候選：回 0。
3. s_i = ng_i + α·ll_i（被教過項見 3.4）；從 i = 1 起找 s 最大的可選候選（`sure` 時只看被教過的），同分取 n-gram 名次較前者（嚴格大於才取代）。
4. 只有最高分比第 0 名的分數高出 δ 以上（`s[j] − s[0] ≥ δ`，含等號）才回傳 j，否則回 0。δ = 0 是原本的 S7a 規則。

第 0 名永遠不被遮罩，所以一定有候選可選。τ 與全部分數用的是整張候選表，不因遮罩而變。

`rerank(ng, cands, key, params, syl, manual, learn)`：取前 `TOPK`，算 `ll`，`pick_masked`；回傳選中的索引與 `ll`（少於 2 個候選回 `(0, None)`）。任何呼叫者給的 `params` 都先過 2.1 的檢查（不合回 `Format`）。

### 3.4 遮罩與學習項

- **手動詞遮罩**（`manual_mask`，詞庫的 `manual` 詞加上重排保護詞，見 `docs/spec/lexicon-and-packs.md`）：n-gram 第一名在某些音節上用了手動詞（或 `data/lexicon/rerank-protect.tsv` 的重排保護詞，`docs/spec/lexicon-and-packs.md` §2.12）時，這些詞受保護；沒有把每個受保護詞留在同一位置的候選不可選。手動詞在別的音節上、或第一名沒用它，就不保護任何東西；沒有受保護的詞就沒有遮罩。書面與聊天、一般與常談兩條路都套用。判斷用的是第一名實際使用的讀音（相鄰鍵誤觸時是詞所經過的詞庫音節）。
- **學習項**（句首單字的紀錄，見 `docs/spec/learning.md`）：`Learning::term(ll)` 給每個候選一個相對第一名的加分：細鍵層 `EPS_SINIT_FINE·(b_i − b_0)`（兩個方向）；中鍵、粗鍵層 `EPS_SINIT_MID`、`EPS_SINIT_COARSE` 乘 `max(b_i − b_0, 0)`，且只在 `ll_i > ll_0` 時；`b` 是紀錄權重 w / (w + 1)。三層只用命中的最長一層，不相加。三檔強度共用同一組常數（學習不屬於強度）。
- **學習守門**：某段 n-gram 第一條路徑有任何一個詞被學習加分（lp 高於該詞在該讀音下不加分時的最佳 lp）時，這段不放進工作，使用者教過的選擇不被重排改掉。句首單字紀錄（SINIT）不算加分；相鄰鍵誤觸的詞不算被教過；詞庫沒有的學到的新詞算被教過。只給評測用的 `set_learn_guard(false)`（`shanjie-replay --learn-guard off`）關閉，App 不呼叫。

### 3.5 融合參數與強度

三個等級只換這一組 (α, τ, δ)，不換模型：

| 設定 | 保守 | 標準 | 積極 |
|---|---|---|---|
| 聊天，常談生效（4.4，核心常數） | 1.3、2、1 | 1.1、2、0 | 1.1、2、0 |
| 書面（`.sjtl` 檔頭，會意 SJ-SR-8-15M） | 0.8、2、1.5 | 0.8、2、0 | 0.8、2、0 |

- 聊天檔頭的標準組是 (0.3, 2, 0.25)；聊天檔頭只在常談不生效時（別的會意檔、沒有常談表）與常談分數不是有限值時當退路。檔頭其餘聊天值此處不列。
- 積極等於標準是選定的值，不是漏寫。
- 這些格在私人的聊天集合與公開集合上選出，量測方式見 `docs/spec/evaluation.md`；格是跟著會意檔與通語底層量的，換了其中一個要重量。
- 取捨：書面用 δ 把「小模型要贏過 n-gram 一個固定分差才換」做成門檻，是因為書面的改壞代價高；聊天保守檔用 δ = 1 同理。

## 4. 常談

常談（SJ-CL）是一張普通的二元詞頻表（`colloquial.sjlm`，SJLM 格式，不用詞類、不用 KN、不用三元表），拿來給聊天設定的重排多加一項「這個斷詞的說法在口語裡常不常見」。定義逐字對應 `reference/proto/colloquial_ref.py`，實作在 `core/src/colloquial.rs`。

### 4.1 分數

對一個候選的斷詞 `words`（詞列）：

- `lc(words)`：從 `<s>` 起，對每個詞 w_i 加 log10 P(w_i | w_{i−1})，最後加 log10 P(`</s>` | w_k)。P 用表自己的二元機率，退到 (c(w) + 1) / (N + V + 1)（N 為總數、V 含 `<s>` 與 `</s>`；表外的詞 c = 0）；結尾項退到表的句末率（`Lm::eos`，λ = 1）。
- `k(words)`：詞在疊加層集合、不在基本詞表的詞數（重複算）。疊加層 = `overlay-add.tsv` 加上引擎建立時讀進來的詞包列；不含 `names-add.tsv`、`sandhi-add.tsv`。基本詞表讀 `mcbpmf-data.txt`：三欄、第一字元不是 `#`、`_`、每個音節一個字（`-` 分隔的音節數等於詞的字數）、分數欄是數字（不是數字回 `ColloquialError::Lexicon`）；疊加層與詞包列是恰好四欄的 tab 分隔列，詞在第二欄。載入後只留「疊加層有、基本詞表沒有」的詞（兩個完整集合不留，留著會多約 10 MiB 尖峰記憶體）。
- `col(words) = lc − GAMMA_PER_MU·k`，`GAMMA_PER_MU = 0.5`。

### 4.2 融合

對一段的前 `TOPK` 個候選，ll 為 3.2、col_i 為 4.1，錨點是第 0 名：

- 閘門 g_i = sigmoid((ll_i − ll_0) / `GATE_T`)（不溢位的寫法），`GATE_T = 1.0`。
- ll_eff_i = ll_i + g_i·(`MU` / α)·(col_i − col_0)，`MU = 0.5`，α 是那一檔的 α。於是融合分數 ng + α·ll_eff = ng + α·ll + g_i·MU·(col_i − col_0)：常談項的權重與 α 無關。
- 之後做 `pick_masked`（3.3），用那一檔的 (α, τ, δ)，帶手動詞遮罩與學習項。
- 第 0 名的項為 0（相對錨點），所以每個 col 加同一個常數不改變結果；閘門只讀 ll_0，不單看 ll_i。
- 任何一個 col 不是有限值（`inf`、NaN）時，這一次呼叫整個不加常談項，用同一檔的檔頭 (α, τ, δ) 做原本的 `pick_masked`（`colloquial::guarded_pick`），不讓無限大或 NaN 左右選字；遮罩與學習項在退路上照樣套用。
- 取捨：常談項只放在重排裡、n-gram 第一名固定當錨點，因為量過 n-gram 分差 ≥ τ 的列不會少任何改對，不必動解碼器。

`TinyLm::rerank_colloquial(ng, cands, words, key, co, level, syl, manual, learn)` 是引擎與 CLI 共用的唯一入口，回傳選中的索引與原始的 ll。

### 4.3 生效條件

一次重排用不用常談項，由 `colloquial::applies(profile, model)` 加「已載入常談表」判斷，三個條件同時成立：

1. 聊天設定（書面不用）。
2. 載入的 `.sjtl` 的 SHA-256 等於 `PAIRED_TINYLM_SHA256`（`356a37be…c5de`，會意 SJ-SR-8-15M；`data/tinylm.sjtl.sha256` 記的同一個值）。換了會意檔常談就靜靜地不生效，所以 selftest 要抓它（8.4）。
3. 引擎載入了常談表。

強度不是條件：保守、標準、積極都生效，各用自己的格（4.4）。不成立時行為與沒有常談的引擎逐位元組相同。引擎在建工作時判斷（記在工作裡），所以換會意檔、換設定、換強度，下一次解碼就生效。

### 4.4 三檔的格

`colloquial::cell(level)` 取具名常數，不讀 `.sjtl` 檔頭：`STANDARD` (1.1, 2.0, 0.0)、`CONSERVATIVE` (1.3, 2.0, 1.0)、`AGGRESSIVE` (1.1, 2.0, 0.0)（積極等於標準）。`MU`、`GATE_T`、`GAMMA_PER_MU` 與 `PAIRED_TINYLM_SHA256` 是同檔的具名常數。工作建立時記下當時的強度（`RerankJob` 的 `level`），融合、退路的檔頭三元組都用那一檔；之後改強度只讓舊工作作廢，不影響它。

### 4.5 句首單字估計

`Colloquial::unseen_start_unigram`：第一個詞在表的句首上下文（`<s>`）裡沒有二元組時，第一個詞的項用表自己的單字估計 (c(w) + 1) / (N + V + 1)，不用句首退回值；見過的句首詞仍用 P(w | `<s>`)。`Engine::load_colloquial` 載入後就打開它（出貨行為）；`Colloquial::load` 本身預設關。另有研究用的 `first_unigram`（每個第一個詞都用單字估計），引擎不開，只有 CLI 的 `--colloquial-first-unigram` 能開。CLI 的 `--colloquial-core` 預設也開溫和版，`--colloquial-no-unseen-start-unigram` 關掉它。

### 4.6 載入、打包、CLI 與重播

- `Engine::load_colloquial(path)`：從引擎建立時的詞庫資料夾讀 `mcbpmf-data.txt` 與 `overlay-add.tsv`，詞包列用引擎自己留著的 `pack_text`（不另找詞包檔）；可在 `load_lm`、`load_tinylm` 之前或之後呼叫。失敗（沒有資料夾、檔案讀不到或格式錯）回 `LoadFailed`，原本的表（或沒有）保持不變，並作廢進行中的工作。載入常談表時尖峰記憶體多 16 至 21 MiB（Mac、3 次、`/usr/bin/time -l`）。
- FFI `shanjie_engine_load_colloquial(engine, path)`：回傳碼同 `load_tinylm`（見 7）；子程序輸出不帶路徑。
- App：殼的 `build()` 在 `load_tinylm` **成功之後**才載入 `Resources/colloquial.sjlm`；失敗只記一行（靜態字串加回傳碼），照沒有常談表重排。沒有獨立的設定開關：跟著語意校正開關與強度走。
- selftest（8.4）要求常談在效：載入回 0，且在聊天、標準強度、前文「這間店的蛋糕很好吃」下打 dev302 第 108 列的讀音，第一次顯示是「你要不要嚐嚐看」（n-gram 與不帶常談的重排都是「你要不要常常看」）。
- 評測 CLI `--colloquial-core FILE`：用同一個 `Colloquial::load_from_dir` 載入（詞包列是 `--packs` 讀的同一份），每列依 4.3 的條件用 `rerank_colloquial` 或 `rerank`，不拒絕任何設定（書面、沒有 `--tinylm` 就照條件不加常談項）；檔案不存在在開始前就報錯。`--tinylm-paired`（沒有值）把這次 `--tinylm` 載入的模型當成配對模型：模型雜湊的條件當成成立，聊天設定與載入常談表兩個條件照舊；要同時有 `--tinylm` 與 `--colloquial-core`，否則是用法錯誤；核心的 `colloquial::applies`、引擎、App 與重播都不受影響。摘要行設定標記 `+colloq:<表 SHA-256 前 8 碼>`，溫和版開著（出貨預設）不加尾碼，關掉加 `+us0`，`--colloquial-first-unigram` 加 `+fu`；給了 `--tinylm-paired` 時接著加 `+pair:<會意檔 SHA-256 前 8 碼>`（模型本來就是配對檔時選字不變，標記照樣加）。`--tinylm-scores` 在常談實際跑過的列（聊天、配對檔或 `--tinylm-paired`、有 `ll`）最後多一個 `"col": [...]`，每個候選一項，不是有限值的是 `null`（這一列的 `c` 是退路的 plain pick）。
- 重播 `shanjie-replay`：像 App 一樣，小模型開著且 `data/lm/colloquial.sjlm` 存在時預設在 `load_tinylm` 之後載入常談表（詞包照重播的 `build_engine`）；`--colloquial FILE` 改載另一份、`--no-colloquial` 不載（兩者並用是錯誤）；摘要行標記同 CLI。
- `tools/bench.py` 與 `tools/kpi.py` 依 `data/colloquial.sjlm.sha256` 有沒有，判斷這一版的設定是否帶常談標記。

### 4.7 資料檔與署名

- 常談表隨 App 打包成 `Resources/colloquial.sjlm`；現行為常談 SJ-CL-2-69K（詞彙 69,457，剪枝 2、折扣 0.75，不綁 `bigram.sjlm` 的雜湊），檔案清單與雜湊見 `docs/spec/models.md`。雜湊記在 `data/colloquial.sjlm.sha256`；`scripts/fetch-models.sh` 與 CI 下載同一個 Release 的兩個附件（表與署名檔）後核對。
- 署名檔 `colloquial-attribution.tsv`（欄位 source、unit_id、author、url、licence、sentences）由 `tools/build_attribution.py` 從口語池的來源欄產生，每個來源單位一列，不含任何語料文字。池裡四個來源的授權都只要求署名，以此署名。一個單位是 unit id 第一個冒號之前的部分；各來源的句數加總必須等於程式裡的 `EXPECTED`（合計 143,895 句，數字在版控裡，不是腳本自己算的）；池裡有列沒有單位 id、影片來源缺網址、句子來源缺作者、或同一個（來源, 單位）在對照檔出現兩次，一律以結束碼 1 失敗（結束碼 2 是用法錯誤）。輸出每次相同。
- `scripts/build-app.sh` 要求表與署名檔存在且雜湊與 `data/*.sha256` 相同才打包，署名檔放進 `Resources/LICENSES/`；`scripts/check-app.sh` 檢查 bundle 裡的署名檔雜湊等於 `data/colloquial-attribution.tsv.sha256` 記的值，然後執行 selftest；表本身的雜湊由 `scripts/check-models.sh` 對 `data/models.tsv` 與 `data/*.sha256` 核對。

## 5. 前文

### 5.1 殼讀什麼

殼在每個組字開始前讀一次插入點左邊的文字，傳給核心當前文：

- 條件：該次按鍵是沒有（Shift 以外）修飾鍵的字元鍵且目前不在組字；學習閘門（安全輸入、黑名單 App、取不到 bundle ID）擋住時不讀、傳 NULL；有標記文字（組字中）時不讀；選取範圍不可讀時不讀。
- 範圍：插入點之前最多 `LeftContext.maxUTF16`（256）個 UTF-16 單位。App 回傳比要求長時只取最後 256 個單位並當作被截過。
- 處理（`LeftContext.raw`）：被截過時先去掉開頭可能被切斷的第一個 grapheme；從最後一個換行（Swift `Character.isNewline`：`\n`、`\r\n`、U+2028、U+2029、VT、FF、NEL）或 NUL 之後開始（NUL 當換行，不刪除，刪除會把 `中\0文` 接成「中文」）；以 grapheme 計數，留最後 `maxGraphemes`（64）個；不濾標點、空白、英文、emoji。
- **終端機類 App**（bundle ID 在 `LeftContext.terminals`：Terminal、iTerm2、Warp、kitty、WezTerm、Alacritty、Hyper、Ghostty）只傳結尾 ≤ 2 個漢字（`LeftContext.han`），因為終端機的一行裝著指令與輸出。編輯器內建的終端機面板與編輯區是同一個 bundle ID，分不出來，不在其內。
- 只在記憶體：不寫檔、不寫偏好、不記 log。`cjk-spacing` 用的前一個字元從同一次讀取取。

### 5.2 核心保存與會意的前文鍵

`set_left_context(text)`（NULL 或空字串＝沒有；不是 UTF-8 回 2 並清空）：

- `left` = `context_key(text)`：結尾連續的漢字最多 2 個（`MAX_CONTEXT`），沒有則空。n-gram 解碼的歷史詞、選字記憶的鍵、預測列、數字層都只看它，所以前文變長不改變這些。
- `left_full` 只留用得到的長度（資料最小化）：載入的會意模型宣告了 L 時，`text` 的最後 L 個 Unicode 純量值（用 `chars()`，不切位元組）；否則，`text` 去掉最多一個結尾半形空白後，結尾是 2 至 32 個 ASCII 字母、且前一個字元不是 ASCII 數字（`english_tail`），就只留那個英文詞和它後面的一個空白；都不是則空。`left_cut` 記 `left_full` 是否截自更長的文字。
- 清除：`clear_all`（送出、Esc、`reset`、回傳碼 4、自動送出都經過它）清 `left`、`left_full`、`left_cut`；空組字區上沒處理的字元鍵（規則 22 的直通）之後也清；換會意模型（`set_tinylm`）也清 `left_full`。只有未完成符號時按 Esc 走 `clear_pend`、不經 `clear_all`，`left` 與 `left_full` 留到下一次組字開始再被覆寫。
- 不外流：`left_full` 不經 C ABI 回傳、不進 `ShanjieOutput`、不寫學習檔、不寫 log、不跨程序、不給網路功能；重排工作只帶最後 L 個字元（沒有宣告 L 時帶 `left`）。已經排進佇列的工作複本活到那次執行結束，Rust `String` 與 Swift 字串釋放時不清零。
- `set_left_context` 不作廢進行中的工作（殼每個組字開始都呼叫它）。

**會意的前文鍵**（`TinyLm::key_for(before)`，引擎與 CLI 共用）：`before` 是組字區這一段之前的全部文字（前文，加上前面各段選定後的字與中間的固定詞、標點）。宣告 L 的模型（版本 5）：`before` 的最後 L 個字元，原文，不經 `context_key`；版本 1 到 4：`context_key(before)`（結尾 ≤ 2 個漢字，沒有則 `SENTINEL`，在 3.2 變空字串）。`RerankJob::run` 依序處理各段，後一段的前文用前面各段**選定後**的字重算（不用 n-gram 第一名）。出貨的會意 SJ-SR-8-15M 沒有宣告 L，所以出貨行為是結尾 ≤ 2 個漢字；模型是否讀長前文取決於檔頭，不取決於殼傳了多少。

### 5.3 英文前文

英文前文表（見 `docs/spec/english-mixed.md`）在新組字區的游標起點也生效：`english_before(0)` 看 `left_full` 是不是上述的英文詞結尾，是就取小寫的詞；`left_full` 被截過（`left_cut`）且英文詞從第 0 個字元開始時不算（可能是半個詞）；`"Joe "` 生效，`"Joe  "`、`"Joe1"`、`"J"` 不生效。組字區有單位時看單位，不看前文。英文前文不改變解碼、重排輸入與學習。

## 6. 引擎的重排工作

### 6.1 產生

`refresh_lm` 的尾端，在小模型已載入、重排開、降權表開（`jobs_on`）時，對每一段自由段：

1. n-best 至少 2 條，且 `rerank_pool`（3.1）回至少 2 條；
2. 通過學習守門（3.4）；
3. 句首單字紀錄（SINIT）：有紀錄的段，候選池、n-gram 分數、第一條與守門看的路徑都用不看句首單字紀錄的解碼；另一次抬升的解碼只用來找含學到的字的路徑，那條路徑以不抬升的 lp 與分數換進池（取代池的最後一條，不取代第一條）。偏好由融合分數的學習項帶出（3.4）。

一個工作含：世代號、`Arc<TinyLm>`、該檔的 (α, τ, δ)（`model.params(profile, level)`）、常談表（`applies` 成立才帶）、強度、`manual` 詞集合（與詞庫共用）、學習項的倍率（出貨為 1）、前文（宣告 L 的模型：`left_full`；否則 `left`）、各段（在 `path` 裡的起點、候選路徑 `(詞, lp, 降權 δ, 音節數, 是否 SINIT 標記)`、n-gram 分數、`b`、層、第一名實際用的讀音、這一段與前一段之間的顯示文字 `gap`）。沒有任何一段可重排時不產生工作。只有最新一次的工作留著；`take_rerank_job` 交出一次。

### 6.2 世代與作廢

每個工作帶產生時的世代號（整個程序共用的 `AtomicU64` 取號，重建的引擎不會重用號碼）。以下會換新號、作廢待處理與進行中的工作：每次重新解碼（`refresh`）、`clear_all`、`load_lm`、`set_lm`、`load_tinylm`、`load_colloquial`、`set_rerank`、`set_rerank_level`、`set_profile`、`set_demote`、學習開檔與清除、評測用的 `set_learn_guard` 與 `set_reading_decode`。

不作廢：`set_learning`（不改解碼，只管選字要不要記下來）、`set_left_context`、游標移動、未完成的注音符號、進入預測列、直通鍵。這些之後算完的結果照樣套用，所以「打得快」時 Enter 送出的是重排後的字而不是 n-gram 的字。

### 6.3 執行

`RerankJob::run` 只用工作裡的資料，不碰引擎，可在任何執行緒；`RerankJob`、`RerankResult` 是 `Send`（編譯期斷言，沒有 `unsafe impl`）。依序處理各段：`before` = 前文 + `gap` + 前面各段選定的字；`key = model.key_for(before)`；聊天且 `applies`（工作建立時已判斷）用 `rerank_colloquial`，否則用 `rerank`；錯誤（含 `TooLong`）選 0。每段記下起點、預期的詞列（n-gram 第一條）、選中的索引、選中路徑、用的 key。工作與結果含候選文字，沒有 `Debug`、不寫進任何紀錄。

### 6.4 套用

`Engine::apply_rerank(result)` 回傳 `None`（不改變）的情形：世代號不同；候選窗開著；預測列已進入（`pred_sel` 有值，不踢掉使用者在列裡的選取）；任一段的 `path` 已不是工作建立時的詞列（核對起點起的每個詞）；每一段都選 0。否則從右到左把選中路徑換進 `path`，重建 `display`，預測列只在套用當下非空時才重算（被按鍵規則清掉的列不會因套用而再出現），回傳新的輸出。音節、游標、固定詞與候選窗不動。之後選字時記下的前文（`pre`）用重排後的文字。

送出（Enter、自動送出、`reset`）一律送畫面上的內容；還沒套用的工作因世代號改變而作廢。

### 6.5 組字區上限

組字區一次最多 `MAX_SYLLABLES`（128）個單位。自由段的長度窗 W = 會意的 ctx − 3（沒有模型時 61）：一個鍵或選字會讓自由段超過 W 時，先把它的開頭凍結到 W − 16 個字元（凍結成固定詞），再解碼；所以工作看到的每一段都放得進模型的視窗，不會落到 `TooLong`。重排開或關、有沒有小模型都照此。細節見 `docs/spec/keys-and-composition.md`。

## 7. C ABI

宣告在 `core/include/shanjie.h`，實作在 `core/src/ffi.rs`；總則（碼 4 的處理、輸出所有權、不寫 log）見 `docs/spec/c-abi.md`。每個匯出都包 `guard`（攔 panic）。

| 函式 | 行為與回傳碼 |
|---|---|
| `shanjie_engine_load_tinylm(engine, path)` | 0 成功；1 engine 或 path 為 NULL；2 路徑不是 UTF-8；3 讀檔失敗、格式錯、沒有載入 n-gram、配對不符（2.3）；4 內部錯誤（引擎重設）。失敗時引擎維持原本的小模型（或沒有），n-gram 照常用。不改 `set_rerank`，不改目前顯示 |
| `shanjie_engine_load_colloquial(engine, path)` | 回傳碼同上；失敗時保留原本的表；作廢進行中的工作 |
| `shanjie_engine_set_rerank(engine, enabled, out)` | `enabled` 0 或 1（其他回 2，狀態不變）；回傳組字快照，關掉時畫面立刻回到 n-gram 的結果 |
| `shanjie_engine_set_rerank_level(engine, level, out)` | 0 保守、1 標準、2 積極；大於 2 回 2（狀態與世代不變）；沒有小模型或模型是版本 1、2 時只記下等級（回 0，實際用標準組）；`load_tinylm`、`load_lm` 保留記下的等級；回傳快照 |
| `shanjie_engine_rerank_levels(engine, *levels)` | 位元遮罩（bit0 保守、bit1 標準、bit2 積極）：沒有小模型 0、版本 1 或 2 為 2、版本 3 以上為 7（不管值是否與標準相同、也不管設定）；engine 或 levels 為 NULL 回 1；不改任何狀態，所以回 4 也不重設 |
| `shanjie_engine_rerank_take(engine, *job)` | 交出最新的工作一次；沒有工作回 0 且 `*job` 為 NULL；`job` 非 NULL 時一律先設 NULL；engine 或 job 為 NULL 回 1；4 時引擎重設且 `*job` 為 NULL。交出的每個工作都要由呼叫端 `free` 恰好一次 |
| `shanjie_rerank_run(job)` | 0 成功；1 job 為 NULL；4 攔下的 panic（沒有碰任何引擎，組字不變，不留結果，連同一個工作更早一次的結果也清掉）。不需要引擎，可在任何執行緒，引擎已釋放後仍可 run 或 free；不可取消，新按鍵只能讓結果作廢 |
| `shanjie_engine_rerank_apply(engine, job, out)` | 取走工作裡的結果：有改變回 0 與快照（handled 1，commit ""）；沒有結果（還沒 run、run 回 4、已套用過）、過期、組字已不是工作建立時的、候選窗開著、預測列已進入、每段都選 0，回 `SHANJIE_NO_CHANGE`（5）且 `*out` 為 NULL；NULL 參數回 1；4 時引擎重設。不釋放工作；同一個工作第二次 apply 一律回 5 |
| `shanjie_rerank_free(job)` | `NULL` 不做事；run 回傳之後可在任何執行緒呼叫 |

`take`、`run`、`apply` 的非 0 碼不代表按鍵直通（它們沒有對應的按鍵）。結果放在 `Mutex<Option<RerankResult>>`，`apply` 用 `take()` 取走，所以即使殼違規讓 run 與 apply 同時發生也不會有資料競爭。未定義行為：同一個工作 free 兩次、free 之後再 run 或 apply、free 一個不是 `take` 交出的指標、run 進行中同時 apply 或 free、同一個工作同時在兩個執行緒使用。工作內有候選文字與前文：不記 log，`load_tinylm` 的路徑也不記。

`shanjie_engine_set_left_context(engine, utf8)`：見 5.2。

## 8. 殼（macOS）

### 8.1 佇列

- `Shell` 必填一個 `RerankRunner`：App 傳 `QueueRerankRunner`，在 label 固定的 `DispatchQueue`（QoS `.utility`）上跑 `shanjie_rerank_run`，跑完回主執行緒；測試傳自己完成的 runner。
- 每次把引擎輸出套到組字區之後呼叫 `take`；一次只跑一個：已有在跑的，新工作留著（只留最新一個，被取代的立刻 free），跑完再接著跑。`take` 回非 0 與 `apply` 回 4 一樣處理（清組字區與候選窗，只記回傳碼）。
- 完成時在主執行緒：用 weak 參照以 `===` 確認 session 仍擁有組字、引擎仍是目前的那個（不用位址比較，不強參照舊引擎），開關仍開，才 `apply`；0 套用輸出、5 不做事、4 照處理碼 4。核心的世代號是第二道防線。無論結果為何，完成處理一進來就用 `defer` 清掉「執行中」並接著跑留著的工作，不會卡住。
- 工作由 Swift 包裝類別持有：原始指標私有，只提供 `run()`（nonisolated）與 `apply(on:)`（主執行緒）；`deinit` 呼叫可注入的 free 函式，所以每條路徑（被取代、引擎重建、開關關掉、過期、碼 4、正常套用）都恰好 free 一次；runner 的 closure 持有包裝物件而不是指標。`@unchecked Sendable` 的三個不變條件：一次只跑一個；apply 只在 run 回傳之後；free 只在 deinit。
- 主執行緒不無限期等背景。唯一的等候是 Enter：這個 session 有重排工作在跑或排隊時，主執行緒最多等 `ENTER_RERANK_WAIT_MS`（100 ms，整次 Enter 一個期限），用每個工作各自的信號量（`run()` 回傳後才發訊號），等到就套用，等不到就送出畫面上的字。
- 殼不寫任何與工作有關的 log：run 非 0 時只記碼。

### 8.2 開關與強度

- 「語意校正」：選單項目與設定視窗開關；存在使用者偏好 `rerank`，沒設過＝開；關掉時丟掉等待中的工作並立刻回到 n-gram 的結果。降權表關閉，或小模型沒載入（`rerankAvailable` 為假）時，開關反灰（打勾仍顯示存著的選擇），因為核心此時不產生工作。
- 強度：設定視窗在開關下面的三段選擇（保守／標準／積極，標籤「強度」），存為偏好 `rerankLevel` 的字串（`conservative`、`standard`、`aggressive`；沒設或不認得的值＝標準）。永遠顯示三個選項；語意校正關閉、開關反灰，或載入的模型沒有保守與積極兩組（遮罩缺 bit0 或 bit2）時整個控制反灰。改了立刻送給引擎並套用回傳的快照；選單沒有強度項目。
- 每次 `build()` 建引擎：`set_rerank(rerankOn)`、再 `set_rerank_level(儲存的等級)`（在 `load_tinylm` 之後、`engine = e` 之前），然後讀 `rerank_levels` 給設定視窗用。

### 8.3 載入與打包

- `build()` 在 `load_lm` 成功後載入 `Resources/tinylm.sjtl`（檔名是 `Shell.tinyLMFile` 常數，路徑只從 bundle 的 Resources 接，沒有 init 參數、偏好鍵或環境變數能指到別處，也不下載），成功再載入 `Resources/colloquial.sjlm`（4.6）。檔案不在或載入失敗：照常用 n-gram（不重排）、不中斷建引擎，只記一行靜態字串加碼。
- `scripts/build-app.sh` 要求 `data/lm/tinylm.sjtl` 存在且雜湊等於 `data/tinylm.sjtl.sha256` 才打包；`scripts/check-app.sh` 檢查 bundle 裡的檔與記的雜湊（不是與 `data/lm/` 的檔互比，那只證明複製正確），並執行 selftest。CI 的 `shell` job 與發版 workflow 下載同一組模型後核對。換會意檔的時候 `.sjtl` 綁的 n-gram 與 KN 雜湊要一起對，否則語意校正會靜靜地沒載入——selftest 會抓到。

### 8.4 selftest

`shanjie --selftest`（`Selftest.swift`）在重排相關處要求：

1. 載入小模型回 0；聊天設定、標準強度、無前文、載入 ACG 與 IT 詞包的引擎，打 dev302 第 65 列（`ㄨㄛˇ ㄉㄥˇ ㄧ ㄒㄧㄚˋ ㄗㄞˋ ㄍㄨㄛˋ ㄑㄩˋ`）：重排開時是「我等一下再過去」，關時是「我等一下在過去」（逐鍵「取 → 算 → 套用」同步驅動）。
2. 載入常談表回 0，重排開、前文「這間店的蛋糕很好吃」，打第 108 列：「你要不要嚐嚐看」。

沒載入模型、模型配不上、常談沒作用時 selftest 失敗，所以語意校正與常談不會靜靜地沒作用地出貨。selftest 不寫檔、不碰偏好。

## 9. 評測 CLI 與重播

- `--tinylm FILE.sjtl`：用 `load_paired` 載入；n-gram 雜湊不符、KN 側檔不符、`--no-classes`、`--no-demote` 都是錯誤（α、τ 只對辨類與降權表開著的分數成立）。每列取候選池（3.1）的前 8 名重排，top1、`--rowstats`、摘要行都用重排後的第一名；前文鍵用 `tl.key_for(ctx)`（`--context` 時；不加 `--context` 時為空）。摘要行設定標記加 `+tl:<檔 SHA-256 前 8 碼>`。
- `--rerank-level conservative|standard|aggressive`（預設 standard）：需要 `--tinylm`（沒有 `--tinylm` 時任何值都是用法錯誤）；用在版本 1、2 的檔且不是 standard 時結束碼 2 並說明；不是 standard 時摘要行在 `+tl:…` 後加 `+rl:conservative` 或 `+rl:aggressive`。`tools/bench.py` 與不在本 repo 的研究工具因此把它當作不是出貨設定。
- `--tinylm-scores OUT`：每列一行 JSON，只有數字沒有文字：`i`、`n`、`ng`、`ok`、`ed`、`glen`、`c`（選中的索引，沒重排為 0）、`ll`（n ≥ 2 的列）、`masked`（手動詞遮罩擋掉的索引，空也寫）；有 `--colloquial-core` 且常談實際跑過的列最後多 `col`（4.6）。有模糊音位元時 `n`、`ng`、`ok`、`ed`、`ll`、`masked` 列的是候選池，`c` 仍是 n-best 的索引。`--tinylm-scores-topk N`（研究用，N ≥ 8）把 `ll` 延伸到前 N 個候選，不改其他欄位；需要 `--tinylm-scores`，不能與 `--fuzzy` 並用。`--tinylm-scores` 與 `--set holdout` 不能並用（逐候選記錄是調 α、τ 的資料）。
- `--tinylm-paired`：見 4.6；常談項對 `--tinylm` 給的模型照出貨方式作用，`col` 欄也在這種列出現。
- `--picks FILE`（有沒有 `--tinylm` 都可用）：每列一行 `列號\t名次\t句子`，行數等於 rowstats，照列號順序（`--threads` 時也是）。名次是送出的那一句在這一列 n-best 的索引（0 是 n-gram 第一名，和 `--tinylm-scores` 的 `c` 同一個索引）；句子是這一列算對錯用的那一句（`top1_sha256` 用的同一份）。`--fuzzy` 時沒有任何讀音覆蓋的列寫 `列號\t-\t`。檔裡有列的內容，所以和 `--dump` 一樣在 `--set holdout` 時拒絕（讀任何檔之前）；rowstats 不變。
- 候選比 ctx − 1 長的列保留第一名並在 stderr 計數；stderr 另印每列重排時間 p50、p95 與載入時間、載入前後的記憶體。
- `shanjie-replay` 的重排選項：`--rerank each|final`、`--no-tinylm`、`--learn-guard on|off`、`--rerank-level`、`--colloquial`、`--no-colloquial`；引擎照 App 建（降權開、重排開、標準強度、載入常談表），所以重播的選字與 CLI 在同條件下相同。詳見 `docs/spec/evaluation.md`。

## 10. 驗證

- **核心**（`make test-rust`）
  - `core/tests/tinylm_tiny.rs`：LL 對 numpy（`ll_matches_numpy_on_both_files`，|ΔLL| < 1e-4）、批次不改 LL、前文長度規則、各版本檔頭讀值與壞檔（任何前綴長度、magic 與版本交叉、壞值、長度差 1 byte）都是 `Format` 不 panic、`pick` 的手算案例與 δ 邊界（含等號）、`pick` 對 `pick.tsv` 逐列相同、`rerank` 把檔頭的 δ 傳給 `pick`、版本 2 到 4 除檔頭外與前一版相同、KN 配對（`load_paired_binds_the_kn_side_file`）、版本 5 的前文長度與 key（`v5_declares_the_left_context_length`、`v5_keys_with_the_raw_last_l_characters`、`a_long_candidate_sees_only_the_tail_of_a_declared_key`）。
  - `core/src/colloquial.rs` 單元測試：sigmoid 不溢位、融合相對錨點且閘門讀 ll_0、`fused_pick_uses_the_registered_cell`、`each_level_picks_with_its_own_cell`、`the_term_weighs_the_same_at_every_alpha`（權重與 α 無關）、非有限 col 退回 plain pick 與遮罩在退路上也有效、學習項在每條路徑上都在。
  - `core/tests/colloquial.rs`：`lc`、`k`、`col` 在合成表上的手算值、疊加層與基本詞表都有的詞不算 k、詞包列只在給定時算、`rerank_colloquial_fuses_with_the_levels_cell`、`a_non_finite_col_falls_back_to_the_levels_header_triple`。
  - `core/tests/engine_colloquial.rs`：`the_term_applies_only_under_its_conditions`、`applies_is_chat_and_the_paired_model`、`each_level_uses_its_own_cell`、`load_order_does_not_matter`、`load_colloquial_turns_the_first_word_estimate_on`、`a_failed_load_changes_nothing_but_the_job`、`k_counts_the_pack_words_the_engine_was_built_with`、`the_colloquial_term_stands_alone`。
  - `core/tests/engine_rerank.rs`：沒有模型或開關關掉時沒有工作且按鍵不變、套用顯示模型的選擇、過期丟棄、不重新解碼的鍵保留工作、`set_learning` 不作廢、後一段用前一段選定的字當前文、宣告的前文長度、英文前文不改解碼與重排輸入、換模型丟前文、兩個引擎不共用世代、工作套用一次且比引擎活得久、自動送出與英文 token 讓工作作廢、`demote_off_makes_no_job`、`new_ngram_model_drops_the_small_model`、預測列進入時 apply 無效、學習守門與 `learn_guard_off_reranks_the_learned_stretch`、`rerank_is_off_until_set_rerank`、`kneser_ney_side_file_refuses_the_model`、手動詞在最終檔上不被重排改掉、`replay_dev302_parity_and_measurements`（需要環境變數指到真的 `.sjtl`，沒給就略過：dev302 每列逐鍵打、每鍵後同步取算套用，送出的句子與 CLI 的第一名逐字相同）。
  - `core/tests/engine_english_next.rs`：英文前文與英文詞結尾的規則、左文隨組字清除、只留英文詞、長前文只以漢字尾段解碼與學習。
  - FFI 子程序測試（`core/src/ffi.rs`）：載入各錯誤碼且輸出不含路徑、沒有工作時 NULL、run → apply → free、apply 兩次、所有 NULL 組合、run 內 panic 回 4 且輸出不含內容、`load_tinylm` 不改重排開關；C 標頭冒煙測試 `core/tests/c/abi_smoke.c` 每個函式至少呼叫一次。
- **CLI**（`cli/tests/tinylm.rs`、`cli/tests/colloquial.rs`、`cli/tests/replay.rs`、`cli/tests/measure_flags.rs`）：`--tinylm-paired` 與 `--picks` 的 `paired_flag_turns_the_term_on_for_another_model`、`paired_flag_changes_nothing_on_the_paired_model`、`picks_match_the_scores_the_dump_and_the_rowstats`、`picks_of_a_row_without_candidates`、`picks_threads_agree_on_the_whole_dev302`、`the_flags_are_refused_where_they_do_not_apply`；n-gram 不符被拒、壞檔與孤兒 `--tinylm-scores`、KN 側檔必須是記著的那一個、`sentinel_context_is_an_empty_k`、`rerank_level_uses_that_levels_set` 與 `rerank_level_refusals`、`summary_line_records_a_non_standard_level`、`a_declared_left_length_keys_cli_rows_like_the_engine`、`no_option_is_byte_identical_to_before`、`the_term_applies_at_every_strength`、`the_shipping_setting_applies_and_threads_agree`、`the_first_word_estimate_is_on_by_default_and_the_flag_turns_it_off`、`scores_carry_col_and_recompute_the_pick`、`colloquial_replay_agrees_with_the_cli`。
- **Python**（`make test-python`）：`tools/test_export_sjtl.py`（不給 δ 時輸出與 golden 逐位元組相同、各版本寫出與讀回、壞值報錯而且不留檔）、`tools/gen_tinylm_tiny.py` 的 golden 可重產、`tools/test_build_attribution.py`（各來源加總與停止條件，合成輸入）。
- **Swift**（`make test-swift`）：`macos/Tests/ShanjieKitTests/RerankTests.swift`（用真核心、真 n-gram、真小模型：按鍵後有工作交給 runner、完成且有改變才多一次 `setMarkedText`、過期不套用、free 的次數等於取到的工作數且沒有 free 兩次、開關預設、重建引擎仍送開關與等級、強度偏好與反灰）、`SelftestTests`、`LogTests`（前文放標記字串，驗證殼只記靜態文字與碼）、`LearningTests` 與 `LeftContextBenchTests`（前文讀取的邊界與延遲）。真佇列 runner 另有一個測試。
- **執行**：selftest 在 CI 的 `shell` job（`make selftest-bundled`）與 `scripts/check-app.sh` 跑。選字品質（出貨標準、判讀集、各檔的 α、τ、δ 怎麼選）見 `docs/spec/evaluation.md`。

## 11. 已知限制

- 重排非同步：組字區先顯示 n-gram 的結果，小模型算完可能換一次字；每個鍵都從 n-gram 重新解碼再重排，已顯示的重排結果可能在下一個鍵先變回 n-gram 的字、算完再換回來（不是已鎖定的字）。
- 重排只在前 8 名（沒有模糊音詞的）候選之間選；n-gram 前 8 名裡沒有正確字的句子，語意校正救不回來。
- 小模型的視窗是 ctx 64：宣告 L 的模型看到的前文是 min(L, ctx − 1 − 候選字數) 個字元；出貨的模型沒有宣告 L，只看結尾 ≤ 2 個漢字。
- 常談只在聊天設定、且只與會意 SJ-SR-8-15M 配對；書面設定與別的會意檔沒有常談項。
- 書面設定教過一次的字詞，有時還會被重排蓋回去（學習守門只擋 n-gram 第一條路徑上有被加分詞的段，細節見 `docs/spec/learning.md`）。
- 終端機類 App 之外的 App，殼傳 64 個 grapheme 的原文，但出貨的會意模型不使用；編輯器的內建終端機面板分不出來，照一般 App 處理。
- `left_full` 與重排工作內的文字在釋放時不清零。
- 錯字回報集的「權限加好了」書面標準與積極兩檔仍選錯（聊天對）。
- SJTL0005 沒有匯出工具，只有測試檔能產生。
