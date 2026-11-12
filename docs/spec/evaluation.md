# 評測與基準（現況規格）

這份寫的是現在的行為。

範圍：評測工具怎麼讀列、怎麼計分、怎麼比較（`shanjie-eval`、`shanjie-replay`、`tools/evalstats.py`）、保留集與調參集的規則、跨版本基準測試（`tools/bench.py`）與每版 KPI 表（`tools/kpi.py`）。解碼器本身的選項見 `docs/spec/decoder.md`，語意校正見 `docs/spec/semantic-rerank.md`，學習見 `docs/spec/learning.md`，發版與 CI 的步驟見 `docs/spec/release-and-ci.md`。

## 1. 總覽

### 1.1 工具與檔案

| 路徑 | 做什麼 |
|---|---|
| `cli/src/main.rs`（`shanjie-eval`） | 每列一次解碼、取選出的那一句；`--lm` 模式輸出摘要行、`--rowstats`、`--forms-rowstats`、`--dump` 等 |
| `cli/src/bin/replay.rs`、`cli/src/replay.rs`（`shanjie-replay`） | 一列一列、一鍵一鍵打進真引擎（`core::engine::Engine`），可模擬改選與學習；研究工具，不出貨 |
| `cli/src/common.rs` | 兩個執行檔共用：讀列、寬鬆對照表與合法寫法表的載入、`row_stats`、`format_rowstats` |
| `reference/proto/lm_eval.py` | Python 參考實作；`--rowstats` 與 Rust 逐位元組相同 |
| `tools/evalstats.py` | 讀兩份 rowstats，印配對比較的一行表格 |
| `tools/build_forms.py`、`eval/forms.tsv` | 合法寫法表的產生器與產物 |
| `tools/holdout_guard.py`、`tools/merge_guard_rows.py` | 保留集與調參集對訓練文字的包含比對、報告合併 |
| `tools/bench.py`、`tools/bench/replay/` | 跨版本基準測試與其按鍵重播程式 |
| `tools/kpi.py` | 每版 KPI 表 |
| `eval/`（見 `eval/README.md`） | 評測列檔、凍結的套件集合、保留集、基準與 KPI 的結果 JSON |

### 1.2 列檔格式與讀列規則

- 列的格式是 `前文|句子|讀音`：三欄以 `|` 分隔，讀音是以空白分開的注音音節（輕聲符號寫在音節後面，例如 `ㄗ˙`）。
- `shanjie-eval --lm` 與 `shanjie-replay` 用同一個讀列函式（`cli::common::three_field_rows`）：只收「恰好三欄」的行，其餘的行（含空行）默默略過。因此兩個工具的列號（從 1 起）互相對得上。
- 讀列的入口：
  - `--rows FILE`：讀這個檔的所有三欄行。
  - `--dev N`：取 `eval/dev/` 頂層的 `*.txt`（依檔名排序，不進子資料夾）串起來的前 N 個三欄行。`--dev 302` 就是開發集 dev302。
  - `--set holdout`（只有 `shanjie-eval`）：讀 `eval/holdout/` 頂層所有 `.txt`（不進子資料夾，所以 `eval/holdout/next/` 底下的檔不會被讀到），用 `parse_rows`（兩欄或三欄都收，空行與 `#` 開頭的行略過，沒有讀音的列用詞庫的讀音，讀不出來的列丟掉）。
  - `--limit N`（`--rows` 時）：只取前 N 列。
- 三個入口擇一，同時給或都不給是錯誤。
- 錯誤訊息不帶列的內容：只有檔案路徑、行號、欄數或長度。

### 1.3 寬鬆對照（lenient）

「第一名正確」一律是寬鬆對照後相等：先套單字對照 `她妳它牠嘗周臺裏 → 他你他他嚐週台裡`，再套 `eval/variants.tsv`（`core::eval::Lenient`）。

- `eval/variants.tsv` 由 `tools/build_variants.py` 從教育部《重編國語辭典修訂本》產生：釋義寫「也作／亦作」、字數相同、至少一個注音讀音相同的詞對，已套單字對照、分組、標準形取最小字串；每行 `異體<TAB>標準形`。載入端不做任何轉換，壞行或重複行是錯誤。來源授權 CC BY-ND 3.0 TW，只用於評測。
- 套法是由左至右的最長匹配，每個匹配換成標準形。
- 表的路徑讀環境變數 `SHANJIE_VARIANTS`（空字串等於沒設），沒設用 repo 的 `eval/variants.tsv`；檔案不存在或壞了就報錯結束。Python 參考實作（`reference/proto/eval.py` 的 `lenient`）讀同一份表，規則相同。
- 候選與正解各自套一次再比；正解用原句、候選用選出的那一句。

### 1.4 摘要行

`shanjie-eval --lm` 印一行：`## <名稱>  lm-<profile><設定標記>  {'n': N, 'top1': T, 'oracle@64': O, 'top1_sha256': '<64 位十六進位>'}`。

- `top1`：寬鬆對照下，選出的那一句等於正解的列數。開了語意校正時，選出的那一句不一定是 n-gram 的第一名。
- `oracle@64`：前 64 名裡有正解（寬鬆對照）的列數。
- `top1_sha256`：每列選出的那一句（原字串，不經寬鬆對照）以 `\n` 串接後的 SHA-256。
- 設定標記依實際開的功能串起來（例如 `+ctx`、`+kn:<8 碼>:θ1:β1`、`+tl:<8 碼>`、`+acg+it`、`+tri:<8 碼>`、`+colloq:<8 碼>`、`+rd+rx:<8 碼>+rm`），讓結果對得回模型與旗標；基準測試用它守住「旗標漏帶」（見 7.3）。
- `--threads N`（預設 1，只有 `--lm` 模式）：以每 32 列一塊分給 N 個執行緒、共用同一份模型；輸出檔與摘要行和 N = 1 逐位元組相同，stderr 的語意校正延遲行會註明是在競爭下量的。
- 保留集（`--set holdout`）只許印指標：`--dump`、`--dump-words`、`--picks`、`--tinylm-scores` 都拒絕（`check_lm_opts` 與 `run_lm` 在讀任何檔之前檢查）；`--rowstats` 只有數字，允許。`--rows` 沒有這道拒絕，見 5.3。

## 2. 逐列統計與配對比較

### 2.1 rowstats 檔

- 每列一行 `列號\t對錯\t錯字數\t正解字數`；列號從 1 起、依評測檔順序；對錯是 `0` 或 `1`；後兩欄是非負整數。檔內只有 ASCII 數字、tab、換行，沒有任何文字。
- 對錯：和摘要行的 top1 相同（寬鬆對照）。
- 錯字數：對的列是 0（寬鬆對照算對、但原字不同的列也是 0，例如正解「她」、第一名「他」）；錯的列是選出的那一句與正解句子（原字串，不經寬鬆對照）的 Levenshtein 距離，單位是 Unicode 純量值。正解字數是正解句子的 Unicode 純量值個數。
- 每一行由一個純函式算出：Rust `row_stats(top1, gold, ok) -> (u32, u32)`、Python `row_stats(top1, gold, ok)`；整個檔也各只有一個寫出函式（Rust `format_rowstats`、Python `format_rowstats(rows)`），所有集合（含 `--set holdout`）、`shanjie-eval`、`shanjie-replay`、`lm_eval.py` 共用。

### 2.2 產生 rowstats

- `shanjie-eval --lm … --rowstats FILE`，可和 `--dev`、`--rows`、`--set holdout` 併用。
- `python3 reference/proto/lm_eval.py … --rowstats FILE`，同樣的位元組。
- `shanjie-replay … --rowstats FILE`：第一次顯示的逐列統計（見 4.6），格式相同。

### 2.3 `tools/evalstats.py compare`

`python3 tools/evalstats.py compare BASE CAND [--label 名稱] [--seed S] [--resamples N] [--net] [--ci L] [--cluster FILE]`，印 Markdown 表頭加一行：

| 欄 | 意思 |
|---|---|
| n | 列數 |
| top1 基準／新 | 兩邊對錯欄的加總 |
| 改對／改壞 | 基準錯、新對的列數／基準對、新錯的列數 |
| p | 精確 McNemar：只看改對與改壞的列，二項分布 1/2 的雙尾機率（`min(1, 2·Σ_{k≤min(b,c)} C(n,k) / 2ⁿ)`，n = 0 時 1.0） |
| CER 基準／新 | 字元錯誤率：各列錯字數總和 ÷ 正解字數總和，印成百分比 |
| Δtop1 95% 區間（列） | 配對 bootstrap：每次對列有放回抽 n 列，算「新 − 基準」的第一名列數差 |
| ΔCER 95% 區間（百分點） | 同一批抽樣算 CER 差（抽到的列的錯字數差總和 ÷ 抽到的正解字數總和） |

- 預設 `--seed 20261008`、`--resamples 10000`；同樣的輸入與參數，輸出逐位元組相同。區間取排序後索引 `int(q·N)` 與 `int((1−q)·N)`，`q = (1 − 水準) / 2`。
- 只用 Python 標準函式庫；`mcnemar_exact`、`levenshtein`、`paired_bootstrap` 可給其他工具 import。
- 這些情況報錯結束（stderr 印 `evalstats: …`，結束碼 1）：兩個檔列數不同、列號不對齊、欄位不是 ASCII 數字、對錯欄不是 0 或 1、兩邊正解字數欄不同（不是同一個評測集）、正解字數總和為 0、`--ci` 不在 0.5 到 0.999 之間、`--cluster` 的代號數和列數不同。
- 標籤裡的 `|` 會轉成 `\|`，免得表格欄位位移。

### 2.4 `--net`、`--ci`、`--cluster`

- `--net`：表尾多三欄。淨值＝改對 − 改壞（1:1）；改壞率＝改壞 ÷ n；改壞數的 95% 精確 Poisson 單尾上界（Garwood：滿足 P(X ≤ 改壞數 | λ) = 0.05 的 λ，用精確累積分布二分求根；改壞 0 時 2.9957、1 時 4.7439）。不給 `--net` 時輸出逐位元組不變。
- `--ci L`（預設 0.95）：改 bootstrap 區間的水準，表頭的「95%」跟著寫成 L（例如 `--ci 0.90` 取 5% 與 95% 分位）；不給時輸出不變。
- `--cluster FILE`：FILE 每列一個叢集代號（同一則訊息、同一段發言、同一個條目），bootstrap 以叢集為單位重抽（叢集依第一次出現的順序編號）；標籤後加「(clustered, M clusters)」；McNemar 不變。每列各自一個叢集時，抽樣結果等於逐列 bootstrap。

### 2.5 `tools/evalstats.py mde`

`mde (--discordant D | --base BASE --cand CAND) --alpha A [--power 0.8]`：不一致 D 列時，雙尾精確 McNemar 在 α 下（`p ≤ α` 就拒絕）檢定力達到 power 的最小淨值（淨值和 D 同奇偶；假設 D 列按二項分布分給兩邊，新版對的機率為 `(D + 淨值) / (2D)`）。給兩個 rowstats 時，D 是對錯欄不同的列數。沒有淨值達得到檢定力時印「no net reaches the power」。用於確認片之前：淨值低於它，在這個列數下分不出和 0 的差別。

### 2.6 報法

- 任何會改到選字結果的改動，每個集合在聊天與書面兩種設定各報一行同一張表：基準與新版各跑一次 `--rowstats`，再 `compare`（需要 1:1 欄時加 `--net`）。
- 保留集：由沒參與實作的 fresh 審查者對基準與新版各跑一次，寫到自己的暫存目錄，檢查檔內只有 `[0-9\t\n]`，用 `compare --net` 只回那一行，然後刪掉 rowstats。細節見第 5 節。
- 私人集合在私人環境裡跑，只報表格的數字。

### 2.7 1:1 計分欄

- 淨值＝改對 − 改壞，改對與改壞等權；改壞率＝改壞 ÷ n；改壞數的上界用 2.4 的 Garwood 上界。
- 改壞率的上限在小集合上只能當點估計看，所以同時報改壞數的 95% 精確上界。
- 子句彼此相關（同一則訊息的子句）時，McNemar 與 bootstrap 假設列獨立，區間偏窄；需要時用 `--cluster`。

### 2.8 驗證

- `python3 -m unittest tools/test_evalstats.py`（`make test-python` 與 CI 的 Python 步驟都跑）：
  - McNemar 已知值：(6, 0) → 0.03125、(9, 1) → 0.021484375、(0, 0) → 1.0、(3, 3) → 1.0；
  - Levenshtein：空字串、相同、一個替換、一插一刪、BMP 外字元；
  - Poisson 上界已知值、大 k 不下溢、確實是累積分布的根；
  - 固定 seed 兩次相同、兩份相同時區間為 [0, 0]、區間包含點估計；
  - `compare` 的各種報錯（列數不同、列號不對齊、非數字、全形數字、空檔）；
  - `--net` 的三欄、不給 `--net` 時輸出不變；
  - `mde` 與暴力窮舉一致、檢定力在 MDE 達到而在低兩列處沒有；
  - 叢集 bootstrap：每列一叢集時等於逐列 bootstrap、相關叢集讓區間變寬、叢集名稱不影響結果、`--ci` 預設不變、超出範圍乾淨報錯。
- Rust 與 Python 的 rowstats 一致：`cli/tests/golden.rs` 的 `lm_rowstats_match_python_byte_for_byte` 比對 dev302 聊天與書面的 `eval/golden/s2-lm-dev302-{chat,formal}.rowstats`（也檢查只含 `[0-9\t\n]`、對錯欄加總等於摘要行的 top1）；CI 的 Python 步驟用 `reference/proto/lm_eval.py` 重產同樣兩個檔並 `cmp`。
- `cli/src/common.rs` 的單元測試：`row_stats_zero_for_ok_rows`（`row_stats("他很好", "她很好", true) == (0, 3)`）、`rowstats_rows_count_from_one`。
- `check_lm_opts` 的單元測試（`cli/src/main.rs`）：保留集加 `--dump` 為錯，保留集加 `--rowstats` 為可。
- 目前的樹上沒有任何測試以 `--set holdout` 執行 CLI。

## 3. 合法寫法分數（forms）

### 3.1 兩個分數

- **現行分數**：1.3 的寬鬆對照。出貨標準、判讀、已登記的實驗都只用它；forms 不改它的任何一位元組。
- **合法寫法分數（forms）**：先做現行的寬鬆對照，再套 `eval/forms.tsv`（詞對、單字對）之後相等。兩個分數並列報，不取代彼此。報告裡「現行錯、forms 對」的列，就是只靠 forms.tsv 才算對的列（例如「姊姊」與「姐姐」、「潮溼」與「潮濕」）。

### 3.2 `eval/forms.tsv`（規則版本 `forms-v1`）與 `tools/build_forms.py`

表由 `tools/build_forms.py` 從同一份教育部辭典資料（`dict-revised_bkup.json`，環境變數 `SHANJIE_MOEDICT` 指向它，SHA-256 寫死在 `tools/build_variants.py` 裡，不符就停）產生，規則全寫在程式裡，不手寫任何一對。來源授權 CC BY-ND 3.0 TW，只用於評測。

- **讀音拆法**（本表所有讀音比對共用）：一個 heteronym 的 `bopomofo` 字串，以 `（讀音）`、`（語音）`、`（又音）` 切開；每段把連續空白（含全形空白）收成一個半形空格、去掉頭尾空白、丟掉空段。一個詞條的讀音集合是所有 heteronym 切出的段的聯集；`bopomofo` 為空的 heteronym 不貢獻讀音。寫進 `char` 行的讀音條件轉成列檔的寫法：每個音節開頭的輕聲符號 `˙` 移到音節結尾（辭典 `˙ㄗ` → 列檔 `ㄗ˙`）。
- **詞對規則**：詞條標題 T（至少 2 字、不含 `{`）的任一個釋義用 `(?:也作|亦作)「([^」]+)」` 找到 Y（引號內以 `」、「` 分隔），Y 與 T 等長、Y ≠ T、不含 `{`、Y 也是詞條，而且兩者的讀音集合有交集。和現行寬鬆對照的差別只在讀音比對：讀音欄把「讀音」「語音」寫成一串的詞（例如「姊姊」）也收得進來。
- **單字規則**：標題是單一字 X，看它的第一個 heteronym；它的釋義只有一條，且符合其一，就收 X→Y（Y 必須也是單字詞條）：(a) 釋義整句符合 `「(.)」的異體字。`（牀→床）；(b) 釋義是空字串、`link` 剛好一項、整句符合 `同「(.)」。`（濕→溼、沈→沉）。「通「Y」」不收（布／佈不進表）；釋義有正文的單字不收（姐不進表）。
- **單字對的讀音條件**：X 的第一個 heteronym 有讀音時，用它切出的讀音；沒有（規則 (a) 的異體字條目）時，用 Y 第一個 heteronym 的讀音。單字對只在該位置的讀音在條件裡時才換，所以「濕」只在讀 ㄕ 時換成「溼」（讀 ㄒㄧˊ 不換）、「沈」只在讀 ㄔㄣˊ 時換成「沉」（姓氏讀 ㄕㄣˇ 不換）。
- **正規化**：
  - 詞對：先套現行單字對照與 `variants.tsv`，套完兩邊相同的丟掉；聯集分組，標準形取最小字串；同一組的詞要有至少一個共同讀音，沒有的組整組不收，說明行記下丟掉的組數。
  - 單字對：X 先套現行單字對照，套完等於 Y 或已被現行對照涵蓋的丟掉；單字對的鏈（X→Y 而 Y 也是某一對的 X）一路解到不再變的字，讀音條件是鏈上每一段條件的交集，交集是空的就在那裡切斷；有環就建表失敗。
- **檔案格式**：`#` 開頭的說明行（四行：規則版本與生成說明、來源 commit 與 SHA-256、格式、詞對數／單字對數／丟掉的組數），接著 `word\t寫法\t標準形` 與 `char\tX\tY\t讀音1,讀音2…`，依類別與寫法排序。`forms-v1` 的表是 28 個詞對、617 個單字對。
- 用法：`python3 tools/build_forms.py` 產生；`--check` 重建後和 repo 裡的檔逐位元組比對，並檢查說明行計數、單字對的鏈沒有未解到底的行、讀音條件沒有開頭的 `˙`；`--explain 寫法` 印出這個寫法由哪條規則、哪個詞條的哪一句原文產生。
- 表的雜湊記在 `eval/README.md`。

### 3.3 `--forms-rowstats`

- `shanjie-eval --lm … --forms-rowstats FILE`：格式同 `--rowstats`，判斷用 forms。可和 `--rowstats` 在同一次執行同時寫。
- **只在加了這個旗標時**才讀 `forms.tsv`（路徑讀 `SHANJIE_FORMS`，沒設用 repo 的 `eval/forms.tsv`）；加了旗標而檔案不存在或格式錯就報錯結束，而且發生在建立任何輸出檔之前（既有的 `--rowstats` 檔不會被清空）。沒加旗標時完全不讀、不檢查這個檔。
- forms 是獨立的一張表（`core::eval::Forms`），在寬鬆對照之後才套，不併進 `Lenient`。順序固定：先現行寬鬆對照，再套詞對（最長匹配），最後套單字對；每道替換都等長，位置不變。
- 單字對用列的讀音（依空白切成音節，和句子逐字對齊）；候選與正解用同一列的讀音。某一列的音節數和句子字數不同（例如含英文）時，這一列只套詞對、不套單字對，stderr 印 `forms: N rows without the single-character step …` 計數。
- 解析規則：`word` 行的寫法與標準形等長，`char` 行的 X、Y 各恰好一個字、讀音條件不得有空項；重複的鍵或壞行是錯誤，錯誤只帶行號。

### 3.4 驗證

- `python3 -m unittest tools/test_build_forms.py`（`make test-python`）在一份迷你辭典上：只因讀音拆開才收的詞對（姊姊／姐姐同一組）、單字規則 (a)（牀→床）、(b)（濕→溼讀音條件 `ㄕ`、沈→沉讀音條件 `ㄔㄣˊ`）、不能有的對（姐／姊、布／佈、小姐／小姊、濕的 `ㄒㄧˊ`、沈的 `ㄕㄣˇ`）、輕聲符號移到結尾、`--check` 抓開頭 `˙` 與說明行計數錯、鏈解到底、讀音條件取交集且斷鏈時停在第一段、異體字條目取目標的第一個 heteronym 讀音、詞組需要共同讀音、環使建表失敗。
- `core/src/tests.rs`：`forms_apply_order_and_reading_condition`（詞對先、單字對後，順序可觀察；每個位置各看自己的讀音條件；音節數不同時只跳過單字步驟；壞行與重複行只帶行號）、`forms_shipped_table_cases`（用真的 `eval/forms.tsv` 與 `variants.tsv`，依評分器的方式先 Lenient 再 Forms：姐姐＝姊姊、小姐≠小姊、濕在 `ㄒㄧˊ` 不換、沈在 `ㄕㄣˇ` 不換）。
- `cli/tests/forms.rs`：潮濕與潮溼同時當正解時，現行分數剛好錯一列而 forms 兩列都對（同一次執行同時寫兩種 rowstats）；音節數不符的列被計數；不加旗標時不讀表（`SHANJIE_FORMS` 指向不存在的檔也成功），加旗標而表不存在時報錯、stdout 空、既有 `--rowstats` 檔保持原樣、forms 檔不被建立。
- 加不加 `--forms-rowstats`，現行分數的 `--rowstats` 與摘要行逐位元組相同。

### 3.5 範圍外

- 人名、地名另外放寬（表只來自辭典，單字對有讀音條件）；「通「Y」」關係與有分工的單字（布佈、周週、了瞭等）；釋義寫在正文而不是整句的單字。
- 出貨標準與判讀規則不因 forms 改變。

## 4. 真引擎重播 `shanjie-replay`

### 4.1 做什麼、怎麼執行

`shanjie-eval` 每列只解碼一次、看第一名；使用者實際看到的是引擎逐鍵的結果：每一鍵重新解碼、語意校正的工作非同步套用、改選凍結其他字、學習改變之後的列。`shanjie-replay` 量這些，也量「使用者要按幾個鍵才能打對」。

```
shanjie-replay (--rows FILE | --dev N) [--profile chat|formal] [--no-tinylm] [--learn off|on]
               [--rerank each|final] [--correct] [--strict] [--learn-file FILE] [--learn-guard on|off]
               [--colloquial FILE | --no-colloquial] [--no-reading-decode] [--packs LIST]
               [--rerank-level conservative|standard|aggressive]
               [--rowstats FILE] [--keys FILE] [--firsts FILE] [--sinit-stretches FILE]
```

- 讀列和 `shanjie-eval --lm` 用同一個函式（1.2），列號對得上；`--rows` 與 `--dev` 擇一。
- 整次執行只照 App 的方式建**一個**引擎（4.2），之後每列：
  1. `set_left_context`（列的第一欄），逐鍵打出讀音；
  2. 每一鍵之後依 `--rerank` 的時序處理語意校正的工作（4.4）；
  3. 打完時記下「第一次顯示」：打字中途引擎已經送出的文字（`Output.commit`，例如組字達到上限自動送出）接上當下的 `preedit`（Enter 會送出的文字）；
  4. `--correct` 時模擬使用者改選（4.5）；
  5. 按 Enter 送出；`--learn on` 時引擎從這次送出學習，學到的留給後面的列（列照檔案順序）。
- 輸出檔在讀完列檔之後才建立；`--rowstats`、`--keys`、`--firsts`、`--sinit-stretches` 任何一個和 `--rows` 或 `--learn-file` 是同一個檔（比對裝置與 inode，所以符號連結與硬連結都算）就拒絕執行，結束碼 2，原檔不動。`--dev` 沒有這道檢查。
- 其他錯誤結束碼 1，訊息不帶列的內容。stderr 印 `models:` 行，列出各模型檔的 SHA-256 前 8 碼（`bigram.sjlm`、存在的 `classes.sjc`／`kn.sjkn`／`trigram.sjt3`／`trigram-chat.sjt3`、`tinylm.sjtl`、載入時的 `colloquial.sjlm`），讓結果對得回模型。
- 模型路徑固定在 repo 的 `data/lm/`。

### 4.2 引擎的設定（照 App）

- 建立順序與預設值照 App 的建引擎流程：帶詞包建引擎（`acg`、`it`、`meme` 與 `beauty`，App 的預設）、`load_lm`（bigram，連帶的 classes／KN／trigram 照 `load_lm` 的規則）、`load_tinylm`、`load_colloquial`（口語表）、降權開、語意校正開、強度 standard、`set_profile`。
- **和 App 的已知差異**（寫進設定標記）：預測列關（`set_prediction(false)`）、英文混打關（`set_english(false)`）、縮寫關。App 預設前兩者都開；開著時 Space 開的候選窗會把預測列的項目排在前面、有的項目不是以游標為結尾的範圍，英文也可能搶走 Space，改選的鍵數就無法照 4.5 定義。兩者都不影響 n-gram 與語意校正的選字。候選窗為橫式，一頁 9 項（`PAGE_SIZE`）。
- 口語表：小模型開著且 `data/lm/colloquial.sjlm` 存在時預設載入（`--no-tinylm` 時不載）；`--colloquial FILE` 改載另一份，`--no-colloquial` 不載（對照用；兩者併用是錯誤）。載入後 `models:` 行多一欄 `colloquial.sjlm:<8 碼>`、設定標記多 `+colloq:<8 碼>`。口語項是否真的用到由核心照條件判斷（聊天、配對的小模型、表已載入）。
- 讀音項：引擎的 n-gram 解碼器帶讀音項（`Engine::load_lm` 建表），所有設定都生效；`--no-reading-decode`（研究用，`Engine::set_reading_decode(false)`）把它關掉。設定標記每次都以 `+rd+rx:<std-exempt.tsv 的 SHA-256 前 8 碼>+rm` 結尾（`+rm`：讀音質量規則，decoder.md §5）（取自引擎真的載入的那張表，`Engine::reading_tag`），加 `--no-reading-decode` 就沒有這三個標記。
- 學習：`--learn off|on`（預設 off）。on 時在第一個鍵之前呼叫一次 `set_learning(true)`；單獨使用時不開學習檔（只在記憶體），新詞 B 照預設開。學習狀態只在這一個引擎、這一次執行內累積。
- `--profile chat|formal`（預設 chat）。`--no-tinylm` 關掉語意校正（對照用）。

### 4.3 選項與評測專用開關

- `--packs LIST`：`acg`、`it`、`quotes`、`meme`、`beauty` 的逗號清單（研究用）；不給時和明寫 `--packs acg,it,meme,beauty`（App 的預設，遮罩 29）相同，迷因詞包加進預設之前的輸出（預設 `acg,it,beauty`）與美妝詞包加進預設之前的輸出（預設 `acg,it`）會變；重複給的 `--packs` 會疊加，第一個取代預設。設定標記按位元順序列出詞包（`+acg`、`+quotes`、`+it`、`+meme`、`+beauty`）。
- `--strict`（只給評測）：第一次顯示、送出文字的對錯與 `--correct` 的改選目標都逐字比，不經寬鬆對照（1.3）。不給時輸出不變；給了設定標記尾端加 `+strict`。
- 列裡的標點：讀音欄可以有單獨一個記號 `，` `。` `？` `！` `：`（照 App 送 Shift＋`,` `.` `/` `1` `;`）或 `、`（不按 Shift 的 `\`），標點和注音留在同一個組字區，每個標點算一鍵；其他記號（例如 `；`，標準排列沒有直接的鍵）報錯。送出文字用字串比，不經寬鬆對照。每百字改選次數的分母不計標點。
- `--learn-guard on|off`（預設 on，等於 App）：學習守門（一段的 n-gram 第一名裡有學習加過分的詞，整段不送語意校正）開或關；off 時那些段照常送語意校正，其他一切不變。引擎多一個 Rust 的設定函式 `set_learn_guard(bool)`（預設 true）與唯讀的 `guard_skips()`，不進 `core/include/shanjie.h`，App 與 `shanjie-eval` 都不提供。設定標記 off 時加 `+noguard`。非 on／off 的值被拒絕。
- `--rerank-level conservative|standard|aggressive`（預設 standard，等於 App；`Engine::set_rerank_level`，只給評測）：換語意校正的檔位。只在不是 standard 時進設定標記（`+level:conservative`、`+level:aggressive`），沒給時輸出不變。
- `--sinit-stretches FILE`（只含數字）：句首單字學習（見 `docs/spec/learning.md`）的規則 10 段，每段一行 `列號\t1\tΔng\tα·Δll`（`ng[錨點] − ng[學到的那條]`、`α·(ll[錨點] − ll[學到的那條])`，6 位小數；錨點就是學到的那條時兩個數是 0），學到的那條不在池裡的段是 `列號\t0\t\t`。不給時其他輸出不變，這個檔不進設定標記。`--learn on` 時 stderr 多一行 `sinit: stretches N swaps N missing N overturned N tau_opened N`（五個計數，全部列加總；`tau_opened` 恆為 0，欄位留著讓這一行維持五個數字）。
- `--learn-file FILE`：見 4.7。

### 4.4 語意校正的時序 `--rerank each|final`

- `each`（預設）：每一鍵之後 `take_rerank_job` → `run` → `apply_rerank`，等於模型每次都在下一鍵之前算完。
- `final`：打字中的工作都不套用（下一鍵會讓它過期），只在最後一個打字鍵之後、Enter（或改選）之前套用一次，等於使用者打得比模型快、送出前停一下。
- 兩者都計數 `rewrites`：`apply_rerank` 回傳 `Some`、而且套用後的 `preedit` 和套用前不同的次數（只算語意校正造成的改字；n-gram 自己逐鍵改字不算）。`final` 每列最多 1。

### 4.5 改選模擬 `--correct`

- 比對用寬鬆對照（`--strict` 時逐字）。顯示的文字與正解字數不同、或和組字區的音節數不同時不改選（一個音節對一個字才知道一次改選的範圍），記成改不了。打字中途已送出的部分不能再改，只改還在組字區的部分（和正解對應的那一段）；中途送出的字數和已完成音節數對不上時整列不改選。
- 每一輪：找第一個不同的字的位置 i；從游標位置 j = i+1 往右（到 i 加引擎最長詞長 `max_window_len` 為止，再往右的候選窗不可能蓋住 i）找第一個「候選窗裡有一項，它的範圍蓋住 i、文字等於正解同一段」的 j（候選窗列出以游標為結尾的各種長度）。掃描時的鍵（移游標、開窗、翻頁、Esc 關窗）不計入 `actual`。
- 找到就照最少鍵的走法選它：游標移到 j（Left／Right 每鍵一個音節，Home／End 比較少時用它們：`min(|Δ|, 1 + j, 1 + (n − j))`）、Space 開窗、Space 翻到那一頁、按那一項在頁內的數字鍵（數字鍵直接選定，不必再按 Enter）。選字不移動游標；之後的改選會蓋掉它重疊到的凍結詞，所以凍結不會擋住後面的修正。
- 選完重新比對，再下一輪；最多改選 2 × 字數次。找不到任何 j、或選完顯示的文字沒有變，就停止改選、記成改不了，直接送出。
- `--correct` 不給時不改選，第一次顯示就是送出的文字。

### 4.6 輸出與數字

輸出都只有數字，不含任何句子。

- `ideal`：照讀音打的最少鍵數（每個音節的注音符號鍵加聲調鍵，一聲用 Space；標點一鍵）加 1（Enter）。`actual`：實際送進引擎的鍵數（打字加改選的移游標、開窗、翻頁、選字鍵加 Enter）。
- 按鍵分數（KySS 的注音版）＝Σideal ÷ Σactual。
- `picks`：改選次數；`final_ok`：送出的文字是否等於正解；`unfixable`：要求了 `--correct`、送出的文字仍不等於正解的列。
- `--rowstats FILE`：第一次顯示的逐列統計，格式同 2.1，可直接給 `tools/evalstats.py compare`。
- `--keys FILE`：每列一行 `列號\tideal\tactual\tpicks\tfinal_ok\trewrites`。
- `--firsts FILE`（只給一致性比對用）：每列一行第一次顯示的文字。檔案可能含文字，只用在公開集合（dev302、打字測驗、錯字回報），檔案不進 repo；與 `--learn-file` 併用一律拒絕（第一次顯示可能含學習檔裡的詞）。
- 摘要行（stdout 一行）：`## <名稱>  <設定標記>  {'n', 'top1', 'final_ok', 'unfixable', 'autocommits', 'kyss', 'picks_per100', 'rewrites', 'guard_skips', 'guard_rows', 'top1_sha256'}`。
  - `top1`：第一次顯示的第一名正確列數；`autocommits`：中途有送出（例如組字達到 128 音節的上限）的列數；`kyss` 四位小數；`picks_per100` ＝ 改選次數 × 100 ÷ 正解字數（不計標點）；`top1_sha256`＝第一次顯示以 `\n` 串接的 SHA-256，算法同 `shanjie-eval`。
  - `guard_skips`：每列打完最後一鍵時（第一次顯示之前、`final` 套用之前），最近一次解碼裡「本來會送語意校正（語意校正開、模型已載入、那段的候選至少 2）而被學習守門擋下」的段數，全部列加總；`guard_rows` 是其中段數至少 1 的列數。沒有學習資料或 `--no-tinylm` 時恆為 0。最後一鍵觸發自動送出時，工作隨送出作廢，那一列計 0。
  - 設定標記：`replay-<profile>+ctx<詞包>+nopred+noen+lm:<8 碼>`，接 `+tl:<8 碼>`（沒有小模型時 `-notinylm`）、`+learn`、`+rerank:final`、`+correct`、`+learnfile`、`+noguard`、`+colloq:<8 碼>`、`+level:…`、`+rd+rx:<8 碼>+rm`，最後（有 `--strict` 時）`+strict`。
- 「學了之後本來對的變錯」：同一份列檔跑 `--learn off` 與 `--learn on` 各一次（都 `--correct`），兩個 `--rowstats` 用 `evalstats compare`，改壞數就是這個數字；工具本身不另算。

### 4.7 `--learn-file`（唯讀載入學習檔）

- 引擎建好後、建立任何輸出檔之前，讀 FILE 的位元組（只有位元組，不帶權限、旗標、ACL、擴充屬性），寫進本次執行專用的暫存目錄（權限 0700、每次不同、在系統暫存目錄下）的 `learning.tsv`（`create_new`、0600），對該目錄呼叫 `learning_open`，**載入完立刻刪掉暫存目錄**（學習資料已在記憶體；刪除失敗在 stderr 警告；之後 `--learn on` 的存檔因目錄不在而無害地失敗）。原檔只被讀，不寫、不改名、不隔離。
- 載入結果不是正常載入（檔頭錯、損壞、全新）、沒有任何可用的筆數、或 `learning_open` 失敗，就報錯結束（錯誤只帶種類），不會在沒有學習資料下默默重放；這些失敗都發生在輸出檔建立之前，既有輸出檔不會被截斷、也不會產生 `.corrupt`。
- 解碼不論 `--learn` 開關都讀載入的學習資料。設定標記加 `+learnfile`；stderr 只印筆數與跳過的行數，不印檔案內容衍生的任何字、前文或讀音。**刻意不記錄載入的是哪一份學習檔**，所以不同快照的 `+learnfile` 結果無法從輸出區分，由執行者自己記。
- FILE 與 `--rows` 或任一輸出檔是同一個檔就拒絕（結束碼 2）；與 `--firsts` 併用拒絕。

### 4.8 與 `shanjie-eval` 的關係

- 一致性：`--learn off --rerank each`、不改選時，第一次顯示的文字和 CLI 出貨設定（`--lm data/lm/bigram.sjlm --profile P --context --packs acg,it,meme,beauty --tinylm data/lm/tinylm.sjtl`，口語表載入時加 `--colloquial-core`）每一列相同；比較時先看摘要行的 `top1_sha256`，不同再用 `--firsts` 逐列比。引擎和 CLI 真的不同的列，歸入「離線對、真引擎錯」的那一桶，不改引擎去遷就重播。
- 重播只在引擎外加唯讀的存取函式；不改引擎行為（唯一的例外是評測專用、預設等於 App 的開關 `set_learn_guard`）。

### 4.9 驗證

`cli/tests/replay.rs`（每個測試用真引擎、n-gram 加小模型；每個引擎約 700 MB，所以同時最多兩個測試拿著引擎（自己的，或它啟動的 CLI 程序的）；用 `eval/dev/user-reported.txt` 的公開列，每個測試寫明它依賴的顯示，模型一換就先在這裡失敗）：

- `ideal_and_actual_are_the_hand_count`：`ideal` 與不改選時的 `actual` 等於手算的鍵數（我再重打 14、跟我說我再換 21）。
- `cursor_move_cost_takes_the_shortest_route`：Left／Right／Home／End 的最短走法。
- `a_repick_costs_the_moves_the_window_and_the_digit`：改選的鍵數是移游標、開窗、翻頁與數字鍵（含要翻兩頁才找得到的字），送出的是正解。
- `strict_compares_the_texts_exactly_and_is_off_by_default`：`--strict` 下「他」「她」不被視為相同；不給旗標時，同樣的列輸出逐位元組不變（除了 `+strict` 標記）。
- `a_punctuation_mark_stays_in_the_composition_and_is_sent_exactly`：標點留在組字區、算一鍵；`；` 報錯。
- `a_gold_text_no_window_offers_is_unfixable`：正解不在任何候選窗時不改選、不無限迴圈，記成改不了。
- `learning_carries_a_pick_to_the_next_row_only_when_on`：`--learn on` 時同一句打兩次，第二次的第一次顯示就是第一次改成的字；off 時不會。
- `rerank_each_applies_while_typing_and_final_only_at_the_end`：`each` 在打字中途多次套用，`final` 的 `rewrites` ≤ 1；和手寫的 take／run／apply 迴圈在第二個引擎上的計數一致。
- `text_committed_mid_row_is_kept_counted_and_not_re_picked`：超過組字上限（`MAX_SYLLABLES` 128 個音節）時中途送出的文字是第一次顯示的一部分、`autocommits` 計到這一列、改選只動還在組字區的音節。
- `summary_line_sums_guard_skips_and_counts_guard_rows`：兩欄的加總與計列方式。
- `a_pick_that_changes_nothing_ends_the_repicking`：沒有進展的改選結束改選。
- `an_output_that_is_the_rows_file_is_refused`：輸出等於列檔時結束碼 2、列檔不變（`./` 別名也算）；列檔讀失敗時（結束碼 1）不建立輸出檔。
- `outputs_are_numbers_only_and_the_rowstats_are_the_clis`：`--rowstats`、`--keys` 只有 `[0-9\t\n]`，`--rowstats` 逐位元組等於 CLI 對同一份列檔的輸出，`--keys` 每列六欄。
- `colloquial_replay_agrees_with_the_cli`、`reading_decode_replay_agrees_with_the_cli`：預設的口語表與讀音項下，選字等於 CLI（`top1_sha256` 相同）；`--colloquial FILE`、`--no-colloquial`、`--no-reading-decode` 的標記與行為。
- `learn_file_changes_the_choice_and_the_source_is_untouched`、`a_corrupt_learn_file_is_an_error`、`an_output_that_is_the_learn_file_is_refused`（含硬連結）：`--learn-file` 的各條規則。
- `learn_guard_counts_and_switches`（另有 `core/tests/engine_rerank.rs`）：預設計入 `guard_skips`／`guard_rows`，`--learn-guard off` 為 0 並加 `+noguard`，預設與明寫 `on` 逐位元組相同。
- `sinit_flags_tag_the_summary_and_the_counters_go_to_stderr`、`sinit_stretches_file_is_numbers_only_and_changes_no_other_output`：`--rerank-level` 與 `--sinit-stretches` 的行為。
- `packs_option_changes_only_the_tag_it_names`：`--packs` 只改它指名的標記，非詞包名稱報錯。

### 4.10 已知限制

- 不模擬打字速度與真的非同步執行緒；`final` 是極端情況的近似。
- 預測列、英文混打、多句不在重播範圍。
- 改選模擬假設使用者知道要去哪裡改；只計成功那一條路的鍵。
- 「學習守門」「句首單字學習」等學習行為只在 `--learn on` 或載入學習檔時有意義。
- 私人集合只在私人環境跑、只帶回數字。

## 5. 保留集

### 5.1 組成

保留集是沒被拿來調參的判讀集，分兩個半邊，各自計次、各自換份：

- **口語半邊**：`eval/holdout/talk.txt`（現役，150 列），公開語料的口語書寫，逐列出處在 `eval/holdout/next/sources.tsv`。`shanjie-eval --set holdout` 只讀這個檔，用聊天設定量。現役是第二份（shard-2）；之後的一份在 `eval/holdout/next/shard-3/talk.txt`（`--set holdout` 不讀子資料夾，換上之前不會被量到），用完的 shard-1 在 `eval/holdout/retired/shard-1/talk.txt`（照樣不讀）。三份互斥。
- **新聞半邊**：repo 外的私人檔（150 列一份，三份），用 `shanjie-eval --lm … --rows <檔> --profile formal --rowstats` 量，用書面設定。檔不進 repo、不訓練、不散布；用前核對 `eval/README.md` 記的 SHA-256，量完刪掉 rowstats 與那台機器上的副本。
- 另有一份私人新聞開發集（2,000 列，repo 外）：開發層，可以調參與選臂，只報數字、不讀列的內容。
- 舊的保留集（227 列）已退役，放在 `eval/dev/holdout-2026-10-03/`，當可讀的挑戰集（`--dev N` 不讀子資料夾，開發集前 302 列不變）。
- 各檔的雜湊與使用紀錄在 `eval/README.md`；雜湊變動就代表被改過。

### 5.2 使用次數與換份

- 一次「使用」：現役保留集的任何一個數字回到實作者或維護流程手上。回填凍結的 tag、量份與份之間的雜訊底線不算。每個半邊各自計次，使用紀錄寫在 `eval/README.md` 的表。
- 換下一份的條件，符合任一個就換：使用滿 8 次；或任何改門檻、改值的動作是因它的數字而做。換份前後的數字不跨份比（表依 `rows_sha256` 分欄）。
- 三份都用完後，把時間窗往後延，用同樣的規則重抽。
- 保留集只在推 `v*` tag 前量；其他時候不讀、不調參。

### 5.3 量法

- 量的人是沒參與實作的 fresh 審查者。口語半邊：`--set holdout --profile chat --rowstats <自己的暫存檔>`。新聞半邊：`--rows <私人檔> --profile formal --rowstats <暫存檔>`。基準與新版各量一次，檔內只許 `[0-9\t\n]`，再 `tools/evalstats.py compare BASE CAND --net`，只回那一行，然後刪掉 rowstats。
- `--rows` 不像 `--set holdout` 會拒絕逐列輸出，所以量新聞半邊的審查者，指示裡明寫禁止 `--dump`、`--dump-words`、`--picks`、`--tinylm-scores`，只准 `--rowstats`（流程規則，不是技術防護）。
- 結果填進基準結果 JSON 的 `holdout.chat`（口語半邊）與 `holdout.formal`（新聞半邊），以及 KPI JSON 的 `holdout_talk_chat` 與 `holdout_news_formal`：`n`、`top1`、`fixed`、`broken`、`p`，另加那個檔案的 `rows_sha256`（口語半邊是 `talk.txt` 的 SHA-256、新聞半邊是那個私人檔的 SHA-256）。`rows_sha256` 一定要填：兩個表依它分欄，沒填的數字在 `tools/bench.py` 只能依 n 分欄、在 `tools/kpi.py` 全放進同一欄。
- `tools/bench.py` 與 `tools/kpi.py` 都不讀 `eval/holdout/` 底下的任何檔；`tools/kpi.py run` 拒絕位在 `eval/holdout/` 底下的列檔。
- 基準結果 JSON 已有 `holdout` 欄位時，`tools/bench.py run --suite v2` 拒絕重跑同一個標籤（保留集的數字量一次就不重來）。

### 5.4 防洩漏比對：`tools/holdout_guard.py` 與計數程式的 `--holdout-guard`

目的：保留集、調參集與公開評測集的句子，不能被下一次建模型時吸進訓練文字。

- **比對的單位**是計數程式看到的樣子。計數是先轉換（`build_counts.convert`：簡轉繁、台灣字形、用詞），再取漢字段（`build_counts.HAN`）才計數，所以比對也在這一層做：轉換之後的每一段漢字，只要**包含**任何一個被保護的句子（整句出現在這段裡），就算命中。包含比對讓外面包了引號、半形標點、空白的句子也命中。
- **比對對象（patterns）**：每個句子照原樣與轉換後（`mixed` 輸入）各一份，再用建模型時的變體合併（`MERGE`：臺／台、佔／占、佈／布等）折疊，因為模型把這些變體當成同一個詞計數。`--text` 與 `--row-files` 的文字同樣以原樣（`native`）與轉換後（`mixed`）兩種形式各搜一次；`--wiki` 的條目是 `mixed` 輸入。最短 4 字（`clean.MIN_LEN`），較短的不參與比對，只計數。比對器是 `tools/lib/clean.py` 的 `Matcher`（以前兩字建索引，每個位置查一次）。
- **條目與文字的處理**（`TEMPLATE`、`MARKUP`、`SENT`、轉換、漢字段；`.bz2`、`.tsv` 的讀法）只有一份，在 `tools/lib/build_counts.py` 與 `tools/lib/build_counts_text.py`；計數、`report` 都呼叫同一份，不另抄。
- `python3 tools/holdout_guard.py report [--rows FILE...] [--text FILE...] [--row-files FILE...] [--wiki DUMP --articles N [--skip M] [--procs P]]`：
  - `--rows`：比對對象（池或保留集），每行恰好三欄 `前文|句子|讀音`，句子欄必須全是漢字（`build_counts.HAN` 完全匹配）、行內不得有換行類字元（`\r`、U+2028、U+2029、U+0085）；空行與 `#` 行略過；不給時預設是 `eval/holdout/` 底下遞迴的所有 `.txt`（含 `next/`）。零個比對對象是錯誤。
  - `--text`：純文字，逐行轉換後取漢字段（`.bz2` 解壓、`.tsv` 取最後一欄），串流讀、壞位元組以 U+FFFD 取代。
  - `--row-files`：來源是列檔（兩欄或三欄都收，句子是第二欄），例如公開評測集。
  - `--wiki`：照 `build_counts.py` 的條目處理，一趟掃 dump，用 `--procs`（預設 CPU 數減 2）個程序；`--articles` 必填，`--skip M` 丟掉前 M 篇。
  - 行號只數 `\n`；BOM 只在第一行去掉。
  - 輸出（stdout）：`patterns<TAB>載入數<TAB>略過數`，每個來源一行 `來源<TAB>單位數<TAB>命中單位數`（單位：`--text` 的一行、`--row-files` 的一個句子、`--wiki` 的一篇；`--wiki` 的來源名固定是 `wiki`），再來每個命中的比對對象一行 `rows<TAB>路徑:行號`（依路徑、行號排序）。**不輸出任何句子或文字**。錯誤只寫路徑、行號與欄數（結束碼 2）。
- **計數程式的開關** `--holdout-guard [ROWS…]`：`tools/lib/build_counts.py`、`tools/lib/build_counts_text.py` 預設關（關著時輸出逐位元組不變）；開著時命中的漢字段不計數，並印「跳過幾段」（只有數字）。不給檔時用 `eval/holdout/` 底下全部 `.txt`；列檔有問題時在載入任何東西之前停下，訊息只有路徑、行號與欄數。`tools/build_digits.py` 一律開著（`--holdout-rows`，不給等於預設路徑），命中的漢字段不計數、並當作邊界。
- 每個計數的來源，建模型時要把被保護的集合明列在指令裡，不靠預設。

### 5.5 合併報告 `tools/merge_guard_rows.py`

`merge_guard_rows.py --clean ROWS_IN ROWS_OUT [--clean …] REPORT…`：把數份 `holdout_guard.py report` 的 stdout 取 `rows` 行的聯集，從對應的 `ROWS_IN` 拿掉那些行，其餘依原順序與原位元組寫到 `ROWS_OUT`。印每個檔的「讀入行數、拿掉行數、留下行數、輸出的 SHA-256」，從不印列的內容。

- 每個 `ROWS_IN` 必須和報告當初 `--rows` 的寫法完全相同；行號只數 `\n`。
- 這些情況停下、什麼都不寫：報告沒有 `patterns` 行或沒有任何來源行（表示比對沒跑完）、各報告的 `patterns` 行不同（不是同一批列檔）、報告指到不是 `--clean` 輸入的檔、報告指到檔外的行、輸出檔已存在（不覆寫）、同一個輸入或輸出給了兩次。

### 5.6 驗證

- `python3 -W ignore -m unittest tools/test_holdout_guard.py tools/test_merge_guard_rows.py`（`make test-python` 與 CI 的 Python 步驟都跑，合成資料加假的轉換表）：
  - 句子在較長漢字段中間也命中；外面包了引號、半形標點、空白也命中；原文是簡體或異體、轉換後才相同也命中；兩邊都套合併折疊；
  - 太短的比對對象被略過並計數；零個比對對象是錯誤；
  - `--rows` 的解析（空行、`#` 行、BOM 只在第一行、`\r\n`、欄數不是三就報錯、非漢字句子報錯、換行類字元報錯）；`--row-files` 兩欄與三欄都收；
  - 錯誤訊息不帶文字（含標記字串的壞列讓工具停下，stdout 與 stderr 都不出現該標記）；
  - `.bz2`、`.tsv` 與非法 UTF-8 的輸入不崩潰；`report` 只輸出列號與計數；
  - `--wiki` 的 `--articles` 必填、`--skip`、多程序（含 spawn 啟動方式）；
  - 計數程式關著時計數不變、開著時跳過命中的段；壞列在任何程序池建立之前停下且訊息不帶文字；`build_counts` 主程式帶程序池時也成立；
  - `merge_guard_rows`：各檔依原順序與原位元組、單獨的 `\r` 不分行、沒有 `patterns` 行或截斷的報告整批停、同一輸出給兩次拒絕、不同列檔集的報告停、未知檔與超出範圍的行不寫任何東西、不覆寫輸出、只印計數。

## 6. 調參集

### 6.1 cvtune-clean-tw0 與 wikitune-clean-tw0

- 兩份調參集（口語那份抽自公開的語音語料句子、書面那份抽自維基百科條目）的正解，繁體句**不套**OpenCC `TWPhrases`（「權限」維持「權限」，不轉成「許可權」）；簡體來源句照舊轉成台灣用詞。舊版（正解套了 `TWPhrases`）留著只拿來對照過去的數字，兩版的列不一一對應，數字不跨版比。
- 兩份集合再對會意模型的七個訓練文字來源去重（`tools/holdout_guard.py report` 比對，工具不需要知道輸入種類；報告用 5.5 的工具合併後拿掉命中的列）。
- 集合不進 repo；列數與 SHA-256 記在 `eval/README.md`。它們是調參層，不是判讀集：抽自和舊調參集同一段來源，大量重疊。
- 用途：cvtune-clean-tw0 是聊天非劣性的量尺（Δtop1 的 90% 配對 bootstrap 下界 ≥ −1.0 個百分點，用 `evalstats compare --ci 0.90`）與選值依據；wikitune-clean-tw0 是書面調參的量尺。
- 不重建的評測集：dev302、打字測驗、錯字回報是手寫的，沒有經過轉換；私人聊天集不經 OpenCC 轉換。

## 7. 跨版本基準測試 `tools/bench.py`

### 7.1 版本與集合分開

- **版本**只對應程式與詞庫：每個 ref 用 `git archive <ref> Cargo.toml Cargo.lock core cli data eval/variants.tsv` 匯出到快取資料夾（環境變數 `SHANJIE_CACHE`）的 `bench/src/<sha>/`；那個 commit 的樹裡有 `windows/` 時一併匯出（新 repo 的工作區把 `windows/*` 列為成員，cargo 就算只編 `-p cli` 也要讀到每個成員的 Cargo.toml）。不含 `eval/` 的其他部分，所以保留集不會被複製出來；不動 repo 的 worktree 清單；匯出分兩步、成功才寫標記；已有標記但缺任何一個要匯出的路徑時（例如加入 `windows/` 之前的匯出）整個重新匯出。各自 `cargo build --release`，另編按鍵重播程式。
- **集合**一律來自套件，所有版本讀同一份，一律用 `--rows`、不用 `--dev`（`--dev` 會讀該版本自己的 `eval/dev`，版本和集合就混在一起）：
  - 公開集合凍結成 `eval/bench/suite-v1/` 與 `eval/bench/suite-v2/` 裡的列檔，SHA-256 寫死在工具裡，不符就失敗退出；
  - 不進 repo 的集合（授權）讀快取資料夾裡的檔，SHA-256 同樣寫死；
  - 私有集合從 `--private-root`（預設環境變數 `SHANJIE_PRIVATE`，空字串等於沒有）讀，雜湊記在結果裡；**所有**私有路徑（集合檔、逐列輸出、上一版的逐列快取）都由 `--private-root` 推出，工具裡沒有其他寫死的私有路徑；私有集合缺檔時，該欄寫「沒有私有資料」並在 stderr 說明，不碰任何私有路徑；公開集合缺檔就失敗退出。
- 工具從不讀取或雜湊 `eval/holdout/` 底下的檔案。
- **本 repo 之前發佈的版本從舊 repo 的 clone 匯出**：本 repo 的 `v*` tag 全都指向同一個新的第一個 commit，tag 名字不能說這個版本是用哪一棵樹建的。`tools/bench.py` 的 `LEGACY_SHA` 把 17 個版本名（`v0.1.0` 到 `v0.6.1`）對到舊 repo 上那個 tag 指向的 commit（不是結果 JSON 記的「被量的 commit」：v0.6.1 被量的是 `9c88b87f`，tag 指的是 `7fdc88a1`）。`Ver(ref)` 遇到表上的版本名，一律從 `--legacy-repo PATH`（沒給就讀環境變數 `SHANJIE_LEGACY_REPO`）給的 clone 以那個 commit 匯出，匯出前先核對 `git cat-file -e <sha>^{commit}`；沒給 clone 或 clone 裡沒有那個 commit 就拒絕並說明怎麼修（例如「v0.4.1 is a legacy version: pass --legacy-repo <a clone of the legacy repository>」），**不退回用本 repo 的同名 tag**。不在表上的 ref（新版本、分支、commit）照舊從本 repo 匯出。快取以 commit 為鍵，同一版換 clone 路徑不會重建。`--legacy-repo` 是 `tools/bench.py run`（v1、v2）與 `tools/kpi.py run` 的參數。

### 7.2 套件 v1（固定用同一個 model-v1，保留給早期版本的歷史表）

- 指令：`tools/bench.py run <ref>... [--label L] [--base REF] [--lm FILE] [--private-root DIR]`。
- 用該版本自己的 CLI：`shanjie-eval --lm <bigram.sjlm> --profile <p> --rows <檔> --dump <暫存>`；模型的 SHA-256 必須等於 `data/bigram.sjlm.sha256`。
- 計分只有一套：每次呼叫 CLI 都設 `SHANJIE_VARIANTS` 為套件凍結的 `eval/bench/suite-v1/variants.tsv`（絕對路徑，SHA-256 寫死），top1 與 `oracle@64` 由工具從 dump 用同一套寬鬆對照算出；CLI 摘要行只當交叉檢查，不符就失敗。
- 集合：dev302（`eval/dev` 的前 302 列，排除 `user-typing.txt`）、typing76（`eval/dev/user-typing.txt`）、probe（讀音變體探針）、調參集 cvtune／wikitune（不進 repo）、私有的聊天調參集。dev302 與 typing76 兩種設定都量，其餘只量聊天。
- 每格指標：n、top1、oracle@64、`top1_sha256`。
- **逐列比較**：和上一列比修好、弄壞與 McNemar 雙尾精確 p；工具檢查兩版逐列結果的列數都等於集合列數、上一版逐列算出的 top1 等於它紀錄裡的 top1，不符就停。快取以（commit、集合 SHA-256、`variants.tsv`、模型）為鍵；紀錄的指紋和目前的套件不同、或快取的雜湊不符時，在目前的套件下重跑上一版，不拿舊結果硬比，並在 stderr 說明原因。
- 結果寫 `eval/bench/results/<label>.json`；逐列輸出只寫快取，不進 repo。`table` 從所有結果 JSON 寫 `site/data/bench.json` 與完整表（含私人集合；`--md FILE` 或 `$SHANJIE_TABLES/benchmark.md`，路徑在公開 repo 裡就拒絕，見 7.11）（7.11）；v1 的表在加入 v2 之後逐位元組不變。

### 7.3 套件 v2：每一版用自己出貨的設定量

- 指令：`tools/bench.py run --suite v2 <ref>... [--as VERSION] [--override-packs off] [--private-root DIR]`，結果寫 `eval/bench/results/v2-<label>.json`（標籤預設是各版名稱以 `+` 相連）。`--lm` 與 `--base` 不用於 v2。
- **設定表 `VERSIONS2`**（工具裡寫死；鍵是版本標籤，`--as` 把一個還沒打 tag 的 commit 對到表上的版本；不在表裡的標籤報錯並指向表）。每一列：
  - `release`、`flags`（CLI 旗標，照 App 的預設：`--context`、`--packs acg`、`--packs acg,it`、`--packs acg,it,beauty` 或 `--packs acg,it,meme,beauty`）、`tinylm`（是否出貨小模型）、`tag`（CLI 摘要行設定標記應有的字串，`{tl8}` 等佔位符換成該版模型的 SHA-256 前 8 碼）；
  - 選用鍵：`kn`（出貨 Kneser-Ney 側檔 `kn.sjkn`）、`tri`（出貨 `trigram.sjt3`，CLI 只在書面設定載入，需另寫 `formal_tag`）、`tri_chat`（另出貨聊天的 `trigram-chat.sjt3`，兩種設定都開 KN）、`colloquial`（出貨口語表：聊天的格帶 `--colloquial-core`，標記多 `+colloq:<8 碼>`）、`reading`（解碼器帶讀音項：每個設定標記最後都是 `+rd+rx:<該版 data/lexicon/std-exempt.tsv 的 SHA-256 前 8 碼>+rm`，聊天的口語標記後面沒有 `+rc`）。`flags` 和 `tag` 是對同一件事的兩個獨立陳述，故意不由其中一個推另一個。新版發版時在表裡加一列。
- **旗標守門**：每一格 CLI 摘要行的設定標記要等於這張表推出的字串（`lm-<profile>` ＋ tag），而且沒有表裡沒寫的標記（例如多出的 `+kn:`、非 standard 的 `+rl:<檔位>`）；不符就那一格失敗。這同時守住「旗標漏帶」與「模型資料夾裡多了側檔被自動載入」。
- **模型檔**：工具讀匯出版本的 `data/*.sha256`（`bigram.sjlm`、`classes.sjc`，以及該列要求的 `kn.sjkn`、`trigram.sjt3`、`trigram-chat.sjt3`、`tinylm.sjtl`、`colloquial.sjlm`），到快取資料夾的 `bench/models/<sha256>/<檔名>` 找；沒有時，若 checkout 自己的 `data/lm/<檔名>` 雜湊相符就複製過去（經暫存名，中斷不留下半個檔），否則失敗退出並指向 `docs/spec/models.md` §11（下載）。雜湊不符也失敗。工具從不下載。
- **模型資料夾**：為每一版組一個暫時的資料夾，只放 `bigram.sjlm`、`classes.sjc` 與該版出貨的側檔／表（符號連結），`--lm` 指向它。因為 CLI 會自動載入同資料夾的 `kn.sjkn`、`trigram*.sjt3`，沒出貨的版本不放，多出來會讓設定標記不符。
- **CLI 沒有模擬的 App 行為**（結果表的說明要寫出來，不說成「和 App 完全相同」）：本機學習（CLI 不學）；殼在每個鍵之後非同步重排、CLI 每列只排一次；殼的前文來自輸入框、CLI 用列檔的前文欄；聊天或書面由 App 依輸入框而定，這裡兩種都量。推論（沒量過）：App 在重排結果回來之前就送出的列保留 n-gram 的第一名，所以開著語意校正的版本在 App 裡的改善可能比表上小。

### 7.4 計分與集合（套件 v2）

- 逐列對錯取自 CLI 選出的那一句（`--rowstats`，`SHANJIE_VARIANTS` 設成套件的 `variants.tsv`）；開 `--tinylm` 時 dump 保留 n-gram 的順序，從 dump 取第一名會忽略語意校正，所以不用 dump 的第一名。CLI 摘要行的 `top1_sha256` 算的就是選出的那一句，直接記進結果。
- 獨立的交叉檢查：**沒有 `--tinylm` 的格**，工具另用 `--dump` 以自己的寬鬆對照重算 top1，要和 rowstats 逐列相同；有 `--tinylm` 的格靠 7.7 的錨點。每格另檢查摘要行的 n 等於集合列數、rowstats 的 top1 等於摘要行的 top1。
- 集合（凍結，SHA-256 寫死）：dev302、typing76、probe（同套件 v1 的檔）；錯字回報（`eval/dev/user-reported.txt` 的前 80 列，凍結在 `eval/bench/suite-v2/user-reported.txt`，之後新增的列不進這版套件）；功能字測試句 fw（`eval/rerank/s7e-function-words.txt`，186 列）；不進 repo 的 cvtune、wikitune、lytune（公開議事發言的口語子集）、wikitypo（維基錯字表）；私有的聊天調參集。每個集合聊天與書面兩種設定都量。
- 逐列比較：每一版對它前面那一版（同一次執行、同一批集合），修好、弄壞、McNemar 精確 p。
- 調參或選值時看過的集合（錯字回報、fw、cvtune、wikitune、lytune、wikitypo、dev302、私有聊天調參集）都不是未見資料；dev302 是挑戰集，只當參考；沒被看過的只有保留集。

### 7.5 速度與記憶體

- 按鍵重播程式 `tools/bench/replay/`：獨立的 crate，執行時產生 `Cargo.toml`，把 `core` 指到該版本匯出的原始碼，只用 v0.1.0 起就有的 API。用法 `replay <詞庫資料夾> <lm 檔> <列檔> <commits 輸出檔> [chat|formal]`（profile 預設 chat）。把每列的讀音轉成標準排列的按鍵（沒有聲調鍵的音節補 Space，每列最後按 Enter），每鍵計時，每列 Enter 送出的字串寫一行到輸出檔，stdout 印一行 JSON：`keys`、`load_ms`（`Engine::new` 加 `load_lm`）、`p95_us`、`max_us`；峰值 RSS 由父程序用 `wait4` 取。
- **重播必須真的在打字**：重播的送出字串要和「同設定的 CLI 格」的第一名逐列相同。v2 的比對格是另跑一格和重播設定完全相同的 CLI（`--profile` 同該列、不帶 `--context`、`--packs`、`--tinylm`，dev302），設定標記必須等於該版 `lm-<profile>` 加它自己的 KN、trigram 與讀音項部分；不符、有任何一列不同或重播失敗，該列的速度欄寫錯誤、工具以非 0 結束。
- 重播不載詞包、沒有前文、沒有語意校正（重播是同步打鍵，語意校正是非同步、不在按鍵路徑上）。每版兩列：聊天，與書面（重播程式的第五個參數 `formal`；有 trigram 表的版本兩列都把表載進記憶體）。舊的結果 JSON 沒有書面列時，表上顯示「—」。
- **同場量測**：各版本輪流跑 3 輪，每版取三輪 p95 的中位數、max 取最大、載入時間取中位數、RSS 取最大；記下機型、macOS 版本與開始、結束時的 load average（結果檔 `env` 的 `machine`、`macos`、`cargo`、`load_avg_start`、`load_avg_end`）。語意校正的每列耗時取 CLI 在 stderr 印的 `tinylm: rerank n=… p50=… p95=…` 行，在三輪重播都跑完之後再跑三次、取中位數，且不和其他格同時跑。
- **速度段前後的狀態**（`speed_window`）：速度量測排在準確率格之後、整輪的最後；`bench.py` 在它開始前與結束後各讀一次 1 分鐘負載與 Time Machine 是否正在備份，寫進 `env` 的四個欄位 `speed_load_before`、`speed_tm_before`、`speed_load_after`、`speed_tm_after`（四個一起寫；沒有 dev302、不量速度時都不寫）。備份狀態由 `tm_running()` 讀：只在 macOS 上執行 `tmutil status`（逾時 30 秒），輸出有 `Running = 1` 是 `true`、沒有是 `false`；不是 macOS、指令失敗或逾時是 `null`。開始前的負載會帶著前面準確率格（多核心）的殘留。這四欄只有加入之後量的結果檔才有，舊檔不回填；KPI 表用它們判斷速度算不算數（§8.2）。
- 速度只報、不設門檻；數字只在同一次執行、同一台機器裡比較才有意義。

### 7.6 保留集欄

- 結果 JSON 的 `holdout` 欄由 fresh 審查者量好後手動填入（5.3）；格式 `{"commit": …, "chat": {n, top1, fixed, broken, p, rows_sha256}, "formal": …}`，`commit` 是保留集實際量的那個 commit。
- 兩張完整表（7.11、8.1）與網站的 /benchmark、/kpi 頁的保留集欄依各自的 `rows_sha256` 分欄，欄位依第一次出現的順序排列；換份時不同份的數字不在同一欄。

### 7.7 錨點檢查

`--override-packs off` 是對歷史量測的重現檢查，只有表裡有錨點的版本（v0.4.0、v0.4.1）可用：拿掉 `--packs` 與它的值、只量聊天、不設 `SHANJIE_VARIANTS`（用 CLI 預設的對照表，和當時量的條件一致），把指定集合的 top1 對工具裡寫死的數字；每一格印 `anchor … ok／MISMATCH`，有不符就以非 0 結束，且**不寫任何結果檔**。有語意校正的格沒有 dump 可以交叉檢查（7.4），錨點是它唯一的獨立守門。

### 7.8 參考列與實打對照表

- `tools/bench.py reference [--private-root DIR]`：用目前 checkout 的 CLI 跑「小麥資料基準」列（不帶 `--lm`，加 `--no-overlay`；只用 McBopomofo 基底詞庫的 unigram 解碼）。這一列不是版本，沒有修好／弄壞。結果寫 `eval/bench/results/reference-mcbpmf.json`，記 commit 與指紋（集合、`variants.tsv`、基底詞庫）；只有 commit 或任一指紋改變、或上次有集合失敗時才重算並在 stderr 說明原因；`core/`、`cli/`、`data/` 有未提交的改動時拒絕。
- `tools/bench.py check-static [--json FILE]`：核對 `eval/bench/static/typing-test.json`（三套輸入法的實打結果，靜態）自己一致，不讀別的檔：`date` 是 `YYYY-MM-DD`；每組有名稱、列出同樣的輸入法（名稱與順序和第一組相同）；每格的 `sentences`、`chars` 是非負整數，`chars` 不小於 `sentences`，有 `n` 時 `sentences` 不大於 `n`；每組的 `sum`（各輸入法的 `sentences` 合計與 `chars` 合計，抄入時算好）等於現在各格相加，所以改任何一格都會失敗。`local_only: true` 的組（資料只放本機、只記統計）只在完整表裡，不進網站資料。實打對照表和版本列的條件不同，不做統計比較。

### 7.9 輸出與驗證

- 可重現：同一版跑兩次，結果 JSON 的準確率欄與 `top1_sha256` 完全相同；`table` 兩次逐位元組相同。
- `python3 -m unittest tools/test_bench.py`（`make test-python`、CI 的 Python 步驟）用假的 CLI，不建置、不碰私有資料：
  - 靜態表檢查（正確的 fixture 通過、改任一格失敗、缺欄位、欄位不是整數、輸入法名稱不一致、合計不符都失敗）；
  - 舊版本的匯出（假的新 repo 有一個指向新 commit 的 `v0.4.1` tag、假的舊 clone 有 `LEGACY_SHA` 指的 commit：表上的版本解出 `LEGACY_SHA`、不是本 repo tag 的 sha；沒給 clone 或 clone 沒有那個 commit 時拒絕；不在表上的 ref 從本 repo 解；`bench run` 與 `kpi run` 都測）；
  - `table` 的網站資料（兩次輸出逐位元組相同、沒有私人集合的鍵、完整表與改動前的內容相同）；完整表的位置（沒給路徑時只寫 JSON、`SHANJIE_TABLES` 生效、公開 repo 裡的路徑在寫任何檔之前就以 1 結束）；
  - 參考列（指令不帶 `--lm` 且帶 `--no-overlay`、私有路徑不被碰、失敗的集合會重跑並說明原因、同 commit 不重算、commit 或指紋變了就重算、參考列沒有修好／弄壞）；
  - v1 的表與保留集表不變；
  - `SuiteV2`：設定表的旗標與標記、集合表、`--as` 命名最後一個 ref、模型資料夾的內容、KN／trigram／口語表／讀音項各種版本的標記與模型檔、模型缺檔或雜湊不符失敗、由 rowstats 計分、無小模型格的 dump 交叉檢查、設定標記不符或摘要行不是一行或 rowstats 壞掉都使該格失敗、重播一致性與不一致時失敗、書面重播格、重播程式設定的比對格、外部對照節、`table` 把 v2 接在原封不動的 v1 文字後面、保留集數字留在各自的欄、重跑不覆寫保留集數字。
  - `SpeedWindow`：`speed_window` 的順序是讀、量、讀，四個欄位是前後各一次讀到的值，沒有 dev302 時不量也不讀；`tm_running` 讀 `tmutil status`（測試換掉 `subprocess.run`，不執行真的 `tmutil`）：`Running = 1` 與否、指令失敗、逾時、不是 macOS 時不呼叫。
- CI 的 Python 步驟另外跑 `tools/test_kpi.py`、`tools/test_evalstats.py`、`tools/test_holdout_guard.py`、`tools/test_merge_guard_rows.py`。

### 7.10 發版前的核對

- 推 `v*` tag 前，被量的 commit 和要打 tag 的 commit 在 `Cargo.toml Cargo.lock core cli data eval/variants.tsv macos scripts` 上必須沒有差異（`macos` 有殼的預設值，`scripts` 有 `build-app.sh` 包進去的模型），差異清單寫在發版 PR 的說明。有差異且都在不改輸出的地方（重構、測試、註解、不出貨的研究工具）時，用兩個 commit 的 CLI 在出貨設定下跑 dev302、打字測驗、錯字回報兩種設定，`--rowstats` 逐位元組相同才沿用保留集的數字；差異碰到 `shanjie-replay` 時，KPI 的重播也要在打 tag 的 commit 上重跑或證明行為相同。
- 結果 JSON 與 `site/data/*.json` 隨發版前的 PR 進 repo；完整表不進公開 repo。步驟與時機見 `docs/spec/release-and-ci.md`。

### 7.11 網站資料 `site/data/bench.json`

- `tools/bench.py table [--md FILE]` 一次讀入所有結果檔，寫 `site/data/bench.json` 與完整表：兩個檔從同一份資料產生，工具不另外讀第二次。完整表含私人集合的欄，寫到 `--md FILE`，沒給時寫 `$SHANJIE_TABLES/benchmark.md`，兩者都沒有時不寫（stderr 說明）；路徑解析後在公開 repo 裡就在寫任何檔之前以 1 結束。`json.dumps(…, ensure_ascii=False, indent=1, sort_keys=True)` 加結尾換行，同樣的輸入兩次輸出逐位元組相同。
- **只放公開集合**：dev302、typing76（打字測驗）、probe、fw、錯字回報（`user-reported`）兩種設定的格，保留集口語半邊（`holdout_chat`，不寫新聞半邊），速度與記憶體，實打對照（`local_only` 以外的組），外部模型對照（`eval/bench/external/*.json`）裡這幾個公開集合的列。名單寫在 `tools/bench.py` 的 `PUBLIC_SETS`、`PUBLIC_EXTERNAL_SETS`，用名稱挑，所以之後新增的集合在列進名單之前不會出現；私人集合與調參集的鍵一個都不寫。失敗的格寫 `{"failed": true}`，沒量的格寫 `{"missing": true}`，都不帶錯誤文字。`holdout_chat` 取結果檔 `holdout.chat` 的那一塊：2026-10-29 以前量的列（沒有 `rows_sha256`，n 227 或 300）是當時的保留集，不是現在的口語半邊；網站要分開呈現時看 `rows_sha256`。
- 頂層鍵：
  - `suite_v1`：陣列，每個結果檔的每一版一列（`results/*.json` 排序後的順序）。
  - `suite_v2`：陣列，每個 `results/v2-*.json` 的每一版一列。
  - `reference`：小麥資料基準列，`commit` 與公開集合的 `accuracy`。
  - `static`：`date` 與 `groups`（每組 `name`、`n`、`ime`：輸入法 → `sentences`、`chars`）。
  - `external`：每個外部模型一項，`name`、`date`、`label`、`ours_label` 與 `rows`（`setting`、`set`、`n`、`ngram`、`ours`、`external`、`fixed`、`broken`、`p`）。
- 版本列（`suite_v1`、`suite_v2` 同形）：`date`、`label`（結果檔標籤）、`ref`、`sha`、`version`（只有 v2）、`machine`、`macos`、`load_avg_start`、`load_avg_end`（速度段前後的四個欄位不放進這份資料，§7.5）；`accuracy`（格名 `集合/設定` → `n`、`top1`、`oracle64`）；`vs`、`vs_prev`（格名 → `fixed`、`broken`、`p`，有上一版時）；`holdout_chat`（`commit`、`n`、`top1`、有的話 `oracle64`、`rows_sha256`，量過時再加 `fixed`、`broken`、`p`）；`speed`（`p95_us`、`max_us`、`load_ms`、`rss_mb`、`keys`、`rounds`、`rows_match`，有語意校正時加 `rerank_p50_ms`、`rerank_p95_ms`，v2 另有書面那一列 `formal`）；`models`（v2，檔名 → SHA-256）。
- 網站的 /benchmark 頁（`site/benchmark.html`）由 `site/numbers.js` 讀這個檔畫表；每格的寫法和完整表逐字相同（數字照 Python 的 round-half-even 捨入；失敗的格網站只寫「失敗」，不帶錯誤訊息，核對時會被列成不同），發版時用核對腳本（研究 repo）比對兩頁與完整表。

### 7.12 發版時的指令順序

| 指令 | 什麼時候 | 內容 |
|---|---|---|
| `tools/bench.py run --suite v2 <上一版 tag> <要打 tag 的 commit> --as <新版> --private-root <私人資料夾> --legacy-repo <legacy repo 的 clone>`（上一版是舊版本時要給，7.1），再 `tools/bench.py table` | 推 `v*` tag 之前 | 套件 v2（7.3）：每一版用自己出貨的模型、詞類表、小模型與旗標量準確率與速度，結果寫 `eval/bench/results/v2-<label>.json`。新版要先在 `VERSIONS2` 加一列（Release 名、出貨旗標、摘要行設定標記，照 App 的預設與 `scripts/build-app.sh` 包進去的模型；設定標記先用 CLI 在小集合實跑一次、照摘要行抄）。要打 tag 的 commit 定下來之後，由沒參與實作的審查者量保留集（上一版對新版的 `evalstats compare` 一行，5.3），填進同一個 JSON；只改綁定路徑以外檔案的後續 PR，條件見 7.10 |
| `tools/kpi.py run <要打 tag 的 commit> --as <新版> --wikitune-clean <列檔>`，在那個 commit 的乾淨 checkout 上 `tools/kpi.py replay --as <新版>`，再 `tools/kpi.py table` | 套件 v2 跑完之後、推 tag 之前 | 每版 KPI（第 8 節）；三者的結果和基準測試結果同一個 PR 進 repo |
| `tools/bench.py reference --private-root <私人資料夾>` | commit 或指紋改變時 | 「小麥資料基準」列（7.8） |
| `tools/bench.py check-static` | 改動 `eval/bench/static/typing-test.json` 時 | 7.8 |
| `tools/bench.py run <commit> --private-root <私人資料夾>` | 不再用於發版 | 套件 v1（7.2），保留給早期版本的歷史表 |

- 兩個 `table` 都設 `SHANJIE_TABLES` 指到公開 repo 以外的資料夾，完整表寫在那裡；寫完用核對腳本確認網站的 /benchmark、/kpi 頁和完整表逐格相同（7.11）。
- 兩個保留集半邊在同一次發版前都量，各加一次「使用」（5.2）。`rows_sha256` 一定要填（5.3）。

## 8. 每版 KPI `tools/kpi.py`（研究工具，不出貨）

### 8.1 子命令

- `run REF... [--as VERSION] --wikitune-clean FILE`：參數同 `tools/bench.py run --suite v2`。匯出、編譯與模型設定直接用 `bench.py` 的 `Ver`、`build`、`setup_models`；wikitune-clean 這一格用 `cell_cmd`（出貨旗標、書面設定）加 `--threads <這個程序能用的核心數>`（CLI 的多執行緒輸出和單執行緒逐位元組相同；還不認 `--threads` 的舊 CLI 不加）、`cli_env(False)`，做和 v2 格相同的檢查（設定標記等於設定表、列數、rowstats 的 top1 等於摘要行）。`--legacy-repo PATH`（或環境變數 `SHANJIE_LEGACY_REPO`）與 `bench.py run` 相同：表上的舊版本從舊 repo 的 clone 匯出（7.1）。寫 `eval/bench/kpi/<版本>.json` 的 `version`、`sha`（量的 commit）與 `wikitune_clean`：`n`、`top1`、`top1_sha256`、`tag`、`rows_sha256`。檔案已有的欄位保留（原子寫入），只更新這次跑的。列檔路徑轉成絕對路徑，位在 `eval/holdout/` 底下的拒絕。會編譯舊版，不在 Mac 上跑。
- `replay --as VERSION`：在**目前的 checkout** 跑（發版時就是要打 tag 的那個 commit）。前置檢查：
  - `--as` 要在 `VERSIONS2` 裡、套件的 `variants.tsv` 雜湊相符；不能是 `LEGACY_SHA` 表上的舊版本（本 repo 的同名 tag 不是那個 commit，也沒有舊版的引擎可重播），要重播舊版就在舊 repo 的 checkout 上跑；
  - 已追蹤的檔有改動就拒絕（未追蹤的結果檔不算）；`VERSION` 的 tag 已存在時它指的 commit 必須就是 HEAD；
  - `data/lm/` 的模型必須恰好是 `VERSIONS2[VERSION]` 出貨的那一組（`bigram.sjlm`、`classes.sjc`，以及該列要求的 `kn.sjkn`、`trigram.sjt3`、`trigram-chat.sjt3`、`tinylm.sjtl`、`colloquial.sjlm`）、每個都和 `data/*.sha256` 相符、`data/lm/` 不能多出不在這組裡的模型檔；
  - 兩份列檔（`eval/bench/suite-v2/user-reported.txt`、`eval/bench/suite-v1/dev302.txt`）的 SHA-256 相符。
  - 然後 `cargo build --release --locked -p cli --bin shanjie-replay --message-format=json`（執行檔路徑取編譯器回報的），跑：錯字回報（聊天、`--correct`，學習關與開各一次）與 dev302（聊天、`--correct`，學習關）。版本不出貨小模型時加 `--no-tinylm`，不出貨口語表時加 `--no-colloquial`。每次跑都核對：stderr 的 `models:` 行等於由 `data/*.sha256` 推出的那一行、摘要行恰好一行、設定標記等於預期（含詞包部分，依該列的 `--packs`；有 `reading` 鍵的版本以 `+rd+rx:<8 碼>+rm` 結尾）、列數等於集合列數、rowstats 的 top1 等於摘要行。
  - 寫入同一個 JSON 的 `replay` 欄：`sha`、`models`、`variants_sha256`，以及 `user-reported` 與 `dev302` 各自的 `rows_sha256`、`n`、`off`（學習關的摘要行數字）；`user-reported` 另有 `on` 與 `learn_fixed`／`learn_broken`（學習開對學習關，由兩份 rowstats 算）。
  - 含 `shanjie-replay` 之前的版本不回填（舊引擎沒有這個工具，用新工具重播舊模型不等於舊版的行為）。
- `table [--results DIR] [--kpi DIR] [--out FILE] [--json FILE]`：讀 `eval/bench/results/v2-*.json` 與 `eval/bench/kpi/*.json`，寫 `site/data/kpi.json`（8.4）與完整表（不手改；兩次輸出逐位元組相同；`--out FILE` 或 `$SHANJIE_TABLES/kpi.md`，規則同 7.11）。

### 8.2 欄位（完整表每個發版一列，最新在上）

| 欄 | 來源 |
|---|---|
| 私有聊天調參集的聊天 top1 | 該版當「新版」的那個 `v2-*.json`（只出現在較舊一側的版本用它唯一的檔）的 `accuracy` |
| error ratio（聊天） | 同一個檔的 `vs_prev`：改壞 ÷ 改對（對上一版）；改對為 0 或沒有上一版時寫「—」 |
| 錯字回報重播 top1（學習關）、學習開改壞 | `kpi/<版本>.json` 的 `replay.user-reported` |
| 按鍵分數 KySS | `replay.dev302.off.kyss`（dev302 聊天）與 `replay.user-reported.off.kyss`（錯字回報聊天） |
| 顯示後被改掉 | `replay.dev302.off.rewrites` ÷ 列數，寫成每百列次數 |
| 保留集口語半邊（聊天）、新聞半邊（書面） | `kpi/<版本>.json` 的 `holdout_talk_chat`、`holdout_news_formal`（5.3） |
| wikitune-clean 書面 top1 | `kpi/<版本>.json` 的 `wikitune_clean`（列檔不限，由 `rows_sha256` 區分） |
| 議事口語子集（lytune）書面 top1 | 同私有聊天調參集那個 `v2-*.json` 的 `accuracy`，只當警示，不進任何判讀 |
| 速度與記憶體 | 同一個檔的 `speed`（按鍵 p95、重排 p95、RSS）；機器閒置時量的才填數字，判定照表後的「速度算不算數」；不同機器（`env.machine`）分欄 |

- 表頭寫明每一欄的集合大小與雜湊（v2 JSON 的 `sets`，或 `kpi/<版本>.json` 記的 `rows_sha256`，以 `#` 加前 8 碼表示）；換集合時欄位分開，不同集合的數字不放在同一欄。
- **速度算不算數**（`speed_verdict`）有兩個規則，看結果檔記了什麼：
  - **速度段規則**：結果檔的 `env` 有 `speed_load_after`（§7.5）時用。`speed_tm_before`、`speed_tm_after` 缺的當成 `null`。`speed_load_before` 只記錄、不判。

    | `speed_tm_before`／`speed_tm_after` | `speed_load_after` | 速度欄 |
    |---|---|---|
    | 任一個是 `true` | 不論 | `—（備份中）` |
    | 都不是 `true` | > 5.0 | `—（速度段後負載 x.xx）` |
    | 都是 `false` | ≤ 5.0 | 數字 |
    | 都不是 `true`、至少一個 `null` | ≤ 5.0 | 數字，後面接 `（備份狀態未知）` |

    備份中的理由優先於負載。5.0（`SPEED_LOAD_AFTER_MAX`）是暫定值：取在 v0.7.0 三輪量測裡沒受干擾的兩輪（量完時 2.77、4.25：前者在 `c3ffeff2` 的結果檔、後者在現行的結果檔）與受干擾的一輪（5.30）之間，只有三輪；受干擾那一輪的結果沒有留在 repo，5.30 與下面的「約四成」取自當時本機的量測紀錄；累積三個帶這些欄位的版本後重訂，並決定 `speed_load_before` 要不要列入。
  - **開跑規則**：沒有 `speed_load_after` 的結果檔（v0.7.0 與更早）用。開跑時的 `load_avg_start` ≤ 2.0（`LOAD_MAX`）才填數字，否則寫「—（負載 x.xx）」，x.xx 是開跑時的負載。它看不到開跑之後才出現的干擾（v0.7.0 有一輪開跑約兩分鐘後開始備份，兩個版本都慢了約四成，照這個規則仍算閒置），所以新的結果檔改用速度段規則；舊的列不重判。
- 沒有這個數字（舊版沒有重播工具、保留集不回填、速度不算數）寫「—」，不報錯。
- 表下方列出每一列的來源檔名。
- 保留集兩個半邊不由 `kpi.py` 量（它從不讀 `eval/holdout/`），由 5.3 的流程填入 JSON。

### 8.3 驗證

`python3 -W ignore -m unittest tools/test_kpi.py`（`make test-python`、CI）用合成的小 JSON 與假的執行檔：

- 同一版出現在兩個 `v2-*.json` 時取它當新版的那個檔；error ratio 的算法與「—」；速度在負載高時不填、依機器分欄；速度段規則的每一列（備份中在前或在後、量完時的負載超過門檻、備份狀態未知、缺備份欄位、開始前的負載不判）與「沒有 `speed_load_after` 就照開跑規則」；舊版沒有重播欄位時寫「—」；
- 換集合時表頭分欄（v2 JSON 的 `sets` 雜湊不同、兩個 `kpi` JSON 的 `rows_sha256` 不同，兩種都測）；保留集兩個半邊從合成 JSON 畫進表、雜湊相同而 n 相同的兩份仍分欄、新聞半邊換檔時口語半邊的欄不動；`table` 兩次輸出逐位元組相同；網站資料（`site/data/kpi.json`）兩次逐位元組相同、沒有私人集合的欄位、數字和表一致；
- `run`：每個可用核心都解碼（`--threads`）、舊 CLI 不加 `--threads`、記錄欄位、`run_cell2` 的各項檢查、相對路徑轉絕對、`eval/holdout/` 底下的列檔被拒絕、未知版本名被拒絕、寫入保留這次沒設的欄位、表上的舊版本從舊 clone 的 `LEGACY_SHA` commit 匯出（沒給 clone 或 clone 沒有那個 commit 時拒絕）；
- `replay`：模型雜湊不符、多一個檔、少一個檔或缺雜湊檔時失敗（並說清楚缺哪個）、口語表與聊天 trigram 表只對出貨它們的版本算模型、舊版本名被拒絕、乾淨樹檢查（已追蹤的改動擋下、未追蹤的結果檔不擋、已存在的 tag 必須是 HEAD）、執行檔路徑取自編譯器訊息、各種設定標記（口語表、詞包、讀音項）不符時失敗、舊標記被拒絕、`replay` 欄的內容等於直接執行 `shanjie-replay` 與 `evalstats` 的結果、既有欄位保留、寫入是原子的。

### 8.4 網站資料 `site/data/kpi.json`

- `tools/kpi.py table` 用同一次讀入寫完整表與 `site/data/kpi.json`（兩個檔同一份資料；排序、縮排、結尾換行同 7.11，兩次輸出逐位元組相同）。
- 頂層只有 `versions`：陣列，和表同序（最新在上），每版一列：`version`；`replay`（`user-reported`、`dev302` 各自的 `rows_sha256`、`n`、`off`：`n`、`top1`、`kyss`、`rewrites`、`picks_per100`；`user-reported` 另有 `on` 與 `learn_fixed`、`learn_broken`）；`holdout_talk_chat`（`n`、`top1`、`rows_sha256`，有量過時加 `fixed`、`broken`、`p`）；`speed`：開跑規則的列是 `machine`、`load_avg_start`、`idle`；速度段規則的列是 `machine`、`rule`（固定是 `"window"`；沒有這個鍵就是開跑規則）、`idle`、`speed_load_before`、`speed_load_after`、`speed_tm_before`、`speed_tm_after`，沒有 `load_avg_start`。兩種都是 `idle` 為真時才有 `p95_us`、`rerank_p95_ms`、`rss_mb`；`idle` 就是 §8.2 的判定，頁面照同一張表產生和完整表相同的文字。沒有的欄位不寫。
- 不寫：私有聊天調參集的聊天 top1 與 error ratio、wikitune-clean、lytune、保留集新聞半邊。

## 9. 已知限制

- 評測 CLI 每列只排一次語意校正、不學習、前文來自列檔，和 App 的逐鍵非同步行為不同；`shanjie-replay` 量逐鍵行為，但仍不模擬真實執行緒與打字速度，預測列與英文混打也關著。
- McNemar 與 bootstrap 假設列獨立，同一則訊息的子句彼此相關時區間偏窄；`--cluster` 只改 bootstrap，不改 McNemar。
- 保留集的 150 列在聊天與書面各一份，淨值要到約 6 比 0 或 9 比 1 才達顯著，只抓得到明顯的退步；份與份之間的雜訊可到約 4%。
- 保留集與私人新聞檔的「不洩漏」靠流程（指示、旗標禁令、`rows_sha256`），`--rows` 路徑沒有技術防護。
- `tools/kpi.py replay` 用目前 checkout 的 `shanjie-replay`，它的設定標記一律以 `+rd+rx:<8 碼>+rm` 結尾；`VERSIONS2` 裡沒有 `reading` 鍵的版本（讀音項進核心之前的版本）因此無法用現行重播程式回填。
- 一致性檢查只比 `top1_sha256`（與必要時的 `--firsts`）；引擎和 CLI 真的不同的列要人工歸因。
- forms 表只收辭典明載的寫法關係；人名、有分工的字、「通」字關係不收。
- 寬鬆對照與 forms 都只用於評測，來源授權不允許改作散布。

## 10. 設計取捨

- rowstats 只放數字：保留集與私人集合的逐列統計可以交給別人比較，而不洩漏任何一句。
- 錯字數對寬鬆對照算對的列歸 0：「她／他」這類寫法差異不該被算成錯字，CER 才和 top1 方向一致。
- 比較用配對檢定與配對 bootstrap：兩邊同一批列，配對才有檢定力；精確 McNemar 在改對加改壞很少時比近似公式可靠。
- 單字對帶讀音條件、整組詞對要共同讀音：沒有讀音條件，「沈」會把姓氏讀音的字也接成「沉」，「沒的」會經鏈接到不相干的詞。
- forms 表獨立於寬鬆對照、只在加旗標時才讀：現行分數的每個位元組因此不依賴它，舊結論不會被倒改。
- 重播只讀原始位元組載入學習檔、載入後立刻刪暫存目錄：學習檔是私人資料，重播不得改它，也不留複本。
- 重播輸出檔晚於讀列建立、拒絕和列檔同一個檔：輸出同名會在讀列之前把列檔清空。
- 防洩漏比對在轉換之後的漢字段上做包含比對：計數程式看到的就是這個形式，句子的雜湊或整句相等會被標點、異體與簡轉繁漏掉。
- 比對報告只輸出列號與計數：比對可在私人環境之外的機器上跑，過程中沒有人需要看到句子。
- 套件 v2 讓 `flags` 和 `tag` 各寫一次：兩者互為守門，少帶旗標或多載入側檔都會讓設定標記不符而失敗。
- 速度重播不載詞包與語意校正、並要求與同設定 CLI 逐列一致：重播確實在打字且只量按鍵路徑，不是量到別的東西。
- 保留集只在推 tag 前量一次、每個半邊獨立計次：用得越多就越接近間接調參，所以用滿 8 次就換份。
- 新聞半邊放 repo 外：該集合的文字不得散布，repo 只留雜湊與列數。

## 11. 方法

前面各節寫工具怎麼算；這一節寫怎麼用它們判斷一個改動要不要留下。

### 11.1 問題與指標

注音輸入時，使用者打的是讀音，輸入法要從同音的字詞裡選出想要的那一串。指標：

- **整句正確率（top-1）**：輸入法第一個給出的整句和正解相同的比例，以子句為單位，用寬鬆對照（1.3）。寬鬆對照的標準定在辭典本身，不從評測句挑；改了標準，之前的數字是舊規則，要重量。
- **合法寫法分數（forms）**：另報、和 top-1 並列、不取代它（第 3 節）。出貨標準與已登記的實驗只用 top-1，forms 只報不判。
- **候選上限（oracle@64）**：正解出現在前 64 名候選裡的比例。判斷器只能從候選裡挑，所以這是上限。
- **對數損失**：前 64 名候選的分數取 softmax 後，正解機率的負對數；正解不在候選裡記為上限 10。它比對錯計數更連續，適合調參數。
- **字元錯誤率（CER）**：2.1、2.3。補充指標，不取代整句正確率。
- **按鍵分數**：4.6，量使用者要按幾個鍵才能打對。
- **延遲與大小**：每打一個音節重新產生候選的 p95（要求 < 16 ms）、詞庫與模型的記憶體（7.5）。

錯誤分兩種，對應不同的修法：

- 正解根本不在候選裡：詞庫或搜尋的問題。
- 在候選裡但排錯：語言模型或語意校正的問題。

### 11.2 評測集的角色

| 角色 | 集合 | 可以做什麼 |
|---|---|---|
| 主要判讀集 | 私人的聊天與書面集合，不公開 | 決定下一步改什麼、改動要不要留；只看統計數字，不讀列的內容 |
| 調參集 | cvtune-clean-tw0（口語）、wikitune-clean-tw0（書面）、私人新聞開發集（6.1、5.1） | 選值、選臂；聊天側用非劣性檢查，書面側只當量尺；可以看錯句 |
| 回歸層 | 打字測驗（typing76）、dev302 的書面設定、錯字回報 | 不當調參量尺；用 11.4 的標準守住不退步 |
| 挑戰集 | dev302 的聊天設定、舊保留集 `eval/dev/holdout-2026-10-03/` | 按類別看，不當門檻；句子少，而且多是專案或 LLM 寫的，和真實聊天的分布不同 |
| 探針集 | 讀音變體探針（見下） | 檢查另一種合規讀音下是否不退步 |
| 保留集 | `eval/holdout/` 的口語半邊與私人新聞半邊（第 5 節） | 只由沒參與實作的審查者在推 tag 前量；開發者不讀內容 |

- 調參集必須**不參與任何計數**：用固定的雜湊切分，或取計數範圍以外的資料。
- **挑戰集的結果不能當結論**：曾有一個判斷器在 dev302 上顯著進步，在私人聊天集合上卻大幅退步。任何改動在挑戰集上的好結果，只能當作「值得在調參集上量」的線索。
- **探針集**：評測集的讀音是一個人一種打法，量不到「同一句用另一種合規讀音打」的情況。探針集從現有集合取含「一」「不」的列，依詞庫生成器同一套規則把每個可換的位置換成另一種教育部合規讀音（本調與變調互換），一列產生一個探針列，沒有可換位置的列略過。產生器是 `tools/build_probe.py`（`--check` 重產比對，規格見 `docs/spec/decoder.md` §8.1）。公開的 dev302 加打字測驗探針放 `eval/probe/s2r-probe.txt`，不放 `eval/dev/`，以免改變「開發集前 302 列」；含私人句子或授權不允許散布的集合，探針只放私人環境。比較時和同一批列原本的讀音配對：先確認現況下換讀音確實變差（前提），再比較改動前後的探針結果，並另外用原讀音確認不退步。

### 11.3 一次迭代

1. 改動前先寫下預期會動到哪一類錯誤。
2. 跑一個或多個設定，每句記下對錯、正解名次、分數差與對數損失（`--rowstats`，2.2）。
3. 和基準逐句配對比較：修好幾句、弄壞幾句、精確 McNemar、bootstrap 區間（2.3），每個集合在聊天與書面各報一行同一張表（2.6）。調參數的前後比較用調參集，不用 dev302。
4. 依 11.4 判斷。
5. 結果與判斷寫進該片的紀錄。

**解析度。** 一句對一句錯的雜訊不能當成進步。在改動影響約 4% 句子的假設下，配對比較要達到 5% 顯著，淨增益要大約：dev302 2.3 個百分點、打字測驗 4.5 個百分點；列數越多越小，一萬兩千列約 0.35。`tools/evalstats.py mde`（2.5）直接算給定不一致列數下的最小淨值。

**分兩軌。**

- 探索：登記一頁，只用調參集與開發層的集合，不出貨、不碰判讀集與保留集，結論只寫方向。
- 確認：一頁鎖定並先 commit（主判讀、規則、集合、預算、診斷），在沒看過的判讀集上一發。不過就是這次確認不成立，不開子片救同一次確認；變體回探索，再確認要新的判讀集。看過結果後改門檻、篩選、集合或格點，這次確認作廢。
- 開確認之前先用既有的分數檔印診斷（單獨挑對率、留住率、融合上限、oracle）；比較模型每臂至少 3 個種子；格點事先固定、至多 24 格。

### 11.4 出貨標準

新模型或改到選字結果的改動，對現行出貨值，四條都要過：

1. **主要判讀集（私人的聊天集合）**：不顯著變差（精確 McNemar p ≥ 0.05，或淨值 ≥ 0），而且改壞 ÷ 改對 ≤ 0.6。
2. **回歸層**：打字測驗（聊天、書面）與 dev302 的書面設定，逐列相同，或改壞 ≤ 2 且淨值 ≥ 0。dev302 的聊天設定照報、逐列標類別，不擋。
3. **聊天非劣性**：cvtune-clean-tw0 的 Δtop1 的 90% 配對 bootstrap 下界 ≥ −1.0 個百分點（6.1）。
4. **錯字回報**（聊天與書面兩種設定合計）：淨值 > 0，而且改壞 ÷ 改對 ≤ 0.6。錯字集只能當「有進步」的證據，不能單獨決定。只加詞的詞包（不改解碼器與模型）：錯字回報改壞 0（含 0／0）算過。

探索階段過了這四條，出貨前還要在沒看過的判讀集確認一發：淨值 > 0、精確 McNemar p < 0.05、改壞 ÷ 改對 ≤ 0.65，門檻在看數字之前先寫定。語意校正的強度檔位與融合參數的選法見 `docs/spec/semantic-rerank.md`。

### 11.5 防止過度擬合

- 不從評測句挑詞加進詞庫；新詞只能來自可再散布、獨立於評測的來源，並能從鎖定版本的來源完整重建（`tools/build_overlay.py --check`）。
- 合成語料的提示詞只寫主題、語氣或混淆字類別，不放任何評測句。
- 看過驗證集或保留集之後才修改的門檻或規則，要在文件裡標明「事後修改」。
- 已參與選模的集合，不能再當獨立的驗證。
- 保留集與調參集不能被吸進訓練文字（5.4 的防洩漏比對）。

**污染測試怎麼定義退步。** 用「輸出和沒學習時不同」算退步，會把原本就錯、變對的列也算進去（例如「近世」變成「近視」，變對了也是不同），所以退步定義為「這一列原本整句等於期望、之後不再等於」；文件、程式、發佈的表三處要用同一個定義。另外要寫清楚三件事：哪些列算污染列（期望是「沒被教的詞」的列，不一定是常用詞）；碰撞怎麼判斷（解碼用的前文 key 取決於 beam 裡的假設，從外面只能近似，要說明近似了什麼）；讀不出 key 的列另計，不悄悄當成碰撞或悄悄排除。

### 11.6 外部輸入法的比較

用相同的注音按鍵把句子打進其他輸入法（macOS 內建注音、小麥注音、自然輸入法），每句打完按 Return 送出，不選字；結果存成 `eval/bench/static/typing-test.json`（7.8）。注意事項：

- macOS 內建注音會學習平常的打字，也會在測驗過程中學到測驗句。要做公平比較，測驗前先備份它的學習檔、測完還原，或在測驗期間關閉學習。
- 自動打字的讀音來自 `tools/readings.py`；破音字無法自動判定的句子不打。讀音標錯會讓每一套一起錯，例如「欸」曾被標成 ㄞˇ。
- 送鍵的工具不在這棵樹。

### 11.7 資料、授權與隱私

| 資料 | 授權 | 用途 |
|---|---|---|
| 小麥注音詞庫 | MIT／BSD | 基底詞庫 |
| 維基百科、維基詞典標題 | CC BY-SA 4.0 | 疊加層（以 CC BY-SA 釋出） |
| 中文維基內文 | CC BY-SA 4.0 | 只訓練、不散布原文 |
| Common Voice zh-TW 句子 | CC0 | 訓練與調參集 |
| Tatoeba | CC BY 2.0 FR | 訓練與調參集 |
| 合成口語句（gpt-oss-120b，Apache-2.0 開放權重） | 模型輸出 | 訓練 |
| 樂詞網（國家教育研究院） | 政府網站資料開放宣告（須註明出處；散布前另行確認） | 領域詞包 |
| 國教院學術名詞開放資料（data.gov.tw 15416、15275） | 政府資料開放授權條款第 1 版（要標示提供機關） | 資訊＋AI 詞包 |
| invade 大陸用語詞表 | CC0 | 語料清理 |
| 萌典／教育部辭典 | CC BY-ND | 只用於評測讀音與寬鬆對照，不進詞庫 |
| 私人的聊天集合 | 取得者授權做本機評測 | 不進 repo，只公開統計數字；可放在其他做評測的私人環境上算評測 |
| 政府新聞稿 | 公文，不得為著作權之標的（著作權法第 9 條第 1 項第 1 款；第 2 項明列公文包括新聞稿） | 評測與訓練 |

- 群聊裡有其他人的發言，所以只能當評測或調參用，**不能用來訓練會隨安裝檔散布的模型**。
- 私人集合與它們產生的列檔、候選傾印、分數，放在 repo 外；repo 只放建置程式、合成的測試資料、數字與雜湊。只拿來訓練或打分的語料不散布原文、不轉交他人。
